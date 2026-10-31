from uuid import uuid4
import pytest
from backend import api
from backend.models import DatedLesson,Placement,Workload
from test_access import client,enable,create,signed  # noqa: F401
from test_journal import entry,lesson,write,graded,DAY
from test_journal_periods import period,close,action,period_clock  # noqa: F401
from test_final_grades import BASE,prepare,sheet,batch,propose,seeded,scale,report,publish,stop_gradebook_jobs  # noqa: F401

ENGLISH='Английский язык'


def english(client,**extra):
    enable(client)
    write(client,graded(entry(client,lesson(client)['lessonKey'])),publish=True).raise_for_status()


def pair(c,b,student,subject=ENGLISH):
    return next(v for v in sheet(c,b).json()['items'] if v['studentId']==student and v['source']['subject']==subject)


def last_job(c):return c.get(BASE+'/preparations?workspace=demo').json()['items'][0]


def test_preparation_assigns_rows_to_the_course_teacher(client):
    english(client);teacher=create(client,'teacher');create(client,'parent')
    p=close(client,period(client));b=prepare(client,p)
    rows=sheet(client,b).json()['items']
    # English is taught by t2 (one account); the Maths teacher t0 has no account, so those rows wait for the administrator.
    assert rows and all(r['assigned']==(teacher['id'] if r['source']['subject']==ENGLISH else '') for r in rows)
    assert any(r['assigned'] for r in rows) and any(not r['assigned'] for r in rows)
    mine={r['id'] for r in rows if r['assigned']}
    assert {r['id'] for r in sheet(signed('teacher'),b).json()['items']}==mine
    job=last_job(client)
    assert job['assignedRows']==len(mine) and job['unassignedRows']==len(rows)-len(mine) and str(len(mine)) in job['message']
    # An automatic assignment is not an edit: no version bump, no history, nothing reaches families.
    row=next(r for r in rows if r['assigned'])
    assert row['version']==1 and client.get(BASE+'/rows/'+row['id']+'/history?workspace=demo').json()['items']==[]
    assert report(signed('parent'),p).json()['items']==[]
    assert [t['id'] for t in row['source']['teachers']]==['t2'] and row['source']['teachers'][0]['lessons']==1


def test_substitute_does_not_become_responsible(client):
    enable(client);teacher=create(client,'teacher');create(client,'teacher',login='cover',grants=[dict(workspace='demo',view='teacher',entityIds=['t1'])])
    state=api.read('demo')
    state.datedLessons=[DatedLesson(id='replacement',name='Замена',kind='change',placementId='eng',sourceDate=DAY,onDate=DAY,teacherIds=['t1'])]
    api.save('demo',state)
    write(client,graded(entry(client,lesson(client)['lessonKey'])),publish=True).raise_for_status()
    b=prepare(client,close(client,period(client)))
    assert {r['assigned'] for r in sheet(client,b).json()['items'] if r['source']['subject']==ENGLISH}=={teacher['id']}
    assert sheet(signed('cover'),b).status_code==404


def test_ambiguous_teacher_is_not_guessed(client):
    english(client);create(client,'teacher');create(client,'teacher',login='twin')
    b=prepare(client,close(client,period(client)))
    rows=[r for r in sheet(client,b).json()['items'] if r['source']['subject']==ENGLISH]
    assert rows and all(r['assigned']=='' for r in rows)


def teach(*extra):
    """More English lessons on Monday for 10A, as (workload id, teacher, period)."""
    school=api.read('demo')
    school.workloads+=[Workload(id=w,groupId='g-a',subjectId='sub2',teacherId=t,hours=1,profileId='secondary',roomIds=['r0']) for w,t,_ in extra]
    school.placements+=[Placement(id=w,workloadId=w,day=0,period=p,roomId='r0') for w,_,p in extra]
    api.save('demo',school)


@pytest.mark.parametrize('extra,order',[([('more','t2',3),('other','t1',4)],[('t2',2),('t1',1)]),([('other','t1',3)],[('t1',1),('t2',1)])])
def test_course_teachers_are_ordered_by_lessons_then_by_id(client,extra,order):
    teach(*extra);english(client)
    accounts={'t2':create(client,'teacher')['id'],'t1':create(client,'teacher',login='second',grants=[dict(workspace='demo',view='teacher',entityIds=['t1'])])['id']}
    row=pair(client,prepare(client,close(client,period(client))),'s1')
    # The teacher with the most lessons answers for the course; on a tie the lower id does.
    assert [(t['id'],t['lessons']) for t in row['source']['teachers']]==order and row['assigned']==accounts[order[0][0]]


def test_row_known_only_from_a_published_journal_takes_the_teachers_of_the_course(client):
    english(client);teacher=create(client,'teacher')
    # s2 leaves 10A after the lesson was published: its document still lists s2, the schedule at closure does not.
    school=api.read('demo');school.students=[x.model_copy(update=dict(classId='c-demo-d')) if x.id=='s2' else x for x in school.students];api.save('demo',school)
    b=prepare(client,close(client,period(client)))
    gone=pair(client,b,'s2');assert gone['source']['expected']==0 and gone['source']['published']==1
    assert [t['id'] for t in gone['source']['teachers']]==['t2'] and gone['assigned']==teacher['id']


def test_reclose_keeps_admin_assignment_over_automatic(client):
    english(client);teacher=create(client,'teacher');other=create(client,'teacher',login='other',grants=[dict(workspace='demo',view='teacher',entityIds=['t1'])])
    p=close(client,period(client));b=prepare(client,p)
    cleared=pair(client,b,'s1');moved=pair(client,b,'s2')
    assert cleared['assigned']==moved['assigned']==teacher['id']
    batch(client,[cleared],'assign',teacherId='').raise_for_status()
    batch(client,[moved],'assign',teacherId=other['id']).raise_for_status()
    b2=prepare(client,close(client,action(client,p,'reopen').json()))
    assert b['id']!=b2['id']
    a,m=pair(client,b2,'s1'),pair(client,b2,'s2')
    assert a['assigned']=='' and m['assigned']==other['id'] and a['version']==m['version']==1
    assert client.get(BASE+'/rows/'+a['id']+'/history?workspace=demo').json()['items']==[]


def test_auto_assign_endpoint_fills_empty_rows_and_is_admin_only(client):
    english(client)
    p=close(client,period(client));b=prepare(client,p)
    assert all(r['assigned']=='' for r in sheet(client,b).json()['items'])
    teacher=create(client,'teacher');url=BASE+'/books/'+b['id']+'/auto-assign?workspace=demo'
    first=client.post(url,json={});assert first.status_code==200,first.text
    done=first.json();rows=sheet(client,b).json()['items']
    english_rows=[r for r in rows if r['source']['subject']==ENGLISH]
    assert done['assigned']==len(english_rows) and done['skipped']==len(rows)-len(english_rows) and done['nextAfter'] is None
    assert len(done['noAccount'])==1
    assert all(r['assigned']==teacher['id'] for r in english_rows)
    for r in english_rows:
        history=client.get(BASE+'/rows/'+r['id']+'/history?workspace=demo').json()['items']
        assert [h['action'] for h in history]==['assign'] and r['version']==2
    assert client.post(url,json={}).json()['assigned']==0
    assert signed('teacher').post(url,json={}).status_code==403
    action(client,p,'reopen').raise_for_status()
    assert client.post(url,json={}).status_code==409


def test_old_book_without_course_teachers_is_skipped_not_failed(client):
    english(client);create(client,'teacher')
    b=prepare(client,close(client,period(client)))
    with api.connect() as db:
        db.execute("UPDATE grading_rows SET assigned='',source=json_remove(source,'$.teachers')")
    done=client.post(BASE+'/books/'+b['id']+'/auto-assign?workspace=demo',json={})
    assert done.status_code==200 and done.json()['assigned']==0 and done.json()['skipped']>0


def own_rows(c,b,teacher,*students,subject=ENGLISH):
    rows=[pair(c,b,s,subject) for s in students]
    return batch(c,rows,'assign',teacherId=teacher).json()['items']


def table(c,b,s,rows,**extra):
    return c.post(BASE+'/books/'+b['id']+'/proposals?workspace=demo',json=dict(requestId=str(uuid4()),scaleId=s['id'],scaleVersion=s['version'],rows=[dict(id=r['id'],version=r['version'])|v for r,v in rows])|extra)


def accept(c,b,s,rows,**extra):
    return c.post(BASE+'/books/'+b['id']+'/accept?workspace=demo',json=dict(requestId=str(uuid4()),scaleId=s['id'],scaleVersion=s['version'],rows=[dict(id=r['id'],version=r['version']) for r in rows])|extra)


def test_batch_save_is_atomic_and_scoped(client):
    p,b,s,r,_=seeded(client);t=signed('teacher');first,second=own_rows(client,b,r['assigned'],'s1','s2')
    stranger=pair(client,b,'s1','Математика')
    bad=table(t,b,s,[(first,dict(label='Выше')),(second,dict(label='Нет такой'))])
    assert bad.status_code==422 and 'Ученик 2' in bad.json()['detail'], bad.text
    assert [(i['version'],i['proposal']) for i in sheet(t,b).json()['items']]==[(first['version'],None),(second['version'],None)]
    # A row of someone else, or of another book, rolls everything back as well.
    assert table(t,b,s,[(first,dict(label='Выше')),(stranger,dict(label='Выше'))]).status_code==404
    assert [i['proposal'] for i in sheet(t,b).json()['items']]==[None,None]
    done=table(t,b,s,[(first,dict(label='Выше')),(second,dict(label='Ниже'))],submit=True)
    assert done.status_code==200,done.text
    assert [(i['id'],i['status']) for i in done.json()['items']]==[(first['id'],'submitted'),(second['id'],'submitted')]
    assert {i['status'] for i in sheet(client,b).json()['items'] if i['id'] in {first['id'],second['id']}}=={'submitted'}
    # Stale versions are refused for the whole table.
    assert table(t,b,s,[(first,dict(label='Выше'))]).status_code==409


def test_batch_save_replays_request_id(client):
    p,b,s,r,_=seeded(client);t=signed('teacher');first,second=own_rows(client,b,r['assigned'],'s1','s2')
    body=dict(requestId=str(uuid4()),scaleId=s['id'],scaleVersion=s['version'],submit=False,rows=[dict(id=first['id'],version=first['version'],label='Выше'),dict(id=second['id'],version=second['version'],label='Ниже')])
    url=BASE+'/books/'+b['id']+'/proposals?workspace=demo'
    one=t.post(url,json=body);two=t.post(url,json=body)
    assert one.status_code==two.status_code==200 and one.json()==two.json()
    for row in (first,second):
        assert [h['action'] for h in t.get(BASE+'/rows/'+row['id']+'/history?workspace=demo').json()['items']].count('saved')==1
    other=dict(body,rows=[dict(body['rows'][0],label='Ниже',explanation='Другое решение')])
    assert t.post(url,json=other).status_code==409


def test_batch_refuses_rows_of_another_book_and_rows_that_are_no_longer_drafts(client):
    p,b,s,r,_=seeded(client);t=signed('teacher');old=pair(t,b,'s1')
    # The period is reopened and closed again: the old book is frozen, but its row is still assigned to the teacher.
    b2=prepare(client,close(client,action(client,p,'reopen').json()));assert b2['id']!=b['id']
    assert table(t,b2,s,[(old,dict(label='Выше'))]).status_code==404 and accept(t,b2,s,[old]).status_code==404
    frozen=sheet(t,b).json()['items'];assert [(i['id'],i['version'],i['proposal']) for i in frozen]==[(old['id'],old['version'],None)]
    # A row that was sent for approval is not a draft any more: the table cannot silently put it back.
    row=pair(t,b2,'s1');sent=table(t,b2,s,[(row,dict(label='Выше'))],submit=True).json()['items'][0]
    again=table(t,b2,s,[(dict(row,version=sent['version']),dict(label='Ниже',explanation='Решение учителя'))])
    assert again.status_code==409 and 'в черновик' in again.json()['detail']
    kept=pair(t,b2,'s1');assert kept['status']=='submitted' and kept['proposal']['label']=='Выше' and kept['version']==sent['version']


def test_accept_recommendations_uses_server_calculation(client):
    p,b,s,r,_=seeded(client);t=signed('teacher');good,weak=own_rows(client,b,r['assigned'],'s1','s2')
    empty=own_rows(client,b,r['assigned'],'s1',subject='Математика')[0]
    manual=table(t,b,s,[(weak,dict(label='Выше',explanation='Решение учителя',comment='Молодец'))]);assert manual.status_code==200,manual.text
    rows=[pair(t,b,'s1'),pair(t,b,'s2'),pair(t,b,'s1','Математика')]
    result=accept(t,b,s,rows);assert result.status_code==200,result.text
    saved=[i['id'] for i in result.json()['items']];skipped={x['id']:x['reason'] for x in result.json()['skipped']}
    assert saved==[good['id']] and skipped=={weak['id']:'manual',empty['id']:'none'}
    got=pair(t,b,'s1');assert got['proposal']['label']==got['proposal']['calculation']['recommended']=='Выше' and got['status']=='draft'
    assert pair(t,b,'s2')['proposal']['label']=='Выше'
    again=accept(t,b,s,[pair(t,b,'s2')],overwrite=True,submit=True);assert again.status_code==200,again.text
    over=pair(t,b,'s2');assert over['proposal']['label']=='Ниже' and over['proposal']['comment']=='Молодец' and over['status']=='submitted'
    # A submitted row is not touched by a later acceptance.
    assert accept(t,b,s,[over]).json()['skipped']==[dict(id=over['id'],reason='status')]
    assert accept(t,b,s,[dict(over,version=99)]).status_code==409


def test_accept_is_not_publication(client):
    p,b,s,r,_=seeded(client);t=signed('teacher');parent=signed('parent')
    rows=own_rows(client,b,r['assigned'],'s1','s2')
    assert accept(t,b,s,rows,submit=True).status_code==200
    assert {i['status'] for i in sheet(client,b).json()['items'] if i['id'] in {x['id'] for x in rows}}=={'submitted'}
    assert report(parent,p).json()['items']==[]
    assert 'journal.finals_published' not in [n['action'] for n in parent.get('/api/notifications/inbox').json()['items']]


def test_sheet_with_scale_returns_advice_to_staff_only(client):
    p,b,s,r,_=seeded(client);t=signed('teacher');parent=signed('parent')
    items=sheet(t,b,scaleId=s['id']).json()['items']
    assert items and items[0]['advice']['recommended']=='Выше'
    assert 'advice' not in sheet(t,b).json()['items'][0]
    assert sheet(parent,b,scaleId=s['id']).status_code==403
    assert sheet(client,b,scaleId='missing').status_code==404


def matrix(c,b):return c.get(BASE+'/books/'+b['id']+'/matrix?workspace=demo')


def test_matrix_counts_and_scope(client):
    p,b,s,r,_=seeded(client);t=signed('teacher')
    data=matrix(client,b).json();total=sheet(client,b).json()['total']
    cell=lambda d:next(c for c in d['cells'] if (c['classId'],c['subjectId'])==(r['classId'],r['subjectId']))
    assert sum(c['total'] for c in data['cells'])==total and sum(c['unassigned'] for c in data['cells'])==total-1
    assert [c['name'] for c in data['classes']]==['10A','10D'] and {c['name'] for c in data['subjects']}=={'Английский язык','Математика'}
    assert cell(data)['total']==2 and cell(data)['draft']==2 and cell(data)['submitted']==0 and cell(data)['proposed']==0
    assert propose(t,r,s).status_code==200
    moved=cell(matrix(client,b).json());assert (moved['draft'],moved['submitted'],moved['proposed'],moved['total'])==(1,1,1,2)
    # A teacher sees the pairs of the own rows only.
    own=matrix(t,b).json();assert [(c['classId'],c['subjectId'],c['total']) for c in own['cells']]==[(r['classId'],r['subjectId'],1)] and [c['name'] for c in own['classes']]==['10A']
    assert matrix(signed('parent'),b).status_code==403
    create(client,'teacher',login='idle',grants=[dict(workspace='demo',view='teacher',entityIds=['t3'])])
    assert matrix(signed('idle'),b).status_code==404 and matrix(client,dict(b,id='missing')).status_code==404


def test_matrix_shows_drafts_left_with_a_departed_teacher(client):
    english(client);teacher=create(client,'teacher');b=prepare(client,close(client,period(client)))
    cell=lambda subject:next(c for c in matrix(client,b).json()['cells'] if c['subjectId']==subject)
    assert (cell('sub2')['total'],cell('sub2')['unassigned'],cell('sub2')['unavailable'])==(2,0,0)
    gone=client.put('/api/access/users/'+teacher['id'],json={k:v for k,v in teacher.items() if k!='id'}|dict(active=False));assert gone.status_code==200,gone.text
    # Nobody can edit the drafts of a departed teacher, so the cell says so; rows without an owner are counted separately.
    assert (cell('sub2')['unassigned'],cell('sub2')['unavailable'],cell('sub0')['unavailable'])==(0,2,0)
    other=create(client,'teacher',login='other')
    batch(client,[r for r in sheet(client,b,subjectId='sub2').json()['items']],'assign',teacherId=other['id']).raise_for_status()
    assert cell('sub2')['unavailable']==0


def test_handles_require_class_and_respect_scope(client):
    p,b,s,r,_=seeded(client);t=signed('teacher');url=BASE+'/books/'+b['id']+'/handles'
    assert client.get(url,params=dict(workspace='demo')).status_code==422 and client.get(url,params=dict(workspace='demo',classId='')).status_code==422
    mine=t.get(url,params=dict(workspace='demo',classId=r['classId'])).json()['items']
    assert [x['id'] for x in mine]==[r['id']] and all('source' not in x and 'proposal' not in x for x in mine)
    everyone=client.get(url,params=dict(workspace='demo',classId=r['classId'],subjectId=r['subjectId'])).json()['items']
    assert len(everyone)==2 and set(everyone[0])=={'id','version','status','assigned','subjectId','classId'}
    # The handles are what the mass actions need: they are accepted as they are.
    assert batch(client,everyone,'assign',teacherId=r['assigned']).status_code==200
    assert client.get(url,params=dict(workspace='demo',classId=r['classId'],status='submitted')).json()['items']==[]
    assert signed('parent').get(url,params=dict(workspace='demo',classId=r['classId'])).status_code==403
