import pytest
from pydantic import ValidationError
from backend.models import State, Placement, TimetableRule
from backend.scheduling import solve, validate_schedule
from backend.editing import EditRequest, edit, inspect_edit


def fixture(days=2):
    periods = [('08:00','08:40'), ('08:45','09:25'), ('09:30','10:10'), ('10:15','10:55'), ('11:00','11:40'), ('11:45','12:25')]
    return State.model_validate(dict(
        settings=dict(days=['Понедельник', 'Вторник'][:days]),
        profiles=[dict(id='p', name='Звонки', periods=[dict(label=str(i), start=a, end=b) for i,(a,b) in enumerate(periods)])],
        classes=[dict(id='c', name='Класс', profileId='p', studentCount=2)],
        students=[dict(id=f's{i}', name=f'Ученик {i}', classId='c') for i in [1,2]],
        groups=[dict(id='g', name='Класс', classIds=['c']), *[dict(id=f'g{i}', name=f'Группа {i}', classIds=['c'], kind='core', studentIds=[f's{i}']) for i in [1,2]]],
        subjects=[dict(id=f'sub{i}', name=f'Предмет {i}') for i in [1,2,3]],
        teachers=[dict(id=f't{i}', name=f'Учитель {i}') for i in [1,2,3]],
        rooms=[dict(id=f'r{i}', name=f'Кабинет {i}', capacity=20) for i in [1,2,3]],
        workloads=[dict(id=f'w{i}', groupId='g', teacherId=f't{i}', subjectId=f'sub{i}', hours=1, maxPerDay=6, profileId='p', roomIds=['r1','r2','r3']) for i in [1,2]],
    ))


def place(w, period, day=0, locked=False, id=None):
    return Placement(id=id or w, workloadId=w, day=day, period=period, roomId='r'+w[-1], locked=locked)


def relation(kind, **kw):
    return TimetableRule(id='rule', name='Связь', kind='relation', scope=kw.pop('scope', 'class'), subjectIds=['sub1'], otherSubjectIds=['sub2'], relation=kind, **kw)


def gaps(**kw):
    return TimetableRule(id='rule', name='Окна', kind='day_shape', scope=kw.pop('scope','class'), gapProfileId='p', **kw)


def generated(s, ids=None):
    result = solve(s, ids, seconds=3)
    assert result['status'] == 'success', result
    candidate = State.model_validate({**s.model_dump(), 'placements': result['placements']})
    assert validate_schedule(candidate)['issueCount'] == 0
    return candidate, result


def test_order_respects_each_pupil_and_fixed_outside_scope_lessons():
    s = fixture(1)
    s.rules = [relation('before')]
    s.placements = [place('w1',3), place('w2',2,locked=True)]
    assert len(validate_schedule(s)['issues']) == 2
    candidate, _ = generated(s, ['w1'])
    assert next(p for p in candidate.placements if p.workloadId == 'w1').period < 2
    assert next(p for p in candidate.placements if p.workloadId == 'w2') == s.placements[1]
    s.workloads[0].groupId, s.workloads[1].groupId = 'g1', 'g2'
    assert validate_schedule(s)['issueCount'] == 0  # Disjoint pupil choices.


def test_not_following_uses_clock_break_and_real_intermediate_lessons():
    s = fixture(1)
    s.rules = [relation('not_following', relationGapMinutes=5)]
    s.placements = [place('w1',0,locked=True), place('w2',1,locked=True)]
    assert len(validate_schedule(s)['issues']) == 2
    assert solve(s, seconds=3)['status'] == 'infeasible'
    s.placements[1].period = 2
    assert validate_schedule(s)['issueCount'] == 0
    s.rules[0].relationGapMinutes = 100
    s.workloads.append(s.workloads[0].model_copy(update=dict(id='w3', teacherId='t3', subjectId='sub3')))
    s.placements.append(place('w3',1,locked=True))
    generated(s)
    # Missing intermediate lesson cannot be used as a free exemption by CP-SAT.
    s.workloads = s.workloads[:2]
    s.placements = s.placements[:2]
    assert solve(s, seconds=3)['status'] == 'infeasible'


@pytest.mark.parametrize('kind,positions', [('same_day', [0,1]), ('different_days', [0,0])])
def test_day_relations_are_bidirectional_but_do_not_require_equal_hours(kind, positions):
    s = fixture()
    s.rules = [relation(kind)]
    s.placements = [place('w1',0,day=positions[0]), place('w2',2,day=positions[1])]
    assert validate_schedule(s)['issueCount'] > 0
    s.workloads[0].hours = 2
    candidate, _ = generated(s)
    a = {p.day for p in candidate.placements if p.workloadId == 'w1'}
    b = {p.day for p in candidate.placements if p.workloadId == 'w2'}
    assert a == b if kind == 'same_day' else a.isdisjoint(b)


def test_soft_relation_penalty_counts_pupils_not_room_alternatives_and_preview_allows_soft():
    s = fixture(1)
    s.rules = [relation('before', mode='soft', weight=7)]
    s.placements = [place('w1',2,locked=True), place('w2',0,locked=True)]
    _, result = generated(s)
    assert result['qualityPenalty'] == 14
    s.placements[0].locked = False
    changed = edit(s, EditRequest(action='move', revision=0, ids=['w1'], period=3))
    assert inspect_edit(s, changed)['canApply']
    assert inspect_edit(s, changed)['report']['qualityPenalty'] == 14


def test_empty_periods_and_blocks_exclude_breaks_edges_and_selected_periods():
    s = fixture(1)
    s.workloads[0].hours = 3
    s.workloads = s.workloads[:1]
    s.rules = [gaps(maxIdlePeriods=2, maxIdleBlocks=1)]
    s.placements = [place('w1',0,id='a'), place('w1',3,id='b'), place('w1',5,id='c')]
    issues = validate_schedule(s)['issues']
    assert len(issues) == 4  # 3 empty periods, 2 distinct windows, 2 pupils.
    s.rules[0].gapExcludedPeriods = [1,2]
    assert validate_schedule(s)['issueCount'] == 0
    s.rules = [gaps(maxIdlePeriods=0)]
    s.placements = [place('w1',1,id='a'), place('w1',2,id='b'), place('w1',3,id='c')]
    assert validate_schedule(s)['issueCount'] == 0  # Normal breaks and day edges aren't gaps.
    generated(s)


def test_gap_count_uses_day_specific_reference_bells_and_any_partial_overlap():
    s = fixture(1)
    s.workloads = s.workloads[:1]
    s.workloads[0].hours = 3
    # Reference period 2 is partially covered by actual period 3.
    profile = s.profiles[0].model_copy(deep=True)
    profile.id = 'reference'
    profile.dayPeriods['0'] = [p.model_copy(deep=True) for p in profile.periods]
    profile.dayPeriods['0'][2].end = '10:20'
    profile.dayPeriods['0'][3].start = '10:20'
    s.profiles.append(profile)
    s.rules = [gaps(maxIdlePeriods=0)]
    s.rules[0].gapProfileId = 'reference'
    s.placements = [place('w1',1,locked=True,id='a'), place('w1',3,locked=True,id='b'), place('w1',4,locked=True,id='c')]
    generated(s)


def test_weekly_gap_limit_sums_days_and_soft_score_matches_solver():
    s = fixture()
    s.workloads = s.workloads[:1]
    s.workloads[0].hours = 4
    s.placements = [place('w1',p,day=d,locked=True,id=f'{d}-{p}') for d in [0,1] for p in [0,2]]
    s.rules = [gaps(maxWeeklyIdlePeriods=1)]
    assert len(validate_schedule(s)['issues']) == 2
    assert solve(s, seconds=3)['status'] == 'infeasible'
    s.rules[0].mode = 'soft'
    s.rules[0].weight = 11
    _, result = generated(s)
    assert result['qualityPenalty'] == 22
    s.rules[0].days = [0]
    assert validate_schedule(s)['issueCount'] == 0
    assert validate_schedule(s)['qualityPenalty'] == 0


def test_gap_generator_fills_internal_periods_instead_of_counting_lunch_minutes():
    s = fixture(1)
    s.workloads = s.workloads[:1]
    s.workloads[0].hours = 3
    s.placements = [place('w1',0,locked=True,id='a'), place('w1',2,locked=True,id='c')]
    s.rules = [gaps(maxIdlePeriods=0)]
    candidate, _ = generated(s)
    assert sorted(p.period for p in candidate.placements) == [0,1,2]


def test_occupied_excluded_period_still_separates_two_windows():
    s = fixture(1)
    s.workloads = s.workloads[:1]
    s.workloads[0].hours = 3
    s.placements = [place('w1',p,locked=True,id=str(p)) for p in [0,2,4]]
    s.rules = [gaps(maxIdleBlocks=1, gapExcludedPeriods=[2])]
    assert len(validate_schedule(s)['issues']) == 2
    assert solve(s, seconds=3)['status'] == 'infeasible'
    s.rules[0].mode = 'soft'
    s.rules[0].weight = 3
    _, result = generated(s)
    assert result['qualityPenalty'] == 6


def test_rule_reference_validation_rejects_ambiguous_subjects_and_gap_profile_deletion():
    with pytest.raises(ValidationError):
        relation('before', scope='workload')
    with pytest.raises(ValidationError):
        TimetableRule(id='r', name='Окна', kind='day_shape', scope='teacher', maxIdlePeriods=0)
    s = fixture()
    s.rules = [gaps(maxIdlePeriods=0, gapExcludedPeriods=[11])]
    with pytest.raises(ValidationError):
        State.model_validate(s.model_dump())
    s.rules = [relation('before')]
    s.rules[0].otherSubjectIds = ['sub1']
    with pytest.raises(ValidationError):
        State.model_validate(s.model_dump())
