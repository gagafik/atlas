from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json

import pytest
from fastapi import HTTPException
from backend import api
from backend.homework_grading import HomeworkGrading
from backend.models import DatedLesson
from test_access import client, signed
from test_homework import setup, get, answer, review
from test_journal import entry, write, report, DAY
from test_journal_periods import period, close, archive, action, period_clock
from test_journal_coverage import report as coverage, english


def options(c,key):
    r=c.get(f'/api/journal/homework/{key}/grading?workspace=demo&studentId=s1')
    assert r.status_code==200,r.text
    return r.json()


def payload(o,**extra):
    return dict(journalVersion=o['journalVersion'],revision=o['revision'],assessmentId='work',score=9,replaceExisting=True)|extra


def grade(c,key,sent,grading,**extra):
    return c.post(f'/api/journal/homework/{key}/review?workspace=demo',json=dict(studentId='s1',version=sent['version'],
        assignment=sent['assignment'],decision='accepted',comment='Проверено',grading=grading)|extra)


def ready(c):
    record,pupil,parent,teacher=setup(c);key=record['lessonKey']
    r=answer(pupil,key,get(pupil,key));assert r.status_code==200,r.text
    return record,r.json(),pupil,parent,teacher


def current_grade(document,id='work',student='s1'):
    return next(g for p in document['content']['records'] if p['studentId']==student for g in p['grades'] if g['assessmentId']==id)


def new_work(id='homework',kind='points',**extra):
    return dict(id=id,name='Домашняя работа',kind=kind,maxScore=20,weight=2)|extra


def test_grade_publication_updates_report_history_notification_and_keeps_schedule(client):
    record,sent,pupil,parent,teacher=ready(client);key=record['lessonKey'];before=api.read('demo')
    o=options(teacher,key);assert o['allowed'] and not o['hasDraftChanges']
    assert o['assessments'][0]['publishedGrade']['score']==8
    response=grade(teacher,key,sent,payload(o,comment='Обоснование верно'));assert response.status_code==200,response.text
    saved=entry(client,key);assert saved['version']==saved['publishedVersion']==record['version']+1
    assert current_grade(saved['document'])['score']==9
    personal=report(parent).json();assert personal['summary']['subjects'][0]['percentage']==90
    assert personal['rows'][0]['record']['attendance']=='present'
    evidence=response.json()['submitted']['grading'];assert evidence['assessment']['id']=='work'
    assert evidence['grade']['score']==9 and evidence['publishedVersion']==saved['publishedVersion']
    assert get(parent,key)['submitted']['grading']==evidence
    history=parent.get(f'/api/journal/homework/{key}/history?workspace=demo&studentId=s1').json()['items']
    assert history[0]['submitted']['grading']==evidence and 'grading' not in history[-1]['submitted']
    notifications=[n['action'] for n in parent.get('/api/notifications/inbox').json()['items']]
    assert 'homework.reviewed' in notifications and notifications.count('journal.updated')==2
    assert api.read('demo')==before


def test_only_target_grade_published_and_unrelated_draft_survives_two_revisions(client):
    record,sent,_,parent,teacher=ready(client);key=record['lessonKey'];original=deepcopy(record['document'])
    content=record['document']['content'];content['topic']='PRIVATE TOPIC';content['homework']='PRIVATE NEW TASK'
    content['records'][0]['comment']='PRIVATE PUPIL COMMENT';content['records'][1]['grades'][0]['score']=1
    draft=write(teacher,record).json();o=options(teacher,key);assert o['hasDraftChanges']
    response=grade(teacher,key,sent,payload(o));assert response.status_code==200,response.text
    saved=entry(client,key);assert saved['version']==draft['version']+2 and saved['publishedVersion']==draft['version']+1
    assert saved['document']['content']['topic']=='PRIVATE TOPIC'
    assert saved['document']['content']['homework']=='PRIVATE NEW TASK'
    assert current_grade(saved['document'])['score']==9 and current_grade(saved['document'],student='s2')['score']==1
    assert 'PRIVATE' not in json.dumps(report(parent).json())
    assert english(coverage(client).json())['hasDraftChanges']
    history=client.get('/api/journal/history',params=dict(workspace='demo',lessonKey=key)).json()
    assert [h['action'] for h in history[:2]]==['journal.draft_saved','journal.published']
    published=client.get('/api/journal/history',params=dict(workspace='demo',lessonKey=key,version=saved['publishedVersion'])).json()[0]['document']
    assert published['content']['topic']==original['content']['topic']
    assert current_grade(published,student='s2')['score']==7
    p=period(client);preview=action(client,p,'preview-close').json();assert preview['draftChanges']==1
    closed=close(client,p)
    archived=archive(client,closed,lessonKey=key).json()
    assert archived['publishedVersion']==saved['publishedVersion'] and archived['document']==published


def test_archived_grade_is_input_to_final_gradebook_without_publishing_private_draft(client):
    from test_final_grades import prepare,sheet
    record,sent,_,parent,teacher=ready(client);key=record['lessonKey']
    record['document']['content']['topic']='PRIVATE TOPIC';write(teacher,record).raise_for_status()
    grade(teacher,key,sent,payload(options(teacher,key))).raise_for_status()
    try:
        p=close(client,period(client));book=prepare(client,p)
        row=next(r for r in sheet(client,book).json()['items'] if r['studentId']=='s1' and r['source']['numeric'])
        assert row['source']['numeric']==1 and float(row['source']['weightedSum'])==90
        assert 'PRIVATE' not in json.dumps(archive(client,p,lessonKey=key).json())
    finally:api.shutdown_gradebook_jobs()


@pytest.mark.parametrize('kind',['points','label'])
def test_new_assessment_records_one_pupil_and_preserves_grade_format(client,kind):
    record,sent,_,parent,teacher=ready(client);key=record['lessonKey'];a=new_work(kind=kind,weight=0)
    mark=payload(options(teacher,key),assessmentId=a['id'],newAssessment=a,score=0 if kind=='points' else None,label='' if kind=='points' else 'A*',replaceExisting=False)
    response=grade(teacher,key,sent,mark);assert response.status_code==200,response.text
    doc=entry(client,key)['document'];assert len(doc['content']['assessments'])==2
    assert current_grade(doc,'homework')['score']==mark['score'] and current_grade(doc,'homework')['label']==mark['label']
    assert len(doc['content']['records'][1]['grades'])==1
    assert report(parent).json()['summary']['subjects'][0]['percentage']==80


def test_existing_grade_requires_explicit_replacement_including_private_value(client):
    record,sent,_,_,teacher=ready(client);key=record['lessonKey']
    record['document']['content']['records'][0]['grades'][0]['score']=5;write(teacher,record).raise_for_status()
    o=options(teacher,key);assert o['assessments'][0]['draftGrade']['score']==5 and o['assessments'][0]['publishedGrade']['score']==8
    before=entry(client,key)
    r=grade(teacher,key,sent,payload(o,replaceExisting=False));assert r.status_code==409
    assert entry(client,key)==before and get(teacher,key)['status']=='submitted'
    grade(teacher,key,sent,payload(o,replaceExisting=True)).raise_for_status()
    assert current_grade(entry(client,key)['document'])['score']==9


@pytest.mark.parametrize('alter',[dict(maxScore=20),dict(kind='label'),None])
def test_changed_or_removed_draft_assessment_is_not_silently_restored(client,alter):
    record,sent,_,_,teacher=ready(client);key=record['lessonKey'];content=record['document']['content']
    if alter is None:
        content['assessments']=[]
        for p in content['records']:p['grades']=[]
    else:
        content['assessments'][0].update(alter)
        if alter.get('kind')=='label':
            for p in content['records']:p['grades'][0].update(score=None,label='B')
    write(teacher,record).raise_for_status();o=options(teacher,key)
    assert not o['assessments'][0]['selectable']
    assert grade(teacher,key,sent,payload(o)).status_code==409
    assert get(teacher,key)['status']=='submitted'


@pytest.mark.parametrize('bad',[dict(score=11),dict(score=3,label='A'),dict(score=None),dict(assessmentId='missing'),dict(score=-1)])
def test_invalid_grade_never_partially_accepts_work(client,bad):
    record,sent,_,_,teacher=ready(client);key=record['lessonKey'];before=entry(client,key)
    response=grade(teacher,key,sent,payload(options(teacher,key),**bad));assert response.status_code in [409,422]
    assert entry(client,key)==before and get(teacher,key)['status']=='submitted'


def test_stale_journal_and_submission_versions_cannot_overwrite_another_action(client):
    record,sent,_,_,teacher=ready(client);key=record['lessonKey'];mark=payload(options(teacher,key))
    write(teacher,record).raise_for_status()
    assert grade(teacher,key,sent,mark).status_code==409
    mark=payload(options(teacher,key));review(teacher,key,sent).raise_for_status()
    assert grade(teacher,key,sent,mark).status_code==409
    assert current_grade(entry(client,key)['document'])['score']==8


def test_two_concurrent_graders_have_one_atomic_winner(client):
    record,sent,_,_,teacher=ready(client);key=record['lessonKey'];o=options(teacher,key)
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses=list(pool.map(lambda score:grade(teacher,key,sent,payload(o,score=score)),[7,9]))
    assert sorted(r.status_code for r in responses)==[200,409]
    assert entry(client,key)['version']==record['version']+1
    assert get(teacher,key)['version']==sent['version']+1


def test_failure_after_journal_write_rolls_back_review_grade_history_and_notifications(client,monkeypatch):
    record,sent,_,parent,teacher=ready(client);key=record['lessonKey'];before=entry(client,key)
    inbox=parent.get('/api/notifications/inbox').json()
    with api.connect() as db:count=db.execute('SELECT count(*) FROM journal_history').fetchone()[0]
    def fail(*args,**kwargs):raise HTTPException(409,'Synthetic review failure')
    monkeypatch.setattr(api.homework,'persist',fail)
    assert grade(teacher,key,sent,payload(options(teacher,key))).status_code==409
    assert entry(client,key)==before and get(teacher,key)['status']=='submitted'
    assert parent.get('/api/notifications/inbox').json()==inbox
    with api.connect() as db:assert db.execute('SELECT count(*) FROM journal_history').fetchone()[0]==count


@pytest.mark.parametrize('change',['closure','account','school'])
def test_mutation_between_preparation_and_write_is_rechecked(client,monkeypatch,change):
    record,sent,_,_,teacher=ready(client);key=record['lessonKey'];mark=payload(options(teacher,key));original=HomeworkGrading.prepare
    def changed(self,ctx):
        result=original(self,ctx)
        if change=='closure':close(client,period(client))
        elif change=='school':api.save('demo',api.read('demo'))
        else:
            with api.connect() as db:db.execute("UPDATE accounts SET version=version+1 WHERE login='teacher'")
        return result
    monkeypatch.setattr(HomeworkGrading,'prepare',changed)
    assert grade(teacher,key,sent,mark).status_code==409
    assert current_grade(entry(client,key)['document'])['score']==8
    with api.connect() as db:
        value=json.loads(db.execute('SELECT document FROM homework_submissions').fetchone()[0])
        assert value['submitted']['review'] is None


def test_closed_period_blocks_grade_but_not_feedback_and_reopen_restores_grading(client):
    record,sent,_,_,teacher=ready(client);key=record['lessonKey'];mark=payload(options(teacher,key));p=close(client,period(client))
    o=options(teacher,key);assert not o['allowed'] and 'закрыт' in o['reason']
    assert grade(teacher,key,sent,mark).status_code==409
    response=review(teacher,key,sent);assert response.status_code==200
    action(client,p,'reopen').raise_for_status()
    grade(teacher,key,response.json(),payload(options(teacher,key))).raise_for_status()
    assert current_grade(entry(client,key)['document'])['score']==9


def test_scope_assignment_roster_sync_and_return_rules(client):
    record,sent,pupil,parent,teacher=ready(client);key=record['lessonKey'];mark=payload(options(teacher,key))
    for account in [pupil,parent,signed('unrelated')]:
        assert account.get(f'/api/journal/homework/{key}/grading?workspace=demo&studentId=s1').status_code==403
        assert grade(account,key,sent,mark).status_code==403
    assert 's2' not in json.dumps(options(teacher,key))
    assert grade(teacher,key,sent,mark,decision='returned',comment='Исправьте').status_code==422
    state=api.read('demo');state.datedLessons=[DatedLesson(id='move',name='Перенос',kind='change',placementId='eng',sourceDate=DAY,onDate='2026-08-18')];api.save('demo',state)
    o=options(teacher,key);assert not o['allowed']
    assert grade(teacher,key,sent,payload(o)).status_code==409
    assert get(parent,key)['status']=='submitted'


def test_new_assessment_respects_limit_and_return_preserves_recorded_grade(client):
    record,sent,pupil,_,teacher=ready(client);key=record['lessonKey']
    record['document']['content']['assessments'] += [new_work(id='a'+str(i)) for i in range(9)]
    write(teacher,record,publish=True).raise_for_status();o=options(teacher,key);assert not o['canCreate']
    assert grade(teacher,key,sent,payload(o,assessmentId='new',newAssessment=new_work(id='new'))).status_code==422
    response=grade(teacher,key,sent,payload(o));response.raise_for_status()
    graded=response.json()['submitted']['grading']
    returned=review(teacher,key,response.json(),'returned','Уточните обоснование');returned.raise_for_status()
    assert returned.json()['submitted']['grading']==graded
    assert current_grade(entry(client,key)['document'])['score']==9
    new=answer(pupil,key,get(pupil,key));new.raise_for_status()
    assert 'grading' not in new.json()['submitted']
    assert current_grade(entry(client,key)['document'])['score']==9
