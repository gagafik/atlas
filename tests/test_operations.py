from contextlib import closing
import sqlite3

from fastapi.testclient import TestClient
import pytest

from backend import api
from test_access import client, enable, create, PASSWORD


def test_readiness_does_not_create_missing_database(tmp_path,monkeypatch):
    path=tmp_path/'absent.db'
    monkeypatch.setattr(api,'DB',path)
    monkeypatch.setenv('TIMETABLE_REQUIRE_AUTH','1')
    # Test probes alone, without application startup jobs that initialize schemas.
    client=TestClient(api.app)
    assert client.get('/api/health').status_code==200
    response=client.get('/api/ready')
    assert response.status_code==503 and response.json()=={'status':'unavailable'}
    assert response.headers['cache-control']=='no-store'
    assert not path.exists()


def test_readiness_requires_real_schema_and_active_administrator(client,monkeypatch):
    assert client.get('/api/ready').status_code==200
    monkeypatch.setenv('TIMETABLE_REQUIRE_AUTH','1')
    assert client.get('/api/ready').status_code==503
    monkeypatch.delenv('TIMETABLE_REQUIRE_AUTH')
    admin=enable(client)
    monkeypatch.setenv('TIMETABLE_REQUIRE_AUTH','1')
    assert client.get('/api/ready').status_code==200
    with closing(sqlite3.connect(api.DB)) as db:
        db.execute('UPDATE accounts SET active=0 WHERE id=?',(admin['id'],));db.commit()
    assert client.get('/api/ready').status_code==503
    assert client.get('/api/health').status_code==200


def test_corrupt_database_probe_returns_generic_unavailable(tmp_path,monkeypatch):
    path=tmp_path/'corrupt.db';path.write_text('private corrupt school contents')
    monkeypatch.setattr(api,'DB',path)
    client=TestClient(api.app)
    response=client.get('/api/ready')
    assert response.status_code==503 and 'private' not in response.text
    assert path.read_text()=='private corrupt school contents'


def test_operations_is_read_only_and_administrator_only(client,monkeypatch):
    monkeypatch.delenv('ATLAS_BACKUP_DIR',raising=False)
    assert client.get('/api/access/operations').status_code==403
    enable(client)
    response=client.get('/api/access/operations')
    assert response.status_code==200 and response.json()==dict(databaseReady=True,backup=dict(state='disabled'))
    assert response.headers['cache-control']=='no-store'
    create(client,role='teacher')
    client.cookies.clear()
    assert client.post('/api/auth/login',json=dict(login='teacher',password=PASSWORD)).status_code==200
    assert client.get('/api/access/operations').status_code==403
    client.cookies.clear()
    assert client.get('/api/access/operations').status_code==401


@pytest.mark.parametrize('change',["active=0","role='teacher'","version=version+1"])
def test_operations_rechecks_account_after_middleware_identification(client,monkeypatch,change):
    admin=enable(client)
    identify=api.access.identify
    def race(request):
        result=identify(request)
        with closing(sqlite3.connect(api.DB)) as db:
            db.execute(f'UPDATE accounts SET {change} WHERE id=?',(admin['id'],));db.commit()
        return result
    monkeypatch.setattr(api.access,'identify',race)
    assert client.get('/api/access/operations').status_code==403


def test_restored_demo_keeps_accounts_permissions_and_workspace(client,tmp_path,monkeypatch):
    from tools.backup_school import create_backup
    from tools.restore_school import restore_backup
    admin=enable(client)
    create(client,role='teacher')
    original=client.get('/api/state?workspace=demo').json()
    create_backup(api.DB,tmp_path/'snapshot')
    restore_backup(tmp_path/'snapshot',tmp_path/'restored')
    monkeypatch.setattr(api,'DB',tmp_path/'restored'/api.DB.name)
    assert client.get('/api/auth/status').json()['user']['id']==admin['id']
    assert client.get('/api/state?workspace=demo').json()==original
    client.cookies.clear()
    assert client.post('/api/auth/login',json=dict(login='teacher',password=PASSWORD)).status_code==200
    assert client.get('/api/access/operations').status_code==403
    assert client.get('/api/state?workspace=demo').status_code==403


def test_write_during_reserved_snapshot_returns_retryable_failure_without_new_session(client,monkeypatch):
    enable(client)
    original=api.access.connect
    def short_wait():
        db=original();db.execute('PRAGMA busy_timeout=50');return db
    monkeypatch.setattr(api.access,'connect',short_wait)
    with closing(sqlite3.connect(api.DB)) as db:
        count=db.execute('SELECT count(*) FROM sessions').fetchone()[0]
        db.execute('BEGIN IMMEDIATE')
        response=client.post('/api/auth/login',json=dict(login='admin',password=PASSWORD))
        assert response.status_code==503 and response.headers['retry-after']=='5'
        assert 'Сервер занят' in response.json()['detail']
        assert db.execute('SELECT count(*) FROM sessions').fetchone()[0]==count
        db.rollback()
    assert client.post('/api/auth/login',json=dict(login='admin',password=PASSWORD)).status_code==200
