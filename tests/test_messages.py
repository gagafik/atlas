from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

from backend import api
from test_access import client,enable,create,signed


def setup(client):
    enable(client)
    parent=create(client,'parent');teacher=create(client,'teacher');pupil=create(client,'student')
    other=create(client,'parent',login='other',grants=[dict(workspace='demo',view='student',entityIds=['s2'])])
    unrelated=create(client,'teacher',login='staff',grants=[dict(workspace='demo',view='teacher',entityIds=['t1'])])
    return dict(parent=parent,teacher=teacher,student=pupil,other=other,staff=unrelated)


def url(path='threads',**params):
    from urllib.parse import urlencode
    return '/api/messages/'+path+'?'+urlencode(dict(workspace='demo',**params))


def payload(target,**kwargs):
    return dict(requestId=str(uuid4()),title='Вопрос по уроку',text='Здравствуйте, уточните задание.',recipientId=target,studentId='s1',**kwargs)


def detail(client,id,**params):
    result=client.get(url('threads/'+id,**params));assert result.status_code==200,result.text
    return result.json()


def test_direct_reply_read_search_and_idempotency(client):
    people=setup(client);parent=signed('parent');teacher=signed('teacher')
    body=payload(people['teacher']['id'])
    sent=parent.post(url(),json=body);assert sent.status_code==200,sent.text
    id=sent.json()['id']
    assert parent.post(url(),json=body).json()['id']==id
    assert parent.post(url(),json=body|dict(text='Different')).status_code==409
    value=detail(teacher,id);assert value['canReply'] and len(value['posts'])==1
    inbox=teacher.get(url(unreadOnly=True)).json();assert len(inbox['items'])==1
    assert teacher.put(url('threads/'+id+'/read'),json=dict(throughId=value['latestId'])).status_code==200
    assert teacher.get(url(unreadOnly=True)).json()['items']==[]
    reply=dict(requestId=str(uuid4()),version=value['version'],text='ПОДГОТОВЬТЕ пример.')
    assert teacher.post(url('threads/'+id+'/reply'),json=reply).status_code==200
    assert teacher.post(url('threads/'+id+'/reply'),json=reply).status_code==200
    assert len(detail(parent,id)['posts'])==2
    assert len(parent.get(url(q='подготовьте')).json()['items'])==1
    assert parent.get('/api/notifications/inbox').json()['items'][0]['action']=='message.received'


def test_contacts_and_thread_boundaries(client):
    people=setup(client);parent=signed('parent');other=signed('other')
    contacts=parent.get(url('contacts',studentId='s1')).json()['items']
    assert people['teacher']['id'] in {u['id'] for u in contacts}
    assert people['other']['id'] not in {u['id'] for u in contacts}
    assert all(set(u)=={'id','name','role'} for u in contacts)
    assert parent.get(url('contacts',studentId='s2')).status_code==403
    assert parent.post(url(),json=payload(people['other']['id'])).status_code==403
    assert parent.post(url(),json=payload(people['teacher']['id'])|dict(studentId='s2')).status_code==403
    id=parent.post(url(),json=payload(people['teacher']['id'])).json()['id']
    for user in [other,client,signed('student')]:
        assert user.get(url('threads/'+id)).status_code==404
    assert parent.get('/api/messages/threads/'+id+'?workspace=school').status_code==403
    assert other.get(url(q='Вопрос')).json()['items']==[]


def test_account_grants_and_changed_teacher_relationship_revoke_access(client):
    people=setup(client);parent=signed('parent');teacher=signed('teacher')
    id=parent.post(url(),json=payload(people['teacher']['id'])).json()['id']
    # Changing the current teaching assignment invalidates cached relation checks.
    state=api.read('demo')
    for workload in state.workloads:
        if workload.teacherId=='t2':workload.teacherId='t1'
        workload.additionalTeacherIds=[t for t in workload.additionalTeacherIds if t!='t2']
    api.save('demo',state)
    assert parent.get(url('threads/'+id)).status_code==404
    assert teacher.get(url('threads/'+id)).status_code==404
    assert parent.get(url()).json()['items']==[]


def test_announcement_private_audience_acknowledgement_and_withdrawal(client):
    people=setup(client);teacher=signed('teacher');parent=signed('parent');pupil=signed('student')
    options=teacher.get(url('options')).json();group=options['groups'][0]['id']
    body=dict(requestId=str(uuid4()),kind='announcement',title='Экскурсия',text='Подтвердите ознакомление.',roles=['parent','student'],groupIds=[group],requireAck=True)
    preview=teacher.post(url('preview'),json=body);assert preview.status_code==200,preview.text
    assert preview.json()['count']>=2
    sent=teacher.post(url(),json=body);assert sent.status_code==200,sent.text
    id=sent.json()['id'];value=detail(parent,id)
    assert value['participants']==[] and not value['canReply'] and value['requireAck']
    assert parent.post(url('threads/'+id+'/reply'),json=dict(requestId=str(uuid4()),version=1,text='Reply')).status_code==409
    assert parent.put(url('threads/'+id+'/read'),json=dict(throughId=value['latestId'])).status_code==200
    assert not detail(parent,id)['acknowledged']
    assert parent.post(url('threads/'+id+'/change'),json=dict(version=1,action='acknowledge')).status_code==200
    assert detail(parent,id)['acknowledged'] and not detail(pupil,id)['acknowledged']
    assert any(p['acknowledged'] for p in detail(teacher,id)['participants'])
    assert parent.post(url('threads/'+id+'/change'),json=dict(version=1,action='withdraw')).status_code==403
    assert teacher.post(url('threads/'+id+'/change'),json=dict(version=1,action='withdraw')).status_code==200
    assert parent.get(url('threads/'+id)).status_code==404
    assert parent.get(url()).json()['items']==[]
    assert detail(teacher,id)['status']=='withdrawn'


def test_announcement_scope_schoolwide_and_account_changes(client):
    people=setup(client);teacher=signed('teacher');parent=signed('parent')
    body=dict(requestId=str(uuid4()),kind='announcement',title='Собрание',text='В пятницу.',roles=['parent'],schoolwide=True)
    assert teacher.post(url(),json=body).status_code==403
    assert parent.post(url(),json=body).status_code==403
    id=client.post(url(),json=body).json()['id']
    account=people['parent'];update={k:v for k,v in account.items() if k!='id'};update['grants'][0]['entityIds']=['s3']
    response=client.put('/api/access/users/'+account['id'],json=update)
    # Use another existing pupil if this seed doesn't have s3.
    assert response.status_code==200,response.text
    assert parent.get(url('threads/'+id)).status_code==401
    assert signed('parent').get(url('threads/'+id)).status_code==404


def test_reply_race_close_and_monotonic_read(client):
    people=setup(client);parent=signed('parent');teacher=signed('teacher')
    id=parent.post(url(),json=payload(people['teacher']['id'])).json()['id'];first=detail(parent,id)
    with ThreadPoolExecutor(max_workers=2) as pool:
        a=pool.submit(teacher.post,url('threads/'+id+'/reply'),json=dict(requestId=str(uuid4()),version=1,text='Ответ'))
        b=pool.submit(parent.post,url('threads/'+id+'/change'),json=dict(version=1,action='close'))
        # Replies never lock the conversation: both win, or the close came first and the reply is refused.
        codes=sorted([a.result().status_code,b.result().status_code]);assert codes in ([200,200],[200,409]),codes
    assert any(p['text']=='Ответ' for p in detail(parent,id)['posts'])==(a.result().status_code==200)
    current=detail(parent,id)
    assert parent.post(url('threads/'+id+'/change'),json=dict(version=current['version'],action='close')).status_code==200
    current=detail(parent,id)
    assert not current['canReply']
    assert teacher.post(url('threads/'+id+'/reply'),json=dict(requestId=str(uuid4()),version=current['version'],text='Поздний ответ')).status_code==409
    assert parent.post(url('threads/'+id+'/change'),json=dict(version=current['version'],action='reopen')).status_code==200
    assert teacher.put(url('threads/'+id+'/read'),json=dict(throughId=current['latestId']+100)).status_code==422
    teacher.put(url('threads/'+id+'/read'),json=dict(throughId=current['latestId']))
    teacher.put(url('threads/'+id+'/read'),json=dict(throughId=first['latestId']))
    assert not teacher.get(url(unreadOnly=True)).json()['items']


def test_simultaneous_group_replies_are_all_stored_in_order(client):
    setup(client);teacher=signed('teacher');parent=signed('parent');student=signed('student')
    id=teacher.post(url(),json=dict(requestId=str(uuid4()),kind='group',title='Обсуждение класса',text='Общий вопрос.',groupIds=['g-a'],roles=['parent','student'])).json()['id']
    before=detail(teacher,id)
    def send(pair):
        who,text=pair;return who.post(url('threads/'+id+'/reply'),json=dict(requestId=str(uuid4()),version=before['version'],text=text))
    with ThreadPoolExecutor(max_workers=2) as pool:responses=list(pool.map(send,[(parent,'Ответ родителя'),(student,'Ответ ученика')]))
    assert [r.status_code for r in responses]==[200,200],[r.text for r in responses]
    value=detail(teacher,id);posts=value['posts']
    assert [p['text'] for p in posts][0]=='Общий вопрос.' and {p['text'] for p in posts}=={'Общий вопрос.','Ответ родителя','Ответ ученика'}
    ids=[p['id'] for p in posts];assert ids==sorted(ids) and len(set(ids))==3
    assert value['version']==before['version']
    # The old version is still valid for management, and a reply needs no version at all.
    assert teacher.post(url('threads/'+id+'/reply'),json=dict(requestId=str(uuid4()),text='Без версии')).status_code==200
    assert teacher.post(url('threads/'+id+'/change'),json=dict(version=before['version'],action='close')).status_code==200


def test_local_mode_requires_accounts_and_plain_text_does_not_modify_school(client):
    assert client.get(url('options')).status_code==403
    people=setup(client);before=api.read('demo').model_dump()
    parent=signed('parent');text='<script>alert(1)</script>'
    id=parent.post(url(),json=payload(people['teacher']['id'])|dict(text=text)).json()['id']
    assert detail(parent,id)['posts'][0]['text']==text
    assert api.read('demo').model_dump()==before


def test_concurrent_duplicate_creation_and_notification_failure_are_atomic(client,monkeypatch):
    import pytest
    people=setup(client);parent=signed('parent');body=payload(people['teacher']['id'])
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda _:parent.post(url(),json=body),range(2)))
    assert all(r.status_code==200 for r in results)
    assert results[0].json()==results[1].json()
    with api.connect() as db:assert db.execute('SELECT count(*) FROM message_posts').fetchone()[0]==1
    def fail(*args,**kwargs):raise RuntimeError('simulated notification failure')
    monkeypatch.setattr(api.notifications,'deliver',fail)
    with pytest.raises(RuntimeError,match='simulated notification'):
        parent.post(url(),json=payload(people['teacher']['id']))
    with api.connect() as db:
        assert db.execute('SELECT count(*) FROM message_threads').fetchone()[0]==1
        assert db.execute('SELECT count(*) FROM message_requests').fetchone()[0]==1


def test_pagination_read_marker_and_rate_limit(client):
    from datetime import timedelta
    from backend.access import now
    people=setup(client);parent=signed('parent');teacher=signed('teacher')
    id=parent.post(url(),json=payload(people['teacher']['id'])).json()['id']
    with api.connect() as db:
        # A long historical conversation must load in disjoint pages.
        for i in range(55):
            post=db.execute('INSERT INTO message_posts(thread_id,author_id,author_name,created,body) VALUES(?,?,?,?,?)',(id,people['teacher']['id'],'Teacher',(now()-timedelta(days=1)).isoformat(),str(i))).lastrowid
        db.execute('UPDATE message_threads SET latest_id=? WHERE id=?',(post,id))
    latest=detail(parent,id);assert len(latest['posts'])==50
    older=detail(parent,id,before=latest['nextBefore']);assert len(older['posts'])==6 and older['nextBefore'] is None
    assert not {p['id'] for p in latest['posts']}&{p['id'] for p in older['posts']}
    parent.put(url('threads/'+id+'/read'),json=dict(throughId=latest['latestId']))
    parent.put(url('threads/'+id+'/read'),json=dict(throughId=older['posts'][0]['id']))
    assert parent.get(url(unreadOnly=True)).json()['items']==[]
    with api.connect() as db:db.execute('UPDATE message_posts SET created=? WHERE author_id=?',(now().isoformat(),people['teacher']['id']))
    assert teacher.post(url('threads/'+id+'/reply'),json=dict(requestId=str(uuid4()),version=1,text='Flood')).status_code==429


def test_full_backup_restores_conversation_and_private_access(client,tmp_path,monkeypatch):
    from tools.backup_school import create_backup
    people=setup(client);parent=signed('parent');teacher=signed('teacher')
    id=parent.post(url(),json=payload(people['teacher']['id'])).json()['id']
    original=detail(parent,id)
    teacher.put(url('threads/'+id+'/read'),json=dict(throughId=original['latestId']))
    original=detail(parent,id)
    destination=tmp_path/'messages-backup';source=api.DB
    create_backup(source,destination)
    monkeypatch.setattr(api,'DB',destination/source.name)
    assert detail(parent,id)==original
    assert signed('other').get(url('threads/'+id)).status_code==404
    assert detail(teacher,id)['posts']==original['posts']
