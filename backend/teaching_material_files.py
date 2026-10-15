"""Private uploads retained by immutable library versions, including copied versions."""
import asyncio
from datetime import timedelta
import os
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException, Request
from starlette.concurrency import run_in_threadpool
from starlette.requests import ClientDisconnect
from .access import now
from . import homework_files as files
from . import teaching_media as media


def tables(db):
    db.execute('''CREATE TABLE IF NOT EXISTS teaching_material_files (
        id TEXT PRIMARY KEY,workspace TEXT,owner_id TEXT,filename TEXT,size INTEGER,created TEXT,status TEXT,sha256 TEXT)''')
    db.execute('CREATE INDEX IF NOT EXISTS teaching_material_files_owner ON teaching_material_files(owner_id,status)')
    db.execute('CREATE INDEX IF NOT EXISTS teaching_material_files_created ON teaching_material_files(status,created)')
    db.execute('''CREATE TABLE IF NOT EXISTS teaching_material_file_links (
        file_id TEXT,material_id TEXT,version INTEGER,PRIMARY KEY(file_id,material_id,version))''')
    db.execute('CREATE INDEX IF NOT EXISTS teaching_material_file_versions ON teaching_material_file_links(material_id,version)')


def root_for(db):
    database=Path(db.execute('PRAGMA database_list').fetchone()[2]).resolve()
    return database.with_suffix('.files')/'materials'


def meta(row):
    return dict(id=row[0],filename=row[3],size=row[4],created=row[5])


def attachments(db, ids):
    result=[]
    for id in ids:
        row=db.execute("SELECT * FROM teaching_material_files WHERE id=? AND status='ready'",(id,)).fetchone()
        if not row: raise HTTPException(409,'Файл версии недоступен. Обратитесь к администратору.')
        result.append(meta(row))
    return result


def bind_files(db, ids, workspace, material, version, user, *, trusted=False):
    for id in ids:
        row=db.execute("SELECT * FROM teaching_material_files WHERE id=? AND workspace=? AND status='ready'",(id,workspace)).fetchone()
        linked=db.execute('SELECT 1 FROM teaching_material_file_links WHERE file_id=? AND material_id=?',(id,material)).fetchone()
        if not row or not (trusted or row[2]==user['id'] or linked):
            raise HTTPException(422,'Файл не найден или принадлежит другому материалу.')
        db.execute('INSERT INTO teaching_material_file_links VALUES (?,?,?)',(id,material,version))


def collect_unused(db):
    rows=db.execute('''SELECT f.id FROM teaching_material_files f WHERE
        ((f.status='uploading' AND f.created<?) OR (f.status='ready' AND f.created<?))
        AND NOT EXISTS(SELECT 1 FROM teaching_material_file_links l WHERE l.file_id=f.id)
        ORDER BY f.created LIMIT 20''',((now()-timedelta(minutes=30)).isoformat(),(now()-timedelta(days=1)).isoformat())).fetchall()
    for (id,) in rows:
        try: files.file_path(root_for(db),id).unlink(missing_ok=True)
        except OSError: continue
        db.execute('DELETE FROM teaching_material_files WHERE id=?',(id,))


def reserve(db, workspace, user, filename):
    name=media.name_for(filename)
    size=db.execute('SELECT coalesce(sum(size),0) FROM teaching_material_files WHERE owner_id=?',(user['id'],)).fetchone()[0]
    pending=db.execute("SELECT count(*) FROM teaching_material_files WHERE owner_id=? AND status='uploading'",(user['id'],)).fetchone()[0]
    if files.total_stored(db)+files.MAX_FILE>files.total_limit() or size+files.MAX_FILE>250*1024*1024:
        raise HTTPException(413,'Лимит хранилища достигнут. Обратитесь к администратору.')
    if pending>=2: raise HTTPException(429,'Дождитесь окончания текущих загрузок.')
    id=uuid4().hex
    db.execute('INSERT INTO teaching_material_files VALUES (?,?,?,?,?,?,?,NULL)',(id,workspace,user['id'],name,files.MAX_FILE,now().isoformat(),'uploading'))
    return id,name,root_for(db)


class MaterialFiles:
    def __init__(self, library):
        self.library,self.access=library,library.access

    def register(self, app, Workspace):
        from .teaching_materials import tables as library_tables, row_for, require_version_access, version_document

        @app.post('/api/journal/material-files')
        async def upload(request:Request,filename:str,workspace:Workspace='school'):
            if not files.UPLOAD_SLOTS.acquire(blocking=False): raise HTTPException(429,'Слишком много загрузок. Попробуйте чуть позже.')
            id=None;path=None;stream=None;finished=False
            try:
                user=self.library.scope(request,workspace)
                def prepare():
                    with self.access.lock,self.access.connect() as db:
                        library_tables(db);db.execute('BEGIN IMMEDIATE');self.library.recheck(db,user,workspace)
                        collect_unused(db)
                        return reserve(db,workspace,user,filename)
                id,name,root=await run_in_threadpool(prepare)
                path=files.file_path(root,id)
                await run_in_threadpool(lambda:root.mkdir(parents=True,exist_ok=True))
                stream=await run_in_threadpool(lambda:path.open('xb'))
                size=0
                async with asyncio.timeout(90):
                    async for chunk in request.stream():
                        size+=len(chunk)
                        if size>files.MAX_FILE: raise HTTPException(413,'Файл должен быть не больше 10 МБ.')
                        await run_in_threadpool(stream.write,chunk)
                    if not size: raise HTTPException(422,'Нельзя загрузить пустой файл.')
                    await run_in_threadpool(stream.flush)
                    await run_in_threadpool(os.fsync,stream.fileno())
                await run_in_threadpool(stream.close);stream=None
                def finish():
                    digest=media.validate_file(path,name)
                    with self.access.lock,self.access.connect() as db:
                        db.execute('BEGIN IMMEDIATE');self.library.recheck(db,user,workspace)
                        db.execute("UPDATE teaching_material_files SET status='ready',size=?,sha256=? WHERE id=?",(size,digest,id))
                        self.access.record(db,'material.file_uploaded',workspace,detail=id)
                        return meta(db.execute('SELECT * FROM teaching_material_files WHERE id=?',(id,)).fetchone())
                result=await run_in_threadpool(finish);finished=True
                return result
            except TimeoutError: raise HTTPException(408,'Загрузка прервана по времени.')
            except ClientDisconnect: raise HTTPException(400,'Загрузка прервана.')
            finally:
                try:
                    if stream: await run_in_threadpool(stream.close)
                    if id and not finished:
                        def cleanup():
                            with self.access.lock,self.access.connect() as db:
                                row=db.execute("SELECT 1 FROM teaching_material_files WHERE id=? AND status='uploading'",(id,)).fetchone()
                                if row:
                                    if path: path.unlink(missing_ok=True)
                                    db.execute('DELETE FROM teaching_material_files WHERE id=?',(id,))
                        await run_in_threadpool(cleanup)
                finally: files.UPLOAD_SLOTS.release()

        @app.get('/api/journal/material-files/{id}')
        def download(id:str,request:Request,workspace:Workspace='school',materialId:str|None=None,version:int|None=None,lessonKey:str|None=None,download:bool=False):
            school=None
            if lessonKey:
                user=self.library.journal.scope(request,workspace)
                if user['role']=='admin' and workspace not in user['workspaces']: raise HTTPException(403,'Нет доступа к пространству.')
                if not materialId or not version: raise HTTPException(422,'Укажите материал и версию урока.')
                school=self.library.snapshots.get(workspace)
            else:user=self.library.scope(request,workspace)
            with self.access.lock,self.access.connect() as db:
                library_tables(db);db.execute('BEGIN')
                if lessonKey:
                    self.library.lesson_file(db,user,workspace,lessonKey,materialId,version,id,school)
                else:
                    self.library.recheck(db,user,workspace)
                    if materialId:
                        row=row_for(db,workspace,materialId)
                        require_version_access(db,user,row,version or row['version'])
                        document,_,_=version_document(db,materialId,version or row['version'])
                        if id not in document['fileIds']: raise HTTPException(404,'Файл не найден в этой версии.')
                row=db.execute("SELECT * FROM teaching_material_files WHERE id=? AND workspace=? AND status='ready'",(id,workspace)).fetchone()
                if not row or not lessonKey and not materialId and row[2]!=user['id']: raise HTTPException(404,'Файл не найден.')
                path=files.file_path(root_for(db),id)
                if not path.is_file(): raise HTTPException(404,'Файл недоступен. Обратитесь к администратору.')
                return media.response(path,row[3],download=download)
