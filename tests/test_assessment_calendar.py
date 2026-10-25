"""Assessment calendar: real lessons only, class load limits, СОЧ windows, family view and history."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path
from uuid import uuid4
import pytest, re
from fastapi.testclient import TestClient
from backend import api
from backend.assessment_calendar import PairLessons, audience, class_load, touches
from backend.calendar_ops import resolve_day
from backend.journal import key_for
from backend.models import DatedLesson, Event, Group, Placement, State
from backend.seed import initial_state
from test_access import client, enable, create, signed, HEADERS

BASE = '/api/assessment-calendar/'
D17, D24, D31 = '2026-08-17', '2026-08-24', '2026-08-31'


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    monkeypatch.setattr('backend.assessment_calendar_routes.today', lambda: date(2026, 8, 10))


def url(part, **query):
    params = '&'.join(f'{k}={v}' for k, v in query.items())
    return BASE + part + '?workspace=demo' + ('&' + params if params else '')
def ok(response):
    assert response.status_code == 200, response.text
    return response.json()
def book(c, group, subject, day, lesson, type='sor', **extra):
    return c.post(url('bookings'), json=dict(requestId=str(uuid4()), groupId=group, subjectId=subject, day=day, lessonId=lesson, typeId=type, topic='Дроби') | extra)
def english(c, day=D17, **extra):
    return book(c, 'g-a', 'sub2', day, f'{day}:eng', **extra)
def teacher(client, login, entity, name='Учитель'):
    return create(client, 'teacher', login=login, name=name, grants=[dict(workspace='demo', view='teacher', entityIds=[entity])])
def guardian(client, login, student, role='parent'):
    return create(client, role, login=login, grants=[dict(workspace='demo', view='student', entityIds=[student])])
def listing(c, start=D17, end='2026-09-20', **query):
    return ok(c.get(url('bookings', start=start, end=end, **query)))['items']
def family(c, student='s1', start=D17, end='2026-09-20'):
    return c.get(url('family', studentId=student, start=start, end=end))
def configure(client, who=None, **changes):
    options = ok(client.get(url('options')))
    body = dict(version=options['configVersion'] or 0, reason='Решение завуча', types=options['types'] or [dict(id='sor', title='СОР', short='СОР')], maxPerDay=options['maxPerDay'], maxPerWeek=options['maxPerWeek'],
                windows=[{k: v for k, v in w.items() if k != 'period'} for w in options['windows']], coordinatorIds=options['coordinatorIds']) | changes
    return (who or client).put(url('config'), json=body)
def change(edit):
    state = api.read('demo')
    edit(state)
    api.save('demo', state)
def history(c, id): return [h['action'] for h in ok(c.get(url(f'bookings/{id}/history')))['items']]
def unread(c): return c.get('/api/notifications/inbox').json()


def demo_state(**changes):
    s = initial_state('demo')
    s.events = []
    s.placements = [Placement(id='eng', workloadId='w0', day=0, period=1, roomId='r0'), Placement(id='math', workloadId='w4', day=0, period=2, roomId='r1')]
    for key, value in changes.items(): setattr(s, key, value)
    return State.model_validate(s.model_dump())


def test_group_state_matches_full_resolution():
    d = date.fromisoformat
    s = demo_state(datedLessons=[
        DatedLesson(id='cx', name='Отмена', kind='cancel', onDate=d(D24), sourceDate=d(D24), placementId='eng'),
        DatedLesson(id='mv', name='Перенос', kind='change', onDate=d('2026-09-01'), sourceDate=d(D31), placementId='eng', startTime='14:00', endTime='14:40'),
        DatedLesson(id='sub', name='Замена', kind='change', onDate=d('2026-09-07'), sourceDate=d('2026-09-07'), placementId='eng', teacherIds=['t3']),
        DatedLesson(id='add', name='Разовый', kind='add', onDate=d('2026-09-09'), groupId='g-a', profileId='secondary', subjectId='sub2', startTime='15:00', endTime='15:40')],
        events=[Event(id='closed', name='Закрыт', start=d('2026-09-14'), end=d('2026-09-14'), kind='extra', effect='cancel', classIds=['c-demo-a'])])
    pl, seen = PairLessons(s), set()
    for offset in range(29):
        day = date.fromordinal(d(D17).toordinal() + offset)
        full = [r for r in resolve_day(s, day)[0] if r['activity'] == 'lesson']
        for group, subject in [('g-a', 'sub2'), ('g-math', 'sub0')]:
            expected = [(r['id'], r['status'], r['start'], r['teacherIds'], r['classIds'], r['studentIds'], key_for(r)) for r in full if r['groupId'] == group and r['subjectId'] == subject]
            got = [(r['id'], r['status'], r['start'], r['teacherIds'], r['classIds'], r['studentIds'], key_for(r)) for r in pl.rows(group, subject, day)]
            assert got == expected, (group, day)
            seen.update(r[1] for r in got)
    assert {'scheduled', 'cancelled', 'changed', 'added'} <= seen


def test_class_load_counts_pupils_not_subgroups():
    one = dict(class_ids=['A'], student_ids=['s1'], whole_ids=[])
    two = dict(class_ids=['A'], student_ids=['s2'], whole_ids=[])
    whole = dict(class_ids=['A'], student_ids=['s1', 's2'], whole_ids=['A'])
    assert class_load([one, two], 'A', {'s1', 's2'}) == 1
    assert class_load([one, two, whole], 'A', {'s1', 's2'}) == 2
    assert class_load([one, one], 'A', {'s1', 's2'}) == 2
    assert class_load([one, two], 'A', {'s1', 's2'}, {'s1'}) == 1 and class_load([two], 'A', {'s1', 's2'}, {'s1'}) == 0
    assert [touches(b, 'A', {'s1', 's2'}, {'s1'}) for b in [one, two, whole]] == [True, False, True]
    assert touches(one, 'B', {'s1'}, None) is False and touches(two, 'A', {'s1', 's2'}, None) is True


def test_teacher_books_only_current_pair_on_a_real_lesson(client, monkeypatch):
    enable(client); create(client, 'teacher'); t = signed('teacher')
    options = ok(t.get(url('options')))
    assert {(p['groupId'], p['subjectId']) for p in options['pairs']} == {('g-a', 'sub2'), ('g-d', 'sub2')} and options['canBook'] and not options['family']
    slots = ok(t.get(url('slots', groupId='g-a', subjectId='sub2', day=D17)))
    assert [(s['lessonId'], s['start']) for s in slots['slots']] == [('2026-08-17:eng', '08:20')] and not slots['past']
    assert ok(t.get(url('slots', groupId='g-a', subjectId='sub2', day='2026-08-18')))['slots'] == []
    assert book(t, 'g-a', 'sub2', '2026-08-18', '2026-08-18:eng').status_code == 422
    assert book(t, 'g-math', 'sub0', D17, '2026-08-17:math').status_code == 403
    assert t.get(url('slots', groupId='g-math', subjectId='sub0', day=D17)).status_code == 403
    body = dict(requestId=str(uuid4()), groupId='g-a', subjectId='sub2', day=D17, lessonId='2026-08-17:eng', typeId='sor', topic='Дроби', publish=False)
    first = ok(t.post(url('bookings'), json=body))
    assert first['status'] == 'planned' and first['group'] == '10A' and first['subject'] == 'Английский язык' and first['start'] == '08:20' and first['canChange']
    assert ok(t.post(url('bookings'), json=body)) == first
    assert t.post(url('bookings'), json=body | dict(topic='Другая тема')).status_code == 409
    duplicate = english(t)
    assert duplicate.status_code == 409 and 'уже назначена' in duplicate.json()['detail']
    assert len(listing(t)) == 1
    monkeypatch.setattr('backend.assessment_calendar_routes.today', lambda: date(2026, 8, 20))
    assert english(t).status_code == 422
    monkeypatch.setattr('backend.assessment_calendar_routes.today', lambda: date(2026, 8, 10))
    assert book(client, 'g-math', 'sub0', D17, '2026-08-17:math').status_code == 200
    change(lambda s: [setattr(w, 'teacherId', 't0') for w in s.workloads if w.id == 'w0'])
    t = signed('teacher')
    assert {(p['groupId'], p['subjectId']) for p in ok(t.get(url('options')))['pairs']} == {('g-d', 'sub2')}
    assert t.post(url(f"bookings/{first['id']}/move"), json=dict(requestId=str(uuid4()), version=1, day=D24, lessonId='2026-08-24:eng', reason='Перенос')).status_code == 404
    assert t.get(url('slots', groupId='g-a', subjectId='sub2', day=D17)).status_code == 403 and english(t, D24).status_code == 403
    assert first['id'] not in {b['id'] for b in listing(t)}


def test_cancelled_moved_substituted_and_extra_lessons(client):
    d = date.fromisoformat
    enable(client); create(client, 'teacher'); teacher(client, 'sub', 't3'); t, other = signed('teacher'), signed('sub')
    change(lambda s: (setattr(s, 'datedLessons', [
        DatedLesson(id='cx', name='Отмена урока', kind='cancel', onDate=d(D24), sourceDate=d(D24), placementId='eng'),
        DatedLesson(id='mv', name='Перенос урока', kind='change', onDate=d('2026-09-01'), sourceDate=d(D31), placementId='eng', startTime='14:00', endTime='14:40'),
        DatedLesson(id='sub', name='Замена учителя', kind='change', onDate=d('2026-09-07'), sourceDate=d('2026-09-07'), placementId='eng', teacherIds=['t3']),
        DatedLesson(id='add', name='Разовый урок', kind='add', onDate=d('2026-09-09'), groupId='g-a', profileId='secondary', subjectId='sub2', startTime='15:00', endTime='15:40')]),
        setattr(s, 'events', [Event(id='closed', name='Класс на экскурсии', start=d('2026-09-14'), end=d('2026-09-14'), kind='extra', effect='cancel', classIds=['c-demo-a'])])))
    def slots(day): return ok(t.get(url('slots', groupId='g-a', subjectId='sub2', day=day)))['slots']
    assert slots(D24) == [] and english(t, D24).status_code == 422
    assert slots(D31) == [] and english(t, D31).status_code == 422
    moved = slots('2026-09-01')
    assert [(s['lessonId'], s['start']) for s in moved] == [('2026-08-31:eng', '14:00')]
    assert book(t, 'g-a', 'sub2', '2026-09-01', '2026-08-31:eng').status_code == 200
    substitute = slots('2026-09-07')
    assert substitute[0]['substitute'] and substitute[0]['teachers'] == 'Учитель истории'
    assert english(t, '2026-09-07').status_code == 200
    assert other.get(url('slots', groupId='g-a', subjectId='sub2', day='2026-09-07')).status_code == 403
    assert slots('2026-09-14') == [] and english(t, '2026-09-14').status_code == 422
    assert [s['lessonId'] for s in slots('2026-09-09')] == ['extra:add']
    assert book(t, 'g-a', 'sub2', '2026-09-09', 'extra:add').status_code == 200
    first = ok(english(t))
    assert first['slotValid'] and first['slotMessage'] == ''
    change(lambda s: setattr(s, 'datedLessons', s.datedLessons + [DatedLesson(id='late', name='Урок отменён директором', kind='cancel', onDate=d(D17), sourceDate=d(D17), placementId='eng')]))
    flagged = next(b for b in listing(t) if b['id'] == first['id'])
    assert not flagged['slotValid'] and 'отменён директором' in flagged['slotMessage']


def test_limits_override_and_parallel_subgroups(client):
    enable(client); create(client, 'teacher', name='Учитель английского'); teacher(client, 'mathteacher', 't0', 'Учитель математики'); teacher(client, 'physteacher', 't1', 'Учитель физики')
    t, math, phys = signed('teacher'), signed('mathteacher'), signed('physteacher')
    change(lambda s: s.placements.append(Placement(id='phys', workloadId='w5', day=0, period=3)))
    ok(configure(client, maxPerDay=1, maxPerWeek=2))
    ok(english(t))
    refused = book(math, 'g-math', 'sub0', D17, '2026-08-17:math')
    assert refused.status_code == 409 and '10A' in refused.json()['detail'] and 'Английский язык' in refused.json()['detail'] and 'Администратор может' in refused.json()['detail']
    assert book(math, 'g-math', 'sub0', D17, '2026-08-17:math', overrideReason='Очень надо').status_code == 403
    slots = ok(client.get(url('slots', groupId='g-math', subjectId='sub0', day=D17, typeId='sor')))
    assert slots['issues'] and slots['canOverride'] and ok(math.get(url('slots', groupId='g-math', subjectId='sub0', day=D17, typeId='sor')))['canOverride'] is False
    assert 'Укажите причину' in book(client, 'g-math', 'sub0', D17, '2026-08-17:math').json()['detail']
    forced = ok(book(client, 'g-math', 'sub0', D17, '2026-08-17:math', overrideReason='Решение завуча'))
    assert forced['override'] == 'Решение завуча' and forced['overrideBy'] == 'Администратор'
    first = ok(client.get(url(f"bookings/{forced['id']}/history")))['items'][0]
    assert first['action'] == 'created' and first['reason'] == 'Решение завуча'
    assert any(e['action'] == 'assessment.override' for e in client.get('/api/access/audit').json())
    # Parallel subgroups of 10A and 10D write on the same day without blocking each other.
    ok(book(math, 'g-math', 'sub0', D24, '2026-08-24:math'))
    assert book(phys, 'g-core', 'sub1', D24, '2026-08-24:phys').status_code == 200
    cell = next(r for r in ok(client.get(url('load', start=D24, end='2026-09-06')))['rows'] if r['name'] == '10A')['cells'][D24]
    assert cell['load'] == 1 and len(cell['items']) == 2
    # A work whose type does not count never blocks the day.
    options = ok(client.get(url('options')))
    ok(configure(client, types=options['types'] + [dict(id='quiz', title='Самостоятельная', short='С/р', category='other', counted=False, window=False, active=True)]))
    assert book(t, 'g-a', 'sub2', D24, '2026-08-24:eng', type='quiz').status_code == 200
    assert english(t, '2026-08-31').status_code == 200
    # Week limit: an extra lesson on Wednesday of the busy week is refused, the next Monday is free.
    change(lambda s: s.datedLessons.append(DatedLesson(id='add', name='Разовый', kind='add', onDate=date(2026, 9, 9), groupId='g-a', profileId='secondary', subjectId='sub2', startTime='15:00', endTime='15:40')))
    change(lambda s: s.datedLessons.append(DatedLesson(id='add2', name='Разовый', kind='add', onDate=date(2026, 9, 14), groupId='g-a', profileId='secondary', subjectId='sub2', startTime='15:00', endTime='15:40')))
    ok(configure(client, maxPerDay=None, maxPerWeek=1))
    week = book(t, 'g-a', 'sub2', '2026-09-09', 'extra:add')
    assert week.status_code == 200
    again = book(t, 'g-a', 'sub2', '2026-09-09', 'extra:add')
    assert again.status_code == 409
    blocked = book(math, 'g-math', 'sub0', '2026-09-07', '2026-09-07:math')
    assert blocked.status_code == 409 and 'на этой неделе' in blocked.json()['detail']
    assert book(math, 'g-math', 'sub0', '2026-09-14', '2026-09-14:math').status_code == 200


def test_soch_window_and_closed_period(client):
    enable(client); create(client, 'teacher'); t = signed('teacher')
    period = ok(client.put('/api/journal/periods/' + uuid4().hex + '?workspace=demo', json=dict(name='Первая четверть', start=D17, end='2026-10-23', kind='quarter', version=0, reason='Создание периода')))
    assert ok(configure(client, windows=[dict(periodId=period['id'], start='2026-10-12', end='2026-10-23')]))['windows'][0]['start'] == '2026-10-12'
    assert configure(client, version=1, windows=[dict(periodId=period['id'], start='2026-11-01', end='2026-11-05')]).status_code == 422
    assert configure(client, version=1, windows=[dict(periodId='absent', start='2026-10-12', end='2026-10-23')]).status_code == 422
    early = english(t, D17, type='soch')
    assert early.status_code == 409 and 'Первая четверть' in early.json()['detail'] and '12.10.2026' in early.json()['detail']
    assert english(t, '2026-10-12', type='soch').status_code == 200
    assert english(t, D24, type='sor').status_code == 200
    with api.connect() as db: db.execute("UPDATE journal_periods SET status='closed'")
    closed = english(t, D31)
    assert closed.status_code == 409 and 'Период журнала закрыт' in closed.json()['detail']
    assert english(client, D31, overrideReason='Всё равно').status_code == 409


def test_family_sees_only_published_own_group_bookings_and_one_notice(client):
    enable(client); create(client, 'teacher'); guardian(client, 'mother', 's1'); guardian(client, 'other', 's3'); guardian(client, 'pupil', 's2', 'student')
    t, mother, other, pupil = signed('teacher'), signed('mother'), signed('other'), signed('pupil')
    planned = ok(english(t, publish=False))
    assert ok(family(mother))['items'] == [] and unread(mother)['unread'] == 0
    published = ok(t.post(url(f"bookings/{planned['id']}/publish"), json=dict(version=planned['version'])))
    assert published['status'] == 'published'
    item, = ok(family(mother))['items']
    assert item['subject'] == 'Английский язык' and item['short'] == 'СОР' and item['topic'] == 'Дроби' and item['slotValid']
    assert not {'note', 'override', 'author', 'studentIds'} & item.keys()
    inbox = unread(mother)
    assert inbox['unread'] == 1 and inbox['items'][0]['action'] == 'assessment.published'
    assert ok(family(other, 's3'))['items'] == [] and unread(other)['unread'] == 0 and family(other, 's1').status_code == 404
    moved = ok(t.post(url(f"bookings/{planned['id']}/move"), json=dict(requestId=str(uuid4()), version=published['version'], day=D24, lessonId='2026-08-24:eng', reason='Болезнь учителя')))
    inbox = unread(mother)
    assert inbox['unread'] == 1 and inbox['items'][0]['action'] == 'assessment.moved' and ok(family(mother))['items'][0]['day'] == D24
    assert [i['day'] for i in ok(family(pupil, 's2'))['items']] == [D24]
    ok(book(client, 'g-math', 'sub0', D31, '2026-08-31:math'))
    assert [i['subject'] for i in ok(family(mother))['items']] == ['Английский язык', 'Математика']
    assert [i['subject'] for i in ok(family(pupil, 's2'))['items']] == ['Английский язык']
    for c in [mother, pupil]:
        assert c.get(url('bookings', start=D17, end=D31)).status_code == 403
        assert c.get(url('load', start=D17, end=D31)).status_code == 403 and c.get(url('export', classId='c-demo-a', month='2026-08')).status_code == 403
        assert configure(client, who=c).status_code == 403
        assert not ok(c.get(url('options')))['canBook']
    assert ok(mother.get(url('options')))['family'] and ok(mother.get(url('options')))['students'] == [dict(id='s1', name='Ученик 1 (пример)', className='10A')]
    ok(t.post(url(f"bookings/{planned['id']}/cancel"), json=dict(version=moved['version'], reason='Работа отменена')))
    assert [i['subject'] for i in ok(family(mother))['items']] == ['Математика']
    assert unread(mother)['items'][0]['action'] == 'assessment.cancelled' and unread(mother)['items'][1]['action'] == 'assessment.published'


def test_staff_notice_when_somebody_else_changes_the_work(client):
    enable(client); create(client, 'teacher'); t = signed('teacher')
    work = ok(english(t))
    assert unread(t)['unread'] == 0
    ok(client.post(url(f"bookings/{work['id']}/cancel"), json=dict(version=work['version'], reason='Административное решение')))
    assert unread(t)['items'][0]['action'] == 'assessment.staff_changed'


def test_history_versions_and_past_bookings(client, monkeypatch):
    enable(client); create(client, 'teacher'); t = signed('teacher')
    work = ok(english(t, publish=False))
    published = ok(t.post(url(f"bookings/{work['id']}/publish"), json=dict(version=1)))
    assert t.post(url(f"bookings/{work['id']}/publish"), json=dict(version=2)).status_code == 409
    edited = ok(t.post(url(f"bookings/{work['id']}/edit"), json=dict(version=2, typeId='sor', topic='Новая тема', note='Заметка')))
    assert edited['topic'] == 'Новая тема' and edited['note'] == 'Заметка' and edited['version'] == 3
    assert t.post(url(f"bookings/{work['id']}/edit"), json=dict(version=2, typeId='sor')).status_code == 409
    request = dict(requestId=str(uuid4()), version=3, day=D24, lessonId='2026-08-24:eng', reason='Совещание')
    moved = ok(t.post(url(f"bookings/{work['id']}/move"), json=request))
    assert moved['day'] == D24 and moved['version'] == 4 and moved['lessonId'] == '2026-08-24:eng'
    assert ok(t.post(url(f"bookings/{work['id']}/move"), json=request)) == moved
    assert t.post(url(f"bookings/{work['id']}/move"), json=request | dict(reason='Другое')).status_code == 409
    other = ok(english(t, D31))
    assert t.post(url(f"bookings/{other['id']}/move"), json=request).status_code == 409
    assert history(t, work['id']) == ['created', 'published', 'edited', 'moved']
    # Moving onto the lesson the work already occupies is allowed.
    again = ok(t.post(url(f"bookings/{work['id']}/move"), json=dict(requestId=str(uuid4()), version=4, day=D24, lessonId='2026-08-24:eng', reason='Без изменений')))
    assert again['version'] == 5 and again['lessonId'] == '2026-08-24:eng'
    assert history(t, work['id'])[-1] == 'moved'
    moved = again
    monkeypatch.setattr('backend.assessment_calendar_routes.today', lambda: date(2026, 9, 1))
    assert t.post(url(f"bookings/{work['id']}/cancel"), json=dict(version=5, reason='Поздно')).status_code == 409
    assert not next(b for b in listing(t) if b['id'] == work['id'])['canChange']
    cancelled = ok(client.post(url(f"bookings/{work['id']}/cancel"), json=dict(version=5, reason='Ошибочная бронь')))
    assert cancelled['status'] == 'cancelled' and cancelled['cancelReason'] == 'Ошибочная бронь'
    assert client.post(url(f"bookings/{work['id']}/cancel"), json=dict(version=6, reason='Ещё раз')).status_code == 409
    assert client.post(url(f"bookings/{work['id']}/cancel"), json=dict(version=5, reason='Ещё раз')).status_code == 409
    assert history(t, work['id']) == ['created', 'published', 'edited', 'moved', 'moved', 'cancelled']
    assert ok(t.get(url('bookings', start=D17, end='2026-09-20', status='cancelled')))['items'][0]['id'] == work['id']
    # The cancelled slot is free again.
    monkeypatch.setattr('backend.assessment_calendar_routes.today', lambda: date(2026, 8, 10))
    assert english(t, D24).status_code == 200


def test_config_types_coordinators_load_and_csv(client):
    enable(client); create(client, 'teacher'); create(client, 'staff', name='Завуч'); t, worker = signed('teacher'), signed('staff')
    options = ok(client.get(url('options')))
    assert configure(client, who=t).status_code == 403 and configure(client, version=5).status_code == 409
    assert {c['role'] for c in options['candidates']} == {'teacher', 'staff'} and options['configVersion'] == 0
    renamed = [x | dict(title='Раздел (СОР)') if x['id'] == 'sor' else x for x in options['types']]
    ok(configure(client, types=renamed))
    assert next(x for x in ok(t.get(url('options')))['types'] if x['id'] == 'sor')['title'] == 'Раздел (СОР)'
    work = ok(english(t, topic='=HYPERLINK("x")'))
    assert work['type'] == 'Раздел (СОР)'
    assert configure(client, version=1, types=[x for x in renamed if x['id'] != 'sor']).status_code == 422
    inactive = [x | dict(active=False) if x['id'] == 'sor' else x for x in renamed]
    ok(configure(client, version=1, types=inactive))
    assert 'sor' not in {x['id'] for x in ok(t.get(url('options')))['types']} and english(t, D24).status_code == 422
    ok(configure(client, version=2, types=renamed))
    # A coordinator named by the administration books any pair and sees the school load, but cannot override or configure.
    assert worker.get(url('load', start=D17, end=D31)).status_code == 403 and book(worker, 'g-math', 'sub0', D17, '2026-08-17:math').status_code == 403
    staff = next(c for c in ok(client.get(url('options')))['candidates'] if c['role'] == 'staff')
    ok(configure(client, version=3, coordinatorIds=[staff['id']], maxPerDay=1))
    assert ok(worker.get(url('options')))['coordinator'] and ok(worker.get(url('options')))['canBook']
    refused = book(worker, 'g-math', 'sub0', D17, '2026-08-17:math')
    assert refused.status_code == 409
    assert book(worker, 'g-math', 'sub0', D17, '2026-08-17:math', overrideReason='Нужно').status_code == 403
    assert ok(book(worker, 'g-math', 'sub0', D24, '2026-08-24:math'))['status'] == 'published'
    assert configure(client, who=worker).status_code == 403
    grid = ok(worker.get(url('load', start=D17, end='2026-08-30')))
    assert grid['days'][0] == D17 and next(r for r in grid['rows'] if r['name'] == '10A')['cells'][D17]['load'] == 1 and grid['maxPerDay'] == 1
    assert t.get(url('load', start=D17, end=D31)).status_code == 403
    users = ok(client.get('/api/access/users'))
    person = next(u for u in users if u['login'] == 'staff')
    update = client.put('/api/access/users/' + person['id'], json={k: v for k, v in person.items() if k != 'id'} | dict(workspaces=['demo', 'school']))
    assert update.status_code == 200, update.text
    assert signed('staff').get(url('load', start=D17, end=D31)).status_code == 403
    exported = t.get(url('export', classId='c-demo-a', month='2026-08'))
    assert exported.status_code == 200 and 'attachment' in exported.headers['content-disposition'] and 'Дата,Начало' in exported.text and "'=HYPERLINK" in exported.text
    assert '17.08.2026' in exported.text and ',СОР,' in exported.text and exported.content.startswith(b'\xef\xbb\xbf')
    change(lambda s: [setattr(w, 'teacherId', 't0') for w in s.workloads if w.id == 'w1'])
    t = signed('teacher')
    assert t.get(url('export', classId='c-demo-d', month='2026-08')).status_code == 403 and t.get(url('export', classId='c-demo-a', month='2026-08')).status_code == 200
    assert t.get(url('export', classId='c-demo-a', month='2026-13')).status_code == 422


def test_editor_anonymous_and_limits_of_ranges(client):
    enable(client); create(client, 'editor'); editor = signed('editor')
    assert TestClient(api.app, headers=HEADERS).get(url('options')).status_code == 401
    options = ok(editor.get(url('options')))
    assert not options['canBook'] and not options['family'] and options['pairs'] == [] and options['configVersion'] is None
    assert editor.get(url('bookings', start=D17, end=D31)).status_code == 403 and editor.get(url('lesson-days', groupId='g-a', subjectId='sub2', start=D17, end=D31)).status_code == 403
    assert client.get(url('bookings', start=D31, end=D17)).status_code == 422 and client.get(url('bookings', start='2026-01-01', end='2026-12-31')).status_code == 422
    assert client.get(url('load', start=D17, end='2026-10-31')).status_code == 422
    days = ok(client.get(url('lesson-days', groupId='g-a', subjectId='sub2', start=D17, end='2026-09-06')))['days']
    assert days == [D17, D24, D31]
    assert client.get(url('bookings/absent/history')).status_code in [404, 403]


def test_audience_counts_classes_without_known_pupils_as_a_whole():
    row = dict(classIds=['A', 'B'], studentIds=['s1'], wholeClassIds=[], rosterComplete=True)
    pupils = {'s1': type('P', (), dict(classId='A'))()}
    assert audience(row, pupils) == dict(class_ids=['A', 'B'], student_ids=['s1'], whole_ids=['B'])
    assert audience(row | dict(rosterComplete=False), pupils)['whole_ids'] == ['A', 'B']
    assert audience(row | dict(wholeClassIds=['A', 'B']), pupils)['whole_ids'] == ['A', 'B']


def test_staff_groups_are_never_bookable_and_parallel_requests_do_not_double_book(client):
    enable(client); create(client, 'teacher'); t = signed('teacher')
    def staff_meeting(s):
        s.groups.append(Group(id='g-staff', name='Методобъединение', kind='staff'))
        s.workloads.append(next(w for w in s.workloads if w.id == 'w0').model_copy(update=dict(id='w-staff', groupId='g-staff')))
    change(staff_meeting)
    assert 'g-staff' not in {p['groupId'] for p in ok(client.get(url('options')))['pairs']}
    assert book(client, 'g-staff', 'sub2', D17, '2026-08-17:eng').status_code == 403
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: english(t), range(2)))
    assert sorted(r.status_code for r in results) == [200, 409]
    assert len(listing(client)) == 1


def test_changing_the_type_rechecks_the_load_and_tells_the_family_once(client):
    enable(client); create(client, 'teacher'); teacher(client, 'mathteacher', 't0', 'Учитель математики'); guardian(client, 'mother', 's1')
    t, math, mother = signed('teacher'), signed('mathteacher'), signed('mother')
    quiz = dict(id='quiz', title='Самостоятельная', short='С/р', category='other', counted=False, window=False, active=True)
    ok(configure(client, maxPerDay=1, types=ok(client.get(url('options')))['types'] + [quiz]))
    work = ok(english(t, type='quiz'))
    ok(book(math, 'g-math', 'sub0', D17, '2026-08-17:math', publish=False))
    assert unread(mother)['unread'] == 1
    def stored(): return next(b for b in listing(t, end=D17) if b['id'] == work['id'])
    def retype(who, version, **extra): return who.post(url(f"bookings/{work['id']}/edit"), json=dict(version=version, typeId='sor', topic='Дроби') | extra)
    # A topic alone changes nothing for the family.
    topic = ok(t.post(url(f"bookings/{work['id']}/edit"), json=dict(version=work['version'], typeId='quiz', topic='Другая тема')))
    assert topic['topic'] == 'Другая тема' and unread(mother)['items'][0]['action'] == 'assessment.published'
    # Turning the work into a counted one weighs on the class again: refused, and only an administrator may override.
    refused = retype(t, topic['version'])
    assert refused.status_code == 409 and '10A' in refused.json()['detail'] and 'Математика' in refused.json()['detail']
    assert retype(t, topic['version'], overrideReason='Очень надо').status_code == 403
    assert stored()['typeId'] == 'quiz'
    forced = ok(retype(client, topic['version'], overrideReason='Решение завуча'))
    assert forced['typeId'] == 'sor' and forced['short'] == 'СОР' and forced['override'] == 'Решение завуча' and forced['version'] == topic['version'] + 1
    last = ok(client.get(url(f"bookings/{work['id']}/history")))['items'][-1]
    assert last['action'] == 'edited' and last['reason'] == 'Решение завуча' and last['data']['after']['typeId'] == 'sor'
    assert any(e['action'] == 'assessment.override' for e in client.get('/api/access/audit').json())
    inbox = unread(mother)
    assert inbox['unread'] == 1 and inbox['items'][0]['action'] == 'assessment.changed'
    # A type that has been switched off cannot be chosen any more.
    ok(configure(client, types=[x | dict(active=False) if x['id'] == 'control' else x for x in ok(client.get(url('options')))['types']]))
    off = retype(t, forced['version'], typeId='control')
    assert off.status_code == 422 and 'действующий тип' in off.json()['detail']


def test_a_colleague_who_only_sees_the_work_cannot_change_it(client):
    enable(client); create(client, 'teacher'); teacher(client, 'mathteacher', 't0', 'Учитель математики'); t, math = signed('teacher'), signed('mathteacher')
    work = ok(english(t, publish=False))
    assert not next(b for b in listing(math) if b['id'] == work['id'])['canChange']
    move = dict(requestId=str(uuid4()), version=1, day=D24, lessonId='2026-08-24:eng', reason='Чужая работа')
    for action, body in [('publish', dict(version=1)), ('edit', dict(version=1, typeId='sor', topic='Чужая тема')), ('cancel', dict(version=1, reason='Чужая работа')), ('move', move)]:
        response = math.post(url(f"bookings/{work['id']}/{action}"), json=body)
        assert response.status_code == 403, (action, response.text)
    still = next(b for b in listing(t) if b['id'] == work['id'])
    assert still['status'] == 'planned' and still['version'] == 1 and still['topic'] == 'Дроби' and history(t, work['id']) == ['created']


def test_cancelled_works_and_moves_inside_a_week_do_not_count_against_the_limits(client):
    enable(client); create(client, 'teacher'); teacher(client, 'mathteacher', 't0', 'Учитель математики'); t, math = signed('teacher'), signed('mathteacher')
    change(lambda s: s.datedLessons.append(DatedLesson(id='add', name='Разовый', kind='add', onDate=date(2026, 8, 19), groupId='g-a', profileId='secondary', subjectId='sub2', startTime='15:00', endTime='15:40')))
    ok(configure(client, maxPerDay=1))
    first = ok(english(t))
    assert book(math, 'g-math', 'sub0', D17, '2026-08-17:math').status_code == 409
    ok(t.post(url(f"bookings/{first['id']}/cancel"), json=dict(version=first['version'], reason='Передумали')))
    free = ok(book(math, 'g-math', 'sub0', D17, '2026-08-17:math'))
    ok(math.post(url(f"bookings/{free['id']}/cancel"), json=dict(version=free['version'], reason='Освобождаем неделю')))
    # With one work a week, moving a work to another lesson of the same week is not refused for the work itself.
    ok(configure(client, maxPerDay=1, maxPerWeek=1))
    own = ok(english(t))
    moved = ok(t.post(url(f"bookings/{own['id']}/move"), json=dict(requestId=str(uuid4()), version=own['version'], day='2026-08-19', lessonId='extra:add', reason='Перенос в неделе')))
    assert moved['day'] == '2026-08-19' and moved['lessonId'] == 'extra:add'
    assert book(math, 'g-math', 'sub0', D17, '2026-08-17:math').status_code == 409


def test_a_move_that_needs_no_override_drops_the_old_one(client):
    enable(client); create(client, 'teacher'); t = signed('teacher')
    ok(configure(client, maxPerDay=1))
    ok(english(t, D24))
    over = ok(book(client, 'g-math', 'sub0', D24, '2026-08-24:math', overrideReason='Решение завуча'))
    assert over['override'] == 'Решение завуча' and over['overrideBy'] == 'Администратор'
    moved = ok(client.post(url(f"bookings/{over['id']}/move"), json=dict(requestId=str(uuid4()), version=over['version'], day=D31, lessonId='2026-08-31:math', reason='Без превышения')))
    assert moved['override'] == '' and moved['overrideBy'] == ''
    assert ok(client.get(url(f"bookings/{over['id']}/history")))['items'][-1]['data']['override'] == {}


def test_a_type_switched_to_not_counted_stops_weighing_on_the_class(client):
    enable(client); create(client, 'teacher'); teacher(client, 'mathteacher', 't0', 'Учитель математики'); t, math = signed('teacher'), signed('mathteacher')
    ok(configure(client, maxPerDay=1))
    ok(english(t))
    assert book(math, 'g-math', 'sub0', D17, '2026-08-17:math', type='control').status_code == 409
    ok(configure(client, types=[x | dict(counted=False) if x['id'] == 'sor' else x for x in ok(client.get(url('options')))['types']]))
    later = ok(book(math, 'g-math', 'sub0', D17, '2026-08-17:math', type='control'))
    cell = next(r for r in ok(client.get(url('load', start=D17, end='2026-08-23')))['rows'] if r['name'] == '10A')['cells'][D17]
    assert later['typeId'] == 'control' and cell['load'] == 1 and [i['short'] for i in cell['items']] == ['К/р']


def test_publishing_rechecks_the_lesson_and_the_journal_period(client):
    d = date.fromisoformat
    enable(client); create(client, 'teacher'); guardian(client, 'mother', 's1'); t, mother = signed('teacher'), signed('mother')
    planned = ok(english(t, publish=False))
    change(lambda s: setattr(s, 'datedLessons', [DatedLesson(id='late', name='Урок отменён директором', kind='cancel', onDate=d(D17), sourceDate=d(D17), placementId='eng')]))
    refused = t.post(url(f"bookings/{planned['id']}/publish"), json=dict(version=planned['version']))
    assert refused.status_code == 409 and 'Перенесите работу' in refused.json()['detail']
    assert ok(family(mother))['items'] == [] and 'assessment.published' not in [i['action'] for i in unread(mother)['items']]
    assert next(b for b in listing(t) if b['id'] == planned['id'])['status'] == 'planned'
    # Moved onto a lesson that still stands, the work can be published.
    moved = ok(t.post(url(f"bookings/{planned['id']}/move"), json=dict(requestId=str(uuid4()), version=planned['version'], day=D24, lessonId='2026-08-24:eng', reason='Урок отменён')))
    assert ok(t.post(url(f"bookings/{planned['id']}/publish"), json=dict(version=moved['version'])))['status'] == 'published'
    assert unread(mother)['items'][0]['action'] == 'assessment.published' and ok(family(mother))['items'][0]['day'] == D24
    # A period closed after the booking blocks the publication, an administrator's too.
    later = ok(english(t, D31, publish=False))
    ok(client.put('/api/journal/periods/' + uuid4().hex + '?workspace=demo', json=dict(name='Первая четверть', start=D17, end='2026-10-23', kind='quarter', version=0, reason='Создание периода')))
    with api.connect() as db: db.execute("UPDATE journal_periods SET status='closed'")
    for who in [t, client]:
        closed = who.post(url(f"bookings/{later['id']}/publish"), json=dict(version=later['version']))
        assert closed.status_code == 409 and 'Период журнала закрыт' in closed.json()['detail']


def test_school_events_are_shown_to_staff_only(client):
    enable(client); create(client, 'teacher'); create(client, 'editor'); guardian(client, 'mother', 's1'); guardian(client, 'pupil', 's2', 'student')
    change(lambda s: setattr(s, 'events', [Event(id='quarantine', name='Карантин в 10D (корь)', start=date(2026, 9, 3), end=date(2026, 9, 10), kind='extra', effect='cancel', classIds=['c-demo-d'])]))
    for login in ['mother', 'pupil', 'editor']:
        assert ok(signed(login).get(url('options')))['schoolEvents'] == [], login
    for who in [client, signed('teacher')]:
        assert [e['name'] for e in ok(who.get(url('options')))['schoolEvents']] == ['Карантин в 10D (корь)']


def test_far_dates_are_refused_instead_of_overflowing(client):
    enable(client); create(client, 'teacher'); guardian(client, 'mother', 's1'); t, mother = signed('teacher'), signed('mother')
    far, early = dict(start='9999-12-01', end='9999-12-31'), dict(start='0001-01-02', end='0001-01-20')
    for who in [client, t]:
        assert who.get(url('lesson-days', groupId='g-a', subjectId='sub2', **far)).status_code == 422
        assert who.get(url('export', classId='c-demo-a', month='9999-12')).status_code == 422
        assert who.get(url('slots', groupId='g-a', subjectId='sub2', day='9999-12-31')).status_code == 422
        assert who.get(url('bookings', **far)).status_code == 422 and who.get(url('bookings', **early)).status_code == 422
    for span in [far, early]:
        assert client.get(url('load', **span)).status_code == 422
        assert mother.get(url('family', studentId='s1', **span)).status_code == 422
    assert 'диапазона' in client.get(url('load', **far)).json()['detail']
    assert client.get(url('load', start=D17, end='2026-08-30')).status_code == 200 and client.get(url('export', classId='c-demo-a', month='2026-08')).status_code == 200


def test_a_coordinator_who_left_or_lost_access_does_not_lock_the_settings(client):
    enable(client); create(client, 'staff', name='Завуч'); create(client, 'staff', login='second', name='Второй завуч'); create(client, 'editor', name='Составитель')
    people = {u['login']: u for u in ok(client.get('/api/access/users'))}
    def edit_account(login, **changes):
        response = client.put('/api/access/users/' + people[login]['id'], json={k: v for k, v in people[login].items() if k != 'id'} | changes)
        assert response.status_code == 200, response.text
    ids = {login: people[login]['id'] for login in ['staff', 'second', 'editor']}
    ok(configure(client, coordinatorIds=list(ids.values())))
    # An editor may be named too: the client menu shows the panel to every coordinator role.
    editor = signed('editor')
    assert ok(editor.get(url('options')))['coordinator'] and editor.get(url('load', start=D17, end=D31)).status_code == 200
    assert 'editor' in {c['role'] for c in ok(client.get(url('options')))['candidates']}
    edit_account('staff', active=False)
    edit_account('second', workspaces=['demo', 'school'])
    options = ok(client.get(url('options')))
    # The leaver is gone and the person whose access changed no longer counts as ticked.
    assert options['coordinatorIds'] == [ids['editor']] and ids['staff'] not in {c['id'] for c in options['candidates']}
    assert signed('second').get(url('load', start=D17, end=D31)).status_code == 403
    # Saving the settings with the stale ids (an older form still sends them) neither fails nor keeps the leaver.
    saved = ok(configure(client, maxPerDay=3, coordinatorIds=list(ids.values())))
    assert saved['maxPerDay'] == 3 and set(saved['coordinatorIds']) == {ids['second'], ids['editor']}
    # Naming somebody again is an explicit act; a person who never was a coordinator has to be valid.
    assert signed('second').get(url('load', start=D17, end=D31)).status_code == 200
    assert configure(client, coordinatorIds=['ghost']).status_code == 422
    assert configure(client, coordinatorIds=[ids['staff']]).status_code == 422


def test_print_styles_hide_the_page_only_while_the_panel_is_open():
    # The stylesheet stays loaded after the panel closes, so an unscoped rule would blank every other printout.
    css = (Path(__file__).resolve().parents[1] / 'src' / 'AssessmentPanel.css').read_text(encoding='utf-8')
    block = css[css.index('@media print'):]
    hiding = re.findall(r'([^{}]+)\{[^{}]*visibility:\s*hidden', block)
    assert hiding and all('dialog.assessments[open]' in selector for selector in hiding)
    assert not re.search(r'(^|[\s,}])body \*', block)
