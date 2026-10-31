"""Course table: pupils against the works of one course, dated rows, roles, and the per-lesson atomic save and publication."""
from datetime import date, datetime, timezone
from types import SimpleNamespace
from uuid import uuid4
import pytest
from fastapi import HTTPException
from backend import api, course_table, journal, journal_periods, journal_reads, timetable_history
from backend.models import DatedLesson, Student
from backend.timetable_history import History
from test_access import client, enable, create, signed
from test_journal import DAY, entry, write, report
from test_journal_history import clock, resave, card, ENGLISH, NEXT  # noqa: F401
from test_journal_periods import period, close
from test_assessment_categories import put_config, shares

URL = '/api/journal/course-table'
G, S = 'g-a', 'sub2'
MATH, MATH_GROUP = 'Математика', 'Математика · группа A'
END = '2026-09-01'


def work(id, max=10, weight=1, category='fo', kind='points'):
    return dict(id=id, name='Работа ' + id, kind=kind, maxScore=max, weight=weight, category=category)


def course(client, day=DAY, works=(), marks=None, publish=False, subject=ENGLISH, group='10A', **content):
    """Open the lesson of that day in the editor, give it works and marks {work: {student: score or label}}, save it as a draft or publication."""
    record = entry(client, card(client, day, subject, group)['lessonKey'], day=day)
    body = record['document']['content']
    body['assessments'] = list(works)
    marks = marks or {}
    for r in body['records']:
        r['grades'] = [dict(assessmentId=w['id'], score=None if isinstance(marks[w['id']][r['studentId']], str) else marks[w['id']][r['studentId']],
                            label=marks[w['id']][r['studentId']] if isinstance(marks[w['id']][r['studentId']], str) else '', comment='')
                       for w in works if r['studentId'] in marks.get(w['id'], {})]
    body.update(content)
    response = write(client, record, publish=publish)
    assert response.status_code == 200, response.text
    return response.json()


def table(c, group=G, subject=S, start=DAY, end=END):
    return c.get(URL, params=dict(workspace='demo', groupId=group, subjectId=subject, start=start, end=end))


def ok(response):
    assert response.status_code == 200, response.text
    return response.json()


def save(c, lessons, publish=False, group=G, subject=S, revision=None, request=None):
    body = dict(requestId=request or str(uuid4()), groupId=group, subjectId=subject, publish=publish, lessons=lessons,
                revision=api.read('demo').revision if revision is None else revision)
    return c.post(URL + '/save?workspace=demo', json=body)


def cell(student, work, value):
    return dict(studentId=student, assessmentId=work, score=None if isinstance(value, str) else value, label=value if isinstance(value, str) else '')


def column(t, day):
    return next(l for l in t['lessons'] if l['day'] == day)


def edit(t, day, *cells):
    lesson = column(t, day)
    return dict(lessonKey=lesson['lessonKey'], day=day, version=lesson['version'], cells=[cell(*c) for c in cells])


def pupil(t, id):
    return next(p for p in t['pupils'] if p['id'] == id)


def mark(t, student, day, work):
    """The pupil's mark in that work of that day's lesson, or None."""
    here = [i for i, l in enumerate(t['lessons']) if l['day'] == day]
    index = next(i for i, w in enumerate(t['works']) if w['lesson'] in here and w['id'] == work)
    return pupil(t, student)['marks'].get(str(index))


def staffed(client):
    """Admin client, the English teacher (t2) as `teacher` and the parent of s1."""
    enable(client)
    create(client, 'teacher')
    create(client, 'parent')
    return signed('teacher'), signed('parent')


def lead(teacher_id, class_id='c-demo-a'):
    school = api.read('demo')
    school.classes = [c.model_copy(update=dict(classTeacherId=teacher_id)) if c.id == class_id else c for c in school.classes]
    api.save('demo', school)


def mathematics(client):
    """The maths teacher (t0) and the class teacher of 10A (t1), as accounts."""
    enable(client)
    create(client, 'teacher', login='math', grants=[dict(workspace='demo', view='teacher', entityIds=['t0'])])
    create(client, 'teacher', login='lead', grants=[dict(workspace='demo', view='teacher', entityIds=['t1'])])
    lead('t1')
    return signed('math'), signed('lead')


def stored_versions():
    with api.connect() as db:
        return db.execute('SELECT lesson_key,version,published_version FROM journal_entries ORDER BY lesson_key').fetchall()


def history_count(c, key):
    return len(c.get('/api/journal/history', params=dict(workspace='demo', lessonKey=key)).json())


def test_table_lists_works_across_lessons(client, clock):
    teacher, _ = staffed(client)
    before = api.read('demo')
    # The later lesson is saved first: the columns follow the dates, not the order of saving.
    course(teacher, NEXT, [work('c', 40, 1, 'soch')], dict(c=dict(s2=33.5)))
    course(teacher, DAY, [work('a'), work('b', 20, 2, 'sor')], dict(a=dict(s1=8, s2=6), b=dict(s1=15)))
    course(client, DAY, [work('m')], dict(m=dict(s1=9)), subject=MATH, group=MATH_GROUP)
    t = ok(table(teacher))
    assert t['canEdit'] and t['scope'] == 'course' and t['source'] == 'draft'
    assert [l['day'] for l in t['lessons']] == [DAY, NEXT]
    assert [(w['lesson'], w['id'], w['category'], w['maxScore'], w['weight']) for w in t['works']] == [(0, 'a', 'fo', 10, 1), (0, 'b', 'sor', 20, 2), (1, 'c', 'soch', 40, 1)]
    assert [c['id'] for c in t['categories']] == ['fo', 'sor', 'soch']
    assert [p['id'] for p in t['pupils']] == ['s1', 's2']
    assert (mark(t, 's1', DAY, 'a')['score'], mark(t, 's2', DAY, 'a')['score'], mark(t, 's1', DAY, 'b')['score'], mark(t, 's2', NEXT, 'c')['score']) == (8, 6, 15, 33.5)
    assert mark(t, 's2', DAY, 'b') is None and mark(t, 's1', NEXT, 'c') is None
    # Nothing of the other course, in any field.
    assert 'Работа m' not in str(t)
    # The period filter leaves out the lesson of the next week.
    first = ok(table(teacher, end=DAY))
    assert [l['day'] for l in first['lessons']] == [DAY] and {w['id'] for w in first['works']} == {'a', 'b'}
    assert api.read('demo') == before


def test_dated_rows(client, clock):
    teacher, _ = staffed(client)
    def arrive(state):
        state.students.append(Student(id='new', name='Новый ученик', classId='c-demo-a'))
        next(s for s in state.students if s.id == 's2').active = False
    resave(arrive, date(2026, 8, 24))
    course(teacher, DAY, [work('a')], dict(a=dict(s1=8, s2=6)))
    course(teacher, NEXT, [work('a')], dict(a=dict(s1=7, new=9)))
    t = ok(table(teacher))
    assert (pupil(t, 's2')['lessons'], pupil(t, 's2')['from'], pupil(t, 's2')['to']) == ([0], None, '2026-08-23')
    assert (pupil(t, 'new')['lessons'], pupil(t, 'new')['from'], pupil(t, 'new')['to']) == ([1], NEXT, None)
    assert (pupil(t, 's1')['lessons'], pupil(t, 's1')['from'], pupil(t, 's1')['to']) == ([0, 1], None, None)


def test_pupil_without_marks_still_gets_a_dated_row(client, clock):
    teacher, _ = staffed(client)
    resave(lambda s: s.students.append(Student(id='new', name='Новый ученик', classId='c-demo-a')), date(2026, 8, 24))
    course(teacher, DAY, [work('a')], dict(a=dict(s1=8)))
    t = ok(table(teacher))
    assert pupil(t, 'new')['from'] == NEXT and pupil(t, 'new')['lessons'] == [] and pupil(t, 'new')['marks'] == {}


def test_frozen_publication_keeps_its_roster_in_the_table(client, clock):
    teacher, _ = staffed(client)
    course(teacher, DAY, [work('a')], dict(a=dict(s1=8)), publish=True)
    resave(lambda s: s.students.append(Student(id='new', name='Новый ученик', classId='c-demo-a')))
    t = ok(table(teacher))
    lesson = column(t, DAY)
    assert lesson['rosterFrozen'] and lesson['editable'] and lesson['reason'] == ''
    assert pupil(t, 's1')['lessons'] == [0] and pupil(t, 'new')['lessons'] == []


def test_class_teacher_reads_only_published_marks_of_own_class(client, clock):
    enable(client)
    create(client, 'teacher', login='math', grants=[dict(workspace='demo', view='teacher', entityIds=['t0'])])
    create(client, 'teacher', login='lead', grants=[dict(workspace='demo', view='teacher', entityIds=['t1'])])
    lead('t1')
    math, head = signed('math'), signed('lead')
    marks = dict(m=dict(s1=7, s3=9))
    course(math, DAY, [work('m')], marks, subject=MATH, group=MATH_GROUP)
    t = ok(table(head, 'g-math', 'sub0'))
    # A draft is invisible; the class teacher has the pupil of the own class and nobody else.
    assert t['canEdit'] is False and t['scope'] == 'class' and t['lessons'] == [] and [p['id'] for p in t['pupils']] == ['s1']
    course(math, DAY, [work('m')], marks, publish=True, subject=MATH, group=MATH_GROUP)
    t = ok(table(head, 'g-math', 'sub0'))
    assert mark(t, 's1', DAY, 'm')['score'] == 7 and [p['id'] for p in t['pupils']] == ['s1'] and not column(t, DAY)['editable']
    assert 's3' not in str(t) and 'Ученик 3' not in str(t) and all(m['score'] != 9 for p in t['pupils'] for m in p['marks'].values())
    assert pupil(t, 's1')['sharePercentage'] == 70
    assert save(head, [edit(t, DAY, ('s1', 'm', 5))], group='g-math', subject='sub0').status_code == 403
    # The course teacher still sees both pupils.
    full = ok(table(math, 'g-math', 'sub0'))
    assert [p['id'] for p in full['pupils']] == ['s1', 's3']
    assert ok(head.get(URL + '/courses?workspace=demo'))['courses'][0]['kind'] == 'class'


def test_admin_and_class_teacher_read_the_publication_not_a_newer_draft(client, clock):
    math, head = mathematics(client)
    course(math, DAY, [work('m')], dict(m=dict(s1=8, s3=9)), publish=True, subject=MATH, group=MATH_GROUP)
    t = ok(table(math, 'g-math', 'sub0'))
    # The teacher changes a mark and adds a work; both stay drafts.
    ok(save(math, [edit(t, DAY, ('s1', 'm', 3))], group='g-math', subject='sub0'))
    record = entry(math, column(t, DAY)['lessonKey'])
    record['document']['content']['assessments'].append(work('draft'))
    assert write(math, record).status_code == 200
    own = ok(table(math, 'g-math', 'sub0'))
    assert [w['id'] for w in own['works']] == ['m', 'draft'] and mark(own, 's1', DAY, 'm') == dict(score=3, label='', comment='', unpublished=True)
    for reader in (client, head):
        t = ok(table(reader, 'g-math', 'sub0'))
        assert [w['id'] for w in t['works']] == ['m'] and mark(t, 's1', DAY, 'm') == dict(score=8, label='', comment='')
        assert pupil(t, 's1')['sharePercentage'] == 80 and not column(t, DAY)['hasUnpublished']


def test_class_teacher_sees_marks_by_the_class_of_the_day(client, clock):
    math, head = mathematics(client)
    # s3 moves from 10D to 10A on NEXT: the 10A class teacher gets what was earned in 10A and nothing from before.
    resave(lambda s: setattr(next(p for p in s.students if p.id == 's3'), 'classId', 'c-demo-a'), date(2026, 8, 24))
    course(math, DAY, [work('m')], dict(m=dict(s1=7, s3=9)), publish=True, subject=MATH, group=MATH_GROUP)
    course(math, NEXT, [work('m')], dict(m=dict(s1=5, s3=4)), publish=True, subject=MATH, group=MATH_GROUP)
    t = ok(table(head, 'g-math', 'sub0'))
    assert [p['id'] for p in t['pupils']] == ['s1', 's3']
    assert pupil(t, 's3')['lessons'] == [1] and mark(t, 's3', DAY, 'm') is None and mark(t, 's3', NEXT, 'm')['score'] == 4
    assert (pupil(t, 's1')['sharePercentage'], pupil(t, 's3')['sharePercentage']) == (60, 40)
    full = ok(table(math, 'g-math', 'sub0'))
    assert mark(full, 's3', DAY, 'm')['score'] == 9 and pupil(full, 's3')['sharePercentage'] == 65


def test_class_teacher_reads_the_whole_class_courses_of_the_own_class(client, clock):
    teacher, _ = staffed(client)
    create(client, 'teacher', login='lead', grants=[dict(workspace='demo', view='teacher', entityIds=['t1'])])
    lead('t1')
    head = signed('lead')
    course(teacher, DAY, [work('a')], dict(a=dict(s1=8)), publish=True)
    course(teacher, NEXT, [work('a')], dict(a=dict(s1=6)))
    listed = ok(head.get(URL + '/courses?workspace=demo'))['courses']
    # The courses of the own class 10A, the mixed maths group by its 10A pupil, and the course t1 teaches; nothing of 10D.
    assert [(c['groupId'], c['subjectId'], c['kind'], c['canEdit']) for c in listed] == [
        ('g-a', 'sub2', 'class', False), ('g-a', 'sub3', 'class', False), ('g-math', 'sub0', 'class', False), ('g-core', 'sub1', 'course', True)]
    t = ok(table(head))
    assert t['scope'] == 'class' and t['canEdit'] is False and [l['day'] for l in t['lessons']] == [DAY] and mark(t, 's1', DAY, 'a')['score'] == 8
    assert table(head, 'g-d', 'sub2').status_code == 403
    assert save(head, [edit(t, DAY, ('s1', 'a', 5))]).status_code == 403


def test_moved_lesson_is_read_by_its_published_date(client, clock):
    teacher, _ = staffed(client)
    first = course(teacher, DAY, [work('a')], dict(a=dict(s1=5)), publish=True)
    resave(lambda s: setattr(s, 'datedLessons', [DatedLesson(id='move', name='Перенос', kind='change', placementId='eng', sourceDate=date(2026, 8, 17),
                                                          onDate=date(2026, 8, 18), startTime='14:00', endTime='14:40')]))
    assert write(teacher, entry(teacher, first['lessonKey'], syncRoster='true'), syncRoster=True).status_code == 200
    moved = '2026-08-18'
    # The draft is dated after the move, the publication still on the old day: each reader finds the lesson by the copy it reads.
    assert [l['day'] for l in ok(table(client, start=DAY, end=DAY))['lessons']] == [DAY]
    assert ok(table(client, start=moved, end=moved))['lessons'] == []
    assert [l['day'] for l in ok(table(teacher, start=moved, end=moved))['lessons']] == [moved]
    assert ok(table(teacher, start=DAY, end=DAY))['lessons'] == []


def test_course_rows_of_a_snapshot_stay_bounded(client, clock):
    snap = api.journal.snapshots.get('demo')
    for i in range(40): snap.course_rows(f'g{i}', 's')(date.fromisoformat(DAY))
    assert len(snap.course_days) <= 32 and ('g39', 's') in snap.course_days and ('g0', 's') not in snap.course_days


def test_rosters_of_a_database_before_dated_rosters_follow_the_group_of_the_day(client, clock):
    snap = api.journal.snapshots.get('demo')
    start, end = date.fromisoformat(DAY), date.fromisoformat(END)
    # Without dated rosters nobody has a start or an end, and whoever is in the group when the period ends has a row.
    before = SimpleNamespace(entities=snap.entities, history=History(snap.state))
    assert course_table.spans(before, 'g-a', start, end) == {'s1': (None, None), 's2': (None, None)}
    assert set(course_table.spans(snap, 'g-a', start, end)) == {'s1', 's2'}


@pytest.mark.parametrize('login', ['unrelated', 'parent', 'student'])
def test_read_permissions(client, clock, login):
    teacher, _ = staffed(client)
    create(client, 'teacher', login='unrelated', grants=[dict(workspace='demo', view='teacher', entityIds=['t1'])])
    create(client, 'student')
    course(teacher, DAY, [work('a')], dict(a=dict(s1=8)), publish=True)
    assert table(signed(login)).status_code == 403


def test_admin_and_course_lists(client, clock):
    teacher, _ = staffed(client)
    create(client, 'teacher', login='unrelated', grants=[dict(workspace='demo', view='teacher', entityIds=['t1'])])
    course(teacher, DAY, [work('a')], dict(a=dict(s1=8)))
    admin = ok(table(client))
    # The administrator reads published marks only.
    assert admin['canEdit'] is False and admin['source'] == 'published' and admin['scope'] == 'admin' and admin['lessons'] == []
    own = ok(teacher.get(URL + '/courses?workspace=demo'))
    assert [(c['groupId'], c['subjectId'], c['kind'], c['canEdit']) for c in own['courses']] == [('g-a', 'sub2', 'course', True), ('g-d', 'sub2', 'course', True)]
    assert own['yearStart'] == '2026-08-17' and own['revision'] == api.read('demo').revision
    other = ok(signed('unrelated').get(URL + '/courses?workspace=demo'))
    assert [(c['groupId'], c['subjectId']) for c in other['courses']] == [('g-core', 'sub1')]
    everything = ok(client.get(URL + '/courses?workspace=demo'))['courses']
    assert len(everything) == 6 and {c['kind'] for c in everything} == {'admin'}
    assert table(teacher, 'g-core', 'sub1').status_code == 403 and table(teacher, 'g-a', 'nope').status_code == 404
    assert table(client, end='2028-01-01').status_code == 422


def test_local_mode_without_accounts_reads_as_administrator(client, clock):
    t = ok(table(client))
    assert t['canEdit'] is False and t['scope'] == 'admin'


def published_course(teacher):
    """Both English lessons, published; fo 8/10 and 6/10 (weights 1 and 3), sor 15/20 (weight 2), soch 30/40."""
    course(teacher, DAY, [work('a'), work('b', 20, 2, 'sor')], dict(a=dict(s1=8), b=dict(s1=15)), publish=True)
    course(teacher, NEXT, [work('c', 10, 3), work('d', 40, 1, 'soch')], dict(c=dict(s1=6), d=dict(s1=30)), publish=True)


def family_share(parent):
    return report(parent).json()['summary']['subjects'][0]['sharePercentage']


def test_share_average_matches_family_report(client, clock):
    teacher, parent = staffed(client)
    published_course(teacher)
    t = ok(table(client))
    # fo (80·1 + 60·3)/4 = 65, sor 75, soch 75 with the default shares 25/25/50.
    assert pupil(t, 's1')['sharePercentage'] == 72.5 == family_share(parent)
    assert {c['id']: c['percentage'] for c in pupil(t, 's1')['categories']} == {'fo': 65, 'sor': 75, 'soch': 75}
    assert pupil(t, 's2')['sharePercentage'] is None
    # A text mark and a work without weight show but add nothing.
    extra = entry(teacher, card(teacher, DAY)['lessonKey'])
    content = extra['document']['content']
    content['assessments'] += [work('t', kind='label'), work('z', 10, 0)]
    content['records'][0]['grades'] += [dict(assessmentId='t', score=None, label='A', comment=''), dict(assessmentId='z', score=1, label='', comment='')]
    assert write(teacher, extra, publish=True).status_code == 200
    again = ok(table(client))
    assert mark(again, 's1', DAY, 't')['label'] == 'A' and mark(again, 's1', DAY, 'z')['score'] == 1
    assert pupil(again, 's1')['sharePercentage'] == 72.5 == family_share(parent)


def test_block_rule_gives_no_average(client, clock):
    teacher, _ = staffed(client)
    course(teacher, DAY, [work('a'), work('b', 20, 2, 'sor')], dict(a=dict(s1=8, s2=5), b=dict(s1=15)), publish=True)
    assert ok(table(client))['pupils'][0]['sharePercentage'] == 77.5
    assert put_config(client, policy=dict(shares(25, 25, 50), missing='block')).status_code == 200
    t = ok(table(client))
    first = pupil(t, 's1')
    assert first['sharePercentage'] is None and first['blocked'] and first['missing'] == ['soch']
    # The marks themselves are still there.
    assert mark(t, 's1', DAY, 'a')['score'] == 8


def test_table_opens_after_schema_marker_is_lower(client, clock):
    teacher, _ = staffed(client)
    course(teacher, DAY, [work('a')], dict(a=dict(s1=8)))
    with api.connect() as db:
        db.execute("DROP INDEX journal_entry_course")
        db.execute("UPDATE journal_projection_versions SET version=? WHERE name='journal_schema'", (journal_reads.SCHEMA - 1,))
        db.commit()
    assert mark(ok(table(teacher)), 's1', DAY, 'a')['score'] == 8
    with api.connect() as db:
        assert db.execute("SELECT version FROM journal_projection_versions WHERE name='journal_schema'").fetchone() == (journal_reads.SCHEMA,)
        assert db.execute("SELECT 1 FROM sqlite_master WHERE name='journal_entry_course'").fetchone()


def test_draft_save_is_hidden_then_publish_keeps_other_drafts(client, clock):
    teacher, parent = staffed(client)
    record = entry(teacher, card(teacher)['lessonKey'])
    record['document']['content'].update(topic='Старая тема', assessments=[work('a')])
    for r in record['document']['content']['records']:
        r['attendance'] = 'present'
    published = write(teacher, record, publish=True).json()
    record = entry(teacher, published['lessonKey'])
    record['document']['content']['topic'] = 'Черновик темы'
    record['document']['content']['records'][1]['attendance'] = 'absent'
    assert write(teacher, record).status_code == 200
    inbox = lambda: parent.get('/api/notifications/inbox').json()['unread']
    feed = lambda: len(parent.get('/api/journal/grades/feed', params=dict(workspace='demo', studentId='s1')).json()['items'])
    seen, notices, events = report(parent).json(), inbox(), feed()
    t = ok(table(teacher))
    saved = ok(save(teacher, [edit(t, DAY, ('s1', 'a', 7))]))
    assert [(s['lessonKey'], s['published']) for s in saved['saved']] == [(published['lessonKey'], False)] and saved['conflicts'] == []
    assert report(parent).json() == seen and inbox() == notices and feed() == events
    t = ok(table(teacher))
    assert mark(t, 's1', DAY, 'a') == dict(score=7, label='', comment='', unpublished=True) and column(t, DAY)['hasUnpublished']
    done = ok(save(teacher, [edit(t, DAY)], publish=True))
    assert done['saved'][0]['published'] is True
    visible = report(parent).json()['rows'][0]
    assert visible['record']['grades'][0]['score'] == 7 and visible['content']['topic'] == 'Старая тема'
    assert inbox() > notices and feed() == events + 1
    # What the teacher had not published stays a draft.
    after = entry(teacher, published['lessonKey'])
    assert after['document']['content']['topic'] == 'Черновик темы' and after['document']['content']['records'][1]['attendance'] == 'absent'
    assert after['hasPublished'] and after['version'] == after['publishedVersion'] + 1
    t = ok(table(teacher))
    assert 'unpublished' not in mark(t, 's1', DAY, 'a') and not column(t, DAY)['hasUnpublished']


def test_publishing_a_lesson_never_published_leaves_attendance_and_topic_in_the_draft(client, clock):
    teacher, parent = staffed(client)
    record = entry(teacher, card(teacher)['lessonKey'])
    record['document']['content'].update(topic='Тема в черновике', assessments=[work('a')])
    record['document']['content']['records'][0]['attendance'] = 'present'
    assert write(teacher, record).status_code == 200
    t = ok(table(teacher))
    assert t['lessons'][0]['publishedVersion'] is None
    ok(save(teacher, [edit(t, DAY, ('s1', 'a', 9))], publish=True))
    visible = report(parent).json()['rows'][0]
    assert visible['record']['grades'][0]['score'] == 9 and visible['record']['attendance'] == 'unmarked' and visible['content']['topic'] == ''
    after = entry(teacher, record['lessonKey'])
    assert after['document']['content']['topic'] == 'Тема в черновике' and after['document']['content']['records'][0]['attendance'] == 'present'


def test_publishing_marks_adds_one_revision_when_nothing_else_is_in_the_draft(client, clock):
    teacher, _ = staffed(client)
    first = course(teacher, DAY, [work('a')], dict(a=dict(s1=5)), publish=True)
    t = ok(table(teacher))
    before = history_count(teacher, first['lessonKey'])
    done = ok(save(teacher, [edit(t, DAY, ('s1', 'a', 9))], publish=True))['saved'][0]
    assert history_count(teacher, first['lessonKey']) == before + 1 and done['version'] == done['publishedVersion']


def test_publishing_everything_pending_leaves_a_hidden_lesson_hidden(client, clock):
    teacher, parent = staffed(client)
    first = course(teacher, DAY, [work('a')], dict(a=dict(s1=5)), publish=True)
    course(teacher, NEXT, [work('a')], dict(a=dict(s1=6)))
    assert teacher.post('/api/journal/withdraw?workspace=demo', json=dict(lessonKey=first['lessonKey'], version=first['version'])).status_code == 200
    assert report(parent).json()['rows'] == []
    t = ok(table(teacher))
    assert column(t, DAY)['withdrawn'] and column(t, DAY)['hasUnpublished'] and not column(t, NEXT)['withdrawn']
    # What the Publish button sends: every lesson with pending marks, and the cells typed in the lesson that has them.
    result = ok(save(teacher, [edit(t, DAY), edit(t, NEXT, ('s1', 'a', 7))], publish=True))
    assert [s['lessonKey'] for s in result['saved']] == [column(t, NEXT)['lessonKey']] and result['unchanged'] == [column(t, DAY)['lessonKey']]
    assert [r['lesson']['date'] for r in report(parent).json()['rows']] == [NEXT]
    # A mark typed in the hidden lesson is the teacher's own decision to show it again.
    fresh = ok(table(teacher))
    assert ok(save(teacher, [edit(fresh, DAY, ('s1', 'a', 4))], publish=True))['saved'][0]['published']
    assert not column(ok(table(teacher)), DAY)['withdrawn'] and len(report(parent).json()['rows']) == 2


def test_save_is_audited_only_when_something_is_saved(client, clock):
    teacher, _ = staffed(client)
    course(teacher, DAY, [work('a')], dict(a=dict(s1=5)))
    t = ok(table(teacher))
    audited = lambda: [a for a in client.get('/api/access/audit').json() if a['action'] == 'journal.course_saved']
    ok(save(teacher, [edit(t, DAY, ('s1', 'a', 5))]))
    assert audited() == []
    ok(save(teacher, [edit(t, DAY, ('s1', 'a', 6))]))
    assert len(audited()) == 1


def test_partial_conflict_saves_the_rest(client, clock):
    teacher, _ = staffed(client)
    course(teacher, DAY, [work('a')], dict(a=dict(s1=5)))
    course(teacher, NEXT, [work('a')], dict(a=dict(s1=5)))
    t = ok(table(teacher))
    # The lesson editor saves the second lesson meanwhile.
    other = entry(teacher, column(t, NEXT)['lessonKey'], day=NEXT)
    other['document']['content']['topic'] = 'Тема'
    assert write(teacher, other).status_code == 200
    result = ok(save(teacher, [edit(t, DAY, ('s1', 'a', 9)), edit(t, NEXT, ('s1', 'a', 10))]))
    assert [s['lessonKey'] for s in result['saved']] == [column(t, DAY)['lessonKey']]
    assert [(c['lessonKey'], c['status']) for c in result['conflicts']] == [(column(t, NEXT)['lessonKey'], 409)]
    fresh = ok(table(teacher))
    assert (mark(fresh, 's1', DAY, 'a')['score'], mark(fresh, 's1', NEXT, 'a')['score']) == (9, 5)
    assert ok(save(teacher, [edit(fresh, NEXT, ('s1', 'a', 10))]))['conflicts'] == []
    assert mark(ok(table(teacher)), 's1', NEXT, 'a')['score'] == 10


def test_closed_period_blocks_that_lesson_only(client, clock, monkeypatch):
    monkeypatch.setattr(journal_periods, 'now', lambda: datetime(2026, 9, 1, 12, tzinfo=timezone.utc))
    teacher, _ = staffed(client)
    course(teacher, DAY, [work('a')], dict(a=dict(s1=5)))
    course(teacher, NEXT, [work('a')], dict(a=dict(s1=5)))
    closed = close(client, period(client))
    t = ok(table(teacher))
    assert not column(t, DAY)['editable'] and closed['name'] in column(t, DAY)['reason'] and column(t, NEXT)['editable']
    result = ok(save(teacher, [edit(t, DAY, ('s1', 'a', 9)), edit(t, NEXT, ('s1', 'a', 9))]))
    assert [(c['lessonKey'], c['status']) for c in result['conflicts']] == [(column(t, DAY)['lessonKey'], 403)] and closed['name'] in result['conflicts'][0]['message']
    assert [s['lessonKey'] for s in result['saved']] == [column(t, NEXT)['lessonKey']]
    fresh = ok(table(teacher))
    assert (mark(fresh, 's1', DAY, 'a')['score'], mark(fresh, 's1', NEXT, 'a')['score']) == (5, 9)


def test_write_to_frozen_past_lesson(client, clock):
    teacher, parent = staffed(client)
    first = course(teacher, DAY, [work('a')], dict(a=dict(s1=5)), publish=True)
    resave(lambda s: s.students.append(Student(id='new', name='Новый ученик', classId='c-demo-a')))
    t = ok(table(teacher))
    result = ok(save(teacher, [edit(t, DAY, ('s1', 'a', 9))], publish=True))
    assert result['conflicts'] == [] and report(parent).json()['rows'][0]['record']['grades'][0]['score'] == 9
    after = entry(teacher, first['lessonKey'])
    assert after['document']['lesson']['students'] == first['document']['lesson']['students'] and after['document']['lesson']['start'] == first['document']['lesson']['start']
    # The pupil added afterwards has no place in that lesson.
    t = ok(table(teacher))
    refused = ok(save(teacher, [edit(t, DAY, ('s1', 'a', 8), ('new', 'a', 4))], publish=True))
    assert refused['saved'] == [] and refused['conflicts'][0]['status'] == 422
    assert [(c['studentId'], c['assessmentId']) for c in refused['conflicts'][0]['cells']] == [('new', 'a')]
    assert mark(ok(table(teacher)), 's1', DAY, 'a')['score'] == 9


def test_cell_problems_reject_the_whole_lesson(client, clock):
    teacher, _ = staffed(client)
    course(teacher, DAY, [work('a'), work('t', kind='label')], dict(a=dict(s1=5)))
    course(teacher, NEXT, [work('a')], dict(a=dict(s1=5)))
    t = ok(table(teacher))
    before = stored_versions()
    bad = edit(t, DAY, ('s1', 'a', 6), ('s2', 'a', 11), ('s3', 'a', 4), ('s1', 't', 4), ('s2', 'gone', 3))
    result = ok(save(teacher, [bad, edit(t, NEXT, ('s2', 'a', 7))]))
    assert [s['lessonKey'] for s in result['saved']] == [column(t, NEXT)['lessonKey']]
    problems = result['conflicts'][0]
    assert problems['status'] == 422 and {(c['studentId'], c['assessmentId']) for c in problems['cells']} == {('s2', 'a'), ('s3', 'a'), ('s1', 't'), ('s2', 'gone')}
    assert [row for row in stored_versions() if row[0] == column(t, DAY)['lessonKey']] == [row for row in before if row[0] == column(t, DAY)['lessonKey']]
    fresh = ok(table(teacher))
    assert (mark(fresh, 's1', DAY, 'a')['score'], mark(fresh, 's2', NEXT, 'a')['score']) == (5, 7)


def test_pupil_who_left_keeps_the_mark_and_gets_no_cell(client, clock):
    teacher, _ = staffed(client)
    course(teacher, DAY, [work('a')], dict(a=dict(s1=5, s2=6)))
    resave(lambda s: setattr(next(p for p in s.students if p.id == 's2'), 'active', False), date.fromisoformat(DAY))
    t = ok(table(teacher))
    assert write(teacher, entry(teacher, column(t, DAY)['lessonKey'], syncRoster='true'), syncRoster=True).status_code == 200
    t = ok(table(teacher))
    # The mark stays and counts, read-only: the lesson roster no longer lists the pupil as enrolled.
    assert column(t, DAY)['editable'] and pupil(t, 's2')['lessons'] == [] and mark(t, 's2', DAY, 'a')['score'] == 6 and pupil(t, 's2')['sharePercentage'] == 60
    before = stored_versions()
    result = ok(save(teacher, [edit(t, DAY, ('s1', 'a', 8), ('s2', 'a', 9))]))
    assert result['saved'] == [] and result['conflicts'][0]['status'] == 422
    assert [(c['studentId'], c['assessmentId']) for c in result['conflicts'][0]['cells']] == [('s2', 'a')]
    assert stored_versions() == before


def test_label_on_a_points_work_is_a_cell_problem(client, clock):
    teacher, _ = staffed(client)
    course(teacher, DAY, [work('a')], dict(a=dict(s1=5)))
    t = ok(table(teacher))
    result = ok(save(teacher, [edit(t, DAY, ('s1', 'a', 'хорошо'), ('s2', 'a', 4))]))
    assert result['saved'] == [] and result['conflicts'][0]['status'] == 422
    assert [(c['studentId'], c['assessmentId']) for c in result['conflicts'][0]['cells']] == [('s1', 'a')]


def test_future_lesson_refused(client, monkeypatch):
    monkeypatch.setattr(journal, 'today', lambda: date(2026, 8, 20))
    monkeypatch.setattr(timetable_history, 'today', lambda: date(2026, 8, 20))
    teacher, _ = staffed(client)
    course(teacher, DAY, [work('a')], dict(a=dict(s1=5)))
    course(teacher, NEXT, [work('a')])
    t = ok(table(teacher))
    assert column(t, DAY)['editable'] and not column(t, NEXT)['editable']
    result = ok(save(teacher, [edit(t, NEXT, ('s1', 'a', 9)), edit(t, DAY, ('s1', 'a', 6))]))
    assert [(c['lessonKey'], c['status']) for c in result['conflicts']] == [(column(t, NEXT)['lessonKey'], 422)] and len(result['saved']) == 1


def test_lesson_needing_roster_review_is_refused(client, clock):
    teacher, _ = staffed(client)
    course(teacher, DAY, [work('a')], dict(a=dict(s1=5)))
    resave(lambda s: setattr(next(p for p in s.placements if p.id == 'eng'), 'period', 3))
    t = ok(table(teacher))
    assert not column(t, DAY)['editable'] and column(t, DAY)['reason']
    result = ok(save(teacher, [edit(t, DAY, ('s1', 'a', 9))]))
    assert result['saved'] == [] and result['conflicts'][0]['status'] == 409
    # The lesson editor brings it up to date; then marks can be written again.
    record = entry(teacher, column(t, DAY)['lessonKey'], syncRoster='true')
    assert write(teacher, record, syncRoster=True).status_code == 200
    t = ok(table(teacher))
    assert column(t, DAY)['editable'] and ok(save(teacher, [edit(t, DAY, ('s1', 'a', 9))]))['saved']


def test_unpublished_roster_sync_blocks_publication_only(client, monkeypatch):
    # On the day of the lesson its time is not frozen yet, so a changed time asks for a review in the lesson editor.
    for module in (journal, timetable_history):
        monkeypatch.setattr(module, 'today', lambda: date(2026, 8, 17))
    teacher, _ = staffed(client)
    course(teacher, DAY, [work('a')], dict(a=dict(s1=5)), publish=True)
    resave(lambda s: setattr(next(p for p in s.placements if p.id == 'eng'), 'period', 3))
    t = ok(table(teacher))
    assert not column(t, DAY)['editable']
    record = entry(teacher, column(t, DAY)['lessonKey'], syncRoster='true')
    assert write(teacher, record, syncRoster=True).status_code == 200
    t = ok(table(teacher))
    assert column(t, DAY)['editable']
    # The draft has the new time and the publication the old one: marks wait in the draft until the lesson is published there.
    refused = ok(save(teacher, [edit(t, DAY, ('s1', 'a', 9))], publish=True))
    assert refused['saved'] == [] and refused['conflicts'][0]['status'] == 409
    assert ok(save(teacher, [edit(t, DAY, ('s1', 'a', 9))]))['saved']
    assert mark(ok(table(teacher)), 's1', DAY, 'a') == dict(score=9, label='', comment='', unpublished=True)


def test_substitute_lesson(client, clock):
    teacher, _ = staffed(client)
    create(client, 'teacher', login='sub', grants=[dict(workspace='demo', view='teacher', entityIds=['t1'])])
    course(teacher, DAY, [work('a')], dict(a=dict(s1=5)), publish=True)
    course(teacher, NEXT, [work('a')], dict(a=dict(s1=6)))
    state = api.read('demo')
    state.datedLessons = [DatedLesson(id='sub', name='Замена', kind='change', placementId='eng', sourceDate=DAY, onDate=DAY, teacherIds=['t1'])]
    api.save('demo', state)
    t = ok(table(teacher))
    assert not column(t, DAY)['editable'] and column(t, DAY)['reason'] and column(t, NEXT)['editable']
    result = ok(save(teacher, [edit(t, DAY, ('s1', 'a', 9)), edit(t, NEXT, ('s1', 'a', 7))]))
    assert [(c['lessonKey'], c['status']) for c in result['conflicts']] == [(column(t, DAY)['lessonKey'], 403)] and len(result['saved']) == 1
    assert mark(ok(table(teacher)), 's1', DAY, 'a')['score'] == 5
    # The substitute is not a teacher of the course.
    sub = signed('sub')
    assert table(sub).status_code == 403 and save(sub, [edit(t, DAY, ('s1', 'a', 9))]).status_code == 403


def test_unpublished_draft_of_a_substitute_lesson_stays_hidden(client, clock):
    teacher, _ = staffed(client)
    course(teacher, DAY, [work('a')], dict(a=dict(s1=5)))
    state = api.read('demo')
    state.datedLessons = [DatedLesson(id='sub', name='Замена', kind='change', placementId='eng', sourceDate=DAY, onDate=DAY, teacherIds=['t1'])]
    api.save('demo', state)
    assert ok(table(teacher))['lessons'] == []


@pytest.mark.parametrize('login', ['admin', 'lead', 'unrelated', 'parent'])
def test_save_permissions(client, clock, login):
    teacher, _ = staffed(client)
    create(client, 'teacher', login='lead', grants=[dict(workspace='demo', view='teacher', entityIds=['t1'])])
    create(client, 'teacher', login='unrelated', grants=[dict(workspace='demo', view='teacher', entityIds=['t3'])])
    lead('t1')
    course(teacher, DAY, [work('a')], dict(a=dict(s1=5)), publish=True)
    t = ok(table(teacher))
    before = stored_versions()
    who = client if login == 'admin' else signed(login)
    assert save(who, [edit(t, DAY, ('s1', 'a', 9))]).status_code == 403
    assert stored_versions() == before


def test_other_course_lesson_is_refused_per_lesson(client, clock):
    teacher, _ = staffed(client)
    create(client, 'teacher', login='math', grants=[dict(workspace='demo', view='teacher', entityIds=['t0'])])
    course(teacher, DAY, [work('a')], dict(a=dict(s1=5)))
    mine = ok(table(teacher))
    course(signed('math'), DAY, [work('m')], dict(m=dict(s1=5)), subject=MATH, group=MATH_GROUP)
    foreign = ok(table(signed('math'), 'g-math', 'sub0'))
    before = stored_versions()
    # A key of another course, a key with no journal and a stale revision: nothing is written.
    result = ok(save(teacher, [edit(foreign, DAY, ('s1', 'm', 9)), dict(lessonKey='f' * 64, day=DAY, version=0, cells=[cell('s1', 'a', 1)])]))
    assert result['saved'] == [] and [c['status'] for c in result['conflicts']] == [403, 409]
    assert stored_versions() == before
    stale = save(teacher, [edit(mine, DAY, ('s1', 'a', 9))], revision=api.read('demo').revision + 1)
    assert stale.status_code == 409 and stored_versions() == before


def record_of(**extra):
    return dict(periodLocks=[], writable=True, needsSync=False, currentDate=DAY, futureLesson=False, document=dict(lesson=dict(date=DAY))) | extra


def test_the_rules_for_writing_marks_apply_in_a_fixed_order(client, clock):
    problem = lambda day_ok=True, current=None, **extra: course_table.editing_problem(record_of(**extra), day_ok, current)
    assert problem() is None
    # A closed period outranks everything else, then the right to write, then the schedule, then the calendar.
    locked = problem(periodLocks=[dict(name='1 четверть')], writable=False, needsSync=True)
    assert locked[0] == 403 and locked[1] not in (course_table.GONE, course_table.FOREIGN)
    assert problem(writable=False, currentDate=None, needsSync=True) == (403, course_table.GONE)
    assert problem(writable=False, needsSync=True) == (403, course_table.FOREIGN)
    assert problem(writable=False, current=dict(status='cancelled')) == (403, course_table.GONE)
    assert problem(needsSync=True, futureLesson=True)[0] == 409
    assert problem(currentDate='2026-08-18', futureLesson=True)[0] == 409
    assert problem(futureLesson=True, day_ok=False)[0] == 422
    assert problem(document=dict(lesson=dict(date='2026-09-02')), currentDate='2026-09-02')[0] == 422
    assert problem(day_ok=False)[0] == 422


def test_publication_refuses_a_roster_that_lost_a_pupil():
    lesson = dict(students=[])
    def content(*ids): return dict(assessments=[], records=[dict(studentId=id, grades=[], topic='') for id in ids])
    old = dict(lesson=lesson, content=content('s1', 's2'))
    with pytest.raises(HTTPException) as caught: course_table.publication(dict(lesson=lesson, content=content('s1')), old)
    assert caught.value.status_code == 409
    assert course_table.publication(dict(lesson=lesson, content=content('s2', 's1')), old)['content']['records'][0]['studentId'] == 's1'


def test_course_table_rechecks_the_account(client, clock, monkeypatch):
    teacher, _ = staffed(client)
    course(teacher, DAY, [work('a')], dict(a=dict(s1=5)))
    t = ok(table(teacher))
    before, original = stored_versions(), api.journal.reading
    def revoke(operation):
        # The account changes after the request was identified but before the read starts.
        with api.connect() as db: db.execute("UPDATE accounts SET version=version+1 WHERE login='teacher'")
        return original(operation)
    monkeypatch.setattr(api.journal, 'reading', revoke)
    assert table(teacher).status_code == 409 and signed('teacher').get(URL + '/courses?workspace=demo').status_code == 409
    monkeypatch.setattr(api.journal, 'reading', original)
    assert table(signed('teacher')).status_code == 200
    snapshots = api.journal.snapshots.get
    def revoke_writer(workspace):
        result = snapshots(workspace)
        with api.connect() as db: db.execute("UPDATE accounts SET version=version+1 WHERE login='teacher'")
        return result
    fresh = signed('teacher')
    monkeypatch.setattr(api.journal.snapshots, 'get', revoke_writer)
    assert save(fresh, [edit(t, DAY, ('s1', 'a', 9))]).status_code == 409 and stored_versions() == before
    monkeypatch.setattr(api.journal.snapshots, 'get', snapshots)
    assert ok(save(signed('teacher'), [edit(t, DAY, ('s1', 'a', 9))]))['saved']


def test_request_limits(client, clock):
    teacher, _ = staffed(client)
    course(teacher, DAY, [work('a')], dict(a=dict(s1=5)))
    t = ok(table(teacher))
    one = edit(t, DAY, ('s1', 'a', 9))
    assert save(teacher, [dict(one, lessonKey=f'{i:064x}') for i in range(11)]).status_code == 422
    assert save(teacher, [one, one]).status_code == 422
    assert save(teacher, [edit(t, DAY, ('s1', 'a', 9), ('s1', 'a', 8))]).status_code == 422
    assert save(teacher, [edit(t, DAY, ('s1', 'a', 11 ** 6))]).status_code == 422
    assert save(teacher, []).status_code == 422


def test_retry_returns_the_first_result(client, clock):
    teacher, _ = staffed(client)
    first = course(teacher, DAY, [work('a')], dict(a=dict(s1=5)), publish=True)
    t = ok(table(teacher))
    request = str(uuid4())
    before = history_count(teacher, first['lessonKey'])
    one = save(teacher, [edit(t, DAY, ('s1', 'a', 9))], publish=True, request=request)
    again = save(teacher, [edit(t, DAY, ('s1', 'a', 9))], publish=True, request=request)
    assert one.status_code == again.status_code == 200 and one.json() == again.json()
    revisions = history_count(teacher, first['lessonKey'])
    assert revisions > before
    fresh = ok(table(teacher))
    assert save(teacher, [edit(fresh, DAY, ('s1', 'a', 3))], publish=True, request=request).status_code == 409
    assert history_count(teacher, first['lessonKey']) == revisions


def test_no_real_change_bumps_nothing(client, clock):
    teacher, parent = staffed(client)
    course(teacher, DAY, [work('a')], dict(a=dict(s1=5)), publish=True)
    t = ok(table(teacher))
    before, notices = stored_versions(), parent.get('/api/notifications/inbox').json()['unread']
    same = ok(save(teacher, [edit(t, DAY, ('s1', 'a', 5))], publish=True))
    assert same['saved'] == [] and same['unchanged'] == [column(t, DAY)['lessonKey']]
    assert ok(save(teacher, [edit(t, DAY)]))['unchanged'] == [column(t, DAY)['lessonKey']]
    assert stored_versions() == before and parent.get('/api/notifications/inbox').json()['unread'] == notices


def test_clearing_a_mark_keeps_its_comment(client, clock):
    teacher, _ = staffed(client)
    record = entry(teacher, card(teacher)['lessonKey'])
    record['document']['content']['assessments'] = [work('a'), work('b')]
    record['document']['content']['records'][0]['grades'] = [dict(assessmentId='a', score=5, label='', comment='Молодец'), dict(assessmentId='b', score=6, label='', comment='')]
    assert write(teacher, record, publish=True).status_code == 200
    t = ok(table(teacher))
    ok(save(teacher, [edit(t, DAY, ('s1', 'a', 8))]))
    shown = entry(teacher, column(t, DAY)['lessonKey'])['document']['content']['records'][0]['grades']
    assert next(g for g in shown if g['assessmentId'] == 'a') == dict(assessmentId='a', score=8, label='', comment='Молодец')
    t = ok(table(teacher))
    ok(save(teacher, [edit(t, DAY, ('s1', 'a', None), ('s1', 'b', None))]))
    shown = entry(teacher, column(t, DAY)['lessonKey'])['document']['content']['records'][0]['grades']
    assert shown == [dict(assessmentId='a', score=None, label='', comment='Молодец')]
    # A zero is a mark; only an empty cell clears.
    t = ok(table(teacher))
    assert mark(t, 's1', DAY, 'a') == dict(score=None, label='', comment='Молодец', unpublished=True)
    ok(save(teacher, [edit(t, DAY, ('s1', 'b', 0))]))
    assert mark(ok(table(teacher)), 's1', DAY, 'b')['score'] == 0


def test_table_marks_reach_lesson_report_and_history(client, clock):
    teacher, parent = staffed(client)
    first = course(teacher, DAY, [work('a')], {}, publish=True)
    t = ok(table(teacher))
    ok(save(teacher, [edit(t, DAY, ('s1', 'a', 4))], publish=True))
    assert entry(teacher, first['lessonKey'])['document']['content']['records'][0]['grades'][0]['score'] == 4
    rows = parent.get('/api/journal/report', params=dict(workspace='demo', studentId='s1', start=DAY, end=END)).json()['rows']
    assert rows[0]['record']['grades'][0]['score'] == 4
    actions = [h['action'] for h in teacher.get('/api/journal/history', params=dict(workspace='demo', lessonKey=first['lessonKey'])).json()]
    assert 'journal.published' in actions[:2]
