from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timedelta,timezone
import json
from uuid import uuid4
from urllib.parse import quote
import pytest
from backend import api,meetings,meeting_routes,meeting_reminders
from backend.models import TeacherAbsence,Commitment
from test_access import client,enable,create,signed


BASE=datetime(2027,4,5,12,tzinfo=timezone.utc)  # 17:00 school time
def request(**kw):return dict(requestId=str(uuid4()),**kw)
def path(part):return '/api/meetings/'+part+'?workspace=demo'
def ok(response):
    assert response.status_code==200,response.text
    return response.json()
def event(client,id):return ok(client.get(path('events/'+id)))
def hosts(teacher,**kw):return dict(accountId=teacher['id'],teacherId='t2',location='301',**kw)
def setup(client,monkeypatch,host_count=1):
    for module in [meetings,meeting_routes,meeting_reminders]:monkeypatch.setattr(module,'now',lambda:BASE-timedelta(days=2))
    enable(client);teacher=create(client,'teacher');parent=create(client,'parent')
    chosen=[hosts(teacher)]
    if host_count==2:
        second=create(client,'teacher',login='math',grants=[dict(workspace='demo',view='teacher',entityIds=['t0'])])
        chosen.append(dict(accountId=second['id'],teacherId='t0',location='302'))
    id=new(client,chosen)
    return id,teacher,parent
def new(client,chosen,**kw):
    definition=dict(title='Встречи с родителями',startsAt=BASE.isoformat(),endsAt=(BASE+timedelta(hours=2)).isoformat(),hosts=chosen)|kw
    return ok(client.post(path('events'),json=request(definition=definition)))['id']
def availability(client,id,action='generate',start=BASE,end=BASE+timedelta(hours=2),hostIds=None,**kw):
    e=event(client,id)
    body=request(version=e['version'],action=action,hostIds=hostIds or [h['accountId'] for h in e['hosts']],startsAt=start.isoformat(),endsAt=end.isoformat(),**kw)
    preview=ok(client.post(path('events/'+id+'/availability/preview'),json=body))
    return body|dict(previewHash=preview['previewHash']),preview
def apply(client,id,**kw):
    body,preview=availability(client,id,**kw)
    return ok(client.post(path('events/'+id+'/availability'),json=body))
def transition(client,id,action,**kw):return client.post(path('events/'+id+'/transition'),json=request(version=event(client,id)['version'],action=action,**kw))
def publish(client,id):apply(client,id);ok(transition(client,id,'publish'))
def slots(client,id):return ok(client.get(path('events/'+id+'/slots')))['items']
def book(client,id,slot,student='s1',**kw):return client.post(path('slots/'+slot['id']+'/book'),json=request(version=slot['version'],eventVersion=event(client,id)['version'],studentId=student,**kw))
def bookings(client,id):return ok(client.get(path('bookings')+'&eventId='+id))['items']
def regrant(account_id,ids,view):
    # An unrelated or real change of the account's rights: bumps the version and rewrites the grants.
    with api.connect() as db:
        raw=json.loads(db.execute('SELECT permissions FROM accounts WHERE id=?',(account_id,)).fetchone()[0]);raw['grants']=[dict(workspace='demo',view=view,entityIds=ids)]
        db.execute('UPDATE accounts SET permissions=?,version=version+1 WHERE id=?',(json.dumps(raw),account_id))


def test_admin_owns_availability_teacher_agenda_parent_privacy_and_unchanged_school(client,monkeypatch):
    id,t,p=setup(client,monkeypatch);before=api.read('demo').model_dump(mode='json')
    teacher=signed('teacher');parent=signed('parent')
    assert parent.get(path('events/'+id)).status_code==404
    body,_=availability(client,id)
    assert teacher.post(path('events/'+id+'/availability'),json=body).status_code==403
    publish(client,id);ss=slots(parent,id);assert len(ss)==12
    result=ok(book(parent,id,ss[0],note='Личный вопрос'));bid=result['id']
    assert bookings(teacher,id)[0]['note']=='Личный вопрос'
    other=create(client,'parent',login='other',grants=[dict(workspace='demo',view='student',entityIds=['s2'])])
    other_client=signed('other');occupied=slots(other_client,id)[0]
    assert occupied['status']=='occupied' and 'booking' not in occupied
    assert other_client.get(path('bookings/'+bid+'/history')).status_code==404
    assert teacher.post(path('bookings/'+bid),json=request(version=1,action='cancel',reason='Late')).status_code==403
    assert parent.get(path('events/'+id+'/export')).status_code==403
    assert api.read('demo').model_dump(mode='json')==before


def test_preview_cancellation_rechecks_bookings_notifies_and_keeps_history(client,monkeypatch):
    id,t,p=setup(client,monkeypatch);publish(client,id);parent=signed('parent')
    body,preview=availability(client,id,action='block',end=BASE+timedelta(minutes=10),reason='Учитель придёт позже')
    assert preview['bookingsToCancel']==0
    bid=ok(book(parent,id,slots(parent,id)[0]))['id']
    assert client.post(path('events/'+id+'/availability'),json=body).status_code==409
    body,preview=availability(client,id,action='block',end=BASE+timedelta(minutes=10),reason='Учитель придёт позже')
    assert preview['bookingsToCancel']==1
    result=ok(client.post(path('events/'+id+'/availability'),json=body));assert result['cancelledBookings']==1
    assert ok(client.post(path('events/'+id+'/availability'),json=body))==result
    assert bookings(parent,id)[0]['status']=='cancelled' and slots(parent,id)[0]['status']=='blocked'
    assert ok(parent.get(path('bookings/'+bid+'/history')))[0]['reason']=='Учитель придёт позже'
    with api.connect() as db:assert db.execute("SELECT count(*) FROM notifications WHERE user_id=? AND action='meeting.cancelled'",(p['id'],)).fetchone()[0]==1
    apply(client,id,action='unblock',end=BASE+timedelta(minutes=10));assert slots(parent,id)[0]['status']=='available'


def test_concurrent_slot_race_has_one_winner_and_idempotent_replay(client,monkeypatch):
    id,t,p=setup(client,monkeypatch);publish(client,id)
    create(client,'parent',login='other',grants=[dict(workspace='demo',view='student',entityIds=['s2'])])
    a,b=signed('parent'),signed('other');s=slots(a,id)[0];version=event(a,id)['version']
    bodies=[request(version=s['version'],eventVersion=version,studentId=student) for student in ['s1','s2']]
    def send(pair):c,body=pair;return c.post(path('slots/'+s['id']+'/book'),json=body)
    with ThreadPoolExecutor(2) as pool:responses=list(pool.map(send,zip([a,b],bodies)))
    assert sorted(r.status_code for r in responses)==[200,409]
    winner=next(i for i,r in enumerate(responses) if r.status_code==200)
    assert send(([a,b][winner],bodies[winner])).json()==responses[winner].json()
    assert len(bookings(client,id))==1


def test_parent_child_and_duplicate_guardian_conflicts(client,monkeypatch):
    id,t,p=setup(client,monkeypatch,2);publish(client,id);parent=signed('parent')
    ss=slots(parent,id);english=[s for s in ss if s['hostId']==t['id']];math=[s for s in ss if s['hostId']!=t['id']]
    ok(book(parent,id,english[0]));assert book(parent,id,math[0]).status_code==409
    assert book(parent,id,english[1]).status_code==409
    create(client,'parent',login='guardian');guardian=signed('guardian')
    assert book(guardian,id,english[1]).status_code==409
    assert book(guardian,id,math[0]).status_code==409
    ok(book(guardian,id,math[1]))


def test_mixed_groups_and_explicit_staff_classes(client,monkeypatch):
    id,t,p=setup(client,monkeypatch,2);publish(client,id)
    create(client,'parent',login='physics',grants=[dict(workspace='demo',view='student',entityIds=['s2'])]);parent=signed('physics')
    assert len(event(parent,id)['hosts'])==1
    adminslots=slots(client,id);math=next(s for s in adminslots if s['hostId']!=t['id'])
    assert book(parent,id,math,student='s2').status_code==404
    staff=create(client,'staff');newid=new(client,[dict(accountId=staff['id'],scope='classes',classIds=['c-demo-a'],location='Library')],startsAt=(BASE+timedelta(days=1)).isoformat(),endsAt=(BASE+timedelta(days=1,hours=2)).isoformat())
    apply(client,newid,start=BASE+timedelta(days=1),end=BASE+timedelta(days=1,hours=2));ok(transition(client,newid,'publish'))
    ok(book(parent,newid,slots(parent,newid)[0],student='s2'))


def test_room_teacher_calendar_conflicts_and_no_partial_bulk_insert(client,monkeypatch):
    id,t,p=setup(client,monkeypatch)
    another=create(client,'teacher',login='math',grants=[dict(workspace='demo',view='teacher',entityIds=['t0'])])
    second=new(client,[hosts(t,roomId='r0'),dict(accountId=another['id'],teacherId='t0',roomId='r0')])
    body,preview=availability(client,second);assert preview['conflictCount']>0
    assert client.post(path('events/'+second+'/availability'),json=body).status_code==409
    assert slots(client,second)==[]
    apply(client,id)
    body,preview=availability(client,second);assert preview['conflictCount']>0
    state=api.read('demo');state.absences=[TeacherAbsence(id='a',name='Unavailable',teacherId='t2',start=BASE.date(),end=BASE.date(),startTime='17:00',endTime='18:00')];api.save('demo',state)
    assert transition(client,id,'publish').status_code==409


def test_current_family_access_removed_and_other_workspace_hidden(client,monkeypatch):
    id,t,p=setup(client,monkeypatch);publish(client,id);parent=signed('parent');bid=ok(book(parent,id,slots(parent,id)[0]))['id']
    from test_families import save
    save(client,p['id'],status='revoked',reason='Access ended')
    assert parent.get(path('bookings/'+bid+'/history')).status_code in [403,404,409]
    assert bookings(client,id)[0]['parentAccessCurrent'] is False
    assert parent.get('/api/meetings/events?workspace=school').status_code==403
    assert bookings(signed('teacher'),id)[0]['id']==bid


def test_cancel_cutoff_attendance_and_event_lifecycle(client,monkeypatch):
    id,t,p=setup(client,monkeypatch);publish(client,id);parent=signed('parent');teacher=signed('teacher')
    bid=ok(book(parent,id,slots(parent,id)[0]))['id']
    change=lambda c,action,v=1:c.post(path('bookings/'+bid),json=request(version=v,action=action,reason='Test'))
    assert change(teacher,'attended').status_code==409
    ok(transition(client,id,'close'));assert not event(parent,id)['registrationOpen']
    ok(transition(client,id,'reopen'))
    for module in [meetings,meeting_routes,meeting_reminders]:monkeypatch.setattr(module,'now',lambda:BASE)
    assert change(parent,'cancel').status_code==409 and change(parent,'attended').status_code==403
    ok(change(teacher,'attended'));assert bookings(parent,id)[0]['status']=='attended'
    assert change(teacher,'no_show').status_code==409
    ok(change(teacher,'no_show',2));assert len(ok(parent.get(path('bookings/'+bid+'/history'))))==3
    ok(transition(client,id,'archive'))
    assert change(teacher,'scheduled',3).status_code==409


def test_booking_and_cancellation_roll_back_when_notification_fails(client,monkeypatch):
    id,t,p=setup(client,monkeypatch);publish(client,id);parent=signed('parent');slot=slots(parent,id)[0]
    original=api.notifications.deliver
    def fail(*args,**kwargs):raise RuntimeError('synthetic notification failure')
    monkeypatch.setattr(api.notifications,'deliver',fail)
    with pytest.raises(RuntimeError):book(parent,id,slot)
    assert bookings(client,id)==[] and slots(parent,id)[0]['version']==slot['version']
    monkeypatch.setattr(api.notifications,'deliver',original);bid=ok(book(parent,id,slot))['id']
    monkeypatch.setattr(api.notifications,'deliver',fail)
    with pytest.raises(RuntimeError):parent.post(path('bookings/'+bid),json=request(version=1,action='cancel'))
    assert bookings(client,id)[0]['status']=='scheduled'


def test_reminders_retry_transaction_no_duplicates_and_cancelled_skip(client,monkeypatch):
    id,t,p=setup(client,monkeypatch);publish(client,id);parent=signed('parent');bid=ok(book(parent,id,slots(parent,id)[0]))['id']
    worker=api.meetings.reminders
    for module in [meetings,meeting_routes,meeting_reminders]:monkeypatch.setattr(module,'now',lambda:BASE-timedelta(minutes=30))
    original=api.notifications.deliver
    def fail(*args,**kwargs):raise RuntimeError('synthetic notification failure')
    monkeypatch.setattr(api.notifications,'deliver',fail)
    with pytest.raises(RuntimeError):worker.process_one()
    with api.connect() as db:assert db.execute('SELECT status FROM meeting_reminders WHERE booking_id=?',(bid,)).fetchone()[0]=='pending'
    monkeypatch.setattr(api.notifications,'deliver',original)
    assert worker.process_one() and not worker.process_one()
    with api.connect() as db:assert db.execute("SELECT count(*) FROM notifications WHERE action='meeting.reminder'").fetchone()[0]==2
    ok(client.post(path('bookings/'+bid),json=request(version=1,action='cancel',reason='Teacher absent')))
    assert not worker.process_one()


def test_pagination_csv_and_read_while_writer_holds_lock(client,monkeypatch):
    id,t,p=setup(client,monkeypatch);publish(client,id);parent=signed('parent');ok(book(parent,id,slots(parent,id)[0],note='=1+1'))
    export=client.get(path('events/'+id+'/export'));assert export.status_code==200 and "'=1+1" in export.text
    first=ok(parent.get(path('events/'+id+'/slots')+'&limit=5'));assert len(first['items'])==5
    second=ok(parent.get(path('events/'+id+'/slots')+'&limit=5&after='+quote(first['nextAfter'])));assert not {s['id'] for s in first['items']} & {s['id'] for s in second['items']}
    with api.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        assert parent.get(path('events/'+id+'/slots')).status_code==200


def test_anonymous_student_and_editor_cannot_manage(client,monkeypatch):
    assert client.get(path('events')).status_code==403
    id,t,p=setup(client,monkeypatch);create(client,'student');create(client,'editor');publish(client,id)
    assert signed('student').get(path('events')).status_code==403
    assert signed('editor').get(path('events/'+id)).status_code==404


def test_reminder_worker_start_once_and_stop(client,monkeypatch):
    setup(client,monkeypatch);worker=meeting_reminders.MeetingReminders(api.meetings)
    worker.start();thread=worker.thread
    try:worker.start();assert worker.thread is thread and thread.is_alive()
    finally:worker.shutdown()
    assert not thread.is_alive()


def test_teacher_cannot_omit_physical_teacher_and_versions_recheck(client,monkeypatch):
    id,t,p=setup(client,monkeypatch)
    definition=event(client,id)['definition']|dict(hosts=[dict(accountId=t['id'],scope='classes',classIds=['c-demo-a'],location='301')])
    assert client.post(path('events'),json=request(definition=definition)).status_code==422
    body,_=availability(client,id)
    apply(client,id,start=BASE+timedelta(hours=1),end=BASE+timedelta(hours=2))
    assert client.post(path('events/'+id+'/availability'),json=body).status_code==409
    assert len(slots(client,id))==6


def test_availability_and_reschedule_do_not_invalidate_parent_booking(client,monkeypatch):
    id,t,p=setup(client,monkeypatch,2);publish(client,id);parent=signed('parent')
    v=event(client,id)['version'];second=next(h['accountId'] for h in event(client,id)['hosts'] if h['accountId']!=t['id'])
    assert apply(client,id,action='block',hostIds=[second],reason='x')['affectedSlots']==12
    assert event(client,id)['version']==v
    with api.connect() as db:assert db.execute('SELECT version FROM meeting_events WHERE id=?',(id,)).fetchone()[0]==v
    slot=next(s for s in slots(parent,id) if s['hostId']==t['id'])
    ok(parent.post(path('slots/'+slot['id']+'/book'),json=request(version=slot['version'],eventVersion=v,studentId='s1')))
    ok(transition(client,id,'close'))
    create(client,'parent',login='other',grants=[dict(workspace='demo',view='student',entityIds=['s2'])]);other=signed('other')
    free=next(s for s in slots(client,id) if s['hostId']==t['id'] and s['status']=='available')
    late=other.post(path('slots/'+free['id']+'/book'),json=request(version=free['version'],eventVersion=v,studentId='s2'))
    assert late.status_code==409 and late.json()['detail'].startswith('Запись изменилась')


def test_availability_preview_survives_timetable_save(client,monkeypatch):
    id,t,p=setup(client,monkeypatch);body,preview=availability(client,id);assert preview['affectedSlots']==12
    state=api.read('demo');state.settings.name='Другое название школы';api.save('demo',state)
    ok(client.post(path('events/'+id+'/availability'),json=body));assert len(slots(client,id))==12


def test_range_operations_skip_started_slots(client,monkeypatch):
    id,t,p=setup(client,monkeypatch,2);first=t['id'];second=next(h['accountId'] for h in event(client,id)['hosts'] if h['accountId']!=first)
    apply(client,id,hostIds=[first]);ok(transition(client,id,'publish'))
    clock=BASE+timedelta(minutes=30)
    for module in [meetings,meeting_routes]:monkeypatch.setattr(module,'now',lambda:clock)
    def statuses(host):
        with api.connect() as db:return [r[0] for r in db.execute('SELECT status FROM meeting_slots WHERE event_id=? AND host_id=? ORDER BY starts_at',(id,host))]
    body,preview=availability(client,id,action='block',hostIds=[first],reason='Учитель придёт позже')
    assert preview['skippedPast']==4 and preview['affectedSlots']==8
    result=ok(client.post(path('events/'+id+'/availability'),json=body));assert result['affectedSlots']==8 and result['skippedPast']==4
    assert statuses(first)==['open']*4+['blocked']*8
    body,preview=availability(client,id,hostIds=[second]);assert preview['skippedPast']==4 and preview['affectedSlots']==8
    assert ok(client.post(path('events/'+id+'/availability'),json=body))['affectedSlots']==8 and len(statuses(second))==8
    body,preview=availability(client,id,action='block',hostIds=[first],end=BASE+timedelta(minutes=20),reason='Поздно')
    assert preview['affectedSlots']==0 and preview['skippedPast']==2
    response=client.post(path('events/'+id+'/availability'),json=body)
    assert response.status_code==422 and 'уже начались или прошли' in response.json()['detail']
    assert statuses(first)==['open']*4+['blocked']*8


def test_bookings_from_date_lists_all_upcoming_for_own_family(client,monkeypatch):
    id,t,p=setup(client,monkeypatch);publish(client,id);parent=signed('parent')
    day=BASE+timedelta(days=1);second=new(client,[hosts(t)],startsAt=day.isoformat(),endsAt=(day+timedelta(hours=2)).isoformat())
    apply(client,second,start=day,end=day+timedelta(hours=2));ok(transition(client,second,'publish'))
    ok(book(parent,id,slots(parent,id)[0]));ok(book(parent,second,slots(parent,second)[0]))
    def listing(account,**params):return ok(account.get('/api/meetings/bookings',params=dict(workspace='demo')|params))['items']
    assert len(listing(parent))==2 and len(listing(parent,fromDate='2027-04-05'))==2
    later=listing(parent,fromDate='2027-04-06');assert len(later)==1 and later[0]['eventId']==second
    assert listing(parent,fromDate='2027-04-07')==[] and len(listing(signed('teacher'),fromDate='2027-04-06'))==1
    create(client,'parent',login='other',grants=[dict(workspace='demo',view='student',entityIds=['s2'])])
    assert listing(signed('other'),fromDate='2027-04-05')==[]


def test_existing_slots_recheck_new_room_booking_before_parent_reservation(client,monkeypatch):
    id,t,p=setup(client,monkeypatch)
    e=event(client,id);definition=e['definition']|dict(hosts=[hosts(t,roomId='r0')])
    ok(client.put(path('events/'+id),json=request(version=e['version'],definition=definition)))
    publish(client,id);parent=signed('parent');slot=slots(parent,id)[0]
    state=api.read('demo');state.commitments=[Commitment(id='room',name='Synthetic booking',onDate=BASE.date(),startTime='17:00',endTime='17:10',roomIds=['r0'])];api.save('demo',state)
    assert book(parent,id,slot).status_code==409
    assert slots(parent,id)[0]['status']=='conflict'


def test_reminder_revocation_and_past_meetings_do_not_notify_parent(client,monkeypatch):
    id,t,p=setup(client,monkeypatch);publish(client,id);parent=signed('parent');ok(book(parent,id,slots(parent,id)[0]))
    from test_families import save
    save(client,p['id'],status='revoked',reason='Ended')
    monkeypatch.setattr(meeting_reminders,'now',lambda:BASE-timedelta(minutes=30))
    assert api.meetings.reminders.process_one()
    with api.connect() as db:
        assert db.execute("SELECT count(*) FROM notifications WHERE action='meeting.reminder' AND user_id=?",(p['id'],)).fetchone()[0]==0
        assert db.execute("SELECT count(*) FROM notifications WHERE action='meeting.reminder' AND user_id=?",(t['id'],)).fetchone()[0]==1


def test_parent_cancels_then_can_rebook_and_event_cancel_preserves_history(client,monkeypatch):
    id,t,p=setup(client,monkeypatch);publish(client,id);parent=signed('parent')
    first=ok(book(parent,id,slots(parent,id)[0]))
    ok(parent.post(path('bookings/'+first['id']),json=request(version=1,action='cancel')))
    ok(book(parent,id,slots(parent,id)[1]));assert len(bookings(parent,id))==2
    ok(transition(client,id,'cancel',reason='Перенос школьного мероприятия'))
    assert all(b['status']=='cancelled' for b in bookings(parent,id))
    assert all(s['status']=='blocked' for s in slots(parent,id))
    assert len(bookings(signed('teacher'),id))==2


def test_second_child_keeps_booking_reminder_and_cancel(client,monkeypatch):
    id,t,p=setup(client,monkeypatch);publish(client,id);parent=signed('parent');bid=ok(book(parent,id,slots(parent,id)[0]))['id']
    regrant(p['id'],['s1','s2'],'student')
    assert len(bookings(parent,id))==1 and len(ok(parent.get(path('bookings/'+bid+'/history'))))==1
    assert bookings(client,id)[0]['parentAccessCurrent'] is True
    monkeypatch.setattr(meeting_reminders,'now',lambda:BASE-timedelta(minutes=30))
    assert api.meetings.reminders.process_one()
    with api.connect() as db:
        assert db.execute("SELECT count(*) FROM notifications WHERE action='meeting.reminder' AND user_id=?",(p['id'],)).fetchone()[0]==1
    ok(parent.post(path('bookings/'+bid),json=request(version=1,action='cancel')))
    assert bookings(parent,id)[0]['status']=='cancelled'


def test_lost_child_link_hides_booking(client,monkeypatch):
    id,t,p=setup(client,monkeypatch);publish(client,id);parent=signed('parent');bid=ok(book(parent,id,slots(parent,id)[0]))['id']
    regrant(p['id'],['s2'],'student')
    assert bookings(parent,id)==[] and parent.get(path('bookings/'+bid+'/history')).status_code==404
    assert bookings(client,id)[0]['parentAccessCurrent'] is False
    monkeypatch.setattr(meeting_reminders,'now',lambda:BASE-timedelta(minutes=30))
    assert api.meetings.reminders.process_one()
    with api.connect() as db:
        assert db.execute("SELECT count(*) FROM notifications WHERE action='meeting.reminder' AND user_id=?",(p['id'],)).fetchone()[0]==0


def test_host_keeps_assignment_after_unrelated_grant_change(client,monkeypatch):
    id,t,p=setup(client,monkeypatch);publish(client,id);parent=signed('parent');ok(book(parent,id,slots(parent,id)[0]))
    regrant(t['id'],['t2','t3'],'teacher')
    assert event(client,id)['hosts'][0]['current'] is True
    assert 'blocked' not in {s['status'] for s in slots(client,id)} and len(bookings(signed('teacher'),id))==1
    regrant(t['id'],['t3'],'teacher')
    assert event(client,id)['hosts'][0]['current'] is False
    assert {s['status'] for s in slots(client,id)} <= {'blocked','occupied'}
    assert bookings(signed('teacher'),id)==[]
