"""Praise and remarks: teaching scope, private family view, acknowledgement and history."""
from datetime import date, timedelta
from uuid import uuid4
from backend import api
from backend.models import DatedLesson
from backend.behaviour import today
from test_access import client, enable, create, signed

BASE = '/api/behaviour/'
DAY = today().isoformat()


def url(part, **query):
    params = '&'.join(f'{k}={v}' for k, v in query.items())
    return BASE + part + '?workspace=demo' + ('&' + params if params else '')
def ok(response):
    assert response.status_code == 200, response.text
    return response.json()
def note(c, students, category='activity', text='Отличная работа', **values):
    return c.post(url('notes'), json=dict(requestId=str(uuid4()), studentIds=students, categoryId=category, day=DAY, text=text) | values)
def staff_list(c, **query): return ok(c.get(url('notes', **query)))['items']
def family(c, student): return c.get(url('family', studentId=student))
def guardian(client, login, student):
    return create(client, 'parent', login=login, grants=[dict(workspace='demo', view='student', entityIds=[student])])
def teacher(client, login, entity):
    return create(client, 'teacher', login=login, grants=[dict(workspace='demo', view='teacher', entityIds=[entity])])


def test_teacher_records_praise_and_each_family_sees_only_own_child(client):
    enable(client); teacher(client, 'math', 't1'); guardian(client, 'mother', 's2'); guardian(client, 'other', 's3'); create(client, 'student')
    t, mother, other, pupil = signed('math'), signed('mother'), signed('other'), signed('student')
    options = ok(t.get(url('options')))
    assert options['canWrite'] and {s['id'] for s in options['students']} == {'s2', 's4'}
    assert note(t, ['s1']).status_code == 403
    body = dict(requestId=str(uuid4()), studentIds=['s2', 's4'], categoryId='activity', day=DAY, text='Отличная работа')
    first = ok(t.post(url('notes'), json=body))
    assert len(first['ids']) == 2 and ok(t.post(url('notes'), json=body)) == first
    assert t.post(url('notes'), json=body | dict(text='Другое')).status_code == 409
    own = ok(family(mother, 's2'))
    assert own['praise'] == 1 and len(own['items']) == 1 and own['items'][0]['author'] == 'Сотрудник'
    assert 's4' not in str(own) and 'Ученик 4' not in str(own)
    assert family(other, 's2').status_code == 404 and family(pupil, 's2').status_code == 404
    assert ok(family(pupil, 's1'))['items'] == []
    assert mother.get('/api/notifications/inbox').json()['unread'] == 1
    assert other.get(url('notes')).status_code == 403
    assert {n['studentId'] for n in staff_list(t)} == {'s2', 's4'}


def test_remark_acknowledgement_edit_withdraw_and_history(client):
    enable(client); teacher(client, 'math', 't1'); teacher(client, 'colleague', 't3'); guardian(client, 'mother', 's2'); create(client, 'student', grants=[dict(workspace='demo', view='student', entityIds=['s2'])])
    t, colleague, mother, pupil = signed('math'), signed('colleague'), signed('mother'), signed('student')
    ok(note(t, ['s2'], 'discipline', 'Разговаривал на контрольной'))
    item = ok(family(mother, 's2'))['items'][0]
    assert item['kind'] == 'remark' and item['requireAck'] and not item['acknowledged']
    assert pupil.post(url(f'notes/{item["id"]}/acknowledge'), json=dict(version=item['version'])).status_code == 404
    assert mother.post(url(f'notes/{item["id"]}/acknowledge'), json=dict(version=item['version'] + 1)).status_code == 409
    acked = ok(mother.post(url(f'notes/{item["id"]}/acknowledge'), json=dict(version=item['version'])))
    assert acked['acknowledged'] and acked['acknowledgedBy'] == 'Сотрудник'
    assert mother.post(url(f'notes/{item["id"]}/acknowledge'), json=dict(version=acked['version'])).status_code == 409
    assert t.get('/api/notifications/inbox').json()['unread'] == 1
    current = staff_list(t)[0]
    assert t.post(url(f'notes/{item["id"]}/edit'), json=dict(version=current['version'], categoryId='discipline', text='Иначе', reason='Уточнение')).status_code == 409
    assert not staff_list(colleague)[0]['canChange']
    assert colleague.post(url(f'notes/{item["id"]}/withdraw'), json=dict(version=current['version'], reason='Ошибка')).status_code == 403
    withdrawn = ok(client.post(url(f'notes/{item["id"]}/withdraw'), json=dict(version=current['version'], reason='Ошибка ученика')))
    assert withdrawn['status'] == 'withdrawn' and withdrawn['withdrawReason'] == 'Ошибка ученика'
    assert ok(family(mother, 's2'))['items'] == [] and ok(family(mother, 's2'))['remark'] == 0
    actions = [h['action'] for h in ok(t.get(url(f'notes/{item["id"]}/history')))['items']]
    assert actions == ['created', 'acknowledged', 'withdrawn']


def test_author_edit_keeps_history_and_praise_needs_no_acknowledgement(client):
    enable(client); teacher(client, 'math', 't1'); guardian(client, 'mother', 's2')
    t, mother = signed('math'), signed('mother')
    ok(note(t, ['s2']))
    item = staff_list(t)[0]
    edited = ok(t.post(url(f'notes/{item["id"]}/edit'), json=dict(version=item['version'], categoryId='help', text='Помог другу', reason='Точнее')))
    assert edited['category'] == 'Помощь одноклассникам' and edited['version'] == 2
    seen = ok(family(mother, 's2'))['items'][0]
    assert seen['text'] == 'Помог другу' and not seen['requireAck']
    assert mother.post(url(f'notes/{item["id"]}/acknowledge'), json=dict(version=seen['version'])).status_code == 409
    assert t.post(url(f'notes/{item["id"]}/edit'), json=dict(version=1, categoryId='help', text='x', reason='y')).status_code == 409


def test_class_staff_capability_admin_categories_and_date_bounds(client):
    enable(client); staff = create(client, 'staff'); worker = signed('staff')
    assert worker.get(url('notes')).status_code == 403
    current = ok(client.get('/api/access/class-staff?workspace=demo'))
    ok(client.put('/api/access/class-staff?workspace=demo', json=dict(version=current['version'], revision=current['revision'], reason='Классный руководитель',
                                                                       assignments={'c-demo-a': [dict(accountId=staff['id'], capabilities=['behaviour_notes'])]})))
    assert ok(note(worker, ['s1', 's2'])) and note(worker, ['s3']).status_code == 403
    assert note(worker, ['s1'], day=(today() + timedelta(days=1)).isoformat()).status_code == 422
    assert note(worker, ['s1'], day=(today() - timedelta(days=400)).isoformat()).status_code == 422
    options = ok(client.get(url('options')))
    categories = [c | dict(active=c['id'] != 'uniform') for c in options['categories']] + [dict(id='late', kind='remark', title='Опоздание', active=True)]
    assert worker.put(url('config'), json=dict(version=options['configVersion'], reason='x', categories=categories)).status_code == 403
    saved = ok(client.put(url('config'), json=dict(version=options['configVersion'], reason='Решение совета', categories=categories, remarkAck=False)))
    assert saved['version'] == 1 and not saved['remarkAck']
    assert client.put(url('config'), json=dict(version=0, reason='x', categories=categories)).status_code == 409
    assert note(worker, ['s1'], 'uniform').status_code == 422
    ok(note(worker, ['s1'], 'late'))
    late = next(n for n in staff_list(worker) if n['categoryId'] == 'late')
    assert late['kind'] == 'remark' and not late['requireAck'] and late['className'] == '10A'


def test_lost_teaching_scope_hides_notes_and_csv_is_safe(client):
    enable(client); teacher(client, 'math', 't1'); t = signed('math')
    ok(note(t, ['s2'], 'discipline', '=HYPERLINK("http://x")')); ok(note(t, ['s2', 's4']))
    summary = ok(t.get(url('summary')))['items']
    assert {(r['studentId'], r['praise'], r['remark']) for r in summary} == {('s2', 1, 1), ('s4', 1, 0)}
    exported = t.get(url('export'))
    assert exported.status_code == 200 and "'=HYPERLINK" in exported.text and '\n=HYPERLINK' not in exported.text
    assert len(staff_list(t, kind='remark')) == 1 and len(staff_list(t, studentId='s4')) == 1
    assert t.get(url('notes', start='2026-01-10', end='2025-01-01')).status_code == 422
    state = api.read('demo')
    for w in state.workloads:
        if w.teacherId == 't1': w.teacherId = 't0'
    api.save('demo', state)
    assert signed('math').get(url('notes')).status_code == 403
    assert len(staff_list(client)) == 3


def assign_class_teacher(client, account, cls='c-demo-a'):
    current = ok(client.get('/api/access/class-staff?workspace=demo'))
    ok(client.put('/api/access/class-staff?workspace=demo', json=dict(version=current['version'], revision=current['revision'], reason='Классный руководитель',
                                                                       assignments={cls: [dict(accountId=account['id'], capabilities=['behaviour_notes'])]})))


def test_approval_queue_publishes_numbered_letter_only_after_admin_decision(client):
    enable(client); class_teacher = teacher(client, 'class', 't1'); guardian(client, 'mother', 's2')
    t, mother = signed('class'), signed('mother'); assign_class_teacher(client, class_teacher)
    created = ok(note(t, ['s2'], 'performance-warning', 'Три неудовлетворительные работы'))
    assert created['status'] == 'pending'
    assert ok(family(mother, 's2'))['items'] == [] and mother.get('/api/notifications/inbox').json()['unread'] == 0
    assert client.get('/api/notifications/inbox').json()['unread'] == 1
    queued = ok(client.get(url('queue')))['items']
    assert len(queued) == 1 and queued[0]['canDecide'] and not queued[0]['letter']
    assert t.post(url(f'notes/{queued[0]["id"]}/approve'), json=dict(version=queued[0]['version'])).status_code == 403
    assert t.get(url(f'notes/{queued[0]["id"]}/letter')).status_code == 409
    approved = ok(client.post(url(f'notes/{queued[0]["id"]}/approve'), json=dict(version=queued[0]['version'], reason='Согласовано')))
    assert approved['status'] == 'published' and approved['letterNumber'] == f'{today().year}-0001' and approved['approvedBy'] == 'Администратор'
    assert client.post(url(f'notes/{approved["id"]}/approve'), json=dict(version=approved['version'])).status_code == 409
    seen = ok(family(mother, 's2'))['items'][0]
    assert seen['requireAck'] and seen['letter'] and seen['levelTitle'] == 'Классный руководитель' and 'approvedBy' not in seen
    letter = ok(mother.get(url(f'notes/{seen["id"]}/letter')))
    assert letter['number'] == approved['letterNumber'] and letter['title'] == 'Предупреждение о неуспеваемости' and letter['student'] == 'Ученик 2 (пример)'
    assert t.get('/api/notifications/inbox').json()['unread'] == 1
    mine = staff_list(t)[0]
    assert not mine['canChange'] and t.post(url(f'notes/{mine["id"]}/withdraw'), json=dict(version=mine['version'], reason='x')).status_code == 403
    second = ok(note(t, ['s1'], 'performance-warning'))
    pending = ok(client.get(url('queue')))['items'][0]
    assert client.post(url(f'notes/{pending["id"]}/reject'), json=dict(version=pending['version'])).status_code == 422
    rejected = ok(client.post(url(f'notes/{pending["id"]}/reject'), json=dict(version=pending['version'], reason='Недостаточно оснований')))
    assert rejected['status'] == 'rejected' and rejected['decision'] == 'Недостаточно оснований' and second['ids'] == [pending['id']]
    actions = [h['action'] for h in ok(t.get(url(f'notes/{approved["id"]}/history')))['items']]
    assert actions == ['created', 'approved']


def test_measure_levels_hidden_notes_and_teacher_limits(client):
    enable(client); teacher(client, 'math', 't1'); guardian(client, 'mother', 's2'); class_teacher = create(client, 'staff')
    t, mother, ct = signed('math'), signed('mother'), signed('staff')
    options = ok(t.get(url('options')))
    assert options['myLevels'] == ['teacher'] and any(c['quick'] for c in options['categories'])
    assert note(t, ['s2'], 'class-remark').status_code == 403 and note(t, ['s2'], 'director-remark').status_code == 403
    assign_class_teacher(client, class_teacher)
    assert ok(ct.get(url('options')))['myLevels'] == ['class_teacher', 'teacher']
    assert ok(note(ct, ['s1', 's2'], 'class-remark'))['status'] == 'published'
    assert note(ct, ['s3'], 'class-remark').status_code == 403
    ok(note(t, ['s2'], 'unprepared', 'Внутренняя пометка', visible=False))
    visible = ok(family(mother, 's2'))
    assert visible['remark'] == 1 and all(n['text'] != 'Внутренняя пометка' for n in visible['items'])
    hidden = next(n for n in staff_list(t) if n['text'] == 'Внутренняя пометка')
    assert not hidden['visible']
    assert mother.post(url(f'notes/{hidden["id"]}/acknowledge'), json=dict(version=hidden['version'])).status_code == 404
    assert mother.get(url(f'notes/{hidden["id"]}/letter')).status_code == 404
    director = ok(note(client, ['s2'], 'director-praise', 'За победу на олимпиаде'))
    assert director['status'] == 'published'
    praised = next(n for n in staff_list(client, kind='praise') if n['categoryId'] == 'director-praise')
    assert praised['letterNumber'] == f'{today().year}-0001' and praised['approvedBy'] == ''
    plain = next(n for n in staff_list(t) if n['categoryId'] == 'unprepared')
    assert t.get(url(f'notes/{plain["id"]}/letter')).status_code == 409
    assert t.post(url(f'notes/{plain["id"]}/edit'), json=dict(version=plain['version'], categoryId='class-remark', text='x', reason='y')).status_code == 403


def test_board_ranks_published_notes_with_shared_places(client):
    enable(client); teacher(client, 'math', 't1'); t = signed('math')
    ok(note(t, ['s2', 's4'])); ok(note(t, ['s2'], 'help')); ok(note(t, ['s4'], 'help'))
    ok(note(t, ['s2'], 'discipline'))
    withdrawn = ok(note(t, ['s4'], 'achievement'))
    row = next(n for n in staff_list(t) if n['id'] == withdrawn['ids'][0])
    ok(t.post(url(f'notes/{row["id"]}/withdraw'), json=dict(version=row['version'], reason='Ошибка')))
    praise = ok(t.get(url('board')))['items']
    assert [(r['place'], r['studentId'], r['count']) for r in praise] == [(1, 's2', 2), (1, 's4', 2)]
    remark = ok(t.get(url('board', kind='remark')))['items']
    assert [(r['place'], r['studentId'], r['count']) for r in remark] == [(1, 's2', 1)]
    assert ok(t.get(url('board', classId='c-demo-d')))['items'][0]['studentId'] == 's4'
    guardian(client, 'mother', 's2')
    assert signed('mother').get(url('board')).status_code == 403


def test_old_rows_and_saved_categories_gain_measure_defaults(client):
    import sqlite3
    from backend import behaviour
    enable(client)
    db = sqlite3.connect(':memory:')
    db.execute('CREATE TABLE behaviour_notes(seq INTEGER PRIMARY KEY AUTOINCREMENT,id TEXT UNIQUE,batch TEXT,workspace TEXT,student_id TEXT,class_id TEXT,'
               'version INTEGER,status TEXT,kind TEXT,category_id TEXT,day TEXT,author_id TEXT,author_name TEXT,created TEXT,updated TEXT,ack_at TEXT,ack_by TEXT,document TEXT)')
    db.execute("INSERT INTO behaviour_notes(id,status) VALUES ('old','published')")
    behaviour.tables(db)
    assert db.execute("SELECT visible FROM behaviour_notes WHERE id='old'").fetchone()[0] == 1
    options = ok(client.get(url('options')))
    legacy = [dict(id=c['id'], kind=c['kind'], title=c['title'], active=c['active']) for c in options['categories']]
    ok(client.put(url('config'), json=dict(version=options['configVersion'], reason='Прежний формат', categories=legacy)))
    saved = ok(client.get(url('options')))['categories']
    assert all(c['level'] == 'teacher' and not c['approval'] and not c['letter'] for c in saved)


LESSON_DAY = '2026-08-17'


def lesson_key(c, subject='Английский язык'):
    rows = ok(c.get('/api/journal/day?workspace=demo&day=' + LESSON_DAY))['lessons']
    return next(r['lessonKey'] for r in rows if r['lesson']['subject'] == subject and not r['archived'])


def lesson_note(c, key, students, category='discipline', **values):
    return note(c, students, category, day=LESSON_DAY, lessonKey=key, **values)


def test_lesson_teacher_marks_pupils_of_that_lesson_and_family_sees_lesson(client, monkeypatch):
    monkeypatch.setattr('backend.behaviour_routes.today', lambda: date(2026, 8, 17))
    enable(client); create(client, 'teacher'); teacher(client, 'other', 't1'); guardian(client, 'mother', 's1')
    t, other, mother = signed('teacher'), signed('other'), signed('mother')
    key = lesson_key(t)
    marks = ok(t.get(url('lesson', lessonKey=key, day=LESSON_DAY)))
    assert marks['canMark'] and marks['notes'] == [] and marks['label']
    assert {c['level'] for c in marks['categories']} == {'teacher'}
    assert ok(lesson_note(t, key, ['s1'], 'discipline', text='Разговаривал'))['status'] == 'published'
    assert lesson_note(t, key, ['s3']).status_code == 403
    assert lesson_note(t, key, ['s1'], 'class-remark').status_code == 403
    assert other.get(url('lesson', lessonKey=key, day=LESSON_DAY)).status_code == 404
    assert lesson_note(other, key, ['s1']).status_code == 404
    title = next(c['title'] for c in marks['categories'] if c['id'] == 'discipline')
    assert [(n['studentId'], n['category']) for n in ok(t.get(url('lesson', lessonKey=key, day=LESSON_DAY)))['notes']] == [('s1', title)]
    seen = ok(family(mother, 's1'))['items'][0]
    assert seen['lesson'] == marks['label'] and seen['text'] == 'Разговаривал'


def test_substitute_marks_lesson_and_closed_period_blocks_marks(client, monkeypatch):
    from test_journal_periods import period, close
    monkeypatch.setattr('backend.behaviour_routes.today', lambda: date(2026, 8, 17))
    enable(client); create(client, 'teacher'); teacher(client, 'substitute', 't3')
    original, substitute = signed('teacher'), signed('substitute')
    key = lesson_key(original)
    state = api.read('demo')
    state.datedLessons = [DatedLesson(id='replace', name='Замена', kind='change', placementId='eng', sourceDate=date(2026, 8, 17), onDate=date(2026, 8, 17), teacherIds=['t3'])]
    api.save('demo', state)
    assert original.get(url('lesson', lessonKey=key, day=LESSON_DAY)).status_code == 404
    assert ok(lesson_note(substitute, key, ['s2'], 'activity'))['status'] == 'published'
    close(client, period(client))
    assert not ok(substitute.get(url('lesson', lessonKey=key, day=LESSON_DAY)))['canMark']
    assert lesson_note(substitute, key, ['s1']).status_code == 403


def test_future_lesson_cannot_be_marked(client, monkeypatch):
    enable(client); create(client, 'teacher')
    t = signed('teacher'); key = lesson_key(t)
    monkeypatch.setattr('backend.behaviour_routes.today', lambda: date(2026, 8, 16))
    assert not ok(t.get(url('lesson', lessonKey=key, day=LESSON_DAY)))['canMark']
    assert lesson_note(t, key, ['s1']).status_code == 422
    # A lesson dated exactly today may already be marked.
    monkeypatch.setattr('backend.behaviour_routes.today', lambda: date(2026, 8, 17))
    assert ok(t.get(url('lesson', lessonKey=key, day=LESSON_DAY)))['canMark']
    assert ok(lesson_note(t, key, ['s1']))['status'] == 'published'
