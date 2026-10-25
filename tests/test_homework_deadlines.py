from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException
from backend import api, homework, homework_deadlines, homework_queue
from backend.models import DatedLesson
from test_access import client, signed
from test_homework import setup, get, answer, review
from test_homework_queue import items, queue, publish
from test_journal import DAY, entry, write


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    for module in [homework,homework_deadlines,homework_queue]:
        monkeypatch.setattr(module,'now',lambda:datetime(2026,8,20,10,tzinfo=timezone.utc))


def change(reader,key,current,due='2026-08-24',**extra):
    return reader.put(f'/api/journal/homework/{key}/deadline?workspace=demo',json=dict(
        studentId='s1',assignment=current['assignment'],version=current['deadline']['version'],
        action='extend',dueDate=due,reason='Дополнительное время по согласованию')|extra)


def history(reader,key,**params):
    return reader.get(f'/api/journal/homework/{key}/deadline-history',params=dict(workspace='demo',studentId='s1',**params))


def blocked(client,record):
    record['document']['content']['homeworkLatePolicy']='block'
    result=write(client,record,publish=True);assert result.status_code==200,result.text
    return result.json()


def test_only_target_deadline_changes_draft_and_shared_assignment_preserved(client):
    record,pupil,parent,teacher=setup(client);record=blocked(client,record);key=record['lessonKey']
    current=get(pupil,key);assert current['deadlineClosed'] and not current['canSubmit']
    saved=answer(pupil,key,current,'draft','Личный черновик').json()
    state=api.read('demo');journal=entry(client,key)
    result=change(teacher,key,get(teacher,key));assert result.status_code==200,result.text
    assert result.json()['effectiveDueDate']=='2026-08-24'
    actual=get(pupil,key)
    assert actual['canSubmit'] and actual['canSaveDraft'] and not actual['deadlineClosed']
    assert actual['assignment']==saved['assignment'] and actual['version']==saved['version']
    assert actual['draft']==saved['draft'] and actual['submitted'] is None
    assert get(parent,key)['dueDate']=='2026-08-24' and get(parent,key)['draft'] is None
    assert get(signed('other-pupil'),key,'s2')['dueDate']=='2026-08-19'
    assert entry(client,key)==journal and api.read('demo')==state
    assert not get(parent,key)['deadline']['canEdit']
    for reader in [pupil,parent]:
        notices=reader.get('/api/notifications/inbox').json()['items']
        assert sum(n['action']=='homework.deadline_changed' for n in notices)==1
        assert history(reader,key).json()['items'][0]['reason']=='Дополнительное время по согласованию'
    assert history(signed('other-pupil'),key).status_code==403


def test_effective_dates_move_queue_windows_order_overdue_and_cursor(client):
    record,pupil,parent,teacher=setup(client);record=blocked(client,record);key=record['lessonKey']
    second=publish(client,'2026-08-24','2026-08-22')
    cursor=queue(pupil,limit=1).json()['nextCursor']
    assert change(teacher,key,get(teacher,key)).status_code==200
    # The keyset cursor survives an extension; the row that moved may be repeated or skipped once.
    resumed=queue(pupil,limit=1,cursor=cursor);assert resumed.status_code==200,resumed.text
    assert resumed.json()['items'][0]['lessonKey']==second['lessonKey']
    assert items(pupil,start='2026-08-19',end='2026-08-19')==[]
    row=items(parent,start='2026-08-24',end='2026-08-24')[0]
    assert row['extended'] and row['baseDueDate']=='2026-08-19' and row['dueDate']=='2026-08-24'
    assert not row['overdue'] and not row['deadlineClosed'] and not row['canSubmit']
    first=queue(pupil,limit=1).json();last=queue(pupil,limit=1,cursor=first['nextCursor']).json()
    assert first['items'][0]['lessonKey']==second['lessonKey'] and last['items'][0]['lessonKey']==key
    assert last['nextCursor'] is None and last['items'][0]['canSubmit']
    assert items(pupil,filter='overdue')==[]
    other=items(signed('other-pupil'),filter='overdue')[0]
    assert other['deadlineClosed'] and not other['extended']
    teacher_rows=items(teacher,filter='all',start='2026-08-24',end='2026-08-24')
    assert [(r['lessonKey'],r['studentId']) for r in teacher_rows]==[(key,'s1')]


def test_submission_snapshot_survives_extension_return_and_revocation(client):
    record,pupil,parent,teacher=setup(client);key=record['lessonKey']
    sent=answer(pupil,key,get(pupil,key)).json();old=sent['submitted']
    assert old['late'] and old['dueDate']=='2026-08-19'
    assert review(teacher,key,sent).status_code==200
    assert change(teacher,key,get(teacher,key)).status_code==200
    assert not get(pupil,key)['canSubmit'] and not get(pupil,key)['assignmentChanged']
    assert get(parent,key)['submitted']['late']
    assert review(teacher,key,get(teacher,key),'returned','Дополнить ответ').status_code==200
    second=answer(pupil,key,get(pupil,key)).json()
    assert second['submitted']['dueDate']=='2026-08-24' and not second['submitted']['late']
    assert second['submitted']['deadlineVersion']==1 and second['submitted']['baseDueDate']=='2026-08-19'
    assert change(teacher,key,get(teacher,key),due=None,action='revoke').status_code==200
    assert get(parent,key)['submitted']==second['submitted']
    original=pupil.get(f'/api/journal/homework/{key}/history?workspace=demo&studentId=s1').json()['items'][-1]['submitted']
    assert original==old


def test_revoke_closes_intake_and_stale_submit_is_atomic(client):
    record,pupil,_,teacher=setup(client);key=record['lessonKey'];blocked(client,record)
    assert change(teacher,key,get(teacher,key)).status_code==200
    draft=answer(pupil,key,get(pupil,key),'draft','Сохранённый ответ').json()
    assert change(teacher,key,get(teacher,key),due=None,action='revoke').status_code==200
    payload=dict(studentId='s1',version=draft['version'],assignment=draft['assignment'],deadlineVersion=1,action='submit',text='Не записывать')
    assert pupil.put(f'/api/journal/homework/{key}?workspace=demo',json=payload).status_code==409
    assert answer(pupil,key,draft).status_code==409  # Legacy client also uses the current deadline.
    current=get(pupil,key)
    assert current['draft']==draft['draft'] and current['version']==draft['version']
    assert current['deadlineClosed'] and current['canSaveDraft']
    assert [h['action'] for h in history(pupil,key).json()['items']]==['revoke','extend']


def test_school_midnight_uses_extension_not_global_date(client,monkeypatch):
    record,pupil,_,teacher=setup(client);key=record['lessonKey'];blocked(client,record)
    assert change(teacher,key,get(teacher,key),due='2026-08-20').status_code==200
    for module in [homework,homework_deadlines,homework_queue]:
        monkeypatch.setattr(module,'now',lambda:datetime(2026,8,20,18,59,59,tzinfo=timezone.utc))
    assert get(pupil,key)['canSubmit'] and items(pupil)[0]['canSubmit']
    for module in [homework,homework_deadlines,homework_queue]:
        monkeypatch.setattr(module,'now',lambda:datetime(2026,8,20,19,0,tzinfo=timezone.utc))
    assert not get(pupil,key)['canSubmit'] and items(pupil,filter='overdue')[0]['deadlineClosed']


def test_simultaneous_revoke_and_submit_use_one_committed_deadline(client):
    record,pupil,_,teacher=setup(client);key=record['lessonKey'];blocked(client,record)
    assert change(teacher,key,get(teacher,key)).status_code==200
    current=get(pupil,key)
    payload=dict(studentId='s1',version=current['version'],assignment=current['assignment'],deadlineVersion=1,action='submit',text='Одновременная отправка')
    with ThreadPoolExecutor(2) as pool:
        sent=pool.submit(pupil.put,f'/api/journal/homework/{key}?workspace=demo',json=payload)
        revoked=pool.submit(change,teacher,key,current,due=None,action='revoke')
        result=sent.result();assert revoked.result().status_code==200
    assert result.status_code in [200,409]
    actual=get(pupil,key);assert actual['deadlineClosed'] and actual['deadline']['version']==2
    if result.status_code==200:
        assert actual['submitted']['dueDate']=='2026-08-24' and actual['submitted']['deadlineVersion']==1 and not actual['submitted']['late']
    else:assert actual['submitted'] is None and actual['version']==0


@pytest.mark.parametrize('actor',['student','parent','other-pupil','unrelated'])
def test_only_current_staff_may_change_deadline(client,actor):
    record,_,_,teacher=setup(client);key=record['lessonKey'];current=get(teacher,key)
    assert change(signed(actor),key,current).status_code==403
    assert get(teacher,key)['deadline']['version']==0


def test_current_teacher_cancelled_and_removed_membership(client):
    record,pupil,_,teacher=setup(client);key=record['lessonKey'];current=get(teacher,key)
    state=api.read('demo');state.datedLessons=[DatedLesson(id='replace',name='Замена',kind='change',placementId='eng',sourceDate=DAY,onDate=DAY,teacherIds=['t1'])]
    api.save('demo',state);replacement=signed('unrelated')
    # The course teacher keeps the right to extend; a substitute reads the work but cannot.
    assert change(replacement,key,get(replacement,key)).status_code==403
    assert change(teacher,key,current).status_code==200
    state=api.read('demo');group=next(g for g in state.groups if g.id==record['document']['lesson']['groupId'])
    group.kind='elective';group.studentIds=['s2'];api.save('demo',state)
    assert not get(pupil,key)['canSubmit'] and not get(teacher,key)['deadline']['canEdit']
    assert change(teacher,key,get(teacher,key)).status_code==403
    state=api.read('demo');state.datedLessons=[DatedLesson(id='cancel',name='Отмена',kind='cancel',placementId='eng',sourceDate=DAY,onDate=DAY)]
    api.save('demo',state)
    assert change(client,key,get(client,key)).status_code==403


@pytest.mark.parametrize('mode',['text','deadline','cleared','withdrawn'])
def test_publication_changes_invalidate_permanently_but_drafts_do_not(client,mode):
    record,pupil,_,teacher=setup(client);key=record['lessonKey'];current=get(pupil,key)
    assert change(teacher,key,current).status_code==200
    draft=entry(client,key);draft['document']['content']['topic']='Только тема'
    assert write(client,draft,publish=True).status_code==200
    assert get(pupil,key)['deadline']['version']==1
    draft=entry(client,key);draft['document']['content']['homework']='Новое условие'
    assert write(client,draft).status_code==200 and get(pupil,key)['deadline']['version']==1
    def publish_due(due):
        moved=entry(client,key);moved['document']['content']['dueDate']=due
        assert write(client,moved,publish=True).status_code==200
    if mode=='text':
        assert write(client,entry(client,key),publish=True).status_code==200
        actual=get(pupil,key)
        # A new text is not a reason to cancel an individual date.
        assert actual['assignment']==current['assignment'] and actual['deadline']['version']==1 and actual['deadline']['extension']
        assert actual['dueDate']=='2026-08-24' and [h['action'] for h in history(pupil,key).json()['items']]==['extend']
        return
    if mode=='deadline':
        publish_due('2026-08-22')  # still before the individual date
        assert get(pupil,key)['deadline']['version']==1 and get(pupil,key)['deadline']['extension']
        publish_due('2026-08-24')  # reaches the individual date
    elif mode=='cleared':publish_due(None)
    else:
        assert client.post('/api/journal/withdraw?workspace=demo',json=dict(lessonKey=key,version=entry(client,key)['version'])).status_code==200
        assert history(pupil,key).status_code==404
        restored=entry(client,key);restored['document']['content']['homework']=record['document']['content']['homework']
        assert write(client,restored,publish=True).status_code==200
    actual=get(pupil,key)
    assert actual['assignment']==current['assignment'] and actual['deadline']['extension'] is None
    assert actual['deadline']['version']==2 and actual['dueDate']=={'deadline':'2026-08-24','cleared':None,'withdrawn':'2026-08-19'}[mode]
    events=history(pupil,key).json()['items']
    assert [h['action'] for h in events]==['invalidated','extend']
    assert {'deadline':'перенесён','cleared':'снят','withdrawn':'отозвана'}[mode] in events[0]['reason']
    assert change(teacher,key,current).status_code==409


def test_extension_survives_material_change_and_queue_keeps_it(client):
    from test_assignment_files import upload as upload_material, save_materials
    from test_homework_queue import RANGE
    record,pupil,parent,teacher=setup(client);key=record['lessonKey']
    assert change(teacher,key,get(teacher,key)).status_code==200
    material=upload_material(client,record).json();save_materials(client,record,[material['id']])
    actual=get(pupil,key);assert actual['deadline']['version']==1 and actual['deadline']['extension'] and actual['dueDate']=='2026-08-24'
    assert items(parent,start='2026-08-24',end='2026-08-24')[0]['extended'] is True
    # Each pupil of the lesson appears exactly once, whether or not an individual date applies.
    rows=[(r['lessonKey'],r['studentId']) for r in items(client,filter='all',**RANGE) if r['lessonKey']==key]
    assert rows.count((key,'s1'))==1 and len(rows)==len(set(rows))


def test_offline_homework_has_no_individual_deadline(client):
    record,pupil,parent,teacher=setup(client);key=record['lessonKey']
    record['document']['content']['homeworkSubmission']='offline'
    assert write(client,record,publish=True).status_code==200
    current=get(teacher,key);assert not current['deadline']['canEdit']
    assert change(teacher,key,current).status_code==422


def test_validation_and_concurrent_cas_notify_exactly_once(client):
    record,pupil,parent,teacher=setup(client);key=record['lessonKey'];current=get(teacher,key)
    for extra in [dict(due='2026-08-19'),dict(due='2027-08-21'),dict(reason='  '),dict(due=None)]:
        assert change(teacher,key,current,**extra).status_code==422
    assert change(teacher,key,current,due=None,action='revoke').status_code==409
    with ThreadPoolExecutor(2) as pool:
        results=list(pool.map(lambda _:change(teacher,key,current).status_code,range(2)))
    assert sorted(results)==[200,409]
    assert len(history(parent,key).json()['items'])==1
    assert sum(n['action']=='homework.deadline_changed' for n in parent.get('/api/notifications/inbox').json()['items'])==1
    record=entry(client,key);record['document']['content']['dueDate']=None
    assert write(client,record,publish=True).status_code==200
    assert not get(teacher,key)['deadline']['canEdit']
    assert change(teacher,key,get(teacher,key)).status_code==422


@pytest.mark.parametrize('due',['9999-07-01','9999-12-31'])
def test_existing_far_future_deadline_remains_readable(client,due):
    record,pupil,_,teacher=setup(client);key=record['lessonKey']
    record['document']['content']['dueDate']=due
    assert write(client,record,publish=True).status_code==200
    current=get(pupil,key)
    assert current['dueDate']==due and current['deadline']['maxDate']=='9999-12-31'
    result=change(teacher,key,current,due='9999-12-31')
    assert result.status_code==(422 if due=='9999-12-31' else 200)


def test_transaction_rolls_back_extension_history_queue_and_notifications(client,monkeypatch):
    record,pupil,parent,teacher=setup(client);key=record['lessonKey'];current=get(teacher,key)
    inbox=parent.get('/api/notifications/inbox').json()
    def snapshots():
        with api.connect() as db:return {t:db.execute('SELECT * FROM '+t).fetchall() for t in ['homework_extensions','homework_extension_history','homework_queue_versions','audit']}
    before=snapshots();notify=api.homework.notify
    def failed(*args):notify(*args);raise HTTPException(409,'Synthetic failure after notification')
    monkeypatch.setattr(api.homework,'notify',failed)
    assert change(teacher,key,current).status_code==409
    assert snapshots()==before and get(pupil,key)['deadline']['version']==0
    assert parent.get('/api/notifications/inbox').json()==inbox


@pytest.mark.parametrize('mutation',['account','schedule','publication'])
def test_write_rechecks_prepared_context(client,monkeypatch,mutation):
    record,_,_,teacher=setup(client);key=record['lessonKey'];current=get(teacher,key);original=api.homework.context
    def raced(*args,**kwargs):
        ctx=original(*args,**kwargs)
        if mutation=='account':
            with api.connect() as db:db.execute("UPDATE accounts SET version=version+1 WHERE login='teacher'")
        elif mutation=='schedule':api.save('demo',api.read('demo'))
        else:
            draft=entry(client,key);draft['document']['content']['homework']='Другая задача'
            assert write(client,draft,publish=True).status_code==200
        return ctx
    monkeypatch.setattr(api.homework,'context',raced)
    assert change(teacher,key,current).status_code==409
    with api.connect() as db:assert db.execute('SELECT count(*) FROM homework_extensions').fetchone()[0]==0


def test_history_pagination_and_full_backup_restore(client,tmp_path,monkeypatch):
    from tools.backup_school import create_backup
    record,_,parent,teacher=setup(client);key=record['lessonKey']
    for day in range(21,32):
        assert change(teacher,key,get(teacher,key),due=f'2026-08-{day}').status_code==200
        assert change(teacher,key,get(teacher,key),due=None,action='revoke').status_code==200
    first=history(parent,key).json();assert len(first['items'])==20 and first['nextBefore']
    second=history(parent,key,before=first['nextBefore']).json()
    assert len(second['items'])==2 and not second['nextBefore']
    assert len({h['id'] for h in first['items']+second['items']})==22
    assert change(teacher,key,get(teacher,key),due='2026-09-01').status_code==200
    expected=get(parent,key);events=history(parent,key).json();source=api.DB;destination=tmp_path/'backup'
    create_backup(source,destination);monkeypatch.setattr(api,'DB',destination/source.name)
    assert get(parent,key)==expected and history(parent,key).json()==events
    assert items(parent,start='2026-09-01',end='2026-09-01')[0]['extended']
