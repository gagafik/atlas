"""Durable local queue with expiring ownership and fenced result publication.

The SQLite file must be on one host. Claim tokens prevent an old worker from
publishing after its lease has been replaced; a lease is not a CPU/RAM quota.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import time
from uuid import uuid4

from fastapi import HTTPException

ACTIVE = ('queued', 'running', 'cancelling')
MAX_ATTEMPTS = 3


def stamp(): return datetime.now(timezone.utc).isoformat()


class Connection(sqlite3.Connection):
    def __exit__(self, *args):
        try: return super().__exit__(*args)
        finally: self.close()


class GenerationQueue:
    def __init__(self, path, *, lease_seconds=15, clock=time.time):
        self.path = Path(path).resolve()
        self.lease_seconds, self.clock = lease_seconds, clock
        with self.connect() as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('CREATE TABLE IF NOT EXISTS generation_jobs (id TEXT PRIMARY KEY, workspace TEXT, created TEXT, document TEXT)')
            db.execute('CREATE INDEX IF NOT EXISTS generation_jobs_recent ON generation_jobs(workspace,created,id)')
            db.execute('''CREATE TABLE IF NOT EXISTS generation_queue (
                job_id TEXT PRIMARY KEY, workspace TEXT NOT NULL, created TEXT NOT NULL,
                status TEXT NOT NULL, request TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0)''')
            db.execute('CREATE INDEX IF NOT EXISTS generation_queue_pending ON generation_queue(status,created,job_id)')
            db.execute('CREATE INDEX IF NOT EXISTS generation_queue_workspace ON generation_queue(workspace,status)')
            db.execute('''CREATE TABLE IF NOT EXISTS generation_owner (
                slot INTEGER PRIMARY KEY CHECK(slot=1), token TEXT, job_id TEXT, expires REAL NOT NULL)''')
            db.execute('INSERT OR IGNORE INTO generation_owner VALUES (1,NULL,NULL,0)')

    def connect(self):
        # Pin the file: work must never follow changes to the caller's global DB.
        return sqlite3.connect(self.path, timeout=5, factory=Connection)

    @contextmanager
    def transaction(self):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            yield db

    def _load(self, db, id, workspace=None):
        row = db.execute('SELECT document FROM generation_jobs WHERE id=? AND (? IS NULL OR workspace=?)',
                         (id,workspace,workspace)).fetchone()
        if not row: raise HTTPException(404, 'Поиск не найден.')
        return json.loads(row[0])

    def _write(self, db, job):
        db.execute('UPDATE generation_jobs SET document=? WHERE id=?', (json.dumps(job,ensure_ascii=False),job['id']))
        db.execute('UPDATE generation_queue SET status=?,attempts=? WHERE job_id=?',
                   (job['status'],job.get('attempts',0),job['id']))

    def migrate(self, request_model):
        """Adopt old active records; incomplete historical records stay interrupted."""
        with self.transaction() as db:
            rows = db.execute('''SELECT j.id,j.workspace,j.created,j.document FROM generation_jobs j
                LEFT JOIN generation_queue q ON j.id=q.job_id WHERE q.job_id IS NULL
                AND json_extract(j.document,'$.status') IN ('queued','running','cancelling')''').fetchall()
            for id, workspace, created, document in rows:
                job = json.loads(document)
                job.update(id=id,workspace=workspace,created=created)
                if job['status'] == 'cancelling':
                    job.update(status='cancelled',message='Отмена сохранена; поиск не возобновляется.')
                else:
                    try:
                        request = request_model.model_validate({k:job[k] for k in request_model.model_fields if k in job})
                        if job['status'] == 'running': job['restarts'] = 1
                        job.update(status='queued',message='Поиск восстановлен в очереди.',attempts=0,progress={'phase':'queued'})
                        db.execute('INSERT INTO generation_queue VALUES (?,?,?,?,?,0)',
                                   (id,workspace,created,'queued',request.model_dump_json()))
                    except (ValueError, TypeError):
                        job.update(status='interrupted',message='В старом задании не сохранены параметры. Запустите поиск заново.')
                self._write(db,job)

    def enqueue(self, workspace, request):
        job = dict(id=str(uuid4()),workspace=workspace,created=stamp(),status='queued',attempts=0,restarts=0,
                   progress={'phase':'queued'},message='В очереди генератора.',**request.model_dump())
        with self.transaction() as db:
            current = db.execute('SELECT revision FROM workspaces WHERE id=?',(workspace,)).fetchone()
            if not current or current[0] != request.revision:
                raise HTTPException(409,'Данные изменились. Обновите страницу перед запуском поиска.')
            count = db.execute("SELECT count(*) FROM generation_queue WHERE workspace=? AND status IN ('queued','running','cancelling')",(workspace,)).fetchone()[0]
            if count >= 3: raise HTTPException(409,'В очереди уже три поиска. Дождитесь результата или отмените один.')
            db.execute('INSERT INTO generation_jobs VALUES (?,?,?,?)',(job['id'],workspace,job['created'],json.dumps(job,ensure_ascii=False)))
            db.execute('INSERT INTO generation_queue VALUES (?,?,?,?,?,0)',
                       (job['id'],workspace,job['created'],'queued',request.model_dump_json()))
        return job

    def _retry(self, db, job):
        if job['status'] == 'cancelling':
            job.update(status='cancelled',message='Отмена сохранена; поиск не возобновляется.')
        elif job.get('attempts',0) >= MAX_ATTEMPTS:
            job.update(status='interrupted',message='Поиск прерван после трёх запусков. Проверьте сервер и запустите его снова.')
        else:
            job.update(status='queued',restarts=job.get('restarts',0)+1,progress={'phase':'queued'},
                       message='Поиск восстановлен после остановки исполнителя. Он начнётся заново с теми же параметрами.')
        job.pop('started',None)
        self._write(db,job)

    def claim(self):
        # Idle coordinators and those observing another live worker are readers.
        # Recheck inside BEGIN IMMEDIATE before actually claiming anything.
        with self.connect() as db:
            owner=db.execute('SELECT token,expires FROM generation_owner WHERE slot=1').fetchone()
            if owner[0] and owner[1] > self.clock(): return None
            if not owner[0] and not db.execute("SELECT 1 FROM generation_queue WHERE status='queued' LIMIT 1").fetchone():
                return None
        with self.transaction() as db:
            owner = db.execute('SELECT token,job_id,expires FROM generation_owner WHERE slot=1').fetchone()
            if owner[0] and owner[2] > self.clock(): return None
            if owner[0]:
                job = self._load(db,owner[1])
                if job['status'] in ACTIVE: self._retry(db,job)
                db.execute('UPDATE generation_owner SET token=NULL,job_id=NULL,expires=0 WHERE slot=1')
            row = db.execute("SELECT job_id,request FROM generation_queue WHERE status='queued' ORDER BY created,job_id LIMIT 1").fetchone()
            if not row: return None
            job = self._load(db,row[0])
            token = str(uuid4())
            job.update(status='running',attempts=job.get('attempts',0)+1,started=self.clock(),elapsed=0,
                       progress={'phase':'building'},message='Генератор готовит поиск.')
            self._write(db,job)
            db.execute('UPDATE generation_owner SET token=?,job_id=?,expires=? WHERE slot=1',
                       (token,job['id'],self.clock()+self.lease_seconds))
            return token,job,json.loads(row[1])

    def _owns(self, db, token, id):
        return db.execute('SELECT 1 FROM generation_owner WHERE slot=1 AND token=? AND job_id=? AND expires>?',
                          (token,id,self.clock())).fetchone() is not None

    def heartbeat(self, token, id, progress):
        """Return ownership and cancellation. Expired ownership cannot be renewed."""
        with self.transaction() as db:
            if not self._owns(db,token,id): return False,True
            job = self._load(db,id)
            job['progress'] = dict(progress)
            job['elapsed'] = round(max(0,self.clock()-job['started']),1)
            self._write(db,job)
            db.execute('UPDATE generation_owner SET expires=? WHERE slot=1',(self.clock()+self.lease_seconds,))
            return True,job['status']=='cancelling'

    def finish(self, token, id, result, preview=None, *, restarting=False):
        """Publish preview + job together, only while still owning this attempt."""
        with self.transaction() as db:
            if not self._owns(db,token,id): return False
            job = self._load(db,id)
            job['elapsed'] = round(max(0,self.clock()-job['started']),1)
            if restarting and job['status'] != 'cancelling':
                self._retry(db,job)
            else:
                if job['status'] == 'cancelling':
                    result=dict(status='cancelled',message='Поиск отменён. Расписание не изменено.',details=[])
                if result['status'] == 'success':
                    current = db.execute('SELECT revision FROM workspaces WHERE id=?',(job['workspace'],)).fetchone()
                    if not current or current[0] != job['revision']:
                        result=dict(status='stale',message='Исходные данные изменились во время поиска. Запустите его для новой версии.',details=[])
                    elif preview is None:
                        raise ValueError('Successful queue result requires an independently checked preview')
                    else:
                        document,checked = preview
                        preview_id = str(uuid4())
                        db.execute('INSERT INTO previews VALUES (?,?,?,?,?,?)',
                                   (preview_id,job['workspace'],job['revision'],stamp(),document,int(checked['canApply'])))
                        db.execute('DELETE FROM previews WHERE workspace=? AND id NOT IN (SELECT id FROM previews WHERE workspace=? ORDER BY created DESC LIMIT 30)',(job['workspace'],job['workspace']))
                        result={**result,'preview':dict(token=preview_id,revision=job['revision'],**checked)}
                job.update(status='ready' if result['status']=='success' else result['status'],result=result,message=result['message'])
                self._write(db,job)
            db.execute('UPDATE generation_owner SET token=NULL,job_id=NULL,expires=0 WHERE slot=1')
        return True

    def cancel(self, id, workspace):
        with self.transaction() as db:
            job = self._load(db,id,workspace)
            if job['status']=='queued': job.update(status='cancelled',message='Поиск отменён до запуска.')
            elif job['status']=='running': job.update(status='cancelling',message='Останавливаем поиск…')
            self._write(db,job)
            return job

    def _public(self, job, tracked):
        if not tracked and job['status'] in ACTIVE:
            job.update(status='interrupted',message='В старом задании не сохранены параметры. Запустите поиск заново.')
        if job['status'] in ['running','cancelling'] and 'started' in job:
            job['elapsed']=round(max(0,self.clock()-job['started']),1)
        return job

    def get(self, id, workspace):
        with self.connect() as db:
            db.execute('BEGIN')
            job = self._load(db,id,workspace)
            control = db.execute('SELECT 1 FROM generation_queue WHERE job_id=?',(id,)).fetchone()
        return self._public(job,control is not None)

    def list(self, workspace):
        with self.connect() as db:
            rows=db.execute('''SELECT j.document,q.job_id FROM generation_jobs j
                LEFT JOIN generation_queue q ON q.job_id=j.id WHERE j.workspace=?
                ORDER BY j.created DESC,j.id DESC LIMIT 20''',(workspace,)).fetchall()
        return [self._public(json.loads(document),tracked is not None) for document,tracked in rows]

    def read_state(self, workspace, revision):
        from .models import State
        with self.connect() as db: row=db.execute('SELECT revision,document FROM workspaces WHERE id=?',(workspace,)).fetchone()
        if not row or row[0]!=revision: raise HTTPException(409,'Исходная версия изменилась, пока поиск ожидал в очереди.')
        return State.model_validate_json(row[1])
