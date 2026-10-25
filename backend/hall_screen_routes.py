"""HTTP routes for hall screens: administrator management and the tokenised public display."""
from contextlib import closing
from datetime import datetime,time,timedelta,timezone
from uuid import uuid4
import hashlib,secrets,sqlite3

from fastapi import HTTPException,Query,Request,Response
from fastapi.responses import JSONResponse
from .access import digest
from .hall_screen import (MAX_ANNOUNCEMENTS,MAX_SCREENS,NAMES,TOKEN,ZONE,AnnouncementEdit,LinkIssue,NewAnnouncement,NewScreen,ScreenEdit,Versioned,iso)
from .notifications import encoded


def not_found():
    # One answer for a missing, malformed, revoked, expired or archived token.
    return JSONResponse(status_code=404,content={'detail':'Экран недоступен.'},headers={'Cache-Control':'no-store','Referrer-Policy':'no-referrer','X-Robots-Tag':'noindex, nofollow'})


def register(service,app,Workspace):
    access=service.access

    def admin(user):
        if user['role']!='admin':raise HTTPException(403,'Информационные экраны настраивает администратор.')

    def building(snap,value):
        if value and value not in {b.id for b in snap.state.campus.buildings}:raise HTTPException(422,'Выберите здание из списка.')

    def editable(db,workspace,id,version):
        """A current, unarchived screen at the version the administrator saw."""
        row=service.screen(db,workspace,id)
        if row['version']!=version:raise HTTPException(409,'Экран изменился. Обновите список.')
        if row['status']!='active':raise HTTPException(409,'Экран убран.')
        return row

    def reload(db,workspace,id,snap,instant):return service.summary(service.screen(db,workspace,id),snap,instant)

    def capacity(db,workspace,instant):
        if db.execute("SELECT count(*) FROM hall_announcements WHERE workspace=? AND status='active' AND ends>?",(workspace,iso(instant))).fetchone()[0]>=MAX_ANNOUNCEMENTS:
            raise HTTPException(422,f'Не больше {MAX_ANNOUNCEMENTS} действующих объявлений.')

    def screens(db,workspace,ids):
        active={r[0] for r in db.execute("SELECT id FROM hall_screens WHERE workspace=? AND status='active'",(workspace,))}
        if not set(ids)<=active:raise HTTPException(422,'Выберите действующие экраны.')

    @app.get('/api/hall/options')
    def options(request:Request,workspace:Workspace='school'):
        with service.context(request,workspace) as (db,user,snap):
            admin(user);instant=service.instant()
            rows=db.execute('SELECT * FROM hall_screens WHERE workspace=? ORDER BY status,created,rowid',(workspace,)).fetchall()
            # Every announcement that can still be shown comes first, whatever its start; only the finished ones are cut.
            moment=iso(instant)
            notes=db.execute("SELECT * FROM hall_announcements WHERE workspace=? AND status='active' AND ends>?",(workspace,moment)).fetchall()
            notes+=db.execute("SELECT * FROM hall_announcements WHERE workspace=? AND ends>=? AND NOT (status='active' AND ends>?) ORDER BY starts DESC,seq DESC LIMIT 100",(workspace,iso(instant-timedelta(days=30)),moment)).fetchall()
            notes.sort(key=lambda r:(r['starts'],r['seq']),reverse=True)
            return dict(screens=[service.summary(r,snap,instant) for r in rows],announcements=[service.announcement_summary(r,instant) for r in notes],
                        buildings=[dict(id=b.id,name=b.name) for b in snap.state.campus.buildings],teacherNames=NAMES,
                        today=instant.astimezone(ZONE).date().isoformat(),timeZone='Asia/Qyzylorda',maxScreens=MAX_SCREENS)

    @app.post('/api/hall/screens')
    def create_screen(body:NewScreen,request:Request,workspace:Workspace='school'):
        with service.context(request,workspace,True) as (db,user,snap):
            admin(user);key,previous=service.replay(db,user,body)
            if previous:return previous
            building(snap,body.buildingId);instant=service.instant()
            if db.execute("SELECT count(*) FROM hall_screens WHERE workspace=? AND status='active'",(workspace,)).fetchone()[0]>=MAX_SCREENS:
                raise HTTPException(422,f'Не больше {MAX_SCREENS} действующих экранов.')
            id=uuid4().hex;data=body.model_dump(exclude={'requestId'})
            db.execute("INSERT INTO hall_screens(id,workspace,version,status,created,updated,document) VALUES (?,?,1,'active',?,?,?)",(id,workspace,iso(instant),iso(instant),encoded(data)))
            service.history(db,workspace,user,'screen',id,'created','',data)
            access.record(db,'hall.created',workspace,detail=body.name)
            return service.remember(db,user,body,key,reload(db,workspace,id,snap,instant))

    @app.put('/api/hall/screens/{id}')
    def edit_screen(id:str,body:ScreenEdit,request:Request,workspace:Workspace='school'):
        with service.context(request,workspace,True) as (db,user,snap):
            admin(user);row=editable(db,workspace,id,body.version);building(snap,body.buildingId);instant=service.instant()
            data=body.model_dump(exclude={'version'})
            db.execute('UPDATE hall_screens SET version=version+1,updated=?,document=? WHERE id=?',(iso(instant),encoded(data),id))
            service.history(db,workspace,user,'screen',id,'updated','',dict(before=row['data'],after=data))
            access.record(db,'hall.updated',workspace,detail=body.name)
            return reload(db,workspace,id,snap,instant)

    @app.post('/api/hall/screens/{id}/link')
    def issue_link(id:str,body:LinkIssue,request:Request,workspace:Workspace='school'):
        with service.context(request,workspace,True) as (db,user,snap):
            admin(user);row=editable(db,workspace,id,body.version);instant=service.instant();today=instant.astimezone(ZONE).date()
            if body.expiresOn and not today<=body.expiresOn<=today+timedelta(days=366):raise HTTPException(422,'Срок ссылки — от сегодня до года.')
            # The link works through the whole last day in school time.
            expires=iso(datetime.combine(body.expiresOn+timedelta(days=1),time(0),ZONE)) if body.expiresOn else None
            raw=secrets.token_urlsafe(32)
            db.execute('UPDATE hall_screens SET version=version+1,updated=?,token=?,token_issued=?,token_expires=?,last_seen=NULL WHERE id=?',(iso(instant),digest(raw),iso(instant),expires,id))
            version=row['version']+1
            service.history(db,workspace,user,'screen',id,'link_issued','',dict(expiresOn=body.expiresOn.isoformat() if body.expiresOn else None))
            access.record(db,'hall.link_issued',workspace,detail=row['data']['name'])
            service.notify_admins(db,workspace,version,'hall.link_issued',user['id'])
            # The only response that ever carries the raw token.
            return dict(screen=reload(db,workspace,id,snap,instant),path='/?hall#'+raw)

    @app.post('/api/hall/screens/{id}/revoke')
    def revoke_link(id:str,body:Versioned,request:Request,workspace:Workspace='school'):
        with service.context(request,workspace,True) as (db,user,snap):
            admin(user);row=editable(db,workspace,id,body.version);instant=service.instant()
            if not row['token']:raise HTTPException(409,'Ссылка не выдана.')
            db.execute('UPDATE hall_screens SET version=version+1,updated=?,token=NULL,token_issued=NULL,token_expires=NULL WHERE id=?',(iso(instant),id))
            service.history(db,workspace,user,'screen',id,'link_revoked',body.reason,{})
            access.record(db,'hall.link_revoked',workspace,detail=row['data']['name'])
            service.notify_admins(db,workspace,row['version']+1,'hall.link_revoked',user['id'])
            return reload(db,workspace,id,snap,instant)

    @app.post('/api/hall/screens/{id}/archive')
    def archive(id:str,body:Versioned,request:Request,workspace:Workspace='school'):
        with service.context(request,workspace,True) as (db,user,snap):
            admin(user);row=editable(db,workspace,id,body.version);instant=service.instant()
            if not body.reason:raise HTTPException(422,'Укажите причину.')
            db.execute("UPDATE hall_screens SET version=version+1,updated=?,status='archived',token=NULL,token_issued=NULL,token_expires=NULL WHERE id=?",(iso(instant),id))
            service.history(db,workspace,user,'screen',id,'archived',body.reason,{})
            access.record(db,'hall.archived',workspace,detail=row['data']['name'])
            if row['token']:service.notify_admins(db,workspace,row['version']+1,'hall.link_revoked',user['id'])
            return reload(db,workspace,id,snap,instant)

    @app.get('/api/hall/screens/{id}/preview')
    def preview(id:str,request:Request,workspace:Workspace='school'):
        with service.context(request,workspace) as (db,user,snap):
            admin(user);row=service.screen(db,workspace,id);instant=service.instant()
            return service.payload(snap,row,service.announcements(db,workspace,instant),instant)

    @app.post('/api/hall/announcements')
    def create_announcement(body:NewAnnouncement,request:Request,workspace:Workspace='school'):
        with service.context(request,workspace,True) as (db,user,snap):
            admin(user);key,previous=service.replay(db,user,body)
            if previous:return previous
            instant=service.instant()
            if body.endsAt<=instant:raise HTTPException(422,'Окончание показа уже прошло.')
            screens(db,workspace,body.screenIds)
            capacity(db,workspace,instant)
            id=uuid4().hex;data=body.model_dump(mode='json',exclude={'requestId','startsAt','endsAt'})
            db.execute("INSERT INTO hall_announcements(id,workspace,version,status,starts,ends,author_id,author_name,created,updated,document) VALUES (?,?,1,'active',?,?,?,?,?,?,?)",
                       (id,workspace,iso(body.startsAt),iso(body.endsAt),user['id'],user['name'],iso(instant),iso(instant),encoded(data)))
            service.history(db,workspace,user,'announcement',id,'created','',data|dict(startsAt=iso(body.startsAt),endsAt=iso(body.endsAt)))
            access.record(db,'hall.announcement_created',workspace,detail=body.title)
            return service.remember(db,user,body,key,service.announcement_summary(service.announcement(db,workspace,id),instant))

    @app.put('/api/hall/announcements/{id}')
    def edit_announcement(id:str,body:AnnouncementEdit,request:Request,workspace:Workspace='school'):
        with service.context(request,workspace,True) as (db,user,snap):
            admin(user);row=service.announcement(db,workspace,id);instant=service.instant()
            if row['version']!=body.version:raise HTTPException(409,'Объявление изменилось. Обновите список.')
            if row['status']!='active':raise HTTPException(409,'Объявление снято.')
            if iso(body.endsAt)!=row['ends'] and body.endsAt<=instant:raise HTTPException(422,'Окончание показа уже прошло.')
            # A finished announcement brought back by a new end takes a place like a new one.
            if row['ends']<=iso(instant)<iso(body.endsAt):capacity(db,workspace,instant)
            # Screens that were chosen earlier and have been removed since may stay in the list.
            screens(db,workspace,[s for s in body.screenIds if s not in row['data']['screenIds']])
            data=body.model_dump(mode='json',exclude={'version','startsAt','endsAt'})
            db.execute('UPDATE hall_announcements SET version=version+1,updated=?,starts=?,ends=?,document=? WHERE id=?',(iso(instant),iso(body.startsAt),iso(body.endsAt),encoded(data),id))
            service.history(db,workspace,user,'announcement',id,'updated','',dict(before=row['data']|dict(startsAt=row['starts'],endsAt=row['ends']),after=data|dict(startsAt=iso(body.startsAt),endsAt=iso(body.endsAt))))
            access.record(db,'hall.announcement_updated',workspace,detail=body.title)
            return service.announcement_summary(service.announcement(db,workspace,id),instant)

    @app.post('/api/hall/announcements/{id}/withdraw')
    def withdraw(id:str,body:Versioned,request:Request,workspace:Workspace='school'):
        with service.context(request,workspace,True) as (db,user,snap):
            admin(user);row=service.announcement(db,workspace,id);instant=service.instant()
            if row['version']!=body.version:raise HTTPException(409,'Объявление изменилось. Обновите список.')
            if row['status']!='active':raise HTTPException(409,'Объявление уже снято.')
            if not body.reason:raise HTTPException(422,'Укажите причину.')
            data=row['data']|dict(withdrawReason=body.reason)
            db.execute("UPDATE hall_announcements SET version=version+1,updated=?,status='withdrawn',document=? WHERE id=?",(iso(instant),encoded(data),id))
            service.history(db,workspace,user,'announcement',id,'withdrawn',body.reason,{})
            access.record(db,'hall.announcement_withdrawn',workspace,detail=row['data']['title'])
            return service.announcement_summary(service.announcement(db,workspace,id),instant)

    @app.get('/api/hall/history')
    def history(request:Request,entity:str=Query(pattern='^(screen|announcement)$'),id:str=Query(min_length=1,max_length=100),workspace:Workspace='school'):
        with service.context(request,workspace) as (db,user,snap):
            admin(user)
            if entity=='screen':service.screen(db,workspace,id)
            else:service.announcement(db,workspace,id)
            rows=db.execute('SELECT created,actor_name,action,reason FROM hall_history WHERE workspace=? AND entity=? AND entity_id=? ORDER BY seq DESC LIMIT 100',(workspace,entity,id)).fetchall()
            return dict(items=[dict(created=r['created'],actor=r['actor_name'],action=r['action'],reason=r['reason']) for r in rows])

    @app.get('/hall-api/display')
    def display(request:Request):
        """Public and tokenless for the session guard (it covers /api/ only); the secret arrives in a header, never in a path or query."""
        client=request.client.host if request.client else 'unknown';raw=request.headers.get('x-hall-token','');instant=service.instant();row=notes=None
        if TOKEN.fullmatch(raw):
            with closing(access.read_connect()) as db:
                db.row_factory=sqlite3.Row;db.execute('BEGIN')
                # Reading never creates the tables: before the first screen exists every token is simply unknown.
                found=db.execute("SELECT * FROM hall_screens WHERE token=? AND status='active'",(digest(raw),)).fetchone() if db.execute("SELECT 1 FROM sqlite_master WHERE name='hall_screens'").fetchone() else None
                if found and not (found['token_expires'] and found['token_expires']<=iso(instant)):row,notes=found,service.announcements(db,found['workspace'],instant)
        if not row:
            # Unknown tokens are counted per address; a valid link is never held back by them.
            service.limiter.failed(client);return not_found()
        service.limiter.hit(row['id'])
        payload=service.payload(service.snapshots.get(row['workspace']),row,notes,instant)
        etag='"'+hashlib.sha256(encoded({k:v for k,v in payload.items() if k!='serverTime'}).encode()).hexdigest()+'"'
        headers={'ETag':etag,'Cache-Control':'private, no-cache','Vary':'X-Hall-Token','X-Atlas-Server-Time':instant.astimezone(timezone.utc).isoformat(),
                 'Referrer-Policy':'no-referrer','X-Content-Type-Options':'nosniff','X-Robots-Tag':'noindex, nofollow'}
        # Never make a TV wait for a long save: the last-seen mark is best effort.
        if access.lock.acquire(blocking=False):
            try:
                with access.connect() as db:db.execute('UPDATE hall_screens SET last_seen=? WHERE id=? AND (last_seen IS NULL OR last_seen<?)',(iso(instant),row['id'],iso(instant-timedelta(minutes=5))))
            except sqlite3.Error:pass
            finally:access.lock.release()
        if request.headers.get('if-none-match')==etag:return Response(status_code=304,headers=headers)
        return Response(encoded(payload),media_type='application/json',headers=headers)
