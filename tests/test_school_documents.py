from concurrent.futures import ThreadPoolExecutor
from datetime import date,datetime,timezone
from uuid import uuid4
import json
import pytest
from backend import api,school_documents
from test_access import client,enable,create,signed

def body(**kw):return dict(requestId=str(uuid4()),**kw)
def path(part):return '/api/documents/'+part+('&' if '?' in part else '?')+'workspace=demo'
def ok(r):
    assert r.status_code==200,r.text
    return r.json()
def definition(**kw):return dict(title='School document',kind='student',series='EDU',text='{{schoolName}}\n{{personName}} {{className}}\n{{purpose}}\n{{number}} {{date}}',fields=[dict(key='purpose',label='Purpose',required=True)])|kw
@pytest.fixture
def setup(client,monkeypatch):
    monkeypatch.setattr(school_documents,'now',lambda:datetime(2026,10,15,9,tzinfo=timezone.utc))
    monkeypatch.setattr(school_documents,'today',lambda:date(2026,10,15))
    enable(client);teacher=create(client,'teacher');parent=create(client,'parent');student=create(client,'student')
    template=ok(client.post(path('templates'),json=body(version=0,definition=definition(),active=True)))
    return template,teacher,parent,student
def new(client,t,**kw):return ok(client.post(path('items'),json=body(templateId=t['id'],templateVersion=t['version'],subjectId='s1',**kw)))
def detail(client,item):return ok(client.get(path('items/'+item['id'])))
def save(client,item,**values):return ok(client.put(path('items/'+item['id']),json=body(version=item['version'],values=values or dict(purpose='Test purpose'))))
def preview(client,item,recipients=False):return ok(client.get(path('items/'+item['id']+'/preview'+('?recipients=true' if recipients else ''))))
def action(client,item,a,**kw):return client.post(path('items/'+item['id']+'/action'),json=body(version=item['version'],action=a,**kw))
def issue(client,item):return action(client,item,'issue',previewHash=preview(client,item)['previewHash'])
def listing(client,**kw):return ok(client.get(path('items'),params=dict(workspace='demo',**kw)))

def test_private_draft_immutable_issue_print_and_revocation(client,setup):
    t,teacher,parent,student=setup;state=api.read('demo');item=save(client,new(client,t));p=signed('parent');pupil=signed('student')
    assert p.get(path('items/'+item['id'])).status_code==404
    issued=ok(issue(client,item));doc=detail(p,item)
    assert doc['number']=='EDU/2026/000001' and 'values' not in doc and 'recipients' not in doc
    assert detail(pupil,item)['snapshot']==doc['snapshot']
    assert client.put(path('items/'+item['id']),json=body(version=issued['version'],values=dict(purpose='Changed'))).status_code==409
    assert api.read('demo')==state
    state.students[0].name='Changed school name of pupil';api.save('demo',state);state=api.read('demo')
    assert detail(p,item)['snapshot']==doc['snapshot']
    printed=p.get(path('items/'+item['id']+'/print'));assert printed.status_code==200 and 'no-store' in printed.headers['cache-control']
    assert 'sandbox' in printed.headers['content-security-policy']
    revoked=ok(action(client,issued,'revoke',reason='Wrong information'))
    assert 'ДОКУМЕНТ ОТОЗВАН' in p.get(path('items/'+item['id']+'/print')).text
    assert detail(p,item)['revocationReason']=='Wrong information'
    ok(action(client,revoked,'archive',reason='Archived'));assert not listing(p)['items'] and len(listing(p,archived=True)['items'])==1
    assert api.read('demo')==state

def test_assignment_preparation_current_permission_and_history(client,setup):
    t,teacher,_,_=setup;item=new(client,t,assigneeId=teacher['id']);staff=signed('teacher')
    assert detail(staff,item)['canPrepare']
    edited=save(staff,item);submitted=ok(action(staff,edited,'submit'))
    assert staff.get(path('items/'+item['id']+'/preview')).status_code==403
    assert action(staff,submitted,'issue',previewHash='x').status_code==403
    returned=ok(action(client,submitted,'return',reason='Fix purpose'));saved=save(staff,returned,purpose='Updated purpose');ok(issue(client,saved))
    assert len(ok(staff.get(path('history/document/'+item['id'])))['items'])>=5
    current=detail(client,item);ok(client.put(path('items/'+item['id']+'/assignee'),json=body(version=current['version'],assigneeId='',reason='Duty ended')))
    assert staff.get(path('items/'+item['id'])).status_code==404
    assert staff.get(path('history/document/'+item['id'])).status_code==404

def test_numbering_race_replay_no_gaps_on_rollback(client,setup,monkeypatch):
    t,*_=setup;a=save(client,new(client,t));b=save(client,new(client,t));pa=preview(client,a);pb=preview(client,b)
    ca=signed('admin');cb=signed('admin')
    with ThreadPoolExecutor(2) as pool:responses=list(pool.map(lambda x:action(*x,'issue',previewHash=pa['previewHash'] if x[1]['id']==a['id'] else pb['previewHash']),[(ca,a),(cb,b)]))
    assert sorted(r.status_code for r in responses)==[200,409]
    loser=[a,b][next(i for i,r in enumerate(responses) if r.status_code==409)]
    p=preview(client,loser);assert p['snapshot']['number'].endswith('000002')
    def fail(*args,**kw):raise RuntimeError('notification failure')
    with monkeypatch.context() as scoped:
        scoped.setattr(api.school_documents.notifications,'deliver',fail)
        with pytest.raises(RuntimeError):issue(client,loser)
    assert preview(client,loser)['snapshot']['number'].endswith('000002') and detail(client,loser)['status']=='draft'
    request=body(version=loser['version'],action='issue',previewHash=preview(client,loser)['previewHash'])
    result=ok(client.post(path('items/'+loser['id']+'/action'),json=request));assert result['number'].endswith('000002')
    assert ok(client.post(path('items/'+loser['id']+'/action'),json=request))==result
    assert client.post(path('items/'+loser['id']+'/action'),json=request|dict(reason='changed')).status_code==409

def test_recipient_scope_changes_and_explicit_regrant(client,setup):
    t,_,parent,_=setup;item=ok(issue(client,save(client,new(client,t))));p=signed('parent')
    create(client,'parent',login='newparent');newparent=signed('newparent')
    assert newparent.get(path('items/'+item['id'])).status_code==404
    access=preview(client,item,True);updated=ok(action(client,item,'recipients',reason='New family account',previewHash=access['previewHash']))
    assert detail(newparent,item)['number']==item['number']
    with api.connect() as db:db.execute('UPDATE accounts SET permissions=? WHERE id=?',('{"workspaces":[],"grants":[]}',parent['id']))
    assert p.get(path('items/'+item['id'])).status_code==403
    assert p.get(path('items/'+item['id']+'/print')).status_code==403
    create(client,'parent',login='other',grants=[dict(workspace='demo',view='student',entityIds=['s3'])]);other=signed('other')
    assert not listing(other)['items'] and other.get(path('history/document/'+item['id'])).status_code==404

def test_preview_rechecks_person_template_and_recipient_changes(client,setup):
    t,*_=setup;item=save(client,new(client,t));p=preview(client,item);state=api.read('demo');state.students[0].name='Changed';api.save('demo',state)
    assert action(client,item,'issue',previewHash=p['previewHash']).status_code==409
    p=preview(client,item);create(client,'parent',login='late')
    assert action(client,item,'issue',previewHash=p['previewHash']).status_code==409
    disabled=ok(client.put(path('templates/'+t['id']),json=body(version=1,definition=t['definition'],active=False,reason='Stop issuance')))
    assert client.get(path('items/'+item['id']+'/preview')).status_code==409
    ok(client.put(path('templates/'+t['id']),json=body(version=disabled['version'],definition=definition(text='New template text'),active=True,reason='Update')))
    issued=ok(issue(client,item));assert 'Test purpose' in detail(client,issued)['snapshot']['text']

@pytest.mark.parametrize('text',['{{unknown}}','{{__import__("os")}}','{{personName','{{purpose}} }}'])
def test_template_rejects_unknown_or_executable_expressions(client,setup,text):
    assert client.post(path('templates'),json=body(version=0,definition=definition(text=text),active=True)).status_code==422

def test_plain_text_html_escape_csv_formula_and_required_fields(client,setup):
    t,*_=setup;item=new(client,t)
    assert client.get(path('items/'+item['id']+'/preview')).status_code==422
    assert client.put(path('items/'+item['id']),json=body(version=item['version'],values=dict(personName='forged'))).status_code==422
    item=save(client,item,purpose='<script>alert(1)</script> & secret');item=ok(issue(client,item));html=client.get(path('items/'+item['id']+'/print')).text
    assert '&lt;script&gt;' in html and '<script>' not in html
    t=ok(client.post(path('templates'),json=body(version=0,definition=definition(title='=SECRET()'),active=True)));new(client,t)
    csv=client.get(path('export?year=2026'));assert csv.content.startswith(b'\xef\xbb\xbf') and "'=SECRET()" in csv.text
    assert signed('parent').get(path('export?year=2026')).status_code==403

def test_internal_and_employee_scope_and_stale_actions(client,setup):
    _,teacher,*_=setup;t=ok(client.post(path('templates'),json=body(version=0,definition=definition(kind='employee'),active=True)))
    item=ok(client.post(path('items'),json=body(templateId=t['id'],templateVersion=1,subjectId=teacher['id'])))
    saved=save(client,item);assert action(client,item,'submit').status_code==409
    issued=ok(issue(client,saved));assert detail(signed('teacher'),issued)['snapshot']['subjectName']=='Сотрудник'
    assert signed('parent').get(path('items/'+item['id'])).status_code==404
    t=ok(client.post(path('templates'),json=body(version=0,definition=definition(kind='internal'),active=True)))
    internal=ok(client.post(path('items'),json=body(templateId=t['id'],templateVersion=1)));ok(issue(client,save(client,internal)))
    assert signed('teacher').get(path('items/'+internal['id'])).status_code==404
    assert signed('teacher').post(path('templates'),json=body(version=0,definition=definition())).status_code==403

def test_access_race_rechecked_after_snapshot(client,setup,monkeypatch):
    _,_,parent,_=setup;p=signed('parent');original=api.school_documents.prepare
    def revoke():
        original()
        with api.connect() as db:db.execute('UPDATE accounts SET version=version+1 WHERE id=?',(parent['id'],))
    monkeypatch.setattr(api.school_documents,'prepare',revoke)
    assert p.get(path('items')).status_code==409

def test_list_and_history_pagination(client,setup):
    t,*_=setup
    for i in range(4):new(client,t)
    a=listing(client,limit=2);b=listing(client,limit=2,before=a['nextBefore']);assert len(a['items'])==len(b['items'])==2 and not b['nextBefore']
    item=a['items'][0];saved=save(client,item);submitted=ok(action(client,saved,'submit'));ok(action(client,submitted,'return',reason='Review'))
    first=ok(client.get(path('history/document/'+item['id']+'?limit=2')));second=ok(client.get(path('history/document/'+item['id']+'?before='+str(first['nextBefore']))))
    assert len(first['items'])==len(second['items'])==2

@pytest.mark.parametrize('login',['parent','student','teacher'])
def test_deactivated_account_loses_detail_print_and_history(client,setup,login):
    t,teacher,parent,student=setup;person={'teacher':teacher,'parent':parent,'student':student}[login]
    item=ok(issue(client,save(client,new(client,t,assigneeId=teacher['id']))));c=signed(login)
    assert c.get(path('items/'+item['id'])).status_code==200
    with api.connect() as db:db.execute('UPDATE accounts SET active=0 WHERE id=?',(person['id'],))
    for route in ['items/'+item['id'],'items/'+item['id']+'/print','history/document/'+item['id']]:assert c.get(path(route)).status_code==401

def test_expired_family_link_and_inactive_pupil_hide_every_surface(client,setup,monkeypatch):
    from backend import families
    t,_,parent,_=setup
    with api.connect() as db:
        permissions=json.loads(db.execute('SELECT permissions FROM accounts WHERE id=?',(parent['id'],)).fetchone()[0]);permissions['familyRules']=[dict(workspace='demo',studentId='s1',startDate='2026-10-01',endDate='2026-10-15')]
        db.execute('UPDATE accounts SET permissions=? WHERE id=?',(json.dumps(permissions),parent['id']))
    monkeypatch.setattr(families,'today',lambda:'2026-10-15')
    item=ok(issue(client,save(client,new(client,t))));p=signed('parent');assert detail(p,item)['number']
    monkeypatch.setattr(families,'today',lambda:'2026-10-16')
    for route in ['items','items/'+item['id'],'items/'+item['id']+'/print']:assert p.get(path(route)).status_code==403
    monkeypatch.setattr(families,'today',lambda:'2026-10-15')
    state=api.read('demo');next(x for x in state.students if x.id=='s1').active=False;api.save('demo',state)
    assert p.get(path('items/'+item['id']+'/print')).status_code==403

def test_assignee_role_change_and_workspace_isolation(client,setup):
    t,teacher,*_=setup;item=ok(issue(client,save(client,new(client,t,assigneeId=teacher['id']))));c=signed('teacher')
    assert c.get('/api/documents/items/'+item['id']+'?workspace=school').status_code==403
    with api.connect() as db:db.execute('UPDATE accounts SET role=?,permissions=? WHERE id=?',('editor',json.dumps(dict(workspaces=['demo'],grants=[])),teacher['id']))
    for route in ['items/'+item['id'],'items/'+item['id']+'/print','history/document/'+item['id']]:assert c.get(path(route)).status_code in [401,404]
