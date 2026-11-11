from datetime import date, timedelta
import pytest
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
    assert not {'permissions','topic'} & set(item)
    assert item['action'] == 'timetable.published' and item['students'][0]['id'] == 's1'
    assert item['revision'] == original.revision + 1
    # A cosmetic lock and an unplaced teacher do not change the personal timetable.
    state = api.read('demo'); state.placements[0].locked = True
    api.save('demo',state)
    assert announce(client)['notified'] == 0
    assert inbox(pupil)['unread'] == 1
    assert inbox(client)['unread'] == 2


def test_read_marks_follow_the_ids_and_never_reach_other_accounts(client):
    enable(client); create(client,'editor')
    editor = signed('editor')
    api.save('demo',api.read('demo'))
    admin_id = inbox(client)['items'][0]['id']
    editor_item = inbox(editor)['items'][0]
    assert editor.post('/api/notifications/read',json={'ids':[admin_id,editor_item['id']]}).status_code == 200
    assert inbox(client)['unread'] == 1 and inbox(editor)['unread'] == 0
    assert editor.post('/api/notifications/read',json={'ids':[editor_item['id']],'read':False}).status_code == 200
    assert inbox(editor)['unread'] == 1


def test_unread_only_lists_the_unread_entries(client):
    enable(client); create(client,'editor')
    editor = signed('editor')
    api.save('demo',api.read('demo'))
    api.save('demo',api.read('demo'))
    older, newer = inbox(editor)['items'][::-1]
    assert editor.post('/api/notifications/read',json={'ids':[older['id']]}).status_code == 200
    assert [i['id'] for i in inbox(editor,'?unreadOnly=true')['items']] == [newer['id']]


def test_read_through_id_marks_only_the_entries_up_to_it(client):
    enable(client)
    api.save('demo',api.read('demo'))
    snapshot = inbox(client)['latestId']
    api.save('demo',api.read('demo'))
    assert client.post('/api/notifications/read',json={'throughId':snapshot}).status_code == 200
    assert inbox(client)['unread'] == 1


@pytest.mark.parametrize('body',[{},{'ids':[1],'throughId':1}])
def test_read_marks_need_exactly_one_selector(client,body):
    enable(client)
    assert client.post('/api/notifications/read',json=body).status_code == 422


def test_inbox_needs_a_signed_in_account(client):
    enable(client)
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
    assert old['unread'] == 1 and old['items'][0]['action'] == 'timetable.published' and not {'title','students','link'} & set(old['items'][0])
    # A change that only touches the former child's class creates nothing for the new grants.
    state = api.read('demo'); state.placements[0].period = 1
    api.save('demo',state)
    assert announce(client)['notified'] == 0
    assert inbox(fresh)['unread'] == 1 and inbox(editor)['unread'] == 0
    assert fresh.post('/api/notifications/read',json={'ids':[old['items'][0]['id']]}).status_code == 200
    assert inbox(fresh)['unread'] == 0


def dated_change(state):
    state.datedLessons = [DatedLesson(id='change',name='Личная причина',kind='change',placementId='eng',sourceDate=date(2026,8,17),onDate=date(2026,8,17),teacherIds=['t3'])]

def duty(state):
    state.commitments = [Commitment(id='duty',name='Встреча',kind='duty',onDate=date(2026,8,17),startTime='17:00',endTime='17:30',location='Холл',requiredTeachers=1,teacherIds=['t3'],profileId='secondary',note='Личные данные')]

def closure(state):
    state.events = [Event(id='closed',name='Причина закрытия',kind='holiday',effect='cancel',start=date(2026,8,17),end=date(2026,8,17),classIds=['c-demo-a'])]

def absence(state):
    state.absences = [TeacherAbsence(id='absence',name='Медицинская причина',teacherId='t3',start=date(2026,8,17),end=date(2026,8,17))]


# (pupil, substitute) unread after the save, how many the announcement tells, (pupil, substitute) unread after it, a private word of the record.
@pytest.mark.parametrize('make,saved,told,announced,private',[
    (dated_change,(0,1),1,(1,1),'Личная причина'),
    (duty,(0,1),0,(0,1),'Личные данные'),
    (closure,(0,0),1,(1,0),'Причина закрытия'),
    (absence,(0,1),0,(0,1),'Медицинская причина')])
def test_dated_changes_tell_exactly_the_accounts_they_concern(client,make,saved,told,announced,private):
    enable(client); create(client,'student'); create(client,'teacher',grants=[dict(workspace='demo',view='teacher',entityIds=['t3'])])
    pupil, substitute = signed('student'), signed('teacher')
    rebase(client)
    state = api.read('demo'); make(state)
    api.save('demo',state)
    assert (inbox(pupil)['unread'], inbox(substitute)['unread']) == saved
    assert announce(client)['notified'] == told
    assert (inbox(pupil)['unread'], inbox(substitute)['unread']) == announced
    assert private not in str(inbox(pupil)) + str(inbox(substitute))


def test_renaming_a_dated_lesson_tells_nobody_again(client):
    enable(client); create(client,'student')
    pupil = signed('student')
    rebase(client)
    state = api.read('demo'); dated_change(state)
    api.save('demo',state)
    announce(client)
    state = api.read('demo'); state.datedLessons[0].name = 'Другая личная причина'
    api.save('demo',state)
    assert announce(client)['notified'] == 0
    assert inbox(pupil)['unread'] == 1


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


def put_state(client, state):
    return client.put('/api/state?workspace=demo', json=state.model_dump(mode='json'))


def test_stale_save_is_refused_and_notifies_nobody(client):
    enable(client)
    original = api.read('demo')
    assert put_state(client, original).status_code == 200
    assert put_state(client, original).status_code == 409
    assert inbox(client)['unread'] == 1


def test_undo_and_redo_are_told_in_the_inbox(client):
    enable(client)
    original = api.read('demo')
    assert put_state(client, original).status_code == 200
    assert client.post('/api/undo?workspace=demo',json={'revision':original.revision+1}).status_code == 200
    assert client.post('/api/redo?workspace=demo',json={'revision':original.revision+2}).status_code == 200
    assert [x['action'] for x in inbox(client)['items']] == ['redo','undo','saved']


def test_notification_failure_rolls_the_save_back(client, monkeypatch):
    enable(client)
    before = api.read('demo')
    def failure(*args,**kwargs): raise RuntimeError('failed notification storage')
    with monkeypatch.context() as patch:
        patch.setattr(api.notifications,'deliver',failure)
        with pytest.raises(RuntimeError): put_state(client, before)
    assert api.read('demo') == before and inbox(client)['unread'] == 0


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
    def entries(): return [i for i in inbox(client)['items'] if i['action'] == 'forms.submitted']  # newest first
    deliver(1,'t1'); deliver(2,'t1')
    (first,) = entries()
    assert first['revision'] == 2 and not first['read']
    assert client.post('/api/notifications/read',json={'ids':[first['id']]}).status_code == 200
    deliver(3,'t1')
    assert [(i['revision'],i['read']) for i in entries()] == [(3,False),(2,True)]
    deliver(4,'t2')
    assert [i['revision'] for i in entries()] == [4,3,2]
    deliver(5); deliver(6)
    assert [i['revision'] for i in entries()] == [6,5,4,3,2]
    assert all('topic' not in item for item in inbox(client)['items'])


def test_old_notifications_table_is_migrated_keeping_its_rows(tmp_path):
    import sqlite3
    from contextlib import closing
    from backend.notifications import Notifications
    from seeding import insert_row, read_row
    with closing(sqlite3.connect(tmp_path / 'old.db')) as db:
        db.execute('CREATE TABLE notifications (id INTEGER PRIMARY KEY AUTOINCREMENT,user_id TEXT,permissions TEXT,workspace TEXT,revision INTEGER,created TEXT,action TEXT,read_at TEXT)')
        insert_row(db, 'notifications', user_id='u', permissions='p', workspace='demo', revision=1, created='2026-01-01T00:00:00', action='saved')
        Notifications(None).tables(db); Notifications(None).tables(db)
        assert db.execute('SELECT count(*) FROM notifications').fetchone()[0] == 1
        old = read_row(db, 'notifications', user_id='u')
        assert (old['action'], old['revision'], old['read_at']) == ('saved', 1, None)
