"""User-held recovery codes: hash-only storage, scoped throttling and atomic use."""
from contextlib import contextmanager
from datetime import timedelta
import re
import secrets

from fastapi import HTTPException, Request, Response
from pydantic import Field, SecretStr
from .access import COOKIE, Credentials, digest, now, password_hash, password_matches

COUNT = 8
DAYS = 365
INVALID = 'Не удалось восстановить доступ. Проверьте логин и резервный код.'


class Manage(Credentials):
    version: int = Field(ge=0)
    currentPassword: SecretStr = Field(min_length=1, max_length=128)


class Recover(Credentials):
    login: str = Field(min_length=1, max_length=64)
    code: SecretStr = Field(min_length=1, max_length=128)
    password: SecretStr = Field(min_length=12, max_length=128)


def tables(db):
    db.execute('''CREATE TABLE IF NOT EXISTS recovery_sets (
        user_id TEXT PRIMARY KEY,version INTEGER NOT NULL,created TEXT NOT NULL,
        expires TEXT NOT NULL,password_stamp TEXT NOT NULL,revoked TEXT,last_used TEXT)''')
    db.execute('''CREATE TABLE IF NOT EXISTS recovery_codes (
        user_id TEXT NOT NULL,code_hash TEXT NOT NULL,used TEXT,PRIMARY KEY(user_id,code_hash))''')
    db.execute('''CREATE TABLE IF NOT EXISTS recovery_attempts (
        id INTEGER PRIMARY KEY,client TEXT NOT NULL,subject TEXT NOT NULL,
        kind TEXT NOT NULL,created TEXT NOT NULL)''')
    db.execute('CREATE INDEX IF NOT EXISTS recovery_attempt_client ON recovery_attempts(kind,client,created)')
    db.execute('CREATE INDEX IF NOT EXISTS recovery_attempt_subject ON recovery_attempts(kind,subject,created)')


def revoke_all(db, user_id):
    """Called in the same transaction as an account/password reset. No commit here."""
    tables(db)
    db.execute('UPDATE recovery_sets SET version=version+1,revoked=? WHERE user_id=?', (now().isoformat(), user_id))
    db.execute('DELETE FROM recovery_codes WHERE user_id=?', (user_id,))


def normalized(code):
    return re.sub(r'[\s-]', '', code).upper()


def code_hash(user_id, code):
    return digest(user_id + ':' + normalized(code))


def batch(db, account):
    row = db.execute('SELECT version,created,expires,password_stamp,revoked,last_used FROM recovery_sets WHERE user_id=?', (account[0],)).fetchone()
    if not row:
        return dict(version=0, status='none', remaining=0, created=None, expires=None, lastUsed=None)
    count = db.execute('SELECT count(*) FROM recovery_codes WHERE user_id=? AND used IS NULL', (account[0],)).fetchone()[0]
    status = ('revoked' if row[4] or row[3] != digest(account[6]) else
              'expired' if row[2] <= now().isoformat() else 'active' if count else 'exhausted')
    return dict(version=row[0], status=status, remaining=count if status=='active' else 0,
                created=row[1], expires=row[2], lastUsed=row[5])


def register(access, app):
    @contextmanager
    def transaction():
        with access.lock, access.connect() as db:
            tables(db)
            db.execute('BEGIN IMMEDIATE')
            yield db

    def authenticated(db, request):
        user = request.state.user
        row = db.execute('''SELECT a.* FROM accounts a JOIN sessions s ON s.user_id=a.id
            WHERE a.id=? AND a.active=1 AND s.token=? AND s.expires>?''',
            (user['id'], digest(request.cookies.get(COOKIE,'')), now().isoformat())).fetchone() if user else None
        if not row or row[5] != user['version']:
            raise HTTPException(401, 'Сеанс изменился. Войдите снова.')
        return row

    def reserve(db, request, subject, kind):
        client = request.client.host if request.client else 'unknown'
        cutoff = (now()-timedelta(minutes=15)).isoformat()
        db.execute('DELETE FROM recovery_attempts WHERE created<?', (cutoff,))
        count = db.execute('SELECT count(*) FROM recovery_attempts WHERE kind=? AND client=? AND subject=?', (kind,client,subject)).fetchone()[0]
        total = db.execute('SELECT count(*) FROM recovery_attempts WHERE kind=? AND client=?', (kind,client)).fetchone()[0]
        own = db.execute('SELECT count(*) FROM recovery_attempts WHERE kind=? AND subject=?', (kind,subject)).fetchone()[0]
        if count >= 8 or total >= (120 if kind=='manage' else 40) or kind=='manage' and own >= 8:
            raise HTTPException(429, 'Слишком много попыток. Повторите через 15 минут.')
        return db.execute('INSERT INTO recovery_attempts(client,subject,kind,created) VALUES (?,?,?,?)',
                          (client,subject,kind,now().isoformat())).lastrowid

    def manage_start(body, request):
        # Commit the reservation before expensive password verification. A failed
        # password must not roll it back, including under parallel requests.
        with transaction() as db:
            row = authenticated(db,request)
            attempt = reserve(db,request,row[0],'manage')
        if not password_matches(body.currentPassword.get_secret_value(),row[6]):
            raise HTTPException(401, 'Текущий пароль неверен.')
        return row, attempt

    def manage_finish(db, body, request, row):
        current = authenticated(db,request)
        if current != row or batch(db,current)['version'] != body.version:
            raise HTTPException(409, 'Учётная запись или резервные коды изменились. Обновите раздел.')
        return current

    @app.get('/api/auth/recovery/options')
    def options(request: Request):
        with transaction() as db:
            row = authenticated(db,request)
            return batch(db,row) | dict(count=COUNT, days=DAYS)

    @app.post('/api/auth/recovery/codes')
    def issue(body: Manage, request: Request):
        row, attempt = manage_start(body,request)
        codes = [secrets.token_hex(16).upper() for _ in range(COUNT)]
        with transaction() as db:
            manage_finish(db,body,request,row)
            stamp = now()
            db.execute('DELETE FROM recovery_codes WHERE user_id=?', (row[0],))
            db.execute('''INSERT INTO recovery_sets VALUES (?,?,?,?,?,NULL,NULL)
                ON CONFLICT(user_id) DO UPDATE SET version=excluded.version,created=excluded.created,
                expires=excluded.expires,password_stamp=excluded.password_stamp,revoked=NULL,last_used=NULL''',
                (row[0],body.version+1,stamp.isoformat(),(stamp+timedelta(days=DAYS)).isoformat(),digest(row[6])))
            db.executemany('INSERT INTO recovery_codes VALUES (?,?,NULL)', [(row[0],code_hash(row[0],code)) for code in codes])
            db.execute('DELETE FROM recovery_attempts WHERE id=?', (attempt,))
            access.record(db,'account.recovery_issued',detail=f'Набор {body.version+1}, кодов {COUNT}',principal=access.public(row))
            value = batch(db,row)
        return value | dict(codes=['-'.join(code[i:i+4] for i in range(0,len(code),4)) for code in codes])

    @app.post('/api/auth/recovery/revoke')
    def revoke(body: Manage, request: Request):
        row, attempt = manage_start(body,request)
        with transaction() as db:
            manage_finish(db,body,request,row)
            revoke_all(db,row[0])
            db.execute('DELETE FROM recovery_attempts WHERE id=?', (attempt,))
            access.record(db,'account.recovery_revoked',principal=access.public(row))
            return batch(db,row)

    def usable(db, login, code):
        row = db.execute('SELECT * FROM accounts WHERE login=?', (login,)).fetchone()
        if not row or not row[4] or batch(db,row)['status'] != 'active':
            return None
        found = db.execute('SELECT used FROM recovery_codes WHERE user_id=? AND code_hash=?', (row[0],code_hash(row[0],code))).fetchone()
        return row if found is not None and found[0] is None else None

    @app.post('/api/auth/recover')
    def recover(body: Recover, request: Request, response: Response):
        login = body.login.lower()
        code = body.code.get_secret_value()
        with transaction() as db:
            attempt = reserve(db,request,digest(login),'recover')
            row = usable(db,login,code)
        if row is None: raise HTTPException(400, INVALID)
        encoded = password_hash(body.password.get_secret_value())
        with transaction() as db:
            current = usable(db,login,code)
            if current is None or current != row: raise HTTPException(400, INVALID)
            stamp = now().isoformat()
            used = db.execute('UPDATE recovery_codes SET used=? WHERE user_id=? AND code_hash=? AND used IS NULL', (stamp,row[0],code_hash(row[0],code)))
            if used.rowcount != 1: raise HTTPException(400, INVALID)
            db.execute('UPDATE accounts SET password=?,version=version+1 WHERE id=?', (encoded,row[0]))
            db.execute('UPDATE recovery_sets SET version=version+1,last_used=?,password_stamp=? WHERE user_id=?', (stamp,digest(encoded),row[0]))
            db.execute('DELETE FROM sessions WHERE user_id=?', (row[0],))
            db.execute('DELETE FROM recovery_attempts WHERE id=?', (attempt,))
            access.record(db,'account.recovered',principal=access.public(row))
        response.delete_cookie(COOKIE,path='/')
        return dict(status='recovered')
