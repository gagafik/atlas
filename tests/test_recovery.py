"""Recovery is a credential operation, tested only on a temporary demo database."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

import pytest
from fastapi.testclient import TestClient
from backend import api, recovery
from test_access import client, enable, create, signed, PASSWORD, HEADERS

NEW = ' new recovery password with spaces '
OPTIONS = '/api/auth/recovery/options'
ISSUE = '/api/auth/recovery/codes'
REVOKE = '/api/auth/recovery/revoke'


def visitor():
    return TestClient(api.app, headers=HEADERS)


def issue(user, password=PASSWORD):
    options = user.get(OPTIONS)
    assert options.status_code == 200, options.text
    result = user.post(ISSUE, json=dict(version=options.json()['version'], currentPassword=password))
    assert result.status_code == 200, result.text
    return result.json()


def recover(code, login='admin', password=NEW, user=None):
    return (user or visitor()).post('/api/auth/recover', json=dict(login=login, code=code, password=password))


def login_as(login='admin', password=NEW):
    user = visitor()
    response = user.post('/api/auth/login', json=dict(login=login, password=password))
    assert response.status_code == 200, response.text
    return user


def account_row(login='admin'):
    with api.connect() as db:
        return db.execute('SELECT * FROM accounts WHERE login=?', (login,)).fetchone()


@pytest.mark.parametrize('role', ['admin', 'editor', 'teacher', 'student', 'parent', 'staff', 'viewer'])
def test_every_role_manages_only_own_codes(client, role):
    admin = enable(client)
    if role == 'admin':
        user, account = client, admin
    else:
        kwargs = dict(grants=[dict(workspace='demo', view='class', entityIds=['c-demo-a'])]) if role == 'viewer' else {}
        account = create(client, role, **kwargs)
        user = signed(role)
    before = api.read('demo')
    assert user.get(OPTIONS).json()['status'] == 'none'
    result = issue(user)
    assert len(set(result['codes'])) == 8 and result['remaining'] == 8
    assert all(len(recovery.normalized(code)) == 32 for code in result['codes'])
    fetched = user.get(OPTIONS)
    assert fetched.headers['cache-control'] == 'no-store'
    assert fetched.json()['status'] == 'active' and 'codes' not in fetched.json()
    with api.connect() as db:
        saved = db.execute('SELECT * FROM recovery_codes').fetchall()
        audit = db.execute("SELECT actor_id,detail FROM audit WHERE action='account.recovery_issued'").fetchall()
        assert {row[0] for row in saved} == {account['id']}
        assert audit[0][0] == account['id']
        stored = '\n'.join(db.iterdump())
    for code in result['codes']:
        assert code not in stored and recovery.normalized(code) not in stored
    assert PASSWORD not in stored and NEW not in stored
    assert api.read('demo') == before
    assert visitor().get(OPTIONS).status_code == 401
    assert user.post(ISSUE, json=dict(version=1, currentPassword=PASSWORD, userId=admin['id'])).status_code == 422


def test_single_use_revokes_sessions_preserves_remaining_and_requires_new_login(client):
    enable(client)
    before = api.read('demo')
    other = signed('admin')
    batch = issue(client)
    anonymous = visitor()
    response = recover('  ' + batch['codes'][0].lower().replace('-', ' ') + '\n', login=' ADMIN ', user=anonymous)
    assert response.status_code == 200 and response.json() == dict(status='recovered')
    assert response.headers['cache-control'] == 'no-store'
    assert 'Max-Age=0' in response.headers['set-cookie']
    assert anonymous.get('/api/auth/status').json()['user'] is None
    assert client.get(OPTIONS).status_code == other.get(OPTIONS).status_code == 401
    assert recover(batch['codes'][0]).status_code == 400
    user = login_as()
    options = user.get(OPTIONS).json()
    assert options['remaining'] == 7 and options['version'] == 2 and options['lastUsed']
    assert options['expires'] == batch['expires']
    assert visitor().post('/api/auth/login', json=dict(login='admin', password=PASSWORD)).status_code == 401
    assert visitor().post('/api/auth/login', json=dict(login='admin', password=NEW.strip())).status_code == 401
    assert recover(batch['codes'][1], password=PASSWORD).status_code == 200
    assert login_as(password=PASSWORD).get(OPTIONS).json()['remaining'] == 6
    assert api.read('demo') == before


def test_regenerate_and_revoke_are_versioned_and_invalidate_old_sets(client):
    enable(client)
    first = issue(client)
    second = issue(client)
    assert second['version'] == 2
    assert recover(first['codes'][0]).status_code == 400
    stale = client.post(REVOKE, json=dict(version=1, currentPassword=PASSWORD))
    assert stale.status_code == 409
    assert client.get(OPTIONS).json()['remaining'] == 8
    response = client.post(REVOKE, json=dict(version=2, currentPassword=PASSWORD))
    assert response.status_code == 200 and response.json()['status'] == 'revoked'
    assert response.json()['version'] == 3 and response.json()['remaining'] == 0
    assert recover(second['codes'][0]).status_code == 400
    assert issue(client)['version'] == 4


def test_expired_disabled_unknown_and_invalid_codes_share_public_error(client, monkeypatch):
    enable(client)
    account = create(client)
    user = signed('editor')
    result = issue(user)
    invalid = recover('bad').json()
    assert invalid == dict(detail=recovery.INVALID)
    assert recover('bad', login='not-existing').json() == invalid
    assert recover(result['codes'][0], login='admin').json() == invalid
    with monkeypatch.context() as patch:
        stamp = recovery.now() + timedelta(days=366)
        patch.setattr(recovery, 'now', lambda: stamp)
        assert recover(result['codes'][0], login='editor').json() == invalid
    with api.connect() as db:
        db.execute("UPDATE recovery_sets SET expires='2000-01-01'")
    assert user.get(OPTIONS).json()['status'] == 'expired'
    body = {key: value for key, value in account.items() if key != 'id'} | dict(active=False)
    assert client.put('/api/access/users/' + account['id'], json=body).status_code == 200
    assert recover(result['codes'][0], login='editor').json() == invalid


@pytest.mark.parametrize('change', ['password', 'login', 'role', 'active'])
def test_admin_credential_changes_revoke_codes(client, change):
    enable(client)
    account = create(client)
    result = issue(signed('editor'))
    body = {key: value for key, value in account.items() if key != 'id'}
    body[change] = {'password': NEW, 'login': 'renamed', 'role': 'staff', 'active': False}[change]
    response = client.put('/api/access/users/' + account['id'], json=body)
    assert response.status_code == 200, response.text
    if change == 'active':
        body.update(active=True, version=response.json()['version'])
        assert client.put('/api/access/users/' + account['id'], json=body).status_code == 200
    assert recover(result['codes'][0], login=body['login']).status_code == 400
    with api.connect() as db:
        assert db.execute('SELECT count(*) FROM recovery_codes WHERE user_id=?', (account['id'],)).fetchone()[0] == 0


def test_permission_change_preserves_codes_but_never_restores_old_permissions(client):
    enable(client)
    account = create(client)
    result = issue(signed('editor'))
    body = {key: value for key, value in account.items() if key != 'id'} | dict(name='Новое имя', workspaces=['school'])
    assert client.put('/api/access/users/' + account['id'], json=body).status_code == 200
    assert recover(result['codes'][0], login='editor').status_code == 200
    user = login_as('editor')
    current = user.get('/api/auth/status').json()['user']
    assert current['name'] == 'Новое имя' and current['workspaces'] == ['school']
    assert user.get('/api/state?workspace=demo').status_code == 403


@pytest.mark.parametrize('method', ['ordinary', 'console'])
def test_password_changes_revoke_codes_including_console_reset(client, monkeypatch, method):
    enable(client)
    result = issue(client)
    if method == 'ordinary':
        response = client.post('/api/auth/password', json=dict(currentPassword=PASSWORD, password=NEW))
        assert response.status_code == 200
    else:
        from tools import reset_account_password
        monkeypatch.setattr('sys.argv', ['reset_account_password', 'admin'])
        monkeypatch.setattr(reset_account_password, 'getpass', lambda _: NEW)
        reset_account_password.main()
    assert recover(result['codes'][0]).status_code == 400
    assert login_as().get(OPTIONS).json()['status'] == 'revoked'


def test_failed_attempts_limit_recovery_then_expire_and_do_not_block_login(client, monkeypatch):
    enable(client)
    result = issue(client)
    for _ in range(8):
        assert recover('bad').status_code == 400
    assert recover(result['codes'][0]).status_code == 429
    assert signed('admin').get(OPTIONS).status_code == 200
    stamp = recovery.now() + timedelta(minutes=16)
    monkeypatch.setattr(recovery, 'now', lambda: stamp)
    assert recover(result['codes'][0]).status_code == 200


def test_client_limit_spans_unknown_logins_and_manage_password_limit_commits(client):
    enable(client)
    for index in range(40):
        assert recover('bad', login='missing-' + str(index)).status_code == 400
    assert recover('bad', login='other').status_code == 429
    for _ in range(8):
        assert client.post(ISSUE, json=dict(version=0, currentPassword='incorrect')).status_code == 401
    assert client.post(ISSUE, json=dict(version=0, currentPassword=PASSWORD)).status_code == 429
    assert client.get(OPTIONS).json()['status'] == 'none'


def test_concurrent_code_use_can_only_succeed_once(client, monkeypatch):
    enable(client)
    result = issue(client)
    barrier = Barrier(2)
    original = recovery.password_hash
    def hashed(password):
        barrier.wait(timeout=15)
        return original(password)
    monkeypatch.setattr(recovery, 'password_hash', hashed)
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: recover(result['codes'][0]), range(2)))
    assert sorted(response.status_code for response in responses) == [200, 400]
    with api.connect() as db:
        assert db.execute('SELECT count(*) FROM recovery_codes WHERE used IS NOT NULL').fetchone()[0] == 1
        assert db.execute("SELECT count(*) FROM audit WHERE action='account.recovered'").fetchone()[0] == 1


def test_concurrent_generation_rejects_stale_set_without_exposing_valid_second_batch(client, monkeypatch):
    enable(client)
    users = [signed('admin'), signed('admin')]
    barrier = Barrier(2)
    original = recovery.password_matches
    def matches(password, encoded):
        barrier.wait(timeout=15)
        return original(password, encoded)
    monkeypatch.setattr(recovery, 'password_matches', matches)
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda user: user.post(ISSUE, json=dict(version=0, currentPassword=PASSWORD)), users))
    assert sorted(response.status_code for response in responses) == [200, 409]
    assert 'codes' not in next(response for response in responses if response.status_code == 409).json()
    assert recover(next(response for response in responses if response.status_code == 200).json()['codes'][0]).status_code == 200


def test_different_codes_racing_do_not_consume_the_rejected_code(client, monkeypatch):
    enable(client)
    result = issue(client)
    barrier = Barrier(2)
    original = recovery.password_hash
    def hashed(password):
        barrier.wait(timeout=15)
        return original(password)
    with monkeypatch.context() as patch:
        patch.setattr(recovery, 'password_hash', hashed)
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(recover, result['codes'][:2]))
    assert sorted(response.status_code for response in responses) == [200, 400]
    rejected = next(index for index, response in enumerate(responses) if response.status_code == 400)
    assert recover(result['codes'][rejected]).status_code == 200
    assert login_as().get(OPTIONS).json()['remaining'] == 6


@pytest.mark.parametrize('mutation', ['account', 'regenerate', 'expire', 'logout'])
def test_state_is_rechecked_after_expensive_credential_work(client, monkeypatch, mutation):
    enable(client)
    result = issue(client)
    original = recovery.password_matches if mutation == 'logout' else recovery.password_hash
    def changed(*args):
        if mutation == 'account':
            with api.connect() as db: db.execute('UPDATE accounts SET version=version+1')
        elif mutation == 'regenerate':
            issue(client)
        elif mutation == 'expire':
            with api.connect() as db: db.execute("UPDATE recovery_sets SET expires='2000-01-01'")
        else:
            with api.connect() as db: db.execute('DELETE FROM sessions')
        return original(*args)
    monkeypatch.setattr(recovery, 'password_matches' if mutation == 'logout' else 'password_hash', changed)
    before = account_row()[6]
    if mutation == 'logout':
        response = client.post(REVOKE, json=dict(version=1, currentPassword=PASSWORD))
        assert response.status_code == 401
    else:
        assert recover(result['codes'][0]).status_code == 400
    assert account_row()[6] == before
    with api.connect() as db:
        assert db.execute('SELECT count(*) FROM recovery_codes WHERE used IS NOT NULL').fetchone()[0] == 0


def test_failed_audit_rolls_back_password_code_and_sessions_but_retains_attempt(client, monkeypatch):
    enable(client)
    result = issue(client)
    before = account_row()
    original = api.access.record
    def broken(db, action, *args, **kwargs):
        if action == 'account.recovered': raise RuntimeError('simulated audit failure')
        return original(db, action, *args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(api.access, 'record', broken)
        with pytest.raises(RuntimeError, match='simulated audit failure'):
            recover(result['codes'][0])
    assert account_row() == before
    assert client.get(OPTIONS).json()['remaining'] == 8
    with api.connect() as db:
        assert db.execute('SELECT count(*) FROM recovery_attempts').fetchone()[0] == 1
    assert recover(result['codes'][0]).status_code == 200


def test_codes_exhaust_without_extending_expiry(client):
    enable(client)
    result = issue(client)
    for code in result['codes']:
        assert recover(code).status_code == 200
    options = login_as().get(OPTIONS).json()
    assert options['status'] == 'exhausted' and options['remaining'] == 0
    assert options['expires'] == result['expires']


def test_validation_csrf_and_unconfigured_access_do_not_expose_credentials(client):
    assert client.get(OPTIONS).status_code == 401
    assert recover('bad').status_code == 400
    enable(client)
    result = issue(client)
    for path, body in [('/api/auth/recover', dict(login='admin', code=result['codes'][0], password='short-123')),
                       (ISSUE, dict(version=1, currentPassword=PASSWORD, extraSecret='hidden-secret')),
                       ('/api/auth/login', dict(login='admin', password='x'*129))]:
        response = client.post(path, json=body)
        assert response.status_code == 422
        assert all('input' not in error and 'ctx' not in error for error in response.json()['detail'])
        for key in ['password', 'currentPassword', 'code', 'extraSecret']:
            if key in body: assert body[key] not in response.text
    payload = dict(login='admin', code=result['codes'][0], password=NEW)
    assert visitor().post('/api/auth/recover', json=payload, headers={'X-Atlas-Request':'0'}).status_code == 403
    assert visitor().post('/api/auth/recover', json=payload, headers={'Origin':'https://evil.example'}).status_code == 403
    assert client.get(OPTIONS).json()['remaining'] == 8
