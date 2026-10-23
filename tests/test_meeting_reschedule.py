"""Atomic transfers, current access, concurrent bookings and reminder lifecycle."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import json
import pytest
from backend import api,meetings,meeting_routes,meeting_reminders
from backend.models import TeacherAbsence
from test_access import client,create,signed
from test_meetings import BASE,setup,publish,slots,book,bookings,event,path,request,ok,apply,transition,new,hosts


def options(c,id,**params):return c.get('/api/meetings/bookings/'+id+'/reschedule-options',params=dict(workspace='demo',**params))
def transfer(c,id,body):return c.post(path('bookings/'+id+'/reschedule'),json=body)
def prepared(client,monkeypatch,**kw):
    eid,t,p=setup(client,monkeypatch,**kw);publish(client,eid);parent=signed('parent')
    source=next(s for s in slots(parent,eid) if s['hostId']==t['id'])
    bid=ok(book(parent,eid,source,note='Private appointment'))['id']
    choices=ok(options(client,bid));target=next(s for s in choices['items'] if s['available'])
    body=request(version=choices['booking']['version'],eventVersion=choices['eventVersion'],sourceSlotVersion=choices['sourceSlotVersion'],targetSlotId=target['id'],targetSlotVersion=target['version'],reason='Teacher arrives later')
    return eid,t,p,parent,source,bid,target,body


def records():
    with api.connect() as db:
        return {table:sorted(db.execute('SELECT * FROM '+table).fetchall(),key=repr) for table in ['meeting_events','meeting_slots','meeting_bookings','meeting_history','meeting_reminders','meeting_requests','notifications','web_push_jobs'] if db.execute("SELECT 1 FROM sqlite_master WHERE name=?",(table,)).fetchone()}


@pytest.mark.parametrize('block',[False,True])
def test_transfer_retains_identity_history_notifies_and_replays(client,monkeypatch,block):
    eid,t,p,parent,source,bid,target,body=prepared(client,monkeypatch)
    before=api.read('demo').model_dump(mode='json');body['blockSource']=block
    moved=ok(transfer(client,bid,body));after=records()
    assert moved['eventVersion']==body['eventVersion']
    with api.connect() as db:assert db.execute('SELECT version FROM meeting_events WHERE id=?',(eid,)).fetchone()[0]==body['eventVersion']
    assert ok(transfer(client,bid,body))==moved and records()==after
    assert transfer(client,bid,body|dict(reason='Different')).status_code==409
    item=bookings(parent,eid)[0]
    assert item['id']==bid and item['version']==2 and item['status']=='scheduled'
    assert item['startsAt']==target['startsAt'] and item['note']=='Private appointment'
    assert item['rescheduleReason']==body['reason'] and not item['canReschedule']
    assert bookings(signed('teacher'),eid)[0]['startsAt']==target['startsAt']
    ss={s['id']:s for s in slots(client,eid)}
    assert ss[source['id']]['status']==('blocked' if block else 'available')
    assert ss[source['id']]['version']==source['version']+2
    assert ss[target['id']]['status']=='occupied' and ss[target['id']]['version']==target['version']+1
    history=ok(parent.get(path('bookings/'+bid+'/history')))[0]
    assert history['action']=='rescheduled' and history['source']['startsAt']==source['startsAt']
    assert history['target']['startsAt']==target['startsAt'] and history['sourceBlocked']==block
    with api.connect() as db:
        due,status=db.execute('SELECT due,status FROM meeting_reminders WHERE booking_id=?',(bid,)).fetchone()
        from datetime import datetime
        assert due==(datetime.fromisoformat(target['startsAt'])-timedelta(hours=1)).isoformat() and status=='pending'
        assert {r[0] for r in db.execute("SELECT user_id FROM notifications WHERE action='meeting.rescheduled'")}=={p['id'],t['id']}
    exported=client.get(path('events/'+eid+'/export'))
    assert body['reason'] in exported.text and 'Причина последнего переноса' in exported.text
    assert api.read('demo').model_dump(mode='json')==before


def test_transfer_does_not_invalidate_other_families_booking(client,monkeypatch):
    eid,t,p,parent,source,bid,target,body=prepared(client,monkeypatch);ok(transfer(client,bid,body))
    create(client,'parent',login='other',grants=[dict(workspace='demo',view='student',entityIds=['s2'])]);other=signed('other')
    free=next(s for s in slots(other,eid) if s['status']=='available')
    ok(other.post(path('slots/'+free['id']+'/book'),json=request(version=free['version'],eventVersion=body['eventVersion'],studentId='s2')))


@pytest.mark.parametrize('field',['version','eventVersion','sourceSlotVersion','targetSlotVersion'])
def test_stale_versions_leave_original_booking_intact(client,monkeypatch,field):
    *_,bid,target,body=prepared(client,monkeypatch);before=records()
    assert transfer(client,bid,body|{field:999}).status_code==409
    assert records()==before


def test_candidates_and_transfer_are_admin_only_and_workspace_scoped(client,monkeypatch):
    eid,t,p,parent,source,bid,target,body=prepared(client,monkeypatch)
    for role in ['parent','teacher']:
        reader=signed(role)
        assert options(reader,bid).status_code==403 and transfer(reader,bid,body).status_code==403
    assert client.get('/api/meetings/bookings/'+bid+'/reschedule-options?workspace=school').status_code==404
    assert client.post('/api/meetings/bookings/'+bid+'/reschedule?workspace=school',json=body).status_code==404
    assert transfer(client,bid,body|dict(reason='   ')).status_code==422
    assert transfer(client,bid,body|dict(targetSlotId=source['id'])).status_code==422


def test_only_same_host_event_can_be_used_and_closed_registration_is_allowed(client,monkeypatch):
    eid,t,p,parent,source,bid,target,body=prepared(client,monkeypatch,host_count=2)
    other=next(s for s in slots(client,eid) if s['hostId']!=t['id'])
    assert transfer(client,bid,body|dict(targetSlotId=other['id'],targetSlotVersion=other['version'])).status_code==422
    other_event=new(client,[hosts(t)],startsAt=(BASE+timedelta(days=1)).isoformat(),endsAt=(BASE+timedelta(days=1,hours=2)).isoformat())
    apply(client,other_event,start=BASE+timedelta(days=1),end=BASE+timedelta(days=1,hours=2))
    other_slot=slots(client,other_event)[0]
    assert transfer(client,bid,body|dict(targetSlotId=other_slot['id'],targetSlotVersion=other_slot['version'])).status_code==422
    ok(transition(client,eid,'close'));body['eventVersion']=event(client,eid)['version']
    assert not event(parent,eid)['registrationOpen']
    ok(transfer(client,bid,body))


def test_candidates_and_commit_recheck_calendar_family_and_current_assignment(client,monkeypatch):
    eid,t,p,parent,source,bid,target,body=prepared(client,monkeypatch)
    original=api.read('demo').model_copy(deep=True);state=original.model_copy(deep=True)
    state.absences=[TeacherAbsence(id='late',name='Unavailable',teacherId='t2',start=BASE.date(),end=BASE.date(),startTime='17:10',endTime='17:20')]
    api.save('demo',state)
    choices=ok(options(client,bid));assert not next(s for s in choices['items'] if s['id']==target['id'])['available']
    before=records();assert transfer(client,bid,body).status_code==409 and records()==before
    state=api.read('demo');state.absences=[];api.save('demo',state)
    from test_families import save
    save(client,p['id'],status='revoked',reason='Access ended')
    assert options(client,bid).status_code==409 and transfer(client,bid,body).status_code==409


@pytest.mark.parametrize('conflict_type',['parent','child'])
def test_parent_or_child_overlap_prevents_transfer_without_losing_original(client,monkeypatch,conflict_type):
    eid,t,p,parent,source,bid,target,body=prepared(client,monkeypatch,host_count=2)
    # Both children have English, while s1 has the second teacher's mixed math group.
    if conflict_type=='child':
        create(client,'parent',login='guardian');other=signed('guardian')
    else:other=parent
    other_slot=next(s for s in slots(other,eid) if s['hostId']!=t['id'] and s['startsAt']==target['startsAt'])
    ok(book(other,eid,other_slot))
    before=records();assert transfer(client,bid,body).status_code==409 and records()==before
    assert not next(s for s in ok(options(client,bid))['items'] if s['id']==target['id'])['available']


def test_race_between_transfer_and_new_booking_has_one_winner(client,monkeypatch):
    eid,t,p,parent,source,bid,target,body=prepared(client,monkeypatch)
    create(client,'parent',login='other',grants=[dict(workspace='demo',view='student',entityIds=['s2'])]);other=signed('other')
    new_body=request(version=target['version'],eventVersion=body['eventVersion'],studentId='s2')
    def move():return transfer(client,bid,body)
    def take():return other.post(path('slots/'+target['id']+'/book'),json=new_body)
    with ThreadPoolExecutor(2) as pool:results=list(pool.map(lambda f:f(),[move,take]))
    assert sorted(r.status_code for r in results)==[200,409]
    original=next(b for b in bookings(client,eid) if b['id']==bid)
    assert original['status']=='scheduled'
    assert original['startsAt']==(target['startsAt'] if results[0].status_code==200 else source['startsAt'])


def test_notification_failure_rolls_back_booking_slots_event_history_and_reminder(client,monkeypatch):
    *_,bid,target,body=prepared(client,monkeypatch);before=records()
    original=api.notifications.deliver
    def fail(*args):
        original(*args)
        raise RuntimeError('synthetic notification failure after insertion')
    monkeypatch.setattr(api.notifications,'deliver',fail)
    with pytest.raises(RuntimeError):transfer(client,bid,body|dict(blockSource=True))
    assert records()==before


def test_sent_reminder_is_rearmed_for_new_time_and_new_version(client,monkeypatch):
    eid,t,p,parent,source,bid,target,body=prepared(client,monkeypatch)
    for module in [meetings,meeting_routes,meeting_reminders]:monkeypatch.setattr(module,'now',lambda:BASE-timedelta(minutes=30))
    assert api.meetings.reminders.process_one()
    ok(transfer(client,bid,body));assert api.meetings.reminders.process_one()
    assert not api.meetings.reminders.process_one()
    with api.connect() as db:
        assert sorted(r[0] for r in db.execute("SELECT revision FROM notifications WHERE action='meeting.reminder' AND user_id=?",(p['id'],)))==[1,2]


def test_blocked_target_and_started_or_cancelled_source_are_rejected(client,monkeypatch):
    eid,t,p,parent,source,bid,target,body=prepared(client,monkeypatch)
    from datetime import datetime
    apply(client,eid,action='block',start=datetime.fromisoformat(target['startsAt']),end=datetime.fromisoformat(target['endsAt']),reason='Absent')
    choice=ok(options(client,bid));assert target['id'] not in {s['id'] for s in choice['items']}
    body['eventVersion']=choice['eventVersion'];body['targetSlotVersion']+=1
    assert transfer(client,bid,body).status_code==409
    monkeypatch.setattr(meetings,'now',lambda:BASE)
    assert options(client,bid).status_code==409 and transfer(client,bid,body).status_code==409


def test_candidate_pagination_uses_school_day_and_exposes_no_other_family_details(client,monkeypatch):
    eid,t,p,parent,source,bid,target,body=prepared(client,monkeypatch)
    first=ok(options(client,bid,limit=2,onDate='2027-04-05'))
    second=ok(options(client,bid,limit=2,after=first['nextAfter'],onDate='2027-04-05'))
    assert len(first['items'])==len(second['items'])==2
    assert not {x['id'] for x in first['items']} & {x['id'] for x in second['items']}
    assert all(set(x)=={'id','version','startsAt','endsAt','available','reason'} for x in first['items'])
    assert ok(options(client,bid,onDate='2027-04-06'))['items']==[]
    assert options(client,bid,after='bad').status_code==422


@pytest.mark.parametrize('change',['teacher','pupil','cancelled'])
def test_access_or_cancellation_change_invalidates_a_previously_loaded_candidate(client,monkeypatch,change):
    eid,t,p,parent,source,bid,target,body=prepared(client,monkeypatch)
    if change=='teacher':
        with api.connect() as db:db.execute('UPDATE accounts SET active=0,version=version+1 WHERE id=?',(t['id'],))
    elif change=='pupil':
        state=api.read('demo');state.students[0].active=False;api.save('demo',state)
    else:ok(client.post(path('bookings/'+bid),json=request(version=1,action='cancel',reason='Cancelled before transfer')))
    before=records()
    assert options(client,bid).status_code==409 and transfer(client,bid,body).status_code==409
    assert records()==before
