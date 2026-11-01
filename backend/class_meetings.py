"""Class and parallel meetings: one form invites the current guardians of the pupils the classes had on the meeting date."""
from datetime import datetime,timedelta
import json
from typing import Literal
from uuid import uuid4
from fastapi import HTTPException,Query,Request
from pydantic import AwareDatetime,Field,model_validator
from . import meetings
from .class_staff import led
from .homework_parallel import grades
from .meeting_availability import Occupancy,ZONE
from .meeting_models import Mutation,RequestId,utc
from .meetings import version
from .models import Model
from .notifications import encoded
from .school_forms import grant_ids

MARKS=3000  # pupils of one meeting; the same bound applies to one marks request


class Draft(Model):
    title: str = Field(min_length=1,max_length=160)
    description: str = Field(default='',max_length=2000)
    classIds: list[str] = Field(default_factory=list,max_length=200)
    grade: str | None = Field(default=None,pattern=r'^\d{1,2}$')
    startsAt: AwareDatetime
    endsAt: AwareDatetime
    roomId: str | None = Field(default=None,max_length=100)
    location: str = Field(default='',max_length=300)

    @model_validator(mode='after')
    def valid(self):
        if bool(self.classIds)==bool(self.grade) or len(set(self.classIds))!=len(self.classIds):raise ValueError('Выберите классы или одну параллель.')
        if self.startsAt.second or self.startsAt.microsecond or self.endsAt.second or self.endsAt.microsecond:raise ValueError('Указывайте время с точностью до минуты.')
        if self.endsAt<=self.startsAt or self.endsAt-self.startsAt>timedelta(hours=6):raise ValueError('Собрание длится от минуты до 6 часов.')
        if self.startsAt.astimezone(ZONE).date()!=self.endsAt.astimezone(ZONE).date():raise ValueError('Собрание проходит в один день по времени школы.')
        if not self.roomId and not self.location:raise ValueError('Укажите кабинет или место собрания.')
        return self


class Preview(Draft):
    id: str | None = Field(default=None,max_length=100)


class Create(Draft,RequestId):
    pass


class Edit(Draft,Mutation):
    pass


class Cancel(Mutation):
    reason: str = Field(min_length=1,max_length=500)


class Sync(Mutation):
    pass


class Mark(Model):
    studentId: str = Field(min_length=1,max_length=100)
    mark: Literal['','present','absent']


class Marks(Mutation):
    marks: list[Mark] = Field(min_length=1,max_length=MARKS)

    @model_validator(mode='after')
    def valid(self):
        if len({m.studentId for m in self.marks})!=len(self.marks):raise ValueError('Не повторяйте учеников.')
        return self


def tables(db):
    db.executescript('''
    CREATE TABLE IF NOT EXISTS class_meetings (
      seq INTEGER PRIMARY KEY AUTOINCREMENT,id TEXT UNIQUE,workspace TEXT,version INTEGER,status TEXT,starts_at TEXT,ends_at TEXT,
      room_id TEXT,teacher_ids TEXT,class_ids TEXT,created TEXT,updated TEXT,document TEXT);
    CREATE INDEX IF NOT EXISTS class_meetings_time ON class_meetings(workspace,status,starts_at,ends_at);
    CREATE TABLE IF NOT EXISTS class_meeting_invitees (
      meeting_id TEXT,student_id TEXT,class_id TEXT,student_name TEXT,mark TEXT NOT NULL DEFAULT '',marked_by TEXT,marked_at TEXT,PRIMARY KEY(meeting_id,student_id));
    CREATE INDEX IF NOT EXISTS class_meeting_invitee_student ON class_meeting_invitees(student_id,meeting_id);
    CREATE INDEX IF NOT EXISTS class_meetings_ready ON class_meetings(workspace,seq);
    ''')


def classes_of(body,snap):
    """The classes a form names: a parallel is every class whose name starts with that number."""
    if body.grade:found=[c for c in snap.state.classes if body.grade in grades([c.name])]
    else:
        found=[c for c in snap.state.classes if c.id in set(body.classIds)]
        if len(found)!=len(body.classIds):found=[]
    if not found:raise HTTPException(422,'Выберите существующие классы или параллель.')
    return found


def may_create(user,class_ids,snap,workspace):
    """An administrator for any class; a class teacher only when they lead every chosen class."""
    return user['role']=='admin' or user['role']=='teacher' and bool(class_ids) and set(class_ids)<=led(user,snap,workspace)


def can_manage(user,row,snap,workspace):
    """By the class teacher now, not the organiser: a replaced class teacher loses the meeting and the new one gains it."""
    return may_create(user,json.loads(row['class_ids']),snap,workspace)


def roster(snap,class_ids,day):
    """Pupils of the classes on that date, by the dated rosters of the timetable history."""
    view=snap.history.context(day)[0]
    return [(s.id,s.classId,s.name) for s in view.students if s.active and s.classId in class_ids]


def conflicts(service,db,workspace,body,teacher_ids,snap,occupancy,ignore=''):
    """Lessons, absences, room bookings, consultation slots and other class meetings that touch the time, the room or the class teachers."""
    found=[];names={t.id:t.name for t in snap.state.teachers}
    for teacher in teacher_ids or [None]:
        slot=dict(event_id='',workspace=workspace,host_id='',teacher_id=teacher,room_id=body.roomId,starts_at=utc(body.startsAt),ends_at=utc(body.endsAt))
        if problem:=service.slot_conflict(db,slot,occupancy,ignore_class=ignore):
            text=f'{names[teacher]}: {problem}' if teacher in names else problem
            if text not in found:found.append(text)
    return found


def guardians(service,db,workspace,ids):
    return service.notifications.granted_users(db,workspace,'student',set(ids),['parent'])


def place(document):return document['roomName'] or document['location']


def notify(service,db,workspace,row,document,users,action,prefix,names):
    """Tell guardians. The entry names only the account's own children among `names`; a reason or description never goes into the title."""
    if not users:return
    local=datetime.fromisoformat(row['starts_at']).astimezone(ZONE);title=f"{prefix}: «{document['title']}», {local:%d.%m %H:%M}, {place(document)}"
    def target(user):
        own=grant_ids(user,workspace,'student')
        return dict(module='class_meetings',recordId=row['id'],day=local.date().isoformat(),students=[dict(id=id,name=name) for id,name in names.items() if id in own],title=title)
    service.notifications.deliver(db,users,workspace,row['version'],action,topic=f"class-meeting:{workspace}:{row['id']}",target=target)


def options(user,snap,workspace):
    """What the form offers: an administrator every class and parallel, a class teacher the classes they lead."""
    own=led(user,snap,workspace);admin=user['role']=='admin'
    classes=[dict(id=c.id,name=c.name) for c in snap.state.classes if admin or c.id in own]
    parallels={}
    if admin:
        for c in snap.state.classes:
            for grade in grades([c.name]):parallels.setdefault(grade,[]).append(c.id)
    return dict(canCreate=admin or bool(own),classes=classes,parallels=[dict(grade=g,classIds=ids) for g,ids in sorted(parallels.items(),key=lambda x:int(x[0]))])


def register(service,app,Workspace):
    base='/api/meetings/class-meetings'

    def gate(user):
        if user['role'] not in ('admin','teacher','parent'):raise HTTPException(403,'Собрания классов доступны администрации, классным руководителям и родителям.')

    def load(db,workspace,id):
        row=db.execute('SELECT * FROM class_meetings WHERE id=? AND workspace=?',(id,workspace)).fetchone()
        if not row:raise HTTPException(404,'Собрание не найдено.')
        return dict(row)

    def invitees(db,id):return [dict(r) for r in db.execute('SELECT * FROM class_meeting_invitees WHERE meeting_id=? ORDER BY student_name,student_id',(id,))]

    def started(row):return datetime.fromisoformat(row['starts_at'])<=meetings.now()

    def plan(db,user,body,snap,workspace,ignore=''):
        """Classes, dated roster, teachers, guardians and conflicts of a form; shared by the preview and the writes."""
        classes=classes_of(body,snap);ids=[c.id for c in classes]
        if not may_create(user,ids,snap,workspace):raise HTTPException(403,'Собрание класса назначает администратор или классный руководитель этого класса.')
        day=body.startsAt.astimezone(ZONE).date();pupils=roster(snap,set(ids),day)
        teachers=sorted({c.classTeacherId for c in classes if c.classTeacherId})
        if body.roomId and body.roomId not in snap.entities['room']:raise HTTPException(422,'Выберите существующий кабинет.')
        if not pupils:raise HTTPException(422,'В выбранных классах нет учеников на эту дату.')
        if len(pupils)>MARKS:raise HTTPException(422,f'В собрании может быть до {MARKS} учеников.')
        occupancy=Occupancy(snap);people=guardians(service,db,workspace,[p[0] for p in pupils])
        covered={i for u in people for i in grant_ids(u,workspace,'student')}
        closed=next((name for id in ids if (name:=occupancy.closed(day,id))),None)
        return dict(classes=classes,ids=ids,pupils=pupils,teachers=teachers,people=people,without=[p[2] for p in pupils if p[0] not in covered],closed=closed,conflicts=conflicts(service,db,workspace,body,teachers,snap,occupancy,ignore))

    def document_of(body,plan,snap,user):
        room=snap.entities['room'][body.roomId].name if body.roomId in snap.entities['room'] else ''
        return dict(title=body.title,description=body.description,location=body.location,roomName=room,grade=body.grade,classNames=[c.name for c in plan['classes']],organizerName=user['name'],reason='')

    def summary(plan):
        return dict(classes=[dict(id=c.id,name=c.name) for c in plan['classes']],pupils=len(plan['pupils']),guardians=len(plan['people']),withoutGuardian=plan['without'][:30],withoutGuardianCount=len(plan['without']),conflicts=plan['conflicts'],closedDay=plan['closed'])

    @app.post(base+'/preview')
    def preview(body:Preview,request:Request,workspace:Workspace='school'):
        with service.context(request,workspace) as (db,user,snap):
            gate(user)
            if body.id and not can_manage(user,load(db,workspace,body.id),snap,workspace):raise HTTPException(404,'Собрание недоступно.')
            return summary(plan(db,user,body,snap,workspace,body.id or ''))

    @app.post(base)
    def create(body:Create,request:Request,workspace:Workspace='school'):
        with service.context(request,workspace,True) as (db,user,snap):
            gate(user);fingerprint,old=service.replay(db,user,body,'class-create:'+workspace)
            if old:return old
            if body.startsAt<=meetings.now():raise HTTPException(422,'Назначьте собрание на будущее время.')
            found=plan(db,user,body,snap,workspace)
            if found['conflicts']:raise HTTPException(409,'Есть пересечения: '+' '.join(found['conflicts']))
            id=str(uuid4());stamp=utc(meetings.now());document=document_of(body,found,snap,user)
            db.execute('INSERT INTO class_meetings(id,workspace,version,status,starts_at,ends_at,room_id,teacher_ids,class_ids,created,updated,document) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                       (id,workspace,1,'scheduled',utc(body.startsAt),utc(body.endsAt),body.roomId,encoded(found['teachers']),encoded(found['ids']),stamp,stamp,encoded(document)))
            for pupil,class_id,name in found['pupils']:
                db.execute('INSERT INTO class_meeting_invitees(meeting_id,student_id,class_id,student_name) VALUES (?,?,?,?)',(id,pupil,class_id,name))
            service.history(db,dict(id=id),user,'created',dict(version=1,pupils=len(found['pupils'])))
            row=dict(id=id,version=1,starts_at=utc(body.startsAt))
            notify(service,db,workspace,row,document,found['people'],'meeting.class_invited','Собрание',{p[0]:p[2] for p in found['pupils']})
            return service.remember(db,user,body,fingerprint,dict(id=id,version=1,pupils=len(found['pupils']),invited=len(found['people']),withoutGuardian=found['without'][:30],withoutGuardianCount=len(found['without'])))

    def family_pupils(db,id,user,snap,workspace):
        own=service.parent_pupils(user,snap,workspace)
        return [r for r in invitees(db,id) if r['student_id'] in own]

    def family_item(row,kids):
        document=json.loads(row['document'])
        return dict(id=row['id'],version=row['version'],status=row['status'],title=document['title'],description=document['description'],startsAt=row['starts_at'],endsAt=row['ends_at'],place=place(document),reason=document['reason'],
                    studentIds=[k['student_id'] for k in kids],students=[dict(id=k['student_id'],name=k['student_name']) for k in kids])

    def flags(row):
        ahead=row['status']=='scheduled'
        return dict(canEdit=ahead and not started(row),canCancel=ahead and not started(row),canMark=ahead and started(row))

    @app.get(base)
    def listing(request:Request,workspace:Workspace='school',before:int=Query(default=9223372036854775807,ge=1),limit:int=Query(default=20,ge=1,le=50)):
        with service.context(request,workspace) as (db,user,snap):
            gate(user);clause='';args=[workspace,before]
            if user['role']=='teacher':
                clause=' AND NOT EXISTS (SELECT 1 FROM json_each(class_ids) WHERE value NOT IN (SELECT value FROM json_each(?)))';args.append(encoded(sorted(led(user,snap,workspace))))
            elif user['role']=='parent':
                clause=' AND id IN (SELECT meeting_id FROM class_meeting_invitees WHERE student_id IN (SELECT value FROM json_each(?)))';args.append(encoded(sorted(service.parent_pupils(user,snap,workspace))))
            rows=[dict(r) for r in db.execute('SELECT * FROM class_meetings WHERE workspace=? AND seq<?'+clause+' ORDER BY seq DESC LIMIT ?',(*args,limit+1))];items=[]
            counts={r[0]:(r[1],r[2]) for r in db.execute('SELECT meeting_id,count(*),count(CASE WHEN mark!=\'\' THEN 1 END) FROM class_meeting_invitees WHERE meeting_id IN (SELECT value FROM json_each(?)) GROUP BY meeting_id',(encoded([r['id'] for r in rows[:limit]]),))}
            for row in rows[:limit]:
                if user['role']=='parent':items.append(family_item(row,family_pupils(db,row['id'],user,snap,workspace)));continue
                document=json.loads(row['document'])
                items.append(dict(id=row['id'],version=row['version'],status=row['status'],title=document['title'],startsAt=row['starts_at'],endsAt=row['ends_at'],place=place(document),classNames=document['classNames'],pupils=counts.get(row['id'],(0,0))[0],marked=counts.get(row['id'],(0,0))[1],reason=document['reason'])|flags(row))
            return dict(items=items,nextBefore=rows[limit-1]['seq'] if len(rows)>limit else None)

    @app.get(base+'/{id}')
    def detail(id:str,request:Request,workspace:Workspace='school'):
        with service.context(request,workspace) as (db,user,snap):
            gate(user);row=load(db,workspace,id)
            if user['role']=='parent':
                kids=family_pupils(db,id,user,snap,workspace)
                if not kids:raise HTTPException(404,'Собрание недоступно.')
                return family_item(row,kids)
            if not can_manage(user,row,snap,workspace):raise HTTPException(404,'Собрание недоступно.')
            document=json.loads(row['document']);people=invitees(db,id);names={}
            for guardian in guardians(service,db,workspace,[p['student_id'] for p in people]):
                for kid in grant_ids(guardian,workspace,'student'):names.setdefault(kid,[]).append(guardian['name'])
            classes={c.id:c.name for c in snap.state.classes}
            roster_rows=[dict(studentId=p['student_id'],name=p['student_name'],classId=p['class_id'],className=classes.get(p['class_id'],''),mark=p['mark'],markedAt=p['marked_at'],guardians=sorted(names.get(p['student_id'],[]))) for p in people]
            history=[dict(action=r['action'],created=r['created'],**json.loads(r['document'])) for r in db.execute("SELECT action,created,document FROM meeting_history WHERE event_id=? AND booking_id='' ORDER BY seq DESC LIMIT 30",(id,))]
            return dict(id=id,version=row['version'],status=row['status'],title=document['title'],description=document['description'],startsAt=row['starts_at'],endsAt=row['ends_at'],place=place(document),roomId=row['room_id'],location=document['location'],
                        grade=document['grade'],classIds=json.loads(row['class_ids']),classNames=document['classNames'],organizerName=document['organizerName'],reason=document['reason'],
                        roster=roster_rows,withoutGuardian=[r['name'] for r in roster_rows if not r['guardians']],history=history)|flags(row)

    def writable(db,user,id,snap,workspace):
        """The meeting a manager may change; another account gets 404, as for a record it cannot see."""
        gate(user);row=load(db,workspace,id)
        if not can_manage(user,row,snap,workspace):raise HTTPException(404,'Собрание недоступно.')
        return row

    def current(row,body):
        version(row,body.version)
        if row['status']!='scheduled':raise HTTPException(409,'Собрание уже отменено.')

    def save(db,row,document,status=None,**columns):
        columns=dict(columns,version=row['version']+1,updated=utc(meetings.now()),document=encoded(document))|({'status':status} if status else {})
        db.execute('UPDATE class_meetings SET '+','.join(f'{k}=?' for k in columns)+' WHERE id=?',(*columns.values(),row['id']))
        return row|columns

    def everyone(db,id,extra=()):
        return [r['student_id'] for r in invitees(db,id)]+list(extra)

    @app.put(base+'/{id}')
    def edit(id:str,body:Edit,request:Request,workspace:Workspace='school'):
        with service.context(request,workspace,True) as (db,user,snap):
            row=writable(db,user,id,snap,workspace);fingerprint,old=service.replay(db,user,body,'class-edit:'+id)
            if old:return old
            current(row,body)
            if started(row):raise HTTPException(409,'Собрание уже началось: изменить время и состав нельзя.')
            if body.startsAt<=meetings.now():raise HTTPException(422,'Назначьте собрание на будущее время.')
            found=plan(db,user,body,snap,workspace,id)
            if found['conflicts']:raise HTTPException(409,'Есть пересечения: '+' '.join(found['conflicts']))
            before=dict(startsAt=row['starts_at'],endsAt=row['ends_at']);old_people=invitees(db,id);new={p[0] for p in found['pupils']}
            marked={p['student_id'] for p in old_people if p['mark']}
            for p in old_people:
                if p['student_id'] not in new and p['student_id'] not in marked:db.execute('DELETE FROM class_meeting_invitees WHERE meeting_id=? AND student_id=?',(id,p['student_id']))
            for pupil,class_id,name in found['pupils']:
                db.execute('INSERT INTO class_meeting_invitees(meeting_id,student_id,class_id,student_name) VALUES (?,?,?,?) ON CONFLICT(meeting_id,student_id) DO UPDATE SET class_id=excluded.class_id,student_name=excluded.student_name',(id,pupil,class_id,name))
            document=document_of(body,found,snap,user)
            row=save(db,row,document,room_id=body.roomId,teacher_ids=encoded(found['teachers']),class_ids=encoded(found['ids']),starts_at=utc(body.startsAt),ends_at=utc(body.endsAt))
            service.history(db,dict(id=id),user,'edited',dict(version=row['version'],before=before,after=dict(startsAt=row['starts_at'],endsAt=row['ends_at'])))
            # Everyone the meeting concerned or concerns now hears of the change, including guardians of a pupil who dropped out.
            ids=set(p['student_id'] for p in old_people)|new
            names={p['student_id']:p['student_name'] for p in old_people}|{p[0]:p[2] for p in found['pupils']}
            notify(service,db,workspace,row,document,guardians(service,db,workspace,ids),'meeting.class_changed','Собрание изменено',names)
            return service.remember(db,user,body,fingerprint,dict(id=id,version=row['version'],pupils=len(found['pupils']),invited=len(found['people']),withoutGuardian=found['without'][:30],withoutGuardianCount=len(found['without'])))

    @app.post(base+'/{id}/sync')
    def sync(id:str,body:Sync,request:Request,workspace:Workspace='school'):
        with service.context(request,workspace,True) as (db,user,snap):
            row=writable(db,user,id,snap,workspace);fingerprint,old=service.replay(db,user,body,'class-sync:'+id)
            if old:return old
            current(row,body)
            day=datetime.fromisoformat(row['starts_at']).astimezone(ZONE).date();now_list=roster(snap,set(json.loads(row['class_ids'])),day)
            have={p['student_id']:p for p in invitees(db,id)};wanted={p[0]:p for p in now_list}
            added=[wanted[i] for i in wanted if i not in have];gone=[have[i] for i in have if i not in wanted and not have[i]['mark']]
            for pupil,class_id,name in added:db.execute('INSERT INTO class_meeting_invitees(meeting_id,student_id,class_id,student_name) VALUES (?,?,?,?)',(id,pupil,class_id,name))
            for p in gone:db.execute('DELETE FROM class_meeting_invitees WHERE meeting_id=? AND student_id=?',(id,p['student_id']))
            for i,(pupil,class_id,name) in wanted.items():
                if i in have and (have[i]['class_id'],have[i]['student_name'])!=(class_id,name):db.execute('UPDATE class_meeting_invitees SET class_id=?,student_name=? WHERE meeting_id=? AND student_id=?',(class_id,name,id,pupil))
            if not added and not gone:return service.remember(db,user,body,fingerprint,dict(id=id,version=row['version'],added=0,removed=0))
            document=json.loads(row['document']);row=save(db,row,document)
            service.history(db,dict(id=id),user,'synced',dict(version=row['version'],added=len(added),removed=len(gone)))
            notify(service,db,workspace,row,document,guardians(service,db,workspace,[p[0] for p in added]),'meeting.class_invited','Собрание',{p[0]:p[2] for p in added})
            notify(service,db,workspace,row,document,guardians(service,db,workspace,[p['student_id'] for p in gone]),'meeting.class_changed','Собрание изменено',{p['student_id']:p['student_name'] for p in gone})
            return service.remember(db,user,body,fingerprint,dict(id=id,version=row['version'],added=len(added),removed=len(gone)))

    @app.post(base+'/{id}/cancel')
    def cancel(id:str,body:Cancel,request:Request,workspace:Workspace='school'):
        with service.context(request,workspace,True) as (db,user,snap):
            row=writable(db,user,id,snap,workspace);fingerprint,old=service.replay(db,user,body,'class-cancel:'+id)
            if old:return old
            current(row,body)
            if started(row):raise HTTPException(409,'Собрание уже началось: отменить его нельзя.')
            document=json.loads(row['document']);document['reason']=body.reason
            people=invitees(db,id);row=save(db,row,document,'cancelled')
            service.history(db,dict(id=id),user,'cancelled',dict(version=row['version'],reason=body.reason))
            notify(service,db,workspace,row,document,guardians(service,db,workspace,[p['student_id'] for p in people]),'meeting.class_cancelled','Собрание отменено',{p['student_id']:p['student_name'] for p in people})
            return service.remember(db,user,body,fingerprint,dict(id=id,version=row['version'],status='cancelled'))

    @app.post(base+'/{id}/marks')
    def mark(id:str,body:Marks,request:Request,workspace:Workspace='school'):
        with service.context(request,workspace,True) as (db,user,snap):
            row=writable(db,user,id,snap,workspace);fingerprint,old=service.replay(db,user,body,'class-marks:'+id)
            if old:return old
            current(row,body)
            if not started(row):raise HTTPException(409,'Посещение отмечают после начала собрания.')
            have={p['student_id']:p for p in invitees(db,id)}
            if {m.studentId for m in body.marks}-have.keys():raise HTTPException(422,'Отмечать можно только приглашённых учеников.')
            changed=[m for m in body.marks if have[m.studentId]['mark']!=m.mark];stamp=utc(meetings.now())
            if not changed:return service.remember(db,user,body,fingerprint,dict(id=id,version=row['version'],changed=0))
            for m in changed:db.execute('UPDATE class_meeting_invitees SET mark=?,marked_by=?,marked_at=? WHERE meeting_id=? AND student_id=?',(m.mark,user['id'] if m.mark else None,stamp if m.mark else None,id,m.studentId))
            row=save(db,row,json.loads(row['document']))
            service.history(db,dict(id=id),user,'marked',dict(version=row['version'],changed=len(changed)))
            return service.remember(db,user,body,fingerprint,dict(id=id,version=row['version'],changed=len(changed)))
