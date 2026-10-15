from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
from uuid import uuid4
import pytest
from backend import api, live_lessons
from test_access import client, enable, create, signed
from test_quizzes import question


def url(path=''):return '/api/live-lessons'+path+('&' if '?' in path else '?')+'workspace=demo'
def ok(r):assert r.status_code==200,r.text;return r.json()
def get(c,path=''):return ok(c.get(url(path)))
def cmd(**kw):return dict(requestId=uuid4().hex,**kw)
def post(c,path='',**kw):return c.post(url(path),json=cmd(**kw))
def current(c,id):return get(c,'/'+id)
def action(c,id,action,**kw):return post(c,'/'+id+'/control',version=current(c,id)['version'],action=action,**kw)


@pytest.fixture
def setup(client,monkeypatch):
    clock=[datetime(2026,10,15,12,tzinfo=timezone.utc)]
    monkeypatch.setattr(live_lessons,'now',lambda:clock[0])
    enable(client)
    for role in ['teacher','student','parent']:create(client,role)
    create(client,'teacher',login='other',grants=[dict(workspace='demo',view='teacher',entityIds=['t1'])])
    create(client,'student',login='peer',grants=[dict(workspace='demo',view='student',entityIds=['s2'])])
    ok(client.get('/api/journal/quiz-bank?workspace=demo'))
    return signed('teacher'),signed('student'),signed('parent'),clock


def new(c,questions=None,**changes):
    qs=questions or [question(c)]
    w=api.read('demo').workloads[0]
    d=dict(title='Interactive synthetic lesson',instructions='Think first',groupId=w.groupId,subjectId=w.subjectId,questions=[dict(id=q['id'],version=q['version']) for q in qs])|changes
    return ok(post(c,version=0,definition=d))


def opened(c,questions=None):
    r=new(c,questions);ok(action(c,r['id'],'start'));ok(action(c,r['id'],'open',seconds=30));return current(c,r['id'])


def answer(c,id,version=0,**kw):
    return c.put(url('/'+id+'/answer'),json=cmd(version=version,studentId='s1',questionIndex=0,answer=dict(selected=['a'],text=''),**kw))


def test_teacher_paced_cycle_family_privacy_and_no_journal_write(client,setup):
    teacher,pupil,parent,_=setup;before=api.read('demo');r=new(teacher);id=r['id']
    assert get(pupil)['items']==[] and pupil.get(url('/'+id)).status_code==404
    assert signed('other').get(url('/'+id)).status_code==404
    ok(action(teacher,id,'start'));view=current(pupil,id);assert view['rounds']==[] and 'definition' not in view
    ok(action(teacher,id,'open',seconds=30));view=current(pupil,id)
    assert 'correctIds' not in json.dumps(view) and 'Эталон' not in json.dumps(view,ensure_ascii=False)
    assert parent.put(url('/'+id+'/answer'),json=cmd(studentId='s1',questionIndex=0,answer={})).status_code==403
    assert answer(signed('peer'),id).status_code==404
    ok(answer(pupil,id));assert current(parent,id)['rounds'][0].get('response') is None
    assert 'mark' not in current(pupil,id)['rounds'][0]['response']
    assert action(teacher,id,'reveal').status_code==409
    ok(action(teacher,id,'close'));assert answer(pupil,id,1).status_code==409
    checked=get(teacher,'/'+id+'/responses?questionIndex=0')['items']
    assert next(x for x in checked if x['studentId']=='s1')['response']['mark']['score']==2
    assert current(parent,id)['rounds'][0].get('response') is None
    ok(action(teacher,id,'reveal'));visible=current(parent,id)['rounds'][0]
    assert visible['solution']['correctIds']==['a'] and visible['response']['mark']['score']==2
    assert parent.get(url('/'+id+'?studentId=s2')).status_code==404
    for suffix in ['/responses?questionIndex=0','/history','/export.csv']:
        assert pupil.get(url('/'+id+suffix)).status_code==404
    ok(action(teacher,id,'end'));assert current(pupil,id)['status']=='ended'
    assert api.read('demo')==before
    with api.connect() as db:assert db.execute('SELECT count(*) FROM journal_entries').fetchone()[0]==0


def test_server_deadline_read_only_and_last_draft_collected(client,setup):
    t,p,_,clock=setup;r=opened(t);ok(answer(p,r['id']));clock[0]+=timedelta(seconds=30)
    view=current(p,r['id']);assert view['rounds'][0]['phase']=='closed' and not view['rounds'][0]['canAnswer']
    assert answer(p,r['id'],1).status_code==409
    assert get(t,'/'+r['id']+'/responses?questionIndex=0')['items'][0]['response']['mark']['score']==2
    ok(action(t,r['id'],'reveal'))
    with api.connect() as db:assert db.execute('SELECT phase FROM live_lesson_rounds').fetchone()[0]=='revealed'


def test_concurrent_answers_versions_and_replay(client,setup):
    t,p,_,_=setup;r=opened(t);id=r['id'];body=cmd(studentId='s1',questionIndex=0,version=0,answer=dict(selected=['a'],text=''))
    with ThreadPoolExecutor(2) as pool:rs=list(pool.map(lambda _:p.put(url('/'+id+'/answer'),json=body),range(2)))
    assert [x.status_code for x in rs]==[200,200] and rs[0].json()==rs[1].json()
    assert p.put(url('/'+id+'/answer'),json=body|dict(submit=True)).status_code==409
    with ThreadPoolExecutor(2) as pool:rs=list(pool.map(lambda _:answer(p,id,1),range(2)))
    assert sorted(x.status_code for x in rs)==[200,409]
    ok(answer(p,id,2,submit=True));assert answer(p,id,3).status_code==409
    ok(action(t,id,'end'));assert ok(p.put(url('/'+id+'/answer'),json=body))['version']==1


def test_control_order_simultaneous_open_and_no_reopen(client,setup):
    t,p,_,_=setup;q=question(t);q2=question(t,'second');r=new(t,[q,q2]);id=r['id'];ok(action(t,id,'start'));v=current(t,id)['version']
    with ThreadPoolExecutor(2) as pool:rs=list(pool.map(lambda _:post(t,'/'+id+'/control',version=v,action='open',questionIndex=0),range(2)))
    assert sorted(x.status_code for x in rs)==[200,409]
    assert action(t,id,'open',questionIndex=1).status_code==409
    ok(action(t,id,'close'));assert action(t,id,'open',questionIndex=0).status_code==409
    ok(action(t,id,'open',questionIndex=1));ok(action(t,id,'end'));assert answer(p,id).status_code==409
    ok(action(t,id,'reveal',questionIndex=1));assert current(p,id)['rounds'][1]['phase']=='revealed'


def test_fixed_snapshot_copy_and_archived_bank(client,setup):
    t,p,_,_=setup;q=question(t);r=new(t,[q]);id=r['id']
    q['question']['correctIds']=['b'];q['question']['text']='Changed in bank'
    ok(t.put('/api/journal/quiz-bank/choice?workspace=demo',json={k:q[k] for k in ['version','subject','tags','question']}|dict(archived=True)))
    assert current(t,id)['definition']['questions'][0]['question']['correctIds']==['a']
    ok(action(t,id,'start'));ok(action(t,id,'open'));copy=ok(action(t,id,'copy'))
    assert current(t,copy['id'])['status']=='draft' and current(t,copy['id'])['rounds']==[]
    assert p.get(url('/'+copy['id'])).status_code==404
    assert current(t,copy['id'])['definition']['questions'][0]['question']['correctIds']==['a']
    assert t.put(url('/'+id),json=cmd(version=3,definition={**current(t,id)['definition'],'questions':[dict(id='choice',version=1)]})).status_code==409


def test_current_teaching_membership_and_account_revocation(client,setup):
    t,p,parent,_=setup;r=opened(t);id=r['id'];body=cmd(studentId='s1',questionIndex=0,answer=dict(selected=['a'],text=''))
    ok(p.put(url('/'+id+'/answer'),json=body))
    state=api.read('demo');state.students[0].active=False;api.save('demo',state)
    assert p.put(url('/'+id+'/answer'),json=body).status_code in {403,404}
    assert parent.get(url('/'+id)).status_code in {403,404}
    state=api.read('demo');state.students[0].active=True;state.workloads[0].teacherId='t1';api.save('demo',state)
    assert t.get(url('/'+id)).status_code==404 and get(t)['items']==[]
    ok(action(client,id,'end'))


def test_member_added_after_start_does_not_join(client,setup):
    t,p,_,_=setup;r=opened(t);state=api.read('demo');person=state.students[0].model_copy(deep=True);person.id='new';person.name='New pupil';state.students.append(person);api.save('demo',state)
    create(client,'student',login='newp',grants=[dict(workspace='demo',view='student',entityIds=['new'])]);newp=signed('newp')
    assert get(newp)['items']==[] and newp.get(url('/'+r['id'])).status_code==404


def test_etag_pupil_isolation_expiry_and_poll_does_not_write(client,setup):
    t,p,_,clock=setup;r=opened(t);id=r['id'];path=url('/'+id+'/status');peer=signed('peer')
    before=p.get(path);assert before.status_code==200 and len(before.content)<500
    with api.connect() as db:before_changes=db.execute('SELECT count(*) FROM live_lesson_history').fetchone()[0]
    cached=p.get(path,headers={'If-None-Match':before.headers['etag']});assert cached.status_code==304 and not cached.content and cached.headers['cache-control']=='no-store'
    ok(peer.put(url('/'+id+'/answer'),json=cmd(studentId='s2',questionIndex=0,answer=dict(selected=['b'],text=''))))
    assert p.get(path,headers={'If-None-Match':before.headers['etag']}).status_code==304
    assert get(t,'/'+id+'/status')['saved']==1
    clock[0]+=timedelta(seconds=31)
    assert p.get(path,headers={'If-None-Match':before.headers['etag']}).json()['phase']=='closed'
    with api.connect() as db:assert db.execute('SELECT count(*) FROM live_lesson_history').fetchone()[0]==before_changes


def test_essay_review_and_correction_history(client,setup):
    t,p,parent,_=setup;q=question(t,kind='essay',options=[],correctIds=[]);r=opened(t,[q]);id=r['id']
    ok(p.put(url('/'+id+'/answer'),json=cmd(studentId='s1',questionIndex=0,answer=dict(text='Reasoning'),submit=True)))
    b=dict(studentId='s1',questionIndex=0,version=1,score=1,comment='Good start')
    assert t.put(url('/'+id+'/review'),json=cmd(**b)).status_code==409
    ok(action(t,id,'close'));assert get(t,'/'+id+'/responses?questionIndex=0')['items'][0]['response']['mark'] is None
    assert t.put(url('/'+id+'/review'),json=cmd(**(b|dict(score=3)))).status_code==422
    ok(t.put(url('/'+id+'/review'),json=cmd(**b)));ok(action(t,id,'reveal'))
    assert current(parent,id)['rounds'][0]['response']['mark']['score']==1
    b.update(version=2,score=2)
    assert t.put(url('/'+id+'/review'),json=cmd(**b)).status_code==422
    ok(t.put(url('/'+id+'/review'),json=cmd(**b,reason='Reread full answer')))
    assert current(p,id)['rounds'][0]['response']['mark']['score']==2
    assert any((x['detail'].get('before') or {}).get('score')==1 for x in get(t,'/'+id+'/history')['items'])


def test_malformed_answers_csrf_and_redacted_validation(client,setup):
    t,p,_,_=setup;r=opened(t);id=r['id'];base=dict(studentId='s1',questionIndex=0)
    for a in [dict(selected=['a','a']),dict(selected=['unknown']),dict(selected=['a','b']),dict(text='spoof')]:
        assert p.put(url('/'+id+'/answer'),json=cmd(**base,answer=a)).status_code==422
    secret='synthetic private answer'*600
    bad=p.put(url('/'+id+'/answer'),json=cmd(**base,answer=dict(text=secret)))
    assert bad.status_code==422 and secret not in bad.text and '"input"' not in bad.text
    assert p.put(url('/'+id+'/answer'),headers={'X-Atlas-Request':'0'},json=cmd(**base,answer={})).status_code==403
    assert p.get('/api/live-lessons?workspace=school').status_code==403


def test_cancel_and_csv_formula_protection(client,setup):
    t,p,parent,_=setup;q=question(t,kind='essay',options=[],correctIds=[]);r=opened(t,[q]);id=r['id']
    ok(p.put(url('/'+id+'/answer'),json=cmd(studentId='s1',questionIndex=0,answer=dict(text='=FORMULA()'))))
    assert action(t,id,'cancel').status_code==422
    ok(action(t,id,'cancel',reason='Class interrupted'))
    assert action(t,id,'reveal').status_code==409 and answer(p,id,1).status_code==409
    export=t.get(url('/'+id+'/export.csv'));assert export.status_code==200 and "'=FORMULA()" in export.text
    assert current(parent,id)['rounds'][0].get('response') is None


def test_bank_owner_access_and_fresh_version(client,setup):
    t,p,_,_=setup;q=question(client,'admin-question');w=api.read('demo').workloads[0]
    d=dict(title='Test',groupId=w.groupId,subjectId=w.subjectId,questions=[dict(id=q['id'],version=1)])
    assert post(t,definition=d).status_code==422
    assert post(p,definition=d).status_code==403
    q=question(t);d['questions']=[dict(id=q['id'],version=2)]
    assert post(t,definition=d).status_code==409


def test_scope_change_between_snapshot_and_transaction_refuses_write(client,setup,monkeypatch):
    t,p,_,_=setup;r=opened(t);original=api.live_lessons.snapshots.get
    def changed(workspace):
        snap=original(workspace);state=api.read(workspace);state.workloads[0].teacherId='t1';api.save(workspace,state);return snap
    monkeypatch.setattr(api.live_lessons.snapshots,'get',changed)
    assert post(t,'/'+r['id']+'/control',version=r['version'],action='end').status_code==409
    with api.connect() as db:assert db.execute('SELECT status FROM live_lessons WHERE id=?',(r['id'],)).fetchone()[0]=='live'


def test_status_etag_cannot_bypass_account_revocation(client,setup):
    t,p,_,_=setup;r=opened(t);path=url('/'+r['id']+'/status');first=p.get(path)
    with api.connect() as db:db.execute("UPDATE accounts SET active=0,version=version+1 WHERE login='student'")
    assert p.get(path,headers={'If-None-Match':first.headers['etag']}).status_code==401


def test_parent_list_requires_same_captured_and_current_child(client,setup):
    t,_,_,_=setup;r=opened(t)
    create(client,'parent',login='two',grants=[dict(workspace='demo',view='student',entityIds=['s1','s3'])])
    parent=signed('two');assert get(parent)['items'][0]['id']==r['id']
    state=api.read('demo');s1=next(p for p in state.students if p.id=='s1');s3=next(p for p in state.students if p.id=='s3');s1.classId,s3.classId=s3.classId,s1.classId;api.save('demo',state)
    assert get(parent)['items']==[] and parent.get(url('/'+r['id'])).status_code==404
