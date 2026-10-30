from datetime import date, timedelta
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from backend import api
from backend.access import LOCAL, now
from backend.models import DatedLesson, Commitment, TeacherAbsence, Event
from test_access import client, enable, create, signed, HEADERS


def inbox(client, query=''):
    response = client.get('/api/notifications/inbox' + query)
    assert response.status_code == 200, response.text
    return response.json()


def announce(client, workspace='demo'):
    response = client.post('/api/notifications/timetable/announce?workspace=' + workspace, json=dict(revision=api.read(workspace).revision))
    assert response.status_code == 200, response.text
    return response.json()


def rebase(client):
    """The fixture's own save is the first save after 'upgrade'; start the announcements from the state the test sees."""
    with api.connect() as db: db.execute('DELETE FROM timetable_announcements')
    assert announce(client)['notified'] == 0


def test_personal_schedule_recipients_and_no_private_payload(client):
    enable(client)
    create(client, 'student')
    create(client, 'student', login='other', grants=[dict(workspace='demo',view='student',entityIds=['s3'])])
    pupil, other = signed('student'), signed('other')
    original = api.read('demo')
    rebase(client)
    assert inbox(pupil)['items'] == []
    # w0 is the whole 10A class; s3 belongs to another class.
    changed = original.model_copy(deep=True)
    changed.placements[0].period = 0
    api.save('demo', changed)
    # Saving tells families nothing; the announcement does, and only the affected pupil hears it.
    assert inbox(pupil)['unread'] == 0 and inbox(other)['unread'] == 0
    assert announce(client)['notified'] == 1
    assert inbox(pupil)['unread'] == 1
    assert inbox(other)['unread'] == 0
    item = inbox(pupil)['items'][0]
    assert set(item) == {'id','workspace','revision','created','action','read','category','title','students','link'}
    assert item['action'] == 'timetable.published' and item['title'] == 'Изменилось расписание' and item['students'][0]['id'] == 's1'
    assert item['revision'] == original.revision + 1
    # A cosmetic lock and an unplaced teacher do not change the personal timetable.
    state = api.read('demo'); state.placements[0].locked = True
    api.save('demo',state)
    assert announce(client)['notified'] == 0
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


def test_grant_changes_keep_own_inbox_and_editor_scope(client):
    enable(client)
    user = create(client,'student')
    create(client,'editor',workspaces=['school'])
    pupil = signed('student'); editor = signed('editor')
    rebase(client)
    state = api.read('demo'); state.placements[0].period = 0
    api.save('demo',state)
    assert inbox(pupil)['unread'] == 0 and inbox(editor)['unread'] == 0
    announce(client)
    assert inbox(pupil)['unread'] == 1 and inbox(editor)['unread'] == 0
    update = {k:v for k,v in user.items() if k!='id'}
    update['grants'] = [dict(workspace='demo',view='student',entityIds=['s3'])]
    assert client.put('/api/access/users/'+user['id'],json=update).status_code == 200
    assert pupil.get('/api/notifications/inbox').status_code == 401
    # The owner keeps the entry, but it no longer names a child the account cannot see.
    fresh = signed('student'); old = inbox(fresh)
    assert old['unread'] == 1 and set(old['items'][0]) == {'id','workspace','revision','created','action','read','category','restricted'} and old['items'][0]['action'] == 'timetable.published'
    # A change that only touches the former child's class creates nothing for the new grants.
    state = api.read('demo'); state.placements[0].period = 1
    api.save('demo',state)
    assert announce(client)['notified'] == 0
    assert inbox(fresh)['unread'] == 1 and inbox(editor)['unread'] == 0
    assert fresh.post('/api/notifications/read',json={'ids':[old['items'][0]['id']]}).status_code == 200
    assert inbox(fresh)['unread'] == 0


def test_dated_lessons_commitments_closures_and_private_reasons(client):
    enable(client); create(client,'student'); create(client,'teacher',grants=[dict(workspace='demo',view='teacher',entityIds=['t3'])])
    pupil, substitute = signed('student'), signed('teacher')
    rebase(client)
    state = api.read('demo')
    state.datedLessons = [DatedLesson(id='change',name='Личная причина',kind='change',placementId='eng',sourceDate=date(2026,8,17),onDate=date(2026,8,17),teacherIds=['t3'])]
    api.save('demo',state)
    assert inbox(pupil)['unread'] == 0 and inbox(substitute)['unread'] == 1
    announce(client)
    assert inbox(pupil)['unread'] == inbox(substitute)['unread'] == 1
    state = api.read('demo'); state.datedLessons[0].name = 'Другая личная причина'
    api.save('demo',state)
    assert announce(client)['notified'] == 0
    assert inbox(pupil)['unread'] == 1
    state = api.read('demo')
    state.commitments = [Commitment(id='duty',name='Встреча',kind='duty',onDate=date(2026,8,17),startTime='17:00',endTime='17:30',location='Холл',requiredTeachers=1,teacherIds=['t3'],profileId='secondary',note='Личные данные')]
    api.save('demo',state)
    assert announce(client)['notified'] == 0
    assert inbox(pupil)['unread'] == 1 and inbox(substitute)['unread'] == 2
    pupil.post('/api/notifications/read',json={'throughId':inbox(pupil)['latestId']}).raise_for_status()
    state = api.read('demo'); state.events = [Event(id='closed',name='Причина закрытия',kind='holiday',effect='cancel',start=date(2026,8,17),end=date(2026,8,17),classIds=['c-demo-a'])]
    api.save('demo',state)
    assert inbox(pupil)['unread'] == 0
    announce(client)
    assert inbox(pupil)['unread'] == 1
    state = api.read('demo'); state.absences = [TeacherAbsence(id='absence',name='Медицинская причина',teacherId='t3',start=date(2026,8,17),end=date(2026,8,17))]
    api.save('demo',state)
    assert inbox(substitute)['unread'] >= 3
    assert 'причина' not in str(inbox(pupil)).lower() and 'причина' not in str(inbox(substitute)).lower()


def test_membership_removal_notifies_previous_recipient(client):
    enable(client); create(client,'student')
    pupil = signed('student')
    rebase(client)
    state = api.read('demo')
    group = next(g for g in state.groups if g.id == next(w.groupId for w in state.workloads if w.id=='w4'))
    assert 's1' in group.studentIds
    group.studentIds.remove('s1')
    api.save('demo',state)
    assert inbox(pupil)['unread'] == 0
    assert announce(client)['notified'] == 1
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


def test_topic_keeps_one_unread_entry(client):
    def deliver(revision,topic=None):
        with api.access.lock, api.connect() as db: api.notifications.deliver(db,[LOCAL],'demo',revision,'forms.submitted',topic=topic)
    def rows(topic):
        with api.connect() as db:
            clause = 'topic IS NULL AND action=?' if topic is None else 'topic=?'
            return db.execute(f'SELECT id,revision,read_at FROM notifications WHERE user_id=? AND {clause} ORDER BY id',(LOCAL['id'],'forms.submitted' if topic is None else topic)).fetchall()
    deliver(1,'t1'); deliver(2,'t1')
    (first,) = rows('t1')
    assert first[1] == 2 and first[2] is None
    assert client.post('/api/notifications/read',json={'ids':[first[0]]}).status_code == 200
    deliver(3,'t1')
    kept = rows('t1')
    assert [r[1] for r in kept] == [2,3] and kept[0][2] and kept[1][2] is None
    deliver(4,'t2')
    assert len(rows('t1')) == 2 and len(rows('t2')) == 1
    deliver(5); deliver(6)
    assert len(rows(None)) == 2
    assert all('topic' not in item for item in inbox(client)['items'])


def test_topic_column_is_added_to_an_existing_notifications_table(tmp_path):
    import sqlite3
    from contextlib import closing
    from backend.notifications import Notifications
    with closing(sqlite3.connect(tmp_path / 'old.db')) as db:
        db.execute('CREATE TABLE notifications (id INTEGER PRIMARY KEY AUTOINCREMENT,user_id TEXT,permissions TEXT,workspace TEXT,revision INTEGER,created TEXT,action TEXT,read_at TEXT)')
        db.execute("INSERT INTO notifications(user_id,permissions,workspace,revision,created,action) VALUES ('u','p','demo',1,'2026-01-01T00:00:00','saved')")
        Notifications(None).tables(db); Notifications(None).tables(db)
        assert db.execute('SELECT action,topic FROM notifications').fetchall() == [('saved', None)]
