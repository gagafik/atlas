import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from backend import api
from backend.seed import initial_state
from backend.models import State, Course, CourseRequest, Group, Placement, TimetableRule, Cycles
from backend.courses import allocate, AllocationRequest
from backend.editing import EditRequest, edit
from backend.scheduling import validate_schedule, roster_complete
from backend.resources import ResourceContext


def fixture():
    s = initial_state('demo')
    s.placements = []
    s.workloads = s.workloads[4:]
    for w in s.workloads:
        w.hours = 1
        w.block = ''
    for g in s.groups:
        if g.kind != 'whole':
            g.studentIds = []
            g.rosterStatus = 'complete'
    s.courses = [Course(id='math', name='Математика', sections=[dict(groupId='g-math', maxSize=2)]),
                 Course(id='physics', name='Физика', sections=[dict(groupId='g-core', maxSize=2)])]
    return s


def request(student, courses=('math', 'physics'), **kw):
    return CourseRequest(id='r-'+student, studentId=student, courseIds=list(courses), **kw)


def run(s, **kw):
    return allocate(s, AllocationRequest(revision=s.revision, seconds=3, **kw))


def test_capacity_priorities_and_fixed_memberships_are_preserved():
    s = fixture()
    s.groups[2].studentIds = ['s3']
    s.groups[3].studentIds = ['s4']
    s.courseRequests = [request('s1'), request('s2', ('physics', 'math'))]
    before = s.model_dump_json()
    result = run(s)
    assert result['status'] == 'success', result
    assert result['assigned'] == result['firstChoices'] == 2
    assert s.model_dump_json() == before
    assert set(result['candidate'].groups[2].studentIds) == {'s1', 's3'}
    assert set(result['candidate'].groups[3].studentIds) == {'s2', 's4'}
    assert result['unplacedHours'] == 2


def test_optional_waitlist_does_not_evict_fixed_students_and_required_fails():
    s = fixture()
    s.groups[2].studentIds = ['s3', 's4']
    s.courseRequests = [request('s1', ('math',), required=False)]
    result = run(s)
    assert result['status'] == 'success' and result['unassigned'] == 1
    assert result['candidate'].groups == s.groups
    s.courseRequests[0].required = True
    result = run(s)
    assert result['status'] == 'infeasible' and 'candidate' not in result


def test_existing_section_preferred_and_locks_must_respect_capacity():
    s = fixture()
    s.courses = [Course(id='math', name='Курс', sections=[dict(groupId='g-math', maxSize=1), dict(groupId='g-core', maxSize=2)])]
    s.groups[3].studentIds = ['s1']
    s.courseRequests = [request('s1', ('math',))]
    result = run(s)
    assert result['rows'][0]['groupId'] == 'g-core' and result['changedStudents'] == 0
    s.groups[2].studentIds = ['s3']
    s.courseRequests[0].lockedGroupId = 'g-math'
    assert run(s)['status'] == 'infeasible'


def test_actual_time_and_all_weeks_are_checked_against_unrelated_courses():
    s = fixture()
    s.cycles = Cycles(patterns=[dict(id='weekly', name='A'), dict(id='b', name='B')])
    s.placements = [Placement(id='p1', workloadId='w4', cycleId='b', day=0, period=1, roomId='r0'),
                    Placement(id='p2', workloadId='w5', cycleId='b', day=0, period=1, roomId='r1')]
    s.groups[3].studentIds = ['s1']
    s.courseRequests = [request('s1', ('math',), required=False)]
    result = run(s)
    assert result['unassigned'] == 1 and result['rows'][0]['reasons']
    assert result['candidate'].groups[3].studentIds == ['s1']
    s.placements[1].cycleId = 'weekly'
    assert run(s)['assigned'] == 1


def test_requests_cannot_duplicate_course_or_overlap_each_other():
    s = fixture()
    s.courseRequests = [request('s1', ('math',)), CourseRequest(id='second', studentId='s1', courseIds=['math'])]
    assert run(s)['status'] == 'infeasible'
    s.courseRequests[1].courseIds = ['physics']
    s.placements = [Placement(id='p1', workloadId='w4', day=0, period=1, roomId='r0'),
                    Placement(id='p2', workloadId='w5', day=0, period=1, roomId='r1')]
    assert run(s)['status'] == 'infeasible'
    s.placements[1].period = 2
    assert run(s)['assigned'] == 2


def test_class_eligibility_archived_pupil_and_incomplete_roster():
    s = fixture()
    s.groups[2].classIds = ['c-demo-d']
    s.courseRequests = [request('s1')]
    assert run(s)['rows'][0]['courseId'] == 'physics'
    s.students[0].active = False
    assert run(s)['status'] == 'blocked'
    s.students[0].active = True
    s.groups[3].rosterStatus = 'partial'
    assert run(s)['status'] == 'blocked'


def test_room_capacity_and_shared_personal_rule_can_choose_alternative():
    s = fixture()
    s.rooms[0].capacity = 1
    s.groups[2].studentIds = ['s3']
    s.placements = [Placement(id='p1', workloadId='w4', day=0, period=1, roomId='r0'),
                    Placement(id='p2', workloadId='w5', day=1, period=1, roomId='r1')]
    s.courseRequests = [request('s1')]
    assert run(s)['rows'][0]['courseId'] == 'physics'
    s.rooms[0].capacity = 10
    s.rules = [TimetableRule(id='rule', name='Пн свободен', kind='availability', scope='student', targetIds=['s1'], unavailable=[dict(day=0, start='00:00', end='23:59')])]
    result = run(s)
    assert result['rows'][0]['courseId'] == 'physics' and result['attempts'] == 2


def test_minimum_section_size_and_partial_request_selection():
    s = fixture()
    s.courseRequests = [request('s1'), request('s2')]
    s.courses[1].sections[0].minSize = 1
    result = run(s, requestIds=['r-s1'])
    assert result['rows'][0]['courseId'] == 'physics'
    assert 's2' not in result['candidate'].groups[2].studentIds + result['candidate'].groups[3].studentIds
    assert run(s, requestIds=[])['status'] == 'blocked'


def test_partial_scope_cannot_overwrite_another_request_for_same_pupil():
    s = fixture()
    s.courseRequests = [request('s1'), CourseRequest(id='other', studentId='s1', courseIds=['physics'])]
    assert run(s, requestIds=['r-s1'])['status'] == 'blocked'


def test_archived_members_do_not_use_seats_but_remain_in_roster():
    s = fixture()
    s.groups[2].studentIds = ['s3']
    s.students[2].active = False
    s.courses[0].sections[0].maxSize = 1
    s.courseRequests = [request('s1', ('math',))]
    result = run(s)
    assert result['assigned'] == 1
    assert set(result['candidate'].groups[2].studentIds) == {'s1', 's3'}
    assert result['sections'][0]['count'] == 1


def test_overlap_uses_clock_not_matching_period_numbers():
    s = fixture()
    s.workloads[1].profileId = 'primary'
    s.placements = [Placement(id='p1', workloadId='w4', day=0, period=1, roomId='r0'),
                    Placement(id='p2', workloadId='w5', day=0, period=1, roomId='r1')]
    s.groups[3].studentIds = ['s1']
    s.courseRequests = [request('s1', ('math',))]
    assert run(s)['assigned'] == 1
    s.profiles[0].periods[1].start = '08:50'
    assert run(s)['status'] == 'infeasible'


def test_course_references_and_deleting_used_course_are_validated():
    s = fixture()
    s.courseRequests = [request('s1')]
    with pytest.raises(ValidationError):
        edit(s, EditRequest(revision=0, action='removeCourse', ids=['math']))
    s.courses[1].sections[0].groupId = 'g-math'
    with pytest.raises(ValidationError):
        State.model_validate(s.model_dump())


def test_explicit_complete_empty_section_is_known_but_unconfirmed_empty_is_not():
    s = fixture()
    g = s.groups[2]
    assert roster_complete(g)
    assert ResourceContext(s).audience_size(s.workloads[0]) == 0
    assert validate_schedule(s)['unknownGroups'] == 0
    g.rosterStatus = None
    assert not roster_complete(g)
    assert ResourceContext(s).audience_size(s.workloads[0]) is None


def test_api_preview_does_not_save_and_stale_result_cannot_apply(tmp_path, monkeypatch):
    monkeypatch.setattr(api, 'DB', tmp_path / 'courses.db')
    client = TestClient(api.app)
    client.get('/api/state?workspace=demo')
    s = fixture()
    s.courseRequests = [request('s1')]
    saved = client.put('/api/state?workspace=demo', json=s.model_dump(mode='json')).json()
    result = client.post('/api/courses/allocate?workspace=demo', json=dict(revision=saved['revision'], seconds=3)).json()
    assert result['status'] == 'success' and result['preview']['canApply']
    assert client.get('/api/state?workspace=demo').json() == saved
    client.put('/api/state?workspace=demo', json=saved)
    assert client.post('/api/editor/apply?workspace=demo', json=dict(revision=saved['revision'], token=result['preview']['token'])).status_code == 409
