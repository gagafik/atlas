import csv,json,tempfile
from datetime import date
from uuid import uuid4
from fastapi import HTTPException,Query,Request
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask
from .payments import STAFF,stamp,today,version
from .payment_models import ConfigWrite,PayersWrite,ChargeCreate,ChargeAdjust,ReceiptCreate,Allocation,AllocationReverse,ReceiptRefund,AutoAllocate,Remind
from .notifications import encoded,permission_key
from .school_forms import enabled,grant_ids

MONEY={'amount','total','allocated','available','outstanding','charged','received','unallocated','balance','overdue','discount','original'}
def wire(value):
    if isinstance(value,list):return [wire(x) for x in value]
    if isinstance(value,dict):return {k:str(v) if k in MONEY and isinstance(v,int) else wire(v) for k,v in value.items()}
    return value
def csv_response(rows,filename):
    f=tempfile.SpooledTemporaryFile(max_size=1024*1024,mode='w+',encoding='utf-8',newline='');f.write('\ufeff');out=csv.writer(f)
    def safe(v):
        text=str(v if v is not None else '')
        return "'"+text if text.lstrip().startswith(('=','+','-','@','\t','\r')) else text
    try:
        for row in rows:out.writerow([safe(v) for v in row])
        f.seek(0)
    except BaseException:f.close();raise
    return StreamingResponse(iter(lambda:f.read(65536),''),media_type='text/csv; charset=utf-8',headers={'Content-Disposition':f'attachment; filename="{filename}"','Cache-Control':'no-store'},background=BackgroundTask(f.close))

def register(s,app,Workspace):
    @app.get('/api/payments/options')
    def options(request:Request,workspace:Workspace='school'):
        with s.context(request,workspace) as (db,u,snap):
            manage=s.manager(db,u,snap,workspace);config=s.config(db,workspace);admin=u['role']=='admin'
            students=[]
            granted=grant_ids(u,workspace,'student') if u['role']=='parent' else set()
            account_rows=db.execute('SELECT * FROM finance_accounts WHERE workspace=?',(workspace,)) if manage else db.execute('SELECT * FROM finance_accounts WHERE workspace=? AND student_id IN (SELECT value FROM json_each(?))',(workspace,encoded(sorted(granted))))
            stored={a['student_id']:dict(a) for a in account_rows}
            for p in snap.state.students:
                if not p.active or not manage and p.id not in granted:continue
                a=stored.get(p.id) or dict(student_id=p.id,code='',version=0,payers='[]')
                if manage or s.family_access(db,u,snap,workspace,a):students.append(dict(id=p.id,name=p.name,classId=p.classId,className=snap.entities['class_'][p.classId].name,code=a['code'],version=a['version']))
            staff=[];parents=[]
            if admin:
                for row in db.execute("SELECT * FROM accounts WHERE active=1 AND role IN ('admin','editor','teacher','staff','parent') ORDER BY name,id"):
                    p=s.access.public(row)
                    if not enabled(p,snap,workspace):continue
                    if p['role']=='parent':parents.append(dict(id=p['id'],name=p['name'],studentIds=sorted(grant_ids(p,workspace,'student'))))
                    else:staff.append(dict(id=p['id'],name=p['name']))
            public={k:config[k] for k in ['version','currency','decimals','familyPolicy','closedThrough']}
            if admin:public['managerIds']=[x['id'] for x in config['managers']]
            return dict(admin=admin,canManage=manage,config=public,students=students,staff=staff,parents=parents,today=today().isoformat())

    @app.put('/api/payments/config')
    def config_write(body:ConfigWrite,request:Request,workspace:Workspace='school'):
        with s.context(request,workspace,True) as (db,u,snap):
            s.admin(u);key,old=s.replay(db,u,body,'config:'+workspace)
            if old:return old
            current=s.config(db,workspace);version(current,body.version)
            used=db.execute('SELECT 1 FROM finance_entries WHERE workspace=? UNION ALL SELECT 1 FROM finance_plans WHERE workspace=? LIMIT 1',(workspace,workspace)).fetchone()
            if used and (body.currency!=current['currency'] or body.decimals!=current['decimals']):raise HTTPException(409,'Валюта и точность фиксируются после первого плана или операции.')
            managers=[]
            for id in body.managerIds:
                row=db.execute('SELECT * FROM accounts WHERE id=? AND active=1',(id,)).fetchone();p=s.access.public(row) if row else None
                if not p or p['role'] not in STAFF or not enabled(p,snap,workspace):raise HTTPException(422,'Проверьте действующих сотрудников.')
                managers.append(dict(id=id,name=p['name'],permissions=permission_key(p)))
            data=dict(currency=body.currency,decimals=body.decimals,familyPolicy=body.familyPolicy,managers=managers,closedThrough=current['closedThrough'])
            db.execute('INSERT INTO finance_config VALUES (?,?,?) ON CONFLICT(workspace) DO UPDATE SET version=excluded.version,document=excluded.document',(workspace,body.version+1,encoded(data)))
            s.history(db,workspace,u,'','configured',body.reason,dict(before=current,after=data))
            return s.remember(db,u,body,key,dict(version=body.version+1))

    @app.get('/api/payments/accounts')
    def accounts(request:Request,workspace:Workspace='school',q:str=Query(default='',max_length=200),offset:int=Query(default=0,ge=0,le=100000)):
        with s.context(request,workspace) as (db,u,snap):
            manage=s.manager(db,u,snap,workspace);rows=[]
            if manage:
                candidates=db.execute('SELECT * FROM finance_accounts WHERE workspace=? ORDER BY name,student_id',(workspace,)).fetchall()
            else:
                ids=grant_ids(u,workspace,'student') if u['role']=='parent' else []
                candidates=[]
                for id in ids:
                    if id not in snap.entities['student'] or not snap.entities['student'][id].active:continue
                    a=s.account(db,workspace,id,snap)
                    if a['code'] and s.family_access(db,u,snap,workspace,a):candidates.append(a)
            # Only names/current classes, balances are loaded for this page.
            for raw in candidates:
                a=dict(raw);p=snap.entities['student'].get(a['student_id']);name=p.name if p else a['name']
                if q.casefold() not in (name+' '+a['code']).casefold():continue
                rows.append(dict(id=a['student_id'],name=name,code=a['code'],version=a['version'],active=bool(p and p.active)))
            count=len(rows);items=rows[offset:offset+30]
            for a in items:a.update(s.summary(db,workspace,a['id']))
            return wire(dict(items=items,totalCount=count,nextOffset=offset+30 if offset+30<count else None))

    @app.get('/api/payments/accounts/{id}')
    def account(request:Request,id:str,workspace:Workspace='school',before:int=Query(default=9223372036854775807,ge=1),itemOffset:int=Query(default=0,ge=0,le=40000)):
        with s.context(request,workspace) as (db,u,snap):
            a,manage=s.require_account(db,u,snap,workspace,id);p=snap.entities['student'].get(id)
            charges=s.charge_rows(db,workspace,id);credits=s.credits(db,workspace,id)
            # Current open items are bounded by account limits; full ledger is paginated.
            entries=db.execute('SELECT * FROM finance_entries WHERE workspace=? AND student_id=? AND seq<? ORDER BY seq DESC LIMIT 51',(workspace,id,before)).fetchall()
            rows=[{k:r[k] for k in ['seq','id','created','book_date','kind','amount','charge_id','receipt_id','label']}|({'reference':r['reference']} if manage else {}) for r in entries[:50]]
            allocations=db.execute('''SELECT a.* FROM finance_allocations a WHERE a.workspace=? AND a.student_id=? AND a.reversal_of IS NULL
                AND NOT EXISTS(SELECT 1 FROM finance_allocations b WHERE b.reversal_of=a.id) ORDER BY a.seq DESC''',(workspace,id)).fetchall()
            result=dict(id=id,name=p.name if p else a['name'],active=bool(p and p.active),version=a['version'],code=a['code'],canManage=manage,summary=s.summary(db,workspace,id,items=(charges,credits)),
                charges=[{k:c[k] for k in ['id','title','due','total','allocated','outstanding']} for c in charges[itemOffset:itemOffset+50]],
                credits=[{k:c[k] for k in ['id','paid_date','total','allocated','available']}|({k:c[k] for k in ['reference','payer','method']} if manage else {}) for c in credits[itemOffset:itemOffset+50]],
                allocations=[{k:r[k] for k in ['id','charge_id','receipt_id','amount','book_date']} for r in allocations[itemOffset:itemOffset+50]],entries=rows,nextBefore=entries[49]['seq'] if len(entries)>50 else None,
                itemOffset=itemOffset,nextItemOffset=itemOffset+50 if max(len(charges),len(credits),len(allocations))>itemOffset+50 else None)
            if u['role']=='admin':result['payerIds']=[x['id'] for x in json.loads(a['payers'])]
            return wire(result)

    @app.put('/api/payments/accounts/{id}/payers')
    def payers(id:str,body:PayersWrite,request:Request,workspace:Workspace='school'):
        with s.context(request,workspace,True) as (db,u,snap):
            s.admin(u);a,_=s.require_account(db,u,snap,workspace,id,True);key,old=s.replay(db,u,body,'payers:'+workspace+':'+id)
            if old:return old
            version(a,body.version);people=[]
            for pid in body.parentIds:
                r=db.execute('SELECT * FROM accounts WHERE id=? AND active=1',(pid,)).fetchone();p=s.access.public(r) if r else None
                if not p or p['role']!='parent' or id not in grant_ids(p,workspace,'student'):raise HTTPException(422,'Плательщик должен иметь действующую связь с ребёнком.')
                people.append(dict(id=pid,name=p['name'],permissions=permission_key(p)))
            s.account(db,workspace,id,snap,True);db.execute('UPDATE finance_accounts SET payers=?,version=version+1 WHERE workspace=? AND student_id=?',(encoded(people),workspace,id))
            s.history(db,workspace,u,id,'payers',body.reason,dict(before=json.loads(a['payers']),after=people))
            return s.remember(db,u,body,key,dict(version=a['version']+1))

    @app.post('/api/payments/accounts/{id}/charges')
    def charge(id:str,body:ChargeCreate,request:Request,workspace:Workspace='school'):
        with s.context(request,workspace,True) as (db,u,snap):
            a,_=s.require_account(db,u,snap,workspace,id,True);key,old=s.replay(db,u,body,'charge:'+workspace+':'+id)
            if old:return old
            version(a,body.version);s.check_capacity(db,workspace,id);s.account(db,workspace,id,snap,True);cid=uuid4().hex
            db.execute('INSERT INTO finance_charges(id,workspace,student_id,plan_id,installment,title,due,created,book_date,document) VALUES (?,?,?,NULL,NULL,?,?,?,?,?)',(cid,workspace,id,body.title,body.due.isoformat(),stamp(),today().isoformat(),encoded(dict(original=body.amount,discount=0))))
            s.entry(db,workspace,u,id,'charge',body.amount,body.title,charge=cid);s.touch(db,workspace,id);s.history(db,workspace,u,id,'charge',body.reason,dict(id=cid,amount=body.amount,due=body.due.isoformat()))
            s.notify(db,workspace,u,snap,[id],'payments:charged')
            return s.remember(db,u,body,key,dict(id=cid,version=a['version']+1))

    @app.post('/api/payments/accounts/{id}/charges/{chargeId}/adjust')
    def adjust(id:str,chargeId:str,body:ChargeAdjust,request:Request,workspace:Workspace='school'):
        with s.context(request,workspace,True) as (db,u,snap):
            a,_=s.require_account(db,u,snap,workspace,id,True);key,old=s.replay(db,u,body,'adjust:'+workspace+':'+id+':'+chargeId)
            if old:return old
            version(a,body.version);c=next((x for x in s.charge_rows(db,workspace,id) if x['id']==chargeId),None)
            if not c:raise HTTPException(404,'Начисление не найдено.')
            if body.amount<c['allocated']:raise HTTPException(409,'Сначала отмените лишнее распределение оплаты.')
            if body.amount==c['total']:raise HTTPException(422,'Сумма не изменилась.')
            s.entry(db,workspace,u,id,'adjustment',body.amount-c['total'],body.label,charge=chargeId);s.touch(db,workspace,id)
            s.history(db,workspace,u,id,'adjusted',body.reason,dict(chargeId=chargeId,before=c['total'],after=body.amount))
            s.notify(db,workspace,u,snap,[id],'payments:adjusted')
            return s.remember(db,u,body,key,dict(version=a['version']+1))

    @app.post('/api/payments/receipts')
    def receipt(body:ReceiptCreate,request:Request,workspace:Workspace='school'):
        with s.context(request,workspace,True) as (db,u,snap):
            s.require_manager(db,u,snap,workspace);key,old=s.replay(db,u,body,'receipt:'+workspace)
            if old:return old
            rid=s.create_receipt(db,u,snap,workspace,body)
            return s.remember(db,u,body,key,dict(id=rid))

    @app.post('/api/payments/accounts/{id}/allocations')
    def allocate(id:str,body:Allocation,request:Request,workspace:Workspace='school'):
        with s.context(request,workspace,True) as (db,u,snap):
            a,_=s.require_account(db,u,snap,workspace,id,True);key,old=s.replay(db,u,body,'allocation:'+workspace+':'+id)
            if old:return old
            version(a,body.version);s.open_day(db,workspace);s.check_capacity(db,workspace,id)
            c=next((x for x in s.charge_rows(db,workspace,id) if x['id']==body.chargeId),None);r=next((x for x in s.credits(db,workspace,id) if x['id']==body.receiptId),None)
            if not c or not r:raise HTTPException(404,'Начисление или оплата не найдены в этом счёте.')
            if body.amount>c['outstanding'] or body.amount>r['available']:raise HTTPException(409,'Распределение превышает остаток начисления или оплаты.')
            aid=s.allocate(db,workspace,u,id,c['id'],r['id'],body.amount,body.reason);s.touch(db,workspace,id);s.history(db,workspace,u,id,'allocated',body.reason,dict(id=aid,chargeId=c['id'],receiptId=r['id'],amount=body.amount))
            return s.remember(db,u,body,key,dict(id=aid,version=a['version']+1))

    @app.post('/api/payments/accounts/{id}/allocations/{allocationId}/reverse')
    def reverse(id:str,allocationId:str,body:AllocationReverse,request:Request,workspace:Workspace='school'):
        with s.context(request,workspace,True) as (db,u,snap):
            a,_=s.require_account(db,u,snap,workspace,id,True);key,old=s.replay(db,u,body,'reverse:'+workspace+':'+id+':'+allocationId)
            if old:return old
            version(a,body.version);s.open_day(db,workspace)
            r=db.execute('SELECT * FROM finance_allocations WHERE workspace=? AND student_id=? AND id=? AND reversal_of IS NULL',(workspace,id,allocationId)).fetchone()
            if not r:raise HTTPException(404,'Распределение не найдено.')
            if db.execute('SELECT 1 FROM finance_allocations WHERE reversal_of=?',(allocationId,)).fetchone():raise HTTPException(409,'Распределение уже отменено.')
            rid=uuid4().hex;db.execute('INSERT INTO finance_allocations(id,workspace,student_id,charge_id,receipt_id,amount,reversal_of,created,book_date,reason,actor_id) VALUES (?,?,?,?,?,?,?,?,?,?,?)',(rid,workspace,id,r['charge_id'],r['receipt_id'],-r['amount'],r['id'],stamp(),today().isoformat(),body.reason,u['id']))
            s.touch(db,workspace,id);s.history(db,workspace,u,id,'allocation_reversed',body.reason,dict(id=rid,original=r['id'],amount=r['amount']))
            return s.remember(db,u,body,key,dict(version=a['version']+1))

    @app.post('/api/payments/accounts/{id}/auto-allocate')
    def auto(id:str,body:AutoAllocate,request:Request,workspace:Workspace='school'):
        with s.context(request,workspace,True) as (db,u,snap):
            a,_=s.require_account(db,u,snap,workspace,id,True);key,old=s.replay(db,u,body,'auto:'+workspace+':'+id)
            if old:return old
            version(a,body.version);s.open_day(db,workspace);s.check_capacity(db,workspace,id);charges=s.charge_rows(db,workspace,id);credits=s.credits(db,workspace,id);items=[]
            for c in charges:
                for r in credits:
                    amount=min(c['outstanding'],r['available'])
                    if amount<=0:continue
                    aid=s.allocate(db,workspace,u,id,c['id'],r['id'],amount,body.reason);items.append(dict(id=aid,amount=amount,chargeId=c['id'],receiptId=r['id']));c['outstanding']-=amount;r['available']-=amount
                    if len(items)>500:raise HTTPException(413,'Слишком много распределений. Обработайте оплаты отдельными операциями.')
            if not items:raise HTTPException(409,'Нет свободной оплаты и неоплаченных начислений.')
            s.touch(db,workspace,id);s.history(db,workspace,u,id,'auto_allocated',body.reason,items)
            return s.remember(db,u,body,key,wire(dict(items=items,version=a['version']+1)))

    @app.post('/api/payments/accounts/{id}/receipts/{receiptId}/refund')
    def refund(id:str,receiptId:str,body:ReceiptRefund,request:Request,workspace:Workspace='school'):
        with s.context(request,workspace,True) as (db,u,snap):
            a,_=s.require_account(db,u,snap,workspace,id,True);key,old=s.replay(db,u,body,'refund:'+workspace+':'+id+':'+receiptId)
            if old:return old
            version(a,body.version);r=next((x for x in s.credits(db,workspace,id) if x['id']==receiptId),None)
            if not r:raise HTTPException(404,'Оплата не найдена.')
            if body.amount>r['available']:raise HTTPException(409,'Сумма превышает свободный остаток оплаты. Сначала отмените распределение.')
            ref=body.reference.strip().casefold()
            if not ref:raise HTTPException(422,'Укажите документ возврата или исправления.')
            if db.execute("SELECT 1 FROM finance_entries WHERE workspace=? AND kind IN ('refund','correction') AND reference=?",(workspace,ref)).fetchone():raise HTTPException(409,'Документ возврата или исправления уже учтён.')
            eid=s.entry(db,workspace,u,id,body.kind,body.amount,'Возврат оплаты' if body.kind=='refund' else 'Исправление ошибочного поступления',receipt=receiptId,reference=ref)
            s.touch(db,workspace,id);s.history(db,workspace,u,id,body.kind,body.reason,dict(id=eid,receiptId=receiptId,amount=body.amount,reference=ref));s.notify(db,workspace,u,snap,[id],'payments:updated')
            return s.remember(db,u,body,key,dict(version=a['version']+1))

    @app.get('/api/payments/accounts/{id}/history')
    def history(id:str,request:Request,workspace:Workspace='school',before:int=Query(default=9223372036854775807,ge=1)):
        with s.context(request,workspace) as (db,u,snap):
            s.require_account(db,u,snap,workspace,id,True)
            rows=db.execute('SELECT * FROM finance_history WHERE workspace=? AND student_id=? AND seq<? ORDER BY seq DESC LIMIT 51',(workspace,id,before)).fetchall()
            return wire(dict(items=[dict(r)|dict(document=json.loads(r['document'])) for r in rows[:50]],nextBefore=rows[49]['seq'] if len(rows)>50 else None))

    @app.post('/api/payments/accounts/{id}/remind')
    def remind(id:str,body:Remind,request:Request,workspace:Workspace='school'):
        with s.context(request,workspace,True) as (db,u,snap):
            a,_=s.require_account(db,u,snap,workspace,id,True);key,old=s.replay(db,u,body,'remind:'+workspace+':'+id)
            if old:return old
            version(a,body.version)
            balance=s.summary(db,workspace,id)
            if balance['overdue']<=balance['unallocated']:raise HTTPException(409,'Нет непокрытой просрочки. При наличии свободной оплаты сначала распределите её по начислениям.')
            if a['reminded'] and a['reminded']==today().isoformat():raise HTTPException(409,'Сегодня напоминание уже отправляли.')
            s.notify(db,workspace,u,snap,[id],'payments:reminder');db.execute('UPDATE finance_accounts SET reminded=?,version=version+1 WHERE workspace=? AND student_id=?',(today().isoformat(),workspace,id));s.history(db,workspace,u,id,'reminded',body.reason,{})
            return s.remember(db,u,body,key,dict(version=a['version']+1))

    @app.get('/api/payments/accounts/{id}/export')
    def export(id:str,request:Request,workspace:Workspace='school'):
        with s.context(request,workspace) as (db,u,snap):
            a,manage=s.require_account(db,u,snap,workspace,id);cfg=s.config(db,workspace)
            def rows():
                yield ['Дата учёта','Операция','Описание','Сумма в минимальных единицах', 'Валюта','Знаков после запятой','Лицевой счёт']
                for r in db.execute('SELECT * FROM finance_entries WHERE workspace=? AND student_id=? ORDER BY seq',(workspace,id)):yield [r['book_date'],r['kind'],r['label'],r['amount'],cfg['currency'],cfg['decimals'],a['code']]
            return csv_response(rows(),'account-statement.csv')
