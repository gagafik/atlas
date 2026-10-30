from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import json
import threading

from fastapi.testclient import TestClient
import pytest

from backend import api, invitations
from backend.access import COOKIE, digest, now
from test_access import client, enable, create, signed, PASSWORD, HEADERS


def issue(client, ids=None, role='parent', **values):
    body = dict(recipient='Получатель приглашения', role=role, days=7,
                grants=[dict(workspace='demo',view='teacher' if role=='teacher' else 'student',
                             entityIds=ids or (['t0'] if role=='teacher' else ['s1']))])
    body.update(values)
    result = client.post('/api/access/invitations', json=body)
    assert result.status_code == 200, result.text
    return result.json()


def visitor(): return TestClient(api.app, headers=HEADERS)


def register(client, code, login='family', **extra):
    return client.post('/api/auth/register', json=dict(code=code, name='Имя родителя', login=login, password=PASSWORD, **extra))


def link(client, code, password=PASSWORD):
    return client.post('/api/auth/invitations/accept',json=dict(code=code,currentPassword=password))


def test_registration_assigns_only_staff_permissions_and_keeps_school_unchanged(client):
    enable(client); before = api.read('demo')
    invite = issue(client, ['s1','s2'])
    anonymous = visitor()
    assert anonymous.get('/api/access/invitations').status_code == 401
    assert anonymous.get('/api/portal/options').status_code == 401
    assert register(anonymous, invite['code'], role='admin').status_code == 422
    result = register(anonymous, invite['code'])
    assert result.status_code == 200, result.text
    user = result.json()['user']
    assert user['role'] == 'parent' and user['grants'][0]['entityIds'] == ['s1','s2']
    assert 'password' not in user
    assert 'HttpOnly' in result.headers['set-cookie'] and 'SameSite=strict' in result.headers['set-cookie']
    options = anonymous.get('/api/portal/options').json()
    assert {r['id'] for s in options for r in s['records']} == {'s1','s2'}
    assert anonymous.get('/api/state?workspace=demo').status_code == 403
    assert anonymous.get('/api/portal/schedule?workspace=demo&view=student&entityId=s3&cycleId=week-a').status_code == 403
    assert api.read('demo') == before
    listed = client.get('/api/access/invitations').json()['items'][0]
    assert listed['status'] == 'accepted' and listed['accountId'] == user['id']
    assert invite['code'] not in json.dumps(listed)
    with api.connect() as db:
        assert db.execute('SELECT token_hash FROM access_invitations').fetchone()[0] == digest(invite['code'])
        assert invite['code'] not in str(db.execute('SELECT * FROM audit').fetchall())
        assert db.execute('SELECT count(*) FROM invitation_attempts').fetchone()[0] == 0
    assert register(visitor(), invite['code'], login='second').status_code == 400
    assert client.post(f"/api/access/invitations/{invite['id']}/revoke").status_code == 409


def test_invitation_requires_admin_csrf_and_exact_entities(client):
    assert register(visitor(), 'a'*43).status_code == 403
    enable(client)
    create(client)
    editor = signed('editor')
    data = dict(recipient='X',role='parent',grants=[dict(workspace='demo',view='student',entityIds=['s1'])])
    assert editor.post('/api/access/invitations',json=data).status_code == 403
    assert editor.get('/api/access/invitations').status_code == 403
    for role in ['admin','editor','viewer']:
        assert client.post('/api/access/invitations',json={**data,'role':role}).status_code == 422
    for view, ids in [('student',['absent']),('teacher',['t0'])]:
        assert client.post('/api/access/invitations',json={**data,'grants':[dict(workspace='demo',view=view,entityIds=ids)]}).status_code == 422
    assert client.post('/api/access/invitations',json={**data,'role':'student','grants':[dict(workspace='demo',view='student',entityIds=['s1','s2'])]}).status_code == 422
    for days in [0,31]: assert client.post('/api/access/invitations',json={**data,'days':days}).status_code == 422
    invite = issue(client)
    bare = TestClient(api.app)
    assert register(bare, invite['code']).status_code == 403
    other_origin = TestClient(api.app,headers={**HEADERS,'Origin':'https://not-school.example'})
    assert register(other_origin, invite['code']).status_code == 403
    assert client.post('/api/access/invitations',json={**data,'grants':[dict(workspace='demo',view='student',entityIds=['s1','s1'])]}).status_code == 422


def test_expired_revoked_deleted_targets_and_busy_login_never_consume_code(client):
    enable(client)
    revoked = issue(client)
    expired = issue(client)
    deleted = issue(client, ['s2'])
    retry = issue(client)
    assert client.post(f"/api/access/invitations/{revoked['id']}/revoke").status_code == 200
    assert client.post(f"/api/access/invitations/{revoked['id']}/revoke").status_code == 200
    with api.connect() as db:
        db.execute('UPDATE access_invitations SET expires=? WHERE id=?',((now()-timedelta(seconds=1)).isoformat(),expired['id']))
    errors = [register(visitor(), code).json() for code in [revoked['code'],expired['code'],'x'*43]]
    assert errors[0] == errors[1] == errors[2]
    state = api.read('demo'); state.students=[s for s in state.students if s.id!='s2']
    # Modify the reference directly in this isolated fixture to exercise a stale code.
    with api.connect() as db: db.execute("UPDATE workspaces SET document=? WHERE id='demo'",(state.model_dump_json(),))
    assert register(visitor(), deleted['code']).status_code == 422
    assert register(visitor(), retry['code'], login='admin').status_code == 409
    assert register(visitor(), retry['code'], login='new-family').status_code == 200
    rows=client.get('/api/access/invitations').json()['items']
    assert {r['id']:r['status'] for r in rows} == {revoked['id']:'revoked',expired['id']:'expired',deleted['id']:'pending',retry['id']:'accepted'}


def test_existing_parent_adds_child_revokes_other_sessions_and_allows_second_representative(client):
    enable(client); create(client,'parent')
    parent = signed('parent'); old_session = signed('parent')
    code = issue(client, ['s2'])['code']
    assert link(parent,code,'wrong password').status_code == 401
    assert register(parent,code).status_code == 409
    result = link(parent,code)
    assert result.status_code == 200, result.text
    assert result.json()['user']['version'] == 1
    assert result.json()['user']['grants'][0]['entityIds'] == ['s1','s2']
    assert old_session.get('/api/portal/options').status_code == 401
    assert parent.get('/api/portal/options').status_code == 200
    second = visitor()
    assert register(second,issue(client,['s1','s2'])['code'],'second-parent').status_code == 200
    with api.connect() as db:
        assert db.execute("SELECT count(*) FROM accounts WHERE role='parent'").fetchone()[0] == 2
    assert {r['id'] for s in second.get('/api/portal/options').json() for r in s['records']} == {'s1','s2'}
    changed = result.json()['user']; changed.pop('id'); changed['grants'][0]['entityIds']=['s1']
    assert client.put('/api/access/users/'+result.json()['user']['id'],json=changed).status_code == 200
    assert parent.get('/api/portal/options').status_code == 401
    assert second.get('/api/portal/options').status_code == 200


def test_student_cannot_merge_another_pupil_and_role_cannot_change(client):
    enable(client); create(client,'student'); create(client,'teacher')
    student=signed('student'); teacher=signed('teacher')
    other=issue(client,['s2'],'student')
    assert link(student,other['code']).status_code == 422
    parent=issue(client)
    assert link(teacher,parent['code']).status_code == 422
    assert link(visitor(),parent['code']).status_code == 401
    assert register(visitor(),other['code'],'new-pupil').json()['user']['role'] == 'student'
    assert register(visitor(),issue(client,role='teacher')['code'],'new-teacher').json()['user']['role'] == 'teacher'
    assert link(student,issue(client,['s1'],'student')['code']).status_code == 200


def test_simultaneous_acceptance_and_revocation_are_atomic(client, monkeypatch):
    enable(client)
    code=issue(client)['code']
    barrier=threading.Barrier(2)
    original=invitations.password_hash
    def pause(password):
        barrier.wait(timeout=10)
        return original(password)
    monkeypatch.setattr(invitations,'password_hash',pause)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda i: register(visitor(),code,f'family-{i}'),range(2)))
    assert sorted(r.status_code for r in results) == [200,400]
    with api.connect() as db: assert db.execute("SELECT count(*) FROM accounts WHERE role='parent'").fetchone()[0] == 1
    invitation=issue(client)
    def revoke_during_hash(password):
        assert client.post(f"/api/access/invitations/{invitation['id']}/revoke").status_code == 200
        return original(password)
    monkeypatch.setattr(invitations,'password_hash',revoke_during_hash)
    assert register(visitor(),invitation['code'],'revoked-during-hash').status_code == 400
    with api.connect() as db: assert db.execute("SELECT count(*) FROM accounts WHERE login='revoked-during-hash'").fetchone()[0] == 0


def test_rate_limit_reserves_attempts_without_hashing_invalid_codes(client, monkeypatch):
    enable(client); invite=issue(client)
    def unexpected(_): raise AssertionError('invalid token must be rejected before password hashing')
    monkeypatch.setattr(invitations,'password_hash',unexpected)
    for _ in range(10): assert register(visitor(),'z'*43).status_code == 400
    assert register(visitor(),'z'*43).status_code == 429
    for i in range(30): assert register(visitor(),f'{i:043d}').status_code == 400
    assert register(visitor(),invite['code']).status_code == 429
    with api.connect() as db: db.execute('UPDATE invitation_attempts SET created=?',((now()-timedelta(minutes=16)).isoformat(),))
    assert register(visitor(),'z'*43).status_code == 400


def test_account_changed_during_password_check_cannot_add_access(client, monkeypatch):
    enable(client); account=create(client,'parent'); parent=signed('parent'); code=issue(client,['s2'])['code']
    original=invitations.password_matches
    def change(password, encoded):
        body={k:v for k,v in account.items() if k!='id'};body['active']=False
        assert client.put('/api/access/users/'+account['id'],json=body).status_code == 200
        return original(password,encoded)
    monkeypatch.setattr(invitations,'password_matches',change)
    assert link(parent,code).status_code == 409
    assert client.get('/api/access/invitations').json()['items'][0]['status'] == 'pending'


def test_listing_is_bounded_and_codes_never_reappear(client):
    enable(client);codes=[issue(client)['code'] for _ in range(52)];pages=[];before=''
    for _ in range(10):
        page=client.get('/api/access/invitations'+before).json();pages.append(page);before='?before='+str(page['nextBefore'])
        if page['nextBefore'] is None:break
    items=[i for page in pages for i in page['items']]
    assert len(pages)>1 and pages[-1]['nextBefore'] is None and len({i['id'] for i in items})==len(items)==52
    assert not any(code in json.dumps(pages) for code in codes)
