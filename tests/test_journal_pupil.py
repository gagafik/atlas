"""The pupil screens read marks and attendance of published lessons in one answer: access, publication and numbers."""
import json
import pytest
from backend import api, final_grades, journal_pupil
from test_access import client, enable, create, signed
from test_journal import DAY, lesson, entry, write, graded
from test_absences import setup as absence_setup, send, decide, mark
from test_grade_feed import family, items, sign
from test_families import save as family_save
from seeding import insert_row

WEEK = '2026-08-24'


def period(reader, student='s1', **params):
    return reader.get('/api/journal/pupil-period', params=dict(workspace='demo', studentId=student, start='2026-08-01', end='2026-09-30') | params)


def publish(client, subject='Английский язык', day=DAY, shape=graded):
    result = write(client, shape(entry(client, lesson(client, subject, day)['lessonKey'], day=day)), publish=True)
    assert result.status_code == 200, result.text
    return result.json()


def mine(record):
    return next(r for r in record['document']['content']['records'] if r['studentId'] == 's1')


def exam_and_late(record):
    """An СОЧ work of 20 points and ten minutes of lateness for s1."""
    graded(record)
    content = record['document']['content']
    content['assessments'] = [dict(id='exam', name='Четвертная работа', kind='points', maxScore=20, weight=2, category='soch')]
    for pupil in content['records']: pupil['grades'] = [dict(assessmentId='exam', score=15, label='', comment='')]
    mine(record).update(attendance='late', lateMinutes=10)
    return record


def test_family_reads_only_its_own_linked_child(client):
    enable(client)
    parent = create(client, 'parent'); create(client, 'student'); create(client, 'editor'); create(client, 'viewer', grants=[dict(workspace='demo', view='class', entityIds=['c-demo-a'])])
    publish(client)
    reader = signed('parent')
    for account in [reader, signed('student'), client]: assert period(account).status_code == 200
    assert period(reader, 's2').status_code == 403 and period(signed('student'), 's2').status_code == 403
    for account in [signed('editor'), signed('viewer')]: assert period(account).status_code == 403
    assert period(reader, end='2027-09-30').status_code == 422 and period(reader, start='2026-09-30', end='2026-08-01').status_code == 422
    assert family_save(client, parent['id'], endDate='2026-01-01').status_code == 200
    assert period(reader).status_code == 403


def test_only_the_published_version_of_a_lesson_is_visible(client):
    enable(client); create(client, 'parent'); parent = signed('parent')
    draft = write(client, graded(entry(client, lesson(client)['lessonKey']))).json()
    assert period(parent).json()['marks'] == [] and period(parent).json()['attendance'] == []
    released = write(client, draft, publish=True).json()
    shown = period(parent).json()
    assert len(shown['marks']) == 1 and shown['marks'][0]['score'] == mine(released)['grades'][0]['score'] and len(shown['attendance']) == 1
    # A later draft is not news to the family.
    later = entry(client, released['lessonKey']); mine(later)['grades'][0]['score'] = 1; later['document']['content']['topic'] = 'Черновик'
    assert write(client, later).status_code == 200
    assert period(parent).json()['marks'] == shown['marks']
    withdrawn = client.post('/api/journal/withdraw?workspace=demo', json=dict(lessonKey=released['lessonKey'], version=entry(client, released['lessonKey'])['version']))
    assert withdrawn.status_code == 200, withdrawn.text
    assert period(parent).json()['marks'] == [] and period(parent).json()['attendance'] == []


def test_summary_is_the_one_of_the_first_report_page(client):
    enable(client); create(client, 'parent'); parent = signed('parent')
    publish(client)
    publish(client, 'Математика', shape=exam_and_late)
    publish(client, shape=exam_and_late, day=WEEK)
    body = period(parent).json()
    first = parent.get('/api/journal/report', params=dict(workspace='demo', studentId='s1', start='2026-08-01', end='2026-09-30')).json()
    assert body['summary'] == first['summary'] and body['absenceCodes'] == first['absenceCodes']
    assert len(body['summary']['subjects']) == 2 and body['summary']['lateShare'] > 0
    assert {m['category'] for m in body['marks']} == {'fo', 'soch'}
    assert [(m['date'], m['start']) for m in body['marks']] == sorted((m['date'], m['start']) for m in body['marks'])
    assert [r['lessonKey'] for r in first['rows']] == [a['lessonKey'] for a in body['attendance']]


def test_marks_are_works_with_a_score_or_a_label(client):
    enable(client); create(client, 'parent')
    def with_label_and_comment(record):
        graded(record)
        content = record['document']['content']
        content['assessments'] += [dict(id='talk', name='Устный ответ', kind='label', maxScore=10, weight=1), dict(id='note', name='Заметка', kind='points', maxScore=10, weight=1)]
        mine(record)['grades'] += [dict(assessmentId='talk', score=None, label='зачёт', comment=''), dict(assessmentId='note', score=None, label='', comment='Только комментарий')]
        return record
    publish(client, shape=with_label_and_comment)
    marks = period(signed('parent')).json()['marks']
    assert {m['assessmentId'] for m in marks} == {'work', 'talk'}
    assert next(m for m in marks if m['assessmentId'] == 'talk')['score'] is None and next(m for m in marks if m['assessmentId'] == 'talk')['label'] == 'зачёт'


def test_accepted_note_excuses_an_absence_until_it_is_revoked(client):
    _, _, parent, teacher = absence_setup(client)
    mark(client)
    row = lambda: period(parent).json()
    assert row()['attendance'][0]['attendance'] == 'absent' and row()['attendance'][0]['byNote'] is False
    notice = send(parent); approved = decide(teacher, notice); assert approved.status_code == 200, approved.text
    excused = row()
    assert excused['attendance'][0]['attendance'] == 'excused' and excused['attendance'][0]['byNote'] is True
    assert excused['summary']['attendance']['excused'] == 1 and excused['summary']['attendance']['absent'] == 0
    assert decide(teacher, approved.json(), 'revoke').status_code == 200
    assert row()['attendance'][0]['attendance'] == 'absent' and row()['summary']['attendance']['absent'] == 1


def test_teacher_reads_only_the_rows_of_own_lessons(client):
    enable(client); create(client, 'teacher'); create(client, 'teacher', login='unrelated', grants=[dict(workspace='demo', view='teacher', entityIds=['t1'])])
    publish(client); publish(client, 'Математика')
    whole = period(client).json(); assert {a['subject'] for a in whole['attendance']} == {'Английский язык', 'Математика'}
    own = period(signed('teacher')).json()
    assert {a['subject'] for a in own['attendance']} == {'Английский язык'} and {m['subject'] for m in own['marks']} == {'Английский язык'}
    assert [s['subject'] for s in own['summary']['subjects']] == ['Английский язык']
    other = period(signed('unrelated')).json()
    assert other['attendance'] == [] and other['marks'] == [] and other['summary']['subjects'] == [] and other['signedAt'] is None


def test_each_account_signs_its_own_marks(client):
    family(client); parent, pupil = signed('parent'), signed('student')
    publish(client); publish(client, 'Математика')
    unsigned = lambda reader: {(m['subject'], m['date']): m['unsigned'] for m in period(reader).json()['marks']}
    assert set(unsigned(parent).values()) == {True} and period(parent).json()['signedAt'] is None
    assert set(unsigned(pupil).values()) == {True} and set(unsigned(client).values()) == {False}
    assert sign(parent, items(parent)['latest']).status_code == 200
    assert set(unsigned(parent).values()) == {False} and period(parent).json()['signedAt']
    # A grade published after the signature is the only one waiting; the pupil signed nothing, so all three wait for the pupil.
    publish(client, day=WEEK)
    assert unsigned(parent) == {('Английский язык', DAY): False, ('Математика', DAY): False, ('Английский язык', WEEK): True}
    assert set(unsigned(pupil).values()) == {True} and len(unsigned(pupil)) == 3 and period(pupil).json()['signedAt'] is None


def test_too_many_records_refuse_before_any_document_is_read(client, monkeypatch):
    enable(client); create(client, 'parent')
    publish(client)
    assert period(signed('parent')).status_code == 200
    monkeypatch.setattr(journal_pupil, 'MAX_ROWS', 0)
    assert period(signed('parent')).status_code == 422


def test_scale_is_the_latest_final_or_the_only_active_threshold_scale(client):
    enable(client); create(client, 'parent'); create(client, 'teacher', login='unrelated', grants=[dict(workspace='demo', view='teacher', entityIds=['t1'])])
    parent, unrelated = signed('parent'), signed('unrelated')
    publish(client)
    def scale(name, active=True, kind='thresholds'):
        return dict(version=1, name=name, kind=kind, bands=[dict(label='2', minPercent='0', points=None), dict(label='5', minPercent='85', points=None)], rounding='none', minCount=1, active=active)
    def final(period_id, name, published_at):
        insert_row(db, 'final_results', workspace='demo', period_id=period_id, student_id='s1', subject_id='sub0', row_id=period_id, version=1,
                   document=json.dumps(dict(scale=scale(name, kind='labels'), publishedAt=published_at)))
    with api.connect() as db:
        final_grades.tables(db)
        insert_row(db, 'grading_scales', workspace='demo', id='a' * 32, version=1, document=json.dumps(scale('Первая')))
        insert_row(db, 'grading_scales', workspace='demo', id='b' * 32, version=1, document=json.dumps(scale('Вторая')))
    assert period(parent).json()['scale'] is None
    with api.connect() as db:
        db.execute("UPDATE grading_scales SET document=? WHERE id=?", (json.dumps(scale('Вторая', active=False)), 'b' * 32))
    chosen = period(parent).json()['scale']
    assert chosen['name'] == 'Первая' and chosen['kind'] == 'thresholds' and [b['label'] for b in chosen['bands']] == ['2', '5'] and float(chosen['bands'][1]['minPercent']) == 85
    with api.connect() as db:
        final('p2', 'Из последнего табеля', '2026-09-01T10:00:00+00:00')
        final('p1', 'Из прошлого табеля', '2026-06-01T10:00:00+00:00')
    assert period(parent).json()['scale']['name'] == 'Из последнего табеля'
    # A teacher does not read the pupil's report cards: the scale of a card must not reach them through the chart.
    assert period(unrelated).json()['scale']['name'] == 'Первая'
