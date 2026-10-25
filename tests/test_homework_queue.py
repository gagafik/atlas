from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json

import pytest
from fastapi import HTTPException
from backend import api, homework_queue
from backend.models import DatedLesson
from test_access import client, create, signed
from test_homework import setup, get, answer, review
from test_journal import DAY, lesson, entry, graded, write


# Explicit dates select range mode (ascending by deadline); without dates a pupil gets the default window.
RANGE=dict(start='2026-08-17',end='2027-06-11')


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
    from test_assignment_files import upload as upload_material, save_materials
    record,pupil,parent,teacher=setup(client);key=record['lessonKey']
    sent=answer(pupil,key,get(pupil,key)).json();assert review(teacher,key,sent).status_code==200
    draft=entry(client,key);draft['document']['content']['homework']='Новые условия'
    assert write(client,draft).status_code==200
    assert items(parent,filter='accepted')[0]['preview']!='Новые условия'
    current=write(client,entry(client,key),publish=True).json()
    # A new text is not a change of conditions: the accepted answer stays accepted.
    assert items(parent,filter='accepted')[0]['preview']=='Новые условия' and items(pupil)==[]
    material=upload_material(client,current).json()
    current=save_materials(client,current,[material['id']])
    row=items(pupil)[0];assert row['assignmentChanged'] and row['canSubmit']
    assert items(parent,filter='accepted')==[] and items(teacher)==[]
    assert client.post('/api/journal/withdraw?workspace=demo',json=dict(lessonKey=key,version=current['version'])).status_code==200
    for reader in [pupil,parent,teacher]:assert items(reader,filter='all',history=True)==[]


def test_course_teacher_keeps_queue_after_replacement_and_cancellation(client):
    record,pupil,parent,teacher=setup(client);key=record['lessonKey']
    assert answer(pupil,key,get(pupil,key)).status_code==200
    state=api.read('demo');state.datedLessons=[DatedLesson(id='replacement',name='Замена',kind='change',placementId='eng',sourceDate=DAY,onDate=DAY,teacherIds=['t1'])]
    api.save('demo',state);replacement=signed('unrelated')
    assert items(teacher)[0]['canReview']
    # A substitute reads the lesson in the history view only and never reviews.
    assert items(replacement)==[]
    old=items(replacement,history=True)[0];assert old['readOnly'] and not old['canReview']
    state=api.read('demo');state.datedLessons=[DatedLesson(id='cancel',name='Отмена',kind='cancel',placementId='eng',sourceDate=DAY,onDate=DAY)]
    api.save('demo',state)
    assert items(parent,filter='all')==[]
    old=items(parent,filter='all',history=True)[0];assert old['readOnly'] and not old['canSubmit']
    assert items(teacher)[0]['canReview']  # work that was already sent stays reviewable


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
    first=queue(pupil,limit=1,**RANGE).json();assert first['nextCursor']
    assert answer(pupil,key,get(pupil,key),'draft','secret').status_code==200
    second=queue(pupil,limit=1,cursor=first['nextCursor'],**RANGE).json()
    third=queue(pupil,limit=1,cursor=second['nextCursor'],**RANGE).json()
    keys=[page['items'][0]['lessonKey'] for page in [first,second,third]]
    assert len(set(keys))==3 and third['nextCursor'] is None
    assert third['items'][0]['dueDate'] is None and third['items'][0]['lessonDate']=='2026-08-31'
    assert queue(pupil,limit=1,cursor=first['nextCursor'],filter='all',**RANGE).status_code==409
    # The pupil's own answer changes no sort key: the cursor keeps working and the answered row drops out.
    assert answer(pupil,key,get(pupil,key)).status_code==200
    assert queue(pupil,cursor=first['nextCursor'],**RANGE).status_code==200


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
    first=queue(pupil,**RANGE).json();assert first['items']==[] and first['nextCursor']
    second=queue(pupil,cursor=first['nextCursor'],**RANGE).json()
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
    # The workload of the group and subject keeps the course teacher; the new lesson teacher is a substitute.
    assert items(teacher)[0]['lessonKey']==key and items(teacher)[0]['canReview']
    assert items(signed('unrelated'))==[]
    found=items(signed('unrelated'),history=True)[0];assert found['lessonKey']==key and found['readOnly']


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


def test_opaque_cursor_hides_rejected_candidates_and_expires(client,monkeypatch):
    record,_,_,_=setup(client)
    publish(client,'2026-08-24','2026-08-25')
    state=api.read('demo');state.datedLessons=[DatedLesson(id='replacement',name='Замена',kind='change',placementId='eng',sourceDate='2026-08-24',onDate='2026-08-24',teacherIds=['t1'])]
    api.save('demo',state);replacement=signed('unrelated');monkeypatch.setattr(homework_queue,'SCAN',1)
    # A substitute only reads: its own lesson appears in the history view.
    result=queue(replacement,filter='all',history='true').json()
    assert result['items']==[]
    cursor=result['nextCursor'];assert len(cursor)==64 and all(c in '0123456789abcdef' for c in cursor)
    assert record['lessonKey'] not in str(result) and 'Ученик' not in str(result)
    assert queue(client,filter='all',history='true',cursor=cursor).status_code==409
    first_cursor=cursor;found=[]
    for _ in range(10):
        result=queue(replacement,filter='all',history='true',cursor=cursor).json();found.extend(result['items']);cursor=result['nextCursor']
        if not cursor:break
    assert found and all(r['lessonDate']=='2026-08-24' and r['readOnly'] for r in found)
    clock=homework_queue.monotonic
    monkeypatch.setattr(homework_queue,'monotonic',lambda:clock()+901)
    assert queue(replacement,filter='all',history='true',cursor=first_cursor).status_code==409


def test_cursor_survives_unrelated_journal_save_and_own_answers(client):
    record,pupil,parent,teacher=setup(client)
    publish(client,'2026-08-24','2026-08-25');publish(client,'2026-08-31')
    first=queue(pupil,limit=1,**RANGE).json();assert first['nextCursor']
    # A draft of another lesson used to bump the global queue version and invalidate every pupil's page 2.
    other=entry(client,lesson(client,'Математика')['lessonKey'])
    assert write(client,other).status_code==200
    second=queue(pupil,limit=1,cursor=first['nextCursor'],**RANGE);assert second.status_code==200,second.text
    second=second.json();assert second['items'][0]['lessonKey']!=first['items'][0]['lessonKey']
    key=second['items'][0]['lessonKey']
    assert answer(pupil,key,get(pupil,key)).status_code==200
    third=queue(pupil,limit=1,cursor=second['nextCursor'],**RANGE);assert third.status_code==200,third.text
    third=third.json();keys=[page['items'][0]['lessonKey'] for page in [first,second,third]]
    assert len(set(keys))==3 and third['nextCursor'] is None


def test_cursor_still_invalidated_by_schedule_and_conditions(client):
    record,pupil,parent,teacher=setup(client)
    publish(client,'2026-08-24','2026-08-25')
    first=queue(pupil,limit=1,**RANGE).json();cursor=first['nextCursor'];assert cursor
    assert queue(pupil,limit=1,cursor=cursor,search='англ',**RANGE).status_code==409
    assert queue(pupil,limit=1,cursor=cursor,history='true',**RANGE).status_code==409
    assert queue(pupil,limit=1,cursor=cursor,start='2026-08-18',end='2027-06-11').status_code==409
    assert queue(parent,limit=1,cursor=cursor,**RANGE).status_code==409
    assert queue(pupil,limit=1,cursor=cursor).status_code==409
    api.save('demo',api.read('demo'))
    assert queue(pupil,limit=1,cursor=cursor,**RANGE).status_code==409


def test_cursor_store_is_thread_safe(client):
    store=api.homework.queue
    def work(worker):
        for i in range(2000):
            key=store.cursor_save([worker,i])
            try:assert store.cursor_read(key)==[worker,i]
            except HTTPException as exc:assert exc.status_code==409  # already evicted by the other workers
    with ThreadPoolExecutor(8) as pool:list(pool.map(work,range(8)))
    assert len(store.cursors)<=4096


def default_window_data(client,monkeypatch):
    record,pupil,parent,teacher=setup(client)
    publish(client,'2026-08-24','2026-08-25');publish(client,'2026-08-31');publish(client,'2026-09-14','2026-09-15')
    # 11:00 on 2026-08-26 in the school time zone.
    monkeypatch.setattr(homework_queue,'now',lambda:datetime(2026,8,26,6,0,tzinfo=timezone.utc))
    return pupil,parent,teacher


def test_default_pupil_window_is_recent_undone_then_two_weeks(client,monkeypatch):
    pupil,parent,teacher=default_window_data(client,monkeypatch)
    result=queue(pupil).json()
    assert result['window']=='upcoming' and result['filter']=='todo'
    assert [r['lessonDate'] for r in result['items']]==['2026-08-24','2026-08-17','2026-08-31']
    assert result['yearStart']=='2026-08-17' and result['yearEnd']=='2027-06-11' and result['start']=='2026-08-17' and result['end']=='2026-09-09'
    assert [r['lessonDate'] for r in items(parent)]==['2026-08-24','2026-08-17','2026-08-31']
    assert [r['dueDate'] for r in items(pupil,filter='overdue')]==['2026-08-25','2026-08-19']
    ranged=queue(pupil,**RANGE).json()
    assert ranged['window']=='range' and [r['lessonDate'] for r in ranged['items']]==['2026-08-17','2026-08-24','2026-08-31','2026-09-14']
    # One explicit date is enough to leave the default window.
    assert queue(pupil,start='2026-08-24').json()['window']=='range'
    assert queue(pupil,end='2026-08-24').json()['window']=='range'


def test_default_window_paging_crosses_phases(client,monkeypatch):
    pupil,parent,teacher=default_window_data(client,monkeypatch)
    expected=[r['lessonKey'] for r in items(pupil)]
    paged=[];cursor=None
    for _ in range(10):
        page=queue(pupil,limit=1,**(dict(cursor=cursor) if cursor else {}));assert page.status_code==200,page.text
        page=page.json();paged.extend(r['lessonKey'] for r in page['items']);cursor=page['nextCursor']
        if not cursor:break
    assert paged==expected and len(set(paged))==3
    # The cursor of the default window belongs to the school day and the window.
    cursor=queue(pupil,limit=1).json()['nextCursor']
    monkeypatch.setattr(homework_queue,'now',lambda:datetime(2026,8,27,6,0,tzinfo=timezone.utc))
    assert queue(pupil,limit=1,cursor=cursor).status_code==409


def test_default_window_shares_one_candidate_scan_budget_between_phases(client,monkeypatch):
    pupil,parent,teacher=default_window_data(client,monkeypatch)
    expected=[r['lessonKey'] for r in items(pupil)];assert len(expected)==3
    # One candidate per request in total: the next phase must not start with a fresh budget.
    monkeypatch.setattr(homework_queue,'SCAN',1)
    paged=[];sizes=[];cursor=None
    for _ in range(10):
        page=queue(pupil,limit=50,**(dict(cursor=cursor) if cursor else {}));assert page.status_code==200,page.text
        page=page.json();sizes.append(len(page['items']));paged.extend(r['lessonKey'] for r in page['items']);cursor=page['nextCursor']
        if not cursor:break
    assert sizes==[1,1,1] and paged==expected and cursor is None


def test_default_window_edges_before_and_after_the_year(client,monkeypatch):
    pupil,parent,teacher=default_window_data(client,monkeypatch)
    monkeypatch.setattr(homework_queue,'now',lambda:datetime(2026,8,1,6,0,tzinfo=timezone.utc))
    empty=queue(pupil).json();assert empty['items']==[] and empty['end']=='2026-08-17' and empty['nextCursor'] is None
    monkeypatch.setattr(homework_queue,'now',lambda:datetime(2026,8,10,6,0,tzinfo=timezone.utc))
    early=queue(pupil).json();assert early['end']=='2026-08-24'
    assert [r['lessonDate'] for r in early['items']]==['2026-08-17']
    monkeypatch.setattr(homework_queue,'now',lambda:datetime(2027,7,1,6,0,tzinfo=timezone.utc))
    late=queue(pupil).json();assert [r['lessonDate'] for r in late['items']]==['2026-09-14','2026-08-31','2026-08-24','2026-08-17']


def test_staff_default_unchanged(client,monkeypatch):
    pupil,parent,teacher=default_window_data(client,monkeypatch)
    result=queue(teacher).json()
    assert result['window']=='range' and result['filter']=='submitted' and result['start']=='2026-08-17' and result['end']=='2027-06-11'
    result=queue(client,filter='all').json()
    assert result['window']=='range' and [r['lessonDate'] for r in result['items']][:2]==['2026-08-17','2026-08-17']


def test_offline_homework_is_never_overdue(client,monkeypatch):
    record,pupil,parent,teacher=setup(client);key=record['lessonKey']
    online=publish(client,'2026-08-24','2026-08-22')['lessonKey']
    record['document']['content']['homeworkSubmission']='offline'
    assert write(client,record,publish=True).status_code==200
    monkeypatch.setattr(homework_queue,'now',lambda:datetime(2026,8,25,6,0,tzinfo=timezone.utc))
    assert [r['lessonKey'] for r in items(pupil)]==[online] and items(pupil)[0]['overdue']
    assert [r['lessonKey'] for r in items(pupil,filter='overdue')]==[online]
    rows={r['lessonKey']:r for r in items(parent,filter='all',**RANGE)}
    row=rows[key];assert row['submission']=='offline' and not row['overdue'] and not row['deadlineClosed'] and not row['canSubmit']
    assert rows[online]['submission']=='online' and rows[online]['overdue']
    pupil_rows={r['lessonKey']:r for r in items(pupil,filter='all',**RANGE)}
    assert not pupil_rows[key]['canSubmit'] and pupil_rows[online]['canSubmit']
    # Before its date an offline task is listed as a to-do, but never as overdue.
    monkeypatch.setattr(homework_queue,'now',lambda:datetime(2026,8,18,6,0,tzinfo=timezone.utc))
    row={r['lessonKey']:r for r in items(pupil)}[key];assert row['submission']=='offline' and not row['overdue']


def stored(db):
    return [db.execute(f'SELECT * FROM {table} ORDER BY 1,2,3').fetchall() for table in ['homework_history','homework_extension_history','journal_entries']]


def snapshot():
    with api.connect() as db:
        return (db.execute('SELECT * FROM homework_submissions ORDER BY lesson_key').fetchall(),db.execute('SELECT * FROM homework_extensions ORDER BY lesson_key').fetchall(),
                db.execute("SELECT version FROM journal_projection_versions WHERE name='homework_queue'").fetchone(),stored(db))


def legacy_database(client, monkeypatch):
    """Lesson a: accepted under the current conditions, with an individual date. Lesson b: sent under older conditions."""
    from backend import homework, homework_deadlines
    from backend.notifications import encoded
    from test_homework_deadlines import change
    for module in [homework,homework_deadlines,homework_queue]:
        monkeypatch.setattr(module,'now',lambda:datetime(2026,8,20,10,tzinfo=timezone.utc))
    record,pupil,parent,teacher=setup(client);a=record['lessonKey']
    b=publish(client,'2026-08-24','2026-08-26')['lessonKey']
    sent=answer(pupil,a,get(pupil,a)).json();assert review(teacher,a,sent).status_code==200
    assert answer(pupil,b,get(pupil,b)).status_code==200
    assert change(teacher,a,get(teacher,a)).status_code==200
    # Rewrite the database the way the previous release stored it: legacy tokens and a version 1 projection.
    with api.connect() as db:
        published={k:json.loads(d) for k,d in db.execute('SELECT lesson_key,published_document FROM journal_entries WHERE published_document IS NOT NULL')}
        older=json.loads(json.dumps(published[b]));older['content']['homework']='Прежний текст'
        for lesson_key,token in [(a,homework.legacy_fingerprint(published[a])),(b,homework.legacy_fingerprint(older))]:
            document=json.loads(db.execute('SELECT document FROM homework_submissions WHERE lesson_key=?',(lesson_key,)).fetchone()[0])
            document['submitted']['assignment']=token
            db.execute('UPDATE homework_submissions SET document=? WHERE lesson_key=?',(encoded(document),lesson_key))
        db.execute('UPDATE homework_extensions SET assignment=? WHERE lesson_key=?',(homework.legacy_fingerprint(published[a]),a))
        db.execute('DROP TABLE homework_queue_tasks')
        db.execute('''CREATE TABLE homework_queue_tasks (
            workspace TEXT,lesson_key TEXT,sort_date TEXT,lesson_date TEXT,due_date TEXT,
            subject TEXT,group_name TEXT,preview TEXT,assignment TEXT,source_kind TEXT,source_id TEXT,draft_teachers TEXT,late_policy TEXT,
            PRIMARY KEY(workspace,lesson_key))''')
        db.execute("UPDATE journal_projection_versions SET version=1 WHERE name='homework_queue'")
    return pupil,parent,teacher,a,b,sent


def test_legacy_fingerprints_migrate_once_and_keep_changed_answers(client,monkeypatch):
    pupil,parent,teacher,a,b,sent=legacy_database(client,monkeypatch)
    legacy=snapshot()
    first=items(teacher,filter='all')
    # The answer given under the current conditions stays accepted; the one given under older conditions stays changed.
    for reader in [pupil,parent]:
        work=get(reader,a);assert work['status']=='accepted' and not work['assignmentChanged']
    assert get(pupil,a)['assignment']==sent['submitted']['assignment'] and get(pupil,a)['deadline']['extension']
    work=get(pupil,b);assert work['assignmentChanged'] and work['canSubmit']
    rows={r['lessonKey']:r for r in items(parent,filter='all',**RANGE)}
    assert rows[a]['status']=='accepted' and not rows[a]['assignmentChanged'] and rows[a]['extended']
    assert rows[b]['assignmentChanged'] and [r['lessonKey'] for r in items(pupil)]==[b]
    migrated=snapshot();assert migrated[2]==(2,) and migrated[0]!=legacy[0] and migrated[1]!=legacy[1]
    assert migrated[3]==legacy[3]  # answer history, extension history and journal entries are not rewritten
    # The migration is idempotent: running it again changes no document and no row of the queue.
    for _ in range(2):
        with api.connect() as db:db.execute("DELETE FROM journal_projection_versions WHERE name='homework_queue'")
        assert items(teacher,filter='all')==first and snapshot()==migrated


def test_failed_projection_migration_leaves_documents_marker_and_index_untouched(client,monkeypatch):
    from backend import homework_queue_projection
    pupil,parent,teacher,a,b,sent=legacy_database(client,monkeypatch)
    legacy=snapshot()
    def failing(*args,**kwargs):raise RuntimeError('Synthetic migration failure')
    with monkeypatch.context() as patch:
        patch.setattr(homework_queue_projection,'answer',failing)
        with pytest.raises(RuntimeError,match='Synthetic migration'):items(teacher,filter='all')
    assert snapshot()==legacy
    with api.connect() as db:assert len(db.execute('PRAGMA table_info(homework_queue_tasks)').fetchall())==13
    assert get(pupil,a)['status']=='accepted' and not get(pupil,a)['assignmentChanged']
    assert snapshot()[2]==(2,)
