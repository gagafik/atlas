from uuid import uuid4

from backend import api
from test_access import client,create,signed
from test_messages import setup,url,detail,payload


def admin_id(client):return client.get('/api/auth/status').json()['user']['id']


def matrix(client,add=(),remove=()):
    """Turn channels on and off like the administrator's form does: the full list of enabled ones, with the version read first."""
    current=client.get(url('channels')).json()
    enabled=({c['id'] for c in current['items'] if c['enabled']}|set(add))-set(remove)
    response=client.put(url('channels'),json=dict(version=current['version'],reason='Проверка',enabled=sorted(enabled)))
    assert response.status_code==200,response.text


def direct(target,student=None,**values):
    body=dict(requestId=str(uuid4()),title='',text='Здравствуйте.',recipientId=target)
    return body|(dict(studentId=student) if student else {})|values


def class_teacher(class_id,teacher_id):
    state=api.read('demo');next(c for c in state.classes if c.id==class_id).classTeacherId=teacher_id;api.save('demo',state)


def reply(user,id,text='Ответ'):return user.post(url('threads/'+id+'/reply'),json=dict(requestId=str(uuid4()),text=text))


def test_staff_role_messages_staff_and_never_reaches_families(client):
    people=setup(client);teacher=signed('teacher')
    create(client,'staff',login='clerk',name='Секретарь');clerk=signed('clerk')
    assert clerk.get(url('options')).status_code==200
    names={u['id'] for u in clerk.get(url('contacts')).json()['items']}
    assert {people['teacher']['id'],admin_id(client)}<=names and not names&{people['parent']['id'],people['student']['id'],people['other']['id']}
    sent=clerk.post(url(),json=direct(people['teacher']['id']));assert sent.status_code==200,sent.text
    id=sent.json()['id'];assert detail(teacher,id)['canReply'] and reply(teacher,id).status_code==200
    assert clerk.post(url(),json=direct(people['teacher']['id'],'s1')).status_code==403
    assert clerk.post(url(),json=direct(people['parent']['id'],'s1')).status_code==403
    # A schoolwide announcement for the staff role reaches the account; the role cannot publish one itself.
    notice=dict(requestId=str(uuid4()),kind='announcement',title='Совещание',text='В пятницу.',roles=['staff'],schoolwide=True)
    announced=client.post(url(),json=notice);assert announced.status_code==200,announced.text
    assert announced.json()['id'] in {t['id'] for t in clerk.get(url()).json()['items']}
    assert clerk.post(url(),json=notice|dict(requestId=str(uuid4()))).status_code==403
    create(client,'staff',login='clerk2',workspaces=['school'])
    assert signed('clerk2').get(url('options')).status_code==403


def test_defaults_equal_todays_rules(client):
    people=setup(client);parent=signed('parent');teacher=signed('teacher');teacher_id=people['teacher']['id']
    create(client,'student',login='mate',grants=[dict(workspace='demo',view='student',entityIds=['s2'])])
    assert [c['id'] for c in client.get(url('channels')).json()['items'] if c['enabled']]==['student_teachers','student_admin','parent_teachers','parent_admin','teacher_students','teacher_parents','staff_staff']
    assert parent.post(url(),json=payload(teacher_id)).status_code==200
    assert parent.post(url(),json=payload(people['staff']['id'])).status_code==403
    assert parent.post(url(),json=payload(people['other']['id'])).status_code==403
    assert parent.post(url(),json=payload(admin_id(client))).status_code==200
    assert signed('mate').post(url(),json=payload(people['student']['id'])|dict(studentId='s2')).status_code==403
    assert teacher.post(url(),json=direct(people['staff']['id'])).status_code==200
    assert signed('staff').get(url('contacts',studentId='s1')).status_code==403


def test_matrix_saved_by_admin_only_with_version_and_history(client):
    people=setup(client);teacher=signed('teacher')
    assert teacher.get(url('channels')).status_code==403
    assert teacher.put(url('channels'),json=dict(version=0,reason='Я',enabled=[])).status_code==403
    current=client.get(url('channels')).json();assert current['version']==0
    enabled=[c['id'] for c in current['items'] if c['enabled']]
    assert client.put(url('channels'),json=dict(version=3,reason='Старая',enabled=enabled)).status_code==409
    assert client.put(url('channels'),json=dict(version=0,reason='Опечатка',enabled=enabled+['nothing'])).status_code==422
    assert client.put(url('channels'),json=dict(version=0,reason='',enabled=enabled)).status_code==422
    assert client.get(url('channels')).json()['version']==0 and client.get(url('channels/history')).json()['items']==[]
    saved=client.put(url('channels'),json=dict(version=0,reason='Классные родители',enabled=enabled+['parent_class_parents']));assert saved.status_code==200,saved.text
    after=client.get(url('channels')).json();assert after['version']==1 and next(c for c in after['items'] if c['id']=='parent_class_parents')['enabled']
    history=client.get(url('channels/history')).json()['items']
    assert len(history)==1 and history[0]['reason']=='Классные родители' and history[0]['changes']==[dict(id='parent_class_parents',label=history[0]['changes'][0]['label'],enabled=True)]
    assert client.put(url('channels'),json=dict(version=0,reason='Ещё раз',enabled=enabled)).status_code==409
    with api.connect() as db:assert db.execute("SELECT count(*) FROM audit WHERE action='message.channels_updated'").fetchone()[0]==1


def test_classmates_and_class_parents_channels(client):
    people=setup(client);parent=signed('parent');other=signed('other');teacher=signed('teacher')
    create(client,'parent',login='far',grants=[dict(workspace='demo',view='student',entityIds=['s3'])])
    matrix(client,add=['parent_class_parents'])
    listed={u['id'] for u in parent.get(url('contacts',studentId='s1')).json()['items']}
    assert people['other']['id'] in listed and people['teacher']['id'] in listed and len(listed)>2
    assert not {u['id'] for u in parent.get(url('contacts',studentId='s1')).json()['items'] if u['role']=='parent'}-{people['other']['id']}
    sent=parent.post(url(),json=payload(people['other']['id']));assert sent.status_code==200,sent.text
    dialog=sent.json()['id'];seen=detail(other,dialog);assert not seen['readOnly'] and seen['canReply']
    assert reply(other,dialog).status_code==200
    made=parent.post(url(),json=dict(requestId=str(uuid4()),kind='group',title='Класс',text='Вопрос всем.',studentId='s1',recipientIds=[people['other']['id'],people['teacher']['id']]))
    assert made.status_code==200,made.text
    group=made.json()['id']
    for user in [parent,other,teacher]:
        value=detail(user,group);assert not value['readOnly'] and value['canReply']
    # Closing the channel freezes what the two families wrote, and nobody can add to it until it is opened again.
    matrix(client,remove=['parent_class_parents'])
    assert parent.post(url(),json=payload(people['other']['id'])|dict(requestId=str(uuid4()))).status_code==403
    first,second=detail(parent,dialog),detail(other,dialog)
    assert first['readOnly'] and second['readOnly'] and first['posts']==second['posts'] and len(first['posts'])==2
    assert reply(other,dialog).status_code==404 and reply(parent,dialog).status_code==404
    assert detail(other,group)['readOnly'] and not detail(parent,group)['readOnly'] and detail(teacher,group)['canReply']
    matrix(client,add=['parent_class_parents'])
    assert not detail(other,dialog)['readOnly'] and detail(other,dialog)['canReply'] and reply(other,dialog).status_code==200
    # Classmates follow the same rule for pupils.
    mate=create(client,'student',login='mate',grants=[dict(workspace='demo',view='student',entityIds=['s2'])]);pupil=signed('student')
    assert pupil.post(url(),json=payload(mate['id'])).status_code==403
    matrix(client,add=['student_classmates'])
    made=pupil.post(url(),json=payload(mate['id']));assert made.status_code==200,made.text
    assert reply(signed('mate'),made.json()['id']).status_code==200


def test_one_way_channel_enforced_on_create_and_reply(client):
    people=setup(client);parent=signed('parent');teacher=signed('teacher');teacher_id=people['teacher']['id']
    started=parent.post(url(),json=payload(teacher_id));assert started.status_code==200,started.text
    mine=started.json()['id']
    matrix(client,remove=['parent_teachers'])
    refused=parent.post(url(),json=payload(teacher_id)|dict(requestId=str(uuid4())));assert refused.status_code==403 and 'отключён' in refused.json()['detail']
    # The teacher still writes first; the family answers a thread that the teacher started.
    theirs=teacher.post(url(),json=direct(people['parent']['id'],'s1'));assert theirs.status_code==200,theirs.text
    other=theirs.json()['id']
    assert detail(parent,other)['canReply'] and reply(parent,other).status_code==200
    # In the thread the parent started, only the teacher can still write.
    assert not detail(parent,mine)['canReply'] and reply(parent,mine).status_code==403
    assert detail(teacher,mine)['canReply'] and reply(teacher,mine).status_code==200
    assert not detail(parent,mine)['readOnly']
    # Both directions closed: the dialog becomes history for everybody, and reopening restores it.
    matrix(client,remove=['teacher_parents'])
    assert detail(parent,mine)['readOnly'] and detail(teacher,mine)['readOnly'] and reply(teacher,mine).status_code==404
    matrix(client,add=['teacher_parents'])
    assert detail(teacher,mine)['canReply'] and not detail(teacher,mine)['readOnly']


def test_matrix_change_seen_inside_write(client):
    people=setup(client);parent=signed('parent')
    assert people['teacher']['id'] in {u['id'] for u in parent.get(url('contacts',studentId='s1')).json()['items']}
    matrix(client,remove=['parent_teachers'])
    assert parent.post(url(),json=payload(people['teacher']['id'])).status_code==403
    assert people['teacher']['id'] not in {u['id'] for u in parent.get(url('contacts',studentId='s1')).json()['items']}


def test_class_teacher_channel_follows_class_teacher_id(client):
    people=setup(client);parent=signed('parent');t1=signed('staff');t1_id=people['staff']['id']
    class_teacher('c-demo-a','t1')
    # t1 does not teach s1: on defaults the class teacher is neither offered nor reachable.
    assert parent.get(url('options')).json()['classTeachers']==[]
    assert parent.post(url(),json=payload(t1_id)).status_code==403
    matrix(client,add=['parent_class_teacher'])
    options=parent.get(url('options')).json()['classTeachers'];assert [(c['studentId'],c['id']) for c in options]==[('s1',t1_id)]
    sent=parent.post(url(),json=payload(t1_id));assert sent.status_code==200,sent.text
    id=sent.json()['id'];assert detail(t1,id)['canReply'] and reply(t1,id).status_code==200
    # The link is read from the class on each request: a new class teacher takes over, the old dialog turns into history.
    third=create(client,'teacher',login='third',grants=[dict(workspace='demo',view='teacher',entityIds=['t3'])])
    class_teacher('c-demo-a','t3')
    assert detail(parent,id)['readOnly'] and detail(t1,id)['readOnly']
    assert [c['id'] for c in parent.get(url('options')).json()['classTeachers']]==[third['id']]
    class_teacher('c-demo-a',None)
    assert parent.get(url('options')).json()['classTeachers']==[]
    assert signed('student').get(url('options')).json()['classTeachers']==[]


def test_class_teacher_families_channel_widens_teacher_reach(client):
    people=setup(client);t1=signed('staff');parent_id=people['parent']['id']
    class_teacher('c-demo-a','t1')
    assert t1.get(url('contacts',studentId='s1')).status_code==403
    assert 's1' not in {s['id'] for s in t1.get(url('options')).json()['students']}
    matrix(client,add=['class_teacher_families'])
    assert parent_id in {u['id'] for u in t1.get(url('contacts',studentId='s1')).json()['items']}
    assert 's1' in {s['id'] for s in t1.get(url('options')).json()['students']}
    sent=t1.post(url(),json=direct(parent_id,'s1'));assert sent.status_code==200,sent.text
    assert reply(signed('parent'),sent.json()['id']).status_code==200
