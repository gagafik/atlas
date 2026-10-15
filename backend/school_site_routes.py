import json, sqlite3
from uuid import uuid4
from fastapi import HTTPException, Query, Request
from .notifications import encoded, permission_key
from .school_forms import enabled
from .school_site import STAFF, stamp, version, config_result, entry_result
from .school_site_models import SaveConfig, SaveEntry, AccessChange, Preview, Action, Content, SiteData
from .school_site_public import render, navigation


def register(s, app, Workspace):
    @app.get('/api/school-site/options')
    def options(request: Request, workspace: Workspace='school'):
        with s.context(request, workspace) as (db,u,snap):
            can = s.editor(db,u,workspace)
            result = dict(canEdit=can, canPublish=u['role']=='admin', url='/school/'+workspace, config=None, staff=[], assignments=[], menuItems=[])
            if not can: return result
            result['config'] = config_result(s.config(db,workspace))
            for id in result['config']['data']['menuIds']:
                r=s.entry(db,workspace,id);result['menuItems'].append(dict(id=id,title=json.loads(r['document'])['title']))
            if u['role']=='admin':
                for row in db.execute('SELECT * FROM accounts WHERE active=1 ORDER BY name LIMIT 1000'):
                    a = s.access.public(row)
                    if a['role'] in STAFF and enabled(a,snap,workspace):
                        result['staff'].append(dict(id=a['id'],name=a['name'],role=a['role']))
                for row in db.execute('SELECT * FROM school_site_access WHERE workspace=?',(workspace,)):
                    a = db.execute('SELECT * FROM accounts WHERE id=? AND active=1',(row['user_id'],)).fetchone()
                    user = s.access.public(a) if a else None
                    result['assignments'].append(dict(id=row['user_id'],current=bool(user and user['role'] in STAFF and enabled(user,snap,workspace) and permission_key(user)==row['permissions'])))
            return result

    @app.put('/api/school-site/config')
    def save_config(body: SaveConfig, request: Request, workspace: Workspace='school'):
        with s.context(request,workspace,True) as (db,u,snap):
            s.admin(u); key,old=s.replay(db,u,body,'config:'+workspace)
            if old:return old
            cfg=s.config(db,workspace); version(cfg,body.version)
            for id in body.data.menuIds:s.entry(db,workspace,id)
            data=body.data.model_dump(mode='json')
            db.execute('INSERT INTO school_site_config VALUES (?,?,?,NULL,0) ON CONFLICT(workspace) DO UPDATE SET version=excluded.version,document=excluded.document',(workspace,cfg['version']+1,encoded(data)))
            result=config_result(s.config(db,workspace));s.history(db,u,workspace,'','save_config',body.reason,data)
            return s.remember(db,u,body,key,result)

    @app.put('/api/school-site/access')
    def access(body: AccessChange, request: Request, workspace: Workspace='school'):
        with s.context(request,workspace,True) as (db,u,snap):
            s.admin(u);key,old=s.replay(db,u,body,'access:'+workspace)
            if old:return old
            cfg=s.config(db,workspace);version(cfg,body.version)
            if not cfg['version']:raise HTTPException(409,'Сначала сохраните настройки сайта.')
            if len(set(body.accountIds))!=len(body.accountIds):raise HTTPException(422,'Сотрудник повторяется.')
            assignments=[]
            for id in body.accountIds:
                row=db.execute('SELECT * FROM accounts WHERE id=? AND active=1',(id,)).fetchone()
                a=s.access.public(row) if row else None
                if not a or a['role'] not in STAFF or not enabled(a,snap,workspace):raise HTTPException(422,'Выберите действующего сотрудника с доступом к пространству.')
                assignments.append((workspace,id,permission_key(a)))
            db.execute('DELETE FROM school_site_access WHERE workspace=?',(workspace,));db.executemany('INSERT INTO school_site_access VALUES (?,?,?)',assignments)
            db.execute('UPDATE school_site_config SET version=version+1 WHERE workspace=?',(workspace,))
            s.history(db,u,workspace,'','access',body.reason,dict(accountIds=body.accountIds))
            return s.remember(db,u,body,key,config_result(s.config(db,workspace)))

    @app.get('/api/school-site/items')
    def listing(request:Request,workspace:Workspace='school',q:str=Query(default='',max_length=100),status:str=Query(default='active',pattern='^(active|draft|published|archived)$'),offset:int=Query(default=0,ge=0,le=10000)):
        with s.context(request,workspace) as (db,u,snap):
            s.require_editor(db,u,workspace)
            where='workspace=? AND archived=?';args=[workspace,int(status=='archived')]
            if status=='draft':where+=' AND published IS NULL'
            if status=='published':where+=' AND published IS NOT NULL'
            # Search private drafts only within the already authorized site.
            if q:where+=' AND instr(draft_search,?)>0';args.append(q.casefold())
            rows=db.execute('SELECT * FROM school_site_entries WHERE '+where+' ORDER BY seq DESC LIMIT 31 OFFSET ?',[*args,offset]).fetchall()
            return dict(items=[dict(id=r['id'],version=r['version'],title=json.loads(r['document'])['title'],kind=r['kind'],slug=r['slug'],archived=bool(r['archived']),hasPublished=bool(r['published']),updated=r['updated']) for r in rows[:30]],hasMore=len(rows)>30)

    @app.get('/api/school-site/items/{id}')
    def detail(id:str,request:Request,workspace:Workspace='school'):
        with s.context(request,workspace) as (db,u,snap):
            s.require_editor(db,u,workspace);return entry_result(s.entry(db,workspace,id))

    def save(body,request,workspace,id=None):
        with s.context(request,workspace,True) as (db,u,snap):
            s.require_editor(db,u,workspace);key,old=s.replay(db,u,body,'save:'+workspace+':'+(id or 'new'))
            if old:return old
            if not s.config(db,workspace)['version']:raise HTTPException(409,'Администратор должен сохранить настройки сайта.')
            previous=s.entry(db,workspace,id) if id else None
            if previous:
                version(previous,body.version)
                if previous['archived']:raise HTTPException(409,'Сначала верните материал из архива.')
                if body.data.slug!=previous['slug']:raise HTTPException(422,'Адрес созданного материала не меняется.')
            elif body.version!=0:raise HTTPException(409,'Новый материал должен иметь версию 0.')
            elif db.execute('SELECT count(*) FROM school_site_entries WHERE workspace=?',(workspace,)).fetchone()[0]>=3000:raise HTTPException(413,'Достигнут предел 3000 материалов на сайт.')
            id=id or uuid4().hex;data=body.data.model_dump(mode='json');s.photos(db,workspace,id,data,True)
            try:
                if previous:db.execute('UPDATE school_site_entries SET version=version+1,kind=?,updated=?,document=? WHERE id=?',(data['kind'],stamp(),encoded(data),id))
                else:db.execute('INSERT INTO school_site_entries(id,workspace,version,slug,kind,archived,created,updated,document) VALUES (?,?,1,?,?,0,?,?,?)',(id,workspace,data['slug'],data['kind'],stamp(),stamp(),encoded(data)))
            except sqlite3.IntegrityError:raise HTTPException(409,'Этот адрес уже занят, в том числе материалом в архиве.') from None
            db.execute('UPDATE school_site_entries SET draft_search=? WHERE id=?',(encoded(data).casefold(),id))
            s.history(db,u,workspace,id,'save',body.reason,data)
            return s.remember(db,u,body,key,entry_result(s.entry(db,workspace,id)))

    @app.post('/api/school-site/items')
    def create(body:SaveEntry,request:Request,workspace:Workspace='school'):return save(body,request,workspace)

    @app.put('/api/school-site/items/{id}')
    def edit(id:str,body:SaveEntry,request:Request,workspace:Workspace='school'):return save(body,request,workspace,id)

    @app.post('/api/school-site/preview/{id}')
    def preview(id:str,body:Preview,request:Request,workspace:Workspace='school'):
        with s.context(request,workspace,True) as (db,u,snap):
            s.require_editor(db,u,workspace);cfg=s.config(db,workspace)
            if not cfg['version']:raise HTTPException(409,'Сначала сохраните настройки сайта.')
            row=cfg if id=='site' else s.entry(db,workspace,id);version(row,body.version)
            if id!='site' and row['archived']:raise HTTPException(409,'Материал находится в архиве.')
            data=json.loads(row['document']);site=json.loads(cfg['document'])
            cards=[json.loads(r['published']) for r in db.execute('SELECT published FROM school_site_entries WHERE workspace=? AND published IS NOT NULL ORDER BY published_at DESC,seq DESC LIMIT 24',(workspace,))] if id=='site' else []
            html=render(site,workspace,navigation(db,workspace,site),entry=data if id!='site' else None,items=cards,preview=True)
            return dict(html=html,token=s.preview(db,u,workspace,'' if id=='site' else id,body.version) if u['role']=='admin' else '',version=body.version)

    @app.post('/api/school-site/action/{id}')
    def action(id:str,body:Action,request:Request,workspace:Workspace='school'):
        with s.context(request,workspace,True) as (db,u,snap):
            s.admin(u);key,old=s.replay(db,u,body,'action:'+workspace+':'+id)
            if old:return old
            cfg=s.config(db,workspace);row=cfg if id=='site' else s.entry(db,workspace,id);version(row,body.version)
            if not row['version']:raise HTTPException(409,'Сначала сохраните настройки сайта.')
            data=json.loads(row['document'])
            if id=='site':
                if body.action=='publish':
                    for item_id in data['menuIds']:
                        if not s.entry(db,workspace,item_id)['published']:raise HTTPException(409,'Сначала опубликуйте все материалы, выбранные для меню.')
                    s.consume(db,u,workspace,'',body)
                    db.execute('UPDATE school_site_config SET version=version+1,published=document,enabled=1 WHERE workspace=?',(workspace,))
                elif body.action=='withdraw':
                    db.execute('UPDATE school_site_config SET version=version+1,enabled=0,published=NULL WHERE workspace=?',(workspace,))
                else:raise HTTPException(422,'Для сайта доступны публикация и отключение.')
                result=config_result(s.config(db,workspace))
            else:
                if body.action=='publish':
                    if row['archived']:raise HTTPException(409,'Верните материал из архива.')
                    s.photos(db,workspace,id,data);s.consume(db,u,workspace,id,body)
                    search=' '.join([data['title'],data['summary'],*[b['text'] for b in data['blocks']]]).casefold()
                    db.execute('UPDATE school_site_entries SET published=document,published_at=?,published_search=? WHERE id=?',(stamp(),search,id))
                elif body.action in ['withdraw','archive']:
                    db.execute('UPDATE school_site_entries SET published=NULL,published_at=NULL,published_search=NULL,archived=? WHERE id=?',(int(body.action=='archive'),id))
                elif body.action=='restore':
                    if not row['archived']:raise HTTPException(409,'Материал не находится в архиве.')
                    db.execute('UPDATE school_site_entries SET archived=0 WHERE id=?',(id,))
                elif body.action=='restore_revision':
                    h=db.execute("SELECT document FROM school_site_history WHERE seq=? AND workspace=? AND entry_id=? AND action IN ('save','publish','restore_revision')",(body.historyId,workspace,id)).fetchone()
                    if not h or row['archived']:raise HTTPException(409,'Редакция недоступна для восстановления.')
                    data=Content.model_validate_json(h['document']).model_dump(mode='json');s.photos(db,workspace,id,data)
                    db.execute('UPDATE school_site_entries SET document=?,kind=?,draft_search=? WHERE id=?',(encoded(data),data['kind'],encoded(data).casefold(),id))
                db.execute('UPDATE school_site_entries SET version=version+1,updated=? WHERE id=?',(stamp(),id))
                result=entry_result(s.entry(db,workspace,id))
            s.history(db,u,workspace,'' if id=='site' else id,body.action,body.reason,data)
            return s.remember(db,u,body,key,result)

    @app.get('/api/school-site/history')
    def history(request:Request,workspace:Workspace='school',id:str=Query(default='',max_length=32),offset:int=Query(default=0,ge=0,le=100000)):
        with s.context(request,workspace) as (db,u,snap):
            s.require_editor(db,u,workspace)
            if id:s.entry(db,workspace,id)
            rows=db.execute('SELECT * FROM school_site_history WHERE workspace=? AND entry_id=? ORDER BY seq DESC LIMIT 31 OFFSET ?',(workspace,id,offset)).fetchall()
            return dict(items=[dict(id=r['seq'],created=r['created'],actor=r['actor_name'],action=r['action'],reason=r['reason'],data=json.loads(r['document'])) for r in rows[:30]],hasMore=len(rows)>30)
