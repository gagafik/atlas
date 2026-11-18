import json
from backend import api
from test_access import client, enable, create, signed
from seeding import insert_row


def remember(db, key, teacher, pupils, kinds=('draft', 'published')):
    document = json.dumps(dict(lesson=dict(teacherIds=[teacher], students=[])))
    insert_row(db, 'journal_entries', workspace='demo', lesson_key=key, version=1, updated='2026-08-17', actor_name='t', document=document, published_document=document, published_version=1, published_at='2026-08-17')
    for pupil, name in pupils:
        for kind in kinds:
            insert_row(db, 'journal_members', workspace='demo', lesson_key=key, student_id=pupil, name=name, kind=kind)


def listed(reader):
    response = reader.get('/api/journal/options?workspace=demo')
    assert response.status_code == 200, response.text
    return {s['id']: s for s in response.json()['students']}


def test_options_return_pupils_who_left_the_state_under_the_rights_of_each_role(client):
    enable(client)
    create(client, 'teacher'); create(client, 'parent', grants=[dict(workspace='demo', view='student', entityIds=['s1', 's2'])])
    state = api.read('demo'); state.students = [x for x in state.students if x.id != 's2']
    for group in state.groups: group.studentIds = [i for i in group.studentIds if i != 's2']
    api.save('demo', state)
    listed(client)  # creates the journal tables
    with api.connect() as db:
        remember(db, 'own-lesson', 't2', [('s2', 'Ушедший свой'), ('s1', 'Текущий')])
        remember(db, 'foreign-lesson', 't-other', [('gone-foreign', 'Ушедший чужой')])
    admin = listed(client)
    assert {'s2', 'gone-foreign'} <= set(admin)
    assert admin['s2']['name'] == 'Ушедший свой' and admin['s2']['className'] is None
    teacher = listed(signed('teacher'))
    assert 's2' in teacher and 'gone-foreign' not in teacher
    parent = listed(signed('parent'))
    assert 's2' in parent and 'gone-foreign' not in parent and 's1' in parent


def test_a_pupil_of_the_state_keeps_the_class_and_is_not_duplicated_by_the_journal(client):
    enable(client)
    listed(client)
    with api.connect() as db:
        remember(db, 'lesson', 't2', [('s1', 'Старое имя')])
    body = client.get('/api/journal/options?workspace=demo').json()
    rows = [s for s in body['students'] if s['id'] == 's1']
    assert len(rows) == 1 and rows[0]['className'] is not None and rows[0]['name'] != 'Старое имя'


def test_a_teacher_does_not_receive_published_only_members_of_own_lessons(client):
    enable(client); create(client, 'teacher')
    listed(client)
    with api.connect() as db:
        remember(db, 'own-lesson', 't2', [('gone-published', 'Только опубликован')], kinds=('published',))
    assert 'gone-published' not in listed(signed('teacher')) and 'gone-published' in listed(client)
