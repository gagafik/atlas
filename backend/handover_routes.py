from datetime import date, timedelta
import json
from fastapi import HTTPException, Query, Request
from .handover import today, version, require_reason
from .handover_models import STAFF, Settings, AdultProposal, AdultDecision, PickupCreate, PickupAction, TransferReview, TransferConfirm
from .notifications import encoded, permission_key
from .payment_routes import csv_response
from .school_forms import enabled


def dates(start, end):
    start=start or today();end=end or start
    if end<start or (end-start).days>30:raise HTTPException(422,'Выберите период от 1 до 31 дня.')
    return start.isoformat(),end.isoformat()


def register(s, app, Workspace):
    @app.get('/api/handover/options')
    def options(request:Request,workspace:Workspace='school'):
        with s.context(request,workspace) as (db,u,snap):
            cfg=s.config(db,workspace);permission=s.permission(db,u,workspace);ids=s.students(db,u,snap,workspace)
            students=[dict(id=p.id,name=p.name,classId=p.classId,className=snap.entities['class_'][p.classId].name) for p in snap.state.students if p.id in ids]
            parents=[]
            if permission and permission['role']=='manager':
                for row in db.execute("SELECT * FROM accounts WHERE active=1 AND role='parent' ORDER BY name,id"):
                    p=s.access.public(row);children=ids&s.students(db,p,snap,workspace)
                    if children:parents.append(dict(id=p['id'],name=p['name'],studentIds=sorted(children)))
            elif u['role']=='parent':parents=[dict(id=u['id'],name=u['name'],studentIds=sorted(ids))]
            staff=[]
            if u['role']=='admin':
                for row in db.execute("SELECT * FROM accounts WHERE active=1 AND role IN ('admin','editor','teacher','staff') ORDER BY name,id"):
                    p=s.access.public(row)
                    if enabled(p,snap,workspace):staff.append(dict(id=p['id'],name=p['name']))
            public={k:cfg[k] for k in ['version','enabled','parentRequests','advanceDays','policy','locations']}
            if u['role']=='admin':public['delegates']=[{k:x[k] for k in ['userId','role','classIds']} for x in cfg['delegates']]
            return dict(admin=u['role']=='admin',parent=u['role']=='parent',permission=(permission or {}).get('role'),students=students,parents=parents,
                        classes=[dict(id=c.id,name=c.name) for c in snap.state.classes] if u['role']=='admin' else [],staff=staff,config=public,today=today().isoformat(),timeZone='Asia/Qyzylorda')

    @app.put('/api/handover/config')
    def write_config(body:Settings,request:Request,workspace:Workspace='school'):
        with s.context(request,workspace,True) as (db,u,snap):
            if u['role']!='admin':raise HTTPException(403,'Настройки выдачи задаёт администрация.')
            key,old=s.replay(db,u,body,'config:'+workspace)
            if old:return old
            current=s.config(db,workspace);version(current,body.version);require_reason(body.reason);delegates=[]
            for d in body.delegates:
                row=db.execute('SELECT * FROM accounts WHERE id=? AND active=1',(d.userId,)).fetchone();p=s.access.public(row) if row else None
                if not p or p['role'] not in STAFF or not enabled(p,snap,workspace) or set(d.classIds)-snap.entities['class_'].keys():raise HTTPException(422,'Проверьте сотрудников и классы назначения.')
                delegates.append(d.model_dump()|dict(permissions=permission_key(p)))
            doc=body.model_dump(exclude={'requestId','version','reason','delegates'},mode='json')|dict(delegates=delegates)
            db.execute('INSERT INTO handover_config VALUES (?,?,?) ON CONFLICT(workspace) DO UPDATE SET version=excluded.version,document=excluded.document',(workspace,body.version+1,encoded(doc)))
            s.history(db,u,workspace,'configured','',body.reason,dict(before=current,after=doc))
            return s.remember(db,u,body,key,dict(version=body.version+1))

    def adult_scope(db,u,snap,w,student=''):
        ids=s.students(db,u,snap,w,False);where=['workspace=?','student_id IN (SELECT value FROM json_each(?))'];values=[w,encoded(sorted(ids))]
        if student:where.append('student_id=?');values.append(student)
        if not s.permission(db,u,w):where.append("(sponsor_id=? OR status='approved')");values.append(u['id'])
        return ' AND '.join(where),values

    @app.get('/api/handover/adults')
    def adults(request:Request,workspace:Workspace='school',studentId:str='',status:str='all',q:str=Query(default='',max_length=120),offset:int=Query(default=0,ge=0,le=100000)):
        if status not in {'all','proposed','approved','rejected','revoked'}:raise HTTPException(422,'Неизвестное состояние разрешения.')
        with s.context(request,workspace) as (db,u,snap):
            where,values=adult_scope(db,u,snap,workspace,studentId)
            if status!='all':where+=' AND status=?';values.append(status)
            if q:
                db.create_function('handover_casefold',1,lambda v:str(v or '').casefold(),deterministic=True)
                where+=" AND instr(handover_casefold(json_extract(document,'$.name')),?)>0";values.append(q.casefold())
            count=db.execute('SELECT count(*) FROM handover_adults WHERE '+where,values).fetchone()[0]
            rows=db.execute('SELECT id FROM handover_adults WHERE '+where+' ORDER BY seq DESC LIMIT 50 OFFSET ?',(*values,offset)).fetchall()
            return dict(items=[s.public_adult(db,u,snap,workspace,s.row(db,workspace,r['id'],'adult')) for r in rows],totalCount=count,nextOffset=offset+50 if offset+50<count else None)

    def read_adult(db,u,snap,w,id):
        row=s.row(db,w,id,'adult');s.child(db,u,snap,w,row['student_id'],active=False)
        if not s.permission(db,u,w) and row['sponsor_id']!=u['id'] and row['status']!='approved':raise HTTPException(404,'Разрешение недоступно.')
        return row

    @app.get('/api/handover/adults/{id}')
    def adult_read(id:str,request:Request,workspace:Workspace='school'):
        with s.context(request,workspace) as (db,u,snap):return s.public_adult(db,u,snap,workspace,read_adult(db,u,snap,workspace,id))

    @app.post('/api/handover/adults')
    def propose(body:AdultProposal,request:Request,workspace:Workspace='school'):
        with s.context(request,workspace,True) as (db,u,snap):
            s.child(db,u,snap,workspace,body.studentId,manager=u['role']!='parent');key,old=s.replay(db,u,body,'adult:'+workspace)
            if old:return old
            return s.remember(db,u,body,key,s.proposal(db,u,snap,workspace,body))

    @app.post('/api/handover/adults/{id}/decision')
    def decision(id:str,body:AdultDecision,request:Request,workspace:Workspace='school'):
        with s.context(request,workspace,True) as (db,u,snap):
            adult=read_adult(db,u,snap,workspace,id)
            if u['role']=='parent':
                if body.action!='revoke' or adult['sponsor_id']!=u['id']:raise HTTPException(403,'Решение о разрешении принимает школа.')
            else:s.child(db,u,snap,workspace,adult['student_id'],manager=True,active=False)
            key,old=s.replay(db,u,body,'adult-decision:'+workspace+':'+id)
            if old:return old
            return s.remember(db,u,body,key,s.decide_adult(db,u,snap,workspace,adult,body))

    def pickup_filter(db,u,snap,w,a,b,student='',status='all',q=''):
        ids=s.students(db,u,snap,w,False);where=['workspace=?','day BETWEEN ? AND ?','student_id IN (SELECT value FROM json_each(?))'];values=[w,a,b,encoded(sorted(ids))]
        if student:where.append('student_id=?');values.append(student)
        if status=='pending':where.append("status IN ('requested','arrived','ready')")
        elif status=='expired':where.append("status IN ('requested','arrived','ready') AND day<?");values.append(today().isoformat())
        elif status!='all':where.append('status=?');values.append(status)
        if q:
            db.create_function('handover_casefold',1,lambda v:str(v or '').casefold(),deterministic=True)
            where.append("instr(handover_casefold(json_extract(document,'$.studentName')||' '||json_extract(document,'$.adultName')),?)>0");values.append(q.casefold())
        return ' AND '.join(where),values

    @app.get('/api/handover/pickups')
    def pickups(request:Request,workspace:Workspace='school',start:date|None=None,end:date|None=None,studentId:str='',status:str='all',q:str=Query(default='',max_length=120),offset:int=Query(default=0,ge=0,le=100000)):
        if status not in {'all','pending','expired','requested','arrived','ready','completed','cancelled','returned'}:raise HTTPException(422,'Неизвестное состояние заявки.')
        a,b=dates(start,end)
        with s.context(request,workspace) as (db,u,snap):
            where,values=pickup_filter(db,u,snap,workspace,a,b,studentId,status,q);count=db.execute('SELECT count(*) FROM handover_pickups WHERE '+where,values).fetchone()[0]
            rows=db.execute('SELECT id FROM handover_pickups WHERE '+where+' ORDER BY day,expected_time,seq LIMIT 50 OFFSET ?',(*values,offset)).fetchall()
            return dict(items=[s.public_pickup(db,u,snap,workspace,s.row(db,workspace,r['id'],'pickup')) for r in rows],totalCount=count,nextOffset=offset+50 if offset+50<count else None)

    @app.get('/api/handover/pickups/export')
    def export(request:Request,workspace:Workspace='school',start:date|None=None,end:date|None=None):
        a,b=dates(start,end)
        with s.context(request,workspace) as (db,u,snap):
            if not s.permission(db,u,workspace):raise HTTPException(403,'Выгрузка доступна назначенным сотрудникам.')
            where,values=pickup_filter(db,u,snap,workspace,a,b)
            def rows():
                yield ['Дата','Ожидаемое время','Ученик','Класс','Взрослый','Место','Состояние','Передан','Вернулся']
                for r in db.execute('SELECT * FROM handover_pickups WHERE '+where+' ORDER BY day,expected_time,seq',values):
                    d=json.loads(r['document']);yield [r['day'],r['expected_time'],d['studentName'],d['className'],d['adultName'],d['locationName'],r['status'],r['completed_at'],r['returned_at']]
            return csv_response(rows(),'child-handovers.csv')

    @app.get('/api/handover/pickups/{id}')
    def pickup_read(id:str,request:Request,workspace:Workspace='school'):
        with s.context(request,workspace) as (db,u,snap):return s.public_pickup(db,u,snap,workspace,s.row(db,workspace,id,'pickup'))

    @app.post('/api/handover/pickups')
    def create_pickup(body:PickupCreate,request:Request,workspace:Workspace='school'):
        with s.context(request,workspace,True) as (db,u,snap):
            adult=read_adult(db,u,snap,workspace,body.adultId);s.child(db,u,snap,workspace,adult['student_id'],manager=u['role']!='parent')
            key,old=s.replay(db,u,body,'pickup:'+workspace)
            if old:return old
            return s.remember(db,u,body,key,s.create_pickup(db,u,snap,workspace,body))

    @app.post('/api/handover/pickups/{id}/action')
    def action(id:str,body:PickupAction,request:Request,workspace:Workspace='school'):
        with s.context(request,workspace,True) as (db,u,snap):
            row=s.row(db,workspace,id,'pickup');s.child(db,u,snap,workspace,row['student_id'],staff=body.action in {'ready','returned','note'},manager=body.action in {'returned','note'},active=False)
            if u['role']=='parent' and row['creator_id']!=u['id'] and (body.action!='cancel' or s.row(db,workspace,row['adult_id'],'adult')['sponsor_id']!=u['id']):raise HTTPException(403,'Действие с этой заявкой недоступно.')
            key,old=s.replay(db,u,body,'pickup-action:'+workspace+':'+id)
            if old:return old
            return s.remember(db,u,body,key,s.change_pickup(db,u,snap,workspace,row,body))

    @app.post('/api/handover/pickups/{id}/review')
    def review(id:str,body:TransferReview,request:Request,workspace:Workspace='school'):
        with s.context(request,workspace) as (db,u,snap):return s.review_transfer(db,u,snap,workspace,s.row(db,workspace,id,'pickup'),body)

    @app.post('/api/handover/pickups/{id}/complete')
    def complete(id:str,body:TransferConfirm,request:Request,workspace:Workspace='school'):
        with s.context(request,workspace,True) as (db,u,snap):
            row=s.row(db,workspace,id,'pickup');s.child(db,u,snap,workspace,row['student_id'],staff=True)
            key,old=s.replay(db,u,body,'complete:'+workspace+':'+id)
            if old:return old
            return s.remember(db,u,body,key,s.complete(db,u,snap,workspace,row,body))

    @app.get('/api/handover/history')
    def history(request:Request,workspace:Workspace='school',studentId:str='',pickupId:str='',adultId:str='',before:int=Query(default=9223372036854775807,ge=1)):
        with s.context(request,workspace) as (db,u,snap):
            ids=s.students(db,u,snap,workspace,False);staff=bool(s.permission(db,u,workspace));where=['workspace=?','seq<?'];values=[workspace,before]
            if u['role']!='admin' or studentId or pickupId or adultId:where.append('student_id IN (SELECT value FROM json_each(?))');values.append(encoded(sorted(ids)))
            for col,v in [('student_id',studentId),('pickup_id',pickupId),('adult_id',adultId)]:
                if v:where.append(col+'=?');values.append(v)
            if not staff:where.append("action IN ('requested','arrive','ready','completed','cancel','returned')")
            rows=db.execute('SELECT * FROM handover_history WHERE '+' AND '.join(where)+' ORDER BY seq DESC LIMIT 51',values).fetchall()
            keys=['seq','created','action','student_id','pickup_id','adult_id']+(['reason','actor_name'] if staff else [])
            return dict(items=[{k:r[k] for k in keys} for r in rows[:50]],nextBefore=rows[49]['seq'] if len(rows)>50 else None)

    @app.get('/api/handover/summary')
    def summary(request:Request,workspace:Workspace='school',start:date|None=None,end:date|None=None):
        a,b=dates(start,end)
        with s.context(request,workspace) as (db,u,snap):
            where,values=pickup_filter(db,u,snap,workspace,a,b)
            rows=db.execute('SELECT day,status,count(*) AS quantity FROM handover_pickups WHERE '+where+' GROUP BY day,status ORDER BY day,status',values).fetchall()
            return dict(items=[dict(r) for r in rows])
