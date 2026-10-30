"""Praise, remarks and educational measures. One row per pupil keeps every family's view private."""
from contextlib import closing,contextmanager
from datetime import date,timedelta
from typing import Literal
from uuid import uuid4
from zoneinfo import ZoneInfo
import csv,hashlib,json,sqlite3,tempfile

from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from pydantic import Field,model_validator
from .access import now
from .models import Model
from .notifications import encoded,student_ids
from .portal_cache import PortalSnapshots
from .school_forms import enabled,grant_ids

Kind=Literal['praise','remark']
Level=Literal['teacher','class_teacher','director']
KINDS={'praise':'Похвала','remark':'Замечание'}
LEVELS={'teacher':'Учитель','class_teacher':'Классный руководитель','director':'Директор'}
STATES={'pending':'На согласовании','published':'Действует','rejected':'Отклонена','withdrawn':'Отозвана'}
def category(id,kind,title,level='teacher',approval=False,quick=False,letter=False):
    return dict(id=id,kind=kind,title=title,active=True,level=level,approval=approval,quick=quick,letter=letter)
DEFAULT_CATEGORIES=[
    category('activity','praise','Активная работа на уроке',quick=True),
    category('help','praise','Помощь одноклассникам',quick=True),
    category('achievement','praise','Успех в конкурсе или олимпиаде'),
    category('class-praise','praise','Похвала классного руководителя','class_teacher'),
    category('director-praise','praise','Благодарность директора','director',letter=True),
    category('discipline','remark','Нарушение дисциплины',quick=True),
    category('unprepared','remark','Не готов к уроку',quick=True),
    category('no-homework','remark','Нет домашнего задания',quick=True),
    category('uniform','remark','Нет школьной формы'),
    category('class-remark','remark','Замечание классного руководителя','class_teacher'),
    category('performance-warning','remark','Предупреждение о неуспеваемости','class_teacher',approval=True,letter=True),
    category('director-remark','remark','Замечание директора','director',letter=True),
]
PAGE=50
def stamp():return now().isoformat()
def today():return now().astimezone(ZoneInfo('Asia/Qyzylorda')).date()

def tables(db):
    from .absences import tables as staff_tables
    staff_tables(db)
    db.executescript('''
    CREATE TABLE IF NOT EXISTS behaviour_config(workspace TEXT PRIMARY KEY,version INTEGER NOT NULL,document TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS behaviour_notes(seq INTEGER PRIMARY KEY AUTOINCREMENT,id TEXT UNIQUE,batch TEXT,workspace TEXT,student_id TEXT,class_id TEXT,
        version INTEGER,status TEXT,kind TEXT,category_id TEXT,day TEXT,author_id TEXT,author_name TEXT,created TEXT,updated TEXT,ack_at TEXT,ack_by TEXT,document TEXT);
    CREATE INDEX IF NOT EXISTS behaviour_student ON behaviour_notes(workspace,student_id,seq);
    CREATE INDEX IF NOT EXISTS behaviour_class ON behaviour_notes(workspace,class_id,day);
    CREATE INDEX IF NOT EXISTS behaviour_day ON behaviour_notes(workspace,day,seq);
    CREATE TABLE IF NOT EXISTS behaviour_history(seq INTEGER PRIMARY KEY AUTOINCREMENT,workspace TEXT,note_id TEXT,actor_id TEXT,actor_name TEXT,created TEXT,action TEXT,reason TEXT,document TEXT);
    CREATE INDEX IF NOT EXISTS behaviour_history_note ON behaviour_history(workspace,note_id,seq);
    CREATE TABLE IF NOT EXISTS behaviour_requests(seq INTEGER PRIMARY KEY AUTOINCREMENT,user_id TEXT,request_id TEXT,fingerprint TEXT,result TEXT,UNIQUE(user_id,request_id));
    CREATE TABLE IF NOT EXISTS behaviour_letters(workspace TEXT,year INTEGER,last INTEGER NOT NULL,PRIMARY KEY(workspace,year));
    ''')
    # Rows written before measures existed are visible published notes.
    if 'visible' not in {r[1] for r in db.execute('PRAGMA table_info(behaviour_notes)')}:
        db.execute('ALTER TABLE behaviour_notes ADD COLUMN visible INTEGER NOT NULL DEFAULT 1')
    db.execute('CREATE INDEX IF NOT EXISTS behaviour_queue ON behaviour_notes(workspace,status,seq)')


class Category(Model):
    id:str=Field(pattern=r'^[a-z0-9-]{1,40}$')
    kind:Kind
    title:str=Field(min_length=1,max_length=120)
    active:bool=True
    level:Level='teacher'
    approval:bool=False
    quick:bool=False
    letter:bool=False


class Config(Model):
    version:int=Field(ge=0)
    reason:str=Field(min_length=1,max_length=500)
    categories:list[Category]=Field(min_length=1,max_length=60)
    remarkAck:bool=True
    @model_validator(mode='after')
    def unique(self):
        if len({c.id for c in self.categories})!=len(self.categories):raise ValueError('Коды категорий не должны повторяться.')
        return self


class NewNotes(Model):
    requestId:str=Field(pattern=r'^[a-f0-9-]{36}$')
    studentIds:list[str]=Field(min_length=1,max_length=40)
    categoryId:str=Field(min_length=1,max_length=40)
    day:date
    text:str=Field(default='',max_length=2000)
    visible:bool=True
    lessonKey:str|None=Field(default=None,pattern=r'^[a-f0-9]{64}$')
    @model_validator(mode='after')
    def unique(self):
        if len(set(self.studentIds))!=len(self.studentIds):raise ValueError('Ученики не должны повторяться.')
        return self


class Edit(Model):
    version:int=Field(ge=1)
    categoryId:str=Field(min_length=1,max_length=40)
    text:str=Field(default='',max_length=2000)
    reason:str=Field(min_length=1,max_length=500)


class Withdraw(Model):
    version:int=Field(ge=1)
    reason:str=Field(min_length=1,max_length=500)


class Decision(Model):
    version:int=Field(ge=1)
    reason:str=Field(default='',max_length=500)


class Acknowledge(Model):
    version:int=Field(ge=1)


class Behaviour:
    def __init__(self,access,notifications,stamp):
        self.access,self.notifications=access,notifications
        self.snapshots=PortalSnapshots(access.read,stamp)

    def prepare(self):
        with closing(self.access.read_connect()) as db:
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='behaviour_queue'").fetchone():return
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
        row=db.execute('SELECT version,document FROM behaviour_config WHERE workspace=?',(workspace,)).fetchone()
        if not row:return dict(version=0,categories=DEFAULT_CATEGORIES,remarkAck=True)
        value=json.loads(row[1])
        # Categories saved before measures existed get the defaults of the new fields.
        return dict(version=row[0],remarkAck=value['remarkAck'],categories=[Category.model_validate(c).model_dump() for c in value['categories']])

    def rights(self,db,user,snap,workspace):
        """Current teaching pupils and classes assigned to the user as class teacher."""
        if user['role'] not in ['teacher','staff']:return set(),set()
        from .class_staff import classes
        from .messages import Directory
        teaching=Directory(snap.state,workspace).pupils(user) if user['role']=='teacher' else set()
        return teaching,classes(db,user,snap,workspace,'behaviour_notes')

    def scope(self,db,user,snap,workspace):
        """Pupils a staff member may record and read now: own teaching groups or assigned classes."""
        active={id:s for id,s in snap.entities['student'].items() if s.active}
        if user['role']=='admin':return set(active)
        teaching,assigned=self.rights(db,user,snap,workspace)
        return {id for id,s in active.items() if id in teaching or s.classId in assigned}

    def levels(self,db,user,snap,workspace):
        if user['role']=='admin':return set(LEVELS)
        teaching,assigned=self.rights(db,user,snap,workspace)
        return ({'teacher'} if teaching or assigned else set())|({'class_teacher'} if assigned else set())

    def allowed(self,db,user,snap,workspace,level,students):
        if user['role']=='admin':return True
        if level=='director':return False
        teaching,assigned=self.rights(db,user,snap,workspace)
        pupils=snap.entities['student']
        if level=='class_teacher':return all(pupils[id].classId in assigned for id in students)
        return all(id in teaching or pupils[id].classId in assigned for id in students)

    def lesson(self,db,user,snap,workspace,day,key):
        """The actual lesson of that day, if the user teaches it now (substitutes included) or is an administrator."""
        from .calendar_ops import resolve_day
        from .journal import key_for,staff_read
        row=next((r for r in resolve_day(snap.state,day)[0] if r['activity']=='lesson' and key_for(r)==key),None)
        if not row or not (user['role']=='admin' or staff_read(user,workspace,row)):raise HTTPException(404,'Урок не найден.')
        if row['status']=='cancelled':raise HTTPException(409,'Урок отменён.')
        closed=bool(db.execute("SELECT 1 FROM sqlite_master WHERE name='journal_periods'").fetchone())
        if closed:
            from .journal_periods import locks
            closed=bool(locks(db,workspace,dict(date=row['date'],sourceKey=row['id'])))
        return row|dict(locked=closed,label=f"{row['subject']}, {date.fromisoformat(row['date']).strftime('%d.%m.%Y')}, {row['start']}")

    def family(self,user,snap,workspace):
        if user['role'] not in ['parent','student']:return set()
        return {id for id in grant_ids(user,workspace,'student') if id in snap.entities['student'] and snap.entities['student'][id].active}

    def note(self,db,workspace,id):
        row=db.execute('SELECT * FROM behaviour_notes WHERE workspace=? AND id=?',(workspace,id)).fetchone()
        if not row:raise HTTPException(404,'Запись не найдена.')
        return dict(row)|dict(data=json.loads(row['document']))

    def staff_note(self,db,user,snap,workspace,id):
        row=self.note(db,workspace,id)
        if row['student_id'] not in self.scope(db,user,snap,workspace):raise HTTPException(404,'Запись не найдена.')
        return row

    def family_note(self,db,user,snap,workspace,id):
        row=self.note(db,workspace,id)
        if row['student_id'] not in self.family(user,snap,workspace) or row['status']!='published' or not row['visible']:raise HTTPException(404,'Запись не найдена.')
        return row

    def may_change(self,user,row):
        if row['status'] not in ['pending','published']:return False
        if user['role']=='admin':return True
        # An approved measure belongs to the administration once it is in force.
        return row['author_id']==user['id'] and not (row['status']=='published' and row['data'].get('approval'))

    def public(self,row,snap,staff=False,user=None):
        pupil=snap.entities['student'].get(row['student_id']);data=row['data']
        result={k:row[k] for k in ['id','version','status','kind','day','created','updated']}|dict(
            categoryId=row['category_id'],category=data['category'],text=data['text'],author=row['author_name'],
            level=data.get('level','teacher'),levelTitle=LEVELS[data.get('level','teacher')],
            studentId=row['student_id'],student=pupil.name if pupil else '',requireAck=data['requireAck'],
            acknowledged=bool(row['ack_at']),acknowledgedAt=row['ack_at'],acknowledgedBy=row['ack_by'],
            letter=bool(data.get('letterNumber')),letterNumber=data.get('letterNumber',''),lesson=data.get('lesson',{}).get('label',''))
        if staff:
            classes={c.id:c.name for c in snap.state.classes}
            result.update(className=classes.get(row['class_id'],''),withdrawReason=data.get('withdrawReason',''),visible=bool(row['visible']),
                          approval=bool(data.get('approval')),decision=data.get('decision',''),approvedBy=data.get('approvedBy',''),
                          canChange=bool(user and self.may_change(user,row)),
                          canDecide=bool(user and user['role']=='admin' and row['status']=='pending'))
        return result

    def history(self,db,workspace,user,note,action,reason,document):
        db.execute('INSERT INTO behaviour_history(workspace,note_id,actor_id,actor_name,created,action,reason,document) VALUES (?,?,?,?,?,?,?,?)',
                   (workspace,note,user['id'],user['name'],stamp(),action,reason,encoded(document)))

    def replay(self,db,user,body):
        key=hashlib.sha256(encoded(body.model_dump(mode='json')).encode()).hexdigest()
        row=db.execute('SELECT fingerprint,result FROM behaviour_requests WHERE user_id=? AND request_id=?',(user['id'],body.requestId)).fetchone()
        if row:
            if row['fingerprint']!=key:raise HTTPException(409,'Идентификатор уже использован другим действием.')
            return key,json.loads(row['result'])
        return key,None

    def remember(self,db,user,body,key,result):
        db.execute('INSERT INTO behaviour_requests(user_id,request_id,fingerprint,result) VALUES (?,?,?,?)',(user['id'],body.requestId,key,encoded(result)))
        db.execute('DELETE FROM behaviour_requests WHERE user_id=? AND seq NOT IN (SELECT seq FROM behaviour_requests WHERE user_id=? ORDER BY seq DESC LIMIT 100)',(user['id'],user['id']))
        return result

    def notify_family(self,db,workspace,students,version,action,note=None):
        """note=dict(id,kind,category,name): the title names the kind and the category only, never the teacher's text or a withdrawal reason."""
        targets=self.notifications.granted_users(db,workspace,'student',students,['student','parent'])
        if not note:return self.notifications.deliver(db,targets,workspace,version,action)
        label=f"{KINDS[note['kind']]}: {note['category']}"
        title={'behaviour.published':label,'behaviour.changed':f"Изменена запись: {KINDS[note['kind']]} — {note['category']}"}.get(action,'Запись о поведении отозвана')
        # A withdrawn note leads nowhere: its entry has no link.
        module=dict(module='behaviour') if action!='behaviour.withdrawn' else {}
        def target(user):
            kids=[s for s in students if s in student_ids(user,workspace)]
            return dict(recordId=note['id'],students=[dict(id=s,name=note.get('name') or '') for s in kids],title=title,**module)
        self.notifications.deliver(db,targets,workspace,version,action,topic=f"behaviour:{workspace}:{note['id']}",supersede=True,target=target)

    def notify_accounts(self,db,workspace,ids,version,action,skip=None):
        rows=[r for r in db.execute('SELECT * FROM accounts WHERE active=1 AND id IN (SELECT value FROM json_each(?))',(encoded(sorted(ids)),)) if r['id']!=skip]
        self.notifications.deliver(db,[self.access.public(r) for r in rows],workspace,version,action)

    def notify_admins(self,db,workspace,version,skip):
        ids={r[0] for r in db.execute("SELECT id FROM accounts WHERE active=1 AND role='admin'")}
        self.notify_accounts(db,workspace,ids,version,'behaviour.pending',skip)

    def category(self,db,workspace,id):
        found=next((c for c in self.config(db,workspace)['categories'] if c['id']==id and c['active']),None)
        if not found:raise HTTPException(422,'Выберите действующую категорию.')
        return found

    def letter_number(self,db,workspace):
        year=today().year
        db.execute('INSERT INTO behaviour_letters(workspace,year,last) VALUES (?,?,1) ON CONFLICT(workspace,year) DO UPDATE SET last=last+1',(workspace,year))
        return f"{year}-{db.execute('SELECT last FROM behaviour_letters WHERE workspace=? AND year=?',(workspace,year)).fetchone()[0]:04d}"

    def filtered(self,db,user,snap,workspace,classId,studentId,kind,start,end,status):
        allowed=self.scope(db,user,snap,workspace)
        if studentId:allowed&={studentId}
        if classId:allowed={id for id in allowed if snap.entities['student'][id].classId==classId}
        clause='workspace=? AND student_id IN (SELECT value FROM json_each(?))'
        params=[workspace,encoded(sorted(allowed))]
        if kind:clause+=' AND kind=?';params.append(kind)
        if status:clause+=' AND status=?';params.append(status)
        if start:clause+=' AND day>=?';params.append(start.isoformat())
        if end:clause+=' AND day<=?';params.append(end.isoformat())
        return clause,params

    def register(self,app,Workspace):
        from .behaviour_routes import register
        register(self,app,Workspace)


def csv_cell(value):
    text='' if value is None else str(value)
    return "'"+text if text.lstrip().startswith(('=','+','-','@')) else text


def export_rows(rows,snap):
    output=tempfile.SpooledTemporaryFile(max_size=512*1024,mode='w+',encoding='utf-8',newline='')
    try:
        writer=csv.writer(output);output.write('﻿')
        writer.writerow(['Дата','Ученик','Класс','Вид','Уровень','Категория','Комментарий','Автор','Статус','Скрыта от семьи','Письмо','Ознакомлен'])
        classes={c.id:c.name for c in snap.state.classes}
        for row in rows:
            data=json.loads(row['document']);pupil=snap.entities['student'].get(row['student_id'])
            writer.writerow([csv_cell(v) for v in [row['day'],pupil.name if pupil else row['student_id'],classes.get(row['class_id'],''),KINDS[row['kind']],
                             LEVELS[data.get('level','teacher')],data['category'],data['text'],row['author_name'],STATES[row['status']],
                             '' if row['visible'] else 'Да',data.get('letterNumber',''),row['ack_at'] or '']])
        output.seek(0)
    except BaseException:
        output.close();raise
    def stream():
        with output:
            while chunk:=output.read(65536):yield chunk.encode('utf-8')
    return StreamingResponse(stream(),media_type='text/csv; charset=utf-8',headers={'Content-Disposition':'attachment; filename="behaviour.csv"'})


def new_id():return uuid4().hex


def window(start,end):
    if start and end and start>end:raise HTTPException(422,'Начало периода позже окончания.')
    if start and end and end-start>timedelta(days=366):raise HTTPException(422,'Период не длиннее 366 дней.')
