"""Independent class duties. They never grant access to grades or timetable editing."""
import json
from typing import Literal

from fastapi import HTTPException, Query, Request
from pydantic import Field, model_validator
from .access import now
from .models import Model
from .journal import user_ids
from .notifications import encoded

Capability = Literal['absence_review','attendance_read','journal_coverage']
CAPABILITIES = ['absence_review','attendance_read','journal_coverage']


def tables(db):
    db.execute('CREATE TABLE IF NOT EXISTS class_staff_permissions (workspace TEXT PRIMARY KEY,version INTEGER NOT NULL,document TEXT NOT NULL)')
    db.execute('''CREATE TABLE IF NOT EXISTS class_staff_history (
        id INTEGER PRIMARY KEY,workspace TEXT NOT NULL,version INTEGER NOT NULL,created TEXT NOT NULL,
        actor_id TEXT NOT NULL,actor_name TEXT NOT NULL,reason TEXT NOT NULL,before_document TEXT NOT NULL,after_document TEXT NOT NULL)''')
    db.execute('CREATE INDEX IF NOT EXISTS class_staff_history_workspace ON class_staff_history(workspace,id)')


def config(db,workspace):
    row=db.execute('SELECT version,document FROM class_staff_permissions WHERE workspace=?',(workspace,)).fetchone()
    if row:return dict(version=row[0],assignments=json.loads(row[1]),legacy=False)
    row=db.execute('SELECT version,document FROM absence_reviewers WHERE workspace=?',(workspace,)).fetchone()
    # Old assignments really granted all three duties. Preserve them until an
    # administrator explicitly reviews and saves the independent permissions.
    legacy=json.loads(row[1]) if row else {}
    return dict(version=row[0] if row else 0,legacy=bool(row),assignments={
        id:[dict(accountId=account,capabilities=CAPABILITIES.copy()) for account in accounts]
        for id,accounts in legacy.items() if accounts})


def eligible(user,snap,workspace):
    if not user.get('active',True):return False
    if user['role']=='staff':return workspace in user['workspaces']
    return user['role']=='teacher' and bool(user_ids(user,workspace,'teacher')&set(snap.entities['teacher']))


def classes(db,user,snap,workspace,capability):
    if not eligible(user,snap,workspace):return set()
    return {id for id,entries in config(db,workspace)['assignments'].items()
            if id in snap.entities['class_'] and any(e['accountId']==user['id'] and capability in e['capabilities'] for e in entries)}


class Assignment(Model):
    accountId:str=Field(min_length=1,max_length=100)
    capabilities:list[Capability]=Field(min_length=1,max_length=3)
    @model_validator(mode='after')
    def unique(self):
        if len(set(self.capabilities))!=len(self.capabilities):raise ValueError('Полномочия не должны повторяться.')
        return self


class Change(Model):
    version:int=Field(ge=0)
    revision:int=Field(ge=0)
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
            return current|dict(revision=snap.state.revision,classes=[dict(id=c.id,name=c.name) for c in snap.state.classes],
                accounts=[dict(id=u['id'],name=u['name'],login=u['login'],role=u['role'],eligible=eligible(u,snap,workspace)) for u in accounts])

    @app.put('/api/access/class-staff')
    def save(body:Change,request:Request,workspace:Workspace='school'):
        user,snap=administrator(request,workspace)
        with access.lock,access.connect() as db:
            prepare(db);db.execute('BEGIN IMMEDIATE');service.recheck(db,user,snap,workspace)
            current=config(db,workspace)
            if current['version']!=body.version or snap.state.revision!=body.revision:
                raise HTTPException(409,'Назначения или школьные данные изменились. Обновите список и повторите проверку.')
            accounts={r[0]:access.public(r) for r in db.execute("SELECT * FROM accounts WHERE active=1 AND role IN ('teacher','staff')")}
            if {id for id,entries in body.assignments.items() if entries}-set(snap.entities['class_']) or any(r.accountId not in accounts or not eligible(accounts[r.accountId],snap,workspace) for entries in body.assignments.values() for r in entries):
                raise HTTPException(422,'Выберите действующие классы и сотрудников с доступом к этому пространству. Удалите недействующие назначения.')
            value={id:[r.model_dump() for r in entries] for id,entries in body.assignments.items() if entries}
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
                if selected:result.append(dict(id=workspace,name=snap.state.settings.name,classes=selected))
            return dict(workspaces=result)
