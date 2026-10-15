"""Atomic administrator transfers of existing parent/teacher appointments."""
from datetime import date,datetime,timedelta
import json
from fastapi import HTTPException,Query,Request
from . import meetings
from .meeting_models import Reschedule,utc
from .meeting_availability import Occupancy,ZONE
from .meeting_routes import cursor
from .messages import Directory
from .notifications import encoded


def participants(service,db,booking,event,snap,workspace):
    owner=service.booking_owner(db,booking,snap,workspace)
    host=service.hosts(db,event,snap,workspace).get(booking['host_id'])
    if not owner or not host or not host['current'] or booking['student_id'] not in service.eligible_pupils(host,Directory(snap.state,workspace)):
        raise HTTPException(409,'Доступ родителя, ученика или ведущего изменился. Проверьте назначение встречи.')
    return owner,host


def movable(booking,event):
    if booking['status']!='scheduled' or event['status'] not in ['open','closed'] or datetime.fromisoformat(booking['starts_at'])<=meetings.now():
        raise HTTPException(409,'Перенос доступен только для предстоящей запланированной встречи действующего мероприятия.')


def conflict(service,db,booking,target,snap,occupancy):
    if target['status']!='open' or datetime.fromisoformat(target['starts_at'])<=meetings.now():
        return 'Этот интервал закрыт или уже начался.'
    if problem:=service.slot_conflict(db,target,occupancy,target['id']):return problem
    if db.execute("SELECT 1 FROM meeting_bookings WHERE slot_id=? AND status!='cancelled' LIMIT 1",(target['id'],)).fetchone():
        return 'Этот интервал уже занят.'
    for column in ['parent_id','student_id']:
        if db.execute(f"SELECT 1 FROM meeting_bookings WHERE workspace=? AND {column}=? AND id!=? AND status!='cancelled' AND starts_at<? AND ends_at>? LIMIT 1",(booking['workspace'],booking[column],booking['id'],target['ends_at'],target['starts_at'])).fetchone():
            return 'У родителя или ребёнка уже есть встреча в это время.'
    return None


def register(service,app,Workspace):
    @app.get('/api/meetings/bookings/{id}/reschedule-options')
    def options(id:str,request:Request,workspace:Workspace='school',onDate:date|None=None,
                after:str=Query(default='',max_length=150),limit:int=Query(default=60,ge=1,le=100)):
        with service.context(request,workspace) as (db,user,snap):
            service.admin(user)
            booking=service.booking(db,workspace,id);event=service.event(db,workspace,booking['event_id'])
            participants(service,db,booking,event,snap,workspace);movable(booking,event)
            source=db.execute('SELECT * FROM meeting_slots WHERE id=?',(booking['slot_id'],)).fetchone()
            if not source or source['status']!='open':raise HTTPException(409,'Исходный интервал изменился. Обновите запись.')
            clause='';params=[event['id'],booking['host_id'],source['id'],utc(meetings.now())]
            if onDate:
                start=datetime.combine(onDate,datetime.min.time(),ZONE)
                clause+=' AND starts_at>=? AND starts_at<?';params.extend([utc(start),utc(start+timedelta(days=1))])
            if after:
                clause+=' AND (starts_at,id)>(?,?)';params.extend(cursor(after))
            rows=db.execute("SELECT * FROM meeting_slots WHERE event_id=? AND host_id=? AND id!=? AND starts_at>? AND status='open'"+clause+' ORDER BY starts_at,id LIMIT ?',(*params,limit+1)).fetchall()
            occupancy=Occupancy(snap);items=[]
            for target in rows[:limit]:
                problem=conflict(service,db,booking,target,snap,occupancy)
                items.append(dict(id=target['id'],version=target['version'],startsAt=target['starts_at'],endsAt=target['ends_at'],available=not problem,reason=problem or ''))
            return dict(booking=service.booking_public(booking,event,user),eventVersion=event['version'],sourceSlotVersion=source['version'],
                        items=items,nextAfter=rows[limit-1]['starts_at']+'|'+rows[limit-1]['id'] if len(rows)>limit else None)

    @app.post('/api/meetings/bookings/{id}/reschedule')
    def reschedule(id:str,body:Reschedule,request:Request,workspace:Workspace='school'):
        with service.context(request,workspace,True) as (db,user,snap):
            service.admin(user)
            booking=service.booking(db,workspace,id);event=service.event(db,workspace,booking['event_id'])
            owner,host=participants(service,db,booking,event,snap,workspace)
            fingerprint,old=service.replay(db,user,body,'reschedule:'+id)
            if old:return old
            movable(booking,event);meetings.version(booking,body.version);meetings.version(event,body.eventVersion)
            source=db.execute('SELECT * FROM meeting_slots WHERE id=? AND workspace=?',(booking['slot_id'],workspace)).fetchone()
            target=db.execute('SELECT * FROM meeting_slots WHERE id=? AND workspace=?',(body.targetSlotId,workspace)).fetchone()
            if not source or source['status']!='open':raise HTTPException(409,'Исходный интервал изменился. Обновите запись.')
            if not target:raise HTTPException(404,'Новый интервал не найден.')
            if target['id']==source['id'] or target['event_id']!=event['id'] or target['host_id']!=booking['host_id']:
                raise HTTPException(422,'Выберите другой интервал у того же ведущего в этом мероприятии.')
            meetings.version(source,body.sourceSlotVersion);meetings.version(target,body.targetSlotVersion)
            if problem:=conflict(service,db,booking,target,snap,Occupancy(snap)):raise HTTPException(409,problem)
            # No cancellation/recreation gap: every change commits or rolls back together.
            version=booking['version']+1;document=json.loads(booking['document'])
            document.update(rescheduleReason=body.reason,updated=utc(meetings.now()))
            db.execute('UPDATE meeting_bookings SET slot_id=?,starts_at=?,ends_at=?,version=?,document=? WHERE id=?',
                       (target['id'],target['starts_at'],target['ends_at'],version,encoded(document),id))
            db.execute('UPDATE meeting_slots SET version=version+1,status=?,reason=? WHERE id=?',
                       ('blocked' if body.blockSource else 'open',body.reason if body.blockSource else '',source['id']))
            db.execute('UPDATE meeting_slots SET version=version+1 WHERE id=?',(target['id'],))
            minutes=event['definition']['reminderMinutes']
            if minutes:
                due=utc(datetime.fromisoformat(target['starts_at'])-timedelta(minutes=minutes))
                db.execute("INSERT INTO meeting_reminders VALUES (?,?,'pending') ON CONFLICT(booking_id) DO UPDATE SET due=excluded.due,status='pending'",(id,due))
            else:
                db.execute("UPDATE meeting_reminders SET status='cancelled' WHERE booking_id=?",(id,))
            def interval(slot):return dict(slotId=slot['id'],startsAt=slot['starts_at'],endsAt=slot['ends_at'])
            service.history(db,event,user,'rescheduled',dict(version=version,reason=body.reason,source=interval(source),target=interval(target),sourceBlocked=body.blockSource),id)
            # Changing availability invalidates open availability previews as well.
            result=service.save_event(db,event,user,'rescheduled',dict(bookingId=id,version=version,sourceBlocked=body.blockSource))
            service.notifications.deliver(db,[owner,host['user']],workspace,version,'meeting.rescheduled')
            return service.remember(db,user,body,fingerprint,dict(id=id,version=version,status='scheduled',startsAt=target['starts_at'],endsAt=target['ends_at'],eventVersion=result['version']))
