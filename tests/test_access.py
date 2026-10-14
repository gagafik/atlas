from datetime import date, datetime, timedelta, timezone
import json
import pytest
from fastapi.testclient import TestClient
from backend import api
from backend.models import Placement, DatedLesson
from backend.seed import initial_state
from backend.access import COOKIE, password_matches

PASSWORD='test password with spaces '
HEADERS={'X-Atlas-Request':'1'}


@pytest.fixture
def client(tmp_path,monkeypatch):
    monkeypatch.setattr(api,'DB',tmp_path/'access.db')
    result=TestClient(api.app,headers=HEADERS)
    result.get('/api/state?workspace=demo')
    s=initial_state('demo');s.events=[]
    s.placements=[Placement(id='eng',workloadId='w0',day=0,period=1,roomId='r0'),Placement(id='math',workloadId='w4',day=0,period=2,roomId='r1')]
    api.save('demo',s)
    return result


def enable(client):
    response=client.post('/api/auth/setup',json=dict(name='Администратор',login='admin',password=PASSWORD))
    assert response.status_code==200,response.text
    return response.json()['user']


def create(client,role='editor',**values):
    data=dict(name='Сотрудник',login=role,role=role,workspaces=['demo'],password=PASSWORD)
    if role in ['student','parent']: data['grants']=[dict(workspace='demo',view='student',entityIds=['s1'])]
    if role=='teacher': data['grants']=[dict(workspace='demo',view='teacher',entityIds=['t2'])]
    data.update(values)
    response=client.post('/api/access/users',json=data)
    assert response.status_code==200,response.text
    return response.json()


def signed(login):
    client=TestClient(api.app,headers=HEADERS)
    response=client.post('/api/auth/login',json=dict(login=login,password=PASSWORD))
    assert response.status_code==200,response.text
    return client


def test_setup_enables_auth_atomically_without_changing_schedule(client):
    before=api.read('demo')
    assert client.get('/api/auth/status').json()==dict(enabled=False,user=None,canSetup=True)
    assert client.get('/api/access/users').status_code==403
    bare=TestClient(api.app)
    assert bare.post('/api/auth/setup',json=dict(name='Admin',login='admin',password=PASSWORD)).status_code==403
    user=enable(client)
    assert user['role']=='admin' and 'password' not in user
    assert client.post('/api/auth/setup',json=dict(name='Admin',login='other',password=PASSWORD)).status_code==409
    assert api.read('demo')==before
    assert bare.get('/api/state?workspace=demo').status_code==401
    assert bare.get('/api/check').status_code==401
    assert bare.get('/openapi.json').status_code==401
    assert client.get('/api/state?workspace=demo').status_code==200
    with api.connect() as db:
        encoded=db.execute('SELECT password FROM accounts').fetchone()[0]
        stored=db.execute('SELECT token FROM sessions').fetchone()[0]
    assert PASSWORD not in encoded and password_matches(PASSWORD,encoded) and not password_matches(PASSWORD.strip(),encoded)
    assert stored!=client.cookies.get(COOKIE)


def test_cookies_csrf_logout_and_expiry(client):
    response=client.post('/api/auth/setup',json=dict(name='Admin',login='admin',password=PASSWORD))
    cookie=response.headers['set-cookie']
    assert 'HttpOnly' in cookie and 'SameSite=strict' in cookie and 'Max-Age=43200' in cookie
    assert client.post('/api/auth/logout',headers={'X-Atlas-Request':'0'}).status_code==403
    assert client.post('/api/auth/logout',headers={'Origin':'https://evil.example'}).status_code==403
    assert client.post('/api/auth/logout').status_code==200
    assert client.get('/api/state').status_code==401
    c=signed('admin')
    with api.connect() as db: db.execute('UPDATE sessions SET expires=?',((datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat(),))
    assert c.get('/api/state').status_code==401


def test_editor_workspace_access_cas_and_attribution(client):
    enable(client);user=create(client)
    editor=signed('editor')
    assert editor.get('/api/state').status_code==403
    assert editor.get('/api/access/users').status_code==403
    assert editor.get('/api/access/audit').status_code==403
    assert editor.post('/api/access/users',json={}).status_code==403
    state=editor.get('/api/state?workspace=demo').json();state['settings']['name']='Изменил составитель'
    response=editor.put('/api/state?workspace=demo',json=state)
    assert response.status_code==200
    assert editor.put('/api/state?workspace=demo',json=state).status_code==409
    events=client.get('/api/access/audit').json()
    saved=next(e for e in events if e['action']=='state.saved')
    assert saved['actorId']==user['id'] and saved['revision']==response.json()['revision'] and saved['workspace']=='demo'


@pytest.mark.parametrize('role',['teacher','student','parent'])
def test_personal_roles_receive_only_projected_allowed_schedule(client,role):
    enable(client);create(client,role)
    viewer=signed(role)
    for path in ['/api/state?workspace=demo','/api/check?workspace=demo','/api/calendar/day?workspace=demo&day=2026-08-17','/api/publications?workspace=demo','/api/variants?workspace=demo','/api/generation-jobs?workspace=demo']:
        assert viewer.get(path).status_code==403,path
    for path in ['/api/editor/apply','/api/exchange/export','/api/commitments/preview','/api/undo']:
        assert viewer.post(path+'?workspace=demo',json={}).status_code==403,path
    options=viewer.get('/api/portal/options').json()
    assert len(options)==1 and len(options[0]['records'])==1
    record=options[0]['records'][0]
    path=f"/api/portal/schedule?workspace=demo&view={record['view']}&entityId={record['id']}"
    result=viewer.get(path)
    assert result.status_code==200 and result.json()['rows']
    text=result.text
    assert 'studentIds' not in text and 'sourceCells' not in text and 'teacherIds' not in text
    s=api.read('demo')
    assert s.students[1].name not in text
    assert viewer.get('/api/portal/schedule?workspace=demo&view=student&entityId=s2').status_code==403
    assert viewer.get(path.replace('workspace=demo','workspace=school')).status_code==403
    assert viewer.get(path+'&day=2026-08-17').status_code==200


def test_personal_day_hides_absence_explanations_and_includes_only_own_rows(client):
    enable(client);create(client,'student')
    s=api.read('demo');s.datedLessons=[DatedLesson(id='private',name='Конфиденциальная причина',kind='cancel',placementId='eng',onDate=date(2026,8,17),sourceDate=date(2026,8,17))];api.save('demo',s)
    viewer=signed('student')
    response=viewer.get('/api/portal/schedule?workspace=demo&view=student&entityId=s1&day=2026-08-17')
    assert response.status_code==200 and 'Конфиденциальная' not in response.text
    assert response.json()['rows'][0]['status']=='cancelled'


def test_user_updates_are_versioned_revoke_sessions_and_preserve_last_admin(client):
    admin=enable(client); user=create(client);editor=signed('editor')
    body={k:v for k,v in user.items() if k!='id'};body['active']=False
    assert client.put('/api/access/users/'+user['id'],json=body).status_code==200
    assert editor.get('/api/state?workspace=demo').status_code==401
    assert client.put('/api/access/users/'+user['id'],json=body).status_code==409
    data={k:v for k,v in admin.items() if k!='id'};data['active']=False
    assert client.put('/api/access/users/'+admin['id'],json=data).status_code==422
    assert client.get('/api/state').status_code==200


def test_invalid_grants_duplicates_and_roles_are_rejected(client):
    enable(client);create(client)
    assert client.post('/api/access/users',json=dict(name='X',login='editor',role='editor',password=PASSWORD)).status_code==409
    for role,view,ids in [('student','student',['s1','s2']),('teacher','student',['s1']),('student','student',['missing'])]:
        r=client.post('/api/access/users',json=dict(name='X',login='test',role=role,password=PASSWORD,grants=[dict(workspace='demo',view=view,entityIds=ids)]))
        assert r.status_code==422


def test_password_change_revokes_other_sessions_and_preserves_current(client):
    enable(client);other=signed('admin')
    response=client.post('/api/auth/password',json=dict(currentPassword=PASSWORD,password='new strong password'))
    assert response.status_code==200
    assert client.get('/api/state').status_code==200 and other.get('/api/state').status_code==401
    assert other.post('/api/auth/login',json=dict(login='admin',password=PASSWORD)).status_code==401
    assert other.post('/api/auth/login',json=dict(login='admin',password='new strong password')).status_code==200


def test_login_attempts_are_limited_and_dont_disclose_account_existence(client):
    enable(client);other=TestClient(api.app,headers=HEADERS)
    for _ in range(8):
        assert other.post('/api/auth/login',json=dict(login='admin',password='wrong')).status_code==401
    assert other.post('/api/auth/login',json=dict(login='admin',password=PASSWORD)).status_code==429
    unknown=other.post('/api/auth/login',json=dict(login='unknown',password='wrong'))
    assert unknown.status_code==401 and unknown.json()['detail']=='Неверный логин или пароль.'


def test_publication_link_stays_public_without_exposing_workspace(client):
    enable(client)
    s=api.read('demo')
    p=client.post('/api/publications/preview?workspace=demo',json=dict(revision=s.revision,name='10A',entityIds=['c-demo-a'],cycleIds=['weekly'])).json()
    entry=client.post('/api/publications?workspace=demo',json=dict(revision=s.revision,token=p['token'])).json()
    visitor=TestClient(api.app)
    assert visitor.get(entry['url']).status_code==200 and visitor.get('/api/state?workspace=demo').status_code==401


def test_unconfigured_remote_access_and_dns_rebinding_host_are_denied(client):
    remote=TestClient(api.app,client=('192.168.1.10',12345),headers=HEADERS)
    assert remote.get('/api/state').status_code==403
    assert remote.post('/api/auth/setup',json=dict(name='X',login='admin',password=PASSWORD)).status_code==403
    rebound=TestClient(api.app,base_url='http://evil.example',headers=HEADERS)
    assert rebound.get('/api/state').status_code==403


def test_presence_shows_other_editors_and_remote_revision_without_overwriting(client):
    enable(client);create(client);editor=signed('editor')
    before=api.read('demo')
    p=editor.post('/api/collaboration/presence?workspace=demo',json=dict(clientId='editor-tab',page='planner')).json()
    assert p['revision']==before.revision
    p=client.post('/api/collaboration/presence?workspace=demo',json=dict(clientId='admin-tab',page='access')).json()
    assert p['peers'][0]['name']=='Сотрудник' and p['peers'][0]['page']=='planner'
    changed=before.model_copy(deep=True);changed.settings.name='Новая версия';api.save('demo',changed)
    p=editor.post('/api/collaboration/presence?workspace=demo',json=dict(clientId='editor-tab',page='planner')).json()
    assert p['revision']==before.revision+1
    assert editor.put('/api/state?workspace=demo',json=before.model_dump(mode='json')).status_code==409
    with api.connect() as db: db.execute('UPDATE presence SET updated=? WHERE client_id=?',((datetime.now(timezone.utc)-timedelta(seconds=80)).isoformat(),'admin-tab'))
    assert editor.post('/api/collaboration/presence?workspace=demo',json=dict(clientId='editor-tab')).json()['peers']==[]
    create(client,'student')
    assert signed('student').post('/api/collaboration/presence?workspace=demo',json=dict(clientId='student-tab')).status_code==403
    assert editor.post('/api/collaboration/presence?workspace=school',json=dict(clientId='editor-tab')).status_code==403


def test_local_recovery_preserves_school_and_revokes_sessions(client,monkeypatch,capsys):
    import sys
    from tools import reset_account_password
    enable(client);before=api.read('demo')
    monkeypatch.setattr(sys,'argv',['reset_account_password.py','admin'])
    monkeypatch.setattr(reset_account_password,'getpass',lambda prompt:'recovered account password')
    reset_account_password.main()
    assert api.read('demo')==before
    assert client.get('/api/state').status_code==401
    assert 'recovered account password' not in capsys.readouterr().out
    assert client.post('/api/auth/login',json=dict(login='admin',password='recovered account password')).status_code==200
