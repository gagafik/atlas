from datetime import date
import pytest
from backend import api
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
    assert pupil.get('/api/notifications/inbox').json()['unread']==1


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


def test_sync_membership_keeps_historical_marks_and_adds_unmarked_pupils(client):
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
    assert first.get('/api/notifications/inbox').json()['unread']==2
    assert second.get('/api/notifications/inbox').json()['unread']==1


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


def test_published_members_index_is_independent_of_draft_and_rebuildable(client):
    record=graded(entry(client,lesson(client,'Математика')['lessonKey']))
    assert write(client,record,publish=True).status_code==200
    state=api.read('demo');next(g for g in state.groups if g.id=='g-math').studentIds.append('s2');api.save('demo',state)
    synced=entry(client,record['lessonKey'],syncRoster=True)
    assert write(client,synced,syncRoster=True).status_code==200
    assert report(client,'s2').json()['rows']==[]
    assert len(report(client,'s1').json()['rows'])==1
    with api.connect() as db:db.execute('DROP TABLE journal_members')
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
