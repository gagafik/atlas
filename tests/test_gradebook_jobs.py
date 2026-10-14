from contextlib import closing
from pathlib import Path
import json,multiprocessing as mp,os,sqlite3,threading,time
import pytest
from backend import api
from backend.access import LOCAL
from backend.gradebook_jobs import Queue,Cancelled
from backend.gradebook_build import compile_book,build_isolated
from test_access import client,enable,create,signed
from test_journal import entry,lesson,write,graded
from test_journal_periods import period,close,action,period_clock
from test_final_grades import prepare,sheet,BASE,stop_gradebook_jobs


def setup(c):
    record=write(c,graded(entry(c,lesson(c)['lessonKey'])),publish=True).json()
    p=close(c,period(c));c.get(BASE+'/options?workspace=demo').raise_for_status()
    return p,record

def staged(c,tmp_path,q=None):
    p,record=setup(c);q=q or Queue(api.DB);created=q.enqueue('demo',p,LOCAL);token,job=q.claim();path=tmp_path/(job['id']+'.db')
    assert compile_book(q.path,job,path,threading.Event(),lambda p:None)['status']=='built'
    return q,token,job,path,p,record

def wait_job(c,id,terminal=('succeeded','cancelled','stale','failed'),timeout=15):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        r=c.get(BASE+'/preparations/'+id+'?workspace=demo');r.raise_for_status();value=r.json()
        if value['status'] in terminal:return value
        time.sleep(.03)
    pytest.fail(str(value))

def test_api_enqueue_idempotence_cancel_and_private_admin_scope(client):
    enable(client);create(client,'teacher');create(client,'parent');p,_=setup(client)
    api.solve_lock.acquire()
    try:
        r=client.post(BASE+'/periods/'+p['id']+'/prepare?workspace=demo',json=dict(version=p['version']));assert r.status_code==202,r.text
        job=r.json();same=client.post(BASE+'/periods/'+p['id']+'/prepare?workspace=demo',json=dict(version=p['version'])).json();assert same['id']==job['id']
        assert 'principal' not in same and 'token' not in same
        for c in [signed('teacher'),signed('parent')]:
            assert c.get(BASE+'/preparations?workspace=demo').status_code==403
            assert c.post(BASE+'/preparations/'+job['id']+'/cancel?workspace=demo').status_code==403
        assert client.get(BASE+'/preparations/'+job['id']+'?workspace=school').status_code==404
        # Waiting for CPU capacity must not prevent ordinary school writes.
        client.put('/api/notifications/preferences',json=dict(enabled=False)).raise_for_status()
        client.post(BASE+'/preparations/'+job['id']+'/cancel?workspace=demo').raise_for_status()
    finally:api.solve_lock.release()
    assert wait_job(client,job['id'])['status']=='cancelled'
    assert not client.get(BASE+'/options?workspace=demo').json()['books']

def test_import_is_invisible_until_complete_and_cache_reused(client,tmp_path,monkeypatch):
    q,token,job,path,p,record=staged(client,tmp_path)
    monkeypatch.setattr('backend.gradebook_jobs.CHUNK',1);observed=[];before=api.read('demo')
    def observe(progress):
        with q.connect() as db:
            if db.execute('SELECT 1 FROM grading_rows LIMIT 1').fetchone():
                id=db.execute('SELECT id FROM grading_rows LIMIT 1').fetchone()[0]
                assert client.get(BASE+'/rows/'+id+'/evidence?workspace=demo').status_code==404
                observed.append(True)
        assert not client.get(BASE+'/options?workspace=demo').json()['books']
        client.put('/api/notifications/preferences',json=dict(enabled=True)).raise_for_status()
    q.import_stage(job,token,path,threading.Event(),observe)
    assert observed and q.get(job['id'],'demo')['status']=='succeeded' and api.read('demo')==before
    b=dict(id=job['bookId']);rows=sheet(client,b).json()['items'];r=next(r for r in rows if r['source']['numeric'])
    first=client.get(BASE+'/rows/'+r['id']+'/evidence?workspace=demo').json()['items'];assert first
    reopened=action(client,p,'reopen').json();p2=close(client,reopened);second=prepare(client,p2)
    with q.connect() as db:
        assert db.execute('SELECT count(*) FROM grading_facts').fetchone()[0]==2
        assert db.execute('SELECT count(*) FROM grading_projected').fetchone()[0]==1
        assert db.execute('SELECT count(*) FROM grading_marks').fetchone()[0]==0
    r2=next(v for v in sheet(client,second).json()['items'] if v['studentId']==r['studentId'] and v['subjectId']==r['subjectId'])
    assert client.get(BASE+'/rows/'+r2['id']+'/evidence?workspace=demo').json()['items']==first

def test_cancel_mid_import_keeps_no_partial_book(client,tmp_path,monkeypatch):
    q,token,job,path,p,_=staged(client,tmp_path);monkeypatch.setattr('backend.gradebook_jobs.CHUNK',1)
    def cancel(progress):q.cancel(job['id'],'demo',LOCAL)
    with pytest.raises(Cancelled):q.import_stage(job,token,path,threading.Event(),cancel)
    q.cleanup_pending(job);q.finish(job['id'],token,dict(status='cancelled'))
    assert q.get(job['id'],'demo')['status']=='cancelled'
    with q.connect() as db:
        assert db.execute('SELECT count(*) FROM grading_books').fetchone()[0]==0
        assert db.execute('SELECT count(*) FROM grading_rows').fetchone()[0]==0
        assert db.execute('SELECT count(*) FROM grading_projected').fetchone()[0]==0
    # An unfinished cache chunk may be reused only after its lesson marker commits.
    assert prepare(client,p)['id']

def test_stale_period_and_revoked_initiator_never_publish(client,tmp_path):
    q,token,job,path,p,_=staged(client,tmp_path)
    action(client,p,'reopen').raise_for_status()
    with pytest.raises(Cancelled):q.import_stage(job,token,path,threading.Event(),lambda p:None)
    q.finish(job['id'],token,dict(status='cancelled'));assert q.get(job['id'],'demo')['status']=='stale'
    admin=enable(client);p2=close(client,client.get('/api/journal/periods?workspace=demo').json()['items'][0])
    j=q.enqueue('demo',p2,admin);token,job=q.claim()
    with q.connect() as db:db.execute('UPDATE accounts SET active=0 WHERE id=?',(admin['id'],))
    assert q.heartbeat(j['id'],token,{})==(False,True)
    assert q.get(j['id'],'demo')['status']=='stale'
    with q.connect() as db:assert db.execute('SELECT count(*) FROM grading_books').fetchone()[0]==0

def test_expired_owner_cannot_publish_or_remove_new_attempt_rows(client,tmp_path):
    clock=[100.0];q=Queue(api.DB,clock=lambda:clock[0],lease=1)
    q,token,job,path,p,_=staged(client,tmp_path,q)
    clock[0]=102;new_token,new_job=q.claim();assert new_token!=token and new_job['bookId']!=job['bookId'] and new_job['attempts']==2
    with q.connect() as db:db.execute('INSERT INTO grading_rows VALUES (?,?,?,?,?,?,?,?,?,?,?)',('demo','new-row',new_job['bookId'],'s1','sub0','c-demo-a','',1,'draft','{}',None))
    q.cleanup_pending(job)
    with q.connect() as db:assert db.execute("SELECT count(*) FROM grading_rows WHERE id='new-row'").fetchone()[0]==1
    assert q.heartbeat(job['id'],token,{})==(False,True)
    with pytest.raises(Cancelled):q.import_stage(job,token,path,threading.Event(),lambda p:None)
    q.finish(job['id'],token,dict(status='failed'));assert q.get(job['id'],'demo')['attempts']==2
    clock[0]=104;assert q.claim()[1]['attempts']==3
    clock[0]=106;assert q.claim() is None;q.reap()
    assert q.get(job['id'],'demo')['status']=='failed'
    with q.connect() as db:assert db.execute('SELECT count(*) FROM grading_rows').fetchone()[0]==0

def claim_and_crash(path,ready):
    q=Queue(path,lease=.3);assert q.claim();ready.set();os._exit(23)

def test_worker_process_crash_is_recovered_from_durable_job(client,tmp_path,monkeypatch):
    p,_=setup(client);q=Queue(api.DB,lease=.3);j=q.enqueue('demo',p,LOCAL)
    monkeypatch.setenv('TIMETABLE_DB',str(api.DB));ctx=mp.get_context('spawn');ready=ctx.Event();child=ctx.Process(target=claim_and_crash,args=(str(api.DB),ready))
    child.start()
    try:assert ready.wait(10);child.join(10);assert child.exitcode==23
    finally:
        if child.is_alive():child.terminate();child.join(3)
        child.close()
    time.sleep(.35);token,job=q.claim();assert job['id']==j['id'] and job['attempts']==2
    path=tmp_path/'recovered.db';compile_book(api.DB,job,path,threading.Event(),lambda p:None);q.heartbeat(job['id'],token,{})
    q.import_stage(job,token,path,threading.Event(),lambda p:None);assert q.get(job['id'],'demo')['status']=='succeeded'

def never_finishes(path,job,stage,cancel,channel):
    Path(stage).write_text('owned staging file',encoding='utf-8')
    while True:time.sleep(.05)

def test_isolated_child_cancellation_is_bounded_and_joined(client,tmp_path,monkeypatch):
    monkeypatch.setenv('TIMETABLE_DB',str(api.DB));cancel=threading.Event();timer=threading.Timer(.2,cancel.set);timer.start();before={p.pid for p in mp.active_children()}
    try:r=build_isolated(api.DB,{},tmp_path/'owned.db',cancel,lambda p:None,target=never_finishes,wall_seconds=10)
    finally:timer.cancel()
    assert r['status']=='cancelled' and {p.pid for p in mp.active_children()}==before


def test_fresh_server_lifespan_initializes_journal_dependencies(tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    monkeypatch.setattr(api,'DB',tmp_path/'fresh.db')
    with TestClient(api.app,headers={'X-Atlas-Request':'1'}) as c:
        response=c.get(BASE+'/options?workspace=demo');assert response.status_code==200,response.text
        assert response.json()['books']==[]
        assert c.get(BASE+'/preparations?workspace=demo').json()['items']==[]


def test_shared_evidence_pages_exclude_other_closures_and_empty_lessons(client,tmp_path):
    q,token,job,path,p,_=staged(client,tmp_path)
    q.import_stage(job,token,path,threading.Event(),lambda p:None)
    row=next(r for r in sheet(client,dict(id=job['bookId'])).json()['items'] if r['source']['numeric'])
    url=BASE+'/rows/'+row['id']+'/evidence?workspace=demo'
    initial=client.get(url).json()['items'];expected=[x['assessment'] for x in initial]
    with q.connect() as db:
        original=db.execute('SELECT * FROM journal_period_snapshots LIMIT 1').fetchone()
        columns=[r[1] for r in db.execute('PRAGMA table_info(journal_period_snapshots)')]
        for n in range(80):
            key='synthetic-'+str(n);works=[dict(assessment=f'Work {n}-{i}',kind='score',score=i,label='',maxScore=10,weight=1) for i in range(10)] if n>=60 else []
            db.execute('INSERT INTO grading_facts(workspace,lesson_key,version,student_id,subject_id,day,start,summary,works) VALUES (?,?,?,?,?,?,?,?,?)',('demo',key,1,row['studentId'],row['subjectId'],p['start'],'08:00','{}',json.dumps(works)))
            # The final lesson is deliberately outside this closure.
            if n<79:
                values=list(original);values[columns.index('lesson_key')]=key
                db.execute('INSERT INTO journal_period_snapshots VALUES ('+','.join('?' for _ in values)+')',values)
                expected.extend(w['assessment'] for w in works)
    seen=[];after=0;ids=set()
    for _ in range(10):
        page=client.get(url+'&after='+str(after)).json();assert len(page['items'])<=50
        for item in page['items']:
            assert item['id'] not in ids;ids.add(item['id']);seen.append(item['assessment'])
        if page['nextAfter'] is None:break
        assert page['nextAfter']>after;after=page['nextAfter']
    else:pytest.fail('Evidence pagination did not finish')
    assert seen==expected and len(seen)==len(initial)+190


def test_queue_limits_and_rechecks_revoked_access_on_pinned_connection(client):
    admin=enable(client);p,_=setup(client);q=Queue(api.DB)
    jobs=[q.enqueue('demo',p,admin)]
    for n in range(2):jobs.append(q.enqueue('demo',close(client,period(client,name='Extra '+str(n))),admin))
    extra=close(client,period(client,name='Full queue'))
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as error:q.enqueue('demo',extra,admin)
    assert error.value.status_code==409
    with q.connect() as db:db.execute('UPDATE accounts SET active=0 WHERE id=?',(admin['id'],))
    for work in [lambda:q.get(jobs[0]['id'],'demo',admin),lambda:q.listing('demo',admin),lambda:q.cancel(jobs[0]['id'],'demo',admin)]:
        with pytest.raises(HTTPException) as error:work()
        assert error.value.status_code==409
    assert all(q.get(j['id'],'demo')['status']=='queued' for j in jobs)


def test_crashed_import_reopened_period_reaps_hidden_rows(client,tmp_path):
    clock=[100.0];q=Queue(api.DB,clock=lambda:clock[0],lease=1)
    q,token,job,path,p,_=staged(client,tmp_path,q)
    with q.connect() as db:db.execute('INSERT INTO grading_rows VALUES (?,?,?,?,?,?,?,?,?,?,?)',('demo','abandoned',job['bookId'],'s1','sub0','c-demo-a','',1,'draft','{}',None))
    action(client,p,'reopen').raise_for_status();clock[0]=102
    assert q.claim() is None;q.reap();assert q.get(job['id'],'demo')['status']=='stale'
    with q.connect() as db:assert not db.execute('SELECT 1 FROM grading_rows').fetchone()


def test_shutdown_requeues_and_resume_stays_with_original_database(client,tmp_path,monkeypatch):
    from backend.gradebook_jobs import Runner
    p,_=setup(client);q=Queue(api.DB);job=q.enqueue('demo',p,LOCAL)
    lock=threading.Lock();lock.acquire();runner=Runner(q,lock);runner.start()
    try:
        deadline=time.monotonic()+10
        while q.get(job['id'],'demo')['status']!='running':
            assert time.monotonic()<deadline;time.sleep(.03)
        runner.shutdown();assert q.get(job['id'],'demo')['status']=='queued'
        other=tmp_path/'other-school.db'
        with closing(sqlite3.connect(other)) as db:db.execute('CREATE TABLE sentinel(value TEXT)')
        monkeypatch.setenv('TIMETABLE_DB',str(q.path));monkeypatch.setattr(api,'DB',other)
        lock.release();runner.start();deadline=time.monotonic()+15
        while q.get(job['id'],'demo')['status'] in ['queued','running']:
            assert time.monotonic()<deadline;time.sleep(.03)
        assert q.get(job['id'],'demo')['status']=='succeeded'
        with closing(sqlite3.connect(other)) as db:assert db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()==[('sentinel',)]
    finally:runner.shutdown()
