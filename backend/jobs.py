"""Queue coordinator; durable state is shared, each solve uses a disposable child."""
from contextlib import asynccontextmanager
import logging
from pathlib import Path
import threading

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from .solver_process import solve_isolated as solve
from .optimization import Optimization
from .generation_queue import GenerationQueue
from .editing import inspect_edit


class JobRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    revision: int = Field(ge=0)
    workloadIds: list[str] | None = Field(default=None,max_length=10000)
    seconds: int = Field(default=30, ge=1, le=600)
    cycleIds: list[str] | None = Field(default=None,max_length=32)
    optimization: Optimization = Field(default_factory=Optimization)


class Runner:
    def __init__(self, queue, solve_lock, *, heartbeat_seconds=1):
        self.queue, self.solve_lock = queue,solve_lock
        self.heartbeat_seconds = heartbeat_seconds
        self.stop = threading.Event()
        self.wake = threading.Event()
        self.guard = threading.Lock()
        self.thread = None

    def start(self):
        with self.guard:
            if self.thread is None or not self.thread.is_alive():
                self.stop.clear()
                self.thread=threading.Thread(target=self.loop,name='generation-coordinator',daemon=True)
                self.thread.start()
            self.wake.set()

    def shutdown(self):
        self.stop.set(); self.wake.set()
        if self.thread: self.thread.join(timeout=12)
        if self.thread and self.thread.is_alive():
            logging.warning('Generator coordinator is still stopping; its lease will expire if the process exits')

    def loop(self):
        while not self.stop.is_set():
            try:
                claimed=self.queue.claim()
                if claimed:
                    self.work(*claimed)
                    continue
            except Exception:
                logging.exception('Generation queue coordinator error')
            self.wake.wait(1)
            self.wake.clear()

    def work(self, token, job, request):
        cancel, monitor_done = threading.Event(),threading.Event()
        progress_lock=threading.Lock()
        progress=dict(job['progress'])
        lost=threading.Event()
        def report(value):
            with progress_lock: progress.update(value)
        def monitor():
            while not monitor_done.wait(self.heartbeat_seconds):
                try:
                    with progress_lock: snapshot=dict(progress)
                    owned,cancelling=self.queue.heartbeat(token,job['id'],snapshot)
                    if not owned: lost.set()
                    if not owned or cancelling or self.stop.is_set(): cancel.set()
                    if not owned: return
                except Exception:
                    # A worker unable to prove ownership must stop its child.
                    logging.exception('Unable to renew generator ownership')
                    lost.set(); cancel.set(); return
        observer=threading.Thread(target=monitor,name='generator-lease',daemon=True)
        observer.start()
        acquired=False
        result=dict(status='cancelled',message='Поиск отменён. Расписание не изменено.',details=[])
        preview=None
        try:
            while not cancel.is_set() and not self.stop.is_set():
                if self.solve_lock.acquire(timeout=.2): acquired=True; break
            if acquired and not cancel.is_set() and not self.stop.is_set():
                body=JobRequest.model_validate(request)
                state=self.queue.read_state(job['workspace'],body.revision)
                options=dict(optimization=body.optimization)
                if body.cycleIds is not None: options['cycle_ids']=body.cycleIds
                result=solve(state,body.workloadIds,body.seconds,cancel=cancel,progress=report,**options)
                if result['status']=='success' and not cancel.is_set() and not self.stop.is_set():
                    report({'phase':'reviewing'})
                    candidate=state.model_copy(update={'placements':result.pop('placements')})
                    checked=inspect_edit(state,candidate)
                    preview=(candidate.model_dump_json(),checked)
                elif result['status']=='success':
                    result=dict(status='cancelled',message='Поиск остановлен; расписание не изменено.',details=[])
        except HTTPException as exc:
            result=dict(status='stale',message=str(exc.detail),details=[])
        except Exception:
            logging.exception('Generation job failed')
            result=dict(status='failed',message='Ошибка поиска. Расписание не изменено; подробности в журнале сервера.',details=[])
        finally:
            if acquired: self.solve_lock.release()
            try:
                if not lost.is_set():
                    with progress_lock: snapshot=dict(progress)
                    owned,_=self.queue.heartbeat(token,job['id'],snapshot)
                    if owned: self.queue.finish(token,job['id'],result,preview,restarting=self.stop.is_set())
            finally:
                monitor_done.set(); observer.join(timeout=6)


def register_jobs(app, Workspace, require_revision, database_path, solve_lock):
    runners={}
    guard=threading.Lock()

    def runner():
        path=str(Path(database_path()).resolve())
        with guard:
            if path not in runners:
                queue=GenerationQueue(path)
                queue.migrate(JobRequest)
                runners[path]=Runner(queue,solve_lock)
            return runners[path]

    def shutdown():
        with guard:
            current=list(runners.values()); runners.clear()
        for item in current: item.shutdown()

    previous=app.router.lifespan_context
    @asynccontextmanager
    async def lifespan(application):
        async with previous(application):
            await run_in_threadpool(lambda: runner().start())
            try: yield
            finally: await run_in_threadpool(shutdown)
    app.router.lifespan_context=lifespan

    @app.post('/api/generation-jobs')
    def start_job(body: JobRequest, workspace: Workspace = 'school'):
        state=require_revision(workspace,body.revision)
        if body.workloadIds is not None and (not body.workloadIds or set(body.workloadIds)-{w.id for w in state.workloads}):
            raise HTTPException(422,'Выберите существующую нагрузку для поиска.')
        if body.cycleIds is not None and (not body.cycleIds or set(body.cycleIds)-{p.id for p in state.cycles.patterns}):
            raise HTTPException(422,'Выберите существующие недели для поиска.')
        item=runner()
        job=item.queue.enqueue(workspace,body)
        item.start()
        return job

    @app.get('/api/generation-jobs')
    def list_jobs(workspace: Workspace = 'school'):
        item=runner(); item.start()
        return item.queue.list(workspace)

    @app.get('/api/generation-jobs/{id}')
    def get_job(id: str, workspace: Workspace = 'school'):
        item=runner(); item.start()
        return item.queue.get(id,workspace)

    @app.post('/api/generation-jobs/{id}/cancel')
    def cancel_job(id: str, workspace: Workspace = 'school'):
        item=runner()
        result=item.queue.cancel(id,workspace)
        item.start()
        return result

    return shutdown
