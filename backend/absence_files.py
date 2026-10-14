"""Private evidence attached to one parent's absence notice."""
import asyncio
from datetime import timedelta
import os
from pathlib import Path
from urllib.parse import quote
from uuid import uuid4
from fastapi import HTTPException, Query, Request
from fastapi.responses import FileResponse
from starlette.concurrency import run_in_threadpool
from starlette.requests import ClientDisconnect
from .access import now
from . import homework_files as storage


def tables(db):
    db.execute('''CREATE TABLE IF NOT EXISTS absence_files (
        id TEXT PRIMARY KEY,workspace TEXT,owner_id TEXT,draft_id TEXT,student_id TEXT,
        filename TEXT,size INTEGER,created TEXT,status TEXT,sha256 TEXT,notice_id TEXT)''')
    db.execute('CREATE INDEX IF NOT EXISTS absence_files_owner ON absence_files(workspace,owner_id,draft_id,status)')
    db.execute('CREATE INDEX IF NOT EXISTS absence_files_notice ON absence_files(notice_id)')


def root_for(db):return Path(db.execute('PRAGMA database_list').fetchone()[2]).resolve().with_suffix('.files')/'absences'
def meta(row):return dict(id=row[0],filename=row[5],size=row[6],created=row[7])


def cleanup(db):
    cutoff=(now()-timedelta(days=1)).isoformat()
    rows=db.execute('SELECT id FROM absence_files WHERE notice_id IS NULL AND created<? LIMIT 50',(cutoff,)).fetchall()
    for row in rows:
        try:storage.file_path(root_for(db),row[0]).unlink(missing_ok=True)
        except OSError:continue
        db.execute('DELETE FROM absence_files WHERE id=?',(row[0],))


class AbsenceFiles:
    def __init__(self,absences):self.absences,self.access=absences,absences.access
    tables=staticmethod(tables)

    def check(self,db,user,snap,workspace,student):
        self.absences.recheck(db,user,snap,workspace)
        self.absences.parent(user,snap,workspace,student)

    def linked(self,db,id):return [meta(r) for r in db.execute("SELECT * FROM absence_files WHERE notice_id=? AND status='ready' ORDER BY created,id",(id,))]

    def bind(self,db,ids,user,workspace,student,draft,id):
        for file in ids:
            row=db.execute("SELECT id FROM absence_files WHERE id=? AND workspace=? AND owner_id=? AND student_id=? AND draft_id=? AND status='ready' AND notice_id IS NULL",(file,workspace,user['id'],student,draft)).fetchone()
            if not row:raise HTTPException(422,'Приложите собственные файлы из этой формы для этого ребёнка.')
            db.execute('UPDATE absence_files SET notice_id=? WHERE id=?',(id,file))

    def register(self,app,Workspace):
        @app.post('/api/journal/absence-files')
        async def upload(request:Request,filename:str,studentId:str,draftId:str=Query(pattern=r'^[a-f0-9-]{36}$'),workspace:Workspace='school'):
            if not storage.UPLOAD_SLOTS.acquire(blocking=False):raise HTTPException(429,'Сервер принимает другие файлы. Повторите позже.')
            reservation=None;handle=None;complete=False
            def prepare():
                user,snap=self.absences.context(request,workspace)
                with self.access.lock,self.access.connect() as db:
                    tables(db);db.execute('BEGIN IMMEDIATE');self.check(db,user,snap,workspace,studentId);cleanup(db)
                    name=storage.name_for(filename)
                    used,count,pending=db.execute("SELECT coalesce(sum(size),0),count(CASE WHEN notice_id IS NULL THEN 1 END),count(CASE WHEN status='uploading' THEN 1 END) FROM absence_files WHERE workspace=? AND owner_id=?",(workspace,user['id'])).fetchone()
                    if used+storage.MAX_FILE>storage.MAX_STUDENT or storage.total_stored(db)+storage.MAX_FILE>storage.total_limit():raise HTTPException(413,'Лимит хранилища достигнут. Обратитесь к школе.')
                    if count>=50:raise HTTPException(413,'Удалите ненужные неотправленные файлы.')
                    if pending>=2:raise HTTPException(429,'Дождитесь завершения текущих загрузок.')
                    id=uuid4().hex;root=root_for(db)
                    db.execute('INSERT INTO absence_files VALUES (?,?,?,?,?,?,?,?,?,NULL,NULL)',(id,workspace,user['id'],draftId,studentId,name,storage.MAX_FILE,now().isoformat(),'uploading'))
                return user,snap,id,name,root
            def finish(size):
                user,snap,id,name,root=reservation;digest=storage.validate_file(storage.file_path(root,id),name)
                with self.access.lock,self.access.connect() as db:
                    db.execute('BEGIN IMMEDIATE');self.check(db,user,snap,workspace,studentId)
                    changed=db.execute("UPDATE absence_files SET status='ready',size=?,sha256=? WHERE id=? AND status='uploading'",(size,digest,id)).rowcount
                    if not changed:raise HTTPException(409,'Загрузка уже недоступна.')
                    self.access.record(db,'absence.file_uploaded',workspace,detail=id)
                    return meta(db.execute('SELECT * FROM absence_files WHERE id=?',(id,)).fetchone())
            try:
                try:length=int(request.headers.get('content-length','0'))
                except ValueError:raise HTTPException(422,'Некорректный размер файла.')
                if length>storage.MAX_FILE:raise HTTPException(413,'Файл должен быть не больше 10 МиБ.')
                reservation=await run_in_threadpool(prepare)
                _,_,id,_,root=reservation;root.mkdir(parents=True,exist_ok=True)
                handle=storage.file_path(root,id).open('xb');size=0
                async with asyncio.timeout(90):
                    async for chunk in request.stream():
                        size+=len(chunk)
                        if size>storage.MAX_FILE:raise HTTPException(413,'Файл должен быть не больше 10 МиБ.')
                        await run_in_threadpool(handle.write,chunk)
                def flush():handle.flush();os.fsync(handle.fileno());handle.close()
                await run_in_threadpool(flush);handle=None
                if not size:raise HTTPException(422,'Выберите непустой файл.')
                result=await run_in_threadpool(finish,size);complete=True;return result
            except TimeoutError:raise HTTPException(408,'Загрузка заняла слишком много времени.') from None
            except ClientDisconnect:raise HTTPException(400,'Соединение прервано во время загрузки.') from None
            finally:
                try:
                    if handle:handle.close()
                    if reservation and not complete:
                        _,_,id,_,root=reservation
                        def discard():
                            with self.access.lock,self.access.connect() as db:
                                db.execute('BEGIN IMMEDIATE')
                                row=db.execute('SELECT status FROM absence_files WHERE id=?',(id,)).fetchone()
                                if not row or row[0]!='uploading':return
                                try:storage.file_path(root,id).unlink(missing_ok=True)
                                except OSError:return
                                db.execute('DELETE FROM absence_files WHERE id=?',(id,))
                        await run_in_threadpool(discard)
                finally:storage.UPLOAD_SLOTS.release()

        @app.get('/api/journal/absence-files')
        def available(request:Request,studentId:str,draftId:str=Query(pattern=r'^[a-f0-9-]{36}$'),workspace:Workspace='school'):
            user,snap=self.absences.context(request,workspace)
            with self.access.lock,self.access.connect() as db:
                tables(db);self.check(db,user,snap,workspace,studentId)
                return dict(items=[meta(r) for r in db.execute("SELECT * FROM absence_files WHERE workspace=? AND owner_id=? AND draft_id=? AND student_id=? AND status='ready' AND notice_id IS NULL ORDER BY created,id LIMIT 50",(workspace,user['id'],draftId,studentId))])

        @app.delete('/api/journal/absence-files/{id}')
        def remove(id:str,request:Request,workspace:Workspace='school'):
            user,snap=self.absences.context(request,workspace)
            with self.access.lock,self.access.connect() as db:
                tables(db);db.execute('BEGIN IMMEDIATE')
                row=db.execute('SELECT * FROM absence_files WHERE id=? AND workspace=? AND owner_id=?',(id,workspace,user['id'])).fetchone()
                if not row:raise HTTPException(404,'Файл не найден.')
                self.check(db,user,snap,workspace,row[4])
                if row[8]!='ready' or row[10] is not None:raise HTTPException(409,'Удаляется только собственный неотправленный файл.')
                storage.file_path(root_for(db),id).unlink(missing_ok=True);db.execute('DELETE FROM absence_files WHERE id=?',(id,))
            return dict(ok=True)

        @app.get('/api/journal/absence-files/{id}')
        def download(id:str,request:Request,workspace:Workspace='school'):
            user,snap=self.absences.context(request,workspace)
            with self.access.lock,self.access.connect() as db:
                tables(db);self.absences.recheck(db,user,snap,workspace)
                row=db.execute("SELECT * FROM absence_files WHERE id=? AND workspace=? AND status='ready'",(id,workspace)).fetchone()
                if not row:raise HTTPException(404,'Файл не найден.')
                if row[10] is None:
                    if row[2]!=user['id']:raise HTTPException(404,'Файл не найден.')
                    self.absences.parent(user,snap,workspace,row[4])
                else:self.absences.find(db,row[10],user,snap,workspace)
                path=storage.file_path(root_for(db),id)
                try:stat=path.stat()
                except OSError:raise HTTPException(404,'Файл недоступен в хранилище.') from None
                self.access.record(db,'absence.file_read',workspace,detail=id)
            return FileResponse(path,stat_result=stat,media_type='application/octet-stream',headers={'Content-Disposition':"attachment; filename*=UTF-8''"+quote(row[5],safe=''),'Cache-Control':'no-store','X-Content-Type-Options':'nosniff','Content-Security-Policy':"sandbox; default-src 'none'"})
