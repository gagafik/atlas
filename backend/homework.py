"""Private pupil drafts, versioned submissions and teacher review of published homework."""
import asyncio
from datetime import date
import hashlib
import json
import os
from typing import Literal
from urllib.parse import quote
from zoneinfo import ZoneInfo

from fastapi import HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import Field
from starlette.concurrency import run_in_threadpool
from starlette.requests import ClientDisconnect

from .access import now
from .models import Model
from .journal import staff_read, user_ids
from .portal_cache import PortalSnapshots
from .notifications import encoded
from . import homework_files as files


class Answer(Model):
    studentId: str = Field(min_length=1,max_length=100)
    version: int = Field(ge=0)
    assignment: str = Field(pattern=r'^[a-f0-9]{64}$')
    action: Literal['draft','submit','withdraw']
    text: str = Field(default='',max_length=10000)
    fileIds: list[str] = Field(default_factory=list,max_length=5)


class Review(Model):
    studentId: str = Field(min_length=1,max_length=100)
    version: int = Field(ge=1)
    assignment: str = Field(pattern=r'^[a-f0-9]{64}$')
    decision: Literal['accepted','returned']
    comment: str = Field(default='',max_length=4000)


def fingerprint(document):
    content=document['content']
    return hashlib.sha256(encoded([content['homework'],content['dueDate']]).encode()).hexdigest()


def status(document):
    sent=document.get('submitted')
    return sent['review']['decision'] if sent and sent.get('review') else 'submitted' if sent else 'not_submitted'


class Homework:
    def __init__(self, journal, stamp):
        self.journal=journal
        self.access=journal.access
        self.snapshots=PortalSnapshots(self.access.read,stamp)
        self.upload_slots=files.UPLOAD_SLOTS

    def tables(self,db):
        self.journal.tables(db);self.access.tables(db)
        db.execute('''CREATE TABLE IF NOT EXISTS homework_submissions (
            workspace TEXT,lesson_key TEXT,student_id TEXT,version INTEGER,document TEXT,
            PRIMARY KEY(workspace,lesson_key,student_id))''')
        db.execute('''CREATE TABLE IF NOT EXISTS homework_history (
            id INTEGER PRIMARY KEY,workspace TEXT,lesson_key TEXT,student_id TEXT,version INTEGER,
            created TEXT,actor_name TEXT,action TEXT,document TEXT)''')
        db.execute('CREATE INDEX IF NOT EXISTS homework_history_target ON homework_history(workspace,lesson_key,student_id,id)')
        files.tables(db)

    def context(self, request, workspace, key, student=None):
        user=self.journal.scope(request,workspace)
        staff=user['role'] in ['admin','teacher']
        if not staff and (student is None or student not in user_ids(user,workspace,'student')):
            raise HTTPException(403,'Нет доступа к работе этого ученика.')
        snapshot=self.snapshots.get(workspace)
        state=snapshot.state
        record=self.journal.find(workspace,key)
        if not record: raise HTTPException(404,'Домашнее задание не найдено.')
        published=record['published']
        current=snapshot.current_lesson(record['document']['lesson'])
        if staff and not (staff_read(user,workspace,record['document']['lesson']) or current and staff_read(user,workspace,current)):
            raise HTTPException(403,'Нет доступа к работам этого урока.')
        if not published or not published['content']['homework']:
            raise HTTPException(404,'Сначала опубликуйте домашнее задание в журнале.')
        roster={s['id']:s for s in published['lesson']['students']}
        if student is not None and student not in roster:
            raise HTTPException(404,'Ученик не включён в опубликованное задание.')
        current_ids=set(current['studentIds']) if current and current['status']!='cancelled' else set()
        return dict(user=user,staff=staff,state=state,record=record,published=published,roster=roster,current=current,
                    revision=state.revision,workspace=workspace,key=key,student=student,assignment=fingerprint(published),
                    canReview=bool(staff and current and current['status']!='cancelled' and staff_read(user,workspace,current)),
                    canSubmit=bool(user['role']=='student' and student in current_ids and roster[student].get('enrolled',True)))

    def recheck(self, db, context):
        self.journal.recheck_account(db,context['user'])
        row=db.execute('SELECT revision FROM workspaces WHERE id=?',(context['workspace'],)).fetchone()
        publication=db.execute('SELECT published_document FROM journal_entries WHERE workspace=? AND lesson_key=?',
                               (context['workspace'],context['key'])).fetchone()
        if not row or row[0]!=context['revision'] or not publication or not publication[0]:
            raise HTTPException(409,'Урок или доступ изменился. Откройте задание заново.')
        document=json.loads(publication[0])
        if fingerprint(document)!=context['assignment'] or {s['id'] for s in document['lesson']['students']}!=set(context['roster']):
            raise HTTPException(409,'Опубликованное задание изменилось. Откройте его заново.')

    def load(self,db,ctx,student):
        row=db.execute('SELECT version,document FROM homework_submissions WHERE workspace=? AND lesson_key=? AND student_id=?',
                       (ctx['workspace'],ctx['key'],student)).fetchone()
        return (row[0],json.loads(row[1])) if row else (0,dict(draft=dict(text='',fileIds=[]),submitted=None,attempts=0))

    def editable(self,ctx,document):
        sent=document['submitted']
        return ctx['canSubmit'] and (sent is None or status(document)=='returned' or sent['assignment']!=ctx['assignment'])

    def attachment_list(self,db,ids):
        return [files.meta(row) for id in ids if (row:=db.execute("SELECT * FROM homework_files WHERE id=? AND status='ready'",(id,)).fetchone())]

    def sent_projection(self,db,sent):
        if not sent: return None
        return {k:v for k,v in sent.items() if k!='fileIds'} | dict(files=self.attachment_list(db,sent['fileIds']))

    def view(self,ctx):
        with self.access.lock,self.access.connect() as db:
            self.tables(db)
            answer=dict(assignment=ctx['assignment'],homework=ctx['published']['content']['homework'],dueDate=ctx['published']['content']['dueDate'],
                        timezone='Asia/Qyzylorda',subject=ctx['published']['lesson']['subject'],staff=ctx['staff'],canReview=ctx['canReview'],
                        maxFileBytes=files.MAX_FILE,maxFiles=5)
            if ctx['student'] is None:
                summaries=[]
                for id,pupil in ctx['roster'].items():
                    version,document=self.load(db,ctx,id)
                    sent=document['submitted']
                    summaries.append(dict(studentId=id,name=pupil['name'],version=version,status=status(document),
                                          submittedAt=sent['submittedAt'] if sent else None,late=sent['late'] if sent else False,
                                          assignmentChanged=bool(sent and sent['assignment']!=ctx['assignment'])))
                return answer | dict(students=summaries)
            version,document=self.load(db,ctx,ctx['student'])
            pupil=ctx['roster'][ctx['student']]
            personal=ctx['user']['role']=='student'
            ready=db.execute("SELECT * FROM homework_files WHERE workspace=? AND lesson_key=? AND student_id=? AND status='ready' ORDER BY created,id",
                             (ctx['workspace'],ctx['key'],ctx['student'])).fetchall() if personal else []
            sent=document['submitted']
            return answer | dict(student=dict(id=ctx['student'],name=pupil['name']),version=version,status=status(document),
                                 canSubmit=self.editable(ctx,document),canWithdraw=bool(ctx['canSubmit'] and sent and not sent.get('review')),
                                 draft=document['draft'] if personal else None,availableFiles=[files.meta(r) for r in ready],
                                 submitted=self.sent_projection(db,sent),assignmentChanged=bool(sent and sent['assignment']!=ctx['assignment']))

    def notify(self,db,ctx,student,version,action):
        role='teacher' if action in ['homework.submitted','homework.withdrawn'] else 'family'
        query="SELECT * FROM accounts WHERE active=1 AND role='teacher'" if role=='teacher' else "SELECT * FROM accounts WHERE active=1 AND role IN ('parent','student')"
        users=[self.access.public(row) for row in db.execute(query)]
        targets=[u for u in users if (bool(user_ids(u,ctx['workspace'],'teacher') & set(ctx['current']['teacherIds'])) if role=='teacher' else student in user_ids(u,ctx['workspace'],'student'))]
        self.journal.notifications.deliver(db,targets,ctx['workspace'],version,action)

    def persist(self,db,ctx,version,document,action):
        db.execute('INSERT INTO homework_submissions VALUES (?,?,?,?,?) ON CONFLICT(workspace,lesson_key,student_id) DO UPDATE SET version=excluded.version,document=excluded.document',
                   (ctx['workspace'],ctx['key'],ctx['student'],version,encoded(document)))
        db.execute('DELETE FROM homework_draft_files WHERE workspace=? AND lesson_key=? AND student_id=?',(ctx['workspace'],ctx['key'],ctx['student']))
        db.executemany('INSERT INTO homework_draft_files VALUES (?,?,?,?)',[(id,ctx['workspace'],ctx['key'],ctx['student']) for id in document['draft']['fileIds']])
        self.access.record(db,action,ctx['workspace'],ctx['revision'],detail=f"{ctx['key']} · {ctx['student']} · редакция {version}")
        if action!='homework.draft':
            sent=document['submitted']
            id=db.execute('INSERT INTO homework_history (workspace,lesson_key,student_id,version,created,actor_name,action,document) VALUES (?,?,?,?,?,?,?,?)',
                          (ctx['workspace'],ctx['key'],ctx['student'],version,now().isoformat(),ctx['user']['name'],action,encoded(sent))).lastrowid
            if sent: db.executemany('INSERT INTO homework_file_links VALUES (?,?)',[(file,id) for file in sent['fileIds']])
            self.notify(db,ctx,ctx['student'],version,action)

    def register(self,app,Workspace):
        @app.get('/api/journal/homework/{key}')
        def get(key:str,request:Request,workspace:Workspace='school',studentId:str|None=None):
            return self.view(self.context(request,workspace,key,studentId))

        @app.put('/api/journal/homework/{key}')
        def answer(key:str,body:Answer,request:Request,workspace:Workspace='school'):
            ctx=self.context(request,workspace,key,body.studentId)
            if not ctx['canSubmit']: raise HTTPException(403,'Отправить работу может только ученик действующего урока.')
            if body.assignment!=ctx['assignment']: raise HTTPException(409,'Задание изменилось. Откройте его заново.')
            with self.access.lock,self.access.connect() as db:
                self.tables(db);db.execute('BEGIN IMMEDIATE');self.recheck(db,ctx)
                version,document=self.load(db,ctx,body.studentId)
                if version!=body.version: raise HTTPException(409,'Работа изменилась в другом окне. Обновите её перед сохранением.')
                if body.action=='withdraw':
                    if not document['submitted'] or document['submitted'].get('review'):
                        raise HTTPException(409,'Проверенную работу можно изменить после возврата учителем.')
                    document['submitted']=None
                else:
                    if not self.editable(ctx,document): raise HTTPException(409,'Работа уже отправлена. Отзовите её или дождитесь возврата учителем.')
                    files.check_files(db,body.fileIds,workspace,key,body.studentId)
                    document['draft']=dict(text=body.text,fileIds=body.fileIds)
                    if body.action=='submit':
                        if not body.text and not body.fileIds: raise HTTPException(422,'Напишите ответ или приложите файл.')
                        due=ctx['published']['content']['dueDate']
                        document['attempts']+=1
                        document['submitted']=dict(**document['draft'],assignment=ctx['assignment'],homework=ctx['published']['content']['homework'],
                            dueDate=due,submittedAt=now().isoformat(),attempt=document['attempts'],review=None,
                            late=bool(due and now().astimezone(ZoneInfo('Asia/Qyzylorda')).date()>date.fromisoformat(due)))
                action={'draft':'homework.draft','submit':'homework.submitted','withdraw':'homework.withdrawn'}[body.action]
                self.persist(db,ctx,version+1,document,action)
            return self.view(ctx)

        @app.post('/api/journal/homework/{key}/review')
        def review(key:str,body:Review,request:Request,workspace:Workspace='school'):
            ctx=self.context(request,workspace,key,body.studentId)
            if not ctx['canReview']: raise HTTPException(403,'Проверка доступна действующему учителю урока или администратору.')
            if body.assignment!=ctx['assignment']: raise HTTPException(409,'Задание изменилось. Обновите его перед проверкой.')
            if body.decision=='returned' and not body.comment: raise HTTPException(422,'Укажите, что нужно доработать.')
            with self.access.lock,self.access.connect() as db:
                self.tables(db);db.execute('BEGIN IMMEDIATE');self.recheck(db,ctx)
                version,document=self.load(db,ctx,body.studentId)
                if version!=body.version: raise HTTPException(409,'Работа изменилась. Откройте актуальную отправку.')
                sent=document['submitted']
                if not sent: raise HTTPException(409,'Ученик ещё не отправил работу или отозвал её.')
                if status(document)=='returned': raise HTTPException(409,'Дождитесь новой отправки ученика.')
                if sent['assignment']!=ctx['assignment'] and body.decision=='accepted':
                    raise HTTPException(409,'Условия задания изменились. Верните работу для новой отправки.')
                sent['review']=dict(decision=body.decision,comment=body.comment,reviewedAt=now().isoformat(),reviewer=ctx['user']['name'])
                self.persist(db,ctx,version+1,document,'homework.reviewed')
            return self.view(ctx)

        @app.get('/api/journal/homework/{key}/history')
        def history(key:str,studentId:str,request:Request,workspace:Workspace='school',before:int|None=Query(default=None,ge=1)):
            ctx=self.context(request,workspace,key,studentId)
            with self.access.lock,self.access.connect() as db:
                self.tables(db)
                rows=db.execute('SELECT id,version,created,actor_name,action,document FROM homework_history WHERE workspace=? AND lesson_key=? AND student_id=? AND (? IS NULL OR id<?) ORDER BY id DESC LIMIT 21',
                                (workspace,key,studentId,before,before)).fetchall()
                items=[dict(id=r[0],version=r[1],created=r[2],actor=r[3],action=r[4],submitted=self.sent_projection(db,json.loads(r[5]))) for r in rows[:20]]
            return dict(items=items,nextBefore=rows[19][0] if len(rows)>20 else None)

        @app.post('/api/journal/homework/{key}/files')
        async def upload(key:str,studentId:str,filename:str,assignment:str,request:Request,workspace:Workspace='school'):
            if not self.upload_slots.acquire(blocking=False): raise HTTPException(429,'Сервер принимает другие файлы. Повторите немного позже.')
            reservation=None;handle=None;complete=False
            def prepare():
                ctx=self.context(request,workspace,key,studentId)
                if not ctx['canSubmit'] or assignment!=ctx['assignment']: raise HTTPException(403,'Загрузка для этого задания сейчас недоступна.')
                with self.access.lock,self.access.connect() as db:
                    self.tables(db);db.execute('BEGIN IMMEDIATE');self.recheck(db,ctx)
                    _,document=self.load(db,ctx,studentId)
                    if not self.editable(ctx,document): raise HTTPException(409,'Сначала отзовите работу или дождитесь её возврата.')
                    files.collect_unused(db)
                    id,name,root=files.reserve(db,workspace,key,studentId,ctx['user']['id'],filename)
                return ctx,id,name,root
            def finish(size):
                ctx,id,name,root=reservation
                digest=files.validate_file(files.file_path(root,id),name)
                with self.access.lock,self.access.connect() as db:
                    db.execute('BEGIN IMMEDIATE')
                    self.recheck(db,ctx)
                    _,document=self.load(db,ctx,studentId)
                    if not self.editable(ctx,document): raise HTTPException(409,'Работа была отправлена во время загрузки.')
                    db.execute("UPDATE homework_files SET status='ready',size=?,sha256=? WHERE id=?",(size,digest,id))
                    self.access.record(db,'homework.file_uploaded',workspace,detail=id)
                    return files.meta(db.execute('SELECT * FROM homework_files WHERE id=?',(id,)).fetchone())
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
                def flush():
                    handle.flush();os.fsync(handle.fileno());handle.close()
                await run_in_threadpool(flush);handle=None
                if not size: raise HTTPException(422,'Выберите непустой файл.')
                result=await run_in_threadpool(finish,size)
                complete=True
                return result
            except TimeoutError: raise HTTPException(408,'Загрузка заняла слишком много времени. Попробуйте снова.') from None
            except ClientDisconnect: raise HTTPException(400,'Соединение прервано во время загрузки.') from None
            finally:
                try:
                    if handle: handle.close()
                    if reservation and not complete:
                        _,id,_,root=reservation
                        def discard():
                            with self.access.lock,self.access.connect() as db:
                                db.execute('BEGIN IMMEDIATE')
                                row=db.execute('SELECT status FROM homework_files WHERE id=?',(id,)).fetchone()
                                # Preserve a ready upload if its HTTP response was lost.
                                if not row or row[0]!='uploading': return
                                try: files.file_path(root,id).unlink(missing_ok=True)
                                except OSError: return
                                db.execute('DELETE FROM homework_files WHERE id=?',(id,))
                        await run_in_threadpool(discard)
                finally: self.upload_slots.release()

        @app.get('/api/journal/homework/{key}/files/{id}')
        def download(key:str,id:str,studentId:str,request:Request,workspace:Workspace='school'):
            ctx=self.context(request,workspace,key,studentId)
            with self.access.lock,self.access.connect() as db:
                self.tables(db)
                row=db.execute("SELECT * FROM homework_files WHERE id=? AND workspace=? AND lesson_key=? AND student_id=? AND status='ready'",(id,workspace,key,studentId)).fetchone()
                visible=ctx['user']['role']=='student' or db.execute('SELECT 1 FROM homework_file_links WHERE file_id=? LIMIT 1',(id,)).fetchone()
                if not row or not visible: raise HTTPException(404,'Файл не найден.')
                path=files.file_path(files.root_for(db),id)
                try: stat=path.stat()
                except OSError: raise HTTPException(404,'Файл недоступен в хранилище.') from None
            return FileResponse(path,stat_result=stat,media_type='application/octet-stream',headers={
                'Content-Disposition':"attachment; filename*=UTF-8''"+quote(row[5],safe=''),
                'Content-Length':str(row[6]),'Cache-Control':'no-store','X-Content-Type-Options':'nosniff',
                'Content-Security-Policy':"sandbox; default-src 'none'"})
