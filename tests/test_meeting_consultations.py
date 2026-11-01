"""Consultation hours: teacher series, pupil switch, one booking per day, teacher self-service and the day view."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date,datetime,timedelta,timezone
import sqlite3
from zoneinfo import ZoneInfo
import pytest
from backend import api,meetings,meeting_routes,meeting_reminders,meeting_consultations
from backend.models import Commitment,Event,TeacherAbsence
from seeding import insert_row,read_row
from test_access import client,enable,create,signed
from test_meetings import BASE,request,path,ok,event as detail,publish as publish_meeting,setup as setup_meeting,new as new_meeting,regrant
from test_meeting_waitlist import queue,join,offered,change
from test_absence_school import lead

ZONE=ZoneInfo('Asia/Qyzylorda')
CLOCK=BASE-timedelta(days=2)  # Saturday 3 April 2027: the Mondays of April are ahead
MONDAYS=['2027-04-05','2027-04-12','2027-04-19','2027-04-26']


def prepared(client,monkeypatch):
    """Administrator, teacher t2 (English to s1 and s2), a parent of s1 and a pupil account of s1."""
    for module in [meetings,meeting_routes,meeting_reminders]:monkeypatch.setattr(module,'now',lambda:CLOCK)
    enable(client);teacher=create(client,'teacher');parent=create(client,'parent');pupil=create(client,'student')
    return teacher,parent,pupil
def other_teacher(client):return create(client,'teacher',login='math',grants=[dict(workspace='demo',view='teacher',entityIds=['t0'])])
def other_family(client,login,student,role='parent'):return create(client,role,login=login,grants=[dict(workspace='demo',view='student',entityIds=[student])])
def get(c,part,**params):return c.get('/api/meetings/'+part,params=dict(workspace='demo')|params)
def series(**kw):
    return dict(requestId=request()['requestId'],teacherId='t2',title='Консультации',startDate=MONDAYS[0],endDate=MONDAYS[-1],weekdays=[0],startTime='08:00',endTime='09:30',slotMinutes=15,roomId='r0')|kw
def preview(c,**kw):return c.post(path('consultations/preview'),json=series(**kw))
def try_series(c,**kw):
    body=series(**kw);found=ok(c.post(path('consultations/preview'),json=body))
    return c.post(path('consultations'),json=body|dict(previewHash=found['previewHash']))
def create_series(c,**kw):return ok(try_series(c,**kw))
def act(c,id,action,**kw):return c.post(path('events/'+id+'/transition'),json=request(version=detail(c,id)['version'],action=action,**kw))
def switch(c,on,version=0,reason='Решение школы'):return c.put(path('settings'),json=request(version=version,pupilBooking=on,reason=reason))
def slots(c,id):return ok(get(c,'events/'+id+'/slots',limit=100))['items']
def local(value):return datetime.fromisoformat(value).astimezone(ZONE)
def on_date(c,id,day):return [s for s in slots(c,id) if local(s['startsAt']).date().isoformat()==day]
def book(c,id,slot,student='s1',**kw):return c.post(path('slots/'+slot['id']+'/book'),json=request(version=slot['version'],eventVersion=detail(c,id)['version'],studentId=student,**kw))
def bookings(c,id):return ok(get(c,'bookings',eventId=id))['items']
def mine(c):return ok(get(c,'consultations/mine'))['items']
def got(c,action):return [i for i in c.get('/api/notifications/inbox').json()['items'] if i['action']==action]
def day_view(c,day,**params):return get(c,'day',onDate=day,**params)
def overlap(slot,lesson):
    return local(slot['startsAt']).strftime('%H:%M')<lesson['end'] and lesson['start']<local(slot['endsAt']).strftime('%H:%M')
def reschedule(c,id,booking,target):
    current=next(b for b in bookings(c,id) if b['id']==booking);source=next(s for s in slots(c,id) if s['startsAt']==current['startsAt'])
    return c.post(path('bookings/'+booking+'/reschedule'),json=request(version=current['version'],eventVersion=detail(c,id)['version'],sourceSlotVersion=source['version'],targetSlotId=target['id'],targetSlotVersion=target['version'],reason='Учитель занят'))


def test_migration_keeps_old_bookings(tmp_path):
    old=sqlite3.connect(tmp_path/'old.db');fresh=sqlite3.connect(tmp_path/'fresh.db')
    old.executescript('''CREATE TABLE meeting_bookings (id TEXT PRIMARY KEY,event_id TEXT,slot_id TEXT,workspace TEXT,parent_id TEXT,student_id TEXT,
      host_id TEXT,permissions TEXT,starts_at TEXT,ends_at TEXT,status TEXT,version INTEGER,document TEXT);
      CREATE UNIQUE INDEX meeting_booked_slot ON meeting_bookings(slot_id) WHERE status!='cancelled';
      CREATE UNIQUE INDEX meeting_booked_child ON meeting_bookings(event_id,host_id,student_id) WHERE status!='cancelled';''')
    row=dict(id='b1',event_id='e1',slot_id='s1',workspace='demo',parent_id='p',student_id='st',host_id='h',permissions='',starts_at='',ends_at='',status='scheduled',version=1,document='{}')
    insert_row(old,'meeting_bookings',row)
    for _ in range(2):meetings.tables(old)
    meetings.tables(fresh)
    assert read_row(old,'meeting_bookings',id='b1')['occasion']==''
    with pytest.raises(sqlite3.IntegrityError):insert_row(old,'meeting_bookings',row,id='b2',slot_id='s2')
    # A cancelled booking holds nothing, and the date of a series booking tells two days of one series apart.
    insert_row(old,'meeting_bookings',row,id='b3',slot_id='s3',status='cancelled');insert_row(old,'meeting_bookings',row,id='b4',slot_id='s4',occasion='2027-04-05')
    names=lambda db:sorted(r[1] for r in db.execute('PRAGMA index_list(meeting_bookings)'))
    assert names(old)==names(fresh) and 'meeting_booked_child' not in names(old)


def test_pupil_switch_is_off_by_default_and_audited(client,monkeypatch):
    id,t,p=setup_meeting(client,monkeypatch);publish_meeting(client,id);create(client,'student')
    student,teacher,parent=signed('student'),signed('teacher'),signed('parent')
    assert student.get(path('options')).status_code==403
    assert student.post(path('slots/unknown/book'),json=request(version=1,eventVersion=1,studentId='s1')).status_code==403
    assert switch(teacher,True).status_code==403 and switch(parent,True).status_code==403
    assert ok(client.get(path('options')))['settingsVersion']==0 and ok(client.get(path('options')))['pupilBooking'] is False
    assert switch(client,True,version=3).status_code==409
    body=request(version=0,pupilBooking=True,reason='Решение педсовета')
    result=ok(client.put(path('settings'),json=body));assert result['version']==1 and ok(client.put(path('settings'),json=body))==result
    assert switch(client,False,version=0).status_code==409
    options=ok(student.get(path('options')));assert options['pupilBooking'] is True and [s['id'] for s in options['students']]==['s1']
    assert student.get(path('events/'+id)).status_code==404 and ok(student.get(path('events')))['items']==[]
    assert ok(client.get(path('options')))['settingsVersion']==1
    with api.connect() as db:
        assert db.execute("SELECT count(*) FROM audit WHERE action='meeting.settings_updated'").fetchone()[0]==1
        assert db.execute("SELECT count(*) FROM meeting_history WHERE action='settings'").fetchone()[0]==1


def test_series_respects_timetable_closed_days_room_bookings_and_absences(client,monkeypatch):
    prepared(client,monkeypatch);teacher=signed('teacher')
    state=api.read('demo')
    state.events=[Event(id='closed',name='Каникулы',start=date(2027,4,12),end=date(2027,4,12),kind='holiday',effect='cancel')]
    state.commitments=[Commitment(id='room',name='Бронь кабинета',onDate=date(2027,4,19),startTime='09:00',endTime='09:15',roomIds=['r0'])]
    state.absences=[TeacherAbsence(id='away',name='Болезнь',teacherId='t2',start=date(2027,4,26),end=date(2027,4,26))]
    api.save('demo',state);before=api.read('demo').model_dump(mode='json')
    found=ok(preview(teacher));reasons={s['reason'] for s in found['skipped']}
    assert found['skippedCount']>0 and all(reasons) and {MONDAYS[1],MONDAYS[2],MONDAYS[3]}<={s['date'] for s in found['skipped']}
    body=series();body['previewHash']=ok(preview(teacher,requestId=body['requestId']))['previewHash']
    made=ok(teacher.post(path('consultations'),json=body))
    assert ok(teacher.post(path('consultations'),json=body))==made
    with api.connect() as db:
        assert db.execute("SELECT count(*) FROM meeting_events WHERE json_extract(document,'$.kind')='consultation'").fetchone()[0]==1
        assert db.execute("SELECT count(*) FROM notifications WHERE action LIKE 'meeting.%'").fetchone()[0]==0
    assert api.read('demo').model_dump(mode='json')==before
    items=slots(teacher,made['id']);assert len(items)==made['slots']
    dates={local(s['startsAt']).date().isoformat() for s in items}
    assert dates=={MONDAYS[0],MONDAYS[2]}
    for day in dates:
        lessons=ok(day_view(teacher,day))['lessons'];assert lessons
        assert not any(overlap(s,l) for s in on_date(teacher,made['id'],day) for l in lessons)
    assert not any(overlap(s,dict(start='09:00',end='09:15')) for s in on_date(teacher,made['id'],MONDAYS[2]))


def test_stale_preview_and_later_closure(client,monkeypatch):
    prepared(client,monkeypatch);teacher=signed('teacher');parent=signed('parent')
    body=series();body['previewHash']=ok(preview(teacher,requestId=body['requestId']))['previewHash']
    state=api.read('demo');state.events=[Event(id='closed',name='Каникулы',start=date(2027,4,19),end=date(2027,4,19),kind='holiday',effect='cancel')];api.save('demo',state)
    assert teacher.post(path('consultations'),json=body).status_code==409
    with api.connect() as db:assert db.execute('SELECT count(*) FROM meeting_events').fetchone()[0]==0
    made=create_series(teacher)
    state=api.read('demo');state.events=[Event(id='later',name='Выходной',start=date(2027,4,12),end=date(2027,4,12),kind='holiday',effect='cancel')];api.save('demo',state)
    closed=on_date(parent,made['id'],MONDAYS[1])
    assert closed and {s['status'] for s in closed}=={'conflict'}
    assert book(parent,made['id'],closed[0]).status_code==409
    assert ok(book(parent,made['id'],on_date(parent,made['id'],MONDAYS[0])[0]))['status']=='scheduled'


def test_series_permissions(client,monkeypatch):
    prepared(client,monkeypatch);math=other_teacher(client);other_family(client,'second','s2');create(client,'staff');teacher=signed('teacher')
    assert teacher.post(path('consultations/preview'),json=series(accountId=math['id'])).status_code==403
    assert preview(teacher,teacherId='t0').status_code==422
    assert preview(teacher,startDate='2025-04-07',endDate='2025-04-28').status_code==422
    assert all(preview(signed(login)).status_code==403 for login in ['parent','student','staff'])
    assert preview(client).status_code==422
    mathematics=signed('math');made=create_series(mathematics,teacherId='t0',roomId='r1')
    tuesdays=create_series(client,accountId=math['id'],teacherId='t0',roomId='r1',weekdays=[1],startDate='2027-04-06',endDate='2027-04-27',title='Вторник')
    foreign=request(version=1,action='generate',hostIds=[math['id']],startsAt=BASE.isoformat(),endsAt=(BASE+timedelta(hours=1)).isoformat())
    assert teacher.post(path('events/'+made['id']+'/availability/preview'),json=foreign).status_code==403
    assert teacher.post(path('events/'+made['id']+'/availability'),json=foreign|dict(previewHash='x')).status_code==403
    assert teacher.get(path('events/'+made['id'])).status_code==404 and teacher.post(path('events/'+made['id']+'/transition'),json=request(version=1,action='close')).status_code==403
    assert {i['id'] for i in ok(mathematics.get(path('consultations')))['items']}=={made['id'],tuesdays['id']} and ok(teacher.get(path('consultations')))['items']==[]
    # t0 teaches s1 (maths group) but not s2.
    assert {i['id'] for i in ok(get(signed('parent'),'consultations'))['items']}=={made['id'],tuesdays['id']}
    stranger=signed('second')
    assert ok(get(stranger,'consultations'))['items']==[] and stranger.get(path('events/'+made['id'])).status_code==404
    assert len(ok(client.get(path('consultations')))['items'])==2 and ok(client.get(path('events')))['items']==[]


def test_race_for_a_slot_has_one_winner_between_parent_and_pupil(client,monkeypatch):
    prepared(client,monkeypatch);other_family(client,'pupil2','s2','student');ok(switch(client,True))
    made=create_series(signed('teacher'));a,b=signed('parent'),signed('pupil2');target=on_date(a,made['id'],MONDAYS[0])[0];version=detail(a,made['id'])['version']
    bodies=[request(version=target['version'],eventVersion=version,studentId=student) for student in ['s1','s2']]
    def send(pair):c,body=pair;return c.post(path('slots/'+target['id']+'/book'),json=body)
    with ThreadPoolExecutor(2) as pool:responses=list(pool.map(send,zip([a,b],bodies)))
    assert sorted(r.status_code for r in responses)==[200,409]
    winner=next(i for i,r in enumerate(responses) if r.status_code==200)
    assert send(([a,b][winner],bodies[winner])).json()==responses[winner].json()
    assert len(bookings(signed('teacher'),made['id']))==1


def test_one_booking_per_day_and_pupils_own_lesson(client,monkeypatch):
    prepared(client,monkeypatch);math=other_teacher(client);other_family(client,'pupil2','s2','student');ok(switch(client,True))
    made=create_series(signed('teacher'),startTime='08:00',endTime='13:00',slotMinutes=20,endDate=MONDAYS[1]);id=made['id']
    parent=signed('parent');pupil2=signed('pupil2')
    # s1 attends the maths group of t0, s2 does not.
    lessons=ok(day_view(client,MONDAYS[0],hostId=math['id']))['lessons'];assert lessons
    def usable(day,clash):return [s for s in on_date(parent,id,day) if s['status']=='available' and clash==any(overlap(s,l) for l in lessons)]
    blocked=usable(MONDAYS[0],True)[0];free=usable(MONDAYS[0],False)
    assert book(parent,id,blocked).status_code==409
    assert ok(book(pupil2,id,blocked,student='s2'))['status']=='scheduled'
    assert ok(book(parent,id,free[0]))['status']=='scheduled'
    assert book(parent,id,free[1]).status_code==409
    assert ok(book(parent,id,usable(MONDAYS[1],False)[0]))['status']=='scheduled'


def test_pupil_books_only_self_and_keeps_booking_after_switch_off(client,monkeypatch):
    prepared(client,monkeypatch);other_family(client,'guardian','s1');ok(switch(client,True))
    made=create_series(signed('teacher'));pupil=signed('student');items=on_date(pupil,made['id'],MONDAYS[0])
    assert book(pupil,made['id'],items[0],student='s2').status_code==403
    result=ok(book(pupil,made['id'],items[0],note='Мой вопрос'))
    guardian=signed('guardian');seen=mine(guardian)
    assert len(seen)==1 and seen[0]['id']==result['id'] and seen[0]['ownBooking'] is False and seen[0]['canCancel'] is False and 'note' not in seen[0] and seen[0]['byPupil'] is True
    own=mine(pupil);assert own[0]['note']=='Мой вопрос' and own[0]['canCancel'] is True
    assert guardian.post(path('bookings/'+result['id']),json=request(version=1,action='cancel')).status_code==404
    ok(switch(client,False,version=1))
    assert pupil.post(path('slots/'+items[1]['id']+'/book'),json=request(version=items[1]['version'],eventVersion=1,studentId='s1')).status_code==403
    assert bookings(pupil,made['id'])[0]['id']==result['id'] and ok(pupil.get(path('options')))['students']==[]
    assert ok(pupil.post(path('bookings/'+result['id']),json=request(version=1,action='cancel')))['status']=='cancelled'
    assert pupil.get(path('options')).status_code==403
    assert mine(signed('parent'))[0]['status']=='cancelled'


def test_teacher_cancels_day_and_series_and_those_booked_are_told(client,monkeypatch):
    prepared(client,monkeypatch);other_family(client,'guardian','s1');other_family(client,'second','s2');ok(switch(client,True))
    teacher=signed('teacher');id=create_series(teacher,endDate=MONDAYS[1])['id'];parent,pupil,guardian,second=signed('parent'),signed('student'),signed('guardian'),signed('second')
    first=ok(book(parent,id,on_date(parent,id,MONDAYS[0])[0]));later=ok(book(pupil,id,on_date(pupil,id,MONDAYS[1])[0]))
    host=detail(teacher,id)['hosts'][0]['accountId'];waiting=ok(join(second,id,host,student='s2'))
    assert detail(teacher,id)['canManage'] is True and waiting['status']=='waiting'
    body=request(version=detail(teacher,id)['version'],action='block',hostIds=[host],startsAt=datetime(2027,4,5,0,0,tzinfo=ZONE).isoformat(),endsAt=datetime(2027,4,5,23,59,tzinfo=ZONE).isoformat(),reason='Учитель болеет')
    found=ok(teacher.post(path('events/'+id+'/availability/preview'),json=body));assert found['bookingsToCancel']==1
    assert ok(teacher.post(path('events/'+id+'/availability'),json=body|dict(previewHash=found['previewHash'])))['cancelledBookings']==1
    assert {b['id']:b['status'] for b in bookings(teacher,id)}=={first['id']:'cancelled',later['id']:'scheduled'}
    assert all(s['status']=='blocked' for s in on_date(teacher,id,MONDAYS[0])) and any(s['status']=='occupied' for s in on_date(teacher,id,MONDAYS[1]))
    for account in [teacher,parent,pupil,guardian]:assert len(got(account,'meeting.cancelled'))==1
    assert not got(second,'meeting.cancelled')
    cancelled=next(i for i in mine(parent) if i['id']==first['id']);assert cancelled['reason']=='Учитель болеет' and cancelled['status']=='cancelled'
    version=detail(teacher,id)['version']
    assert pupil.post(path('events/'+id+'/transition'),json=request(version=version,action='cancel',reason='x')).status_code==403
    assert teacher.post(path('events/'+id+'/transition'),json=request(version=version,action='cancel')).status_code==422
    ok(teacher.post(path('events/'+id+'/transition'),json=request(version=version,action='cancel',reason='Консультации отменены')))
    assert [b['status'] for b in mine(pupil)]==['cancelled','cancelled'] and len(got(pupil,'meeting.cancelled'))==2 and len(got(guardian,'meeting.cancelled'))==2
    assert ok(second.get(path('events/'+id+'/waitlist')))['items'][0]['status']=='cancelled'
    assert all(s['status']=='blocked' for s in slots(teacher,id))


def test_teacher_moves_booking_to_other_date(client,monkeypatch):
    prepared(client,monkeypatch);other_family(client,'guardian','s1');other_teacher(client);ok(switch(client,True))
    teacher=signed('teacher');id=create_series(teacher,endDate=MONDAYS[1])['id'];parent=signed('parent')
    first=on_date(parent,id,MONDAYS[0]);second=on_date(parent,id,MONDAYS[1])
    booking=ok(book(parent,id,first[0]))['id']
    assert signed('math').post(path('bookings/'+booking+'/reschedule'),json=request(version=1,eventVersion=1,sourceSlotVersion=1,targetSlotId=second[0]['id'],targetSlotVersion=1,reason='x')).status_code==403
    choices=ok(get(teacher,'bookings/'+booking+'/reschedule-options'))
    assert any(i['id']==second[0]['id'] and i['available'] for i in choices['items'])
    moved=ok(reschedule(teacher,id,booking,second[0]))
    assert moved['startsAt']==second[0]['startsAt']
    for account in ['parent','student','guardian']:assert len(got(signed(account),'meeting.rescheduled'))==1
    # The old date is free for the child again, the new one is taken.
    again=ok(book(parent,id,on_date(parent,id,MONDAYS[0])[1]))['id']
    assert reschedule(teacher,id,again,on_date(teacher,id,MONDAYS[1])[1]).status_code==409


def test_waitlist_with_pupil_and_teacher_offer(client,monkeypatch):
    prepared(client,monkeypatch);other_teacher(client);ok(switch(client,True))
    teacher=signed('teacher');id=create_series(teacher)['id'];pupil=signed('student');host=detail(teacher,id)['hosts'][0]['accountId']
    wid=ok(join(pupil,id,host,note='После уроков'))['id']
    assert queue(teacher,id)['items'][0]['canOffer'] is True and queue(pupil,id)['items'][0]['canAccept'] is False
    assert signed('math').get(path('events/'+id+'/waitlist')).status_code==403
    result,_=offered(teacher,pupil,id,wid);assert result['status']=='offered' and len(got(pupil,'meeting.waitlist_offer'))==1
    accepted=ok(change(pupil,id,wid,'accept')[0])
    assert bookings(pupil,id)[0]['id']==accepted['bookingId']
    offer=request(version=1,eventVersion=1,slotId='x',slotVersion=1,expiresAt=BASE.isoformat())
    assert signed('math').post(path('waitlist/'+wid+'/offer'),json=offer).status_code==404 and pupil.post(path('waitlist/'+wid+'/offer'),json=offer).status_code==403


def test_day_view_combines_lessons_slots_and_class_meetings(client,monkeypatch):
    teacher,parent,pupil=prepared(client,monkeypatch);other_teacher(client);lead('t2');tc=signed('teacher')
    id=create_series(tc,endDate=MONDAYS[0],startTime='08:00',endTime='08:45')['id']
    ok(book(signed('parent'),id,on_date(tc,id,MONDAYS[0])[0]))
    parents=new_meeting(client,[dict(accountId=teacher['id'],teacherId='t2',location='301')]);publish_meeting(client,parents)
    meeting=ok(client.post(path('class-meetings'),json=request(title='Собрание 10А',classIds=['c-demo-a'],startsAt=datetime(2027,4,5,13,0,tzinfo=ZONE).isoformat(),endsAt=datetime(2027,4,5,14,0,tzinfo=ZONE).isoformat(),location='Актовый зал')))
    view=ok(day_view(tc,MONDAYS[0]))
    assert view['lessons'] and all(isinstance(l['rooms'],str) for l in view['lessons']) and {s['kind'] for s in view['slots']}=={'consultation','meeting'}
    booked=next(s for s in view['slots'] if s['kind']=='consultation' and s['status']=='occupied')
    assert booked['booking']['studentName'].startswith('Ученик 1') and booked['conflict'] is None
    assert len([s for s in view['slots'] if s['kind']=='meeting'])==12
    assert [m['id'] for m in view['classMeetings']]==[meeting['id']] and view['classMeetings'][0]['pupils']==2
    assert ok(day_view(tc,'2027-04-06'))['slots']==[] and day_view(signed('math'),MONDAYS[0],hostId=teacher['id']).status_code==403
    assert ok(day_view(client,MONDAYS[0],hostId=teacher['id']))['hostName']==teacher['name'] and day_view(signed('parent'),MONDAYS[0]).status_code==403
    ok(tc.post(path('events/'+id+'/transition'),json=request(version=detail(tc,id)['version'],action='cancel',reason='Отмена')))
    assert {s['kind'] for s in ok(day_view(tc,MONDAYS[0]))['slots']}=={'meeting'}


def test_first_request_after_an_update_migrates_a_school_database(client,monkeypatch):
    prepared(client,monkeypatch);ok(client.get(path('options')))
    # Bring the tables back to the shape an existing school has: it already carries the waiting-list marker but nothing newer.
    with api.access.lock,api.connect() as db:
        db.execute('DROP INDEX meeting_booked_child_occasion');db.execute('ALTER TABLE meeting_bookings DROP COLUMN occasion')
        db.execute("CREATE UNIQUE INDEX meeting_booked_child ON meeting_bookings(event_id,host_id,student_id) WHERE status!='cancelled'")
        for table in ['meeting_settings','class_meetings','class_meeting_invitees']:db.execute('DROP TABLE '+table)
    ok(client.get(path('options')))
    with api.connect() as db:
        assert 'occasion' in {r[1] for r in db.execute('PRAGMA table_info(meeting_bookings)')}
        names={r[1] for r in db.execute('PRAGMA index_list(meeting_bookings)')};assert 'meeting_booked_child_occasion' in names and 'meeting_booked_child' not in names
    made=create_series(signed('teacher'));assert ok(book(signed('parent'),made['id'],on_date(signed('parent'),made['id'],MONDAYS[0])[0]))['status']=='scheduled'


def test_series_caps_count_closed_series_and_free_up_by_archive_or_cancel(client,monkeypatch):
    prepared(client,monkeypatch);other_teacher(client)
    monkeypatch.setattr(meeting_consultations,'MAX_OWN_SERIES',2);monkeypatch.setattr(meeting_consultations,'MAX_SERIES',3)
    teacher,math=signed('teacher'),signed('math');week=lambda n:dict(startDate=MONDAYS[n],endDate=MONDAYS[n])
    a=create_series(teacher,**week(0))['id'];b=create_series(teacher,**week(1))['id']
    assert try_series(teacher,**week(2)).status_code==422
    # Closing a series stops bookings but it still counts; archiving frees the place.
    ok(act(teacher,a,'close'));assert try_series(teacher,**week(2)).status_code==422
    mine=dict(teacherId='t0',roomId='r1');create_series(math,**mine,**week(0))
    assert try_series(math,**mine,**week(1)).status_code==422
    ok(act(teacher,a,'archive'));ok(try_series(teacher,**week(2)))
    ok(act(teacher,b,'cancel',reason='Не веду'));ok(try_series(math,**mine,**week(1)))


def test_series_slot_cap_refuses_a_window_with_too_many_slots(client,monkeypatch):
    prepared(client,monkeypatch);teacher=signed('teacher');monkeypatch.setattr(meeting_consultations,'MAX_SLOTS',3)
    after_school=dict(startDate=MONDAYS[0],endDate=MONDAYS[0],roomId=None,location='Кабинет',startTime='17:00')
    assert preview(teacher,**after_school,endTime='19:00').status_code==422
    with api.connect() as db:assert db.execute('SELECT count(*) FROM meeting_events').fetchone()[0]==0
    assert create_series(teacher,**after_school,endTime='17:45')['slots']==3


def test_series_do_not_count_toward_the_hundred_event_limit(client,monkeypatch):
    teacher,_,_=prepared(client,monkeypatch);made=create_series(signed('teacher'))
    def parent_event():
        definition=dict(title='Встречи',startsAt=BASE.isoformat(),endsAt=(BASE+timedelta(hours=2)).isoformat(),hosts=[dict(accountId=teacher['id'],teacherId='t2',location='301')])
        return client.post(path('events'),json=request(definition=definition))
    with api.connect() as db:
        base=read_row(db,'meeting_events',id=made['id']);base.pop('seq')
        for number in range(100):insert_row(db,'meeting_events',base,id=f'series{number}')
    first=ok(parent_event())['id']
    with api.connect() as db:
        base=read_row(db,'meeting_events',id=first);base.pop('seq')
        for number in range(99):insert_row(db,'meeting_events',base,id=f'parent{number}')
    assert parent_event().status_code==422
    listed=ok(client.get(path('events')+'&limit=50'))['items'];assert len(listed)==50 and all(i['title']=='Встречи' for i in listed)


def test_class_only_closure_stops_the_pupils_of_that_class_but_not_the_series(client,monkeypatch):
    prepared(client,monkeypatch);parent=signed('parent')
    state=api.read('demo')
    state.events=[Event(id='a',name='Карантин 10А',start=date(2027,4,12),end=date(2027,4,12),kind='holiday',effect='cancel',classIds=['c-demo-a']),
                  Event(id='d',name='Карантин 10Д',start=date(2027,4,19),end=date(2027,4,19),kind='holiday',effect='cancel',classIds=['c-demo-d'])]
    api.save('demo',state);id=create_series(signed('teacher'))['id']
    assert on_date(parent,id,MONDAYS[1]) and on_date(parent,id,MONDAYS[2])
    assert book(parent,id,on_date(parent,id,MONDAYS[1])[0]).status_code==409
    state=api.read('demo');state.events=state.events[1:];api.save('demo',state)
    # With the closure of the pupil's class gone the same booking goes through, and another class's closure never stood in the way.
    assert ok(book(parent,id,on_date(parent,id,MONDAYS[1])[0]))['status']=='scheduled'
    assert ok(book(parent,id,on_date(parent,id,MONDAYS[2])[0]))['status']=='scheduled'


def test_family_list_drops_a_series_once_it_is_over(client,monkeypatch):
    prepared(client,monkeypatch);teacher=signed('teacher');parent=signed('parent');id=create_series(teacher,endDate=MONDAYS[1])['id']
    assert [i['id'] for i in ok(get(parent,'consultations'))['items']]==[id]
    monkeypatch.setattr(meetings,'now',lambda:datetime(2027,4,13,tzinfo=timezone.utc))
    assert ok(get(parent,'consultations'))['items']==[] and [i['id'] for i in ok(get(teacher,'consultations'))['items']]==[id]


def test_old_booking_does_not_stop_a_new_waitlist_request_in_a_series(client,monkeypatch):
    prepared(client,monkeypatch);teacher=signed('teacher');parent=signed('parent');id=create_series(teacher)['id'];host=detail(teacher,id)['hosts'][0]['accountId']
    ok(book(parent,id,on_date(parent,id,MONDAYS[0])[0]))
    assert join(parent,id,host).status_code==409
    monkeypatch.setattr(meetings,'now',lambda:datetime(2027,4,6,tzinfo=timezone.utc))
    assert ok(join(parent,id,host))['status']=='waiting'


def test_reminder_reaches_booker_host_pupil_and_guardians_of_a_consultation_only(client,monkeypatch):
    host,_,_=prepared(client,monkeypatch);other_family(client,'guardian','s1');ok(switch(client,True))
    teacher,parent,pupil,guardian=signed('teacher'),signed('parent'),signed('student'),signed('guardian')
    series_id=create_series(teacher)['id'];ok(book(parent,series_id,on_date(parent,series_id,MONDAYS[0])[0]))
    # An hour before the consultation (08:00 school time), then before the parent meeting at 17:00.
    monkeypatch.setattr(meeting_reminders,'now',lambda:datetime(2027,4,5,2,30,tzinfo=timezone.utc))
    assert api.meetings.reminders.process_one()
    assert [len(got(account,'meeting.reminder')) for account in [teacher,parent,pupil,guardian]]==[1,1,1,1]
    meeting=new_meeting(client,[dict(accountId=host['id'],teacherId='t2',location='301')]);publish_meeting(client,meeting)
    private=ok(book(parent,meeting,ok(teacher.get(path('events/'+meeting+'/slots')))['items'][0]))
    monkeypatch.setattr(meeting_reminders,'now',lambda:BASE-timedelta(minutes=30))
    assert api.meetings.reminders.process_one()
    assert [len(got(account,'meeting.reminder')) for account in [teacher,parent,pupil,guardian]]==[2,2,1,1] and private['status']=='scheduled'


def test_host_whose_link_ended_loses_control_and_the_day_view(client,monkeypatch):
    account,_,_=prepared(client,monkeypatch);other_family(client,'second','s2');teacher=signed('teacher');parent=signed('parent')
    id=create_series(teacher,endDate=MONDAYS[1])['id'];host=detail(teacher,id)['hosts'][0]['accountId']
    first,later=on_date(parent,id,MONDAYS[0]),on_date(parent,id,MONDAYS[1])
    booking=ok(book(parent,id,first[0],note='Личный вопрос'))['id'];wid=ok(join(signed('second'),id,host,student='s2'))['id']
    own=lambda c,**kw:[s for s in ok(day_view(c,MONDAYS[0],**kw))['slots'] if s['status']=='occupied']
    assert own(teacher)[0]['booking']['note']=='Личный вопрос'
    current=next(b for b in bookings(teacher,id) if b['id']==booking);source=next(s for s in slots(teacher,id) if s['startsAt']==current['startsAt']);version=detail(teacher,id)['version']
    # The account is linked to another teacher now: nothing of the series is theirs any more, and the day view hides it too.
    regrant(account['id'],['t0'],'teacher')
    block=request(version=version,action='block',hostIds=[host],startsAt=datetime(2027,4,6,0,0,tzinfo=ZONE).isoformat(),endsAt=datetime(2027,4,6,23,59,tzinfo=ZONE).isoformat(),reason='x')
    assert teacher.post(path('events/'+id+'/availability/preview'),json=block).status_code==403
    assert teacher.post(path('events/'+id+'/transition'),json=request(version=version,action='cancel',reason='x')).status_code==403
    assert teacher.post(path('bookings/'+booking+'/reschedule'),json=request(version=current['version'],eventVersion=version,sourceSlotVersion=source['version'],targetSlotId=later[0]['id'],targetSlotVersion=later[0]['version'],reason='x')).status_code==403
    assert teacher.post(path('waitlist/'+wid+'/offer'),json=request(version=1,eventVersion=version,slotId=later[1]['id'],slotVersion=later[1]['version'],expiresAt=BASE.isoformat())).status_code==404
    assert own(teacher)==[] and bookings(teacher,id)==[]
    # The administrator still sees the series on the host's day, with the booking, and it is still open.
    assert own(client,hostId=account['id'])[0]['booking']['note']=='Личный вопрос'
    with api.connect() as db:assert db.execute("SELECT status FROM meeting_events WHERE id=?",(id,)).fetchone()[0]=='open'


def test_cancelling_a_series_leaves_meetings_that_already_took_place(client,monkeypatch):
    prepared(client,monkeypatch);other_family(client,'guardian','s1');ok(switch(client,True))
    teacher,parent,pupil,guardian=signed('teacher'),signed('parent'),signed('student'),signed('guardian')
    id=create_series(teacher,endDate=MONDAYS[1])['id']
    held=ok(book(parent,id,on_date(parent,id,MONDAYS[0])[0]));ahead=ok(book(parent,id,on_date(parent,id,MONDAYS[1])[0]))
    # The first consultation is over and nobody marked it.
    for module in [meetings,meeting_routes]:monkeypatch.setattr(module,'now',lambda:datetime(2027,4,8,tzinfo=timezone.utc))
    ok(act(teacher,id,'cancel',reason='Меняю день приёма'))
    assert {b['id']:b['status'] for b in bookings(teacher,id)}=={held['id']:'scheduled',ahead['id']:'cancelled'}
    assert [len(got(account,'meeting.cancelled')) for account in [parent,pupil,guardian]]==[1,1,1]
    assert {s['status'] for s in on_date(teacher,id,MONDAYS[0])}>={'occupied'} and {s['status'] for s in on_date(teacher,id,MONDAYS[1])}=={'blocked'}
    assert ok(teacher.post(path('bookings/'+held['id']),json=request(version=1,action='attended')))['status']=='attended'
