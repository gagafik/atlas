from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import json
import sqlite3
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from backend import api
from backend.access import now
from test_access import client, enable, create, signed, PASSWORD


def request(**values): return dict(requestId=str(uuid4()),**values)
def route(id='',suffix=''): return '/api/forms/items'+('/'+id if id else '')+suffix+'?workspace=demo'
def get(client,id):
    response=client.get(route(id));assert response.status_code==200,response.text;return response.json()
def new(client,kind='survey'):
    response=client.post(route(),json=request(title='School form',kind=kind));assert response.status_code==200,response.text
    return response.json()['id']
def configure(client,id,roles=['parent'],questions=None,**values):
    form=get(client,id)
    definition=form['definition']|dict(description='Please respond',questions=questions or [dict(id='q',title='Choose',kind='single',required=True,choices=['First','Second'])],audience=dict(roles=roles,classIds=[]))|values
    response=client.put(route(id),json=request(version=form['version'],definition=definition));assert response.status_code==200,response.text
    return get(client,id)
def publish(client,id):
    preview=client.get(route(id,'/preview'));assert preview.status_code==200,preview.text
    response=client.post(route(id,'/transition'),json=request(version=preview.json()['version'],action='publish',audienceHash=preview.json()['audienceHash']))
    assert response.status_code==200,response.text
    return get(client,id)
def answer(client,id,entity='s1',action='submit',answers=None,version=0,formVersion=None,**kw):
    formVersion=formVersion or get(client,id)['version']
    return client.put(route(id,'/answer'),json=request(version=version,formVersion=formVersion,entityId=entity,action=action,answers=answers if answers is not None else {'q':'First'},**kw))
def assign(client,id,user,permission='manage'):
    response=client.put(route(id,'/access'),json=request(version=get(client,id)['version'],collaborators=[dict(userId=user['id'],permission=permission)]));assert response.status_code==200,response.text


def test_admin_delegates_each_form_separately_and_manager_cannot_expand_audience(client):
    enable(client);teacher=create(client,role='teacher');create(client,role='parent')
    first=new(client);second=new(client);configure(client,first);assign(client,first,teacher)
    manager=signed('teacher')
    assert manager.get('/api/forms/options?workspace=demo').json()['canCreate'] is False
    assert manager.post(route(),json=request(title='Unauthorized')).status_code==403
    assert manager.get(route(second)).status_code==404
    form=get(manager,first);definition=form['definition']|dict(title='Teacher prepared')
    assert manager.put(route(first),json=request(version=form['version'],definition=definition)).status_code==200
    form=get(manager,first);definition['audience']['roles']=['student']
    assert manager.put(route(first),json=request(version=form['version'],definition=definition)).status_code==403
    assert manager.put(route(first,'/access'),json=request(version=form['version'],collaborators=[])).status_code==403
    publish(manager,first)
    assert signed('parent').get(route(first)).status_code==200


def test_parent_student_and_sibling_answers_are_private_and_survey_is_immutable(client):
    enable(client);create(client,role='parent');create(client,role='student');create(client,role='parent',login='other',grants=[dict(workspace='demo',view='student',entityIds=['s2'])])
    id=new(client);configure(client,id,roles=['parent','student']);publish(client,id)
    parent=signed('parent');student=signed('student');other=signed('other')
    response=answer(parent,id);assert response.status_code==200,response.text
    assert student.get(route(id,'/answer')+'&entityId=s1').json()['answer']['version']==0
    assert other.get(route(id,'/answer')+'&entityId=s1').status_code==404
    assert parent.get(route(id,'/responses')).status_code==404
    assert parent.get(route(id,'/export')).status_code==404
    assert parent.get(route(id)+'&workspace=school').status_code==403
    form=get(client,id)
    assert client.put(route(id),json=request(version=form['version'],definition=form['definition']|dict(title='Changed'))).status_code==409
    assert 'audience' not in get(parent,id)['definition']


def test_application_save_submit_return_resubmit_approve_history_and_csv(client):
    enable(client);create(client,role='parent');reviewer=create(client,role='staff')
    id=new(client,'application');configure(client,id,questions=[dict(id='q',title='Reason',kind='text')]);assign(client,id,reviewer,'review');publish(client,id)
    parent=signed('parent');staff=signed('staff')
    saved=answer(parent,id,action='save',answers={'q':'=private draft'});assert saved.status_code==200,saved.text
    assert staff.get(route(id,'/responses')).json()['items']==[]
    assert '=private draft' not in staff.get(route(id,'/export')).text
    assert 'draft' not in get(staff,id)['counts']
    sent=answer(parent,id,version=1,answers={'q':'=SUM(1,2)'}).json()
    assert sent['version']==2
    assert staff.post(route(id,'/transition'),json=request(version=get(staff,id)['version'],action='close')).status_code==404
    review_url=route(id,'/responses/'+sent['id']+'/review')
    reviewed=staff.post(review_url,json=request(version=2,formVersion=get(staff,id)['version'],action='returned',comment='Clarify'))
    assert reviewed.status_code==200,reviewed.text
    personal=parent.get(route(id,'/answer')+'&entityId=s1').json()
    assert personal['answer']['comment']=='Clarify' and personal['history'][0]['status']=='returned'
    sent=answer(parent,id,version=3,answers={'q':'=SUM(3,4)'}).json();assert sent['version']==4
    assert staff.post(review_url,json=request(version=4,formVersion=get(staff,id)['version'],action='approved')).status_code==200
    assert answer(parent,id,version=5,action='withdraw').status_code==409
    csv=staff.get(route(id,'/export'));assert csv.status_code==200 and "'=SUM(3,4)" in csv.text


def test_preview_detects_audience_changes_and_no_response_created_by_invalid_publish(client):
    enable(client);create(client,role='parent');id=new(client);configure(client,id)
    preview=client.get(route(id,'/preview')).json();create(client,role='parent',login='another')
    response=client.post(route(id,'/transition'),json=request(version=preview['version'],action='publish',audienceHash=preview['audienceHash']))
    assert response.status_code==409 and get(client,id)['status']=='draft'
    publish(client,id)


def test_idempotent_submission_and_notification_rollback(client,monkeypatch):
    enable(client);create(client,role='parent');id=new(client);configure(client,id);form=publish(client,id);parent=signed('parent')
    body=request(version=0,formVersion=form['version'],entityId='s1',action='submit',answers={'q':'First'})
    original=api.notifications.deliver
    def fail(*args,**kwargs):raise RuntimeError('transport unavailable')
    monkeypatch.setattr(api.notifications,'deliver',fail)
    with pytest.raises(RuntimeError):parent.put(route(id,'/answer'),json=body)
    assert parent.get(route(id,'/answer')+'&entityId=s1').json()['answer']['version']==0
    monkeypatch.setattr(api.notifications,'deliver',original)
    first=parent.put(route(id,'/answer'),json=body);second=parent.put(route(id,'/answer'),json=body)
    assert first.status_code==second.status_code==200 and first.json()==second.json()
    with closing(sqlite3.connect(api.DB)) as db:
        assert db.execute("SELECT count(*) FROM school_form_history WHERE action='submit'").fetchone()[0]==1
    body['answers']={'q':'Second'}
    assert parent.put(route(id,'/answer'),json=body).status_code==409


def test_revoking_collaborator_hides_form_and_pending_changes_are_rechecked(client):
    enable(client);staff=create(client,role='staff');id=new(client);configure(client,id);assign(client,id,staff)
    responsible=signed('staff');form=get(responsible,id)
    assert client.put(route(id,'/access'),json=request(version=form['version'],collaborators=[])).status_code==200
    assert responsible.get(route(id)).status_code==404
    assert responsible.put(route(id),json=request(version=form['version'],definition=form['definition'])).status_code==404


def test_deadline_withdraw_stale_review_and_reopen(client,monkeypatch):
    enable(client);create(client,role='parent');id=new(client,'application');configure(client,id,closesAt=(now()+timedelta(hours=1)).isoformat());publish(client,id);parent=signed('parent')
    sent=answer(parent,id).json()
    assert answer(parent,id,version=1,action='withdraw').status_code==200
    assert client.post(route(id,'/responses/'+sent['id']+'/review'),json=request(version=1,formVersion=get(client,id)['version'],action='approved')).status_code==409
    assert client.get(route(id,'/responses')).json()['items'][0]['answers']=={}
    future=now()+timedelta(hours=2);monkeypatch.setattr('backend.school_forms.now',lambda:future)
    assert answer(parent,id,version=2).status_code==409
    form=get(client,id)
    assert client.post(route(id,'/transition'),json=request(version=form['version'],action='reopen',closesAt=(future+timedelta(hours=1)).isoformat())).status_code==200
    assert answer(parent,id,version=2).status_code==200


def test_consent_accepts_explicit_no_and_rejects_missing_required_answers(client):
    enable(client);create(client,role='parent');id=new(client,'consent')
    configure(client,id,questions=[dict(id='q',title='I agree',kind='consent',required=True)]);publish(client,id);parent=signed('parent')
    assert answer(parent,id,answers={}).status_code==422
    assert answer(parent,id,answers={'q':False}).status_code==200


def test_family_revoke_expiry_and_reactivation_are_checked_without_relogin(client,monkeypatch):
    from backend import families
    from test_families import save
    enable(client);person=create(client,role='parent');id=new(client);configure(client,id);publish(client,id);parent=signed('parent')
    sent=answer(parent,id).json()
    assert save(client,person['id'],status='revoked').status_code==200
    for suffix in ['', '/answer', '/responses', '/export', '/summary']:
        assert parent.get(route(id,suffix)+'&entityId=s1').status_code==403
    assert answer(parent,id,version=1,action='withdraw',formVersion=get(client,id)['version']).status_code==403
    # School staff retain the historical submitted answer, even when the family link ends.
    assert client.get(route(id,'/responses')).json()['items'][0]['id']==sent['id']
    assert save(client,person['id'],status='active',endDate='2026-10-15').status_code==200
    monkeypatch.setattr(families,'today',lambda:'2026-10-15')
    assert parent.get(route(id,'/answer')+'&entityId=s1').status_code==200
    monkeypatch.setattr(families,'today',lambda:'2026-10-16')
    assert parent.get(route(id,'/answer')+'&entityId=s1').status_code==403


@pytest.mark.parametrize('change',['inactive','class'])
def test_current_student_activity_and_class_control_access(client,change):
    enable(client);create(client,role='parent');state=api.read('demo');pupil=next(s for s in state.students if s.id=='s1')
    id=new(client);configure(client,id,audience=dict(roles=['parent'],classIds=[pupil.classId]));publish(client,id);parent=signed('parent')
    assert get(parent,id)['contexts'][0]['entityId']=='s1'
    if change=='inactive':pupil.active=False
    else:pupil.classId=next(c.id for c in state.classes if c.id!=pupil.classId)
    api.save('demo',state)
    response=parent.get(route(id))
    assert response.status_code==(403 if change=='inactive' else 404)
    if change=='class':assert parent.get(route()).json()['items']==[]


def test_multiple_children_have_separate_answers_and_new_accounts_are_not_auto_added(client):
    enable(client);create(client,role='parent',grants=[dict(workspace='demo',view='student',entityIds=['s1','s2'])])
    id=new(client);configure(client,id);publish(client,id);parent=signed('parent')
    assert {c['entityId'] for c in get(parent,id)['contexts']}=={'s1','s2'}
    a=answer(parent,id,'s1').json();b=answer(parent,id,'s2',answers={'q':'Second'}).json()
    assert a['id']!=b['id']
    create(client,role='parent',login='new-parent')
    assert signed('new-parent').get(route(id)).status_code==404
    assert client.get(route(id,'/summary')).json()['questions'][0]['choices']=={'First':1,'Second':1}


def test_changed_employee_permissions_require_new_form_assignment(client):
    enable(client);staff=create(client,role='staff');id=new(client);configure(client,id);assign(client,id,staff)
    body={k:v for k,v in staff.items() if k!='id'};body['workspaces']=['school','demo']
    assert client.put('/api/access/users/'+staff['id'],json=body).status_code==200
    employee=signed('staff')
    assert employee.get(route(id)).status_code==404
    assert get(client,id)['collaborators'][0]['current'] is False
    assign(client,id,staff)
    assert employee.get(route(id)).status_code==200


def test_duplicate_creation_and_concurrent_answer_write_are_atomic(client):
    enable(client);create(client,role='parent');body=request(title='Single form')
    with ThreadPoolExecutor(max_workers=2) as pool:
        created=list(pool.map(lambda _:client.post(route(),json=body),range(2)))
    assert all(r.status_code==200 for r in created) and created[0].json()==created[1].json()
    id=created[0].json()['id'];configure(client,id);form=publish(client,id);parent=signed('parent')
    with ThreadPoolExecutor(max_workers=2) as pool:
        writes=list(pool.map(lambda choice:answer(parent,id,answers={'q':choice},formVersion=form['version']),['First','Second']))
    assert sorted(r.status_code for r in writes)==[200,409]
    with closing(sqlite3.connect(api.DB)) as db:
        assert db.execute('SELECT count(*) FROM school_form_answers').fetchone()[0]==1
        assert db.execute("SELECT count(*) FROM school_form_history WHERE action='submit'").fetchone()[0]==1


def test_concurrent_review_and_withdraw_have_one_winner(client):
    enable(client);create(client,role='parent');id=new(client,'application');configure(client,id);form=publish(client,id);parent=signed('parent');sent=answer(parent,id).json()
    with ThreadPoolExecutor(max_workers=2) as pool:
        withdrawal=pool.submit(answer,parent,id,version=1,action='withdraw',formVersion=form['version'])
        review=pool.submit(client.post,route(id,'/responses/'+sent['id']+'/review'),json=request(version=1,formVersion=form['version'],action='approved'))
        assert sorted([withdrawal.result().status_code,review.result().status_code])==[200,409]


def test_private_draft_withdrawal_history_summary_and_copy(client):
    enable(client);create(client,role='parent');staff=create(client,role='staff');id=new(client);configure(client,id);assign(client,id,staff);publish(client,id);parent=signed('parent')
    draft=answer(parent,id,action='save').json();history=route(id,'/responses/'+draft['id']+'/history')
    assert client.get(history).status_code==404
    assert client.get(route(id,'/summary')).json()['responses']==0
    answer(parent,id,version=1);answer(parent,id,version=2,action='withdraw')
    assert all(h['action']!='save' and 'answers' not in h for h in client.get(history).json())
    assert client.get(route(id,'/summary')).json()['responses']==0
    assert parent.get(history).status_code==404
    copied=client.post(route(id,'/copy'),json=request(version=get(client,id)['version']));assert copied.status_code==200
    copy=get(client,copied.json()['id'])
    assert copy['status']=='draft' and copy['counts']=={} and copy['audienceCount']==0 and copy['collaborators']==[]
    assert copy['definition']['questions']==get(client,id)['definition']['questions']
    assert signed('staff').get(route(copy['id'])).status_code==404
    assert parent.get(route(copy['id'])).status_code==404


def test_csv_spools_large_exports_and_closes_temporary_file(client,monkeypatch):
    import tempfile
    enable(client);create(client,role='parent');id=new(client);configure(client,id,questions=[dict(id='q',title='Reason',kind='text')]);publish(client,id);parent=signed('parent')
    answer(parent,id,answers={'q':'x'*3000})
    original=tempfile.SpooledTemporaryFile;files=[]
    def spool(**kwargs):
        result=original(**(kwargs|dict(max_size=16)));files.append(result);return result
    monkeypatch.setattr('backend.school_forms.tempfile.SpooledTemporaryFile',spool)
    response=client.get(route(id,'/export'))
    assert response.status_code==200 and 'x'*3000 in response.text and response.headers['cache-control']=='no-store'
    assert len(files)==1 and files[0]._rolled and files[0].closed


def test_form_reads_do_not_require_database_writer_lock(client):
    enable(client);create(client,role='parent');id=new(client);configure(client,id);publish(client,id)
    with closing(sqlite3.connect(api.DB)) as db:
        db.execute('BEGIN IMMEDIATE')
        with ThreadPoolExecutor(max_workers=1) as pool:
            future=pool.submit(client.get,route(id,'/summary'))
            try:assert future.result(timeout=3).status_code==200
            finally:db.rollback()


def test_scheduled_form_opening_and_invalid_reopen_are_safe(client,monkeypatch):
    enable(client);create(client,role='parent');id=new(client);moment=now()
    configure(client,id,opensAt=(moment+timedelta(hours=2)).isoformat(),closesAt=(moment+timedelta(hours=4)).isoformat());form=publish(client,id);parent=signed('parent')
    assert answer(parent,id).status_code==409
    assert client.post(route(id,'/transition'),json=request(version=form['version'],action='reopen',closesAt=(moment+timedelta(hours=1)).isoformat())).status_code==422
    assert get(client,id)['version']==form['version']
    monkeypatch.setattr('backend.school_forms.now',lambda:moment+timedelta(hours=3))
    assert answer(parent,id).status_code==200


@pytest.mark.parametrize('changed',['account','school'])
def test_authority_and_school_revision_are_rechecked_inside_transaction(client,monkeypatch,changed):
    enable(client);person=create(client,role='parent');id=new(client);configure(client,id);form=publish(client,id);parent=signed('parent')
    original=api.school_forms.snapshots.get
    def snapshot(workspace):
        snap=original(workspace)
        if changed=='account':
            with api.connect() as db:db.execute('UPDATE accounts SET version=version+1 WHERE id=?',(person['id'],))
        else:api.save('demo',api.read('demo'))
        return snap
    monkeypatch.setattr(api.school_forms.snapshots,'get',snapshot)
    assert answer(parent,id,formVersion=form['version']).status_code==409
    with closing(sqlite3.connect(api.DB)) as db:assert db.execute('SELECT count(*) FROM school_form_answers').fetchone()[0]==0


def test_form_and_response_cursor_pagination_and_cross_workspace_isolation(client):
    enable(client);create(client,role='parent',grants=[dict(workspace='demo',view='student',entityIds=['s1','s2'])]);parent=signed('parent');ids=[]
    for _ in range(3):
        id=new(client);configure(client,id);publish(client,id);ids.append(id)
    page=parent.get(route()+'&limit=1').json();seen=[page['items'][0]['id']]
    while page['nextBefore']:
        page=parent.get(route()+f"&limit=1&before={page['nextBefore']}").json();seen.extend(i['id'] for i in page['items'])
    assert seen==ids[::-1]
    answer(parent,ids[0],'s1');answer(parent,ids[0],'s2')
    page=client.get(route(ids[0],'/responses')+'&limit=1').json();first=page['items'][0]['id']
    following=client.get(route(ids[0],'/responses')+f"&limit=1&after={page['nextAfter']}").json()
    assert following['items'][0]['id']!=first and following['nextAfter'] is None
    assert client.get(route(ids[0]).replace('workspace=demo','workspace=school')).status_code==404


@pytest.mark.parametrize('kind,value,valid',[
    ('text','Text',True),('text','x'*4001,False),('text',False,False),
    ('single','absent',False),('multiple',['First','First'],False),('multiple',['First','Second'],True),
    ('consent','yes',False),('consent',0,False),('consent',False,True),
    ('date','2026-02-30',False),('date','2026-10-15',True),('number',0,True),('number',True,False),('number',1e13,False),
])
def test_answer_types_are_validated_before_save(kind,value,valid):
    from fastapi import HTTPException
    from backend.school_forms_models import Definition, AnswerWrite, validate_answers
    definition=Definition(title='Test',questions=[dict(id='q',title='Question',kind=kind,choices=['First','Second'] if kind in ['single','multiple'] else [])]).model_dump(mode='json')
    body=AnswerWrite(**request(version=0,formVersion=1,action='submit',answers={'q':value}))
    if valid:validate_answers(definition,body.answers,True)
    else:
        with pytest.raises(HTTPException) as exc:validate_answers(definition,body.answers,True)
        assert exc.value.status_code==422


def count_notices(user_id,action='forms.submitted',unread=True):
    with closing(sqlite3.connect(api.DB)) as db:
        return db.execute('SELECT count(*) FROM notifications WHERE user_id=? AND action=?'+(' AND read_at IS NULL' if unread else ''),(user_id,action)).fetchone()[0]


def review_body(client,id,version,action,comment=''):
    return request(version=version,formVersion=get(client,id)['version'],action=action,comment=comment)


def test_reviewer_cannot_decide_own_application(client):
    admin=enable(client);staff=create(client,role='staff');id=new(client,'application')
    configure(client,id,roles=['staff'],questions=[dict(id='q',title='Reason',kind='text')]);assign(client,id,staff,'review');publish(client,id)
    st=signed('staff');sent=answer(st,id,entity='',answers={'q':'Mine'});assert sent.status_code==200,sent.text
    sent=sent.json();url=route(id,'/responses/'+sent['id']+'/review')
    refused=st.post(url,json=review_body(st,id,1,'approved'))
    assert refused.status_code==403 and 'другой ответственный' in refused.json()['detail']
    item=st.get(route(id,'/responses')).json()['items'][0]
    assert item['own'] is True and item['status']=='submitted' and 'user_id' not in item and 'userId' not in item
    assert client.get(route(id,'/responses')).json()['items'][0]['own'] is False
    assert client.post(url,json=review_body(client,id,1,'approved')).status_code==200
    # The author is not told about their own submission; the administrator who owns the form is.
    assert count_notices(staff['id'])==0 and count_notices(admin['id'])==1


def test_returned_application_keeps_status_comment_and_private_correction(client):
    enable(client);create(client,role='parent');reviewer=create(client,role='staff')
    id=new(client,'application');configure(client,id,questions=[dict(id='q',title='Reason',kind='text')]);assign(client,id,reviewer,'review');publish(client,id)
    parent=signed('parent');staff=signed('staff')
    answer(parent,id,action='save',answers={'q':'Draft'})
    sent=answer(parent,id,version=1,answers={'q':'Original'}).json()
    review_url=route(id,'/responses/'+sent['id']+'/review')
    assert staff.post(review_url,json=review_body(staff,id,2,'returned','Clarify')).status_code==200
    saved=answer(parent,id,version=3,action='save',answers={'q':'=secret edit'});assert saved.status_code==200,saved.text
    mine=parent.get(route(id,'/answer')+'&entityId=s1').json()['answer']
    assert mine['status']=='returned' and mine['comment']=='Clarify' and mine['answers']=={'q':'=secret edit'} and 'draft' not in mine
    item=staff.get(route(id,'/responses')).json()['items'][0]
    assert item['status']=='returned' and item['comment']=='Clarify' and item['answers']=={'q':'Original'} and 'draft' not in item
    assert '=secret edit' not in staff.get(route(id,'/export')).text and 'secret edit' not in staff.get(route(id,'/responses/'+sent['id']+'/history')).text
    with closing(sqlite3.connect(api.DB)) as db:
        documents=[json.loads(d) for d, in db.execute('SELECT document FROM school_form_history WHERE answer_id=?',(sent['id'],))]
    assert documents and all('draft' not in d and 'secret edit' not in json.dumps(d) for d in documents)
    assert answer(parent,id,version=4,answers={'q':'Final'}).status_code==200
    item=staff.get(route(id,'/responses')).json()['items'][0]
    assert item['status']=='submitted' and item['comment']=='' and item['answers']=={'q':'Final'} and 'draft' not in item
    with closing(sqlite3.connect(api.DB)) as db:
        assert 'draft' not in json.loads(db.execute('SELECT document FROM school_form_answers WHERE id=?',(sent['id'],)).fetchone()[0])


def test_returned_application_can_be_corrected_after_deadline(client,monkeypatch):
    enable(client);create(client,role='parent');create(client,role='parent',login='parent2',grants=[dict(workspace='demo',view='student',entityIds=['s2'])])
    id=new(client,'application');configure(client,id,closesAt=(now()+timedelta(hours=1)).isoformat());publish(client,id)
    parent=signed('parent');p2=signed('parent2')
    sent=answer(parent,id).json()
    assert client.post(route(id,'/responses/'+sent['id']+'/review'),json=review_body(client,id,1,'returned','Fix')).status_code==200
    future=now()+timedelta(hours=2);monkeypatch.setattr('backend.school_forms.now',lambda:future)
    assert answer(parent,id,version=2,action='save').status_code==200
    resent=answer(parent,id,version=3);assert resent.status_code==200 and resent.json()['status']=='submitted'
    refused=answer(p2,id,'s2');assert refused.status_code==409 and 'Приём ответов сейчас закрыт.' in refused.json()['detail']
    assert answer(parent,id,version=4,action='withdraw').status_code==409


def test_new_application_after_decision_keeps_decided_history(client):
    enable(client);create(client,role='parent');reviewer=create(client,role='staff')
    id=new(client,'application');configure(client,id,questions=[dict(id='q',title='Reason',kind='text')]);assign(client,id,reviewer,'review');publish(client,id)
    parent=signed('parent');staff=signed('staff')
    sent=answer(parent,id,answers={'q':'First try'}).json()
    assert staff.post(route(id,'/responses/'+sent['id']+'/review'),json=review_body(staff,id,1,'approved','Done')).status_code==200
    assert answer(parent,id,version=2,action='save',answers={'q':'Second try'}).status_code==200
    item=staff.get(route(id,'/responses')).json()['items'][0]
    assert item['status']=='approved' and item['answers']=={'q':'First try'} and item['comment']=='Done'
    again=answer(parent,id,version=3,answers={'q':'Second try'});assert again.status_code==200,again.text
    item=staff.get(route(id,'/responses')).json()['items'][0]
    assert item['status']=='submitted' and item['comment']=='' and item['answers']=={'q':'Second try'}
    history=staff.get(route(id,'/responses/'+sent['id']+'/history')).json()
    assert {'approved','submitted'}<={h['status'] for h in history} and 'Done' in {h['comment'] for h in history}
    with closing(sqlite3.connect(api.DB)) as db:
        rows=db.execute("SELECT document FROM school_form_history WHERE answer_id=? AND action='approved'",(sent['id'],)).fetchall()
    assert len(rows)==1 and json.loads(rows[0][0])['answers']=={'q':'First try'}
    # A decided application cannot be withdrawn, but the resubmitted one can.
    assert answer(parent,id,version=4,action='withdraw').status_code==200


def test_submissions_coalesce_reviewer_notifications(client):
    admin=enable(client)
    create(client,role='parent',grants=[dict(workspace='demo',view='student',entityIds=['s1','s2'])])
    create(client,role='parent',login='parent2',grants=[dict(workspace='demo',view='student',entityIds=['s3'])])
    reviewer=create(client,role='staff');first=new(client);second=new(client)
    for id in [first,second]:configure(client,id);assign(client,id,reviewer,'review');publish(client,id)
    parent=signed('parent');p2=signed('parent2')
    for who,entity in [(parent,'s1'),(parent,'s2'),(p2,'s3')]:assert answer(who,first,entity).status_code==200
    assert count_notices(reviewer['id'])==1 and count_notices(admin['id'])==1
    assert answer(parent,second,'s1').status_code==200
    assert count_notices(reviewer['id'])==2 and count_notices(admin['id'])==2
    assert answer(p2,second,'s3').status_code==200
    assert count_notices(reviewer['id'])==2 and count_notices(reviewer['id'],unread=False)==2


def test_responses_and_csv_ordered_by_class_then_name(client):
    import csv as csvlib,io
    state=api.read('demo')
    for c in state.classes:
        if c.id=='c-demo-d':c.name='7D'
    api.save('demo',state)
    enable(client);create(client,role='parent',grants=[dict(workspace='demo',view='student',entityIds=['s1','s2','s3','s4'])])
    id=new(client);configure(client,id);publish(client,id);parent=signed('parent')
    for entity in ['s4','s1','s3','s2']:assert answer(parent,id,entity).status_code==200
    items=client.get(route(id,'/responses')).json()['items']
    assert [i['entityName'] for i in items]==[f'Ученик {n} (пример)' for n in [3,4,1,2]]
    assert [i['className'] for i in items]==['7D','7D','10A','10A']
    seen=[];page=client.get(route(id,'/responses')+'&limit=1').json()
    while True:
        seen+=[i['entityName'] for i in page['items']]
        if not page['nextAfter']:break
        page=client.get(route(id,'/responses')+f"&limit=1&after={page['nextAfter']}").json()
    assert seen==[i['entityName'] for i in items]
    rows=list(csvlib.reader(io.StringIO(client.get(route(id,'/export')).text.lstrip('﻿'))))
    assert rows[0][:3]==['Отправитель','Ученик','Класс'] and [r[1] for r in rows[1:]]==seen and [r[2] for r in rows[1:]]==['7D','7D','10A','10A']
    assert client.get(route(id,'/responses')+'&after=missing').status_code==409
    # A cursor stays valid after its answer is withdrawn and saved again as a private draft.
    first=items[0]['id'];assert answer(parent,id,'s3',version=1,action='withdraw').status_code==200 and answer(parent,id,'s3',version=2,action='save').status_code==200
    rest=client.get(route(id,'/responses')+f'&after={first}').json()
    assert [i['entityName'] for i in rest['items']]==[f'Ученик {n} (пример)' for n in [4,1,2]] and rest['nextAfter'] is None


def test_natural_order_puts_numbers_before_text_and_ignores_case():
    from backend.natural import natural
    assert sorted(['10B','7A','10A','7a'],key=natural)==['7A','7a','10A','10B']
    assert sorted(['b','10','a1b','','2',None],key=natural)==['',None,'2','10','a1b','b']
