import pytest
from fastapi.testclient import TestClient
from backend import api


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(api, 'DB', tmp_path / 'test.db')
    return TestClient(api.app)


def test_storage_revision_and_undo(client):
    state = client.get('/api/state?workspace=demo').json()
    original = state['settings']['name']
    stale = dict(state)
    state['settings']['name'] = 'Изменение 1'
    saved = client.put('/api/state?workspace=demo', json=state)
    assert saved.status_code == 200
    assert saved.json()['revision'] == 1
    assert client.put('/api/state?workspace=demo', json=stale).status_code == 409
    state = saved.json()
    state['settings']['name'] = 'Изменение 2'
    client.put('/api/state?workspace=demo', json=state)
    restored = client.post('/api/undo?workspace=demo', json={'revision': 2}).json()
    assert restored['settings']['name'] == 'Изменение 1'
    restored = client.post('/api/undo?workspace=demo', json={'revision': 3}).json()
    assert restored['settings']['name'] == original
    assert client.get('/api/state?workspace=demo').json() == restored


def test_bad_import_does_not_overwrite(client):
    state = client.get('/api/state?workspace=demo').json()
    invalid = {**state, 'teachers': []}
    assert client.put('/api/state?workspace=demo', json=invalid).status_code == 422
    assert client.get('/api/state?workspace=demo').json() == state


def test_demo_separate_and_generate_is_saved(client):
    school = client.get('/api/state').json()
    state = client.get('/api/state?workspace=demo').json()
    result = client.post('/api/generate?workspace=demo', json={'revision': state['revision'], 'seconds': 3})
    assert result.status_code == 200
    assert result.json()['status'] == 'success'
    assert len(client.get('/api/state?workspace=demo').json()['placements']) == 24
    assert client.get('/api/state').json() == school


def test_external_site_cannot_write(client):
    state = client.get('/api/state?workspace=demo').json()
    response = client.put('/api/state?workspace=demo', json=state, headers={'origin': 'https://example.com'})
    assert response.status_code == 403


def test_stale_generator_does_not_overwrite(client):
    client.get('/api/state?workspace=demo')
    assert client.post('/api/generate?workspace=demo', json={'revision': 999}).status_code == 409


def test_cycle_preview_is_atomic_and_checks_and_dates_select_a_week(client):
    original = client.get('/api/state?workspace=demo').json()
    cycles = dict(patterns=[dict(id='weekly', name='A'), dict(id='b', name='B')], periods=[])
    preview = client.post('/api/editor/preview?workspace=demo', json=dict(revision=0, action='cycles', cycles=cycles)).json()
    assert preview['canApply']
    assert client.get('/api/state?workspace=demo').json() == original
    saved = client.post('/api/editor/apply?workspace=demo', json=dict(revision=0, token=preview['token']))
    assert saved.status_code == 200 and saved.json()['cycles'] == cycles
    assert client.post('/api/editor/apply?workspace=demo', json=dict(revision=0, token=preview['token'])).status_code == 409
    assert client.get('/api/check?workspace=demo&cycleId=b').json()['required'] == 24
    assert client.get('/api/check?workspace=demo&cycleId=missing').status_code == 422
    day = client.get('/api/calendar-pattern?workspace=demo&day=2026-08-24').json()
    assert day['cycleId'] == 'b'
    assert client.get('/api/calendar-pattern?workspace=demo&day=2026-08-16').json()['cycleId'] is None
