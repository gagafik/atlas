import csv,json,sqlite3,tempfile
from datetime import timedelta
from uuid import uuid4
from fastapi import HTTPException,Query,Request
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask
from .library import STAFF,stamp,today,version
from .library_models import TitleWrite,CopiesCreate,CopyWrite,ConfigWrite,Issue,LoanAction
from .school_forms import enabled,grant_ids
from .notifications import encoded,permission_key


def register(s,app,Workspace):
    @app.get('/api/library/options')
    def options(request:Request,workspace:Workspace='school'):
        with s.context(request,workspace) as (db,user,snap):
            scope=s.scope(db,user,snap,workspace);config=s.config(db,workspace);admin=user['role']=='admin'
            students=[dict(id=p.id,name=p.name,classId=p.classId,className=snap.entities['class_'][p.classId].name) for p in snap.state.students if p.active and s.manage_borrower(scope,'student',p.id,snap)]
            employees=[]
            if scope['all']:
                for raw in db.execute("SELECT * FROM accounts WHERE active=1 AND role IN ('admin','editor','teacher','staff') ORDER BY name,id"):
                    p=s.access.public(raw)
                    if enabled(p,snap,workspace):employees.append(dict(id=p['id'],name=p['name']))
            return dict(admin=admin,canCatalog=scope['all'],canIssue=scope['all'] or bool(scope['classes']),students=students,employees=employees,classes=[dict(id=c.id,name=c.name) for c in snap.state.classes if scope['all'] or c.id in scope['classes']],today=today().isoformat(),defaultDue=(today()+timedelta(days=config['loanDays'])).isoformat(),config=({**config,'assignments':[{k:a[k] for k in ['accountId','wholeSchool','classIds']} for a in config['assignments']]} if admin else None))

    @app.put('/api/library/config')
    def settings(body:ConfigWrite,request:Request,workspace:Workspace='school'):
        with s.context(request,workspace,True) as (db,user,snap):
            s.admin(user);key,old=s.replay(db,user,body,'config:'+workspace)
            if old:return old
            current=s.config(db,workspace);version(current,body.version);assignments=[]
            for a in body.assignments:
                p=s.account(db,a.accountId)
                if not p or p['role'] not in STAFF or not enabled(p,snap,workspace) or set(a.classIds)-snap.entities['class_'].keys():raise HTTPException(422,'Проверьте действующие аккаунты и классы.')
                assignments.append(a.model_dump()|dict(permissions=permission_key(p),accountName=p['name'],classNames=[snap.entities['class_'][c].name for c in a.classIds]))
            data=dict(assignments=assignments,loanDays=body.loanDays,maxRenewals=body.maxRenewals,maxLoans=body.maxLoans)
            db.execute('INSERT INTO library_config VALUES (?,?,?) ON CONFLICT(workspace) DO UPDATE SET version=excluded.version,document=excluded.document',(workspace,body.version+1,encoded(data)))
            s.history(db,workspace,user,'config',workspace,'configured',body.reason,dict(before=current,after=data))
            return s.remember(db,user,body,key,dict(version=body.version+1))

    @app.get('/api/library/titles')
    def titles(request:Request,workspace:Workspace='school',q:str=Query(default='',max_length=200),kind:str=Query(default='',pattern=r'^(book|textbook)?$'),archived:bool=False,before:int=Query(default=9223372036854775807,ge=1),limit:int=Query(default=20,ge=1,le=100)):
        with s.context(request,workspace) as (db,user,snap):
            if archived:s.catalog_manager(db,user,snap,workspace)
            sql='SELECT * FROM library_titles WHERE workspace=? AND seq<?';args=[workspace,before]
            if not archived:sql+=' AND archived=0'
            if q:sql+=' AND instr(search,?)>0';args.append(q.casefold())
            if kind:sql+=" AND json_extract(document,'$.kind')=?";args.append(kind)
            rows=db.execute(sql+' ORDER BY seq DESC LIMIT ?',args+[limit+1]).fetchall();items=[]
            for r in rows[:limit]:
                counts=db.execute("SELECT count(*) AS total,sum(CASE WHEN c.status='available' AND c.condition!='damaged' AND NOT EXISTS(SELECT 1 FROM library_loans l WHERE l.copy_id=c.id AND l.status IN ('active','lost')) THEN 1 ELSE 0 END) AS available FROM library_copies c WHERE title_id=? AND status!='withdrawn'",(r['id'],)).fetchone()
                items.append(dict(id=r['id'],version=r['version'],archived=bool(r['archived']),data=json.loads(r['document']),total=counts['total'],available=counts['available'] or 0))
            return dict(items=items,nextBefore=rows[limit-1]['seq'] if len(rows)>limit else None)

    def save_title(body,request,workspace,id=None):
        with s.context(request,workspace,True) as (db,user,snap):
            s.catalog_manager(db,user,snap,workspace);key,old=s.replay(db,user,body,'title:'+workspace+':'+str(id))
            if old:return old
            if id:
                row=s.title(db,workspace,id);version(row,body.version)
                if not body.reason:raise HTTPException(422,'Укажите причину изменения каталога.')
            elif body.version!=0:raise HTTPException(409,'Новое название должно иметь версию 0.')
            id=id or str(uuid4());data=body.data.model_dump(mode='json');search=' '.join(str(v or '') for v in data.values()).casefold();new_version=body.version+1
            db.execute('INSERT INTO library_titles(id,workspace,version,archived,search,document) VALUES (?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET version=excluded.version,archived=excluded.archived,search=excluded.search,document=excluded.document',(id,workspace,new_version,body.archived,search,encoded(data)))
            result=dict(id=id,version=new_version,data=data,archived=body.archived);s.history(db,workspace,user,'title',id,'title_saved',body.reason,result)
            return s.remember(db,user,body,key,result)
    @app.post('/api/library/titles')
    def add_title(body:TitleWrite,request:Request,workspace:Workspace='school'):return save_title(body,request,workspace)
    @app.put('/api/library/titles/{id}')
    def edit_title(id:str,body:TitleWrite,request:Request,workspace:Workspace='school'):return save_title(body,request,workspace,id)

    @app.get('/api/library/titles/{id}/copies')
    def copies(id:str,request:Request,workspace:Workspace='school',before:int=Query(default=9223372036854775807,ge=1),limit:int=Query(default=30,ge=1,le=100),q:str=Query(default='',max_length=80)):
        with s.context(request,workspace) as (db,user,snap):
            scope=s.scope(db,user,snap,workspace)
            if not scope['all'] and not scope['classes']:raise HTTPException(403,'Экземпляры доступны ответственным за выдачу.')
            s.title(db,workspace,id);rows=db.execute('SELECT id,seq FROM library_copies WHERE workspace=? AND title_id=? AND seq<? AND instr(inventory_key,?)>0 ORDER BY seq DESC LIMIT ?',(workspace,id,before,q.casefold(),limit+1)).fetchall()
            return dict(items=[s.copy(db,workspace,r['id']) for r in rows[:limit]],nextBefore=rows[limit-1]['seq'] if len(rows)>limit else None)

    @app.post('/api/library/titles/{id}/copies')
    def add_copies(id:str,body:CopiesCreate,request:Request,workspace:Workspace='school'):
        with s.context(request,workspace,True) as (db,user,snap):
            s.catalog_manager(db,user,snap,workspace);key,old=s.replay(db,user,body,'copies:'+workspace+':'+id)
            if old:return old
            title=s.title(db,workspace,id);version(title,body.titleVersion)
            if title['archived']:raise HTTPException(409,'Сначала верните название из архива.')
            items=[]
            try:
                for item in body.items:
                    copyid=str(uuid4());db.execute('INSERT INTO library_copies(id,workspace,title_id,version,inventory,inventory_key,location,condition,status) VALUES (?,?,?,?,?,?,?,?,?)',(copyid,workspace,id,1,item.inventory,item.inventory.casefold(),item.location,item.condition,item.status))
                    copy=s.copy(db,workspace,copyid);s.history(db,workspace,user,'copy',copyid,'copy_created','',copy);items.append(copy)
            except sqlite3.IntegrityError:raise HTTPException(409,'Инвентарный номер уже существует. Ни один экземпляр из пакета не добавлен.')
            return s.remember(db,user,body,key,dict(items=items))

    @app.put('/api/library/copies/{id}')
    def edit_copy(id:str,body:CopyWrite,request:Request,workspace:Workspace='school'):
        with s.context(request,workspace,True) as (db,user,snap):
            s.catalog_manager(db,user,snap,workspace);key,old=s.replay(db,user,body,'copy:'+workspace+':'+id)
            if old:return old
            row=s.copy(db,workspace,id);version(row,body.version)
            if row['onLoan']:raise HTTPException(409,'Экземпляр на руках. Оформите возврат, потерю или решение по выдаче.')
            data=body.data
            try:db.execute('UPDATE library_copies SET version=version+1,inventory=?,inventory_key=?,location=?,condition=?,status=? WHERE id=?',(data.inventory,data.inventory.casefold(),data.location,data.condition,data.status,id))
            except sqlite3.IntegrityError:raise HTTPException(409,'Инвентарный номер уже существует.')
            result=s.copy(db,workspace,id);s.history(db,workspace,user,'copy',id,'copy_changed',body.reason,dict(before=row,after=result))
            return s.remember(db,user,body,key,result)

    @app.post('/api/library/loans/preview')
    def preview(body:Issue,request:Request,workspace:Workspace='school'):
        with s.context(request,workspace) as (db,user,snap):return s.issue_preview(db,user,snap,workspace,body)

    @app.post('/api/library/loans')
    def issue(body:Issue,request:Request,workspace:Workspace='school'):
        with s.context(request,workspace,True) as (db,user,snap):
            # Recheck authority before returning any replay result.
            scope=s.scope(db,user,snap,workspace)
            if any(not s.manage_borrower(scope,i.borrower.kind,i.borrower.id,snap) for i in body.items):raise HTTPException(403,'Нет права выдачи этим читателям.')
            key,old=s.replay(db,user,body,'issue:'+workspace)
            if old:return old
            p=s.issue_preview(db,user,snap,workspace,body)
            if not body.previewHash or p['previewHash']!=body.previewHash:raise HTTPException(409,'Состав выдачи или данные изменились. Повторите проверку.')
            items=[];recipients=s.recipient_map(db,snap,workspace);notify={}
            for item in p['items']:
                c,b=item['copy'],item['borrower'];id=str(uuid4());time=stamp();doc=dict(title=c['title'],inventory=c['inventory'],conditionOut=c['condition'])
                db.execute('INSERT INTO library_loans(id,workspace,copy_id,version,kind,borrower_id,class_id,borrower_name,status,issued,due,returned,renewals,reminded,document) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(id,workspace,c['id'],1,b['kind'],b['id'],b['classId'],b['name'],'active',time,p['due'],None,0,None,encoded(doc)))
                db.execute('UPDATE library_copies SET version=version+1 WHERE id=?',(c['id'],));row=s.loan(db,workspace,id);public=s.public_loan(row,True)
                s.history(db,workspace,user,'loan',id,'issued','',public);s.history(db,workspace,user,'copy',c['id'],'issued','',public);items.append(public)
                notify.update({u['id']:u for u in recipients.get((b['kind'],b['id']),[]) if u['id']!=user['id']})
            s.notifications.deliver(db,list(notify.values()),workspace,1,'library.issued')
            return s.remember(db,user,body,key,dict(items=items))

    def loan_list(db,user,snap,workspace,before,limit,status,overdue):
        scope=s.scope(db,user,snap,workspace);sql='SELECT * FROM library_loans WHERE workspace=? AND seq<?';args=[workspace,before]
        if status=='open':sql+=" AND status IN ('active','lost')"
        elif status:sql+=' AND status=?';args.append(status)
        if overdue:sql+=" AND ((status='active' AND due<?) OR status='lost')";args.append(today().isoformat())
        if not scope['all']:
            students=[p.id for p in snap.state.students if p.active and (p.classId in scope['classes'] or user['role'] in ['parent','student'] and p.id in grant_ids(user,workspace,'student'))]
            sql+=" AND ((kind='student' AND borrower_id IN (SELECT value FROM json_each(?))) OR (kind='employee' AND borrower_id=?))";args.extend([encoded(students),user['id'] if user['role'] in STAFF else ''])
        rows=db.execute(sql+' ORDER BY seq DESC LIMIT ?',args+[limit+1]).fetchall();items=[]
        for row in rows[:limit]:items.append(s.public_loan(row,s.require_loan(db,row,user,snap,workspace)))
        return dict(items=items,nextBefore=rows[limit-1]['seq'] if len(rows)>limit else None)

    @app.get('/api/library/loans')
    def loans(request:Request,workspace:Workspace='school',before:int=Query(default=9223372036854775807,ge=1),limit:int=Query(default=20,ge=1,le=100),status:str=Query(default='open',pattern=r'^(open|active|lost|returned|resolved|cancelled)?$'),overdue:bool=False):
        with s.context(request,workspace) as (db,user,snap):return loan_list(db,user,snap,workspace,before,limit,status,overdue)

    @app.get('/api/library/loans/{id}')
    def loan_detail(id:str,request:Request,workspace:Workspace='school'):
        with s.context(request,workspace) as (db,user,snap):
            row=s.loan(db,workspace,id);return s.public_loan(row,s.require_loan(db,row,user,snap,workspace))

    @app.post('/api/library/loans/{id}/action')
    def action(id:str,body:LoanAction,request:Request,workspace:Workspace='school'):
        with s.context(request,workspace,True) as (db,user,snap):
            row=s.loan(db,workspace,id);s.require_loan(db,row,user,snap,workspace,True)
            if body.action in ['writeoff','cancel']:s.admin(user)
            key,old=s.replay(db,user,body,'loan-action:'+workspace+':'+id)
            if old:return old
            version(row,body.version);doc=json.loads(row['document']);a=body.action
            if a in ['lost','writeoff','cancel','renew'] and not body.reason:raise HTTPException(422,'Укажите причину действия.')
            if row['status'] not in (['lost'] if a in ['found','writeoff'] else ['active'] if a!='remind' else ['active','lost']):raise HTTPException(409,'Состояние выдачи изменилось.')
            if a=='renew':
                config=s.config(db,workspace)
                if not body.due or body.due.isoformat()<=row['due'] or not today()<=body.due<=today()+timedelta(days=365):raise HTTPException(422,'Новая дата должна быть позже прежней, не раньше сегодня и не дальше года.')
                if row['renewals']>=config['maxRenewals']:raise HTTPException(409,'Достигнуто разрешённое число продлений.')
                row.update(due=body.due.isoformat(),renewals=row['renewals']+1)
            elif a=='remind':
                if row['reminded']==today().isoformat():raise HTTPException(409,'Напоминание по этой выдаче уже отправляли сегодня.')
                row['reminded']=today().isoformat()
            else:
                copy_status='available';condition=body.condition
                if a=='lost':row['status']='lost';copy_status='lost';condition=s.copy(db,workspace,row['copy_id'])['condition']
                elif a=='writeoff':row['status']='resolved';row['returned']=stamp();copy_status='withdrawn';condition=s.copy(db,workspace,row['copy_id'])['condition']
                elif a=='cancel':row['status']='cancelled';row['returned']=stamp();condition=doc['conditionOut']
                else:row['status']='returned';row['returned']=stamp();doc['conditionIn']=condition;copy_status='repair' if condition=='damaged' else 'available'
                db.execute('UPDATE library_copies SET version=version+1,status=?,condition=? WHERE id=?',(copy_status,condition,row['copy_id']))
            row['version']+=1
            db.execute('UPDATE library_loans SET version=?,status=?,due=?,returned=?,renewals=?,reminded=?,document=? WHERE id=?',(row['version'],row['status'],row['due'],row['returned'],row['renewals'],row['reminded'],encoded(doc),id));row['document']=encoded(doc)
            result=s.public_loan(row,True);s.history(db,workspace,user,'loan',id,a,body.reason,result);s.history(db,workspace,user,'copy',row['copy_id'],a,body.reason,result);s.notify(db,row,user,snap,workspace,'library.reminder' if a=='remind' else 'library.changed')
            return s.remember(db,user,body,key,result)

    @app.get('/api/library/history/{entity}/{id}')
    def history(entity:str,id:str,request:Request,workspace:Workspace='school',before:int=Query(default=9223372036854775807,ge=1),limit:int=Query(default=20,ge=1,le=50)):
        with s.context(request,workspace) as (db,user,snap):
            if entity=='loan':s.require_loan(db,s.loan(db,workspace,id),user,snap,workspace,True)
            elif entity=='config':s.admin(user)
            elif entity in ['title','copy','audit']:s.catalog_manager(db,user,snap,workspace)
            else:raise HTTPException(404,'История недоступна.')
            rows=db.execute('SELECT * FROM library_history WHERE workspace=? AND entity=? AND entity_id=? AND seq<? ORDER BY seq DESC LIMIT ?',(workspace,entity,id,before,limit+1)).fetchall()
            return dict(items=[dict(seq=r['seq'],created=r['created'],actor=r['actor_name'],action=r['action'],reason=r['reason'],document=json.loads(r['document'])) for r in rows[:limit]],nextBefore=rows[limit-1]['seq'] if len(rows)>limit else None)

    @app.get('/api/library/export')
    def export(request:Request,workspace:Workspace='school',status:str=Query(default='open',pattern=r'^(open|active|lost|returned|resolved|cancelled)?$'),overdue:bool=False):
        with s.context(request,workspace) as (db,user,snap):
            result=loan_list(db,user,snap,workspace,9223372036854775807,10000,status,overdue)
            if result['nextBefore']:raise HTTPException(422,'Больше 10000 выдач. Уточните фильтр; для большего объёма нужна пакетная выгрузка.')
        out=tempfile.SpooledTemporaryFile(max_size=1024*1024,mode='w+',encoding='utf-8',newline='');out.write('\ufeff');writer=csv.writer(out,delimiter=';');writer.writerow(['Книга','Инвентарный номер','Читатель','Выдана','Вернуть до','Состояние','Закрыта'])
        def safe(v):
            t=str(v or '');return "'"+t if t.lstrip().startswith(('=','+','-','@')) else t
        for r in result['items']:writer.writerow([safe(v) for v in [r['title']['title'],r['inventory'],r['borrowerName'],r['issued'],r['due'],r['status'],r['returned']]])
        out.seek(0);return StreamingResponse(iter(lambda:out.read(65536),''),media_type='text/csv; charset=utf-8',headers={'Cache-Control':'no-store','Content-Disposition':'attachment; filename="library-loans.csv"'},background=BackgroundTask(out.close))
