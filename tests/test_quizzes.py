from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
from uuid import uuid4

import pytest
from backend import api, quizzes, quiz_bank, quiz_attempts, quiz_catalog
from backend.models import DatedLesson
from test_access import client, signed, create
from test_homework import setup
from test_journal import DAY

NOW=datetime(2026,8,17,9,tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    for module in [quizzes,quiz_bank,quiz_attempts,quiz_catalog]:monkeypatch.setattr(module,'now',lambda:NOW)


def question(reader,id='choice',**extra):
    content=dict(kind='single',text='Выберите 2 + 2',points=2,options=[dict(id='a',text='4'),dict(id='b',text='5')],correctIds=['a'],explanation='Эталон только после разбора')|extra
    r=reader.put(f'/api/journal/quiz-bank/{id}?workspace=demo',json=dict(version=0,subject='Английский язык',tags=['пример'],question=content))
    assert r.status_code==200,r.text
    return r.json()


def body(record,questions,**extra):
    definition=dict(title='Контрольная',instructions='Прочитайте вопросы',variants=[dict(id='v1',name='Вариант 1',questions=[dict(id=q['id'],version=q['version']) for q in questions])],
                    opensAt=(NOW-timedelta(hours=1)).isoformat(),closesAt=(NOW+timedelta(hours=1)).isoformat(),timeLimitMinutes=10,maxAttempts=2,shuffleQuestions=True,shuffleOptions=True)
    definition.update(extra.pop('definition',{}))
    return dict(version=0,revision=api.read('demo').revision,lessonKey=record['lessonKey'],day=DAY,definition=definition,studentIds=['s1','s2'],publish=True)|extra


def ready(client,**extra):
    record,pupil,parent,teacher=setup(client);q=question(teacher)
    payload=body(record,[q],**extra);r=teacher.put('/api/journal/quizzes/test-one?workspace=demo',json=payload)
    assert r.status_code==200,r.text
    return r.json(),pupil,parent,teacher,payload


def detail(reader,id='test-one',student='s1'):
    r=reader.get(f'/api/journal/quizzes/{id}',params=dict(workspace='demo',studentId=student));assert r.status_code==200,r.text
    return r.json()


def start(reader,current,**extra):
    return reader.post(f'/api/journal/quizzes/{current["id"]}/attempts?workspace=demo',json=dict(version=current['version'],studentId='s1',requestId=uuid4().hex)|extra)


def save(reader,attempt,answers=None,submit=False,**extra):
    return reader.put(f'/api/journal/quizzes/{attempt["quizId"]}/attempts/{attempt["id"]}?workspace=demo',json=dict(version=attempt['version'],answers=answers or {},submit=submit)|extra)


def action(reader,current,kind):
    return reader.post(f'/api/journal/quizzes/{current["id"]}/actions?workspace=demo',json=dict(version=current['version'],action=kind,reason='Решение учителя'))


def attempt(reader,current):
    r=reader.get(f'/api/journal/quizzes/{current["quizId"]}/attempts/{current["id"]}?workspace=demo');assert r.status_code==200,r.text
    return r.json()


def test_bank_publication_attempt_review_release_family_isolation(client):
    quiz,pupil,parent,teacher,_=ready(client);before=api.read('demo')
    for reader in [pupil,parent]:
        assert reader.get('/api/journal/quiz-bank?workspace=demo').status_code==403
        view=detail(reader)
        assert 'document' not in view and 'students' not in view and 'correctIds' not in json.dumps(view)
    assert parent.get('/api/journal/quizzes/test-one?workspace=demo&studentId=s2').status_code==403
    begun=start(pupil,quiz).json();assert begun['status']=='active'
    assert 'correctIds' not in json.dumps(begun) and 'Эталон' not in json.dumps(begun,ensure_ascii=False)
    saved=save(pupil,begun,{'choice':dict(selected=['a'],text='')}).json()
    assert attempt(parent,saved).get('answers') is None and attempt(teacher,saved).get('answers') is None
    submitted=save(pupil,saved,saved['answers'],True).json()
    assert submitted['status']=='reviewed' and not submitted['resultsVisible'] and 'marks' not in submitted
    checked=attempt(teacher,submitted);assert checked['score']==2
    assert action(teacher,quiz,'release').status_code==409
    closed=action(teacher,quiz,'close').json();assert closed['status']=='closed'
    released=action(teacher,closed,'release').json();assert released['released']
    visible=attempt(parent,submitted);assert visible['score']==2 and visible['solutions'][0]['question']['correctIds']==['a']
    assert not detail(pupil)['canStart'] and start(pupil,released).status_code==409
    assert api.read('demo')==before


def test_drafts_and_bank_edits_do_not_change_published_questions(client):
    quiz,pupil,parent,teacher,payload=ready(client,publish=False)
    assert teacher.get('/api/journal/quizzes?workspace=demo').json()['items'][0]['status']=='draft'
    assert pupil.get('/api/journal/quizzes?workspace=demo').json()['items']==[]
    assert pupil.get('/api/journal/quizzes/test-one?workspace=demo&studentId=s1').status_code==403
    payload.update(version=quiz['version'],publish=True)
    quiz=teacher.put('/api/journal/quizzes/test-one?workspace=demo',json=payload).json()
    bank=teacher.get('/api/journal/quiz-bank?workspace=demo').json()['items'][0]
    bank['question']['correctIds']=['b'];bank['question']['text']='Изменённый вопрос'
    assert teacher.put('/api/journal/quiz-bank/choice?workspace=demo',json={k:bank[k] for k in ['version','subject','tags','question']}|dict(archived=True)).status_code==200
    begun=start(pupil,quiz).json();assert begun['questions'][0]['text']=='Выберите 2 + 2'
    submitted=save(pupil,begun,{'choice':dict(selected=['a'])},True).json()
    assert attempt(teacher,submitted)['score']==2
    payload.update(version=quiz['version']);assert teacher.put('/api/journal/quizzes/test-one?workspace=demo',json=payload).status_code==409


def test_server_timer_preserves_last_saved_answers_and_survives_reload(client,monkeypatch):
    quiz,pupil,parent,teacher,_=ready(client)
    begun=start(pupil,quiz).json();deadline=begun['deadline']
    saved=save(pupil,begun,{'choice':dict(selected=['a'])}).json()
    assert attempt(pupil,saved)['deadline']==deadline
    after=NOW+timedelta(minutes=11)
    for module in [quizzes,quiz_attempts]:monkeypatch.setattr(module,'now',lambda:after)
    response=save(pupil,saved,{'choice':dict(selected=['b'])},True)
    assert response.status_code==200 and response.json()['expired']
    result=attempt(teacher,response.json());assert result['score']==2 and result['finishReason']=='timed_out' and result['submittedAt']==deadline
    assert result['answers']['choice']['selected']==['a']


def test_start_is_idempotent_one_active_attempt_and_attempt_limit(client):
    quiz,pupil,_,teacher,_=ready(client);request_id=uuid4().hex
    with ThreadPoolExecutor(2) as pool:results=list(pool.map(lambda _:start(pupil,quiz,requestId=request_id).json(),range(2)))
    assert results[0]['id']==results[1]['id']
    assert start(pupil,quiz).json()['id']==results[0]['id']
    first=save(pupil,results[0],submit=True).json()
    assert start(pupil,quiz,requestId=request_id).json()['id']==first['id']
    second=start(pupil,quiz).json();assert second['number']==2 and second['id']!=first['id']
    assert save(pupil,second,submit=True).status_code==200
    assert start(pupil,quiz).status_code==409
    assert len(detail(pupil)['attempts'])==2


def test_quiz_writes_work_on_a_database_without_the_journal_schema_marker(client):
    # A database upgraded from an earlier release has no marker until a journal read or the startup migration writes it.
    quiz,pupil,_,teacher,_=ready(client)
    with api.connect() as db:db.execute("DELETE FROM journal_projection_versions WHERE name='journal_schema'")
    current=detail(pupil)
    for _ in range(3):
        attempt_=start(pupil,current);assert attempt_.status_code==200,attempt_.text
        assert save(pupil,attempt_.json()).status_code==200
    with api.connect() as db:db.execute("DELETE FROM journal_projection_versions WHERE name='journal_schema'")
    question(teacher,'second')
    with api.connect(read_only=True) as db:
        assert db.execute("SELECT 1 FROM journal_projection_versions WHERE name='journal_schema'").fetchone()


def test_autograding_types_manual_marks_and_hidden_results(client):
    record,pupil,parent,teacher=setup(client)
    base=dict(options=[],correctIds=[])
    questions=[question(teacher,'multi',kind='multiple',correctIds=['a','b']),
               question(teacher,'short',kind='short',accepted=['Hello world'],**base),
               question(teacher,'number',kind='number',number=3.14,tolerance=.01,**base),
               question(teacher,'essay',kind='essay',**base)]
    quiz=teacher.put('/api/journal/quizzes/test-one?workspace=demo',json=body(record,questions)).json()
    begun=start(pupil,quiz).json()
    answers={'multi':dict(selected=['b','a']), 'short':dict(text=' ＨＥＬＬＯ   world '),'number':dict(text='3,15'),'essay':dict(text='Обоснование ответа')}
    submitted=save(pupil,begun,answers,True).json();assert submitted['status']=='submitted'
    assert 'marks' not in attempt(parent,submitted)
    url=f'/api/journal/quizzes/test-one/attempts/{begun["id"]}/review?workspace=demo'
    assert teacher.post(url,json=dict(version=submitted['version'],marks={})).status_code==422
    marked=teacher.post(url,json=dict(version=submitted['version'],marks={'essay':dict(score=1,comment='Не хватает примера')},comment='Прочитайте разбор'))
    assert marked.status_code==200,marked.text
    assert marked.json()['score']==7 and marked.json()['status']=='reviewed'
    refused=teacher.post(url,json=dict(version=marked.json()['version'],marks={'multi':dict(score=0)}))
    assert refused.status_code==422 and 'объяснение' in refused.json()['detail']
    # The automatic score needs no comment and keeps the mark automatic; a comment makes it a teacher's mark.
    same=teacher.post(url,json=dict(version=marked.json()['version'],marks={'multi':dict(score=2)}))
    assert same.status_code==200,same.text
    assert same.json()['score']==7 and same.json()['marks']['multi']['automatic'] is True
    commented=teacher.post(url,json=dict(version=same.json()['version'],marks={'multi':dict(score=2,comment='Проверено вручную')}))
    assert commented.status_code==200 and commented.json()['marks']['multi']==dict(score=2,comment='Проверено вручную',automatic=False)
    restored=teacher.post(url,json=dict(version=commented.json()['version'],marks={'multi':dict(score=2)}))
    assert restored.status_code==200 and restored.json()['marks']['multi']['automatic'] is True and restored.json()['marks']['multi']['comment']==''
    bare=teacher.post(url,json=dict(version=restored.json()['version'],marks={'essay':dict(score=1)}));assert bare.status_code==200
    teacher.post(url,json=dict(version=bare.json()['version'],marks={'essay':dict(score=1,comment='Не хватает примера')})).raise_for_status()
    closed=action(teacher,quiz,'close').json();assert action(teacher,closed,'release').status_code==200
    result=attempt(parent,submitted);assert result['score']==7 and result['marks']['essay']['comment']=='Не хватает примера'


def test_variant_assignment_and_answer_validation(client):
    record,pupil,_,teacher=setup(client);one=question(teacher);two=question(teacher,'other',text='Другой вариант')
    variants=[dict(id='v1',name='Первый',questions=[dict(id=one['id'],version=1)]),dict(id='v2',name='Второй',questions=[dict(id=two['id'],version=1)])]
    payload=body(record,[one],definition=dict(variants=variants),variantsByStudent={'s1':'v2','s2':'v1'})
    r=teacher.put('/api/journal/quizzes/test-one?workspace=demo',json=payload);assert r.status_code==200,r.text
    begun=start(pupil,r.json()).json();assert begun['variant']['id']=='v2' and begun['questions'][0]['id']=='other'
    for answers in [{'choice':dict(selected=['a'])},{'other':dict(selected=['a','b'])},{'other':dict(selected=['missing'])},{'other':dict(selected=['a','a'])}]:
        assert save(pupil,begun,answers).status_code==422
    assert attempt(pupil,begun)['version']==1


def test_current_teacher_membership_and_family_permissions(client):
    quiz,pupil,parent,teacher,_=ready(client);begun=start(pupil,quiz).json()
    assert start(parent,quiz).status_code==403
    assert save(parent,begun,submit=True).status_code==403
    unrelated=signed('unrelated');assert unrelated.get('/api/journal/quizzes/test-one?workspace=demo').status_code==403
    state=api.read('demo');state.datedLessons=[DatedLesson(id='replacement',name='Замена',kind='change',placementId='eng',sourceDate=DAY,onDate=DAY,teacherIds=['t1'])];api.save('demo',state)
    assert unrelated.get('/api/journal/quizzes/test-one?workspace=demo').status_code==403
    assert teacher.get('/api/journal/quizzes/test-one?workspace=demo').json()['canManage']
    state=api.read('demo');group=next(g for g in state.groups if g.id==quiz['document']['lesson']['groupId']);group.kind='elective';group.studentIds=['s2'];api.save('demo',state)
    assert not detail(pupil)['canStart'] and save(pupil,begun,submit=True).status_code==403
    assert pupil.get('/api/journal/quizzes?workspace=demo').json()['items']==[]
    assert pupil.get('/api/journal/quizzes?workspace=demo&history=true').json()['items'][0]['readOnly']


def test_cancelled_lesson_does_not_freeze_quiz(client):
    quiz,pupil,parent,teacher,_=ready(client)
    state=api.read('demo');state.datedLessons=[DatedLesson(id='cancel',name='Отмена',kind='cancel',placementId='eng',sourceDate=DAY,onDate=DAY)];api.save('demo',state)
    current=teacher.get('/api/journal/quizzes/test-one?workspace=demo').json();assert current['canManage']
    seen=detail(pupil);assert seen['canStart']
    catalog=lambda reader:[i['id'] for i in reader.get('/api/journal/quizzes?workspace=demo').json()['items']]
    assert catalog(pupil)==['test-one'] and catalog(parent)==['test-one'] and catalog(teacher)==['test-one']
    begun=start(pupil,current);assert begun.status_code==200,begun.text
    assert save(pupil,begun.json(),submit=True).status_code==200
    assert [s['enrolled'] for s in current['students']]==[True,True]
    closed=action(teacher,current,'close');assert closed.status_code==200,closed.text
    assert action(client,closed.json(),'withdraw').status_code==200


def test_admin_manages_quiz_when_the_lesson_placement_is_gone(client):
    quiz,pupil,parent,teacher,_=ready(client)
    state=api.read('demo');state.placements=[p for p in state.placements if p.id!='eng'];api.save('demo',state)
    admin=client.get('/api/journal/quizzes/test-one?workspace=demo').json();assert admin['canManage']
    assert teacher.get('/api/journal/quizzes/test-one?workspace=demo').json()['canManage']
    assert action(client,admin,'close').status_code==200


def test_substitute_does_not_take_the_quiz_and_course_teacher_keeps_it(client):
    quiz,pupil,parent,teacher,_=ready(client);unrelated=signed('unrelated')
    state=api.read('demo');state.datedLessons=[DatedLesson(id='replacement',name='Замена',kind='change',placementId='eng',sourceDate=DAY,onDate=DAY,teacherIds=['t1'])];api.save('demo',state)
    assert unrelated.get('/api/journal/quizzes/test-one?workspace=demo').status_code==403
    assert unrelated.get('/api/journal/quizzes?workspace=demo&history=true').json()['items']==[]
    assert action(unrelated,quiz,'close').status_code==403
    assert teacher.get('/api/journal/quizzes/test-one?workspace=demo').json()['canManage']
    assert [i['id'] for i in teacher.get('/api/journal/quizzes?workspace=demo').json()['items']]==['test-one']
    sent=save(pupil,start(pupil,quiz).json(),submit=True).json()
    assert attempt(teacher,sent)['status']=='reviewed'
    notices=lambda reader:[n for n in reader.get('/api/notifications/inbox').json()['items'] if n['action']=='quiz.submitted']
    assert len(notices(teacher))==1 and notices(unrelated)==[]


def test_course_teacher_and_owner_manage_after_reassignment(client):
    quiz,pupil,parent,teacher,_=ready(client);unrelated=signed('unrelated')
    catalog=lambda reader:[i['id'] for i in reader.get('/api/journal/quizzes?workspace=demo').json()['items']]
    assert unrelated.get('/api/journal/quizzes/test-one?workspace=demo').status_code==403 and catalog(unrelated)==[]
    state=api.read('demo');w=next(w for w in state.workloads if w.id=='w0');w.additionalTeacherIds=['t1'];api.save('demo',state)
    assert unrelated.get('/api/journal/quizzes/test-one?workspace=demo').json()['canManage']
    assert teacher.get('/api/journal/quizzes/test-one?workspace=demo').json()['canManage']
    # Not the owner: only the group + subject pair of the workload lists the quiz for the second course teacher.
    assert catalog(unrelated)==['test-one'] and catalog(teacher)==['test-one']
    closed=action(unrelated,quiz,'close');assert closed.status_code==200,closed.text
    state=api.read('demo');w=next(w for w in state.workloads if w.id=='w0');w.teacherId='t1';w.additionalTeacherIds=[];api.save('demo',state)
    assert teacher.get('/api/journal/quizzes/test-one?workspace=demo').json()['canManage']
    assert unrelated.get('/api/journal/quizzes/test-one?workspace=demo').json()['canManage']
    # The owner left the workload but still has the quiz in the current list; the new course teacher has it too.
    assert catalog(teacher)==['test-one'] and catalog(unrelated)==['test-one']
    create(client,'teacher',login='third',grants=[dict(workspace='demo',view='teacher',entityIds=['t3'])])
    third=signed('third');assert third.get('/api/journal/quizzes/test-one?workspace=demo').status_code==403 and catalog(third)==[]
    assert action(third,closed.json(),'withdraw').status_code==403


def test_snapshot_teacher_reads_but_does_not_manage(client):
    record,pupil,parent,teacher=setup(client);q=question(client)
    r=client.put('/api/journal/quizzes/admin-one?workspace=demo',json=body(record,[q]));assert r.status_code==200,r.text
    assert teacher.get('/api/journal/quizzes/admin-one?workspace=demo').json()['canManage']
    state=api.read('demo');w=next(w for w in state.workloads if w.id=='w0');w.teacherId='t1';api.save('demo',state)
    seen=teacher.get('/api/journal/quizzes/admin-one?workspace=demo');assert seen.status_code==200,seen.text
    assert seen.json()['canManage'] is False
    assert action(teacher,seen.json(),'close').status_code==403
    assert teacher.get('/api/journal/quizzes?workspace=demo').json()['items']==[]
    item=teacher.get('/api/journal/quizzes?workspace=demo&history=true').json()['items'][0]
    assert item['id']=='admin-one' and item['readOnly'] is True
    assert signed('unrelated').get('/api/journal/quizzes/admin-one?workspace=demo').json()['canManage']


def test_teacher_catalog_is_filtered_in_sql(client):
    quiz,pupil,parent,teacher,_=ready(client)
    with api.connect() as db:
        row=db.execute("SELECT summary FROM quizzes WHERE id='test-one'").fetchone()
        summary=json.loads(row[0]);summary['lesson'].update(groupId='g-d',subjectId='sub3',teacherIds=['t3'])
        for i in range(120):
            db.execute('INSERT INTO quizzes VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',('demo',f'other-{i:03}',1,'someone','2026-01-01T00:00:00+00:00','published','2000-01-01T00:00:00+00:00',f'k{i}','{}',None,0,json.dumps(summary)))
    for path in ['/api/journal/quizzes?workspace=demo&limit=50','/api/journal/quizzes?workspace=demo&limit=50&history=true']:
        assert [i['id'] for i in teacher.get(path).json()['items']]==['test-one']
    first=client.get('/api/journal/quizzes?workspace=demo&limit=50&history=true').json()['items']
    assert len(first)==50 and all(i['id'].startswith('other-') for i in first)


def test_save_conflict_and_close_do_not_overwrite_answers(client):
    quiz,pupil,_,teacher,_=ready(client);begun=start(pupil,quiz).json()
    with ThreadPoolExecutor(2) as pool:responses=list(pool.map(lambda option:save(pupil,begun,{'choice':dict(selected=[option])}),['a','b']))
    assert sorted(r.status_code for r in responses)==[200,409]
    saved=next(r.json() for r in responses if r.status_code==200)
    assert action(teacher,quiz,'close').status_code==200
    assert save(pupil,saved,{'choice':dict(selected=['b'])},True).status_code==409
    actual=attempt(teacher,saved);assert actual['answers']==saved['answers'] and actual['finishReason']=='closed'


def test_copy_reuses_authorized_snapshot_when_bank_version_is_archived(client):
    quiz,pupil,_,teacher,payload=ready(client)
    bank=teacher.get('/api/journal/quiz-bank?workspace=demo').json()['items'][0]
    assert teacher.put('/api/journal/quiz-bank/choice?workspace=demo',json={k:bank[k] for k in ['version','subject','tags','question']}|dict(archived=True)).status_code==200
    copy=teacher.put('/api/journal/quizzes/test-copy?workspace=demo',json=payload|dict(copyFrom=quiz['id']))
    assert copy.status_code==200,copy.text
    begun=start(pupil,copy.json()).json();assert begun['questions'][0]['text']==bank['question']['text']
    unrelated=signed('unrelated')
    assert unrelated.get('/api/journal/quiz-bank?workspace=demo').json()['items']==[]
    assert unrelated.put('/api/journal/quiz-bank/choice?workspace=demo',json={k:bank[k] for k in ['version','subject','tags','question']}).status_code==403


def test_grade_publication_preserves_journal_draft_and_correction_history(client):
    from test_journal import entry,write
    quiz,pupil,parent,teacher,_=ready(client);begun=start(pupil,quiz).json()
    sent=save(pupil,begun,{'choice':dict(selected=['a'])},True).json()
    endpoint=f'/api/journal/quizzes/test-one/attempts/{sent["id"]}'
    assert teacher.get(endpoint+'/grading?workspace=demo').status_code==409
    closed=action(teacher,quiz,'close').json();released=action(teacher,closed,'release').json()
    draft=entry(teacher,quiz['lessonKey']);draft['document']['content']['topic']='Личная тема черновика'
    assert write(teacher,draft).status_code==200
    options=teacher.get(endpoint+'/grading?workspace=demo').json();assert options['allowed']
    grading=dict(journalVersion=options['journalVersion'],revision=options['revision'],assessmentId='work',score=9,replaceExisting=True)
    marked=teacher.post(endpoint+'/grade?workspace=demo',json=dict(version=sent['version'],grading=grading))
    assert marked.status_code==200,marked.text
    assert marked.json()['grading']['sourceScore']==2 and not marked.json()['grading']['stale']
    actual=entry(teacher,quiz['lessonKey']);assert actual['document']['content']['topic']=='Личная тема черновика'
    assert actual['version']>actual['publishedVersion']
    correction=dict(version=marked.json()['version'],marks={'choice':dict(score=1,comment='Частичный балл после разбора')},reason='Уточнение критериев')
    assert teacher.post(endpoint+'/review?workspace=demo',json=correction|dict(reason='')).status_code==422
    changed=teacher.post(endpoint+'/review?workspace=demo',json=correction);assert changed.status_code==200,changed.text
    result=attempt(parent,sent);assert result['score']==1 and result['grading']['stale']
    history=parent.get(endpoint+'/history?workspace=demo').json()['items']
    assert any(h.get('attempt',{}).get('score')==2 for h in history)
    assert any(h.get('attempt',{}).get('score')==1 for h in history)


def test_quiz_transfer_uses_quiz_category(client):
    from test_journal import entry
    quiz,pupil,parent,teacher,payload=ready(client,definition=dict(journalCategory='soch'))
    assert quiz['document']['definition']['journalCategory']=='soch'
    # A category the school does not have is refused when the quiz is saved.
    bad=body(entry(teacher,quiz['lessonKey']),[question(teacher,'second')],definition=dict(journalCategory='nope'))
    refused=teacher.put('/api/journal/quizzes/test-two?workspace=demo',json=bad);assert refused.status_code==422 and 'категорию журнала' in refused.text
    begun=start(pupil,quiz).json();sent=save(pupil,begun,{'choice':dict(selected=['a'])},True).json()
    closed=action(teacher,quiz,'close').json();action(teacher,closed,'release')
    endpoint=f'/api/journal/quizzes/test-one/attempts/{sent["id"]}';options=teacher.get(endpoint+'/grading?workspace=demo').json()
    assert options['defaultCategory']=='soch' and [c['id'] for c in options['categories']]==['fo','sor','soch']
    work=dict(id='quiz',name='Тест',kind='points',maxScore=10,weight=1)
    grading=dict(journalVersion=options['journalVersion'],revision=options['revision'],assessmentId='quiz',newAssessment=work,score=7,replaceExisting=False)
    marked=teacher.post(endpoint+'/grade?workspace=demo',json=dict(version=sent['version'],grading=grading));assert marked.status_code==200,marked.text
    assert {a['id']:a['category'] for a in entry(teacher,quiz['lessonKey'])['document']['content']['assessments']}=={'work':'fo','quiz':'soch'}


def test_quiz_category_falls_back_to_school_default(client):
    quiz,pupil,parent,teacher,_=ready(client,definition=dict(journalCategory='sor'))
    cur=client.get('/api/journal/categories?workspace=demo').json()
    off=[dict(c,active=False) if c['id']=='sor' else c for c in cur['categories']]
    switched=client.put('/api/journal/categories?workspace=demo',json=dict(version=cur['version'],reason='Отключить СОР',categories=off,defaultCategory=cur['defaultCategory'],policy=cur['policy'],rules=[]))
    assert switched.status_code==200,switched.text
    begun=start(pupil,quiz).json();sent=save(pupil,begun,submit=True).json()
    closed=action(teacher,quiz,'close').json();action(teacher,closed,'release')
    endpoint=f'/api/journal/quizzes/test-one/attempts/{sent["id"]}';options=teacher.get(endpoint+'/grading?workspace=demo').json()
    assert options['defaultCategory']=='fo'
    work=dict(id='quiz',name='Тест',kind='points',maxScore=10,weight=1)
    grading=dict(journalVersion=options['journalVersion'],revision=options['revision'],assessmentId='quiz',newAssessment=work,score=5,replaceExisting=False)
    assert teacher.post(endpoint+'/grade?workspace=demo',json=dict(version=sent['version'],grading=grading)).status_code==200
    from test_journal import entry
    assert entry(teacher,quiz['lessonKey'])['document']['content']['assessments'][-1]['category']=='fo'


def test_grade_failure_rolls_back_journal_result_and_notifications(client,monkeypatch):
    from fastapi import HTTPException
    from test_journal import entry
    quiz,pupil,parent,teacher,_=ready(client);begun=start(pupil,quiz).json();sent=save(pupil,begun,submit=True).json()
    closed=action(teacher,quiz,'close').json();action(teacher,closed,'release')
    endpoint=f'/api/journal/quizzes/test-one/attempts/{sent["id"]}';options=teacher.get(endpoint+'/grading?workspace=demo').json()
    before=entry(teacher,quiz['lessonKey']);inbox=parent.get('/api/notifications/inbox').json()
    def fail(*args):raise HTTPException(409,'Synthetic failure')
    monkeypatch.setattr(api.quizzes,'persist_attempt',fail)
    r=teacher.post(endpoint+'/grade?workspace=demo',json=dict(version=sent['version'],grading=dict(journalVersion=options['journalVersion'],revision=options['revision'],assessmentId='work',score=0,replaceExisting=True)))
    assert r.status_code==409
    assert entry(teacher,quiz['lessonKey'])==before and parent.get('/api/notifications/inbox').json()==inbox
    assert attempt(parent,sent).get('grading') is None


def test_family_sees_submitted_answers_but_no_keys_scores_or_drafts_before_release(client):
    quiz,pupil,parent,teacher,_=ready(client);begun=start(pupil,quiz).json()
    sent=save(pupil,begun,{'choice':dict(selected=['a'])},True).json()
    endpoint=f'/api/journal/quizzes/test-one/attempts/{sent["id"]}'
    for reader in [pupil,parent]:
        view=attempt(reader,sent);assert view['answers']['choice']['selected']==['a']
        for result in [view,reader.get(endpoint+'/history?workspace=demo').json(),detail(reader),reader.get('/api/journal/quizzes?workspace=demo').json()]:
            encoded=json.dumps(result,ensure_ascii=False)
            assert all(key not in encoded for key in ['correctIds','explanation','marks','Эталон','"score"'])
        overview=detail(reader)['attempts'][0];assert 'questions' not in overview and 'answers' not in overview
    assert signed('other-pupil').get(endpoint+'?workspace=demo').status_code==403
    assert pupil.get(endpoint+'?workspace=school').status_code==403


def test_school_timezone_opening_and_global_close_override_timer(client,monkeypatch):
    quiz,pupil,_,teacher,_=ready(client,definition=dict(opensAt='2026-08-17T14:01',closesAt='2026-08-17T14:03'))
    assert quiz['opensAt'].startswith('2026-08-17T09:01')
    assert start(pupil,quiz).status_code==409
    for module in [quizzes,quiz_attempts]:monkeypatch.setattr(module,'now',lambda:NOW+timedelta(minutes=2))
    begun=start(pupil,quiz).json();assert datetime.fromisoformat(begun['deadline'])==NOW+timedelta(minutes=3)
    for module in [quizzes,quiz_attempts]:monkeypatch.setattr(module,'now',lambda:NOW+timedelta(minutes=3))
    sent=save(pupil,begun,{'choice':dict(selected=['a'])},True).json()
    assert sent['expired'] and sent['answers']=={} and attempt(teacher,sent)['score']==0


@pytest.mark.parametrize('change',['account','teacher','roster','quiz'])
@pytest.mark.parametrize('operation',['answer','review'])
def test_rights_and_version_changes_between_context_and_write(client,monkeypatch,change,operation):
    quiz,pupil,parent,teacher,_=ready(client);begun=start(pupil,quiz).json()
    work=begun if operation=='answer' else save(pupil,begun,submit=True).json()
    with api.connect() as db:before=db.execute('SELECT version,document FROM quiz_attempts WHERE id=?',(work['id'],)).fetchone()
    original=api.quizzes.context
    def changed(*args,**kwargs):
        ctx=original(*args,**kwargs)
        if change=='account':
            with api.connect() as db:db.execute('UPDATE accounts SET version=version+1 WHERE id=?',(ctx['user']['id'],))
        elif change=='quiz':
            with api.connect() as db:db.execute('UPDATE quizzes SET version=version+1 WHERE id=?',(quiz['id'],))
        else:
            state=api.read('demo')
            if change=='teacher':state.datedLessons=[DatedLesson(id='swap',name='Замена',kind='change',placementId='eng',sourceDate=DAY,onDate=DAY,teacherIds=['t1'])]
            else:
                group=next(g for g in state.groups if g.id==quiz['document']['lesson']['groupId']);group.kind='elective';group.studentIds=['s2']
            api.save('demo',state)
        return ctx
    monkeypatch.setattr(api.quizzes,'context',changed)
    result=save(pupil,work,{'choice':dict(selected=['a'])}) if operation=='answer' else teacher.post(f'/api/journal/quizzes/test-one/attempts/{work["id"]}/review?workspace=demo',json=dict(version=work['version'],marks={'choice':dict(score=1,comment='Проверка')}))
    assert result.status_code==409,result.text
    with api.connect() as db:assert db.execute('SELECT version,document FROM quiz_attempts WHERE id=?',(work['id'],)).fetchone()==before


@pytest.mark.parametrize('change',['closure','account','school','journal'])
def test_journal_grade_rechecks_changes_after_preparation(client,monkeypatch,change):
    from backend.homework_grading import HomeworkGrading
    from test_journal_periods import close,period
    from test_journal import entry
    quiz,pupil,_,teacher,_=ready(client);sent=save(pupil,start(pupil,quiz).json(),submit=True).json()
    action(teacher,action(teacher,quiz,'close').json(),'release')
    endpoint=f'/api/journal/quizzes/test-one/attempts/{sent["id"]}';o=teacher.get(endpoint+'/grading?workspace=demo').json()
    original=HomeworkGrading.prepare
    def changed(self,ctx,**kwargs):
        result=original(self,ctx,**kwargs)
        if change=='closure':close(client,period(client))
        elif change=='school':api.save('demo',api.read('demo'))
        elif change=='journal':
            with api.connect() as db:db.execute("UPDATE journal_entries SET version=version+1 WHERE workspace='demo'")
        else:
            with api.connect() as db:db.execute("UPDATE accounts SET version=version+1 WHERE login='teacher'")
        return result
    monkeypatch.setattr(HomeworkGrading,'prepare',changed)
    r=teacher.post(endpoint+'/grade?workspace=demo',json=dict(version=sent['version'],grading=dict(journalVersion=o['journalVersion'],revision=o['revision'],assessmentId='work',score=9,replaceExisting=True)))
    assert r.status_code==409,r.text
    assert entry(client,quiz['lessonKey'])['document']['content']['records'][0]['grades'][0]['score']==8
    assert attempt(pupil,sent).get('grading') is None


def test_catalog_pagination_rights_generation_and_compact_payloads(client):
    quiz,pupil,parent,teacher,payload=ready(client)
    for index in range(3):
        r=teacher.put(f'/api/journal/quizzes/copy-{index}?workspace=demo',json=payload);assert r.status_code==200,r.text
    listing=pupil.get('/api/journal/quizzes?workspace=demo&limit=1').json();seen=[listing['items'][0]['id']];cursor=listing['nextCursor']
    assert 's2' not in json.dumps(listing) and len(json.dumps(listing))<1500
    assert parent.get('/api/journal/quizzes',params=dict(workspace='demo',cursor=cursor)).status_code==409
    while listing['nextCursor']:
        listing=pupil.get('/api/journal/quizzes',params=dict(workspace='demo',limit=1,cursor=listing['nextCursor'])).json();seen.extend(item['id'] for item in listing['items'])
    assert len(seen)==len(set(seen))==4
    action(teacher,quiz,'withdraw')
    assert pupil.get('/api/journal/quizzes',params=dict(workspace='demo',cursor=cursor)).status_code==409
    assert len(pupil.get('/api/journal/quizzes?workspace=demo').json()['items'])==3
    assert len(pupil.get('/api/journal/quizzes?workspace=demo&history=true').json()['items'])==4
    assert teacher.get('/api/journal/quizzes?workspace=demo&search=несуществующее').json()['items']==[]


def test_numeric_autograding_rejects_extreme_exponents_and_question_validation():
    from backend.quiz_models import Question,Definition,auto_mark
    from pydantic import ValidationError
    q=Question(kind='number',text='Число',number=4).model_dump()
    for text in ['1e999999999','NaN','-Infinity','не число','']:
        assert auto_mark(q,dict(text=text))['score']==0
    with pytest.raises(ValidationError):Question(kind='short',text='Текст',accepted=['   '])
    with pytest.raises(ValidationError):Definition(title='Тест',variants=[dict(id='v',name='v',questions=[dict(id='q',version=1)])],opensAt='0001-01-01T00:00:00+14:00',closesAt='0001-01-02T00:00:00Z')


def test_quiz_backup_preserves_bank_publication_attempt_and_history(client,tmp_path):
    from contextlib import closing
    import sqlite3
    from tools.backup_school import create_backup
    quiz,pupil,_,teacher,_=ready(client);sent=save(pupil,start(pupil,quiz).json(),{'choice':dict(selected=['a'])},True).json()
    action(teacher,action(teacher,quiz,'close').json(),'release')
    target=tmp_path/'quiz-snapshot';manifest=create_backup(api.DB,target)
    with api.connect() as source,closing(sqlite3.connect(target/manifest['database']['path'])) as restored:
        for table in ['quiz_questions','quizzes','quiz_members','quiz_attempts','quiz_history','quiz_catalog_versions']:
            assert restored.execute(f'SELECT * FROM {table} ORDER BY rowid').fetchall()==source.execute(f'SELECT * FROM {table} ORDER BY rowid').fetchall()
        assert restored.execute('PRAGMA integrity_check').fetchone()==('ok',)
