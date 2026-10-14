from concurrent.futures import ThreadPoolExecutor
import json
import multiprocessing as mp
import os
import threading
import time

from fastapi import HTTPException
from fastapi.testclient import TestClient
import pytest

from backend import api, jobs
from backend.generation_queue import GenerationQueue
from backend.jobs import JobRequest, Runner
from test_jobs import client


def wait_status(queue, id, status, timeout=10):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        result=queue.get(id,'demo')
        if result['status'] in status: return result
        time.sleep(.03)
    pytest.fail(f'Expected {status}; got {result}')


def child_claim_and_exit(path, ready):
    queue=GenerationQueue(path,lease_seconds=.8)
    assert queue.claim() is not None
    ready.set()
    os._exit(23)


def child_competitor(path, start, output):
    queue=GenerationQueue(path)
    start.wait(10)
    result=queue.claim()
    output.put(result[1]['id'] if result else None)


def test_global_claim_across_processes_and_atomic_queue_limit(client):
    queue=GenerationQueue(api.DB)
    def submit(_):
        try: return GenerationQueue(api.DB).enqueue('demo',JobRequest(revision=0))['id']
        except HTTPException as e: return e.status_code
    with ThreadPoolExecutor(max_workers=8) as pool: results=list(pool.map(submit,range(12)))
    assert len([r for r in results if isinstance(r,str)])==3
    assert results.count(409)==9
    ctx=mp.get_context('spawn'); start=ctx.Event(); output=ctx.Queue()
    processes=[ctx.Process(target=child_competitor,args=(str(api.DB),start,output)) for _ in range(3)]
    try:
        for process in processes: process.start()
        start.set()
        claimed=[output.get(timeout=15) for _ in processes]
        for process in processes:
            process.join(10); assert process.exitcode==0
        assert len([x for x in claimed if x is not None])==1
        assert sum(j['status']=='running' for j in queue.list('demo'))==1
        assert sum(j['status']=='queued' for j in queue.list('demo'))==2
        waiting=next(j for j in queue.list('demo') if j['status']=='queued')
        queue.cancel(waiting['id'],'demo')
        queue.enqueue('demo',JobRequest(revision=0))
    finally:
        for process in processes:
            if process.is_alive(): process.terminate(); process.join(5)
            process.close()
        output.close(); output.join_thread()


def test_expired_owner_is_fenced_and_only_current_attempt_publishes(client):
    clock=[1000.0]
    first=GenerationQueue(api.DB,lease_seconds=10,clock=lambda:clock[0])
    second=GenerationQueue(api.DB,lease_seconds=10,clock=lambda:clock[0])
    original=api.read('demo')
    job=first.enqueue('demo',JobRequest(revision=0,workloadIds=['w0'],seconds=7,cycleIds=['weekly'],optimization={'early':3,'stability':0,'preferences':2}))
    token,attempt,request=first.claim()
    assert attempt['attempts']==1 and second.claim() is None
    clock[0]+=9
    assert first.heartbeat(token,job['id'],{'phase':'searching','solutions':1})==(True,False)
    clock[0]+=9
    assert second.claim() is None
    clock[0]+=2
    next_token,next_attempt,next_request=second.claim()
    assert next_request==request and next_attempt['attempts']==2 and next_attempt['restarts']==1
    assert first.heartbeat(token,job['id'],{})==(False,True)
    preview=(original.model_dump_json(),dict(canApply=True,diff={},report={},newIssues=[]))
    success=dict(status='success',message='Prepared')
    assert first.finish(token,job['id'],success,preview) is False
    with api.connect() as db: assert db.execute('SELECT count(*) FROM previews').fetchone()[0]==0
    assert second.finish(next_token,job['id'],success,preview) is True
    assert first.finish(token,job['id'],dict(status='failed',message='Old worker')) is False
    assert first.get(job['id'],'demo')['status']=='ready'
    with api.connect() as db: assert db.execute('SELECT count(*) FROM previews').fetchone()[0]==1
    assert api.read('demo')==original


def test_cancellation_survives_restart_and_wins_before_publication(client):
    clock=[1000.0]; queue=GenerationQueue(api.DB,lease_seconds=2,clock=lambda:clock[0])
    job=queue.enqueue('demo',JobRequest(revision=0))
    token,_,_=queue.claim()
    queue.cancel(job['id'],'demo')
    assert queue.heartbeat(token,job['id'],{})==(True,True)
    state=api.read('demo')
    preview=(state.model_dump_json(),dict(canApply=True))
    assert queue.finish(token,job['id'],dict(status='success',message='Candidate'),preview)
    assert queue.get(job['id'],'demo')['status']=='cancelled'
    with api.connect() as db: assert db.execute('SELECT count(*) FROM previews').fetchone()[0]==0
    second=queue.enqueue('demo',JobRequest(revision=0)); queue.claim()
    queue.cancel(second['id'],'demo'); clock[0]+=3
    assert queue.claim() is None
    assert queue.get(second['id'],'demo')['status']=='cancelled'
    with pytest.raises(HTTPException) as exc: queue.cancel(second['id'],'school')
    assert exc.value.status_code==404


def test_recovery_has_bounded_retries_and_preserves_queued_work(client):
    clock=[1000.0]; queue=GenerationQueue(api.DB,lease_seconds=2,clock=lambda:clock[0])
    first=queue.enqueue('demo',JobRequest(revision=0))
    for attempt in range(1,4):
        claim=queue.claim()
        assert claim[1]['attempts']==attempt
        clock[0]+=3
    assert queue.claim() is None
    assert queue.get(first['id'],'demo')['status']=='interrupted'
    next_job=queue.enqueue('demo',JobRequest(revision=0))
    assert queue.claim()[1]['id']==next_job['id']


def test_stale_state_cannot_publish_or_apply_after_queued_or_running_search(client):
    queue=GenerationQueue(api.DB)
    job=queue.enqueue('demo',JobRequest(revision=0));token,_,_=queue.claim()
    state=api.read('demo'); changed=state.model_copy(deep=True); changed.settings.name='Changed while searching'; api.save('demo',changed)
    preview=(state.model_dump_json(),dict(canApply=True))
    assert queue.finish(token,job['id'],dict(status='success',message='Candidate'),preview)
    assert queue.get(job['id'],'demo')['status']=='stale'
    with api.connect() as db: assert db.execute('SELECT count(*) FROM previews').fetchone()[0]==0
    with pytest.raises(HTTPException) as exc: queue.enqueue('demo',JobRequest(revision=0))
    assert exc.value.status_code==409
    with pytest.raises(HTTPException) as exc: queue.read_state('demo',0)
    assert exc.value.status_code==409


def test_shutdown_requeues_and_new_coordinator_recovers_without_api_polling(client,monkeypatch):
    queue=GenerationQueue(api.DB); before=api.read('demo')
    job=queue.enqueue('demo',JobRequest(revision=0))
    entered=threading.Event()
    def waiting(state,ids,seconds,cancel,progress,**options):
        entered.set(); assert cancel.wait(5)
        return dict(status='cancelled',message='Stopped',details=[])
    monkeypatch.setattr(jobs,'solve',waiting)
    runner=Runner(queue,threading.Lock(),heartbeat_seconds=.05)
    runner.start()
    try: assert entered.wait(5)
    finally: runner.shutdown()
    assert not runner.thread.is_alive()
    assert queue.get(job['id'],'demo')['status']=='queued'
    monkeypatch.setattr(jobs,'solve',lambda state,*args,**kw: dict(status='success',message='Candidate',placements=state.placements,details=[]))
    # Lifecycle startup must recover it without GET /generation-jobs starting a worker.
    with TestClient(api.app):
        result=wait_status(queue,job['id'],['ready'])
        assert result['attempts']==2 and result['restarts']==1
    assert api.read('demo')==before


def test_process_crash_recovery_preserves_queue_and_creates_real_preview(client):
    queue=GenerationQueue(api.DB); before=api.read('demo')
    first=queue.enqueue('demo',JobRequest(revision=0,seconds=2))
    second=queue.enqueue('demo',JobRequest(revision=0,seconds=2))
    ctx=mp.get_context('spawn');ready=ctx.Event()
    process=ctx.Process(target=child_claim_and_exit,args=(str(api.DB),ready))
    try:
        process.start(); assert ready.wait(10)
        process.join(10); assert process.exitcode==23
    finally:
        if process.is_alive(): process.terminate(); process.join(5)
        process.close()
    assert queue.get(first['id'],'demo')['status']=='running'
    assert queue.get(second['id'],'demo')['status']=='queued'
    runner=Runner(queue,threading.Lock(),heartbeat_seconds=.1)
    runner.start()
    try:
        recovered=wait_status(queue,first['id'],['ready'],timeout=20)
        waiting=wait_status(queue,second['id'],['ready'],timeout=20)
        assert recovered['attempts']==2 and recovered['restarts']==1
        assert waiting['attempts']==1
        assert recovered['result']['preview']['canApply']
        assert recovered['progress']['execution']=='process'
    finally: runner.shutdown()
    assert api.read('demo')==before
    with api.connect() as db: assert db.execute('SELECT count(*) FROM previews').fetchone()[0]==2


def test_legacy_jobs_migrate_idempotently_and_invalid_records_stay_interrupted(client):
    queue=GenerationQueue(api.DB)
    legacy=dict(id='legacy',status='running',**JobRequest(revision=0,seconds=2).model_dump())
    with api.connect() as db:
        db.execute('INSERT INTO generation_jobs VALUES (?,?,?,?)',('legacy','demo','2026-01-01',json.dumps(legacy)))
        db.execute('INSERT INTO generation_jobs VALUES (?,?,?,?)',('bad','demo','2026-01-01',json.dumps(dict(id='bad',status='queued'))))
        db.execute('INSERT INTO generation_jobs VALUES (?,?,?,?)',('cancel','demo','2026-01-01',json.dumps(dict(id='cancel',status='cancelling'))))
    queue.migrate(JobRequest); queue.migrate(JobRequest)
    assert queue.get('legacy','demo')['status']=='queued'
    assert queue.get('legacy','demo')['restarts']==1
    assert queue.get('bad','demo')['status']=='interrupted'
    assert queue.get('cancel','demo')['status']=='cancelled'
    with api.connect() as db: assert db.execute('SELECT count(*) FROM generation_queue').fetchone()[0]==1


def test_running_queue_keeps_its_database_when_global_path_changes(client,monkeypatch,tmp_path):
    queue=GenerationQueue(api.DB);before=api.read('demo')
    job=queue.enqueue('demo',JobRequest(revision=0))
    monkeypatch.setattr(api,'DB',tmp_path/'another.db')
    assert queue.read_state('demo',0)==before
    assert queue.claim()[1]['id']==job['id']
    assert not api.DB.exists()
