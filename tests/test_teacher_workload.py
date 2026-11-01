import csv
import io
import json
from datetime import date, datetime, timedelta, timezone
import pytest
from backend import api, journal, timetable_history, workload_facts
from backend.models import DatedLesson, Event, Group, Placement, State, TeacherAbsence
from test_access import client, enable, create, signed
from test_journal import lesson, entry, write

MON = date(2026, 8, 17)
TODAY = date(2026, 9, 14)


def at(day, clock):
    """A UTC instant that is this school time (Asia/Qyzylorda is UTC+5)."""
    hour, minute = map(int, clock.split(':'))
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=timezone(timedelta(hours=5))).astimezone(timezone.utc)


@pytest.fixture
def clock(monkeypatch):
    """The school clock: late on a Monday after the first weeks, so those weeks are over; clock(day, 'HH:MM') moves it.
    The journal reads it too, so a publication is stamped with the school time the test is at."""
    def set(day=TODAY, time='12:00'):
        monkeypatch.setattr(workload_facts, 'now', lambda: at(day, time))
        monkeypatch.setattr(timetable_history, 'today', lambda: day)
        monkeypatch.setattr(journal, 'now', lambda: at(day, time))
    set()
    return set


def change(day=MON, placement='eng', **values):
    return DatedLesson(id=f'c-{placement}-{day}', name='Замена', kind='change', placementId=placement, sourceDate=day, onDate=day, **values)


def stored(**update):
    s = api.read('demo')
    for key, value in update.items(): setattr(s, key, value)
    return api.save('demo', State.model_validate(s.model_dump()))


def report(c, start=MON, end=MON + timedelta(days=6), **params):
    return c.get('/api/staff-time/workload', params=dict(workspace='demo', start=str(start), end=str(end), **params))


def figures(c, teacher='t2', start=MON, end=MON + timedelta(days=6), **params):
    response = report(c, start, end, teacherId=teacher, **params)
    assert response.status_code == 200, response.text
    return response.json()['teachers'][0]


def publish(client, day=MON):
    record = entry(client, lesson(client, day=day.isoformat())['lessonKey'], day=day.isoformat())
    record['document']['content']['topic'] = 'Тема'
    for pupil in record['document']['content']['records']: pupil['attendance'] = 'present'
    response = write(client, record, publish=True)
    assert response.status_code == 200, response.text


def publish_topic(client, day):
    """What a teacher may post ahead of a lesson: the topic and homework, with nobody marked."""
    record = entry(client, lesson(client, day=day.isoformat())['lessonKey'], day=day.isoformat())
    record['document']['content']['topic'], record['document']['content']['homework'] = 'Тема', 'Параграф 4'
    for pupil in record['document']['content']['records']: pupil.update(attendance='unmarked', lateMinutes=0, grades=[])
    response = write(client, record, publish=True)
    assert response.status_code == 200, response.text


def the(items, teacher):
    return next(t for t in items if t['teacherId'] == teacher)


def rows_of(text):
    return list(csv.reader(io.StringIO(text.removeprefix('﻿')), delimiter=';'))


def test_a_dated_substitution_moves_the_lesson_and_its_minutes_to_the_substitute(client, clock):
    stored(datedLessons=[change(teacherIds=['t3'], changeType='substitution')])
    row = next(r for r in client.get('/api/calendar/day?workspace=demo&day=2026-08-17').json()['rows'] if r['placementId'] == 'eng')
    minutes = lambda t: int(t[:2]) * 60 + int(t[3:])
    t2, t3 = figures(client, 't2'), figures(client, 't3')
    assert (t2['taught'], t2['missed'], t2['planned']) == (0, 1, 1)
    assert (t3['taught'], t3['substitutions'], t3['byType']['substitution'], t3['planned']) == (1, 1, 1, 0)
    assert t3['minutes'] == minutes(row['end']) - minutes(row['start'])
    assert t3['lessons'][0]['outcome'] == 'taught' and t3['lessons'][0]['kind'] == 'substitution'
    assert the(report(client).json()['teachers'], 't3')['taught'] == 1


def test_cancelled_days_and_calendar_closures(client, clock):
    first, second = date(2026, 8, 24), date(2026, 8, 31)
    stored(datedLessons=[DatedLesson(id='cancel', name='Отмена', kind='cancel', placementId='eng', sourceDate=first, onDate=first)])
    week = figures(client, 't2', first, first + timedelta(days=6))
    assert (week['planned'], week['cancelled'], week['missed'], week['taught']) == (1, 1, 1, 0)
    before = figures(client, 't2', first, second)
    stored(events=[Event(id='closed', name='Каникулы', kind='holiday', start=second, end=second, effect='cancel')])
    after = figures(client, 't2', first, second)
    # A closure is neither planned nor missed; the same week without it still counts the lesson.
    assert after['planned'] == before['planned'] - 1 and after['taught'] == before['taught'] - 1
    assert (after['missed'], after['cancelled']) == (before['missed'], before['cancelled'])


def test_a_dated_template_change_keeps_each_side_of_its_boundary(client, clock):
    s = api.read('demo')
    s.workloads[0].teacherId = 't1'
    api.save('demo', s, effective=date(2026, 9, 7))
    old = lambda t: figures(client, t, MON, date(2026, 9, 6))
    assert old('t2')['taught'] == 3 and old('t2')['own'] == 3
    assert old('t1')['taught'] == 0 and old('t1')['planned'] == 0
    new = lambda t: figures(client, t, date(2026, 9, 7), date(2026, 9, 14))
    assert new('t1')['taught'] == 2 and new('t1')['own'] == 2
    assert new('t2')['taught'] == 0 and new('t2')['planned'] == 0
    # No publication was needed: the dated template alone decides.
    assert new('t1')['confirmed'] == 0 and new('t1')['lessons'][0]['confirmed'] is False


def test_an_undated_template_change_leaves_published_lessons_with_who_taught_them(client, clock):
    publish(client)
    s = api.read('demo')
    s.workloads[0].teacherId = 't3'
    api.save('demo', s)
    end = date(2026, 8, 31)
    t2, t3 = figures(client, 't2', MON, end), figures(client, 't3', MON, end)
    assert (t2['taught'], t2['journalOnly'], t2['confirmed'], t2['differs'], t2['planned']) == (1, 1, 1, 1, 0)
    # The two unpublished Mondays follow the template, and the published one was replaced for the new owner.
    assert (t3['taught'], t3['own'], t3['planned'], t3['missed']) == (2, 2, 3, 1)


def test_a_dated_change_beats_a_publication(client, clock):
    publish(client)
    stored(datedLessons=[change(teacherIds=['t3'], changeType='substitution')])
    t2, t3 = figures(client, 't2'), figures(client, 't3')
    assert (t2['taught'], t2['missed']) == (0, 1)
    assert (t3['taught'], t3['confirmed']) == (1, 1)
    assert t3['lessons'][0]['differs'] is True and t3['lessons'][0]['kind'] == 'substitution'


def test_only_finished_lessons_count_as_taught(client, clock):
    clock(MON, '08:30')
    during = figures(client, 't2', MON, MON)
    assert (during['upcoming'], during['taught'], during['plannedToDate'], during['planned']) == (1, 0, 0, 1)
    clock(MON, '09:30')
    after = figures(client, 't2', MON, MON)
    assert (after['upcoming'], after['taught'], after['plannedToDate'], after['delta']) == (0, 1, 1, 0)
    # A publication made during the lesson counts it at once.
    clock(MON, '08:30')
    publish(client)
    published = figures(client, 't2', MON, MON)
    assert (published['upcoming'], published['taught'], published['confirmed']) == (0, 1, 1)
    # The schedule planned that lesson too, so the publication closes no gap and opens none.
    assert (published['plannedToDate'], published['delta']) == (1, 0)


def test_a_topic_posted_ahead_does_not_make_the_lesson_taught(client, clock):
    ahead = TODAY + timedelta(days=7)
    before = figures(client, 't2', ahead, ahead)
    publish_topic(client, ahead)
    posted = figures(client, 't2', ahead, ahead)
    assert (posted['upcoming'], posted['taught'], posted['plannedToDate'], posted['confirmed'], posted['minutes']) == (1, 0, 0, 0, 0)
    assert posted == before and posted['lessons'][0]['outcome'] == 'upcoming'
    # A closure added afterwards leaves nothing to count: the lesson was never given.
    stored(events=[Event(id='closed', name='Каникулы', kind='holiday', start=ahead, end=ahead, effect='cancel')])
    closed = figures(client, 't2', ahead, ahead)
    assert (closed['taught'], closed['minutes'], closed['confirmed'], closed['differs'], closed['planned']) == (0, 0, 0, 0, 0)
    # The topic does not become proof once the bell has rung: the day is over and the closure still stands.
    clock(ahead, '12:00')
    over = figures(client, 't2', ahead, ahead)
    assert (over['taught'], over['minutes'], over['confirmed'], over['differs'], over['planned'], over['missed'], over['upcoming']) == (0, 0, 0, 0, 0, 0, 0)
    assert over['lessons'] == []


def test_a_topic_posted_ahead_leaves_the_absent_teacher_without_a_substitute_once_the_lesson_is_over(client, clock):
    ahead = TODAY + timedelta(days=7)
    publish_topic(client, ahead)
    stored(absences=[TeacherAbsence(id='a', name='Командировка', teacherId='t2', start=ahead, end=ahead)])
    clock(ahead, '12:00')
    gone = figures(client, 't2', ahead, ahead)
    assert (gone['taught'], gone['minutes'], gone['confirmed'], gone['uncovered'], gone['missed'], gone['missedAbsent']) == (0, 0, 0, 1, 1, 1)
    assert gone['lessons'][0]['outcome'] == 'uncovered' and gone['lessons'][0]['confirmed'] is False
    # The lesson is given and written up after the bell: that publication counts, from then on.
    publish(client, ahead)
    held = figures(client, 't2', ahead, ahead)
    assert (held['taught'], held['minutes'], held['confirmed'], held['uncovered'], held['missed'], held['missedAbsent']) == (1, 40, 1, 0, 0, 0)


def test_a_topic_posted_ahead_for_a_lesson_that_left_the_timetable_counts_nothing(client, clock):
    ahead = TODAY + timedelta(days=7)
    publish_topic(client, ahead)
    stored(placements=[p for p in api.read('demo').placements if p.id != 'eng'])
    clock(ahead, '12:00')
    t2 = figures(client, 't2', ahead, ahead)
    # Neither a planned lesson nor a journal-only one: the publication was made before the lesson could have taken place.
    assert (t2['taught'], t2['journalOnly'], t2['confirmed'], t2['differs'], t2['planned'], t2['minutes']) == (0, 0, 0, 0, 0, 0)


def test_a_topic_posted_before_the_bell_does_not_confirm_the_lesson_after_it(client, clock):
    clock(MON, '07:00')
    publish_topic(client, MON)
    early = figures(client, 't2', MON, MON)
    assert (early['upcoming'], early['taught'], early['confirmed']) == (1, 0, 0)
    clock(MON, '08:20')
    started = figures(client, 't2', MON, MON)
    assert (started['upcoming'], started['taught'], started['confirmed'], started['plannedToDate']) == (1, 0, 0, 0)
    clock(MON, '09:30')
    over = figures(client, 't2', MON, MON)
    # Counted by the schedule, as if nothing had been published: the early topic confirms nothing, in the journal or against it.
    assert (over['upcoming'], over['taught'], over['own'], over['journalOnly'], over['confirmed'], over['differs'], over['plannedToDate']) == (0, 1, 1, 0, 0, 0, 1)
    assert over['lessons'][0]['confirmed'] is False
    # Written up after the bell, the lesson is confirmed.
    publish(client)
    again = figures(client, 't2', MON, MON)
    assert (again['taught'], again['confirmed'], again['own'], again['differs']) == (1, 1, 1, 0)


def test_a_publication_counts_from_the_minute_the_lesson_starts(client, clock):
    clock(MON, '08:19')
    publish(client)
    clock(MON, '08:30')
    early = figures(client, 't2', MON, MON)
    assert (early['upcoming'], early['taught'], early['confirmed']) == (1, 0, 0)
    clock(MON, '08:20')
    publish(client)
    clock(MON, '08:30')
    started = figures(client, 't2', MON, MON)
    assert (started['upcoming'], started['taught'], started['confirmed']) == (0, 1, 1)


def test_a_closure_added_after_the_journal_was_published_keeps_the_taught_lesson(client, clock):
    publish(client)
    stored(events=[Event(id='closed', name='Каникулы', kind='holiday', start=MON, end=MON, effect='cancel')])
    t2 = figures(client, 't2', MON, MON)
    # The publication proves the lesson was given, though the calendar now closes the day: not planned, not missed, and flagged.
    assert (t2['taught'], t2['differs'], t2['planned'], t2['missed'], t2['confirmed']) == (1, 1, 0, 0, 1)


def test_an_absent_teacher_without_a_substitute_has_not_taught(client, clock):
    stored(absences=[TeacherAbsence(id='a', name='Болезнь', teacherId='t2', start=MON, end=MON)])
    gone = figures(client, 't2', MON, MON)
    assert (gone['taught'], gone['uncovered'], gone['missed'], gone['missedAbsent']) == (0, 1, 1, 1)
    assert gone['lessons'][0]['outcome'] == 'uncovered' and gone['lessons'][0]['absent'] is True
    publish(client)
    held = figures(client, 't2', MON, MON)
    assert (held['taught'], held['uncovered'], held['missed']) == (1, 0, 0)


def test_a_merged_group_adds_no_second_lesson_for_the_same_time(client, clock):
    s = api.read('demo')
    s.placements.append(Placement(id='hist', workloadId='w3', day=0, period=1, roomId='r2'))
    s.datedLessons = [change(placement='hist', changeType='merge', mergeWith=f'{MON}:eng', teacherIds=['t2'], roomIds=['r0'])]
    api.save('demo', State.model_validate(s.model_dump()))
    host, merged = figures(client, 't2', MON, MON), figures(client, 't3', MON, MON)
    assert (host['taught'], host['merged'], host['minutes']) == (1, 1, 40)
    assert host['byType']['merge'] == 1 and host['substitutions'] == 0
    assert (merged['planned'], merged['missed'], merged['taught']) == (1, 1, 0)


def test_a_merge_whose_host_changed_is_an_ordinary_substitution(client, clock):
    s = api.read('demo')
    s.placements.append(Placement(id='hist', workloadId='w3', day=0, period=1, roomId='r2'))
    s.datedLessons = [change(placement='hist', changeType='merge', mergeWith=f'{MON}:eng', teacherIds=['t2'], roomIds=['r0']), change(teacherIds=['t3'], changeType='substitution')]
    api.save('demo', State.model_validate(s.model_dump()))
    t2 = figures(client, 't2', MON, MON)
    # The host no longer has this teacher, so nothing is merged: the lesson is a replacement, counted once among them.
    assert (t2['taught'], t2['merged'], t2['substitutions'], t2['byType']['substitution'], t2['byType']['merge']) == (1, 0, 1, 1, 0)
    assert sum(t2['byType'].values()) == t2['substitutions']


def test_weeks_add_up_to_the_period_and_are_clipped_to_it(client, clock):
    stored(datedLessons=[change(date(2026, 8, 24), teacherIds=['t3'], changeType='substitution')])
    start, end = date(2026, 8, 19), date(2026, 9, 1)
    teacher = figures(client, 't2', start, end)
    weeks = teacher['weeks']
    assert [(w['start'], w['end']) for w in weeks] == [('2026-08-19', '2026-08-23'), ('2026-08-24', '2026-08-30'), ('2026-08-31', '2026-09-01')]
    for key in ['taught', 'minutes', 'planned', 'missed', 'own']:
        assert sum(w[key] for w in weeks) == teacher[key]
    assert weeks[0]['taught'] == 0 and weeks[1]['missed'] == 1 and weeks[2]['taught'] == 1
    assert len(report(client, start, start).json()['teachers']) == 0
    single = figures(client, 't2', MON, MON)
    assert len(single['weeks']) == 1


def test_the_school_report_lists_only_teachers_with_figures_and_has_no_lessons(client, clock):
    response = report(client)
    assert response.status_code == 200
    body = response.json()
    assert {t['teacherId'] for t in body['teachers']} == {'t0', 't2'}
    assert all('lessons' not in t and 'weeks' not in t for t in body['teachers'])
    assert body['totals']['planned'] == sum(t['planned'] for t in body['teachers'])
    assert body['source'] == 'dated_schedule_and_published_journals' and body['asOf'] == '2026-09-14 12:00'
    one = figures(client, 't1')
    assert one['planned'] == 0 and one['weeks'] and one['lessons'] == []
    assert report(client, teacherId='missing').status_code == 404


def test_validation_and_clipping(client, clock):
    assert report(client, MON, MON + timedelta(days=366)).status_code == 422
    assert report(client, MON + timedelta(days=366), MON + timedelta(days=366)).status_code == 422
    assert report(client, MON, MON - timedelta(days=1)).status_code == 422
    assert report(client, date(2025, 1, 1), date(2025, 6, 1)).status_code == 422
    clipped = report(client, date(2026, 8, 1), date(2026, 8, 20)).json()
    assert (clipped['start'], clipped['end']) == ('2026-08-17', '2026-08-20')
    assert report(client, MON, MON + timedelta(days=365)).status_code == 200


def test_workload_access_follows_the_account(client, clock):
    enable(client)
    create(client, 'teacher'); create(client, 'editor'); create(client, 'staff'); create(client, 'student'); create(client, 'parent')
    teacher = signed('teacher')
    for login in ['editor', 'staff', 'student', 'parent']:
        other = signed(login)
        for path in ['/options', '', '/export']:
            response = other.get('/api/staff-time/workload' + path, params=dict(workspace='demo', start=str(MON), end=str(MON)))
            assert response.status_code == 403, (login, path)
    assert client.get('/api/staff-time/workload/options?workspace=demo').json()['canSeeAll'] is True
    options = teacher.get('/api/staff-time/workload/options?workspace=demo').json()
    assert options['canSeeAll'] is False and options['teachers'] == [dict(id='t2', name='Учитель английского')] and options['today'] == '2026-09-14'
    own = report(teacher, MON, MON + timedelta(days=13))
    assert own.status_code == 200 and [t['teacherId'] for t in own.json()['teachers']] == ['t2']
    assert 'Учитель математики' not in own.text and own.json()['totals']['planned'] == own.json()['teachers'][0]['planned']
    # A colleague is as good as missing, in the report and the export alike.
    for path in ['', '/export']:
        response = teacher.get('/api/staff-time/workload' + path, params=dict(workspace='demo', start=str(MON), end=str(MON), teacherId='t0'))
        assert response.status_code == 404 and 'Учитель математики' not in response.text
    assert teacher.get('/api/staff-time/workload/export', params=dict(workspace='demo', start=str(MON), end=str(MON))).text.count('Учитель математики') == 0
    # A workspace without the teacher's assignment has nothing for them.
    assert teacher.get('/api/staff-time/workload/options?workspace=school').status_code == 403
    users = {u['login']: u for u in client.get('/api/access/users').json()}
    moved = {k: v for k, v in users['teacher'].items() if k != 'id'} | dict(grants=[dict(workspace='demo', view='teacher', entityIds=['t1'])])
    assert client.put('/api/access/users/' + users['teacher']['id'], json=moved).status_code == 200
    again = signed('teacher')
    now = report(again, MON, MON + timedelta(days=13))
    assert now.status_code == 200 and [t['teacherId'] for t in now.json()['teachers']] == ['t1']
    assert 'Учитель английского' not in now.text
    assert report(again, teacherId='t2').status_code == 404


def test_a_teacher_with_two_entities_sees_both(client, clock):
    enable(client)
    create(client, 'teacher', grants=[dict(workspace='demo', view='teacher', entityIds=['t2', 't3'])])
    both = report(signed('teacher'), MON, MON + timedelta(days=6))
    assert [t['teacherId'] for t in both.json()['teachers']] == ['t2', 't3']


def test_csv_views_match_the_report_protect_formulas_and_are_audited(client, clock):
    enable(client)
    stored(teachers=[t.model_copy(update=dict(name='=SUM(1)')) if t.id == 't2' else t for t in api.read('demo').teachers],
           datedLessons=[change(date(2026, 8, 24), teacherIds=['t3'], changeType='substitution')])
    start, end = MON, date(2026, 8, 31)
    params = dict(workspace='demo', start=str(start), end=str(end))
    school = client.get('/api/staff-time/workload', params=params).json()
    export = lambda view, **extra: client.get('/api/staff-time/workload/export', params=params | dict(view=view) | extra)
    teachers = export('teachers')
    assert teachers.status_code == 200 and teachers.text.startswith('﻿') and 'attachment' in teachers.headers['content-disposition']
    assert '=SUM' not in teachers.headers['content-disposition'] and 'teacher-workload-2026-08-17-2026-08-31-teachers.csv' in teachers.headers['content-disposition']
    table = rows_of(teachers.text)
    assert len(table) == 1 + len(school['teachers']) + 1 and table[-1][0] == 'Итого'
    assert any(row[0] == "'=SUM(1)" for row in table)
    header = table[0]
    taught = header.index('Проведено уроков')
    assert [int(r[taught]) for r in table[1:-1]] == [t['taught'] for t in school['teachers']]
    assert int(table[-1][taught]) == school['totals']['taught']
    weeks = rows_of(export('weeks').text)
    assert len(weeks) == 1 + len(school['teachers']) * 3 and weeks[0][:3] == ['Учитель', 'Неделя с', 'Неделя по']
    one = client.get('/api/staff-time/workload', params=params | dict(teacherId='t3')).json()['teachers'][0]
    lessons = rows_of(export('lessons', teacherId='t3').text)
    assert len(lessons) == 1 + len(one['lessons']) and lessons[1][7] == 'Проведён' and lessons[1][9] == 'Нет'
    everyone = rows_of(export('lessons').text)
    assert len(everyone) > len(lessons)
    assert export('summary').status_code == 422
    audited = lambda: [json.loads(e['detail']) for e in client.get('/api/access/audit').json() if e['action'] == 'staff-time.workload_exported']
    assert sorted((d['view'], d['teacherId']) for d in audited()) == [('lessons', ''), ('lessons', 't3'), ('teachers', ''), ('weeks', '')]
    assert all(d['start'] == '2026-08-17' and d['end'] == '2026-08-31' for d in audited())
    # Reading the report alone is not audited.
    client.get('/api/staff-time/workload', params=params)
    assert len(audited()) == 4


def test_a_shortfall_against_the_schedule_is_a_number_in_the_csv(client, clock):
    stored(datedLessons=[change(teacherIds=['t3'], changeType='substitution')])
    params = dict(workspace='demo', start=str(MON), end=str(MON + timedelta(days=6)))
    assert figures(client, 't2')['delta'] == -1
    for view in ['teachers', 'weeks']:
        table = rows_of(client.get('/api/staff-time/workload/export', params=params | dict(view=view)).text)
        mine = next(r for r in table[1:] if r[0] == 'Учитель английского')
        # A negative figure must stay a plain number, not text with a quote in front of it.
        assert mine[table[0].index('Разница с расписанием')] == '-1'


def test_the_breakdown_of_substitutions_in_the_csv_adds_up_to_them(client, clock):
    s = api.read('demo')
    s.placements.append(Placement(id='hist', workloadId='w3', day=0, period=1, roomId='r2'))
    s.datedLessons = [change(placement='hist', changeType='merge', mergeWith=f'{MON}:eng', teacherIds=['t2'], roomIds=['r0']),
                      change(date(2026, 8, 24), teacherIds=['t2'], placement='math', changeType='self_study')]
    api.save('demo', State.model_validate(s.model_dump()))
    params = dict(workspace='demo', start=str(MON), end=str(date(2026, 8, 31)))
    table = rows_of(client.get('/api/staff-time/workload/export', params=params).text)
    header, host = table[0], next(r for r in table[1:] if r[0] == 'Учитель английского')
    cell = lambda name: int(host[header.index(name)])
    parts = [name for name in header if name.startswith('Из них: ')]
    assert 'Из них: объединение' not in parts and cell('Присоединено при объединении') == 1
    assert cell('Замены') == sum(cell(name) for name in parts) == 1


def test_local_mode_without_accounts_is_the_administrator(client, clock):
    assert report(client).status_code == 200
    assert client.get('/api/staff-time/workload/export', params=dict(workspace='demo', start=str(MON), end=str(MON))).status_code == 200


def test_a_publication_by_a_teacher_outside_every_version_gets_a_neutral_name(client, clock):
    publish(client)
    s = api.read('demo')
    s.teachers = [t for t in s.teachers if t.id != 't2']
    s.workloads = [w.model_copy(update=dict(teacherId='t3')) if w.teacherId == 't2' else w for w in s.workloads]
    api.save('demo', State.model_validate(s.model_dump()))
    body = report(client, MON, MON).json()
    assert 'Учитель вне текущего состава' in {t['name'] for t in body['teachers']}


def test_a_teacher_renamed_later_keeps_the_current_name_in_every_period(client, clock):
    s = api.read('demo')
    s.teachers = [t.model_copy(update=dict(name='Новая фамилия')) if t.id == 't2' else t for t in s.teachers]
    api.save('demo', s, effective=date(2026, 9, 7))
    # A period wholly before the change is still reported under the name the teacher has now.
    assert figures(client, 't2', MON, date(2026, 8, 31))['name'] == 'Новая фамилия'
    assert figures(client, 't2', date(2026, 9, 7), date(2026, 9, 14))['name'] == 'Новая фамилия'


def test_a_teacher_who_left_can_still_be_opened_for_the_period_he_taught(client, clock):
    s = api.read('demo')
    s.teachers = [t for t in s.teachers if t.id != 't2']
    s.workloads = [w.model_copy(update=dict(teacherId='t3')) if w.teacherId == 't2' else w for w in s.workloads]
    api.save('demo', State.model_validate(s.model_dump()), effective=date(2026, 9, 7))
    august = (MON, date(2026, 8, 31))
    assert 't2' in {t['teacherId'] for t in report(client, *august).json()['teachers']}
    # The row of the school table leads somewhere: the weeks, the lessons and the export of that teacher.
    gone = figures(client, 't2', *august)
    assert gone['taught'] == 3 and len(gone['weeks']) == 3 and len(gone['lessons']) == 3
    export = client.get('/api/staff-time/workload/export', params=dict(workspace='demo', start=str(MON), end='2026-08-31', teacherId='t2', view='lessons'))
    assert export.status_code == 200 and len(rows_of(export.text)) == 4
    # A teacher who is neither on the roster nor in the period, or never existed, is as good as missing.
    assert report(client, date(2026, 9, 7), date(2026, 9, 14), teacherId='t2').status_code == 404
    assert report(client, *august, teacherId='nobody').status_code == 404


def test_a_moved_lesson_counts_once_on_the_day_it_was_given(client, clock):
    wednesday = date(2026, 8, 19)
    stored(datedLessons=[DatedLesson(id='moved', name='Перенос', kind='change', placementId='eng', sourceDate=MON, onDate=wednesday, changeType='move')])
    before = figures(client, 't2', MON, wednesday)
    assert (before['planned'], before['moved'], before['taught'], before['missed'], before['own']) == (1, 1, 1, 0, 1)
    publish(client, wednesday)
    after = figures(client, 't2', MON, wednesday)
    # The publication belongs to the new date: it confirms that lesson, and the source day is not a disagreement.
    assert (after['planned'], after['moved'], after['taught'], after['confirmed'], after['differs']) == (1, 1, 1, 1, 0)


def test_the_published_time_is_the_time_taught(client, clock):
    publish(client)
    s = api.read('demo')
    profile = next(p for p in s.profiles if p.id == 'secondary')
    profile.dayPeriods = {'0': [p.model_copy(update=dict(start=f'{int(p.start[:2]) + 1:02d}{p.start[2:]}', end=f'{int(p.end[:2]) + 1:02d}{p.end[2:]}')) for p in profile.periods]}
    api.save('demo', State.model_validate(s.model_dump()))
    [taught] = figures(client, 't2', MON, MON)['lessons']
    assert (taught['start'], taught['end'], taught['minutes']) == ('08:20', '09:00', 40)


def test_a_publication_whose_lesson_left_the_timetable_still_counts_for_who_taught_it(client, clock):
    publish(client)
    stored(placements=[p for p in api.read('demo').placements if p.id != 'eng'])
    t2 = figures(client, 't2', MON, MON)
    assert (t2['taught'], t2['journalOnly'], t2['confirmed'], t2['differs'], t2['planned'], t2['minutes']) == (1, 1, 1, 1, 0, 40)


def test_a_changed_subject_does_not_count_the_published_time_twice(client, clock):
    publish(client)
    stored(datedLessons=[change(subjectId='sub3', changeType='substitution')])
    t2 = figures(client, 't2', MON, MON)
    assert (t2['taught'], t2['minutes'], t2['planned']) == (1, 40, 1)


def test_a_dated_change_of_subject_and_teacher_credits_only_the_substitute(client, clock):
    publish(client)
    stored(datedLessons=[change(subjectId='sub3', teacherIds=['t3'], changeType='substitution')])
    t2, t3 = figures(client, 't2', MON, MON), figures(client, 't3', MON, MON)
    # The new subject gives the lesson another journal key, but the published time is still one slot: it is not credited to both.
    assert (t2['taught'], t2['minutes'], t2['missed'], t2['planned']) == (0, 0, 1, 1)
    assert [x['outcome'] for x in t2['lessons']] == ['replaced']
    assert (t3['taught'], t3['minutes'], t3['substitutions']) == (1, 40, 1)
    assert report(client, MON, MON).json()['totals']['taught'] == 2


def test_an_undated_change_of_subject_and_teacher_keeps_the_published_lesson_with_who_taught_it(client, clock):
    publish(client)
    s = api.read('demo')
    s.workloads[0].teacherId, s.workloads[0].subjectId = 't3', 'sub3'
    api.save('demo', s)
    end = date(2026, 8, 31)
    t2, t3 = figures(client, 't2', MON, end), figures(client, 't3', MON, end)
    # The same result as when only the teacher changes: the publication outranks the template, and the new owner is replaced for that day.
    assert (t2['taught'], t2['journalOnly'], t2['confirmed'], t2['differs'], t2['planned']) == (1, 1, 1, 1, 0)
    assert (t3['taught'], t3['own'], t3['planned'], t3['missed']) == (2, 2, 3, 1)


def test_a_cancelled_lesson_that_was_published_is_not_taught_and_disagrees_with_the_journal(client, clock):
    publish(client)
    stored(datedLessons=[DatedLesson(id='cancel', name='Отмена', kind='cancel', placementId='eng', sourceDate=MON, onDate=MON)])
    t2 = figures(client, 't2', MON, MON)
    assert (t2['taught'], t2['cancelled'], t2['missed'], t2['differs'], t2['planned']) == (0, 1, 1, 1, 1)
    # The journal cannot confirm a lesson the schedule no longer holds.
    assert t2['confirmed'] == 0


def test_staff_meetings_are_not_lessons(client, clock):
    s = api.read('demo')
    s.groups.append(Group(id='g-staff', name='Методобъединение', kind='staff'))
    s.workloads.append(next(w for w in s.workloads if w.id == 'w0').model_copy(update=dict(id='w-staff', groupId='g-staff')))
    s.placements.append(Placement(id='meeting', workloadId='w-staff', day=0, period=3, roomId='r2'))
    api.save('demo', State.model_validate(s.model_dump()))
    assert figures(client, 't2', MON, MON)['planned'] == 1


def month_totals(c, month):
    response = c.get(f'/api/calendar/substitution-report?workspace=demo&month={month}')
    assert response.status_code == 200, response.text
    return {t['teacherId']: t for t in response.json()['teachers']}


# What both reports count; the month totals call taught «held».
SHARED = ['planned', 'own', 'substitutions', 'extra', 'missed', 'missedAbsent', 'cancelled', 'moved', 'merged', 'upcoming', 'uncovered']


def test_the_month_totals_and_the_workload_report_agree_when_nothing_is_published(client, clock):
    s = api.read('demo')
    s.placements.append(Placement(id='hist', workloadId='w3', day=0, period=1, roomId='r2'))
    s.datedLessons = [change(date(2026, 8, 24), teacherIds=['t3'], changeType='substitution'),
                      DatedLesson(id='cancel', name='Отмена', kind='cancel', placementId='eng', sourceDate=date(2026, 8, 31), onDate=date(2026, 8, 31)),
                      DatedLesson(id='moved', name='Перенос', kind='change', placementId='math', sourceDate=date(2026, 8, 24), onDate=date(2026, 8, 26), changeType='move'),
                      change(placement='hist', changeType='merge', mergeWith=f'{MON}:eng', teacherIds=['t2'], roomIds=['r0']),
                      DatedLesson(id='extra', name='Разовый', kind='add', changeType='extra', onDate=date(2026, 8, 19), groupId=s.workloads[0].groupId, profileId='secondary',
                                  subjectId=s.workloads[0].subjectId, startTime='14:00', endTime='14:40', teacherIds=['t2'], roomIds=['r0'])]
    # One absence is over, one is still ahead of the clock: both reports treat them alike.
    s.absences = [TeacherAbsence(id='past', name='Болезнь', teacherId='t0', start=date(2026, 9, 7), end=date(2026, 9, 7)),
                  TeacherAbsence(id='ahead', name='Командировка', teacherId='t2', start=date(2026, 9, 21), end=date(2026, 9, 21))]
    api.save('demo', State.model_validate(s.model_dump()))
    seen = dict.fromkeys(SHARED, 0)
    for month, first, last in [('2026-08', MON, date(2026, 8, 31)), ('2026-09', date(2026, 9, 1), date(2026, 9, 30))]:
        totals, body = month_totals(client, month), report(client, first, last).json()
        assert {t['teacherId'] for t in body['teachers']} == set(totals)
        for t in body['teachers']:
            other = totals[t['teacherId']]
            assert {k: t[k] for k in SHARED} == {k: other[k] for k in SHARED} and t['taught'] == other['held']
            for k in SHARED: seen[k] += t[k]
    # The comparison is not vacuous: every kind of fact is present.
    assert [k for k in SHARED if not seen[k]] == [], seen


def test_the_two_reports_differ_for_a_lesson_published_during_an_absence(client, clock):
    stored(absences=[TeacherAbsence(id='a', name='Болезнь', teacherId='t2', start=MON, end=MON)])
    publish(client)
    month, mine = month_totals(client, '2026-08')['t2'], figures(client, 't2', MON, date(2026, 8, 31))
    # Only the workload report reads the journal: it finds the lesson taught, the month totals still call it left without a substitute.
    assert (month['uncovered'], mine['uncovered'], mine['differs']) == (1, 0, 0)
    assert mine['taught'] == month['held'] + 1 and mine['confirmed'] == 1


def test_the_two_reports_differ_for_a_lesson_published_before_it_ends(client, clock):
    clock(MON, '08:30')
    publish(client)
    month, mine = month_totals(client, '2026-08')['t2'], figures(client, 't2', MON, date(2026, 8, 31))
    assert (mine['taught'], mine['confirmed']) == (month['held'] + 1, 1) and month['upcoming'] == mine['upcoming'] + 1


def test_a_lesson_in_an_entered_absence_is_without_a_substitute_even_before_its_date(client, clock):
    ahead = TODAY + timedelta(days=7)
    stored(absences=[TeacherAbsence(id='a', name='Командировка', teacherId='t2', start=ahead, end=ahead)])
    t2 = figures(client, 't2', ahead, ahead)
    # The lesson has not been due yet, but nobody is going to teach it: it is reported at once, not when its day comes.
    assert (t2['planned'], t2['plannedToDate'], t2['uncovered'], t2['missed'], t2['missedAbsent'], t2['upcoming'], t2['taught']) == (1, 0, 1, 1, 1, 0, 0)
    assert t2['lessons'][0]['outcome'] == 'uncovered'
    assert month_totals(client, '2026-09')['t2']['uncovered'] == t2['uncovered']
    # A substitute turns it into a lesson that is merely still to come.
    stored(datedLessons=[change(ahead, teacherIds=['t3'], changeType='substitution')])
    t2, t3 = figures(client, 't2', ahead, ahead), figures(client, 't3', ahead, ahead)
    assert (t2['uncovered'], t2['missed'], t3['upcoming'], t3['uncovered']) == (0, 1, 1, 0)
