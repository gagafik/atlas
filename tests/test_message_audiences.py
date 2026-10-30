from datetime import date
from uuid import uuid4

import pytest

from backend import api,journal,message_audiences,timetable_history
from test_access import client,create,signed
from test_message_channels import class_teacher,matrix
from test_messages import url,detail

NEXT=date(2026,8,24)


@pytest.fixture
def school(client):
    """Admin, two teachers (t0 teaches s1/s3, t2 teaches everybody), parents of s1/s2/s3, a pupil, staff and an editor."""
    from test_access import enable
    enable(client)
    teacher=lambda login,name,entity:create(client,'teacher',login=login,name=name,grants=[dict(workspace='demo',view='teacher',entityIds=[entity])])
    parent=lambda login,name,pupil:create(client,'parent',login=login,name=name,grants=[dict(workspace='demo',view='student',entityIds=[pupil])])
    people=dict(t0=teacher('teach0','Учитель 0','t0'),t2=teacher('teacher','Учитель 2','t2'),t3=teacher('teach3','Учитель 3','t3'),
                p1=parent('parent','Родитель 1','s1'),p2=parent('parent2','Родитель 2','s2'),p3=parent('parent3','Родитель 3','s3'),
                pupil=create(client,'student',login='student',name='Ученик 1'),clerk=create(client,'staff',login='clerk',name='Секретарь'),
                editor=create(client,'editor',login='editor',name='Редактор'))
    class_teacher('c-demo-a','t0')
    return people


def announcement(**values):return dict(requestId=str(uuid4()),kind='announcement',title='Тема',text='Текст объявления.')|values


def preview(user,**values):return user.post(url('preview'),json=announcement(**values))


def names(response):
    assert response.status_code==200,response.text
    value=response.json();assert value['count']<=20
    return {p['name'] for p in value['sample']}


def test_presets_resolve_exact_accounts(client,school):
    t0,t2=signed('teach0'),signed('teacher')
    assert names(preview(client,audience='school',roles=['parent']))=={'Родитель 1','Родитель 2','Родитель 3'}
    assert names(preview(client,audience='teachers'))=={'Учитель 0','Учитель 2','Учитель 3'}
    assert names(preview(client,audience='class_teachers'))=={'Учитель 0'}
    assert names(preview(client,audience='staff'))=={'Секретарь','Редактор'}
    # t0 teaches only the mathematics group of s1 and s3.
    assert names(preview(t0,audience='my_courses',roles=['parent']))=={'Родитель 1','Родитель 3'}
    assert preview(t0,audience='my_courses',roles=['parent'],groupIds=['g-a']).status_code==403
    assert names(preview(t0,audience='my_courses',roles=['parent'],groupIds=['g-math']))=={'Родитель 1','Родитель 3'}
    assert names(preview(t2,audience='my_classes',roles=['student']))=={'Ученик 1'}
    # Roles are chosen only where they make sense, and nothing is silently widened.
    assert preview(client,audience='teachers',roles=['parent']).status_code==422
    assert preview(client,audience='school').status_code==422
    assert preview(client,audience='school',roles=['parent'],groupIds=['g-a']).status_code==422
    assert preview(t2,audience='my_classes',roles=['teacher']).status_code==422
    assert preview(t2,audience='my_courses',roles=['parent'],recipientId=school['p1']['id']).status_code==422
    assert preview(client,audience='saved',savedGroupId=str(uuid4())).status_code==422
    group=client.post(url('preview'),json=dict(requestId=str(uuid4()),kind='group',title='Х',text='Х',audience='school',roles=['parent']))
    assert group.status_code==422


def test_teacher_presets_are_gated_by_matrix(client,school):
    t2=signed('teacher')
    assert t2.get(url('options')).json()['audiences']==['my_classes','my_courses','saved']
    assert preview(t2,audience='school',roles=['parent']).status_code==403
    assert preview(t2,audience='teachers').status_code==403
    matrix(client,add=['teacher_school'])
    assert 'school' in t2.get(url('options')).json()['audiences'] and preview(t2,audience='school',roles=['parent']).status_code==200
    assert preview(t2,audience='teachers').status_code==403
    matrix(client,add=['teacher_colleagues_all'])
    assert names(preview(t2,audience='teachers'))=={'Учитель 0','Учитель 3'}
    assert names(preview(t2,audience='class_teachers'))=={'Учитель 0'} and names(preview(t2,audience='staff'))=={'Секретарь','Редактор'}
    # Without the channel to parents a teacher cannot write to them in any of the ways, old bodies included.
    matrix(client,remove=['teacher_parents'])
    assert preview(t2,audience='my_courses',roles=['parent']).status_code==403 and preview(t2,audience='school',roles=['parent']).status_code==403
    assert preview(t2,groupIds=['g-a'],roles=['parent']).status_code==403
    assert preview(t2,groupIds=['g-a'],roles=['student']).status_code==200


def test_audience_uses_current_dated_links(client,school,monkeypatch):
    monkeypatch.setattr(timetable_history,'today',lambda:date(2026,9,1));monkeypatch.setattr(journal,'today',lambda:date(2026,9,1))
    state=api.read('demo')
    # t2 teaches only the 10A lesson group; t3 keeps both whole groups.
    next(w for w in state.workloads if w.id=='w1').teacherId='t3';api.save('demo',state)
    t2,t3,parent=signed('teacher'),signed('teach3'),signed('parent')
    assert names(preview(t2,audience='my_classes',roles=['parent']))=={'Родитель 1','Родитель 2'}
    sent=t2.post(url(),json=announcement(audience='my_classes',roles=['parent']));assert sent.status_code==200,sent.text
    id=sent.json()['id'];assert detail(parent,id)['title']=='Тема'
    state=api.read('demo');next(s for s in state.students if s.id=='s1').classId='c-demo-d';api.save('demo',state,effective=NEXT)
    assert names(preview(t2,audience='my_classes',roles=['parent']))=={'Родитель 2'}
    assert names(preview(t3,audience='my_classes',roles=['parent']))=={'Родитель 1','Родитель 2','Родитель 3'}
    # An announcement already sent stays with the family whose child it was about.
    with api.connect() as db:assert db.execute("SELECT scope_students FROM message_members WHERE thread_id=? AND user_id=?",(id,school['p1']['id'])).fetchone()[0]=='["s1"]'
    assert detail(signed('parent'),id)['posts'][0]['text']=='Текст объявления.'
    state=api.read('demo');next(s for s in state.students if s.id=='s2').active=False;api.save('demo',state)
    assert names(preview(t3,audience='my_classes',roles=['parent']))=={'Родитель 1','Родитель 3'}
    # A teacher who lost the workload loses the course at once; the new one gets it.
    state=api.read('demo');next(w for w in state.workloads if w.id=='w0').teacherId='t3';api.save('demo',state)
    assert preview(t2,audience='my_courses',roles=['parent']).status_code==422
    assert names(preview(t3,audience='my_courses',roles=['parent']))=={'Родитель 1','Родитель 3'}


def test_preview_limit_and_create_agree(client,school,monkeypatch):
    monkeypatch.setattr(message_audiences,'ANNOUNCEMENT_LIMIT',1)
    body=announcement(audience='school',roles=['parent'])
    value=client.post(url('preview'),json=body).json()
    assert value['overLimit'] and value['limit']==1 and value['count']==3 and value['more']==0 and value['unavailable']==0
    assert client.post(url(),json=body).status_code==422
    with api.connect() as db:assert db.execute('SELECT count(*) FROM message_threads').fetchone()[0]==0
    monkeypatch.setattr(message_audiences,'ANNOUNCEMENT_LIMIT',5000)
    assert not client.post(url('preview'),json=body).json()['overLimit']
    sent=client.post(url(),json=body);assert sent.status_code==200,sent.text
    assert sent.json()['id'] in {t['id'] for t in signed('parent').get(url()).json()['items']}


def test_family_cannot_use_audience_and_announcement_needs_subject(client,school):
    assert preview(signed('parent'),audience='school',roles=['parent']).status_code==403
    assert signed('parent').post(url(),json=announcement(audience='school',roles=['parent'])).status_code==403
    assert client.post(url(),json=announcement(audience='teachers',title='')).status_code==422
    assert client.post(url(),json=announcement(audience='teachers')).status_code==200


def new_group(user,ids,name='Моя группа',id=None,version=0):
    id=id or str(uuid4());return id,user.put(url('saved-groups/'+id),json=dict(version=version,name=name,userIds=ids))


def test_saved_group_is_private_and_rechecked_at_send(client,school):
    t2,t0=signed('teacher'),signed('teach0');parent=signed('parent');clerk=signed('clerk')
    id,saved=new_group(t2,[school['p1']['id'],school['clerk']['id']]);assert saved.status_code==200,saved.text
    assert t0.get(url('saved-groups/'+id)).status_code==404 and t0.get(url('saved-groups')).json()['items']==[]
    assert [(g['name'],g['total'],g['available']) for g in t2.get(url('saved-groups')).json()['items']]==[('Моя группа',2,2)]
    assert t0.put(url('saved-groups/'+id),json=dict(version=1,name='Чужая',userIds=[school['clerk']['id']])).status_code==409
    assert t0.delete(url('saved-groups/'+id,version=1)).status_code==404
    assert preview(t2,audience='saved',savedGroupId=id).json()['count']==2
    # t2 stops teaching s1: the parent can no longer be written to, the group itself is left as it was.
    state=api.read('demo');next(w for w in state.workloads if w.id=='w0').teacherId='t3';api.save('demo',state)
    value=preview(t2,audience='saved',savedGroupId=id).json();assert value['count']==1 and value['unavailable']==1
    shown=t2.get(url('saved-groups/'+id)).json();assert [p['name'] for p in shown['people']]==['Секретарь'] and shown['unavailable']==1
    assert 'Родитель 1' not in str(shown)
    sent=t2.post(url(),json=announcement(audience='saved',savedGroupId=id));assert sent.status_code==200,sent.text
    assert sent.json()['id'] in {t['id'] for t in clerk.get(url()).json()['items']} and parent.get(url()).json()['items']==[]
    assert t2.delete(url('saved-groups/'+id,version=1)).status_code==200
    assert preview(t2,audience='saved',savedGroupId=id).status_code==422 and t2.get(url('saved-groups')).json()['items']==[]


def test_saved_group_version_quota_and_reach(client,school,monkeypatch):
    t0,t2=signed('teach0'),signed('teacher')
    id,saved=new_group(t2,[school['clerk']['id']]);assert saved.status_code==200
    assert new_group(t2,[school['clerk']['id'],school['p1']['id']],id=id,version=0)[1].status_code==409
    assert new_group(t2,[school['clerk']['id'],school['p1']['id']],id=id,version=1)[1].status_code==200
    # t0 does not teach s2: that family is out of reach, so it cannot enter the group.
    assert new_group(t0,[school['p2']['id']])[1].status_code==403 and new_group(t0,[school['p1']['id']])[1].status_code==200
    assert new_group(t0,['no-such-account'])[1].status_code==403
    assert new_group(t0,[school['p1']['id'],school['p1']['id']])[1].status_code==422
    found=t0.get(url('saved-groups/candidates',q='Родитель')).json()['items'];assert {p['name'] for p in found}=={'Родитель 1','Родитель 3'}
    assert 'Родитель 2' not in {p['name'] for p in t0.get(url('saved-groups/candidates')).json()['items']}
    monkeypatch.setattr(message_audiences,'MAX_SAVED',2)
    assert new_group(t2,[school['clerk']['id']])[1].status_code==200
    assert new_group(t2,[school['clerk']['id']])[1].status_code==413
    assert signed('parent').get(url('saved-groups')).status_code==403
    assert signed('parent').put(url('saved-groups/'+str(uuid4())),json=dict(version=0,name='Х',userIds=[school['clerk']['id']])).status_code==403
