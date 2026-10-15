from concurrent.futures import ThreadPoolExecutor
from datetime import date,datetime,timezone
from uuid import uuid4
import json
import pytest
from backend import api,library
from test_access import client,enable,create,signed

def body(**kw):return dict(requestId=str(uuid4()),**kw)
def path(part):return '/api/library/'+part+('&' if '?' in part else '?')+'workspace=demo'
def ok(r):
    assert r.status_code==200,r.text
    return r.json()
def get(c,part):return ok(c.get(path(part)))
def post(c,part,**kw):return c.post(path(part),json=body(**kw))
def data(**kw):return dict(title='Physics',author='Synthetic author',kind='textbook')|kw
def title(c,**kw):return ok(post(c,'titles',version=0,data=data(**kw)))
def copies(c,t,*numbers):return ok(post(c,'titles/'+t['id']+'/copies',titleVersion=t['version'],items=[dict(inventory=n) for n in numbers]))['items']
def payload(copy,student='s1',kind='student'):return dict(items=[dict(copyId=copy['id'],copyVersion=copy['version'],borrower=dict(kind=kind,id=student))],due='2026-11-01')
def checkout(c,copy,student='s1',kind='student'):
    p=payload(copy,student,kind);preview=ok(post(c,'loans/preview',**p));return ok(post(c,'loans',**p,previewHash=preview['previewHash']))['items'][0]
def act(c,loan,action,**kw):return post(c,'loans/'+loan['id']+'/action',version=loan['version'],action=action,**kw)
def assign(c,teacher,whole=False,classes=None,**kw):
    current=get(c,'options')['config'];return ok(c.put(path('config'),json=body(version=current['version'],assignments=[dict(accountId=teacher['id'],wholeSchool=whole,classIds=classes or ([] if whole else ['c-demo-a']))],reason='Assigned school duty',**kw)))
@pytest.fixture
def setup(client,monkeypatch):
    monkeypatch.setattr(library,'now',lambda:datetime(2026,10,15,12,tzinfo=timezone.utc));monkeypatch.setattr(library,'today',lambda:date(2026,10,15))
    enable(client);teacher=create(client,'teacher');parent=create(client,'parent');student=create(client,'student');t=title(client);c=copies(client,t,'BOOK-001','BOOK-002','BOOK-003')
    return teacher,parent,student,t,c

def test_catalog_private_loans_immutable_record_and_current_family(client,setup):
    teacher,parent,student,t,c=setup;state=api.read('demo');loan=checkout(client,c[0]);p=signed('parent');pupil=signed('student')
    assert get(p,'titles')['items'][0]['available']==2
    assert get(p,'loans')['items'][0]['id']==loan['id']==get(pupil,'loans')['items'][0]['id']
    assert not get(p,'loans/'+loan['id'])['canManage']
    assert p.get(path('titles/'+t['id']+'/copies')).status_code==403
    assert act(p,loan,'return').status_code==404
    assert p.get(path('history/loan/'+loan['id'])).status_code==404
    assert get(p,'options')['students']==[] and get(p,'options')['employees']==[]
    stranger=create(client,'parent',login='stranger',grants=[dict(workspace='demo',view='student',entityIds=['s3'])]);other=signed('stranger')
    assert not get(other,'loans')['items'] and other.get(path('loans/'+loan['id'])).status_code==404
    ok(client.put(path('titles/'+t['id']),json=body(version=1,data=data(title='Changed title'),reason='Metadata fix')))
    assert get(p,'loans/'+loan['id'])['title']['title']=='Physics'
    assert api.read('demo')==state
    with api.connect() as db:db.execute('UPDATE accounts SET permissions=?,version=version+1 WHERE id=?',(json.dumps(dict(workspaces=['demo'],grants=[])),parent['id']))
    assert p.get(path('loans/'+loan['id'])).status_code in [403,404]
    assert p.get(path('export')).status_code==403

def test_per_class_delegation_and_global_catalog_authority(client,setup):
    teacher,_,_,t,c=setup;staff=signed('teacher')
    assert not get(staff,'options')['canIssue']
    assert post(staff,'loans/preview',**payload(c[0])).status_code==403
    assign(client,teacher);opts=get(staff,'options');assert opts['canIssue'] and not opts['canCatalog'] and {s['id'] for s in opts['students']}=={'s1','s2'}
    assert post(staff,'titles',version=0,data=data()).status_code==403
    assert post(staff,'loans/preview',**payload(c[1],'s3')).status_code==403
    own=checkout(staff,c[0]);other=checkout(client,c[1],'s3')
    assert [x['id'] for x in get(staff,'loans')['items']]==[own['id']]
    assert staff.get(path('loans/'+other['id'])).status_code==404
    assert act(staff,own,'cancel',reason='Mistake').status_code==403
    assign(client,teacher,whole=True);assert get(staff,'options')['canCatalog'];assert title(staff)['data']['title']=='Physics'
    # Moving the child removes old class-scoped management; an administrator can reconcile.
    assign(client,teacher);state=api.read('demo');state.students[0].classId='c-demo-d';api.save('demo',state)
    assert act(staff,own,'return').status_code==404
    assert own['inventory'] not in staff.get(path('export')).text
    ok(act(client,own,'return'))

def test_double_checkout_and_atomic_bulk_failure(client,setup):
    *_,t,c=setup;p=payload(c[0]);preview=ok(post(client,'loans/preview',**p));a=signed('admin');b=signed('admin')
    with ThreadPoolExecutor(2) as pool:result=list(pool.map(lambda session:post(session,'loans',**p,previewHash=preview['previewHash']),[a,b]))
    assert sorted(r.status_code for r in result)==[200,409]
    cart=dict(items=[payload(x)['items'][0] for x in c[1:]],due='2026-11-01');preview=ok(post(client,'loans/preview',**cart));checkout(client,c[2],'s3')
    assert post(client,'loans',**cart,previewHash=preview['previewHash']).status_code==409
    current=get(client,'titles/'+t['id']+'/copies')['items'];assert not next(x for x in current if x['id']==c[1]['id'])['onLoan']

def test_bulk_issue_rollback_notifications_and_idempotence(client,setup,monkeypatch):
    *_,t,c=setup;cart=dict(items=[payload(x)['items'][0] for x in c[:2]],due='2026-11-01');p=ok(post(client,'loans/preview',**cart));saved=api.notifications.deliver
    def fail(*args,**kw):raise RuntimeError('synthetic failure')
    monkeypatch.setattr(api.notifications,'deliver',fail)
    with pytest.raises(RuntimeError):post(client,'loans',**cart,previewHash=p['previewHash'])
    assert not get(client,'loans')['items'] and get(client,'titles')['items'][0]['available']==3
    monkeypatch.setattr(api.notifications,'deliver',saved);request=body(**cart,previewHash=p['previewHash']);first=ok(client.post(path('loans'),json=request));assert client.post(path('loans'),json=request).json()==first
    assert len(get(client,'loans')['items'])==2
    assert client.post(path('loans'),json=request|dict(due='2026-11-02')).status_code==409

def test_return_repair_loss_found_and_writeoff(client,setup):
    *_,t,c=setup;loan=checkout(client,c[0]);returned=ok(act(client,loan,'return',condition='damaged'))
    assert returned['status']=='returned' and returned['conditionIn']=='damaged'
    copy=next(x for x in get(client,'titles/'+t['id']+'/copies')['items'] if x['id']==c[0]['id']);assert copy['status']=='repair'
    assert post(client,'loans/preview',**payload(copy)).status_code==409
    lost=ok(act(client,checkout(client,c[1]),'lost',reason='Reported lost'));assert lost['status']=='lost'
    assert len(get(client,'loans?overdue=true')['items'])==1
    assert ok(act(client,lost,'found',condition='worn'))['status']=='returned'
    lost=ok(act(client,checkout(client,c[2]),'lost',reason='Lost'));assert ok(act(client,lost,'writeoff',reason='School decision'))['status']=='resolved'
    current=get(client,'titles/'+t['id']+'/copies')['items'];assert next(x for x in current if x['id']==c[2]['id'])['status']=='withdrawn'
    assert {'issued','return','copy_created'}<={h['action'] for h in get(client,'history/copy/'+c[0]['id'])['items']}

def test_renewal_limits_due_validation_reminders_and_stale_versions(client,setup,monkeypatch):
    teacher,*_,c=setup;assign(client,teacher,whole=True,maxRenewals=1,maxLoans=2)
    loan=checkout(client,c[0]);assert act(client,loan,'renew',due='2026-10-31',reason='Need longer').status_code==422
    renewed=ok(act(client,loan,'renew',due='2026-12-01',reason='Need longer'));assert renewed['renewals']==1
    assert act(client,loan,'return').status_code==409
    assert act(client,renewed,'renew',due='2026-12-02',reason='Again').status_code==409
    reminded=ok(act(client,renewed,'remind'));assert act(client,reminded,'remind').status_code==409
    monkeypatch.setattr(library,'now',lambda:datetime(2026,12,2,12,tzinfo=timezone.utc));monkeypatch.setattr(library,'today',lambda:date(2026,12,2));assert get(client,'loans?overdue=true')['items'][0]['overdue']

def test_batch_capacity_checks_every_item_before_writing(client,setup):
    teacher,*_,c=setup;assign(client,teacher,maxLoans=1)
    p=dict(items=[payload(x)['items'][0] for x in c[:2]],due='2026-11-01')
    assert post(client,'loans/preview',**p).status_code==409 and not get(client,'loans')['items']
    p=payload(c[0]);p['due']='2026-10-14';assert post(client,'loans/preview',**p).status_code==422

def test_preview_rechecks_names_permissions_title_and_copy(client,setup):
    teacher,*_,t,c=setup;assign(client,teacher);staff=signed('teacher');p=payload(c[0]);preview=ok(post(staff,'loans/preview',**p))
    state=api.read('demo');state.students[0].name='New current name';api.save('demo',state)
    assert post(staff,'loans',**p,previewHash=preview['previewHash']).status_code==409
    preview=ok(post(staff,'loans/preview',**p));assign(client,teacher,classes=['c-demo-d'])
    assert post(staff,'loans',**p,previewHash=preview['previewHash']).status_code==403
    preview=ok(post(client,'loans/preview',**p));ok(client.put(path('titles/'+t['id']),json=body(version=1,data=data(title='Revised'),reason='Correct metadata')))
    assert post(client,'loans',**p,previewHash=preview['previewHash']).status_code==409

def test_inventory_detects_movements_and_preserves_closed_report(client,setup):
    *_,t,c=setup;state=api.read('demo');audit=ok(post(client,'audits',title='October inventory'));assert audit['counts']=={'pending':3}
    assert post(client,'audits',title='Second').status_code==409
    assert post(client,'audits/'+audit['id']+'/action',version=1,action='close',reason='Done').status_code==409
    loan=checkout(client,c[0]);rows=get(client,'audits/'+audit['id']);assert rows['audit']['changed']==1
    first=next(x for x in rows['items'] if x['copyId']==c[0]['id'])
    assert post(client,f'audits/{audit["id"]}/copies/{first["copyId"]}',version=1,result='seen').status_code==409
    ok(post(client,f'audits/{audit["id"]}/copies/{first["copyId"]}',version=1,result='refresh',note='Loan checked'))
    for i in rows['items']:
        if i['copyId']!=first['copyId']:ok(post(client,f'audits/{audit["id"]}/copies/{i["copyId"]}',version=1,result='seen'))
    current=get(client,'audits/'+audit['id']);assert current['audit']['counts']=={'loaned':1,'seen':2}
    closed=ok(post(client,'audits/'+audit['id']+'/action',version=current['audit']['version'],action='close',reason='Verified'));assert closed['status']=='closed'
    report=get(client,'audits/'+audit['id']);ok(act(client,loan,'return'));assert get(client,'audits/'+audit['id'])==report
    assert api.read('demo')==state

def test_inventory_missing_never_changes_copy_or_loan(client,setup):
    teacher,*_,t,c=setup;a=ok(post(client,'audits',title='Missing report'));r=get(client,'audits/'+a['id'])['items'][0]
    assert post(client,f'audits/{a["id"]}/copies/{r["copyId"]}',version=1,result='missing').status_code==422
    ok(post(client,f'audits/{a["id"]}/copies/{r["copyId"]}',version=1,result='missing',note='Shelf checked'))
    assert get(client,'titles')['items'][0]['available']==3
    assert signed('parent').get(path('audits')).status_code==403
    assign(client,teacher);assert signed('teacher').get(path('audits')).status_code==403

def test_duplicate_inventory_numbers_atomic_creation_and_archive(client,setup):
    *_,t,c=setup
    assert post(client,'titles/'+t['id']+'/copies',titleVersion=1,items=[dict(inventory='NEW'),dict(inventory='book-001')]).status_code==409
    assert len(get(client,'titles/'+t['id']+'/copies')['items'])==3
    assert post(client,'titles/'+t['id']+'/copies',titleVersion=1,items=[dict(inventory='x'),dict(inventory='X')]).status_code==422
    ok(client.put(path('titles/'+t['id']),json=body(version=1,data=data(),archived=True,reason='Old edition')))
    assert not get(signed('parent'),'titles')['items']
    assert post(client,'loans/preview',**payload(c[0])).status_code==409
    assert len(get(client,'titles?archived=true')['items'])==1

def test_employee_borrowing_does_not_grant_management(client,setup):
    teacher,*_,c=setup;loan=checkout(client,c[0],teacher['id'],'employee');staff=signed('teacher')
    assert get(staff,'loans')['items'][0]['id']==loan['id'] and not get(staff,'loans/'+loan['id'])['canManage']
    assert act(staff,loan,'return').status_code==404
    assert not get(signed('parent'),'loans')['items']

@pytest.mark.parametrize('role',['parent','student','teacher'])
def test_deactivated_reader_or_librarian_loses_access(client,setup,role):
    teacher,parent,student,_,c=setup;assign(client,teacher);loan=checkout(client,c[0]);person=dict(parent=parent,student=student,teacher=teacher)[role];session=signed(role)
    assert session.get(path('loans/'+loan['id'])).status_code==200
    with api.connect() as db:db.execute('UPDATE accounts SET active=0,version=version+1 WHERE id=?',(person['id'],))
    for route in ['options','loans','loans/'+loan['id'],'export','history/loan/'+loan['id']]:assert session.get(path(route)).status_code in [401,403,404]

def test_permission_key_and_expired_family_rule(client,setup):
    teacher,parent,*_,c=setup;assign(client,teacher);staff=signed('teacher');checkout(client,c[0])
    with api.connect() as db:db.execute("UPDATE accounts SET role='staff',version=version+1 WHERE id=?",(teacher['id'],))
    assert not get(staff,'options')['canIssue']
    with api.connect() as db:
        row=db.execute('SELECT permissions FROM accounts WHERE id=?',(parent['id'],)).fetchone();doc=json.loads(row[0]);doc['familyRules']=[dict(workspace='demo',studentId='s1',startDate='2020-01-01',endDate='2020-12-31')];db.execute('UPDATE accounts SET permissions=?,version=version+1 WHERE id=?',(json.dumps(doc),parent['id']))
    assert signed('parent').get(path('export')).status_code==403

def test_csv_formula_escape_pagination_and_scoped_history(client,setup):
    teacher,*_,t,c=setup;new=title(client,title='=HYPERLINK("bad")');cp=copies(client,new,'=1+2')[0];a=checkout(client,cp);b=checkout(client,c[0],'s3');p=signed('parent')
    csv=p.get(path('export'));assert csv.status_code==200 and csv.content.startswith(b'\xef\xbb\xbf') and "'=1+2" in csv.text and 'Physics' not in csv.text
    first=get(client,'loans?limit=1');second=get(client,'loans?limit=1&before='+str(first['nextBefore']));assert first['items'][0]['id']!=second['items'][0]['id']
    first=get(client,'titles?limit=1');assert get(client,'titles?limit=1&before='+str(first['nextBefore']))['items']
    assign(client,teacher);staff=signed('teacher');assert staff.get(path('history/loan/'+b['id'])).status_code==404
    assert get(staff,'history/loan/'+a['id'])['items']

def test_canonical_authority_and_workspace_race(client,setup,monkeypatch):
    teacher,*_,t,c=setup;assign(client,teacher);staff=signed('teacher');original=api.library.snapshots.get
    def changed(workspace):
        snap=original(workspace)
        with api.connect() as db:db.execute("UPDATE accounts SET role='viewer',version=version+1 WHERE id=?",(teacher['id'],))
        return snap
    monkeypatch.setattr(api.library.snapshots,'get',changed)
    assert post(staff,'loans/preview',**payload(c[0])).status_code==409
    assert not get(client,'loans')['items']

def test_replay_after_assignment_revocation_does_not_reveal_loan(client,setup):
    teacher,*_,c=setup;assign(client,teacher);staff=signed('teacher');p=payload(c[0]);preview=ok(post(staff,'loans/preview',**p));request=body(**p,previewHash=preview['previewHash']);assert staff.post(path('loans'),json=request).status_code==200
    config=get(client,'options')['config'];ok(client.put(path('config'),json=body(version=config['version'],assignments=[],reason='Assignment ended')))
    assert staff.post(path('loans'),json=request).status_code==403

def test_workspace_isolation_and_inactive_child(client,setup):
    *_,c=setup;loan=checkout(client,c[0]);state=api.read('demo')
    with api.connect() as db:db.execute('INSERT INTO workspaces VALUES (?,?,?)',('school',state.revision,state.model_dump_json()))
    assert client.get('/api/library/loans/'+loan['id']+'?workspace=school').status_code==404
    assert not client.get('/api/library/loans?workspace=school').json()['items']
    state.students[0].active=False;api.save('demo',state);p=signed('parent')
    assert p.get(path('loans/'+loan['id'])).status_code==403
    assert p.get(path('export')).status_code==403

def test_copy_search_is_bounded_and_role_checked(client,setup):
    teacher,*_,t,c=setup;assign(client,teacher);staff=signed('teacher')
    items=get(staff,'titles/'+t['id']+'/copies?q=book-002')['items'];assert len(items)==1 and items[0]['id']==c[1]['id']
    assert not get(staff,'titles/'+t['id']+'/copies?q=%25')['items']
    assert signed('parent').get(path('titles/'+t['id']+'/copies?q=BOOK')).status_code==403
