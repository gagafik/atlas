"""Private message attachments, streamed and quota-limited before publication."""
import asyncio
from datetime import timedelta
import os
from pathlib import Path
from urllib.parse import quote
from uuid import uuid4

from fastapi import HTTPException,Query,Request
from fastapi.responses import FileResponse
from starlette.concurrency import run_in_threadpool
from starlette.requests import ClientDisconnect
from .access import now
from . import homework_files as storage

MAX_OWNER=250*1024*1024


def tables(db):
    db.execute('''CREATE TABLE IF NOT EXISTS message_files (
        id TEXT PRIMARY KEY,workspace TEXT,owner_id TEXT,draft_id TEXT,target_thread TEXT,
        filename TEXT,size INTEGER,created TEXT,status TEXT,sha256 TEXT,post_id INTEGER)''')
    db.execute('CREATE INDEX IF NOT EXISTS message_files_owner ON message_files(workspace,owner_id,draft_id,status)')
    db.execute('CREATE INDEX IF NOT EXISTS message_files_post ON message_files(post_id)')
    db.execute('CREATE INDEX IF NOT EXISTS message_files_expiry ON message_files(status,created)')


def root_for(db):
    return Path(db.execute('PRAGMA database_list').fetchone()[2]).resolve().with_suffix('.files')/'messages'


def meta(row):return dict(id=row[0],filename=row[5],size=row[6],created=row[7])


def collect_unused(db):
    from .message_drafts import tables as draft_tables
    draft_tables(db)
    rows=db.execute('''SELECT id FROM message_files WHERE post_id IS NULL AND
        ((status='uploading' AND created<?) OR (status='ready' AND created<?))
        AND NOT EXISTS (SELECT 1 FROM message_drafts d
            WHERE d.workspace=message_files.workspace AND d.owner_id=message_files.owner_id
            AND d.id=message_files.draft_id AND d.status='active'
            AND EXISTS(SELECT 1 FROM json_each(d.content,'$.fileIds') WHERE value=message_files.id))
        ORDER BY created LIMIT 20''',((now()-timedelta(minutes=30)).isoformat(),(now()-timedelta(days=1)).isoformat())).fetchall()
    root=root_for(db)
    for (id,) in rows:
        try:storage.file_path(root,id).unlink(missing_ok=True)
        except OSError:continue
        db.execute('DELETE FROM message_files WHERE id=?',(id,))


class MessageFiles:
    def __init__(self,messages):self.messages=messages;self.access=messages.access

    def can_upload(self,db,user,directory,thread_id):
        self.messages.recheck(db,user,directory)
        if thread_id:
            thread,_=self.messages.find(db,thread_id,user,directory,self.messages.thread_accounts(db,thread_id,user))
            if thread['kind']=='announcement' or thread['status']!='open':raise HTTPException(409,'Вложения в этой переписке сейчас недоступны.')

    def bind(self,db,ids,user,workspace,draft_id,thread_id,post_id,*,new=False):
        tables(db)
        if len(ids)!=len(set(ids)):raise HTTPException(422,'Файл указан несколько раз.')
        for id in ids:
            row=db.execute("SELECT * FROM message_files WHERE id=? AND workspace=? AND owner_id=? AND draft_id=? AND status='ready' AND post_id IS NULL",(id,workspace,user['id'],draft_id)).fetchone()
            if not row or row[4]!=(None if new else thread_id):raise HTTPException(422,'Файл не найден или относится к другому сообщению.')
            db.execute('UPDATE message_files SET target_thread=?,post_id=? WHERE id=?',(thread_id,post_id,id))

    def for_posts(self,db,posts):
        tables(db);result={id:[] for id in posts}
        if posts:
            from .notifications import encoded
            for row in db.execute("SELECT * FROM message_files WHERE status='ready' AND post_id IN (SELECT value FROM json_each(?))",(encoded(posts),)):
                result[row[10]].append(meta(row))
        return result

    def register(self,app,Workspace):
        @app.post('/api/messages/files')
        async def upload(request:Request,filename:str,draftId:str=Query(pattern=r'^[a-f0-9-]{36}$'),threadId:str|None=None,workspace:Workspace='school'):
            if not storage.UPLOAD_SLOTS.acquire(blocking=False):raise HTTPException(429,'Сервер принимает другие файлы. Повторите немного позже.')
            reservation=None;handle=None;complete=False
            def prepare():
                user,directory=self.messages.context(request,workspace)
                with self.access.lock,self.access.connect() as db:
                    self.messages.tables(db);tables(db);db.execute('BEGIN IMMEDIATE');self.can_upload(db,user,directory,threadId)
                    collect_unused(db);name=storage.name_for(filename)
                    used,count,pending=db.execute("SELECT coalesce(sum(size),0),count(CASE WHEN post_id IS NULL THEN 1 END),count(CASE WHEN status='uploading' THEN 1 END) FROM message_files WHERE workspace=? AND owner_id=?",(workspace,user['id'])).fetchone()
                    if used+storage.MAX_FILE>MAX_OWNER or storage.total_stored(db)+storage.MAX_FILE>storage.total_limit():raise HTTPException(413,'Лимит хранилища достигнут. Обратитесь к администратору.')
                    if count>=50:raise HTTPException(413,'Слишком много неотправленных вложений. Удалите ненужные файлы.')
                    if pending>=2:raise HTTPException(429,'Дождитесь окончания текущих загрузок.')
                    id=uuid4().hex;root=root_for(db)
                    db.execute('INSERT INTO message_files VALUES (?,?,?,?,?,?,?,?,?,NULL,NULL)',(id,workspace,user['id'],draftId,threadId,name,storage.MAX_FILE,now().isoformat(),'uploading'))
                return user,directory,id,name,root
            def finish(size):
                user,directory,id,name,root=reservation
                digest=storage.validate_file(storage.file_path(root,id),name)
                with self.access.lock,self.access.connect() as db:
                    db.execute('BEGIN IMMEDIATE');self.can_upload(db,user,directory,threadId)
                    db.execute("UPDATE message_files SET status='ready',size=?,sha256=? WHERE id=? AND status='uploading'",(size,digest,id))
                    row=db.execute('SELECT * FROM message_files WHERE id=?',(id,)).fetchone()
                    if not row:raise HTTPException(409,'Загрузка уже недоступна.')
                    self.access.record(db,'message.file_uploaded',workspace,detail=id)
                    return meta(row)
            try:
                try:length=int(request.headers.get('content-length','0'))
                except ValueError:raise HTTPException(422,'Некорректный размер файла.')
                if length>storage.MAX_FILE:raise HTTPException(413,'Файл должен быть не больше 10 МБ.')
                reservation=await run_in_threadpool(prepare)
                _,_,id,_,root=reservation;root.mkdir(parents=True,exist_ok=True)
                handle=storage.file_path(root,id).open('xb');size=0
                async with asyncio.timeout(90):
                    async for chunk in request.stream():
                        size+=len(chunk)
                        if size>storage.MAX_FILE:raise HTTPException(413,'Файл должен быть не больше 10 МБ.')
                        await run_in_threadpool(handle.write,chunk)
                def flush():handle.flush();os.fsync(handle.fileno());handle.close()
                await run_in_threadpool(flush);handle=None
                if not size:raise HTTPException(422,'Выберите непустой файл.')
                result=await run_in_threadpool(finish,size);complete=True;return result
            except TimeoutError:raise HTTPException(408,'Загрузка заняла слишком много времени. Попробуйте снова.') from None
            except ClientDisconnect:raise HTTPException(400,'Соединение прервано во время загрузки.') from None
            finally:
                try:
                    if handle:handle.close()
                    if reservation and not complete:
                        _,_,id,_,root=reservation
                        def discard():
                            with self.access.lock,self.access.connect() as db:
                                db.execute('BEGIN IMMEDIATE')
                                row=db.execute('SELECT status FROM message_files WHERE id=?',(id,)).fetchone()
                                if not row or row[0]!='uploading':return
                                try:storage.file_path(root,id).unlink(missing_ok=True)
                                except OSError:return
                                db.execute('DELETE FROM message_files WHERE id=?',(id,))
                        await run_in_threadpool(discard)
                finally:storage.UPLOAD_SLOTS.release()

        @app.get('/api/messages/files')
        def drafts(request:Request,draftId:str=Query(pattern=r'^[a-f0-9-]{36}$'),workspace:Workspace='school'):
            user,directory=self.messages.context(request,workspace)
            with self.access.lock,self.access.connect() as db:
                tables(db)
                rows=db.execute("SELECT * FROM message_files WHERE workspace=? AND owner_id=? AND draft_id=? AND status='ready' AND post_id IS NULL ORDER BY created,id LIMIT 50",(workspace,user['id'],draftId)).fetchall()
                return dict(items=[meta(row) for row in rows])

        @app.delete('/api/messages/files/{id}')
        def remove(id:str,request:Request,workspace:Workspace='school'):
            user,directory=self.messages.context(request,workspace)
            with self.access.lock,self.access.connect() as db:
                tables(db);db.execute('BEGIN IMMEDIATE');self.messages.recheck(db,user,directory)
                row=db.execute('SELECT status,post_id FROM message_files WHERE id=? AND workspace=? AND owner_id=?',(id,workspace,user['id'])).fetchone()
                if not row:raise HTTPException(404,'Файл не найден.')
                if row[0]!='ready' or row[1] is not None:raise HTTPException(409,'Можно удалить только своё неотправленное вложение.')
                from .message_drafts import protects_file
                if protects_file(db,id):raise HTTPException(409,'Файл сохранён в черновике. Снимите его выбор и сохраните черновик перед удалением.')
                storage.file_path(root_for(db),id).unlink(missing_ok=True)
                db.execute('DELETE FROM message_files WHERE id=?',(id,))
            return dict(ok=True)

        @app.get('/api/messages/files/{id}')
        def download(id:str,request:Request,workspace:Workspace='school'):
            user,directory=self.messages.context(request,workspace)
            with self.access.lock,self.access.connect() as db:
                self.messages.tables(db);tables(db)
                row=db.execute("SELECT * FROM message_files WHERE id=? AND workspace=? AND status='ready'",(id,workspace)).fetchone()
                if not row:raise HTTPException(404,'Файл не найден.')
                if row[10] is None:
                    if row[2]!=user['id']:raise HTTPException(404,'Файл не найден.')
                else:
                    self.messages.find(db,row[4],user,directory,self.messages.thread_accounts(db,row[4],user))
                    if self.messages.moderation.state(db,row[10])['hidden']:raise HTTPException(404,'Сообщение с этим файлом скрыто.')
                path=storage.file_path(root_for(db),id)
                try:stat=path.stat()
                except OSError:raise HTTPException(404,'Файл недоступен в хранилище.') from None
            return FileResponse(path,stat_result=stat,media_type='application/octet-stream',headers={
                'Content-Disposition':"attachment; filename*=UTF-8''"+quote(row[5],safe=''),'Content-Length':str(row[6]),
                'Cache-Control':'no-store','X-Content-Type-Options':'nosniff','Content-Security-Policy':"sandbox; default-src 'none'"})
