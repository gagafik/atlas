"""School-issued, single-use enrollment codes; permissions are chosen by staff."""
from contextlib import contextmanager
from datetime import timedelta
import json
import secrets
from typing import Literal
from uuid import uuid4

from fastapi import HTTPException, Query, Request, Response
from pydantic import Field, SecretStr, ValidationError, model_validator

from .access import Account, Credentials, Grant, Setup, digest, now, password_hash, password_matches
from .models import Model


class Invitation(Model):
    recipient: str = Field(min_length=1, max_length=160)
    role: Literal['parent', 'student', 'teacher']
    grants: list[Grant] = Field(min_length=1, max_length=50)
    days: int = Field(default=7, ge=1, le=30)

    @model_validator(mode='after')
    def permissions(self):
        Account(name=self.recipient, login='invitation', role=self.role,
                workspaces=sorted({g.workspace for g in self.grants}), grants=self.grants)
        return self


class Registration(Setup):
    code: SecretStr = Field(min_length=32, max_length=128)


class LinkInvitation(Credentials):
    code: SecretStr = Field(min_length=32, max_length=128)
    currentPassword: SecretStr = Field(min_length=1, max_length=128)


def tables(db):
    db.execute('''CREATE TABLE IF NOT EXISTS access_invitations (
        id INTEGER PRIMARY KEY, token_hash TEXT NOT NULL UNIQUE, recipient TEXT NOT NULL,
        role TEXT NOT NULL, permissions TEXT NOT NULL, created TEXT NOT NULL,
        expires TEXT NOT NULL, creator_id TEXT NOT NULL, revoked TEXT,
        accepted TEXT, account_id TEXT)''')
    db.execute('''CREATE TABLE IF NOT EXISTS invitation_attempts (
        id INTEGER PRIMARY KEY, client TEXT NOT NULL, token_hash TEXT NOT NULL, created TEXT NOT NULL)''')
    db.execute('CREATE INDEX IF NOT EXISTS invitation_attempt_client ON invitation_attempts(client,created)')
    db.execute('CREATE INDEX IF NOT EXISTS invitation_attempt_token ON invitation_attempts(token_hash,created)')


def available(db, token_hash):
    row = db.execute('SELECT * FROM access_invitations WHERE token_hash=?', (token_hash,)).fetchone()
    if not row or row[8] or row[9] or row[6] <= now().isoformat():
        raise HTTPException(400, 'Приглашение недействительно, истекло или уже принято. Попросите школу выдать новое.')
    return row


def validate_targets(db, grants):
    # Read the current document inside the same transaction as the grant. Do not
    # call Access.read here: it acquires the non-reentrant writer lock itself.
    documents = {}
    for grant in grants:
        space = grant['workspace']
        if space not in documents:
            row = db.execute('SELECT document FROM workspaces WHERE id=?', (space,)).fetchone()
            documents[space] = json.loads(row[0]) if row else {}
        key = 'teachers' if grant['view'] == 'teacher' else 'students'
        ids = {item['id'] for item in documents[space].get(key, [])}
        if set(grant['entityIds']) - ids:
            raise HTTPException(422, 'Школьные записи изменились. Попросите администратора проверить приглашение.')


def public(row):
    return dict(id=row[0], recipient=row[2], role=row[3], **json.loads(row[4]),
                created=row[5], expires=row[6], accepted=row[9], accountId=row[10],
                status='accepted' if row[9] else 'revoked' if row[8] else
                'expired' if row[6] <= now().isoformat() else 'pending')


def register_invitations(app, access):
    @contextmanager
    def transaction():
        with access.lock, access.connect() as db:
            tables(db)
            db.execute('BEGIN IMMEDIATE')
            yield db

    def admin(db, request):
        user = request.state.user
        row = db.execute('SELECT role,active,version FROM accounts WHERE id=?', (user['id'],)).fetchone() if user else None
        if not row or row[0] != 'admin' or not row[1] or row[2] != user['version']:
            raise HTTPException(403, 'Управление приглашениями доступно действующему администратору.')

    def reserve(request, code):
        client = request.client.host if request.client else 'unknown'
        hashed = digest(code.get_secret_value().strip())
        with transaction() as db:
            db.execute('DELETE FROM invitation_attempts WHERE created<?', ((now()-timedelta(minutes=15)).isoformat(),))
            by_client = db.execute('SELECT count(*) FROM invitation_attempts WHERE client=?', (client,)).fetchone()[0]
            by_code = db.execute('SELECT count(*) FROM invitation_attempts WHERE token_hash=?', (hashed,)).fetchone()[0]
            if by_client >= 40 or by_code >= 10:
                raise HTTPException(429, 'Слишком много попыток. Повторите через 15 минут.')
            attempt = db.execute('INSERT INTO invitation_attempts (client,token_hash,created) VALUES (?,?,?)',
                                 (client,hashed,now().isoformat())).lastrowid
        return hashed, attempt

    def finish(db, invitation, account_id, attempt, response, request):
        # The write transaction serializes revoke, acceptance and permission changes.
        db.execute('UPDATE access_invitations SET accepted=?,account_id=? WHERE id=?',
                   (now().isoformat(), account_id, invitation[0]))
        db.execute('DELETE FROM invitation_attempts WHERE id=?', (attempt,))
        db.execute('DELETE FROM sessions WHERE user_id=?', (account_id,))
        access.issue_session(db, account_id, response, request)
        user = access.public(db.execute('SELECT * FROM accounts WHERE id=?', (account_id,)).fetchone())
        access.record(db, 'invitation.accepted', detail=f'Приглашение {invitation[0]} · {invitation[3]}', principal=user)
        return dict(enabled=True, user=user)

    @app.post('/api/access/invitations')
    def create(body: Invitation, request: Request):
        # Initialize an unvisited demo workspace, then recheck records atomically.
        for space in {g.workspace for g in body.grants}: access.read(space)
        permissions = dict(workspaces=sorted({g.workspace for g in body.grants}),
                           grants=[g.model_dump() for g in body.grants])
        raw = secrets.token_urlsafe(32)
        created = now()
        with transaction() as db:
            admin(db, request)
            validate_targets(db, permissions['grants'])
            id = db.execute('''INSERT INTO access_invitations
                (token_hash,recipient,role,permissions,created,expires,creator_id) VALUES (?,?,?,?,?,?,?)''',
                (digest(raw),body.recipient,body.role,json.dumps(permissions),created.isoformat(),
                 (created+timedelta(days=body.days)).isoformat(),request.state.user['id'])).lastrowid
            access.record(db, 'invitation.created', detail=f'Приглашение {id} · {body.recipient} · {body.role}')
            row = db.execute('SELECT * FROM access_invitations WHERE id=?', (id,)).fetchone()
        return dict(**public(row), code=raw)

    @app.get('/api/access/invitations')
    def listing(request: Request, before: int | None = Query(default=None, ge=1)):
        with transaction() as db:
            admin(db, request)
            rows = db.execute('SELECT * FROM access_invitations WHERE (? IS NULL OR id<?) ORDER BY id DESC LIMIT 51',
                              (before,before)).fetchall()
        return dict(items=[public(row) for row in rows[:50]], nextBefore=rows[49][0] if len(rows)>50 else None)

    @app.post('/api/access/invitations/{id}/revoke')
    def revoke(id: int, request: Request):
        with transaction() as db:
            admin(db, request)
            row = db.execute('SELECT * FROM access_invitations WHERE id=?', (id,)).fetchone()
            if not row: raise HTTPException(404, 'Приглашение не найдено.')
            if row[9]: raise HTTPException(409, 'Приглашение уже принято. Измените доступ в учётной записи пользователя.')
            if not row[8]:
                db.execute('UPDATE access_invitations SET revoked=? WHERE id=?', (now().isoformat(),id))
                access.record(db, 'invitation.revoked', detail=f'Приглашение {id}')
        return dict(status='revoked')

    @app.post('/api/auth/register')
    def register(body: Registration, request: Request, response: Response):
        if not request.state.auth_enabled: raise HTTPException(403, 'Школа ещё не включила учётные записи.')
        if request.state.user: raise HTTPException(409, 'Для текущего аккаунта используйте «Принять приглашение».')
        hashed, attempt = reserve(request, body.code)
        with transaction() as db: available(db, hashed)
        encoded = password_hash(body.password.get_secret_value())
        with transaction() as db:
            invitation = available(db, hashed)
            permissions = json.loads(invitation[4])
            validate_targets(db, permissions['grants'])
            if db.execute('SELECT 1 FROM accounts WHERE login=?', (body.login,)).fetchone():
                raise HTTPException(409, 'Этот логин уже используется. Выберите другой или войдите в свой аккаунт.')
            id = str(uuid4())
            db.execute('INSERT INTO accounts VALUES (?,?,?,?,?,?,?,?)',
                       (id,body.login,body.name,invitation[3],1,0,encoded,invitation[4]))
            access.record(db, 'account.registered', detail=f'Приглашение {invitation[0]}',
                          principal=dict(id=id,name=body.name))
            return finish(db, invitation, id, attempt, response, request)

    @app.post('/api/auth/invitations/accept')
    def accept(body: LinkInvitation, request: Request, response: Response):
        user = request.state.user
        if not user: raise HTTPException(401, 'Войдите в свою учётную запись.')
        hashed, attempt = reserve(request, body.code)
        with transaction() as db:
            available(db, hashed)
            row = db.execute('SELECT * FROM accounts WHERE id=?', (user['id'],)).fetchone()
        if not row or not row[4] or not password_matches(body.currentPassword.get_secret_value(), row[6]):
            raise HTTPException(401, 'Проверьте текущий пароль.')
        with transaction() as db:
            current = db.execute('SELECT * FROM accounts WHERE id=?', (user['id'],)).fetchone()
            if current != row or row[5] != user['version']:
                raise HTTPException(409, 'Учётная запись изменилась. Войдите заново.')
            invitation = available(db, hashed)
            if row[3] != invitation[3]: raise HTTPException(422, 'Роль приглашения не совпадает с ролью вашего аккаунта.')
            permissions = json.loads(row[7])
            incoming = json.loads(invitation[4])
            validate_targets(db, incoming['grants'])
            grants = {(g['workspace'],g['view']): g for g in permissions['grants']}
            for grant in incoming['grants']:
                key = (grant['workspace'],grant['view'])
                old = grants.get(key, dict(entityIds=[]))
                grants[key] = dict(grant)
                grants[key]['entityIds'] = sorted(set(old['entityIds']) | set(grant['entityIds']))
            permissions = dict(workspaces=sorted(set(permissions['workspaces']) | set(incoming['workspaces'])),
                               grants=list(grants.values()))
            try:
                Account(name=row[2],login=row[1],role=row[3],**permissions)
            except ValidationError:
                raise HTTPException(422, 'Приглашение нельзя объединить с текущим доступом. Обратитесь к администратору.') from None
            db.execute('UPDATE accounts SET permissions=?,version=version+1 WHERE id=?',
                       (json.dumps(permissions),user['id']))
            return finish(db, invitation, user['id'], attempt, response, request)
