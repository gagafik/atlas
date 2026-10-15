"""A separate, rate-limited identity realm. Never issues a school session."""
from datetime import timedelta
import asyncio, hmac, secrets, sqlite3, threading
from fastapi import HTTPException, Request, Response
from fastapi.responses import JSONResponse
from starlette.requests import ClientDisconnect
from .access import now, digest, password_hash, password_matches
from .admissions import COOKIE, stamp
from .admission_models import FamilyLogin, FamilyRegister, FamilyRecover

def public(u):return dict(id=u['id'],login=u['login'],name=u['name'],version=u['version'])
HASH_SLOTS=threading.BoundedSemaphore(4)

def bounded_hash(function,*args):
    if not HASH_SLOTS.acquire(blocking=False):raise HTTPException(429,'Сервер проверяет другие входы. Повторите через несколько секунд.')
    try:return function(*args)
    finally:HASH_SLOTS.release()

def register(s,app):
    @app.middleware('http')
    async def guard(request,call_next):
        path=request.url.path
        if not path.startswith(('/admissions-api/','/api/admissions/')):return await call_next(request)
        unsafe=request.method not in ['GET','HEAD','OPTIONS']
        if unsafe and request.headers.get('x-atlas-request')!='1':return JSONResponse(status_code=403,content={'detail':'Откройте кабинет и повторите действие.'},headers={'Cache-Control':'no-store'})
        # JSON is bounded even for chunked requests; document streams have their own cap.
        if unsafe and '/files' not in path:
            body=bytearray()
            try:
                async with asyncio.timeout(30):
                    async for chunk in request.stream():
                        body.extend(chunk)
                        if len(body)>65536:return JSONResponse(status_code=413,content={'detail':'Запрос слишком большой.'},headers={'Cache-Control':'no-store'})
            except (TimeoutError,ClientDisconnect):return JSONResponse(status_code=408,content={'detail':'Передача запроса прервана. Повторите действие.'},headers={'Cache-Control':'no-store'})
            request._body=bytes(body)
        response=await call_next(request)
        response.headers['Cache-Control']='no-store';response.headers['X-Content-Type-Options']='nosniff';response.headers['Referrer-Policy']='no-referrer'
        return response

    def attempt(request,login,kind):
        client=request.client.host if request.client else 'unknown';cutoff=(now()-timedelta(hours=1)).isoformat()
        with s.database(True) as db:
            db.execute('DELETE FROM admission_attempts WHERE created<?',(cutoff,))
            total=db.execute('SELECT count(*) FROM admission_attempts WHERE client=?',(client,)).fetchone()[0]
            per=db.execute('SELECT count(*) FROM admission_attempts WHERE login=? AND kind=?',(digest(login),kind)).fetchone()[0]
            registrations=db.execute("SELECT count(*) FROM admission_attempts WHERE client=? AND kind='register'",(client,)).fetchone()[0]
            if total>=50 or per>=10 or kind=='register' and registrations>=10:raise HTTPException(429,'Слишком много попыток. Повторите через час.')
            db.execute('INSERT INTO admission_attempts(client,login,kind,created) VALUES (?,?,?,?)',(client,digest(login),kind,stamp()))

    def issue(db,u,request,response):
        raw=secrets.token_urlsafe(32)
        db.execute('DELETE FROM admission_sessions WHERE expires<=?',(stamp(),))
        db.execute('INSERT INTO admission_sessions VALUES (?,?,?,?)',(digest(raw),u['id'],stamp(),(now()+timedelta(hours=12)).isoformat()))
        db.execute('DELETE FROM admission_sessions WHERE user_id=? AND token NOT IN (SELECT token FROM admission_sessions WHERE user_id=? ORDER BY created DESC LIMIT 5)',(u['id'],u['id']))
        response.set_cookie(COOKIE,raw,max_age=12*3600,httponly=True,samesite='strict',secure=request.url.scheme=='https',path='/admissions-api')
        return public(u)

    @app.get('/admissions-api/session')
    def session(request:Request):
        try:
            with s.family(request) as (db,u):return dict(user=public(u))
        except HTTPException as exc:
            if exc.status_code in [401,404]:return dict(user=None)
            raise

    @app.post('/admissions-api/register')
    def signup(body:FamilyRegister,request:Request,response:Response):
        attempt(request,body.login,'register')
        with s.database() as db:s.accepting(s.campaign(db,body.campaignId,public=True))
        password=bounded_hash(password_hash,body.password.get_secret_value())
        with s.database(True) as db:
            s.accepting(s.campaign(db,body.campaignId,public=True))
            if db.execute('SELECT count(*) FROM admission_families').fetchone()[0]>=10000:raise HTTPException(413,'Лимит кабинетов достигнут. Обратитесь в школу.')
            id=secrets.token_hex(16)
            try:db.execute('INSERT INTO admission_families VALUES (?,?,?,?,?,1,?)',(id,body.login,body.name,password,digest(body.recoveryCode.get_secret_value()),stamp()))
            except sqlite3.IntegrityError:raise HTTPException(409,'Логин занят. Если кабинет уже создавался, войдите или восстановите доступ.') from None
            u=db.execute('SELECT * FROM admission_families WHERE id=?',(id,)).fetchone()
            return dict(user=issue(db,u,request,response))

    @app.post('/admissions-api/login')
    def login(body:FamilyLogin,request:Request,response:Response):
        login=body.login.lower();attempt(request,login,'login')
        with s.database() as db:row=db.execute('SELECT * FROM admission_families WHERE login=?',(login,)).fetchone()
        valid=bounded_hash(password_matches,body.password.get_secret_value(),row['password'] if row else s.access.dummy)
        if not row or not valid:raise HTTPException(401,'Неверный логин или пароль.')
        with s.database(True) as db:
            current=db.execute('SELECT * FROM admission_families WHERE id=?',(row['id'],)).fetchone()
            if dict(current)!=dict(row):raise HTTPException(409,'Доступ изменился. Повторите вход.')
            return dict(user=issue(db,row,request,response))

    @app.post('/admissions-api/recover')
    def recover(body:FamilyRecover,request:Request,response:Response):
        login=body.login.lower();attempt(request,login,'recover')
        if body.recoveryCode.get_secret_value()==body.newRecoveryCode.get_secret_value():raise HTTPException(422,'Сохраните новый резервный код.')
        with s.database() as db:row=db.execute('SELECT * FROM admission_families WHERE login=?',(login,)).fetchone()
        if not hmac.compare_digest(digest(body.recoveryCode.get_secret_value()),row['recovery'] if row else digest('invalid')) or not row:raise HTTPException(401,'Неверный логин или резервный код.')
        password=bounded_hash(password_hash,body.password.get_secret_value())
        with s.database(True) as db:
            current=db.execute('SELECT * FROM admission_families WHERE id=?',(row['id'],)).fetchone()
            if dict(current)!=dict(row):raise HTTPException(409,'Доступ уже изменился. Повторите вход.')
            db.execute('UPDATE admission_families SET password=?,recovery=?,version=version+1 WHERE id=?',(password,digest(body.newRecoveryCode.get_secret_value()),row['id']))
            db.execute('DELETE FROM admission_sessions WHERE user_id=?',(row['id'],))
            u=db.execute('SELECT * FROM admission_families WHERE id=?',(row['id'],)).fetchone()
            return dict(user=issue(db,u,request,response))

    @app.post('/admissions-api/logout')
    def logout(request:Request,response:Response):
        with s.database(True) as db:db.execute('DELETE FROM admission_sessions WHERE token=?',(digest(request.cookies.get(COOKIE,'')),))
        response.delete_cookie(COOKIE,path='/admissions-api');return dict(ok=True)
