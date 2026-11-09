import threading
import time
import pytest
from fastapi.testclient import TestClient
from backend import api, jobs
from backend.scheduling import solve
from backend.seed import initial_state
from seeding import insert_row


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(api, 'DB', tmp_path / 'jobs.db')
    c = TestClient(api.app)
    c.get('/api/state?workspace=demo')
    yield c
    api.shutdown_generation_jobs()


def finished(client, id):
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        result = client.get(f'/api/generation-jobs/{id}?workspace=demo').json()
        if result['status'] not in ['queued', 'running', 'cancelling']:
            # Wait for the worker to persist its terminal state before fixture teardown.
            with api.connect() as db:
                import json
                stored = json.loads(db.execute('SELECT document FROM generation_jobs WHERE id=?', (id,)).fetchone()[0])
            if stored['status'] not in ['queued', 'running', 'cancelling']:
                return result
        time.sleep(0.02)
    pytest.fail('Job did not finish')


def test_background_search_returns_preview_without_writing(client):
    before = client.get('/api/state?workspace=demo').json()
    weights={'early':0,'stability':100,'preferences':2}
    started = client.post('/api/generation-jobs?workspace=demo', json={'revision': 0, 'seconds': 2, 'optimization':weights})
    assert started.status_code == 200
    id = started.json()['id']
    assert client.get(f'/api/generation-jobs/{id}?workspace=school').status_code == 404
    result = finished(client, id)
    assert result['status'] == 'ready', result
    assert result['result']['preview']['canApply']
    assert result['optimization']==result['result']['optimization']['weights']==weights
    assert result['progress']['solutions'] >= 1
    assert client.get('/api/state?workspace=demo').json() == before
    state = {**before, 'revision': 0}
    state['settings']['name'] = 'New revision'
    client.put('/api/state?workspace=demo', json=state)
    preview = result['result']['preview']
    assert client.post('/api/editor/apply?workspace=demo', json={'token': preview['token'], 'revision': 1}).status_code == 409
    assert client.post('/api/generation-jobs?workspace=demo', json={'revision': 0}).status_code == 409


def test_cancellation_of_running_and_queued_search(client, monkeypatch):
    entered = threading.Event()
    completed = threading.Event()
    def long_solve(state, *args, cancel, progress, **options):
        entered.set()
        progress({'phase': 'searching'})
        assert cancel.wait(5)
        completed.set()
        return {'status': 'cancelled', 'message': 'Cancelled', 'details': []}
    monkeypatch.setattr(jobs, 'solve', long_solve)
    a = client.post('/api/generation-jobs?workspace=demo', json={'revision': 0}).json()
    assert entered.wait(3)
    b = client.post('/api/generation-jobs?workspace=demo', json={'revision': 0}).json()
    response = client.post(f'/api/generation-jobs/{b["id"]}/cancel?workspace=demo').json()
    assert response['status'] == 'cancelled'
    assert client.post(f'/api/generation-jobs/{a["id"]}/cancel?workspace=school').status_code == 404
    client.post(f'/api/generation-jobs/{a["id"]}/cancel?workspace=demo')
    assert finished(client, a['id'])['status'] == 'cancelled'
    assert finished(client, b['id'])['status'] == 'cancelled'
    assert completed.wait(2)
    assert client.get('/api/state?workspace=demo').json()['revision'] == 0


def test_solver_honors_cancel_before_search_and_during_model_build():
    event = threading.Event()
    event.set()
    assert solve(initial_state('demo'), cancel=event)['status'] == 'cancelled'
    event.clear()
    def progress(value):
        event.set()
    assert solve(initial_state('demo'), cancel=event, progress=progress)['status'] == 'cancelled'


def test_a_job_left_running_by_a_restart_is_reported_interrupted(client):
    import json
    with api.connect() as db:
        insert_row(db, 'generation_jobs', id='old', workspace='demo', created='2026-01-01', document=json.dumps({'id': 'old', 'status': 'running'}))
    assert client.get('/api/generation-jobs/old?workspace=demo').json()['status'] == 'interrupted'


def test_a_search_for_an_unknown_workload_is_refused(client):
    assert client.post('/api/generation-jobs?workspace=demo', json={'revision': 0, 'workloadIds': ['absent']}).status_code == 422
