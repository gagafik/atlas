"""Private templates, preparation, issuance, delivery and school archive."""
import csv,json,tempfile
from uuid import uuid4
from fastapi import Request,Query,HTTPException
from fastapi.responses import HTMLResponse,StreamingResponse
from starlette.background import BackgroundTask
from .document_models import TemplateWrite,NewDocument,DraftWrite,Action,Assign,values_for
from .school_documents import STAFF,stamp,today,version
from .school_forms import enabled
from .notifications import encoded,permission_key


def register(s,app,Workspace):
    @app.get('/api/documents/options')
    def options(request:Request,workspace:Workspace='school'):
        with s.context(request,workspace) as (db,user,snap):
            result=dict(canManage=user['role']=='admin',students=[],employees=[],templates=[],year=today().year)
            if user['role']=='admin':
                result['students']=[dict(id=p.id,name=p.name,className=snap.entities['class_'][p.classId].name if p.classId in snap.entities['class_'] else '') for p in snap.state.students if p.active]
                for raw in db.execute("SELECT * FROM accounts WHERE active=1 AND role IN ('admin','editor','teacher','staff') ORDER BY name,id"):
                    person=s.access.public(raw)
                    if enabled(person,snap,workspace):result['employees'].append(dict(id=person['id'],name=person['name']))
                result['templates']=[dict(id=r['id'],version=r['version'],active=bool(r['active']),definition=json.loads(r['document'])) for r in db.execute('SELECT * FROM document_templates WHERE workspace=? ORDER BY seq DESC LIMIT 200',(workspace,))]
            return result

    @app.post('/api/documents/templates')
    def create_template(body:TemplateWrite,request:Request,workspace:Workspace='school'):
        with s.context(request,workspace,True) as (db,user,snap):
            s.admin(user);key,old=s.replay(db,user,body,'template-create:'+workspace)
            if old:return old
            if body.version!=0:raise HTTPException(409,'Новый шаблон должен иметь версию 0.')
            if db.execute('SELECT count(*) FROM document_templates WHERE workspace=?',(workspace,)).fetchone()[0]>=200:raise HTTPException(422,'Не более 200 шаблонов в пространстве. Измените существующий шаблон.')
            id=str(uuid4());time=stamp();definition=body.definition.model_dump(mode='json')
            db.execute('INSERT INTO document_templates(id,workspace,version,active,created,updated,document) VALUES (?,?,?,?,?,?,?)',(id,workspace,1,body.active,time,time,encoded(definition)))
            result=dict(id=id,version=1,active=body.active,definition=definition);s.history(db,workspace,user,'template',id,'created',body.reason,result)
            return s.remember(db,user,body,key,result)

    @app.put('/api/documents/templates/{id}')
    def edit_template(id:str,body:TemplateWrite,request:Request,workspace:Workspace='school'):
        with s.context(request,workspace,True) as (db,user,snap):
            s.admin(user);row=s.template(db,workspace,id);key,old=s.replay(db,user,body,'template-edit:'+workspace+':'+id)
            if old:return old
            version(row,body.version)
            if not body.reason:raise HTTPException(422,'Укажите причину изменения шаблона.')
            if row['definition']['kind']!=body.definition.kind:raise HTTPException(422,'Для другого вида получателя создайте отдельный шаблон.')
            definition=body.definition.model_dump(mode='json');result=dict(id=id,version=body.version+1,active=body.active,definition=definition)
            db.execute('UPDATE document_templates SET version=?,active=?,updated=?,document=? WHERE id=?',(result['version'],body.active,stamp(),encoded(definition),id));s.history(db,workspace,user,'template',id,'changed',body.reason,result)
            return s.remember(db,user,body,key,result)

    @app.post('/api/documents/items')
    def create(body:NewDocument,request:Request,workspace:Workspace='school'):
        with s.context(request,workspace,True) as (db,user,snap):
            s.admin(user);key,old=s.replay(db,user,body,'create:'+workspace)
            if old:return old
            template=s.template(db,workspace,body.templateId);version(template,body.templateVersion)
            if not template['active']:raise HTTPException(409,'Шаблон отключён.')
            if db.execute("SELECT count(*) FROM school_documents WHERE workspace=? AND status IN ('draft','submitted','returned')",(workspace,)).fetchone()[0]>=1000:raise HTTPException(422,'Сначала разберите подготовленные документы.')
            definition=template['definition'];kind=definition['kind']
            if bool(body.subjectId)==(kind=='internal'):raise HTTPException(422,'Для личного документа выберите получателя; внутренний документ не имеет личного получателя.')
            row=dict(kind=kind,subject_id=body.subjectId);subject=s.subject(db,row,snap,workspace);permissions=s.assignee(db,body.assigneeId,snap,workspace)
            id=str(uuid4());time=stamp();doc=dict(definition=definition,templateVersion=template['version'],values={})
            db.execute('INSERT INTO school_documents(id,workspace,template_id,version,status,archived,kind,subject_id,subject_name,title,number,created,updated,issued,assignee_id,assignee_permissions,document) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(id,workspace,body.templateId,1,'draft',0,kind,body.subjectId,subject['name'],definition['title'],None,time,time,None,body.assigneeId,permissions,encoded(doc)))
            row=s.document(db,workspace,id);s.history(db,workspace,user,'document',id,'created','',s.public(row,True));s.notify(db,row,user,[s.account(db,body.assigneeId)],'documents.assigned')
            return s.remember(db,user,body,key,s.summary(row))

    @app.get('/api/documents/items')
    def listing(request:Request,workspace:Workspace='school',before:int=Query(default=9223372036854775807,ge=1),limit:int=Query(default=20,ge=1,le=100),status:str=Query(default='',pattern=r'^(draft|submitted|returned|cancelled|issued|revoked)?$'),archived:bool=False,year:int=Query(default=0,ge=0,le=9998)):
        with s.context(request,workspace) as (db,user,snap):return s.listing(db,user,snap,workspace,before,limit,status,archived,year)

    @app.get('/api/documents/items/{id}')
    def detail(id:str,request:Request,workspace:Workspace='school'):
        with s.context(request,workspace) as (db,user,snap):
            row=s.document(db,workspace,id);manage=s.require(db,row,user,snap,workspace)
            return s.public(row,manage)

    @app.put('/api/documents/items/{id}')
    def edit(id:str,body:DraftWrite,request:Request,workspace:Workspace='school'):
        with s.context(request,workspace,True) as (db,user,snap):
            row=s.document(db,workspace,id);s.require(db,row,user,snap,workspace,True);key,old=s.replay(db,user,body,'edit:'+workspace+':'+id)
            if old:return old
            version(row,body.version)
            if row['status'] not in ['draft','returned'] or row['archived']:raise HTTPException(409,'Запись нельзя изменять в этом состоянии.')
            row['content']['values']=values_for(row['content']['definition'],body.values);row['status']='draft'
            return s.remember(db,user,body,key,s.save(db,row,user,'saved'))

    @app.put('/api/documents/items/{id}/assignee')
    def assign(id:str,body:Assign,request:Request,workspace:Workspace='school'):
        with s.context(request,workspace,True) as (db,user,snap):
            s.admin(user);row=s.document(db,workspace,id);key,old=s.replay(db,user,body,'assign:'+workspace+':'+id)
            if old:return old
            version(row,body.version);row['assignee_permissions']=s.assignee(db,body.assigneeId,snap,workspace);row['assignee_id']=body.assigneeId
            result=s.save(db,row,user,'assigned',body.reason);s.notify(db,row,user,[s.account(db,body.assigneeId)],'documents.assigned')
            return s.remember(db,user,body,key,result)

    @app.get('/api/documents/items/{id}/preview')
    def preview(id:str,request:Request,workspace:Workspace='school',recipients:bool=False):
        with s.context(request,workspace) as (db,user,snap):
            s.admin(user);row=s.document(db,workspace,id)
            return s.access_preview(db,row,snap,workspace)[0] if recipients else s.issue_preview(db,row,snap,workspace)[0]

    @app.post('/api/documents/items/{id}/action')
    def action(id:str,body:Action,request:Request,workspace:Workspace='school'):
        with s.context(request,workspace,True) as (db,user,snap):
            row=s.document(db,workspace,id);s.require(db,row,user,snap,workspace,True)
            if body.action not in ['submit','withdraw']:s.admin(user)
            key,old=s.replay(db,user,body,'action:'+workspace+':'+id)
            if old:return old
            version(row,body.version)
            if body.action in ['return','cancel','revoke','archive','unarchive','recipients'] and not body.reason:raise HTTPException(422,'Укажите причину действия.')
            if row['archived'] and body.action not in ['unarchive','revoke','recipients']:raise HTTPException(409,'Сначала верните документ из архива.')
            notify=[];notification='documents.changed'
            if body.action=='issue':
                preview,notify,(series,year,next_number)=s.issue_preview(db,row,snap,workspace)
                if body.previewHash!=preview['previewHash']:raise HTTPException(409,'Номер, получатели или данные изменились. Повторите проверку.')
                db.execute('INSERT INTO document_counters VALUES (?,?,?,?) ON CONFLICT(workspace,series,year) DO UPDATE SET next_number=excluded.next_number',(workspace,series,year,next_number+1))
                row.update(status='issued',number=preview['snapshot']['number'],issued=stamp(),subject_name=preview['snapshot']['subjectName']);row['content']['snapshot']=preview['snapshot'];s.install_recipients(db,row,notify);notification='documents.issued'
            elif body.action=='recipients':
                if row['status'] not in ['issued','revoked']:raise HTTPException(409,'Получатели назначаются выданному документу.')
                preview,notify=s.access_preview(db,row,snap,workspace)
                if body.previewHash!=preview['previewHash']:raise HTTPException(409,'Получатели изменились. Повторите проверку доступа.')
                s.install_recipients(db,row,notify);notification='documents.issued'
            elif body.action in ['archive','unarchive']:
                if row['status'] not in ['issued','revoked','cancelled'] or bool(row['archived'])==(body.action=='archive'):raise HTTPException(409,'Недопустимое состояние архива.')
                row['archived']=int(body.action=='archive')
            else:
                transitions={'submit':(['draft','returned'],'submitted'),'withdraw':(['submitted'],'draft'),'return':(['submitted'],'returned'),'cancel':(['draft','submitted','returned'],'cancelled'),'revoke':(['issued'],'revoked')}
                allowed,status=transitions[body.action]
                if row['status'] not in allowed:raise HTTPException(409,'Состояние документа изменилось.')
                if body.action=='submit':
                    values_for(row['content']['definition'],row['content']['values'],True);s.subject(db,row,snap,workspace)
                    notify=[s.access.public(r) for r in db.execute("SELECT * FROM accounts WHERE role='admin' AND active=1")];notification='documents.submitted'
                elif body.action=='return':
                    assignee=s.account(db,row['assignee_id'])
                    notify=[assignee] if assignee and row['assignee_permissions']==permission_key(assignee) else []
                elif body.action=='revoke':
                    row['content']['revocationReason']=body.reason;notification='documents.revoked'
                    notify=[p for p in s.recipients(db,row,snap,workspace) if db.execute('SELECT 1 FROM document_recipients WHERE document_id=? AND user_id=? AND permissions=?',(id,p['id'],permission_key(p))).fetchone()]
                row['status']=status
            result=s.save(db,row,user,body.action,body.reason);s.notify(db,row,user,notify,notification)
            return s.remember(db,user,body,key,result)

    @app.get('/api/documents/history/{entity}/{id}')
    def history(entity:str,id:str,request:Request,workspace:Workspace='school',before:int=Query(default=9223372036854775807,ge=1),limit:int=Query(default=20,ge=1,le=50)):
        with s.context(request,workspace) as (db,user,snap):
            if entity=='template':s.admin(user);s.template(db,workspace,id)
            elif entity=='document':s.require(db,s.document(db,workspace,id),user,snap,workspace,True)
            else:raise HTTPException(404,'История недоступна.')
            rows=db.execute('SELECT * FROM document_history WHERE workspace=? AND entity=? AND entity_id=? AND seq<? ORDER BY seq DESC LIMIT ?',(workspace,entity,id,before,limit+1)).fetchall()
            return dict(items=[dict(seq=r['seq'],created=r['created'],actor=r['actor_name'],action=r['action'],reason=r['reason'],document=json.loads(r['document'])) for r in rows[:limit]],nextBefore=rows[limit-1]['seq'] if len(rows)>limit else None)

    @app.get('/api/documents/items/{id}/print')
    def print_document(id:str,request:Request,workspace:Workspace='school',download:bool=False):
        with s.context(request,workspace) as (db,user,snap):
            row=s.document(db,workspace,id);s.require(db,row,user,snap,workspace)
            if row['status'] not in ['issued','revoked']:raise HTTPException(409,'Печать доступна после выдачи документа.')
            html=s.html(row)
        return HTMLResponse(html,headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff','Content-Disposition':('attachment' if download else 'inline')+f'; filename="document-{row["id"]}.html"','Content-Security-Policy':"default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'; sandbox allow-modals"})

    @app.get('/api/documents/export')
    def export(request:Request,year:int=Query(ge=1,le=9998),workspace:Workspace='school'):
        with s.context(request,workspace) as (db,user,snap):
            s.admin(user);result=s.listing(db,user,snap,workspace,9223372036854775807,10000,archived=True,year=year)
            if result['nextBefore']:raise HTTPException(422,'За год больше 10000 документов. Нужна отдельная пакетная выгрузка.')
        output=tempfile.SpooledTemporaryFile(max_size=1024*1024,mode='w+',encoding='utf-8',newline='');output.write('\ufeff');writer=csv.writer(output,delimiter=';')
        writer.writerow(['Название','Получатель','Номер','Состояние','Архив','Создан','Выдан'])
        def safe(v):
            text=str(v or '');return "'"+text if text.lstrip().startswith(('=','+','-','@')) else text
        for row in result['items']:writer.writerow([safe(row[k]) for k in ['title','subjectName','number','status','archived','created','issued']])
        output.seek(0);return StreamingResponse(iter(lambda:output.read(65536),''),media_type='text/csv; charset=utf-8',headers={'Content-Disposition':f'attachment; filename="school-documents-{year}.csv"'},background=BackgroundTask(output.close))
