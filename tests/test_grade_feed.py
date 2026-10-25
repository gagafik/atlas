import json

import pytest
from fastapi import HTTPException
from backend import api
from test_access import client, enable, create, signed
from test_journal import lesson, entry, write, graded, report, DAY
from test_homework_grading import ready, grade, payload, options

OTHER_PARENT=dict(workspace='demo',view='student',entityIds=['s2'])


def feed(c,student='s1',**params):
    return c.get('/api/journal/grades/feed',params=dict(workspace='demo',studentId=student)|params)


def sign(c,through,student='s1'):
    return c.post('/api/journal/grades/sign?workspace=demo',json=dict(studentId=student,throughSeq=through))


def signatures(c,student='s1'):
    return c.get('/api/journal/grades/signatures',params=dict(workspace='demo',studentId=student))


def items(c,student='s1',**params):
    response=feed(c,student,**params);assert response.status_code==200,response.text
    return response.json()


def events():
    with api.connect() as db:return db.execute('SELECT count(*) FROM grade_events').fetchone()[0]


def family(client):
    """Admin client plus s1's parent and pupil, s2's parent, s1's English teacher and a physics teacher (s2/s4 only)."""
    enable(client)
    accounts=dict(student=create(client,'student',name='Ученица первая'),parent=create(client,'parent',name='Родитель ученика 1'))
    accounts['other-parent']=create(client,'parent',login='other-parent',name='Родитель ученика 2',grants=[OTHER_PARENT])
    create(client,'teacher');create(client,'teacher',login='unrelated',grants=[dict(workspace='demo',view='teacher',entityIds=['t1'])])
    return accounts


def published(client):
    response=write(client,graded(entry(client,lesson(client)['lessonKey'])),publish=True)
    assert response.status_code==200,response.text
    return response.json()


def republish(client,record):
    response=write(client,record,publish=True);assert response.status_code==200,response.text
    return response.json()


def previous(score,**values):
    return dict(score=score,label='',maxScore=10,weight=1,assessment='Проверочная',category='fo',commentChanged=False)|values


def test_draft_saves_are_silent_and_publication_creates_private_newest_first_events(client):
    family(client);parent,pupil,other=signed('parent'),signed('student'),signed('other-parent')
    row=lesson(client);record=graded(entry(client,row['lessonKey']))
    draft=write(client,record);assert draft.status_code==200,draft.text
    assert events()==0
    empty=items(parent)
    assert empty['items']==[] and empty['unsigned']==0 and empty['latest'] is None and empty['nextBefore'] is None and empty['signedAt'] is None
    saved=republish(client,draft.json())
    assert events()==2
    body=items(parent)
    assert len(body['items'])==1 and body['unsigned']==1 and body['canSign'] and body['role']=='parent' and body['signedAt'] is None
    item=body['items'][0]
    assert body['latest']==item['seq'] and item['created']
    assert {k:item[k] for k in ['change','signed','subject','date','start','teachers','assessment','kind','maxScore','weight','score','label','comment','previous']}==dict(
        change='new',signed=False,subject='Английский язык',date=DAY,start=row['lesson']['start'],teachers=row['lesson']['teachers'],assessment='Проверочная',
        kind='points',maxScore=10,weight=1,score=8,label='',comment='Индивидуальная обратная связь s1',previous=None)
    text=json.dumps(body,ensure_ascii=False)
    assert 's2' not in text and 'Ученик 2' not in text and 'Комментарий только' not in text
    assert items(pupil)['items']==body['items'] and items(pupil)['role']=='student'
    theirs=items(other,'s2')
    assert [(i['change'],i['score']) for i in theirs['items']]==[('new',7)] and 's1' not in json.dumps(theirs) and 'Ученик 1' not in json.dumps(theirs,ensure_ascii=False)
    content=saved['document']['content']
    content['assessments'].append(dict(id='label',name='Участие',kind='label',maxScore=10,weight=1))
    content['records'][0]['grades'].append(dict(assessmentId='label',label='A*'))
    republish(client,saved)
    body=items(parent)
    assert [(i['change'],i['assessment']) for i in body['items']]==[('new','Участие'),('new','Проверочная')]
    assert body['items'][0]['kind']=='label' and body['items'][0]['label']=='A*' and body['items'][0]['score'] is None
    assert body['items'][0]['seq']>body['items'][1]['seq'] and body['latest']==body['items'][0]['seq'] and body['unsigned']==2
    assert items(other,'s2')==theirs


def test_only_the_newest_state_of_each_grade_stays_visible_and_withdrawal_hides_it(client):
    family(client);parent,other=signed('parent'),signed('other-parent')
    saved=published(client);count=events()
    saved['document']['content']['records'][0]['grades'][0]['score']=6
    draft=write(client,saved);assert draft.status_code==200,draft.text
    assert events()==count
    current=republish(client,draft.json())
    body=items(parent)
    assert [(i['change'],i['score']) for i in body['items']]==[('changed',6)] and body['items'][0]['previous']==previous(8) and events()==count+1
    current['document']['content']['topic']='Другая тема';current['document']['content']['records'][0]['attendance']='late';current['document']['content']['records'][0]['lateMinutes']=3
    current=republish(client,current)
    assert events()==count+1
    current['document']['content']['records'][0]['grades'][0]['comment']='Новый отзыв'
    current=republish(client,current)
    body=items(parent);newest=body['items'][0]
    assert len(body['items'])==1 and newest['change']=='changed' and newest['comment']=='Новый отзыв' and newest['previous']==previous(6,commentChanged=True)
    # A corrected comment is not kept anywhere the family can read it.
    assert 'Индивидуальная обратная связь' not in json.dumps(body,ensure_ascii=False)
    current['document']['content']['records'][0]['grades']=[]
    current=republish(client,current)
    newest=items(parent)['items'][0]
    assert (newest['change'],newest['score'],newest['comment'],newest['assessment'])==('removed',None,'','Проверочная') and newest['previous']['score']==6
    assert report(parent).json()['rows'][0]['record']['grades']==[]
    current['document']['content']['records'][0]['grades']=[dict(assessmentId='work',score=9)]
    current=republish(client,current)
    assert [(i['change'],i['score'],i['previous']) for i in items(parent)['items']]==[('new',9,None)]
    count=events()
    response=client.post('/api/journal/withdraw?workspace=demo',json=dict(lessonKey=current['lessonKey'],version=current['version']))
    assert response.status_code==200,response.text
    # Withdrawn from pupils and parents: nothing of that lesson stays in the feed.
    assert events()==count and report(parent).json()['rows']==[]
    for account,student in [(parent,'s1'),(other,'s2')]:
        hidden=items(account,student)
        assert hidden['items']==[] and hidden['unsigned']==0 and hidden['latest'] is None
    again=republish(client,entry(client,current['lessonKey']))
    assert [(i['change'],i['score']) for i in items(parent)['items']]==[('new',9)] and again['publishedVersion']


def test_assessment_changes_are_reported_with_the_values_they_replace(client):
    family(client);parent=signed('parent')
    saved=published(client);content=saved['document']['content']
    content['assessments'][0]['weight']=5
    current=republish(client,saved)
    newest=items(parent)['items'][0]
    assert (newest['change'],newest['weight'])==('changed',5) and newest['previous']==previous(8)
    current['document']['content']['assessments'][0]['name']='Контрольная'
    current=republish(client,current)
    newest=items(parent)['items'][0]
    assert (newest['change'],newest['assessment'],newest['previous']['assessment'])==('changed','Контрольная','Проверочная')
    current['document']['content']['assessments'][0]['maxScore']=5
    current['document']['content']['records'][0]['grades']=[]
    current['document']['content']['records'][1]['grades'][0]['score']=4
    current=republish(client,current)
    newest=items(parent)['items'][0]
    # The removed grade keeps the maximum it was given against, never "8 из 5".
    assert (newest['change'],newest['maxScore'],newest['score'],newest['previous']['score'],newest['previous']['maxScore'])==('removed',10,None,8,10)


def test_feed_carries_category_and_category_change(client):
    family(client);parent=signed('parent')
    saved=published(client);content=saved['document']['content']
    assert items(parent)['items'][0]['category']=='fo'
    # A document stored before categories existed has no key and reads as ФО, so it never looks like a change.
    from backend.grade_feed import describe
    mark=dict(score=8,label='',comment='');work=dict(maxScore=10,weight=1,name='Проверочная')
    assert describe(mark,work)==describe(mark,work|dict(category='fo')) and describe(mark,work)!=describe(mark,work|dict(category='sor'))
    content['assessments'][0]['category']='sor'
    current=republish(client,saved)
    newest=items(parent)['items'][0]
    assert (newest['change'],newest['category'],newest['previous']['category'])==('changed','sor','fo')
    current['document']['content']['assessments'][0]['category']='soch'
    current=republish(client,current)
    newest=items(parent)['items'][0]
    assert (newest['change'],newest['category'],newest['previous']['category'],newest['score'])==('changed','soch','sor',8)
    count=events();current['document']['content']['topic']='Другая тема'
    republish(client,current)
    assert events()==count


def test_each_family_account_signs_through_what_it_has_seen(client):
    family(client);parent,pupil=signed('parent'),signed('student')
    assert sign(parent,1).status_code==409
    saved=published(client)
    latest=items(parent)['latest']
    with api.connect() as db:assert db.execute("SELECT max(seq) FROM grade_events WHERE student_id='s2'").fetchone()[0]==latest+1
    assert sign(parent,latest+1).status_code==409
    assert sign(parent,0).status_code==422 and sign(parent,2**70).status_code==422
    assert feed(parent,before=2**70).status_code==422
    response=sign(parent,latest);assert response.status_code==200,response.text
    first=response.json()
    assert first['throughSeq']==latest and first['unsigned']==0 and first['signedAt']
    body=items(parent)
    assert body['unsigned']==0 and body['signedAt']==first['signedAt'] and all(i['signed'] for i in body['items'])
    view=items(pupil)
    assert view['unsigned']==1 and view['signedAt'] is None and not view['items'][0]['signed']
    saved['document']['content']['records'][0]['grades'][0]['score']=10
    republish(client,saved)
    body=items(parent)
    assert body['unsigned']==1 and body['signedAt']==first['signedAt']
    assert [(i['score'],i['signed']) for i in body['items']]==[(10,False)] and body['latest']>latest
    # A stale or repeated signature acknowledges nothing new and keeps its time.
    stale=sign(parent,latest);assert stale.status_code==200,stale.text
    assert stale.json()==dict(throughSeq=latest,signedAt=first['signedAt'],unsigned=1)
    response=sign(pupil,items(pupil)['latest']);assert response.status_code==200,response.text
    assert response.json()['unsigned']==0 and items(pupil)['unsigned']==0
    assert items(parent)['unsigned']==1
    response=sign(parent,body['latest']);assert response.status_code==200,response.text
    signed_at=response.json()['signedAt']
    assert response.json()['throughSeq']==body['latest'] and response.json()['unsigned']==0 and items(parent)['unsigned']==0 and signed_at!=first['signedAt']
    assert sign(parent,latest).json()==dict(throughSeq=body['latest'],signedAt=signed_at,unsigned=0)


def test_repeated_signature_survives_withdrawal_of_signed_grades(client):
    family(client);parent=signed('parent')
    saved=published(client)
    response=client.post('/api/journal/withdraw?workspace=demo',json=dict(lessonKey=saved['lessonKey'],version=saved['version']))
    assert response.status_code==200,response.text
    republish(client,entry(client,saved['lessonKey']))
    latest=items(parent)['latest'];first=sign(parent,latest).json()
    current=entry(client,saved['lessonKey'])
    response=client.post('/api/journal/withdraw?workspace=demo',json=dict(lessonKey=current['lessonKey'],version=current['version']))
    assert response.status_code==200,response.text
    # The signed grades are gone from the feed; a retry of the same signature is still acknowledged.
    assert items(parent)['items']==[]
    assert sign(parent,latest).json()==dict(throughSeq=latest,signedAt=first['signedAt'],unsigned=0)
    assert sign(parent,latest+10).status_code==409


def test_feed_sign_and_signature_access(client):
    accounts=family(client)
    parent,pupil,other,teacher,unrelated=signed('parent'),signed('student'),signed('other-parent'),signed('teacher'),signed('unrelated')
    def row(name,role,signed_at=None,unsigned=0):return dict(name=name,role=role,signedAt=signed_at,unsigned=unsigned)
    assert signatures(teacher).json()==dict(items=[row('Родитель ученика 1','parent'),row('Ученица первая','student')],events=False)
    published(client)
    latest=items(parent)['latest']
    assert feed(other).status_code==403 and sign(other,latest).status_code==403
    assert feed(pupil,'s2').status_code==403 and sign(pupil,latest+1,'s2').status_code==403
    assert parent.get('/api/journal/grades/feed',params=dict(workspace='school',studentId='s1')).status_code==403
    for staff in [teacher,unrelated,client]:
        assert feed(staff).status_code==403 and sign(staff,latest).status_code==403
    with api.connect() as db:assert db.execute('SELECT count(*) FROM grade_signatures').fetchone()[0]==0
    signed_at=sign(parent,latest).json()['signedAt']
    # Current family members appear even before they sign, with everything unsigned.
    expected=[row('Родитель ученика 1','parent',signed_at,0),row('Ученица первая','student',None,1)]
    assert signatures(teacher).json()==dict(items=expected,events=True)
    assert signatures(client).json()==dict(items=expected,events=True)
    for account in [unrelated,parent,pupil,other]:
        assert signatures(account).status_code==403
    assert signatures(unrelated,'s2').json()==dict(items=[row('Родитель ученика 2','parent',None,1)],events=True)
    assert signatures(teacher,'s3').json()==dict(items=[],events=False)
    user=accounts['parent'];body={k:v for k,v in user.items() if k!='id'};body['grants']=[OTHER_PARENT]
    response=client.put('/api/access/users/'+user['id'],json=body);assert response.status_code==200,response.text
    assert feed(parent).status_code in [401,403] and sign(parent,latest).status_code in [401,403]
    moved=signed('parent')
    assert feed(moved).status_code==403 and sign(moved,latest).status_code==403
    assert [i['score'] for i in items(moved,'s2')['items']]==[7]
    # A former family member disappears from the pupil's list instead of looking "signed".
    assert signatures(teacher).json()==dict(items=[row('Ученица первая','student',None,1)],events=True)
    assert [s['name'] for s in signatures(unrelated,'s2').json()['items']]==['Родитель ученика 1','Родитель ученика 2']


def test_feed_pages_newest_first_without_gaps(client):
    family(client);parent=signed('parent')
    days=['2026-08-17','2026-08-24','2026-08-31','2026-09-07','2026-09-14','2026-09-21']
    for day in days:
        record=graded(entry(client,lesson(client,day=day)['lessonKey'],day=day));content=record['document']['content']
        content['assessments']=[dict(id=f'a{i}',name=f'Работа {i}',kind='points',maxScore=10,weight=1) for i in range(10)]
        for pupil in content['records']:pupil['grades']=[]
        content['records'][0]['grades']=[dict(assessmentId=f'a{i}',score=i) for i in range(10)]
        republish(client,record)
    first=items(parent)
    assert len(first['items'])==50 and first['unsigned']==60 and first['latest']==first['items'][0]['seq']
    assert first['nextBefore']==first['items'][-1]['seq']
    second=items(parent,before=first['nextBefore'])
    assert len(second['items'])==10 and second['nextBefore'] is None and second['unsigned']==60
    assert {i['date'] for i in second['items']}=={days[0]} and first['items'][0]['date']==days[-1]
    seqs=[i['seq'] for i in first['items']+second['items']]
    assert seqs==sorted(seqs,reverse=True) and len(set(seqs))==60
    assert sign(parent,first['latest']).json()['unsigned']==0


@pytest.mark.parametrize('stage',['store_revision','deliver','withdraw'])
def test_failure_after_journal_write_leaves_grade_events_unchanged(client,monkeypatch,stage):
    family(client);parent=signed('parent')
    saved=published(client);before=items(parent);count=events()
    with api.connect() as db:hidden=db.execute('SELECT count(*) FROM grade_events WHERE hidden=1').fetchone()[0]
    def fail(*args,**kwargs):raise HTTPException(409,'Synthetic failure after journal write')
    if stage=='store_revision':
        original=api.journal.store_revision
        def failing(*args,**kwargs):
            original(*args,**kwargs)
            fail()
        monkeypatch.setattr(api.journal,'store_revision',failing)
    else:
        monkeypatch.setattr(api.journal.notifications,'deliver',fail)
    if stage=='withdraw':
        response=client.post('/api/journal/withdraw?workspace=demo',json=dict(lessonKey=saved['lessonKey'],version=saved['version']))
    else:
        saved['document']['content']['records'][0]['grades'][0]['score']=3
        response=write(client,saved,publish=True)
    assert response.status_code==409,response.text
    assert events()==count and items(parent)==before
    with api.connect() as db:assert db.execute('SELECT count(*) FROM grade_events WHERE hidden=1').fetchone()[0]==hidden
    current=entry(client,saved['lessonKey'])
    assert current['version']==saved['version'] and current['publishedVersion']==saved['publishedVersion']


def test_homework_grade_published_into_journal_creates_feed_event(client):
    record,sent,_,parent,teacher=ready(client);key=record['lessonKey'];other=signed('other-pupil')
    theirs=items(other,'s2')
    current=entry(teacher,key);content=current['document']['content']
    content['topic']='PRIVATE TOPIC';content['records'][1]['grades'][0]['score']=1
    response=write(teacher,current);assert response.status_code==200,response.text
    assert items(other,'s2')==theirs
    response=grade(teacher,key,sent,payload(options(teacher,key)));assert response.status_code==200,response.text
    body=items(parent)
    assert [i['change'] for i in body['items']]==['changed'] and body['unsigned']==1
    assert body['items'][0]['score']==9 and body['items'][0]['previous']['score']==8 and body['items'][0]['assessment']=='Проверочная'
    assert 'PRIVATE' not in json.dumps(body) and 's2' not in json.dumps(body)
    assert items(other,'s2')==theirs


def test_failed_homework_review_leaves_no_grade_event(client,monkeypatch):
    record,sent,_,parent,teacher=ready(client);key=record['lessonKey']
    before=items(parent);count=events()
    def fail(*args,**kwargs):raise HTTPException(409,'Synthetic review failure')
    monkeypatch.setattr(api.homework,'persist',fail)
    assert grade(teacher,key,sent,payload(options(teacher,key))).status_code==409
    assert events()==count and items(parent)==before
