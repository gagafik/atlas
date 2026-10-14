from concurrent.futures import ThreadPoolExecutor
import json
import pytest
from fastapi.testclient import TestClient
from backend import api
from backend.access import password_matches
from tools.create_admin import create_first_admin
from tools.serve import verify_ready
from test_access import client,HEADERS,PASSWORD


def test_server_requires_admin_and_preserves_schedule(client,monkeypatch):
    before=api.read('demo').model_dump()
    monkeypatch.setenv('TIMETABLE_REQUIRE_AUTH','1')
    for path in ['/api/state?workspace=demo','/api/auth/status','/api/health']:
        assert client.get(path).status_code==503
    assert client.post('/api/auth/setup',json=dict(login='admin',name='Admin',password=PASSWORD)).status_code==503
    with pytest.raises(ValueError,match='Создайте администратора'):verify_ready()
    id=create_first_admin('admin','Administrator',PASSWORD)
    verify_ready()
    assert client.get('/api/health').status_code==200
    assert client.get('/api/auth/status').json()==dict(enabled=True,user=None,canSetup=False)
    assert client.get('/api/state?workspace=demo').status_code==401
    assert api.read('demo').model_dump()==before
    with api.connect() as db:
        row=db.execute('SELECT * FROM accounts WHERE id=?',(id,)).fetchone()
        assert row[6]!=PASSWORD and password_matches(PASSWORD,row[6])
        assert json.loads(row[7])==dict(workspaces=['school','demo'],grants=[])
    with pytest.raises(ValueError,match='уже существуют'):create_first_admin('other','Other administrator',PASSWORD)
    authenticated=TestClient(api.app,headers=HEADERS,base_url='https://atlas.example.org')
    response=authenticated.post('/api/auth/login',json=dict(login='admin',password=PASSWORD))
    assert response.status_code==200,response.text
    assert 'Secure' in response.headers['set-cookie']
    assert authenticated.get('/api/state?workspace=demo').status_code==200
    # If the accounts are lost, the web server must not fall back to local admin.
    with api.connect() as db:db.execute('DELETE FROM accounts')
    assert authenticated.get('/api/state?workspace=demo').status_code==503


def test_console_bootstrap_race_cannot_create_two_owners(client):
    def attempt(i):
        try:return create_first_admin('admin'+str(i),'Admin',PASSWORD)
        except ValueError:return None
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(attempt,[1,2]))
    assert sum(value is not None for value in results)==1
    with api.connect() as db:
        assert db.execute('SELECT count(*) FROM accounts').fetchone()[0]==1
