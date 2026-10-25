"""Assessment calendar: teachers book СОР/СОЧ dates on real lessons; class load limits and family view."""
from collections import Counter
from contextlib import closing,contextmanager
from datetime import date,timedelta
from typing import Literal
from uuid import uuid4
from zoneinfo import ZoneInfo
import copy,hashlib,json,sqlite3

from fastapi import HTTPException
from pydantic import Field,model_validator
from .access import now
from .behaviour import csv_cell
from .calendar_ops import DayContext,resolve_day
from .cycles import pattern_on
from .models import Model
from .notifications import encoded,permission_key
from .portal_cache import PortalSnapshots
from .resources import teacher_ids
from .school_forms import enabled,grant_ids

STATES={'planned':'Забронировано','published':'Опубликовано','cancelled':'Отменено'}
CATEGORIES={'summative_unit':'СОР (за раздел)','summative_term':'СОЧ (за четверть)','other':'Другое'}
# Roles the administration may name as calendar coordinators (the client menu must show the panel to all of them).
COORDINATOR_ROLES=['editor','teacher','staff']
YEARS=range(2000,2101)
def kind(id,title,short,category='other',counted=True,window=False):
    return dict(id=id,title=title,short=short,category=category,counted=counted,window=window,active=True)
DEFAULT_TYPES=[
    kind('sor','Суммативное оценивание за раздел','СОР','summative_unit'),
    kind('soch','Суммативное оценивание за четверть','СОЧ','summative_term',window=True),
    kind('control','Контрольная работа','К/р'),
    kind('exam','Экзамен','Экзамен'),
]
# The limits are an assumption until the school confirms its own rules.
DEFAULTS=dict(version=0,types=DEFAULT_TYPES,maxPerDay=2,maxPerWeek=None,windows=[],coordinators=[])
def stamp():return now().isoformat()
def today():return now().astimezone(ZoneInfo('Asia/Qyzylorda')).date()
def new_id():return uuid4().hex
def label(day):return (day if isinstance(day,date) else date.fromisoformat(day)).strftime('%d.%m.%Y')


def tables(db):
    db.executescript('''
    CREATE TABLE IF NOT EXISTS assessment_config(workspace TEXT PRIMARY KEY,version INTEGER NOT NULL,document TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS assessment_bookings(seq INTEGER PRIMARY KEY AUTOINCREMENT,id TEXT UNIQUE,workspace TEXT,version INTEGER,status TEXT,type_id TEXT,category TEXT,
        day TEXT,start TEXT,end_time TEXT,lesson_id TEXT,lesson_key TEXT,group_id TEXT,subject_id TEXT,class_ids TEXT,student_ids TEXT,whole_ids TEXT,
        author_id TEXT,author_name TEXT,created TEXT,updated TEXT,published_at TEXT,document TEXT);
    CREATE INDEX IF NOT EXISTS assessment_bookings_day ON assessment_bookings(workspace,day,seq);
    CREATE INDEX IF NOT EXISTS assessment_bookings_pair ON assessment_bookings(workspace,group_id,subject_id,day);
    CREATE UNIQUE INDEX IF NOT EXISTS assessment_bookings_slot ON assessment_bookings(workspace,day,lesson_id,group_id,subject_id) WHERE status!='cancelled';
    CREATE TABLE IF NOT EXISTS assessment_history(seq INTEGER PRIMARY KEY AUTOINCREMENT,workspace TEXT,booking_id TEXT,actor_id TEXT,actor_name TEXT,created TEXT,action TEXT,reason TEXT,document TEXT);
    CREATE INDEX IF NOT EXISTS assessment_history_booking ON assessment_history(workspace,booking_id,seq);
    CREATE TABLE IF NOT EXISTS assessment_requests(seq INTEGER PRIMARY KEY AUTOINCREMENT,user_id TEXT,request_id TEXT,fingerprint TEXT,result TEXT,UNIQUE(user_id,request_id));
    ''')


class AssessmentType(Model):
    id:str=Field(pattern=r'^[a-z0-9-]{1,40}$')
    title:str=Field(min_length=1,max_length=80)
    short:str=Field(min_length=1,max_length=12)
    category:Literal['summative_unit','summative_term','other']='other'
    counted:bool=True
    window:bool=False
    active:bool=True


class Window(Model):
    periodId:str=Field(min_length=1,max_length=64)
    start:date
    end:date
    @model_validator(mode='after')
    def ordered(self):
        if self.start>self.end:raise ValueError('Начало окна позже окончания.')
        return self


class Config(Model):
    version:int=Field(ge=0)
    reason:str=Field(min_length=1,max_length=500)
    types:list[AssessmentType]=Field(min_length=1,max_length=30)
    maxPerDay:int|None=Field(default=2,ge=1,le=10)
    maxPerWeek:int|None=Field(default=None,ge=1,le=30)
    windows:list[Window]=Field(default_factory=list,max_length=40)
    coordinatorIds:list[str]=Field(default_factory=list,max_length=50)
    @model_validator(mode='after')
    def unique(self):
        if len({t.id for t in self.types})!=len(self.types):raise ValueError('Коды типов не должны повторяться.')
        if len({w.periodId for w in self.windows})!=len(self.windows):raise ValueError('Окно для периода задаётся один раз.')
        if len(set(self.coordinatorIds))!=len(self.coordinatorIds):raise ValueError('Сотрудники не должны повторяться.')
        return self


class NewBooking(Model):
    requestId:str=Field(pattern=r'^[a-f0-9-]{36}$')
    groupId:str=Field(min_length=1,max_length=100)
    subjectId:str=Field(min_length=1,max_length=100)
    day:date
    lessonId:str=Field(min_length=1,max_length=200)
    typeId:str=Field(min_length=1,max_length=40)
    topic:str=Field(default='',max_length=200)
    note:str=Field(default='',max_length=1000)
    publish:bool=True
    overrideReason:str=Field(default='',max_length=500)


class Move(Model):
    requestId:str=Field(pattern=r'^[a-f0-9-]{36}$')
    version:int=Field(ge=1)
    day:date
    lessonId:str=Field(min_length=1,max_length=200)
    reason:str=Field(min_length=1,max_length=500)
    overrideReason:str=Field(default='',max_length=500)


class Edit(Model):
    version:int=Field(ge=1)
    typeId:str=Field(min_length=1,max_length=40)
    topic:str=Field(default='',max_length=200)
    note:str=Field(default='',max_length=1000)
    overrideReason:str=Field(default='',max_length=500)


class Publish(Model):
    version:int=Field(ge=1)


class Cancel(Model):
    version:int=Field(ge=1)
    reason:str=Field(min_length=1,max_length=500)


def group_state(state,group_id):
    """A cheap copy holding only one group's lessons.

    DatedLesson changes never carry groupId (models.py), so a change belongs to the group of its
    placement; only extra lessons name a group. The rows of resolve_day on this copy therefore match
    the full resolution for that group (checked for cancel, move, substitution, extra and class closure).
    """
    loads={w.id for w in state.workloads if w.groupId==group_id}
    places=[p for p in state.placements if p.workloadId in loads];ids={p.id for p in places}
    dated=[c for c in state.datedLessons if c.placementId in ids or (not c.placementId and c.groupId==group_id)]
    return state.model_copy(update=dict(groups=[g for g in state.groups if g.id==group_id],placements=places,datedLessons=dated,commitments=[]))


class PairLessons:
    """Per-request cache of the real dated lessons of single groups."""
    def __init__(self,state):
        self.state,self.groups,self.days=state,{},{}

    def context(self,group_id):
        if group_id not in self.groups:
            gs=group_state(self.state,group_id);self.groups[group_id]=(gs,DayContext(gs))
        return self.groups[group_id]

    def rows(self,group_id,subject_id,day):
        if (group_id,day) not in self.days:
            gs,ctx=self.context(group_id)
            self.days[group_id,day]=[r for r in resolve_day(gs,day,ctx)[0] if r['activity']=='lesson' and r['groupId']==group_id]
        return [r for r in self.days[group_id,day] if r['subjectId']==subject_id]

    def slots(self,group_id,subject_id,day):
        return [r for r in self.rows(group_id,subject_id,day) if r['status']!='cancelled']

    def find(self,group_id,subject_id,day,lesson_id):
        return next((r for r in self.rows(group_id,subject_id,day) if r['id']==lesson_id),None)


def lesson_days(pl,state,group,subject,start,end):
    """ISO dates in the range on which the group has a lesson of the subject."""
    gs=pl.context(group)[0]
    weekly={(p.day,p.cycleId) for p in gs.placements};extra={c.onDate for c in gs.datedLessons}
    result=[]
    for i in range((end-start).days+1):
        day=start+timedelta(days=i);cycle=pattern_on(state,day)
        if ((cycle is not None and (day.weekday(),cycle) in weekly) or day in extra) and pl.slots(group,subject,day):result.append(day.isoformat())
    return result


def audience(row,students):
    """Who writes the work: classes, pupils, and classes counted as a whole (whole-class groups, incomplete rosters, classes without imported pupils)."""
    classes=set(row['classIds'])
    whole=set(row['wholeClassIds'])|(classes if not row['rosterComplete'] else set())|{c for c in classes if not any(students[s].classId==c for s in row['studentIds'] if s in students)}
    return dict(class_ids=list(row['classIds']),student_ids=list(row['studentIds']),whole_ids=sorted(whole))


def class_load(items,class_id,members,pupils=None):
    """Most works any one pupil of the class writes: whole-class works count for everyone, subgroup works per pupil."""
    relevant=[b for b in items if class_id in b['class_ids']]
    whole=sum(class_id in b['whole_ids'] for b in relevant)
    counts=Counter(s for b in relevant if class_id not in b['whole_ids'] for s in b['student_ids'] if s in members and (pupils is None or s in pupils))
    return whole+max(counts.values(),default=0)


def touches(b,class_id,members,pupils):
    return class_id in b['class_ids'] and (class_id in b['whole_ids'] or pupils is None or bool(set(b['student_ids'])&pupils&members))


def check_days(*days):
    """Far dates (year 9999) would overflow the week arithmetic, so only school-sized years are accepted."""
    if any(d.year not in YEARS for d in days):raise HTTPException(422,f'Дата вне допустимого диапазона ({YEARS[0]}–{YEARS[-1]}).')


def check_window(start,end,days):
    check_days(start,end)
    if start>end:raise HTTPException(422,'Начало периода позже окончания.')
    if (end-start).days>days:raise HTTPException(422,f'Период не длиннее {days} дней.')


def link_journal(db,workspace,booking_id,assessment_id,user,key,was=None):
    """Record (or, with None, drop) the journal work a booking was entered as. The link is metadata: the booking version stays, so calendar editors never get a 409."""
    db.execute("UPDATE assessment_bookings SET document=json_set(document,'$.journal.assessmentId',?) WHERE workspace=? AND id=?",(assessment_id,workspace,booking_id))
    db.execute('INSERT INTO assessment_history(workspace,booking_id,actor_id,actor_name,created,action,reason,document) VALUES (?,?,?,?,?,?,?,?)',
               (workspace,booking_id,user['id'],user['name'],stamp(),'journal_linked' if assessment_id else 'journal_unlinked','',encoded(dict(lessonKey=key,assessmentId=assessment_id or was))))


class AssessmentCalendar:
    def __init__(self,access,notifications,stamp):
        self.access,self.notifications=access,notifications
        self.snapshots=PortalSnapshots(access.read,stamp)

    def prepare(self):
        with closing(self.access.read_connect()) as db:
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='assessment_bookings_slot'").fetchone():return
        with self.access.lock,self.access.connect() as db:tables(db);self.notifications.tables(db)

    @contextmanager
    def context(self,request,workspace,write=False):
        user=request.state.user
        if not user:raise HTTPException(403,'Раздел доступен после входа.')
        snap=self.snapshots.get(workspace)
        if not enabled(user,snap,workspace):raise HTTPException(403,'Нет доступа к этому пространству.')
        self.prepare()
        def check(db):
            row=db.execute('SELECT * FROM accounts WHERE id=? AND active=1',(user['id'],)).fetchone();rev=db.execute('SELECT revision FROM workspaces WHERE id=?',(workspace,)).fetchone()
            if not row or self.access.public(row)!=user or not rev or rev[0]!=snap.state.revision:raise HTTPException(409,'Доступ или данные школы изменились. Обновите страницу.')
            db.row_factory=sqlite3.Row
        if write:
            with self.access.lock,self.access.connect() as db:db.execute('BEGIN IMMEDIATE');check(db);yield db,user,snap
        else:
            with closing(self.access.read_connect()) as db:db.execute('BEGIN');check(db);yield db,user,snap

    def config(self,db,workspace):
        row=db.execute('SELECT version,document FROM assessment_config WHERE workspace=?',(workspace,)).fetchone()
        if not row:return copy.deepcopy(DEFAULTS)
        value=json.loads(row[1])
        return dict(version=row[0],types=[AssessmentType.model_validate(t).model_dump() for t in value['types']],maxPerDay=value.get('maxPerDay'),
                    maxPerWeek=value.get('maxPerWeek'),windows=value.get('windows',[]),coordinators=value.get('coordinators',[]))

    def coordinator(self,db,user,workspace,cfg=None):
        """Named by the administration; a change of role, grants or workspaces revokes it by itself."""
        cfg=cfg or self.config(db,workspace)
        return user['role'] in COORDINATOR_ROLES and any(c['id']==user['id'] and c['permissions']==permission_key(user) for c in cfg['coordinators'])

    def manager(self,db,user,workspace,cfg=None):
        return user['role']=='admin' or self.coordinator(db,user,workspace,cfg)

    def names(self,snap):
        """Names and current teachers per (group, subject); derived from the immutable snapshot."""
        found=snap.__dict__.get('assessment_names')
        if found is None:
            st=snap.state;teachers={t.id:t.name for t in st.teachers};by_pair={}
            for w in st.workloads:
                for t in teacher_ids(w):
                    if t in teachers:by_pair.setdefault((w.groupId,w.subjectId),{})[t]=teachers[t]
            found=snap.assessment_names=dict(subject={s.id:s.name for s in st.subjects},klass={c.id:c.name for c in st.classes},
                                             group={g.id:g.name for g in st.groups},teachers={k:', '.join(v.values()) for k,v in by_pair.items()})
        return found

    def pairs(self,user,snap,workspace,manager):
        """(group, subject) pairs the user may book: current workloads, never staff groups."""
        staff={g.id for g in snap.state.groups if g.kind=='staff'}
        if manager:return {(w.groupId,w.subjectId) for w in snap.state.workloads if w.groupId not in staff}
        if user['role']!='teacher':return set()
        ids=grant_ids(user,workspace,'teacher')&snap.entities['teacher'].keys()
        return {(w.groupId,w.subjectId) for w in snap.state.workloads if w.groupId not in staff and ids&set(teacher_ids(w))}

    def teacher_pairs(self,snap,ids):
        ids=set(ids)
        return {(w.groupId,w.subjectId) for w in snap.state.workloads if ids&set(teacher_ids(w))}

    def classes_of(self,snap,pairs):
        groups=snap.entities['group']
        return {c for g,_ in pairs if g in groups for c in groups[g].classIds}

    def staff_scope(self,db,user,snap,workspace,cfg=None):
        """('all', pairs) for managers; (visible classes, pairs) for teachers of at least one pair."""
        manager=self.manager(db,user,workspace,cfg);pairs=self.pairs(user,snap,workspace,manager)
        if manager:return 'all',pairs
        if pairs:return self.classes_of(snap,pairs),pairs
        raise HTTPException(403,'Календарь контрольных доступен учителям классов, ответственным за календарь и администрации.')

    def family(self,user,snap,workspace):
        if user['role'] not in ['parent','student']:return set()
        return {id for id in grant_ids(user,workspace,'student') if id in snap.entities['student'] and snap.entities['student'][id].active}

    def groups_of(self,snap,student_id):
        s=snap.entities['student'][student_id]
        return {g.id for g in snap.state.groups if g.kind!='staff' and (s.classId in g.classIds if g.kind=='whole' else s.id in g.studentIds)}

    def members(self,snap,group_id):
        g=snap.entities['group'].get(group_id)
        return {p.id for p in snap.state.students if p.active and g and g.kind!='staff' and (p.classId in g.classIds if g.kind=='whole' else p.id in g.studentIds)}

    def booking(self,db,workspace,id):
        row=db.execute('SELECT * FROM assessment_bookings WHERE workspace=? AND id=?',(workspace,id)).fetchone()
        if not row:raise HTTPException(404,'Работа не найдена.')
        return self.decoded(row)

    def decoded(self,row):
        return dict(row)|dict(data=json.loads(row['document']),class_ids=json.loads(row['class_ids']),student_ids=json.loads(row['student_ids']),whole_ids=json.loads(row['whole_ids']))

    def type_of(self,cfg,id,active=True):
        found=next((t for t in cfg['types'] if t['id']==id and (t['active'] or not active)),None)
        if not found:raise HTTPException(422,'Выберите действующий тип работы.')
        return found

    def type_for(self,cfg,row):
        """The type of an existing booking; a removed or renamed type falls back to the stored labels."""
        found=next((t for t in cfg['types'] if t['id']==row['type_id']),None)
        return found or kind(row['type_id'],row['data'].get('type',''),row['data'].get('short',''),row['category'])

    def periods(self,db,workspace):
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='journal_periods'").fetchone():return []
        return [dict(id=r[0],name=r[1],start=r[2],end=r[3],status=r[4]) for r in db.execute('SELECT id,name,start_day,end_day,status FROM journal_periods WHERE workspace=? ORDER BY start_day,id',(workspace,))]

    def limit_issues(self,db,workspace,snap,cfg,typ,day,aud,exclude=None):
        """Messages and ids of the bookings that make the day, the week or the СОЧ window unavailable."""
        messages,conflicts=[],[]
        names=self.names(snap)
        if typ['counted'] and (cfg['maxPerDay'] or cfg['maxPerWeek']):
            monday=day-timedelta(days=day.weekday());kinds={t['id']:t['counted'] for t in cfg['types']}
            rows=db.execute("SELECT * FROM assessment_bookings WHERE workspace=? AND status IN ('planned','published') AND id!=? AND day BETWEEN ? AND ?",
                            (workspace,exclude or '',monday.isoformat(),(monday+timedelta(days=6)).isoformat())).fetchall()
            # A work whose type was switched to "not counted" no longer weighs on the class; unknown types still do.
            week=[self.decoded(r) for r in rows if kinds.get(r['type_id'],True)];same=[b for b in week if b['day']==day.isoformat()]
            for c in aud['class_ids']:
                members={s.id for s in snap.state.students if s.active and s.classId==c}
                pupils=None if c in aud['whole_ids'] else set(aud['student_ids'])&members
                for pool,limit,text in [(same,cfg['maxPerDay'],'в этот день уже {n} (не больше {m} в день)'),(week,cfg['maxPerWeek'],'на этой неделе уже {n} (не больше {m} в неделю)')]:
                    n=class_load(pool,c,members,pupils) if limit else 0
                    if not limit or n+1<=limit:continue
                    hits=[b for b in pool if touches(b,c,members,pupils)]
                    listing='; '.join(f"{label(b['day'])} {names['subject'].get(b['subject_id'],b['data'].get('subject',''))} — {b['data'].get('short','')} ({b['author_name']})" for b in hits)
                    messages.append(f"{names['klass'].get(c,c)}: {text.format(n=n,m=limit)}"+(f': {listing}.' if listing else '.'))
                    conflicts.extend(b['id'] for b in hits if b['id'] not in conflicts)
        if typ['window']:
            iso=day.isoformat()
            for p in self.periods(db,workspace):
                window=next((w for w in cfg['windows'] if w['periodId']==p['id']),None) if p['start']<=iso<=p['end'] else None
                if window and not window['start']<=iso<=window['end']:
                    messages.append(f"{typ['short']} в периоде «{p['name']}» проводится с {label(window['start'])} по {label(window['end'])}.")
        return messages,conflicts

    def history(self,db,workspace,user,booking,action,reason,document):
        db.execute('INSERT INTO assessment_history(workspace,booking_id,actor_id,actor_name,created,action,reason,document) VALUES (?,?,?,?,?,?,?,?)',
                   (workspace,booking,user['id'],user['name'],stamp(),action,reason,encoded(document)))

    def replay(self,db,user,body,scope):
        # The scope binds a requestId to one action, so it cannot be reused on another booking.
        key=hashlib.sha256(encoded(dict(scope=scope,body=body.model_dump(mode='json'))).encode()).hexdigest()
        row=db.execute('SELECT fingerprint,result FROM assessment_requests WHERE user_id=? AND request_id=?',(user['id'],body.requestId)).fetchone()
        if row:
            if row['fingerprint']!=key:raise HTTPException(409,'Идентификатор уже использован другим действием.')
            return key,json.loads(row['result'])
        return key,None

    def remember(self,db,user,body,key,result):
        db.execute('INSERT INTO assessment_requests(user_id,request_id,fingerprint,result) VALUES (?,?,?,?)',(user['id'],body.requestId,key,encoded(result)))
        db.execute('DELETE FROM assessment_requests WHERE user_id=? AND seq NOT IN (SELECT seq FROM assessment_requests WHERE user_id=? ORDER BY seq DESC LIMIT 100)',(user['id'],user['id']))
        return result

    def notify_family(self,db,workspace,snap,row,action):
        targets=self.notifications.granted_users(db,workspace,'student',self.members(snap,row['group_id']),['student','parent'])
        self.notifications.deliver(db,targets,workspace,row['version'],action,topic=f"assessment:{workspace}:{row['id']}")

    def notify_teachers(self,db,workspace,snap,row,action,actor):
        """Tell the teachers of the pair when somebody else changes their work."""
        ids={t for w in snap.state.workloads if w.groupId==row['group_id'] and w.subjectId==row['subject_id'] for t in teacher_ids(w)}
        if grant_ids(actor,workspace,'teacher')&ids:return
        targets=[u for u in self.notifications.granted_users(db,workspace,'teacher',ids,['teacher']) if u['id']!=actor['id']]
        self.notifications.deliver(db,targets,workspace,row['version'],'assessment.staff_changed',topic=f"assessment:{workspace}:{row['id']}")

    def current(self,row,lesson):
        """The lesson row the work stands on and a note when the timetable no longer has it."""
        valid=bool(lesson and lesson['status']!='cancelled')
        message='' if valid else ('; '.join(lesson['reasons']) or 'Урок отменён.') if lesson else 'Урока в расписании больше нет.'
        return valid,message

    def public(self,row,snap,user,pairs,manager,today,lesson=None):
        data=row['data'];names=self.names(snap);pair=row['group_id'],row['subject_id']
        # An active work follows the timetable: a changed lesson time shows through; a cancelled one is flagged.
        cancelled=row['status']=='cancelled'
        valid,message=(True,'') if cancelled else self.current(row,lesson)
        live=None if cancelled or not valid else lesson
        return dict(id=row['id'],version=row['version'],status=row['status'],statusTitle=STATES[row['status']],day=row['day'],
                    start=live['start'] if live else row['start'],end=live['end'] if live else row['end_time'],lessonId=row['lesson_id'],lessonKey=row['lesson_key'],
                    typeId=row['type_id'],type=data.get('type',''),short=data.get('short',''),category=row['category'],topic=data.get('topic',''),note=data.get('note',''),
                    groupId=row['group_id'],group=names['group'].get(row['group_id'],data.get('group','')),subjectId=row['subject_id'],
                    subject=names['subject'].get(row['subject_id'],data.get('subject','')),classIds=row['class_ids'],
                    classes=', '.join(names['klass'].get(c,c) for c in row['class_ids']),teachers=names['teachers'].get(pair,data.get('teachers','')),
                    author=row['author_name'],created=row['created'],updated=row['updated'],publishedAt=row['published_at'],
                    override=data.get('override',{}).get('reason',''),overrideBy=data.get('override',{}).get('by',''),cancelReason=data.get('cancelReason',''),
                    canChange=row['status']!='cancelled' and (manager or pair in pairs) and (user['role']=='admin' or date.fromisoformat(row['day'])>=today),
                    slotValid=valid,slotMessage=message,journalAssessmentId=(data.get('journal') or {}).get('assessmentId'))

    def family_item(self,row,snap,lesson=None):
        data=row['data'];names=self.names(snap);valid,_=self.current(row,lesson)
        live=lesson if valid else None
        item=dict(id=row['id'],day=row['day'],start=live['start'] if live else row['start'],end=live['end'] if live else row['end_time'],
                  subject=names['subject'].get(row['subject_id'],data.get('subject','')),group=names['group'].get(row['group_id'],data.get('group','')),
                  short=data.get('short',''),type=data.get('type',''),topic=data.get('topic',''),
                  teachers=names['teachers'].get((row['group_id'],row['subject_id']),data.get('teachers','')),updated=row['updated'],slotValid=valid)
        if not valid:item['slotMessage']='Дата уточняется: урок по расписанию изменён.'
        return item

    def register(self,app,Workspace):
        from .assessment_calendar_routes import register
        register(self,app,Workspace)
