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
from .resources import teacher_ids
from . import homework_files as files
from . import assignment_files as materials
from .homework_grading import HomeworkGrading, ReviewGrade
from . import homework_deadlines as deadlines


class Answer(Model):
    studentId: str = Field(min_length=1,max_length=100)
    version: int = Field(ge=0)
    assignment: str = Field(pattern=r'^[a-f0-9]{64}$')
    action: Literal['draft','submit','withdraw']
    text: str = Field(default='',max_length=10000)
    fileIds: list[str] = Field(default_factory=list,max_length=5)
    deadlineVersion: int | None = Field(default=None,ge=0)


class Review(Model):
    studentId: str = Field(min_length=1,max_length=100)
    version: int = Field(ge=1)
    assignment: str = Field(pattern=r'^[a-f0-9]{64}$')
    decision: Literal['accepted','returned']
    comment: str = Field(default='',max_length=4000)
    grading: ReviewGrade | None = None


def legacy_fingerprint(document):
    """Pre-F4 token (text and deadline included); only migration reads it."""
    content=document['content']
    fields=[content['homework'],content['dueDate']]
    # Keep old submissions comparable when the new settings retain their defaults.
    if content.get('homeworkFiles') or content.get('homeworkLatePolicy','allow')!='allow':
        fields.extend([content.get('homeworkFiles',[]),content.get('homeworkLatePolicy','allow')])
    return hashlib.sha256(encoded(fields).encode()).hexdigest()


def substance(mode, files):
    return hashlib.sha256(encoded(['homework-substance',mode,sorted(files)]).encode()).hexdigest()


def fingerprint(document):
    """Substantive conditions: online submission on/off and the set of assignment files. Text, deadline and late policy are not substantive."""
    content=document['content']
    return substance(content.get('homeworkSubmission','online'),content.get('homeworkFiles',[]))


def conditions(document):
    """Everything a pupil is shown; guards writes against a stale context without reopening answers."""
    content=document['content']
    return hashlib.sha256(encoded([fingerprint(document),content['homework'],content['dueDate'],content.get('homeworkLatePolicy','allow')]).encode()).hexdigest()


def course_teachers(state, lesson):
    """Teachers of the course by workload (group + subject), as live_lessons.pairs decides; a substitution never makes one."""
    return {id for w in state.workloads if w.groupId==lesson['groupId'] and w.subjectId==lesson['subjectId'] for id in teacher_ids(w)}


def migrate_fingerprints(db):
    """Move stored tokens to the substantive fingerprint once (idempotent). Answers to older conditions keep their old token and stay changed; history is never rewritten."""
    tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if 'homework_submissions' in tables:
        position=0;cached=(None,None)
        while rows:=db.execute('SELECT rowid,workspace,lesson_key,document FROM homework_submissions WHERE rowid>? ORDER BY rowid LIMIT 500',(position,)).fetchall():
            position=rows[-1][0]
            for rowid,workspace,key,raw in rows:
                document=json.loads(raw);sent=document.get('submitted')
                if not sent:continue
                if cached[0]!=(workspace,key):cached=((workspace,key),publication_tokens(db,workspace,key))
                tokens=cached[1]
                # Without a publication the answer is described by its own files; otherwise only answers to the current conditions move.
                new=substance('online',sent.get('assignmentFileIds',[])) if tokens is None else tokens[1] if sent['assignment']==tokens[0] else None
                if new and new!=sent['assignment']:
                    sent['assignment']=new
                    db.execute('UPDATE homework_submissions SET document=? WHERE rowid=?',(encoded(document),rowid))
    if 'homework_extensions' in tables:
        for workspace,key,student,assignment in db.execute('SELECT workspace,lesson_key,student_id,assignment FROM homework_extensions').fetchall():
            tokens=publication_tokens(db,workspace,key)
            if tokens and assignment==tokens[0]:
                db.execute('UPDATE homework_extensions SET assignment=? WHERE workspace=? AND lesson_key=? AND student_id=?',(tokens[1],workspace,key,student))


def publication_tokens(db, workspace, key):
    """(legacy, current) tokens of the published homework, or None when it is withdrawn or has no homework."""
    row=db.execute('SELECT published_document FROM journal_entries WHERE workspace=? AND lesson_key=?',(workspace,key)).fetchone()
    document=json.loads(row[0]) if row and row[0] else None
    return (legacy_fingerprint(document),fingerprint(document)) if document and document['content']['homework'] else None


def overdue(content, instant=None):
    return bool(content['dueDate'] and (instant or now()).astimezone(ZoneInfo('Asia/Qyzylorda')).date()>date.fromisoformat(content['dueDate']))


def deadline_closed(content, instant=None):
    return content.get('homeworkLatePolicy','allow')=='block' and overdue(content,instant)


def status(document):
    sent=document.get('submitted')
    return sent['review']['decision'] if sent and sent.get('review') else 'submitted' if sent else 'not_submitted'


class Homework:
    def __init__(self, journal, stamp):
        self.journal=journal
        self.access=journal.access
        # Journal and homework share one schedule cache, and so does every module built on homework.snapshots.
        self.snapshots=journal.snapshots or PortalSnapshots(self.access.read,stamp)

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
        from .homework_bulk import tables as request_tables
        request_tables(db)

    def context(self, request, workspace, key, student=None):
        user=self.journal.scope(request,workspace)
        staff=user['role'] in ['admin','teacher']
        if not staff and (student is None or student not in user_ids(user,workspace,'student')):
            raise HTTPException(403,'Нет доступа к работе этого ученика.')
        snapshot=self.snapshots.get(workspace)
        record=self.journal.find(workspace,key)
        return self.resolved_context(user,snapshot,record,workspace,key,student)

    def resolved_context(self,user,snapshot,record,workspace,key,student=None):
        """Shared queue/detail authorization; caller supplies a consistent snapshot."""
        staff=user['role'] in ['admin','teacher']
        if not staff and (student is None or student not in user_ids(user,workspace,'student')):
            raise HTTPException(403,'Нет доступа к работе этого ученика.')
        state=snapshot.state
        if not record: raise HTTPException(404,'Домашнее задание не найдено.')
        published=record['published']
        current=snapshot.current_lesson(record['document']['lesson'])
        # The course (workload) teachers review; a substitute only reads. A one-off lesson without a workload falls back to its live teachers.
        course=course_teachers(state,record['document']['lesson'])
        live=current if current and current['status']!='cancelled' else None
        reviewers=course or (set(live['teacherIds']) if live else set())
        own=user_ids(user,workspace,'teacher') if user['role']=='teacher' else set()
        if staff and not (user['role']=='admin' or own&reviewers or staff_read(user,workspace,record['document']['lesson']) or current and staff_read(user,workspace,current)):
            raise HTTPException(403,'Нет доступа к работам этого урока.')
        if not published or not published['content']['homework']:
            raise HTTPException(404,'Сначала опубликуйте домашнее задание в журнале.')
        roster={s['id']:s for s in published['lesson']['students']}
        if student is not None and student not in roster:
            raise HTTPException(404,'Ученик не включён в опубликованное задание.')
        current_ids=set(current['studentIds'])&snapshot.active if current and current['status']!='cancelled' else set()
        return dict(user=user,staff=staff,state=state,record=record,published=published,roster=roster,current=current,members=current_ids,
                    revision=state.revision,workspace=workspace,key=key,student=student,assignment=fingerprint(published),
                    conditions=conditions(published),online=published['content'].get('homeworkSubmission','online')=='online',reviewers=reviewers,
                    canReview=bool(staff and (user['role']=='admin' or own&reviewers)),
                    canSubmit=bool(user['role']=='student' and student in current_ids and roster[student].get('enrolled',True)))

    def recheck(self, db, context):
        self.journal.recheck_account(db,context['user'])
        row=db.execute('SELECT revision FROM workspaces WHERE id=?',(context['workspace'],)).fetchone()
        publication=db.execute('SELECT published_document FROM journal_entries WHERE workspace=? AND lesson_key=?',
                               (context['workspace'],context['key'])).fetchone()
        if not row or row[0]!=context['revision'] or not publication or not publication[0]:
            raise HTTPException(409,'Урок или доступ изменился. Откройте задание заново.')
        document=json.loads(publication[0])
        if conditions(document)!=context['conditions'] or {s['id'] for s in document['lesson']['students']}!=set(context['roster']):
            raise HTTPException(409,'Опубликованное задание изменилось. Откройте его заново.')

    def load(self,db,ctx,student):
        row=db.execute('SELECT version,document FROM homework_submissions WHERE workspace=? AND lesson_key=? AND student_id=?',
                       (ctx['workspace'],ctx['key'],student)).fetchone()
        return (row[0],json.loads(row[1])) if row else (0,dict(draft=dict(text='',fileIds=[]),submitted=None,attempts=0))

    def editable(self,ctx,document):
        sent=document['submitted']
        return ctx['canSubmit'] and ctx['online'] and (sent is None or status(document)=='returned' or sent['assignment']!=ctx['assignment'])

    def attachment_list(self,db,ids):
        return [files.meta(row) for id in ids if (row:=db.execute("SELECT * FROM homework_files WHERE id=? AND status='ready'",(id,)).fetchone())]

    def sent_projection(self,db,sent):
        if not sent: return None
        return {k:v for k,v in sent.items() if k not in ['fileIds','assignmentFileIds']} | dict(
            files=self.attachment_list(db,sent['fileIds']),assignmentFiles=materials.attachments(db,sent.get('assignmentFileIds',[])),
            latePolicy=sent.get('latePolicy','allow'))

    def view(self,ctx):
        with self.access.lock,self.access.connect() as db:
            self.tables(db);db.execute('BEGIN');self.recheck(db,ctx)
            content=ctx['published']['content']
            answer=dict(assignment=ctx['assignment'],homework=ctx['published']['content']['homework'],dueDate=ctx['published']['content']['dueDate'],
                        timezone='Asia/Qyzylorda',subject=ctx['published']['lesson']['subject'],staff=ctx['staff'],canReview=ctx['canReview'],
                        maxFileBytes=files.MAX_FILE,maxFiles=5,assignmentFiles=materials.attachments(db,content.get('homeworkFiles',[])),
                        latePolicy=content.get('homeworkLatePolicy','allow'),deadlineClosed=deadline_closed(content),
                        submission=content.get('homeworkSubmission','online'))
            if ctx['student'] is None:
                summaries=[]
                for id,pupil in ctx['roster'].items():
                    version,document=self.load(db,ctx,id)
                    sent=document['submitted']
                    summaries.append(dict(studentId=id,name=pupil['name'],version=version,status=status(document),
                                          dueDate=deadlines.effective(db,ctx,id)[0]['dueDate'],
                                          submittedAt=sent['submittedAt'] if sent else None,late=sent['late'] if sent else False,
                                          assignmentChanged=bool(sent and sent['assignment']!=ctx['assignment'])))
                return answer | dict(students=summaries)
            version,document=self.load(db,ctx,ctx['student'])
            pupil=ctx['roster'][ctx['student']]
            personal=ctx['user']['role']=='student'
            ready=db.execute("SELECT * FROM homework_files WHERE workspace=? AND lesson_key=? AND student_id=? AND status='ready' ORDER BY created,id",
                             (ctx['workspace'],ctx['key'],ctx['student'])).fetchall() if personal else []
            sent=document['submitted']
            deadline=deadlines.view(db,ctx)
            answer.update(deadline=deadline,dueDate=deadline['effectiveDueDate'],deadlineClosed=deadline['deadlineClosed'])
            return answer | dict(student=dict(id=ctx['student'],name=pupil['name']),version=version,status=status(document),
                                 canSaveDraft=self.editable(ctx,document),canSubmit=self.editable(ctx,document) and not answer['deadlineClosed'],
                                 canWithdraw=bool(ctx['canSubmit'] and sent and not sent.get('review')),
                                 draft=document['draft'] if personal else None,availableFiles=[files.meta(r) for r in ready],
                                 submitted=self.sent_projection(db,sent),assignmentChanged=bool(sent and sent['assignment']!=ctx['assignment']))

    def notify(self,db,ctx,student,version,action):
        role='teacher' if action in ['homework.submitted','homework.withdrawn'] else 'family'
        targets=self.journal.notifications.granted_users(db,ctx['workspace'],'teacher' if role=='teacher' else 'student',
            sorted(ctx['reviewers']) if role=='teacher' else [student],['teacher'] if role=='teacher' else ['parent','student'])
        self.journal.notifications.deliver(db,targets,ctx['workspace'],version,action)

    def persist(self,db,ctx,version,document,action):
        db.execute('INSERT INTO homework_submissions VALUES (?,?,?,?,?) ON CONFLICT(workspace,lesson_key,student_id) DO UPDATE SET version=excluded.version,document=excluded.document',
                   (ctx['workspace'],ctx['key'],ctx['student'],version,encoded(document)))
        from .homework_queue_projection import answer as queue_answer
        queue_answer(db,ctx['workspace'],ctx['key'],ctx['student'],document)
        db.execute('DELETE FROM homework_draft_files WHERE workspace=? AND lesson_key=? AND student_id=?',(ctx['workspace'],ctx['key'],ctx['student']))
        db.executemany('INSERT INTO homework_draft_files VALUES (?,?,?,?)',[(id,ctx['workspace'],ctx['key'],ctx['student']) for id in document['draft']['fileIds']])
        self.access.record(db,action,ctx['workspace'],ctx['revision'],detail=f"{ctx['key']} · {ctx['student']} · редакция {version}")
        if action!='homework.draft':
            sent=document['submitted']
            id=db.execute('INSERT INTO homework_history (workspace,lesson_key,student_id,version,created,actor_name,action,document) VALUES (?,?,?,?,?,?,?,?)',
                          (ctx['workspace'],ctx['key'],ctx['student'],version,now().isoformat(),ctx['user']['name'],action,encoded(sent))).lastrowid
            if sent:
                db.executemany('INSERT INTO homework_file_links VALUES (?,?)',[(file,id) for file in sent['fileIds']])
                db.executemany('INSERT OR IGNORE INTO assignment_submission_links VALUES (?,?)',[(file,ctx['student']) for file in sent.get('assignmentFileIds',[])])
            self.notify(db,ctx,ctx['student'],version,action)

    def register(self,app,Workspace):
        from .homework_queue import HomeworkQueue
        self.queue=HomeworkQueue(self)
        self.queue.register(app,Workspace)
        deadlines.HomeworkDeadlines(self).register(app,Workspace)
        from .homework_bulk import HomeworkBulk
        HomeworkBulk(self).register(app,Workspace)
        from .homework_parallel import HomeworkParallel
        HomeworkParallel(self).register(app,Workspace)
        materials.AssignmentFiles(self).register(app,Workspace)
        grading=HomeworkGrading(self)

        @app.get('/api/journal/homework/{key}/grading')
        def grade_options(key:str,studentId:str,request:Request,workspace:Workspace='school'):
            return grading.options(self.context(request,workspace,key,studentId))
        @app.get('/api/journal/homework/{key}')
        def get(key:str,request:Request,workspace:Workspace='school',studentId:str|None=None):
            return self.view(self.context(request,workspace,key,studentId))

        @app.put('/api/journal/homework/{key}')
        def answer(key:str,body:Answer,request:Request,workspace:Workspace='school'):
            ctx=self.context(request,workspace,key,body.studentId)
            if not ctx['canSubmit']: raise HTTPException(403,'Отправить работу может только ученик действующего урока.')
            if body.action!='withdraw' and not ctx['online']: raise HTTPException(409,'Это задание сдаётся без онлайн-отправки: работу показывают учителю на уроке.')
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
                        instant=now()
                        content,extension,_=deadlines.effective(db,ctx)
                        if body.deadlineVersion is not None and body.deadlineVersion!=(extension[0] if extension else 0):
                            raise HTTPException(409,'Срок сдачи изменился. Обновите работу перед отправкой.')
                        if deadline_closed(content,instant): raise HTTPException(409,'Приём работ закрыт. Черновик можно сохранить; для отправки учитель должен продлить срок или разрешить позднюю сдачу.')
                        if not body.text and not body.fileIds: raise HTTPException(422,'Напишите ответ или приложите файл.')
                        due=content['dueDate']
                        document['attempts']+=1
                        document['submitted']=dict(**document['draft'],assignment=ctx['assignment'],homework=ctx['published']['content']['homework'],
                            dueDate=due,submittedAt=instant.isoformat(),attempt=document['attempts'],review=None,
                            baseDueDate=ctx['published']['content']['dueDate'],deadlineVersion=extension[0] if extension else 0,
                            assignmentFileIds=content.get('homeworkFiles',[]),latePolicy=content.get('homeworkLatePolicy','allow'),late=overdue(content,instant))
                action={'draft':'homework.draft','submit':'homework.submitted','withdraw':'homework.withdrawn'}[body.action]
                self.persist(db,ctx,version+1,document,action)
            return self.view(ctx)

        @app.post('/api/journal/homework/{key}/review')
        def review(key:str,body:Review,request:Request,workspace:Workspace='school'):
            ctx=self.context(request,workspace,key,body.studentId)
            if not ctx['canReview']: raise HTTPException(403,'Проверку выполняет учитель курса или администратор.')
            if body.assignment!=ctx['assignment']: raise HTTPException(409,'Задание изменилось. Обновите его перед проверкой.')
            if body.decision=='returned' and not body.comment: raise HTTPException(422,'Укажите, что нужно доработать.')
            if body.grading and body.decision!='accepted':raise HTTPException(422,'Оценка записывается при принятии работы. Возврат не меняет оценку.')
            grade_record=grading.prepare(ctx) if body.grading else None
            with self.access.lock,self.access.connect() as db:
                self.tables(db);db.execute('BEGIN IMMEDIATE');self.recheck(db,ctx)
                version,document=self.load(db,ctx,body.studentId)
                if version!=body.version: raise HTTPException(409,'Работа изменилась. Откройте актуальную отправку.')
                sent=document['submitted']
                if not sent: raise HTTPException(409,'Ученик ещё не отправил работу или отозвал её.')
                if status(document)=='returned': raise HTTPException(409,'Дождитесь новой отправки ученика.')
                if sent['assignment']!=ctx['assignment'] and body.decision=='accepted':
                    raise HTTPException(409,'Условия задания изменились. Верните работу для новой отправки.')
                created=now().isoformat()
                if body.grading:sent['grading']=grading.apply(db,ctx,grade_record,body.grading,created)
                sent['review']=dict(decision=body.decision,comment=body.comment,reviewedAt=created,reviewer=ctx['user']['name'])
                self.persist(db,ctx,version+1,document,'homework.reviewed')
            return self.view(ctx)

        @app.get('/api/journal/homework/{key}/history')
        def history(key:str,studentId:str,request:Request,workspace:Workspace='school',before:int|None=Query(default=None,ge=1)):
            ctx=self.context(request,workspace,key,studentId)
            with self.access.lock,self.access.connect() as db:
                self.tables(db);db.execute('BEGIN');self.recheck(db,ctx)
                rows=db.execute('SELECT id,version,created,actor_name,action,document FROM homework_history WHERE workspace=? AND lesson_key=? AND student_id=? AND (? IS NULL OR id<?) ORDER BY id DESC LIMIT 21',
                                (workspace,key,studentId,before,before)).fetchall()
                items=[dict(id=r[0],version=r[1],created=r[2],actor=r[3],action=r[4],submitted=self.sent_projection(db,json.loads(r[5]))) for r in rows[:20]]
            return dict(items=items,nextBefore=rows[19][0] if len(rows)>20 else None)

        @app.post('/api/journal/homework/{key}/files')
        async def upload(key:str,studentId:str,filename:str,assignment:str,request:Request,workspace:Workspace='school'):
            slots=await files.acquire_slot()
            reservation=None;handle=None;complete=False
            def prepare():
                ctx=self.context(request,workspace,key,studentId)
                if not ctx['canSubmit'] or not ctx['online'] or assignment!=ctx['assignment']: raise HTTPException(403,'Загрузка для этого задания сейчас недоступна.')
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
                finally: slots.release()

        @app.get('/api/journal/homework/{key}/files/{id}')
        def download(key:str,id:str,studentId:str,request:Request,workspace:Workspace='school'):
            ctx=self.context(request,workspace,key,studentId)
            with self.access.lock,self.access.connect() as db:
                self.tables(db);db.execute('BEGIN');self.recheck(db,ctx)
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
