"""Bank packages: template, export and import with preview; never extracts archive paths."""
import asyncio
from collections import Counter
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import time
from uuid import uuid4

from fastapi import HTTPException, Query, Request, Response
from fastapi.responses import FileResponse
from pydantic import ValidationError
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool
from starlette.requests import ClientDisconnect

from . import quiz_bank_package as pkg, quiz_files, homework_files as files
from .access import now
from .notifications import encoded

HEADERS={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'}


def temp_folder(root):
    """Per-request folder on the data volume; abandoned ones (client gone) are swept after an hour."""
    base=root.parent/'quiz-bank-tmp';base.mkdir(parents=True,exist_ok=True)
    limit=time.time()-3600
    for child in base.iterdir():
        try:
            if child.stat().st_mtime<limit:shutil.rmtree(child,ignore_errors=True)
        except OSError:continue
    return Path(tempfile.mkdtemp(dir=base))


def kind_for(filename):
    lower=filename.lower()
    if lower.endswith('.json'):return 'json'
    if lower.endswith('.zip'):return 'zip'
    raise HTTPException(422,'Выберите ZIP-пакет Атласа или JSON по шаблону.')


def limit_for(kind):
    return (pkg.MAX_MANIFEST,'JSON должен быть не больше 2 МБ.') if kind=='json' else (pkg.MAX_ARCHIVE,'ZIP-пакет должен быть не больше 55 МБ.')


async def receive(request,path,limit,message):
    try:length=int(request.headers.get('content-length','0'))
    except ValueError:raise HTTPException(422,'Некорректный размер файла.') from None
    if length>limit:raise HTTPException(413,message)
    stream=await run_in_threadpool(path.open,'xb');size=0;digest=hashlib.sha256()
    try:
        async with asyncio.timeout(300):
            async for chunk in request.stream():
                size+=len(chunk)
                if size>limit:raise HTTPException(413,message)
                digest.update(chunk);await run_in_threadpool(stream.write,chunk)
        if not size:raise HTTPException(422,'Нельзя загрузить пустой файл.')
        await run_in_threadpool(stream.flush)
    finally:await run_in_threadpool(stream.close)
    return digest.hexdigest(),size


def register(app,quizzes,Workspace):
    @app.get('/api/journal/quiz-bank/template')
    def template(request:Request,workspace:Workspace='school'):
        quizzes.journal.scope(request,workspace,staff=True)
        return Response(pkg.template_bytes(),media_type='application/json',
                        headers=HEADERS|{'Content-Disposition':'attachment; filename="atlas-quiz-bank-template.json"'})

    @app.post('/api/journal/quiz-bank/export')
    def export(body:pkg.Selection,request:Request,workspace:Workspace='school'):
        user=quizzes.journal.scope(request,workspace,staff=True)
        def read(db):
            quizzes.journal.recheck_account(db,user)
            rows=[]
            for ref in body.items:
                row=db.execute('SELECT version,owner_id,subject,archived,document FROM quiz_questions WHERE workspace=? AND id=?',(workspace,ref.id)).fetchone()
                if not row or user['role']!='admin' and row[1]!=user['id']:raise HTTPException(403,'Вопрос отсутствует в доступном банке.')
                if row[0]!=ref.version:raise HTTPException(409,'Вопрос в банке изменился. Обновите банк.')
                rows.append((row[2],bool(row[3]),json.loads(row[4])))
            ids=list(dict.fromkeys(id for _,_,doc in rows for id in doc['question'].get('fileIds',[])+doc['question'].get('solutionFileIds',[])))
            stored={}
            for start in range(0,len(ids),400):
                chunk=ids[start:start+400]
                for r in db.execute(f"SELECT id,filename,size,sha256 FROM quiz_files WHERE workspace=? AND status='ready' AND id IN ({','.join('?'*len(chunk))})",(workspace,*chunk)):stored[r[0]]=r
            if len(stored)!=len(ids) or any(r[3] is None for r in stored.values()):
                raise HTTPException(409,'Вложение отсутствует в хранилище. Обратитесь к администратору.')
            return rows,[stored[id] for id in ids],quiz_files.root_for(db)
        rows,stored,root=quizzes.read_only(read)
        if len(stored)>pkg.MAX_FILES:raise HTTPException(413,f'Выбранные вопросы содержат больше {pkg.MAX_FILES} файлов. Уменьшите выборку.')
        if sum(r[2] for r in stored)>pkg.MAX_TOTAL:raise HTTPException(413,'Вложения выбранных вопросов занимают больше 50 МБ. Уменьшите выборку.')
        # Internal storage ids never leave the system: every file gets a fresh package id.
        alias={r[0]:uuid4().hex for r in stored}
        try:
            entries=[]
            for subject,archived,doc in rows:
                question={k:v for k,v in doc['question'].items() if k not in ('attachments','solutionAttachments')}
                for field in ('fileIds','solutionFileIds'):question[field]=[alias[id] for id in question.get(field,[])]
                entries.append(pkg.Entry(subject=subject,tags=doc.get('tags',[]),archived=archived,question=question))
            package=pkg.Package(format='atlas.quiz-bank',version=1,questions=entries,
                                files=[pkg.PackageFile(id=alias[r[0]],filename=r[1],size=r[2],sha256=r[3]) for r in stored])
        except ValidationError:raise HTTPException(409,'Вопрос в банке повреждён. Обратитесь к администратору.') from None
        name=f'atlas-quiz-bank-{now().date().isoformat()}'
        if not stored:
            return Response(pkg.manifest_bytes(package,pretty=True),media_type='application/json',
                            headers=HEADERS|{'Content-Disposition':f'attachment; filename="{name}.json"'})
        if not files.UPLOAD_SLOTS.acquire(blocking=False):raise HTTPException(429,'Слишком много загрузок. Попробуйте чуть позже.')
        try:
            folder=temp_folder(root)
            try:pkg.write_package(folder/'package.zip',package,{alias[r[0]]:files.file_path(root,r[0]) for r in stored})
            except BaseException:
                shutil.rmtree(folder,ignore_errors=True);raise
        finally:files.UPLOAD_SLOTS.release()
        return FileResponse(folder/'package.zip',media_type='application/zip',filename=name+'.zip',headers=HEADERS,
                            background=BackgroundTask(shutil.rmtree,folder,True))

    @app.post('/api/journal/quiz-bank/import/preview')
    async def preview(request:Request,filename:str=Query(min_length=1,max_length=180),workspace:Workspace='school'):
        kind=kind_for(filename)
        if not files.UPLOAD_SLOTS.acquire(blocking=False):raise HTTPException(429,'Слишком много загрузок. Попробуйте чуть позже.')
        folder=None
        try:
            user=quizzes.journal.scope(request,workspace,staff=True)
            root=await run_in_threadpool(quizzes.read_only,quiz_files.root_for)
            folder=await run_in_threadpool(temp_folder,root);path=folder/'package'
            digest,size=await receive(request,path,*limit_for(kind))
            package=await run_in_threadpool(pkg.inspect_package,path,filename)
            total=sum(f.size for f in package.files)
            if package.files:
                def check(db):
                    quizzes.journal.recheck_account(db,user);quiz_files.package_quota(db,user,total)
                await run_in_threadpool(quizzes.read_only,check)
            return dict(packageHash=digest,format=kind,size=size,questions=len(package.questions),archived=sum(q.archived for q in package.questions),
                        files=len(package.files),totalSize=total,subjects=sorted({q.subject for q in package.questions if q.subject})[:20],
                        kinds=dict(Counter(q.question.kind for q in package.questions)),
                        items=[dict(subject=q.subject,tags=q.tags,kind=q.question.kind,text=q.question.text[:200],points=q.question.points,
                                    files=len(q.question.fileIds)+len(q.question.solutionFileIds),archived=q.archived) for q in package.questions])
        except TimeoutError:raise HTTPException(408,'Загрузка прервана по времени.') from None
        except ClientDisconnect:raise HTTPException(400,'Загрузка прервана.') from None
        finally:
            try:
                if folder:await run_in_threadpool(shutil.rmtree,folder,True)
            finally:files.UPLOAD_SLOTS.release()

    @app.post('/api/journal/quiz-bank/import')
    async def apply(request:Request,filename:str=Query(min_length=1,max_length=180),requestId:str=Query(pattern=r'^[a-f0-9-]{32,36}$'),
                    packageHash:str=Query(pattern=r'^[a-f0-9]{64}$'),workspace:Workspace='school'):
        kind=kind_for(filename)
        if not files.UPLOAD_SLOTS.acquire(blocking=False):raise HTTPException(429,'Слишком много загрузок. Попробуйте чуть позже.')
        folder=None;reserved={};committed=False
        try:
            user=quizzes.journal.scope(request,workspace,staff=True)
            root=await run_in_threadpool(quizzes.read_only,quiz_files.root_for)
            folder=await run_in_threadpool(temp_folder,root);path=folder/'package'
            digest,_=await receive(request,path,*limit_for(kind))
            if digest!=packageHash:raise HTTPException(409,'Файл отличается от проверенного. Выполните предпросмотр заново.')
            # The preview is never trusted: the file is inspected again.
            package=await run_in_threadpool(pkg.inspect_package,path,filename)

            def replay(db):
                row=db.execute('SELECT package_hash,result FROM quiz_bank_imports WHERE workspace=? AND owner_id=? AND request_id=?',(workspace,user['id'],requestId)).fetchone()
                if not row:return None
                if row[0]!=packageHash:raise HTTPException(409,'Этот запрос импорта уже выполнен для другого пакета. Выберите файл заново.')
                return json.loads(row[1])

            def prepare():
                with quizzes.access.lock,quizzes.access.connect() as db:
                    quizzes.tables(db);db.execute('BEGIN IMMEDIATE');quizzes.journal.recheck_account(db,user)
                    old=replay(db)
                    if old is not None or not package.files:return old,{},None
                    quiz_files.collect_unused(db)
                    return (None,*quiz_files.reserve_package(db,workspace,user,package.files))

            def commit():
                with quizzes.access.lock,quizzes.access.connect() as db:
                    db.execute('BEGIN IMMEDIATE');quizzes.journal.recheck_account(db,user)
                    old=replay(db)
                    if old is not None:return old,False
                    if reserved:
                        ours=db.execute(f"SELECT count(*) FROM quiz_files WHERE owner_id=? AND status='uploading' AND id IN ({','.join('?'*len(reserved))})",(user['id'],*reserved.values())).fetchone()[0]
                        if ours!=len(reserved):raise HTTPException(409,'Импорт прерван. Повторите попытку.')
                        for file in package.files:
                            db.execute("UPDATE quiz_files SET status='ready',sha256=? WHERE id=?",(file.sha256,reserved[file.id]))
                    stamp=now().isoformat();ids=[]
                    for entry in package.questions:
                        question=entry.question.model_dump(mode='json',exclude={'attachments','solutionAttachments'})
                        for field in ('fileIds','solutionFileIds'):question[field]=[reserved[id] for id in question[field]]
                        id=str(uuid4());document=dict(tags=entry.tags,question=question)
                        quiz_files.bind_question(db,workspace,id,1,user,document['question'])
                        db.execute('INSERT INTO quiz_questions VALUES (?,?,?,?,?,?,?,?)',(workspace,id,1,user['id'],stamp,entry.subject,int(entry.archived),encoded(document)))
                        ids.append(id)
                    result=dict(imported=len(ids),archived=sum(q.archived for q in package.questions),files=len(package.files),questionIds=ids)
                    db.execute('INSERT INTO quiz_bank_imports VALUES (?,?,?,?,?,?)',(workspace,user['id'],requestId,packageHash,stamp,encoded(result)))
                    quizzes.access.record(db,'quiz.bank_imported',workspace,detail=f"{result['imported']} вопр. · {result['files']} файлов")
                    return result,True

            old,reserved,storage=await run_in_threadpool(prepare)
            if old is not None:return old
            if reserved:
                await run_in_threadpool(lambda:storage.mkdir(parents=True,exist_ok=True))
                await run_in_threadpool(pkg.extract_files,path,package,{id:files.file_path(storage,reserved[id]) for id in reserved})
            result,committed=await run_in_threadpool(commit)
            return result
        except TimeoutError:raise HTTPException(408,'Загрузка прервана по времени.') from None
        except ClientDisconnect:raise HTTPException(400,'Загрузка прервана.') from None
        finally:
            try:
                if reserved and not committed:
                    def release():
                        with quizzes.access.lock,quizzes.access.connect() as db:
                            db.execute('BEGIN IMMEDIATE');quiz_files.release_reserved(db,list(reserved.values()))
                    await run_in_threadpool(release)
            finally:
                try:
                    if folder:await run_in_threadpool(shutil.rmtree,folder,True)
                finally:files.UPLOAD_SLOTS.release()
