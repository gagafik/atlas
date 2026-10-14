from datetime import date, timedelta
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from backend import api
from backend.access import now
from backend.models import DatedLesson, Commitment, TeacherAbsence, Event
from test_access import client, enable, create, signed, HEADERS


def inbox(client, query=''):
    response = client.get('/api/notifications/inbox' + query)
    assert response.status_code == 200, response.text
    return response.json()


def test_personal_schedule_recipients_and_no_private_payload(client):
    enable(client)
    create(client, 'student')
    create(client, 'student', login='other', grants=[dict(workspace='demo',view='student',entityIds=['s3'])])
    pupil, other = signed('student'), signed('other')
    original = api.read('demo')
    assert inbox(pupil)['items'] == []
    # w0 is the whole 10A class; s3 belongs to another class.
    changed = original.model_copy(deep=True)
    changed.placements[0].period = 0
    api.save('demo', changed)
    assert inbox(pupil)['unread'] == 1
    assert inbox(other)['unread'] == 0
    item = inbox(pupil)['items'][0]
    assert set(item) == {'id','workspace','revision','created','action','read'}
    assert item['revision'] == original.revision + 1
    # A cosmetic lock and an unplaced teacher do not change the personal timetable.
    state = api.read('demo'); state.placements[0].locked = True
    api.save('demo',state)
    assert inbox(pupil)['unread'] == 1
    assert inbox(client)['unread'] == 2


def test_read_marks_preferences_pagination_and_account_isolation(client):
    enable(client); create(client,'editor')
    editor = signed('editor')
    state = api.read('demo')
    api.save('demo',state)
    admin_id = inbox(client)['items'][0]['id']
    editor_item = inbox(editor)['items'][0]
    assert editor.post('/api/notifications/read',json={'ids':[admin_id,editor_item['id']]}).status_code == 200
    assert inbox(client)['unread'] == 1 and inbox(editor)['unread'] == 0
    assert editor.post('/api/notifications/read',json={'ids':[editor_item['id']],'read':False}).status_code == 200
    assert inbox(editor)['unread'] == 1
    assert editor.put('/api/notifications/preferences',json={'enabled':False}).status_code == 200
    api.save('demo',api.read('demo'))
    assert inbox(editor)['unread'] == 1 and not inbox(editor)['enabled']
    assert inbox(editor,'?unreadOnly=true')['items'] == [editor_item]
    snapshot = inbox(client)['latestId']
    api.save('demo',api.read('demo'))
    assert client.post('/api/notifications/read',json={'throughId':snapshot}).status_code == 200
    assert inbox(client)['unread'] == 1
    assert client.post('/api/notifications/read',json={}).status_code == 422
    assert client.post('/api/notifications/read',json={'ids':[1],'throughId':1}).status_code == 422
    assert TestClient(api.app).get('/api/notifications/inbox').status_code == 401


def test_grant_changes_revoke_old_inbox_and_editor_scope(client):
    enable(client)
    user = create(client,'student')
    create(client,'editor',workspaces=['school'])
    pupil = signed('student'); editor = signed('editor')
    state = api.read('demo'); state.placements[0].period = 0
    api.save('demo',state)
    assert inbox(pupil)['unread'] == 1 and inbox(editor)['unread'] == 0
    update = {k:v for k,v in user.items() if k!='id'}
    update['grants'] = [dict(workspace='demo',view='student',entityIds=['s3'])]
    assert client.put('/api/access/users/'+user['id'],json=update).status_code == 200
    assert pupil.get('/api/notifications/inbox').status_code == 401
    assert inbox(signed('student'))['items'] == []


def test_dated_lessons_commitments_closures_and_private_reasons(client):
    enable(client); create(client,'student'); create(client,'teacher',grants=[dict(workspace='demo',view='teacher',entityIds=['t3'])])
    pupil, substitute = signed('student'), signed('teacher')
    state = api.read('demo')
    state.datedLessons = [DatedLesson(id='change',name='Личная причина',kind='change',placementId='eng',sourceDate=date(2026,8,17),onDate=date(2026,8,17),teacherIds=['t3'])]
    api.save('demo',state)
    assert inbox(pupil)['unread'] == inbox(substitute)['unread'] == 1
    state = api.read('demo'); state.datedLessons[0].name = 'Другая личная причина'
    api.save('demo',state)
    assert inbox(pupil)['unread'] == 1
    state = api.read('demo')
    state.commitments = [Commitment(id='duty',name='Встреча',kind='duty',onDate=date(2026,8,17),startTime='17:00',endTime='17:30',location='Холл',requiredTeachers=1,teacherIds=['t3'],profileId='secondary',note='Личные данные')]
    api.save('demo',state)
    assert inbox(pupil)['unread'] == 1 and inbox(substitute)['unread'] == 2
    state = api.read('demo'); state.events = [Event(id='closed',name='Причина закрытия',kind='holiday',effect='cancel',start=date(2026,8,17),end=date(2026,8,17),classIds=['c-demo-a'])]
    api.save('demo',state)
    assert inbox(pupil)['unread'] == 2
    state = api.read('demo'); state.absences = [TeacherAbsence(id='absence',name='Медицинская причина',teacherId='t3',start=date(2026,8,17),end=date(2026,8,17))]
    api.save('demo',state)
    assert inbox(substitute)['unread'] >= 3
    assert 'причина' not in str(inbox(pupil)).lower() and 'причина' not in str(inbox(substitute)).lower()


def test_membership_removal_notifies_previous_recipient(client):
    enable(client); create(client,'student')
    pupil = signed('student')
    state = api.read('demo')
    group = next(g for g in state.groups if g.id == next(w.groupId for w in state.workloads if w.id=='w4'))
    assert 's1' in group.studentIds
    group.studentIds.remove('s1')
    api.save('demo',state)
    assert inbox(pupil)['unread'] == 1


def test_transaction_undo_redo_stale_save_and_failure_rollback(client, monkeypatch):
    enable(client)
    original = api.read('demo')
    api.save('demo',original)
    with pytest.raises(HTTPException): api.save('demo',original)
    assert inbox(client)['unread'] == 1
    assert client.post('/api/undo?workspace=demo',json={'revision':original.revision+1}).status_code == 200
    assert client.post('/api/redo?workspace=demo',json={'revision':original.revision+2}).status_code == 200
    assert [x['action'] for x in inbox(client)['items']] == ['redo','undo','saved']
    before = api.read('demo')
    def failure(*args,**kwargs): raise RuntimeError('failed notification storage')
    monkeypatch.setattr(api.notifications,'capture',failure)
    with pytest.raises(RuntimeError): api.save('demo',before)
    assert api.read('demo') == before and inbox(client)['unread'] == 3


def test_retention_and_paged_unread_history(client):
    enable(client)
    api.save('demo',api.read('demo'))
    with api.connect() as db:
        row = db.execute('SELECT user_id,permissions,workspace,revision,created,action FROM notifications WHERE user_id!=?',('local',)).fetchone()
        db.executemany('INSERT INTO notifications(user_id,permissions,workspace,revision,created,action) VALUES (?,?,?,?,?,?)',[row]*502)
    api.save('demo',api.read('demo'))
    first = inbox(client)
    assert first['unread'] == 500 and len(first['items']) == 50
    second = inbox(client,'?beforeId='+str(first['nextBeforeId']))
    assert len(second['items']) == 50 and not {x['id'] for x in first['items']} & {x['id'] for x in second['items']}
    with api.connect() as db: db.execute('UPDATE notifications SET created=?',((now()-timedelta(days=91)).isoformat(),))
    assert inbox(client)['unread'] == 0
