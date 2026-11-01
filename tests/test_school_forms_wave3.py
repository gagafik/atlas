"""School forms, wave 3: access that follows live links, standing forms, announcement audiences,
seat limits, the list of people who have not answered with a reminder, and anonymous surveys."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import timedelta
import json
import sqlite3
from uuid import uuid4

import pytest
from backend import api, families
from backend.access import now
from backend.school_forms_models import ANONYMOUS_MIN
from test_access import client, enable, create, signed
from test_school_forms import request, route, get, new, configure, publish, answer, assign, count_notices, review_body

FIRST_ONLY=[dict(id='q',title='Choose',kind='single',required=True,choices=['First','Second'],limits={'First':1})]


def parent(client,login,*pupils):
    return create(client,role='parent',login=login,name='Родитель '+login,grants=[dict(workspace='demo',view='student',entityIds=list(pupils))])


def pending(client,id,**query):
    response=client.get(route(id,'/pending')+''.join(f'&{k}={v}' for k,v in query.items()));assert response.status_code==200,response.text
    return response.json()


def remind(client,id,requestId=None):
    return client.post(route(id,'/remind'),json=dict(requestId=requestId or str(uuid4())))


def set_class_teacher(class_id,teacher_id):
    state=api.read('demo');state.classes=[c.model_copy(update=dict(classTeacherId=teacher_id)) if c.id==class_id else c for c in state.classes];api.save('demo',state)


def test_definition_rules(client):
    enable(client);create(client,role='parent')
    survey=new(client);plain=new(client,'form')
    for id,values in [
        (survey,dict(questions=[dict(id='q',title='Choose',kind='single',choices=['First','Second'],limits={'Third':1})])),
        (survey,dict(questions=[dict(id='q',title='Choose',kind='single',choices=['First','Second'],limits={'First':0})])),
        (survey,dict(questions=[dict(id='q',title='Choose',kind='single',choices=['First','Second'],limits={'First':5001})])),
        (survey,dict(questions=[dict(id='q',title='Text',kind='text',limits={'x':1})])),
        (plain,dict(anonymous=True)),
        (survey,dict(anonymous=True,questions=FIRST_ONLY)),
        (survey,dict(anonymous=True,standing=True)),
        (survey,dict(standing=True,closesAt=(now()+timedelta(days=3)).isoformat())),
        (survey,dict(standing=True,audience=dict(roles=[],classIds=[],preset='teachers'))),
        (survey,dict(audience=dict(roles=['parent'],classIds=[],preset='staff'))),
        (survey,dict(audience=dict(roles=[],classIds=[],preset='saved'))),
        (survey,dict(audience=dict(roles=[],classIds=[],savedGroupId='group'))),
    ]:
        form=get(client,id)
        refused=client.put(route(id),json=request(version=form['version'],definition=form['definition']|dict(description='x')|values))
        assert refused.status_code==422,(values,refused.text)
        assert get(client,id)==form
    form=get(client,survey)
    assert client.put(route(survey),json=request(version=form['version'],definition=form['definition']|dict(anonymous=True))).status_code==200


def test_manager_cannot_switch_standing_or_anonymous(client):
    enable(client);teacher=create(client,role='teacher');id=new(client);configure(client,id);assign(client,id,teacher)
    # A draft stored before audiences had presets and forms had these switches.
    with api.connect() as db:db.execute("UPDATE school_forms SET document=json_remove(document,'$.audience.preset','$.audience.savedGroupId','$.standing','$.anonymous') WHERE id=?",(id,))
    manager=signed('teacher');form=get(manager,id)
    assert 'preset' not in form['definition']['audience'] and 'standing' not in form['definition']
    for values in [dict(standing=True),dict(anonymous=True),dict(audience=dict(roles=['student'],classIds=[]))]:
        assert manager.put(route(id),json=request(version=form['version'],definition=form['definition']|values)).status_code==403,values
    assert manager.put(route(id),json=request(version=form['version'],definition=form['definition']|dict(title='Only the title'))).status_code==200
    assert get(manager,id)['definition']['title']=='Only the title'
    # The administrator can switch it on.
    form=get(client,id);assert client.put(route(id),json=request(version=form['version'],definition=form['definition']|dict(standing=True))).status_code==200


def test_new_tables_appear_in_an_existing_database_and_admissions_gain_no_limits(client):
    enable(client);create(client,role='parent');id=new(client);configure(client,id)
    with api.connect() as db:
        for table in ['school_form_ballots','school_form_anonymous','school_form_reminders']:db.execute('DROP TABLE '+table)
    assert client.get(route()).status_code==200
    with closing(sqlite3.connect(api.DB)) as db:
        assert {'school_form_ballots','school_form_anonymous','school_form_reminders'}<={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    from backend.school_forms_models import Question
    assert 'limits' not in Question(id='q',title='Document').model_dump()


def test_ending_one_child_link_keeps_form_for_other_child(client,monkeypatch):
    from test_families import save
    enable(client);person=create(client,role='parent',grants=[dict(workspace='demo',view='student',entityIds=['s1','s2'])])
    id=new(client);configure(client,id);publish(client,id);mine=signed('parent')
    assert {c['entityId'] for c in get(mine,id)['contexts']}=={'s1','s2'}
    assert save(client,person['id'],'s2',endDate='2030-01-01').status_code==200
    monkeypatch.setattr(families,'today',lambda:'2030-01-02')
    assert {c['entityId'] for c in get(mine,id)['contexts']}=={'s1'}
    assert [i['id'] for i in mine.get(route()).json()['items']]==[id]
    assert answer(mine,id,'s1').status_code==200
    assert answer(mine,id,'s2').status_code==404


def test_teacher_grant_change_keeps_survey_role_change_closes_it(client):
    enable(client);teacher=create(client,role='teacher');id=new(client);configure(client,id,roles=['teacher']);publish(client,id)
    body={k:v for k,v in teacher.items() if k!='id'};body['grants']=[dict(workspace='demo',view='teacher',entityIds=['t1','t2'])]
    assert client.put('/api/access/users/'+teacher['id'],json=body).status_code==200
    mine=signed('teacher')
    assert get(mine,id)['contexts']==[dict(entityId='',name=teacher['name'])]
    assert answer(mine,id,'').status_code==200
    body=next({k:v for k,v in u.items() if k!='id'} for u in client.get('/api/access/users').json() if u['id']==teacher['id'])
    body['role']='staff';body['grants']=[]
    changed=client.put('/api/access/users/'+teacher['id'],json=body);assert changed.status_code==200,changed.text
    assert signed('teacher').get(route(id)).status_code==404
    assert signed('teacher').get(route()).json()['items']==[]


def test_standing_form_accepts_family_added_after_publish(client):
    enable(client);create(client,role='parent');id=new(client,'application')
    configure(client,id,standing=True,questions=[dict(id='q',title='Reason',kind='text')]);publish(client,id)
    parent(client,'later','s2');later=signed('later')
    listed=later.get(route()).json()['items']
    assert [i['id'] for i in listed]==[id] and listed[0]['standing'] is True
    assert [(c['entityId'],c['status']) for c in listed[0]['contexts']]==[('s2','none')]
    sent=answer(later,id,'s2',answers={'q':'Нужна справка'});assert sent.status_code==200,sent.text
    assert later.get(route()).json()['items'][0]['contexts'][0]['status']=='submitted'
    item=client.get(route(id,'/responses')).json()['items'][0]
    assert item['entityName']=='Ученик 2 (пример)' and item['answers']=={'q':'Нужна справка'}
    assert 'Ученик 2 (пример)' in client.get(route(id,'/export')).text
    assert client.get(route(id,'/summary')).json()['responses']==1
    # A form that is not standing keeps the audience it was published to.
    other=new(client);configure(client,other);publish(client,other);parent(client,'after','s3')
    assert signed('after').get(route(other)).status_code==404


def test_standing_draft_leaves_no_trace_for_staff(client):
    enable(client);create(client,role='parent');id=new(client,'application')
    configure(client,id,standing=True,questions=[dict(id='q',title='Reason',kind='text')]);publish(client,id)
    before=get(client,id)['audienceCount'];parent(client,'later','s2');parent(client,'drafter','s3');mine=signed('later')
    saved=answer(mine,id,'s2',action='save',answers={'q':'secret draft'});assert saved.status_code==200,saved.text
    seen=get(client,id);assert seen['audienceCount']==before and seen['counts']=={}
    assert mine.get(route(id,'/answer')+'&entityId=s2').json()['answer']['answers']=={'q':'secret draft'}
    assert client.get(route(id,'/responses')).json()['items']==[]
    # The first submission is what puts the account on the list; the answer is then visible to staff.
    assert answer(mine,id,'s2',version=1,answers={'q':'sent'}).status_code==200
    assert get(client,id)['audienceCount']==before+1 and client.get(route(id,'/responses')).json()['items'][0]['answers']=={'q':'sent'}
    # Somebody who only kept a draft is not on the list, so a closed form is no longer theirs.
    assert answer(signed('drafter'),id,'s3',action='save',answers={'q':'unsent'}).status_code==200
    assert client.post(route(id,'/transition'),json=request(version=get(client,id)['version'],action='close')).status_code==200
    assert signed('drafter').get(route(id)).status_code==404 and mine.get(route(id)).status_code==200


def test_standing_form_closes_and_respects_audience(client):
    enable(client);create(client,role='parent');create(client,role='student')
    id=new(client,'application')
    configure(client,id,standing=True,questions=[dict(id='q',title='Reason',kind='text')],audience=dict(roles=['parent'],classIds=['c-demo-a']));publish(client,id)
    applicant=parent(client,'applicant','s2');parent(client,'wrong-class','s3');student=signed('student')
    assert student.get(route(id)).status_code==404 and student.get(route()).json()['items']==[]
    assert signed('wrong-class').get(route(id)).status_code==404 and answer(signed('wrong-class'),id,'s3',answers={'q':'x'},formVersion=get(client,id)['version']).status_code==404
    mine=signed('applicant');sent=answer(mine,id,'s2',answers={'q':'x'}).json()
    assert client.post(route(id,'/responses/'+sent['id']+'/review'),json=review_body(client,id,1,'returned','Fix')).status_code==200
    assert client.post(route(id,'/transition'),json=request(version=get(client,id)['version'],action='close')).status_code==200
    parent(client,'newcomer','s1')
    assert signed('newcomer').get(route(id)).status_code==404 and signed('newcomer').get(route()).json()['items']==[]
    assert answer(mine,id,'s2',version=2,answers={'q':'fixed'}).status_code==200
    form=get(client,id)
    assert client.post(route(id,'/transition'),json=request(version=form['version'],action='reopen',closesAt=(now()+timedelta(days=1)).isoformat())).status_code==422
    assert get(client,id)['version']==form['version']
    assert client.post(route(id,'/transition'),json=request(version=form['version'],action='reopen')).status_code==200
    assert get(signed('newcomer'),id)['contexts'][0]['entityId']=='s1'


def test_copy_keeps_new_switches_and_limits(client):
    enable(client);create(client,role='parent');id=new(client)
    configure(client,id,standing=True,questions=FIRST_ONLY)
    copied=client.post(route(id,'/copy'),json=request(version=get(client,id)['version']));assert copied.status_code==200
    definition=get(client,copied.json()['id'])['definition']
    assert definition['standing'] is True and definition['questions'][0]['limits']=={'First':1}


def test_class_teachers_audience_follows_the_current_link(client):
    enable(client);create(client,role='teacher');create(client,role='teacher',login='other',grants=[dict(workspace='demo',view='teacher',entityIds=['t1'])])
    set_class_teacher('c-demo-a','t2')
    id=new(client);configure(client,id,audience=dict(roles=[],classIds=[],preset='class_teachers'))
    preview=client.get(route(id,'/preview')).json();assert preview['roles']=={'teacher':1} and preview['responses']==1
    publish(client,id);leader=signed('teacher');colleague=signed('other')
    assert get(leader,id)['contexts']==[dict(entityId='',name='Сотрудник')] and answer(leader,id,'').status_code==200
    assert colleague.get(route(id)).status_code==404
    assert pending(client,id)['audience']==1
    set_class_teacher('c-demo-a','t1')
    assert leader.get(route(id)).status_code==404 and leader.get(route()).json()['items']==[]
    assert pending(client,id)['audience']==0 and pending(client,id)['pendingCount']==0
    # No class has a class teacher any more: nothing to address.
    set_class_teacher('c-demo-a',None)
    again=new(client);configure(client,again,audience=dict(roles=[],classIds=[],preset='class_teachers'))
    assert client.get(route(again,'/preview')).status_code==422 and get(client,again)['status']=='draft'


def test_teachers_and_staff_presets_resolve_to_those_roles(client):
    enable(client);create(client,role='teacher');create(client,role='staff');create(client,role='editor');create(client,role='parent')
    id=new(client);configure(client,id,audience=dict(roles=[],classIds=[],preset='teachers'));publish(client,id)
    assert get(signed('teacher'),id)['contexts'][0]['entityId']==''
    for login in ['staff','editor','parent']:assert signed(login).get(route(id)).status_code==404
    copied=client.post(route(id,'/copy'),json=request(version=get(client,id)['version'])).json()['id']
    configure(client,copied,audience=dict(roles=[],classIds=[],preset='staff'));publish(client,copied)
    for login in ['staff','editor']:assert signed(login).get(route(copied)).status_code==200
    for login in ['teacher','parent']:assert signed(login).get(route(copied)).status_code==404


def test_saved_group_is_owner_only_and_skips_expired_family_link(client,monkeypatch):
    from test_families import save
    enable(client);teacher=create(client,role='teacher');kid=parent(client,'kid','s1');group=str(uuid4())
    saved=client.put('/api/messages/saved-groups/'+group+'?workspace=demo',json=dict(version=0,name='Круг',userIds=[teacher['id'],kid['id']]))
    assert saved.status_code==200,saved.text
    id=new(client);configure(client,id,audience=dict(roles=[],classIds=[],preset='saved',savedGroupId=group));assign(client,id,teacher)
    assert client.get(route(id,'/preview')).json()['responses']==2
    assert save(client,kid['id'],endDate='2030-01-01').status_code==200
    monkeypatch.setattr(families,'today',lambda:'2030-01-02')
    preview=client.get(route(id,'/preview')).json();assert preview['unavailable']==1 and preview['responses']==1 and preview['roles']=={'teacher':1}
    # A delegated manager neither previews nor publishes somebody else's group; another administrator does not own it.
    assert signed('teacher').get(route(id,'/preview')).status_code==403
    create(client,role='admin',login='admin2');assert signed('admin2').get(route(id,'/preview')).status_code==422
    publish(client,id)
    assert answer(signed('teacher'),id,'').status_code==200
    # A group that was deleted after the audience was set cannot be published.
    other=new(client);configure(client,other,audience=dict(roles=[],classIds=[],preset='saved',savedGroupId=str(uuid4())))
    assert client.get(route(other,'/preview')).status_code==422


def test_seat_limit_holds_under_concurrent_submissions(client):
    enable(client);people=[parent(client,f'par{i}',f's{i}') for i in range(1,5)]
    id=new(client);configure(client,id,questions=[dict(id='q',title='Choose',kind='single',required=True,choices=['First','Second'],limits={'First':2})]);form=publish(client,id)
    clients=[signed(f'par{i}') for i in range(1,5)]
    with ThreadPoolExecutor(max_workers=4) as pool:
        codes=sorted(pool.map(lambda pair:answer(pair[1],id,f's{pair[0]}',formVersion=form['version']).status_code,enumerate(clients,1)))
    assert codes==[200,200,409,409]
    assert get(client,id)['seatsLeft']=={'q':{'First':0}}
    assert client.get(route(id,'/summary')).json()['questions'][0]['choices']['First']==2
    with closing(sqlite3.connect(api.DB)) as db:assert db.execute('SELECT count(*) FROM school_form_answers').fetchone()[0]==2


def test_seats_are_released_and_kept_by_status(client):
    enable(client);parent(client,'ann','s1');parent(client,'bob','s2')
    id=new(client,'application');configure(client,id,questions=FIRST_ONLY);form=publish(client,id);a=signed('ann');b=signed('bob')
    body=request(version=0,formVersion=form['version'],entityId='s1',action='submit',answers={'q':'First'})
    held=a.put(route(id,'/answer'),json=body);assert held.status_code==200,held.text
    refused=answer(b,id,'s2');assert refused.status_code==409 and '«First»' in refused.json()['detail']
    assert len(client.get(route(id,'/responses')).json()['items'])==1
    # A save reserves nothing and never fails on a full option.
    assert answer(b,id,'s2',action='save').status_code==200
    assert client.get(route(id,'/seats')).json()=={'q':{'First':0}} and b.get(route(id,'/seats')).json()=={'q':{'First':0}}
    assert answer(a,id,'s1',version=1,action='withdraw').status_code==200
    assert client.get(route(id,'/seats')).json()=={'q':{'First':1}}
    sent=answer(b,id,'s2',version=1);assert sent.status_code==200,sent.text
    assert answer(a,id,'s1',version=2).status_code==409
    # Returning keeps the seat: the holder resubmits the same option at full capacity, nobody else gets in.
    assert client.post(route(id,'/responses/'+sent.json()['id']+'/review'),json=review_body(client,id,sent.json()['version'],'returned','Fix')).status_code==200
    assert client.get(route(id,'/seats')).json()=={'q':{'First':0}} and answer(a,id,'s1',version=2).status_code==409
    again=answer(b,id,'s2',version=3);assert again.status_code==200,again.text
    # A rejection frees the seat.
    assert client.post(route(id,'/responses/'+again.json()['id']+'/review'),json=review_body(client,id,again.json()['version'],'rejected','No')).status_code==200
    assert client.get(route(id,'/seats')).json()=={'q':{'First':1}}
    retry=request(version=2,formVersion=form['version'],entityId='s1',action='submit',answers={'q':'First'})
    final=a.put(route(id,'/answer'),json=retry);assert final.status_code==200,final.text
    # Retrying a successful submission at full capacity returns the stored result, not a refusal.
    replay=a.put(route(id,'/answer'),json=retry);assert replay.status_code==200 and replay.json()==final.json()
    assert client.get(route(id,'/seats')).json()=={'q':{'First':0}}


def test_multiple_choice_needs_every_seat_and_refusal_writes_nothing(client):
    enable(client);parent(client,'ann','s1');parent(client,'bob','s2')
    questions=[dict(id='m',title='Days',kind='multiple',required=False,choices=['Mon','Tue','Wed'],limits={'Mon':1,'Tue':5})]
    id=new(client);configure(client,id,questions=questions);form=publish(client,id);a=signed('ann');b=signed('bob')
    assert answer(a,id,'s1',answers={'m':['Mon','Tue']}).status_code==200
    assert get(client,id)['seatsLeft']=={'m':{'Mon':0,'Tue':4}}
    refused=answer(b,id,'s2',answers={'m':['Tue','Mon']});assert refused.status_code==409
    assert get(client,id)['seatsLeft']=={'m':{'Mon':0,'Tue':4}}
    with closing(sqlite3.connect(api.DB)) as db:
        assert db.execute('SELECT count(*) FROM school_form_answers').fetchone()[0]==1
        assert db.execute("SELECT count(*) FROM school_form_history WHERE action='submit'").fetchone()[0]==1
    # An optional limited question left empty takes no seat; unlimited options are never refused.
    assert answer(b,id,'s2',answers={'m':['Wed']}).status_code==200 and answer(signed('ann'),id,'s1',version=1,action='withdraw').status_code==200


def test_second_parent_of_same_child_does_not_take_another_seat(client):
    enable(client);parent(client,'mum','s1');parent(client,'dad','s1')
    id=new(client);configure(client,id,questions=[dict(id='q',title='Choose',kind='single',required=True,choices=['First','Second'],limits={'First':5})]);form=publish(client,id)
    mum=signed('mum');dad=signed('dad')
    first=answer(mum,id,'s1');assert first.status_code==200
    refused=dad.put(route(id,'/answer'),json=request(version=0,formVersion=form['version'],entityId='s1',action='submit',answers={'q':'Second'}))
    assert refused.status_code==409 and 'ребёнка' in refused.json()['detail']
    assert answer(mum,id,'s1',version=1,action='withdraw').status_code==200
    assert answer(dad,id,'s1').status_code==200


def test_own_seat_survives_a_private_correction_and_stays_out_of_staff_results(client):
    enable(client);parent(client,'ann','s1');parent(client,'bob','s2')
    id=new(client,'application');configure(client,id,questions=FIRST_ONLY);form=publish(client,id);a=signed('ann');b=signed('bob')
    mine=lambda who,entity:who.get(route(id,'/answer')+'&entityId='+entity).json()['answer']
    assert mine(a,'s1')['heldAnswers']=={}
    sent=answer(a,id,'s1',formVersion=form['version']);assert sent.status_code==200
    assert mine(a,'s1')['heldAnswers']=={'q':'First'}
    assert client.post(route(id,'/responses/'+sent.json()['id']+'/review'),json=review_body(client,id,sent.json()['version'],'returned','Fix')).status_code==200
    # A correction of the last seat's option to another one is private: the applicant still holds the first option, staff still see it.
    version=mine(a,'s1')['version'];assert answer(a,id,'s1',version=version,action='save',answers={'q':'Second'}).status_code==200
    personal=mine(a,'s1');assert personal['answers']=={'q':'Second'} and personal['heldAnswers']=={'q':'First'}
    assert client.get(route(id,'/seats')).json()=={'q':{'First':0}}
    assert client.get(route(id,'/responses')).json()['items'][0]['answers']=={'q':'First'}
    # The field belongs to the owner's own read: staff results and the form itself never carry it, and another family holds nothing.
    assert 'heldAnswers' not in client.get(route(id,'/responses')).text and 'heldAnswers' not in client.get(route(id)).text and 'heldAnswers' not in b.get(route(id)).text
    assert mine(b,'s2')['heldAnswers']=={}
    # The server agrees with the screen: going back to the held option at full capacity is accepted, and a withdrawal holds nothing.
    assert answer(a,id,'s1',version=personal['version'],answers={'q':'First'}).status_code==200
    assert mine(a,'s1')['heldAnswers']=={'q':'First'}
    assert answer(a,id,'s1',version=mine(a,'s1')['version'],action='withdraw').status_code==200 and mine(a,'s1')['heldAnswers']=={}


def test_decided_application_seat_rules(client):
    enable(client);parent(client,'ann','s1');parent(client,'bob','s2')
    id=new(client,'application');configure(client,id,questions=FIRST_ONLY);form=publish(client,id);a=signed('ann');b=signed('bob')
    decide=lambda sent,action:client.post(route(id,'/responses/'+sent['id']+'/review'),json=review_body(client,id,sent['version'],action,'x'))
    # An approved application keeps its seat for the next cycle of the same applicant, and nobody else gets in.
    first=answer(a,id,'s1',formVersion=form['version']).json();assert decide(first,'approved').status_code==200
    assert answer(b,id,'s2').status_code==409
    again=answer(a,id,'s1',version=2);assert again.status_code==200,again.text
    # After a rejection the seat is free: another family takes it, and the rejected applicant competes like anyone else.
    assert decide(again.json(),'rejected').status_code==200 and client.get(route(id,'/seats')).json()=={'q':{'First':1}}
    assert answer(b,id,'s2').status_code==200
    refused=answer(a,id,'s1',version=4);assert refused.status_code==409 and '«First»' in refused.json()['detail']


def test_pending_list_keeps_drafts_private_and_follows_answers(client):
    enable(client);parent(client,'pa1','s1');parent(client,'pa1b','s1');parent(client,'pa2','s2');reviewer=create(client,role='staff')
    id=new(client);configure(client,id);assign(client,id,reviewer,'review');publish(client,id)
    one=signed('pa1');two=signed('pa2');second=signed('pa1b')
    state=pending(client,id);assert state['pendingCount']==3 and state['audience']==3 and state['answered']==0 and state['canRemind'] is True
    assert {i['name'] for i in state['items']}=={'Родитель pa1','Родитель pa1b','Родитель pa2'}
    assert all(set(i)=={'name','role','entityName','className','remindedToday'} for i in state['items'])
    assert answer(one,id,'s1',action='save').status_code==200
    assert pending(client,id)['pendingCount']==3
    sent=answer(one,id,'s1',version=1);assert sent.status_code==200
    # Parents answer separately, but the second parent of an answered child is not chased.
    state=pending(client,id);assert [i['name'] for i in state['items']]==['Родитель pa2'] and state['answered']==2
    assert answer(one,id,'s1',version=2,action='withdraw').status_code==200
    assert pending(client,id)['pendingCount']==3
    assert answer(second,id,'s1').status_code==200 and pending(client,id)['pendingCount']==1
    assert 'userId' not in client.get(route(id,'/pending')+'&workspace=demo').text and '"id"' not in client.get(route(id,'/pending')).text
    for who in [one,two]:assert who.get(route(id,'/pending')).status_code==404
    delegate=signed('staff')
    assert delegate.get(route(id,'/pending')).status_code==200 and pending(delegate,id)['canRemind'] is False
    assert remind(delegate,id).status_code==404
    page=pending(client,id,limit=1,offset=0);assert len(page['items'])==1 and page['nextOffset'] is None


def test_pending_pages_in_class_order(client):
    enable(client);parent(client,'late','s3');parent(client,'early','s1');parent(client,'middle','s2')
    id=new(client);configure(client,id);publish(client,id)
    page=pending(client,id,limit=2);assert [i['name'] for i in page['items']]==['Родитель early','Родитель middle'] and page['nextOffset']==2
    rest=pending(client,id,limit=2,offset=page['nextOffset']);assert [i['name'] for i in rest['items']]==['Родитель late'] and rest['nextOffset'] is None


def test_reminder_reaches_only_pending_once_a_day(client,monkeypatch):
    enable(client);waiting=parent(client,'waiting','s1');done=parent(client,'done','s2')
    id=new(client);configure(client,id);publish(client,id)
    assert answer(signed('done'),id,'s2').status_code==200
    body=dict(requestId=str(uuid4()));first=client.post(route(id,'/remind'),json=body)
    assert first.status_code==200 and first.json()=={'reminded':1,'skipped':0}
    assert count_notices(waiting['id'],'forms.reminder')==1 and count_notices(done['id'],'forms.reminder')==0
    again=remind(client,id);assert again.json()=={'reminded':0,'skipped':1}
    assert count_notices(waiting['id'],'forms.reminder',unread=False)==1
    assert client.post(route(id,'/remind'),json=body).json()==first.json()
    assert pending(client,id)['remindedToday']==1 and pending(client,id)['items'][0]['remindedToday'] is True
    future=now()+timedelta(days=1)
    with monkeypatch.context() as patch:
        patch.setattr('backend.school_forms.now',lambda:future)
        assert remind(client,id).json()['reminded']==1
    assert count_notices(waiting['id'],'forms.reminder')==1
    assert client.post(route(id,'/transition'),json=request(version=get(client,id)['version'],action='close')).status_code==200
    assert remind(client,id).status_code==409 and pending(client,id)['canRemind'] is False


def test_list_is_readable_but_not_remindable_before_opening_and_absent_for_standing_forms(client):
    enable(client);waiting=parent(client,'waiting','s1')
    soon=new(client);configure(client,soon,opensAt=(now()+timedelta(days=1)).isoformat());publish(client,soon)
    state=pending(client,soon);assert state['applicable'] is True and state['pendingCount']==1 and state['canRemind'] is False
    assert remind(client,soon).status_code==409
    standing=new(client,'application');configure(client,standing,standing=True,questions=[dict(id='q',title='Reason',kind='text')]);publish(client,standing)
    state=pending(client,standing)
    assert state['applicable'] is False and state['items']==[] and state['pendingCount']==0 and state['audience']==0 and state['canRemind'] is False
    refused=remind(client,standing);assert refused.status_code==409
    assert count_notices(waiting['id'],'forms.reminder',unread=False)==0


def test_reminder_names_only_the_unanswered_children(client):
    enable(client);mum=parent(client,'mum','s1','s2');id=new(client);configure(client,id);publish(client,id)
    assert answer(signed('mum'),id,'s1').status_code==200
    assert remind(client,id).json()['reminded']==1
    with closing(sqlite3.connect(api.DB)) as db:
        target=json.loads(db.execute("SELECT target FROM notifications WHERE user_id=? AND action='forms.reminder'",(mum['id'],)).fetchone()[0])
    assert [s['id'] for s in target['students']]==['s2'] and target['recordId']==id and target['module']=='forms'


def test_reminder_opens_for_recipient_and_rolls_back(client,monkeypatch):
    from test_families import save
    enable(client);waiting=parent(client,'waiting','s1');parent(client,'other','s2')
    id=new(client);configure(client,id);publish(client,id)
    original=api.notifications.deliver
    def fail(*args,**kwargs):raise RuntimeError('transport unavailable')
    monkeypatch.setattr(api.notifications,'deliver',fail)
    with pytest.raises(RuntimeError):remind(client,id)
    monkeypatch.setattr(api.notifications,'deliver',original)
    with closing(sqlite3.connect(api.DB)) as db:assert db.execute('SELECT count(*) FROM school_form_reminders').fetchone()[0]==0
    assert remind(client,id).json()['reminded']==2
    notice=lambda login:next(i for i in signed(login).get('/api/notifications/inbox').json()['items'] if i['action']=='forms.reminder')['id']
    mine=signed('waiting');opened=mine.post(f"/api/notifications/{notice('waiting')}/open");assert opened.status_code==200 and opened.json()['formId']==id
    assert mine.post(f"/api/notifications/{notice('other')}/open").status_code==404
    # An entry about a child closes once the family link to the child ends.
    assert save(client,waiting['id'],'s1',status='revoked').status_code==200
    assert signed('waiting').post(f"/api/notifications/{notice('waiting')}/open").status_code==404


def test_anonymous_survey_stores_no_link_between_person_and_answer(client):
    enable(client);people=[parent(client,f'voter{i}','s1') for i in range(ANONYMOUS_MIN)]
    id=new(client);configure(client,id,anonymous=True,questions=[dict(id='q',title='Comment',kind='text')]);publish(client,id)
    for i in range(ANONYMOUS_MIN):
        sent=answer(signed(f'voter{i}'),id,'s1',answers={'q':f'marker-{i}'});assert sent.status_code==200,sent.text
        assert sent.json()=={'id':'','version':1,'status':'submitted'}
    found=0
    with closing(sqlite3.connect(api.DB)) as db:
        for table, in db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
            for row in db.execute(f'SELECT * FROM "{table}"').fetchall():
                text=str(row)
                if 'marker-' not in text:continue
                found+=1
                assert not any(p['id'] in text or p['login'] in text or p['name'] in text for p in people),table
    assert found==ANONYMOUS_MIN
    page=client.get(route(id,'/responses')).json()
    assert page['withheld'] is False and sorted(a['answers']['q'] for a in page['items'])==[f'marker-{i}' for i in range(ANONYMOUS_MIN)]
    assert all(set(item)=={'id','answers'} for item in page['items'])
    text=client.get(route(id,'/export')).text
    assert 'Отправитель' not in text and 'marker-0' in text
    for person in people:
        assert person['name'] not in text
        for suffix in ['/responses','/summary','/pending']:assert person['name'] not in client.get(route(id,suffix)).text
    assert client.get(route(id,'/summary')).json()['responses']==ANONYMOUS_MIN


def test_anonymous_results_open_at_minimum_and_pending_has_no_names(client):
    enable(client)
    for i in range(ANONYMOUS_MIN-1):parent(client,f'voter{i}','s1')
    small=new(client);configure(client,small,anonymous=True)
    refused=client.post(route(small,'/transition'),json=request(version=client.get(route(small,'/preview')).json()['version'],action='publish',audienceHash=client.get(route(small,'/preview')).json()['audienceHash']))
    assert refused.status_code==422 and get(client,small)['status']=='draft'
    last=[parent(client,f'voter{i}','s1') for i in range(ANONYMOUS_MIN-1,ANONYMOUS_MIN+1)]
    id=new(client);configure(client,id,anonymous=True);publish(client,id)
    for i in range(ANONYMOUS_MIN-1):assert answer(signed(f'voter{i}'),id,'s1').status_code==200
    page=client.get(route(id,'/responses')).json()
    assert page['withheld'] is True and page['items']==[] and page['minimum']==ANONYMOUS_MIN
    assert client.get(route(id,'/summary')).json()['questions']==[] and client.get(route(id,'/export')).status_code==409
    assert answer(signed(f'voter{ANONYMOUS_MIN-1}'),id,'s1').status_code==200
    assert len(client.get(route(id,'/responses')).json()['items'])==ANONYMOUS_MIN
    assert client.get(route(id,'/summary')).json()['questions'][0]['choices']['First']==ANONYMOUS_MIN and client.get(route(id,'/export')).status_code==200
    state=pending(client,id)
    assert state['anonymous'] is True and state['items']==[] and state['nextOffset'] is None and state['answered']==ANONYMOUS_MIN and state['pendingCount']==1
    sent=remind(client,id);assert sent.status_code==200 and set(sent.json())=={'reminded','skipped'} and sent.json()['reminded']==1
    assert count_notices(last[1]['id'],'forms.reminder')==1 and all(count_notices(p['id'],'forms.reminder')==0 for p in [last[0]])
    # A manager who is also a recipient sees nothing more than the administrator.
    assert get(client,id)['counts']=={'submitted':ANONYMOUS_MIN}


def test_anonymous_answer_is_final(client):
    admin=enable(client);people=[parent(client,f'voter{i}','s1') for i in range(ANONYMOUS_MIN)]
    id=new(client);configure(client,id,anonymous=True);publish(client,id);mine=signed('voter0')
    assert answer(mine,id,'s1',action='save').status_code==422 and answer(mine,id,'s1',action='withdraw').status_code==422
    assert mine.get(route(id,'/answer')+'&entityId=s1').json()['answer']['status']=='draft'
    body=request(version=0,formVersion=get(client,id)['version'],entityId='s1',action='submit',answers={'q':'First'})
    first=mine.put(route(id,'/answer'),json=body);assert first.status_code==200
    # A retry with the same identifier returns the first result even if the answer differs: the stored request holds no answer.
    assert mine.put(route(id,'/answer'),json=dict(body,answers={'q':'Second'})).json()==first.json()
    refused=answer(mine,id,'s1');assert refused.status_code==409 and 'уже ответили' in refused.json()['detail']
    personal=mine.get(route(id,'/answer')+'&entityId=s1').json()
    assert personal['answer']['status']=='submitted' and personal['answer']['answers']=={} and personal['history']==[]
    assert count_notices(admin['id'])==0
    assert mine.get(route()).json()['items'][0]['contexts'][0]['status']=='submitted'
    with closing(sqlite3.connect(api.DB)) as db:
        assert db.execute('SELECT count(*) FROM school_form_answers').fetchone()[0]==0 and db.execute('SELECT count(*) FROM school_form_history WHERE answer_id!=?',('',)).fetchone()[0]==0
        assert db.execute('SELECT count(*) FROM school_form_anonymous').fetchone()[0]==1
        assert 'Second' not in ' '.join(str(r) for r in db.execute('SELECT * FROM school_form_requests'))


def test_anonymous_ballot_keeps_the_guards_of_named_answers(client):
    enable(client);[parent(client,f'voter{i}','s1') for i in range(ANONYMOUS_MIN+1)]
    questions=[dict(id='q',title='Choose',kind='single',required=True,choices=['First','Second']),dict(id='note',title='Note',kind='text')]
    id=new(client);configure(client,id,anonymous=True,questions=questions);publish(client,id);mine=signed('voter0');version=get(client,id)['version']
    # A refusal writes nothing: the same account can still cast its ballot afterwards.
    assert answer(mine,id,'s1',answers={'note':'x'}).status_code==422
    assert answer(mine,id,'s1',answers={'q':'Third'}).status_code==422
    assert answer(mine,id,'s1',formVersion=version+99).status_code==409
    with closing(sqlite3.connect(api.DB)) as db:assert db.execute('SELECT count(*) FROM school_form_ballots').fetchone()[0]==0
    for i in range(ANONYMOUS_MIN):
        sent=answer(signed(f'voter{i}'),id,'s1',answers={'q':'First','note':'=SUM(1,2)' if i==0 else 'plain'});assert sent.status_code==200,sent.text
    # Spreadsheet formulas are neutralised in the anonymous CSV as in the named one.
    assert "'=SUM(1,2)" in client.get(route(id,'/export')).text
    assert client.post(route(id,'/transition'),json=request(version=get(client,id)['version'],action='close')).status_code==200
    late=answer(signed(f'voter{ANONYMOUS_MIN}'),id,'s1');assert late.status_code==409,late.text
    assert client.get(route(id,'/summary')).json()['responses']==ANONYMOUS_MIN


def test_manager_publishes_a_preset_audience_but_not_a_saved_group(client):
    enable(client);create(client,role='teacher');manager=create(client,role='staff');create(client,role='editor',login='editor')
    id=new(client);configure(client,id,audience=dict(roles=[],classIds=[],preset='teachers'));assign(client,id,manager)
    boss=signed('staff');publish(boss,id)
    assert signed('teacher').get(route(id)).status_code==200 and signed('editor').get(route(id)).status_code==404


def test_forms_published_before_these_switches_keep_working(client):
    enable(client);create(client,role='parent');reviewer=create(client,role='staff')
    id=new(client);configure(client,id);assign(client,id,reviewer,'review');publish(client,id)
    with api.connect() as db:
        db.execute("UPDATE school_forms SET document=json_remove(document,'$.audience.preset','$.audience.savedGroupId','$.standing','$.anonymous','$.questions[0].limits') WHERE id=?",(id,))
    mine=signed('parent');form=get(mine,id)
    assert 'standing' not in form['definition'] and form['seatsLeft']=={}
    assert [i['standing'] for i in mine.get(route()).json()['items']]==[False]
    assert answer(mine,id,formVersion=form['version']).status_code==200
    staff=signed('staff')
    assert staff.get(route(id,'/responses')).json()['items'][0]['status']=='submitted' and staff.get(route(id,'/summary')).json()['responses']==1
    assert pending(staff,id)['pendingCount']==0 and staff.get(route(id,'/export')).status_code==200
