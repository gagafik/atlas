"""Plans, live teaching permissions, immutable lesson pins and progress transitions."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import date, datetime, timezone

import pytest
from backend import api, curriculum
from backend.models import DatedLesson
from test_access import client, enable, create, signed
from test_journal import entry, lesson, write, report

BASE='/api/journal/curriculum'


def request(c, path='', method='GET', body=None, **params):
    return c.request(method,BASE+path,params=dict(workspace='demo')|params,json=body)


def content(**extra):
    w=api.read('demo').workloads[0]
    return dict(title='Английский язык: годовой план',groupId=w.groupId,subjectId=w.subjectId,start='2026-08-17',end='2027-06-11',
        topics=[dict(id='intro',title='Знакомство',hours=2,plannedDate='2026-08-17',standard='Представиться и задать вопрос'),
                dict(id='family',title='Семья',hours=3,plannedDate=None,standard='Описать семью')])|extra


def save(c, plan=None, **extra):
    return request(c,'/'+plan['id'] if plan else '', 'PUT' if plan else 'POST',
                   dict(version=plan['version'] if plan else 0,content=(deepcopy(plan['content']) if plan else content())|extra))


def new(c, **extra):
    r=save(c,**extra);assert r.status_code==200,r.text
    return r.json()


def attach(c, plan, record=None, topic='intro', units=1, publish=False):
    record=record or entry(c,lesson(c)['lessonKey'])
    record['document']['content']['curriculum']=dict(planId=plan['id'],version=plan['version'],topicId=topic,units=units,title='FORGED',standard='FORGED')
    return write(c,record,publish=publish)


def progress(c, plan, **params):
    r=request(c,f"/{plan['id']}/progress",**params);assert r.status_code==200,r.text
    return r.json()


def staff(c):
    enable(c);create(c,'teacher');create(c,'teacher',login='other',grants=[dict(workspace='demo',view='teacher',entityIds=['t1'])])
    create(c,'student');create(c,'parent')
    return signed('teacher'),signed('other'),signed('student'),signed('parent')


def test_shared_plan_versions_copy_archive_and_immutable_scope(client):
    teacher,other,pupil,parent=staff(client);before=api.read('demo');p=new(teacher)
    assert p['version']==1 and progress(teacher,p)['totals']==dict(planned=5,actual=0,review=0,upcoming=0)
    assert request(other,'/'+p['id']).status_code==403
    for c in [pupil,parent]:
        assert request(c,'/options').status_code==403
        assert save(c).status_code==403
        assert request(c,'/'+p['id']).status_code==403
    assert request(teacher,'/'+p['id'],workspace='school').status_code==403
    p2=save(teacher,p,title='Новая редакция').json()
    assert p2['version']==2
    assert request(teacher,'/'+p['id'],version=1).json()['content']['title']==p['content']['title']
    assert [h['version'] for h in request(teacher,f"/{p['id']}/history").json()['items']]==[2,1]
    assert save(teacher,p).status_code==409
    assert save(teacher,p2,start='2026-08-18').status_code==422
    assert save(teacher).status_code==409
    copied=request(teacher,f"/{p['id']}/copy",'POST',dict(version=1,groupId=p['content']['groupId'],start='2027-08-17',end='2028-06-11')).json()
    assert copied['version']==1 and copied['content']['topics'][0]['plannedDate'] is None
    assert copied['content']['topics'][0]['id']!='intro' and progress(teacher,copied)['totals']['actual']==0
    archived=request(teacher,f"/{p['id']}/archive",'POST',dict(version=2,archived=True)).json()
    assert archived['archived'] and archived['version']==3
    assert save(teacher,archived).status_code==409
    replacement=new(teacher)
    assert request(teacher,f"/{p['id']}/archive",'POST',dict(version=3,archived=False)).status_code==409
    assert request(teacher,'',groupId=p['content']['groupId'],subjectId=p['content']['subjectId'],archived=True).json()['items'][0]['id']==p['id']
    assert api.read('demo')==before


def test_current_additional_teacher_grants_rechecked_after_workload_change(client):
    teacher,other,_,_=staff(client);p=new(teacher)
    s=api.read('demo');s.workloads[0].additionalTeacherIds=['t1'];api.save('demo',s)
    assert request(other,'/'+p['id']).status_code==200
    assert save(other,p,title='Совместная редакция').status_code==200
    s=api.read('demo');s.workloads[0].additionalTeacherIds=[];api.save('demo',s)
    assert request(other,'/'+p['id']).status_code==403
    assert request(other,f"/{p['id']}/progress").status_code==403


def test_publication_pins_canonical_topic_and_draft_does_not_change_progress(client):
    teacher,_,_,parent=staff(client);p=new(teacher)
    record=attach(teacher,p).json();key=record['lessonKey']
    assert record['document']['content']['curriculum']['title']=='Знакомство'
    assert progress(teacher,p)['totals']['actual']==0 and report(parent).json()['rows']==[]
    record=write(teacher,record,publish=True).json()
    assert progress(teacher,p)['totals']['actual']==1
    p2=save(teacher,p,topics=[p['content']['topics'][1]]).json()
    r=attach(teacher,p2,record,topic='family',units=2);assert r.status_code==200,r.text
    record=r.json();pub=report(parent).json()['rows'][0]['content']['curriculum']
    assert pub['title']=='Знакомство' and pub['version']==1
    current=progress(teacher,p);assert current['topics'][-1]['retired'] and current['totals']['actual']==1
    record=write(teacher,record,publish=True).json()
    assert progress(teacher,p)['totals']['actual']==2
    assert len(progress(teacher,p)['lessons'])==1  # republishing never doubles hours
    assert teacher.post('/api/journal/withdraw?workspace=demo',json=dict(lessonKey=key,version=record['version'])).status_code==200
    assert progress(teacher,p)['totals']['actual']==0 and report(parent).json()['rows']==[]
    old=teacher.get('/api/journal/history',params=dict(workspace='demo',lessonKey=key,version=2)).json()[0]['document']
    assert old['content']['curriculum']==pub


def test_plan_binding_rejects_wrong_scope_stale_version_missing_topic_atomically(client):
    p=new(client);p2=save(client,p,title='Changed').json()
    assert attach(client,p).status_code==409
    assert attach(client,p2,topic='missing').status_code==422
    zero=attach(client,p2,units=0);assert zero.status_code==422 and 'Часов по теме' in str(zero.json()['detail'])
    math=entry(client,lesson(client,subject='Математика')['lessonKey'])
    assert attach(client,p2,math).status_code==422
    r=attach(client,p2);assert r.status_code==200,r.text
    record=r.json()
    p3=request(client,f"/{p['id']}/archive",'POST',dict(version=2,archived=True)).json()
    assert write(client,record,publish=True).status_code==200  # retained old version is valid
    nextweek=entry(client,lesson(client,day='2026-08-24')['lessonKey'],day='2026-08-24')
    assert attach(client,p2,nextweek).status_code==409
    with api.connect() as db:
        assert db.execute('SELECT count(*) FROM curriculum_bindings').fetchone()[0]==1
        assert db.execute('SELECT count(*) FROM curriculum_publications').fetchone()[0]==1


def test_moved_cancelled_missing_and_future_publications_need_review(client,monkeypatch):
    monkeypatch.setattr(curriculum,'now',lambda:datetime(2026,8,16,20,tzinfo=timezone.utc)) # Aug 17 in school timezone
    p=new(client);record=attach(client,p,publish=True).json();key=record['lessonKey']
    assert progress(client,p)['totals']['actual']==1
    assert progress(client,p,asOf='2026-08-16')['totals']['upcoming']==1
    assert progress(client,p,asOf='2029-01-01')['asOf']=='2026-08-17'
    s=api.read('demo');s.datedLessons=[DatedLesson(id='move',name='Перенос',kind='change',placementId='eng',sourceDate=date(2026,8,17),onDate=date(2026,8,18),startTime='14:00',endTime='14:40')];api.save('demo',s)
    assert progress(client,p)['totals']['review']==1
    synced=entry(client,key,syncRoster=True)
    r=write(client,synced,publish=True,syncRoster=True);assert r.status_code==200,r.text
    assert progress(client,p)['totals']['upcoming']==1
    s=api.read('demo');s.datedLessons=[DatedLesson(id='cancel',name='Отмена',kind='cancel',placementId='eng',sourceDate=date(2026,8,17),onDate=date(2026,8,17))];api.save('demo',s)
    assert progress(client,p)['totals']['review']==1
    s=api.read('demo');s.datedLessons=[];s.placements=[x for x in s.placements if x.id!='eng'];api.save('demo',s)
    assert progress(client,p)['totals']['review']==1


def test_substitute_can_select_matching_plan_without_own_workload(client):
    teacher,other,_,_=staff(client);p=new(teacher)
    s=api.read('demo');s.datedLessons=[DatedLesson(id='sub',name='Замена',kind='change',placementId='eng',sourceDate=date(2026,8,17),onDate=date(2026,8,17),teacherIds=['t1'])];api.save('demo',s)
    key=lesson(other)['lessonKey']
    r=request(other,'/lesson',day='2026-08-17',lessonKey=key);assert r.status_code==200,r.text
    assert r.json()['items'][0]['id']==p['id']
    assert request(other,'/'+p['id']).status_code==403
    assert request(teacher,'/lesson',day='2026-08-17',lessonKey=key).status_code==403
    assert attach(other,p,publish=True).status_code==200
    assert progress(teacher,p)['totals']['actual']==1


def test_simultaneous_plan_updates_one_winner(client):
    p=new(client)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda title: save(client,p,title=title).status_code,['one','two']))
    assert sorted(results)==[200,409]
    assert request(client,'/'+p['id']).json()['version']==2


def test_plan_validation_and_separate_school_workspace(client):
    for patch in [dict(end='2026-08-16'),dict(end='2030-01-01'),dict(topics=[]),dict(topics=[content()['topics'][0]]*2),
                  dict(topics=[content()['topics'][0]|dict(plannedDate='2026-01-01')])]:
        assert save(client,**patch).status_code==422
    p=new(client)
    assert request(client,'/'+p['id'],workspace='school').status_code==404
    assert request(client,f"/{p['id']}/copy",'POST',dict(version=1,groupId=p['content']['groupId'],start='2027-08-17',end='2027-01-01')).status_code==422


def test_grading_keeps_distinct_draft_and_published_topic_and_progress(client):
    from test_homework_grading import ready, grade, options, payload
    record,sent,_,parent,teacher=ready(client);p=new(teacher);key=record['lessonKey']
    record=attach(teacher,p,record,publish=True).json()
    record=attach(teacher,p,record,topic='family',units=2).json()
    r=grade(teacher,key,sent,payload(options(teacher,key)));assert r.status_code==200,r.text
    assert entry(teacher,key)['document']['content']['curriculum']['topicId']=='family'
    assert report(parent).json()['rows'][0]['content']['curriculum']['topicId']=='intro'
    assert progress(teacher,p)['totals']['actual']==1


@pytest.mark.parametrize('change',['account','school'])
def test_write_rechecks_captured_account_and_school_inside_transaction(client,monkeypatch,change):
    teacher,_,_,_=staff(client);original=api.curriculum.recheck
    def altered(db,user,workspace,school):
        if change=='account': db.execute("UPDATE accounts SET version=version+1 WHERE login='teacher'")
        else: db.execute("UPDATE workspaces SET revision=revision+1 WHERE id='demo'")
        return original(db,user,workspace,school)
    monkeypatch.setattr(api.curriculum,'recheck',altered)
    assert save(teacher).status_code==409
    with api.connect() as db:
        assert db.execute('SELECT count(*) FROM curriculum_plans').fetchone()[0]==0


def test_extra_lesson_matches_subject_and_group_without_workload_id(client):
    teacher,other,_,_=staff(client);p=new(teacher);s=api.read('demo')
    s.datedLessons=[DatedLesson(id='extra',name='Дополнительный урок',kind='add',onDate=date(2026,8,18),profileId='primary',
        groupId=p['content']['groupId'],subjectId=p['content']['subjectId'],teacherIds=['t1'],startTime='14:00',endTime='14:40')]
    api.save('demo',s);key=lesson(other,day='2026-08-18')['lessonKey']
    assert request(other,'/lesson',day='2026-08-18',lessonKey=key).json()['items'][0]['id']==p['id']
    record=entry(other,key,day='2026-08-18')
    assert attach(other,p,record,publish=True).status_code==200


def test_closed_period_archive_and_backup_keep_pinned_plan_version(client,tmp_path,monkeypatch):
    from test_journal_periods import period,close,archive
    from tools.backup_school import create_backup
    p=new(client);record=attach(client,p,publish=True).json();key=record['lessonKey']
    closed=close(client,period(client));p2=save(client,p,title='Новая редакция').json()
    assert attach(client,p2,record).status_code==403
    assert archive(client,closed,lessonKey=key).json()['document']['content']['curriculum']['version']==1
    source=api.DB;backup=tmp_path/'backup';create_backup(source,backup);monkeypatch.setattr(api,'DB',backup/source.name)
    # The copy answers the same: both plan versions, the archived lesson pinned to version 1, its hours counted once.
    assert [h['version'] for h in request(client,f"/{p['id']}/history").json()['items']]==[2,1]
    assert request(client,'/'+p['id'],version=1).json()['content']['title']==p['content']['title']
    assert archive(client,closed,lessonKey=key).json()['document']['content']['curriculum']['version']==1
    assert progress(client,p)['totals']['actual']==1


def test_progress_pages_list_every_lesson_once_without_student_records(client):
    import json
    from seeding import read_row,insert_row
    p=new(client);record=attach(client,p,publish=True).json();keys={record['lessonKey']}
    with api.connect() as db:
        template=read_row(db,'journal_entries')
        for i in range(55):
            key=f'{i:064x}';keys.add(key);insert_row(db,'journal_entries',template,lesson_key=key)
            insert_row(db,'curriculum_publications',workspace='demo',lesson_key=key,plan_id=p['id'],topic_id='intro',units=1,day='2026-08-17')
    first=progress(client,p);pages=[first];listed=[l['lessonKey'] for l in first['lessons']]
    assert first['hasMore']  # the seeded lessons do not fit one page
    while pages[-1]['hasMore']:
        assert len(pages)<100  # the offset must advance
        pages.append(progress(client,p,lessonOffset=len(listed)));listed+=[l['lessonKey'] for l in pages[-1]['lessons']]
    assert len(listed)==len(set(listed))==first['totalLessons']==len(keys) and set(listed)==keys
    assert all(page['totals']==first['totals'] for page in pages)
    assert 'student' not in json.dumps(first) and 'records' not in json.dumps(first)
