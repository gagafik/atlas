from uuid import uuid4

from backend import api
from backend.message_channels import DEFAULT_CHANNELS
from test_access import client,create,signed
from test_message_management import add,candidates
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
    # The other family reads the dialog but learns no name of a child who is not theirs.
    child=next(s.name for s in api.read('demo').students if s.id=='s1')
    row=lambda user:next(t for t in user.get(url()).json()['items'] if t['id']==dialog)
    assert row(parent)['children']==[dict(id='s1',name=child)] and row(other)['children']==[]
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


def test_matrix_change_seen_inside_write(client,monkeypatch):
    people=setup(client);parent=signed('parent');teacher_id=people['teacher']['id']
    assert teacher_id in {u['id'] for u in parent.get(url('contacts',studentId='s1')).json()['items']}
    started=parent.post(url(),json=payload(teacher_id)).json()['id']
    matrix(client,remove=['parent_teachers'])
    assert parent.post(url(),json=payload(teacher_id)).status_code==403
    assert teacher_id not in {u['id'] for u in parent.get(url('contacts',studentId='s1')).json()['items']}
    # A switch made after the request began (it still holds the old matrix) is seen by the write itself.
    monkeypatch.setattr(type(api.messages.channels),'current',lambda self,workspace:DEFAULT_CHANNELS)
    assert parent.post(url(),json=payload(teacher_id)|dict(requestId=str(uuid4()))).status_code==403
    assert reply(parent,started).status_code==403


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


def family_group(teacher,recipients,student='s1'):
    made=teacher.post(url(),json=dict(requestId=str(uuid4()),kind='group',title='Семья',text='Приватно.',studentId=student,recipientIds=recipients))
    assert made.status_code==200,made.text
    return made.json()['id']


def offered(user,id):return {p['id'] for p in candidates(user,id).json()['items']}


def join(user,id,target):return add(user,id,[target],detail(user,id)['version'])


def test_teacher_without_a_link_joins_a_family_group_only_through_the_channels_of_its_family_roles(client):
    people=setup(client);t2=signed('teacher');parent=signed('parent');t1=signed('staff');t1_id=people['staff']['id']
    parent_id,pupil_id=people['parent']['id'],people['student']['id']
    # t1 does not teach s1, so on defaults nothing links this teacher to the family.
    led=family_group(t2,[parent_id,pupil_id]);mine=family_group(parent,[people['teacher']['id'],admin_id(client)])
    assert t1_id not in offered(parent,mine) and join(parent,mine,t1_id).status_code==403
    # A channel for pupils does not open a conversation with a parent.
    matrix(client,add=['student_any_teacher'])
    assert t1_id not in offered(parent,mine) and join(parent,mine,t1_id).status_code==403
    # The families' channels let a family bring any teacher in; a teacher who runs a conversation never pulls in a colleague who does not teach the pupil.
    matrix(client,add=['parent_any_teacher','student_any_teacher'])
    assert t1_id not in offered(t2,led) and join(t2,led,t1_id).status_code==403
    matrix(client,remove=['student_any_teacher'])
    assert t1_id in offered(parent,mine) and join(parent,mine,t1_id).status_code==200
    seen=detail(t1,mine);assert not seen['readOnly'] and seen['canReply'] and seen['posts'][0]['text']=='Приватно.'
    # The colleague stays only while the channel of every family role in the conversation stays open.
    assert join(parent,mine,pupil_id).status_code==200 and detail(t1,mine)['readOnly']
    matrix(client,add=['student_any_teacher'])
    assert not detail(t1,mine)['readOnly']
    matrix(client,remove=['parent_any_teacher'])
    assert detail(t1,mine)['readOnly']


def test_group_candidates_leave_out_whom_a_closed_channel_forbids(client):
    people=setup(client);t2=signed('teacher')
    mum=create(client,'parent',login='mum',name='Мама')
    group=family_group(t2,[people['parent']['id'],people['student']['id']])
    assert mum['id'] in offered(t2,group)
    matrix(client,remove=['teacher_parents'])
    assert mum['id'] not in offered(t2,group) and join(t2,group,mum['id']).status_code==403


def test_group_candidates_offer_class_parents_while_their_channel_is_open(client):
    people=setup(client);parent=signed('parent');other_id=people['other']['id']
    far=create(client,'parent',login='far',grants=[dict(workspace='demo',view='student',entityIds=['s3'])])
    group=family_group(parent,[people['teacher']['id'],admin_id(client)])
    assert other_id not in offered(parent,group) and join(parent,group,other_id).status_code==403
    matrix(client,add=['parent_class_parents'])
    assert other_id in offered(parent,group) and far['id'] not in offered(parent,group)
    assert join(parent,group,far['id']).status_code==403
    assert join(parent,group,other_id).status_code==200 and detail(signed('other'),group)['canReply']


def test_colleague_in_a_family_group_does_not_reopen_a_closed_channel(client):
    people=setup(client);t2=signed('teacher');parent=signed('parent')
    colleague=create(client,'teacher',login='colleague',grants=[dict(workspace='demo',view='teacher',entityIds=['t0'])]);other=signed('colleague')
    recipients=[people['parent']['id'],people['student']['id']]
    crowded=family_group(t2,recipients);alone=family_group(t2,recipients)
    assert join(t2,crowded,colleague['id']).status_code==200
    matrix(client,remove=['teacher_parents','teacher_students'])
    # Neither teacher may write to the family any more, however many colleagues share the conversation.
    for user in [t2,other]:
        assert not detail(user,crowded)['canReply'] and reply(user,crowded).status_code==403
    assert detail(parent,crowded)['canReply'] and reply(parent,crowded).status_code==200
    # Nor does adding a colleague afterwards bring the channel back.
    assert join(t2,alone,colleague['id']).status_code==200
    assert not detail(t2,alone)['canReply'] and reply(t2,alone).status_code==403


def test_parent_manager_adds_the_other_family_of_the_pupil_whatever_the_class_channel_says(client):
    people=setup(client);parent=signed('parent');mum=create(client,'parent',login='mum',name='Мама')
    group=family_group(parent,[people['teacher']['id'],admin_id(client)])
    assert mum['id'] in offered(parent,group) and join(parent,group,mum['id']).status_code==200
    assert not detail(signed('mum'),group)['readOnly']


def test_staff_to_staff_channel_can_be_switched_off(client):
    people=setup(client);teacher=signed('teacher');colleague=people['staff']['id']
    assert colleague in {u['id'] for u in teacher.get(url('contacts')).json()['items']}
    matrix(client,remove=['staff_staff'])
    assert colleague not in {u['id'] for u in teacher.get(url('contacts')).json()['items']}
    assert teacher.post(url(),json=direct(colleague)).status_code==403
    # The administrator needs no channel; a colleague only reaches the administrator.
    assert client.post(url(),json=direct(colleague)).status_code==200
    assert signed('staff').post(url(),json=direct(people['teacher']['id'])).status_code==403


def test_staff_accounts_are_offered_for_a_staff_group(client):
    people=setup(client);teacher=signed('teacher')
    clerk=create(client,'staff',login='clerk',name='Секретарь');second=create(client,'staff',login='clerk2',name='Помощник')
    made=teacher.post(url(),json=dict(requestId=str(uuid4()),kind='group',title='Совещание',text='Обсудим.',recipientIds=[clerk['id'],people['staff']['id']]));assert made.status_code==200,made.text
    group=made.json()['id']
    assert second['id'] in offered(teacher,group)
    assert join(teacher,group,second['id']).status_code==200 and not detail(signed('clerk2'),group)['readOnly']
