import json
from fastapi import HTTPException,Query,Request
from .notifications import encoded,permission_key
from .registers import ELIGIBLE,iso,version
from .registers_models import ConfigWrite
from .school_forms import enabled,grant_ids

TABLES=dict(visitors='registers_visitors',incidents='registers_incidents',inventory='registers_inventory')

def register(s,app,Workspace):
    @app.get('/api/registers/options')
    def options(request:Request,workspace:Workspace='school'):
        with s.context(request,workspace) as (db,u,snap):
            cfg=s.config(db,workspace);caps=s.capabilities(db,u,snap,workspace,cfg);admin=u['role']=='admin'
            pupils=snap.entities['student']
            family=u['role']=='parent' and any(pupils[i].active for i in grant_ids(u,workspace,'student') if i in pupils)
            result=dict(admin=admin,capabilities=sorted(caps),today=s.today().isoformat(),now=iso(s.now()),familySummaries=cfg['familySummaries'],family=family)
            # Rooms, staff and pupils are listed only for the registers that need them.
            if caps:result|=dict(rooms=[dict(id=r.id,name=r.name) for r in snap.state.rooms],staff=s.staff_list(db,snap,workspace))
            if 'incidents' in caps:
                classes=snap.entities['class_']
                result['students']=sorted((dict(id=p.id,name=p.name,classId=p.classId,className=classes[p.classId].name if p.classId in classes else '') for p in snap.state.students if p.active),key=lambda p:(p['className'],p['name']))
            if 'inventory' in caps:result['categories']=[r[0] for r in db.execute("SELECT DISTINCT category FROM registers_inventory WHERE workspace=? ORDER BY category",(workspace,))]
            if admin:
                eligible=[];found={}
                for raw in db.execute("SELECT * FROM accounts WHERE active=1 AND role IN ('editor','teacher','staff') ORDER BY name,id"):
                    a=s.access.public(raw)
                    if enabled(a,snap,workspace):eligible.append(dict(id=a['id'],name=a['name'],role=a['role']));found[a['id']]=a
                result['config']=dict(version=cfg['version'],familySummaries=cfg['familySummaries'],assignments=[a|dict(current=a['accountId'] in found and a['permissions']==permission_key(found[a['accountId']])) for a in cfg['assignments']])
                result['eligible']=eligible
            return result

    @app.put('/api/registers/config')
    def config(body:ConfigWrite,request:Request,workspace:Workspace='school'):
        with s.context(request,workspace,True) as (db,u,snap):
            if u['role']!='admin':raise HTTPException(403,'Права журналов назначает администрация.')
            key,old=s.replay(db,u,body,'config:'+workspace)
            if old:return old
            current=s.config(db,workspace);version(current,body.version);assignments=[]
            for item in body.assignments:
                a=s.account(db,item.accountId)
                if not a or a['role'] not in ELIGIBLE or not enabled(a,snap,workspace):raise HTTPException(422,'Выберите действующих сотрудников этого пространства.')
                assignments.append(dict(accountId=a['id'],name=a['name'],permissions=permission_key(a),capabilities=sorted(item.capabilities)))
            after=dict(assignments=assignments,familySummaries=body.familySummaries)
            db.execute('INSERT INTO registers_config VALUES (?,?,?) ON CONFLICT(workspace) DO UPDATE SET version=excluded.version,document=excluded.document',(workspace,body.version+1,encoded(after)))
            s.history(db,workspace,'config','config',u,'configured',body.reason,dict(before=dict(assignments=current['assignments'],familySummaries=current['familySummaries']),after=after))
            s.access.record(db,'registers.configured',workspace,detail=f'Редакция {body.version+1} · {body.reason}',principal=u)
            return s.remember(db,u,body,key,dict(version=body.version+1))

    @app.get('/api/registers/history/{register}/{id}')
    def history(register:str,id:str,request:Request,workspace:Workspace='school',before:int=Query(default=9223372036854775807,ge=1),limit:int=Query(default=20,ge=1,le=50)):
        if register not in [*TABLES,'config']:raise HTTPException(404,'Журнал не найден.')
        with s.context(request,workspace) as (db,u,snap):
            if register=='config':
                if u['role']!='admin':raise HTTPException(403,'История назначений доступна администрации.')
                if id!='config':raise HTTPException(404,'Запись не найдена.')
            else:
                s.require(db,u,snap,workspace,register)
                if not db.execute(f'SELECT 1 FROM {TABLES[register]} WHERE workspace=? AND id=?',(workspace,id)).fetchone():raise HTTPException(404,'Запись не найдена.')
            rows=db.execute('SELECT * FROM registers_history WHERE workspace=? AND register=? AND entity_id=? AND seq<? ORDER BY seq DESC LIMIT ?',(workspace,register,id,before,limit+1)).fetchall()
            items,nxt=s.page(rows,limit)
            return dict(items=[dict(seq=r['seq'],actor=r['actor_name'],created=r['created'],action=r['action'],reason=r['reason'],data=json.loads(r['document'])) for r in items],nextBefore=nxt)
