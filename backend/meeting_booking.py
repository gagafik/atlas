"""Shared checks and atomic insertion for direct and waitlist bookings."""
from datetime import datetime,timedelta
from uuid import uuid4
from fastapi import HTTPException
from . import meetings
from .meeting_models import utc
from .meeting_availability import Occupancy
from .notifications import encoded,permission_key


def check(service,db,event,slot,user,student,snap,workspace,ignore_wait=None):
    from .meeting_waitlist import hold
    if slot['status']!='open' or datetime.fromisoformat(slot['starts_at'])<=meetings.now():
        raise HTTPException(409,'Запись на этот интервал закрыта.')
    if hold(service,db,slot,snap,workspace,ignore_wait):raise HTTPException(409,'Этот интервал предложен семье из очереди ожидания.')
    if service.slot_conflict(db,slot,Occupancy(snap),slot['id']):raise HTTPException(409,'Ведущий или кабинет уже занят. Выберите другой интервал.')
    if db.execute("SELECT 1 FROM meeting_bookings WHERE status!='cancelled' AND (slot_id=? OR (event_id=? AND host_id=? AND student_id=?)) LIMIT 1",(slot['id'],event['id'],slot['host_id'],student)).fetchone():
        raise HTTPException(409,'Интервал занят или за этого ребёнка уже есть запись к ведущему в этом мероприятии.')
    for column,value in [('parent_id',user['id']),('student_id',student)]:
        if db.execute(f"SELECT 1 FROM meeting_bookings WHERE workspace=? AND {column}=? AND status!='cancelled' AND starts_at<? AND ends_at>? LIMIT 1",(workspace,value,slot['ends_at'],slot['starts_at'])).fetchone():
            raise HTTPException(409,'У вас или ребёнка уже есть встреча в это время.')


def insert(service,db,event,slot,user,student,host,snap,workspace,note):
    booking_id=str(uuid4());document=dict(parentName=user['name'],studentName=snap.entities['student'][student].name,hostName=host['name'],studentId=student,note=note,reason='',location=host['location'],roomName=snap.entities['room'][host['roomId']].name if host['roomId'] in snap.entities['room'] else '',updated=utc(meetings.now()))
    # permissions is stored for compatibility and no longer compared: the parent-pupil link is checked instead (Meetings.booking_owner).
    db.execute('INSERT INTO meeting_bookings VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',(booking_id,event['id'],slot['id'],workspace,user['id'],student,host['accountId'],permission_key(user),slot['starts_at'],slot['ends_at'],'scheduled',1,encoded(document)))
    db.execute('UPDATE meeting_slots SET version=version+1 WHERE id=?',(slot['id'],))
    if event['definition']['reminderMinutes']:
        due=utc(datetime.fromisoformat(slot['starts_at'])-timedelta(minutes=event['definition']['reminderMinutes']))
        db.execute("INSERT INTO meeting_reminders VALUES (?,?,'pending')",(booking_id,due))
    service.history(db,event,user,'booked',dict(version=1),booking_id)
    service.notifications.deliver(db,[user,host['user']],workspace,1,'meeting.booked',target=service.notice(booking_id,event,document,slot['starts_at'],'Запись на встречу'))
    return dict(id=booking_id,version=1,status='scheduled')
