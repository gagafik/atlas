import csv,json,tempfile
from uuid import uuid4
from fastapi import HTTPException,Query,Request
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask
from .clubs import STAFF,today,stamp,version,unpack
from .clubs_models import ClubWrite,ClubAction,AccessWrite,Join,MemberAction
from .notifications import encoded,permission_key
from .school_forms import enabled

def register(s,app,Workspace):
    @app.get('/api/clubs/options')
    def options(request:Request,workspace:Workspace='school'):
        with s.context(request,workspace) as (db,u,snap):
            admin=u['role']=='admin';staff=[];own=s.pupils(u,snap,workspace)
            if admin:
                for r in db.execute("SELECT * FROM accounts WHERE active=1 AND role IN ('admin','editor','staff','teacher') ORDER BY name,id"):
                    a=s.access.public(r)
                    if enabled(a,snap,workspace):staff.append(dict(id=a['id'],name=a['name'],role=a['role']))
            return dict(admin=admin,today=today(),staff=staff,classes=[dict(id=c.id,name=c.name) for c in snap.state.classes],students=[dict(id=p.id,name=p.name,classId=p.classId) for p in snap.state.students if p.id in own],canManage=admin or bool(db.execute('SELECT 1 FROM club_access a JOIN clubs c ON c.id=a.club_id WHERE c.workspace=? AND a.user_id=? AND a.permissions=?',(workspace,u['id'],permission_key(u))).fetchone()))

    @app.get('/api/clubs/items')
    def items(request:Request,workspace:Workspace='school',mode:str=Query(default='catalog',pattern='^(catalog|manage|mine)$'),q:str=Query(default='',max_length=200),before:int=Query(default=9223372036854775807,ge=1),limit:int=Query(default=20,ge=1,le=100)):
        with s.context(request,workspace) as (db,u,snap):
            own=s.pupils(u,snap,workspace);classes=sorted({snap.entities['student'][id].classId for id in own})
            if mode=='manage':where="(?='admin' OR EXISTS(SELECT 1 FROM club_access a WHERE a.club_id=c.id AND a.user_id=? AND a.permissions=?))";args=[u['role'],u['id'],permission_key(u)]
            elif mode=='mine':where='EXISTS(SELECT 1 FROM club_members m WHERE m.club_id=c.id AND m.student_id IN (SELECT value FROM json_each(?)))';args=[encoded(sorted(own))]
            else:where="c.status='open' AND (?=1 OR EXISTS(SELECT 1 FROM json_each(c.document,'$.classIds') a WHERE a.value IN (SELECT value FROM json_each(?))))";args=[int(u['role'] in STAFF),encoded(classes)]
            rows=db.execute('SELECT c.* FROM clubs c WHERE c.workspace=? AND c.seq<? AND instr(c.search,?)>0 AND '+where+' ORDER BY c.seq DESC LIMIT ?',[workspace,before,q.strip().casefold(),*args,limit+1]).fetchall()
            return dict(items=[s.public(db,u,snap,unpack(r)) for r in rows[:limit]],nextBefore=rows[limit-1]['seq'] if len(rows)>limit else None)

    @app.get('/api/clubs/items/{id}')
    def item(id:str,request:Request,workspace:Workspace='school'):
        with s.context(request,workspace) as (db,u,snap):
            c=s.club(db,workspace,id);s.visible(db,u,snap,c);result=s.public(db,u,snap,c)
            if s.manager(db,u,c):result['students']=[dict(id=p.id,name=p.name,classId=p.classId) for p in snap.state.students if s.eligible(snap,c,p.id)]
            return result

    def save(body,request,workspace,id):
        with s.context(request,workspace,True) as (db,u,snap):
            previous=s.club(db,workspace,id) if id else None
            if previous:s.require_manager(db,u,previous)
            elif u['role']!='admin':raise HTTPException(403,'Кружок создаёт администрация.')
            key,old=s.replay(db,u,body,'club:'+workspace+':'+(id or 'new'))
            if old:return old
            data=body.data.model_dump(mode='json')
            if any(cid not in snap.entities['class_'] for cid in data['classIds']):raise HTTPException(422,'Выберите действующие классы.')
            if previous:
                version(previous,body.version)
                if previous['status']=='archived':raise HTTPException(409,'Архивный кружок не редактируется.')
                if len(body.reason)<3:raise HTTPException(422,'Укажите причину изменения.')
                if u['role']!='admin' and set(data['classIds'])!=set(previous['data']['classIds']):raise HTTPException(403,'Аудиторию меняет администрация.')
                enrolled=db.execute("SELECT student_id FROM club_members WHERE club_id=? AND status='enrolled'",(id,)).fetchall()
                if len(enrolled)>data['capacity']:raise HTTPException(409,'Нельзя уменьшить лимит ниже числа зачисленных.')
                if any(p['student_id'] in snap.entities['student'] and snap.entities['student'][p['student_id']].classId not in data['classIds'] for p in enrolled):raise HTTPException(409,'Сначала завершите записи учеников исключаемых классов.')
                if db.execute("SELECT 1 FROM club_sessions WHERE club_id=? AND status<>'cancelled' AND (date<? OR date>?)",(id,data['startsOn'],data['endsOn'])).fetchone():raise HTTPException(409,'В новый период не помещаются занятия кружка.')
                db.execute('UPDATE clubs SET version=version+1,updated=?,search=?,document=? WHERE id=?',(stamp(),(data['title']+' '+data['description']).casefold(),encoded(data),id))
            else:
                if body.version!=0:raise HTTPException(409,'Новая карточка должна иметь версию 0.')
                id=uuid4().hex;db.execute("INSERT INTO clubs(id,workspace,version,status,created,updated,search,document) VALUES (?,?,1,'draft',?,?,?,?)",(id,workspace,stamp(),stamp(),(data['title']+' '+data['description']).casefold(),encoded(data)))
            c=s.club(db,workspace,id);s.history(db,u,c,id,'saved',body.reason,dict(before=previous['data'] if previous else None,after=data));s.advance(db,u,snap,c)
            s.notify(db,u,snap,c,[r[0] for r in db.execute("SELECT student_id FROM club_members WHERE club_id=? AND status IN ('enrolled','waiting')",(id,))])
            return s.remember(db,u,body,key,s.public(db,u,snap,c))
    @app.post('/api/clubs/items')
    def create(body:ClubWrite,request:Request,workspace:Workspace='school'):return save(body,request,workspace,None)
    @app.put('/api/clubs/items/{id}')
    def edit(id:str,body:ClubWrite,request:Request,workspace:Workspace='school'):return save(body,request,workspace,id)

    @app.put('/api/clubs/items/{id}/access')
    def access(id:str,body:AccessWrite,request:Request,workspace:Workspace='school'):
        with s.context(request,workspace,True) as (db,u,snap):
            if u['role']!='admin':raise HTTPException(403,'Ответственных назначает администрация.')
            c=s.club(db,workspace,id);key,old=s.replay(db,u,body,'access:'+workspace+':'+id)
            if old:return old
            version(c,body.version);assigned=[]
            for aid in body.accountIds:
                a=s.account(db,aid)
                if not a or a['role'] not in STAFF or not enabled(a,snap,workspace):raise HTTPException(422,'Выберите действующих сотрудников пространства.')
                assigned.append(a)
            # Changing staff must not introduce conflicts into existing future sessions.
            targets={a['id'] for a in assigned}
            overlap=db.execute("SELECT DISTINCT b.club_id FROM club_sessions a JOIN club_sessions b ON a.workspace=b.workspace AND a.club_id<>b.club_id AND a.date=b.date AND a.start<b.end AND b.start<a.end WHERE a.club_id=? AND a.date>=? AND a.status IN ('planned','active') AND b.status IN ('planned','active')",(id,today())).fetchall()
            if any(targets & {a['id'] for a in s.active_staff(db,snap,s.club(db,workspace,r[0]))} for r in overlap):raise HTTPException(409,'У сотрудника уже есть другое занятие кружка в это время.')
            previous=[dict(r) for r in db.execute('SELECT user_id,name FROM club_access WHERE club_id=?',(id,))]
            db.execute('DELETE FROM club_access WHERE club_id=?',(id,))
            for a in assigned:db.execute('INSERT INTO club_access VALUES (?,?,?,?)',(id,a['id'],permission_key(a),a['name']))
            s.bump(db,c);s.history(db,u,c,id,'access',body.reason,dict(before=previous,after=[dict(user_id=a['id'],name=a['name']) for a in assigned]))
            s.notifications.deliver(db,[a for a in assigned if a['id']!=u['id']],workspace,c['version'],'clubs.changed')
            return s.remember(db,u,body,key,s.public(db,u,snap,c))

    @app.post('/api/clubs/items/{id}/action')
    def action(id:str,body:ClubAction,request:Request,workspace:Workspace='school'):
        with s.context(request,workspace,True) as (db,u,snap):
            c=s.club(db,workspace,id);s.require_manager(db,u,c);key,old=s.replay(db,u,body,'action:'+workspace+':'+id)
            if old:return old
            version(c,body.version)
            if c['status']=='archived':raise HTTPException(409,'Кружок уже в архиве.')
            if body.action=='publish':
                if c['status'] not in ['draft','closed'] or today()>c['data']['endsOn']:raise HTTPException(409,'Публикация сейчас недоступна.')
                target='open'
            elif body.action=='close':
                if c['status']!='open':raise HTTPException(409,'Приём сейчас не открыт.')
                target='closed'
            elif body.action=='archive':
                if db.execute("SELECT 1 FROM club_sessions WHERE club_id=? AND status='active'",(id,)).fetchone():raise HTTPException(409,'Сначала завершите или отмените открытые отметки занятия.')
                target='archived'
            else:
                if c['status']!='open':raise HTTPException(409,'Очередь доступна у открытого кружка.')
                target=c['status']
            previous=c['status'];db.execute('UPDATE clubs SET status=? WHERE id=?',(target,id));c['status']=target;s.bump(db,c)
            if body.action in ['close','archive']:
                for r in db.execute("SELECT * FROM club_members WHERE club_id=? AND status IN ('enrolled','waiting')",(id,)).fetchall():
                    if r['status']=='waiting' or target=='archived':s.change_member(db,u,snap,c,unpack(r),'completed' if r['status']=='enrolled' else 'withdrawn',body.reason)
                if target=='archived':
                    for r in db.execute("SELECT id FROM club_sessions WHERE club_id=? AND status='planned'",(id,)).fetchall():
                        db.execute("UPDATE club_sessions SET status='cancelled',version=version+1 WHERE id=?",(r['id'],));s.history(db,u,c,r['id'],'session-cancelled',body.reason,{})
            promoted=s.advance(db,u,snap,c,manual=body.action=='advance')
            s.history(db,u,c,id,body.action,body.reason,dict(before=previous,after=target,promoted=len(promoted)))
            return s.remember(db,u,body,key,s.public(db,u,snap,c))

    @app.get('/api/clubs/items/{id}/members')
    def members(id:str,request:Request,workspace:Workspace='school',status:str=Query(default='',pattern='^(enrolled|waiting|withdrawn|rejected|completed|ineligible)?$'),after:int=Query(default=0,ge=0),limit:int=Query(default=50,ge=1,le=100)):
        with s.context(request,workspace) as (db,u,snap):
            c=s.club(db,workspace,id);s.visible(db,u,snap,c);manager=s.manager(db,u,c);own=encoded(sorted(s.pupils(u,snap,workspace)))
            rows=db.execute("SELECT * FROM club_members WHERE club_id=? AND seq>? AND (?='' OR status=?) AND (?=1 OR student_id IN (SELECT value FROM json_each(?))) ORDER BY seq LIMIT ?",(id,after,status,status,int(manager),own,limit+1)).fetchall()
            return dict(items=[s.public_member(db,u,snap,c,unpack(r)) for r in rows[:limit]],nextAfter=rows[limit-1]['seq'] if len(rows)>limit else None)

    @app.post('/api/clubs/items/{id}/members')
    def join(id:str,body:Join,request:Request,workspace:Workspace='school'):
        with s.context(request,workspace,True) as (db,u,snap):
            c=s.club(db,workspace,id);manager=s.manager(db,u,c)
            if not manager and body.studentId not in s.pupils(u,snap,workspace):raise HTTPException(403,'Выберите своего действующего ребёнка.')
            if manager and len(body.reason)<3:raise HTTPException(422,'Укажите основание записи сотрудником.')
            key,old=s.replay(db,u,body,'join:'+workspace+':'+id)
            if old:return old
            version(c,body.clubVersion)
            if not s.public(db,u,snap,c)['accepting']:raise HTTPException(409,'Запись сейчас закрыта.')
            if not s.eligible(snap,c,body.studentId):raise HTTPException(422,'Ученик не входит в аудиторию кружка.')
            if db.execute("SELECT 1 FROM club_members WHERE club_id=? AND student_id=? AND status IN ('enrolled','waiting')",(id,body.studentId)).fetchone():raise HTTPException(409,'Ребёнок уже записан или стоит в очереди.')
            if db.execute("SELECT count(*) FROM club_members WHERE workspace=? AND student_id=? AND status IN ('enrolled','waiting')",(workspace,body.studentId)).fetchone()[0]>=20:raise HTTPException(422,'У ребёнка уже 20 активных записей.')
            if db.execute("SELECT count(*) FROM club_members WHERE club_id=? AND status='waiting'",(id,)).fetchone()[0]>=500:raise HTTPException(422,'Очередь заполнена.')
            if s.conflict(db,c,body.studentId):raise HTTPException(409,'Занятие пересекается с другим кружком ребёнка.')
            s.bump(db,c);s.advance(db,u,snap,c)
            counts={r[0]:r[1] for r in db.execute("SELECT status,count(*) FROM club_members WHERE club_id=? AND status IN ('enrolled','waiting') GROUP BY status",(id,))}
            status='enrolled' if counts.get('enrolled',0)<c['data']['capacity'] and not counts.get('waiting',0) else 'waiting'
            p=snap.entities['student'][body.studentId];mid=uuid4().hex;data=dict(studentName=p.name,className=snap.entities['class_'][p.classId].name,decision=body.reason,requesterPermissions=permission_key(u))
            db.execute('INSERT INTO club_members(id,club_id,workspace,student_id,user_id,version,status,created,updated,enrolled_on,left_on,document) VALUES (?,?,?,?,?,1,?,?,?,?,?,?)',(mid,id,workspace,p.id,u['id'],status,stamp(),stamp(),today() if status=='enrolled' else '','',encoded(data)))
            s.history(db,u,c,mid,'joined',body.reason,dict(studentId=p.id,studentName=p.name,status=status));s.notify(db,u,snap,c,[p.id])
            return s.remember(db,u,body,key,s.public_member(db,u,snap,c,s.member(db,c,mid)))

    @app.post('/api/clubs/items/{id}/members/{mid}/action')
    def member_action(id:str,mid:str,body:MemberAction,request:Request,workspace:Workspace='school'):
        with s.context(request,workspace,True) as (db,u,snap):
            c=s.club(db,workspace,id);r=s.member(db,c,mid);manager=s.manager(db,u,c)
            if not manager and (body.action!='withdraw' or r['student_id'] not in s.pupils(u,snap,workspace)):raise HTTPException(403,'Нет права изменить эту запись.')
            key,old=s.replay(db,u,body,'member-action:'+workspace+':'+mid)
            if old:return old
            version(c,body.clubVersion);version(r,body.version)
            if c['status']=='archived' or r['status'] not in ['enrolled','waiting']:raise HTTPException(409,'Запись уже завершена.')
            s.bump(db,c)
            if body.action=='promote':
                head=db.execute("SELECT id FROM club_members WHERE club_id=? AND status='waiting' ORDER BY seq LIMIT 1",(id,)).fetchone()
                if not head or head[0]!=mid:raise HTTPException(409,'Сначала рассмотрите предыдущую запись в очереди.')
                promoted=s.advance(db,u,snap,c,manual=True)
                if mid not in promoted:raise HTTPException(409,'Зачисление невозможно: проверьте место, сроки, текущие права и пересечения занятий.')
            else:
                s.change_member(db,u,snap,c,r,'withdrawn' if body.action=='withdraw' else 'rejected',body.reason);s.advance(db,u,snap,c)
            return s.remember(db,u,body,key,s.public_member(db,u,snap,c,s.member(db,c,mid)))

    @app.get('/api/clubs/items/{id}/history')
    def history(id:str,request:Request,workspace:Workspace='school',before:int=Query(default=9223372036854775807,ge=1)):
        with s.context(request,workspace) as (db,u,snap):
            c=s.club(db,workspace,id);s.require_manager(db,u,c);rows=db.execute('SELECT * FROM club_history WHERE club_id=? AND seq<? ORDER BY seq DESC LIMIT 51',(id,before)).fetchall()
            return dict(items=[dict(seq=r['seq'],actor=r['actor_name'],created=r['created'],action=r['action'],reason=r['reason'],data=json.loads(r['document'])) for r in rows[:50]],nextBefore=rows[49]['seq'] if len(rows)>50 else None)

    @app.get('/api/clubs/items/{id}/export')
    def export(id:str,request:Request,workspace:Workspace='school',kind:str=Query(default='members',pattern='^(members|attendance)$')):
        with s.context(request,workspace) as (db,u,snap):
            c=s.club(db,workspace,id);s.visible(db,u,snap,c);manager=s.manager(db,u,c);own=encoded(sorted(s.pupils(u,snap,workspace)))
            if kind=='members':
                rows=db.execute('SELECT document,status,created,enrolled_on,left_on FROM club_members WHERE club_id=? AND (?=1 OR student_id IN (SELECT value FROM json_each(?))) ORDER BY seq LIMIT 10001',(id,int(manager),own)).fetchall()
                result=[['Ученик','Класс','Состояние','Записан','Зачислен','Завершил']]+[[json.loads(r['document'])['studentName'],json.loads(r['document'])['className'],r['status'],r['created'],r['enrolled_on'],r['left_on']] for r in rows]
            else:
                rows=db.execute('SELECT a.*,s.date,s.start,s.status session_status FROM club_attendance a JOIN club_sessions s ON s.id=a.session_id WHERE s.club_id=? AND (?=1 OR a.student_id IN (SELECT value FROM json_each(?))) ORDER BY s.date,s.start,a.student_id LIMIT 10001',(id,int(manager),own)).fetchall()
                result=[['Ученик','Дата','Начало','Занятие','Отметка','Пояснение']]+[[json.loads(r['document'])['studentName'],r['date'],r['start'],r['session_status'],r['status'],r['note']] for r in rows]
            if len(rows)>10000:raise HTTPException(422,'Выгрузка превышает 10 000 строк.')
            f=tempfile.SpooledTemporaryFile(mode='w+',encoding='utf-8',newline='',max_size=1024*1024);f.write('\ufeff');w=csv.writer(f)
            for row in result:w.writerow(["'"+str(x) if str(x).lstrip().startswith(('=','+','-','@')) else str(x) for x in row])
            f.seek(0);return StreamingResponse(f,media_type='text/csv; charset=utf-8',headers={'Content-Disposition':'attachment; filename="clubs-'+kind+'.csv"','Cache-Control':'no-store'},background=BackgroundTask(f.close))
