from datetime import date, datetime, timezone
import pytest
from fastapi.testclient import TestClient
from backend import api, timetable_history, workload_facts
from backend.models import State, Placement, Teacher, DatedLesson, TeacherAbsence, ScheduleDay, Event, Commitment, TimetableRule, SubstitutionWeights
from backend.models import TeachingPeriod, Cycles, Named
from backend.cycles import pattern_on
from backend.commitments import DutyRequest, allocate_duties
from backend.seed import initial_state
from backend.calendar_ops import day_report, change_type, SubstituteRequest, substitute_options
from backend.editing import inspect_edit
from backend.substitutions import monthly_totals, day_plan, DayPlanRequest
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

def test_change_type_validation_matrix():
    base = dict(id='c', name='Замена', placementId='eng', sourceDate=MON, onDate=MON)
    DatedLesson(kind='change', **base)
    DatedLesson(kind='change', changeType='substitution', teacherIds=['t0'], **base)
    DatedLesson(kind='change', changeType='merge', mergeWith=f'{MON}:hist', **base)
    DatedLesson(kind='change', changeType='self_study', teacherIds=['t0'], **base)
    DatedLesson(kind='change', changeType='move', startTime='16:00', endTime='16:40', **base)
    DatedLesson(kind='change', changeType='move', **(base | dict(onDate=TUE)))
    DatedLesson(kind='cancel', changeType='cancel', **base)
    DatedLesson(kind='add', changeType='extra', id='x', name='Разовый', onDate=MON, groupId='g-a', profileId='secondary', subjectId='sub2', startTime='10:00', endTime='10:40')
    invalid = [
        (dict(kind='cancel', changeType='substitution'), 'Тип замены'),
        (dict(kind='change', changeType='cancel'), 'Тип замены'),
        (dict(kind='change', changeType='extra'), 'Тип замены'),
        (dict(kind='change', changeType='merge'), 'присоединяется группа'),
        (dict(kind='change', changeType='substitution', mergeWith='x'), 'присоединяется группа'),
        (dict(kind='change', mergeWith='x'), 'присоединяется группа'),
        (dict(kind='change', changeType='self_study'), 'укажите ответственного'),
        (dict(kind='change', changeType='self_study', teacherIds=[]), 'укажите ответственного'),
        (dict(kind='change', changeType='move'), 'менять дату или время'),
    ]
    for extra, message in invalid:
        with pytest.raises(ValueError, match=message): DatedLesson(**(base | extra))
    with pytest.raises(ValueError, match='Тип замены'):
        DatedLesson(id='x', name='Разовый', kind='add', changeType='merge', mergeWith='a', onDate=MON, groupId='g-a', profileId='secondary', subjectId='sub2', startTime='10:00', endTime='10:40')


def test_old_documents_load_with_defaults_and_legacy_type_is_derived():
    s = initial_state('demo')
    dump = s.model_dump()
    assert dump['scheduleDays'] == [] and dump['settings']['substitutionWeights'] == dict(subject=40, group=30, presence=20, fairness=20, building=10)
    del dump['scheduleDays']; del dump['settings']['substitutionWeights']
    assert State.model_validate(dump) == s
    assert [change_type(c) for c in [None, change(teacherIds=['t0']), change(day=MON).model_copy(update={'onDate': TUE}),
                                     DatedLesson(id='k', name='О', kind='cancel', placementId='eng', sourceDate=MON, onDate=MON),
                                     change(changeType='self_study', teacherIds=['t0'])]] == [None, 'substitution', 'move', 'cancel', 'self_study']


def test_schedule_day_validation():
    s = fixture()
    def make(*days, **update):
        value = s.model_copy(update=dict(scheduleDays=list(days)) | update)
        return State.model_validate(value.model_dump())
    day = ScheduleDay(id='a', name='Отработка', onDate=SAT, day=0)
    assert make(day).scheduleDays == [day]
    with pytest.raises(ValueError, match='один «день в расписании»'): make(day, ScheduleDay(id='b', name='Ещё', onDate=SAT, day=1))
    with pytest.raises(ValueError, match='вне учебной недели'): make(ScheduleDay(id='a', name='Д', onDate=TUE, day=5))
    with pytest.raises(ValueError, match='выходит за учебный год'): make(ScheduleDay(id='a', name='Д', onDate=date(2026, 8, 15), day=0))
    with pytest.raises(ValueError, match='связанные данные'): make(ScheduleDay(id='a', name='Д', onDate=SAT, day=0, classIds=['missing']))
    with pytest.raises(ValueError, match='отсутствующую неделю'): make(ScheduleDay(id='a', name='Д', onDate=SAT, day=0, cycleId='missing'))
    with pytest.raises(ValueError, match='и так работает'): ScheduleDay(id='a', name='Д', onDate=MON, day=0)
    with pytest.raises(ValueError, match='не должны повторяться'): ScheduleDay(id='a', name='Д', onDate=SAT, day=0, classIds=['c-demo-a', 'c-demo-a'])
    periods = dict(cycles=s.cycles.model_copy(update=dict(periods=[TeachingPeriod(id='p', name='Период', start=date(2026, 8, 24), end=date(2027, 6, 11), anchor=date(2026, 8, 24), patternIds=['weekly'])])))
    with pytest.raises(ValueError, match='Дата вне учебных периодов'): make(day, **periods)
    assert make(ScheduleDay(id='a', name='Д', onDate=SAT, day=0, cycleId='weekly'), **periods).scheduleDays[0].cycleId == 'weekly'
    assert ScheduleDay(id='a', name='Д', onDate=MON, day=0, cycleId='weekly').cycleId == 'weekly'


def merge_and_self_study():
    s = ranked()
    s.datedLessons = [change('eng', MON, teacherIds=['t3'], roomIds=['r2'], changeType='merge', mergeWith=f'{MON}:hist'),
                      change('hist', date(2026, 8, 24), teacherIds=['t0'], changeType='self_study')]
    s.scheduleDays = [ScheduleDay(id='sat', name='Отработка', onDate=SAT, day=0, classIds=['c-demo-a'])]
    return State.model_validate(s.model_dump())


def test_types_and_schedule_days_roundtrip_xml_and_workbook():
    from backend.exporting import state_xml, read_state_xml, workbook, ExportRequest, TABLES
    from backend.tabular import parse_file
    from backend.importing import mapped_state, MappingRequest
    s = merge_and_self_study()
    assert read_state_xml(state_xml(s)) == s
    book = parse_file('data.xlsx', workbook(s, ExportRequest(revision=0, format='xlsx_data')))
    for key in ['datedLessons', 'scheduleDays']:
        number = next(i for i, x in enumerate(book['sheets']) if x['name'] == TABLES[key])
        sheet = book['sheets'][number]
        result = mapped_state(s, book, MappingRequest(revision=0, token='test', table=key, sheet=number, mapping={h: i for i, h in enumerate(sheet['rows'][0])}, skipBlanks=False))
        assert result['status'] == 'success' and result['candidate'] == s
    assert TABLES['scheduleDays'] == 'Дни в расписании'


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
    assert [i['ids'] for i in split] == [[f'{TUE}:M0']] and 'Математика' in split[0]['message']
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
    banned = run(s, 'a')
    assert banned['status'] == 'infeasible' and 'запрет по номеру урока' in banned['details'][0]
    assert run(s.model_copy(update=dict(commitments=[duty('a', '09:30', '09:50', next_week)])), 'a')['status'] == 'success'
    # Hard rules follow the week and weekday of the date.
    hard = TimetableRule(id='hard', name='Занят', kind='availability', scope='teacher', targetIds=['t1'], days=[0], cycleIds=['B'], unavailable=[dict(day=0, start='09:00', end='10:00')])
    blocked = run(ab_state(rules=[hard], commitments=[duty('a', '09:30', '09:50')]), 'a')
    assert blocked['status'] == 'infeasible' and 'обязательная недоступность' in blocked['details'][0]
    assert run(ab_state(rules=[hard], commitments=[duty('a', '09:30', '09:50', next_week)]), 'a')['status'] == 'success'
    # A lunch rule of the chosen day keeps two short duties from consuming the break.
    lunch = TimetableRule(id='lunch', name='Обед', kind='day_shape', scope='teacher', targetIds=['t1'], days=[0], cycleIds=['B'], lunchStart='12:00', lunchEnd='13:00', lunchMinutes=30)
    assert run(ab_state(rules=[lunch], commitments=[duty('a', '12:00', '12:20'), duty('b', '12:40', '13:00')]), 'a', 'b')['status'] == 'infeasible'
    assert run(ab_state(rules=[lunch], commitments=[duty('a', '12:00', '12:20', next_week), duty('b', '12:40', '13:00', next_week)]), 'a', 'b')['status'] == 'success'


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
    assert all('changeType' in r for r in day_report(s, MON)['rows'])


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
    s = ranked(); before = s.model_dump()
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
    s.campus = s.campus.model_validate(dict(buildings=[dict(id='a', name='A'), dict(id='b', name='B')], defaultTravelMinutes=0))
    s.rooms[0].buildingId = 'a'; s.rooms[1].buildingId = 'b'
    s = State.model_validate(s.model_dump())
    t4 = next(c for c in options(s)['candidates'] if c['id'] == 't4')
    building = next(f for f in t4['factors'] if f['key'] == 'building')
    assert (building['points'], building['detail']) == (0, 'Нужен переход в другое здание')
    s.rooms[1].buildingId = 'a'
    t4 = next(c for c in options(s)['candidates'] if c['id'] == 't4')
    assert next(f for f in t4['factors'] if f['key'] == 'building')['points'] == 10


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
    from backend.portal_cache import Snapshot
    from backend.publications import public_document, PublicationRequest
    s = self_study_state(); snapshot = Snapshot(s)
    rows = snapshot.day_rows(MON, 'teacher', 't5')
    assert [r['changeType'] for r in rows] == ['self_study']
    assert all('changeType' not in r for r in snapshot.day_rows(MON, 'student', 's1'))
    assert all('changeType' not in r for r in snapshot.day_rows(MON, 'class_', 'c-demo-a'))
    request = dict(revision=s.revision, name='Дни', kind='dated', firstDate=MON, lastDate=MON)
    teacher, _ = public_document(s, PublicationRequest(view='teacher', entityIds=['t5'], **request))
    assert 'Самостоятельная работа' in teacher
    klass, _ = public_document(s, PublicationRequest(view='class', entityIds=['c-demo-a'], **request))
    assert 'Самостоятельная работа' not in klass and 'Изменено' in klass


def test_api_change_type_persists(web):
    s = stored(ranked())
    value = change('eng', MON, teacherIds=['t3'], roomIds=['r2'], changeType='merge', mergeWith=f'{MON}:hist')
    preview = web.post('/api/calendar/preview?workspace=demo', json=dict(revision=s.revision, table='datedLessons', value=value.model_dump(mode='json')))
    assert preview.status_code == 200, preview.text
    assert preview.json()['diff']['changes'][0]['after'].startswith('Объединение')
    saved = web.post('/api/editor/apply?workspace=demo', json=dict(revision=s.revision, token=preview.json()['token']))
    assert saved.status_code == 200, saved.text
    assert web.get('/api/state?workspace=demo').json()['datedLessons'][0]['changeType'] == 'merge'
    row = next(r for r in web.get(f'/api/calendar/day?workspace=demo&day={MON}').json()['rows'] if r['placementId'] == 'eng')
    assert row['changeType'] == 'merge' and row['mergeWith'] == f'{MON}:hist'
    with pytest.raises(ValueError, match='присоединяется группа'): change('eng2', MON, changeType='merge')
    stale = change('eng2', MON, teacherIds=['t0'], roomIds=['r1'], changeType='merge', mergeWith=f'{MON}:hist')
    blocked = web.post('/api/calendar/preview?workspace=demo', json=dict(revision=saved.json()['revision'], table='datedLessons', value=stale.model_dump(mode='json'))).json()
    assert not blocked['canApply'] and any(i['type'] == 'dated_merge' for i in blocked['newIssues'])


def test_schedule_day_preview_api(web):
    s = stored(fixture())
    value = ScheduleDay(id='sat', name='Отработка', onDate=SAT, day=0).model_dump(mode='json')
    preview = web.post('/api/calendar/preview?workspace=demo', json=dict(revision=s.revision, table='scheduleDays', value=value)).json()
    assert preview['canApply'] and preview['diff']['changes'][0]['table'] == 'scheduleDays' and 'Понедельник' in preview['diff']['changes'][0]['after']
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

def plan(s, teacher='t2', day=MON):
    return day_plan(s, DayPlanRequest(revision=s.revision, day=day, teacherId=teacher))


def test_day_plan_proposes_each_lesson_greedily():
    s = ranked(); before = s.model_dump()
    result = plan(s)
    assert [l['row']['id'] for l in result['lessons']] == [f'{MON}:eng', f'{MON}:eng2']
    assert [l['proposal']['teacherId'] for l in result['lessons']] == ['t4', 't4']
    first, second = result['lessons']
    assert first['proposal']['action'] == 'substitution' and first['row']['editable'] and first['row']['actions'] == ['substitution', 'merge', 'self_study', 'cancel', 'none']
    assert all(c['checked'] for c in first['candidates'][:3]) and first['busyCount'] == 1
    t4 = next(c for c in second['candidates'] if c['id'] == 't4')
    assert t4['lessons'] == next(c for c in first['candidates'] if c['id'] == 't4')['lessons'] + 1
    assert result['absences'][0]['id'] == 'abs' and any(o['rowId'] == f'{MON}:hist' for o in first['mergeOptions'])
    assert s.model_dump() == before
    with pytest.raises(ValueError, match='Учитель не найден'): plan(s, teacher='missing')
    with pytest.raises(ValueError, match='вне учебного года'): plan(s, day=date(2027, 7, 1))
    assert plan(s, teacher='t5')['lessons'] == []


def test_day_plan_without_free_teacher_proposes_nothing():
    s = ranked()
    s.absences += [TeacherAbsence(id=f'a-{t}', name='Нет', teacherId=t, start=MON, end=MON) for t in ['t0', 't1', 't3', 't4', 't5']]
    s = State.model_validate(s.model_dump())
    result = plan(s)
    assert [l['proposal']['action'] for l in result['lessons']] == ['none', 'none']
    assert 'Свободных учителей не найдено' in result['lessons'][0]['proposal']['reason']


def test_day_plan_leaves_lessons_outside_a_partial_absence_alone():
    first, second = (l['row'] for l in plan(ranked())['lessons'])
    def planned(start, end):
        s = ranked()
        s.absences = [TeacherAbsence(id='abs', name='Врач', teacherId='t2', start=MON, end=MON, startTime=start, endTime=end)]
        return plan(State.model_validate(s.model_dump()))['lessons']
    lessons = planned(first['start'], first['end'])
    assert [l['proposal']['action'] for l in lessons] == ['substitution', 'none']
    reason = lessons[1]['proposal']['reason']
    assert 'вне времени отсутствия' in reason and f"{first['start']}–{first['end']}" in reason
    # The lesson stays available for a manual choice, with ranked candidates.
    assert lessons[1]['row']['actions'] == ['substitution', 'merge', 'self_study', 'cancel', 'none'] and lessons[1]['candidates']
    # An absence that touches neither lesson proposes nothing; touching only the second proposes only it.
    assert [l['proposal']['action'] for l in planned('12:00', '13:00')] == ['none', 'none']
    assert [l['proposal']['action'] for l in planned(second['start'], second['end'])] == ['none', 'substitution']
    # Without a recorded absence every lesson of the teacher is planned.
    s = ranked(); s.absences = []
    assert [l['proposal']['action'] for l in plan(s)['lessons']] == ['substitution', 'substitution']


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


def test_day_plan_validation(web):
    s = stored(ranked())
    def preview(items, **extra):
        return web.post('/api/calendar/day-plan/preview?workspace=demo', json=dict(revision=s.revision, day=str(MON), teacherId='t2', assignments=items) | extra)
    def refused(items, marker, **extra):
        response = preview(items, **extra)
        assert response.status_code == 422 and marker in response.json()['detail'], response.text
    refused(assignments(('d0', 'substitution', dict(teacherId='t5'))), 'больше не ведёт')
    refused(assignments(('missing', 'cancel', {})), 'больше не ведёт')
    refused(assignments(('eng', 'cancel', {}), ('eng', 'substitution', dict(teacherId='t4'))), 'дважды')
    refused(assignments(('eng', 'none', {})), 'хотя бы одно')
    refused(assignments(('eng', 'substitution', {})), 'учителя')
    refused(assignments(('eng', 'substitution', dict(teacherId='t2'))), 'учителя')
    refused(assignments(('eng', 'self_study', dict(teacherId='missing'))), 'учителя')
    refused(assignments(('eng', 'merge', dict(mergeWith=f'{MON}:eng'))), 'объединения')
    refused(assignments(('eng', 'merge', dict(mergeWith=f'{MON}:d0'))), 'объединения')
    assert preview([]).status_code == 422
    assert preview(assignments(('eng', 'substitution', dict(teacherId='t4'))), teacherId='missing').status_code == 422
    refused(assignments(('eng', 'cancel', {})), 'учебного года', day='2027-07-01')
    cancel = preview(assignments(('eng', 'cancel', {})))
    assert cancel.status_code == 200 and cancel.json()['canApply'] and cancel.json()['diff']['added'] == 1
    merged = preview(assignments(('eng', 'merge', dict(mergeWith=f'{MON}:hist'))))
    assert merged.status_code == 200 and merged.json()['canApply']
    stale = web.post('/api/calendar/day-plan?workspace=demo', json=dict(revision=s.revision - 1, day=str(MON), teacherId='t2'))
    assert stale.status_code == 409


def test_day_plan_replaces_existing_change():
    s = ranked()
    s.datedLessons = [change('eng', MON, roomIds=['r1'])]
    s = State.model_validate(s.model_dump())
    from backend.substitutions import plan_candidate, DayPlanPreview
    body = DayPlanPreview(revision=0, day=MON, teacherId='t2', assignments=[dict(rowId=f'{MON}:eng', action='substitution', teacherId='t4')])
    candidate = plan_candidate(s, body)
    assert len(candidate.datedLessons) == 1
    record = candidate.datedLessons[0]
    assert record.id == s.datedLessons[0].id and record.roomIds == ['r1'] and record.teacherIds == ['t4'] and record.changeType == 'substitution'
    moved = ranked()
    moved.datedLessons = [change('eng', MON, teacherIds=['t2']).model_copy(update=dict(onDate=TUE, changeType='move'))]
    moved = State.model_validate(moved.model_dump())
    on_tue = DayPlanPreview(revision=0, day=TUE, teacherId='t2', assignments=[dict(rowId=f'{MON}:eng', action='cancel')])
    with pytest.raises(ValueError, match='отменяется в форме урока'): plan_candidate(moved, on_tue)
    other = DayPlanPreview(revision=0, day=TUE, teacherId='t2', assignments=[dict(rowId=f'{MON}:eng', action='self_study', teacherId='t5')])
    with pytest.raises(ValueError, match='только замена учителя'): plan_candidate(moved, other)
    kept = plan_candidate(moved, DayPlanPreview(revision=0, day=TUE, teacherId='t2', assignments=[dict(rowId=f'{MON}:eng', action='substitution', teacherId='t4')]))
    assert kept.datedLessons[0].changeType == 'move' and kept.datedLessons[0].teacherIds == ['t4']


def hosted_state():
    s = ranked()
    s.placements = [p for p in s.placements if p.id != 'hist']
    s.workloads.append(s.workloads[1].model_copy(update=dict(id='wh', teacherId='t2')))
    s.placements.append(Placement(id='host', workloadId='wh', day=0, period=1, roomId='r1'))
    s.datedLessons = [change('eng', MON, teacherIds=['t2'], roomIds=['r1'], changeType='merge', mergeWith=f'{MON}:host')]
    return State.model_validate(s.model_dump())


def test_day_plan_keeps_a_merged_group_with_its_host():
    from backend.substitutions import plan_candidate, DayPlanPreview
    s = hosted_state()
    assert not [i for i in day_report(s, MON)['issues'] if i['type'] in ['dated_collision', 'dated_merge']]
    lessons = {l['row']['id']: l for l in plan(s)['lessons']}
    host, merged = lessons[f'{MON}:host'], lessons[f'{MON}:eng']
    assert host['proposal']['action'] == 'substitution'
    assert merged['proposal'] == dict(action='merge', mergeWith=f'{MON}:host')
    chosen = host['proposal']['teacherId']
    body = DayPlanPreview(revision=0, day=MON, teacherId='t2', assignments=[dict(rowId=f'{MON}:host', action='substitution', teacherId=chosen),
                                                                              dict(rowId=f'{MON}:eng', action='merge', mergeWith=f'{MON}:host')])
    candidate = plan_candidate(s, body)
    by_placement = {c.placementId: c for c in candidate.datedLessons}
    assert by_placement['eng'].teacherIds == [chosen] and by_placement['eng'].changeType == 'merge' and by_placement['host'].teacherIds == [chosen]
    report = day_report(candidate, MON)
    assert not [i for i in report['issues'] if i['type'] in ['dated_collision', 'dated_merge', 'dated_absence'] and f'{MON}:eng' in i['ids'] + [f'{MON}:host']]
    cancelled = DayPlanPreview(revision=0, day=MON, teacherId='t2', assignments=[dict(rowId=f'{MON}:host', action='cancel'), dict(rowId=f'{MON}:eng', action='merge', mergeWith=f'{MON}:host')])
    with pytest.raises(ValueError, match='Урок для объединения не найден'): plan_candidate(s, cancelled)
    chain = DayPlanPreview(revision=0, day=MON, teacherId='t2', assignments=[dict(rowId=f'{MON}:host', action='merge', mergeWith=f'{MON}:eng'), dict(rowId=f'{MON}:eng', action='merge', mergeWith=f'{MON}:host')])
    with pytest.raises(ValueError, match='Урок для объединения не найден'): plan_candidate(s, chain)


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


def test_monthly_totals_exclude_closures_and_validate():
    s = ranked(); s.absences = []
    s.events = [Event(id='closed', name='Каникулы', kind='holiday', start=date(2026, 8, 24), end=date(2026, 8, 24), effect='cancel')]
    s = State.model_validate(s.model_dump())
    t2 = next(t for t in monthly_totals(s, '2026-08')['teachers'] if t['teacherId'] == 't2')
    assert (t2['planned'], t2['missed'], t2['held']) == (4, 0, 4)
    for month in ['2026-13', '2026-8', 'август', '']:
        with pytest.raises(ValueError, match='ГГГГ-ММ'): monthly_totals(s, month)
    with pytest.raises(ValueError, match='Месяц вне учебного года'): monthly_totals(s, '2027-08')
    with pytest.raises(ValueError, match='Учитель не найден'): monthly_totals(s, '2026-08', 'missing')
    assert monthly_totals(s, '2026-08')['first'] == '2026-08-17'


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
