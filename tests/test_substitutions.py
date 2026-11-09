from datetime import date, datetime, timezone
import pytest
from fastapi.testclient import TestClient
from backend import api, timetable_history, workload_facts
from backend.models import State, Placement, Teacher, DatedLesson, TeacherAbsence, ScheduleDay, Event, Commitment, TimetableRule, SubstitutionWeights
from backend.models import TeachingPeriod, Cycles, Named
from backend.cycles import pattern_on
from backend.commitments import DutyRequest, allocate_duties
from backend.seed import initial_state
from backend.calendar_ops import day_report, SubstituteRequest, substitute_options
from backend.editing import inspect_edit
from backend.substitutions import monthly_totals
from test_access import client, enable, create, signed
from test_journal import lesson, entry, write
from test_journal_coverage import known

MON, TUE, SAT, SUN = date(2026, 8, 17), date(2026, 8, 18), date(2026, 8, 22), date(2026, 8, 23)


def fixture():
    s = initial_state('demo'); s.events = []
    s.placements = [Placement(id='eng', workloadId='w0', day=0, period=1, roomId='r0'),
                    Placement(id='math', workloadId='w4', day=0, period=2, roomId='r1')]
    return s


def ranked():
    s = initial_state('demo'); s.events = []
    s.teachers += [Teacher(id='t4', name='Второй учитель английского'), Teacher(id='t5', name='Педагог-организатор')]
    s.workloads[1].teacherId = 't4'
    s.placements = [Placement(id='eng', workloadId='w0', day=0, period=1, roomId='r0'),
                    Placement(id='eng2', workloadId='w0', day=0, period=3, roomId='r0'),
                    Placement(id='d0', workloadId='w1', day=0, period=0, roomId='r1'),
                    Placement(id='d2', workloadId='w1', day=0, period=2, roomId='r1'),
                    Placement(id='hist', workloadId='w3', day=0, period=1, roomId='r2')]
    s.absences = [TeacherAbsence(id='abs', name='Болезнь', teacherId='t2', start=MON, end=MON)]
    return State.model_validate(s.model_dump())


def change(placement='eng', day=MON, **values):
    return DatedLesson(id='c-' + placement + str(day), name='Замена', kind='change', placementId=placement, sourceDate=day, onDate=day, **values)


def request(day=MON, row='eng', teacher='t2'):
    return SubstituteRequest(revision=0, day=day, rowId=f'{day}:{row}', teacherId=teacher)


@pytest.fixture
def web(tmp_path, monkeypatch):
    monkeypatch.setattr(api, 'DB', tmp_path / 'substitutions.db')
    result = TestClient(api.app)
    result.get('/api/state?workspace=demo')
    return result


def stored(s):
    return api.save('demo', s)


# ---- S1: model --------------------------------------------------------------------------------

CHANGE = dict(id='c', name='Замена', placementId='eng', sourceDate=MON, onDate=MON)
ADDED = dict(id='x', name='Разовый', onDate=MON, groupId='g-a', profileId='secondary', subjectId='sub2', startTime='10:00', endTime='10:40')
VALID_CHANGES = {
    'plain-change': CHANGE | dict(kind='change'),
    'substitution': CHANGE | dict(kind='change', changeType='substitution', teacherIds=['t0']),
    'merge': CHANGE | dict(kind='change', changeType='merge', mergeWith=f'{MON}:hist'),
    'self-study': CHANGE | dict(kind='change', changeType='self_study', teacherIds=['t0']),
    'move-in-time': CHANGE | dict(kind='change', changeType='move', startTime='16:00', endTime='16:40'),
    'move-to-another-date': CHANGE | dict(kind='change', changeType='move', onDate=TUE),
    'cancel': CHANGE | dict(kind='cancel', changeType='cancel'),
    'extra-lesson': ADDED | dict(kind='add', changeType='extra'),
}
INVALID_CHANGES = {
    'cancel-typed-as-substitution': CHANGE | dict(kind='cancel', changeType='substitution'),
    'change-typed-as-cancel': CHANGE | dict(kind='change', changeType='cancel'),
    'change-typed-as-extra': CHANGE | dict(kind='change', changeType='extra'),
    'merge-without-a-host': CHANGE | dict(kind='change', changeType='merge'),
    'substitution-with-a-host': CHANGE | dict(kind='change', changeType='substitution', mergeWith='x'),
    'untyped-change-with-a-host': CHANGE | dict(kind='change', mergeWith='x'),
    'self-study-without-a-teacher': CHANGE | dict(kind='change', changeType='self_study'),
    'self-study-with-an-empty-teacher-list': CHANGE | dict(kind='change', changeType='self_study', teacherIds=[]),
    'move-that-moves-nothing': CHANGE | dict(kind='change', changeType='move'),
    'extra-lesson-typed-as-merge': ADDED | dict(kind='add', changeType='merge', mergeWith='a'),
}


@pytest.mark.parametrize('values', VALID_CHANGES.values(), ids=VALID_CHANGES)
def test_valid_change_type_combinations_are_created(values):
    assert DatedLesson(**values).id == values['id']


@pytest.mark.parametrize('values', INVALID_CHANGES.values(), ids=INVALID_CHANGES)
def test_invalid_change_type_combinations_are_rejected(values):
    with pytest.raises(ValueError): DatedLesson(**values)


def test_old_documents_load_with_defaults():
    s = initial_state('demo')
    dump = s.model_dump()
    assert dump['scheduleDays'] == [] and dump['settings']['substitutionWeights'] == SubstitutionWeights().model_dump()
    del dump['scheduleDays']; del dump['settings']['substitutionWeights']
    assert State.model_validate(dump) == s


SATURDAY = ScheduleDay(id='a', name='Отработка', onDate=SAT, day=0)


def schedule_state(*days, **update):
    value = fixture().model_copy(update=dict(scheduleDays=list(days)) | update)
    return State.model_validate(value.model_dump())


def later_periods():
    return dict(cycles=fixture().cycles.model_copy(update=dict(periods=[TeachingPeriod(id='p', name='Период', start=date(2026, 8, 24), end=date(2027, 6, 11), anchor=date(2026, 8, 24), patternIds=['weekly'])])))


INVALID_SCHEDULE_DAYS = {
    'two-days-for-one-date': lambda: schedule_state(SATURDAY, ScheduleDay(id='b', name='Ещё', onDate=SAT, day=1)),
    'weekday-outside-the-school-week': lambda: schedule_state(ScheduleDay(id='a', name='Д', onDate=TUE, day=5)),
    'date-outside-the-school-year': lambda: schedule_state(ScheduleDay(id='a', name='Д', onDate=date(2026, 8, 15), day=0)),
    'unknown-class': lambda: schedule_state(ScheduleDay(id='a', name='Д', onDate=SAT, day=0, classIds=['missing'])),
    'unknown-week-pattern': lambda: schedule_state(ScheduleDay(id='a', name='Д', onDate=SAT, day=0, cycleId='missing')),
    'date-that-already-runs-this-weekday': lambda: ScheduleDay(id='a', name='Д', onDate=MON, day=0),
    'repeated-class': lambda: ScheduleDay(id='a', name='Д', onDate=SAT, day=0, classIds=['c-demo-a', 'c-demo-a']),
    'date-outside-the-teaching-periods': lambda: schedule_state(SATURDAY, **later_periods()),
}


@pytest.mark.parametrize('build', INVALID_SCHEDULE_DAYS.values(), ids=INVALID_SCHEDULE_DAYS)
def test_invalid_schedule_days_are_rejected(build):
    with pytest.raises(ValueError): build()


def test_valid_schedule_days_are_accepted():
    assert schedule_state(SATURDAY).scheduleDays == [SATURDAY]
    assert schedule_state(ScheduleDay(id='a', name='Д', onDate=SAT, day=0, cycleId='weekly'), **later_periods()).scheduleDays[0].cycleId == 'weekly'
    assert ScheduleDay(id='a', name='Д', onDate=MON, day=0, cycleId='weekly').cycleId == 'weekly'


def test_schedule_day_notifies_personal_accounts(client):
    enable(client); create(client, 'teacher')
    teacher = signed('teacher')
    before = teacher.get('/api/notifications/inbox').json()['unread']
    state = api.read('demo')
    state.scheduleDays = [ScheduleDay(id='sat', name='Отработка', onDate=SAT, day=0)]
    api.save('demo', state)
    assert teacher.get('/api/notifications/inbox').json()['unread'] == before + 1


# ---- S2: the date runs another weekday ---------------------------------------------------------

def test_saturday_runs_monday_template():
    s = fixture()
    s.scheduleDays = [ScheduleDay(id='sat', name='Отработка', onDate=SAT, day=0)]
    s = State.model_validate(s.model_dump()); snapshot = s.model_dump()
    report = day_report(s, SAT)
    assert {r['id'] for r in report['rows']} == {f'{SAT}:eng', f'{SAT}:math'}
    monday = {r['placementId']: (r['start'], r['end']) for r in day_report(s, MON)['rows']}
    assert {r['placementId']: (r['start'], r['end']) for r in report['rows']} == monday
    assert report['scheduleDay']['day'] == 0 and day_report(s, MON)['scheduleDay'] is None
    assert day_report(s, SUN)['rows'] == [] and day_report(s, date(2026, 8, 29))['rows'] == []
    assert [r['id'] for r in day_report(s, MON)['rows']] == [f'{MON}:eng', f'{MON}:math']
    assert s.model_dump() == snapshot


def test_class_selective_override():
    s = fixture()
    s.placements = [Placement(id='A0', workloadId='w0', day=0, period=1, roomId='r0'),
                    Placement(id='A1', workloadId='w2', day=1, period=3, roomId='r2'),
                    Placement(id='D1', workloadId='w1', day=1, period=1, roomId='r1')]
    s.scheduleDays = [ScheduleDay(id='tue', name='Класс А по понедельнику', onDate=TUE, day=0, classIds=['c-demo-a'])]
    s = State.model_validate(s.model_dump())
    report = day_report(s, TUE)
    assert {r['placementId'] for r in report['rows']} == {'A0', 'D1'}
    assert not any(i['type'] == 'dated_day_split' for i in report['issues'])
    s.placements.append(Placement(id='M0', workloadId='w4', day=0, period=2, roomId='r1'))
    split = [i for i in day_report(s, TUE)['issues'] if i['type'] == 'dated_day_split']
    assert [i['ids'] for i in split] == [[f'{TUE}:M0']]
    blocked = inspect_edit(s.model_copy(update=dict(placements=s.placements[:3])), s)
    assert not blocked['canApply'] and any(i['type'] == 'dated_day_split' for i in blocked['newIssues'])


def test_change_on_overridden_date_and_orphan_on_removal():
    s = fixture()
    s.scheduleDays = [ScheduleDay(id='sat', name='Отработка', onDate=SAT, day=0)]
    s.datedLessons = [DatedLesson(id='c', name='Замена', kind='change', placementId='eng', sourceDate=SAT, onDate=SAT, teacherIds=['t0'], changeType='substitution')]
    s = State.model_validate(s.model_dump())
    report = day_report(s, SAT)
    row = next(r for r in report['rows'] if r['placementId'] == 'eng')
    assert not any(i['type'] == 'dated_orphan' for i in report['issues'])
    assert row['changeType'] == 'substitution' and row['teacherIds'] == ['t0']
    removal = s.model_copy(update=dict(scheduleDays=[]))
    preview = inspect_edit(s, removal)
    assert not preview['canApply'] and any(i['type'] == 'dated_orphan' for i in preview['newIssues'])


def test_override_uses_monday_teacher_unavailability_and_bookings():
    s = fixture()
    s.scheduleDays = [ScheduleDay(id='sat', name='Отработка', onDate=SAT, day=0)]
    s = State.model_validate(s.model_dump())
    assert day_report(s, SAT)['issueCount'] == 0
    next(t for t in s.teachers if t.id == 't2').unavailable = ['0:1']
    assert any(i['type'] == 'dated_availability' for i in day_report(s, SAT)['issues'])
    next(t for t in s.teachers if t.id == 't2').unavailable = []
    row = next(r for r in day_report(s, SAT)['rows'] if r['placementId'] == 'eng')
    booking = Commitment(id='book', name='Бронь', kind='booking', onDate=SAT, startTime=row['start'], endTime=row['end'], roomIds=['r0'])
    s.commitments = [booking]
    assert any(i['type'] == 'dated_collision' for i in day_report(s, SAT)['issues'])
    s.commitments = [booking.model_copy(update=dict(onDate=date(2026, 8, 29)))]
    assert not any(i['type'] == 'dated_collision' for i in day_report(s, date(2026, 8, 29))['issues'])


def test_override_date_uses_the_chosen_weekday_for_lesson_rules():
    s = fixture()
    s.scheduleDays = [ScheduleDay(id='sat', name='Отработка', onDate=SAT, day=0)]
    s.rules = [TimetableRule(id='r', name='Учитель занят', kind='availability', scope='teacher', targetIds=['t2'], unavailable=[dict(day=0, start='08:00', end='18:00')])]
    s = State.model_validate(s.model_dump())
    assert any(i['type'] == 'dated_rule' for i in day_report(s, SAT)['issues'])
    assert not any(i['type'] == 'dated_rule' for i in day_report(s, date(2026, 8, 29))['issues'])
    s.rules[0].days = [1]
    assert not any(i['type'] == 'dated_rule' for i in day_report(s, SAT)['issues'])
    selective = s.model_copy(update=dict(scheduleDays=[ScheduleDay(id='sat', name='Класс А', onDate=SAT, day=0, classIds=['c-demo-a'])], rules=[]))
    selective = State.model_validate(selective.model_dump())
    next(t for t in selective.teachers if t.id == 't2').unavailable = ['0:1']
    assert any(i['type'] == 'dated_availability' for i in day_report(selective, SAT)['issues'])


def later(t): return f'{int(t[:2]) + 1:02d}{t[2:]}'


def monday_bells_an_hour_later(s):
    """The «secondary» profile rings an hour later on Mondays, so a wrong bell source shows."""
    profile = next(p for p in s.profiles if p.id == 'secondary')
    profile.dayPeriods = {'0': [p.model_copy(update=dict(start=later(p.start), end=later(p.end))) for p in profile.periods]}
    return State.model_validate(s.model_dump())


def test_override_date_takes_the_bells_of_the_chosen_weekday():
    s = fixture()
    s.scheduleDays = [ScheduleDay(id='sat', name='Отработка', onDate=SAT, day=0)]
    s = monday_bells_an_hour_later(s)
    profile = next(p for p in s.profiles if p.id == 'secondary')
    monday = profile.dayPeriods['0']
    expected = {'eng': (monday[1].start, monday[1].end), 'math': (monday[2].start, monday[2].end)}
    assert expected['eng'] != (profile.periods[1].start, profile.periods[1].end)
    times = lambda day: {r['placementId']: (r['start'], r['end']) for r in day_report(s, day)['rows']}
    assert times(MON) == expected and times(SAT) == expected


def ab_state(**update):
    """Weeks A and B; Saturday 22 August is in an A week and runs a Monday of week B."""
    s = initial_state('demo'); s.events = []
    s.cycles = Cycles(patterns=[Named(id='A', name='Неделя A'), Named(id='B', name='Неделя B')])
    s.placements = [Placement(id='engA', workloadId='w0', day=0, period=1, roomId='r0', cycleId='A'),
                    Placement(id='mathB', workloadId='w4', day=0, period=2, roomId='r1', cycleId='B')]
    s.scheduleDays = [ScheduleDay(id='sat', name='Отработка', onDate=SAT, day=0, cycleId='B')]
    return State.model_validate(s.model_copy(update=update).model_dump())


def test_override_runs_another_week_pattern():
    s = ab_state()
    report = day_report(s, SAT)
    assert [r['id'] for r in report['rows']] == [f'{SAT}:mathB'] and report['cycleId'] == 'B'
    assert report['scheduleDay']['cycleId'] == 'B' and pattern_on(s, SAT) == 'A'
    own = day_report(s.model_copy(update=dict(scheduleDays=[])), SAT)
    assert own['rows'] == [] and own['cycleId'] == 'A'
    assert [r['id'] for r in day_report(s, MON)['rows']] == [f'{MON}:engA']
    same_week = s.model_copy(update=dict(scheduleDays=[ScheduleDay(id='sat', name='Отработка', onDate=SAT, day=0)]))
    assert [r['id'] for r in day_report(same_week, SAT)['rows']] == [f'{SAT}:engA']


def test_class_selective_override_splits_groups_of_the_chosen_week():
    s = ab_state(scheduleDays=[ScheduleDay(id='sat', name='Класс А', onDate=SAT, day=0, cycleId='B', classIds=['c-demo-a'])])
    report = day_report(s, SAT)
    assert [i['ids'] for i in report['issues'] if i['type'] == 'dated_day_split'] == [[f'{SAT}:mathB']]
    assert {r['placementId'] for r in report['rows']} == {'mathB'}


def test_override_applies_rules_of_the_chosen_week_and_weekday():
    rule = TimetableRule(id='busy', name='Математик занят', kind='availability', scope='teacher', targetIds=['t0'], days=[0], cycleIds=['B'], unavailable=[dict(day=0, start='08:00', end='18:00')])
    broken = lambda s: [i['ids'] for i in day_report(s, SAT)['issues'] if i['type'] == 'dated_rule']
    assert broken(ab_state(rules=[rule])) == [[f'{SAT}:mathB']]
    assert broken(ab_state(rules=[rule.model_copy(update=dict(cycleIds=['A']))])) == []
    assert broken(ab_state(rules=[rule.model_copy(update=dict(days=[1]))])) == []


def test_override_template_explains_an_emptied_day():
    rule = TimetableRule(id='min', name='Минимум', kind='daily_load', scope='teacher', targetIds=['t0'], days=[0], cycleIds=['B'], minLessons=1, includeEmptyDays=True)
    cancel = DatedLesson(id='x', name='Отмена', kind='cancel', placementId='mathB', sourceDate=SAT, onDate=SAT)
    away = TeacherAbsence(id='away', name='Врач', teacherId='t0', start=SAT, end=SAT, startTime='08:00', endTime='23:00')
    rules = lambda s: [i['type'] for i in day_report(s, SAT)['issues'] if i['type'] == 'dated_rule']
    assert rules(ab_state(rules=[rule], datedLessons=[cancel])) == ['dated_rule']
    # The chosen day's lesson fell inside the absence, so the empty day is explained.
    assert rules(ab_state(rules=[rule], datedLessons=[cancel], absences=[away])) == []


def test_override_applies_to_bookings_and_occupied_lunch():
    closed = TimetableRule(id='closed', name='Кабинет закрыт', kind='availability', scope='room', targetIds=['r2'], days=[0], cycleIds=['B'], unavailable=[dict(day=0, start='08:00', end='18:00')])
    lunch = TimetableRule(id='lunch', name='Обед', kind='day_shape', scope='teacher', targetIds=['t2'], days=[0], cycleIds=['B'], lunchStart='12:00', lunchEnd='13:00', lunchMinutes=30)
    booking = Commitment(id='book', name='Бронь', kind='booking', onDate=SAT, startTime='16:00', endTime='17:00', roomIds=['r2'])
    duty = Commitment(id='duty', name='Дежурство', kind='duty', onDate=SAT, startTime='12:00', endTime='13:00', location='Коридор', requiredTeachers=1, teacherIds=['t2'], profileId='secondary')
    issues = day_report(ab_state(rules=[closed, lunch], commitments=[booking, duty]), SAT)['issues']
    assert [i['ids'] for i in issues if i['type'] == 'dated_rule'] == [['commitment:book']]
    assert [i['ids'] for i in issues if i['type'] == 'dated_lunch'] == [['commitment:duty']]
    # Saturday 29 August is a plain Saturday: rules written for Mondays of week B do not reach it.
    next_week = date(2026, 8, 29)
    plain = ab_state(rules=[closed, lunch], scheduleDays=[], commitments=[c.model_copy(update=dict(onDate=next_week)) for c in (booking, duty)])
    assert day_report(plain, next_week)['issues'] == []


def test_override_applies_to_duty_allocation():
    def duty(id, start, end, day=SAT):
        return Commitment(id=id, name='Дежурство', kind='duty', onDate=day, startTime=start, endTime=end, location='Коридор', requiredTeachers=1, profileId='secondary', eligibleTeacherIds=['t1'])
    def run(s, *ids): return allocate_duties(s, DutyRequest(revision=0, commitmentIds=list(ids), seconds=1))
    next_week = date(2026, 8, 29)
    # Lesson-number bans and bells come from the Monday the date runs as.
    s = ab_state(commitments=[duty('a', '09:30', '09:50')])
    next(t for t in s.teachers if t.id == 't1').unavailable = ['0:1']
    s = monday_bells_an_hour_later(s)
    assert run(s, 'a')['status'] == 'infeasible'
    assert run(s.model_copy(update=dict(commitments=[duty('a', '09:30', '09:50', next_week)])), 'a')['status'] == 'success'
    # Hard rules follow the week and weekday of the date.
    hard = TimetableRule(id='hard', name='Занят', kind='availability', scope='teacher', targetIds=['t1'], days=[0], cycleIds=['B'], unavailable=[dict(day=0, start='09:00', end='10:00')])
    assert run(ab_state(rules=[hard], commitments=[duty('a', '09:30', '09:50')]), 'a')['status'] == 'infeasible'
    assert run(ab_state(rules=[hard], commitments=[duty('a', '09:30', '09:50', next_week)]), 'a')['status'] == 'success'


def merge_state(capacity=25):
    s = fixture()
    s.placements = [Placement(id='eng', workloadId='w0', day=0, period=1, roomId='r0'), Placement(id='hist', workloadId='w3', day=0, period=1, roomId='r2')]
    for c in s.classes: c.studentCount = 10
    s.rooms[2].capacity = capacity
    s.datedLessons = [change('eng', MON, teacherIds=['t3'], roomIds=['r2'], changeType='merge', mergeWith=f'{MON}:hist')]
    return State.model_validate(s.model_dump())


def test_merge_rows_skip_teacher_room_collision_and_join_capacity():
    s = merge_state(25)
    assert day_report(s, MON)['issueCount'] == 0
    row = next(r for r in day_report(s, MON)['rows'] if r['placementId'] == 'eng')
    assert row['changeType'] == 'merge' and row['mergeWith'] == f'{MON}:hist'
    tight = merge_state(15)
    capacity = [i for i in day_report(tight, MON)['issues'] if i['type'] == 'dated_capacity']
    assert [i['ids'] for i in capacity] == [[f'{MON}:hist']]
    missing = merge_state(25); missing.datedLessons[0].mergeWith = 'missing'
    assert [i['type'] for i in day_report(missing, MON)['issues'] if i['type'] == 'dated_merge']
    stale = merge_state(25)
    stale.datedLessons.append(change('hist', MON, teacherIds=['t0']))
    types = [i['type'] for i in day_report(stale, MON)['issues']]
    assert 'dated_merge' in types
    assert 'dated_collision' not in [i['type'] for i in day_report(merge_state(25), MON)['issues']]


def test_a_stale_merge_blocks_only_the_edit_that_creates_it():
    valid = merge_state(25)
    stale = merge_state(25); stale.datedLessons.append(change('hist', MON, teacherIds=['t0']))
    stale = State.model_validate(stale.model_dump())
    created = inspect_edit(valid, stale)
    assert not created['canApply'] and any(i['type'] == 'dated_merge' for i in created['newIssues'])
    unrelated = State.model_validate(stale.model_copy(update=dict(commitments=[Commitment(id='book', name='Бронь', kind='booking', onDate=MON, startTime='16:00', endTime='17:00', roomIds=['r1'])])).model_dump())
    later = inspect_edit(stale, unrelated)
    assert later['canApply'] and later['newIssues'] == []
    # The notice stays in the day's report until the merge is recreated.
    assert any(i['type'] == 'dated_merge' for i in day_report(unrelated, MON)['issues'])


def crowded(capacity, size=5):
    """A third group (10C) next to eng merged into hist."""
    s = merge_state(capacity)
    s.classes.append(s.classes[1].model_copy(update=dict(id='c3', name='10C', studentCount=size)))
    s.groups.append(s.groups[1].model_copy(update=dict(id='g-c', name='10C', classIds=['c3'])))
    s.workloads.append(s.workloads[1].model_copy(update=dict(id='wc', groupId='g-c', teacherId='t2')))
    s.placements.append(Placement(id='third', workloadId='wc', day=0, period=1, roomId='r1'))
    return s


def test_merge_options_count_groups_already_in_the_host():
    def hist_option(s, row='third', teacher='t2'):
        found = substitute_options(s, SubstituteRequest(revision=0, day=MON, rowId=f'{MON}:{row}', teacherId=teacher))['mergeOptions']
        return next(o for o in found if o['rowId'] == f'{MON}:hist')
    joining = State.model_validate(crowded(25, 10).model_dump())
    option = hist_option(joining)
    # hist (10) already hosts eng (10): a third group of 10 makes 30 places in a room of 25.
    assert (option['capacity'], option['attendance'], option['fits']) == (25, 30, False)
    merged = joining.model_copy(update=dict(datedLessons=[*joining.datedLessons, change('third', MON, teacherIds=['t3'], roomIds=['r2'], changeType='merge', mergeWith=f'{MON}:hist')]))
    assert [i['ids'] for i in day_report(merged, MON)['issues'] if i['type'] == 'dated_capacity'] == [[f'{MON}:hist']]
    roomy = State.model_validate(crowded(30, 10).model_dump())
    assert (hist_option(roomy)['attendance'], hist_option(roomy)['fits']) == (30, True)
    roomy_merged = roomy.model_copy(update=dict(datedLessons=merged.datedLessons))
    assert not [i for i in day_report(roomy_merged, MON)['issues'] if i['type'] == 'dated_capacity']
    # eng is already one of the groups in hist: editing it does not count it twice.
    assert (hist_option(joining, 'eng', 't3')['attendance'], hist_option(joining, 'eng', 't3')['fits']) == (20, True)


def test_two_groups_may_join_the_same_host():
    s = crowded(40)
    s.datedLessons.append(change('third', MON, teacherIds=['t3'], roomIds=['r2'], changeType='merge', mergeWith=f'{MON}:hist'))
    s = State.model_validate(s.model_dump())
    report = day_report(s, MON)
    assert not [i for i in report['issues'] if i['type'] in ['dated_collision', 'dated_merge']]
    assert not [i for i in report['issues'] if i['type'] == 'dated_capacity']
    s.rooms[2].capacity = 24
    assert [i['ids'] for i in day_report(s, MON)['issues'] if i['type'] == 'dated_capacity'] == [[f'{MON}:hist']]


def test_legacy_change_type_is_derived():
    s = fixture()
    assert day_report(s, MON)['rows'][0]['changeType'] is None
    moved = change(teacherIds=['t0']).model_copy(update={'onDate': TUE})
    s.datedLessons = [moved]
    source = next(r for r in day_report(s, MON)['rows'])
    target = next(r for r in day_report(s, TUE)['rows'] if r['changeId'])
    assert source['status'] == 'cancelled' and source['movedTo'] == str(TUE) and source['changeType'] == 'move'
    assert target['changeType'] == 'move' and target['movedTo'] is None
    s.datedLessons = [change(teacherIds=['t0'])]
    assert day_report(s, MON)['rows'][0]['changeType'] == 'substitution'
    s.datedLessons = [DatedLesson(id='k', name='Отмена', kind='cancel', placementId='eng', sourceDate=MON, onDate=MON)]
    assert day_report(s, MON)['rows'][0]['changeType'] == 'cancel'
    s.datedLessons = [DatedLesson(id='x', name='Разовый', kind='add', onDate=SAT, groupId='g-a', profileId='secondary', subjectId='sub2', startTime='10:00', endTime='10:40', teacherIds=['t2'], roomIds=['r0'])]
    assert day_report(s, SAT)['rows'][0]['changeType'] == 'extra'


def test_override_reaches_journal_coverage_portal_and_publication(client):
    from backend.portal_cache import Snapshot
    from backend.publications import public_document, PublicationRequest
    state = api.read('demo')
    state.scheduleDays = [ScheduleDay(id='sat', name='Отработка', onDate=SAT, day=0)]
    api.save('demo', state)
    enable(client)
    found = lesson(client, day=str(SAT))
    record = entry(client, found['lessonKey'], day=str(SAT))
    assert write(client, record, publish=True).status_code == 200
    assert client.get('/api/journal/day?workspace=demo&day=2026-08-23').json()['lessons'] == []
    known()
    coverage = client.post('/api/journal/coverage/report?workspace=demo', json=dict(start=str(SAT), end=str(SAT), status='all')).json()
    assert coverage['counts']['expected'] == 2
    state = api.read('demo')
    assert Snapshot(state).day_rows(SAT, 'teacher', 't2')
    document, _ = public_document(state, PublicationRequest(revision=state.revision, name='Суббота', kind='dated', view='teacher', entityIds=['t2'], firstDate=SAT, lastDate=SAT))
    assert 'Английский язык' in document


# ---- S3: ranking -------------------------------------------------------------------------------

def options(s, **update):
    return substitute_options(s, request())


def test_ranking_order_and_explanations():
    s = ranked()
    s.settings.substitutionWeights = SubstitutionWeights(subject=40, group=30, presence=20, fairness=20, building=10)
    before = s.model_dump()
    result = options(s)
    available = [c for c in result['candidates'] if c['available']]
    assert [c['id'] for c in available] == ['t4', 't0', 't1', 't5'] and [c['score'] for c in available] == [80, 35, 35, 20]
    assert {c['id'] for c in result['candidates'] if not c['available']} == {'t2', 't3'}
    # Free teachers come first whatever the points: the absent subject teacher scores above the best substitute.
    assert [c['id'] for c in result['candidates']] == ['t4', 't0', 't1', 't5', 't2', 't3']
    scores = {c['id']: c['score'] for c in result['candidates']}
    assert scores['t2'] > scores['t4'] and scores['t3'] > scores['t0']
    points = lambda c: {f['key']: f['points'] for f in c['factors']}
    t4, t0 = points(available[0]), points(available[1])
    assert (t4['subject'], t4['group'], t4['presence'], t4['fairness'], t4['building']) == (40, 0, 20, 20, 0)
    assert t0['group'] == 15
    assert s.model_dump() == before


def test_fairness_and_weights_change_order():
    s = ranked()
    s.datedLessons = [change('eng', date(2026, 8, 24), teacherIds=['t4'], changeType='substitution')]
    s.settings.substitutionWeights = SubstitutionWeights(subject=40, group=30, presence=20, fairness=100, building=10)
    order = [c['id'] for c in options(s)['candidates'] if c['available']]
    assert order[:3] == ['t0', 't1', 't5'] and order[3] == 't4'
    assert {c['id']: c['score'] for c in options(s)['candidates']}['t0'] == 115
    s.settings.substitutionWeights = SubstitutionWeights(subject=0, group=100, presence=0, fairness=0, building=0)
    order = [c['id'] for c in options(s)['candidates'] if c['available']]
    assert order[:2] == ['t0', 't1'] and order.index('t5') < order.index('t4')
    s.settings.substitutionWeights = SubstitutionWeights(subject=0, group=0, presence=0, fairness=0, building=0)
    zero = [c for c in options(s)['candidates'] if c['available']]
    assert {c['score'] for c in zero} == {0} and [c['lessons'] for c in zero] == sorted(c['lessons'] for c in zero)


def test_building_factor():
    s = ranked()
    s.settings.substitutionWeights = SubstitutionWeights(subject=40, group=30, presence=20, fairness=20, building=10)
    s.campus = s.campus.model_validate(dict(buildings=[dict(id='a', name='A'), dict(id='b', name='B')], defaultTravelMinutes=0))
    s.rooms[0].buildingId = 'a'; s.rooms[1].buildingId = 'b'
    s = State.model_validate(s.model_dump())
    building = lambda: next(f for c in options(s)['candidates'] if c['id'] == 't4' for f in c['factors'] if f['key'] == 'building')['points']
    other_building = building()
    s.rooms[1].buildingId = 'a'
    assert building() > other_building


def test_merge_options_listed():
    s = ranked()
    result = options(s)
    hist = next(o for o in result['mergeOptions'] if o['rowId'] == f'{MON}:hist')
    assert hist['teacherIds'] == ['t3'] and hist['roomIds'] == ['r2'] and hist['fits'] is None
    assert all(o['rowId'] != f'{MON}:eng' and o['rowId'] != f'{MON}:eng2' for o in result['mergeOptions'])


def test_excluded_change_does_not_count_against_its_own_substitute():
    s = ranked()
    s.datedLessons = [change('eng', MON, teacherIds=['t4'], changeType='substitution')]
    s = State.model_validate(s.model_dump())
    row = f'{MON}:eng'
    t4 = next(c for c in substitute_options(s, SubstituteRequest(revision=0, day=MON, rowId=row, teacherId='t4'))['candidates'] if c['id'] == 't4')
    assert t4['substitutions'] == 0


def test_fairness_counts_takeovers_of_the_month_only():
    aug24, sep7 = date(2026, 8, 24), date(2026, 9, 7)
    s = ranked()
    s.datedLessons = [change('eng', sep7, teacherIds=['t4'], changeType='substitution'),
                      change('eng', aug24, teacherIds=['t5'], changeType='self_study'),
                      change('eng2', aug24, teacherIds=['t3'], roomIds=['r2'], changeType='merge', mergeWith=f'{aug24}:hist'),
                      DatedLesson(id='mv', name='Перенос', kind='change', placementId='d0', sourceDate=aug24, onDate=date(2026, 8, 25), teacherIds=['t0'], changeType='move'),
                      change('d2', aug24, teacherIds=['t4'], roomIds=['r0'])]
    s = State.model_validate(s.model_dump())
    counts = lambda day, row: {c['id']: c['substitutions'] for c in substitute_options(s, request(day, row))['candidates']}
    # August: self-study (t5) and a merge (t3) count; a September change, a move and a change that only
    # restates the planned teacher (t4 keeps his own lesson) do not.
    assert counts(MON, 'eng') == {'t0': 0, 't1': 0, 't2': 0, 't3': 1, 't4': 0, 't5': 1}
    fairness = {c['id']: next(f for f in c['factors'] if f['key'] == 'fairness') for c in substitute_options(s, request(MON, 'eng'))['candidates']}
    assert fairness['t5']['points'] == 0 and fairness['t4']['points'] == fairness['t4']['max']
    assert counts(sep7, 'eng2') == {'t0': 0, 't1': 0, 't2': 0, 't3': 0, 't4': 1, 't5': 0}


def test_weights_api(web):
    s = stored(ranked())
    response = web.put('/api/calendar/substitution-weights?workspace=demo', json=dict(revision=s.revision, weights=dict(subject=1, group=2, presence=3, fairness=4, building=5)))
    assert response.status_code == 200, response.text
    assert response.json()['revision'] == s.revision + 1 and response.json()['settings']['substitutionWeights']['group'] == 2
    assert api.read('demo').settings.substitutionWeights.building == 5
    assert web.get('/api/history?workspace=demo').json()['undo'] >= 1
    assert web.put('/api/calendar/substitution-weights?workspace=demo', json=dict(revision=s.revision, weights=dict(subject=1, group=2, presence=3, fairness=4, building=5))).status_code == 409
    assert web.put('/api/calendar/substitution-weights?workspace=demo', json=dict(revision=s.revision + 1, weights=dict(subject=101, group=2, presence=3, fairness=4, building=5))).status_code == 422
    assert web.post('/api/undo?workspace=demo', json=dict(revision=s.revision + 1)).status_code == 200
    assert api.read('demo').settings.substitutionWeights == SubstitutionWeights()


def test_calendar_endpoints_reject_non_staff_accounts(client):
    enable(client); create(client, 'teacher'); create(client, 'student'); create(client, 'parent')
    revision = api.read('demo').revision
    calls = [('put', '/api/calendar/substitution-weights', dict(revision=revision, weights=dict(subject=1, group=2, presence=3, fairness=4, building=5))),
             ('post', '/api/calendar/day-plan', dict(revision=revision, day='2026-08-17', teacherId='t2')),
             ('post', '/api/calendar/day-plan/preview', dict(revision=revision, day='2026-08-17', teacherId='t2', assignments=[dict(rowId='x', action='cancel')])),
             ('post', '/api/calendar/substitutes', dict(revision=revision, day='2026-08-17', rowId='x')),
             ('get', '/api/calendar/substitution-report?month=2026-08', None),
             ('get', '/api/calendar/absence-feed', None)]
    for login in ['teacher', 'student', 'parent']:
        user = signed(login)
        for method, path, body in calls:
            response = getattr(user, method)(path + ('&' if '?' in path else '?') + 'workspace=demo', **({'json': body} if body else {}))
            assert response.status_code == 403, (login, path, response.status_code)
    assert client.get('/api/calendar/substitution-report?workspace=demo&month=2026-08').status_code == 200
    assert client.get('/api/calendar/absence-feed?workspace=demo').status_code == 200


# ---- S4: types in portal, publications and the editor --------------------------------------------

def self_study_state():
    s = ranked()
    s.datedLessons = [change('eng', MON, teacherIds=['t5'], changeType='self_study')]
    return State.model_validate(s.model_dump())


def test_types_visible_to_teacher_only_in_portal_and_publication():
    from backend.calendar_ops import TYPES
    from backend.portal_cache import Snapshot
    from backend.publications import public_document, PublicationRequest
    s = self_study_state(); snapshot = Snapshot(s)
    rows = snapshot.day_rows(MON, 'teacher', 't5')
    assert [r['changeType'] for r in rows] == ['self_study']
    assert all('changeType' not in r for r in snapshot.day_rows(MON, 'student', 's1'))
    assert all('changeType' not in r for r in snapshot.day_rows(MON, 'class_', 'c-demo-a'))
    request = dict(revision=s.revision, name='Дни', kind='dated', firstDate=MON, lastDate=MON)
    teacher, _ = public_document(s, PublicationRequest(view='teacher', entityIds=['t5'], **request))
    assert TYPES['self_study'] in teacher
    klass, _ = public_document(s, PublicationRequest(view='class', entityIds=['c-demo-a'], **request))
    assert TYPES['self_study'] not in klass


def test_api_change_type_persists(web):
    s = stored(ranked())
    value = change('eng', MON, teacherIds=['t3'], roomIds=['r2'], changeType='merge', mergeWith=f'{MON}:hist')
    preview = web.post('/api/calendar/preview?workspace=demo', json=dict(revision=s.revision, table='datedLessons', value=value.model_dump(mode='json')))
    assert preview.status_code == 200, preview.text
    saved = web.post('/api/editor/apply?workspace=demo', json=dict(revision=s.revision, token=preview.json()['token']))
    assert saved.status_code == 200, saved.text
    assert web.get('/api/state?workspace=demo').json()['datedLessons'][0]['changeType'] == 'merge'
    row = next(r for r in web.get(f'/api/calendar/day?workspace=demo&day={MON}').json()['rows'] if r['placementId'] == 'eng')
    assert row['changeType'] == 'merge' and row['mergeWith'] == f'{MON}:hist'
    stale = change('eng2', MON, teacherIds=['t0'], roomIds=['r1'], changeType='merge', mergeWith=f'{MON}:hist')
    blocked = web.post('/api/calendar/preview?workspace=demo', json=dict(revision=saved.json()['revision'], table='datedLessons', value=stale.model_dump(mode='json'))).json()
    assert not blocked['canApply'] and any(i['type'] == 'dated_merge' for i in blocked['newIssues'])


def test_schedule_day_preview_api(web):
    s = stored(fixture())
    value = ScheduleDay(id='sat', name='Отработка', onDate=SAT, day=0).model_dump(mode='json')
    preview = web.post('/api/calendar/preview?workspace=demo', json=dict(revision=s.revision, table='scheduleDays', value=value)).json()
    assert preview['canApply'] and preview['diff']['changes'][0]['table'] == 'scheduleDays'
    applied = web.post('/api/editor/apply?workspace=demo', json=dict(revision=s.revision, token=preview['token'])).json()
    report = web.get(f'/api/calendar/day?workspace=demo&day={SAT}').json()
    assert len(report['rows']) == 2 and report['scheduleDay']['day'] == 0
    removal = web.post('/api/calendar/preview?workspace=demo', json=dict(revision=applied['revision'], table='scheduleDays', removeId='sat')).json()
    assert removal['canApply']
    web.post('/api/editor/apply?workspace=demo', json=dict(revision=applied['revision'], token=removal['token']))
    assert web.get(f'/api/calendar/day?workspace=demo&day={SAT}').json()['rows'] == []
    bad = web.post('/api/calendar/preview?workspace=demo', json=dict(revision=applied['revision'] + 1, table='scheduleDays', value=ScheduleDay(id='x', name='Д', onDate=SUN, day=6, cycleId='weekly').model_dump(mode='json')))
    assert bad.status_code == 422


# ---- S5: whole-day plan ----------------------------------------------------------------------

def plan_request(web, teacher='t2', day=MON):
    return web.post('/api/calendar/day-plan?workspace=demo', json=dict(revision=api.read('demo').revision, day=str(day), teacherId=teacher))


def plan(web, s, **kwargs):
    """The proposals of the whole-day plan for a school state stored for the test."""
    s.revision = api.read('demo').revision
    stored(s)
    response = plan_request(web, **kwargs)
    assert response.status_code == 200, response.text
    return response.json()


def test_day_plan_proposes_each_lesson_greedily(web):
    result = plan(web, ranked())
    before = api.read('demo')
    assert [l['row']['id'] for l in result['lessons']] == [f'{MON}:eng', f'{MON}:eng2']
    assert [l['proposal']['teacherId'] for l in result['lessons']] == ['t4', 't4']
    first, second = result['lessons']
    assert first['proposal']['action'] == 'substitution' and first['row']['editable'] and first['row']['actions'] == ['substitution', 'merge', 'self_study', 'cancel', 'none']
    assert all(c['checked'] for c in first['candidates'][:3]) and first['busyCount'] == 1
    t4 = next(c for c in second['candidates'] if c['id'] == 't4')
    assert t4['lessons'] == next(c for c in first['candidates'] if c['id'] == 't4')['lessons'] + 1
    assert result['absences'][0]['id'] == 'abs' and any(o['rowId'] == f'{MON}:hist' for o in first['mergeOptions'])
    assert plan_request(web).status_code == 200 and api.read('demo') == before
    assert plan_request(web, teacher='t5').json()['lessons'] == []


@pytest.mark.parametrize('values', [dict(teacher='missing'), dict(day=date(2027, 7, 1))], ids=['unknown-teacher', 'day-outside-the-school-year'])
def test_day_plan_is_refused_for_an_unknown_teacher_and_a_day_outside_the_year(web, values):
    stored(ranked())
    assert plan_request(web, **values).status_code == 422


def test_day_plan_without_free_teacher_proposes_nothing(web):
    s = ranked()
    s.absences += [TeacherAbsence(id=f'a-{t}', name='Нет', teacherId=t, start=MON, end=MON) for t in ['t0', 't1', 't3', 't4', 't5']]
    result = plan(web, State.model_validate(s.model_dump()))
    assert [l['proposal']['action'] for l in result['lessons']] == ['none', 'none']


def test_day_plan_leaves_lessons_outside_a_partial_absence_alone(web):
    first, second = (l['row'] for l in plan(web, ranked())['lessons'])
    def planned(start, end):
        s = ranked()
        s.absences = [TeacherAbsence(id='abs', name='Врач', teacherId='t2', start=MON, end=MON, startTime=start, endTime=end)]
        return plan(web, State.model_validate(s.model_dump()))['lessons']
    lessons = planned(first['start'], first['end'])
    assert [l['proposal']['action'] for l in lessons] == ['substitution', 'none']
    # The lesson stays available for a manual choice, with ranked candidates.
    assert lessons[1]['row']['actions'] == ['substitution', 'merge', 'self_study', 'cancel', 'none'] and lessons[1]['candidates']
    # An absence that touches neither lesson proposes nothing; touching only the second proposes only it.
    assert [l['proposal']['action'] for l in planned('12:00', '13:00')] == ['none', 'none']
    assert [l['proposal']['action'] for l in planned(second['start'], second['end'])] == ['none', 'substitution']
    # Without a recorded absence every lesson of the teacher is planned.
    s = ranked(); s.absences = []
    assert [l['proposal']['action'] for l in plan(web, s)['lessons']] == ['substitution', 'substitution']


def assignments(*items): return [dict(rowId=f'{MON}:{r}', action=a, **extra) for r, a, extra in items]


def test_day_plan_api_conflict_blocks_and_nothing_is_saved(web):
    s = stored(ranked())
    preview = web.post('/api/calendar/day-plan/preview?workspace=demo', json=dict(revision=s.revision, day=str(MON), teacherId='t2', assignments=assignments(('eng', 'substitution', dict(teacherId='t3')))))
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert not body['canApply'] and any(i['type'] == 'dated_collision' for i in body['newIssues'])
    applied = web.post('/api/editor/apply?workspace=demo', json=dict(revision=s.revision, token=body['token']))
    assert applied.status_code == 422
    assert api.read('demo').datedLessons == []


def test_day_plan_api_atomic_apply_and_replay(web):
    s = stored(ranked())
    payload = dict(revision=s.revision, day=str(MON), teacherId='t2', name='Болезнь Т2', assignments=assignments(('eng', 'substitution', dict(teacherId='t4')), ('eng2', 'self_study', dict(teacherId='t5'))))
    preview = web.post('/api/calendar/day-plan/preview?workspace=demo', json=payload)
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert body['canApply'] and body['diff']['added'] == 2
    applied = web.post('/api/editor/apply?workspace=demo', json=dict(revision=s.revision, token=body['token']))
    assert applied.status_code == 200, applied.text
    saved = applied.json()
    assert saved['revision'] == s.revision + 1
    assert {c['changeType'] for c in saved['datedLessons']} == {'substitution', 'self_study'} and {c['name'] for c in saved['datedLessons']} == {'Болезнь Т2'}
    day = web.get(f'/api/calendar/day?workspace=demo&day={MON}').json()
    assert not any(i['type'] == 'dated_absence' for i in day['issues'])
    assert {r['placementId']: r['changeType'] for r in day['rows'] if r['changeId']} == {'eng': 'substitution', 'eng2': 'self_study'}
    assert web.post('/api/editor/apply?workspace=demo', json=dict(revision=s.revision, token=body['token'])).status_code == 409
    assert len(api.read('demo').datedLessons) == 2
    assert web.post('/api/calendar/day-plan/preview?workspace=demo', json=payload).status_code == 409
    assert web.post('/api/undo?workspace=demo', json=dict(revision=saved['revision'])).status_code == 200
    assert api.read('demo').datedLessons == []


def plan_preview(web, items, day=MON, **extra):
    return web.post('/api/calendar/day-plan/preview?workspace=demo', json=dict(revision=api.read('demo').revision, day=str(day), teacherId='t2', assignments=items) | extra)


REFUSED_PLANS = {
    'lesson-of-another-teacher': (assignments(('d0', 'substitution', dict(teacherId='t5'))), {}),
    'unknown-lesson': (assignments(('missing', 'cancel', {})), {}),
    'lesson-planned-twice': (assignments(('eng', 'cancel', {}), ('eng', 'substitution', dict(teacherId='t4'))), {}),
    'nothing-chosen': (assignments(('eng', 'none', {})), {}),
    'substitution-without-a-teacher': (assignments(('eng', 'substitution', {})), {}),
    'substitute-is-the-absent-teacher': (assignments(('eng', 'substitution', dict(teacherId='t2'))), {}),
    'unknown-substitute': (assignments(('eng', 'self_study', dict(teacherId='missing'))), {}),
    'merge-into-itself': (assignments(('eng', 'merge', dict(mergeWith=f'{MON}:eng'))), {}),
    'merge-into-a-lesson-at-another-time': (assignments(('eng', 'merge', dict(mergeWith=f'{MON}:d0'))), {}),
    'no-assignments': ([], {}),
    'unknown-teacher': (assignments(('eng', 'substitution', dict(teacherId='t4'))), dict(teacherId='missing')),
    'day-outside-the-school-year': (assignments(('eng', 'cancel', {})), dict(day='2027-07-01')),
}


@pytest.mark.parametrize('items,extra', REFUSED_PLANS.values(), ids=REFUSED_PLANS)
def test_day_plan_assignments_that_cannot_be_applied_are_refused(web, items, extra):
    stored(ranked())
    assert plan_preview(web, items, **extra).status_code == 422


@pytest.mark.parametrize('items', [assignments(('eng', 'cancel', {})), assignments(('eng', 'merge', dict(mergeWith=f'{MON}:hist')))], ids=['cancel', 'merge'])
def test_day_plan_assignments_that_can_be_applied_are_previewed(web, items):
    stored(ranked())
    preview = plan_preview(web, items)
    assert preview.status_code == 200 and preview.json()['canApply'] and preview.json()['diff']['added'] == 1


def test_day_plan_of_a_stale_revision_is_refused(web):
    s = stored(ranked())
    assert web.post('/api/calendar/day-plan?workspace=demo', json=dict(revision=s.revision - 1, day=str(MON), teacherId='t2')).status_code == 409


def apply_plan(web, preview):
    assert preview.status_code == 200, preview.text
    applied = web.post('/api/editor/apply?workspace=demo', json=dict(revision=api.read('demo').revision, token=preview.json()['token']))
    assert applied.status_code == 200, applied.text
    return api.read('demo').datedLessons


def test_day_plan_replaces_the_existing_change_of_a_lesson(web):
    s = ranked()
    s.datedLessons = [change('eng', MON, roomIds=['r1'])]
    stored(State.model_validate(s.model_dump()))
    (record,) = apply_plan(web, plan_preview(web, assignments(('eng', 'substitution', dict(teacherId='t4')))))
    assert record.id == s.datedLessons[0].id and record.roomIds == ['r1'] and record.teacherIds == ['t4'] and record.changeType == 'substitution'


def moved_lesson_state():
    s = ranked()
    s.datedLessons = [change('eng', MON, teacherIds=['t2']).model_copy(update=dict(onDate=TUE, changeType='move'))]
    return State.model_validate(s.model_dump())


@pytest.mark.parametrize('items', [assignments(('eng', 'cancel', {})), assignments(('eng', 'self_study', dict(teacherId='t5')))], ids=['cancel', 'self-study'])
def test_day_plan_only_replaces_the_teacher_of_a_moved_lesson(web, items):
    stored(moved_lesson_state())
    assert plan_preview(web, items, day=TUE).status_code == 422


def test_day_plan_keeps_a_moved_lesson_moved(web):
    stored(moved_lesson_state())
    (record,) = apply_plan(web, plan_preview(web, assignments(('eng', 'substitution', dict(teacherId='t4'))), day=TUE))
    assert record.changeType == 'move' and record.teacherIds == ['t4']


def hosted_state():
    s = ranked()
    s.placements = [p for p in s.placements if p.id != 'hist']
    s.workloads.append(s.workloads[1].model_copy(update=dict(id='wh', teacherId='t2')))
    s.placements.append(Placement(id='host', workloadId='wh', day=0, period=1, roomId='r1'))
    s.datedLessons = [change('eng', MON, teacherIds=['t2'], roomIds=['r1'], changeType='merge', mergeWith=f'{MON}:host')]
    return State.model_validate(s.model_dump())


def test_day_plan_keeps_a_merged_group_with_its_host(web):
    s = hosted_state()
    assert not [i for i in day_report(s, MON)['issues'] if i['type'] in ['dated_collision', 'dated_merge']]
    lessons = {l['row']['id']: l for l in plan(web, s)['lessons']}
    host, merged = lessons[f'{MON}:host'], lessons[f'{MON}:eng']
    assert host['proposal']['action'] == 'substitution'
    assert merged['proposal'] == dict(action='merge', mergeWith=f'{MON}:host')
    chosen = host['proposal']['teacherId']
    chosen_plan = assignments(('host', 'substitution', dict(teacherId=chosen)), ('eng', 'merge', dict(mergeWith=f'{MON}:host')))
    by_placement = {c.placementId: c for c in apply_plan(web, plan_preview(web, chosen_plan))}
    assert by_placement['eng'].teacherIds == [chosen] and by_placement['eng'].changeType == 'merge' and by_placement['host'].teacherIds == [chosen]
    issues = web.get(f'/api/calendar/day?workspace=demo&day={MON}').json()['issues']
    assert not [i for i in issues if i['type'] in ['dated_collision', 'dated_merge', 'dated_absence'] and f'{MON}:eng' in i['ids'] + [f'{MON}:host']]


@pytest.mark.parametrize('items', [assignments(('host', 'cancel', {}), ('eng', 'merge', dict(mergeWith=f'{MON}:host'))),
                                   assignments(('host', 'merge', dict(mergeWith=f'{MON}:eng')), ('eng', 'merge', dict(mergeWith=f'{MON}:host')))], ids=['host-cancelled', 'chain-of-merges'])
def test_day_plan_does_not_merge_a_group_into_a_cancelled_or_merged_host(web, items):
    stored(hosted_state())
    assert plan_preview(web, items).status_code == 422


# ---- S6: monthly totals ----------------------------------------------------------------------

def totals_state():
    s = ranked()
    s.datedLessons = [change('eng', MON, teacherIds=['t4'], changeType='substitution'),
                      change('eng2', MON, teacherIds=['t5'], changeType='self_study'),
                      DatedLesson(id='cancel', name='Отмена', kind='cancel', placementId='eng', sourceDate=date(2026, 8, 24), onDate=date(2026, 8, 24)),
                      DatedLesson(id='move', name='Перенос', kind='change', placementId='eng', sourceDate=date(2026, 8, 31), onDate=date(2026, 9, 1), changeType='move')]
    return State.model_validate(s.model_dump())


def test_monthly_totals():
    s = totals_state()
    report = monthly_totals(s, '2026-08')
    people = {t['teacherId']: t for t in report['teachers']}
    t2 = people['t2']
    assert (t2['planned'], t2['held'], t2['own'], t2['missed'], t2['missedAbsent'], t2['cancelled'], t2['moved'], t2['substitutions']) == (6, 2, 2, 3, 2, 1, 1, 0)
    t4 = people['t4']
    assert (t4['planned'], t4['own'], t4['substitutions'], t4['held'], t4['byType']['substitution']) == (6, 6, 1, 7, 1)
    t5 = people['t5']
    assert (t5['planned'], t5['substitutions'], t5['byType']['self_study'], t5['held']) == (0, 1, 1, 1)
    assert (people['t3']['planned'], people['t3']['held']) == (3, 3)
    assert report['teachers'] == sorted(report['teachers'], key=lambda t: t['name'])
    assert report['totals']['substitutions'] == 2 and report['totals']['byType']['self_study'] == 1 and report['totals']['planned'] == sum(t['planned'] for t in report['teachers'])
    assert report['first'] == '2026-08-17' and report['last'] == '2026-08-31'
    september = {t['teacherId']: t for t in monthly_totals(s, '2026-09')['teachers']}['t2']
    assert september['held'] == september['planned'] + 1
    only = monthly_totals(s, '2026-08', 't0')
    assert [t['teacherId'] for t in only['teachers']] == ['t0'] and only['teachers'][0]['planned'] == 0


def test_monthly_totals_exclude_closures():
    s = ranked(); s.absences = []
    s.events = [Event(id='closed', name='Каникулы', kind='holiday', start=date(2026, 8, 24), end=date(2026, 8, 24), effect='cancel')]
    s = State.model_validate(s.model_dump())
    t2 = next(t for t in monthly_totals(s, '2026-08')['teachers'] if t['teacherId'] == 't2')
    assert (t2['planned'], t2['missed'], t2['held']) == (4, 0, 4)
    assert monthly_totals(s, '2026-08')['first'] == '2026-08-17'


@pytest.mark.parametrize('query', ['month=2026-13', 'month=2026-8', 'month=august', 'month=2027-08', 'month=2026-08&teacherId=missing'],
                         ids=['month-13', 'month-without-zero', 'not-a-month', 'month-outside-the-year', 'unknown-teacher'])
def test_monthly_report_refuses_a_bad_month_and_an_unknown_teacher(web, query):
    stored(ranked())
    assert web.get('/api/calendar/substitution-report?workspace=demo&' + query).status_code == 422


def test_monthly_totals_count_override_dates_and_skip_staff_groups():
    s = fixture()
    s.scheduleDays = [ScheduleDay(id='sat', name='Отработка', onDate=SAT, day=0)]
    s = State.model_validate(s.model_dump())
    t2 = next(t for t in monthly_totals(s, '2026-08')['teachers'] if t['teacherId'] == 't2')
    assert t2['planned'] == 4 and t2['held'] == 4


def test_monthly_report_api_uses_the_timetable_of_each_date_and_the_school_clock(web, monkeypatch):
    monkeypatch.setattr(workload_facts, 'now', lambda: datetime(2026, 8, 24, 7, 0, tzinfo=timezone.utc))
    monkeypatch.setattr(timetable_history, 'today', lambda: date(2026, 8, 24))
    stored(ranked())
    report = lambda: {t['teacherId']: t for t in web.get('/api/calendar/substitution-report?workspace=demo&month=2026-08').json()['teachers']}
    before = report()['t2']
    assert before['planned'] == before['held'] + before['uncovered'] + before['upcoming'] and before['uncovered'] > 0 and before['upcoming'] > 0
    s = api.read('demo')
    s.workloads[0].teacherId = 't5'
    api.save('demo', s, effective=date(2026, 8, 24))
    after = report()
    # Lessons of the days before the change stay with the teacher who had them.
    assert 0 < after['t2']['planned'] < before['planned']
    assert after['t5']['planned'] == before['planned'] - after['t2']['planned'] and after['t5']['upcoming'] > 0
    assert after['t2']['uncovered'] == before['uncovered']


def test_monthly_report_api(web, monkeypatch):
    monkeypatch.setattr(workload_facts, 'now', lambda: datetime(2026, 9, 14, 7, 0, tzinfo=timezone.utc))
    stored(totals_state())
    response = web.get('/api/calendar/substitution-report?workspace=demo&month=2026-08')
    assert response.status_code == 200 and response.json()['totals']['substitutions'] == 2
    assert web.get('/api/calendar/substitution-report?workspace=demo&month=bad').status_code == 422
    assert web.get('/api/calendar/substitution-report?workspace=demo&month=2026-08&teacherId=zzz').status_code == 422
