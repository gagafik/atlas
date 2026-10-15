import csv,json,sqlite3,tempfile
from uuid import uuid4
from fastapi import HTTPException,Query,Request
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask
from .lost_found import STAFF,stamp,today,version
from .lost_found_models import ItemWrite,ItemAction,ClaimCreate,ClaimAction,ConfigWrite
from .notifications import encoded,permission_key
from .school_forms import enabled,grant_ids

def register(s,app,Workspace):
    @app.get('/api/lost-found/options')
    def options(request:Request,workspace:Workspace='school'):
        with s.context(request,workspace) as (db,u,snap):
            admin=u['role']=='admin';config=s.config(db,workspace);staff=[]
            if admin:
                for row in db.execute("SELECT * FROM accounts WHERE active=1 AND role IN ('admin','editor','teacher','staff') ORDER BY name,id"):
                    a=s.access.public(row)
                    if enabled(a,snap,workspace):staff.append(dict(id=a['id'],name=a['name'],role=a['role']))
            students=[dict(id=p.id,name=p.name) for p in snap.state.students if p.active and p.id in grant_ids(u,workspace,'student')] if u['role'] in ['parent','student'] else []
            return dict(admin=admin,canManage=s.manager(db,u,workspace),canClaim=u['role'] in STAFF or bool(students),selfClaim=u['role'] in STAFF,students=students,staff=staff,today=today().isoformat(),config=dict(version=config['version'],accountIds=[a['accountId'] for a in config['assignments']]) if admin else None)

    @app.put('/api/lost-found/config')
    def config(body:ConfigWrite,request:Request,workspace:Workspace='school'):
        with s.context(request,workspace,True) as (db,u,snap):
            if u['role']!='admin':raise HTTPException(403,'Ответственных назначает администрация.')
            key,old=s.replay(db,u,body,'config:'+workspace)
            if old:return old
            current=s.config(db,workspace);version(current,body.version);assignments=[]
            for id in body.accountIds:
                a=s.account(db,id)
                if not a or a['role'] not in STAFF or not enabled(a,snap,workspace):raise HTTPException(422,'Выберите действующих сотрудников этого пространства.')
                assignments.append(dict(accountId=id,name=a['name'],permissions=permission_key(a)))
            db.execute('INSERT INTO lost_found_config VALUES (?,?,?) ON CONFLICT(workspace) DO UPDATE SET version=excluded.version,document=excluded.document',(workspace,body.version+1,encoded(assignments)))
            s.history(db,workspace,u,'config','','configured',body.reason,dict(before=current,after=assignments))
            return s.remember(db,u,body,key,dict(version=body.version+1))

    @app.get('/api/lost-found/items')
    def items(request:Request,workspace:Workspace='school',q:str=Query(default='',max_length=200),category:str=Query(default='',pattern='^(clothing|electronics|books|keys|other)?$'),status:str=Query(default='',pattern='^(draft|open|reserved|returned|archived)?$'),before:int=Query(default=9223372036854775807,ge=1),limit:int=Query(default=20,ge=1,le=100)):
        with s.context(request,workspace) as (db,u,snap):
            manager=s.manager(db,u,workspace)
            if status and status not in ['open','reserved'] and not manager:raise HTTPException(403,'Этот список доступен ответственному.')
            states=[status] if status else ['open','reserved']
            rows=db.execute("SELECT * FROM lost_found_items WHERE workspace=? AND status IN (SELECT value FROM json_each(?)) AND seq<? AND instr(search,?)>0 AND (?='' OR json_extract(document,'$.category')=?) ORDER BY seq DESC LIMIT ?",(workspace,encoded(states),before,q.strip().casefold(),category,category,limit+1)).fetchall()
            return dict(items=[s.public_item(dict(r)|dict(data=json.loads(r['document'])),manager) for r in rows[:limit]],nextBefore=rows[limit-1]['seq'] if len(rows)>limit else None)

    @app.get('/api/lost-found/items/{id}')
    def item(id:str,request:Request,workspace:Workspace='school'):
        with s.context(request,workspace) as (db,u,snap):
            row=s.item(db,workspace,id);s.visible_item(db,row,u,snap,workspace)
            return s.public_item(row,s.manager(db,u,workspace))

    def save(body,request,workspace,id):
        with s.context(request,workspace,True) as (db,u,snap):
            s.require_manager(db,u,workspace);key,old=s.replay(db,u,body,'item:'+workspace+':'+(id or 'new'))
            if old:return old
            previous=s.item(db,workspace,id) if id else None
            if previous:
                version(previous,body.version)
                if previous['status'] not in ['draft','open']:raise HTTPException(409,'Сначала завершите проверку владельца; закрытые находки не редактируются.')
                if len(body.reason)<3:raise HTTPException(422,'Укажите причину изменения.')
            elif body.version!=0 or body.data.photoIds:raise HTTPException(422,'Сначала сохраните новую находку без фото.')
            if body.data.foundOn>today():raise HTTPException(422,'Дата находки не может быть в будущем.')
            data=body.data.model_dump(mode='json');id=id or uuid4().hex
            for file in data['photoIds']:
                r=db.execute("SELECT * FROM lost_found_files WHERE id=? AND workspace=? AND item_id=? AND status='ready'",(file,workspace,id)).fetchone()
                if not r or (not r['linked'] and r['owner_id']!=u['id']):raise HTTPException(422,'Фото недоступно или относится к другой находке.')
                db.execute('UPDATE lost_found_files SET linked=1 WHERE id=?',(file,))
            # Search only public fields, never private ownership clues or pickup instructions.
            search=' '.join(str(data[k]) for k in ['title','description','foundPlace']).casefold()
            if previous:db.execute('UPDATE lost_found_items SET version=version+1,updated=?,search=?,document=? WHERE id=?',(stamp(),search,encoded(data),id))
            else:db.execute('INSERT INTO lost_found_items(id,workspace,version,status,created,updated,search,document) VALUES (?,?,1,?,?,?,?,?)',(id,workspace,'draft',stamp(),stamp(),search,encoded(data)))
            s.history(db,workspace,u,id,'','saved',body.reason,dict(before=previous['data'] if previous else None,after=data))
            return s.remember(db,u,body,key,s.public_item(s.item(db,workspace,id),True))

    @app.post('/api/lost-found/items')
    def create(body:ItemWrite,request:Request,workspace:Workspace='school'):return save(body,request,workspace,None)
    @app.put('/api/lost-found/items/{id}')
    def edit(id:str,body:ItemWrite,request:Request,workspace:Workspace='school'):return save(body,request,workspace,id)

    @app.post('/api/lost-found/items/{id}/action')
    def item_action(id:str,body:ItemAction,request:Request,workspace:Workspace='school'):
        with s.context(request,workspace,True) as (db,u,snap):
            s.require_manager(db,u,workspace);row=s.item(db,workspace,id);key,old=s.replay(db,u,body,'item-action:'+workspace+':'+id)
            if old:return old
            version(row,body.version)
            allowed={'publish':(['draft'],'open'),'withdraw':(['open'],'draft'),'archive':(['draft','open','returned'],'archived')}
            valid,target=allowed[body.action]
            if row['status'] not in valid:raise HTTPException(409,'Действие недоступно в текущем состоянии находки.')
            if body.action=='archive':
                for raw in db.execute("SELECT id FROM lost_found_claims WHERE item_id=? AND status='pending'",(id,)).fetchall():
                    claim=s.claim(db,workspace,raw['id']);s.change_claim(db,claim,'rejected',u,'Объявление закрыто: '+body.reason);s.notify(db,claim,u,snap,workspace)
            s.change_item(db,row,target);s.history(db,workspace,u,id,'',body.action,body.reason,dict(before=row['status'],after=target))
            return s.remember(db,u,body,key,s.public_item(s.item(db,workspace,id),True))

    @app.post('/api/lost-found/items/{id}/claims')
    def claim_create(id:str,body:ClaimCreate,request:Request,workspace:Workspace='school'):
        with s.context(request,workspace,True) as (db,u,snap):
            name=u['name']
            if u['role'] in ['parent','student']:
                p=snap.entities['student'].get(body.studentId)
                if not p or not p.active or p.id not in grant_ids(u,workspace,'student'):raise HTTPException(403,'Выберите своего действующего ребёнка.')
                name=p.name
            elif u['role'] not in STAFF or body.studentId:raise HTTPException(403,'Нет права подать такую заявку.')
            row=s.item(db,workspace,id);key,old=s.replay(db,u,body,'claim:'+workspace+':'+id)
            if old:
                previous=s.claim(db,workspace,old['id'])
                if not s.owns(previous,u,snap,workspace):raise HTTPException(404,'Прежняя заявка больше недоступна.')
                return old
            version(row,body.itemVersion)
            if row['status']!='open':raise HTTPException(409,'Новые заявки сейчас не принимаются.')
            if db.execute("SELECT count(*) FROM lost_found_claims WHERE item_id=? AND status IN ('pending','approved')",(id,)).fetchone()[0]>=100:raise HTTPException(409,'По этой находке уже много заявок. Обратитесь к ответственному.')
            if db.execute("SELECT count(*) FROM lost_found_claims WHERE workspace=? AND user_id=? AND status IN ('pending','approved')",(workspace,u['id'])).fetchone()[0]>=5:raise HTTPException(409,'Сначала завершите или отзовите прежние заявки: одновременно допускается пять.')
            claim_id=uuid4().hex;data=dict(proof=body.proof,claimantName=u['name'],ownerName=name,decision='',receivedBy='',reviewerName='')
            try:db.execute('INSERT INTO lost_found_claims(id,workspace,item_id,user_id,permissions,student_id,version,status,created,updated,document) VALUES (?,?,?,?,?,?,1,?,?,?,?)',(claim_id,workspace,id,u['id'],permission_key(u),body.studentId,'pending',stamp(),stamp(),encoded(data)))
            except sqlite3.IntegrityError:raise HTTPException(409,'У вас уже есть незакрытая заявка на эту вещь.') from None
            s.history(db,workspace,u,id,claim_id,'claimed','',data);s.notify_managers(db,u,snap,workspace,1)
            return s.remember(db,u,body,key,s.public_claim(db,s.claim(db,workspace,claim_id),u,snap,workspace))

    def claim_rows(db,u,snap,workspace,all,item_id,status,before,limit):
        clauses=['workspace=?','seq<?'];args=[workspace,before]
        if all:s.require_manager(db,u,workspace)
        else:
            ids=[p.id for p in snap.state.students if p.active and p.id in grant_ids(u,workspace,'student')] if u['role'] in ['parent','student'] else []
            clauses+=['user_id=?','permissions=?',"(student_id IN (SELECT value FROM json_each(?)) OR (student_id IS NULL AND ?))"];args +=[u['id'],permission_key(u),encoded(ids),u['role'] in STAFF]
        if item_id:clauses.append('item_id=?');args.append(item_id)
        if status:clauses.append('status=?');args.append(status)
        return db.execute('SELECT * FROM lost_found_claims WHERE '+' AND '.join(clauses)+' ORDER BY seq DESC LIMIT ?',[*args,limit]).fetchall()

    @app.get('/api/lost-found/claims')
    def claims(request:Request,workspace:Workspace='school',all:bool=False,itemId:str=Query(default='',max_length=100),status:str=Query(default='',pattern='^(pending|approved|rejected|withdrawn|collected)?$'),before:int=Query(default=9223372036854775807,ge=1),limit:int=Query(default=20,ge=1,le=100)):
        with s.context(request,workspace) as (db,u,snap):
            rows=claim_rows(db,u,snap,workspace,all,itemId,status,before,limit+1)
            return dict(items=[s.public_claim(db,dict(r)|dict(data=json.loads(r['document'])),u,snap,workspace) for r in rows[:limit]],nextBefore=rows[limit-1]['seq'] if len(rows)>limit else None)

    @app.get('/api/lost-found/claims/{id}')
    def claim(id:str,request:Request,workspace:Workspace='school'):
        with s.context(request,workspace) as (db,u,snap):
            r=s.claim(db,workspace,id);s.require_claim(db,r,u,snap,workspace);return s.public_claim(db,r,u,snap,workspace)

    @app.post('/api/lost-found/claims/{id}/action')
    def claim_action(id:str,body:ClaimAction,request:Request,workspace:Workspace='school'):
        with s.context(request,workspace,True) as (db,u,snap):
            row=s.claim(db,workspace,id);s.require_claim(db,row,u,snap,workspace)
            if body.action=='withdraw':
                if not s.owns(row,u,snap,workspace):raise HTTPException(403,'Отозвать можно только свою заявку.')
            else:
                s.require_manager(db,u,workspace)
                if body.action in ['approve','collect'] and row['user_id']==u['id']:raise HTTPException(403,'Вашу заявку должен проверить другой ответственный.')
            item=s.item(db,workspace,row['item_id']);key,old=s.replay(db,u,body,'claim-action:'+workspace+':'+id)
            if old:return old
            version(row,body.version);version(item,body.itemVersion)
            transitions={'approve':(['pending'],'approved'),'collect':(['approved'],'collected'),'reject':(['pending','approved'],'rejected'),'reopen':(['approved'],'pending'),'withdraw':(['pending','approved'],'withdrawn')}
            valid,target=transitions[body.action]
            if row['status'] not in valid:raise HTTPException(409,'Заявка уже обработана. Обновите список.')
            if body.action in ['approve','collect']:
                if not s.current_owner(db,row,snap,workspace):raise HTTPException(409,'Связь заявителя с владельцем устарела. Уточните доступ перед выдачей.')
                if body.action=='approve' and item['status']!='open':raise HTTPException(409,'Находка сейчас недоступна для подтверждения владельца.')
                if body.action=='collect' and (item['status']!='reserved' or len(body.receivedBy)<3):raise HTTPException(422,'Укажите, кому фактически передана вещь.')
                if body.action=='approve' and not item['data']['pickupInstructions']:raise HTTPException(422,'Сначала укажите порядок получения в карточке находки.')
            if body.action=='approve':s.change_item(db,item,'reserved')
            elif body.action=='collect':
                s.change_item(db,item,'returned')
                for raw in db.execute("SELECT id FROM lost_found_claims WHERE item_id=? AND status='pending'",(item['id'],)).fetchall():
                    other=s.claim(db,workspace,raw['id']);s.change_claim(db,other,'rejected',u,'Вещь передана подтверждённому владельцу.');s.notify(db,other,u,snap,workspace)
            elif row['status']=='approved':s.change_item(db,item,'open')
            s.change_claim(db,row,target,u,body.reason,body.receivedBy if body.action=='collect' else '')
            s.notify(db,row,u,snap,workspace)
            if body.action=='withdraw':s.notify_managers(db,u,snap,workspace,row['version'])
            return s.remember(db,u,body,key,s.public_claim(db,s.claim(db,workspace,id),u,snap,workspace))

    @app.get('/api/lost-found/history/{id}')
    def history(id:str,request:Request,workspace:Workspace='school',before:int=Query(default=9223372036854775807,ge=1),limit:int=Query(default=20,ge=1,le=50)):
        with s.context(request,workspace) as (db,u,snap):
            s.require_manager(db,u,workspace)
            if id=='config' and u['role']!='admin':raise HTTPException(403,'История назначений доступна администрации.')
            if id!='config':s.item(db,workspace,id)
            rows=db.execute('SELECT * FROM lost_found_history WHERE workspace=? AND item_id=? AND seq<? ORDER BY seq DESC LIMIT ?',(workspace,id,before,limit+1)).fetchall()
            return dict(items=[dict(seq=r['seq'],claimId=r['claim_id'],actor=r['actor_name'],created=r['created'],action=r['action'],reason=r['reason'],data=json.loads(r['document'])) for r in rows[:limit]],nextBefore=rows[limit-1]['seq'] if len(rows)>limit else None)

    @app.get('/api/lost-found/export')
    def export(request:Request,workspace:Workspace='school',all:bool=False,status:str=Query(default='',pattern='^(pending|approved|rejected|withdrawn|collected)?$')):
        file=tempfile.SpooledTemporaryFile(mode='w+',encoding='utf-8',newline='',max_size=512*1024)
        try:
            with s.context(request,workspace) as (db,u,snap):
                rows=claim_rows(db,u,snap,workspace,all,'',status,9223372036854775807,10001)
                if len(rows)>10000:raise HTTPException(422,'Более 10 000 заявок. Уточните состояние для выгрузки.')
                file.write('\ufeff');writer=csv.writer(file);writer.writerow(['Находка','Заявитель','Владелец','Состояние','Создана','Обновлена','Решение','Получил'])
                for r in rows:
                    d=json.loads(r['document']);item=s.item(db,workspace,r['item_id']);values=[item['data']['title'],d['claimantName'],d['ownerName'],r['status'],r['created'],r['updated'],d['decision'],d['receivedBy']]
                    writer.writerow(["'"+str(v) if str(v).lstrip().startswith(('=','+','-','@','\t','\r')) else str(v) for v in values])
            file.seek(0)
            return StreamingResponse(iter(lambda:file.read(65536),''),media_type='text/csv; charset=utf-8',headers={'Content-Disposition':'attachment; filename="lost-found.csv"','Cache-Control':'no-store'},background=BackgroundTask(file.close))
        except BaseException:file.close();raise
