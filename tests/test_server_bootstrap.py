from concurrent.futures import ThreadPoolExecutor
import json
import pytest
from fastapi.testclient import TestClient
from backend import api
from backend.access import password_matches
from tools.create_admin import create_first_admin
from tools.serve import verify_ready
from seeding import read_row
from test_access import client,HEADERS,PASSWORD


@pytest.fixture
def server(client,monkeypatch):
    monkeypatch.setenv('TIMETABLE_REQUIRE_AUTH','1')
    return client


def test_server_without_an_administrator_refuses_everything_but_health_and_keeps_the_schedule(server):
    before=api.read('demo').model_dump()
    for path in ['/api/state?workspace=demo','/api/auth/status','/api/ready']:
        assert server.get(path).status_code==503
    assert server.get('/api/health').status_code==200
    assert server.post('/api/auth/setup',json=dict(login='admin',name='Admin',password=PASSWORD)).status_code==503
    with pytest.raises(ValueError):verify_ready()
    assert api.read('demo').model_dump()==before


def test_first_administrator_makes_the_server_ready_and_closes_the_setup(server):
    before=api.read('demo').model_dump()
    id=create_first_admin('admin','Administrator',PASSWORD)
    verify_ready()
    assert server.get('/api/ready').status_code==200
    assert server.get('/api/auth/status').json()==dict(enabled=True,user=None,canSetup=False)
    assert server.get('/api/state?workspace=demo').status_code==401
    assert api.read('demo').model_dump()==before
    with api.connect() as db:row=read_row(db,'accounts',id=id)
    assert row['password']!=PASSWORD and password_matches(PASSWORD,row['password'])
    assert json.loads(row['permissions'])==dict(workspaces=['school','demo'],grants=[])


def test_a_second_administrator_cannot_be_created_from_the_console(server):
    create_first_admin('admin','Administrator',PASSWORD)
    with pytest.raises(ValueError):create_first_admin('other','Other administrator',PASSWORD)


def test_administrator_logs_in_over_https_with_a_secure_cookie(server):
    create_first_admin('admin','Administrator',PASSWORD)
    authenticated=TestClient(api.app,headers=HEADERS,base_url='https://atlas.example.org')
    response=authenticated.post('/api/auth/login',json=dict(login='admin',password=PASSWORD))
    assert response.status_code==200,response.text
    assert 'Secure' in response.headers['set-cookie']
    assert authenticated.get('/api/state?workspace=demo').status_code==200


def test_lost_accounts_do_not_let_the_server_fall_back_to_local_admin(server):
    create_first_admin('admin','Administrator',PASSWORD)
    authenticated=TestClient(api.app,headers=HEADERS)
    assert authenticated.post('/api/auth/login',json=dict(login='admin',password=PASSWORD)).status_code==200
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
