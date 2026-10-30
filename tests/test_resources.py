import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from backend import api
from backend.models import State, Placement, TimetableRule
from backend.scheduling import solve, validate_schedule


def fixture():
    return State.model_validate(dict(
        settings=dict(days=['Понедельник', 'Вторник']),
        profiles=[dict(id='p', name='Звонки', periods=[dict(label=str(i), start=a, end=b) for i, (a, b) in enumerate([
            ('08:00', '08:40'), ('08:45', '09:25'), ('09:45', '10:25'), ('10:30', '11:10')])])],
        classes=[dict(id=f'c{i}', name=f'Класс {i}', profileId='p', studentCount=1) for i in [1, 2]],
        students=[dict(id=f's{i}', name=f'Ученик {i}', classId=f'c{i}') for i in [1, 2]],
        teachers=[dict(id=f't{i}', name=f'Учитель {i}') for i in [1, 2, 3]],
        groups=[dict(id=f'g{i}', name=f'Класс {i}', classIds=[f'c{i}']) for i in [1, 2]] + [dict(id='staff', name='Совещание', kind='staff')],
        subjects=[dict(id='s', name='Предмет')],
        campus=dict(buildings=[dict(id='a', name='А'), dict(id='b', name='Б')], defaultTravelMinutes=20),
        rooms=[dict(id='a1', name='А1', capacity=10, buildingId='a'), dict(id='a2', name='А2', capacity=10, buildingId='a'), dict(id='b1', name='Б1', capacity=20, buildingId='b')],
        workloads=[dict(id=f'w{i}', groupId=f'g{i}', subjectId='s', teacherId=f't{i}', hours=1, profileId='p', roomIds=[room]) for i, room in [(1, 'a1'), (2, 'b1')]],
    ))


def placement(wid, period, room, locked=False, day=0):
    return Placement(id='lesson-' + wid, workloadId=wid, day=day, period=period, roomId=room, locked=locked)


def generated(s, ids=None):
    result = solve(s, ids, seconds=3)
    assert result['status'] == 'success', result
    candidate = State.model_validate({**s.model_dump(), 'placements': result['placements']})
    assert validate_schedule(candidate)['issueCount'] == 0
    return candidate


def test_every_coteacher_is_reserved_and_reported():
    s = fixture()
    s.workloads[0].additionalTeacherIds = ['t2']
    s.placements = [placement('w1', 0, 'a1'), placement('w2', 0, 'b1')]
    issue = next(i for i in validate_schedule(s)['issues'] if i['type'] == 'collision')
    assert issue['collision']['teacherIds'] == ['t2']
    candidate = generated(s)
    assert len(candidate.placements) == 2


def test_coteacher_legacy_and_clock_rule_availability_and_daily_limit():
    s = fixture()
    s.workloads = s.workloads[:1]
    s.workloads[0].additionalTeacherIds = ['t2']
    s.teachers[1].unavailable = ['0:0', '0:1', '0:2', '0:3']
    s.rules = [TimetableRule(id='r', name='Вторник занят', kind='availability', scope='teacher', targetIds=['t2'], unavailable=[dict(day=1, start='00:00', end='23:59')])]
    assert solve(s, seconds=3)['status'] == 'infeasible'
    s.rules = [TimetableRule(id='r', name='Нулевая нагрузка', kind='daily_load', scope='teacher', targetIds=['t2'], maxLessons=0)]
    assert solve(s, seconds=3)['status'] == 'infeasible'
    s.placements = [placement('w1', 0, 'a1')]
    assert {'availability', 'rule'} <= {i['type'] for i in validate_schedule(s)['issues']}


def test_teacherless_is_explicit_and_staff_has_no_pupil_resource():
    s = fixture()
    for w in s.workloads:
        w.teacherId = None
        w.teacherRequired = False
        w.groupId = 'staff'
        w.roomIds = []
        w.roomRequired = False
        w.allowedPeriods = [0]
    candidate = generated(s)
    report = validate_schedule(candidate)
    assert report['unknownGroups'] == report['missingTeachers'] == report['missingRooms'] == 0
    assert all(p.roomId is None for p in candidate.placements)
    s.workloads[0].teacherRequired = True
    assert solve(s)['status'] == 'blocked'


def test_all_permanent_rooms_reserved_and_support_double_lessons_without_primary():
    s = fixture()
    s.workloads[0].roomIds = []
    s.workloads[0].requiredRoomIds = ['a1', 'a2']
    s.workloads[0].hours = 2
    s.workloads[0].lessonLengths = [2]
    s.workloads[1].roomIds = ['a2']
    s.placements = [placement('w1', 0, None), placement('w2', 0, 'a2')]
    issue = next(i for i in validate_schedule(s)['issues'] if i['type'] == 'collision')
    assert issue['collision']['roomIds'] == ['a2']
    generated(s)


def test_permanent_room_availability_and_daily_rules_apply_to_every_room():
    s = fixture()
    s.workloads = s.workloads[:1]
    s.workloads[0].roomIds = []
    s.workloads[0].requiredRoomIds = ['a1', 'a2']
    s.rules = [TimetableRule(id='r', name='А2 закрыт', kind='availability', scope='room', targetIds=['a2'], unavailable=[dict(day=d, start='00:00', end='23:59') for d in [0, 1]])]
    assert solve(s, seconds=3)['status'] == 'infeasible'
    s.rules = [TimetableRule(id='r', name='А2 нагрузка', kind='daily_load', scope='room', targetIds=['a2'], maxLessons=0)]
    s.placements = [placement('w1', 0, None)]
    assert any(i['type'] == 'rule' for i in validate_schedule(s)['issues'])
    assert solve(s, seconds=3)['status'] == 'infeasible'


def test_capacity_sum_room_choice_unknown_counts_and_cannot_shrink_known_roster():
    s = fixture()
    s.workloads = s.workloads[:1]
    s.classes[0].studentCount = 15
    s.workloads[0].roomIds = ['a1', 'b1']
    s.placements = [placement('w1', 0, 'a1')]
    assert any(i['type'] == 'room_capacity' for i in validate_schedule(s)['issues'])
    assert generated(s).placements[0].roomId == 'b1'
    s.workloads[0].roomIds = []
    s.workloads[0].requiredRoomIds = ['a1', 'a2']
    assert generated(s).placements[0].roomId is None
    s.classes[0].studentCount = None
    s.placements = [placement('w1', 0, None)]
    assert validate_schedule(s)['unknownCapacity'] == 1
    s.workloads[0].studentCount = 1
    s.workloads[0].groupId = 'g2'
    s.groups[1].kind = 'core'
    s.groups[1].classIds = ['c1', 'c2']
    s.groups[1].studentIds = ['s1', 's2']
    s.rooms[0].capacity = 1
    s.workloads[0].requiredRoomIds = ['a1']
    assert any(i['type'] == 'room_capacity' for i in validate_schedule(s)['issues'])


def test_simultaneous_rooms_cannot_span_buildings():
    s = fixture()
    s.workloads[0].requiredRoomIds = ['b1']
    s.placements = [placement('w1', 0, 'a1')]
    assert any(i['type'] == 'room_building' for i in validate_schedule(s)['issues'])
    assert solve(s)['status'] == 'blocked'


@pytest.mark.parametrize('shared', ['teacher', 'pupil'])
def test_transfers_actual_time_direction_and_partial_generation(shared):
    s = fixture()
    if shared == 'teacher':
        s.workloads[1].additionalTeacherIds = ['t1']
    else:
        s.workloads[1].groupId = 'g1'
    s.placements = [placement('w1', 0, 'a1', True), placement('w2', 1, 'b1')]
    report = validate_schedule(s)
    travel = next(i for i in report['issues'] if i['type'] == 'travel')
    assert {i['type'] for i in report['issues']} == {'travel'}
    assert travel['affectedStudentIds'] == (['s1'] if shared == 'pupil' else [])
    candidate = generated(s, ['w2'])
    assert candidate.placements[0] == s.placements[0]
    # Both bell boundaries matter; there is only a five-minute transfer.
    s.campus.transfers = State.model_validate({**s.model_dump(), 'campus': {**s.campus.model_dump(), 'transfers': [dict(fromId='a', toId='b', minutes=5)]}}).campus.transfers
    assert validate_schedule(s)['issueCount'] == 0
    s.placements = [placement('w1', 1, 'a1'), placement('w2', 0, 'b1')]
    assert any(i['type'] == 'travel' for i in validate_schedule(s)['issues'])
    s.placements[1].day = 1
    assert validate_schedule(s)['issueCount'] == 0


def test_unknown_building_is_reported_and_roomless_requires_explicit_empty_assignment():
    s = fixture()
    s.rooms[0].buildingId = None
    s.placements = [placement('w1', 0, 'a1')]
    report = validate_schedule(s)
    assert report['unknownBuildings'] == 1 and not report['completeChecks']
    raw = s.model_dump()
    raw['workloads'][0].update(roomRequired=False, roomIds=[])
    with pytest.raises(ValidationError):
        State.model_validate(raw)


def test_transfers_follow_consecutive_lessons_not_a_journey_replaced_by_another_lesson():
    raw = fixture().model_dump()
    raw['campus']['buildings'].append(dict(id='c', name='В'))
    raw['campus']['transfers'] = [dict(fromId='a', toId='c', minutes=5), dict(fromId='c', toId='b', minutes=20), dict(fromId='a', toId='b', minutes=100)]
    raw['rooms'].append(dict(id='c1', name='В1', buildingId='c', capacity=20))
    raw['workloads'][1]['groupId'] = 'g1'
    raw['workloads'].append({**raw['workloads'][0], 'id': 'w3', 'roomIds': ['c1']})
    s = State.model_validate(raw)
    s.placements = [placement('w1', 0, 'a1', True), placement('w3', 1, 'c1', True), placement('w2', 2, 'b1', True)]
    assert validate_schedule(s)['issueCount'] == 0
    generated(s)


def test_reference_validation_prevents_duplicate_teachers_rooms_and_invalid_classless_groups():
    for field, value in [('additionalTeacherIds', ['t1']), ('additionalTeacherIds', ['absent']), ('requiredRoomIds', ['a1']), ('teacherRequired', False)]:
        raw = fixture().model_dump()
        raw['workloads'][0][field] = value
        with pytest.raises(ValidationError):
            State.model_validate(raw)
    raw = fixture().model_dump()
    raw['groups'][0]['kind'] = 'staff'
    with pytest.raises(ValidationError):
        State.model_validate(raw)
    raw = fixture().model_dump()
    raw['campus']['buildings'] = []
    with pytest.raises(ValidationError):
        State.model_validate(raw)


def saved_school(tmp_path, monkeypatch, s):
    monkeypatch.setattr(api, 'DB', tmp_path / 'editor.db')
    client = TestClient(api.app)
    s.revision = client.get('/api/state?workspace=demo').json()['revision']
    return client, client.put('/api/state?workspace=demo', json=s.model_dump(mode='json')).json()


def test_editor_blocks_new_coteacher_conflict(tmp_path, monkeypatch):
    s = fixture()
    s.placements = [placement('w1', 0, 'a1'), placement('w2', 0, 'b1')]
    client, saved = saved_school(tmp_path, monkeypatch, s)
    preview = client.post('/api/editor/preview?workspace=demo', json=dict(revision=saved['revision'], action='workloads', ids=['w1'], patch=dict(additionalTeacherIds=['t2'])))
    assert preview.status_code == 200, preview.text
    assert not preview.json()['canApply']
    assert client.post('/api/editor/apply?workspace=demo', json=dict(revision=saved['revision'], token=preview.json()['token'])).status_code == 422
    assert next(w for w in client.get('/api/state?workspace=demo').json()['workloads'] if w['id'] == 'w1')['additionalTeacherIds'] == []


def test_campus_preview_applies_once_and_only_on_apply(tmp_path, monkeypatch):
    client, saved = saved_school(tmp_path, monkeypatch, fixture())
    campus = {**saved['campus'], 'defaultTravelMinutes': 35}
    preview = client.post('/api/editor/preview?workspace=demo', json=dict(revision=saved['revision'], action='campus', campus=campus)).json()
    assert preview['canApply'] and preview['diff']['total'] == 1
    assert client.get('/api/state?workspace=demo').json()['campus']['defaultTravelMinutes'] == 20
    applied = client.post('/api/editor/apply?workspace=demo', json=dict(revision=saved['revision'], token=preview['token'])).json()
    assert applied['campus']['defaultTravelMinutes'] == 35
    assert client.post('/api/editor/apply?workspace=demo', json=dict(revision=saved['revision'], token=preview['token'])).status_code == 409
