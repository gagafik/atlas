"""Teacher materials: immutable files, journal-history retention and scoped delivery."""
import asyncio
from datetime import date, timedelta
import os
from pathlib import Path
from urllib.parse import quote
from uuid import uuid4

from fastapi import HTTPException, Query, Request
from fastapi.responses import FileResponse
from starlette.concurrency import run_in_threadpool
from starlette.requests import ClientDisconnect

from .access import now
from . import homework_files as files
from .journal_periods import require_open

MAX_OWNER = 250*1024*1024
MAX_TASK = 100


def tables(db):
    db.execute('''CREATE TABLE IF NOT EXISTS assignment_files (
        id TEXT PRIMARY KEY,workspace TEXT,lesson_key TEXT,owner_id TEXT,
        filename TEXT,size INTEGER,created TEXT,status TEXT,sha256 TEXT,day TEXT)''')
    db.execute('CREATE INDEX IF NOT EXISTS assignment_files_target ON assignment_files(workspace,lesson_key,status)')
    db.execute('CREATE INDEX IF NOT EXISTS assignment_files_owner ON assignment_files(owner_id,status)')
    db.execute('CREATE INDEX IF NOT EXISTS assignment_files_created ON assignment_files(status,created)')
    db.execute('CREATE TABLE IF NOT EXISTS assignment_file_links (file_id TEXT PRIMARY KEY)')
    db.execute('''CREATE TABLE IF NOT EXISTS assignment_submission_links (
        file_id TEXT,student_id TEXT,PRIMARY KEY(file_id,student_id))''')


def root_for(db):
    return Path(db.execute('PRAGMA database_list').fetchone()[2]).resolve().with_suffix('.files')/'assignments'


def meta(row):
    return dict(id=row[0],filename=row[4],size=row[5],created=row[6])


def attachments(db, ids):
    return [meta(row) for id in ids if (row:=db.execute("SELECT * FROM assignment_files WHERE id=? AND status='ready'",(id,)).fetchone())]


def bind(db, ids, workspace, key, owner):
    if len(set(ids))!=len(ids): raise HTTPException(422,'Один материал указан несколько раз.')
    for id in ids:
        if not db.execute('''SELECT 1 FROM assignment_files f WHERE id=? AND workspace=? AND lesson_key=? AND status='ready'
            AND (owner_id=? OR EXISTS(SELECT 1 FROM assignment_file_links l WHERE l.file_id=f.id))''',(id,workspace,key,owner)).fetchone():
            raise HTTPException(422,'Материал не найден или относится к другому уроку/автору.')
    # A saved journal revision is immutable evidence, including unpublished drafts.
    db.executemany('INSERT OR IGNORE INTO assignment_file_links VALUES (?)',[(id,) for id in ids])


def collect_unused(db):
    rows=db.execute('''SELECT f.id FROM assignment_files f WHERE
        ((status='uploading' AND created<?) OR (status='ready' AND created<?))
        AND NOT EXISTS(SELECT 1 FROM assignment_file_links l WHERE l.file_id=f.id)
        ORDER BY created LIMIT 20''',((now()-timedelta(minutes=30)).isoformat(),(now()-timedelta(days=1)).isoformat())).fetchall()
    for (id,) in rows:
        try: files.file_path(root_for(db),id).unlink(missing_ok=True)
        except OSError: continue
        db.execute('DELETE FROM assignment_files WHERE id=?',(id,))


def reserve(db, ctx, filename):
    name=files.name_for(filename)
    owner=ctx['user']['id']
    size=db.execute('SELECT coalesce(sum(size),0) FROM assignment_files WHERE owner_id=?',(owner,)).fetchone()[0]
    count=db.execute('SELECT count(*) FROM assignment_files WHERE workspace=? AND lesson_key=?',(ctx['workspace'],ctx['key'])).fetchone()[0]
    pending=db.execute("SELECT count(*) FROM assignment_files WHERE owner_id=? AND status='uploading'",(owner,)).fetchone()[0]
    if files.total_stored(db)+files.MAX_FILE>files.total_limit() or size+files.MAX_FILE>MAX_OWNER:
        raise HTTPException(413,'Лимит хранилища достигнут. Обратитесь к администратору.')
    if count>=MAX_TASK: raise HTTPException(413,'Достигнут лимит материалов урока с учётом истории.')
    if pending>=2: raise HTTPException(429,'Дождитесь окончания текущих загрузок.')
    id=uuid4().hex
    db.execute('INSERT INTO assignment_files VALUES (?,?,?,?,?,?,?,?,NULL,?)',
               (id,ctx['workspace'],ctx['key'],owner,name,files.MAX_FILE,now().isoformat(),'uploading',ctx['day'].isoformat()))
    return id,name,root_for(db)


class AssignmentFiles:
    def __init__(self, homework):
        self.homework=homework
        self.journal=homework.journal
        self.access=homework.access

    def context(self, request, workspace, key, day, writable=False):
        user=self.journal.scope(request,workspace,staff=True)
        if user['role']=='admin' and workspace not in user['workspaces']: raise HTTPException(403,'Нет доступа к пространству.')
        state=self.homework.snapshots.get(workspace).state
        entry=self.journal.entry(state,workspace,user,key,day)
        if writable and (not entry['writable'] or entry['needsSync'] or entry['currentDate']!=day.isoformat()):
            raise HTTPException(409,'Урок закрыт или изменён. Обновите журнал перед загрузкой материалов.')
        return dict(user=user,workspace=workspace,key=key,day=day,revision=state.revision,record=entry,writable=writable)

    def recheck(self, db, ctx):
        self.journal.recheck_account(db,ctx['user'])
        revision=db.execute('SELECT revision FROM workspaces WHERE id=?',(ctx['workspace'],)).fetchone()
        row=db.execute('SELECT version FROM journal_entries WHERE workspace=? AND lesson_key=?',(ctx['workspace'],ctx['key'])).fetchone()
        if not revision or revision[0]!=ctx['revision'] or (row[0] if row else 0)!=ctx['record']['version']:
            raise HTTPException(409,'Журнал или расписание изменилось. Обновите запись.')
        if ctx['writable']:
            require_open(db,ctx['workspace'],ctx['record']['document']['lesson'])

    def register(self, app, Workspace):
        @app.get('/api/journal/assignment-files')
        def listing(request:Request,lessonKey:str,day:date,workspace:Workspace='school'):
            ctx=self.context(request,workspace,lessonKey,day)
            with self.access.lock,self.access.connect() as db:
                self.homework.tables(db);db.execute('BEGIN');self.recheck(db,ctx)
                rows=db.execute('''SELECT * FROM assignment_files f WHERE workspace=? AND lesson_key=? AND status='ready'
                    AND (owner_id=? OR EXISTS(SELECT 1 FROM assignment_file_links l WHERE l.file_id=f.id)) ORDER BY created,id LIMIT 100''',
                    (workspace,lessonKey,ctx['user']['id'])).fetchall()
                return dict(items=[meta(r) for r in rows])

        @app.post('/api/journal/assignment-files')
        async def upload(request:Request,lessonKey:str,day:date,filename:str,revision:int=Query(ge=0),version:int=Query(ge=0),workspace:Workspace='school'):
            if not files.UPLOAD_SLOTS.acquire(blocking=False): raise HTTPException(429,'Сервер принимает другие файлы. Повторите позже.')
            reservation=None;handle=None;complete=False
            def prepare():
                ctx=self.context(request,workspace,lessonKey,day,writable=True)
                if ctx['revision']!=revision or ctx['record']['version']!=version: raise HTTPException(409,'Обновите журнал перед загрузкой материалов.')
                with self.access.lock,self.access.connect() as db:
                    self.homework.tables(db);db.execute('BEGIN IMMEDIATE');self.recheck(db,ctx)
                    collect_unused(db)
                    id,name,root=reserve(db,ctx,filename)
                return ctx,id,name,root
            def finish(size):
                ctx,id,name,root=reservation
                digest=files.validate_file(files.file_path(root,id),name)
                with self.access.lock,self.access.connect() as db:
                    db.execute('BEGIN IMMEDIATE');self.recheck(db,ctx)
                    db.execute("UPDATE assignment_files SET status='ready',size=?,sha256=? WHERE id=?",(size,digest,id))
                    self.access.record(db,'homework.material_uploaded',workspace,detail=id)
                    return meta(db.execute('SELECT * FROM assignment_files WHERE id=?',(id,)).fetchone())
            try:
                try: length=int(request.headers.get('content-length','0'))
                except ValueError: raise HTTPException(422,'Некорректный размер файла.')
                if length>files.MAX_FILE: raise HTTPException(413,'Файл должен быть не больше 10 МБ.')
                reservation=await run_in_threadpool(prepare)
                _,id,_,root=reservation
                root.mkdir(parents=True,exist_ok=True)
                handle=files.file_path(root,id).open('xb')
                size=0
                async with asyncio.timeout(90):
                    async for chunk in request.stream():
                        size+=len(chunk)
                        if size>files.MAX_FILE: raise HTTPException(413,'Файл должен быть не больше 10 МБ.')
                        await run_in_threadpool(handle.write,chunk)
                    if not size: raise HTTPException(422,'Пустой файл нельзя приложить.')
                    await run_in_threadpool(handle.flush)
                    await run_in_threadpool(os.fsync,handle.fileno())
                    await run_in_threadpool(handle.close);handle=None
                    result=await run_in_threadpool(finish,size)
                    complete=True
                    return result
            except TimeoutError: raise HTTPException(408,'Загрузка заняла слишком много времени.') from None
            except ClientDisconnect: raise HTTPException(400,'Загрузка прервана.') from None
            finally:
                try:
                    if handle: handle.close()
                    if reservation and not complete:
                        def discard():
                            _,id,_,root=reservation
                            with self.access.lock,self.access.connect() as db:
                                db.execute('BEGIN IMMEDIATE')
                                row=db.execute('SELECT status FROM assignment_files WHERE id=?',(id,)).fetchone()
                                if row and row[0]=='uploading':
                                    try: files.file_path(root,id).unlink(missing_ok=True)
                                    except OSError: return
                                    db.execute('DELETE FROM assignment_files WHERE id=?',(id,))
                        await run_in_threadpool(discard)
                finally:
                    files.UPLOAD_SLOTS.release()

        @app.get('/api/journal/assignment-files/{id}')
        def download(id:str,request:Request,workspace:Workspace='school',studentId:str|None=None):
            user=self.journal.scope(request,workspace)
            with self.access.lock,self.access.connect() as db:
                self.homework.tables(db)
                row=db.execute("SELECT * FROM assignment_files WHERE id=? AND workspace=? AND status='ready'",(id,workspace)).fetchone()
            if not row: raise HTTPException(404,'Материал не найден.')
            staff=user['role'] in ['admin','teacher']
            ctx=self.context(request,workspace,row[2],date.fromisoformat(row[9])) if staff else self.homework.context(request,workspace,row[2],studentId)
            with self.access.lock,self.access.connect() as db:
                db.execute('BEGIN')
                if staff:
                    self.recheck(db,ctx)
                    visible=row[3]==user['id'] or db.execute('SELECT 1 FROM assignment_file_links WHERE file_id=?',(id,)).fetchone()
                else:
                    self.homework.recheck(db,ctx)
                    visible=id in ctx['published']['content'].get('homeworkFiles',[]) or db.execute(
                        'SELECT 1 FROM assignment_submission_links WHERE file_id=? AND student_id=?',(id,studentId)).fetchone()
                if not visible: raise HTTPException(404,'Материал не найден.')
                # GC may have removed an abandoned upload since the first lookup.
                if not db.execute("SELECT 1 FROM assignment_files WHERE id=? AND status='ready'",(id,)).fetchone(): raise HTTPException(404,'Материал не найден.')
                path=files.file_path(root_for(db),id)
                try: stat=path.stat()
                except OSError: raise HTTPException(404,'Материал недоступен в хранилище.') from None
            return FileResponse(path,stat_result=stat,media_type='application/octet-stream',headers={
                'Content-Disposition':"attachment; filename*=UTF-8''"+quote(row[4],safe=''),
                'Content-Length':str(row[5]),'Cache-Control':'no-store','X-Content-Type-Options':'nosniff',
                'Content-Security-Policy':"sandbox; default-src 'none'"})
