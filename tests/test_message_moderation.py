from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from backend import api
from test_access import client, create, signed
from test_messages import setup, url, detail, payload
from test_message_files_groups import group, upload, download
from test_message_management import add


def report(user, post, **values):
    return user.post(url(f'posts/{post}/report'),json=dict(requestId=str(uuid4()),reason='Проверьте это сообщение.',shareEvidence=True)|values)


def case(user, id): return user.get(url('reports/'+id))


def decision(user, id, action='hide', **values):
    state=case(user,id).json()
    return user.post(url('reports/'+id+'/decision'),json=dict(requestId=str(uuid4()),version=state['version'],postVersion=state['postVersion'],action=action,note='Проверено администратором.')|values)


def deactivate(client,user):
    assert client.put('/api/access/users/'+user['id'],json={k:v for k,v in user.items() if k!='id'}|dict(active=False)).status_code==200


def evidence(user, id, file): return user.get(url('reports/'+id+'/files/'+file))


def seed(client):
    people=setup(client);parent=signed('parent');teacher=signed('teacher');thread=group(teacher)
    draft=str(uuid4());file=upload(parent,draft,thread).json()['id']
    assert parent.post(url('threads/'+thread+'/reply'),json=dict(requestId=str(uuid4()),version=1,text='Уникальный спорный текст',draftId=draft,fileIds=[file])).status_code==200
    post=detail(teacher,thread)['posts'][-1]['id']
    return people,parent,teacher,thread,post,file


def test_report_consent_privacy_scope_and_retries(client):
    people,parent,teacher,thread,post,file=seed(client)
    assert report(teacher,post,shareEvidence=False).status_code==422
    assert report(teacher,post,reason=' ').status_code==422
    assert report(parent,post).status_code==422
    for outside in [client,signed('staff')]: assert report(outside,post).status_code==404
    request=str(uuid4());response=report(teacher,post,requestId=request);assert response.status_code==200,response.text
    id=response.json()['id']
    assert report(teacher,post,requestId=request).json()['id']==id
    assert report(teacher,post,requestId=request,reason='Different').status_code==409
    assert report(teacher,post).status_code==409
    admin=case(client,id).json();assert admin['evidence']['text']=='Уникальный спорный текст'
    assert set(admin['evidence'])=={'threadId','title','kind','authorId','author','created','text','files'}
    assert evidence(client,id,file).content==b'Private attachment'
    assert evidence(teacher,id,file).status_code==403
    assert evidence(client,id,'not-reported').status_code==404
    assert 'evidence' not in case(teacher,id).json()
    assert case(parent,id).status_code==404 and parent.get(url('reports')).json()['items']==[]
    assert client.get(url('threads/'+thread)).status_code==404
    assert download(client,file).status_code==404
    assert client.get('/api/messages/reports/'+id+'?workspace=school').status_code==404
    with api.connect() as db:
        actions=[r[0] for r in db.execute("SELECT action FROM audit WHERE action LIKE 'message.evidence%'")]
        assert 'message.evidence_read' in actions and 'message.evidence_file_read' in actions
        assert not db.execute("SELECT 1 FROM audit WHERE detail LIKE '%Уникальный%'").fetchone()


def test_hide_restore_search_files_versions_and_report_history(client):
    _,parent,teacher,thread,post,file=seed(client)
    id=report(teacher,post).json()['id'];original=detail(parent,thread)
    assert parent.get(url(q='Уникальный')).json()['items']
    state=case(client,id).json();request=str(uuid4())
    body=dict(requestId=request,version=state['version'],postVersion=state['postVersion'],action='hide',note='Скрыто после проверки')
    for _ in range(2): assert client.post(url('reports/'+id+'/decision'),json=body).status_code==200
    after=detail(parent,thread);assert after['version']==original['version']+1
    hidden=after['posts'][-1];assert hidden['hidden'] is True and hidden['files']==[] and 'Уникальный' not in hidden['text']
    assert parent.get(url(q='Уникальный')).json()['items']==[]
    for user in [parent,teacher]: assert download(user,file).status_code==404
    assert evidence(client,id,file).status_code==200
    assert decision(teacher,id).status_code==403
    assert decision(client,id,'restore',postVersion=0).status_code==409
    assert decision(client,id,'restore').status_code==200
    assert detail(parent,thread)['posts'][-1]['text']=='Уникальный спорный текст'
    assert download(parent,file).status_code==200
    assert len(case(teacher,id).json()['history'])==2
    assert parent.get(url(q='Уникальный')).json()['items']


def test_two_reports_share_one_moderation_version_and_race(client):
    _,parent,teacher,thread,post,file=seed(client)
    first=report(teacher,post).json()['id'];second=report(signed('student'),post).json()['id']
    bodies=[dict(requestId=str(uuid4()),version=1,postVersion=0,action='hide',note='Reviewed') for _ in range(2)]
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda pair:client.post(url('reports/'+pair[0]+'/decision'),json=pair[1]).status_code,zip([first,second],bodies)))
    assert sorted(results)==[200,409]
    assert all(case(client,id).json()['postVersion']==1 for id in [first,second])
    assert decision(client,first,'dismiss').status_code==200
    assert download(parent,file).status_code==404  # Dismiss does not silently unhide another report's decision.
    assert decision(client,second,'restore').status_code==200


def test_author_admin_cannot_decide_own_report_and_withdrawn_evidence_survives(client):
    people=setup(client);parent=signed('parent')
    body=dict(requestId=str(uuid4()),kind='announcement',title='Объявление',text='Материалы для проверки',roles=['parent'],schoolwide=True)
    thread=client.post(url(),json=body).json()['id'];post=detail(parent,thread)['posts'][0]['id']
    id=report(parent,post).json()['id']
    assert not case(client,id).json()['canReview']
    assert decision(client,id).status_code==403
    create(client,'admin',login='second-admin');admin=signed('second-admin')
    assert client.post(url('threads/'+thread+'/change'),json=dict(version=1,action='withdraw')).status_code==200
    assert parent.get(url('threads/'+thread)).status_code==404
    assert case(parent,id).status_code==200
    assert decision(admin,id).status_code==200
    assert case(admin,id).json()['evidence']['text']=='Материалы для проверки'


def test_report_and_decision_roll_back_notifications(client,monkeypatch):
    _,parent,teacher,thread,post,file=seed(client)
    original=api.notifications.deliver
    def fail(*args,**kwargs): raise RuntimeError('notification transaction failed')
    monkeypatch.setattr(api.notifications,'deliver',fail)
    with pytest.raises(RuntimeError): report(teacher,post)
    assert teacher.get(url('reports')).json()['items']==[]
    monkeypatch.setattr(api.notifications,'deliver',original)
    id=report(teacher,post).json()['id'];before=detail(parent,thread)
    monkeypatch.setattr(api.notifications,'deliver',fail)
    with pytest.raises(RuntimeError): decision(client,id)
    assert detail(parent,thread)==before and case(client,id).json()['status']=='open'
    assert download(parent,file).status_code==200


def talk(teacher,parent,thread,count,clock=None):
    """The teacher writes `count` posts a minute apart (when a clock is given); returns their ids as the parent sees them."""
    from datetime import timedelta
    for i in range(count):
        assert teacher.post(url('threads/'+thread+'/reply'),json=dict(requestId=str(uuid4()),text=f'Запись {i}')).status_code==200
        if clock:clock[0]+=timedelta(minutes=1)
    return [p['id'] for p in detail(parent,thread)['posts'][-count:]]


def test_reports_are_rate_limited_per_hour(client,monkeypatch):
    from datetime import timedelta
    from backend.access import now
    clock=[now()]
    for module in ['message_moderation','messages']:monkeypatch.setattr('backend.'+module+'.now',lambda:clock[0])
    setup(client);teacher=signed('teacher');parent=signed('parent');thread=group(teacher)
    posts=talk(teacher,parent,thread,30,clock);codes=[]
    for post in posts:
        codes.append(report(parent,post).status_code)
        if codes[-1]==429:break
    assert codes[0]==200 and codes[-1]==429 and set(codes[:-1])=={200}
    clock[0]+=timedelta(days=1)
    assert report(parent,posts[len(codes)-1]).status_code==200


def test_report_list_is_paged_by_offset_and_filtered_by_status(client,monkeypatch):
    from datetime import timedelta
    from backend.access import now
    clock=[now()];monkeypatch.setattr('backend.message_moderation.now',lambda:clock[0])
    setup(client);teacher=signed('teacher');parent=signed('parent');thread=group(teacher)
    for post in talk(teacher,parent,thread,4):
        assert report(parent,post).status_code==200;clock[0]+=timedelta(days=1)
    everything=[r['id'] for r in parent.get(url('reports')).json()['items']];assert len(everything)==4
    assert [r['id'] for r in parent.get(url('reports',offset=1)).json()['items']]==everything[1:]
    assert [r['id'] for r in parent.get(url('reports',status='open')).json()['items']]==everything
    assert parent.get(url('reports',status='dismissed')).json()['items']==[]


def test_reports_list_shows_only_own_reports(client):
    setup(client);teacher=signed('teacher');parent=signed('parent');thread=group(teacher)
    made=report(parent,talk(teacher,parent,thread,1)[0]);assert made.status_code==200,made.text
    assert [r['id'] for r in parent.get(url('reports')).json()['items']]==[made.json()['id']]
    for outsider in [signed('student'),signed('other')]:assert outsider.get(url('reports')).json()['items']==[]


def test_former_member_reports_only_seen_posts(client):
    people,parent,teacher,thread,post,file=seed(client)
    seen=detail(teacher,thread)['posts'][0]['id']
    assert teacher.post(url('threads/'+thread+'/participants'),json=dict(version=1,userId=people['parent']['id'],action='remove')).status_code==200
    assert teacher.post(url('threads/'+thread+'/reply'),json=dict(requestId=str(uuid4()),text='Позже')).status_code==200
    later=detail(teacher,thread)['posts'][-1]['id']
    assert detail(parent,thread)['readOnly']
    # A removed participant may still report what they saw before the removal, never what came after.
    assert report(parent,later).status_code==404
    assert report(parent,seen).status_code==200


def test_backup_restores_hidden_evidence_and_decisions(client,tmp_path,monkeypatch):
    from tools.backup_school import create_backup
    _,parent,teacher,thread,post,file=seed(client)
    id=report(teacher,post).json()['id'];assert decision(client,id).status_code==200
    source=api.DB;target=tmp_path/'moderation-backup';create_backup(source,target)
    monkeypatch.setattr(api,'DB',target/source.name)
    assert evidence(client,id,file).content==b'Private attachment'
    assert download(parent,file).status_code==404
    assert case(client,id).json()['history'][0]['action']=='hide'
    assert decision(client,id,'restore').status_code==200
    assert download(parent,file).content==b'Private attachment'


def test_recover_only_unavailable_manager_to_existing_eligible_member(client):
    people=setup(client);teacher=signed('teacher');parent=signed('parent');thread=group(teacher)
    successor=create(client,'teacher',login='successor');new=signed('successor')
    assert add(teacher,thread,[successor['id']],detail(teacher,thread)['version']).status_code==200
    body=dict(requestId=str(uuid4()),version=detail(teacher,thread)['version'],userId=successor['id'],reason='Учитель больше не работает в школе.')
    assert client.post(url('recovery/'+thread),json=body).status_code==409
    deactivate(client,people['teacher'])
    before=api.read('demo').model_dump()
    assert detail(parent,thread)['readOnly']
    assert parent.get(url('recovery')).status_code==403
    item=client.get(url('recovery')).json()['items'][0]
    assert item['id']==thread and {c['id'] for c in item['candidates']}=={successor['id']}
    body=body|dict(version=item['version'])
    for uid in [people['parent']['id'],client.get('/api/auth/status').json()['user']['id']]:
        assert client.post(url('recovery/'+thread),json=body|dict(userId=uid)).status_code==422
    for _ in range(2): assert client.post(url('recovery/'+thread),json=body).status_code==200
    assert client.get(url('recovery')).json()['items']==[]
    assert client.get(url('threads/'+thread)).status_code==404
    value=detail(new,thread);assert value['canManage'] and value['authorId']==people['teacher']['id']
    assert detail(parent,thread)['posts'][-1]['text'].endswith(body['reason'])
    assert api.read('demo').model_dump()==before
    # The recovery notice reaches the members who are linked now: it stays readable after the link to the child is lost.
    state=api.read('demo')
    for pupil in state.students:
        if pupil.id=='s1':pupil.classId='c-demo-d'
    api.save('demo',state)
    frozen=detail(parent,thread);assert frozen['readOnly'] and frozen['posts'][-1]['text'].endswith(body['reason'])


def deactivated_manager_with_successor(client):
    people=setup(client);teacher=signed('teacher');thread=group(teacher)
    successor=create(client,'teacher',login='successor')
    assert add(teacher,thread,[successor['id']],detail(teacher,thread)['version']).status_code==200
    return people,teacher,thread,successor


def test_recovery_refuses_a_candidate_who_was_removed(client):
    people,teacher,thread,successor=deactivated_manager_with_successor(client)
    assert teacher.post(url('threads/'+thread+'/participants'),json=dict(version=detail(teacher,thread)['version'],userId=successor['id'],action='remove')).status_code==200
    deactivate(client,people['teacher'])
    item=client.get(url('recovery')).json()['items'][0];assert item['candidates']==[]
    assert client.post(url('recovery/'+thread),json=dict(requestId=str(uuid4()),version=item['version'],userId=successor['id'],reason='Восстановление')).status_code==422


def test_recovery_rechecks_version_scope_and_rolls_back_on_failure(client,monkeypatch):
    people,teacher,thread,successor=deactivated_manager_with_successor(client)
    deactivate(client,people['teacher'])
    version=lambda:client.get(url('recovery')).json()['items'][0]['version']
    body=lambda **extra:dict(requestId=str(uuid4()),version=version(),userId=successor['id'],reason='Восстановление')|extra
    assert client.post(url('recovery/'+thread),json=body(version=version()-1)).status_code==409
    before=version()
    def fail(*args,**kwargs): raise RuntimeError('notification transaction failed')
    with monkeypatch.context() as patch:
        patch.setattr(api.notifications,'deliver',fail)
        with pytest.raises(RuntimeError): client.post(url('recovery/'+thread),json=body())
    assert version()==before
    state=api.read('demo')
    for w in state.workloads:
        if w.teacherId=='t2':w.teacherId='t1'
        w.additionalTeacherIds=[t for t in w.additionalTeacherIds if t!='t2']
    api.save('demo',state)
    assert client.post(url('recovery/'+thread),json=body()).status_code==422
