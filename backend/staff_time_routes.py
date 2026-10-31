"""Staff time endpoints. Transactions never write teaching workspace documents."""
import csv,json,tempfile
from datetime import date
from uuid import uuid4
from fastapi import Request,Query,HTTPException
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask
from .notifications import encoded
from .school_forms import enabled
from .staff_time import bounds,today,stamp,version
from .staff_time_models import SaveDay,DayAction,Leave,LeaveAction,Settings,PeriodAction,minutes


def register(s,app,Workspace):
    def require_person(db,id,snap,workspace):
        person=s.person(db,id,snap,workspace)
        if not person:raise HTTPException(422,'Сотрудник больше не имеет доступа к этому пространству.')
        return person

    def report_data(db,user,workspace,month,employee,archive):
        bounds(month)
        if user['role']!='admin':employee=user['id']
        if archive:
            rows=db.execute('SELECT document FROM staff_time_archives WHERE workspace=? AND month=? AND version=?'+(' AND employee_id=?' if employee else '')+' ORDER BY employee_id',[workspace,month,archive]+([employee] if employee else [])).fetchall()
            docs=[json.loads(r[0]) for r in rows]
            return dict(days=[x for d in docs for x in d['days']],requests=[x for d in docs for x in d['requests']],summary=[d['summary'] for d in docs])
        days,requests=s.month_data(db,workspace,month,employee)
        return dict(days=[s.public_day(r) for r in days],requests=[s.public_leave(r) for r in requests],summary=s.report(days,requests,month))

    @app.get('/api/staff-time/options')
    def options(request:Request,workspace:Workspace='school'):
        with s.context(request,workspace) as (db,user,snap):
            people=[user]
            if user['role']=='admin':
                people=[s.access.public(r) for r in db.execute("SELECT * FROM accounts WHERE active=1 AND role IN ('admin','editor','teacher','staff') ORDER BY name,id")]
                people=[p for p in people if enabled(p,snap,workspace)]
            return dict(canManage=user['role']=='admin',userId=user['id'],today=today().isoformat(),settings=s.settings(db,workspace),employees=[dict(id=p['id'],name=p['name']) for p in people])

    @app.get('/api/staff-time/report')
    def report(request:Request,month:str,workspace:Workspace='school',employee:str='',archive:int=Query(default=0,ge=0)):
        with s.context(request,workspace) as (db,user,snap):
            result=report_data(db,user,workspace,month,employee,archive)
            owner='' if user['role']=='admin' else user['id']
            archives=[r[0] for r in db.execute('SELECT DISTINCT version FROM staff_time_archives WHERE workspace=? AND month=?'+(' AND employee_id=?' if owner else '')+' ORDER BY version DESC',[workspace,month]+([owner] if owner else []))]
            return result|dict(period=s.period(db,workspace,month),archives=archives)

    @app.put('/api/staff-time/settings')
    def settings(body:Settings,request:Request,workspace:Workspace='school'):
        with s.context(request,workspace,True) as (db,user,snap):
            s.admin(user);key,old=s.replay(db,user,body,'settings:'+workspace)
            if old:return old
            current=s.settings(db,workspace);version(current,body.version)
            result=dict(version=body.version+1,selfReport=body.selfReport)
            db.execute('INSERT INTO staff_time_settings VALUES (?,?,?) ON CONFLICT(workspace) DO UPDATE SET version=excluded.version,self_report=excluded.self_report',(workspace,result['version'],body.selfReport))
            s.history(db,workspace,user,'settings','settings','', 'changed',body.reason,result)
            return s.remember(db,user,body,key,result)

    @app.put('/api/staff-time/days')
    def save_day(body:SaveDay,request:Request,workspace:Workspace='school'):
        with s.context(request,workspace,True) as (db,user,snap):
            s.scope(user,body.employeeId)
            if user['role']!='admin' and not s.settings(db,workspace)['selfReport']:raise HTTPException(403,'Табель ведёт администрация.')
            person=require_person(db,body.employeeId,snap,workspace);key,old=s.replay(db,user,body,'save:'+workspace)
            if old:return old
            s.open_dates(db,workspace,body.onDate)
            if body.onDate>today():raise HTTPException(422,'Фактическое время можно записать только за сегодняшний или прошедший день. Для будущего отсутствия подайте заявку.')
            row=db.execute('SELECT * FROM staff_time_days WHERE workspace=? AND employee_id=? AND on_date=?',(workspace,body.employeeId,body.onDate.isoformat())).fetchone()
            version(dict(row) if row else dict(version=0),body.version)
            if row and row['status'] not in ['draft','returned','void']:raise HTTPException(409,'Сначала отзовите запись или откройте её для исправления.')
            id=row['id'] if row else str(uuid4());time=stamp();doc=body.document.model_dump(mode='json')|dict(employeeName=person['name'],source='admin' if user['role']=='admin' else 'employee')
            db.execute('INSERT INTO staff_time_days VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET version=excluded.version,status=excluded.status,updated=excluded.updated,document=excluded.document',(id,workspace,body.employeeId,body.onDate.isoformat(),body.version+1,'draft',row['created'] if row else time,time,encoded(doc)))
            result=s.public_day(s.day(db,workspace,id));s.history(db,workspace,user,'day',id,body.employeeId,'saved','',result)
            return s.remember(db,user,body,key,result)

    @app.post('/api/staff-time/days/{id}/action')
    def day_action(id:str,body:DayAction,request:Request,workspace:Workspace='school'):
        with s.context(request,workspace,True) as (db,user,snap):
            row=s.day(db,workspace,id);s.scope(user,row['employee_id'])
            if body.action not in ['submit','withdraw']:s.admin(user)
            key,old=s.replay(db,user,body,'day:'+workspace+':'+id)
            if old:return old
            version(row,body.version);s.open_dates(db,workspace,date.fromisoformat(row['on_date']))
            transitions={'submit':(['draft','returned'],'submitted'),'withdraw':(['submitted'],'draft'),'approve':(['draft','submitted'],'approved'),'return':(['submitted'],'returned'),'reopen':(['approved'],'draft'),'void':(['draft','submitted','returned','approved'],'void')}
            allowed,status=transitions[body.action]
            if row['status'] not in allowed:raise HTTPException(409,'Состояние записи не допускает это действие.')
            if body.action in ['return','reopen','void'] and not body.reason:raise HTTPException(422,'Укажите причину изменения.')
            if body.action=='submit' and user['role']!='admin' and not s.settings(db,workspace)['selfReport']:raise HTTPException(403,'Самостоятельная подача часов отключена.')
            if body.action in ['submit','approve']:require_person(db,row['employee_id'],snap,workspace)
            if body.action=='approve' and s.work_conflicts(db,workspace,row['employee_id'],row['on_date'],json.loads(row['document'])):raise HTTPException(409,'Рабочие часы пересекаются с согласованным отсутствием.')
            db.execute('UPDATE staff_time_days SET status=?,version=version+1,updated=? WHERE id=?',(status,stamp(),id))
            result=s.public_day(s.day(db,workspace,id));s.history(db,workspace,user,'day',id,row['employee_id'],body.action,body.reason,result)
            s.notify(db,user,snap,workspace,row['employee_id'],result['version'],'staff-time.submitted' if status=='submitted' else 'staff-time.changed',review=status=='submitted')
            return s.remember(db,user,body,key,result)

    @app.post('/api/staff-time/leave')
    def leave(body:Leave,request:Request,workspace:Workspace='school'):
        with s.context(request,workspace,True) as (db,user,snap):
            s.scope(user,body.employeeId);person=require_person(db,body.employeeId,snap,workspace);key,old=s.replay(db,user,body,'leave:'+workspace)
            if old:return old
            s.open_dates(db,workspace,body.startDate,body.endDate)
            if db.execute("SELECT count(*) FROM staff_time_leave WHERE workspace=? AND employee_id=? AND status='submitted'",(workspace,body.employeeId)).fetchone()[0]>=50:raise HTTPException(422,'Сначала разберите ранее поданные заявки.')
            id=str(uuid4());time=stamp();doc={k:v for k,v in body.model_dump(mode='json').items() if k not in ['requestId','employeeId','startDate','endDate']}|dict(employeeName=person['name'])
            db.execute('INSERT INTO staff_time_leave(id,workspace,employee_id,start_date,end_date,start_min,end_min,version,status,created,updated,document) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',(id,workspace,body.employeeId,body.startDate.isoformat(),body.endDate.isoformat(),minutes(body.startTime) if body.startTime else 0,minutes(body.endTime) if body.endTime else 1440,1,'submitted',time,time,encoded(doc)))
            result=s.public_leave(s.leave(db,workspace,id));s.history(db,workspace,user,'leave',id,body.employeeId,'submitted','',result)
            s.notify(db,user,snap,workspace,body.employeeId,1,'staff-time.submitted',review=True)
            return s.remember(db,user,body,key,result)

    @app.get('/api/staff-time/leave/{id}/preview')
    def leave_preview(id:str,request:Request,workspace:Workspace='school'):
        with s.context(request,workspace) as (db,user,snap):
            s.admin(user);row=s.leave(db,workspace,id)
            return s.leave_preview(db,workspace,row)

    @app.post('/api/staff-time/leave/{id}/action')
    def leave_action(id:str,body:LeaveAction,request:Request,workspace:Workspace='school'):
        with s.context(request,workspace,True) as (db,user,snap):
            row=s.leave(db,workspace,id);s.scope(user,row['employee_id'])
            if body.action!='withdraw':s.admin(user)
            key,old=s.replay(db,user,body,'leave-action:'+workspace+':'+id)
            if old:return old
            version(row,body.version);s.open_dates(db,workspace,date.fromisoformat(row['start_date']),date.fromisoformat(row['end_date']))
            if row['status']!=('approved' if body.action=='cancel' else 'submitted'):raise HTTPException(409,'Состояние заявки изменилось.')
            if body.action in ['reject','cancel'] and not body.reason:raise HTTPException(422,'Укажите причину решения.')
            if body.action=='approve':
                require_person(db,row['employee_id'],snap,workspace);preview=s.leave_preview(db,workspace,row)
                if preview['conflictCount']:raise HTTPException(409,'Отсутствие пересекается с подтверждёнными часами или другой заявкой.')
                if body.previewHash!=preview['previewHash']:raise HTTPException(409,'Данные изменились. Повторите проверку заявки.')
            status={'approve':'approved','reject':'rejected','withdraw':'withdrawn','cancel':'cancelled'}[body.action]
            db.execute('UPDATE staff_time_leave SET status=?,version=version+1,updated=? WHERE id=?',(status,stamp(),id))
            result=s.public_leave(s.leave(db,workspace,id));s.history(db,workspace,user,'leave',id,row['employee_id'],body.action,body.reason,result)
            s.notify(db,user,snap,workspace,row['employee_id'],result['version'],'staff-time.changed')
            if body.action in ['approve','cancel']:s.notify_schedulers(db,user,snap,workspace,row['employee_id'],result['version'],body.action)
            return s.remember(db,user,body,key,result)

    @app.get('/api/staff-time/periods/{month}/preview')
    def period_preview(month:str,request:Request,workspace:Workspace='school'):
        with s.context(request,workspace) as (db,user,snap):
            s.admin(user);return s.close_preview(db,workspace,month)[0]

    @app.post('/api/staff-time/periods/{month}/action')
    def period_action(month:str,body:PeriodAction,request:Request,workspace:Workspace='school'):
        with s.context(request,workspace,True) as (db,user,snap):
            s.admin(user);key,old=s.replay(db,user,body,'period:'+workspace+':'+month)
            if old:return old
            period=s.period(db,workspace,month);version(period,body.version);n=body.version+1;time=stamp();last=period['last_closed']
            if body.action=='close':
                if period['status']!='open':raise HTTPException(409,'Месяц уже закрыт.')
                if bounds(month)[1]>=today():raise HTTPException(422,'Закрыть можно только завершившийся календарный месяц.')
                preview,days,requests,summary=s.close_preview(db,workspace,month)
                if preview['pendingDays'] or preview['pendingRequests']:raise HTTPException(409,'Подтвердите, отклоните или аннулируйте все записи на проверке.')
                if preview['previewHash']!=body.previewHash:raise HTTPException(409,'Табель изменился. Повторите проверку перед закрытием.')
                if not summary:raise HTTPException(422,'В месяце нет записей для архива.')
                for person in summary:
                    owner=person['employeeId'];doc=dict(summary=person,days=[s.public_day(r) for r in days if r['employee_id']==owner],requests=[s.public_leave(r) for r in requests if r['employee_id']==owner])
                    db.execute('INSERT INTO staff_time_archives VALUES (?,?,?,?,?,?)',(workspace,month,n,owner,time,encoded(doc)))
                status='closed';last=n
            else:
                if period['status']!='closed':raise HTTPException(409,'Месяц уже открыт.')
                if not body.reason:raise HTTPException(422,'Укажите причину открытия для исправлений.')
                status='open'
            db.execute('INSERT INTO staff_time_periods VALUES (?,?,?,?,?,?) ON CONFLICT(workspace,month) DO UPDATE SET version=excluded.version,status=excluded.status,updated=excluded.updated,last_closed=excluded.last_closed',(workspace,month,n,status,time,last))
            result=s.period(db,workspace,month);s.history(db,workspace,user,'period',month,'',body.action,body.reason,result)
            return s.remember(db,user,body,key,result)

    @app.get('/api/staff-time/history/{entity}/{id}')
    def history(entity:str,id:str,request:Request,workspace:Workspace='school',before:int=Query(default=9223372036854775807,ge=1),limit:int=Query(default=30,ge=1,le=100)):
        with s.context(request,workspace) as (db,user,snap):
            if entity=='day':s.scope(user,s.day(db,workspace,id)['employee_id'])
            elif entity=='leave':s.scope(user,s.leave(db,workspace,id)['employee_id'])
            elif entity in ['period','settings']:s.admin(user)
            else:raise HTTPException(404,'История не найдена.')
            rows=db.execute('SELECT * FROM staff_time_history WHERE workspace=? AND entity=? AND entity_id=? AND seq<? ORDER BY seq DESC LIMIT ?',(workspace,entity,id,before,limit+1)).fetchall()
            return dict(items=[dict(seq=r['seq'],created=r['created'],actor=r['actor_name'],action=r['action'],reason=r['reason'],document=json.loads(r['document'])) for r in rows[:limit]],nextBefore=rows[limit-1]['seq'] if len(rows)>limit else None)

    @app.get('/api/staff-time/export')
    def export(request:Request,month:str,workspace:Workspace='school',employee:str='',archive:int=Query(default=0,ge=0)):
        with s.context(request,workspace) as (db,user,snap):result=report_data(db,user,workspace,month,employee,archive)
        output=tempfile.SpooledTemporaryFile(max_size=1024*1024,mode='w+',encoding='utf-8',newline='')
        output.write('\ufeff')
        writer=csv.writer(output,delimiter=';')
        def safe(value):
            text=str(value if value is not None else '')
            return "'"+text if text.lstrip().startswith(('=','+','-','@')) else text
        writer.writerow(['Сотрудник','Дата / начало','Конец','Тип','Статус','Рабочие минуты','Минуты отсутствия','Запись'])
        for r in result['days']:
            d=r['document'];writer.writerow([safe(d['employeeName']),r['onDate'],'','Табель',r['status'],r['totals']['workMinutes'],r['totals']['absenceMinutes'],safe(json.dumps(d,ensure_ascii=False))])
        for r in result['requests']:writer.writerow([safe(r['employeeName']),r['startDate'],r['endDate'],'Согласование отсутствия',r['status'],'','',safe(json.dumps(r,ensure_ascii=False))])
        output.seek(0)
        return StreamingResponse(iter(lambda:output.read(65536),''),media_type='text/csv; charset=utf-8',headers={'Content-Disposition':f'attachment; filename="staff-time-{month}.csv"'},background=BackgroundTask(output.close))
