import asyncio
from datetime import date, timedelta
import sqlite3
import threading

import httpx
import pytest

from backend import api, portal_cache
from backend.models import Placement, Student
from backend.portal_cache import PortalSnapshots, Snapshot
from backend.seed import initial_state
from test_access import client, enable, create, signed


def test_snapshot_reuses_day_for_distinct_users_and_copies_responses(monkeypatch):
    state=initial_state('demo')
    state.placements=[Placement(id='p',workloadId='w4',day=0,period=2,roomId='r0')]
    snapshot=Snapshot(state)
    original=portal_cache.resolve_day
    calls=[]
    def resolve(*args): calls.append(1); return original(*args)
    monkeypatch.setattr(portal_cache,'resolve_day',resolve)
    day=date(2026,8,17)
    first=snapshot.day_rows(day,'student','s1')
    assert first and snapshot.day_rows(day,'student','s3')==first
    assert snapshot.day_rows(day,'student','s2')==[]
    # Only a teacher's own rows carry the substitution type; the rest is shared.
    assert [{k:v for k,v in r.items() if k!='changeType'} for r in snapshot.day_rows(day,'teacher','t0')]==first
    first[0]['subject']='caller mutation'
    assert snapshot.day_rows(day,'student','s1')[0]['subject']=='Математика'
    assert calls==[1]


def test_cache_replaces_changed_revision_and_database_and_is_bounded():
    current=initial_state('demo'); identity='one'; reads=[]
    def read(_): reads.append(1); return current.model_copy(deep=True)
    cache=PortalSnapshots(read,lambda _: (identity,current.revision))
    first=cache.get('demo')
    assert cache.get('demo') is first and len(reads)==1
    current.revision+=1
    assert cache.get('demo') is not first
    identity='two'
    assert cache.get('demo').state.revision==current.revision and len(reads)==3
    identity='three'; cache.get('demo')
    assert len(cache.cache)==2 and ('one','demo') not in cache.cache


def test_per_snapshot_day_and_week_limits_and_week_copy():
    state=initial_state('demo')
    state.students.extend(Student(id=f'new{i}',name=f'New {i}',classId='c-demo-a') for i in range(130))
    state.placements=[Placement(id='p',workloadId='w0',day=0,period=2,roomId='r0')]
    snapshot=Snapshot(state)
    for n in range(12): snapshot.day_rows(date(2026,8,17)+timedelta(days=n),'student','s1')
    for n in range(130): snapshot.week_rows('weekly','student',f'new{n}')
    assert len(snapshot.days)==8 and len(snapshot.weeks)==128
    row=snapshot.week_rows('weekly','student','new129'); row[0]['subject']='changed'
    assert snapshot.week_rows('weekly','student','new129')[0]['subject']!='changed'


def test_warmed_portal_invalidates_on_save_undo_and_permission_revocation(client):
    enable(client); user=create(client,'student'); viewer=signed('student')
    path='/api/portal/schedule?workspace=demo&view=student&entityId=s1&day=2026-08-17'
    before=viewer.get(path).json()
    state=api.read('demo'); state.subjects[2].name='Changed subject'; api.save('demo',state)
    after=viewer.get(path).json()
    assert after['revision']>before['revision'] and after['rows'][0]['subject']=='Changed subject'
    assert client.post('/api/undo?workspace=demo',json={'revision':after['revision']}).status_code==200
    assert viewer.get(path).json()['rows']==before['rows']
    body={k:v for k,v in user.items() if k!='id'}
    body['grants'][0]['entityIds']=['s2']
    assert client.put('/api/access/users/'+user['id'],json=body).status_code==200
    assert viewer.get(path).status_code==401
    viewer=signed('student')
    assert viewer.get(path).status_code==403
    assert viewer.get(path.replace('entityId=s1','entityId=s2')).status_code==200


def test_connection_context_closes_after_commit_and_rollback(tmp_path,monkeypatch):
    monkeypatch.setattr(api,'DB',tmp_path/'connection.sqlite')
    with api.connect() as committed: committed.execute('CREATE TABLE sample (value TEXT)')
    with pytest.raises(sqlite3.ProgrammingError): committed.execute('SELECT 1')
    with pytest.raises(RuntimeError):
        with api.connect() as rolled_back:
            rolled_back.execute("INSERT INTO sample VALUES ('x')")
            raise RuntimeError('rollback')
    with pytest.raises(sqlite3.ProgrammingError): rolled_back.execute('SELECT 1')
    with api.connect(read_only=True) as read_only:
        assert read_only.execute('SELECT count(*) FROM sample').fetchone()[0]==0
        with pytest.raises(sqlite3.OperationalError): read_only.execute("INSERT INTO sample VALUES ('no')")


def test_session_lookup_is_outside_event_loop_and_status_does_not_repeat(client,monkeypatch):
    enable(client)
    calls=[]; original=api.access.identify
    def identify(request): calls.append(threading.get_ident()); return original(request)
    monkeypatch.setattr(api.access,'identify',identify)
    async def request():
        loop_thread=threading.get_ident()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api.app),base_url='http://testserver') as browser:
            response=await browser.get('/api/auth/status')
        assert response.status_code==200 and response.json()['enabled']
        assert len(calls)==1 and calls[0]!=loop_thread
    asyncio.run(request())
