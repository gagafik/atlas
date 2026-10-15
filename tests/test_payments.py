from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timezone
from uuid import uuid4
import json
import pytest
from backend import api,payments
from test_access import client,enable,create,signed

def path(part):return '/api/payments/'+part+('&' if '?' in part else '?')+'workspace=demo'
def body(**kw):return dict(requestId=str(uuid4()),**kw)
def ok(r):
    assert r.status_code==200,r.text
    return r.json()
def get(c,p):return ok(c.get(path(p)))
def post(c,p,**kw):return c.post(path(p),json=body(**kw))
def account(c,id='s1'):return get(c,'accounts/'+id)
def charge(c,id='s1',amount=10000,**kw):return ok(post(c,'accounts/'+id+'/charges',version=account(c,id)['version'],amount=amount,title='School fee',due='2026-10-01',reason='Approved fee',**kw))
def receipt(c,id='s1',amount=15000,**kw):return ok(post(c,'receipts',paidDate='2026-10-14',reference=uuid4().hex,reason='Bank statement checked',parts=[dict(studentId=id,version=account(c,id)['version'],amount=amount)],**kw))
def config(c,**kw):
    cfg=get(c,'options')['config'];data={k:cfg[k] for k in ['version','currency','decimals','familyPolicy','managerIds']};data.update(kw)
    return ok(c.put(path('config'),json=body(reason='School finance policy',**data)))
def payers(c,parent,id='s1'):
    return ok(c.put(path('accounts/'+id+'/payers'),json=body(version=account(c,id)['version'],parentIds=[parent['id']],reason='School checked payer')))
def act(c,id,action,**kw):return post(c,'accounts/'+id+'/'+action,version=account(c,id)['version'],reason='Verified operation',**kw)
@pytest.fixture
def setup(client,monkeypatch):
    monkeypatch.setattr(payments,'now',lambda:datetime(2026,10,15,12,tzinfo=timezone.utc))
    enable(client);parent=create(client,'parent');teacher=create(client,'teacher');pupil=create(client,'student');other=create(client,'parent',login='other',grants=[dict(workspace='demo',view='student',entityIds=['s3'])])
    return parent,teacher,pupil,other

def test_explicit_finance_permissions_and_current_parent_relationship(client,setup):
    parent,teacher,*_=setup;state=api.read('demo');charge(client);p=signed('parent');t=signed('teacher');pupil=signed('student')
    assert not get(p,'accounts')['items'] and not get(p,'options')['students']
    assert p.get(path('accounts/s1')).status_code==404
    assert t.get(path('report')).status_code==403 and pupil.get(path('accounts/s1')).status_code==404
    payers(client,parent);assert account(p)['summary']['balance']=='10000'
    assert p.get(path('accounts/s1/history')).status_code==404
    assert act(p,'s1','auto-allocate').status_code==404
    config(client,managerIds=[teacher['id']]);assert get(t,'options')['canManage'];assert account(t)['canManage']
    config(client,managerIds=[]);assert t.get(path('accounts/s1')).status_code==404
    with api.connect() as db:db.execute('UPDATE accounts SET permissions=?,version=version+1 WHERE id=?',(json.dumps(dict(workspaces=['demo'],grants=[])),parent['id']))
    assert p.get(path('accounts/s1')).status_code in [403,404]
    assert api.read('demo')==state

def test_verified_policy_requires_verified_active_link_not_legacy_grant(client,setup):
    parent,*_=setup;charge(client);config(client,familyPolicy='verified');p=signed('parent')
    assert p.get(path('accounts/s1')).status_code==404
    with api.connect() as db:
        row=db.execute('SELECT document FROM family_links WHERE parent_id=? AND student_id=?',(parent['id'],'s1')).fetchone();doc=json.loads(row[0]);doc['verified']=True
        db.execute('UPDATE family_links SET document=? WHERE parent_id=? AND student_id=?',(json.dumps(doc),parent['id'],'s1'))
    assert account(p)['summary']['balance']=='10000'
    with api.connect() as db:
        doc['status']='revoked';db.execute('UPDATE family_links SET document=? WHERE parent_id=? AND student_id=?',(json.dumps(doc),parent['id'],'s1'))
    assert p.get(path('accounts/s1')).status_code==404

def test_money_conservation_allocation_adjustment_refund_and_immutable_history(client,setup):
    parent,*_=setup;payers(client,parent);c=charge(client);r=receipt(client);a=account(client)
    assert a['summary']==dict(charged='10000',received='15000',outstanding='10000',unallocated='15000',balance='-5000',overdue='10000')
    allocation=ok(act(client,'s1','allocations',chargeId=c['id'],receiptId=r['id'],amount=10000))
    a=account(client);assert a['summary']['outstanding']=='0' and a['summary']['unallocated']=='5000'
    assert act(client,'s1',f'charges/{c["id"]}/adjust',amount=9000,label='Discount').status_code==409
    assert act(client,'s1',f'receipts/{r["id"]}/refund',amount=5001,reference='R1').status_code==409
    ok(act(client,'s1',f'receipts/{r["id"]}/refund',amount=5000,reference='R1'))
    assert account(client)['summary']['balance']=='0'
    ok(act(client,'s1',f'allocations/{allocation["id"]}/reverse'))
    ok(act(client,'s1',f'charges/{c["id"]}/adjust',amount=9000,label='Approved discount'))
    ok(act(client,'s1','auto-allocate'));assert account(client)['summary']['unallocated']=='1000'
    ok(act(client,'s1',f'receipts/{r["id"]}/refund',amount=1000,reference='R2'))
    assert account(client)['summary']['balance']=='0'
    assert len(account(client)['entries'])==5
    history=get(client,'accounts/s1/history');assert {'receipt','refund','allocation_reversed','adjusted'}<={x['action'] for x in history['items']}
    family=account(signed('parent'));assert all('reference' not in r and 'payer' not in r for r in family['credits'])
    assert all('reference' not in r for r in family['entries'])

def test_duplicate_racing_payment_cannot_double_credit_and_replay_is_permission_checked(client,setup):
    _,teacher,*_=setup;charge(client);v=account(client)['version'];payload=dict(paidDate='2026-10-15',reference='BANK-42',reason='Checked',parts=[dict(studentId='s1',version=v,amount=10000)])
    sessions=[signed('admin'),signed('admin')]
    with ThreadPoolExecutor(2) as pool:results=list(pool.map(lambda c:post(c,'receipts',**payload),sessions))
    assert sorted(r.status_code for r in results)==[200,409];assert account(client)['summary']['received']=='10000'
    config(client,managerIds=[teacher['id']]);t=signed('teacher');req=body(**(payload|dict(reference='BANK-43',parts=[dict(studentId='s1',version=account(client)['version'],amount=1000)])))
    first=ok(t.post(path('receipts'),json=req));assert t.post(path('receipts'),json=req).json()==first
    config(client,managerIds=[]);assert t.post(path('receipts'),json=req).status_code==403

def test_concurrent_allocation_and_atomic_split_receipt(client,setup):
    c=charge(client);r=receipt(client,amount=10000);v=account(client)['version']
    sessions=[signed('admin'),signed('admin')];payload=dict(version=v,reason='Match',chargeId=c['id'],receiptId=r['id'],amount=10000)
    with ThreadPoolExecutor(2) as pool:results=list(pool.map(lambda x:post(x,'accounts/s1/allocations',**payload),sessions))
    assert sorted(x.status_code for x in results)==[200,409]
    first=account(client)['summary'];assert post(client,'receipts',paidDate='2026-10-15',reference='SPLIT-1',reason='Family receipt',parts=[dict(studentId='s1',version=account(client)['version'],amount=1000),dict(studentId='missing',version=0,amount=1000)]).status_code==404
    assert account(client)['summary']==first

def test_same_receipt_two_children_never_discloses_other_child_or_full_amount(client,setup):
    parent,*_=setup;payers(client,parent);r=ok(post(client,'receipts',paidDate='2026-10-15',reference='PRIVATE-BANK',payer='Private payer',reason='Private staff note',parts=[dict(studentId='s1',version=account(client)['version'],amount=1000),dict(studentId='s3',version=0,amount=9000)]))
    p=signed('parent');data=account(p);assert data['summary']['received']=='1000'
    text=json.dumps(data);assert 'Private' not in text and 'PRIVATE' not in text and '9000' not in text
    assert p.get(path('accounts/s3')).status_code==404

def test_plans_preview_discounts_repeat_guard_and_atomic_rollback(client,setup,monkeypatch):
    plan=ok(post(client,'plans',title='Term',installments=[dict(label='October',due='2026-10-01',amount=10001),dict(label='November',due='2026-11-01',amount=10001)]))
    data=dict(planVersion=1,students=[dict(studentId='s1',discountBps=5000),dict(studentId='s3',discountAmount=1000)],reason='Concessions approved')
    preview=ok(post(client,'plans/'+plan['id']+'/preview',**data));assert preview['total']=='28002'
    assert preview['items'][0]['discount']=='5001' and preview['items'][0]['amount']=='5000'
    saved=api.payments.history
    def fail(*a,**kw):raise RuntimeError('Synthetic rollback')
    monkeypatch.setattr(api.payments,'history',fail)
    with pytest.raises(RuntimeError):post(client,'plans/'+plan['id']+'/issue',**data,previewHash=preview['previewHash'])
    assert not get(client,'accounts')['items'];monkeypatch.setattr(api.payments,'history',saved)
    req=body(**data,previewHash=preview['previewHash']);result=ok(client.post(path('plans/'+plan['id']+'/issue'),json=req));assert result['count']==4
    assert client.post(path('plans/'+plan['id']+'/issue'),json=req).json()==result
    assert post(client,'plans/'+plan['id']+'/preview',**data).status_code==409
    assert account(client)['summary']['charged']=='10000'
    cfg=get(client,'options')['config'];cfg.pop('closedThrough');assert client.put(path('config'),json=body(**cfg|dict(currency='USD',reason='Changed'))).status_code==409

def test_stale_preview_after_account_or_school_change(client,setup):
    p=ok(post(client,'plans',title='Trip',installments=[dict(label='Visit',due='2026-11-01',amount=1000)]));data=dict(planVersion=1,students=[dict(studentId='s1')],reason='Trip')
    preview=ok(post(client,'plans/'+p['id']+'/preview',**data));charge(client)
    assert post(client,'plans/'+p['id']+'/issue',**data,previewHash=preview['previewHash']).status_code==409
    preview=ok(post(client,'plans/'+p['id']+'/preview',**data));state=api.read('demo');state.students[0].name='New name';api.save('demo',state)
    assert post(client,'plans/'+p['id']+'/issue',**data,previewHash=preview['previewHash']).status_code==409

def test_bank_import_exact_matching_errors_and_duplicate_sources(client,setup):
    charge(client);code=account(client)['code'];text=f'bank_id,paid_date,account_code,amount,payer\nB1,2026-10-14,{code},123.45,Parent\nB2,2026-10-14,{code},10.01,Parent\n'
    data=dict(text=text,source='Bank 1',reason='Statement matched');p=ok(post(client,'import/preview',**data));assert not p['errors'] and p['total']=='13346'
    req=body(**data,previewHash=p['previewHash']);result=ok(client.post(path('import'),json=req));assert result['count']==2 and account(client)['summary']['received']=='13346'
    assert client.post(path('import'),json=req).json()==result
    p=ok(post(client,'import/preview',**data));assert len(p['errors'])==2
    assert post(client,'import',**data,previewHash=p['previewHash']).status_code==422
    bad=data|dict(text=text.replace('B1','B3').replace('B2','B4').replace('10.01','1e2'));p=ok(post(client,'import/preview',**bad));assert len(p['errors'])==1
    assert post(client,'import',**bad,previewHash=p['previewHash']).status_code==422 and account(client)['summary']['received']=='13346'

def test_close_period_freezes_day_and_historical_report_survives_later_refund(client,setup,monkeypatch):
    charge(client);r=receipt(client);ok(act(client,'s1','auto-allocate'));data=dict(version=get(client,'options')['config']['version'],through='2026-10-15',reason='Month closed')
    p=ok(post(client,'periods/preview',**data));closed=ok(post(client,'periods',**data,previewHash=p['previewHash']))
    frozen=client.get(path('periods/'+closed['id']+'/export')).text;report=get(client,'report?through=2026-10-15')
    assert act(client,'s1',f'receipts/{r["id"]}/refund',amount=1,reference='REF-1').status_code==409
    monkeypatch.setattr(payments,'now',lambda:datetime(2026,10,16,12,tzinfo=timezone.utc));ok(act(client,'s1',f'receipts/{r["id"]}/refund',amount=5000,reference='REF-1'))
    assert client.get(path('periods/'+closed['id']+'/export')).text==frozen
    assert get(client,'report?through=2026-10-15')==report
    assert get(client,'report')['totals']['balance']=='0'

def test_notify_only_current_financial_recipients_and_csv_escaping(client,setup):
    parent,*_=setup;payers(client,parent);p=signed('parent')
    c=ok(post(client,'accounts/s1/charges',version=account(client)['version'],amount=1000,title='=HYPERLINK("evil")',due='2026-10-01',reason='Private reason'))
    with api.connect() as db:
        rows=db.execute("SELECT user_id,action FROM notifications WHERE action LIKE 'payments:%'").fetchall()
        assert rows and {r[0] for r in rows}=={parent['id']} and {r[1] for r in rows}=={'payments:charged'}
    exported=p.get(path('accounts/s1/export'));assert exported.status_code==200 and "'=HYPERLINK" in exported.text and 'Private reason' not in exported.text
    assert p.get(path('report/export')).status_code==403
    ok(act(client,'s1','remind'));assert act(client,'s1','remind').status_code==409

def test_integer_validation_references_and_school_day_boundary(client,setup,monkeypatch):
    assert post(client,'accounts/s1/charges',version=0,amount=0.1,title='Fee',due='2026-10-01',reason='Approved').status_code==422
    monkeypatch.setattr(payments,'now',lambda:datetime(2026,10,15,20,30,tzinfo=timezone.utc));charge(client);assert account(client)['entries'][0]['book_date']=='2026-10-16'
    assert account(client)['summary']['charged']=='10000'
    r=receipt(client,amount=1000);ok(act(client,'s1',f'receipts/{r["id"]}/refund',amount=100,reference='ВОЗВРАТ'))
    assert act(client,'s1',f'receipts/{r["id"]}/refund',amount=100,reference='возврат').status_code==409

def test_manual_bank_receipt_and_csv_share_duplicate_identity(client,setup):
    charge(client);code=account(client)['code']
    ok(post(client,'receipts',paidDate='2026-10-15',reference='OP-1',source='Bank One',reason='Manual match',parts=[dict(studentId='s1',version=account(client)['version'],amount=1000)]))
    data=dict(text=f'bank_id,paid_date,account_code,amount,payer\nop-1,2026-10-15,{code},10.00,Parent\n',source='bank one',reason='Statement')
    p=ok(post(client,'import/preview',**data));assert len(p['errors'])==1 and not p['items']
    assert post(client,'import',**data,previewHash=p['previewHash']).status_code==422

def test_covered_debt_does_not_trigger_false_payment_reminder(client,setup):
    charge(client);receipt(client,amount=10000)
    assert act(client,'s1','remind').status_code==409

def test_finance_access_policy_change_and_expiry_apply_without_login(client,setup):
    parent,*_=setup;payers(client,parent);charge(client);p=signed('parent');assert account(p)['summary']['balance']=='10000'
    config(client,familyPolicy='verified');assert p.get(path('accounts/s1')).status_code==404
    config(client,familyPolicy='assigned');assert account(p)['summary']['balance']=='10000'
    with api.connect() as db:
        r=db.execute('SELECT permissions FROM accounts WHERE id=?',(parent['id'],)).fetchone();permissions=json.loads(r[0]);permissions['familyRules']=[dict(workspace='demo',studentId='s1',startDate=None,endDate='2020-01-01')]
        db.execute('UPDATE accounts SET permissions=?,version=version+1 WHERE id=?',(json.dumps(permissions),parent['id']))
    assert p.get(path('accounts/s1')).status_code in [403,404]

def test_permission_revocation_between_snapshot_and_transaction(client,setup,monkeypatch):
    _,teacher,*_=setup;config(client,managerIds=[teacher['id']]);t=signed('teacher');original=api.payments.snapshots.get
    def changed(workspace):
        snap=original(workspace)
        with api.connect() as db:db.execute('UPDATE accounts SET version=version+1 WHERE id=?',(teacher['id'],))
        return snap
    monkeypatch.setattr(api.payments.snapshots,'get',changed)
    assert t.get(path('report')).status_code==409

def test_unposted_account_not_in_foreign_space_and_no_csrf(client,setup):
    charge(client)
    assert signed('parent').get('/api/payments/accounts/s1?workspace=school').status_code in [403,404]
    assert client.post(path('receipts'),headers={'X-Atlas-Request':'0'},json=body(paidDate='2026-10-15',reference='R',reason='Synthetic',parts=[dict(studentId='s1',version=1,amount=1000)])).status_code==403

def test_complete_backup_restore_preserves_ledger_permissions_and_replay(client,setup,tmp_path,monkeypatch):
    from tools.backup_school import create_backup
    from tools.restore_school import verify_backup,restore_backup
    parent,*_=setup;payers(client,parent);charge(client);receipt(client);ok(act(client,'s1','auto-allocate'))
    p=signed('parent');before=account(p);report=get(client,'report');state=api.read('demo')
    backup=tmp_path/'backup';create_backup(api.DB,backup);verify_backup(backup)
    destination=tmp_path/'restored';restore_backup(backup,destination);monkeypatch.setattr(api,'DB',destination/api.DB.name)
    assert account(p)==before and get(client,'report')==report and api.read('demo')==state
    assert len(get(client,'accounts/s1/history')['items'])==4
    verify_backup(backup)
