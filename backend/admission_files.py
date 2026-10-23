"""Private applicant documents; authorization is rechecked when an upload finishes."""
import asyncio, os
from pathlib import Path
from uuid import uuid4
from fastapi import HTTPException, Query, Request
from fastapi.responses import FileResponse
from starlette.concurrency import run_in_threadpool
from starlette.requests import ClientDisconnect
from . import homework_files as storage
from .access import now
from .admissions import version

def root_for(db):return Path(db.execute('PRAGMA database_list').fetchone()[2]).resolve().with_suffix('.files')/'admissions'
def meta(r):return dict(id=r['id'],filename=r['filename'],size=r['size'],created=r['created'],linked=bool(r['linked']))

def referenced(db,id,application_id):
    return bool(db.execute("SELECT 1 FROM admission_applications a,json_each(a.document,'$.files') f WHERE a.id=? AND json_extract(f.value,'$.id')=? LIMIT 1",(application_id,id)).fetchone())

def unused_rows(db,limit=20,days=1):
    # Draft documents keep their files (linked stays 0 until submit). The filter is part of the query so that
    # LIMIT counts deletable rows only: many older draft files must not hide an abandoned upload behind them.
    stale,old=storage.cutoffs(days)
    return [(r[0],r[1]) for r in db.execute("""SELECT id,size FROM admission_files WHERE linked=0 AND ((status='uploading' AND created<?) OR (status='ready' AND created<?))
        AND NOT EXISTS(SELECT 1 FROM admission_applications a,json_each(a.document,'$.files') f WHERE a.id=admission_files.application_id AND json_extract(f.value,'$.id')=admission_files.id)
        ORDER BY created LIMIT ?""",(stale,old,limit))]

def collect_unused(db):
    root=root_for(db)
    for id,_ in unused_rows(db):
        try:storage.release_row(db,'admission_files',root,id)
        except OSError:continue

def register(s,app,Workspace):
    def writable(db,u,id,expected):
        row=s.application(db,id,u);version(row,expected);s.editable(row,s.campaign(db,row['campaign_id']));return row

    @app.post('/admissions-api/applications/{id}/files')
    async def upload(id:str,request:Request,applicationVersion:int=Query(ge=1),filename:str=Query(min_length=1,max_length=180)):
        name=storage.name_for(filename)
        if Path(name).suffix.lower() not in ['.pdf','.png','.jpg','.jpeg']:raise HTTPException(422,'Выберите PDF, JPG или PNG.')
        slots=await storage.acquire_slot()
        reservation=None;handle=None;complete=False
        def prepare():
            with s.family(request,True) as (db,u):
                writable(db,u,id,applicationVersion);collect_unused(db)
                used,pending=db.execute("SELECT coalesce(sum(size),0),count(CASE WHEN status='uploading' THEN 1 END) FROM admission_files WHERE owner_id=?",(u['id'],)).fetchone()
                if used+storage.MAX_FILE>100*1024*1024:raise HTTPException(413,'Лимит хранилища достигнут. Обратитесь в школу.')
                storage.require_capacity(db,'admissions')
                if pending>=2:raise storage.busy('Дождитесь окончания текущих загрузок.')
                if db.execute('SELECT count(*) FROM admission_files WHERE application_id=?',(id,)).fetchone()[0]>=40:raise HTTPException(413,'Достигнут предел 40 документов с учётом истории. Удалите неиспользованные загрузки.')
                file_id=uuid4().hex
                db.execute('INSERT INTO admission_files VALUES (?,?,?,?,?,?,?,NULL,0)',(file_id,id,u['id'],name,storage.MAX_FILE,now().isoformat(),'uploading'))
                return file_id,root_for(db)
        def finish(size):
            file_id,root=reservation;digest=storage.validate_file(storage.file_path(root,file_id),name)
            with s.family(request,True) as (db,u):
                writable(db,u,id,applicationVersion)
                if not db.execute("UPDATE admission_files SET size=?,sha256=?,status='ready' WHERE id=? AND status='uploading'",(size,digest,file_id)).rowcount:raise HTTPException(409,'Загрузка уже недоступна.')
                return meta(db.execute('SELECT * FROM admission_files WHERE id=?',(file_id,)).fetchone())
        try:
            try:length=int(request.headers.get('content-length','0'))
            except ValueError:raise HTTPException(422,'Некорректный размер файла.')
            if length>storage.MAX_FILE:raise HTTPException(413,'Документ должен быть не больше 10 МБ.')
            reservation=await run_in_threadpool(prepare);file_id,root=reservation;root.mkdir(parents=True,exist_ok=True)
            handle=storage.file_path(root,file_id).open('xb');size=0
            async with asyncio.timeout(90):
                async for chunk in request.stream():
                    size+=len(chunk)
                    if size>storage.MAX_FILE:raise HTTPException(413,'Документ должен быть не больше 10 МБ.')
                    await run_in_threadpool(handle.write,chunk)
            def flush():handle.flush();os.fsync(handle.fileno());handle.close()
            await run_in_threadpool(flush);handle=None
            if not size:raise HTTPException(422,'Выберите непустой документ.')
            result=await run_in_threadpool(finish,size);complete=True;return result
        except TimeoutError:raise HTTPException(408,'Загрузка заняла слишком много времени.') from None
        except ClientDisconnect:raise HTTPException(400,'Соединение прервано при загрузке.') from None
        finally:
            try:
                if handle:handle.close()
                if reservation and not complete:
                    def discard():
                        file_id,root=reservation
                        with s.database(True) as db:
                            r=db.execute('SELECT status FROM admission_files WHERE id=?',(file_id,)).fetchone()
                            if not r or r[0]!='uploading':return
                            try:storage.file_path(root,file_id).unlink(missing_ok=True)
                            except OSError:return
                            db.execute('DELETE FROM admission_files WHERE id=?',(file_id,))
                    await run_in_threadpool(discard)
            finally:slots.release()

    @app.get('/admissions-api/applications/{id}/files')
    def files(id:str,request:Request):
        with s.family(request) as (db,u):
            s.application(db,id,u);return dict(items=[meta(r) for r in db.execute("SELECT * FROM admission_files WHERE application_id=? AND status='ready' ORDER BY created,id",(id,))])

    def download(db,r):
        path=storage.file_path(root_for(db),r['id'])
        if not path.is_file():raise HTTPException(404,'Документ недоступен.')
        return FileResponse(path,media_type='application/octet-stream',filename=r['filename'],headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff','Content-Security-Policy':"default-src 'none'; sandbox"})

    @app.get('/admissions-api/files/{id}')
    def family_file(id:str,request:Request):
        with s.family(request) as (db,u):
            r=db.execute("SELECT * FROM admission_files WHERE id=? AND owner_id=? AND status='ready'",(id,u['id'])).fetchone()
            if not r:raise HTTPException(404,'Документ недоступен.')
            s.application(db,r['application_id'],u);return download(db,r)

    @app.get('/api/admissions/files/{id}')
    def staff_file(id:str,request:Request,workspace:Workspace='school'):
        with s.staff(request,workspace) as (db,u,snap):
            r=db.execute("SELECT * FROM admission_files WHERE id=? AND status='ready' AND linked=1",(id,)).fetchone()
            if not r:raise HTTPException(404,'Документ недоступен.')
            s.staff_case(db,u,workspace,r['application_id']);return download(db,r)

    @app.delete('/admissions-api/files/{id}')
    def remove(id:str,request:Request):
        with s.family(request,True) as (db,u):
            r=db.execute("SELECT * FROM admission_files WHERE id=? AND owner_id=? AND status='ready'",(id,u['id'])).fetchone()
            if not r:raise HTTPException(404,'Документ недоступен.')
            if r['linked'] or referenced(db,id,r['application_id']):raise HTTPException(409,'Сначала уберите файл из черновика. Документы из отправленной истории сохраняются.')
            storage.file_path(root_for(db),id).unlink(missing_ok=True);db.execute('DELETE FROM admission_files WHERE id=?',(id,));return dict(ok=True)
