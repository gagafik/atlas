from datetime import date
import pytest
from fastapi.testclient import TestClient
from backend import api
from backend.models import State, Placement, Event, DatedLesson, TeacherAbsence, Cycles, Room, TimetableRule
from backend.seed import initial_state
from backend.calendar_ops import day_report, check_dates, operational_dates, substitute_options, SubstituteRequest
from backend.editing import inspect_edit

MON = date(2026, 8, 17)
TUE = date(2026, 8, 18)


def fixture():
    s = initial_state('demo')
    s.events = []
    s.placements = [Placement(id='eng', workloadId='w0', day=0, period=1, roomId='r0'),
                    Placement(id='math', workloadId='w4', day=1, period=2, roomId='r1'),
                    Placement(id='phy', workloadId='w5', day=1, period=2, roomId='r2')]
    return s


def change(**kwargs):
    return DatedLesson(id='change', name='Замена', kind='change', placementId='eng', sourceDate=MON, onDate=MON, **kwargs)


def test_date_resolves_real_day_clock_and_cycles_without_mutation():
    s = fixture(); before = s.model_dump()
    r = day_report(s, MON)
    assert len(r['rows']) == 1 and r['rows'][0]['subject'] == 'Английский язык'
    assert day_report(s, date(2026, 8, 16))['rows'] == []
    assert day_report(s, date(2026, 8, 22))['rows'] == []
    assert s.model_dump() == before
    s.cycles = Cycles(patterns=[dict(id='weekly', name='A'), dict(id='b', name='B')])
    assert day_report(s, date(2026, 8, 24))['rows'] == []


def test_calendar_note_is_not_cancellation_and_explicit_class_scope_filters_mixed_audience():
    s = fixture(); s.events = [Event(id='e', name='Reading', kind='reading', start=TUE, end=TUE, grades='Все')]
    assert all(r['status'] != 'cancelled' for r in day_report(s, TUE)['rows'])
    s.events[0].effect = 'cancel'; s.events[0].classIds = ['c-demo-a']
    r = next(r for r in day_report(s, TUE)['rows'] if r['placementId'] == 'math')
    assert 'c-demo-a' not in r['classIds'] and 's1' not in r['studentIds']
    assert 's1' in r['originalStudentIds'] and r['excludedClassIds'] == ['c-demo-a']
    assert r['studentIds'] and r['status'] == 'scheduled'
    s.events[0].classIds = []
    assert all(r['status'] == 'cancelled' for r in day_report(s, TUE)['rows'])
    assert day_report(s, TUE)['issueCount'] == 0


def test_cancellation_frees_resources_and_is_limited_to_single_occurrence():
    s = fixture(); s.placements.append(s.placements[0].model_copy(update={'id': 'eng2'}))
    assert day_report(s, MON)['issueCount'] == 1
    s.datedLessons = [DatedLesson(id='c', name='Отмена', kind='cancel', placementId='eng', sourceDate=MON, onDate=MON)]
    assert day_report(s, MON)['issueCount'] == 0
    assert day_report(s, date(2026, 8, 24))['issueCount'] == 1


def test_move_keeps_original_display_and_checks_target_day_and_source_orphans():
    s = fixture()
    c = change(startTime='16:00', endTime='16:40', teacherIds=['t0'])
    c.onDate = TUE
    s.datedLessons = [c]
    original = day_report(s, MON)['rows'][0]
    assert original['status'] == 'cancelled' and original['start'] != '16:00' and original['teacherIds'] != ['t0']
    moved = next(r for r in day_report(s, TUE)['rows'] if r['changeId'])
    assert moved['status'] == 'changed' and moved['start'] == '16:00'
    assert moved['teacherIds'] == ['t0']
    altered = s.model_copy(deep=True); altered.placements[0].day = 2
    preview = inspect_edit(s, altered)
    assert not preview['canApply'] and any(i['type'] == 'dated_orphan' for i in preview['newIssues'])


def test_absence_is_reported_and_substitution_checks_coteachers_and_rooms():
    s = fixture(); w = s.workloads[0]
    s.absences = [TeacherAbsence(id='a', name='Болезнь', teacherId=w.teacherId, start=MON, end=MON)]
    assert day_report(s, MON)['issues'][0]['type'] == 'dated_absence'
    s.datedLessons = [change(teacherIds=['t0'])]
    assert day_report(s, MON)['issueCount'] == 0
    s.placements.append(Placement(id='other', workloadId='w4', day=0, period=1, roomId='r1'))
    assert any(i['type'] == 'dated_collision' for i in day_report(s, MON)['issues'])
    s.datedLessons[0].teacherIds = ['t0', w.teacherId]
    assert any(i['type'] == 'dated_absence' for i in day_report(s, MON)['issues'])


def test_added_lesson_on_weekend_and_explicit_empty_resource_lists():
    s = fixture(); saturday = date(2026, 8, 22)
    s.datedLessons = [DatedLesson(id='x', name='Разовый', kind='add', onDate=saturday, groupId='g-math', profileId=s.workloads[4].profileId, subjectId=s.workloads[4].subjectId, startTime='10:00', endTime='10:40', teacherIds=[], roomIds=[])]
    # Use the existing subject ID, keeping fixtures independent of translations.
    s.datedLessons[0].subjectId = s.workloads[4].subjectId
    s = State.model_validate(s.model_dump())
    report = day_report(s, saturday)
    assert len(report['rows']) == 1 and report['rows'][0]['status'] == 'added'
    assert report['missingTeachers'] == report['missingRooms'] == 1 and not report['completeChecks']


def test_capacity_and_actual_time_availability_are_checked_for_replacement():
    s = fixture(); s.rooms[0].capacity = 1
    s.datedLessons = [change()]
    s.classes[0].studentCount = 20
    assert any(i['type'] == 'dated_capacity' for i in day_report(s, MON)['issues'])
    s.rules = [TimetableRule(id='r', name='Запрет', kind='availability', scope='teacher', targetIds=[s.workloads[0].teacherId], unavailable=[dict(day=0, start='08:00', end='18:00')])]
    assert any(i['type'] == 'dated_rule' for i in day_report(s, MON)['issues'])


def test_invalid_refs_duplicate_changes_and_dates_rejected():
    s = fixture(); s.datedLessons = [change(), change().model_copy(update={'id': 'second'})]
    with pytest.raises(ValueError, match='одно изменение'): State.model_validate(s.model_dump())
    s.datedLessons = [change(teacherIds=['missing'])]
    with pytest.raises(ValueError, match='учителя'): State.model_validate(s.model_dump())
    with pytest.raises(ValueError): DatedLesson(id='a', name='bad', kind='add', onDate=MON)
    with pytest.raises(ValueError): TeacherAbsence(id='a', name='bad', teacherId='t0', start=MON, end=MON, startTime='12:00', endTime='11:00')


def test_substitute_candidates_exclude_absent_busy_and_keep_other_teachers():
    s = fixture(); teacher = s.workloads[0].teacherId
    s.absences = [TeacherAbsence(id='a', name='Нет', teacherId=teacher, start=MON, end=MON)]
    s.placements.append(Placement(id='busy', workloadId='w4', day=0, period=1, roomId='r1'))
    before = s.model_dump()
    result = substitute_options(s, SubstituteRequest(revision=0, day=MON, rowId=f'{MON}:eng', teacherId=teacher))
    options = {r['id']: r for r in result['candidates']}
    assert not options[teacher]['available'] and not options[s.workloads[4].teacherId]['available']
    assert any(r['available'] for r in result['candidates'])
    assert s.model_dump() == before
    with pytest.raises(ValueError): substitute_options(s, SubstituteRequest(revision=0, day=MON, rowId='missing'))


def test_dated_room_change_respects_directed_travel_and_added_lesson_legacy_availability():
    s = fixture()
    s.campus = s.campus.model_validate(dict(buildings=[dict(id='a', name='A'), dict(id='b', name='B')], defaultTravelMinutes=60))
    s.rooms[0].buildingId = 'a'; s.rooms[1].buildingId = 'b'
    s.placements = [s.placements[0], s.placements[0].model_copy(update={'id':'next', 'period':2, 'roomId':'r1'})]
    s = State.model_validate(s.model_dump())
    assert any(i['type'] == 'dated_travel' for i in day_report(s, MON)['issues'])
    s.datedLessons = [DatedLesson(id='x', name='Дополнительный', kind='add', onDate=TUE, groupId=s.workloads[0].groupId, profileId=s.workloads[0].profileId, subjectId=s.workloads[0].subjectId, startTime='08:00', endTime='12:00', teacherIds=[s.workloads[0].teacherId], roomIds=[])]
    next(t for t in s.teachers if t.id == s.workloads[0].teacherId).unavailable = ['1:1']
    assert any(i['type'] == 'dated_availability' for i in day_report(s, TUE)['issues'])


def test_api_preview_absence_apply_substitute_undo_and_stale_revision(tmp_path, monkeypatch):
    monkeypatch.setattr(api, 'DB', tmp_path / 'dated.db')
    client = TestClient(api.app)
    client.get('/api/state?workspace=demo')
    s = fixture(); s = api.save('demo', s)
    before = client.get('/api/state?workspace=demo').json()
    a = TeacherAbsence(id='a', name='Отсутствие', teacherId=s.workloads[0].teacherId, start=MON, end=MON)
    p = client.post('/api/calendar/preview?workspace=demo', json=dict(revision=s.revision, table='absences', value=a.model_dump(mode='json'))).json()
    assert p['canApply'] and any(i['type'] == 'dated_absence' for i in p['newIssues'])
    assert client.get('/api/state?workspace=demo').json() == before
    applied = client.post('/api/editor/apply?workspace=demo', json=dict(revision=s.revision, token=p['token'])).json()
    assert applied['placements'] == before['placements']
    assert client.post('/api/calendar/preview?workspace=demo', json=dict(revision=s.revision, table='absences', removeId='a')).status_code == 409
    p = client.post('/api/calendar/preview?workspace=demo', json=dict(revision=applied['revision'], table='datedLessons', value=change(teacherIds=['t0']).model_dump(mode='json'))).json()
    assert p['canApply'] and p['diff']['added'] == 1
    newer = client.post('/api/editor/apply?workspace=demo', json=dict(revision=applied['revision'], token=p['token'])).json()
    assert client.get(f'/api/calendar/day?workspace=demo&day={MON}').json()['issueCount'] == 0
    assert client.get(f'/api/calendar/day?workspace=school&day={MON}').json()['revision'] != newer['revision']
    client.post('/api/undo?workspace=demo', json=dict(revision=newer['revision']))
    assert any(i['type'] == 'dated_absence' for i in client.get(f'/api/calendar/day?workspace=demo&day={MON}').json()['issues'])


def test_calendar_data_roundtrips_xml_and_workbook_without_losing_overrides():
    from backend.exporting import state_xml, read_state_xml, workbook, ExportRequest, TABLES
    from backend.tabular import parse_file
    from backend.importing import mapped_state, MappingRequest
    s = fixture(); s.datedLessons = [change(teacherIds=['t0'])]
    s.absences = [TeacherAbsence(id='a', name='Нет', teacherId='t2', start=MON, end=MON)]
    s.events = [Event(id='closed', name='Каникулы', kind='extra', start=TUE, end=TUE, effect='cancel', classIds=['c-demo-a'])]
    assert read_state_xml(state_xml(s)) == s
    book = parse_file('data.xlsx', workbook(s, ExportRequest(revision=0, format='xlsx_data')))
    for key in ['datedLessons', 'absences', 'events']:
        number = next(i for i, x in enumerate(book['sheets']) if x['name'] == TABLES[key])
        sheet = book['sheets'][number]
        request = MappingRequest(revision=0, token='test', table=key, sheet=number, mapping={h:i for i,h in enumerate(sheet['rows'][0])}, skipBlanks=False)
        result = mapped_state(s, book, request)
        assert result['status'] == 'success' and result['candidate'] == s
