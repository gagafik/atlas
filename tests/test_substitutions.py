from datetime import date
import pytest
from fastapi.testclient import TestClient
from backend import api
from backend.models import State, Placement, Teacher, DatedLesson, TeacherAbsence, ScheduleDay, Event, Commitment, TimetableRule, SubstitutionWeights
from backend.models import TeachingPeriod
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


def test_diff_shows_type_and_schedule_day_labels():
    before = ranked(); after = merge_and_self_study()
    labels = {(c['table'], c['id']): c for c in inspect_edit(before, after)['diff']['changes']}
    assert labels['datedLessons', f'c-eng{MON}']['after'].startswith('Объединение · 2026-08-17')
    assert labels['datedLessons', 'c-hist2026-08-24']['after'].startswith('Самостоятельная работа')
    day = labels['scheduleDays', 'sat']
    assert day['kind'] == 'added' and 'Понедельник' in day['after']
    assert day['fields']['day']['displayAfter'] == 'Понедельник' and day['fields']['classIds']['displayAfter'] == '10A'
    assert labels['datedLessons', f'c-eng{MON}']['fields']['changeType']['displayAfter'] == 'Объединение'


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


def test_override_date_uses_the_chosen_weekday_for_rules_and_lunch():
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


def test_two_groups_may_join_the_same_host():
    s = merge_state(40)
    s.classes.append(s.classes[1].model_copy(update=dict(id='c3', name='10C', studentCount=5)))
    s.groups.append(s.groups[1].model_copy(update=dict(id='g-c', name='10C', classIds=['c3'])))
    s.workloads.append(s.workloads[1].model_copy(update=dict(id='wc', groupId='g-c', teacherId='t2')))
    s.placements.append(Placement(id='third', workloadId='wc', day=0, period=1, roomId='r1'))
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
    t4 = {f['key']: f for f in available[0]['factors']}
    assert (t4['subject']['points'], t4['subject']['detail']) == (40, 'Ведёт этот предмет')
    assert t4['group']['points'] == 0
    assert (t4['presence']['points'], t4['presence']['detail']) == (20, 'Уроки до и после — без лишнего прихода')
    assert (t4['fairness']['points'], t4['fairness']['detail']) == (20, 'Замен в этом месяце: 0')
    assert (t4['building']['points'], t4['building']['detail']) == (0, 'Здания кабинетов не заданы')
    t0 = {f['key']: f for f in available[1]['factors']}
    assert (t0['group']['points'], t0['group']['detail']) == (15, 'Ведёт уроки в этом классе')
    assert result['weights'] == dict(subject=40, group=30, presence=20, fairness=20, building=10)
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
             ('get', '/api/calendar/substitution-report?month=2026-08', None)]
    for login in ['teacher', 'student', 'parent']:
        user = signed(login)
        for method, path, body in calls:
            response = getattr(user, method)(path + ('&' if '?' in path else '?') + 'workspace=demo', **({'json': body} if body else {}))
            assert response.status_code == 403, (login, path, response.status_code)
    assert client.get('/api/calendar/substitution-report?workspace=demo&month=2026-08').status_code == 200


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
    assert 'больше не ведёт' in preview(assignments(('d0', 'substitution', dict(teacherId='t5')))).json()['detail']
    assert 'больше не ведёт' in preview(assignments(('missing', 'cancel', {}))).json()['detail']
    assert preview(assignments(('eng', 'cancel', {}), ('eng', 'substitution', dict(teacherId='t4')))).json()['detail'] == 'Урок указан дважды.'
    assert preview(assignments(('eng', 'none', {}))).json()['detail'] == 'Выберите хотя бы одно действие.'
    assert preview(assignments(('eng', 'substitution', {}))).json()['detail'] == 'Выберите учителя.'
    assert preview(assignments(('eng', 'substitution', dict(teacherId='t2')))).json()['detail'] == 'Выберите учителя.'
    assert preview(assignments(('eng', 'self_study', dict(teacherId='missing')))).json()['detail'] == 'Выберите учителя.'
    assert preview(assignments(('eng', 'merge', dict(mergeWith=f'{MON}:eng')))).json()['detail'] == 'Урок для объединения не найден.'
    assert preview(assignments(('eng', 'merge', dict(mergeWith=f'{MON}:d0')))).json()['detail'] == 'Урок для объединения не найден.'
    assert preview([]).status_code == 422
    assert preview(assignments(('eng', 'substitution', dict(teacherId='t4'))), teacherId='missing').status_code == 422
    assert preview(assignments(('eng', 'cancel', {})), day='2027-07-01').json()['detail'] == 'Дата вне учебного года.'
    cancel = preview(assignments(('eng', 'cancel', {})))
    assert cancel.status_code == 200 and cancel.json()['canApply'] and cancel.json()['diff']['changes'][0]['after'].startswith('Отмена')
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


def test_monthly_report_api(web):
    stored(totals_state())
    response = web.get('/api/calendar/substitution-report?workspace=demo&month=2026-08')
    assert response.status_code == 200 and response.json()['totals']['substitutions'] == 2
    assert web.get('/api/calendar/substitution-report?workspace=demo&month=bad').status_code == 422
    assert web.get('/api/calendar/substitution-report?workspace=demo&month=2026-08&teacherId=zzz').status_code == 422
