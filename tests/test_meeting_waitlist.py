"""Waiting requests, expiring holds, concurrent confirmations and access changes."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import pytest
from backend import api,meetings
from test_access import client,create,signed
from test_meetings import BASE,setup,publish,event,path,request,ok,slots,book,bookings,apply,transition,availability,regrant


def queue(c,eid,**params):return ok(c.get('/api/meetings/events/'+eid+'/waitlist',params=dict(workspace='demo',**params)))
def join(c,eid,host,student='s1',**kw):return c.post(path('events/'+eid+'/waitlist'),json=request(version=event(c,eid)['version'],hostId=host,studentId=student,**kw))
def offered(client,parent,eid,wid,slot=None,**kw):
    row=next(r for r in queue(client,eid)['items'] if r['id']==wid);slot=slot or next(s for s in slots(client,eid) if s['hostId']==row['hostId'] and s['status']=='available')
    body=request(version=row['version'],eventVersion=event(client,eid)['version'],slotId=slot['id'],slotVersion=slot['version'],expiresAt=(BASE-timedelta(days=1)).isoformat())|kw
    result=ok(client.post(path('waitlist/'+wid+'/offer'),json=body))
    return result,body
def change(c,eid,wid,action,**kw):
    q=queue(c,eid);r=next(r for r in q['items'] if r['id']==wid)
    body=request(version=r['version'],action=action,eventVersion=q['eventVersion'],slotVersion=r['offer']['slotVersion'] if r['offer'] else None)|kw
    return c.post(path('waitlist/'+wid),json=body),body
def prepared(client,monkeypatch,**kw):
    eid,t,p=setup(client,monkeypatch,**kw);publish(client,eid);parent=signed('parent')
    wid=ok(join(parent,eid,t['id'],note='После 17:00'))['id']
    return eid,t,p,parent,wid
def records():
    with api.connect() as db:
        names=[r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND (name LIKE 'meeting_%' OR name IN ('notifications','web_push_jobs'))")]
        return {n:sorted(db.execute('SELECT * FROM '+n).fetchall(),key=repr) for n in names}


def test_private_queue_offer_hold_accept_reminder_history_and_replays(client,monkeypatch):
    eid,t,p,parent,wid=prepared(client,monkeypatch);state=api.read('demo').model_dump(mode='json')
    assert bookings(parent,eid)==[]
    create(client,'parent',login='other',grants=[dict(workspace='demo',view='student',entityIds=['s2'])]);other=signed('other')
    assert queue(other,eid)['items']==[]
    assert signed('teacher').get(path('events/'+eid+'/waitlist')).status_code==403
    assert other.get(path('waitlist/'+wid+'/history')).status_code==404
    offered_result,body=offered(client,parent,eid,wid);snapshot=records()
    assert ok(client.post(path('waitlist/'+wid+'/offer'),json=body))==offered_result and records()==snapshot
    row=queue(parent,eid)['items'][0];ss=slots(other,eid);held=next(s for s in ss if s['status']=='held')
    assert 'booking' not in held and 'parentName' not in held and 'offer' not in held
    assert book(other,eid,held,'s2').status_code==409
    response,body=change(parent,eid,wid,'accept');result=ok(response);snapshot=records()
    assert ok(parent.post(path('waitlist/'+wid),json=body))==result and records()==snapshot
    booking=bookings(parent,eid)[0];assert booking['id']==result['bookingId'] and booking['note']=='После 17:00'
    assert len(bookings(signed('teacher'),eid))==1 and queue(parent,eid)['items'][0]['status']=='accepted'
    assert [h['action'] for h in ok(parent.get(path('waitlist/'+wid+'/history')))]==['accepted','offered','joined']
    with api.connect() as db:
        assert db.execute('SELECT count(*) FROM meeting_reminders WHERE booking_id=?',(result['bookingId'],)).fetchone()[0]==1
        assert db.execute("SELECT count(*) FROM notifications WHERE action='meeting.waitlist_offer' AND user_id=?",(p['id'],)).fetchone()[0]==1
    assert api.read('demo').model_dump(mode='json')==state


def test_expired_offer_releases_hold_without_worker_and_can_be_reoffered(client,monkeypatch):
    eid,t,p,parent,wid=prepared(client,monkeypatch);offered(client,parent,eid,wid)
    monkeypatch.setattr(meetings,'now',lambda:BASE-timedelta(hours=12))
    row=queue(parent,eid)['items'][0];assert row['status']=='expired' and not row['canAccept']
    assert all(s['status']=='available' for s in slots(parent,eid))
    assert change(parent,eid,wid,'accept')[0].status_code==409
    offered(client,parent,eid,wid,expiresAt=(BASE-timedelta(hours=1)).isoformat())
    ok(change(parent,eid,wid,'accept')[0])


@pytest.mark.parametrize('action',['decline','withdraw','revoke','cancel'])
def test_releasing_offer_preserves_history_and_releases_slot(client,monkeypatch,action):
    eid,t,p,parent,wid=prepared(client,monkeypatch);offered(client,parent,eid,wid)
    c=client if action in ['revoke','cancel'] else parent
    response,body=change(c,eid,wid,action,reason='Изменились планы');result=ok(response);snapshot=records()
    assert ok(c.post(path('waitlist/'+wid),json=body))==result and records()==snapshot
    row=queue(parent,eid)['items'][0]
    assert row['status']==('waiting' if action in ['decline','revoke'] else 'withdrawn' if action=='withdraw' else 'cancelled')
    assert row['offer'] is None and bookings(parent,eid)==[] and slots(parent,eid)[0]['status']=='available'


def test_duplicate_guardian_queue_and_booking_prevented_and_direct_booking_resolves_request(client,monkeypatch):
    eid,t,p,parent,wid=prepared(client,monkeypatch);create(client,'parent',login='guardian');guardian=signed('guardian')
    assert join(guardian,eid,t['id']).status_code==409
    offered(client,parent,eid,wid);target=next(s for s in slots(guardian,eid) if s['status']=='available')
    ok(book(guardian,eid,target));assert queue(parent,eid)['items'][0]['status']=='fulfilled'
    assert not any(s['status']=='held' for s in slots(parent,eid))
    history=ok(parent.get(path('waitlist/'+wid+'/history')))[0];assert history['bookingId'] is None
    assert join(parent,eid,t['id']).status_code==409


def test_held_time_cannot_be_used_for_admin_reschedule(client,monkeypatch):
    eid,t,p,parent,wid=prepared(client,monkeypatch);offered(client,parent,eid,wid)
    create(client,'parent',login='other',grants=[dict(workspace='demo',view='student',entityIds=['s2'])]);other=signed('other')
    target=next(s for s in slots(other,eid) if s['status']=='available');bid=ok(book(other,eid,target,'s2'))['id']
    held=queue(parent,eid)['items'][0]['offer']
    choices=ok(client.get(path('bookings/'+bid+'/reschedule-options')))
    assert not next(s for s in choices['items'] if s['id']==held['slotId'])['available']
    response=client.post(path('bookings/'+bid+'/reschedule'),json=request(version=1,eventVersion=choices['eventVersion'],sourceSlotVersion=choices['sourceSlotVersion'],targetSlotId=held['slotId'],targetSlotVersion=held['slotVersion'],reason='Move'))
    assert response.status_code==409


def test_block_preview_includes_offers_and_becomes_stale_when_offer_changes(client,monkeypatch):
    eid,t,p,parent,wid=prepared(client,monkeypatch)
    body,preview=availability(client,eid,action='block',end=BASE+timedelta(minutes=10),reason='Absent')
    assert preview['offersToRevoke']==0
    offered(client,parent,eid,wid)
    assert client.post(path('events/'+eid+'/availability'),json=body).status_code==409
    body,preview=availability(client,eid,action='block',end=BASE+timedelta(minutes=10),reason='Absent');assert preview['offersToRevoke']==1
    ok(client.post(path('events/'+eid+'/availability'),json=body))
    row=queue(parent,eid)['items'][0];assert row['status']=='waiting' and row['offer'] is None and row['reason']=='Absent'


@pytest.mark.parametrize('action',['cancel','archive'])
def test_event_cancellation_or_archive_finishes_queue(client,monkeypatch,action):
    eid,t,p,parent,wid=prepared(client,monkeypatch);offered(client,parent,eid,wid)
    ok(transition(client,eid,action,reason='No meetings'))
    row=queue(parent,eid)['items'][0];assert row['status']=='cancelled' and row['offer'] is None
    assert change(parent,eid,wid,'accept')[0].status_code==409


def test_closed_registration_stops_new_requests_but_existing_offer_can_be_accepted(client,monkeypatch):
    eid,t,p,parent,wid=prepared(client,monkeypatch);ok(transition(client,eid,'close'))
    assert join(parent,eid,t['id']).status_code==409
    offered(client,parent,eid,wid);ok(change(parent,eid,wid,'accept')[0])


@pytest.mark.parametrize('field',['version','eventVersion','slotVersion'])
def test_stale_offer_versions_leave_queue_and_slot_intact(client,monkeypatch,field):
    eid,t,p,parent,wid=prepared(client,monkeypatch);s=slots(client,eid)[0];snapshot=records()
    body=request(version=1,eventVersion=event(client,eid)['version'],slotId=s['id'],slotVersion=s['version'],expiresAt=(BASE-timedelta(days=1)).isoformat());body[field]=999
    assert client.post(path('waitlist/'+wid+'/offer'),json=body).status_code==409 and records()==snapshot


@pytest.mark.parametrize('field',['version','eventVersion','slotVersion'])
def test_stale_accept_versions_leave_hold_and_no_booking(client,monkeypatch,field):
    eid,t,p,parent,wid=prepared(client,monkeypatch);offered(client,parent,eid,wid);snapshot=records()
    assert change(parent,eid,wid,'accept',**{field:999})[0].status_code==409 and records()==snapshot


@pytest.mark.parametrize('which',['teacher','student','family'])
def test_current_rights_rechecked_and_invalid_offer_no_longer_holds_slot(client,monkeypatch,which):
    eid,t,p,parent,wid=prepared(client,monkeypatch);offered(client,parent,eid,wid)
    row=queue(parent,eid)['items'][0];body=request(version=row['version'],action='accept',eventVersion=event(client,eid)['version'],slotVersion=row['offer']['slotVersion'])
    if which=='teacher':
        with api.connect() as db:db.execute('UPDATE accounts SET active=0,version=version+1 WHERE id=?',(t['id'],))
    elif which=='student':
        s=api.read('demo');next(x for x in s.students if x.id=='s1').active=False;api.save('demo',s)
    else:
        from test_families import save
        save(client,p['id'],status='revoked',reason='No access')
    assert parent.post(path('waitlist/'+wid),json=body).status_code in [403,404,409]
    assert not any(s['status']=='held' for s in slots(client,eid))
    assert not queue(client,eid)['items'][0]['current']


def test_calendar_and_parent_overlap_rechecked_on_confirmation(client,monkeypatch):
    eid,t,p,parent,wid=prepared(client,monkeypatch,host_count=2);offered(client,parent,eid,wid)
    other=next(s for s in slots(parent,eid) if s['hostId']!=t['id'])
    ok(book(parent,eid,other));assert change(parent,eid,wid,'accept')[0].status_code==409
    booking=bookings(parent,eid)[0];ok(parent.post(path('bookings/'+booking['id']),json=request(version=1,action='cancel')))
    from backend.models import TeacherAbsence
    s=api.read('demo');s.absences=[TeacherAbsence(id='absent',name='Absent',teacherId='t2',start=BASE.date(),end=BASE.date(),startTime='17:00',endTime='17:10')];api.save('demo',s)
    assert change(parent,eid,wid,'accept')[0].status_code==409


def test_offer_vs_direct_booking_race_has_one_winner(client,monkeypatch):
    eid,t,p,parent,wid=prepared(client,monkeypatch);create(client,'parent',login='other',grants=[dict(workspace='demo',view='student',entityIds=['s2'])]);other=signed('other')
    s=slots(client,eid)[0];ev=event(client,eid)['version'];offer=request(version=1,eventVersion=ev,slotId=s['id'],slotVersion=s['version'],expiresAt=(BASE-timedelta(days=1)).isoformat());booking=request(version=s['version'],eventVersion=ev,studentId='s2')
    with ThreadPoolExecutor(2) as pool:
        results=list(pool.map(lambda f:f(),[lambda:client.post(path('waitlist/'+wid+'/offer'),json=offer),lambda:other.post(path('slots/'+s['id']+'/book'),json=booking)]))
    assert sorted(r.status_code for r in results)==[200,409]


@pytest.mark.parametrize('stage',['offer','accept'])
def test_notification_failure_rolls_back_entire_operation(client,monkeypatch,stage):
    eid,t,p,parent,wid=prepared(client,monkeypatch)
    if stage=='accept':offered(client,parent,eid,wid)
    snapshot=records();original=api.notifications.deliver
    def fail(*args,**kwargs):original(*args,**kwargs);raise RuntimeError('synthetic delivery failure')
    monkeypatch.setattr(api.notifications,'deliver',fail)
    with pytest.raises(RuntimeError):
        if stage=='offer':offered(client,parent,eid,wid)
        else:change(parent,eid,wid,'accept')
    assert records()==snapshot


def test_role_workspace_deadline_and_pagination_contracts(client,monkeypatch):
    eid,t,p,parent,wid=prepared(client,monkeypatch);s=slots(client,eid)[0]
    body=request(version=1,eventVersion=event(client,eid)['version'],slotId=s['id'],slotVersion=s['version'],expiresAt=(BASE-timedelta(days=1)).isoformat())
    assert parent.post(path('waitlist/'+wid+'/offer'),json=body).status_code==403
    assert client.post(path('waitlist/'+wid+'/offer'),json=body|dict(expiresAt=(BASE+timedelta(minutes=1)).isoformat())).status_code==422
    assert client.get('/api/meetings/events/'+eid+'/waitlist?workspace=school').status_code==404
    ok(change(parent,eid,wid,'withdraw')[0]);second=ok(join(parent,eid,t['id']))['id']
    first=queue(client,eid,limit=1);next_page=queue(client,eid,limit=1,after=first['nextAfter'])
    assert first['items'][0]['id']==wid and next_page['items'][0]['id']==second and next_page['nextAfter'] is None


def test_old_expired_offer_withdrawal_does_not_invalidate_new_family_hold(client,monkeypatch):
    eid,t,p,parent,wid=prepared(client,monkeypatch);s=slots(client,eid)[0];offered(client,parent,eid,wid,s)
    create(client,'parent',login='other',grants=[dict(workspace='demo',view='student',entityIds=['s2'])]);other=signed('other')
    other_id=ok(join(other,eid,t['id'],'s2'))['id']
    monkeypatch.setattr(meetings,'now',lambda:BASE-timedelta(hours=12))
    s=slots(client,eid)[0];offered(client,other,eid,other_id,s,expiresAt=(BASE-timedelta(hours=1)).isoformat())
    row=queue(other,eid)['items'][0];ev=event(client,eid)['version']
    ok(change(parent,eid,wid,'withdraw')[0])
    response=other.post(path('waitlist/'+other_id),json=request(version=row['version'],action='accept',eventVersion=ev,slotVersion=row['offer']['slotVersion']))
    ok(response)


def test_second_child_keeps_queue_entry_offer_and_accept(client,monkeypatch):
    eid,t,p,parent,wid=prepared(client,monkeypatch)
    regrant(p['id'],['s1','s2'],'student')
    assert queue(parent,eid)['items'][0]['current'] is True
    offered(client,parent,eid,wid)
    response,_=change(parent,eid,wid,'accept');assert response.status_code==200,response.text
    assert len(bookings(parent,eid))==1
