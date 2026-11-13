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
    with pytest.raises(ValueError):
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



def test_reducing_existing_daily_violation_can_be_applied_but_new_excess_cannot():
    s = fixture_state()
    s.workloads[0].maxPerDay = 1
    s.placements += [Placement(id=f'a{i}', workloadId='w0', day=0, period=i, roomId='r0') for i in (2, 3)]
    reduced = inspect_edit(s, edit(s, EditRequest(revision=0, action='move', ids=['a3'], day=3, period=1)))
    assert reduced['canApply'] and reduced['newIssues'] == []
    s.placements.append(Placement(id='a4', workloadId='w0', day=3, period=1, roomId='r0'))
    shifted = inspect_edit(s, edit(s, EditRequest(revision=0, action='move', ids=['a3'], day=3, period=2)))
    assert not shifted['canApply'] and any(i['type'] == 'daily' for i in shifted['newIssues'])

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


def rename(client, name):
    """Save the demo workspace under a new settings name and return the stored state."""
    state = client.get('/api/state?workspace=demo').json()
    state['settings']['name'] = name
    return client.put('/api/state?workspace=demo', json=state).json()


def test_undo_then_redo_restores_the_change(client):
    changed = rename(client, 'Changed')
    undone = client.post('/api/undo?workspace=demo', json={'revision': changed['revision']}).json()
    assert undone['settings']['name'] != 'Changed'
    assert client.get('/api/history?workspace=demo').json()['redo'] == 1
    redone = client.post('/api/redo?workspace=demo', json={'revision': undone['revision']}).json()
    assert redone['settings']['name'] == 'Changed'


def test_new_edit_after_undo_drops_the_redo_branch(client):
    changed = rename(client, 'Changed')
    undone = client.post('/api/undo?workspace=demo', json={'revision': changed['revision']}).json()
    branched = client.put('/api/state?workspace=demo', json=undone).json()
    assert client.get('/api/history?workspace=demo').json()['redo'] == 0
    assert client.post('/api/redo?workspace=demo', json={'revision': branched['revision']}).status_code == 400


def test_named_variant_previews_and_restores_the_saved_state(client):
    before = client.get('/api/state?workspace=demo').json()
    variant = client.post('/api/variants?workspace=demo', json={'revision': before['revision'], 'name': 'До правок'}).json()
    changed = rename(client, 'Changed')
    preview = client.post(f'/api/variants/{variant["id"]}/preview?workspace=demo', json={'revision': changed['revision']}).json()
    assert [c['table'] for c in preview['diff']['changes']] == ['settings']
    applied = client.post('/api/editor/apply?workspace=demo', json={'revision': changed['revision'], 'token': preview['token']})
    assert applied.status_code == 200 and applied.json()['settings'] == before['settings']


def test_variants_belong_to_their_workspace(client):
    variant = client.post('/api/variants?workspace=demo', json={'revision': 1, 'name': 'До правок'}).json()
    assert client.get('/api/variants?workspace=school').json() == []
    assert client.delete(f'/api/variants/{variant["id"]}?workspace=school').status_code == 404
    assert client.delete(f'/api/variants/{variant["id"]}?workspace=demo').status_code == 200


def test_slot_hints_mark_the_busy_slot_unavailable_and_a_free_one_available(client):
    slots = client.post('/api/editor/slots?workspace=demo', json={'revision': 1, 'placementId': 'a'}).json()['slots']
    slot = lambda day, period: next(s for s in slots if s['day'] == day and s['period'] == period)
    assert not slot(1, 1)['available'] and slot(4, 1)['available']


def preview_rule(client, revision, rule):
    return client.post('/api/editor/preview?workspace=demo', json={'revision': revision, 'action': 'rule', 'rule': rule}).json()


def preview_move(client, revision):
    return client.post('/api/editor/preview?workspace=demo', json={'revision': revision, 'action': 'move', 'ids': ['a'], 'day': 4}).json()


def apply_preview(client, revision, preview):
    response = client.post('/api/editor/apply?workspace=demo', json={'revision': revision, 'token': preview['token']})
    assert response.status_code == 200
    return response.json()['revision']


def teacher_rule(**values):
    return {'id': 'teacher-load', 'name': 'Предел учителя', 'kind': 'daily_load', 'scope': 'teacher', 'targetIds': ['t2'], 'maxLessons': 0, 'weight': 7, **values}


def test_hard_rule_review_lists_violations_and_blocks_a_move_into_them(client):
    preview = preview_rule(client, 1, teacher_rule())
    assert preview['canApply'] and preview['newIssues']
    assert preview['diff']['changes'][0]['fields']['targetIds']['after'] == ['t2']
    revision = apply_preview(client, 1, preview)
    assert not preview_move(client, revision)['canApply']


def test_soft_rule_penalises_but_does_not_block_a_move(client):
    preview = preview_rule(client, 1, teacher_rule(mode='soft'))
    warnings = preview['report']['warningCount']
    assert preview['canApply'] and warnings and preview['report']['qualityPenalty'] == 7 * warnings
    revision = apply_preview(client, 1, preview)
    allowed = preview_move(client, revision)
    assert allowed['canApply'] and allowed['report']['warningCount'] == warnings
