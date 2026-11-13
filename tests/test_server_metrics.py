import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import json
import sqlite3
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from backend.server_metrics import RequestMetrics, MetricsMiddleware, ResourceSnapshot, COUNT_LIMIT, WINDOW
from backend.operations import register
from backend import api
from seeding import insert_row
from test_access import client, enable


class Clock:
    def __init__(self): self.now = 1000.
    def __call__(self): return self.now


async def send(message): pass
async def receive(): return {'type': 'http.disconnect'}


def scope(path='/api/private/student-123', kind='http'):
    return dict(type=kind, path=path, query_string=b'password=secret', headers=[(b'cookie', b'private-token')])


def test_stream_timing_includes_final_body_but_not_background_work():
    clock=Clock(); metrics=RequestMetrics(clock)
    async def streamed(scope, receive, send):
        await send(dict(type='http.response.start',status=200))
        clock.now+=.1
        await send(dict(type='http.response.body',body=b'private school body',more_body=True))
        assert metrics.snapshot()['inFlight']==1 and metrics.snapshot()['totalRequests']==0
        clock.now+=.2
        await send(dict(type='http.response.body',body=b'end'))
        assert metrics.snapshot()['inFlight']==0
        clock.now+=20  # Background work after delivery must not inflate response time.
    asyncio.run(MetricsMiddleware(streamed,metrics)(scope(),receive,send))
    result=metrics.snapshot()
    assert result['totalRequests']==1 and result['window']['averageMs']==300
    assert result['window']['p95UpperMs']>=result['window']['averageMs']
    assert all(word not in json.dumps(result) for word in ['private','student','password','token'])


@pytest.mark.parametrize('path,kind',[('/api/health','http'),('/api/ready','http'),('/api/access/operations','http'),('/assets/app.js','http'),('/api/ws','websocket')])
def test_excluded_requests_are_not_measured(path,kind):
    metrics=RequestMetrics()
    async def endpoint(scope,receive,send): pass
    asyncio.run(MetricsMiddleware(endpoint,metrics)(scope(path,kind),receive,send))
    assert metrics.snapshot()['totalRequests']==0


@pytest.mark.parametrize('error',[RuntimeError('private failure'),asyncio.CancelledError()])
def test_exception_and_cancellation_release_inflight_without_swallowing(error):
    metrics=RequestMetrics()
    async def failed(scope,receive,send): raise error
    with pytest.raises(type(error)):
        asyncio.run(MetricsMiddleware(failed,metrics)(scope(),receive,send))
    result=metrics.snapshot()
    assert result['inFlight']==0 and result['totalRequests']==1
    assert result['window']['incomplete']==1
    assert result['window']['serverErrors']==int(isinstance(error,RuntimeError))


def test_disconnect_during_send_records_incomplete_delivery_once():
    metrics=RequestMetrics()
    async def endpoint(scope,receive,send):
        await send(dict(type='http.response.start',status=200))
        await send(dict(type='http.response.body',body=b'x'))
    async def disconnected(message):
        if message['type']=='http.response.body': raise OSError('gone')
    with pytest.raises(OSError): asyncio.run(MetricsMiddleware(endpoint,metrics)(scope(),receive,disconnected))
    result=metrics.snapshot()
    assert result['totalRequests']==1 and result['inFlight']==0
    assert result['window']['incomplete']==1 and result['window']['serverErrors']==0


def test_p95_is_the_upper_bound_of_the_slowest_of_the_fastest_ninety_five_percent():
    clock=Clock();metrics=RequestMetrics(clock)
    for i in range(100):
        start=metrics.begin();clock.now+=.01 if i<95 else 31
        metrics.finish(start,200 if i<98 else 503)
    result=metrics.snapshot()
    assert 10<=result['window']['p95UpperMs']<1000
    assert result['window']['serverErrors']==result['window']['unavailable']==2


def test_requests_older_than_the_window_are_forgotten_but_still_counted_in_total():
    clock=Clock();metrics=RequestMetrics(clock)
    for _ in range(10):
        start=metrics.begin();clock.now+=.01;metrics.finish(start,200)
    clock.now+=WINDOW
    result=metrics.snapshot()
    assert result['window']['requests']==0 and result['window']['averageMs'] is None
    assert result['totalRequests']==10


def test_requests_slower_than_any_bucket_report_overflow_instead_of_a_bound():
    clock=Clock();metrics=RequestMetrics(clock)
    for _ in range(3):
        start=metrics.begin();clock.now+=3600;metrics.finish(start,429)
    result=metrics.snapshot()
    assert result['window']['p95Overflow'] and result['window']['p95UpperMs'] is None
    assert result['window']['rateLimited']==result['window']['clientErrors']==result['window']['requests']>0
    json.dumps(result,allow_nan=False)


def test_parallel_recording_keeps_every_request():
    metrics=RequestMetrics()
    def batch(_):
        for _ in range(200): metrics.finish(metrics.begin(),200)
    with ThreadPoolExecutor(max_workers=8) as pool: list(pool.map(batch,range(16)))
    result=metrics.snapshot()
    assert result['inFlight']==0 and result['totalRequests']==result['window']['requests']==3200


def test_actual_http_authorization_rejections_and_self_probes(client):
    enable(client)
    metrics=api.app.state.operations_metrics
    before=metrics.snapshot()['totalRequests']
    for path in ['/api/health','/api/ready','/api/access/operations']:
        assert client.get(path).status_code==200
    assert metrics.snapshot()['totalRequests']==before
    client.cookies.clear()
    assert client.get('/api/state?workspace=demo').status_code==401
    assert client.post('/api/auth/login',json={}).status_code==422
    result=metrics.snapshot()
    assert result['totalRequests']==before+2 and result['window']['clientErrors']>=2


def test_exception_handler_and_unhandled_500_are_both_observed(tmp_path):
    app=FastAPI();register(app,None,lambda:tmp_path/'absent.db')
    @app.get('/api/busy')
    def busy():
        error=sqlite3.OperationalError('secret');error.sqlite_errorcode=sqlite3.SQLITE_BUSY;raise error
    @app.get('/api/fail')
    def fail(): raise RuntimeError('private details')
    client=TestClient(app,raise_server_exceptions=False)
    assert client.get('/api/busy').status_code==503
    assert client.get('/api/fail').status_code==500
    result=app.state.operations_metrics.snapshot()
    assert result['totalRequests']==result['window']['serverErrors']==2
    assert result['window']['unavailable']==1 and result['window']['incomplete']==1


def test_resource_cache_ttl_and_database_switch(tmp_path):
    # The age of a snapshot restarts at 0 only when resources were collected again.
    ttl=5;clock=Clock();resources=ResourceSnapshot(clock,ttl=ttl)
    path=tmp_path/'one.db'
    assert resources.snapshot(path)['ageSeconds']==0
    clock.now+=ttl-1;assert resources.snapshot(path)['ageSeconds']==ttl-1
    clock.now+=1;assert resources.snapshot(path)['ageSeconds']==0
    clock.now+=1;assert resources.snapshot(path)['ageSeconds']==1
    assert resources.snapshot(tmp_path/'two.db')['ageSeconds']==0


def test_queue_counts_on_the_real_schema_are_capped_read_only_and_private(client):
    enable(client)
    with api.connect() as db:
        for i in range(COUNT_LIMIT+1):
            insert_row(db,'push_outbox',device_id=f'device-{i}',device_version=1,notification_id=i,generation=1,status='queued',attempts=0,available=0,expires=1,last_error='private pupil')
    answer=client.get('/api/access/operations')
    assert answer.status_code==200,answer.text
    queues={q['name']:q for q in answer.json()['resources']['queues']}
    assert queues['push']['counts']['queued']==dict(count=COUNT_LIMIT,capped=True)
    assert queues['push']['counts']['failed']==dict(count=0,capped=False)
    # Reading never creates the other queues' tables.
    assert not queues['generation']['initialized'] and not queues['reports']['initialized']
    assert 'private' not in answer.text and 'pupil' not in answer.text


def test_resources_absent_database_and_missing_index_do_not_bootstrap(tmp_path):
    path=tmp_path/'missing.db'
    assert ResourceSnapshot().snapshot(path)['queues'] is None and not path.exists()
    with closing(sqlite3.connect(path)) as db: db.execute('CREATE TABLE generation_queue (status TEXT)')
    assert ResourceSnapshot().snapshot(path)['queues'] is None


@pytest.mark.parametrize('free,level',[(50*1024**3,'normal'),(5*1024**3,'warning'),(100*1024**2,'critical')])
def test_disk_thresholds(tmp_path,monkeypatch,free,level):
    monkeypatch.setattr('backend.server_metrics.shutil.disk_usage',lambda path:SimpleNamespace(free=free,total=100*1024**3))
    assert ResourceSnapshot().snapshot(tmp_path/'absent.db')['disk']['level']==level


def test_resource_failures_are_unknown_not_empty_or_leaked(tmp_path,monkeypatch):
    def failed(path): raise OSError('private disk path')
    monkeypatch.setattr('backend.server_metrics.shutil.disk_usage',failed)
    result=ResourceSnapshot().snapshot(tmp_path/'absent.db')
    assert result['disk'] is None and result['queues'] is None and 'private' not in json.dumps(result)
