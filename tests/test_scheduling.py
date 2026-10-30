from copy import deepcopy
import pytest
from pydantic import ValidationError
from backend.seed import initial_state
from backend.models import State, Placement
from backend.scheduling import solve, validate_schedule, times, indexes, overlap


def demo():
    return initial_state('demo')


def generated():
    s = demo()
    result = solve(s, seconds=5)
    assert result['status'] == 'success', result
    s.placements = result['placements']
    return s


def test_generator_fulfills_hours_and_synchronizes_cross_class_groups():
    s = generated()
    assert validate_schedule(s)['issueCount'] == 0
    for w in s.workloads:
        assert sum(p.workloadId == w.id for p in s.placements) == w.hours
    # These groups contain different pupils from the same two classes.
    a = {(p.day, p.period) for p in s.placements if p.workloadId == 'w4'}
    b = {(p.day, p.period) for p in s.placements if p.workloadId == 'w5'}
    assert a == b
    assert len(a) == 5


def test_shared_student_in_synchronous_groups_is_impossible():
    s = demo()
    s.groups[-1].studentIds.append('s1')
    before = s.model_dump_json()
    assert solve(s, seconds=2)['status'] == 'infeasible'
    assert s.model_dump_json() == before


def test_unknown_group_membership_blocks_solver_and_incomplete_checks():
    s = demo()
    s.groups[-1].studentIds = []
    assert solve(s)['status'] == 'blocked'
    assert validate_schedule(s)['unknownGroups'] == 1
    assert not validate_schedule(s)['completeChecks']


def test_pinned_lessons_keep_exact_position_and_identity():
    s = generated()
    p = s.placements[0]
    p.locked = True
    before = p.model_dump()
    result = solve(s, seconds=3)
    assert result['status'] == 'success'
    assert next(x.model_dump() for x in result['placements'] if x.id == p.id) == before


def test_moved_generated_lessons_keep_identity_and_source_metadata():
    s = generated()
    p = next(p for p in s.placements if p.workloadId == 'w0')
    p.sourceCells = ['source-lesson']
    p.classIds = ['c-demo-a']
    teacher = next(t for t in s.teachers if t.id == s.workloads[0].teacherId)
    teacher.unavailable = [f'{p.day}:{p.period}']
    result = solve(s, seconds=3)
    assert result['status'] == 'success'
    moved = next(x for x in result['placements'] if x.id == p.id)
    assert (moved.day, moved.period) != (p.day, p.period)
    assert moved.sourceCells == ['source-lesson'] and moved.classIds == ['c-demo-a']
    assert {x.id for x in result['placements']} == {x.id for x in s.placements}


def test_impossible_availability_preserves_original_schedule():
    s = generated()
    s.teachers[0].unavailable = [f'{d}:{p}' for d in range(5) for p in range(11)]
    before = deepcopy(s.placements)
    result = solve(s, seconds=2)
    assert result['status'] in ['infeasible', 'blocked']
    assert s.placements == before


def test_no_teacher_double_booking_even_for_different_classes():
    s = demo()
    s.placements = [Placement(id='a', workloadId='w0', day=0, period=1, roomId='r0'), Placement(id='b', workloadId='w1', day=0, period=1, roomId='r1')]
    assert validate_schedule(s)['issueCount'] == 1
    assert 'учитель' in validate_schedule(s)['issues'][0]['message']


def test_bell_profiles_use_time_not_period_number():
    s = demo()
    s.workloads[0].profileId = 'primary'
    s.placements = [Placement(id='a', workloadId='w0', day=0, period=1, roomId='r0'), Placement(id='b', workloadId='w1', day=0, period=1, roomId='r0')]
    # Primary 09:00-09:40 and secondary 08:20-09:00 share a period number,
    # but merely touch in time and must not cause a teacher/room conflict.
    assert validate_schedule(s)['issueCount'] == 0
    s.profiles[0].periods[1].start = '08:50'
    assert validate_schedule(s)['issueCount'] == 1


def test_excess_weekly_load_is_blocked_before_search():
    s = demo()
    load = s.workloads[0]
    load.maxPerDay = 1
    load.hours = len(s.settings.days) * load.maxPerDay
    assert solve(s, ['w0'], seconds=3)['status'] != 'blocked'
    load.hours += 1
    assert solve(s, ['w0'], seconds=3)['status'] == 'blocked'


def test_missing_teacher_and_rooms_never_claim_valid_solution():
    s = demo()
    s.workloads[0].teacherId = None
    s.workloads[1].roomIds = []
    result = solve(s)
    assert result['status'] == 'blocked'
    assert len(result['details']) == 2


def test_partial_synchronous_block_rejected():
    s = demo()
    assert solve(s, ['w4'])['status'] == 'blocked'


def test_dangling_references_and_duplicate_ids_rejected():
    s = demo().model_dump(mode='json')
    s['teachers'] = []
    with pytest.raises(ValidationError):
        State.model_validate(s)
    s = demo().model_dump(mode='json')
    s['classes'].append(s['classes'][0])
    with pytest.raises(ValidationError):
        State.model_validate(s)


def test_invalid_student_class_is_rejected():
    s = demo().model_dump(mode='json')
    s['groups'][-1]['classIds'] = ['c-demo-a']
    with pytest.raises(ValidationError):
        State.model_validate(s)


def test_manual_edit_cannot_silently_break_synchronous_block():
    s = generated()
    p = next(p for p in s.placements if p.workloadId == 'w4')
    p.period = 8
    assert any(issue['type'] == 'block' for issue in validate_schedule(s)['issues'])


def test_scoped_generation_preserves_unrelated_existing_collisions():
    s = demo()
    # Existing imported clashes outside scope should be reported, but need not
    # prevent repair of a separate resource subset.
    s.placements = [Placement(id='a', workloadId='w0', day=0, period=1, roomId='r0'), Placement(id='b', workloadId='w1', day=0, period=1, roomId='r0')]
    result = solve(s, ['w2', 'w3'], seconds=3)
    assert result['status'] == 'success', result
    assert [p for p in result['placements'] if p.id in ['a', 'b']] == s.placements


def test_collision_details_identify_shared_student_by_id_not_name():
    s = demo()
    s.groups[-1].studentIds.append('s1')
    s.students[1].name = s.students[0].name
    s.placements = [Placement(id='math', workloadId='w4', day=1, period=3, roomId='r0'),
                    Placement(id='physics', workloadId='w5', day=1, period=3, roomId='r1')]
    issue = next(i for i in validate_schedule(s)['issues'] if i['type'] == 'collision')
    assert issue['ids'] == ['math', 'physics']
    assert issue['collision'] == dict(day=1, start='10:30', end='11:10', studentIds=['s1'],
                                     classIds=[], teacherIds=[], roomIds=[])


def test_collision_details_whole_class_and_mixed_group():
    s = demo()
    s.placements = [Placement(id='class', workloadId='w0', day=0, period=1, roomId='r0'),
                    Placement(id='mixed', workloadId='w4', day=0, period=1, roomId='r1')]
    issue = next(i for i in validate_schedule(s)['issues'] if i['type'] == 'collision')
    assert issue['collision']['studentIds'] == ['s1']
    assert issue['collision']['classIds'] == ['c-demo-a']


def test_collision_details_keep_class_evidence_without_pupil_records():
    s = demo()
    s.students = []
    for group in s.groups:
        group.studentIds = []
    s.placements = [Placement(id='english', workloadId='w0', day=0, period=1, roomId='r0'),
                    Placement(id='history', workloadId='w2', day=0, period=1, roomId='r1')]
    issue = next(i for i in validate_schedule(s)['issues'] if i['type'] == 'collision')
    assert issue['collision']['studentIds'] == []
    assert issue['collision']['classIds'] == ['c-demo-a']


def test_collision_details_time_intersection_and_shared_resources():
    s = demo()
    s.workloads[0].profileId = 'primary'
    s.profiles[0].periods[1].start = '08:50'
    s.placements = [Placement(id='a', workloadId='w0', day=0, period=1, roomId='r0'),
                    Placement(id='b', workloadId='w1', day=0, period=1, roomId='r0')]
    issue = validate_schedule(s)['issues'][0]
    assert issue['collision'] == dict(day=0, start='08:50', end='09:00', teacherIds=['t2'],
                                     roomIds=['r0'], studentIds=[], classIds=[])
