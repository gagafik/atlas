"""Independent class duties. They never grant access to grades or timetable editing.

A class teacher (SchoolClass.classTeacherId) holds every duty of the own class by default;
the registry only adds deputies and explicit revocations."""
import json
from typing import Literal

from fastapi import HTTPException, Query, Request
from pydantic import Field, model_validator
from .access import actor, now
from .models import Model
from .journal import user_ids
from .notifications import encoded

Capability = Literal['absence_review','attendance_read','journal_coverage','behaviour_notes','attendance_grid']
# Appended last: the order is user-visible. attendance_grid edits and confirms the class grid; reading it is attendance_read.
CAPABILITIES = ['absence_review','attendance_read','journal_coverage','behaviour_notes','attendance_grid']
# Duties that the single legacy reviewer assignment granted before permissions were split.
LEGACY = ['absence_review','attendance_read','journal_coverage']


def tables(db):
    db.execute('CREATE TABLE IF NOT EXISTS class_staff_permissions (workspace TEXT PRIMARY KEY,version INTEGER NOT NULL,document TEXT NOT NULL)')
    db.execute('''CREATE TABLE IF NOT EXISTS class_staff_history (
        id INTEGER PRIMARY KEY,workspace TEXT NOT NULL,version INTEGER NOT NULL,created TEXT NOT NULL,
        actor_id TEXT NOT NULL,actor_name TEXT NOT NULL,reason TEXT NOT NULL,before_document TEXT NOT NULL,after_document TEXT NOT NULL)''')
    db.execute('CREATE INDEX IF NOT EXISTS class_staff_history_workspace ON class_staff_history(workspace,id)')
    db.execute('''CREATE TABLE IF NOT EXISTS class_teacher_history (
        id INTEGER PRIMARY KEY,workspace TEXT NOT NULL,revision INTEGER NOT NULL,created TEXT NOT NULL,
        actor_id TEXT NOT NULL,actor_name TEXT NOT NULL,action TEXT NOT NULL,class_id TEXT NOT NULL,class_name TEXT NOT NULL,
        before_id TEXT,before_name TEXT,after_id TEXT,after_name TEXT)''')
    db.execute('CREATE INDEX IF NOT EXISTS class_teacher_history_workspace ON class_teacher_history(workspace,id)')


def teacher_changes(before,after):
    """(class id, class name, old teacher id, new teacher id) for every class whose class teacher differs between two states."""
    old={c.id:c.classTeacherId for c in before.classes};new={c.id:c.classTeacherId for c in after.classes}
    names={c.id:c.name for c in before.classes}|{c.id:c.name for c in after.classes}
    return [(id,names[id],old.get(id),new.get(id)) for id in sorted(old.keys()|new.keys()) if old.get(id)!=new.get(id)]


def record_teachers(db,workspace,before,after,action):
    """Only an administrator may change a class teacher, by any write path; the caller's transaction rolls back on 403."""
    changes=teacher_changes(before,after)
    if not changes:return
    user=actor.get()
    if user['role']!='admin':raise HTTPException(403,'Изменение затрагивает классного руководителя. Его может выполнить только администратор.')
    tables(db);stamp=now().isoformat()
    old={t.id:t.name for t in before.teachers};new={t.id:t.name for t in after.teachers}
    for id,name,was,became in changes:
        db.execute('INSERT INTO class_teacher_history(workspace,revision,created,actor_id,actor_name,action,class_id,class_name,before_id,before_name,after_id,after_name) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
            (workspace,after.revision,stamp,user['id'],user['name'],action,id,name,was,old.get(was),became,new.get(became)))


def config(db,workspace):
    row=db.execute('SELECT version,document FROM class_staff_permissions WHERE workspace=?',(workspace,)).fetchone()
    if row:return dict(version=row[0],assignments=json.loads(row[1]),legacy=False)
    row=db.execute('SELECT version,document FROM absence_reviewers WHERE workspace=?',(workspace,)).fetchone()
    # Old assignments really granted all three duties. Preserve them until an
    # administrator explicitly reviews and saves the independent permissions.
    legacy=json.loads(row[1]) if row else {}
    return dict(version=row[0] if row else 0,legacy=bool(row),assignments={
        id:[dict(accountId=account,capabilities=LEGACY.copy()) for account in accounts]
        for id,accounts in legacy.items() if accounts})


def eligible(user,snap,workspace):
    if not user.get('active',True):return False
    if user['role']=='staff':return workspace in user['workspaces']
    return user['role']=='teacher' and bool(user_ids(user,workspace,'teacher')&set(snap.entities['teacher']))


def led(user,snap,workspace):
    """Classes where one of the user's current teacher records is the class teacher."""
    if user['role']!='teacher' or not user.get('active',True):return set()
    own=user_ids(user,workspace,'teacher')
    return {c.id for c in snap.state.classes if c.classTeacherId and c.classTeacherId in own}


def classes(db,user,snap,workspace,capability):
    if not eligible(user,snap,workspace):return set()
    entries=config(db,workspace)['assignments']
    explicit={id for id,rows in entries.items() if id in snap.entities['class_'] and any(e['accountId']==user['id'] and capability in e.get('capabilities',[]) for e in rows)}
    # Derived from the live link on every request. An explicit revocation of this duty for this account wins.
    derived={id for id in led(user,snap,workspace) if not any(e['accountId']==user['id'] and capability in e.get('revoked',[]) for e in entries.get(id,[]))}
    return explicit|derived


class Assignment(Model):
    accountId:str=Field(min_length=1,max_length=100)
    capabilities:list[Capability]=Field(default_factory=list,max_length=len(CAPABILITIES))
    revoked:list[Capability]=Field(default_factory=list,max_length=len(CAPABILITIES))  # duties taken from the class teacher of this class
    @model_validator(mode='after')
    def unique(self):
        if len(set(self.capabilities))!=len(self.capabilities) or len(set(self.revoked))!=len(self.revoked):raise ValueError('Полномочия не должны повторяться.')
        if not self.capabilities and not self.revoked:raise ValueError('Выберите хотя бы одно полномочие или снятие.')
        if set(self.capabilities)&set(self.revoked):raise ValueError('Полномочие не может быть одновременно выдано и снято.')
        return self


class Change(Model):
    version:int=Field(ge=0)
    revision:int|None=Field(default=None,ge=0)  # old clients still send it; it is ignored
    reason:str=Field(min_length=1,max_length=500)
    assignments:dict[str,list[Assignment]]
    @model_validator(mode='after')
    def bounded(self):
        if len(self.assignments)>500 or any(len(rows)>20 or len({r.accountId for r in rows})!=len(rows) for rows in self.assignments.values()):
            raise ValueError('До 500 классов и 20 разных сотрудников на класс.')
        return self


def register(service,app,Workspace):
    from .absences import tables as prepare
    access=service.access

    def administrator(request,workspace):
        user,snap=service.context(request,workspace)
        if user['role']!='admin':raise HTTPException(403,'Назначать полномочия может администратор.')
        return user,snap

    @app.get('/api/access/class-staff')
    def options(request:Request,workspace:Workspace='school'):
        user,snap=administrator(request,workspace)
        with access.lock,access.connect() as db:
            prepare(db);db.execute('BEGIN IMMEDIATE');service.recheck(db,user,snap,workspace)
            current=config(db,workspace)
            accounts=[access.public(r) for r in db.execute("SELECT * FROM accounts WHERE role IN ('teacher','staff') ORDER BY name,id")]
            names={t.id:t.name for t in snap.state.teachers}
            # Accounts that hold the class teacher duties by default: active teachers linked to the class teacher record.
            leaders=lambda c:[u['id'] for u in accounts if u['role']=='teacher' and u.get('active',True) and c.classTeacherId and c.classTeacherId in user_ids(u,workspace,'teacher')]
            return current|dict(revision=snap.state.revision,classes=[dict(id=c.id,name=c.name,classTeacherId=c.classTeacherId,classTeacherName=names.get(c.classTeacherId),classTeacherAccounts=leaders(c)) for c in snap.state.classes],
                accounts=[dict(id=u['id'],name=u['name'],login=u['login'],role=u['role'],eligible=eligible(u,snap,workspace)) for u in accounts])

    @app.put('/api/access/class-staff')
    def save(body:Change,request:Request,workspace:Workspace='school'):
        user,snap=administrator(request,workspace)
        with access.lock,access.connect() as db:
            prepare(db);db.execute('BEGIN IMMEDIATE');service.recheck(db,user,snap,workspace)
            current=config(db,workspace)
            # Classes and accounts of the body are validated below against the current school data,
            # so a timetable save between loading and saving does not matter.
            if current['version']!=body.version:
                raise HTTPException(409,'Назначения изменились. Обновите список и повторите проверку.')
            accounts={r[0]:access.public(r) for r in db.execute("SELECT * FROM accounts WHERE active=1 AND role IN ('teacher','staff')")}
            if {id for id,entries in body.assignments.items() if entries}-set(snap.entities['class_']) or any(r.capabilities and (r.accountId not in accounts or not eligible(accounts[r.accountId],snap,workspace)) for entries in body.assignments.values() for r in entries):
                raise HTTPException(422,'Выберите действующие классы и сотрудников с доступом к этому пространству. Удалите недействующие назначения.')
            value={id:[dict(accountId=r.accountId,capabilities=r.capabilities)|(dict(revoked=r.revoked) if r.revoked else {}) for r in entries] for id,entries in body.assignments.items() if entries}
            version=current['version']+1
            db.execute('INSERT INTO class_staff_permissions VALUES (?,?,?) ON CONFLICT(workspace) DO UPDATE SET version=excluded.version,document=excluded.document',(workspace,version,encoded(value)))
            db.execute('INSERT INTO class_staff_history(workspace,version,created,actor_id,actor_name,reason,before_document,after_document) VALUES (?,?,?,?,?,?,?,?)',
                (workspace,version,now().isoformat(),user['id'],user['name'],body.reason,encoded(current['assignments']),encoded(value)))
            access.record(db,'class_staff.updated',workspace,detail=f'Редакция {version} · {body.reason}')
            return dict(version=version,assignments=value,legacy=False)

    @app.get('/api/access/class-staff/history')
    def history(request:Request,workspace:Workspace='school',before:int|None=Query(default=None,ge=1)):
        user,snap=administrator(request,workspace)
        with access.lock,access.connect() as db:
            prepare(db);db.execute('BEGIN IMMEDIATE');service.recheck(db,user,snap,workspace)
            rows=db.execute('SELECT id,version,created,actor_name,reason,before_document,after_document FROM class_staff_history WHERE workspace=? AND (? IS NULL OR id<?) ORDER BY id DESC LIMIT 21',(workspace,before,before)).fetchall()
            return dict(items=[dict(id=r[0],version=r[1],created=r[2],actor=r[3],reason=r[4],before=json.loads(r[5]),after=json.loads(r[6])) for r in rows[:20]],nextBefore=rows[19][0] if len(rows)>20 else None)

    @app.get('/api/access/class-staff/teachers/history')
    def teachers_history(request:Request,workspace:Workspace='school',classId:str='',before:int|None=Query(default=None,ge=1)):
        user,snap=administrator(request,workspace)
        with access.lock,access.connect() as db:
            prepare(db);db.execute('BEGIN IMMEDIATE');service.recheck(db,user,snap,workspace)
            rows=db.execute("SELECT id,revision,created,actor_name,action,class_id,class_name,before_id,before_name,after_id,after_name FROM class_teacher_history WHERE workspace=? AND (?='' OR class_id=?) AND (? IS NULL OR id<?) ORDER BY id DESC LIMIT 21",(workspace,classId,classId,before,before)).fetchall()
            person=lambda id,name:dict(id=id,name=name) if id else None
            return dict(items=[dict(id=r[0],revision=r[1],created=r[2],actor=r[3],action=r[4],classId=r[5],className=r[6],before=person(r[7],r[8]),after=person(r[9],r[10])) for r in rows[:20]],nextBefore=rows[19][0] if len(rows)>20 else None)

    @app.get('/api/journal/class-staff/options')
    def own(request:Request):
        user=request.state.user
        if not user or user['role'] not in ['teacher','staff']:raise HTTPException(403,'Раздел доступен назначенным сотрудникам.')
        spaces=user['workspaces'] if user['role']=='staff' else sorted({g['workspace'] for g in user['grants'] if g['view']=='teacher'})
        snapshots=[(workspace,service.snapshots.get(workspace)) for workspace in spaces]
        with access.lock,access.connect() as db:
            prepare(db);db.execute('BEGIN IMMEDIATE');service.journal.recheck_account(db,user)
            result=[]
            for workspace,snap in snapshots:
                service.recheck(db,user,snap,workspace)
                scopes={cap:classes(db,user,snap,workspace,cap) for cap in CAPABILITIES}
                selected=[dict(id=c.id,name=c.name,capabilities=[cap for cap in CAPABILITIES if c.id in scopes[cap]]) for c in snap.state.classes if any(c.id in ids for ids in scopes.values())]
                if selected:result.append(dict(id=workspace,name=snap.state.settings.name,classes=selected,classTeacherOf=sorted(led(user,snap,workspace))))
            return dict(workspaces=result)
