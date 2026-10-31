from datetime import date
import pytest
from backend import api, journal
from backend.models import Student, DatedLesson
from test_access import client, enable, create, signed

DAY='2026-08-17'


def lesson(client,subject='Английский язык',day=DAY):
    response=client.get('/api/journal/day?workspace=demo&day='+day)
    assert response.status_code==200,response.text
    return next(row for row in response.json()['lessons'] if row['lesson']['subject']==subject and not row['archived'])


def entry(client,key,**params):
    response=client.get('/api/journal/entry',params=dict(workspace='demo',lessonKey=key,day=DAY)|params)
    assert response.status_code==200,response.text
    return response.json()


def write(client,record,publish=False,**extra):
    payload=dict(lessonKey=record['lessonKey'],day=record['document']['lesson']['date'],revision=record['revision'],version=record['version'],content=record['document']['content'],publish=publish,**extra)
    return client.put('/api/journal/entry?workspace=demo',json=payload)


def report(client,student='s1'):
    return client.get('/api/journal/report',params=dict(workspace='demo',studentId=student,start='2026-08-01',end='2026-09-01'))


def graded(record):
    content=record['document']['content']
    content['topic']='Тема урока'
    content['homework']='Прочитать страницу 12'
    content['dueDate']='2026-08-19'
    content['assessments']=[dict(id='work',name='Проверочная',kind='points',maxScore=10,weight=1)]
    for i,pupil in enumerate(content['records']):
        pupil.update(attendance='present',comment='Комментарий только '+pupil['studentId'],grades=[dict(assessmentId='work',score=8-i,label='',comment='Индивидуальная обратная связь '+pupil['studentId'])])
    return record


def test_draft_publish_personal_projection_and_preserved_published_version(client):
    enable(client);create(client,'student');create(client,'parent')
    pupil,parent=signed('student'),signed('parent')
    original=api.read('demo')
    initial=graded(entry(client,lesson(client)['lessonKey']))
    result=write(client,initial);assert result.status_code==200,result.text
    assert report(pupil).json()['rows']==[]
    saved=result.json()
    result=write(client,saved,publish=True);assert result.status_code==200,result.text
    visible=report(parent).json()
    assert len(visible['rows'])==1 and visible['rows'][0]['record']['studentId']=='s1'
    assert 's2' not in str(visible) and 'Ученик 2' not in str(visible)
    assert visible['summary']['subjects'][0]['percentage']==80
    saved=result.json();saved['document']['content']['topic']='Неопубликованный черновик'
    result=write(client,saved);assert result.status_code==200,result.text
    assert report(pupil).json()['rows'][0]['content']['topic']=='Тема урока'
    assert result.json()['version']==3 and result.json()['publishedVersion']==2
    assert api.read('demo')==original
    # The publication tells the family about the grade and the homework, one entry each.
    assert pupil.get('/api/notifications/inbox').json()['unread']==2


def test_role_permissions_and_teacher_scope(client):
    enable(client);create(client,'teacher');create(client,'teacher',login='unrelated',grants=[dict(workspace='demo',view='teacher',entityIds=['t1'])])
    create(client,'student');create(client,'editor');create(client,'viewer',grants=[dict(workspace='demo',view='class',entityIds=['c-demo-a'])])
    teacher=signed('teacher');key=lesson(teacher)['lessonKey'];record=graded(entry(teacher,key))
    result=write(teacher,record,publish=True);assert result.status_code==200,result.text
    unrelated=signed('unrelated')
    assert unrelated.get('/api/journal/entry',params=dict(workspace='demo',lessonKey=key,day=DAY)).status_code==403
    assert write(unrelated,result.json()).status_code==403
    assert report(unrelated).json()['rows']==[]
    pupil=signed('student')
    assert report(pupil,'s2').status_code==403
    for account in [pupil,signed('editor'),signed('viewer')]:
        assert account.get('/api/journal/day?workspace=demo&day='+DAY).status_code==403
        assert account.get('/api/journal/history?workspace=demo&lessonKey='+key).status_code==403
        assert write(account,result.json()).status_code==403
    assert teacher.get('/api/journal/options?workspace=school').status_code==403
    assert teacher.get('/api/state?workspace=demo').status_code==403


def test_validation_grades_attendance_and_roster(client):
    record=graded(entry(client,lesson(client)['lessonKey']))
    content=record['document']['content']
    content['records'][0]['grades'][0]['score']=11
    assert write(client,record).status_code==422
    content['records'][0]['grades'][0]['score']=0
    content['records'][0]['lateMinutes']=5
    assert write(client,record).status_code==422
    content['records'][0]['attendance']='late'
    content['records'][1]['studentId']='s3'
    assert write(client,record).status_code==422
    content['records'][1]['studentId']='s2'
    content['records'][1]['grades'].append(content['records'][1]['grades'][0])
    assert write(client,record).status_code==422
    content['records'][1]['grades'].pop()
    result=write(client,record,publish=True);assert result.status_code==200,result.text
    assert report(client).json()['summary']['subjects'][0]['percentage']==0
    assert report(client).json()['summary']['lateMinutes']==5


def test_stale_versions_rollback_and_audit(client,monkeypatch):
    enable(client)
    record=graded(entry(client,lesson(client)['lessonKey']))
    saved=write(client,record,publish=True).json()
    assert write(client,record).status_code==409
    api.save('demo',api.read('demo'))
    assert write(client,saved).status_code==409
    saved=entry(client,record['lessonKey'])
    with api.connect() as db:
        audit=db.execute("SELECT actor_name,action FROM audit WHERE action='journal.published'").fetchone()
        assert audit==('Администратор','journal.published')
    monkeypatch.setattr(api.journal,'notify',lambda *args: (_ for _ in ()).throw(RuntimeError('storage failed')))
    with pytest.raises(RuntimeError):write(client,saved,publish=True)
    assert entry(client,record['lessonKey'])['version']==saved['version']


def test_sync_membership_keeps_historical_marks_and_adds_unmarked_pupils(client,monkeypatch):
    # On the lesson day a changed roster still asks for review; later days keep the published roster (test_journal_history).
    monkeypatch.setattr(journal,'today',lambda:date(2026,8,17))
    record=graded(entry(client,lesson(client,'Математика')['lessonKey']))
    saved=write(client,record,publish=True).json()
    state=api.read('demo')
    group=next(g for g in state.groups if g.id=='g-math')
    group.studentIds=['s2','s3']
    api.save('demo',state)
    stale=entry(client,record['lessonKey']);assert stale['needsSync']
    assert write(client,stale).status_code==409
    refreshed=entry(client,record['lessonKey'],syncRoster=True)
    assert {s['id']:s['enrolled'] for s in refreshed['document']['lesson']['students']}=={'s1':False,'s2':True,'s3':True}
    records={r['studentId']:r for r in refreshed['document']['content']['records']}
    assert records['s1']['grades'][0]['score']==8
    assert records['s2']['attendance']=='unmarked' and records['s2']['grades']==[]
    response=write(client,refreshed,publish=True,syncRoster=True);assert response.status_code==200,response.text
    assert report(client,'s1').json()['rows'][0]['record']['grades'][0]['score']==8


def test_moved_lesson_keeps_identity_and_cancel_preserves_history(client):
    record=graded(entry(client,lesson(client)['lessonKey']))
    write(client,record,publish=True)
    state=api.read('demo');state.datedLessons=[DatedLesson(id='move',name='Причина',kind='change',placementId='eng',sourceDate=date(2026,8,17),onDate=date(2026,8,18),startTime='14:00',endTime='14:40')]
    api.save('demo',state)
    moved=lesson(client,day='2026-08-18')
    assert moved['lessonKey']==record['lessonKey']
    value=entry(client,record['lessonKey'],syncRoster=True)
    assert value['document']['lesson']['date']=='2026-08-18'
    result=write(client,value,publish=True,syncRoster=True);assert result.status_code==200,result.text
    assert len(report(client).json()['rows'])==1
    assert report(client).json()['rows'][0]['lesson']['date']=='2026-08-18'
    state=api.read('demo');state.datedLessons=[DatedLesson(id='cancel',name='Отмена',kind='cancel',placementId='eng',sourceDate=date(2026,8,17),onDate=date(2026,8,17))]
    api.save('demo',state)
    value=entry(client,record['lessonKey']);assert not value['writable']
    assert write(client,value).status_code==403
    result=client.post('/api/journal/withdraw?workspace=demo',json=dict(lessonKey=record['lessonKey'],version=value['version']))
    assert result.status_code==200 and report(client).json()['rows']==[]
    history=client.get('/api/journal/history',params=dict(workspace='demo',lessonKey=record['lessonKey'])).json()
    assert [h['action'] for h in history]==['journal.withdrawn','journal.published','journal.published']
    old=client.get('/api/journal/history',params=dict(workspace='demo',lessonKey=record['lessonKey'],version=1)).json()[0]['document']
    assert old['content']['records'][0]['grades'][0]['score']==8


def test_different_subject_does_not_reassign_old_grades(client):
    record=graded(entry(client,lesson(client)['lessonKey']))
    write(client,record,publish=True)
    state=api.read('demo');state.workloads[0].subjectId='sub3';api.save('demo',state)
    rows=client.get('/api/journal/day?workspace=demo&day='+DAY).json()['lessons']
    assert next(r for r in rows if r['lessonKey']==record['lessonKey'])['archived']
    new=next(r for r in rows if r['lesson']['subject']=='История')
    assert new['lessonKey']!=record['lessonKey'] and new['version']==0
    assert entry(client,new['lessonKey'])['document']['content']['records'][0]['grades']==[]
    assert report(client).json()['rows'][0]['lesson']['subject']=='Английский язык'


def test_weighted_points_text_grades_and_only_affected_pupil_notifications(client):
    enable(client);create(client,'student');create(client,'student',login='pupil2',grants=[dict(workspace='demo',view='student',entityIds=['s2'])])
    first,second=signed('student'),signed('pupil2')
    record=graded(entry(client,lesson(client)['lessonKey']))
    content=record['document']['content']
    content['assessments'] += [dict(id='second',name='Тест',kind='points',maxScore=20,weight=2),dict(id='label',name='Участие',kind='label',maxScore=10,weight=1)]
    content['records'][0]['grades'] += [dict(assessmentId='second',score=17),dict(assessmentId='label',label='A*')]
    response=write(client,record,publish=True);assert response.status_code==200,response.text
    result=report(first).json()['summary']['subjects'][0]
    assert result['percentage']==83.33 and result['pointsCount']==2 and result['labelCount']==1
    updated=response.json();updated['document']['content']['records'][0]['grades'][0]['score']=9
    write(client,updated,publish=True)
    # Grade (replaced by the correction) and homework (once) for each of the two affected pupils.
    assert first.get('/api/notifications/inbox').json()['unread']==2
    assert second.get('/api/notifications/inbox').json()['unread']==2


def test_account_permission_race_rechecked_in_transaction(client,monkeypatch):
    enable(client);user=create(client,'teacher');teacher=signed('teacher')
    record=entry(teacher,lesson(teacher)['lessonKey'])
    original=api.journal.entry
    def changed(*args,**kwargs):
        result=original(*args,**kwargs)
        with api.connect() as db:db.execute('UPDATE accounts SET version=version+1 WHERE id=?',(user['id'],))
        return result
    monkeypatch.setattr(api.journal,'entry',changed)
    assert write(teacher,record).status_code==409
    with api.connect() as db:assert db.execute('SELECT count(*) FROM journal_entries').fetchone()[0]==0


def test_pupil_added_only_to_draft_sees_the_lesson_after_republishing(client,monkeypatch):
    monkeypatch.setattr(journal,'today',lambda:date(2026,8,17))
    record=graded(entry(client,lesson(client,'Математика')['lessonKey']))
    assert write(client,record,publish=True).status_code==200
    state=api.read('demo');next(g for g in state.groups if g.id=='g-math').studentIds.append('s2');api.save('demo',state)
    synced=entry(client,record['lessonKey'],syncRoster=True)
    assert write(client,synced,syncRoster=True).status_code==200
    assert report(client,'s2').json()['rows']==[]
    assert len(report(client,'s1').json()['rows'])==1
    current=entry(client,record['lessonKey'])
    assert write(client,current,publish=True).status_code==200
    assert len(report(client,'s2').json()['rows'])==1


def test_replacement_teacher_and_extra_lesson_permissions(client):
    enable(client);create(client,'teacher');create(client,'teacher',login='replacement',grants=[dict(workspace='demo',view='teacher',entityIds=['t3'])])
    original=signed('teacher');replacement=signed('replacement')
    saved=write(original,graded(entry(original,lesson(original)['lessonKey'])),publish=True).json()
    state=api.read('demo')
    state.datedLessons=[DatedLesson(id='replace',name='Замена',kind='change',placementId='eng',sourceDate=date(2026,8,17),onDate=date(2026,8,17),teacherIds=['t3']),DatedLesson(id='extra',name='Урок',kind='add',onDate=date(2026,8,18),groupId='g-core',profileId='secondary',subjectId='sub1',startTime='13:00',endTime='13:40',teacherIds=['t3'],roomIds=['r1'])]
    api.save('demo',state)
    old=entry(original,saved['lessonKey']);assert not old['writable']
    assert write(original,old).status_code==403
    changed=entry(replacement,saved['lessonKey'],syncRoster=True)
    assert changed['writable'] and write(replacement,changed,publish=True,syncRoster=True).status_code==200
    added=lesson(replacement,subject='Физика',day='2026-08-18')
    value=entry(replacement,added['lessonKey'],day='2026-08-18')
    assert {r['studentId'] for r in value['document']['content']['records']}=={'s2','s4'}
    assert write(replacement,value,publish=True).status_code==200


def test_replaced_teacher_cannot_withdraw_published_journal(client):
    enable(client);create(client,'parent');create(client,'teacher');create(client,'teacher',login='replacement',grants=[dict(workspace='demo',view='teacher',entityIds=['t3'])])
    original,replacement,parent=signed('teacher'),signed('replacement'),signed('parent')
    saved=write(original,graded(entry(original,lesson(original)['lessonKey'])),publish=True).json()
    state=api.read('demo')
    state.datedLessons=[DatedLesson(id='replace',name='Замена',kind='change',placementId='eng',sourceDate=date(2026,8,17),onDate=date(2026,8,17),teacherIds=['t3'])]
    api.save('demo',state)
    old=entry(original,saved['lessonKey'])
    assert not old['writable'] and not old['canWithdraw']
    assert original.post('/api/journal/withdraw?workspace=demo',json=dict(lessonKey=saved['lessonKey'],version=old['version'])).status_code==403
    assert len(report(parent).json()['rows'])==1
    current=entry(replacement,saved['lessonKey'])
    assert current['canWithdraw']
    assert replacement.post('/api/journal/withdraw?workspace=demo',json=dict(lessonKey=saved['lessonKey'],version=current['version'])).status_code==200
    assert report(parent).json()['rows']==[]


def test_future_lesson_allows_planning_but_not_marks(client,monkeypatch):
    from backend import journal
    monkeypatch.setattr(journal,'today',lambda:date(2026,8,16))
    record=entry(client,lesson(client)['lessonKey']);content=record['document']['content']
    content.update(topic='План урока',homework='Прочитать страницу 12',dueDate='2026-08-19',assessments=[dict(id='work',name='Проверочная',kind='points',maxScore=10,weight=1)])
    planned=write(client,record,publish=True);assert planned.status_code==200,planned.text
    assert entry(client,record['lessonKey'])['futureLesson'] is True
    saved=planned.json();saved['document']['content']['records'][0]['attendance']='present'
    refused=write(client,saved,publish=True);assert refused.status_code==422 and 'Урок ещё не прошёл' in refused.json()['detail']
    drafted=write(client,saved);assert drafted.status_code==200,drafted.text
    saved=drafted.json();pupil=saved['document']['content']['records'][0]
    pupil.update(attendance='unmarked',grades=[dict(assessmentId='work',score=5,label='',comment='')])
    assert write(client,saved,publish=True).status_code==422
    pupil['grades']=[dict(assessmentId='work',score=None,label='',comment='Подготовиться')]
    commented=write(client,saved,publish=True);assert commented.status_code==200,commented.text
    monkeypatch.setattr(journal,'today',lambda:date(2026,8,17))
    saved=commented.json();saved['document']['content']['records'][0]['attendance']='present'
    published=write(client,saved,publish=True);assert published.status_code==200,published.text
    assert entry(client,record['lessonKey'])['futureLesson'] is False


@pytest.mark.parametrize('field,value,fragment',[('maxScore',0,'Максимум баллов'),('maxScore',None,'Максимум баллов'),('maxScore','10','Максимум баллов'),('maxScore',True,'Максимум баллов'),('maxScore',10001,'Максимум баллов'),('weight',None,'Вес работы'),('weight',101,'Вес работы'),('weight',-1,'Вес работы')])
def test_assessment_numbers_have_russian_messages(client,field,value,fragment):
    record=graded(entry(client,lesson(client)['lessonKey']))
    record['document']['content']['assessments'][0][field]=value
    r=write(client,record);assert r.status_code==422
    assert fragment in str(r.json()['detail']) and 'Input should' not in str(r.json()['detail'])
    record['document']['content']['assessments'][0][field]=0 if field=='weight' else 10
    assert write(client,record).status_code==200


def test_assessment_and_topic_models_reject_nan_infinity_and_fractional_hours():
    from pydantic import ValidationError
    from backend.journal import Assessment,TopicReference
    for field,value in [('maxScore',float('nan')),('maxScore',float('inf')),('weight',float('nan')),('weight',float('-inf'))]:
        with pytest.raises(ValidationError,match='Максимум баллов|Вес работы'):Assessment(id='a',name='Работа',**{field:value})
    for units in [1.5,0,13,None,'2']:
        with pytest.raises(ValidationError,match='Часов по теме'):TopicReference(planId='a'*32,version=1,topicId='intro',units=units)
    assert TopicReference(planId='a'*32,version=1,topicId='intro',units=12).units==12


def pages(reader,student='s1',**params):
    return reader.get('/api/journal/report',params=dict(workspace='demo',studentId=student,start='2026-08-01',end='2026-09-30')|params)


def test_report_pages_by_keyset_with_summary_on_first_page(client):
    for day in [DAY,'2026-08-24','2026-08-31']:
        assert write(client,graded(entry(client,lesson(client,day=day)['lessonKey'],day=day)),publish=True).status_code==200
    first=pages(client,limit=2);assert first.status_code==200,first.text;first=first.json()
    assert [r['lesson']['date'] for r in first['rows']]==[DAY,'2026-08-24'] and first['nextAfter']
    assert first['summary']['attendance']['present']==3 and first['source']=='published'
    second=pages(client,limit=2,after=first['nextAfter']);assert second.status_code==200,second.text;second=second.json()
    assert [r['lesson']['date'] for r in second['rows']]==['2026-08-31'] and second['summary'] is None and second['nextAfter'] is None
    keys=[r['lessonKey'] for r in first['rows']+second['rows']];assert len(set(keys))==3
    whole=pages(client).json();assert [r['lessonKey'] for r in whole['rows']]==keys and whole['nextAfter'] is None and whole['summary']==first['summary']
    # A page of exactly the remaining size ends the sequence without an empty extra page.
    assert pages(client,limit=3).json()['nextAfter'] is None
    stale=pages(client,limit=1,after=f'{DAY}|00:00|'+'0'*64);assert stale.status_code==200 and len(stale.json()['rows'])==1
    assert pages(client,after='bad').status_code==422
    assert pages(client,limit=0).status_code==422 and pages(client,limit=201).status_code==422
    assert pages(client,end='2027-09-30').status_code==422


def test_report_paging_respects_teacher_scope_and_withdrawn_lessons(client):
    enable(client);create(client,'teacher');create(client,'teacher',login='unrelated',grants=[dict(workspace='demo',view='teacher',entityIds=['t1'])])
    published=[write(client,graded(entry(client,lesson(client,day=day)['lessonKey'],day=day)),publish=True).json() for day in [DAY,'2026-08-24']]
    unrelated=pages(signed('unrelated'),limit=1).json()
    assert unrelated['rows']==[] and unrelated['nextAfter'] is None and unrelated['summary']['attendance']['present']==0
    teacher=signed('teacher');first=pages(teacher,limit=1).json()
    assert len(first['rows'])==1 and first['nextAfter'] and first['summary']['attendance']['present']==2
    second=pages(teacher,limit=1,after=first['nextAfter']).json()
    assert len(second['rows'])==1 and second['nextAfter'] is None and second['rows'][0]['lessonKey']!=first['rows'][0]['lessonKey']
    assert client.post('/api/journal/withdraw?workspace=demo',json=dict(lessonKey=published[0]['lessonKey'],version=published[0]['version'])).status_code==200
    assert [r['lesson']['date'] for r in pages(teacher).json()['rows']]==['2026-08-24']


def test_report_teacher_filter_keeps_later_pages_full(client):
    enable(client);create(client,'teacher')
    # Another teacher's Maths lesson lies between the two lessons of the assigned teacher.
    for subject,day in [('Английский язык',DAY),('Математика','2026-08-24'),('Английский язык','2026-08-31')]:
        assert write(client,graded(entry(client,lesson(client,subject,day)['lessonKey'],day=day)),publish=True).status_code==200
    assert [r['lesson']['date'] for r in pages(client).json()['rows']]==[DAY,'2026-08-24','2026-08-31']
    teacher=signed('teacher');first=pages(teacher,limit=1).json()
    assert [r['lesson']['date'] for r in first['rows']]==[DAY] and first['nextAfter']
    second=pages(teacher,limit=1,after=first['nextAfter']).json()
    assert [r['lesson']['date'] for r in second['rows']]==['2026-08-31'] and second['nextAfter'] is None


def test_options_panels_by_role(client):
    from backend import final_grades
    enable(client);teacher=create(client,'teacher');create(client,'student');create(client,'parent')
    def panels(reader):
        response=reader.get('/api/journal/options?workspace=demo');assert response.status_code==200,response.text
        assert response.json()['absenceNotices']==response.json()['panels']['absences']
        return response.json()['panels']
    def flags(**on):return dict(finalGrades=False,absences=False,attendance=False,attendanceGrid=False,coverage=False,periods=False,courseTable=False)|on
    assert panels(client)==dict(finalGrades=True,absences=True,attendance=True,attendanceGrid=True,coverage=True,periods=True,courseTable=True)
    assert panels(signed('teacher'))==flags(attendanceGrid=True,coverage=True,periods=True,courseTable=True)
    assert panels(signed('parent'))==flags(absences=True)
    assert panels(signed('student'))==flags()
    with api.connect() as db:
        final_grades.tables(db)
        db.execute("INSERT INTO grading_rows VALUES ('demo','row1','book1','s2','sub0','c-demo-a',?,1,'draft','{}',NULL)",(teacher['id'],))
    assert panels(signed('teacher'))['finalGrades'] is True and panels(signed('student'))['finalGrades'] is False
    with api.connect() as db:
        db.execute("INSERT INTO final_results VALUES ('demo','period1','s1','sub0','row2',1,'{}')")
    assert panels(signed('student'))['finalGrades'] is True and panels(signed('parent'))['finalGrades'] is True
    assert panels(signed('teacher'))['finalGrades'] is True
