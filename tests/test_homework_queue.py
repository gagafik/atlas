from datetime import datetime, timezone

from backend import api, homework_queue
from backend.models import DatedLesson
from test_access import client, create, signed
from test_homework import setup, get, answer, review
from test_journal import DAY, lesson, entry, graded, write


def queue(client, **params):
    return client.get('/api/journal/homework-queue',params=dict(workspace='demo',**params))


def items(client, **params):
    response=queue(client,**params)
    assert response.status_code==200,response.text
    return response.json()['items']


def publish(client, day, due=None):
    key=lesson(client,day=day)['lessonKey']
    record=graded(entry(client,key,day=day))
    record['document']['content']['dueDate']=due
    response=write(client,record,publish=True)
    assert response.status_code==200,response.text
    return response.json()


def test_queue_scope_statuses_and_private_drafts(client):
    record,pupil,parent,teacher=setup(client);key=record['lessonKey']
    before=api.read('demo')
    assert len(items(pupil))==1 and items(parent)[0]['studentId']=='s1'
    assert items(teacher)==[] and items(signed('unrelated'),filter='all')==[]
    assert len(items(teacher,filter='all'))==len(record['document']['lesson']['students'])
    assert pupil.get('/api/journal/homework-queue?workspace=school').status_code==403
    create(client,'editor');assert queue(signed('editor')).status_code==403
    assert answer(pupil,key,get(pupil,key),'draft','PRIVATE ANSWER').status_code==200
    for reader in [pupil,parent,teacher]:
        raw=queue(reader,filter='all').text
        assert 'PRIVATE ANSWER' not in raw and 'draft' not in raw and 'grades' not in raw
    sent=answer(pupil,key,get(pupil,key)).json()
    assert items(pupil)==[] and len(items(teacher))==1 and items(teacher)[0]['canReview']
    assert not items(parent,filter='submitted')[0]['canSubmit']
    assert review(teacher,key,sent,'returned','Исправьте').status_code==200
    assert items(pupil)[0]['status']=='returned' and items(pupil)[0]['canSubmit']
    sent=answer(pupil,key,get(pupil,key)).json()
    assert review(teacher,key,sent).status_code==200
    assert items(teacher)==[] and items(pupil,filter='accepted')[0]['status']=='accepted'
    assert api.read('demo')==before


def test_changed_conditions_reopen_accepted_answer_and_publication_withdrawal(client):
    record,pupil,parent,teacher=setup(client);key=record['lessonKey']
    sent=answer(pupil,key,get(pupil,key)).json();assert review(teacher,key,sent).status_code==200
    draft=entry(client,key);draft['document']['content']['homework']='Новые условия'
    assert write(client,draft).status_code==200
    assert items(parent,filter='accepted')[0]['preview']!='Новые условия'
    current=write(client,entry(client,key),publish=True).json()
    row=items(pupil)[0];assert row['assignmentChanged'] and row['canSubmit']
    assert items(parent,filter='accepted')==[] and items(teacher)==[]
    assert client.post('/api/journal/withdraw?workspace=demo',json=dict(lessonKey=key,version=current['version'])).status_code==200
    for reader in [pupil,parent,teacher]:assert items(reader,filter='all',history=True)==[]


def test_replacement_and_cancelled_history_use_current_rights(client):
    record,pupil,parent,teacher=setup(client);key=record['lessonKey']
    assert answer(pupil,key,get(pupil,key)).status_code==200
    state=api.read('demo');state.datedLessons=[DatedLesson(id='replacement',name='Замена',kind='change',placementId='eng',sourceDate=DAY,onDate=DAY,teacherIds=['t1'])]
    api.save('demo',state);replacement=signed('unrelated')
    assert items(teacher)==[]
    old=items(teacher,history=True)[0];assert old['readOnly'] and not old['canReview']
    assert items(replacement)[0]['canReview']
    state=api.read('demo');state.datedLessons=[DatedLesson(id='cancel',name='Отмена',kind='cancel',placementId='eng',sourceDate=DAY,onDate=DAY)]
    api.save('demo',state)
    assert items(parent,filter='all')==[]
    old=items(parent,filter='all',history=True)[0];assert old['readOnly'] and not old['canSubmit']
    assert items(teacher)==[] and not items(teacher,history=True)[0]['canReview']


def test_removed_pupil_retains_only_historical_access(client):
    record,pupil,parent,teacher=setup(client);key=record['lessonKey']
    state=api.read('demo');group=next(g for g in state.groups if g.id==record['document']['lesson']['groupId'])
    group.kind='elective';group.studentIds=['s2'];api.save('demo',state)
    assert items(pupil)==[] and items(parent,filter='all')==[]
    assert items(pupil,history=True)[0]['readOnly']
    assert not items(pupil,history=True)[0]['canSubmit']


def test_cursor_order_filter_binding_and_private_draft_stability(client):
    record,pupil,parent,teacher=setup(client);key=record['lessonKey']
    publish(client,'2026-08-24','2026-08-19');publish(client,'2026-08-31')
    first=queue(pupil,limit=1).json();assert first['nextCursor']
    assert answer(pupil,key,get(pupil,key),'draft','secret').status_code==200
    second=queue(pupil,limit=1,cursor=first['nextCursor']).json()
    third=queue(pupil,limit=1,cursor=second['nextCursor']).json()
    keys=[page['items'][0]['lessonKey'] for page in [first,second,third]]
    assert len(set(keys))==3 and third['nextCursor'] is None
    assert third['items'][0]['dueDate'] is None and third['items'][0]['lessonDate']=='2026-08-31'
    assert queue(pupil,limit=1,cursor=first['nextCursor'],filter='all').status_code==409
    assert answer(pupil,key,get(pupil,key)).status_code==200
    assert queue(pupil,cursor=first['nextCursor']).status_code==409


def test_school_timezone_deadline_and_due_date_search(client,monkeypatch):
    record,pupil,parent,teacher=setup(client);key=record['lessonKey']
    record=entry(client,key);record['document']['content']['homeworkLatePolicy']='block'
    assert write(client,record,publish=True).status_code==200
    monkeypatch.setattr(homework_queue,'now',lambda:datetime(2026,8,19,18,59,tzinfo=timezone.utc))
    assert items(parent,filter='overdue')==[] and items(pupil)[0]['canSubmit']
    monkeypatch.setattr(homework_queue,'now',lambda:datetime(2026,8,19,19,0,tzinfo=timezone.utc))
    row=items(pupil,filter='overdue')[0];assert row['deadlineClosed'] and not row['canSubmit']
    assert items(parent,start='2026-08-19',end='2026-08-19',search='АНГЛИЙСКИЙ')[0]['lessonKey']==key
    assert items(parent,start=DAY,end=DAY)==[]
    assert items(parent,search='нет такого предмета')==[]
    assert items(parent,search='%')==[]


def test_projection_backfill_matches_live_rows_and_does_not_touch_source_documents(client):
    record,pupil,parent,teacher=setup(client);key=record['lessonKey']
    answer(pupil,key,get(pupil,key))
    expected=items(teacher)
    with api.connect() as db:
        original=db.execute('SELECT * FROM journal_entries').fetchall(),db.execute('SELECT * FROM homework_submissions').fetchall()
        db.execute("DELETE FROM journal_projection_versions WHERE name='homework_queue'")
        db.execute('DELETE FROM homework_queue_tasks');db.execute('DELETE FROM homework_queue_answers')
    assert items(teacher)==expected
    with api.connect() as db:
        assert (db.execute('SELECT * FROM journal_entries').fetchall(),db.execute('SELECT * FROM homework_submissions').fetchall())==original


def test_queue_rechecks_account_and_workspace_races(client,monkeypatch):
    _,pupil,_,_=setup(client)
    original=api.homework.snapshots.get
    def revoke(workspace):
        result=original(workspace)
        with api.connect() as db:db.execute("UPDATE accounts SET version=version+1 WHERE login='student'")
        return result
    monkeypatch.setattr(api.homework.snapshots,'get',revoke)
    assert queue(pupil).status_code==409
    monkeypatch.setattr(api.homework.snapshots,'get',original);pupil=signed('student')
    def revise(workspace):
        result=original(workspace);api.save(workspace,api.read(workspace));return result
    monkeypatch.setattr(api.homework.snapshots,'get',revise)
    assert queue(pupil).status_code==409


def test_candidate_scan_is_bounded_and_does_not_materialize_answers(client,monkeypatch):
    record,pupil,parent,teacher=setup(client)
    # Force tiny candidate batches to exercise continuation past read-only rows.
    publish(client,'2026-08-24','2026-08-25')
    state=api.read('demo');state.datedLessons=[DatedLesson(id='cancel',name='Отмена',kind='cancel',placementId='eng',sourceDate=DAY,onDate=DAY)]
    api.save('demo',state);monkeypatch.setattr(homework_queue,'SCAN',1)
    def unexpected(*args,**kwargs):raise AssertionError('Queue must not read private answer documents')
    monkeypatch.setattr(api.homework,'load',unexpected)
    first=queue(pupil).json();assert first['items']==[] and first['nextCursor']
    second=queue(pupil,cursor=first['nextCursor']).json()
    assert len(second['items'])==1 and not second['nextCursor']


def test_queue_rejects_invalid_pagination_and_periods(client):
    _,pupil,_,_=setup(client)
    for args in [dict(cursor='!bad'),dict(limit=51),dict(limit=0),dict(filter='draft'),dict(start='2026-01-01',end='2028-01-01'),dict(start='2026-08-20',end='2026-08-19')]:
        assert queue(pupil,**args).status_code==422


def test_extra_lesson_reassignment_is_found_without_published_teacher_match(client):
    _,pupil,parent,teacher=setup(client)
    state=api.read('demo');load=next(w for w in state.workloads if w.id=='w0')
    state.datedLessons=[DatedLesson(id='extra',name='Дополнительный урок',kind='add',onDate='2026-08-18',groupId=load.groupId,subjectId=load.subjectId,teacherIds=['t2'],roomIds=['r0'],profileId='primary',startTime='09:00',endTime='09:45')]
    api.save('demo',state);record=publish(client,'2026-08-18','2026-08-20');key=record['lessonKey']
    assert answer(pupil,key,get(pupil,key)).status_code==200
    state=api.read('demo');state.datedLessons[0].teacherIds=['t1'];api.save('demo',state)
    assert items(teacher)==[]
    assert items(signed('unrelated'))[0]['lessonKey']==key


def test_parent_grants_change_and_failed_review_leave_no_false_status(client):
    record,pupil,parent,teacher=setup(client);key=record['lessonKey']
    sent=answer(pupil,key,get(pupil,key)).json()
    assert review(teacher,key,{**sent,'version':sent['version']+1}).status_code==409
    assert items(teacher)[0]['status']=='submitted'
    account=parent.get('/api/auth/status').json()['user']
    body={k:v for k,v in account.items() if k!='id'};body['grants'][0]['entityIds']=['s2']
    assert client.put('/api/access/users/'+account['id'],json=body).status_code==200
    assert queue(parent).status_code==401
    rows=items(signed('parent'),filter='all');assert rows and all(r['studentId']=='s2' for r in rows)
