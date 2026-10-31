"""Course tests: assignment to a course, the results table and the one-button transfer of marks into a lesson of the course."""
from datetime import date, datetime, timedelta, timezone
import json
from uuid import uuid4
import pytest
from backend import api, journal, journal_periods, quiz_attempts, quiz_bank, quiz_catalog, quiz_transfer, quizzes, timetable_history
from backend.models import DatedLesson, Student
from test_access import client, create, signed
from test_journal import DAY, entry, write, report
from test_journal_history import clock, resave, card, ENGLISH, NEXT  # noqa: F401
from test_journal_periods import period, close
from test_quizzes import question, start, save as answer, action, attempt
from test_course_table import work as journal_work, course as lesson_with, table, edit, save as table_save, ok, stored_versions, history_count, lead, staffed as accounts, G, S, MATH, MATH_GROUP

TODAY = datetime(2026, 9, 1, 9, tzinfo=timezone.utc)
FUTURE = '2026-09-07'


@pytest.fixture(autouse=True)
def school_clock(monkeypatch, clock):
    """The quiz modules and the journal agree that it is 1 September 2026."""
    for module in [quizzes, quiz_bank, quiz_attempts, quiz_catalog, quiz_transfer]: monkeypatch.setattr(module, 'now', lambda: TODAY)


def staffed(client):
    """The admin client, the English teacher (t2) and the parent of s1, on a database whose journal tables exist (the quiz bank writes need them)."""
    teacher, parent = accounts(client)
    card(teacher)
    return teacher, parent


def course_body(questions, **extra):
    definition = dict(title='Контрольная по курсу', instructions='', variants=[dict(id='v1', name='Вариант 1', questions=[dict(id=q['id'], version=q['version']) for q in questions])],
                      opensAt=(TODAY - timedelta(hours=1)).isoformat(), closesAt=(TODAY + timedelta(hours=1)).isoformat(), timeLimitMinutes=10, maxAttempts=2,
                      shuffleQuestions=True, shuffleOptions=True)
    definition.update(extra.pop('definition', {}))
    return dict(version=0, revision=api.read('demo').revision, groupId=G, subjectId=S, definition=definition, studentIds=['s1', 's2'], publish=True) | extra


def world(client, publish=True, questions=None, **extra):
    """Admin client, the English teacher (t2), the parent and the pupils s1 and s2, and a course test assigned to s1 and s2."""
    teacher, parent = staffed(client)
    create(client, 'student')
    create(client, 'student', login='other-pupil', grants=[dict(workspace='demo', view='student', entityIds=['s2'])])
    quiz = teacher.put('/api/journal/quizzes/test-one?workspace=demo', json=course_body(questions or [question(teacher)], publish=publish, **extra))
    assert quiz.status_code == 200, quiz.text
    return quiz.json(), teacher, parent, signed('student'), signed('other-pupil')


def solve(pupil, quiz, student='s1', choice='a', answers=None):
    """The pupil's finished attempt (a single question with the key 'a')."""
    begun = start(pupil, quiz, studentId=student)
    assert begun.status_code == 200, begun.text
    done = answer(pupil, begun.json(), answers or {'choice': dict(selected=[choice])}, True)
    assert done.status_code == 200, done.text
    return done.json()


def release(teacher, quiz):
    closed = action(teacher, quiz, 'close')
    assert closed.status_code == 200, closed.text
    released = action(teacher, closed.json(), 'release')
    assert released.status_code == 200, released.text
    return released.json()


def key_of(c, day=DAY, subject=ENGLISH, group='10A'):
    return card(c, day, subject, group)['lessonKey']


def tbody(key, day=DAY, **extra):
    body = dict(lessonKey=key, day=day, revision=api.read('demo').revision, pick='last', publish=False)
    if 'assessmentId' not in extra: body['newWork'] = dict(name='Контрольная')
    return body | extra


def preview(c, body, quiz='test-one'):
    return c.post(f'/api/journal/quizzes/{quiz}/transfer/preview?workspace=demo', json=body)


def prepare(c, body, quiz='test-one', overwrite=(), request=None):
    """The body of a confirmed transfer: the shown preview's token; a refused preview has none."""
    seen = ok(preview(c, body, quiz))
    return body | dict(requestId=request or str(uuid4()), token=seen['token'] or '0' * 64, overwrite=list(overwrite))


def post(c, payload, quiz='test-one'):
    return c.post(f'/api/journal/quizzes/{quiz}/transfer?workspace=demo', json=payload)


def send(c, body, quiz='test-one', **options):
    return post(c, prepare(c, body, quiz, **options), quiz)


def summary(c, quiz='test-one'):
    return ok(c.get(f'/api/journal/quizzes/{quiz}/transfer?workspace=demo'))


def rows(seen):
    return {r['studentId']: r for r in seen['rows']}


def outcomes(seen):
    return [(r['studentId'], r['outcome'], r['value']) for r in seen['rows']]


def inbox(c):
    return c.get('/api/notifications/inbox').json()['unread']


def grades(c, key, student='s1', day=DAY):
    """The student's grades in the draft of that lesson, as {work: score}."""
    record = next(r for r in entry(c, key, day=day)['document']['content']['records'] if r['studentId'] == student)
    return {g['assessmentId']: g['score'] for g in record['grades']}


def published_grades(parent, student='s1'):
    return [{g['assessmentId']: g['score'] for g in row['record']['grades']} for row in report(parent, student).json()['rows']]


def test_course_quiz_reaches_pupils_without_touching_the_journal(client):
    quiz, teacher, parent, pupil, other = world(client)
    before = stored_versions()
    assert before == []
    assert quiz['scope'] == 'course' and quiz['canManage'] and quiz['canTransfer'] and quiz['groupId'] == G and quiz['subjectId'] == S
    for reader in [pupil, parent, teacher]:
        items = reader.get('/api/journal/quizzes?workspace=demo').json()['items']
        assert [(i['id'], i['course']) for i in items] == [('test-one', True)]
    # No lesson is needed on that day: the pupil starts and finishes, and the teacher is notified.
    sent = solve(pupil, quiz)
    assert sent['status'] == 'reviewed' and attempt(teacher, sent)['score'] == 2
    assert len([n for n in teacher.get('/api/notifications/inbox').json()['items'] if n['action'] == 'quiz.submitted']) == 1
    assert pupil.get('/api/journal/quizzes/test-one?workspace=demo&studentId=s1').json()['scope'] == 'course'
    # The per-attempt transfer needs a lesson; a course test goes through the results table.
    refused = teacher.get(f'/api/journal/quizzes/test-one/attempts/{sent["id"]}/grading?workspace=demo')
    assert refused.status_code == 409 and 'таблицы результатов' in refused.json()['detail']
    assert stored_versions() == before


def test_course_quiz_assignment_rights(client):
    teacher, _ = staffed(client)
    create(client, 'teacher', login='unrelated', grants=[dict(workspace='demo', view='teacher', entityIds=['t3'])])
    create(client, 'teacher', login='lead', grants=[dict(workspace='demo', view='teacher', entityIds=['t1'])])
    lead('t1')
    bank = question(teacher)
    put = lambda c, **extra: c.put('/api/journal/quizzes/test-one?workspace=demo', json=course_body([bank], **extra))
    assert put(signed('unrelated')).status_code == 403
    assert put(signed('lead')).status_code == 403
    assert put(teacher, groupId='nowhere').status_code == 404
    # A pupil of another group cannot be assigned.
    outside = put(teacher, studentIds=['s1', 's3'])
    assert outside.status_code == 422 and 'действующим ученикам' in outside.json()['detail']
    assert put(client, publish=False).status_code == 200
    assert stored_versions() == []
    # A lesson or a course, exactly one of them.
    whole = course_body([bank])
    lesson = {k: v for k, v in whole.items() if k not in ['groupId', 'subjectId']}
    for odd in [whole | dict(lessonKey='a' * 64, day=DAY), {k: v for k, v in whole.items() if k != 'groupId'}, lesson, lesson | dict(lessonKey='a' * 64)]:
        assert teacher.put('/api/journal/quizzes/other?workspace=demo', json=odd).status_code == 422


def test_course_quiz_survives_a_change_of_the_lesson_schedule(client):
    quiz, teacher, _, pupil, _ = world(client)
    resave(lambda s: setattr(s, 'datedLessons', [DatedLesson(id='replacement', name='Замена', kind='change', placementId='eng', sourceDate=date(2026, 8, 17), onDate=date(2026, 8, 17), teacherIds=['t1'])]))
    assert start(pupil, quiz, studentId='s1').status_code == 200
    assert teacher.get('/api/journal/quizzes/test-one?workspace=demo').json()['canManage']
    resave(lambda s: setattr(s, 'placements', [p for p in s.placements if p.id != 'eng']))
    assert teacher.get('/api/journal/quizzes/test-one?workspace=demo').json()['canTransfer']


def test_course_roster_endpoint(client):
    teacher, _ = staffed(client)
    create(client, 'teacher', login='unrelated', grants=[dict(workspace='demo', view='teacher', entityIds=['t3'])])
    url = '/api/journal/quiz-courses?workspace=demo&groupId=%s&subjectId=%s'
    found = ok(teacher.get(url % (G, S)))
    assert [(s['id'], s['name']) for s in found['students']] == [('s1', 'Ученик 1 (пример)'), ('s2', 'Ученик 2 (пример)')] and found['revision'] == api.read('demo').revision
    assert signed('unrelated').get(url % (G, S)).status_code == 403
    assert teacher.get(url % ('nowhere', S)).status_code == 404


def test_draft_transfer_stays_hidden_then_publication_reaches_the_family(client):
    quiz, teacher, parent, pupil, other = world(client, definition=dict(journalCategory='sor'))
    first = solve(pupil, quiz, 's1', 'a')
    solve(other, quiz, 's2', 'b')
    key = key_of(teacher)
    seen, unread, results = report(parent).json(), inbox(parent), attempt(parent, first)
    shown = ok(preview(teacher, tbody(key)))
    assert shown['allowed'] and outcomes(shown) == [('s1', 'write', 2), ('s2', 'write', 0)] and shown['work']['maxScore'] == 2 and shown['work']['category'] == 'sor'
    done = ok(send(teacher, tbody(key)))
    assert done.pop('version') == 1 and done == dict(status='saved', lessonKey=key, day=DAY, assessmentId=shown['work']['id'], publishedVersion=None, published=False,
                                                    counts=dict(written=2, updated=0, overwritten=0, same=0, kept=0, skipped=0))
    draft = entry(teacher, key)
    assert [(a['id'], a['maxScore'], a['category']) for a in draft['document']['content']['assessments']] == [(shown['work']['id'], 2, 'sor')]
    assert grades(teacher, key, 's1') == {shown['work']['id']: 2} and grades(teacher, key, 's2') == {shown['work']['id']: 0}
    assert report(parent).json() == seen and inbox(parent) == unread and attempt(parent, first) == results
    # Publishing is possible once the review is open to the family.
    refused = send(teacher, tbody(key, publish=True))
    assert refused.status_code == 409 and 'разбор' in refused.json()['detail']
    release(teacher, quiz)
    record = entry(teacher, key)
    record['document']['content']['topic'] = 'Тема только в черновике'
    assert write(teacher, record).status_code == 200
    publication = ok(preview(teacher, tbody(key, publish=True)))
    assert publication['otherPending'] and outcomes(publication) == [('s1', 'same', 2), ('s2', 'same', 0)]
    done = ok(send(teacher, tbody(key, publish=True)))
    assert done['status'] == 'saved' and done['published'] and done['publishedVersion'] is not None
    visible = report(parent).json()['rows'][0]
    assert visible['record']['grades'][0]['score'] == 2 and visible['content']['topic'] == '' and inbox(parent) > unread
    assert entry(teacher, key)['document']['content']['topic'] == 'Тема только в черновике'


def test_transfer_into_a_lesson_that_has_no_journal_entry_yet(client):
    quiz, teacher, parent, pupil, other = world(client)
    solve(pupil, quiz, 's1', 'a'), solve(other, quiz, 's2', 'b')
    release(teacher, quiz)
    key = key_of(teacher, NEXT)
    assert stored_versions() == []
    done = ok(send(teacher, tbody(key, NEXT, publish=True)))
    assert done['status'] == 'saved' and done['published']
    visible = report(parent).json()['rows']
    assert [r['lesson']['date'] for r in visible] == [NEXT] and visible[0]['record']['grades'][0]['score'] == 2 and visible[0]['record']['attendance'] == 'unmarked'
    with api.connect() as db:
        stored = json.loads(db.execute('SELECT document FROM journal_entries WHERE lesson_key=?', (key,)).fetchone()[0])
    assert [r['attendance'] for r in stored['content']['records']] == ['unmarked', 'unmarked'] and stored['content']['topic'] == ''


def test_existing_work_converts_to_its_own_maximum(client):
    quiz, teacher, parent, pupil, other = world(client)
    solve(pupil, quiz, 's1', 'a'), solve(other, quiz, 's2', 'b')
    first = lesson_with(teacher, DAY, [journal_work('a', 10)], {})
    shown = ok(preview(teacher, tbody(first['lessonKey'], assessmentId='a')))
    assert [(r['studentId'], r['score'], r['maxScore'], r['value'], r['scaled']) for r in shown['rows']] == [('s1', 2, 2, 10, True), ('s2', 0, 2, 0, True)]
    assert ok(send(teacher, tbody(first['lessonKey'], assessmentId='a')))['counts']['written'] == 2
    assert grades(teacher, first['lessonKey'], 's1') == {'a': 10} and grades(teacher, first['lessonKey'], 's2') == {'a': 0}
    # A work with an odd maximum is rounded to hundredths and never above the maximum.
    odd = lesson_with(teacher, NEXT, [journal_work('b', 3)], {})
    third = ok(preview(teacher, tbody(odd['lessonKey'], NEXT, assessmentId='b')))
    assert rows(third)['s1']['value'] == 3 and rows(third)['s2']['value'] == 0


def test_retry_and_rerun(client):
    quiz, teacher, parent, pupil, other = world(client)
    solve(pupil, quiz, 's1', 'a'), solve(other, quiz, 's2', 'b')
    release(teacher, quiz)
    key = key_of(teacher)
    payload = prepare(teacher, tbody(key, publish=True))
    first = ok(post(teacher, payload))
    versions, count, unread = stored_versions(), history_count(teacher, key), inbox(parent)
    assert first['status'] == 'saved' and unread > 0
    # The same request returns the first result and writes nothing, even after the journal moved on.
    assert ok(post(teacher, payload)) == first
    assert post(teacher, payload | dict(pick='best')).status_code == 409
    assert stored_versions() == versions and history_count(teacher, key) == count and inbox(parent) == unread
    # A fresh request finds the same work and the same marks: nothing to write.
    again = ok(send(teacher, tbody(key, publish=True)))
    assert again['status'] == 'unchanged' and again['counts']['same'] == 2 and again['version'] == first['version']
    assert stored_versions() == versions and history_count(teacher, key) == count and inbox(parent) == unread
    assert len(summary(teacher)['runs']) == 1


def test_teacher_mark_is_kept_without_a_choice_and_replaced_with_one(client):
    quiz, teacher, parent, pupil, other = world(client)
    solve(pupil, quiz, 's1', 'a'), solve(other, quiz, 's2', 'b')
    first = lesson_with(teacher, DAY, [journal_work('a', 10)], dict(a=dict(s1=5)))
    key = first['lessonKey']
    record = entry(teacher, key)
    record['document']['content']['records'][0]['grades'][0]['comment'] = 'Ответил устно'
    assert write(teacher, record).status_code == 200
    shown = ok(preview(teacher, tbody(key, assessmentId='a')))
    assert outcomes(shown) == [('s1', 'conflict', 10), ('s2', 'write', 0)] and rows(shown)['s1']['draft'] == dict(score=5, label='')
    # Naming a pupil whose mark is not in conflict is refused, and nothing is written.
    before = stored_versions()
    assert send(teacher, tbody(key, assessmentId='a'), overwrite=['s2']).status_code == 422
    assert send(teacher, tbody(key, assessmentId='a'), overwrite=['nobody']).status_code == 422
    assert stored_versions() == before
    kept = ok(send(teacher, tbody(key, assessmentId='a')))
    assert kept['counts']['kept'] == 1 and kept['counts']['written'] == 1 and grades(teacher, key, 's1') == {'a': 5} and grades(teacher, key, 's2') == {'a': 0}
    replaced = ok(send(teacher, tbody(key, assessmentId='a'), overwrite=['s1']))
    assert replaced['counts']['overwritten'] == 1 and replaced['counts']['kept'] == 0
    grade = next(r for r in entry(teacher, key)['document']['content']['records'] if r['studentId'] == 's1')['grades'][0]
    assert (grade['score'], grade['comment']) == (10, 'Ответил устно')


def test_mark_changed_after_preview_is_not_overwritten(client):
    quiz, teacher, parent, pupil, other = world(client)
    solve(pupil, quiz, 's1', 'a')
    first = lesson_with(teacher, DAY, [journal_work('a', 10)], {})
    payload = prepare(teacher, tbody(first['lessonKey'], assessmentId='a'))
    t = ok(table(teacher))
    ok(table_save(teacher, [edit(t, DAY, ('s1', 'a', 3))]))
    before = stored_versions()
    refused = post(teacher, payload)
    assert refused.status_code == 409 and 'ещё раз' in refused.json()['detail']
    assert stored_versions() == before and grades(teacher, first['lessonKey']) == {'a': 3}
    assert summary(teacher)['marks'] == []


def test_corrected_result_updates_own_mark_but_not_a_teacher_edit(client):
    quiz, teacher, parent, pupil, other = world(client)
    sent = solve(pupil, quiz, 's1', 'a')
    key = key_of(teacher)
    work = ok(send(teacher, tbody(key)))['assessmentId']
    review = lambda current, score: ok(teacher.post(f'/api/journal/quizzes/test-one/attempts/{current["id"]}/review?workspace=demo',
                                                    json=dict(version=current['version'], marks={'choice': dict(score=score, comment='Пересмотр')})))
    corrected = review(sent, 1)
    assert summary(teacher)['marks'][0]['stale']
    shown = ok(preview(teacher, tbody(key)))
    assert outcomes(shown)[0] == ('s1', 'update', 1)
    assert ok(send(teacher, tbody(key)))['counts']['updated'] == 1 and grades(teacher, key) == {work: 1}
    assert not summary(teacher)['marks'][0]['stale']
    # The teacher then types another mark in the course table: a later correction asks before replacing it.
    t = ok(table(teacher))
    ok(table_save(teacher, [edit(t, DAY, ('s1', work, 2))]))
    review(corrected, 0)
    assert ok(preview(teacher, tbody(key)))['rows'][0]['outcome'] == 'conflict'
    assert ok(send(teacher, tbody(key)))['counts']['kept'] == 1 and grades(teacher, key) == {work: 2}


def test_closed_period_refuses_preview_and_transfer(client, monkeypatch):
    monkeypatch.setattr(journal_periods, 'now', lambda: TODAY)
    quiz, teacher, parent, pupil, other = world(client)
    solve(pupil, quiz, 's1', 'a')
    key = key_of(teacher)
    closed = close(client, period(client))
    before = stored_versions()
    shown = ok(preview(teacher, tbody(key)))
    assert shown['allowed'] is False and closed['name'] in shown['reason'] and shown['rows'] == [] and shown['token'] == ''
    refused = send(teacher, tbody(key))
    assert refused.status_code == 403 and closed['name'] in refused.json()['detail']
    assert stored_versions() == before and summary(teacher)['marks'] == [] and summary(teacher)['runs'] == []
    # The other lesson of the course is open.
    assert ok(send(teacher, tbody(key_of(teacher, NEXT), NEXT)))['status'] == 'saved'


def test_frozen_lesson_keeps_its_roster(client):
    teacher, parent = staffed(client)
    create(client, 'student')
    first = lesson_with(teacher, DAY, [journal_work('a', 2)], {}, publish=True)
    resave(lambda s: s.students.append(Student(id='new', name='Новый ученик', classId='c-demo-a')))
    create(client, 'student', login='newbie', grants=[dict(workspace='demo', view='student', entityIds=['new'])])
    posted = teacher.put('/api/journal/quizzes/test-one?workspace=demo', json=course_body([question(teacher)], studentIds=['s1', 'new']))
    assert posted.status_code == 200, posted.text
    solve(signed('student'), posted.json(), 's1', 'a'), solve(signed('newbie'), posted.json(), 'new', 'a')
    roster = entry(teacher, first['lessonKey'])['document']['lesson']['students']
    shown = ok(preview(teacher, tbody(first['lessonKey'], assessmentId='a')))
    assert sorted((r['studentId'], r['outcome']) for r in shown['rows']) == [('new', 'skip'), ('s1', 'write')]
    assert 'в составе' in rows(shown)['new']['reason']
    assert ok(send(teacher, tbody(first['lessonKey'], assessmentId='a')))['counts'] == dict(written=1, updated=0, overwritten=0, same=0, kept=0, skipped=1)
    assert entry(teacher, first['lessonKey'])['document']['lesson']['students'] == roster and grades(teacher, first['lessonKey']) == {'a': 2}


def test_pupil_who_left_is_skipped_and_keeps_the_old_mark(client):
    quiz, teacher, parent, pupil, other = world(client)
    solve(pupil, quiz, 's1', 'a'), solve(other, quiz, 's2', 'a')
    first = lesson_with(teacher, DAY, [journal_work('a', 10)], dict(a=dict(s2=6)))
    key = first['lessonKey']
    resave(lambda s: setattr(next(p for p in s.students if p.id == 's2'), 'active', False), date.fromisoformat(DAY))
    assert write(teacher, entry(teacher, key, syncRoster='true'), syncRoster=True).status_code == 200
    shown = ok(preview(teacher, tbody(key)))
    assert outcomes(shown)[0][:2] == ('s1', 'write') and rows(shown)['s2']['outcome'] == 'skip' and not rows(shown)['s2']['enrolled']
    ok(send(teacher, tbody(key)))
    assert grades(teacher, key, 's2') == {'a': 6} and list(grades(teacher, key, 's1').values()) == [2]


def test_rows_without_a_finished_attempt_are_skipped(client):
    teacher, _ = staffed(client)
    create(client, 'student')
    create(client, 'student', login='other-pupil', grants=[dict(workspace='demo', view='student', entityIds=['s2'])])
    essay = question(teacher, 'essay', kind='essay', text='Объясните', points=3, options=[], correctIds=[])
    posted = teacher.put('/api/journal/quizzes/test-one?workspace=demo', json=course_body([question(teacher), essay])).json()
    pupil, other, key = signed('student'), signed('other-pupil'), key_of(teacher)
    assert [(r['outcome'], r['reason']) for r in ok(preview(teacher, tbody(key)))['rows']] == [('skip', 'Тест не начат.')] * 2
    sent = solve(pupil, posted, 's1', answers={'choice': dict(selected=['a']), 'essay': dict(text='Ответ')})
    begun = start(other, posted, studentId='s2').json()
    shown = ok(preview(teacher, tbody(key)))
    assert [(r['studentId'], r['outcome'], r['reason']) for r in shown['rows']] == [('s1', 'skip', 'Ждёт ручной проверки.'), ('s2', 'skip', 'В тесте идёт попытка.')]
    assert shown['counts']['skip'] == 2 and begun['status'] == 'active'
    # Nothing to transfer: no revision, no history, no work.
    nothing = ok(send(teacher, tbody(key)))
    assert nothing['status'] == 'unchanged' and stored_versions() == [] and summary(teacher)['runs'] == []
    ok(teacher.post(f'/api/journal/quizzes/test-one/attempts/{sent["id"]}/review?workspace=demo', json=dict(version=sent['version'], marks={'essay': dict(score=3)})))
    assert outcomes(ok(preview(teacher, tbody(key))))[0] == ('s1', 'write', 5)


def test_pick_the_last_or_the_best_attempt(client):
    quiz, teacher, parent, pupil, other = world(client)
    solve(pupil, quiz, 's1', 'a')
    solve(pupil, quiz, 's1', 'b')
    key = key_of(teacher)
    assert rows(ok(preview(teacher, tbody(key, pick='last'))))['s1']['value'] == 0
    best = rows(ok(preview(teacher, tbody(key, pick='best'))))['s1']
    assert best['value'] == 2 and best['attemptNumber'] == 1


def test_substitute_lesson_is_refused_and_its_draft_stays_private(client):
    quiz, teacher, parent, pupil, other = world(client)
    solve(pupil, quiz, 's1', 'a')
    first = lesson_with(teacher, DAY, [journal_work('a', 10)], dict(a=dict(s1=5)))
    key = first['lessonKey']
    resave(lambda s: setattr(s, 'datedLessons', [DatedLesson(id='replacement', name='Замена', kind='change', placementId='eng', sourceDate=date(2026, 8, 17), onDate=date(2026, 8, 17), teacherIds=['t1'])]))
    lessons = ok(teacher.get(f'/api/journal/quizzes/test-one/transfer/lessons?workspace=demo&day={DAY}'))['lessons']
    assert [(l['lessonKey'], l['editable'], l['works']) for l in lessons] == [(key, False, [])] and 'другой учитель' in lessons[0]['reason']
    shown = ok(preview(teacher, tbody(key, assessmentId='a')))
    assert shown['allowed'] is False and shown['rows'] == [] and shown['work'] is None and 'другой учитель' in shown['reason']
    before = stored_versions()
    assert send(teacher, tbody(key, assessmentId='a')).status_code == 403 and stored_versions() == before


def test_lessons_of_the_day_list_the_works(client):
    quiz, teacher, parent, pupil, other = world(client)
    solve(pupil, quiz, 's1', 'a')
    lesson_with(teacher, DAY, [journal_work('a', 10), journal_work('t', kind='label')], dict(a=dict(s1=5)))
    listed = lambda day: ok(teacher.get(f'/api/journal/quizzes/test-one/transfer/lessons?workspace=demo&day={day}'))['lessons']
    found = listed(DAY)
    assert len(found) == 1 and found[0]['editable'] and found[0]['version'] == 1 and found[0]['hasUnpublished'] and not found[0]['hasPublished']
    assert [(w['id'], w['kind'], w['maxScore']) for w in found[0]['works']] == [('a', 'points', 10), ('t', 'label', 10)]
    future = listed(FUTURE)
    assert len(future) == 1 and not future[0]['editable'] and 'ещё не прошёл' in future[0]['reason'] and future[0]['works'] == []
    assert listed('2026-08-18') == []
    assert signed('student').get(f'/api/journal/quizzes/test-one/transfer/lessons?workspace=demo&day={DAY}').status_code == 403


def test_refused_targets(client):
    quiz, teacher, parent, pupil, other = world(client)
    solve(pupil, quiz, 's1', 'a')
    release(teacher, quiz)
    key = key_of(teacher)
    labelled = lesson_with(teacher, NEXT, [journal_work('t', kind='label')], {})
    foreign = lesson_with(client, DAY, [journal_work('m')], {}, subject=MATH, group=MATH_GROUP)
    future = key_of(teacher, FUTURE)
    withdrawn = lesson_with(teacher, '2026-08-31', [journal_work('h')], dict(h=dict(s1=4)), publish=True)
    assert teacher.post('/api/journal/withdraw?workspace=demo', json=dict(lessonKey=withdrawn['lessonKey'], version=withdrawn['version'])).status_code == 200
    before = stored_versions()
    cases = [(tbody(future, FUTURE), 422, 'ещё не прошёл'), (tbody(labelled['lessonKey'], NEXT, assessmentId='t'), 422, 'с баллами'),
             (tbody(foreign['lessonKey']), 403, 'не относится'), (tbody(withdrawn['lessonKey'], '2026-08-31', assessmentId='h', publish=True), 409, 'скрыт'),
             (tbody(key, assessmentId='gone'), 409, 'Работа удалена'), (tbody(key, revision=api.read('demo').revision - 1), 409, 'Расписание изменилось')]
    for body, status, text in cases:
        if body['revision'] == api.read('demo').revision:
            shown = ok(preview(teacher, body))
            assert shown['allowed'] is False and text in shown['reason'] and shown['rows'] == []
        else: assert preview(teacher, body).status_code == 409
        refused = post(teacher, body | dict(requestId=str(uuid4()), token='0' * 64, overwrite=[]))
        assert refused.status_code == status and text in refused.json()['detail']
    assert stored_versions() == before
    # A hidden lesson still takes a draft transfer.
    assert ok(send(teacher, tbody(withdrawn['lessonKey'], '2026-08-31', assessmentId='h'), overwrite=['s1']))['status'] == 'saved'


def test_unsynced_published_roster_refuses_publication_but_takes_a_draft(client, monkeypatch):
    quiz, teacher, parent, pupil, other = world(client)
    solve(pupil, quiz, 's1', 'a')
    release(teacher, quiz)
    # On the day itself the lesson is not frozen yet: a new pupil reaches the draft roster but not the publication.
    for module in [journal, timetable_history]: monkeypatch.setattr(module, 'today', lambda: date(2026, 8, 17))
    first = lesson_with(teacher, DAY, [journal_work('a', 10)], {}, publish=True)
    resave(lambda s: s.students.append(Student(id='new', name='Новый ученик', classId='c-demo-a')))
    assert write(teacher, entry(teacher, first['lessonKey'], syncRoster='true'), syncRoster=True).status_code == 200
    refused = send(teacher, tbody(first['lessonKey'], assessmentId='a', publish=True))
    assert refused.status_code == 409 and 'состав' in refused.json()['detail']
    assert ok(send(teacher, tbody(first['lessonKey'], assessmentId='a')))['status'] == 'saved'


@pytest.mark.parametrize('login', ['parent', 'student', 'unrelated', 'lead', 'substitute', 'owner'])
def test_permissions(client, login):
    teacher, _ = staffed(client)
    create(client, 'student')
    grant = lambda entity: [dict(workspace='demo', view='teacher', entityIds=[entity])]
    workload = lambda extra: resave(lambda s: setattr(next(w for w in s.workloads if w.id == 'w0'), 'additionalTeacherIds', extra))
    if login == 'owner':
        # The owner teaches the course for a while, assigns the test and then leaves the workload.
        create(client, 'teacher', login='owner', grants=grant('t0'))
        workload(['t0'])
        author = signed('owner')
    else: author = teacher
    assert author.put('/api/journal/quizzes/test-one?workspace=demo', json=course_body([question(author)])).status_code == 200
    key = key_of(teacher)
    if login == 'owner': workload([])
    elif login in ['unrelated', 'lead', 'substitute']: create(client, 'teacher', login=login, grants=grant('t1'))
    if login == 'lead': lead('t1')
    if login == 'substitute':
        resave(lambda s: setattr(s, 'datedLessons', [DatedLesson(id='replacement', name='Замена', kind='change', placementId='eng', sourceDate=date(2026, 8, 17), onDate=date(2026, 8, 17), teacherIds=['t1'])]))
    payload = prepare(client, tbody(key))
    before = stored_versions()
    reader = signed(login)
    assert reader.post('/api/journal/quizzes/test-one/transfer/preview?workspace=demo', json=tbody(key)).status_code == 403
    assert post(reader, payload).status_code == 403
    assert reader.get(f'/api/journal/quizzes/test-one/transfer/lessons?workspace=demo&day={DAY}').status_code == 403
    assert stored_versions() == before
    # What was transferred is read by those who manage the test, the author included; the administrator transfers.
    assert reader.get('/api/journal/quizzes/test-one/transfer?workspace=demo').status_code == (200 if login == 'owner' else 403)
    assert ok(preview(client, tbody(key)))['allowed']


def test_history_names_who_transferred(client):
    quiz, teacher, parent, pupil, other = world(client)
    solve(pupil, quiz, 's1', 'a')
    key = key_of(teacher)
    ok(send(teacher, tbody(key)))
    found = summary(teacher)
    name = next(u['name'] for u in client.get('/api/access/users').json() if u['login'] == 'teacher')
    assert found['runs'][0]['actor'] == name != 'Администратор' and found['runs'][0]['lessonKey'] == key and found['runs'][0]['counts']['written'] == 1
    assert [(m['studentId'], m['actor'], m['value'], m['day']) for m in found['marks']] == [('s1', name, 2, DAY)] and found['defaultDay'] == DAY and found['defaultLessonKey'] == key
    assert teacher.get('/api/journal/history', params=dict(workspace='demo', lessonKey=key)).json()[0]['actor'] == name


def test_publication_warns_about_other_pending_marks(client):
    quiz, teacher, parent, pupil, other = world(client)
    solve(pupil, quiz, 's1', 'a')
    release(teacher, quiz)
    first = lesson_with(teacher, DAY, [journal_work('a', 2), journal_work('b', 10)], dict(b=dict(s1=7)))
    key = first['lessonKey']
    assert ok(preview(teacher, tbody(key, assessmentId='a', publish=True)))['otherPending']
    ok(send(teacher, tbody(key, assessmentId='a', publish=True)))
    assert published_grades(parent) == [{'a': 2, 'b': 7}]


def test_transfer_reads_on_a_database_without_the_new_table(client):
    quiz, teacher, parent, pupil, other = world(client)
    solve(pupil, quiz, 's1', 'a')
    with api.connect() as db:
        db.execute('DROP TABLE IF EXISTS quiz_transfer_marks')
        db.execute('DROP TABLE IF EXISTS homework_requests')
    assert summary(teacher)['marks'] == []
    with api.connect() as db:
        db.execute('DROP TABLE IF EXISTS quiz_transfer_marks')
    assert ok(send(teacher, tbody(key_of(teacher))))['status'] == 'saved'


def test_cancelled_lesson_takes_no_marks(client):
    quiz, teacher, parent, pupil, other = world(client)
    solve(pupil, quiz, 's1', 'a')
    first = lesson_with(teacher, DAY, [journal_work('a', 10)], {})
    resave(lambda s: setattr(s, 'datedLessons', [DatedLesson(id='cancel', name='Отмена', kind='cancel', placementId='eng', sourceDate=date(2026, 8, 17), onDate=date(2026, 8, 17))]))
    found = ok(teacher.get(f'/api/journal/quizzes/test-one/transfer/lessons?workspace=demo&day={DAY}'))['lessons']
    assert [(l['lessonKey'], l['editable']) for l in found] == [(first['lessonKey'], False)] and 'отменён' in found[0]['reason']
    before = stored_versions()
    shown = ok(preview(teacher, tbody(first['lessonKey'], assessmentId='a')))
    assert shown['allowed'] is False and shown['rows'] == []
    assert send(teacher, tbody(first['lessonKey'], assessmentId='a')).status_code == 403 and stored_versions() == before


@pytest.mark.parametrize('stage', ['draft', 'withdrawn'])
def test_unpublished_or_withdrawn_quiz_transfers_nothing(client, stage):
    quiz, teacher, parent, pupil, other = world(client, publish=stage == 'withdrawn')
    if stage == 'withdrawn': assert action(teacher, quiz, 'withdraw').status_code == 200
    key = key_of(teacher)
    shown = ok(preview(teacher, tbody(key)))
    assert shown['allowed'] is False and 'не опубликован' in shown['reason'] and shown['rows'] == []
    assert send(teacher, tbody(key)).status_code == 409 and stored_versions() == []


def test_copy_moves_a_test_between_a_course_and_a_lesson(client):
    quiz, teacher, parent, pupil, other = world(client)
    key, bank = key_of(teacher), dict(id='choice', version=1)
    lesson = {k: v for k, v in course_body([bank]).items() if k not in ['groupId', 'subjectId']} | dict(lessonKey=key, day=DAY, copyFrom='test-one')
    made = teacher.put('/api/journal/quizzes/lesson-copy?workspace=demo', json=lesson)
    assert made.status_code == 200, made.text
    assert (made.json()['scope'], made.json()['lessonKey']) == ('lesson', key) and made.json()['document']['definition']['variants'][0]['questions'][0]['id'] == 'choice'
    again = teacher.put('/api/journal/quizzes/course-copy?workspace=demo', json=course_body([bank], copyFrom='lesson-copy'))
    assert again.status_code == 200 and again.json()['scope'] == 'course' and again.json()['lessonKey'].startswith('course:')
    # A published course test cannot turn into a lesson test in place; a lesson test transfers through the same results table.
    assert teacher.put('/api/journal/quizzes/test-one?workspace=demo', json=dict(lesson, version=quiz['version'], copyFrom=None)).status_code == 409
    solve(pupil, made.json(), 's1', 'a')
    found = summary(teacher, 'lesson-copy')
    assert (found['defaultDay'], found['defaultLessonKey']) == (DAY, key)
    assert outcomes(ok(preview(teacher, tbody(key), 'lesson-copy')))[0] == ('s1', 'write', 2)
    assert ok(send(teacher, tbody(key), 'lesson-copy'))['counts']['written'] == 1


def test_default_day_is_the_latest_lesson_day_of_the_course(client):
    quiz, teacher, parent, pupil, other = world(client)
    # The test closes on a Tuesday; the course has English on Mondays only, so the day to offer is the Monday before.
    found = summary(teacher)
    assert (found['defaultDay'], found['defaultLessonKey']) == ('2026-08-31', '')
    day = ok(teacher.get(f'/api/journal/quizzes/test-one/transfer/lessons?workspace=demo&day={found["defaultDay"]}'))['lessons']
    assert len(day) == 1 and day[0]['editable']


def test_a_work_of_the_test_turned_into_a_text_scale_is_refused_by_the_preview(client):
    quiz, teacher, parent, pupil, other = world(client)
    solve(pupil, quiz, 's1', 'a'), solve(other, quiz, 's2', 'b')
    release(teacher, quiz)
    key = key_of(teacher)
    assert ok(send(teacher, tbody(key)))['status'] == 'saved'
    # The teacher turns the work this transfer created into a text scale and clears its marks.
    record = entry(teacher, key)
    record['document']['content']['assessments'][0]['kind'] = 'label'
    for r in record['document']['content']['records']: r['grades'] = []
    assert write(teacher, record).status_code == 200
    before = stored_versions()
    for publish in [False, True]:
        shown = ok(preview(teacher, tbody(key, publish=publish)))
        assert shown['allowed'] is False and shown['rows'] == [] and 'текстовой' in shown['reason']
    refused = post(teacher, tbody(key) | dict(requestId=str(uuid4()), token='0' * 64, overwrite=[]))
    assert refused.status_code == 422 and 'текстовой' in refused.json()['detail'] and stored_versions() == before
    # With the points restored the same transfer goes through again.
    record = entry(teacher, key)
    record['document']['content']['assessments'][0]['kind'] = 'points'
    assert write(teacher, record).status_code == 200
    assert ok(send(teacher, tbody(key)))['counts']['written'] == 2


@pytest.mark.parametrize('kind', ['cancel', 'change'])
def test_default_day_skips_a_day_the_teacher_cannot_enter_marks(client, kind):
    quiz, teacher, parent, pupil, other = world(client)
    held = lambda day, id: DatedLesson(id=id, name='Изменение', kind=kind, placementId='eng', sourceDate=day, onDate=day, **(dict(teacherIds=['t1']) if kind == 'change' else {}))
    resave(lambda s: setattr(s, 'datedLessons', [held(date(2026, 8, 31), 'last')]))
    found = summary(teacher)
    assert (found['defaultDay'], found['defaultLessonKey']) == ('2026-08-24', '')
    day = ok(teacher.get(f'/api/journal/quizzes/test-one/transfer/lessons?workspace=demo&day={found["defaultDay"]}'))['lessons']
    assert len(day) == 1 and day[0]['editable']
    # When no lesson of the last two weeks takes marks, the latest day with a lesson is still offered, and the panel shows why it is closed.
    resave(lambda s: setattr(s, 'datedLessons', [held(date(2026, 8, 31), 'last'), held(date(2026, 8, 24), 'before')]))
    assert summary(teacher)['defaultDay'] == '2026-08-31'
