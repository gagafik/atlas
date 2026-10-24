"""Bounded photo uploads. Store only decoded, resized pixels without metadata."""
import asyncio,hashlib,io,os,warnings
from pathlib import Path
from uuid import uuid4
from fastapi import HTTPException,Query,Request
from fastapi.responses import FileResponse
from PIL import Image,ImageOps,UnidentifiedImageError
from starlette.concurrency import run_in_threadpool
from starlette.requests import ClientDisconnect
from . import homework_files as storage
from .access import now
from .lost_found import version

def root_for(db):return Path(db.execute('PRAGMA database_list').fetchone()[2]).resolve().with_suffix('.files')/'lost-found'
def meta(r):return dict(id=r['id'],filename=r['filename'],size=r['size'],created=r['created'],linked=bool(r['linked']))
def unused_rows(db,limit=20,days=1):
    stale,old=storage.cutoffs(days)
    return [(r[0],r[1]) for r in db.execute("SELECT id,size FROM lost_found_files WHERE linked=0 AND ((status='uploading' AND created<?) OR (status='ready' AND created<?)) ORDER BY created LIMIT ?",(stale,old,limit))]
def collect_unused(db):
    root=root_for(db)
    for id,_ in unused_rows(db):
        try:storage.release_row(db,'lost_found_files',root,id)
        except OSError:continue

def sanitize(path):
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error',Image.DecompressionBombWarning)
            with Image.open(path) as original:
                if original.format not in ['JPEG','PNG'] or original.width*original.height>20_000_000:raise HTTPException(422,'Выберите JPG или PNG не более 20 мегапикселей.')
                original.load()
                rotated=ImageOps.exif_transpose(original);rotated.thumbnail((1600,1600))
                pixels=Image.new('RGB',rotated.size,'white')
                if 'A' in rotated.getbands():pixels.paste(rotated,mask=rotated.getchannel('A'))
                else:pixels.paste(rotated.convert('RGB'))
                output=io.BytesIO();pixels.save(output,format='JPEG',quality=85,optimize=True)
                payload=output.getvalue();pixels.close();rotated.close()
        if len(payload)>storage.MAX_FILE:raise HTTPException(413,'Фото слишком большое после обработки.')
        with path.open('wb') as file:file.write(payload);file.flush();os.fsync(file.fileno())
        return len(payload),hashlib.sha256(payload).hexdigest()
    except (UnidentifiedImageError,OSError,ValueError,Image.DecompressionBombError,Image.DecompressionBombWarning):raise HTTPException(422,'Не удалось прочитать фото. Выберите исправный JPG или PNG.') from None

def register(s,app,Workspace):
    def writable(db,u,workspace,item_id,expected):
        s.require_manager(db,u,workspace);item=s.item(db,workspace,item_id);version(item,expected)
        if item['status'] not in ['draft','open']:raise HTTPException(409,'Фото можно менять только в черновике или открытом объявлении.')

    @app.post('/api/lost-found/items/{id}/photos')
    async def upload(id:str,request:Request,itemVersion:int=Query(ge=1),workspace:Workspace='school'):
        slots=await storage.acquire_slot()
        reservation=None;handle=None;complete=False
        def prepare():
            with s.context(request,workspace,True) as (db,u,snap):
                writable(db,u,workspace,id,itemVersion);collect_unused(db)
                used,pending=db.execute("SELECT coalesce(sum(size),0),count(CASE WHEN status='uploading' THEN 1 END) FROM lost_found_files WHERE owner_id=?",(u['id'],)).fetchone()
                if used+storage.MAX_FILE>250*1024*1024:raise HTTPException(413,'Лимит хранилища достигнут.')
                storage.require_capacity(db,'lost-found')
                if pending>=2:raise storage.busy('Дождитесь окончания текущих загрузок.')
                if db.execute('SELECT count(*) FROM lost_found_files WHERE item_id=?',(id,)).fetchone()[0]>=20:raise HTTPException(413,'Достигнут предел 20 фото с учётом истории. Удалите неиспользованные загрузки.')
                file_id=uuid4().hex;root=root_for(db)
                db.execute('INSERT INTO lost_found_files VALUES (?,?,?,?,?,?,?,?,NULL,0)',(file_id,workspace,id,u['id'],'finding-photo.jpg',storage.MAX_FILE,now().isoformat(),'uploading'))
                return file_id,root
        def finish():
            file_id,root=reservation;size,digest=sanitize(storage.file_path(root,file_id))
            with s.context(request,workspace,True) as (db,u,snap):
                writable(db,u,workspace,id,itemVersion)
                changed=db.execute("UPDATE lost_found_files SET size=?,sha256=?,status='ready' WHERE id=? AND status='uploading'",(size,digest,file_id)).rowcount
                if not changed:raise HTTPException(409,'Загрузка уже недоступна.')
                return meta(db.execute('SELECT * FROM lost_found_files WHERE id=?',(file_id,)).fetchone())
        try:
            try:length=int(request.headers.get('content-length','0'))
            except ValueError:raise HTTPException(422,'Некорректный размер фото.')
            if length>storage.MAX_FILE:raise HTTPException(413,'Фото должно быть не больше 10 МБ.')
            reservation=await run_in_threadpool(prepare);file_id,root=reservation;root.mkdir(parents=True,exist_ok=True);handle=storage.file_path(root,file_id).open('xb');size=0
            async with asyncio.timeout(90):
                async for chunk in request.stream():
                    size+=len(chunk)
                    if size>storage.MAX_FILE:raise HTTPException(413,'Фото должно быть не больше 10 МБ.')
                    await run_in_threadpool(handle.write,chunk)
            def flush():handle.flush();os.fsync(handle.fileno());handle.close()
            await run_in_threadpool(flush);handle=None
            if not size:raise HTTPException(422,'Выберите непустое фото.')
            result=await run_in_threadpool(finish);complete=True;return result
        except TimeoutError:raise HTTPException(408,'Загрузка заняла слишком много времени.') from None
        except ClientDisconnect:raise HTTPException(400,'Соединение прервано при загрузке.') from None
        finally:
            try:
                if handle:handle.close()
                if reservation and not complete:
                    def discard():
                        file_id,root=reservation
                        with s.access.lock,s.access.connect() as db:
                            db.execute('BEGIN IMMEDIATE')
                            row=db.execute('SELECT status FROM lost_found_files WHERE id=?',(file_id,)).fetchone()
                            if not row or row[0]!='uploading':return
                            try:storage.file_path(root,file_id).unlink(missing_ok=True)
                            except OSError:return
                            db.execute('DELETE FROM lost_found_files WHERE id=?',(file_id,))
                    await run_in_threadpool(discard)
            finally:slots.release()

    @app.get('/api/lost-found/items/{id}/photos')
    def drafts(id:str,request:Request,workspace:Workspace='school'):
        with s.context(request,workspace) as (db,u,snap):
            s.require_manager(db,u,workspace);s.item(db,workspace,id)
            rows=db.execute("SELECT * FROM lost_found_files WHERE workspace=? AND item_id=? AND status='ready' AND (linked=1 OR owner_id=?) ORDER BY created,id",(workspace,id,u['id'])).fetchall()
            return dict(items=[meta(r) for r in rows])

    @app.get('/api/lost-found/photos/{id}')
    def photo(id:str,request:Request,workspace:Workspace='school'):
        with s.context(request,workspace) as (db,u,snap):
            r=db.execute("SELECT * FROM lost_found_files WHERE id=? AND workspace=? AND status='ready'",(id,workspace)).fetchone()
            if not r:raise HTTPException(404,'Фото недоступно.')
            item=s.item(db,workspace,r['item_id']);manager=s.manager(db,u,workspace)
            if manager:
                if not r['linked'] and r['owner_id']!=u['id']:raise HTTPException(404,'Фото недоступно.')
            elif item['status'] not in ['open','reserved'] or id not in item['data']['photoIds']:raise HTTPException(404,'Фото недоступно.')
            path=storage.file_path(root_for(db),id)
            if not path.is_file():raise HTTPException(404,'Фото недоступно.')
            return FileResponse(path,media_type='image/jpeg',filename='finding-photo.jpg',content_disposition_type='inline',headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff','Content-Security-Policy':"default-src 'none'; sandbox"})

    @app.delete('/api/lost-found/photos/{id}')
    def remove(id:str,request:Request,workspace:Workspace='school'):
        with s.context(request,workspace,True) as (db,u,snap):
            s.require_manager(db,u,workspace)
            r=db.execute('SELECT * FROM lost_found_files WHERE workspace=? AND id=? AND owner_id=?',(workspace,id,u['id'])).fetchone()
            if not r:raise HTTPException(404,'Фото недоступно.')
            if r['linked'] or r['status']!='ready':raise HTTPException(409,'Можно удалить только свою неиспользованную загрузку. История сохраняется.')
            storage.file_path(root_for(db),id).unlink(missing_ok=True);db.execute('DELETE FROM lost_found_files WHERE id=?',(id,))
            return dict(ok=True)
