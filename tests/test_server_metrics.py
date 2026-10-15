import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import hashlib
import json
import sqlite3
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from backend.server_metrics import RequestMetrics, MetricsMiddleware, ResourceSnapshot, QUEUES, WINDOW
from backend.operations import register
from backend import api
from test_access import client, enable, create, PASSWORD


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
    assert result['window']['p95UpperMs']==500
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


def test_histogram_percentile_expiry_and_fixed_memory():
    clock=Clock();metrics=RequestMetrics(clock)
    for i in range(100):
        start=metrics.begin();clock.now+=.01 if i<95 else 31
        metrics.finish(start,200 if i<98 else 503)
    result=metrics.snapshot()
    assert result['window']['p95UpperMs']==50
    assert result['window']['serverErrors']==result['window']['unavailable']==2
    clock.now+=WINDOW
    result=metrics.snapshot()
    assert result['window']['requests']==0 and result['window']['averageMs'] is None
    assert result['totalRequests']==100
    for _ in range(1000):
        start=metrics.begin();clock.now+=31;metrics.finish(start,429)
    assert len(metrics.buckets)==WINDOW
    result=metrics.snapshot()
    assert result['window']['p95Overflow'] and result['window']['p95UpperMs'] is None
    assert result['window']['rateLimited']==result['window']['clientErrors']==result['window']['requests']
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


def test_resource_cache_ttl_and_database_switch(tmp_path,monkeypatch):
    clock=Clock();resources=ResourceSnapshot(clock);calls=[]
    monkeypatch.setattr(resources,'collect',lambda path: calls.append(path) or {'disk':None,'queues':None})
    path=tmp_path/'one.db'
    resources.snapshot(path);clock.now+=9;assert resources.snapshot(path)['ageSeconds']==9
    assert len(calls)==1
    clock.now+=1;resources.snapshot(path);assert len(calls)==2
    resources.snapshot(tmp_path/'two.db');assert len(calls)==3


def test_indexed_queue_counts_are_capped_read_only_and_private(tmp_path):
    path=tmp_path/'private-school.db'
    with closing(sqlite3.connect(path)) as db:
        for _,table,index,_ in QUEUES:
            db.execute(f'CREATE TABLE {table} (status TEXT, secret TEXT)')
            db.execute(f'CREATE INDEX {index} ON {table}(status)')
            db.executemany(f'INSERT INTO {table} VALUES (?,?)',[('queued','private pupil')] * 1001)
            db.execute(f'INSERT INTO {table} VALUES (?,?)',('running','private pupil'))
        db.commit()
    before=hashlib.sha256(path.read_bytes()).hexdigest()
    result=ResourceSnapshot().snapshot(path)
    assert len(result['queues'])==3
    assert all(q['counts']['queued']==dict(count=1000,capped=True) for q in result['queues'])
    assert result['queues'][0]['counts']['running']==dict(count=1,capped=False)
    assert hashlib.sha256(path.read_bytes()).hexdigest()==before
    assert all(word not in json.dumps(result) for word in ['private',str(tmp_path),'secret','pupil'])


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


def test_non_admin_cannot_trigger_resource_collection(client,monkeypatch):
    enable(client);create(client,role='teacher');client.cookies.clear()
    assert client.post('/api/auth/login',json=dict(login='teacher',password=PASSWORD)).status_code==200
    def forbidden(path): raise AssertionError('Must authorize before collecting')
    monkeypatch.setattr(api.app.state.operations_resources,'snapshot',forbidden)
    assert client.get('/api/access/operations').status_code==403
    client.cookies.clear()
    assert client.get('/api/access/operations').status_code==401
