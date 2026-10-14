import pytest
from fastapi.testclient import TestClient
from backend import api
from backend.editing import EditRequest, edit, inspect_edit
from backend.models import Placement
from backend.seed import initial_state


def fixture_state():
    s = initial_state('demo')
    s.placements = [Placement(id='a', workloadId='w0', day=0, period=1, roomId='r0'),
                    Placement(id='b', workloadId='w1', day=1, period=1, roomId='r1'),
                    Placement(id='m', workloadId='w4', day=2, period=3, roomId='r0'),
                    Placement(id='f', workloadId='w5', day=2, period=3, roomId='r1')]
    return s


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(api, 'DB', tmp_path / 'test.db')
    c = TestClient(api.app)
    c.get('/api/state?workspace=demo')
    c.put('/api/state?workspace=demo', json=fixture_state().model_dump(mode='json'))
    return c


def test_linked_move_is_atomic_and_locked_partner_protected():
    s = fixture_state()
    request = EditRequest(revision=0, action='move', ids=['m'], day=3)
    changed = edit(s, request)
    assert [p.day for p in changed.placements if p.id in ['m', 'f']] == [3, 3]
    assert s.placements[2].day == 2
    assert inspect_edit(s, changed)['canApply']
    s.placements[3].locked = True
    with pytest.raises(ValueError, match='закреплённые'):
        edit(s, request)


def test_swap_preserves_ids_rooms_and_source_and_obeys_real_time():
    s = fixture_state()
    s.placements[0].sourceCells = ['original']
    changed = edit(s, EditRequest(revision=0, action='swap', ids=['a', 'b']))
    assert [(p.id, p.day, p.roomId) for p in changed.placements[:2]] == [('a', 1, 'r0'), ('b', 0, 'r1')]
    assert changed.placements[0].sourceCells == ['original']
    assert inspect_edit(s, changed)['canApply']
    conflicting = edit(s, EditRequest(revision=0, action='move', ids=['a'], day=1))
    assert not inspect_edit(s, conflicting)['canApply']


def test_copy_hours_and_independent_block_detected():
    s = fixture_state()
    s.workloads[0].hours = 1
    copied = edit(s, EditRequest(revision=0, action='copy', ids=['a'], day=3))
    assert len(copied.placements) == 5
    assert not inspect_edit(s, copied)['canApply']
    copied = edit(s, EditRequest(revision=0, action='copy', ids=['a'], day=3, increaseHours=True))
    assert copied.workloads[0].hours == 2
    assert inspect_edit(s, copied)['canApply']
    broken = edit(s, EditRequest(revision=0, action='move', ids=['m'], day=4, linked=False))
    assert any(i['type'] == 'block' for i in inspect_edit(s, broken)['newIssues'])


def test_bulk_fields_validate_references_and_bounds():
    s = fixture_state()
    with pytest.raises(ValueError):
        edit(s, EditRequest(revision=0, action='workloads', ids=['w0'], patch={'teacherId': 'absent'}))
    with pytest.raises(ValueError):
        edit(s, EditRequest(revision=0, action='workloads', ids=['w0'], patch={'hours': 0}))
    changed = edit(s, EditRequest(revision=0, action='workloads', ids=['w0', 'w1'], patch={'hours': 6}))
    assert [w.hours for w in changed.workloads[:2]] == [6, 6]
    assert len(inspect_edit(s, changed)['diff']['changes']) == 2


def test_worsening_existing_daily_violation_is_not_silently_allowed():
    s = fixture_state()
    s.workloads[0].maxPerDay = 1
    s.placements.append(Placement(id='a2', workloadId='w0', day=0, period=2, roomId='r0'))
    changed = edit(s, EditRequest(revision=0, action='copy', ids=['a'], period=3))
    result = inspect_edit(s, changed)
    assert not result['canApply']
    assert any(i['type'] == 'daily' for i in result['newIssues'])


def test_preview_does_not_write_and_stale_or_conflicting_apply_rejected(client):
    original = client.get('/api/state?workspace=demo').json()
    preview = client.post('/api/editor/preview?workspace=demo', json={'revision': 1, 'action': 'move', 'ids': ['a'], 'day': 4}).json()
    assert preview['canApply'] and preview['diff']['changed'] == 1
    assert client.get('/api/state?workspace=demo').json() == original
    bad = client.post('/api/editor/preview?workspace=demo', json={'revision': 1, 'action': 'move', 'ids': ['a'], 'day': 1}).json()
    assert not bad['canApply']
    assert client.post('/api/editor/apply?workspace=demo', json={'revision': 1, 'token': bad['token']}).status_code == 422
    assert client.post('/api/editor/apply?workspace=school', json={'revision': 1, 'token': preview['token']}).status_code == 404
    applied = client.post('/api/editor/apply?workspace=demo', json={'revision': 1, 'token': preview['token']})
    assert applied.status_code == 200 and applied.json()['placements'][0]['day'] == 4
    assert client.post('/api/editor/apply?workspace=demo', json={'revision': 1, 'token': preview['token']}).status_code == 409


def test_undo_redo_branch_and_named_variant(client):
    variant = client.post('/api/variants?workspace=demo', json={'revision': 1, 'name': 'До правок'}).json()
    state = client.get('/api/state?workspace=demo').json()
    state['settings']['name'] = 'Changed'
    client.put('/api/state?workspace=demo', json=state)
    assert client.post('/api/undo?workspace=demo', json={'revision': 2}).json()['settings']['name'] != 'Changed'
    assert client.get('/api/history?workspace=demo').json()['redo'] == 1
    assert client.post('/api/redo?workspace=demo', json={'revision': 3}).json()['settings']['name'] == 'Changed'
    preview = client.post(f'/api/variants/{variant["id"]}/preview?workspace=demo', json={'revision': 4}).json()
    assert preview['diff']['total'] == 1
    assert preview['diff']['changes'][0]['table'] == 'settings'
    assert client.post('/api/editor/apply?workspace=demo', json={'revision': 4, 'token': preview['token']}).status_code == 200
    undone = client.post('/api/undo?workspace=demo', json={'revision': 5}).json()
    client.put('/api/state?workspace=demo', json=undone)
    assert client.get('/api/history?workspace=demo').json()['redo'] == 0
    assert client.post('/api/redo?workspace=demo', json={'revision': 7}).status_code == 400
    assert client.get('/api/variants?workspace=school').json() == []
    assert client.delete(f'/api/variants/{variant["id"]}?workspace=school').status_code == 404
    assert client.delete(f'/api/variants/{variant["id"]}?workspace=demo').status_code == 200


def test_slot_hints_and_generation_preview_keep_state(client):
    original = client.get('/api/state?workspace=demo').json()
    slots = client.post('/api/editor/slots?workspace=demo', json={'revision': 1, 'placementId': 'a'}).json()
    assert len(slots['slots']) == 55
    assert not next(s for s in slots['slots'] if s['day'] == 1 and s['period'] == 1)['available']
    assert next(s for s in slots['slots'] if s['day'] == 4 and s['period'] == 1)['available']
    generated = client.post('/api/generate?workspace=demo', json={'revision': 1, 'preview': True, 'seconds': 3}).json()
    assert generated['status'] == 'success'
    assert 'state' not in generated and generated['preview']['canApply']
    assert client.get('/api/state?workspace=demo').json() == original


def test_rule_configuration_can_be_reviewed_with_violations_and_soft_rules_do_not_block(client):
    rule = {'id': 'teacher-load', 'name': 'Предел учителя', 'kind': 'daily_load', 'scope': 'teacher', 'targetIds': ['t2'], 'maxLessons': 0, 'weight': 7}
    preview = client.post('/api/editor/preview?workspace=demo', json={'revision': 1, 'action': 'rule', 'rule': rule}).json()
    assert preview['canApply'] and len(preview['newIssues']) == 2
    assert preview['diff']['changes'][0]['fields']['targetIds']['displayAfter'] == 'Учитель английского'
    assert client.post('/api/editor/apply?workspace=demo', json={'revision': 1, 'token': preview['token']}).status_code == 200
    blocked = client.post('/api/editor/preview?workspace=demo', json={'revision': 2, 'action': 'move', 'ids': ['a'], 'day': 4}).json()
    assert not blocked['canApply']
    rule['mode'] = 'soft'
    soft = client.post('/api/editor/preview?workspace=demo', json={'revision': 2, 'action': 'rule', 'rule': rule}).json()
    assert soft['canApply'] and soft['report']['qualityPenalty'] == 14
    client.post('/api/editor/apply?workspace=demo', json={'revision': 2, 'token': soft['token']})
    allowed = client.post('/api/editor/preview?workspace=demo', json={'revision': 3, 'action': 'move', 'ids': ['a'], 'day': 4}).json()
    assert allowed['canApply'] and allowed['report']['warningCount'] == 2
