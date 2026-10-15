"""Plans, atomic bank reconciliation and immutable period closing snapshots."""
import csv,io,json,re
from datetime import date
from uuid import uuid4
from fastapi import HTTPException,Query,Request
from .payments import fingerprint,stamp,today,version
from .payment_models import PlanCreate,PlanIssue,Versioned,ImportBank,ClosePeriod,ReceiptCreate,MAX_MONEY
from .payment_routes import wire,csv_response
from .notifications import encoded

def decimal_minor(raw,decimals):
    if not re.fullmatch(r'\d{1,13}(?:[.,]\d{1,'+str(decimals)+r'})?' if decimals else r'\d{1,13}',raw):raise ValueError('Сумма должна быть положительным десятичным числом без разделителей тысяч.')
    parts=raw.replace(',','.').split('.');amount=int(parts[0])*10**decimals+int((parts[1] if len(parts)>1 else '').ljust(decimals,'0') or '0')
    if not 1<=amount<=MAX_MONEY:raise ValueError('Сумма вне допустимого диапазона.')
    return amount

def register(s,app,Workspace):
    def plan(db,w,id):
        row=db.execute('SELECT * FROM finance_plans WHERE workspace=? AND id=?',(w,id)).fetchone()
        if not row:raise HTTPException(404,'План начислений не найден.')
        return dict(row)|dict(data=json.loads(row['document']))
    def issue_preview(db,u,snap,w,id,body):
        s.require_manager(db,u,snap,w);s.open_day(db,w);p=plan(db,w,id);version(p,body.planVersion)
        if p['archived']:raise HTTPException(409,'План находится в архиве.')
        rows=[]
        for item in body.students:
            pupil=snap.entities['student'].get(item.studentId)
            if not pupil or not pupil.active:raise HTTPException(422,'Выберите действующих учеников.')
            a=s.account(db,w,pupil.id,snap);s.check_capacity(db,w,pupil.id,len(p['data']['installments']))
            if db.execute('SELECT 1 FROM finance_charges WHERE plan_id=? AND student_id=?',(id,pupil.id)).fetchone():raise HTTPException(409,f'План уже начислен ученику {pupil.name}.')
            for index,inst in enumerate(p['data']['installments']):
                # Half up, applied once per installment, with an explicit original/discount record.
                discount=(inst['amount']*item.discountBps+5000)//10000 if item.discountBps else item.discountAmount
                if discount>inst['amount']:raise HTTPException(422,'Скидка превышает взнос.')
                rows.append(dict(studentId=pupil.id,name=pupil.name,accountVersion=a['version'],index=index,title=p['data']['title']+' · '+inst['label'],due=inst['due'],original=inst['amount'],discount=discount,amount=inst['amount']-discount))
        result=dict(items=rows,total=sum(x['amount'] for x in rows))
        return result|dict(previewHash=fingerprint([result,s.config(db,w),snap.state.revision,p['version']]))

    @app.get('/api/payments/plans')
    def plans(request:Request,workspace:Workspace='school',before:int=Query(default=9223372036854775807,ge=1),archived:bool=False):
        with s.context(request,workspace) as (db,u,snap):
            s.require_manager(db,u,snap,workspace);rows=db.execute('SELECT * FROM finance_plans WHERE workspace=? AND archived=? AND seq<? ORDER BY seq DESC LIMIT 31',(workspace,int(archived),before)).fetchall()
            return wire(dict(items=[dict(id=r['id'],version=r['version'],archived=bool(r['archived']),data=json.loads(r['document'])) for r in rows[:30]],nextBefore=rows[29]['seq'] if len(rows)>30 else None))
    @app.post('/api/payments/plans')
    def plan_create(body:PlanCreate,request:Request,workspace:Workspace='school'):
        with s.context(request,workspace,True) as (db,u,snap):
            s.require_manager(db,u,snap,workspace);key,old=s.replay(db,u,body,'plan:'+workspace)
            if old:return old
            if db.execute('SELECT count(*) FROM finance_plans WHERE workspace=? AND archived=0',(workspace,)).fetchone()[0]>=500:raise HTTPException(413,'Достигнут предел 500 действующих планов.')
            id=uuid4().hex;doc=body.model_dump(mode='json',exclude={'requestId'})
            db.execute('INSERT INTO finance_plans(id,workspace,version,archived,document) VALUES (?,?,1,0,?)',(id,workspace,encoded(doc)));s.history(db,workspace,u,'','plan_created',body.title,dict(id=id,data=doc))
            return s.remember(db,u,body,key,dict(id=id,version=1))
    @app.post('/api/payments/plans/{id}/archive')
    def archive(id:str,body:Versioned,request:Request,workspace:Workspace='school'):
        with s.context(request,workspace,True) as (db,u,snap):
            s.require_manager(db,u,snap,workspace);key,old=s.replay(db,u,body,'archive:'+workspace+':'+id)
            if old:return old
            p=plan(db,workspace,id);version(p,body.version)
            if p['archived']:raise HTTPException(409,'План уже в архиве.')
            db.execute('UPDATE finance_plans SET archived=1,version=version+1 WHERE id=?',(id,));s.history(db,workspace,u,'','plan_archived',body.reason,dict(id=id))
            return s.remember(db,u,body,key,dict(version=p['version']+1))
    @app.post('/api/payments/plans/{id}/preview')
    def preview(id:str,body:PlanIssue,request:Request,workspace:Workspace='school'):
        with s.context(request,workspace) as (db,u,snap):return wire(issue_preview(db,u,snap,workspace,id,body))
    @app.post('/api/payments/plans/{id}/issue')
    def issue(id:str,body:PlanIssue,request:Request,workspace:Workspace='school'):
        with s.context(request,workspace,True) as (db,u,snap):
            s.require_manager(db,u,snap,workspace);key,old=s.replay(db,u,body,'issue:'+workspace+':'+id)
            if old:return old
            p=issue_preview(db,u,snap,workspace,id,body)
            if body.previewHash!=p['previewHash']:raise HTTPException(409,'Предпросмотр изменился. Проверьте суммы заново.')
            ids=set()
            for row in p['items']:
                student=row['studentId'];s.account(db,workspace,student,snap,True);cid=uuid4().hex
                db.execute('INSERT INTO finance_charges(id,workspace,student_id,plan_id,installment,title,due,created,book_date,document) VALUES (?,?,?,?,?,?,?,?,?,?)',(cid,workspace,student,id,row['index'],row['title'],row['due'],stamp(),today().isoformat(),encoded(dict(original=row['original'],discount=row['discount']))))
                s.entry(db,workspace,u,student,'charge',row['amount'],row['title'],charge=cid);s.history(db,workspace,u,student,'plan_charged',body.reason,dict(chargeId=cid,planId=id,**row));ids.add(student)
            for student in ids:s.touch(db,workspace,student)
            s.notify(db,workspace,u,snap,sorted(ids),'payments:charged')
            return s.remember(db,u,body,key,wire(dict(count=len(p['items']),total=p['total'])))

    def bank_preview(db,u,snap,w,body):
        s.require_manager(db,u,snap,w);s.open_day(db,w);cfg=s.config(db,w);raw=body.text.lstrip('\ufeff')
        if not body.source.strip():raise HTTPException(422,'Укажите источник выписки.')
        reader=csv.DictReader(io.StringIO(raw))
        required=['bank_id','paid_date','account_code','amount','payer']
        if reader.fieldnames!=required:raise HTTPException(422,'Нужен CSV с колонками bank_id,paid_date,account_code,amount,payer в этом порядке.')
        rows=[];seen=set();versions={};errors=[]
        for number,row in enumerate(reader,2):
            if number>101:raise HTTPException(413,'Импортируйте не более 100 поступлений за один раз.')
            try:
                if None in row or any(v is None for v in row.values()):raise ValueError('Число колонок не совпадает.')
                bank_id=row['bank_id'].strip();payer=row['payer'].strip()
                if not 1<=len(bank_id)<=100 or len(payer)>160:raise ValueError('Проверьте номер операции и имя плательщика.')
                ref='bank:'+body.source.strip().casefold()+':'+bank_id.casefold()
                if ref in seen:raise ValueError('Номер операции повторяется внутри файла.')
                seen.add(ref)
                if db.execute('SELECT 1 FROM finance_receipts WHERE workspace=? AND reference_key=?',(w,ref)).fetchone():raise ValueError('Операция этого источника уже учтена.')
                a=db.execute('SELECT * FROM finance_accounts WHERE workspace=? AND code=?',(w,row['account_code'].strip().upper())).fetchone()
                if not a:raise ValueError('Лицевой счёт не найден. Сопоставьте его вручную.')
                amount=decimal_minor(row['amount'].strip(),cfg['decimals']);paid=date.fromisoformat(row['paid_date'].strip())
                if paid>today():raise ValueError('Дата поступления в будущем.')
                sid=a['student_id'];p=snap.entities['student'].get(sid);versions.setdefault(sid,a['version'])
                rows.append(dict(line=number,reference=bank_id,referenceKey=ref,paidDate=paid.isoformat(),studentId=sid,name=p.name if p else a['name'],code=a['code'],version=versions[sid],amount=amount,payer=payer))
                versions[sid]+=1
            except (ValueError,TypeError) as e:errors.append(dict(line=number,message=str(e)))
        if not rows and not errors:raise HTTPException(422,'В файле нет поступлений.')
        result=dict(items=rows,errors=errors,total=sum(x['amount'] for x in rows))
        return result|dict(previewHash=fingerprint([result,cfg,snap.state.revision]))
    @app.post('/api/payments/import/preview')
    def import_preview(body:ImportBank,request:Request,workspace:Workspace='school'):
        with s.context(request,workspace) as (db,u,snap):return wire(bank_preview(db,u,snap,workspace,body))
    @app.post('/api/payments/import')
    def import_apply(body:ImportBank,request:Request,workspace:Workspace='school'):
        with s.context(request,workspace,True) as (db,u,snap):
            s.require_manager(db,u,snap,workspace);key,old=s.replay(db,u,body,'import:'+workspace)
            if old:return old
            preview=bank_preview(db,u,snap,workspace,body)
            if preview['errors']:raise HTTPException(422,'Исправьте все строки перед импортом. Ничего не сохранено.')
            if body.previewHash!=preview['previewHash']:raise HTTPException(409,'Сопоставление изменилось. Выполните предпросмотр заново.')
            ids=[]
            for r in preview['items']:
                cmd=ReceiptCreate(requestId=body.requestId,paidDate=r['paidDate'],method='bank',reference=r['reference'],payer=r['payer'],reason=body.reason,parts=[dict(studentId=r['studentId'],version=r['version'],amount=r['amount'])])
                ids.append(s.create_receipt(db,u,snap,workspace,cmd,r['referenceKey']))
            return s.remember(db,u,body,key,dict(count=len(ids),receiptIds=ids))

    def report(db,w,through):
        # One scan per indexed table, not one query per family.
        day=through.isoformat();items={r['student_id']:dict(id=r['student_id'],name=r['name'],code=r['code'],charged=0,received=0,outstanding=0,unallocated=0,balance=0,overdue=0) for r in db.execute('SELECT * FROM finance_accounts WHERE workspace=?',(w,))}
        rows=db.execute('''WITH ct AS(SELECT charge_id,SUM(amount) total FROM finance_entries WHERE workspace=? AND book_date<=? AND charge_id IS NOT NULL GROUP BY charge_id),
        ca AS(SELECT charge_id,SUM(amount) allocated FROM finance_allocations WHERE workspace=? AND book_date<=? GROUP BY charge_id)
        SELECT c.student_id,c.due,ct.total,COALESCE(ca.allocated,0) allocated FROM finance_charges c JOIN ct ON ct.charge_id=c.id LEFT JOIN ca ON ca.charge_id=c.id WHERE c.workspace=?''',(w,day,w,day,w))
        for r in rows:
            a=items[r['student_id']];a['charged']+=r['total'];out=r['total']-r['allocated'];a['outstanding']+=out
            if r['due']<day:a['overdue']+=out
        for r in db.execute('SELECT student_id,-SUM(amount) total FROM finance_entries WHERE workspace=? AND book_date<=? AND receipt_id IS NOT NULL GROUP BY student_id',(w,day)):items[r['student_id']]['received']=r['total']
        for a in items.values():a['unallocated']=a['received']-(a['charged']-a['outstanding']);a['balance']=a['charged']-a['received']
        return dict(through=day,items=list(items.values()),totals={key:sum(a[key] for a in items.values()) for key in ['charged','received','outstanding','unallocated','balance','overdue']})
    s.report=report
    @app.get('/api/payments/report')
    def report_get(request:Request,workspace:Workspace='school',through:date|None=None,offset:int=Query(default=0,ge=0,le=100000)):
        with s.context(request,workspace) as (db,u,snap):
            s.require_manager(db,u,snap,workspace);day=through or today()
            if day>today():raise HTTPException(422,'Отчёт доступен до сегодняшней даты.')
            r=report(db,workspace,day);count=len(r['items']);r['items']=r['items'][offset:offset+50];r.update(totalCount=count,nextOffset=offset+50 if offset+50<count else None)
            return wire(r)
    @app.get('/api/payments/report/export')
    def report_export(request:Request,workspace:Workspace='school',through:date|None=None):
        with s.context(request,workspace) as (db,u,snap):
            s.require_manager(db,u,snap,workspace);day=through or today()
            if day>today():raise HTTPException(422,'Отчёт доступен до сегодняшней даты.')
            r=report(db,workspace,day);cfg=s.config(db,workspace)
            rows=[['По дату','Лицевой счёт','Ученик','Начислено','Поступило за вычетом возвратов','Не погашено','Свободная оплата','Баланс','Просрочено','Валюта','Точность (минимальные единицы)']]
            rows.extend([r['through'],a['code'],a['name'],*[a[k] for k in ['charged','received','outstanding','unallocated','balance','overdue']],cfg['currency'],cfg['decimals']] for a in r['items'])
            return csv_response(rows,'payments-report.csv')
    def close_preview(db,u,w,body):
        s.admin(u);cfg=s.config(db,w);version(cfg,body.version)
        if body.through>today() or cfg['closedThrough'] and body.through.isoformat()<=cfg['closedThrough']:raise HTTPException(422,'Выберите дату после прежнего закрытия, не позднее сегодня.')
        r=report(db,w,body.through);result=dict(through=r['through'],totals=r['totals'],accounts=len(r['items']),currency=cfg['currency'],decimals=cfg['decimals'])
        return result|dict(previewHash=fingerprint([r,cfg]))
    @app.post('/api/payments/periods/preview')
    def period_preview(body:ClosePeriod,request:Request,workspace:Workspace='school'):
        with s.context(request,workspace) as (db,u,snap):return wire(close_preview(db,u,workspace,body))
    @app.post('/api/payments/periods')
    def close(body:ClosePeriod,request:Request,workspace:Workspace='school'):
        with s.context(request,workspace,True) as (db,u,snap):
            s.admin(u);key,old=s.replay(db,u,body,'close:'+workspace)
            if old:return old
            p=close_preview(db,u,workspace,body)
            if body.previewHash!=p['previewHash']:raise HTTPException(409,'Итоги изменились. Проверьте закрытие заново.')
            id=uuid4().hex;r=report(db,workspace,body.through);cfg=s.config(db,workspace)
            document=dict(**r,currency=cfg['currency'],decimals=cfg['decimals'])
            db.execute('INSERT INTO finance_periods(id,workspace,through,created,actor_id,actor_name,reason,document) VALUES (?,?,?,?,?,?,?,?)',(id,workspace,body.through.isoformat(),stamp(),u['id'],u['name'],body.reason,encoded(document)))
            data={k:v for k,v in cfg.items() if k!='version'};data['closedThrough']=body.through.isoformat()
            db.execute('INSERT INTO finance_config VALUES (?,?,?) ON CONFLICT(workspace) DO UPDATE SET version=excluded.version,document=excluded.document',(workspace,cfg['version']+1,encoded(data)));s.history(db,workspace,u,'','period_closed',body.reason,dict(id=id,**p))
            return s.remember(db,u,body,key,dict(id=id,version=cfg['version']+1))
    @app.get('/api/payments/periods')
    def periods(request:Request,workspace:Workspace='school',before:int=Query(default=9223372036854775807,ge=1)):
        with s.context(request,workspace) as (db,u,snap):
            s.require_manager(db,u,snap,workspace);rows=db.execute('SELECT * FROM finance_periods WHERE workspace=? AND seq<? ORDER BY seq DESC LIMIT 31',(workspace,before)).fetchall()
            return wire(dict(items=[{k:r[k] for k in ['id','through','created','actor_name','reason']}|dict(totals=json.loads(r['document'])['totals']) for r in rows[:30]],nextBefore=rows[29]['seq'] if len(rows)>30 else None))
    @app.get('/api/payments/periods/{id}/export')
    def period_export(id:str,request:Request,workspace:Workspace='school'):
        with s.context(request,workspace) as (db,u,snap):
            s.require_manager(db,u,snap,workspace);r=db.execute('SELECT * FROM finance_periods WHERE workspace=? AND id=?',(workspace,id)).fetchone()
            if not r:raise HTTPException(404,'Закрытие не найдено.')
            d=json.loads(r['document']);rows=[['Дата закрытия','Лицевой счёт','Ученик','Начислено','Поступило нетто','Не погашено','Свободная оплата','Баланс','Просрочено','Валюта','Точность']]
            rows.extend([d['through'],a['code'],a['name'],*[a[k] for k in ['charged','received','outstanding','unallocated','balance','overdue']],d['currency'],d['decimals']] for a in d['items'])
            return csv_response(rows,'closed-payments-period.csv')
