from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from backend import api, canteen, payments
from test_access import client, enable, create, signed


def path(part): return '/api/canteen/' + part + ('&' if '?' in part else '?') + 'workspace=demo'
def command(**kw): return dict(requestId=str(uuid4()), **kw)
def ok(response):
    assert response.status_code == 200, response.text
    return response.json()
def get(c, part): return ok(c.get(path(part)))
def post(c, part, **kw): return c.post(path(part), json=command(**kw))
def settings(c, **kw):
    cfg=get(c, 'options')['config'];cfg.update(kw)
    return ok(c.put(path('config'), json=command(**cfg, reason='School canteen decision')))
def definition(price=0, capacity=None, **kw):
    data=dict(day='2026-10-16', meal='lunch', title='Autumn lunch', starts='12:00', ends='14:00',
              opensAt='2026-10-01T08:00:00+05:00', orderClosesAt='2026-10-16T09:00:00+05:00', cancelClosesAt='2026-10-16T09:30:00+05:00',
              variants=[dict(id='standard', title='Lunch A', dishes='Soup, rice, vegetables', allergens=['Milk'], price=price, capacity=capacity)])
    data.update(kw);return data
def menu(c, publish=True, **kw):
    result=ok(post(c, 'menus', definition=definition(**kw)))
    if publish: result=ok(post(c, 'menus/'+result['id']+'/transition', version=1, action='publish'))
    return get(c, 'menus/'+result['id'])
def reservation(m, student='s1', variant='standard', **kw):
    result=dict(menuId=m['id'], menuVersion=m['version'], studentId=student, variantId=variant, expectedPrice=next(x['price'] for x in m['definition']['variants'] if x['id']==variant));result.update(kw);return result
def book(c, m, student='s1', **kw): return ok(post(c, 'orders', reservation=reservation(m,student), **kw))
def action(c, id, name, **kw):
    row=get(c, 'orders/'+id)
    return post(c, 'orders/'+id+'/action', version=row['version'], action=name, **kw)
def finance(c, part): return ok(c.get('/api/payments/'+part+'?workspace=demo'))
def topup(c, parent, student='s1', amount=50000):
    a=finance(c, 'accounts/'+student)
    ok(c.put('/api/payments/accounts/'+student+'/payers?workspace=demo', json=command(version=a['version'], parentIds=[parent['id']], reason='Verified payer')))
    a=finance(c, 'accounts/'+student)
    ok(c.post('/api/payments/receipts?workspace=demo', json=command(paidDate='2026-10-15', reference=uuid4().hex, reason='Verified receipt', parts=[dict(studentId=student, version=a['version'], amount=amount)])))


@pytest.fixture
def setup(client, monkeypatch):
    clock=[datetime(2026,10,15,3,tzinfo=timezone.utc)]
    monkeypatch.setattr(canteen, 'now', lambda:clock[0]);monkeypatch.setattr(payments, 'now', lambda:clock[0])
    enable(client)
    people={role:create(client, role) for role in ['parent','student','teacher','staff']}
    people['other']=create(client,'parent',login='other',grants=[dict(workspace='demo',view='student',entityIds=['s3'])])
    return people,clock


def test_explicit_roles_drafts_and_family_privacy(client,setup):
    people,_=setup;state=api.read('demo');m=menu(client,publish=False);p=signed('parent');teacher=signed('teacher')
    assert not get(p,'menus')['items'];assert p.get(path('menus/'+m['id'])).status_code==404
    assert teacher.post(path('menus'),json=command(definition=definition())).status_code==403
    assert p.get(path('report')).status_code==403
    ok(post(client,'menus/'+m['id']+'/transition',version=1,action='publish'));m=get(client,'menus/'+m['id'])
    order=book(p,m);book(client,m,'s3')
    assert len(get(p,'orders')['items'])==1 and len(get(signed('other'),'orders')['items'])==1
    assert signed('other').get(path('orders/'+order['id'])).status_code==404
    assert p.get(path('history')).status_code==403 and p.get(path('orders/export')).status_code==403
    assert 'chargeId' not in get(p,'orders/'+order['id'])
    assert p.post(path('orders'),json=command(reservation=reservation(m,'s3'))).status_code==404
    assert api.read('demo')==state


def test_capacity_race_and_duplicate_guardian_order(client,setup):
    people,_=setup;m=menu(client,capacity=1)
    clients=[signed('parent'),signed('other')]
    with ThreadPoolExecutor(2) as pool:
        responses=list(pool.map(lambda pair:post(pair[0],'orders',reservation=reservation(m,pair[1])),zip(clients,['s1','s3'])))
    assert sorted(r.status_code for r in responses)==[200,409]
    assert len(get(client,'orders')['items'])==1
    ok(action(client,get(client,'orders')['items'][0]['id'],'cancel'))
    first=book(signed('parent'),m)
    assert post(client,'orders',reservation=reservation(m)).status_code==409
    assert get(client,'menus/'+m['id'])['reserved']=={'standard':1}
    assert first['version']==1


def test_replay_lost_response_and_revoked_delegate(client,setup):
    people,_=setup;settings(client,delegates=[dict(userId=people['staff']['id'],role='manager')]);staff=signed('staff');m=menu(client)
    cmd=command(reservation=reservation(m));one=ok(staff.post(path('orders'),json=cmd));assert ok(staff.post(path('orders'),json=cmd))==one
    cmd['reason']='different';assert staff.post(path('orders'),json=cmd).status_code==409
    settings(client,delegates=[]);cmd['reason']='';assert staff.post(path('orders'),json=cmd).status_code==404
    assert len(get(client,'orders')['items'])==1


def test_student_ordering_and_teacher_cannot_self_grant(client,setup):
    people,_=setup;m=menu(client);pupil=signed('student');teacher=signed('teacher')
    assert post(pupil,'orders',reservation=reservation(m)).status_code==403
    assert teacher.put(path('config'),json=command(version=0,reason='No',delegates=[])).status_code==403
    settings(client,studentOrdering=True);order=book(pupil,m)
    ok(action(signed('parent'),order['id'],'cancel'))
    assert get(pupil,'orders/'+order['id'])['status']=='cancelled'


def test_paid_order_atomic_ledger_and_cancellation_credit(client,setup):
    people,_=setup;settings(client,financeEnabled=True);m=menu(client,price=12500);p=signed('parent')
    assert post(p,'orders',reservation=reservation(m)).status_code==403
    topup(client,people['parent'],amount=20000);before=finance(client,'accounts/s1')
    order=book(p,m);account=finance(client,'accounts/s1')
    assert account['summary']['balance']=='-7500' and account['summary']['outstanding']=='0'
    assert account['summary']['unallocated']=='7500';assert len(account['allocations'])==1
    ok(action(p,order['id'],'cancel'));after=finance(client,'accounts/s1')
    assert after['summary']['balance']==before['summary']['balance']=='-20000'
    assert after['summary']['charged']=='0' and after['summary']['received']=='20000'
    assert not after['allocations'] and len(after['entries'])==3
    assert book(p,m)['id']!=order['id']


def test_insufficient_credit_no_order_or_partial_charge_and_opt_in_debt(client,setup):
    people,_=setup;settings(client,financeEnabled=True);m=menu(client,price=10000);topup(client,people['parent'],amount=5000)
    assert post(signed('parent'),'orders',reservation=reservation(m)).status_code==409
    assert not get(client,'orders')['items'];assert finance(client,'accounts/s1')['summary']['charged']=='0'
    settings(client,financeEnabled=True,allowDebt=True);book(signed('parent'),m)
    assert finance(client,'accounts/s1')['summary']['balance']=='5000'


def test_batch_preview_rolls_back_and_capacity_failure_is_whole_batch(client,setup):
    m=menu(client,capacity=2);data=dict(action='reserve',reservations=[reservation(m,'s1'),reservation(m,'s2')])
    preview=ok(post(client,'batch/preview',**data))
    assert preview['count']==2 and not get(client,'orders')['items'] and not get(client,'menus/'+m['id'])['reserved']
    with api.connect() as db: assert db.execute("SELECT count(*) FROM canteen_history WHERE action='ordered'").fetchone()[0]==0
    book(client,m,'s3');assert post(client,'batch',**data,previewHash=preview['previewHash']).status_code==409
    assert len(get(client,'orders')['items'])==1
    single=dict(action='reserve',reservations=[reservation(m,'s1')]);p=ok(post(client,'batch/preview',**single))
    cmd=command(**single,previewHash=p['previewHash']);result=ok(client.post(path('batch'),json=cmd));assert result['count']==1
    assert ok(client.post(path('batch'),json=cmd))==result


def test_paid_batch_previews_no_money_and_stale_finance_cannot_commit(client,setup):
    people,_=setup;settings(client,financeEnabled=True);m=menu(client,price=10000);topup(client,people['parent'])
    data=dict(action='reserve',reservations=[reservation(m)])
    before=finance(client,'accounts/s1');p=ok(post(client,'batch/preview',**data))
    assert finance(client,'accounts/s1')==before
    a=finance(client,'accounts/s1')
    ok(client.post('/api/payments/accounts/s1/charges?workspace=demo',json=command(version=a['version'],title='Other fee',amount=100,due='2026-10-16',reason='Other approved fee')))
    assert post(client,'batch',**data,previewHash=p['previewHash']).status_code==409
    assert not get(client,'orders')['items']


def test_cutoff_boundaries_late_override_and_service_window(client,setup):
    _,clock=setup;m=menu(client);p=signed('parent');order=book(p,m)
    clock[0]=datetime(2026,10,16,4,tzinfo=timezone.utc) # 09:00 exact
    assert post(client,'orders',reservation=reservation(m,'s2')).status_code==409
    assert post(client,'orders',reservation=reservation(m,'s2'),overrideCutoff=True).status_code==422
    ok(post(client,'orders',reservation=reservation(m,'s2'),overrideCutoff=True,reason='School approved late order'))
    clock[0]=datetime(2026,10,16,4,30,tzinfo=timezone.utc)
    assert action(p,order['id'],'cancel').status_code==409
    assert action(client,order['id'],'serve',overrideCutoff=True,reason='Attempt before meal').status_code==409
    clock[0]=datetime(2026,10,16,7,tzinfo=timezone.utc);ok(action(client,order['id'],'serve'))
    assert action(client,order['id'],'serve').status_code==409
    ok(action(client,order['id'],'undo-service',reason='Wrong row selected'))
    clock[0]=datetime(2026,10,16,9,tzinfo=timezone.utc)
    assert action(client,order['id'],'serve').status_code==409
    ok(action(client,order['id'],'missed'));assert action(p,order['id'],'undo-service',reason='No').status_code==403
    ok(action(client,order['id'],'undo-service',reason='School correction'))
    ok(action(client,order['id'],'cancel',overrideCutoff=True,reason='School approved exception'))


def test_issuer_scope_kitchen_aggregates_and_no_financial_access(client,setup):
    people,clock=setup;snap=api.read('demo');own=snap.students[1].classId
    # Select a pupil from another class without relying on demo numbering.
    other=next(p.id for p in snap.students if p.active and p.classId!=own)
    settings(client,delegates=[dict(userId=people['teacher']['id'],role='issuer',classIds=[own]),dict(userId=people['staff']['id'],role='kitchen')])
    m=menu(client);own_id=next(p.id for p in snap.students if p.active and p.classId==own)
    first=book(client,m,own_id);second=book(client,m,other);t=signed('teacher');k=signed('staff')
    assert len(get(t,'orders')['items'])==1
    assert t.get(path('orders/'+second['id'])).status_code==404
    assert k.get(path('orders')).status_code==403 and k.get(path('orders/'+first['id'])).status_code==404
    report=get(k,'report');assert report['items'][0]['portions']==2 and 'studentName' not in json.dumps(report)
    assert t.get('/api/payments/accounts/'+own_id+'?workspace=demo').status_code==404
    assert post(t,'orders',reservation=reservation(m,'s2')).status_code==404
    clock[0]=datetime(2026,10,16,7,tzinfo=timezone.utc);ok(action(t,first['id'],'serve'))
    assert get(t,'report')['items'][0]['portions']==1


def test_published_menu_immutable_copy_and_close_retains_orders(client,setup):
    m=menu(client);order=book(signed('parent'),m)
    assert client.put(path('menus/'+m['id']),json=command(version=m['version'],definition=definition(price=1))).status_code==409
    ok(post(client,'menus/'+m['id']+'/transition',version=m['version'],action='close'))
    assert get(client,'orders/'+order['id'])['status']=='ordered'
    assert post(client,'orders',reservation=reservation(get(client,'menus/'+m['id']),'s2')).status_code==409
    assert post(client,'menus/'+m['id']+'/transition',version=m['version']+1,action='cancel',reason='Cancelled lunch').status_code==409
    copy=ok(post(client,'menus/'+m['id']+'/copy',version=m['version']+1,day='2026-10-17'))
    copied=get(client,'menus/'+copy['id']);assert copied['status']=='draft' and copied['definition']['orderClosesAt'].startswith('2026-10-17')
    ok(action(signed('parent'),order['id'],'cancel'))
    ok(post(client,'menus/'+m['id']+'/transition',version=m['version']+1,action='cancel',reason='Cancelled lunch'))


def test_expired_family_link_and_changed_account_block_old_replays(client,setup):
    people,_=setup;m=menu(client);p=signed('parent');cmd=command(reservation=reservation(m));ok(p.post(path('orders'),json=cmd))
    with api.connect() as db:
        db.execute('UPDATE accounts SET permissions=?,version=version+1 WHERE id=?',(json.dumps(dict(workspaces=['demo'],grants=[])),people['parent']['id']))
    assert p.post(path('orders'),json=cmd).status_code==403
    assert p.get(path('orders')).status_code==403


def test_notification_failure_rolls_back_order_and_money(client,setup,monkeypatch):
    people,_=setup;settings(client,financeEnabled=True);m=menu(client,price=10000);topup(client,people['parent']);before=finance(client,'accounts/s1')
    def fail(*args,**kwargs): raise RuntimeError('Synthetic notification transaction failure')
    monkeypatch.setattr(api.canteen,'notify',fail)
    with pytest.raises(RuntimeError): post(client,'orders',reservation=reservation(m))
    assert not get(client,'orders')['items'] and finance(client,'accounts/s1')==before


def test_closed_finance_day_preserves_order_and_money_on_failed_cancel(client,setup):
    people,_=setup;settings(client,financeEnabled=True);m=menu(client,price=10000);topup(client,people['parent']);order=book(signed('parent'),m)
    cfg=finance(client,'options')['config'];data=dict(version=cfg['version'],through='2026-10-15',reason='Financial day checked')
    preview=ok(client.post('/api/payments/periods/preview?workspace=demo',json=command(**data)))
    ok(client.post('/api/payments/periods?workspace=demo',json=command(**data,previewHash=preview['previewHash'])))
    before=finance(client,'accounts/s1');assert action(signed('parent'),order['id'],'cancel').status_code==409
    assert get(client,'orders/'+order['id'])['status']=='ordered' and finance(client,'accounts/s1')==before


def test_scope_changed_between_preview_and_commit_and_csv_safety(client,setup):
    m=menu(client);data=dict(action='reserve',reservations=[reservation(m)])
    p=ok(post(client,'batch/preview',**data));state=api.read('demo');pupil=next(x for x in state.students if x.id=='s1');pupil.name='=HYPERLINK("unsafe")';api.save('demo',state)
    assert post(client,'batch',**data,previewHash=p['previewHash']).status_code==409
    book(client,m);r=client.get(path('orders/export'));assert r.status_code==200 and "'=HYPERLINK" in r.text and r.headers['cache-control']=='no-store'


def test_unicode_name_search(client,setup):
    state=api.read('demo');state.students[0].name='Қаныш Әлиев';api.save('demo',state)
    book(client,menu(client))
    assert len(get(client,'orders?q=қаныш әЛИЕВ')['items'])==1
    assert not get(client,'orders?q=Unknown')['items']


def test_csrf_strict_money_and_local_mode_requires_auth(client,setup):
    m=menu(client)
    assert client.post(path('orders'),headers={'X-Atlas-Request':'0'},json=command(reservation=reservation(m))).status_code==403
    assert post(client,'orders',reservation=reservation(m,expectedPrice=0.1)).status_code==422
    bare=TestClient(api.app);assert bare.get(path('options')).status_code==401


def test_backup_roundtrip_preserves_menu_order_ledger_and_audit(client,setup,tmp_path):
    from tools.backup_school import create_backup
    from tools.restore_school import verify_backup,restore_backup
    import sqlite3
    people,_=setup;settings(client,financeEnabled=True);m=menu(client,price=10000);topup(client,people['parent']);order=book(signed('parent'),m);ok(action(signed('parent'),order['id'],'cancel'))
    directory=tmp_path/'backup';target=tmp_path/'restored';create_backup(api.DB,directory);verify_backup(directory);restore_backup(directory,target)
    with api.connect() as db, sqlite3.connect(target/api.DB.name) as restored:
        names=[r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND (name LIKE 'canteen_%' OR name LIKE 'finance_%' OR name='workspaces')")]
        for name in names:
            assert sorted([tuple(x) for x in db.execute('SELECT * FROM '+name)],key=repr)==sorted(restored.execute('SELECT * FROM '+name).fetchall(),key=repr)


def test_paid_student_denied_and_revoked_payer_cannot_create_new_charge(client,setup):
    people,_=setup;settings(client,financeEnabled=True,studentOrdering=True);m=menu(client,price=10000);topup(client,people['parent'])
    assert post(signed('student'),'orders',reservation=reservation(m)).status_code==403
    p=signed('parent');cmd=command(reservation=reservation(m));first=ok(p.post(path('orders'),json=cmd))
    a=finance(client,'accounts/s1')
    ok(client.put('/api/payments/accounts/s1/payers?workspace=demo',json=command(version=a['version'],parentIds=[],reason='Payer changed')))
    before=finance(client,'accounts/s1')
    # Replay returns only the original receipt of the operation, without a new charge.
    assert ok(p.post(path('orders'),json=cmd))==first
    assert finance(client,'accounts/s1')==before
    ok(action(p,first['id'],'cancel'))
    assert post(p,'orders',reservation=reservation(m)).status_code==403


def test_issuer_class_scope_revocation_between_preview_and_commit(client,setup):
    people,clock=setup;m=menu(client);o=book(client,m);snap=api.read('demo');own=snap.students[0].classId
    other=next(p.classId for p in snap.students if p.classId!=own)
    settings(client,delegates=[dict(userId=people['teacher']['id'],role='issuer',classIds=[own])])
    clock[0]=datetime(2026,10,16,7,tzinfo=timezone.utc);t=signed('teacher');body=dict(action='serve',orders=[dict(id=o['id'],version=1)])
    preview=ok(post(t,'batch/preview',**body))
    settings(client,delegates=[dict(userId=people['teacher']['id'],role='issuer',classIds=[other])])
    assert post(t,'batch',**body,previewHash=preview['previewHash']).status_code==404
    assert get(client,'orders/'+o['id'])['status']=='ordered'
