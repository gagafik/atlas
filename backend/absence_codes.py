"""School-defined absence types: one versioned list per space with history. Types are switched off, never deleted."""
import json

from fastapi import HTTPException, Query, Request
from pydantic import Field, model_validator
from .access import now
from .models import Model
from .notifications import encoded

DEFAULTS=[dict(id='illness',name='Болезнь',parents=True,active=True),
          dict(id='family',name='Семейные обстоятельства',parents=True,active=True),
          dict(id='school',name='Представляет школу (олимпиада, соревнование)',parents=False,active=True),
          dict(id='other',name='Другая уважительная причина',parents=True,active=True)]
INVALID='Выберите действующий тип отсутствия.'


def tables(db):
    db.execute('CREATE TABLE IF NOT EXISTS absence_code_sets (workspace TEXT PRIMARY KEY,version INTEGER NOT NULL,document TEXT NOT NULL,updated TEXT NOT NULL,actor_name TEXT NOT NULL)')
    db.execute('''CREATE TABLE IF NOT EXISTS absence_code_history (
        id INTEGER PRIMARY KEY,workspace TEXT NOT NULL,version INTEGER NOT NULL,created TEXT NOT NULL,actor_id TEXT NOT NULL,actor_name TEXT NOT NULL,
        reason TEXT NOT NULL,before_document TEXT NOT NULL,after_document TEXT NOT NULL,request_id TEXT NOT NULL,UNIQUE(workspace,actor_id,request_id))''')
    db.execute('CREATE INDEX IF NOT EXISTS absence_code_history_workspace ON absence_code_history(workspace,id)')


def current(db,workspace):
    """The saved list, or the built-in defaults until the first save. Safe on read-only connections."""
    row=db.execute('SELECT version,document,updated,actor_name FROM absence_code_sets WHERE workspace=?',(workspace,)).fetchone() if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='absence_code_sets'").fetchone() else None
    return dict(version=row[0],items=json.loads(row[1]),updated=row[2],actor=row[3]) if row else dict(version=0,items=[dict(c) for c in DEFAULTS],updated=None,actor=None)


def names(db,workspace):return {c['id']:c['name'] for c in current(db,workspace)['items']}


def check(db,workspace,code,role,*,stale=False):
    """The code id to store, or None. Parents choose only types marked for them; staff any active type."""
    if not code:return None
    item=next((c for c in current(db,workspace)['items'] if c['id']==code),None)
    if not item or not item['active'] or role=='parent' and not item['parents']:raise HTTPException(409 if stale else 422,INVALID)
    return code


class Code(Model):
    id: str = Field(pattern=r'^[a-z0-9][a-z0-9-]{0,39}$')
    name: str = Field(min_length=1,max_length=80)
    parents: bool = True
    active: bool = True


class CodeSet(Model):
    requestId: str = Field(pattern=r'^[a-f0-9-]{36}$')
    version: int = Field(ge=0)
    reason: str = Field(min_length=3,max_length=500)
    items: list[Code] = Field(min_length=1,max_length=30)
    @model_validator(mode='after')
    def valid(self):
        if len({c.id for c in self.items})!=len(self.items):raise ValueError('Коды типов не должны повторяться.')
        if len({c.name.casefold() for c in self.items})!=len(self.items):raise ValueError('Названия типов не должны повторяться.')
        if not any(c.active for c in self.items):raise ValueError('Оставьте хотя бы один действующий тип.')
        return self


def register(service,app,Workspace):
    def administrator(request,workspace):
        user,snap=service.context(request,workspace)
        if user['role']!='admin':raise HTTPException(403,'Типы отсутствия настраивает администратор.')
        return user,snap

    # Registered before /api/journal/absences/{id}, otherwise "codes" would be read as a notice id.
    @app.get('/api/journal/absences/codes')
    def listing(request:Request,workspace:Workspace='school'):
        user,_=service.context(request,workspace)
        with service.access.lock,service.access.connect() as db:
            tables(db);service.journal.recheck_account(db,user);value=current(db,workspace)
            # Who saved the list and when belong to the administrator's history; everybody else needs only the labels.
            return value if user['role']=='admin' else dict(version=value['version'],items=value['items'])

    @app.get('/api/journal/absences/codes/history')
    def history(request:Request,workspace:Workspace='school',before:int|None=Query(default=None,ge=1)):
        user,_=administrator(request,workspace)
        with service.access.lock,service.access.connect() as db:
            tables(db);service.journal.recheck_account(db,user)
            rows=db.execute('SELECT id,version,created,actor_name,reason,before_document,after_document FROM absence_code_history WHERE workspace=? AND (? IS NULL OR id<?) ORDER BY id DESC LIMIT 51',(workspace,before,before)).fetchall()
            return dict(items=[dict(id=r[0],version=r[1],created=r[2],actor=r[3],reason=r[4],before=json.loads(r[5]),after=json.loads(r[6])) for r in rows[:50]],nextBefore=rows[49][0] if len(rows)>50 else None)

    @app.put('/api/journal/absences/codes')
    def save(body:CodeSet,request:Request,workspace:Workspace='school'):
        user,_=administrator(request,workspace);document=encoded([c.model_dump() for c in body.items])
        with service.access.lock,service.access.connect() as db:
            tables(db);db.execute('BEGIN IMMEDIATE')
            # Codes do not depend on the timetable, so only the account is rechecked.
            service.journal.recheck_account(db,user);saved=current(db,workspace)
            previous=db.execute('SELECT after_document FROM absence_code_history WHERE workspace=? AND actor_id=? AND request_id=?',(workspace,user['id'],body.requestId)).fetchone()
            if previous:
                if previous[0]!=document:raise HTTPException(409,'Этот запрос уже использован для другого списка.')
                return saved
            if saved['version']!=body.version:raise HTTPException(409,'Типы отсутствия уже изменили. Обновите раздел.')
            if {c['id'] for c in saved['items']}-{c.id for c in body.items}:raise HTTPException(422,'Тип отсутствия нельзя удалить: выключите его, чтобы прежние записки сохранили название.')
            before=encoded(saved['items'])
            if before==document:return saved
            stamp=now().isoformat();version=saved['version']+1
            db.execute('INSERT INTO absence_code_sets VALUES (?,?,?,?,?) ON CONFLICT(workspace) DO UPDATE SET version=excluded.version,document=excluded.document,updated=excluded.updated,actor_name=excluded.actor_name',(workspace,version,document,stamp,user['name']))
            db.execute('INSERT INTO absence_code_history(workspace,version,created,actor_id,actor_name,reason,before_document,after_document,request_id) VALUES (?,?,?,?,?,?,?,?,?)',(workspace,version,stamp,user['id'],user['name'],body.reason,before,document,body.requestId))
            service.access.record(db,'absence.codes_saved',workspace,detail=str(version))
            return dict(version=version,items=json.loads(document),updated=stamp,actor=user['name'])
