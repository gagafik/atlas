"""Consultation hours: a teacher's weekly series of 1:1 slots on the meetings tables, and the teacher's day across events."""
from datetime import date,datetime,time,timedelta
import hashlib
import json
from uuid import uuid4
from fastapi import HTTPException,Query,Request
from . import meetings
from .meeting_availability import Occupancy,ZONE
from .meeting_models import ConsultationSeries,Host,utc
from .meetings import FAMILY_ROLES
from .messages import Directory
from .notifications import encoded
from .school_forms import grant_ids

MAX_SLOTS=5000      # per series
MAX_OWN_SERIES=10   # open or closed series of one teacher
MAX_SERIES=1000     # open or closed series of a school
KIND="json_extract(document,'$.kind')='consultation'"


def host_of(service,db,user,body,snap,workspace):
    """The host of a series: a teacher for themselves, an administrator for any teacher account. Raises 403 or 422."""
    if user['role'] not in ('teacher','admin'):raise HTTPException(403,'Часы консультаций создаёт учитель или администрация.')
    if user['role']=='teacher' and body.accountId not in (None,user['id']):raise HTTPException(403,'Учитель создаёт часы консультаций только для себя.')
    account=body.accountId or user['id']
    row=db.execute('SELECT * FROM accounts WHERE id=? AND active=1',(account,)).fetchone();person=service.access.public(row) if row else None
    host=Host(accountId=account,teacherId=body.teacherId,scope='teaching',roomId=body.roomId or None,location=body.location).model_dump(mode='json')
    if problem:=service.host_problem(person,host,snap,workspace):raise HTTPException(422,problem)
    settings=snap.state.settings
    if body.startDate<settings.yearStart or body.endDate>settings.yearEnd:raise HTTPException(422,'Период консультаций должен лежать в учебном году.')
    return host


def candidates(service,db,body,host,occupancy,workspace):
    """Slots the series would create, and what it skips: closed days, and slots over a lesson, a room booking, an absence or another meeting."""
    slots,skipped,past=[],[],0
    length=timedelta(minutes=body.slotMinutes);step=length+timedelta(minutes=body.gapMinutes)
    first,last=time.fromisoformat(body.startTime),time.fromisoformat(body.endTime);day=body.startDate
    for _ in range((body.endDate-body.startDate).days+1):
        if day.weekday() in body.weekdays:
            if name:=occupancy.closed(day):skipped.append(dict(date=day.isoformat(),reason=f'Школа закрыта: {name}.'))
            else:
                instant=datetime.combine(day,first,ZONE);limit=datetime.combine(day,last,ZONE)
                while instant+length<=limit:
                    if instant<=meetings.now():past+=1
                    else:
                        slot=dict(id='',event_id='',workspace=workspace,host_id=host['accountId'],teacher_id=host['teacherId'],room_id=host['roomId'],starts_at=utc(instant),ends_at=utc(instant+length),version=0,status='open',reason='')
                        # Skipping per slot keeps the rest of the window bookable.
                        if problem:=service.slot_conflict(db,slot,occupancy):skipped.append(dict(date=day.isoformat(),startsAt=slot['starts_at'],reason=problem))
                        else:slots.append(slot)
                        if len(slots)>MAX_SLOTS:raise HTTPException(422,f'В одной серии допускается до {MAX_SLOTS} интервалов. Сократите период или время приёма.')
                    instant+=step
        day+=timedelta(days=1)
    key=hashlib.sha256(encoded([[[s['starts_at'],s['ends_at'],s['teacher_id'],s['room_id']] for s in slots],skipped,past]).encode()).hexdigest()
    return slots,skipped,past,key


def summary(slots,skipped,past,key):
    """What the teacher confirms: the count, and why anything is skipped, once per date and reason."""
    seen={}
    for item in skipped:
        found=seen.setdefault((item['date'],item['reason']),dict(item,count=0));found['count']+=1
    return dict(slotCount=len(slots),days=len({datetime.fromisoformat(s['starts_at']).astimezone(ZONE).date() for s in slots}),skipped=list(seen.values())[:30],skippedCount=len(skipped),skippedPast=past,previewHash=key)


def register(service,app,Workspace):
    base='/api/meetings/consultations'

    @app.post(base+'/preview')
    def preview(body:ConsultationSeries,request:Request,workspace:Workspace='school'):
        with service.context(request,workspace) as (db,user,snap):
            host=host_of(service,db,user,body,snap,workspace)
            return summary(*candidates(service,db,body,host,Occupancy(snap),workspace))

    @app.post(base)
    def create(body:ConsultationSeries,request:Request,workspace:Workspace='school'):
        # Resolving every date is slow: do it in a read transaction so the global write lock is only held for the cheap checks and inserts.
        with service.context(request,workspace) as (db,user,snap):
            host_of(service,db,user,body,snap,workspace);prefill=Occupancy(snap);day=body.startDate
            for _ in range((body.endDate-body.startDate).days+1):
                if day.weekday() in body.weekdays:prefill.index(day)
                day+=timedelta(days=1)
        with service.context(request,workspace,True) as (db,user,snap):
            host=host_of(service,db,user,body,snap,workspace);fingerprint,old=service.replay(db,user,body,'consultations:'+workspace)
            if old:return old
            if db.execute("SELECT count(*) FROM meeting_events e JOIN meeting_hosts h ON h.event_id=e.id WHERE e.workspace=? AND e.status IN ('open','closed') AND json_extract(e.document,'$.kind')='consultation' AND h.account_id=?",(workspace,host['accountId'])).fetchone()[0]>=MAX_OWN_SERIES:
                raise HTTPException(422,f'У ведущего может быть не более {MAX_OWN_SERIES} действующих серий консультаций. Закройте или отправьте в архив старые.')
            if db.execute(f"SELECT count(*) FROM meeting_events WHERE workspace=? AND status IN ('open','closed') AND {KIND}",(workspace,)).fetchone()[0]>=MAX_SERIES:
                raise HTTPException(422,f'В школе может быть не более {MAX_SERIES} действующих серий консультаций.')
            slots,skipped,past,key=candidates(service,db,body,host,prefill if prefill.snap is snap else Occupancy(snap),workspace)
            if key!=body.previewHash:raise HTTPException(409,'Расписание или занятость изменились. Повторите проверку.')
            if not slots:raise HTTPException(422,'Все интервалы уже начались или пересекаются с занятостью.' if past or skipped else 'В выбранное время не помещается ни один полный интервал.')
            id=str(uuid4());stamp=utc(meetings.now())
            definition=dict(kind='consultation',title=body.title,description=body.description,pattern=dict(startDate=body.startDate.isoformat(),endDate=body.endDate.isoformat(),weekdays=sorted(body.weekdays),startTime=body.startTime,endTime=body.endTime),
                            startsAt=utc(datetime.combine(body.startDate,time.min,ZONE)),endsAt=utc(datetime.combine(body.endDate+timedelta(days=1),time.min,ZONE)),bookingOpensAt=None,bookingClosesAt=None,
                            slotMinutes=body.slotMinutes,gapMinutes=body.gapMinutes,cancelMinutes=body.cancelMinutes,reminderMinutes=body.reminderMinutes,hosts=[host])
            db.execute('INSERT INTO meeting_events(id,workspace,version,status,created,updated,document) VALUES (?,?,?,?,?,?,?)',(id,workspace,1,'open',stamp,stamp,encoded(definition)))
            event=service.event(db,workspace,id);service.install_hosts(db,event,definition,snap,workspace)
            for slot in slots:
                slot.update(id=str(uuid4()),event_id=id,version=1)
                db.execute('INSERT INTO meeting_slots VALUES (:id,:event_id,:workspace,:host_id,:teacher_id,:room_id,:starts_at,:ends_at,:version,:status,:reason)',slot)
            service.history(db,event,user,'created',dict(version=1,kind='consultation',slots=len(slots),skipped=len(skipped),skippedPast=past))
            # No mass notice: a series does not push hundreds of families, it shows in their list.
            return service.remember(db,user,body,fingerprint,dict(id=id,version=1,status='open',slots=len(slots),skipped=len(skipped),skippedPast=past))

    def item(event,hosts,pupils=None,directory=None):
        host=next(iter(hosts.values()),None)
        result=dict(id=event['id'],title=event['definition']['title'],version=event['version'],status=event['status'],startsAt=event['definition']['startsAt'],endsAt=event['definition']['endsAt'],
                    hostId=host['accountId'] if host else '',hostName=host['name'] if host else '',pattern=event['definition'].get('pattern'))
        if pupils is not None:result['studentIds']=sorted(pupils & service.eligible_pupils(host,directory))
        return result

    @app.get(base)
    def listing(request:Request,workspace:Workspace='school',before:int=Query(default=9223372036854775807,ge=1),limit:int=Query(default=20,ge=1,le=50)):
        with service.context(request,workspace) as (db,user,snap):
            if user['role'] in FAMILY_ROLES:
                pupils=service.bookable_pupils(db,dict(definition=dict(kind='consultation')),user,snap,workspace);items=[]
                if pupils:
                    directory=Directory(snap.state,workspace)
                    rows=db.execute(f"SELECT id FROM meeting_events WHERE workspace=? AND status='open' AND {KIND} AND json_extract(document,'$.endsAt')>? ORDER BY seq DESC LIMIT 1000",(workspace,utc(meetings.now()))).fetchall()
                    for row in rows:
                        event=service.event(db,workspace,row['id']);hosts={id:h for id,h in service.hosts(db,event,snap,workspace).items() if h['current'] and pupils & service.eligible_pupils(h,directory)}
                        if hosts:items.append(item(event,hosts,pupils,directory))
                return dict(items=items,nextBefore=None)
            if user['role']=='admin':
                rows=db.execute(f"SELECT id,seq FROM meeting_events WHERE workspace=? AND seq<? AND {KIND} ORDER BY seq DESC LIMIT ?",(workspace,before,limit+1)).fetchall()
            else:
                rows=db.execute(f"SELECT e.id,e.seq FROM meeting_hosts h JOIN meeting_events e ON e.id=h.event_id WHERE h.account_id=? AND e.workspace=? AND e.seq<? AND json_extract(e.document,'$.kind')='consultation' ORDER BY e.seq DESC LIMIT ?",(user['id'],workspace,before,limit+1)).fetchall()
            items=[]
            for row in rows[:limit]:
                event=service.event(db,workspace,row['id']);items.append(item(event,service.visible_hosts(db,event,user,snap,workspace)))
            return dict(items=items,nextBefore=rows[limit-1]['seq'] if len(rows)>limit else None)

    @app.get(base+'/mine')
    def mine(request:Request,workspace:Workspace='school'):
        with service.context(request,workspace) as (db,user,snap):
            if user['role'] not in FAMILY_ROLES:raise HTTPException(403,'Записи на консультации показываются семье.')
            pupils=service.parent_pupils(user,snap,workspace);events={};items=[]
            rows=db.execute(f"SELECT * FROM meeting_bookings WHERE workspace=? AND student_id IN (SELECT value FROM json_each(?)) AND event_id IN (SELECT id FROM meeting_events WHERE workspace=? AND {KIND}) AND (status!='cancelled' OR json_extract(document,'$.updated')>?) ORDER BY starts_at DESC,id LIMIT 100",
                            (workspace,encoded(sorted(pupils)),workspace,utc(meetings.now()-timedelta(days=30)))).fetchall()
            for booking in rows:
                if booking['event_id'] not in events:events[booking['event_id']]=service.event(db,workspace,booking['event_id'])
                public=service.booking_public(booking,events[booking['event_id']],user);own=booking['parent_id']==user['id']
                result=dict(id=public['id'],eventId=public['eventId'],eventTitle=public['eventTitle'],version=public['version'],hostName=public['hostName'],studentId=public['studentId'],studentName=public['studentName'],bookedBy=public['parentName'],byPupil=public.get('byPupil',False),
                            startsAt=public['startsAt'],endsAt=public['endsAt'],status=public['status'],location=public['location'],roomName=public['roomName'],reason=public['reason'],ownBooking=own,canCancel=public['canCancel'] and own)
                # A guardian sees that a child is booked, but the private note belongs to whoever wrote it.
                if own:result['note']=public['note']
                items.append(result)
            return dict(items=items)

    @app.get('/api/meetings/day')
    def day(request:Request,workspace:Workspace='school',onDate:date|None=None,hostId:str=''):
        with service.context(request,workspace) as (db,user,snap):
            if user['role'] in FAMILY_ROLES:raise HTTPException(403,'День ведущего доступен сотрудникам.')
            if hostId and hostId!=user['id'] and user['role']!='admin':raise HTTPException(403,'Чужой день доступен только администрации.')
            onDate=onDate or meetings.now().astimezone(ZONE).date()
            row=db.execute('SELECT * FROM accounts WHERE id=? AND active=1',(hostId or user['id'],)).fetchone()
            if not row:raise HTTPException(404,'Аккаунт не найден.')
            target=service.access.public(row);teachers=sorted(grant_ids(target,workspace,'teacher') & snap.entities['teacher'].keys())
            lessons=sorted(({k:r[k] for k in ['start','end','subject','group','rooms','status','activity']} for t in teachers for r in snap.day_rows(onDate,'teacher',t)),key=lambda r:(r['start'],r['end']))
            begin=datetime.combine(onDate,time.min,ZONE);occupancy=Occupancy(snap);events={};slots=[]
            rows=db.execute("SELECT s.* FROM meeting_slots s JOIN meeting_events e ON e.id=s.event_id WHERE s.workspace=? AND s.host_id=? AND s.starts_at>=? AND s.starts_at<? AND e.status NOT IN ('cancelled','archived') ORDER BY s.starts_at,s.id LIMIT 500",
                            (workspace,target['id'],utc(begin),utc(begin+timedelta(days=1)))).fetchall()
            for slot in rows:
                if slot['event_id'] not in events:events[slot['event_id']]=service.event(db,workspace,slot['event_id'])
                event=events[slot['event_id']];host=service.hosts(db,event,snap,workspace).get(target['id'])
                booking=db.execute("SELECT * FROM meeting_bookings WHERE slot_id=? AND status!='cancelled'",(slot['id'],)).fetchone()
                result=dict(id=slot['id'],eventId=event['id'],eventTitle=event['definition']['title'],kind='consultation' if meetings.consultation(event) else 'meeting',startsAt=slot['starts_at'],endsAt=slot['ends_at'],
                            status='blocked' if slot['status']=='blocked' else 'occupied' if booking else 'past' if datetime.fromisoformat(slot['starts_at'])<=meetings.now() else 'available',
                            location=host['location'] if host else '',roomName=snap.entities['room'][slot['room_id']].name if slot['room_id'] in snap.entities['room'] else '')
                if booking:
                    result['booking']=service.booking_public(booking,event,user)
                    # A lesson, absence or closure added after the booking shows here.
                    result['conflict']=service.slot_conflict(db,slot,occupancy,slot['id']) if slot['status']=='open' else None
                slots.append(result)
            gatherings=[]
            for meeting in db.execute("SELECT * FROM class_meetings WHERE workspace=? AND status='scheduled' AND starts_at>=? AND starts_at<? AND EXISTS (SELECT 1 FROM json_each(teacher_ids) WHERE value IN (SELECT value FROM json_each(?))) ORDER BY starts_at,id",(workspace,utc(begin),utc(begin+timedelta(days=1)),encoded(teachers))):
                document=json.loads(meeting['document'])
                gatherings.append(dict(id=meeting['id'],title=document['title'],startsAt=meeting['starts_at'],endsAt=meeting['ends_at'],place=document['roomName'] or document['location'],classNames=document['classNames'],
                                       pupils=db.execute('SELECT count(*) FROM class_meeting_invitees WHERE meeting_id=?',(meeting['id'],)).fetchone()[0]))
            return dict(date=onDate.isoformat(),hostName=target['name'],lessons=lessons,slots=slots,classMeetings=gatherings)
