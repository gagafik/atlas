"""Private waiting requests and time-limited, parent-confirmed slot offers."""
from datetime import datetime
import json
from uuid import uuid4
from fastapi import HTTPException,Query,Request
from pydantic import AwareDatetime,Field
from typing import Literal
from . import meetings
from .meetings import FAMILY_ROLES
from .meeting_models import Mutation,utc
from .messages import Directory
from .notifications import encoded,permission_key
from .school_forms import enabled
from .meeting_booking import check,insert


class Join(Mutation):
    hostId:str=Field(min_length=1,max_length=100)
    studentId:str=Field(min_length=1,max_length=100)
    note:str=Field(default='',max_length=500)


class Offer(Mutation):
    eventVersion:int=Field(ge=1)
    slotId:str=Field(min_length=1,max_length=100)
    slotVersion:int=Field(ge=1)
    expiresAt:AwareDatetime
    reason:str=Field(default='',max_length=500)


class Change(Mutation):
    action:Literal['accept','decline','withdraw','revoke','cancel']
    eventVersion:int|None=Field(default=None,ge=1)
    slotVersion:int|None=Field(default=None,ge=1)
    reason:str=Field(default='',max_length=500)


def tables(db):
    db.executescript('''
    CREATE TABLE IF NOT EXISTS meeting_waitlist (
      seq INTEGER PRIMARY KEY AUTOINCREMENT,id TEXT UNIQUE,event_id TEXT,workspace TEXT,
      parent_id TEXT,student_id TEXT,host_id TEXT,permissions TEXT,status TEXT,version INTEGER,
      created TEXT,updated TEXT,slot_id TEXT,expires_at TEXT,document TEXT);
    CREATE UNIQUE INDEX IF NOT EXISTS meeting_waitlist_child ON meeting_waitlist(event_id,host_id,student_id) WHERE status IN ('waiting','offered');
    CREATE INDEX IF NOT EXISTS meeting_waitlist_event ON meeting_waitlist(event_id,seq);
    CREATE INDEX IF NOT EXISTS meeting_waitlist_parent ON meeting_waitlist(workspace,parent_id,event_id,seq);
    CREATE INDEX IF NOT EXISTS meeting_waitlist_hold ON meeting_waitlist(slot_id,status,expires_at);
    CREATE TABLE IF NOT EXISTS meeting_waitlist_history (
      seq INTEGER PRIMARY KEY AUTOINCREMENT,wait_id TEXT,created TEXT,actor_id TEXT,action TEXT,document TEXT);
    CREATE INDEX IF NOT EXISTS meeting_waitlist_history_owner ON meeting_waitlist_history(wait_id,seq);
    CREATE INDEX IF NOT EXISTS meeting_waitlist_ready ON meeting_waitlist(event_id,status,seq);
    ''')


def history(db,row,user,action,detail):
    db.execute('INSERT INTO meeting_waitlist_history(wait_id,created,actor_id,action,document) VALUES (?,?,?,?,?)',
               (row['id'],utc(meetings.now()),user['id'],action,encoded(detail)))


def participants(service,db,row,event,snap,workspace):
    owner=service.booking_owner(db,row,snap,workspace)
    host=service.hosts(db,event,snap,workspace).get(row['host_id'])
    current=bool(owner and enabled(owner,snap,workspace) and host and row['student_id'] in service.eligible_pupils(host,Directory(snap.state,workspace)))
    return owner,host,current


def live_offer(row,event):
    return row['status']=='offered' and event['status'] in ['open','closed'] and row['expires_at']>utc(meetings.now())


def active_offer(db,row,event):
    if not live_offer(row,event):return False
    slot=db.execute('SELECT version,status FROM meeting_slots WHERE id=?',(row['slot_id'],)).fetchone()
    return bool(slot and slot['status']=='open' and slot['version']==json.loads(row['document']).get('heldVersion'))


def hold(service,db,slot,snap,workspace,ignore=None):
    for row in db.execute("SELECT * FROM meeting_waitlist WHERE slot_id=? AND status='offered' AND expires_at>?",(slot['id'],utc(meetings.now()))):
        if row['id']==ignore:continue
        event=service.event(db,workspace,row['event_id'])
        if active_offer(db,row,event) and participants(service,db,row,event,snap,workspace)[2]:return row
    return None


def release(db,row):
    if row['slot_id'] and row['expires_at'] and row['expires_at']>utc(meetings.now()):
        db.execute('UPDATE meeting_slots SET version=version+1 WHERE id=? AND version=?',(row['slot_id'],json.loads(row['document']).get('heldVersion')))


def update(db,row,user,status,action,reason='',**detail):
    release(db,row);doc=json.loads(row['document']);doc['reason']=reason
    db.execute('UPDATE meeting_waitlist SET status=?,version=version+1,updated=?,slot_id=NULL,expires_at=NULL,document=? WHERE id=?',
               (status,utc(meetings.now()),encoded(doc),row['id']))
    history(db,row,user,action,dict(version=row['version']+1,reason=reason,**detail))


def cancel_for_event(service,db,event,user,snap,workspace,reason,slot_ids=None):
    clause=" AND status='offered' AND slot_id IN (SELECT value FROM json_each(?))" if slot_ids is not None else " AND status IN ('waiting','offered')"
    args=(event['id'],encoded(slot_ids)) if slot_ids is not None else (event['id'],)
    rows=db.execute('SELECT * FROM meeting_waitlist WHERE event_id=?'+clause,args).fetchall()
    for row in rows:
        update(db,row,user,'waiting' if slot_ids is not None else 'cancelled','revoked' if slot_ids is not None else 'cancelled',reason)
        owner,_,_=participants(service,db,row,event,snap,workspace)
        if owner:service.notifications.deliver(db,[owner],workspace,row['version']+1,'meeting.waitlist_changed')
    return len(rows)


def booked(service,db,event,slot,user,student,snap,workspace,booking_id):
    row=db.execute("SELECT * FROM meeting_waitlist WHERE event_id=? AND host_id=? AND student_id=? AND status IN ('waiting','offered')",(event['id'],slot['host_id'],student)).fetchone()
    if row:
        update(db,row,user,'fulfilled','booked','За ребёнка оформлена запись на встречу.',bookingId=booking_id if row['parent_id']==user['id'] else None)
        owner,_,_=participants(service,db,row,event,snap,workspace)
        if owner and owner['id']!=user['id']:service.notifications.deliver(db,[owner],workspace,row['version']+1,'meeting.waitlist_changed')


def public(service,db,row,event,user,snap,workspace):
    owner,host,current=participants(service,db,row,event,snap,workspace)
    manager=service.can_manage(db,event,user,snap,workspace)
    if not manager and (row['parent_id']!=user['id'] or not owner):return None
    doc=json.loads(row['document']);active=row['status'] in ['waiting','offered'] and event['status'] in ['open','closed'] and event['definition']['endsAt']>utc(meetings.now())
    offered=active_offer(db,row,event);slot=db.execute('SELECT * FROM meeting_slots WHERE id=?',(row['slot_id'],)).fetchone() if row['slot_id'] else None
    return doc|dict(id=row['id'],version=row['version'],hostId=row['host_id'],studentId=row['student_id'],created=row['created'],status='expired' if row['status']=='offered' and not offered else row['status'],current=current,
        canOffer=manager and active and current and not offered,canAccept=user['role'] in FAMILY_ROLES and offered and current and bool(slot and slot['status']=='open') and row['student_id'] in service.bookable_pupils(db,event,user,snap,workspace),
        canWithdraw=user['role'] in FAMILY_ROLES and row['status'] in ['waiting','offered'],canManage=manager and row['status'] in ['waiting','offered'],
        offer=dict(slotId=slot['id'],slotVersion=slot['version'],startsAt=slot['starts_at'],endsAt=slot['ends_at'],expiresAt=row['expires_at']) if slot else None)


def register(service,app,Workspace):
    def access(db,workspace,id,user,snap):
        row=db.execute('SELECT * FROM meeting_waitlist WHERE id=? AND workspace=?',(id,workspace)).fetchone()
        if not row:raise HTTPException(404,'Заявка не найдена.')
        event=service.event(db,workspace,row['event_id'])
        if not public(service,db,row,event,user,snap,workspace):raise HTTPException(404,'Заявка недоступна.')
        return row,event

    @app.get('/api/meetings/events/{id}/waitlist')
    def listing(id:str,request:Request,workspace:Workspace='school',after:int=Query(default=0,ge=0),limit:int=Query(default=30,ge=1,le=100)):
        with service.context(request,workspace) as (db,user,snap):
            event=service.event(db,workspace,id);manager=service.can_manage(db,event,user,snap,workspace)
            if not manager and user['role'] not in FAMILY_ROLES:raise HTTPException(403,'Очередь доступна семье, ведущему консультаций и администрации.')
            service.require_event(db,event,user,snap,workspace)
            clause='' if manager else ' AND parent_id=?';args=[] if manager else [user['id']]
            rows=db.execute('SELECT * FROM meeting_waitlist WHERE workspace=? AND event_id=? AND seq>?'+clause+' ORDER BY seq LIMIT ?',(workspace,id,after,*args,limit+1)).fetchall()
            items=[p for row in rows[:limit] if (p:=public(service,db,row,event,user,snap,workspace))]
            return dict(items=items,nextAfter=rows[limit-1]['seq'] if len(rows)>limit else None,eventVersion=event['version'])

    @app.post('/api/meetings/events/{id}/waitlist')
    def join(id:str,body:Join,request:Request,workspace:Workspace='school'):
        with service.context(request,workspace,True) as (db,user,snap):
            if user['role'] not in FAMILY_ROLES:raise HTTPException(403,'Выберите своего ребёнка.')
            event=service.event(db,workspace,id)
            if body.studentId not in service.bookable_pupils(db,event,user,snap,workspace):raise HTTPException(403,'Выберите своего ребёнка.')
            host=service.hosts(db,event,snap,workspace).get(body.hostId)
            if not host or body.studentId not in service.eligible_pupils(host,Directory(snap.state,workspace)):raise HTTPException(404,'Ведущий недоступен.')
            fingerprint,old=service.replay(db,user,body,'waitlist:'+id)
            if old:return old
            meetings.version(event,body.version)
            if not service.registration_open(event):raise HTTPException(409,'Приём заявок закрыт.')
            # In a series an old booking does not stop a new request; only a coming one does.
            if db.execute("SELECT 1 FROM meeting_bookings WHERE event_id=? AND host_id=? AND student_id=? AND status!='cancelled'"+(' AND starts_at>?' if meetings.consultation(event) else ''),(id,body.hostId,body.studentId,*([utc(meetings.now())] if meetings.consultation(event) else []))).fetchone():raise HTTPException(409,'За ребёнка уже есть запись к этому ведущему.')
            previous=db.execute("SELECT * FROM meeting_waitlist WHERE event_id=? AND host_id=? AND student_id=? AND status IN ('waiting','offered')",(id,body.hostId,body.studentId)).fetchone()
            if previous:
                if participants(service,db,previous,event,snap,workspace)[2]:raise HTTPException(409,'За ребёнка уже есть действующая заявка к этому ведущему.')
                update(db,previous,user,'cancelled','cancelled','Прежний доступ изменился; создана новая заявка.')
            if db.execute("SELECT count(*) FROM meeting_waitlist WHERE event_id=? AND status IN ('waiting','offered')",(id,)).fetchone()[0]>=20000:raise HTTPException(409,'Достигнут лимит 20 000 действующих заявок мероприятия.')
            wait_id=str(uuid4());stamp=utc(meetings.now());doc=dict(parentName=user['name'],studentName=snap.entities['student'][body.studentId].name,hostName=host['name'],note=body.note,reason='')
            # permissions is stored for compatibility and no longer compared: the parent-pupil link is checked instead (Meetings.booking_owner).
            db.execute('INSERT INTO meeting_waitlist(id,event_id,workspace,parent_id,student_id,host_id,permissions,status,version,created,updated,document) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                       (wait_id,id,workspace,user['id'],body.studentId,body.hostId,permission_key(user),'waiting',1,stamp,stamp,encoded(doc)))
            history(db,dict(id=wait_id),user,'joined',dict(version=1))
            return service.remember(db,user,body,fingerprint,dict(id=wait_id,version=1,status='waiting'))

    @app.post('/api/meetings/waitlist/{id}/offer')
    def offer(id:str,body:Offer,request:Request,workspace:Workspace='school'):
        with service.context(request,workspace,True) as (db,user,snap):
            if user['role'] in FAMILY_ROLES:raise HTTPException(403,'Предложение интервала делает ведущий консультаций или администрация.')
            row,event=access(db,workspace,id,user,snap)
            owner,host,current=participants(service,db,row,event,snap,workspace)
            if not current:raise HTTPException(409,'Права семьи или ведущего изменились.')
            fingerprint,old=service.replay(db,user,body,'wait-offer:'+id)
            if old:return old
            meetings.version(row,body.version);meetings.version(event,body.eventVersion)
            if row['status'] not in ['waiting','offered'] or active_offer(db,row,event) or event['status'] not in ['open','closed']:raise HTTPException(409,'Заявка уже обработана. Сначала отзовите действующее предложение.')
            slot=db.execute('SELECT * FROM meeting_slots WHERE id=? AND event_id=? AND host_id=?',(body.slotId,event['id'],row['host_id'])).fetchone()
            if not slot:raise HTTPException(404,'Выберите интервал этого ведущего и мероприятия.')
            meetings.version(slot,body.slotVersion)
            if body.expiresAt<=meetings.now() or body.expiresAt>datetime.fromisoformat(slot['starts_at']):raise HTTPException(422,'Срок ответа должен быть в будущем, не позже начала встречи.')
            check(service,db,event,slot,owner,row['student_id'],snap,workspace,id)
            release(db,row);doc=json.loads(row['document']);doc['reason']=body.reason.strip();doc['heldVersion']=slot['version']+1
            db.execute("UPDATE meeting_waitlist SET status='offered',version=version+1,updated=?,slot_id=?,expires_at=?,document=? WHERE id=?",(utc(meetings.now()),slot['id'],utc(body.expiresAt),encoded(doc),id))
            db.execute('UPDATE meeting_slots SET version=version+1 WHERE id=?',(slot['id'],))
            history(db,row,user,'offered',dict(version=row['version']+1,startsAt=slot['starts_at'],endsAt=slot['ends_at'],expiresAt=utc(body.expiresAt),reason=body.reason))
            service.notifications.deliver(db,[owner],workspace,row['version']+1,'meeting.waitlist_offer')
            return service.remember(db,user,body,fingerprint,dict(id=id,version=row['version']+1,status='offered'))

    @app.post('/api/meetings/waitlist/{id}')
    def change(id:str,body:Change,request:Request,workspace:Workspace='school'):
        with service.context(request,workspace,True) as (db,user,snap):
            row,event=access(db,workspace,id,user,snap)
            if (body.action in ['accept','decline','withdraw'])!=(user['role'] in FAMILY_ROLES):raise HTTPException(403,'Это действие недоступно вашей роли.')
            owner,host,current=participants(service,db,row,event,snap,workspace)
            if body.action=='accept' and row['student_id'] not in service.bookable_pupils(db,event,user,snap,workspace):raise HTTPException(403,'Запись учеников на консультации выключена школой.')
            if body.action=='accept' and not current:raise HTTPException(409,'Права семьи или ведущего изменились.')
            fingerprint,old=service.replay(db,user,body,'wait-change:'+id)
            if old:return old
            meetings.version(row,body.version)
            if row['status'] not in ['waiting','offered']:raise HTTPException(409,'Заявка уже завершена.')
            owner,host,current=participants(service,db,row,event,snap,workspace)
            if body.action=='accept':
                if not current or not active_offer(db,row,event):raise HTTPException(409,'Предложение истекло или доступ изменился. Обновите очередь.')
                meetings.version(event,body.eventVersion)
                slot=db.execute('SELECT * FROM meeting_slots WHERE id=?',(row['slot_id'],)).fetchone()
                if not slot:raise HTTPException(409,'Предложенный интервал недоступен.')
                meetings.version(slot,body.slotVersion)
                check(service,db,event,slot,owner,row['student_id'],snap,workspace,id)
                result=insert(service,db,event,slot,owner,row['student_id'],host,snap,workspace,json.loads(row['document'])['note'])
                update(db,row,user,'accepted','accepted',bookingId=result['id'],startsAt=slot['starts_at'],endsAt=slot['ends_at'])
                return service.remember(db,user,body,fingerprint,dict(id=id,version=row['version']+1,status='accepted',bookingId=result['id']))
            if body.action in ['decline','revoke'] and row['status']!='offered':raise HTTPException(409,'Нет предложения для отклонения или отзыва.')
            if user['role'] not in FAMILY_ROLES and not body.reason.strip():raise HTTPException(422,'Укажите причину.')
            status='waiting' if body.action in ['decline','revoke'] else 'withdrawn' if body.action=='withdraw' else 'cancelled'
            update(db,row,user,status,body.action,body.reason.strip())
            if user['role'] not in FAMILY_ROLES and owner:service.notifications.deliver(db,[owner],workspace,row['version']+1,'meeting.waitlist_changed')
            return service.remember(db,user,body,fingerprint,dict(id=id,version=row['version']+1,status=status))

    @app.get('/api/meetings/waitlist/{id}/history')
    def audit(id:str,request:Request,workspace:Workspace='school'):
        with service.context(request,workspace) as (db,user,snap):
            access(db,workspace,id,user,snap)
            return [dict(action=r['action'],created=r['created'],**json.loads(r['document'])) for r in db.execute('SELECT * FROM meeting_waitlist_history WHERE wait_id=? ORDER BY seq DESC LIMIT 30',(id,))]
