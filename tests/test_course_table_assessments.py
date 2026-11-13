"""Course table: «Новое назначение» adds one work to a lesson of the course as a draft, with the rules of the lesson editor."""
from datetime import date, datetime, timezone
from uuid import uuid4
import pytest
from backend import api, journal_periods
from test_access import client, create, signed  # noqa: F401
from test_journal import DAY, entry, report
from test_journal_history import clock, card, resave, NEXT, TODAY  # noqa: F401
from backend.models import DatedLesson
from test_journal_periods import period, close
from test_assessment_categories import put_config
from test_course_table import URL, G, S, MATH, MATH_GROUP, END, course, edit, mark, column, ok, save, staffed, table, work


FUTURE = '2026-09-07'


def offered(c, start=DAY, end=END):
    return c.get(URL + '/lessons', params=dict(workspace='demo', groupId=G, subjectId=S, start=start, end=end))


def lesson_of(c, day=DAY):
    return next(l for l in ok(offered(c))['lessons'] if l['day'] == day)


def add(c, day=DAY, **changes):
    """Post a new work to the lesson of that day, as the dialog does for the lesson it lists."""
    found = lesson_of(c, day)
    body = dict(requestId=str(uuid4()), groupId=G, subjectId=S, revision=api.read('demo').revision, day=day, lessonKey=found['lessonKey'],
                version=found['version'], title='СОР 1', category='sor', kind='points', maxScore=20, weight=2) | changes
    return c.post(URL + '/assessments?workspace=demo', json=body), body


def family_rows(parent):
    return report(parent).json()['rows']


def test_the_teacher_adds_a_work_that_stays_a_draft_until_marks_are_published(client, clock):
    teacher, parent = staffed(client)
    before = family_rows(parent)
    created = ok(add(teacher)[0])
    assert created['published'] is False and created['category'] == 'sor'
    t = ok(table(teacher))
    found = next(w for w in t['works'] if w['id'] == created['assessmentId'])
    assert (found['name'], found['category'], found['maxScore'], found['weight'], found['kind']) == ('СОР 1', 'sor', 20, 2, 'points')
    assert t['lessons'][found['lesson']]['version'] == created['version'] and t['lessons'][found['lesson']]['editable']
    assert family_rows(parent) == before
    done = ok(save(teacher, [edit(t, DAY, ('s1', created['assessmentId'], 17))], publish=True))
    assert done['conflicts'] == [] and done['saved'][0]['published'] is True
    rows = family_rows(parent)
    grade = rows[0]['record']['grades'][0]
    assert grade['assessmentId'] == created['assessmentId'] and grade['score'] == 17
    assert next(a for a in rows[0]['content']['assessments'] if a['id'] == created['assessmentId'])['category'] == 'sor'
    assert mark(ok(table(teacher)), 's1', DAY, created['assessmentId'])['score'] == 17


def test_a_work_is_added_to_a_lesson_that_already_has_works_and_a_publication(client, clock):
    teacher, parent = staffed(client)
    course(teacher, DAY, [work('a')], dict(a=dict(s1=5)), publish=True)
    seen = family_rows(parent)
    before = lesson_of(teacher)
    created = ok(add(teacher, category='', kind='label', title='  Устный ответ  ')[0])
    assert created['version'] == before['version'] + 1 and created['index'] == 1 and created['category'] == 'fo'
    t = ok(table(teacher))
    assert [(w['id'], w['name']) for w in t['works']] == [('a', 'Работа a'), (created['assessmentId'], 'Устный ответ')]
    assert column(t, DAY)['hasUnpublished'] and mark(t, 's1', DAY, 'a')['score'] == 5
    assert family_rows(parent) == seen


def test_the_dialog_lists_only_days_where_a_work_can_be_added(client, clock, monkeypatch):
    monkeypatch.setattr(journal_periods, 'now', lambda: datetime(2026, 9, 1, 12, tzinfo=timezone.utc))
    teacher, _ = staffed(client)
    days = {l['day'] for l in ok(offered(teacher, end=FUTURE))['lessons']}
    assert days >= {DAY, NEXT} and not any(d > TODAY.isoformat() for d in days)
    # A lesson that already has a journal is judged by the editor's rules: a moved one (its roster and time need a sync), a substitute-led one
    # and one of a closed period drop out.
    course(teacher, DAY, [work('a')])
    course(teacher, NEXT, [work('a')])
    resave(lambda s: setattr(s, 'datedLessons', [DatedLesson(id='move', name='Перенос', kind='change', placementId='eng', sourceDate=date.fromisoformat(DAY),
                                                         onDate=date.fromisoformat('2026-08-18'), startTime='14:00', endTime='14:40')]))
    assert '2026-08-18' not in {l['day'] for l in ok(offered(teacher))['lessons']}
    state = api.read('demo')
    state.datedLessons = [DatedLesson(id='sub', name='Замена', kind='change', placementId='eng', sourceDate=date.fromisoformat(NEXT), onDate=date.fromisoformat(NEXT), teacherIds=['t1'])]
    api.save('demo', state)
    assert NEXT not in {l['day'] for l in ok(offered(teacher))['lessons']}
    close(client, period(client))
    days = {l['day'] for l in ok(offered(teacher))['lessons']}
    assert NEXT not in days
    # The range narrows the answer; an inverted one is refused.
    assert {l['day'] for l in ok(offered(teacher, start=NEXT, end=NEXT))['lessons']} <= {NEXT}
    assert offered(teacher, start=END, end=DAY).status_code == 422


def test_a_lesson_fills_up_and_then_takes_no_more_works(client, clock):
    teacher, _ = staffed(client)
    for _ in range(100):
        if lesson_of(teacher)['full']: break
        ok(add(teacher)[0])
    full = lesson_of(teacher)
    assert full['full'] is True and full['works'] > 1
    refused, _ = add(teacher)
    assert refused.status_code == 422
    assert lesson_of(teacher)['works'] == full['works']


def test_listing_is_for_the_teacher_of_the_course(client, clock):
    staffed(client)
    create(client, 'teacher', login='unrelated', grants=[dict(workspace='demo', view='teacher', entityIds=['t3'])])
    assert offered(client).status_code == 403 and offered(signed('unrelated')).status_code == 403 and offered(signed('parent')).status_code == 403


CASES = [('administrator', 403), ('foreign teacher', 403), ('parent', 403), ('unknown category', 422), ('disabled category', 422),
         ('lesson of another course with a journal', 403), ('lesson of another course without a journal', 403), ('day not of the lesson', 409),
         ('day outside the year', 422), ('closed period', 403), ('bad maximum', 422), ('blank title', 422),
         ('future lesson', 422), ('cancelled lesson', 403), ('lesson of a substitute', 403), ('moved lesson', 409)]


def changed(case):
    """The dated lesson that turns the lesson of DAY into the case."""
    day, kind = date.fromisoformat(DAY), 'cancel' if case == 'cancelled lesson' else 'change'
    if case == 'moved lesson':
        return DatedLesson(id='x', name='Перенос', kind=kind, placementId='eng', sourceDate=day, onDate=date.fromisoformat(NEXT), startTime='14:00', endTime='14:40')
    if case == 'cancelled lesson': return DatedLesson(id='x', name='Отмена', kind=kind, placementId='eng', sourceDate=day, onDate=day)
    return DatedLesson(id='x', name='Замена', kind=kind, placementId='eng', sourceDate=day, onDate=day, teacherIds=['t1'])


@pytest.mark.parametrize('case,status', CASES)
def test_refusals_leave_the_table_as_it_was(client, clock, monkeypatch, case, status):
    monkeypatch.setattr(journal_periods, 'now', lambda: datetime(2026, 9, 1, 12, tzinfo=timezone.utc))
    teacher, _ = staffed(client)
    create(client, 'teacher', login='unrelated', grants=[dict(workspace='demo', view='teacher', entityIds=['t3'])])
    # Teaches both courses, so that only the course checks of the route can refuse a lesson of the other one.
    create(client, 'teacher', login='both', grants=[dict(workspace='demo', view='teacher', entityIds=['t2', 't0'])])
    course(teacher, DAY, [work('a')], dict(a=dict(s1=5)), publish=True)
    found, who, changes = lesson_of(teacher), teacher, {}
    if case == 'administrator': who = client
    if case == 'foreign teacher': who = signed('unrelated')
    if case == 'parent': who = signed('parent')
    if case == 'unknown category': changes = dict(category='nothing')
    if case == 'disabled category':
        categories = client.get('/api/journal/categories', params=dict(workspace='demo')).json()['categories']
        ok(put_config(client, categories=[dict(c, active=False) if c['id'] == 'sor' else c for c in categories]))
    if case == 'lesson of another course with a journal':
        who, other = signed('both'), course(client, DAY, [work('m')], subject=MATH, group=MATH_GROUP)
        changes = dict(lessonKey=other['lessonKey'], version=other['version'])
    if case == 'lesson of another course without a journal':
        who, changes = signed('both'), dict(lessonKey=card(client, NEXT, MATH, MATH_GROUP)['lessonKey'], version=0, day=NEXT)
    if case == 'day not of the lesson': changes = dict(day=NEXT)
    if case == 'day outside the year': changes = dict(day='2024-01-08')
    if case == 'closed period': close(client, period(client))
    if case == 'bad maximum': changes = dict(maxScore=0)
    if case == 'blank title': changes = dict(title='   ')
    if case == 'future lesson': changes = dict(day=FUTURE, lessonKey=card(client, FUTURE)['lessonKey'], version=0)
    if case in ('cancelled lesson', 'lesson of a substitute', 'moved lesson'): resave(lambda s: setattr(s, 'datedLessons', [changed(case)]))
    body = dict(requestId=str(uuid4()), groupId=G, subjectId=S, revision=api.read('demo').revision, day=DAY, lessonKey=found['lessonKey'], version=found['version'],
                title='СОР 1', category='sor', kind='points', maxScore=20, weight=2) | changes
    shown, before = ok(offered(teacher, end=FUTURE))['lessons'], ok(table(teacher))
    assert who.post(URL + '/assessments?workspace=demo', json=body).status_code == status
    assert ok(offered(teacher, end=FUTURE))['lessons'] == shown and ok(table(teacher)) == before


def test_a_repeated_request_returns_the_first_answer_and_adds_nothing(client, clock):
    teacher, _ = staffed(client)
    first, body = add(teacher)
    again = teacher.post(URL + '/assessments?workspace=demo', json=body)
    assert first.status_code == again.status_code == 200 and first.json() == again.json()
    assert len(ok(table(teacher))['works']) == 1
    other = teacher.post(URL + '/assessments?workspace=demo', json=body | dict(title='Другое'))
    assert other.status_code == 409 and len(ok(table(teacher))['works']) == 1


def test_a_stale_lesson_or_schedule_gives_a_conflict(client, clock):
    teacher, _ = staffed(client)
    course(teacher, DAY, [work('a')])
    found = lesson_of(teacher)
    ok(add(teacher)[0])
    stale = add(teacher, version=found['version'])[0]
    assert stale.status_code == 409
    old = add(teacher, revision=api.read('demo').revision + 1)[0]
    assert old.status_code == 409
    assert len(ok(table(teacher))['works']) == 2
