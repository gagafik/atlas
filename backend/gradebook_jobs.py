"""Durable, fenced gradebook jobs; short commits and shared immutable evidence."""
from contextlib import asynccontextmanager,closing
import json
import logging
from pathlib import Path
import sqlite3
import threading
import time
from uuid import uuid4
from fastapi import HTTPException,Request
from starlette.concurrency import run_in_threadpool
from .access import Access,actor,now
from .notifications import encoded
from .gradebook_build import build_isolated,Cancelled

ACTIVE=('queued','running','cancelling')
CHUNK=250


def tables(db):
    if 'source_kind' not in {r[1] for r in db.execute('PRAGMA table_info(grading_books)')}:
        db.execute("ALTER TABLE grading_books ADD COLUMN source_kind TEXT NOT NULL DEFAULT 'legacy'")
    db.execute('''CREATE TABLE IF NOT EXISTS grading_facts(id INTEGER PRIMARY KEY,workspace TEXT,lesson_key TEXT,version INTEGER,student_id TEXT,subject_id TEXT,day TEXT,start TEXT,summary TEXT,works TEXT,UNIQUE(workspace,lesson_key,version,student_id))''')
    db.execute('CREATE INDEX IF NOT EXISTS grading_fact_pupil ON grading_facts(workspace,student_id,subject_id,id)')
    db.execute('''CREATE TABLE IF NOT EXISTS grading_projected(workspace TEXT,lesson_key TEXT,version INTEGER,PRIMARY KEY(workspace,lesson_key,version))''')
    # Format 2 facts carry per-category sums and the work's category; older ones are derived again from the closure document once.
    if 'format' not in {r[1] for r in db.execute('PRAGMA table_info(grading_projected)')}:
        db.execute('ALTER TABLE grading_projected ADD COLUMN format INTEGER NOT NULL DEFAULT 1')
    db.execute('''CREATE TABLE IF NOT EXISTS grading_preparations(id TEXT PRIMARY KEY,workspace TEXT,created TEXT,status TEXT,token TEXT,expires REAL,document TEXT)''')
    db.execute('CREATE INDEX IF NOT EXISTS grading_job_pending ON grading_preparations(status,created)')
    # Last migration marker, read without taking the school's writer lock.
    db.execute('CREATE INDEX IF NOT EXISTS grading_job_recent ON grading_preparations(workspace,created)')


def public(job):
    return {k:v for k,v in job.items() if k not in ['principal','start','end','abandoned','needsCleanup']}


def carried(db,workspace,book_id,chunk):
    """Assignment and proposal of the previous closure for the pairs in this chunk, as drafts for re-checking."""
    wanted={(r[1],r[2]) for r in chunk};result={}
    for student,subject,assigned,proposal in db.execute('SELECT student_id,subject_id,assigned,proposal FROM grading_rows WHERE workspace=? AND book_id=? AND student_id IN (SELECT value FROM json_each(?))',(workspace,book_id,encoded(sorted({r[1] for r in chunk})))):
        if (student,subject) not in wanted:continue
        if proposal:
            value=json.loads(proposal);value.pop('approval',None);value['carriedFrom']=dict(bookId=book_id);proposal=encoded(value)
        result[(student,subject)]=(assigned,proposal)
    return result


class Queue:
    def __init__(self,path,*,clock=time.time,lease=15):
        self.path=Path(path).resolve();self.clock=clock;self.lease=lease
        from .final_grades import tables as grade_tables
        from .journal import Journal
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE');Journal(None,None).tables(db);grade_tables(db)

    def connect(self):
        from .generation_queue import Connection
        return sqlite3.connect(self.path,timeout=5,factory=Connection)

    def load(self,db,id,workspace=None):
        raw=db.execute('SELECT document FROM grading_preparations WHERE id=? AND (? IS NULL OR workspace=?)',(id,workspace,workspace)).fetchone()
        if not raw:raise HTTPException(404,'Задание подготовки не найдено.')
        return json.loads(raw[0])

    def write(self,db,job):
        job['updated']=now().isoformat()
        db.execute('UPDATE grading_preparations SET status=?,document=? WHERE id=?',(job['status'],encoded(job),job['id']))

    def audit(self,db,job,action):
        token=actor.set(job['principal'])
        try:Access(None,None,None).record(db,'journal.gradebook_'+action,job['workspace'],detail=encoded(dict(jobId=job['id'],bookId=job['bookId'],periodId=job['periodId'],closureVersion=job['closureVersion'])))
        finally:actor.reset(token)

    def valid(self,db,job):
        p=db.execute('SELECT status,version FROM journal_periods WHERE workspace=? AND id=?',(job['workspace'],job['periodId'])).fetchone()
        if not p or p!=('closed',job['closureVersion']):return 'Учебный период открыт или закрыт повторно. Запустите подготовку актуального закрытия.'
        user=job['principal']
        if user['id']=='local':
            if db.execute('SELECT 1 FROM accounts LIMIT 1').fetchone():return 'Доступ изменился. Администратору нужно запустить подготовку заново.'
        else:
            row=db.execute('SELECT * FROM accounts WHERE id=? AND active=1',(user['id'],)).fetchone()
            if not row or Access(None,None,None).public(row)!=user or user['role']!='admin' or job['workspace'] not in user['workspaces']:
                return 'Доступ инициатора изменился. Администратору нужно запустить подготовку заново.'
        return None

    def enqueue(self,workspace,period,user):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            job=dict(id=uuid4().hex,workspace=workspace,periodId=period['id'],periodName=period['name'],closureVersion=period['version'],start=period['start'],end=period['end'],principal=user,created=now().isoformat(),updated=now().isoformat(),status='queued',phase='queued',completed=0,total=0,attempts=0,bookId=uuid4().hex,message='Подготовка поставлена в очередь.')
            problem=self.valid(db,job)
            if problem:raise HTTPException(409,problem)
            ready=db.execute('SELECT id FROM grading_books WHERE workspace=? AND period_id=? AND closure_version=?',(workspace,period['id'],period['version'])).fetchone()
            if ready:return public(job|dict(id='',status='succeeded',phase='done',bookId=ready[0],message='Ведомость уже готова.'))
            existing=db.execute("SELECT document FROM grading_preparations WHERE workspace=? AND status IN ('queued','running','cancelling') AND json_extract(document,'$.periodId')=? AND json_extract(document,'$.closureVersion')=?",(workspace,period['id'],period['version'])).fetchone()
            if existing:return public(json.loads(existing[0]))
            if db.execute("SELECT count(*) FROM grading_preparations WHERE workspace=? AND status IN ('queued','running','cancelling')",(workspace,)).fetchone()[0]>=3:raise HTTPException(409,'В очереди уже три ведомости. Дождитесь результата или отмените задание.')
            db.execute('INSERT INTO grading_preparations VALUES (?,?,?,?,?,?,?)',(job['id'],workspace,job['created'],job['status'],'',0,encoded(job)))
            self.audit(db,job,'queued')
            return public(job)

    def authorize(self,db,user,workspace):
        from .journal import Journal
        Journal(Access(None,None,None),None).recheck_account(db,user)
        if user['role']!='admin' or workspace not in user['workspaces']:raise HTTPException(403,'Подготовкой ведомостей управляет администратор.')

    def get(self,id,workspace,user=None):
        with self.connect() as db:
            db.execute('BEGIN')
            if user is not None:self.authorize(db,user,workspace)
            return public(self.load(db,id,workspace))

    def listing(self,workspace,user=None):
        with self.connect() as db:
            db.execute('BEGIN')
            if user is not None:self.authorize(db,user,workspace)
            return [public(json.loads(r[0])) for r in db.execute("SELECT document FROM grading_preparations WHERE workspace=? ORDER BY status IN ('queued','running','cancelling') DESC,created DESC LIMIT 50",(workspace,))]

    def pending(self):
        with self.connect() as db:return bool(db.execute("SELECT 1 FROM grading_preparations WHERE status IN ('queued','running','cancelling') LIMIT 1").fetchone())

    def cancel(self,id,workspace,user):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE');job=self.load(db,id,workspace)
            # Recheck cancellation permissions against this pinned database too.
            self.authorize(db,user,workspace)
            if job['status'] not in ACTIVE:raise HTTPException(409,'Подготовка уже завершена. Готовая ведомость не удалена.')
            job['status']='cancelled' if job['status']=='queued' else 'cancelling';job['message']='Отмена сохранена.';job['needsCleanup']=True
            self.write(db,job)
            audit=job|dict(principal=user);self.audit(db,audit,'cancel_requested')
            return public(job)

    def owns(self,db,id,token):
        return bool(db.execute("SELECT 1 FROM grading_preparations WHERE id=? AND token=? AND expires>? AND status IN ('running','cancelling')",(id,token,self.clock())).fetchone())

    def claim(self):
        with self.connect() as db:
            if db.execute("SELECT 1 FROM grading_preparations WHERE status IN ('running','cancelling') AND expires>?",(self.clock(),)).fetchone():return None
            if not db.execute("SELECT 1 FROM grading_preparations WHERE status IN ('queued','running','cancelling') LIMIT 1").fetchone():return None
            db.execute('BEGIN IMMEDIATE')
            if db.execute("SELECT 1 FROM grading_preparations WHERE status IN ('running','cancelling') AND expires>?",(self.clock(),)).fetchone():return None
            for (raw,) in db.execute("SELECT document FROM grading_preparations WHERE status IN ('running','cancelling')").fetchall():
                job=json.loads(raw)
                job['status']='cancelled' if job['status']=='cancelling' else 'failed' if job['attempts']>=3 else 'queued'
                job['needsCleanup']=job['status']!='queued'
                job['message']='Подготовка восстановлена после остановки исполнителя.' if job['status']=='queued' else 'Подготовка остановлена. Можно запустить новое задание.'
                self.write(db,job)
            while True:
                raw=db.execute("SELECT document FROM grading_preparations WHERE status='queued' ORDER BY created LIMIT 1").fetchone()
                if not raw:return None
                job=json.loads(raw[0]);problem=self.valid(db,job)
                if problem:
                    job.update(status='stale',message=problem,needsCleanup=True);self.write(db,job);self.audit(db,job,'stale');continue
                if job['attempts']:
                    job['abandoned']=job.get('abandoned',[])+[job['bookId']];job['bookId']=uuid4().hex
                token=uuid4().hex;job.update(status='running',phase='waiting',completed=0,total=0,attempts=job['attempts']+1,message='Ожидание свободного исполнителя.')
                self.write(db,job);db.execute('UPDATE grading_preparations SET token=?,expires=? WHERE id=?',(token,self.clock()+self.lease,job['id']))
                return token,job

    def heartbeat(self,id,token,progress):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if not self.owns(db,id,token):return False,True
            job=self.load(db,id);problem=self.valid(db,job)
            if problem:
                job.update(status='stale',message=problem,needsCleanup=True);self.write(db,job);self.audit(db,job,'stale');return False,True
            job.update(progress)
            if job['status']=='running':job['message']='Ожидание свободного исполнителя.' if job['phase']=='waiting' else 'Подготовка выполняется. Можно продолжать работу.'
            self.write(db,job);db.execute('UPDATE grading_preparations SET expires=? WHERE id=?',(self.clock()+self.lease,id))
            return True,job['status']=='cancelling'

    def finish(self,id,token,result,restarting=False):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if not self.owns(db,id,token):return
            job=self.load(db,id)
            problem=self.valid(db,job)
            status='cancelled' if job['status']=='cancelling' else 'stale' if problem else 'queued' if restarting and job['attempts']<3 else result['status']
            message=problem if status=='stale' else 'Подготовка продолжится после перезапуска.' if status=='queued' else result.get('message','Подготовка отменена; ведомость не создана.')
            job.update(status=status,message=message,phase='queued' if status=='queued' else job['phase'],needsCleanup=status!='queued')
            self.write(db,job);db.execute('UPDATE grading_preparations SET token=\'\',expires=0 WHERE id=?',(id,));self.audit(db,job,status)

    def cleanup_pending(self,job):
        while True:
            with self.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                count=db.execute('''DELETE FROM grading_rows WHERE rowid IN (SELECT r.rowid FROM grading_rows r WHERE r.workspace=? AND r.book_id=?
                    AND NOT EXISTS(SELECT 1 FROM grading_books b WHERE b.workspace=r.workspace AND b.id=r.book_id) LIMIT ?)''',(job['workspace'],job['bookId'],CHUNK)).rowcount
            if not count:break

    def reap(self):
        with self.connect() as db:
            jobs=[json.loads(r[0]) for r in db.execute("SELECT document FROM grading_preparations WHERE status NOT IN ('queued','running','cancelling') AND json_extract(document,'$.needsCleanup')=1")]
        for job in jobs:
            for id in job.get('abandoned',[])+[job['bookId']]:self.cleanup_pending(job|dict(bookId=id))
            with self.connect() as db:
                db.execute('BEGIN IMMEDIATE');latest=self.load(db,job['id']);latest['needsCleanup']=False;self.write(db,latest)

    def import_stage(self,job,token,stage,cancel,progress):
        self.cleanup_pending(job)
        # Keep connections open, but commit each batch separately. Reopening a
        # writer for every 250 rows repeatedly rebuilds SQLite's page cache.
        with closing(sqlite3.connect(Path(stage).resolve().as_uri()+'?mode=ro',uri=True)) as source,closing(sqlite3.connect(self.path,timeout=5)) as db:
            total=sum(source.execute('SELECT count(*) FROM '+table).fetchone()[0] for table in ['facts','projected','rows']);done=0;previous=False
            for table in ['facts','projected','rows']:
                cursor=source.execute('SELECT * FROM '+table)
                while chunk:=cursor.fetchmany(CHUNK):
                    if cancel.is_set():raise Cancelled()
                    with db:
                        db.execute('BEGIN IMMEDIATE')
                        if not self.owns(db,job['id'],token) or self.load(db,job['id'])['status']=='cancelling' or self.valid(db,job):raise Cancelled()
                        if table=='facts':db.executemany('INSERT INTO grading_facts(workspace,lesson_key,version,student_id,subject_id,day,start,summary,works) VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(workspace,lesson_key,version,student_id) DO UPDATE SET summary=excluded.summary,works=excluded.works',[(job['workspace'],*r) for r in chunk])
                        elif table=='projected':db.executemany('INSERT INTO grading_projected(workspace,lesson_key,version,format) VALUES (?,?,?,?) ON CONFLICT(workspace,lesson_key,version) DO UPDATE SET format=excluded.format',[(job['workspace'],*r) for r in chunk])
                        else:
                            # Re-closing keeps who is responsible and the proposed grade; evidence may differ, so rows restart as drafts.
                            # The carry writes no grading_history entry; the earlier book stays frozen.
                            if previous is False:previous=db.execute('SELECT id FROM grading_books WHERE workspace=? AND period_id=? AND closure_version<? ORDER BY closure_version DESC LIMIT 1',(job['workspace'],job['periodId'],job['closureVersion'])).fetchone()
                            old=carried(db,job['workspace'],previous[0],chunk) if previous else {}
                            picked=[old.get((r[1],r[2]),('',None)) for r in chunk]
                            db.executemany('INSERT INTO grading_rows VALUES (?,?,?,?,?,?,?,?,?,?,?)',[(job['workspace'],r[0],job['bookId'],r[1],r[2],r[3],c[0],1,'draft',r[4],c[1]) for r,c in zip(chunk,picked)])
                    done+=len(chunk);progress(dict(phase='saving',completed=done,total=total))
                    # Give waiting journal writers a chance before reacquiring
                    # SQLite's single writer slot for the next batch.
                    if cancel.wait(.005):raise Cancelled()
            with db:
                db.execute('BEGIN IMMEDIATE')
                if cancel.is_set() or not self.owns(db,job['id'],token):raise Cancelled()
                latest=self.load(db,job['id']);problem=self.valid(db,job)
                if latest['status']=='cancelling' or problem:raise Cancelled()
                db.execute('INSERT INTO grading_books(workspace,id,period_id,closure_version,created,source_kind) VALUES (?,?,?,?,?,?)',(job['workspace'],job['bookId'],job['periodId'],job['closureVersion'],now().isoformat(),'shared'))
                latest.update(status='succeeded',phase='done',completed=total,total=total,message='Ведомость готова.');self.write(db,latest)
                db.execute('UPDATE grading_preparations SET token=\'\',expires=0 WHERE id=?',(job['id'],));self.audit(db,latest,'prepared')


class Runner:
    def __init__(self,queue,compute_lock):
        self.queue=queue;self.compute_lock=compute_lock;self.stop=threading.Event();self.guard=threading.Lock();self.thread=None

    def start(self):
        with self.guard:
            if self.thread is None or not self.thread.is_alive():
                self.stop.clear();self.thread=threading.Thread(target=self.loop,name='gradebook-coordinator',daemon=True);self.thread.start()

    def shutdown(self):
        self.stop.set()
        if self.thread:self.thread.join(timeout=12)

    def loop(self):
        while not self.stop.is_set() and self.queue.path.exists():
            try:
                claimed=self.queue.claim()
                if claimed:self.work(*claimed);continue
                self.queue.reap()
                with self.guard:
                    if not self.queue.pending():self.thread=None;return
            except Exception:logging.exception('Gradebook coordinator failed')
            self.stop.wait(.5)

    def work(self,token,job):
        cancel=threading.Event();done=threading.Event();guard=threading.Lock();progress=dict(phase='waiting',completed=0,total=0);acquired=False
        directory=self.queue.path.with_suffix(self.queue.path.suffix+'.grading');directory.mkdir(mode=0o700,exist_ok=True)
        if directory.is_symlink():raise ValueError('Staging directory must not be a link')
        stage=directory/(job['id']+'-'+token+'.db')
        def report(p):
            with guard:progress.update(p)
        def monitor():
            while not done.wait(.5):
                try:
                    with guard:p=dict(progress)
                    owned,cancelling=self.queue.heartbeat(job['id'],token,p)
                    if not owned or cancelling or self.stop.is_set():cancel.set()
                    if not owned:return
                except Exception:cancel.set();return
        observer=threading.Thread(target=monitor,name='gradebook-lease',daemon=True);observer.start()
        result=dict(status='cancelled');published=False
        try:
            for id in job.get('abandoned',[]):self.queue.cleanup_pending(job|dict(bookId=id))
            while not cancel.is_set() and not self.stop.is_set():
                if self.compute_lock.acquire(timeout=.2):acquired=True;break
            if acquired and not cancel.is_set() and not self.stop.is_set():
                result=build_isolated(self.queue.path,job,stage,cancel,report)
                if result['status']=='built':
                    self.queue.import_stage(job,token,stage,cancel,report);published=True
        except Cancelled:result=dict(status='cancelled')
        except Exception:
            logging.exception('Gradebook preparation failed');result=dict(status='failed',message='Ошибка подготовки; ведомость не создана. Можно повторить задание.')
        finally:
            if acquired:self.compute_lock.release()
            done.set();observer.join(timeout=6)
            if not published:
                self.queue.cleanup_pending(job);self.queue.finish(job['id'],token,result,restarting=self.stop.is_set())
            # Only our exact staging file, after the owned child has joined.
            for suffix in ['', '-journal','-wal','-shm']:Path(str(stage)+suffix).unlink(missing_ok=True)


def register_jobs(app,grades,Workspace,database_path,compute_lock):
    runners={};guard=threading.Lock()
    def runner():
        path=str(Path(database_path()).resolve())
        with guard:
            if path not in runners:runners[path]=Runner(Queue(path),compute_lock)
            return runners[path]
    def shutdown():
        with guard:current=list(runners.values());runners.clear()
        for r in current:r.shutdown()
    previous=app.router.lifespan_context
    @asynccontextmanager
    async def lifespan(application):
        async with previous(application):
            await run_in_threadpool(lambda:runner().start())
            try:yield
            finally:await run_in_threadpool(shutdown)
    app.router.lifespan_context=lifespan

    from .final_grades import Prepare
    @app.post('/api/final-grades/periods/{id}/prepare',status_code=202)
    def prepare(id:str,body:Prepare,request:Request,workspace:Workspace='school'):
        user,snap=grades.context(request,workspace,True,True)
        with grades.access.read_connect() as db:
            db.execute('BEGIN');grades.recheck(db,user,snap,workspace);period=grades.periods.find(db,workspace,id,body.version)
        r=runner();job=r.queue.enqueue(workspace,period,user);r.start();return job

    @app.get('/api/final-grades/preparations')
    def listing(request:Request,workspace:Workspace='school'):
        user,snap=grades.context(request,workspace,True,True)
        with grades.access.read_connect() as db:grades.recheck(db,user,snap,workspace)
        r=runner();r.start();return dict(items=r.queue.listing(workspace,user))

    @app.get('/api/final-grades/preparations/{id}')
    def get(id:str,request:Request,workspace:Workspace='school'):
        user,snap=grades.context(request,workspace,True,True)
        with grades.access.read_connect() as db:grades.recheck(db,user,snap,workspace)
        r=runner();r.start();return r.queue.get(id,workspace,user)

    @app.post('/api/final-grades/preparations/{id}/cancel')
    def cancel(id:str,request:Request,workspace:Workspace='school'):
        user,snap=grades.context(request,workspace,True,True)
        r=runner();job=r.queue.cancel(id,workspace,user);r.start();return job
    return shutdown


def shared_evidence(db,workspace,row,after):
    book=db.execute('SELECT period_id,closure_version FROM grading_books WHERE workspace=? AND id=?',(workspace,row['bookId'])).fetchone()
    facts=db.execute('''SELECT f.id,f.lesson_key,f.version,f.day,f.start,f.works FROM grading_facts f
        WHERE f.workspace=? AND f.student_id=? AND f.subject_id=? AND f.id>=? AND f.works!='[]' AND EXISTS
        (SELECT 1 FROM journal_period_snapshots s WHERE s.workspace=f.workspace AND s.period_id=? AND s.closure_version=? AND s.lesson_key=f.lesson_key AND s.published_version=f.version)
        ORDER BY f.id LIMIT 51''',(workspace,row['studentId'],row['subjectId'],after//16,book[0],book[1])).fetchall()
    items=[]
    for id,key,version,day,start,works in facts:
        for i,work in enumerate(json.loads(works)):
            cursor=id*16+i+1
            if cursor>after:items.append(dict(id=cursor,lessonKey=key,publishedVersion=version,date=day,start=start,**work))
            if len(items)>50:return dict(items=items[:50],nextAfter=items[49]['id'])
    return dict(items=items,nextAfter=None)
