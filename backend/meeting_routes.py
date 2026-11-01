"""HTTP endpoints for school-administered meetings."""
from datetime import date,datetime,timedelta
import csv,json,tempfile
from uuid import uuid4,UUID
from fastapi import Request,Query,HTTPException
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask
from .access import now
from .meeting_models import Create,Edit,Transition,Availability,Book,BookingChange,SwitchSettings,utc
from .meeting_availability import Occupancy,ZONE
from .meetings import version,FAMILY_ROLES
from .messages import Directory
from .school_forms import enabled,grant_ids
from .notifications import encoded,permission_key
from . import meeting_waitlist,class_meetings
from .meeting_booking import check as check_booking,insert as insert_booking


def cursor(value):
    try:
        stamp,id=value.split('|',1);instant=datetime.fromisoformat(stamp)
        if instant.tzinfo is None:raise ValueError()
        UUID(id)
        return [utc(instant),id]
    except (ValueError,TypeError):raise HTTPException(422,'Некорректный указатель страницы.')


def register(service,app,Workspace):
    @app.get('/api/meetings/options')
    def options(request:Request,workspace:Workspace='school'):
        with service.context(request,workspace) as (db,user,snap):
            pupil_booking=service.pupil_booking(db,workspace)
            # A pupil gets no children to pick while the school keeps the switch off: they may only read and cancel what they already have.
            pupils=sorted(service.parent_pupils(user,snap,workspace)) if user['role']!='student' or pupil_booking else []
            result=dict(canManage=user['role']=='admin',pupilBooking=pupil_booking,timeZone=str(ZONE),students=[dict(id=id,name=snap.entities['student'][id].name) for id in pupils],hosts=[],rooms=[],classes=[],ownTeachers=[],classMeeting=class_meetings.options(user,snap,workspace))
            if user['role']=='teacher':
                result['ownTeachers']=[dict(id=id,name=snap.entities['teacher'][id].name) for id in sorted(grant_ids(user,workspace,'teacher') & snap.entities['teacher'].keys())]
                result['rooms']=[dict(id=x.id,name=x.name) for x in snap.state.rooms]
            if user['role']=='admin':
                result['settingsVersion']=(db.execute('SELECT version FROM meeting_settings WHERE workspace=?',(workspace,)).fetchone() or [0])[0]
                for row in db.execute("SELECT * FROM accounts WHERE active=1 AND role IN ('teacher','staff','editor','admin') ORDER BY name,id"):
                    person=service.access.public(row)
                    if enabled(person,snap,workspace):result['hosts'].append(dict(id=person['id'],name=person['name'],role=person['role'],teachers=[dict(id=id,name=snap.entities['teacher'][id].name) for id in sorted(grant_ids(person,workspace,'teacher') & snap.entities['teacher'].keys())]))
                result['rooms']=[dict(id=x.id,name=x.name) for x in snap.state.rooms]
                result['classes']=[dict(id=x.id,name=x.name) for x in snap.state.classes]
            return result

    @app.put('/api/meetings/settings')
    def switch(body:SwitchSettings,request:Request,workspace:Workspace='school'):
        with service.context(request,workspace,True) as (db,user,snap):
            service.admin(user);fingerprint,old=service.replay(db,user,body,'settings:'+workspace)
            if old:return old
            row=db.execute('SELECT version,document FROM meeting_settings WHERE workspace=?',(workspace,)).fetchone()
            if body.version!=(row['version'] if row else 0):raise HTTPException(409,'Настройка изменилась. Обновите страницу.')
            was=bool(row and json.loads(row['document']).get('pupilBooking'));number=body.version+1
            db.execute('INSERT INTO meeting_settings(workspace,version,document) VALUES (?,?,?) ON CONFLICT(workspace) DO UPDATE SET version=excluded.version,document=excluded.document',(workspace,number,encoded(dict(pupilBooking=body.pupilBooking))))
            service.history(db,dict(id='settings:'+workspace),user,'settings',dict(version=number,pupilBooking=body.pupilBooking,before=was,reason=body.reason))
            service.access.record(db,'meeting.settings_updated',workspace,detail=f'pupilBooking={int(body.pupilBooking)}: {body.reason}',principal=user)
            return service.remember(db,user,body,fingerprint,dict(version=number,pupilBooking=body.pupilBooking))

    @app.get('/api/meetings/events')
    def events(request:Request,workspace:Workspace='school',before:int=Query(default=9223372036854775807,ge=1),limit:int=Query(default=20,ge=1,le=50)):
        with service.context(request,workspace) as (db,user,snap):
            # Consultation series have their own list: hundreds of teachers' series must not crowd out the school's parent events.
            rows=db.execute("SELECT id,seq FROM meeting_events WHERE workspace=? AND seq<? AND COALESCE(json_extract(document,'$.kind'),'meeting')='meeting' ORDER BY seq DESC LIMIT ?",(workspace,before,limit+1)).fetchall();items=[]
            for row in rows[:limit]:
                event=service.event(db,workspace,row['id']);hosts=service.visible_hosts(db,event,user,snap,workspace)
                if user['role']!='admin' and not hosts:continue
                items.append(dict(id=event['id'],title=event['definition']['title'],version=event['version'],status=event['status'],startsAt=event['definition']['startsAt'],endsAt=event['definition']['endsAt']))
            return dict(items=items,nextBefore=rows[limit-1]['seq'] if len(rows)>limit else None)

    @app.post('/api/meetings/events')
    def create(body:Create,request:Request,workspace:Workspace='school'):
        with service.context(request,workspace,True) as (db,user,snap):
            service.admin(user);fingerprint,old=service.replay(db,user,body,'create:'+workspace)
            if old:return old
            if db.execute("SELECT count(*) FROM meeting_events WHERE workspace=? AND status!='archived' AND COALESCE(json_extract(document,'$.kind'),'meeting')='meeting'",(workspace,)).fetchone()[0]>=100:raise HTTPException(422,'Архивируйте старые мероприятия: не более 100 действующих.')
            if body.definition.endsAt<=now():raise HTTPException(422,'Создайте мероприятие в будущем.')
            id=str(uuid4());stamp=utc(now());definition=body.definition.document()
            db.execute('INSERT INTO meeting_events(id,workspace,version,status,created,updated,document) VALUES (?,?,?,?,?,?,?)',(id,workspace,1,'draft',stamp,stamp,encoded(definition)))
            event=service.event(db,workspace,id);service.install_hosts(db,event,definition,snap,workspace)
            service.history(db,event,user,'created',dict(version=1))
            return service.remember(db,user,body,fingerprint,dict(id=id,version=1))

    @app.get('/api/meetings/events/{id}')
    def detail(id:str,request:Request,workspace:Workspace='school'):
        with service.context(request,workspace) as (db,user,snap):
            event=service.event(db,workspace,id);hosts=service.require_event(db,event,user,snap,workspace)
            definition={k:v for k,v in event['definition'].items() if k!='hosts'}
            manage=service.can_manage(db,event,user,snap,workspace)
            public=[];directory=Directory(snap.state,workspace);pupils=service.bookable_pupils(db,event,user,snap,workspace)
            for host in hosts.values():
                item={k:host[k] for k in ['accountId','name','current','location','roomId']}
                item['roomName']=snap.entities['room'][host['roomId']].name if host['roomId'] in snap.entities['room'] else ''
                if manage:item.update({k:host[k] for k in ['teacherId','scope','classIds']})
                if user['role'] in FAMILY_ROLES:item['studentIds']=sorted(pupils & service.eligible_pupils(host,directory))
                public.append(item)
            return dict(id=id,version=event['version'],status=event['status'],definition=definition,hosts=public,registrationOpen=service.registration_open(event),canManage=manage,slotCount=db.execute('SELECT count(*) FROM meeting_slots WHERE event_id=?',(id,)).fetchone()[0] if manage else None)

    @app.put('/api/meetings/events/{id}')
    def edit(id:str,body:Edit,request:Request,workspace:Workspace='school'):
        with service.context(request,workspace,True) as (db,user,snap):
            service.admin(user);event=service.event(db,workspace,id);fingerprint,old=service.replay(db,user,body,'edit:'+id)
            if old:return old
            version(event,body.version)
            if event['status']!='draft':raise HTTPException(409,'Опубликованные параметры не меняются. Управляйте отдельными интервалами.')
            if db.execute('SELECT 1 FROM meeting_slots WHERE event_id=? LIMIT 1',(id,)).fetchone():raise HTTPException(409,'Сначала настройте мероприятие и ведущих, затем создавайте интервалы. Для других параметров создайте новое мероприятие.')
            event['definition']=body.definition.document();service.install_hosts(db,event,event['definition'],snap,workspace)
            return service.remember(db,user,body,fingerprint,service.save_event(db,event,user,'edited'))

    @app.post('/api/meetings/events/{id}/availability/preview')
    def preview(id:str,body:Availability,request:Request,workspace:Workspace='school'):
        with service.context(request,workspace) as (db,user,snap):
            event=service.event(db,workspace,id);service.manage(db,event,user,snap,workspace)
            return service.availability(db,event,body,snap,workspace)[0]

    @app.post('/api/meetings/events/{id}/availability')
    def availability(id:str,body:Availability,request:Request,workspace:Workspace='school'):
        with service.context(request,workspace,True) as (db,user,snap):
            event=service.event(db,workspace,id);service.manage(db,event,user,snap,workspace);fingerprint,old=service.replay(db,user,body,'availability:'+id)
            if old:return old
            preview,slots,bookings=service.availability(db,event,body,snap,workspace)
            if preview['conflictCount']:raise HTTPException(409,'Есть пересечения. Уточните время, ведущего или кабинет.')
            if preview['previewHash']!=body.previewHash:raise HTTPException(409,'Интервалы или записи изменились. Повторите проверку перед применением.')
            if not slots:raise HTTPException(422,'Все интервалы выбранного времени уже начались или прошли.' if preview['skippedPast'] else 'Нет интервалов для этого действия.')
            if body.action=='generate':
                for slot in slots:
                    slot['id']=str(uuid4());slot['version']=1
                    db.execute('INSERT INTO meeting_slots VALUES (:id,:event_id,:workspace,:host_id,:teacher_id,:room_id,:starts_at,:ends_at,:version,:status,:reason)',slot)
            else:
                if body.action=='block':meeting_waitlist.cancel_for_event(service,db,event,user,snap,workspace,body.reason,[s['id'] for s in slots])
                for booked in bookings:service.cancel_booking(db,service.booking(db,workspace,booked['id']),event,user,snap,workspace,body.reason)
                for slot in slots:db.execute('UPDATE meeting_slots SET status=?,version=version+1,reason=? WHERE id=?',('blocked' if body.action=='block' else 'open',body.reason if body.action=='block' else '',slot['id']))
            result=service.note_event(db,event,user,body.action,dict(affectedSlots=len(slots),cancelledBookings=len(bookings),skippedPast=preview['skippedPast'],reason=body.reason))
            return service.remember(db,user,body,fingerprint,result|dict(affectedSlots=len(slots),cancelledBookings=len(bookings),skippedPast=preview['skippedPast']))

    @app.post('/api/meetings/events/{id}/transition')
    def transition(id:str,body:Transition,request:Request,workspace:Workspace='school'):
        with service.context(request,workspace,True) as (db,user,snap):
            event=service.event(db,workspace,id);service.manage(db,event,user,snap,workspace);fingerprint,old=service.replay(db,user,body,'transition:'+id)
            if old:return old
            version(event,body.version);recipients=[]
            if body.action in ['publish','reopen']:
                if event['status']!=('draft' if body.action=='publish' else 'closed'):raise HTTPException(409,'Недопустимый переход состояния.')
                if datetime.fromisoformat(event['definition']['endsAt'])<=now() or event['definition']['bookingClosesAt'] and datetime.fromisoformat(event['definition']['bookingClosesAt'])<=now():raise HTTPException(422,'Срок записи или встреч уже прошёл.')
                hosts=service.hosts(db,event,snap,workspace)
                if any(not h['current'] for h in hosts.values()):raise HTTPException(422,'Доступ ведущего изменился; публикация недоступна.')
                slots=db.execute("SELECT * FROM meeting_slots WHERE event_id=? AND status='open' AND starts_at>?",(id,utc(now()))).fetchall()
                if not slots:raise HTTPException(422,'Создайте свободные интервалы в будущем.')
                occupancy=Occupancy(snap)
                if any(service.slot_conflict(db,s,occupancy,s['id']) for s in slots):raise HTTPException(409,'Интервалы пересекаются со школьным календарём или другими встречами.')
                event['status']='open'
                if body.action=='publish':
                    directory=Directory(snap.state,workspace);eligible=set().union(*(service.eligible_pupils(h,directory) for h in hosts.values()))
                    recipients=[h['user'] for h in hosts.values()]
                    for row in db.execute("SELECT * FROM accounts WHERE active=1 AND role='parent'"):
                        person=service.access.public(row)
                        if service.parent_pupils(person,snap,workspace) & eligible:recipients.append(person)
            elif body.action=='close':
                if event['status']!='open':raise HTTPException(409,'Запись уже закрыта.')
                event['status']='closed'
            elif body.action=='cancel':
                if event['status'] in ['cancelled','archived'] or not body.reason:raise HTTPException(422,'Укажите причину отмены действующего мероприятия.')
                # Meetings that already started took place: they keep their record, their outcome can still be marked, and nobody is told they were cancelled.
                instant=utc(now())
                for row in db.execute("SELECT * FROM meeting_bookings WHERE event_id=? AND status='scheduled' AND starts_at>?",(id,instant)).fetchall():service.cancel_booking(db,dict(row),event,user,snap,workspace,body.reason)
                db.execute("UPDATE meeting_slots SET status='blocked',version=version+1,reason=? WHERE event_id=? AND starts_at>?",(body.reason,id,instant));event['status']='cancelled'
            else:
                if event['status']=='archived':raise HTTPException(409,'Мероприятие уже в архиве.')
                if db.execute("SELECT 1 FROM meeting_bookings WHERE event_id=? AND status='scheduled' AND ends_at>? LIMIT 1",(id,utc(now()))).fetchone():raise HTTPException(409,'Сначала отмените предстоящие встречи или дождитесь их окончания.')
                event['status']='archived'
            if body.action in ['cancel','archive']:
                meeting_waitlist.cancel_for_event(service,db,event,user,snap,workspace,body.reason or 'Мероприятие отправлено в архив.')
            result=service.save_event(db,event,user,body.action,dict(reason=body.reason))
            if recipients:service.notifications.deliver(db,recipients,workspace,event['version'],'meeting.published')
            return service.remember(db,user,body,fingerprint,result)

    @app.get('/api/meetings/events/{id}/slots')
    def slots(id:str,request:Request,workspace:Workspace='school',hostId:str='',onDate:date|None=None,after:str=Query(default='',max_length=150),limit:int=Query(default=60,ge=1,le=100)):
        with service.context(request,workspace) as (db,user,snap):
            event=service.event(db,workspace,id);hosts=service.require_event(db,event,user,snap,workspace)
            if hostId and hostId not in hosts:raise HTTPException(404,'Ведущий недоступен.')
            selected=[hostId] if hostId else list(hosts);clause='';params=[id,encoded(selected)]
            if onDate:
                start=datetime.combine(onDate,datetime.min.time(),ZONE);clause+=' AND starts_at>=? AND starts_at<?';params += [utc(start),utc(start+timedelta(days=1))]
            if after:
                pair=cursor(after)
                clause+=' AND (starts_at,id)>(?,?)';params+=pair
            rows=db.execute('SELECT * FROM meeting_slots WHERE event_id=? AND host_id IN (SELECT value FROM json_each(?))'+clause+' ORDER BY starts_at,id LIMIT ?',(*params,limit+1)).fetchall()
            occupancy=Occupancy(snap);items=[];manage=service.can_manage(db,event,user,snap,workspace)
            for row in rows[:limit]:
                host=hosts[row['host_id']];booking=db.execute("SELECT * FROM meeting_bookings WHERE slot_id=? AND status!='cancelled'",(row['id'],)).fetchone()
                conflict=service.slot_conflict(db,row,occupancy,row['id']) if row['status']=='open' and host['current'] else None
                status='blocked' if row['status']=='blocked' or not host['current'] else 'occupied' if booking else 'held' if meeting_waitlist.hold(service,db,row,snap,workspace) else 'past' if datetime.fromisoformat(row['starts_at'])<=now() else 'conflict' if conflict else 'available'
                item=dict(id=row['id'],version=row['version'],hostId=row['host_id'],hostName=host['name'],startsAt=row['starts_at'],endsAt=row['ends_at'],status=status,location=host['location'],roomName=snap.entities['room'][host['roomId']].name if host['roomId'] in snap.entities['room'] else '')
                if manage:item.update(reason=row['reason'],conflict=conflict)
                if booking and service.booking_access(db,booking,event,user,snap,workspace):item['booking']=service.booking_public(booking,event,user)
                items.append(item)
            return dict(items=items,nextAfter=rows[limit-1]['starts_at']+'|'+rows[limit-1]['id'] if len(rows)>limit else None)

    @app.post('/api/meetings/slots/{id}/book')
    def book(id:str,body:Book,request:Request,workspace:Workspace='school'):
        with service.context(request,workspace,True) as (db,user,snap):
            if user['role'] not in FAMILY_ROLES:raise HTTPException(403,'Выберите ребёнка, к которому у вас есть действующий доступ.')
            slot=db.execute('SELECT * FROM meeting_slots WHERE id=? AND workspace=?',(id,workspace)).fetchone()
            if not slot:raise HTTPException(404,'Интервал не найден.')
            event=service.event(db,workspace,slot['event_id'])
            if body.studentId not in service.bookable_pupils(db,event,user,snap,workspace):raise HTTPException(403,'Выберите ребёнка, к которому у вас есть действующий доступ.')
            host=service.hosts(db,event,snap,workspace).get(slot['host_id'])
            if not host or body.studentId not in service.eligible_pupils(host,Directory(snap.state,workspace)):raise HTTPException(404,'Запись к этому ведущему недоступна.')
            fingerprint,old=service.replay(db,user,body,'book:'+id)
            if old:return old
            version(event,body.eventVersion);version(slot,body.version)
            if not service.registration_open(event) or slot['status']!='open' or datetime.fromisoformat(slot['starts_at'])<=now():raise HTTPException(409,'Запись на этот интервал закрыта.')
            check_booking(service,db,event,slot,user,body.studentId,snap,workspace)
            result=insert_booking(service,db,event,slot,user,body.studentId,host,snap,workspace,body.note)
            meeting_waitlist.booked(service,db,event,slot,user,body.studentId,snap,workspace,result['id'])
            return service.remember(db,user,body,fingerprint,result)

    @app.get('/api/meetings/bookings')
    def bookings(request:Request,workspace:Workspace='school',eventId:str='',onDate:date|None=None,fromDate:date|None=None,after:str=Query(default='',max_length=150),limit:int=Query(default=30,ge=1,le=100)):
        with service.context(request,workspace) as (db,user,snap):
            clause='';params=[workspace]
            if user['role']!='admin':clause+=' AND '+('parent_id' if user['role'] in FAMILY_ROLES else 'host_id')+'=?';params.append(user['id'])
            if eventId:clause+=' AND event_id=?';params.append(eventId)
            if onDate:
                start=datetime.combine(onDate,datetime.min.time(),ZONE);clause+=' AND starts_at>=? AND starts_at<?';params.extend([utc(start),utc(start+timedelta(days=1))])
            if fromDate:clause+=' AND starts_at>=?';params.append(utc(datetime.combine(fromDate,datetime.min.time(),ZONE)))
            if after:
                pair=cursor(after)
                clause+=' AND (starts_at,id)>(?,?)';params+=pair
            rows=db.execute('SELECT * FROM meeting_bookings WHERE workspace=?'+clause+' ORDER BY starts_at,id LIMIT ?',(*params,limit+1)).fetchall();items=[];events={}
            for booking in rows[:limit]:
                if booking['event_id'] not in events:events[booking['event_id']]=service.event(db,workspace,booking['event_id'])
                event=events[booking['event_id']]
                if service.booking_access(db,booking,event,user,snap,workspace):
                    item=service.booking_public(booking,event,user)
                    if user['role'] not in FAMILY_ROLES:item['parentAccessCurrent']=bool(service.booking_owner(db,booking,snap,workspace))
                    items.append(item)
            return dict(items=items,nextAfter=rows[limit-1]['starts_at']+'|'+rows[limit-1]['id'] if len(rows)>limit else None)

    @app.post('/api/meetings/bookings/{id}')
    def change(id:str,body:BookingChange,request:Request,workspace:Workspace='school'):
        with service.context(request,workspace,True) as (db,user,snap):
            booking=service.booking(db,workspace,id);event=service.event(db,workspace,booking['event_id'])
            if not service.booking_access(db,booking,event,user,snap,workspace):raise HTTPException(404,'Запись недоступна.')
            fingerprint,old=service.replay(db,user,body,'change:'+id)
            if old:return old
            version(booking,body.version)
            if body.action=='cancel':
                if user['role'] not in FAMILY_ROLES and not service.can_manage(db,event,user,snap,workspace):raise HTTPException(403,'Учитель сообщает об изменении времени администрации.')
                if booking['status']!='scheduled':raise HTTPException(409,'Эту запись уже нельзя отменить.')
                if user['role'] in FAMILY_ROLES and now()>=datetime.fromisoformat(booking['starts_at'])-timedelta(minutes=event['definition']['cancelMinutes']):raise HTTPException(409,'Срок самостоятельной отмены прошёл. Обратитесь в школу.')
                if user['role'] not in FAMILY_ROLES and not body.reason:raise HTTPException(422,'Укажите причину отмены.')
                service.cancel_booking(db,booking,event,user,snap,workspace,body.reason)
                status='cancelled'
            else:
                if user['role'] in FAMILY_ROLES:raise HTTPException(403,'Итог встречи отмечает ведущий или администрация.')
                if not service.markable(booking,event):raise HTTPException(409,'Итог доступен только для начавшейся действующей встречи.')
                document=json.loads(booking['document']);document['updated']=utc(now())
                db.execute('UPDATE meeting_bookings SET status=?,version=version+1,document=? WHERE id=?',(body.action,encoded(document),id))
                if body.action!='scheduled':db.execute("UPDATE meeting_reminders SET status='skipped' WHERE booking_id=? AND status='pending'",(id,))
                service.history(db,event,user,body.action,dict(version=booking['version']+1),id);status=body.action
                if owner:=service.booking_owner(db,booking,snap,workspace):service.notifications.deliver(db,[owner],workspace,booking['version']+1,'meeting.updated',target=service.notice(id,event,document,booking['starts_at'],'Итог встречи отмечен'))
            return service.remember(db,user,body,fingerprint,dict(id=id,status=status,version=booking['version']+1))

    @app.get('/api/meetings/bookings/{id}/history')
    def history(id:str,request:Request,workspace:Workspace='school'):
        with service.context(request,workspace) as (db,user,snap):
            booking=service.booking(db,workspace,id);event=service.event(db,workspace,booking['event_id'])
            if not service.booking_access(db,booking,event,user,snap,workspace):raise HTTPException(404,'Запись недоступна.')
            return [dict(action=r['action'],created=r['created'],**json.loads(r['document'])) for r in db.execute('SELECT action,created,document FROM meeting_history WHERE booking_id=? ORDER BY seq DESC LIMIT 30',(id,))]

    @app.get('/api/meetings/events/{id}/export')
    def export(id:str,request:Request,workspace:Workspace='school'):
        with service.context(request,workspace) as (db,user,snap):
            event=service.event(db,workspace,id);hosts=service.require_event(db,event,user,snap,workspace)
            if user['role'] in FAMILY_ROLES:raise HTTPException(403,'Выгрузка списка предназначена для ведущего и администрации.')
            output=tempfile.SpooledTemporaryFile(max_size=1024*1024,mode='w+',encoding='utf-8',newline='');output.write('\ufeff');writer=csv.writer(output)
            def cell(v):
                text=str(v or '');return "'"+text if text.lstrip().startswith(('=','+','-','@')) else text
            writer.writerow(['Начало (время школы)','Окончание','Ведущий','Родитель','Ученик','Статус','Место','Комментарий','Причина отмены','Причина последнего переноса'])
            for booking in db.execute('SELECT * FROM meeting_bookings WHERE event_id=? AND host_id IN (SELECT value FROM json_each(?)) ORDER BY starts_at,id',(id,encoded(list(hosts)))):
                d=json.loads(booking['document']);writer.writerow([cell(v) for v in [datetime.fromisoformat(booking['starts_at']).astimezone(ZONE).isoformat(),datetime.fromisoformat(booking['ends_at']).astimezone(ZONE).isoformat(),d['hostName'],d['parentName'],d['studentName'],booking['status'],d['roomName'] or d['location'],d['note'],d['reason'],d.get('rescheduleReason','')]])
            output.seek(0)
            def chunks():
                try:
                    while chunk:=output.read(65536):yield chunk
                finally:output.close()
            return StreamingResponse(chunks(),media_type='text/csv; charset=utf-8',headers={'Content-Disposition':'attachment; filename="meetings.csv"','Cache-Control':'no-store'},background=BackgroundTask(output.close))
