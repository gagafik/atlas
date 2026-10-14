import pytest
from pydantic import ValidationError
from backend.models import State, TimetableRule, Placement
from backend.seed import initial_state
from backend.scheduling import solve, validate_schedule
from backend.rules import pattern_valid


def fixture(hours=4):
    s = initial_state('demo')
    s.workloads = [s.workloads[0]]
    s.workloads[0].hours = hours
    s.workloads[0].maxPerDay = max(1, hours)
    return s


def rule(kind, scope='teacher', targets=None, **fields):
    return TimetableRule(id=fields.pop('id', 'r'), name=fields.pop('name', 'Правило'), kind=kind, scope=scope,
                         targetIds=targets if targets is not None else ['t2'] if scope == 'teacher' else [], **fields)


def generated(s):
    result = solve(s, seconds=3)
    assert result['status'] == 'success', result
    candidate = State.model_validate({**s.model_dump(), 'placements': result['placements']})
    assert validate_schedule(candidate)['issueCount'] == 0
    return candidate, result


@pytest.mark.parametrize('scope,target', [('teacher','t2'), ('class','c-demo-a'), ('student','s1'), ('group','g-a'), ('subject','sub2'), ('workload','w0')])
def test_resource_unavailability_uses_real_clock_for_every_scope(scope, target):
    s = fixture(2)
    s.rules = [rule('availability', scope, [target], unavailable=[{'day': 0, 'start': '00:00', 'end': '23:59'}])]
    s.placements = [Placement(id='p', workloadId='w0', day=0, period=1, roomId='r0')]
    assert any(i['type'] == 'rule' for i in validate_schedule(s)['issues'])
    candidate, _ = generated(s)
    assert all(p.day != 0 for p in candidate.placements)


def test_unavailable_room_can_be_replaced_by_another_room():
    s = fixture(2)
    s.rules = [rule('availability', 'room', ['r0'], unavailable=[{'day': d, 'start': '00:00', 'end': '23:59'} for d in range(5)])]
    candidate, _ = generated(s)
    assert all(p.roomId != 'r0' for p in candidate.placements)


def test_daily_limit_aggregates_loads_and_class_limit_follows_each_pupil():
    s = initial_state('demo')
    s.workloads = s.workloads[4:]
    for w in s.workloads:
        w.hours = 1
    s.rules = [rule('daily_load', 'class', ['c-demo-a'], maxLessons=1)]
    s.placements = [Placement(id='m', workloadId='w4', day=0, period=2, roomId='r0', locked=True),
                    Placement(id='p', workloadId='w5', day=0, period=2, roomId='r1', locked=True)]
    assert validate_schedule(s)['issueCount'] == 0
    candidate, _ = generated(s)
    assert len(candidate.placements) == 2  # Two electives, one lesson per actual pupil.
    extra = initial_state('demo').workloads[0]
    extra.hours = 1
    s.workloads.append(extra)
    s.placements.append(Placement(id='e', workloadId='w0', day=0, period=3, roomId='r0'))
    issues = [i for i in validate_schedule(s)['issues'] if i['type'] == 'rule']
    assert len(issues) == 2
    assert {tuple(i['affectedStudentIds']) for i in issues} == {('s1',), ('s2',)}


def test_subject_limit_is_per_pupil_not_total_across_school():
    s = initial_state('demo')
    s.workloads = s.workloads[:2]
    for w in s.workloads: w.hours = 1
    s.workloads[1].teacherId = 't3'
    s.rules = [rule('daily_load', 'subject', ['sub2'], maxLessons=1)]
    s.placements = [Placement(id='a', workloadId='w0', day=0, period=2, roomId='r0', locked=True),
                    Placement(id='b', workloadId='w1', day=0, period=2, roomId='r1', locked=True)]
    assert validate_schedule(s)['issueCount'] == 0
    generated(s)


def test_daily_minimum_active_days_and_all_days_differ():
    s = fixture(4)
    s.rules = [rule('daily_load', minLessons=2, maxLessons=2)]
    candidate, _ = generated(s)
    assert len({p.day for p in candidate.placements}) == 2
    s.rules[0].includeEmptyDays = True
    result = solve(s, seconds=2)
    assert result['status'] == 'infeasible'
    assert 'r' in result['conflictingRuleIds']


def test_soft_limits_report_penalty_but_do_not_block_and_weight_changes_choice():
    s = fixture(4)
    s.rules = [rule('distribution', minDays=5, mode='soft', weight=1000)]
    candidate, result = generated(s)
    report = validate_schedule(candidate)
    assert len({p.day for p in candidate.placements}) == 4
    assert report['issueCount'] == 0 and report['warningCount'] == 1
    assert report['qualityPenalty'] == result['qualityPenalty'] == 1000
    s.rules[0].enabled = False
    assert validate_schedule(s)['warningCount'] == 0


def test_day_shape_idle_start_end_and_consecutive_match_validator():
    s = fixture(3)
    s.rules = [rule('day_shape', earliestStart='09:00', latestEnd='14:30', maxIdleMinutes=15, maxConsecutive=2)]
    s.placements = [Placement(id='a', workloadId='w0', day=0, period=1, roomId='r0'),
                    Placement(id='b', workloadId='w0', day=0, period=4, roomId='r0'),
                    Placement(id='c', workloadId='w0', day=0, period=8, roomId='r0')]
    assert len([i for i in validate_schedule(s)['issues'] if i['type'] == 'rule']) == 3
    candidate, _ = generated(s)
    assert all(p.period not in [0, 1, 7, 8, 9, 10] for p in candidate.placements)


def test_continuous_lunch_break_not_sum_of_short_gaps_and_named_unsat_core():
    s = fixture(1)
    s.workloads[0].allowedPeriods = [2]
    s.placements = [Placement(id='p', workloadId='w0', day=0, period=2, roomId='r0', locked=True)]
    s.rules = [rule('day_shape', lunchStart='09:00', lunchEnd='10:30', lunchMinutes=60, name='Обед')]
    assert validate_schedule(s)['issueCount'] == 1
    result = solve(s, seconds=2)
    assert result['status'] == 'infeasible' and result['conflictingRuleIds'] == ['r']
    assert 'Обед' in result['details']
    s.rules[0].mode = 'soft'
    candidate, _ = generated(s)
    assert validate_schedule(candidate)['warningCount'] == 1
    s.rules[0].lunchMinutes = 30
    s.rules[0].mode = 'hard'
    generated(s)


def test_weekly_spread_with_distance_between_days():
    s = fixture(3)
    s.workloads[0].maxPerDay = 1
    s.rules = [rule('distribution', 'workload', ['w0'], minDays=3, maxDays=3, minDayDistance=2)]
    candidate, _ = generated(s)
    assert {p.day for p in candidate.placements} == {0, 2, 4}


def test_double_triple_sessions_keep_room_and_obey_maximum_break():
    s = fixture(2)
    w = s.workloads[0]
    w.lessonLengths = [2]
    w.allowedPeriods = [1, 2]  # Secondary has a 45-minute break here.
    assert solve(s, seconds=2)['status'] == 'infeasible'
    w.maxLessonBreak = 45
    candidate, _ = generated(s)
    assert len({(p.day, p.roomId) for p in candidate.placements}) == 1
    assert pattern_valid(candidate, w, candidate.placements)
    w.hours, w.maxPerDay, w.lessonLengths, w.allowedPeriods = 3, 3, [3], [2, 3, 4]
    s.placements = [Placement(id='middle', workloadId='w0', day=2, period=3, roomId='r1', locked=True)]
    candidate, _ = generated(s)
    assert {(p.day, p.period, p.roomId) for p in candidate.placements} == {(2, p, 'r1') for p in [2, 3, 4]}
    assert next(p for p in candidate.placements if p.id == 'middle').locked


def test_independent_pattern_validation_detects_broken_pair_and_room_change():
    s = fixture(3)
    w = s.workloads[0]
    w.lessonLengths = [2, 1]
    s.placements = [Placement(id=str(i), workloadId='w0', day=0, period=p, roomId='r0') for i,p in enumerate([2, 3, 5])]
    assert pattern_valid(s, w, s.placements)
    s.placements[1].roomId = 'r1'
    assert any(i['type'] == 'lesson_pattern' for i in validate_schedule(s)['issues'])
    s.placements.pop()
    assert pattern_valid(s, w, s.placements)  # Unplaced hours are handled separately.


def test_invalid_rule_references_windows_and_pattern_rejected():
    s = fixture()
    with pytest.raises(ValidationError): rule('availability', unavailable=[{'day': 0, 'start': '10:00', 'end': '09:00'}])
    with pytest.raises(ValidationError): rule('daily_load', minLessons=4, maxLessons=2)
    with pytest.raises(ValidationError): rule('day_shape', lunchStart='12:00', lunchEnd='12:20', lunchMinutes=30)
    s.rules = [rule('daily_load', targets=['absent'], maxLessons=4)]
    with pytest.raises(ValidationError): State.model_validate(s.model_dump())
    s.rules = []
    s.workloads[0].lessonLengths = [2]
    with pytest.raises(ValidationError): State.model_validate(s.model_dump())


def test_all_student_rules_exclude_archived_pupils_and_personal_availability_is_scoped():
    s = fixture(5)
    s.students[2].active = s.students[3].active = False
    s.rules = [rule('daily_load', 'student', [], minLessons=1, maxLessons=1, includeEmptyDays=True)]
    generated(s)
    s.rules = [rule('availability', 'student', ['s1'], unavailable=[{'day': 0, 'start': '08:00', 'end': '09:00'}])]
    s.placements = [Placement(id='p', workloadId='w0', day=0, period=1, roomId='r0')]
    assert validate_schedule(s)['issues'][0]['affectedStudentIds'] == ['s1']


def test_generation_cancels_while_building_rule_constraints():
    import threading
    event = threading.Event()
    s = fixture()
    s.rules = [rule('daily_load', maxLessons=1)]
    def progress(value):
        if value['phase'] == 'building_rules':
            event.set()
    assert solve(s, cancel=event, progress=progress)['status'] == 'cancelled'
