"""Opt-in device delivery. Private subscriptions, coalescing outbox, fenced retries."""
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import base64
import json
import logging
import os
import re
import threading
import time
from urllib.parse import urlsplit
from uuid import uuid4

from fastapi import HTTPException, Request
from pydantic import Field, SecretStr, field_validator
from starlette.concurrency import run_in_threadpool
from .access import COOKIE, Credentials, digest, now
from .notifications import PUSH_LABELS, SYSTEM, encoded, permission_key, preference_map, quiet_until

PROVIDERS = {'fcm.googleapis.com', 'updates.push.services.mozilla.com', 'web.push.apple.com'}
MAX_DEVICES = 10
MAX_ATTEMPTS = 6
LEASE = 60
PAYLOAD = '{"type":"atlas-update"}'


def push_payload(category):
    """The only thing a push carries: a category id from the fixed list; the service worker maps it to a fixed label."""
    return encoded(dict(type='atlas-update', category=category if category in PUSH_LABELS else 'other'))


def b64(value):
    return base64.urlsafe_b64encode(value).decode().rstrip('=')


def decode(value):
    if not re.fullmatch(r'[A-Za-z0-9_-]+={0,2}', value): raise ValueError('Недопустимый ключ подписки.')
    return base64.urlsafe_b64decode(value + '=' * (-len(value) % 4))


def endpoint_host(value):
    try:
        url = urlsplit(value)
        if (url.scheme != 'https' or url.hostname not in PROVIDERS or url.port not in [None,443]
                or url.username or url.password or url.fragment or not url.path or
                any(ord(c) < 33 or ord(c) > 126 for c in value) or '\\' in value):
            raise ValueError()
        return url.hostname
    except ValueError:
        raise ValueError('Этот адрес службы уведомлений не поддерживается.') from None


class Keys(Credentials):
    p256dh: SecretStr = Field(min_length=80, max_length=100)
    auth: SecretStr = Field(min_length=20, max_length=30)

    @field_validator('p256dh', 'auth')
    @classmethod
    def valid_key(cls, value, info):
        raw = decode(value.get_secret_value())
        if info.field_name == 'auth':
            if len(raw) != 16: raise ValueError('Недопустимый ключ подписки.')
        else:
            from cryptography.hazmat.primitives.asymmetric import ec
            if len(raw) != 65: raise ValueError('Недопустимый ключ подписки.')
            try: ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(),raw)
            except ValueError: raise ValueError('Недопустимый ключ подписки.') from None
        return value


class Subscription(Credentials):
    endpoint: SecretStr = Field(min_length=20,max_length=2048)
    keys: Keys
    expirationTime: float | None = Field(default=None,gt=0,allow_inf_nan=False)
    publicKey: str = Field(min_length=80,max_length=100)
    name: str = Field(min_length=1,max_length=80)
    version: int = Field(default=0,ge=0)

    @field_validator('endpoint')
    @classmethod
    def valid_endpoint(cls, value):
        endpoint_host(value.get_secret_value())
        return value


class DeviceVersion(Credentials):
    version: int = Field(ge=1)


def tables(db):
    db.execute('''CREATE TABLE IF NOT EXISTS push_config (
        id INTEGER PRIMARY KEY CHECK(id=1),public_key TEXT NOT NULL,private_key TEXT NOT NULL,created REAL NOT NULL)''')
    db.execute('''CREATE TABLE IF NOT EXISTS push_devices (
        id TEXT PRIMARY KEY,endpoint_hash TEXT UNIQUE NOT NULL,endpoint TEXT NOT NULL,
        p256dh TEXT NOT NULL,auth TEXT NOT NULL,user_id TEXT NOT NULL,session_hash TEXT NOT NULL,
        account_version INTEGER NOT NULL,version INTEGER NOT NULL,name TEXT NOT NULL,
        created REAL NOT NULL,updated REAL NOT NULL,expires REAL NOT NULL,active INTEGER NOT NULL,
        last_sent REAL,last_status TEXT NOT NULL,last_test REAL)''')
    db.execute('CREATE INDEX IF NOT EXISTS push_device_owner ON push_devices(user_id,active,account_version)')
    db.execute('''CREATE TABLE IF NOT EXISTS push_outbox (
        device_id TEXT PRIMARY KEY,device_version INTEGER NOT NULL,notification_id INTEGER NOT NULL,
        generation INTEGER NOT NULL,status TEXT NOT NULL,attempts INTEGER NOT NULL,available REAL NOT NULL,
        expires REAL NOT NULL,lease TEXT,lease_until REAL,last_error TEXT,completed REAL)''')
    db.execute('CREATE INDEX IF NOT EXISTS push_outbox_due ON push_outbox(status,available)')


def revoke_session(db, user_id, session_hash, endpoint_hash=None):
    tables(db)
    db.execute('''UPDATE push_devices SET active=0,version=version+1,last_status='signed_out'
        WHERE user_id=? AND active=1 AND (session_hash=? OR endpoint_hash=?)''', (user_id,session_hash,endpoint_hash))
    db.execute("UPDATE push_outbox SET status='cancelled',lease=NULL,last_error='signed_out' WHERE device_id IN (SELECT id FROM push_devices WHERE user_id=? AND active=0)", (user_id,))


def enqueue(db, user, notice_id, *, device_id=None, clock=time.time, quiet_until=None, immediate=False):
    """Inside the producer transaction; no I/O or key generation on the save path. quiet_until: epoch second before which the push waits.

    immediate: a device test replaces even a job that waits for the quiet hours to end."""
    if user['id'] == 'local': return
    stamp = clock()
    until = quiet_until or 0
    devices = db.execute('''SELECT id,version FROM push_devices WHERE user_id=? AND active=1
        AND account_version=? AND expires>? AND (? IS NULL OR id=?)''',
        (user['id'],user['version'],stamp,device_id,device_id)).fetchall()
    for id, version in devices:
        db.execute('''INSERT INTO push_outbox VALUES (?,?,?,1,'queued',0,?,?,NULL,NULL,NULL,NULL)
            ON CONFLICT(device_id) DO UPDATE SET device_version=excluded.device_version,
            notification_id=excluded.notification_id,generation=push_outbox.generation+1,
            status=CASE WHEN push_outbox.status='sending' AND push_outbox.device_version=excluded.device_version THEN 'sending' ELSE 'queued' END,
            attempts=CASE WHEN push_outbox.status IN ('queued','sending') AND push_outbox.device_version=excluded.device_version THEN push_outbox.attempts ELSE 0 END,
            available=CASE WHEN ? THEN excluded.available WHEN push_outbox.status='queued' AND push_outbox.device_version=excluded.device_version THEN max(push_outbox.available,?) ELSE excluded.available END,
            expires=excluded.expires,completed=NULL''', (id,version,notice_id,max(stamp+2,until),max(stamp+86400,until+3600),int(immediate),until))


def retry_seconds(value, stamp):
    try:
        delay = float(value) if str(value).strip().isdigit() else parsedate_to_datetime(value).timestamp()-stamp
        return max(0,min(86400,delay))
    except (ValueError,TypeError,OverflowError): return 0


def transport(subscription, private_key, subject, payload=PAYLOAD):
    """Only known HTTPS push services; no redirects, proxies or response bodies."""
    import requests
    from pywebpush import webpush, WebPushException
    endpoint_host(subscription['endpoint'])

    class PushSession(requests.Session):
        def post(self, url, **kwargs):
            endpoint_host(url)
            with super().post(url,**kwargs,allow_redirects=False,stream=True) as original:
                # pywebpush inspects response.text even with verbose=False.
                result=requests.Response()
                result.status_code=original.status_code
                result.headers=original.headers.copy()
                result._content=b''
                return result
    with PushSession() as session:
        session.trust_env=False
        try:
            response=webpush(subscription_info=subscription,data=payload,vapid_private_key=private_key,
                vapid_claims={'sub':subject},ttl=300,timeout=(3,8),requests_session=session,
                headers={'Urgency':'normal','Topic':'atlas-updates'})
        except WebPushException as exc:
            if exc.response is None: return 0,None
            response=exc.response
        except requests.RequestException:
            return 0,None
        return response.status_code,response.headers.get('Retry-After')


class PushService:
    def __init__(self, access, *, send=transport, clock=time.time):
        self.access,self.send,self.clock=access,send,clock
        self.stop=threading.Event()
        self.threads=[]

    def subject(self):
        if os.environ.get('ATLAS_WEB_PUSH_ENABLED') != '1': return None
        subject=os.environ.get('ATLAS_WEB_PUSH_SUBJECT','').strip()
        if not re.fullmatch(r'mailto:[^\s@]+@[^\s@]+\.[^\s@]+',subject):
            raise ValueError('Для Web Push задайте ATLAS_WEB_PUSH_SUBJECT=mailto:адрес администратора.')
        return subject

    @contextmanager
    def transaction(self):
        with self.access.lock,self.access.connect() as db:
            self.access.tables(db)
            from .notifications import Notifications
            Notifications(self.access).tables(db)
            db.execute('BEGIN IMMEDIATE')
            yield db

    def config(self,db):
        row=db.execute('SELECT public_key,private_key FROM push_config WHERE id=1').fetchone()
        if row:return row
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.hazmat.primitives.serialization import Encoding,PublicFormat
        private=ec.generate_private_key(ec.SECP256R1())
        public=b64(private.public_key().public_bytes(Encoding.X962,PublicFormat.UncompressedPoint))
        secret=b64(private.private_numbers().private_value.to_bytes(32,'big'))
        db.execute('INSERT INTO push_config VALUES (1,?,?,?)',(public,secret,self.clock()))
        return public,secret

    def authenticated(self,db,request):
        user=request.state.user
        row=db.execute('''SELECT a.* FROM accounts a JOIN sessions s ON s.user_id=a.id
            WHERE a.id=? AND a.active=1 AND s.token=? AND s.expires>?''',
            (user['id'],digest(request.cookies.get(COOKIE,'')),now().isoformat())).fetchone() if user else None
        if not row or row[5]!=user['version']:raise HTTPException(401,'Сеанс изменился. Войдите снова.')
        return row

    def devices(self,db,row):
        stamp=self.clock()
        rows=db.execute('''SELECT id,endpoint_hash,version,name,created,updated,expires,active,
            account_version,last_sent,last_status FROM push_devices WHERE user_id=? ORDER BY updated DESC''',(row[0],)).fetchall()
        return [dict(id=r[0],fingerprint=r[1],version=r[2],name=r[3],created=r[4],updated=r[5],expires=r[6],
            active=bool(r[7] and r[8]==row[5] and r[6]>stamp),lastSent=r[9],
            status='account_changed' if r[8]!=row[5] else 'expired' if r[6]<=stamp else r[10]) for r in rows]

    def claim(self):
        stamp=self.clock()
        with self.transaction() as db:
            db.execute("UPDATE push_outbox SET status='queued',lease=NULL,available=? WHERE status='sending' AND lease_until<=?",(stamp,stamp))
            db.execute("UPDATE push_outbox SET status='failed',last_error='expired',completed=? WHERE status='queued' AND (expires<=? OR attempts>=?)",(stamp,stamp,MAX_ATTEMPTS))
            row=db.execute("SELECT device_id,device_version,notification_id,generation,attempts FROM push_outbox WHERE status='queued' AND available<=? ORDER BY available,device_id LIMIT 1",(stamp,)).fetchone()
            if not row:return None
            token=uuid4().hex
            db.execute("UPDATE push_outbox SET status='sending',attempts=attempts+1,lease=?,lease_until=? WHERE device_id=?",(token,stamp+LEASE,row[0]))
            return dict(device=row[0],version=row[1],notice=row[2],generation=row[3],attempt=row[4]+1,token=token)

    def prepare(self,job):
        """Recheck immediately before network I/O; a session's natural expiry is OK."""
        with self.transaction() as db:
            queue=db.execute("SELECT 1 FROM push_outbox WHERE device_id=? AND lease=? AND status='sending' AND lease_until>? AND expires>?",(job['device'],job['token'],self.clock(),self.clock())).fetchone()
            device=db.execute('SELECT * FROM push_devices WHERE id=?',(job['device'],)).fetchone()
            account=db.execute('SELECT * FROM accounts WHERE id=?',(device[5],)).fetchone() if device else None
            if not queue or not device or not account:return None
            valid=device[13] and device[8]==job['version'] and device[12]>self.clock() and account[4] and account[5]==device[7]
            if not valid:return None
            prefs=preference_map(db,[account[0]])[account[0]]
            user=self.access.public(account)
            cutoff=datetime.fromtimestamp(self.clock()-86400,timezone.utc).isoformat()
            # The newest unread entry whose category is not muted names the push; muting everything cancels it.
            notice=db.execute('''SELECT coalesce(category,'other') FROM notifications WHERE user_id=? AND permissions=? AND read_at IS NULL
                AND id<=? AND created>=? AND coalesce(category,'other') NOT IN (SELECT value FROM json_each(?)) ORDER BY id DESC LIMIT 1''',
                (user['id'],permission_key(user),job['notice'],cutoff,encoded(sorted(prefs['muted'])))).fetchone()
            if not notice:return None
            # Quiet hours delay the push, never the inbox entry; a device test ignores them.
            until=quiet_until(prefs,self.clock()) if notice[0]!=SYSTEM else None
            if until:return dict(defer=until)
            _,private=self.config(db)
            return dict(endpoint=device[2],keys=dict(p256dh=device[3],auth=device[4])),private,push_payload(notice[0])

    def defer(self,job,until):
        """Put a claimed job back until the quiet hours end; a newer event keeps its generation."""
        with self.transaction() as db:
            db.execute("UPDATE push_outbox SET status='queued',available=?,lease=NULL,lease_until=NULL,attempts=max(attempts-1,0),expires=max(expires,?) WHERE device_id=? AND lease=?",
                       (until,until+3600,job['device'],job['token']))

    def finish(self,job,status,retry_after=None):
        stamp=self.clock()
        with self.transaction() as db:
            row=db.execute("SELECT generation,expires FROM push_outbox WHERE device_id=? AND lease=? AND status='sending'",(job['device'],job['token'])).fetchone()
            device=db.execute('SELECT active,version FROM push_devices WHERE id=?',(job['device'],)).fetchone()
            if not row or not device or not device[0] or device[1]!=job['version']:return
            if status is None:
                # A newer unread event may have arrived after this job was claimed.
                # Recheck that generation instead of cancelling it with the old one.
                state,error,delay=('queued' if row[0]!=job['generation'] else 'cancelled'),'access_or_read',0
            elif 200<=status<300:
                state,error,delay=('queued' if row[0]!=job['generation'] else 'sent'),None,2
                db.execute("UPDATE push_devices SET last_sent=?,last_status='accepted' WHERE id=?",(stamp,job['device']))
            elif status in [404,410]:
                state,error,delay='failed','subscription_expired',0
                db.execute("UPDATE push_devices SET active=0,version=version+1,last_status='subscription_expired' WHERE id=?",(job['device'],))
            else:
                retryable=status==0 or status in [408,425,429] or status>=500
                delay=max(min(3600,30*2**(job['attempt']-1)),retry_seconds(retry_after,stamp))
                state='queued' if retryable and job['attempt']<MAX_ATTEMPTS and stamp+delay<row[1] else 'failed'
                error='network' if status==0 else f'http_{status}'
                db.execute('UPDATE push_devices SET last_status=? WHERE id=?',(error,job['device']))
            db.execute('''UPDATE push_outbox SET status=?,available=?,lease=NULL,lease_until=NULL,last_error=?,
                completed=?,attempts=CASE WHEN ? THEN 0 ELSE attempts END WHERE device_id=? AND lease=?''',
                (state,stamp+delay,error,None if state=='queued' else stamp,status is not None and 200<=status<300,job['device'],job['token']))

    def process_one(self):
        subject=self.subject()
        if not subject:return False
        job=self.claim()
        if not job:return False
        prepared=self.prepare(job)
        if prepared is None:self.finish(job,None);return True
        if isinstance(prepared,dict):self.defer(job,prepared['defer']);return True
        try:status,retry=self.send(prepared[0],prepared[1],subject,prepared[2])
        except Exception:
            # Endpoint URLs, credentials and provider response bodies are private.
            status,retry=0,None
        self.finish(job,status,retry)
        return True

    def start(self):
        self.threads=[thread for thread in self.threads if thread.is_alive()]
        if not self.subject() or self.threads:return
        with self.transaction() as db:self.config(db)
        self.stop.clear()
        def work():
            while not self.stop.is_set():
                try:progress=self.process_one()
                except Exception as exc:
                    logging.getLogger(__name__).warning('Web Push worker error: %s',type(exc).__name__)
                    progress=False
                self.stop.wait(0.05 if progress else 2)
        for index in range(4):
            thread=threading.Thread(target=work,name=f'atlas-push-{index}',daemon=True)
            self.threads.append(thread);thread.start()

    def shutdown(self):
        self.stop.set()
        for thread in self.threads:thread.join(timeout=15)
        self.threads=[thread for thread in self.threads if thread.is_alive()]

    def register(self,app):
        previous=app.router.lifespan_context
        @asynccontextmanager
        async def lifespan(application):
            async with previous(application):
                await run_in_threadpool(self.start)
                try:yield
                finally:await run_in_threadpool(self.shutdown)
        app.router.lifespan_context=lifespan

        @app.get('/api/notifications/push/options')
        def options(request:Request):
            if not request.state.user:return dict(configured=False,reason='login',publicKey=None,devices=[])
            subject=self.subject()
            with self.transaction() as db:
                row=self.authenticated(db,request)
                public=self.config(db)[0] if subject else None
                return dict(configured=bool(subject),reason=None if subject else 'server',publicKey=public,devices=self.devices(db,row),limit=MAX_DEVICES)

        @app.post('/api/notifications/push/devices')
        def subscribe(body:Subscription,request:Request):
            if not self.subject():raise HTTPException(409,'Фоновая доставка ещё не настроена администратором.')
            stamp=self.clock()
            expires=min(body.expirationTime/1000 if body.expirationTime else stamp+180*86400,stamp+180*86400)
            if expires<=stamp:raise HTTPException(422,'Срок подписки браузера истёк. Включите уведомления заново.')
            endpoint=body.endpoint.get_secret_value();fingerprint=digest(endpoint)
            with self.transaction() as db:
                row=self.authenticated(db,request)
                if self.config(db)[0]!=body.publicKey:raise HTTPException(409,'Ключ сервера изменился. Обновите раздел и включите уведомления заново.')
                old=db.execute('SELECT id,user_id,version,created,active,account_version,expires FROM push_devices WHERE endpoint_hash=?',(fingerprint,)).fetchone()
                if old and old[1]!=row[0]:raise HTTPException(409,'Подписка связана с другим аккаунтом. Отключите подписку браузера и включите её снова.')
                if (old[2] if old else 0)!=body.version:raise HTTPException(409,'Устройство изменилось. Обновите список.')
                # Only working devices count; old inactive entries are bounded.
                count=db.execute('SELECT count(*) FROM push_devices WHERE user_id=? AND active=1 AND account_version=? AND expires>?',(row[0],row[5],stamp)).fetchone()[0]
                reviving=not old or not (old[4] and old[5]==row[5] and old[6]>stamp)
                if reviving and count>=MAX_DEVICES:raise HTTPException(409,'Уже подключено 10 устройств. Отключите или удалите ненужное.')
                id=old[0] if old else uuid4().hex
                version=(old[2] if old else 0)+1
                db.execute('''INSERT OR REPLACE INTO push_devices VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,1,NULL,'enabled',NULL)''',
                    (id,fingerprint,endpoint,body.keys.p256dh.get_secret_value(),body.keys.auth.get_secret_value(),row[0],digest(request.cookies.get(COOKIE,'')),row[5],version,body.name,old[3] if old else stamp,stamp,expires))
                db.execute('DELETE FROM push_outbox WHERE device_id=?',(id,))
                stale=[(r[0],) for r in db.execute('SELECT id FROM push_devices WHERE user_id=? AND id!=? AND NOT(active=1 AND account_version=? AND expires>?) ORDER BY updated DESC LIMIT -1 OFFSET ?',(row[0],id,row[5],stamp,MAX_DEVICES))]
                db.executemany('DELETE FROM push_devices WHERE id=?',stale);db.executemany('DELETE FROM push_outbox WHERE device_id=?',stale)
                self.access.record(db,'push.enabled',detail=id,principal=self.access.public(row))
                return next(d for d in self.devices(db,row) if d['id']==id)

        @app.post('/api/notifications/push/devices/{id}/remove')
        def remove(id:str,body:DeviceVersion,request:Request):
            with self.transaction() as db:
                row=self.authenticated(db,request)
                device=db.execute('SELECT version FROM push_devices WHERE id=? AND user_id=?',(id,row[0])).fetchone()
                if not device:raise HTTPException(404,'Устройство не найдено.')
                if device[0]!=body.version:raise HTTPException(409,'Устройство изменилось. Обновите список.')
                db.execute('DELETE FROM push_devices WHERE id=?',(id,))
                db.execute('DELETE FROM push_outbox WHERE device_id=?',(id,))
                self.access.record(db,'push.removed',detail=id,principal=self.access.public(row))
                return dict(status='removed')

        @app.post('/api/notifications/push/devices/{id}/test',status_code=202)
        def test(id:str,body:DeviceVersion,request:Request):
            if not self.subject():raise HTTPException(409,'Фоновая доставка ещё не настроена администратором.')
            with self.transaction() as db:
                row=self.authenticated(db,request)
                device=db.execute('SELECT version,active,account_version,last_test,expires FROM push_devices WHERE id=? AND user_id=?',(id,row[0])).fetchone()
                if not device:raise HTTPException(404,'Устройство не найдено.')
                if device[0]!=body.version or not device[1] or device[2]!=row[5] or device[4]<=self.clock():raise HTTPException(409,'Включите уведомления на устройстве заново.')
                if device[3] and device[3]>self.clock()-60:raise HTTPException(429,'Проверку можно повторить через минуту.')
                user=self.access.public(row)
                notice=db.execute('INSERT INTO notifications (user_id,permissions,workspace,revision,created,action,category) VALUES (?,?,?,?,?,?,?)',
                    (row[0],permission_key(user),'school',0,now().isoformat(),'push.test',SYSTEM)).lastrowid
                db.execute('DELETE FROM notifications WHERE user_id=? AND id NOT IN (SELECT id FROM notifications WHERE user_id=? ORDER BY id DESC LIMIT 500)',(row[0],row[0]))
                enqueue(db,user,notice,device_id=id,clock=self.clock,immediate=True)
                db.execute('UPDATE push_devices SET last_test=? WHERE id=?',(self.clock(),id))
                return dict(status='queued')

        @app.get('/api/access/push/status')
        def status(request:Request):
            with self.transaction() as db:
                row=self.authenticated(db,request)
                if row[3]!='admin':raise HTTPException(403,'Доступно только администратору.')
                counts=dict(db.execute('SELECT status,count(*) FROM push_outbox GROUP BY status').fetchall())
                devices=db.execute('SELECT count(*) FROM push_devices WHERE active=1').fetchone()[0]
                return dict(configured=bool(self.subject()),devices=devices,queue=counts,workers=sum(t.is_alive() for t in self.threads))
