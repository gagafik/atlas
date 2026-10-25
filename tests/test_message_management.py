from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest

from backend import api, message_groups
from backend.access import now
from backend.notifications import encoded
from test_access import client, create, signed
from test_messages import setup, url, detail, payload
from test_message_files_groups import group, upload, download
from test_message_drafts import save, get


def add(user, thread, ids, version, **changes):
    body=dict(requestId=str(uuid4()),version=version,userIds=ids,shareHistory=True)|changes
    return user.post(url('threads/'+thread+'/participants/add'),json=body)


def transfer(user, thread, target, version, **changes):
    body=dict(requestId=str(uuid4()),version=version,userId=target)|changes
    return user.post(url('threads/'+thread+'/manager'),json=body)


def roster(user, thread):return user.get(url('threads/'+thread+'/management'))


def candidates(user, thread, **params):return user.get(url('threads/'+thread+'/candidates',**params))


def test_add_exposes_history_only_after_confirmation_and_current_scope(client):
    people=setup(client);teacher=signed('teacher');parent=signed('parent');thread=group(teacher)
    late=create(client,'parent',login='late',name='Поздний участник');late_user=signed('late')
    outsider=create(client,'parent',login='outside',grants=[dict(workspace='demo',view='student',entityIds=['s3'])])
    before=api.read('demo').model_dump()
    draft=str(uuid4());file=upload(teacher,draft,thread).json()['id']
    teacher.post(url('threads/'+thread+'/reply'),json=dict(requestId=str(uuid4()),version=1,text='История',draftId=draft,fileIds=[file]))
    assert download(late_user,file).status_code==404
    choices=candidates(teacher,thread).json()['items']
    assert late['id'] in {p['id'] for p in choices} and outsider['id'] not in {p['id'] for p in choices}
    for user,code in [(parent,403),(client,404),(late_user,404)]:
        assert roster(user,thread).status_code==code
        assert candidates(user,thread).status_code==code
        assert add(user,thread,[late['id']],1).status_code==code
    assert add(teacher,thread,[late['id']],1,shareHistory=False).status_code==422
    assert add(teacher,thread,[late['id'],outsider['id']],1).status_code==403
    assert late_user.get(url('threads/'+thread)).status_code==404
    request=str(uuid4())
    assert add(teacher,thread,[late['id']],1,requestId=request).status_code==200
    assert add(teacher,thread,[late['id']],1,requestId=request).status_code==200
    value=detail(late_user,thread);assert value['version']==2 and len(value['posts'])==3
    assert download(late_user,file).content==b'Private attachment'
    assert late_user.get('/api/notifications/inbox').json()['items'][-1]['action']=='message.received'
    assert add(teacher,thread,[late['id']],1).status_code==409
    assert api.read('demo').model_dump()==before


def test_transfer_keeps_authorship_and_survives_departure_and_deactivation_of_creator(client):
    people=setup(client);teacher=signed('teacher');parent=signed('parent');thread=group(teacher)
    next_user=create(client,'teacher',login='next',name='Новый учитель');successor=signed('next')
    assert add(teacher,thread,[next_user['id']],1).status_code==200
    assert transfer(teacher,thread,people['parent']['id'],2).status_code==422
    assert transfer(parent,thread,next_user['id'],2).status_code==403
    assert transfer(teacher,thread,people['teacher']['id'],2).status_code==422
    request=str(uuid4())
    assert transfer(teacher,thread,next_user['id'],2,requestId=request).status_code==200
    assert transfer(teacher,thread,next_user['id'],2,requestId=request).status_code==200
    value=detail(successor,thread)
    assert value['canManage'] and value['managerId']==next_user['id'] and value['authorId']==people['teacher']['id']
    assert value['posts'][0]['authorId']==people['teacher']['id']
    assert not detail(teacher,thread)['canManage']
    assert roster(teacher,thread).status_code==403
    assert teacher.post(url('threads/'+thread+'/change'),json=dict(version=3,action='close')).status_code==403
    assert teacher.post(url('threads/'+thread+'/participants'),json=dict(version=3,userId=people['parent']['id'],action='remove')).status_code==403
    assert teacher.post(url('threads/'+thread+'/participants'),json=dict(version=3,userId=people['teacher']['id'],action='leave')).status_code==200
    assert detail(teacher,thread)['readOnly']
    body={k:v for k,v in people['teacher'].items() if k!='id'}|dict(active=False)
    assert client.put('/api/access/users/'+people['teacher']['id'],json=body).status_code==200
    assert detail(parent,thread)['managerId']==next_user['id'] and detail(successor,thread)['canManage']
    assert successor.post(url('threads/'+thread+'/change'),json=dict(version=4,action='close')).status_code==200


def test_family_group_transfer_keeps_all_teachers_and_child_scope(client):
    people=setup(client);parent=signed('parent');teacher=signed('teacher');admin=client.get('/api/auth/status').json()['user']
    body=dict(requestId=str(uuid4()),kind='group',title='Один ребёнок',text='Общий вопрос',recipientIds=[people['teacher']['id'],admin['id']],studentId='s1')
    thread=parent.post(url(),json=body).json()['id']
    assert transfer(parent,thread,people['teacher']['id'],1).status_code==200
    assert detail(client,thread)['managerId']==people['teacher']['id'] and detail(parent,thread)['canReply']
    assert parent.post(url('threads/'+thread+'/participants'),json=dict(version=2,userId=people['parent']['id'],action='leave')).status_code==200
    assert detail(teacher,thread)['canManage'] and detail(client,thread)['canReply']
    second_parent=create(client,'parent',login='secondparent')
    assert add(teacher,thread,[second_parent['id']],3).status_code==200
    assert add(teacher,thread,[people['other']['id']],4).status_code==403
    # Another guardian of the same child is eligible; another child in the class is not.
    assert detail(signed('secondparent'),thread)['canReply']


def test_staff_group_and_closed_group_management(client):
    people=setup(client);teacher=signed('teacher');admin=client.get('/api/auth/status').json()['user']
    body=dict(requestId=str(uuid4()),kind='group',title='Сотрудники',text='План',recipientIds=[people['staff']['id'],admin['id']])
    thread=teacher.post(url(),json=body).json()['id']
    editor=create(client,'editor');editor_user=signed('editor')
    assert add(teacher,thread,[editor['id']],1).status_code==200
    assert add(teacher,thread,[people['parent']['id']],2).status_code==403
    assert teacher.post(url('threads/'+thread+'/change'),json=dict(version=2,action='close')).status_code==200
    assert transfer(teacher,thread,editor['id'],3).status_code==200
    assert detail(editor_user,thread)['canManage'] and not detail(editor_user,thread)['canReply']
    assert editor_user.post(url('threads/'+thread+'/change'),json=dict(version=4,action='reopen')).status_code==200


def test_explicit_readmission_restores_history_and_retains_private_draft(client):
    people=setup(client);teacher=signed('teacher');parent=signed('parent');thread=group(teacher)
    id=str(uuid4());file=upload(parent,id,thread).json()['id']
    assert save(parent,id,dict(requestId=str(uuid4()),text='Личный черновик',fileIds=[file]),thread=thread).status_code==200
    assert parent.post(url('threads/'+thread+'/participants'),json=dict(version=1,userId=people['parent']['id'],action='leave')).status_code==200
    assert detail(parent,thread)['readOnly'] and get(parent,id).status_code==200
    assert add(teacher,thread,[people['parent']['id']],2).status_code==200
    assert detail(parent,thread)['version']==3
    assert get(teacher,id).status_code==404
    assert parent.post(url('threads/'+thread+'/reply'),json=dict(requestId=str(uuid4()),version=3,text='Вернулся',draftId=id,draftVersion=1,fileIds=[file])).status_code==200
    assert download(teacher,file).status_code==200


def test_capacity_counts_inactive_accounts_until_explicit_removal(client,monkeypatch):
    people=setup(client);teacher=signed('teacher');thread=group(teacher)
    count=len(roster(teacher,thread).json()['members'])
    monkeypatch.setattr(message_groups,'MAX_MEMBERS',count+1)
    first=create(client,'parent',login='first');second=create(client,'parent',login='second')
    assert add(teacher,thread,[first['id']],1).status_code==200
    body={k:v for k,v in first.items() if k!='id'}|dict(active=False)
    assert client.put('/api/access/users/'+first['id'],json=body).status_code==200
    value=roster(teacher,thread).json();assert not next(p for p in value['members'] if p['id']==first['id'])['accessible']
    assert add(teacher,thread,[second['id']],2).status_code==422
    assert teacher.post(url('threads/'+thread+'/participants'),json=dict(version=2,userId=first['id'],action='remove')).status_code==200
    assert add(teacher,thread,[second['id']],3).status_code==200


def test_add_and_handoff_roll_back_if_notification_fails(client,monkeypatch):
    people=setup(client);teacher=signed('teacher');thread=group(teacher)
    next_user=create(client,'teacher',login='next')
    def fail(*args,**kwargs):raise RuntimeError('simulated delivery failure')
    with monkeypatch.context() as patch:
        patch.setattr(api.notifications,'deliver',fail)
        with pytest.raises(RuntimeError,match='delivery'):add(teacher,thread,[next_user['id']],1)
    assert len(detail(teacher,thread)['posts'])==1 and signed('next').get(url('threads/'+thread)).status_code==404
    assert add(teacher,thread,[next_user['id']],1).status_code==200
    with monkeypatch.context() as patch:
        patch.setattr(api.notifications,'deliver',fail)
        with pytest.raises(RuntimeError,match='delivery'):transfer(teacher,thread,next_user['id'],2)
    value=detail(teacher,thread);assert value['canManage'] and value['version']==2 and len(value['posts'])==2
    assert not detail(signed('next'),thread)['canManage']


def test_concurrent_add_reply_and_handoff_removal_are_versioned(client):
    people=setup(client);teacher=signed('teacher');parent=signed('parent');thread=group(teacher)
    next_user=create(client,'teacher',login='next')
    with ThreadPoolExecutor(max_workers=2) as pool:
        addition=pool.submit(add,teacher,thread,[next_user['id']],1)
        reply=pool.submit(parent.post,url('threads/'+thread+'/reply'),json=dict(requestId=str(uuid4()),version=1,text='Одновременный ответ'))
        results=[addition.result(),reply.result()]
    # A reply neither waits for nor invalidates the membership change: both are stored.
    assert [r.status_code for r in results]==[200,200]
    value=detail(teacher,thread);assert value['version']==2 and len(value['posts'])==3
    version=detail(teacher,thread)['version']
    with ThreadPoolExecutor(max_workers=2) as pool:
        handoff=pool.submit(transfer,teacher,thread,next_user['id'],version)
        remove=pool.submit(teacher.post,url('threads/'+thread+'/participants'),json=dict(version=version,userId=next_user['id'],action='remove'))
        results=[handoff.result(),remove.result()]
    assert sorted(r.status_code for r in results)==[200,409]
    assert detail(teacher,thread)['version']==version+1
    value=detail(signed('next'),thread);assert value['readOnly']==(results[1].status_code==200)


def test_candidate_pages_filter_school_scope_before_deserializing_accounts(client,monkeypatch):
    people=setup(client);teacher=signed('teacher');thread=group(teacher)
    with api.connect() as db:
        password=db.execute('SELECT password FROM accounts WHERE id=?',(people['parent']['id'],)).fetchone()[0]
        for prefix,pupil,count in [('foreign','s3',4000),('late','s1',120)]:
            perms=encoded(dict(workspaces=[],grants=[dict(workspace='demo',view='student',entityIds=[pupil])]))
            db.executemany('INSERT INTO accounts VALUES (?,?,?,?,1,1,?,?)',[(f'{prefix}{i}',f'{prefix}{i}',f'Кандидат {prefix} {i:04d}','parent',password,perms) for i in range(count)])
        # Old excluded memberships must not cause full account decoding on every read.
        db.executemany('INSERT INTO message_members (thread_id,user_id,role,scope_students) VALUES (?,?,?,?)',[(thread,f'foreign{i}','parent',encoded(['s3'])) for i in range(4000)])
        db.executemany('INSERT INTO message_exclusions VALUES (?,?,?,?)',[(thread,f'foreign{i}','remove',now().isoformat()) for i in range(4000)])
    original=api.access.public;decoded=[]
    def count(row):decoded.append(row[0]);return original(row)
    monkeypatch.setattr(api.access,'public',count)
    first=candidates(teacher,thread,q='КАНДИДАТ').json();second=candidates(teacher,thread,q='КАНДИДАТ',offset=first['nextOffset']).json()
    assert len(first['items'])==100 and len(second['items'])==20 and second['nextOffset'] is None
    ids=[p['id'] for page in [first,second] for p in page['items']]
    assert len(set(ids))==120 and all(id.startswith('late') for id in ids)
    assert not any(id.startswith('foreign') for id in decoded) and len(decoded)<160


def test_new_manager_scope_checked_after_assignment_change_and_backup(client,tmp_path,monkeypatch):
    from tools.backup_school import create_backup
    people=setup(client);teacher=signed('teacher');thread=group(teacher);parent=signed('parent')
    next_user=create(client,'teacher',login='next');successor=signed('next')
    assert add(teacher,thread,[next_user['id']],1).status_code==200
    assert transfer(teacher,thread,next_user['id'],2).status_code==200
    source=api.DB;target=tmp_path/'management-backup'
    create_backup(source,target);monkeypatch.setattr(api,'DB',target/source.name)
    assert detail(successor,thread)['canManage'] and detail(parent,thread)['managerId']==next_user['id']
    state=api.read('demo')
    for workload in state.workloads:
        if workload.teacherId=='t2':workload.teacherId='t1'
        workload.additionalTeacherIds=[t for t in workload.additionalTeacherIds if t!='t2']
    api.save('demo',state)
    assert detail(parent,thread)['readOnly']
    assert roster(successor,thread).status_code==404
    assert add(successor,thread,[people['staff']['id']],3).status_code==404


def leave_class():
    state=api.read('demo')
    for pupil in state.students:
        if pupil.id=='s1':pupil.classId='c-demo-d'
    api.save('demo',state)


def marks(thread):
    with api.connect() as db:
        return dict(db.execute('SELECT user_id,live_through FROM message_members WHERE thread_id=?',(thread,))),db.execute('SELECT latest_id FROM message_threads WHERE id=?',(thread,)).fetchone()[0]


def test_added_member_reads_history_after_the_class_changes(client):
    people=setup(client);teacher=signed('teacher');thread=group(teacher)
    late=create(client,'parent',login='late',name='Поздний участник');late_user=signed('late')
    assert add(teacher,thread,[late['id']],1).status_code==200
    leave_class()
    value=detail(late_user,thread);assert value['readOnly'] and len(value['posts'])==2


def test_readmitted_member_keeps_what_was_said_while_removed(client):
    people=setup(client);teacher=signed('teacher');parent=signed('parent');thread=group(teacher)
    assert teacher.post(url('threads/'+thread+'/participants'),json=dict(version=1,userId=people['parent']['id'],action='remove')).status_code==200
    assert teacher.post(url('threads/'+thread+'/reply'),json=dict(requestId=str(uuid4()),text='Пока вас нет')).status_code==200
    assert add(teacher,thread,[people['parent']['id']],2).status_code==200
    leave_class()
    texts=[p['text'] for p in detail(parent,thread)['posts']]
    assert detail(parent,thread)['readOnly'] and 'Пока вас нет' in texts and len(texts)==4


def test_every_post_path_advances_only_the_members_with_a_live_link(client):
    people=setup(client);teacher=signed('teacher');student=signed('student');thread=group(teacher)
    next_user=create(client,'teacher',login='next');assert add(teacher,thread,[next_user['id']],1).status_code==200
    values,latest=marks(thread);assert latest==2 and set(values.values())=={2}
    assert transfer(teacher,thread,next_user['id'],2).status_code==200
    values,latest=marks(thread);assert latest==3 and set(values.values())=={3}
    # The removal notice reaches the remaining members but not the person removed before it was published.
    assert signed('next').post(url('threads/'+thread+'/participants'),json=dict(version=3,userId=people['parent']['id'],action='remove')).status_code==200
    values,latest=marks(thread);assert latest==4 and values[people['parent']['id']]==3 and {v for k,v in values.items() if k!=people['parent']['id']}=={4}
    assert student.post(url('threads/'+thread+'/participants'),json=dict(version=4,userId=people['student']['id'],action='leave')).status_code==200
    values,latest=marks(thread);assert latest==5 and values[people['student']['id']]==4 and values[next_user['id']]==5


def test_leaving_or_removal_in_a_pre_migration_group_keeps_its_history(client):
    people=setup(client);teacher=signed('teacher');parent=signed('parent');student=signed('student');thread=group(teacher)
    with api.connect() as db:db.execute('UPDATE message_members SET live_through=NULL')
    assert parent.post(url('threads/'+thread+'/participants'),json=dict(version=1,userId=people['parent']['id'],action='leave')).status_code==200
    assert teacher.post(url('threads/'+thread+'/participants'),json=dict(version=2,userId=people['student']['id'],action='remove')).status_code==200
    # Both were linked when they went, so their bound is the last post before their own notice.
    for user,count in [(parent,1),(student,2)]:
        value=detail(user,thread);assert value['readOnly'] and len(value['posts'])==count and value['latestId']==count
        assert [t['id'] for t in user.get(url()).json()['items']]==[thread]


def test_roster_tells_read_only_history_from_no_access(client):
    people=setup(client);teacher=signed('teacher');thread=group(teacher)
    away=create(client,'parent',login='away');assert add(teacher,thread,[away['id']],1).status_code==200
    body={k:v for k,v in away.items() if k!='id'}|dict(active=False)
    assert client.put('/api/access/users/'+away['id'],json=body).status_code==200
    leave_class()
    with api.connect() as db:db.execute('UPDATE message_members SET live_through=NULL WHERE thread_id=? AND user_id=?',(thread,people['student']['id']))
    states={p['id']:(p['accessible'],p['history']) for p in roster(teacher,thread).json()['members']}
    assert states[people['teacher']['id']]==(True,False) and states[away['id']]==(False,False)
    assert states[people['parent']['id']]==(False,True) and states[people['student']['id']]==(False,False)

