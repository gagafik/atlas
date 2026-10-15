import csv, io, json, sqlite3, tempfile
from datetime import datetime, timedelta
from uuid import uuid4
from fastapi import HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from .access import now
from .notifications import encoded, permission_key
from .school_forms import enabled
from .admissions import STAFF, stamp, version, campaign_result, case_result
from .admission_models import CampaignSave, CampaignAction, Reviewers, ApplicationCreate, ApplicationSave, ApplicationData, Withdrawal, Review

def register(s,app,Workspace):
    @app.get('/api/admissions/options')
    def options(request:Request,workspace:Workspace='school'):
        with s.staff(request,workspace) as (db,u,snap):
            staff=[]
            if u['role']=='admin':
                for r in db.execute('SELECT * FROM accounts WHERE active=1 ORDER BY name LIMIT 1000'):
                    user=s.access.public(r)
                    if user['role'] in STAFF and enabled(user,snap,workspace):staff.append(dict(id=user['id'],name=user['name'],role=user['role']))
            return dict(canManage=u['role']=='admin',staff=staff)

    @app.get('/api/admissions/campaigns')
    def campaigns(request:Request,workspace:Workspace='school',offset:int=Query(default=0,ge=0,le=10000)):
        with s.staff(request,workspace) as (db,u,snap):
            rows=db.execute('SELECT c.* FROM admission_campaigns c WHERE c.workspace=? AND (? OR EXISTS(SELECT 1 FROM admission_reviewers r WHERE r.campaign_id=c.id AND r.user_id=? AND r.permissions=?)) ORDER BY seq DESC LIMIT 31 OFFSET ?',(workspace,u['role']=='admin',u['id'],permission_key(u),offset)).fetchall()
            return dict(items=[campaign_result(r) for r in rows[:30]],hasMore=len(rows)>30)

    @app.get('/api/admissions/campaigns/{id}')
    def campaign(id:str,request:Request,workspace:Workspace='school'):
        with s.staff(request,workspace) as (db,u,snap):
            row=s.campaign(db,id,workspace);s.require_reviewer(db,u,row);result=campaign_result(row);assignments=[]
            if u['role']=='admin':
                for r in db.execute('SELECT * FROM admission_reviewers WHERE campaign_id=?',(id,)):
                    a=db.execute('SELECT * FROM accounts WHERE id=? AND active=1',(r['user_id'],)).fetchone();a=s.access.public(a) if a else None
                    assignments.append(dict(id=r['user_id'],current=bool(a and a['role'] in STAFF and enabled(a,snap,workspace) and permission_key(a)==r['permissions'])))
            result['reviewers']=assignments;return result

    def save_campaign(body,request,workspace,id=None):
        with s.staff(request,workspace,True) as (db,u,snap):
            s.admin(u);key,old=s.replay(db,u,body,'campaign:'+workspace+':'+(id or 'new'))
            if old:return old
            if id:
                row=s.campaign(db,id,workspace);version(row,body.version)
                if row['published'] or row['status']!='draft':raise HTTPException(409,'Опубликованная анкета зафиксирована. Создайте новую кампанию для других условий.')
                db.execute('UPDATE admission_campaigns SET document=?,version=version+1 WHERE id=?',(encoded(body.data.model_dump(mode='json')),id))
            else:
                if body.version:raise HTTPException(409,'Новая кампания должна иметь версию 0.')
                if db.execute("SELECT count(*) FROM admission_campaigns WHERE workspace=? AND status!='archived'",(workspace,)).fetchone()[0]>=100:raise HTTPException(413,'Достигнут предел 100 кампаний вне архива.')
                id=uuid4().hex;db.execute("INSERT INTO admission_campaigns(id,workspace,version,status,published,created,document) VALUES (?,?,1,'draft',0,?,?)",(id,workspace,stamp(),encoded(body.data.model_dump(mode='json'))))
            s.history(db,u,id,'','campaign_saved','staff',document=body.data.model_dump(mode='json'))
            return s.remember(db,u,body,key,campaign_result(s.campaign(db,id,workspace)))

    @app.post('/api/admissions/campaigns')
    def create_campaign(body:CampaignSave,request:Request,workspace:Workspace='school'):return save_campaign(body,request,workspace)
    @app.put('/api/admissions/campaigns/{id}')
    def edit_campaign(id:str,body:CampaignSave,request:Request,workspace:Workspace='school'):return save_campaign(body,request,workspace,id)

    @app.put('/api/admissions/campaigns/{id}/reviewers')
    def reviewers(id:str,body:Reviewers,request:Request,workspace:Workspace='school'):
        with s.staff(request,workspace,True) as (db,u,snap):
            s.admin(u);row=s.campaign(db,id,workspace);key,old=s.replay(db,u,body,'reviewers:'+id)
            if old:return old
            version(row,body.version)
            if len(set(body.accountIds))!=len(body.accountIds):raise HTTPException(422,'Сотрудник указан дважды.')
            people=[]
            for account_id in body.accountIds:
                a=db.execute('SELECT * FROM accounts WHERE id=? AND active=1',(account_id,)).fetchone();a=s.access.public(a) if a else None
                if not a or a['role'] not in STAFF or not enabled(a,snap,workspace):raise HTTPException(422,'Выберите действующего сотрудника этого пространства.')
                people.append((id,a['id'],permission_key(a)))
            db.execute('DELETE FROM admission_reviewers WHERE campaign_id=?',(id,));db.executemany('INSERT INTO admission_reviewers VALUES (?,?,?)',people)
            db.execute('UPDATE admission_campaigns SET version=version+1 WHERE id=?',(id,));s.history(db,u,id,'','reviewers','staff',document=body.accountIds)
            return s.remember(db,u,body,key,campaign_result(s.campaign(db,id,workspace)))

    @app.post('/api/admissions/campaigns/{id}/action')
    def campaign_action(id:str,body:CampaignAction,request:Request,workspace:Workspace='school'):
        with s.staff(request,workspace,True) as (db,u,snap):
            s.admin(u);row=s.campaign(db,id,workspace);key,old=s.replay(db,u,body,'campaign_action:'+id)
            if old:return old
            version(row,body.version)
            if row['status']=='archived':raise HTTPException(409,'Кампания уже в архиве.')
            if body.action=='close' and row['status']!='open':raise HTTPException(409,'Закрыть можно только открытый приём.')
            if body.action=='open' and datetime.fromisoformat(json.loads(row['document'])['closesAt'])<=now():raise HTTPException(409,'Срок этой кампании уже истёк.')
            if body.action=='archive' and db.execute("SELECT 1 FROM admission_applications WHERE campaign_id=? AND status IN ('submitted','in_review','returned','waitlisted') LIMIT 1",(id,)).fetchone():raise HTTPException(409,'Сначала завершите рассмотрение поданных заявок.')
            target={'open':'open','close':'closed','archive':'archived'}[body.action]
            db.execute('UPDATE admission_campaigns SET status=?,published=max(published,?),version=version+1 WHERE id=?',(target,int(body.action=='open'),id))
            s.history(db,u,id,'',body.action,'staff',body.reason)
            return s.remember(db,u,body,key,campaign_result(s.campaign(db,id,workspace)))

    @app.get('/admissions-api/campaigns')
    def public_campaigns():
        try:
            with s.database() as db:return dict(items=[campaign_result(r) for r in db.execute("SELECT * FROM admission_campaigns WHERE published=1 AND status='open' ORDER BY seq DESC LIMIT 100")])
        except HTTPException as exc:
            if exc.status_code==404:return dict(items=[])
            raise

    @app.get('/admissions-api/campaigns/{id}')
    def public_campaign(id:str):
        with s.database() as db:return campaign_result(s.campaign(db,id,public=True))

    @app.post('/admissions-api/applications')
    def new_application(body:ApplicationCreate,request:Request):
        with s.family(request,True) as (db,u):
            c=s.campaign(db,body.campaignId);key,old=s.replay(db,u,body,'new_case:'+c['id'])
            if old:return old
            s.accepting(c)
            if not c['published'] or body.version:raise HTTPException(409,'Проверьте приёмную кампанию.')
            if db.execute('SELECT count(*) FROM admission_applications WHERE family_id=?',(u['id'],)).fetchone()[0]>=10:raise HTTPException(413,'Достигнут предел 10 заявок семьи. Обратитесь в школу.')
            if db.execute('SELECT count(*) FROM admission_applications WHERE campaign_id=?',(c['id'],)).fetchone()[0]>=5000:raise HTTPException(413,'Лимит заявок кампании достигнут. Обратитесь в школу.')
            id=uuid4().hex;data=ApplicationData(guardianName=u['name']).model_dump(mode='json')
            db.execute("INSERT INTO admission_applications(id,campaign_id,family_id,version,status,created,updated,document) VALUES (?,?,?,1,'draft',?,?,?)",(id,c['id'],u['id'],stamp(),stamp(),encoded(data)))
            return s.remember(db,u,body,key,case_result(s.application(db,id,u),True))

    @app.get('/admissions-api/applications')
    def own_applications(request:Request):
        with s.family(request) as (db,u):
            return dict(items=[dict(id=r['id'],campaignId=r['campaign_id'],title=r['title'],childName=json.loads(r['document'])['childName'],status=r['status'],updated=r['updated']) for r in db.execute("SELECT a.*,json_extract(c.document,'$.title') title FROM admission_applications a JOIN admission_campaigns c ON c.id=a.campaign_id WHERE family_id=? ORDER BY a.seq DESC LIMIT 10",(u['id'],))])

    @app.get('/admissions-api/applications/{id}')
    def own_case(id:str,request:Request):
        with s.family(request) as (db,u):
            row=s.application(db,id,u);return dict(**case_result(row,True),campaign=campaign_result(s.campaign(db,row['campaign_id'])))

    @app.put('/admissions-api/applications/{id}')
    def save_case(id:str,body:ApplicationSave,request:Request):
        with s.family(request,True) as (db,u):
            row=s.application(db,id,u);c=s.campaign(db,row['campaign_id']);key,old=s.replay(db,u,body,'save_case:'+id)
            if old:return old
            version(row,body.version);submitting=body.action=='submit';s.editable(row,c,submitting)
            data=body.data.model_dump(mode='json');s.check_data(db,row,c,data,submitting)
            try:
                if submitting:
                    duplicate=' '.join(data['childName'].casefold().split())+'|'+data['birthDate']
                    db.execute("UPDATE admission_applications SET document=?,submitted=?,submitted_at=?,updated=?,status='submitted',reply_until=NULL,duplicate_key=?,search=?,version=version+1 WHERE id=?",(encoded(data),encoded(data),stamp(),stamp(),duplicate,encoded([data['childName'],data['guardianName'],data['email'],data['phone']]).casefold(),id))
                    for f in data['files']:db.execute('UPDATE admission_files SET linked=1 WHERE id=?',(f['id'],))
                    s.history(db,u,c['id'],id,'submitted','both',document=data)
                else:db.execute('UPDATE admission_applications SET document=?,updated=?,version=version+1 WHERE id=?',(encoded(data),stamp(),id))
            except sqlite3.IntegrityError:raise HTTPException(409,'У этой семьи уже есть действующая заявка на этого ребёнка в кампании.') from None
            return s.remember(db,u,body,key,case_result(s.application(db,id,u),True))

    @app.post('/admissions-api/applications/{id}/withdraw')
    def withdraw(id:str,body:Withdrawal,request:Request):
        with s.family(request,True) as (db,u):
            row=s.application(db,id,u);key,old=s.replay(db,u,body,'withdraw:'+id)
            if old:return old
            version(row,body.version)
            if row['status']=='withdrawn':raise HTTPException(409,'Заявка уже отозвана.')
            db.execute("UPDATE admission_applications SET status='withdrawn',updated=?,version=version+1 WHERE id=?",(stamp(),id));s.history(db,u,row['campaign_id'],id,'withdrawn','both',body.reason)
            return s.remember(db,u,body,key,case_result(s.application(db,id,u),True))

    @app.get('/api/admissions/campaigns/{id}/applications')
    def applications(id:str,request:Request,workspace:Workspace='school',status:str=Query(default='',pattern='^(|submitted|in_review|returned|waitlisted|accepted|rejected|withdrawn)$'),q:str=Query(default='',max_length=100),offset:int=Query(default=0,ge=0,le=10000)):
        with s.staff(request,workspace) as (db,u,snap):
            c=s.campaign(db,id,workspace);s.require_reviewer(db,u,c)
            where='campaign_id=? AND submitted IS NOT NULL';args=[id]
            if status:where+=' AND status=?';args.append(status)
            if q:where+=' AND instr(search,?)>0';args.append(q.casefold())
            rows=db.execute('SELECT * FROM admission_applications WHERE '+where+' ORDER BY seq DESC LIMIT 31 OFFSET ?',[*args,offset]).fetchall()
            counts={r[0]:r[1] for r in db.execute('SELECT status,count(*) FROM admission_applications WHERE campaign_id=? AND submitted IS NOT NULL GROUP BY status',(id,))}
            return dict(items=[dict(id=r['id'],version=r['version'],status=r['status'],childName=json.loads(r['submitted'])['childName'],grade=json.loads(r['submitted'])['grade'],updated=r['updated']) for r in rows[:30]],hasMore=len(rows)>30,counts=counts)

    @app.get('/api/admissions/applications/{id}')
    def detail(id:str,request:Request,workspace:Workspace='school'):
        with s.staff(request,workspace) as (db,u,snap):
            row,c=s.staff_case(db,u,workspace,id);return dict(**case_result(row),campaign=campaign_result(c))

    @app.post('/api/admissions/applications/{id}/review')
    def review(id:str,body:Review,request:Request,workspace:Workspace='school'):
        with s.staff(request,workspace,True) as (db,u,snap):
            row,c=s.staff_case(db,u,workspace,id);key,old=s.replay(db,u,body,'review:'+id)
            if old:return old
            version(row,body.version)
            if c['status']=='archived':raise HTTPException(409,'Кампания в архиве.')
            if body.action in ['accepted','rejected','reopen']:s.admin(u)
            if body.action=='reopen':
                if row['status'] not in ['accepted','rejected']:raise HTTPException(409,'Пересмотреть можно принятое решение.')
                target='in_review'
            elif body.action in ['note','message']:target=row['status']
            else:
                if row['status'] not in ['submitted','in_review','waitlisted'] and not (row['status']=='returned' and body.action=='returned'):raise HTTPException(409,'Дождитесь повторной подачи или пересмотрите решение.')
                target=body.action
            if body.replyUntil and not now()<body.replyUntil<=now()+timedelta(days=180):raise HTTPException(422,'Срок уточнений должен быть в будущем, не дальше 180 дней.')
            reply=body.replyUntil.isoformat() if body.action=='returned' else row['reply_until'] if body.action in ['note','message'] else None
            db.execute('UPDATE admission_applications SET status=?,reply_until=?,updated=?,version=version+1 WHERE id=?',(target,reply,stamp(),id))
            s.history(db,u,c['id'],id,body.action,'staff' if body.action=='note' else 'both',body.message)
            return s.remember(db,u,body,key,case_result(s.application(db,id)))

    def history_result(db,id,staff,offset):
        rows=db.execute("SELECT * FROM admission_history WHERE application_id=? AND audience IN (?, 'both') ORDER BY seq DESC LIMIT 31 OFFSET ?",(id,'staff' if staff else 'family',offset)).fetchall()
        return dict(items=[dict(id=r['seq'],created=r['created'],actor=r['actor_name'],action=r['action'],message=r['message'],data=json.loads(r['document']) if r['document'] else None) for r in rows[:30]],hasMore=len(rows)>30)

    @app.get('/admissions-api/applications/{id}/history')
    def family_history(id:str,request:Request,offset:int=Query(default=0,ge=0,le=10000)):
        with s.family(request) as (db,u):s.application(db,id,u);return history_result(db,id,False,offset)

    @app.get('/api/admissions/applications/{id}/history')
    def staff_history(id:str,request:Request,workspace:Workspace='school',offset:int=Query(default=0,ge=0,le=10000)):
        with s.staff(request,workspace) as (db,u,snap):s.staff_case(db,u,workspace,id);return history_result(db,id,True,offset)

    @app.get('/api/admissions/campaigns/{id}/export')
    def export(id:str,request:Request,workspace:Workspace='school'):
        output=tempfile.SpooledTemporaryFile(max_size=1024*1024,mode='w+b')
        def write(values):
            text=io.StringIO();csv.writer(text).writerow(["'"+str(v) if str(v).lstrip().startswith(('=','+','-','@','\t','\r')) else v for v in values]);output.write(text.getvalue().encode('utf-8'))
        try:
            with s.staff(request,workspace) as (db,u,snap):
                c=s.campaign(db,id,workspace);s.require_reviewer(db,u,c);output.write(b'\xef\xbb\xbf')
                write(['Заявка','Статус','Ребёнок','Дата рождения','Параллель','Представитель','Email','Телефон','Подано'])
                for r in db.execute('SELECT * FROM admission_applications WHERE campaign_id=? AND submitted IS NOT NULL ORDER BY seq',(id,)):
                    d=json.loads(r['submitted']);write([r['id'],r['status'],d['childName'],d['birthDate'],d['grade'],d['guardianName'],d['email'],d['phone'],r['submitted_at']])
            output.seek(0)
        except BaseException:output.close();raise
        def chunks():
            try:
                while data:=output.read(65536):yield data
            finally:output.close()
        return StreamingResponse(chunks(),media_type='text/csv; charset=utf-8',headers={'Content-Disposition':'attachment; filename="admissions.csv"','Cache-Control':'no-store'})
