"""Admin-controlled meeting availability and private family appointments."""
from contextlib import closing, contextmanager
from datetime import datetime, timedelta
import hashlib
import json
import sqlite3
from uuid import uuid4

from fastapi import HTTPException
from .access import now
from .notifications import encoded, permission_key
from .portal_cache import PortalSnapshots
from .messages import Directory
from .school_forms import grant_ids, enabled
from .meeting_models import utc
from .meeting_availability import Occupancy, ZONE


def tables(db):
    db.executescript('''
    CREATE TABLE IF NOT EXISTS meeting_events (
      seq INTEGER PRIMARY KEY AUTOINCREMENT,id TEXT UNIQUE,workspace TEXT,version INTEGER,
      status TEXT,created TEXT,updated TEXT,document TEXT);
    CREATE INDEX IF NOT EXISTS meeting_event_space ON meeting_events(workspace,seq);
    CREATE TABLE IF NOT EXISTS meeting_hosts (
      event_id TEXT,account_id TEXT,permissions TEXT,name TEXT,document TEXT,PRIMARY KEY(event_id,account_id));
    CREATE INDEX IF NOT EXISTS meeting_host_owner ON meeting_hosts(account_id,event_id);
    CREATE TABLE IF NOT EXISTS meeting_slots (
      id TEXT PRIMARY KEY,event_id TEXT,workspace TEXT,host_id TEXT,teacher_id TEXT,room_id TEXT,
      starts_at TEXT,ends_at TEXT,version INTEGER,status TEXT,reason TEXT);
    CREATE INDEX IF NOT EXISTS meeting_slot_event ON meeting_slots(event_id,starts_at,id);
    CREATE INDEX IF NOT EXISTS meeting_slot_teacher ON meeting_slots(workspace,teacher_id,status,starts_at,ends_at);
    CREATE INDEX IF NOT EXISTS meeting_slot_room ON meeting_slots(workspace,room_id,status,starts_at,ends_at);
    CREATE INDEX IF NOT EXISTS meeting_slot_host ON meeting_slots(workspace,host_id,status,starts_at,ends_at);
    CREATE TABLE IF NOT EXISTS meeting_bookings (
      id TEXT PRIMARY KEY,event_id TEXT,slot_id TEXT,workspace TEXT,parent_id TEXT,student_id TEXT,
      host_id TEXT,permissions TEXT,starts_at TEXT,ends_at TEXT,status TEXT,version INTEGER,document TEXT);
    CREATE UNIQUE INDEX IF NOT EXISTS meeting_booked_slot ON meeting_bookings(slot_id) WHERE status!='cancelled';
    CREATE UNIQUE INDEX IF NOT EXISTS meeting_booked_child ON meeting_bookings(event_id,host_id,student_id) WHERE status!='cancelled';
    CREATE INDEX IF NOT EXISTS meeting_parent_time ON meeting_bookings(workspace,parent_id,status,starts_at,ends_at);
    CREATE INDEX IF NOT EXISTS meeting_child_time ON meeting_bookings(workspace,student_id,status,starts_at,ends_at);
    CREATE INDEX IF NOT EXISTS meeting_host_time ON meeting_bookings(workspace,host_id,starts_at,id);
    CREATE INDEX IF NOT EXISTS meeting_booking_event ON meeting_bookings(event_id,starts_at,id);
    CREATE TABLE IF NOT EXISTS meeting_history (
      seq INTEGER PRIMARY KEY AUTOINCREMENT,event_id TEXT,booking_id TEXT,created TEXT,actor_id TEXT,action TEXT,document TEXT);
    CREATE INDEX IF NOT EXISTS meeting_history_owner ON meeting_history(event_id,booking_id,seq);
    CREATE TABLE IF NOT EXISTS meeting_reminders (booking_id TEXT PRIMARY KEY,due TEXT,status TEXT);
    CREATE INDEX IF NOT EXISTS meeting_reminder_due ON meeting_reminders(status,due,booking_id);
    CREATE TABLE IF NOT EXISTS meeting_requests (
      seq INTEGER PRIMARY KEY AUTOINCREMENT,user_id TEXT,request_id TEXT,fingerprint TEXT,result TEXT,UNIQUE(user_id,request_id));
    CREATE INDEX IF NOT EXISTS meeting_request_owner ON meeting_requests(user_id,seq);
    ''')
    from .meeting_waitlist import tables as waitlist_tables
    waitlist_tables(db)


def version(item,expected):
    if item['version']!=expected:raise HTTPException(409,'Запись изменилась. Обновите данные перед действием.')


class Meetings:
    def __init__(self,access,notifications,stamp):
        self.access,self.notifications=access,notifications
        self.snapshots=PortalSnapshots(access.read,stamp)

    def prepare(self):
        with closing(self.access.read_connect()) as db:
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='meeting_waitlist_ready'").fetchone():return
        with self.access.lock,self.access.connect() as db:tables(db);self.notifications.tables(db)

    @contextmanager
    def context(self,request,workspace,write=False):
        user=request.state.user
        if not user or user['role'] not in ['admin','teacher','staff','editor','parent']:raise HTTPException(403,'Для встреч нужен аккаунт родителя или сотрудника.')
        snap=self.snapshots.get(workspace)
        if not enabled(user,snap,workspace):raise HTTPException(403,'Нет доступа к встречам этого пространства.')
        self.prepare()
        def check(db):
            account=db.execute('SELECT * FROM accounts WHERE id=? AND active=1',(user['id'],)).fetchone()
            revision=db.execute('SELECT revision FROM workspaces WHERE id=?',(workspace,)).fetchone()
            if not account or self.access.public(account)!=user or not revision or revision[0]!=snap.state.revision:raise HTTPException(409,'Права или школьные данные изменились. Обновите страницу.')
            db.row_factory=sqlite3.Row
        if write:
            with self.access.lock,self.access.connect() as db:
                db.execute('BEGIN IMMEDIATE');check(db);yield db,user,snap
        else:
            with closing(self.access.read_connect()) as db:
                db.execute('BEGIN');check(db);yield db,user,snap

    def admin(self,user):
        if user['role']!='admin':raise HTTPException(403,'Даты, ведущих и интервалы назначает администрация.')

    def event(self,db,workspace,id):
        row=db.execute('SELECT * FROM meeting_events WHERE id=? AND workspace=?',(id,workspace)).fetchone()
        if not row:raise HTTPException(404,'Мероприятие не найдено.')
        result=dict(row);result['definition']=json.loads(result.pop('document'));return result

    def hosts(self,db,event,snap,workspace):
        if '_hosts' in event:return event['_hosts']
        result={}
        for row in db.execute('SELECT * FROM meeting_hosts WHERE event_id=?',(event['id'],)):
            user_row=db.execute('SELECT * FROM accounts WHERE id=? AND active=1',(row['account_id'],)).fetchone()
            user=self.access.public(user_row) if user_row else None
            host=json.loads(row['document']);host.update(name=row['name'],permissions=row['permissions'])
            host['current']=bool(user and enabled(user,snap,workspace) and permission_key(user)==row['permissions'] and (not host['teacherId'] or host['teacherId'] in grant_ids(user,workspace,'teacher') & snap.entities['teacher'].keys()) and (not host['roomId'] or host['roomId'] in snap.entities['room']) and set(host['classIds'])<=snap.entities['class_'].keys())
            host['user']=user;result[host['accountId']]=host
        event['_hosts']=result
        return result

    def eligible_pupils(self,host,directory):
        if not host['current']:return set()
        if host['scope']=='classes':return {s.id for s in directory.students.values() if s.classId in host['classIds']}
        return set().union(*(directory.members[g] for g in directory.teacher_groups.get(host['teacherId'],())))

    def parent_pupils(self,user,snap,workspace):
        return {id for id in grant_ids(user,workspace,'student') if id in snap.entities['student'] and snap.entities['student'][id].active} if user['role']=='parent' else set()

    def visible_hosts(self,db,event,user,snap,workspace):
        hosts=self.hosts(db,event,snap,workspace)
        if user['role']=='admin':return hosts
        if user['role']!='parent':return {id:h for id,h in hosts.items() if id==user['id'] and h['current']}
        if event['status']=='draft':return {}
        directory=Directory(snap.state,workspace);pupils=self.parent_pupils(user,snap,workspace)
        return {id:h for id,h in hosts.items() if pupils & self.eligible_pupils(h,directory)}

    def require_event(self,db,event,user,snap,workspace):
        hosts=self.visible_hosts(db,event,user,snap,workspace)
        if not hosts and user['role']!='admin':raise HTTPException(404,'Мероприятие недоступно.')
        return hosts

    def install_hosts(self,db,event,definition,snap,workspace):
        db.execute('DELETE FROM meeting_hosts WHERE event_id=?',(event['id'],))
        for host in definition['hosts']:
            row=db.execute('SELECT * FROM accounts WHERE id=? AND active=1',(host['accountId'],)).fetchone()
            user=self.access.public(row) if row else None
            if not user or user['role'] not in ['admin','teacher','staff','editor'] or not enabled(user,snap,workspace):raise HTTPException(422,'Выберите действующий аккаунт ведущего.')
            if user['role']=='teacher' and not host['teacherId']:raise HTTPException(422,'Для аккаунта учителя укажите преподавателя, чтобы проверить занятость.')
            if host['teacherId'] and (user['role']!='teacher' or host['teacherId'] not in grant_ids(user,workspace,'teacher') & snap.entities['teacher'].keys()):raise HTTPException(422,'Преподаватель не связан с этим аккаунтом.')
            if host['scope']=='teaching' and user['role']!='teacher':raise HTTPException(422,'Для сотрудника укажите доступные ему классы этого мероприятия.')
            if set(host['classIds'])-snap.entities['class_'].keys() or host['roomId'] and host['roomId'] not in snap.entities['room']:raise HTTPException(422,'Выберите существующие классы и кабинет.')
            db.execute('INSERT INTO meeting_hosts VALUES (?,?,?,?,?)',(event['id'],user['id'],permission_key(user),user['name'],encoded(host)))

    def replay(self,db,user,body,operation):
        fingerprint=hashlib.sha256(encoded([operation,body.model_dump(mode='json')]).encode()).hexdigest()
        row=db.execute('SELECT fingerprint,result FROM meeting_requests WHERE user_id=? AND request_id=?',(user['id'],body.requestId)).fetchone()
        if row:
            if row['fingerprint']!=fingerprint:raise HTTPException(409,'Идентификатор уже использован для другого действия.')
            return fingerprint,json.loads(row['result'])
        return fingerprint,None

    def remember(self,db,user,body,fingerprint,result):
        db.execute('INSERT INTO meeting_requests(user_id,request_id,fingerprint,result) VALUES (?,?,?,?)',(user['id'],body.requestId,fingerprint,encoded(result)))
        db.execute('DELETE FROM meeting_requests WHERE user_id=? AND seq NOT IN (SELECT seq FROM meeting_requests WHERE user_id=? ORDER BY seq DESC LIMIT 100)',(user['id'],user['id']))
        return result

    def history(self,db,event,user,action,document,booking=''):
        db.execute('INSERT INTO meeting_history(event_id,booking_id,created,actor_id,action,document) VALUES (?,?,?,?,?,?)',(event['id'],booking,utc(now()),user['id'],action,encoded(document)))

    def save_event(self,db,event,user,action,detail=None):
        event['version']+=1;event['updated']=utc(now())
        db.execute('UPDATE meeting_events SET version=?,status=?,updated=?,document=? WHERE id=?',(event['version'],event['status'],event['updated'],encoded(event['definition']),event['id']))
        self.history(db,event,user,action,detail or dict(version=event['version'],status=event['status']))
        return dict(id=event['id'],version=event['version'],status=event['status'])

    def note_event(self,db,event,user,action,detail):
        """Slots and transfers change availability, not the event: keep its version."""
        event['updated']=utc(now());db.execute('UPDATE meeting_events SET updated=? WHERE id=?',(event['updated'],event['id']))
        self.history(db,event,user,action,detail)
        return dict(id=event['id'],version=event['version'],status=event['status'])

    def registration_open(self,event):
        doc=event['definition'];instant=now()
        return event['status']=='open' and (not doc['bookingOpensAt'] or datetime.fromisoformat(doc['bookingOpensAt'])<=instant) and (not doc['bookingClosesAt'] or instant<datetime.fromisoformat(doc['bookingClosesAt'])) and instant<datetime.fromisoformat(doc['endsAt'])

    def slot_conflict(self,db,slot,occupancy,ignore=None):
        if (problem:=occupancy.conflict(slot['starts_at'],slot['ends_at'],slot['teacher_id'],slot['room_id'])):return problem
        for column,value in [('host_id',slot['host_id']),('teacher_id',slot['teacher_id']),('room_id',slot['room_id'])]:
            if not value:continue
            row=db.execute(f"SELECT 1 FROM meeting_slots s JOIN meeting_events e ON e.id=s.event_id WHERE s.workspace=? AND s.{column}=? AND s.status='open' AND e.status NOT IN ('cancelled','archived') AND s.starts_at<? AND s.ends_at>? AND s.id!=? LIMIT 1",(slot['workspace'],value,slot['ends_at'],slot['starts_at'],ignore or '')).fetchone()
            if row:return 'Ведущий или кабинет занят другим интервалом встреч.'
        return None

    def availability(self,db,event,body,snap,workspace):
        version(event,body.version)
        if event['status'] in ['cancelled','archived']:raise HTTPException(409,'Мероприятие отменено или в архиве.')
        if body.startsAt.astimezone(ZONE).date()!=body.endsAt.astimezone(ZONE).date():raise HTTPException(422,'Укажите интервалы одного календарного дня по времени школы.')
        if body.startsAt<datetime.fromisoformat(event['definition']['startsAt']) or body.endsAt>datetime.fromisoformat(event['definition']['endsAt']):raise HTTPException(422,'Интервалы должны входить в период мероприятия.')
        hosts=self.hosts(db,event,snap,workspace)
        if set(body.hostIds)-hosts.keys():raise HTTPException(422,'Выберите ведущих этого мероприятия.')
        occupancy=Occupancy(snap);slots=[];bookings=[];conflicts=[];proposed={};skipped=0
        def proposal_conflict(slot):
            found=False
            for column in ['host_id','teacher_id','room_id']:
                value=slot[column]
                if not value:continue
                intervals=proposed.setdefault((column,value),[])
                found=found or any(left<slot['ends_at'] and slot['starts_at']<right for left,right in intervals)
                intervals.append((slot['starts_at'],slot['ends_at']))
            return found
        if body.action=='generate':
            duration=timedelta(minutes=event['definition']['slotMinutes']);step=duration+timedelta(minutes=event['definition']['gapMinutes'])
            for id in body.hostIds:
                host=hosts[id]
                if not host['current']:raise HTTPException(422,'Доступ ведущего изменился; проверьте назначение.')
                instant=body.startsAt
                while instant+duration<=body.endsAt:
                    # Intervals that already started are skipped and counted, not a reason to refuse the range.
                    if instant<=now():skipped+=1;instant+=step;continue
                    slot=dict(id='',event_id=event['id'],workspace=workspace,host_id=id,teacher_id=host['teacherId'],room_id=host['roomId'],starts_at=utc(instant),ends_at=utc(instant+duration),version=0,status='open',reason='')
                    if problem:=self.slot_conflict(db,slot,occupancy):conflicts.append(dict(host=host['name'],startsAt=slot['starts_at'],reason=problem))
                    # Newly proposed slots may conflict with each other even before insertion.
                    if proposal_conflict(slot):conflicts.append(dict(host=host['name'],startsAt=slot['starts_at'],reason='В выбранных интервалах пересекается ведущий или кабинет.'))
                    slots.append(slot);instant+=step
                    if len(slots)>5000:raise HTTPException(422,'За одно действие можно добавить до 5000 интервалов.')
            if not slots:raise HTTPException(422,'Все интервалы выбранного времени уже начались или прошли.' if skipped else 'В выбранное время не помещается ни один полный интервал.')
            count=db.execute('SELECT count(*) FROM meeting_slots WHERE event_id=?',(event['id'],)).fetchone()[0]
            if count+len(slots)>20000:raise HTTPException(422,'На мероприятие допускается до 20 000 интервалов.')
        else:
            rows=db.execute('SELECT * FROM meeting_slots WHERE event_id=? AND host_id IN (SELECT value FROM json_each(?)) AND starts_at<? AND ends_at>? ORDER BY starts_at,id',(event['id'],encoded(body.hostIds),utc(body.endsAt),utc(body.startsAt)))
            found=[dict(r) for r in rows if r['status']==('open' if body.action=='block' else 'blocked')];instant=now()
            # Started and past intervals (and meetings in progress) are left alone and only counted.
            slots=[r for r in found if datetime.fromisoformat(r['starts_at'])>instant];skipped=len(found)-len(slots)
            for slot in slots:
                if body.action=='unblock':
                    if not hosts[slot['host_id']]['current']:raise HTTPException(422,'Нельзя открыть интервал недоступного ведущего.')
                    if problem:=self.slot_conflict(db,slot,occupancy,slot['id']):conflicts.append(dict(host=hosts[slot['host_id']]['name'],startsAt=slot['starts_at'],reason=problem))
                row=db.execute("SELECT id,version,status FROM meeting_bookings WHERE slot_id=? AND status!='cancelled'",(slot['id'],)).fetchone()
                if body.action=='block' and row and row['status']!='scheduled':raise HTTPException(422,'Отмеченные встречи нельзя отменить через интервалы.')
                if row:bookings.append(dict(row))
            if body.action=='unblock':
                for slot in slots:
                    if proposal_conflict(slot):conflicts.append(dict(host=hosts[slot['host_id']]['name'],startsAt=slot['starts_at'],reason='В выбранных интервалах пересекается ведущий или кабинет.'))
        offers=[]
        if body.action=='block':
            offers=[dict(r) for r in db.execute("SELECT id,version,expires_at FROM meeting_waitlist WHERE event_id=? AND status='offered' AND slot_id IN (SELECT value FROM json_each(?)) ORDER BY id",(event['id'],encoded([s['id'] for s in slots])))]
        # Calendar conflicts are recomputed here at every call, so the school revision is not part of the hash.
        key=hashlib.sha256(encoded([body.action,slots,bookings,offers,conflicts,skipped]).encode()).hexdigest()
        return dict(affectedSlots=len(slots),bookingsToCancel=len(bookings),offersToRevoke=len(offers),skippedPast=skipped,conflicts=conflicts[:30],conflictCount=len(conflicts),previewHash=key),slots,bookings

    def booking_owner(self,db,booking,snap,workspace):
        row=db.execute('SELECT * FROM accounts WHERE id=? AND active=1',(booking['parent_id'],)).fetchone()
        user=self.access.public(row) if row else None
        return user if user and permission_key(user)==booking['permissions'] and booking['student_id'] in self.parent_pupils(user,snap,workspace) else None

    def booking(self,db,workspace,id):
        row=db.execute('SELECT * FROM meeting_bookings WHERE id=? AND workspace=?',(id,workspace)).fetchone()
        if not row:raise HTTPException(404,'Запись на встречу не найдена.')
        return dict(row)

    def booking_access(self,db,booking,event,user,snap,workspace):
        if user['role']=='admin':return True
        if user['role']=='parent':return booking['parent_id']==user['id'] and bool(self.booking_owner(db,booking,snap,workspace))
        host=self.hosts(db,event,snap,workspace).get(user['id'])
        return bool(booking['host_id']==user['id'] and host and host['current'])

    def booking_public(self,booking,event,user):
        doc=json.loads(booking['document'])
        doc['canReschedule']=user['role']=='admin' and booking['status']=='scheduled' and event['status'] in ['open','closed'] and now()<datetime.fromisoformat(booking['starts_at'])
        return doc|dict(id=booking['id'],eventId=event['id'],eventTitle=event['definition']['title'],status=booking['status'],version=booking['version'],startsAt=booking['starts_at'],endsAt=booking['ends_at'],canCancel=booking['status']=='scheduled' and (user['role']=='admin' or user['role']=='parent' and now()<datetime.fromisoformat(booking['starts_at'])-timedelta(minutes=event['definition']['cancelMinutes'])),canMark=user['role']!='parent' and booking['status']!='cancelled' and event['status'] not in ['cancelled','archived'] and now()>=datetime.fromisoformat(booking['starts_at']))

    def cancel_booking(self,db,booking,event,user,snap,workspace,reason,notify=True):
        document=json.loads(booking['document']);document.update(reason=reason,updated=utc(now()))
        db.execute("UPDATE meeting_bookings SET status='cancelled',version=version+1,document=? WHERE id=?",(encoded(document),booking['id']))
        db.execute("UPDATE meeting_slots SET version=version+1 WHERE id=?",(booking['slot_id'],))
        db.execute("UPDATE meeting_reminders SET status='cancelled' WHERE booking_id=? AND status='pending'",(booking['id'],))
        self.history(db,event,user,'cancelled',dict(reason=reason,version=booking['version']+1),booking['id'])
        if notify:
            recipients=[];owner=self.booking_owner(db,booking,snap,workspace);host=self.hosts(db,event,snap,workspace).get(booking['host_id'])
            if owner:recipients.append(owner)
            if host and host['current']:recipients.append(host['user'])
            self.notifications.deliver(db,recipients,workspace,booking['version']+1,'meeting.cancelled')

    def register(self,app,Workspace):
        from .meeting_routes import register
        from .meeting_reschedule import register as register_reschedule
        from .meeting_reminders import MeetingReminders
        register(self,app,Workspace)
        register_reschedule(self,app,Workspace)
        from .meeting_waitlist import register as register_waitlist
        register_waitlist(self,app,Workspace)
        self.reminders=MeetingReminders(self);self.reminders.register(app)
