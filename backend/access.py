"""Local accounts, opaque sessions, server permissions and attributable edits."""
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
from typing import Literal
from uuid import uuid4

from fastapi import HTTPException, Request, Response
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
from pydantic import ConfigDict, Field, SecretStr, model_validator, field_validator
from .models import Model

Role = Literal['admin', 'editor', 'teacher', 'student', 'parent', 'viewer']
WORKSPACES = ['school', 'demo']
COOKIE = 'atlas_session'
LOCAL = dict(id='local', name='Локальный пользователь', role='admin', workspaces=WORKSPACES, grants=[])
actor = ContextVar('atlas_actor', default=LOCAL)


def now(): return datetime.now(timezone.utc)
def digest(value): return hashlib.sha256(value.encode()).hexdigest()


def password_hash(password):
    salt = secrets.token_hex(16)
    value = hashlib.scrypt(password.encode(), salt=salt.encode(), n=16384, r=8, p=1, maxmem=64*1024*1024).hex()
    return salt + ':' + value


def password_matches(password, encoded):
    salt, expected = encoded.split(':')
    value = hashlib.scrypt(password.encode(), salt=salt.encode(), n=16384, r=8, p=1, maxmem=64*1024*1024).hex()
    return hmac.compare_digest(value, expected)


class Grant(Model):
    workspace: Literal['school', 'demo']
    view: Literal['class', 'teacher', 'room', 'group', 'student']
    entityIds: list[str] = Field(min_length=1, max_length=200)


class Credentials(Model):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=False)

    @field_validator('name','login',mode='before',check_fields=False)
    @classmethod
    def trim_labels(cls, value): return value.strip() if isinstance(value,str) else value


class Account(Credentials):
    name: str = Field(min_length=1, max_length=160)
    login: str = Field(pattern=r'^[a-z0-9][a-z0-9._-]{2,63}$')
    role: Role
    active: bool = True
    workspaces: list[Literal['school', 'demo']] = Field(default_factory=lambda: ['school'], max_length=2)
    grants: list[Grant] = Field(default_factory=list, max_length=50)
    password: SecretStr | None = Field(default=None, min_length=12, max_length=128)
    version: int = Field(default=0, ge=0)

    @model_validator(mode='after')
    def permissions(self):
        if len(set(self.workspaces)) != len(self.workspaces): raise ValueError('Пространства не должны повторяться.')
        if self.role in ['admin','editor']:
            if self.grants: raise ValueError('Администратор и составитель используют доступ к пространствам.')
            if not self.workspaces: raise ValueError('Выберите рабочее пространство.')
        else:
            if not self.grants and self.role!='parent': raise ValueError('Выберите разрешённые расписания.')
            keys = [(g.workspace,g.view) for g in self.grants]
            if len(set(keys)) != len(keys): raise ValueError('Объедините одинаковые области доступа.')
            for g in self.grants:
                if len(set(g.entityIds)) != len(g.entityIds): raise ValueError('Записи доступа не должны повторяться.')
                if self.role == 'teacher' and g.view != 'teacher': raise ValueError('Учителю назначается расписание преподавателя.')
                if self.role in ['student','parent'] and g.view != 'student': raise ValueError('Ученику и родителю назначается личное расписание ученика.')
                if self.role == 'student' and len(g.entityIds) != 1: raise ValueError('Ученику доступно одно личное расписание в каждом пространстве.')
        return self


class Setup(Credentials):
    name: str = Field(min_length=1,max_length=160)
    login: str = Field(pattern=r'^[a-z0-9][a-z0-9._-]{2,63}$')
    password: SecretStr = Field(min_length=12,max_length=128)


class Login(Credentials):
    login: str = Field(min_length=1,max_length=64)
    password: SecretStr = Field(min_length=1,max_length=128)


class PasswordChange(Credentials):
    currentPassword: SecretStr = Field(min_length=1,max_length=128)
    password: SecretStr = Field(min_length=12,max_length=128)


class Presence(Model):
    clientId: str = Field(pattern=r'^[A-Za-z0-9_-]{8,100}$')
    page: str = Field(default='',max_length=80)


class Access:
    def __init__(self, connect, lock, read, read_connect=None):
        self.connect, self.lock, self.read = connect, lock, read
        self.read_connect = read_connect or connect
        self.dummy = password_hash(secrets.token_urlsafe(32))

    def tables(self, db):
        db.execute('CREATE TABLE IF NOT EXISTS accounts (id TEXT PRIMARY KEY,login TEXT UNIQUE,name TEXT,role TEXT,active INTEGER,version INTEGER,password TEXT,permissions TEXT)')
        db.execute('CREATE TABLE IF NOT EXISTS sessions (token TEXT PRIMARY KEY,user_id TEXT,created TEXT,expires TEXT)')
        db.execute('CREATE TABLE IF NOT EXISTS login_attempts (id INTEGER PRIMARY KEY,client TEXT,login TEXT,created TEXT)')
        db.execute('CREATE TABLE IF NOT EXISTS audit (id INTEGER PRIMARY KEY,created TEXT,actor_id TEXT,actor_name TEXT,action TEXT,workspace TEXT,revision INTEGER,detail TEXT)')
        db.execute('CREATE TABLE IF NOT EXISTS presence (client_id TEXT,user_id TEXT,workspace TEXT,name TEXT,page TEXT,updated TEXT,PRIMARY KEY(client_id,user_id))')

    def record(self, db, action, workspace=None, revision=None, detail='', principal=None):
        self.tables(db)
        user = principal or actor.get()
        db.execute('INSERT INTO audit (created,actor_id,actor_name,action,workspace,revision,detail) VALUES (?,?,?,?,?,?,?)',
                   (now().isoformat(),user['id'],user['name'],action,workspace,revision,detail))
        db.execute('DELETE FROM audit WHERE id NOT IN (SELECT id FROM audit ORDER BY id DESC LIMIT 10000)')

    def public(self, row, *, configured=False):
        id, login, name, role, active, version, _, permissions = row
        permissions=json.loads(permissions)
        grants=permissions['grants']
        if role=='parent' and not configured:
            from .families import effective_grants
            grants=effective_grants(permissions)
        return dict(id=id,login=login,name=name,role=role,active=bool(active),version=version,workspaces=permissions['workspaces'],grants=grants)

    def identify(self, request):
        raw = request.cookies.get(COOKIE, '')
        def lookup():
            with self.read_connect() as db:
                db.execute('BEGIN')
                enabled = bool(db.execute('SELECT 1 FROM accounts LIMIT 1').fetchone())
                row = db.execute('SELECT a.* FROM accounts a JOIN sessions s ON s.user_id=a.id WHERE s.token=? AND s.expires>? AND a.active=1', (digest(raw),now().isoformat())).fetchone() if raw else None
                return enabled, row
        try:
            enabled, row = lookup()
        except sqlite3.OperationalError as exc:
            # Bootstrap only. Ordinary reads neither create tables nor acquire
            # the global writer lock. WAL readers see a committed snapshot.
            if str(exc) not in ['no such table: accounts','no such table: sessions','unable to open database file']:
                raise
            with self.lock, self.connect() as db: self.tables(db)
            enabled, row = lookup()
        return enabled, self.public(row) if row else None

    def issue_session(self, db, user_id, response, request):
        raw, created = secrets.token_urlsafe(32), now()
        db.execute('DELETE FROM sessions WHERE expires<=?', (created.isoformat(),))
        db.execute('INSERT INTO sessions VALUES (?,?,?,?)', (digest(raw),user_id,created.isoformat(),(created+timedelta(hours=12)).isoformat()))
        db.execute('DELETE FROM sessions WHERE user_id=? AND token NOT IN (SELECT token FROM sessions WHERE user_id=? ORDER BY created DESC LIMIT 10)', (user_id,user_id))
        response.set_cookie(COOKIE,raw,max_age=12*3600,httponly=True,samesite='strict',secure=request.url.scheme=='https',path='/')

    def local(self, request):
        return request.client and request.client.host in ['127.0.0.1','::1','testclient'] and request.url.hostname in ['127.0.0.1','localhost','::1','testserver']

    async def guard(self, request, call_next):
        path = request.url.path
        if not (path.startswith('/api/') or path == '/openapi.json'): return await call_next(request)
        # SQLite/lock waits must not block the ASGI event loop for every client.
        enabled, user = await run_in_threadpool(self.identify, request)
        request.state.user = user
        request.state.auth_enabled = enabled
        if not enabled and os.environ.get('TIMETABLE_REQUIRE_AUTH') == '1':
            return JSONResponse(status_code=503,content={'detail':'Сервер ещё не настроен. Администратор должен создать первую учётную запись в консоли сервера.'})
        public = path in ['/api/auth/status','/api/auth/login','/api/auth/logout','/api/auth/setup','/api/auth/register','/api/health']
        unsafe = request.method not in ['GET','HEAD','OPTIONS']
        if not enabled and not self.local(request) and path != '/api/health':
            return JSONResponse(status_code=403,content={'detail':'Сначала включите учётные записи на компьютере сервера.'})
        if unsafe and (enabled or path.startswith('/api/auth/')) and request.headers.get('x-atlas-request') != '1':
            return JSONResponse(status_code=403,content={'detail':'Откройте приложение и повторите действие.'})
        if enabled and not public:
            if not user: return JSONResponse(status_code=401,content={'detail':'Войдите в учётную запись.'})
            administrative = path.startswith('/api/access/')
            portal = path.startswith('/api/portal/') or path == '/api/portal'
            personal = path in ['/api/auth/password','/api/auth/invitations/accept'] or path.startswith(('/api/notifications/','/api/journal/','/api/intake/','/api/messages/'))
            workspace = request.query_params.get('workspace','school')
            allowed = user['role'] == 'admin' or personal or portal or (not administrative and user['role'] == 'editor' and workspace in user['workspaces'])
            if not allowed: return JSONResponse(status_code=403,content={'detail':'У вашей учётной записи нет доступа к этому действию.'})
        elif not enabled and path.startswith('/api/access/'):
            return JSONResponse(status_code=403,content={'detail':'Сначала создайте администратора в разделе доступа.'})
        context = actor.set(user or LOCAL)
        try:
            response = await call_next(request)
            response.headers['Cache-Control'] = 'no-store'
            return response
        finally: actor.reset(context)

    def validate_grants(self, value):
        tables = dict(class_='classes',teacher='teachers',room='rooms',group='groups',student='students')
        for grant in value.grants:
            state = self.read(grant.workspace)
            rows = getattr(state,tables['class_' if grant.view=='class' else grant.view])
            if set(grant.entityIds)-{x.id for x in rows}: raise HTTPException(422,'Выбраны отсутствующие записи доступа.')

    def register(self, app):
        app.middleware('http')(self.guard)

        @app.get('/api/auth/status')
        def status(request: Request):
            enabled,user = request.state.auth_enabled,request.state.user
            return dict(enabled=enabled,user=user,canSetup=not enabled and bool(self.local(request)))

        @app.post('/api/auth/setup')
        def setup(body: Setup, request: Request, response: Response):
            if not self.local(request): raise HTTPException(403,'Первый администратор создаётся на компьютере сервера.')
            encoded = password_hash(body.password.get_secret_value())
            id = str(uuid4())
            with self.lock,self.connect() as db:
                self.tables(db); db.execute('BEGIN IMMEDIATE')
                if db.execute('SELECT 1 FROM accounts LIMIT 1').fetchone(): raise HTTPException(409,'Администратор уже создан. Войдите в систему.')
                db.execute('INSERT INTO accounts VALUES (?,?,?,?,?,?,?,?)',(id,body.login,body.name,'admin',1,0,encoded,json.dumps(dict(workspaces=WORKSPACES,grants=[]))))
                self.issue_session(db,id,response,request)
                self.record(db,'access.enabled',detail=body.login)
            return dict(enabled=True,user=dict(id=id,name=body.name,login=body.login,role='admin',active=True,version=0,workspaces=WORKSPACES,grants=[]))

        @app.post('/api/auth/login')
        def login(body: Login,request: Request,response: Response):
            client = request.client.host if request.client else 'unknown'
            normalized = body.login.lower()
            with self.lock,self.connect() as db:
                self.tables(db)
                cutoff=(now()-timedelta(minutes=15)).isoformat()
                db.execute('DELETE FROM login_attempts WHERE created<?',(cutoff,))
                count=db.execute('SELECT count(*) FROM login_attempts WHERE client=? AND login=?',(client,normalized)).fetchone()[0]
                total=db.execute('SELECT count(*) FROM login_attempts WHERE client=?',(client,)).fetchone()[0]
                if count>=8 or total>=40: raise HTTPException(429,'Слишком много попыток. Повторите через 15 минут.')
                # Reserve the attempt before hashing to bound parallel attempts too.
                db.execute('INSERT INTO login_attempts (client,login,created) VALUES (?,?,?)',(client,normalized,now().isoformat()))
                row=db.execute('SELECT * FROM accounts WHERE login=?',(normalized,)).fetchone()
            valid=password_matches(body.password.get_secret_value(),row[6] if row else self.dummy)
            if not row or not row[4] or not valid:
                with self.lock,self.connect() as db:
                    self.record(db,'account.login_failed',detail=normalized,principal=dict(id='anonymous',name='Неавторизованный вход'))
                raise HTTPException(401,'Неверный логин или пароль.')
            with self.lock,self.connect() as db:
                current=db.execute('SELECT * FROM accounts WHERE id=?',(row[0],)).fetchone()
                if current != row: raise HTTPException(409,'Учётная запись изменилась. Повторите вход.')
                db.execute('DELETE FROM login_attempts WHERE client=? AND login=?',(client,normalized))
                self.issue_session(db,row[0],response,request)
                self.record(db,'account.signed_in',principal=self.public(row))
            return dict(enabled=True,user=self.public(row))

        @app.post('/api/auth/logout')
        def logout(request: Request,response: Response):
            with self.lock,self.connect() as db:
                self.tables(db); db.execute('DELETE FROM sessions WHERE token=?',(digest(request.cookies.get(COOKIE,'')),))
                if request.state.user: self.record(db,'account.signed_out')
            response.delete_cookie(COOKIE,path='/')
            return dict(status='signed_out')

        @app.post('/api/auth/password')
        def change_password(body: PasswordChange,request: Request,response: Response):
            user=request.state.user
            if not user: raise HTTPException(401,'Сначала войдите в систему.')
            with self.lock,self.connect() as db: row=db.execute('SELECT * FROM accounts WHERE id=?',(user['id'],)).fetchone()
            if not password_matches(body.currentPassword.get_secret_value(),row[6]): raise HTTPException(401,'Текущий пароль неверен.')
            encoded=password_hash(body.password.get_secret_value())
            with self.lock,self.connect() as db:
                result=db.execute('UPDATE accounts SET password=?,version=version+1 WHERE id=? AND version=?',(encoded,user['id'],row[5]))
                if not result.rowcount: raise HTTPException(409,'Учётная запись изменилась. Повторите действие.')
                db.execute('DELETE FROM sessions WHERE user_id=?',(user['id'],))
                self.issue_session(db,user['id'],response,request)
                self.record(db,'account.password_changed')
            return dict(status='updated')

        @app.get('/api/access/users')
        def users():
            with self.lock,self.connect() as db:
                return [self.public(row,configured=True) for row in db.execute('SELECT * FROM accounts ORDER BY name').fetchall()]

        def write_user(body,id=None):
            self.validate_grants(body)
            encoded=password_hash(body.password.get_secret_value()) if body.password else None
            if id is None and encoded is None: raise HTTPException(422,'Для новой записи задайте пароль.')
            with self.lock,self.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                old=db.execute('SELECT * FROM accounts WHERE id=?',(id,)).fetchone() if id else None
                if id and not old: raise HTTPException(404,'Учётная запись не найдена.')
                if old and old[5]!=body.version: raise HTTPException(409,'Учётная запись изменена другим администратором. Обновите список.')
                if db.execute('SELECT 1 FROM accounts WHERE login=? AND id!=?',(body.login,id or '')).fetchone(): raise HTTPException(409,'Этот логин уже используется.')
                if old and old[3]=='admin' and old[4] and (body.role!='admin' or not body.active):
                    if db.execute("SELECT count(*) FROM accounts WHERE role='admin' AND active=1 AND id!=?",(id,)).fetchone()[0]==0:
                        raise HTTPException(422,'Нельзя отключить или понизить последнего администратора.')
                id=id or str(uuid4())
                version=old[5]+1 if old else 0
                from .families import sync_account
                permissions=sync_account(db,id,body.role,dict(workspaces=WORKSPACES if body.role=='admin' else body.workspaces,grants=[g.model_dump() for g in body.grants]),source='account.assignment',previous=old)
                permissions=json.dumps(permissions,ensure_ascii=False)
                db.execute('INSERT OR REPLACE INTO accounts VALUES (?,?,?,?,?,?,?,?)',(id,body.login,body.name,body.role,int(body.active),version,encoded or old[6],permissions))
                db.execute('DELETE FROM sessions WHERE user_id=?',(id,))
                self.record(db,'account.updated' if old else 'account.created',detail=body.login+' · '+body.role)
                return self.public(db.execute('SELECT * FROM accounts WHERE id=?',(id,)).fetchone(),configured=True)

        @app.post('/api/access/users')
        def create_user(body: Account): return write_user(body)

        @app.put('/api/access/users/{id}')
        def update_user(id: str,body: Account): return write_user(body,id)

        @app.get('/api/access/audit')
        def audit(before: int | None=None):
            with self.lock,self.connect() as db:
                rows=db.execute('SELECT id,created,actor_id,actor_name,action,workspace,revision,detail FROM audit WHERE (? IS NULL OR id<?) ORDER BY id DESC LIMIT 100',(before,before)).fetchall()
            return [dict(zip(['id','created','actorId','actorName','action','workspace','revision','detail'],row)) for row in rows]

        @app.post('/api/collaboration/presence')
        def presence(body: Presence,workspace: Literal['school','demo']='school'):
            user=actor.get()
            stamp=now()
            with self.lock,self.connect() as db:
                self.tables(db)
                db.execute('DELETE FROM presence WHERE updated<?',((stamp-timedelta(seconds=75)).isoformat(),))
                db.execute('INSERT OR REPLACE INTO presence VALUES (?,?,?,?,?,?)',(body.clientId,user['id'],workspace,user['name'],body.page,stamp.isoformat()))
                peers=db.execute("SELECT p.client_id,p.name,p.page,p.updated FROM presence p LEFT JOIN accounts a ON p.user_id=a.id WHERE p.workspace=? AND NOT (p.client_id=? AND p.user_id=?) AND (p.user_id='local' OR a.active=1 AND a.role IN ('admin','editor')) ORDER BY p.updated DESC LIMIT 30",(workspace,body.clientId,user['id'])).fetchall()
                current=db.execute('SELECT revision FROM workspaces WHERE id=?',(workspace,)).fetchone()
            return dict(revision=current[0] if current else None,peers=[dict(clientId=r[0],name=r[1],page=r[2],updated=r[3]) for r in peers])
