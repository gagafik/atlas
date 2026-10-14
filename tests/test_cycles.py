from datetime import date
import pytest
from pydantic import ValidationError
from backend.models import State, Cycles, Placement, TimetableRule, TeachingPeriod
from backend.seed import initial_state
from backend.cycles import week_state, pattern_on
from backend.scheduling import solve, validate_schedule
from backend.editing import EditRequest, edit, inspect_edit


def fixture():
    s = initial_state('demo')
    s.workloads = s.workloads[:1]
    s.workloads[0].hours = 1
    s.cycles = Cycles(patterns=[dict(id='weekly',name='Неделя A'), dict(id='b',name='Неделя B')])
    return s


def p(id, week, period=1, locked=False):
    return Placement(id=id, workloadId='w0', day=0, period=period, roomId='r0', cycleId=week, locked=locked)


def test_same_clock_in_different_weeks_never_collides_and_hours_are_per_week():
    s = fixture()
    s.placements = [p('a','weekly'), p('b','b')]
    report = validate_schedule(s)
    assert report['issueCount'] == 0 and report['placed'] == report['required'] == 2
    s.workloads[0].cycleHours = {'b': 2}
    assert validate_schedule(s)['required'] == 3
    assert week_state(s,'b').workloads[0].hours == 2
    assert s.workloads[0].hours == 1


def test_generate_all_patterns_preserves_identity_and_partial_week_selection():
    s = fixture()
    s.placements = [p('a','weekly',locked=True)]
    s.workloads[0].cycleHours = {'b': 2}
    result = solve(s, seconds=3)
    assert result['status'] == 'success', result
    candidate = State.model_validate({**s.model_dump(), 'placements': result['placements']})
    assert validate_schedule(candidate)['issueCount'] == 0
    assert len([p for p in candidate.placements if p.cycleId=='b']) == 2
    assert next(p for p in candidate.placements if p.cycleId=='weekly') == s.placements[0]
    before = [p for p in candidate.placements if p.cycleId=='b']
    result = solve(candidate, seconds=3, cycle_ids=['weekly'])
    assert result['status'] == 'success'
    assert [p for p in result['placements'] if p.cycleId=='b'] == before


def test_rules_scope_and_lesson_lengths_can_differ_by_week():
    s = fixture()
    s.workloads[0].cycleHours = {'b':2}
    s.workloads[0].cycleLessonLengths = {'b':[2]}
    s.rules = [TimetableRule(id='r',name='Пн свободен в B',kind='availability',scope='teacher',targetIds=['t2'],cycleIds=['b'],unavailable=[dict(day=0,start='00:00',end='23:59')])]
    s.placements = [p('a','weekly',locked=True),p('b','b')]
    issues = validate_schedule(s)['issues']
    assert len(issues) == 1 and issues[0]['cycleId']=='b'
    result = solve(s,seconds=3)
    assert result['status']=='success', result
    assert all(x.day!=0 for x in result['placements'] if x.cycleId=='b')
    # Failure of one week never exposes a partially generated schedule.
    s.placements[1].locked=True
    result=solve(s,seconds=3)
    assert result['status']=='infeasible' and 'placements' not in result


def test_inactive_workloads_and_workload_rules_do_not_leak_into_other_weeks():
    s=fixture()
    s.workloads[0].cycleIds=['weekly']
    s.rules=[TimetableRule(id='r',name='Минимум',kind='daily_load',scope='workload',targetIds=['w0'],minLessons=1,includeEmptyDays=True)]
    assert week_state(s,'b').workloads == [] and week_state(s,'b').rules == []
    assert validate_schedule(s,'b')['required']==0
    s.placements=[p('b','b')]
    with pytest.raises(ValidationError): State.model_validate(s.model_dump())


def test_copy_increases_only_target_week_hours_and_block_links_are_week_local():
    s=fixture()
    s.placements=[p('a','weekly'),p('b','b')]
    s.workloads[0].block='block'
    moved=edit(s,EditRequest(revision=0,action='move',ids=['a'],day=1))
    assert next(x for x in moved.placements if x.id=='b').day==0
    copied=edit(s,EditRequest(revision=0,action='copy',ids=['a'],cycleId='b',period=2,increaseHours=True))
    assert copied.workloads[0].hours==1 and copied.workloads[0].cycleHours=={'b':2}
    assert inspect_edit(s,copied)['canApply']


def test_copy_week_preview_preserves_source_and_requires_explicit_replacement():
    s=fixture();s.placements=[p('a','weekly',locked=True)]
    copied=edit(s,EditRequest(revision=0,action='copyCycle',sourceCycleId='weekly',cycleId='b'))
    assert copied.placements[0]==s.placements[0]
    assert copied.placements[1].cycleId=='b' and not copied.placements[1].locked
    assert inspect_edit(s,copied)['canApply']
    with pytest.raises(ValueError): edit(copied,EditRequest(revision=0,action='copyCycle',sourceCycleId='weekly',cycleId='b'))


def test_calendar_rotation_and_terms_use_monday_anchor_without_skipping_holidays():
    s=fixture()
    assert pattern_on(s,date(2026,8,17))=='weekly'
    assert pattern_on(s,date(2026,8,24))=='b'
    assert pattern_on(s,date(2026,8,31))=='weekly'
    s.cycles.periods=[TeachingPeriod(id='t',name='Семестр',start='2026-09-01',end='2026-12-20',anchor='2026-08-31',patternIds=['b','weekly'])]
    s=State.model_validate(s.model_dump())
    assert pattern_on(s,date(2026,8,31)) is None
    assert pattern_on(s,date(2026,9,1))=='b'
    assert pattern_on(s,date(2026,9,7))=='weekly'
    assert pattern_on(s,date(2026,12,21)) is None


def test_deleting_used_pattern_and_overlapping_terms_are_rejected():
    s=fixture();s.placements=[p('b','b')]
    with pytest.raises(ValueError): edit(s,EditRequest(revision=0,action='cycles',cycles=Cycles()))
    with pytest.raises(ValidationError): Cycles(periods=[dict(id=str(i),name='Период',start='2026-08-17',end='2026-12-01',anchor='2026-08-17',patternIds=['weekly']) for i in [1,2]])
    s.workloads[0].cycleHours={'b':2};s.workloads[0].cycleLessonLengths={'b':[3]}
    with pytest.raises(ValidationError): State.model_validate(s.model_dump())
