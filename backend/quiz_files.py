"""Immutable private question files; downloads use the quiz's existing projections."""
import asyncio
import os
from pathlib import Path
from typing import Literal
from uuid import uuid4

from fastapi import HTTPException, Request
from starlette.concurrency import run_in_threadpool
from starlette.requests import ClientDisconnect
from .access import now
from . import homework_files as files, teaching_media as media


def tables(db):
    db.execute('''CREATE TABLE IF NOT EXISTS quiz_files (
        id TEXT PRIMARY KEY,workspace TEXT,owner_id TEXT,filename TEXT,size INTEGER,created TEXT,status TEXT,sha256 TEXT)''')
    db.execute('CREATE INDEX IF NOT EXISTS quiz_files_owner ON quiz_files(owner_id,status)')
    db.execute('CREATE INDEX IF NOT EXISTS quiz_files_created ON quiz_files(status,created)')
    db.execute('''CREATE TABLE IF NOT EXISTS quiz_file_links (
        file_id TEXT,workspace TEXT,question_id TEXT,version INTEGER,
        PRIMARY KEY(file_id,workspace,question_id,version))''')


def root_for(db):
    return Path(db.execute('PRAGMA database_list').fetchone()[2]).resolve().with_suffix('.files')/'quizzes'


def meta(row):
    return dict(id=row[0],filename=row[3],size=row[4],created=row[5])


def bank_access(db,row,user,workspace):
    if row[2]==user['id']:return True
    # Copying an accessible bank question preserves its immutable file bytes.
    query='''SELECT 1 FROM quiz_file_links l JOIN quiz_questions q
        ON q.workspace=l.workspace AND q.id=l.question_id
        WHERE l.file_id=? AND l.workspace=?'''
    args=[row[0],workspace]
    if user['role']!='admin':query+=' AND q.owner_id=?';args.append(user['id'])
    return bool(db.execute(query+' LIMIT 1',args).fetchone())


def bind_question(db,workspace,question,version,user,document):
    for field,projection in [('fileIds','attachments'),('solutionFileIds','solutionAttachments')]:
        document[projection]=[]
        for id in document[field]:
            row=db.execute("SELECT * FROM quiz_files WHERE id=? AND workspace=? AND status='ready'",(id,workspace)).fetchone()
            if not row or not bank_access(db,row,user,workspace):
                raise HTTPException(422,'Файл недоступен. Загрузите его или выберите из своего банка вопросов.')
            document[projection].append(meta(row))
            db.execute('INSERT INTO quiz_file_links VALUES (?,?,?,?)',(id,workspace,question,version))


def unused_rows(db,limit=20,days=1):
    stale,old=files.cutoffs(days)
    return [(r[0],r[1]) for r in db.execute('''SELECT f.id,f.size FROM quiz_files f WHERE
        ((f.status='uploading' AND f.created<?) OR (f.status='ready' AND f.created<?))
        AND NOT EXISTS(SELECT 1 FROM quiz_file_links l WHERE l.file_id=f.id)
        ORDER BY f.created LIMIT ?''',(stale,old,limit))]


def collect_unused(db):
    root=root_for(db)
    for id,_ in unused_rows(db):
        try:files.release_row(db,'quiz_files',root,id)
        except OSError:continue


def reserve(db,workspace,user,filename):
    name=media.name_for(filename)
    size=db.execute('SELECT coalesce(sum(size),0) FROM quiz_files WHERE owner_id=?',(user['id'],)).fetchone()[0]
    pending=db.execute("SELECT count(*) FROM quiz_files WHERE owner_id=? AND status='uploading'",(user['id'],)).fetchone()[0]
    if size+files.MAX_FILE>250*1024*1024:raise HTTPException(413,'Лимит хранилища достигнут. Обратитесь к администратору.')
    files.require_capacity(db,'quizzes')
    if pending>=2:raise files.busy('Дождитесь окончания текущих загрузок.')
    id=uuid4().hex
    db.execute('INSERT INTO quiz_files VALUES (?,?,?,?,?,?,?,NULL)',(id,workspace,user['id'],name,files.MAX_FILE,now().isoformat(),'uploading'))
    return id,name,root_for(db)


# Bank packages touch storage quotas only through these functions.
def collect_own_stale(db,user):
    """A crash can strand up to 200 reservations, but collect_unused frees only 20 school-wide per call
    and a later 429 would roll even that back: the caller's own abandoned rows are freed at once."""
    root=root_for(db)
    for (id,) in db.execute("SELECT id FROM quiz_files WHERE owner_id=? AND status='uploading' AND created<?",(user['id'],files.cutoffs()[0])).fetchall():
        try:files.release_row(db,'quiz_files',root,id)
        except OSError:continue


def package_quota(db,user,total):
    own=db.execute('SELECT coalesce(sum(size),0) FROM quiz_files WHERE owner_id=?',(user['id'],)).fetchone()[0]
    if own+total>250*1024*1024:
        mb=f'{total/1048576:.1f}'.replace('.',',')
        raise HTTPException(413,f'Лимит хранилища достигнут: вложения пакета занимают {mb} МБ. Обратитесь к администратору.')
    # The section budget, the school-wide cap and the free-disk reserve apply to a package as to any upload.
    files.require_capacity(db,'quizzes',total)


def reserve_package(db,workspace,user,package_files):
    collect_own_stale(db,user)
    package_quota(db,user,sum(f.size for f in package_files))
    pending=db.execute("SELECT count(*) FROM quiz_files WHERE owner_id=? AND status='uploading'",(user['id'],)).fetchone()[0]
    if pending>=2:raise files.busy('Дождитесь окончания текущих загрузок.')
    reserved={}
    for file in package_files:
        reserved[file.id]=id=uuid4().hex
        db.execute('INSERT INTO quiz_files VALUES (?,?,?,?,?,?,?,NULL)',(id,workspace,user['id'],file.filename,file.size,now().isoformat(),'uploading'))
    return reserved,root_for(db)


def release_reserved(db,ids):
    root=root_for(db)
    for id in ids:
        row=db.execute('SELECT status FROM quiz_files WHERE id=?',(id,)).fetchone()
        # The ids are ours alone: a vanished row still leaves its bytes to remove, a ready one is committed.
        if row and row[0]!='uploading':continue
        try:files.release_row(db,'quiz_files',root,id)
        except OSError:continue  # The row and its quota stay until collect_unused succeeds later.


class QuizFiles:
    def __init__(self,quizzes):
        self.quizzes,self.access=quizzes,quizzes.access

    def register(self,app,Workspace):
        @app.post('/api/journal/quiz-files')
        async def upload(request:Request,filename:str,workspace:Workspace='school'):
            slots=await files.acquire_slot()
            id=None;path=None;stream=None;finished=False
            try:
                user=self.quizzes.journal.scope(request,workspace,staff=True)
                def prepare():
                    with self.access.lock,self.access.connect() as db:
                        self.quizzes.tables(db);db.execute('BEGIN IMMEDIATE');self.quizzes.journal.recheck_account(db,user)
                        collect_unused(db);collect_own_stale(db,user)
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
                        db.execute('BEGIN IMMEDIATE');self.quizzes.journal.recheck_account(db,user)
                        db.execute("UPDATE quiz_files SET status='ready',size=?,sha256=? WHERE id=?",(size,digest,id))
                        self.access.record(db,'quiz.file_uploaded',workspace,detail=id)
                        return meta(db.execute('SELECT * FROM quiz_files WHERE id=?',(id,)).fetchone())
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
                                row=db.execute("SELECT 1 FROM quiz_files WHERE id=? AND status='uploading'",(id,)).fetchone()
                                if row:
                                    if path: path.unlink(missing_ok=True)
                                    db.execute('DELETE FROM quiz_files WHERE id=?',(id,))
                        await run_in_threadpool(cleanup)
                finally: slots.release()

        @app.get('/api/journal/quiz-files/{id}')
        def download(id:str,request:Request,workspace:Workspace='school',quizId:str|None=None,
                     attemptId:str|None=None,questionId:str|None=None,
                     part:Literal['attachments','solutionAttachments']='attachments',download:bool=False):
            quizzes=self.quizzes
            ctx=quizzes.context(request,workspace,quizId,attempt_id=attemptId) if quizId else None
            user=ctx['user'] if ctx else quizzes.journal.scope(request,workspace,staff=True)
            def read(db):
                quizzes.journal.recheck_account(db,user)
                row=db.execute("SELECT * FROM quiz_files WHERE id=? AND workspace=? AND status='ready'",(id,workspace)).fetchone()
                if not row:raise HTTPException(404,'Файл недоступен.')
                if ctx:
                    quizzes.recheck(db,ctx)
                    if not ctx['staff']:
                        current=ctx['current']
                        if not current or current['status']=='cancelled' or ctx['student'] not in current['studentIds']:
                            raise HTTPException(403,'Нет действующего доступа к материалам этого урока.')
                    if attemptId:
                        from .quiz_attempts import load
                        view=quizzes.attempt_view(ctx,load(db,workspace,quizId,attemptId))
                        questions=(view.get('questions',[]) if part=='attachments' else
                                   [dict(q['question'],id=q['id']) for q in view.get('solutions',[])])
                    elif ctx['staff']:
                        document=ctx['quiz']['published'] or ctx['quiz']['document']
                        questions=[dict(q['question'],id=q['id']) for v in document['definition']['variants'] for q in v['questions']]
                    else:raise HTTPException(404,'Сначала начните назначенную попытку.')
                    if not any(q['id']==questionId and any(f['id']==id for f in q.get(part,[])) for q in questions):
                        raise HTTPException(404,'Файл недоступен в этом вопросе или разборе.')
                elif attemptId or questionId or not bank_access(db,row,user,workspace):
                    raise HTTPException(404,'Файл недоступен.')
                return files.file_path(root_for(db),id),row[3]
            path,name=quizzes.read_only(read)
            if not path.is_file():raise HTTPException(404,'Файл отсутствует в хранилище.')
            return media.response(path,name,download=download,inline_images=True)
