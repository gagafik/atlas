from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from uuid import uuid4

from backend import api
from backend.access import now
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
    with api.connect() as db:assert {r[0] for r in db.execute('SELECT live_through FROM message_members WHERE thread_id=?',(id,))}=={detail(parent,id)['latestId']}
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


def test_changed_teacher_keeps_read_only_dialog(client):
    people=setup(client);parent=signed('parent');teacher=signed('teacher')
    draft=str(uuid4());uploaded=parent.post(url('files',draftId=draft,filename='answer.txt'),content=b'Private attachment');assert uploaded.status_code==200,uploaded.text
    file=uploaded.json()['id']
    id=parent.post(url(),json=payload(people['teacher']['id'])|dict(draftId=draft,fileIds=[file])).json()['id']
    # Changing the current teaching assignment invalidates cached relation checks.
    state=api.read('demo')
    for workload in state.workloads:
        if workload.teacherId=='t2':workload.teacherId='t1'
        workload.additionalTeacherIds=[t for t in workload.additionalTeacherIds if t!='t2']
    api.save('demo',state)
    # Both sides keep reading what was said while the link existed, but nobody can add to it.
    for user in [parent,teacher]:
        value=detail(user,id)
        assert value['readOnly'] and not value['canReply'] and not value['canManage'] and value['participants']==[] and len(value['posts'])==1
        assert user.get(url('files/'+file)).status_code==200
        assert user.post(url('threads/'+id+'/reply'),json=dict(requestId=str(uuid4()),text='x')).status_code==404
        assert user.get(url()).json()['items'][0]['readOnly'] is True
    assert client.get(url('files/'+file)).status_code==404
    # The new teacher was never a participant.
    staff=signed('staff')
    assert staff.get(url('threads/'+id)).status_code==404 and staff.get(url()).json()['items']==[]


def test_message_notification_survives_rights_change_and_open_rechecks_access(client):
    people=setup(client);parent=signed('parent');teacher=signed('teacher')
    id=parent.post(url(),json=payload(people['teacher']['id'])).json()['id']
    assert [i['action'] for i in teacher.get('/api/notifications/inbox').json()['items']]==['message.received']
    update={k:v for k,v in people['teacher'].items() if k!='id'};update['grants']=[dict(workspace='demo',view='teacher',entityIds=['t1'])]
    assert client.put('/api/access/users/'+people['teacher']['id'],json=update).status_code==200
    assert teacher.get('/api/notifications/inbox').status_code==401
    teacher=signed('teacher');inbox=teacher.get('/api/notifications/inbox').json()
    assert inbox['unread']==1 and inbox['items'][0]['action']=='message.received'
    # Opening it re-checks current access: the old conversation is history, and t1 does not teach s1.
    assert detail(teacher,id)['readOnly']
    assert teacher.get(url('contacts',studentId='s1')).status_code==403
    assert teacher.post('/api/notifications/read',json={'ids':[inbox['items'][0]['id']]}).status_code==200
    assert teacher.get('/api/notifications/inbox').json()['unread']==0


def test_legacy_rows_without_live_through_stay_hidden_after_link_loss(client):
    people=setup(client);parent=signed('parent');teacher=signed('teacher')
    id=parent.post(url(),json=payload(people['teacher']['id'])).json()['id']
    with api.connect() as db:db.execute('UPDATE message_members SET live_through=NULL')
    assert [t['id'] for t in parent.get(url()).json()['items']]==[id] and not detail(parent,id)['readOnly']
    state=api.read('demo')
    for workload in state.workloads:
        if workload.teacherId=='t2':workload.teacherId='t1'
        workload.additionalTeacherIds=[t for t in workload.additionalTeacherIds if t!='t2']
    api.save('demo',state)
    # No record of what they saw exists, so nothing is exposed.
    for user in [parent,teacher]:
        assert user.get(url('threads/'+id)).status_code==404 and user.get(url()).json()['items']==[]


def test_opening_a_legacy_dialog_records_the_bound_before_the_link_is_lost(client):
    people=setup(client);parent=signed('parent');teacher=signed('teacher')
    id=parent.post(url(),json=payload(people['teacher']['id'])).json()['id']
    with api.connect() as db:db.execute('UPDATE message_members SET live_through=NULL')
    # Only the participant who opened the dialogue after the upgrade is covered; the other row stays unknown.
    assert parent.put(url('threads/'+id+'/read'),json=dict(throughId=detail(parent,id)['latestId'])).status_code==200
    with api.connect() as db:marks=dict(db.execute('SELECT user_id,live_through FROM message_members WHERE thread_id=?',(id,)))
    assert marks[people['parent']['id']]==detail(parent,id)['latestId'] and marks[people['teacher']['id']] is None
    state=api.read('demo')
    for workload in state.workloads:
        if workload.teacherId=='t2':workload.teacherId='t1'
        workload.additionalTeacherIds=[t for t in workload.additionalTeacherIds if t!='t2']
    api.save('demo',state)
    value=detail(parent,id);assert value['readOnly'] and len(value['posts'])==1
    assert [t['id'] for t in parent.get(url()).json()['items']]==[id]
    assert teacher.get(url('threads/'+id)).status_code==404 and teacher.get(url()).json()['items']==[]


def test_switching_between_family_and_staff_sides_drops_the_history(client):
    people=setup(client);parent=signed('parent');teacher_id=people['teacher']['id']
    id=parent.post(url(),json=payload(teacher_id)).json()['id']
    # The teacher login now belongs to a family: it must not read a staff-to-family dialogue.
    update={k:v for k,v in people['teacher'].items() if k!='id'}|dict(role='parent',grants=[dict(workspace='demo',view='student',entityIds=['s2'])])
    assert client.put('/api/access/users/'+teacher_id,json=update).status_code==200
    former=signed('teacher')
    assert former.get(url('threads/'+id)).status_code==404 and former.get(url()).json()['items']==[]
    # A change within the family side keeps the history read-only.
    update={k:v for k,v in people['parent'].items() if k!='id'}|dict(role='student')
    assert client.put('/api/access/users/'+people['parent']['id'],json=update).status_code==200
    pupil=signed('parent');value=detail(pupil,id)
    assert value['readOnly'] and not value['canReply'] and len(value['posts'])==1


def test_child_added_and_removed_keep_only_own_history(client):
    people=setup(client);teacher_id=people['teacher']['id']
    multi=create(client,'parent',login='multi',grants=[dict(workspace='demo',view='student',entityIds=['s1','s3'])])
    first=signed('multi')
    about_s1=first.post(url(),json=payload(teacher_id)).json()['id']
    about_s3=first.post(url(),json=payload(teacher_id)|dict(studentId='s3')).json()['id']
    other_thread=signed('other').post(url(),json=payload(teacher_id)|dict(studentId='s2')).json()['id']
    update={k:v for k,v in multi.items() if k!='id'};update['grants'][0]['entityIds']=['s3']
    assert client.put('/api/access/users/'+multi['id'],json=update).status_code==200
    current=signed('multi')
    # The removed child's conversation is history now; the other child's stays live.
    kept=detail(current,about_s1);assert kept['readOnly'] and not kept['canReply'] and len(kept['posts'])==1
    assert current.post(url('threads/'+about_s1+'/reply'),json=dict(requestId=str(uuid4()),text='x')).status_code==404
    live=detail(current,about_s3);assert not live['readOnly'] and live['canReply']
    items={t['id']:t for t in current.get(url()).json()['items']}
    assert items[about_s1]['readOnly'] and not items[about_s3]['readOnly']
    assert current.post(url('threads/'+about_s3+'/reply'),json=dict(requestId=str(uuid4()),text='Ещё вопрос')).status_code==200
    # A newly linked child brings no older conversations between other people.
    parent=people['parent'];update={k:v for k,v in parent.items() if k!='id'};update['grants'][0]['entityIds']=['s1','s2']
    assert client.put('/api/access/users/'+parent['id'],json=update).status_code==200
    added=signed('parent')
    assert added.get(url('threads/'+other_thread)).status_code==404
    assert other_thread not in {t['id'] for t in added.get(url()).json()['items']}


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
    parent=signed('parent');value=detail(parent,id)
    assert value['readOnly'] and value['posts'][0]['text']=='В пятницу.'
    assert parent.post(url('threads/'+id+'/change'),json=dict(version=1,action='acknowledge')).status_code==404


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


def history(thread,author,count):
    with api.connect() as db:
        for i in range(count):
            post=db.execute('INSERT INTO message_posts(thread_id,author_id,author_name,created,body) VALUES(?,?,?,?,?)',(thread,author,'Teacher',(now()-timedelta(days=1)).isoformat(),str(i))).lastrowid
        db.execute('UPDATE message_threads SET latest_id=? WHERE id=?',(post,thread))


def test_long_conversation_loads_in_disjoint_pages_covering_every_post(client):
    people=setup(client);parent=signed('parent')
    id=parent.post(url(),json=payload(people['teacher']['id'])).json()['id']
    history(id,people['teacher']['id'],55)
    seen=[];before=None
    for _ in range(100):
        page=detail(parent,id,**({} if before is None else dict(before=before)))
        assert page['posts']
        seen+=[p['id'] for p in page['posts']]
        before=page['nextBefore']
        if before is None:break
    assert before is None
    with api.connect() as db:stored=sorted(r[0] for r in db.execute('SELECT id FROM message_posts WHERE thread_id=?',(id,)))
    assert sorted(seen)==stored


def test_reading_an_older_post_does_not_move_the_read_marker_back(client):
    people=setup(client);parent=signed('parent')
    id=parent.post(url(),json=payload(people['teacher']['id'])).json()['id']
    history(id,people['teacher']['id'],3)
    posts=detail(parent,id)['posts']
    assert [t['id'] for t in parent.get(url(unreadOnly=True)).json()['items']]==[id]
    assert parent.put(url('threads/'+id+'/read'),json=dict(throughId=posts[-1]['id'])).status_code==200
    assert parent.put(url('threads/'+id+'/read'),json=dict(throughId=posts[0]['id'])).status_code==200
    assert parent.get(url(unreadOnly=True)).json()['items']==[]


def test_flooding_a_conversation_is_rate_limited_per_author(client,monkeypatch):
    fixed=now();monkeypatch.setattr('backend.messages.now',lambda:fixed)
    people=setup(client);parent=signed('parent');teacher=signed('teacher')
    id=parent.post(url(),json=payload(people['teacher']['id'])).json()['id']
    codes=[]
    for i in range(300):
        codes.append(teacher.post(url('threads/'+id+'/reply'),json=dict(requestId=str(uuid4()),version=1,text=f'Flood {i}')).status_code)
        if codes[-1]==429:break
    assert codes[0]==200 and codes[-1]==429 and set(codes[:-1])=={200}
    assert parent.post(url('threads/'+id+'/reply'),json=dict(requestId=str(uuid4()),version=1,text='Всё ещё можно')).status_code==200


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


def test_live_through_column_is_added_to_existing_member_table(tmp_path):
    import sqlite3
    from contextlib import closing
    with closing(sqlite3.connect(tmp_path/'old.db')) as db:
        db.execute('CREATE TABLE message_members (thread_id TEXT,user_id TEXT,role TEXT,scope_students TEXT,read_id INTEGER DEFAULT 0,ack_version INTEGER,PRIMARY KEY(thread_id,user_id))')
        db.execute("INSERT INTO message_members VALUES ('t','u','parent','[\"s1\"]',7,2)")
        api.messages.tables(db);api.messages.tables(db)
        assert 'live_through' in {r[1] for r in db.execute('PRAGMA table_info(message_members)')}
        assert db.execute('SELECT thread_id,user_id,role,scope_students,read_id,ack_version,live_through FROM message_members').fetchall()==[('t','u','parent','["s1"]',7,2,None)]


def test_inbox_row_shows_sender_date_child_and_preview(client):
    people=setup(client);teacher=signed('teacher')
    renamed={k:v for k,v in people['parent'].items() if k!='id'}|dict(name='Мама Ани')
    assert client.put('/api/access/users/'+people['parent']['id'],json=renamed).status_code==200
    parent=signed('parent');child=next(s.name for s in api.read('demo').students if s.id=='s1')
    sent=parent.post(url(),json=payload(people['teacher']['id'])|dict(title=''));assert sent.status_code==200,sent.text
    inbox=teacher.get(url()).json();row=inbox['items'][0]
    assert row['sender']=='Мама Ани' and row['title']=='' and 'уточните задание' in row['preview'] and not row['hasFiles'] and row['date']
    assert row['children']==[dict(id='s1',name=child)]
    assert inbox['unreadCount']==1 and teacher.get(url('unread')).json()=={'count':1,'more':False}
    assert parent.get(url()).json()['unreadCount']==0 and parent.get(url()).json()['items'][0]['children']==[dict(id='s1',name=child)]
    id=sent.json()['id'];assert teacher.put(url('threads/'+id+'/read'),json=dict(throughId=detail(teacher,id)['latestId'])).status_code==200
    assert teacher.get(url('unread')).json()['count']==0 and teacher.get(url()).json()['unreadCount']==0
    # An account that took part in nothing has nothing to count.
    assert signed('other').get(url('unread')).json()['count']==0


def test_inbox_preview_respects_history_and_moderation(client):
    from test_message_files_groups import group,upload
    people=setup(client);teacher=signed('teacher');parent=signed('parent');other=signed('other')
    id=group(teacher)
    say=lambda text,**extra:teacher.post(url('threads/'+id+'/reply'),json=dict(requestId=str(uuid4()),text=text)|extra)
    assert say('Перед удалением').status_code==200
    removed=teacher.post(url('threads/'+id+'/participants'),json=dict(version=detail(teacher,id)['version'],userId=people['other']['id'],action='remove'));assert removed.status_code==200,removed.text
    draft=str(uuid4());file=upload(teacher,draft,id).json()['id']
    assert say('Секрет',draftId=draft,fileIds=[file]).status_code==200
    row=lambda user:next(t for t in user.get(url()).json()['items'] if t['id']==id)
    # Whoever left or was removed reads no further than the last post they could see.
    assert row(other)['readOnly'] and row(other)['preview']=='Перед удалением' and not row(other)['hasFiles']
    assert row(parent)['preview']=='Секрет' and row(parent)['hasFiles'] and row(parent)['sender']==people['teacher']['name']
    post=detail(parent,id)['posts'][-1]['id']
    made=parent.post(url('posts/'+str(post)+'/report'),json=dict(requestId=str(uuid4()),reason='Нарушение',shareEvidence=True));assert made.status_code==200,made.text
    state=client.get(url('reports/'+made.json()['id'])).json()
    assert client.post(url('reports/'+made.json()['id']+'/decision'),json=dict(requestId=str(uuid4()),version=state['version'],postVersion=state['postVersion'],action='hide',note='Скрыто')).status_code==200
    assert row(parent)['preview']=='Сообщение скрыто администратором.' and not row(parent)['hasFiles']
    assert 'Секрет' not in str(teacher.get(url()).json())


def test_announcement_requires_subject_but_dialog_does_not(client):
    people=setup(client);teacher=signed('teacher');parent=signed('parent')
    body=dict(requestId=str(uuid4()),kind='announcement',title='',text='Текст',groupIds=['g-a'],roles=['parent'])
    refused=teacher.post(url(),json=body);assert refused.status_code==422 and 'тему' in refused.json()['detail']
    assert teacher.post(url(),json=body|dict(title='Тема')).status_code==200
    assert parent.post(url(),json=payload(people['teacher']['id'])|dict(title='')).status_code==200
    group=teacher.post(url(),json=dict(requestId=str(uuid4()),kind='group',title='',text='Вопрос.',groupIds=['g-a'],roles=['parent','student']))
    assert group.status_code==200,group.text


def test_unread_never_counts_a_thread_the_account_cannot_read(client):
    from test_message_files_groups import group
    people=setup(client);teacher=signed('teacher');parent=signed('parent');other=signed('other')
    notice=teacher.post(url(),json=dict(requestId=str(uuid4()),kind='announcement',title='Тема',text='Текст',groupIds=['g-a'],roles=['parent'])).json()['id']
    assert parent.get(url('unread')).json()=={'count':1,'more':False}
    assert teacher.post(url('threads/'+notice+'/change'),json=dict(version=1,action='withdraw')).status_code==200
    assert parent.get(url('threads/'+notice)).status_code==404
    assert parent.get(url('unread')).json()=={'count':0,'more':False} and parent.get(url()).json()['unreadCount']==0
    # Whoever was removed from a discussion is not told about what was said after that.
    id=group(teacher)
    removed=teacher.post(url('threads/'+id+'/participants'),json=dict(version=detail(teacher,id)['version'],userId=people['other']['id'],action='remove'));assert removed.status_code==200,removed.text
    assert other.get(url('unread')).json()['count']==1
    assert other.put(url('threads/'+id+'/read'),json=dict(throughId=detail(other,id)['latestId'])).status_code==200
    assert teacher.post(url('threads/'+id+'/reply'),json=dict(requestId=str(uuid4()),text='После ухода.')).status_code==200
    assert other.get(url('unread')).json()['count']==0 and other.get(url()).json()['unreadCount']==0


def test_unread_answers_before_any_message_was_ever_sent(client):
    setup(client)
    assert signed('parent').get(url('unread')).json()=={'count':0,'more':False}
