from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import json
from uuid import uuid4
import pytest
from backend import api,absence_bulk
from test_access import client,create,signed
from test_absences import setup,body,send,decide,url,mark,upload,reviewers
from test_journal import entry,report


def batch(items,**values):
    return dict(requestId=str(uuid4()),items=[dict(id=n['id'],version=n['version']) for n in items],action='approve',comment='Проверены все выбранные заявления')|values


def prepare(teacher,items,**values):
    response=teacher.post(url('/bulk/preview'),json=batch(items,**values))
    assert response.status_code==200,response.text
    return response.json()


def apply(teacher,preview):return teacher.post(url('/bulk/'+preview['id']+'/apply'))


def two(parent):return [send(parent),send(parent,body(startDate='2026-08-18',endDate='2026-08-18'))]


def counts():
    with api.connect() as db:
        return {t:db.execute(f'SELECT count(*) FROM {t}').fetchone()[0] for t in ['absence_history','notifications','audit']}


def test_preview_private_details_and_atomic_effect_with_replay(client):
    _,_,parent,teacher=setup(client);school=api.read('demo');record=mark(client)
    payload=body();file=upload(parent,payload).json()
    notices=[send(parent,payload|dict(fileIds=[file['id']])),send(parent,body(startDate='2026-08-18',endDate='2026-08-18'))]
    request=batch(notices)
    preview=teacher.post(url('/bulk/preview'),json=request).json()
    assert len(preview['items'])==2 and preview['items'][0]['reason']==payload['reason']
    assert preview['items'][0]['files'][0]['id']==file['id']
    assert report(parent).json()['summary']['attendance']['absent']==1
    before=counts();assert teacher.post(url('/bulk/preview'),json=request).json()==preview
    assert counts()==before
    response=apply(teacher,preview);assert response.status_code==200,response.text
    result=response.json();assert result['count']==2 and all(n['version']==2 and n['status']=='approved' for n in result['items'])
    assert 'reason' not in result['items'][0]
    assert report(parent).json()['summary']['attendance']['excused']==1
    assert entry(client,record['lessonKey'])['document']==record['document']
    for notice in notices:
        history=parent.get(url('/'+notice['id']+'/history')).json()['items']
        assert len(history)==2 and history[0]['batchId']==preview['id'] and history[0]['comment']==request['comment']
    after=counts()
    assert apply(teacher,preview).json()==result
    assert teacher.post(url('/bulk/preview'),json=request).json()==result
    assert counts()==after and api.read('demo')==school
    assert teacher.post(url('/bulk/preview'),json=request|dict(comment='Другой текст')).status_code==409
    own=parent.get('/api/notifications/inbox').json()['items']
    assert sum(n['action']=='absence.updated' for n in own)==1
    other=signed('other-parent').get('/api/notifications/inbox').json()['items']
    assert sum(n['action']=='journal.updated' for n in other)==2  # journal publication, then batch
    assert not any(n['action']=='absence.updated' for n in other)


def test_all_or_nothing_when_one_notice_changes(client):
    _,_,parent,teacher=setup(client);notices=two(parent);preview=prepare(teacher,notices)
    assert decide(client,notices[1],'reject').status_code==200
    before=counts();response=apply(teacher,preview);assert response.status_code==409
    assert counts()==before
    assert client.get(url('/'+notices[0]['id'])).json()['status']=='pending'
    with api.connect() as db:assert db.execute('SELECT status FROM absence_batches WHERE id=?',(preview['id'],)).fetchone()[0]=='preview'


def test_concurrent_batches_and_repeated_application(client):
    _,_,parent,teacher=setup(client);notices=two(parent)
    choices=[prepare(teacher,notices),prepare(teacher,notices,action='reject')]
    with ThreadPoolExecutor(max_workers=2) as pool:responses=list(pool.map(lambda p:apply(teacher,p),choices))
    assert sorted(r.status_code for r in responses)==[200,409]
    winner=choices[next(i for i,r in enumerate(responses) if r.status_code==200)]
    before=counts()
    with ThreadPoolExecutor(max_workers=2) as pool:repeated=list(pool.map(lambda _:apply(teacher,winner),range(2)))
    assert all(r.status_code==200 for r in repeated) and repeated[0].json()==repeated[1].json()
    assert counts()==before


def test_preview_and_receipt_bound_to_actor_and_workspace(client):
    _,_,parent,teacher=setup(client);notices=two(parent);preview=prepare(teacher,notices)
    for account in [parent,signed('other-parent'),signed('student')]:
        assert account.post(url('/bulk/preview'),json=batch(notices)).status_code==403
        assert apply(account,preview).status_code==403
    assert signed('other-teacher').post(url('/bulk/preview'),json=batch(notices)).status_code==404
    assert apply(client,preview).status_code==404
    assert client.post('/api/journal/absences/bulk/'+preview['id']+'/apply?workspace=school').status_code==404
    assert apply(teacher,dict(id='missing')).status_code==404
    assert apply(teacher,preview).status_code==200
    reviewers({})
    assert apply(teacher,preview).status_code==404


def test_mixed_class_selection_does_not_leak_details(client):
    _,_,parent,teacher=setup(client);notice=send(parent)
    pupil=next(s for s in api.read('demo').students if s.active and s.classId!='c-demo-a')
    create(client,'parent',login='different-class',grants=[dict(workspace='demo',view='student',entityIds=[pupil.id])])
    other=send(signed('different-class'),body(studentId=pupil.id,reason='Other class private reason'))
    response=teacher.post(url('/bulk/preview'),json=batch([notice,other]))
    assert response.status_code==404 and 'Other class private reason' not in response.text
    assert prepare(client,[notice,other])['items'][1]['reason']=='Other class private reason'
    assert client.get(url('/'+notice['id'])).json()['version']==1


@pytest.mark.parametrize('change',['reviewers','expiry','transfer'])
def test_preview_invalidated_by_context_changes(client,monkeypatch,change):
    _,person,parent,teacher=setup(client);notices=two(parent);preview=prepare(teacher,notices)
    if change=='expiry':
        clock=absence_bulk.now();monkeypatch.setattr(absence_bulk,'now',lambda:clock+timedelta(minutes=15))
    elif change=='reviewers':
        reviewers({'c-demo-a':[person['id']]})
    else:
        state=api.read('demo')
        next(s for s in state.students if s.id=='s1').classId=next(c.id for c in state.classes if c.id!='c-demo-a')
        api.save('demo',state)
    before=counts();assert apply(teacher,preview).status_code==(404 if change=='transfer' else 409)
    assert counts()==before and client.get(url('/'+notices[0]['id'])).json()['version']==1


def test_timetable_save_does_not_invalidate_bulk_preview(client):
    _,_,parent,teacher=setup(client);notices=two(parent);preview=prepare(teacher,notices)
    state=api.read('demo');state.settings.name='School revision changed';api.save('demo',state)
    before=counts();response=apply(teacher,preview);assert response.status_code==200,response.text
    assert all(n['status']=='approved' and n['version']==2 for n in response.json()['items'])
    assert counts()['absence_history']==before['absence_history']+2


def test_notification_failure_rolls_back_whole_batch_and_can_retry(client,monkeypatch):
    _,_,parent,teacher=setup(client);notices=two(parent);preview=prepare(teacher,notices)
    original=api.notifications.deliver;calls=0
    def fail(*args,**kwargs):
        nonlocal calls
        calls+=1
        result=original(*args,**kwargs)
        if calls==2:raise RuntimeError('batch failed after private and journal notifications')
        return result
    before=counts();monkeypatch.setattr(api.notifications,'deliver',fail)
    with pytest.raises(RuntimeError):apply(teacher,preview)
    assert counts()==before
    assert all(client.get(url('/'+n['id'])).json()['version']==1 for n in notices)
    monkeypatch.setattr(api.notifications,'deliver',original)
    assert apply(teacher,preview).status_code==200


def test_revoke_reject_and_backup_preserve_receipt_and_raw_attendance(client,tmp_path,monkeypatch):
    from tools.backup_school import create_backup
    _,_,parent,teacher=setup(client);record=mark(client);notices=two(parent)
    preview=prepare(teacher,notices);approved=apply(teacher,preview).json()
    reversal=prepare(teacher,approved['items'],action='revoke')
    assert apply(teacher,reversal).status_code==200
    assert report(parent).json()['summary']['attendance']['absent']==1
    assert entry(client,record['lessonKey'])['document']==record['document']
    fresh=send(parent);rejected=apply(teacher,prepare(teacher,[fresh],action='reject')).json()
    assert rejected['items'][0]['status']=='rejected'
    assert teacher.post(url('/bulk/preview'),json=batch(rejected['items'])).status_code==409
    destination=tmp_path/'bulk-backup';source=api.DB;create_backup(source,destination)
    monkeypatch.setattr(api,'DB',destination/source.name)
    before=counts();assert apply(teacher,preview).json()==approved and counts()==before
    assert report(parent).json()['summary']['attendance']['absent']==1


def test_bounded_selection_rate_and_blank_validation(client):
    _,_,parent,teacher=setup(client);notice=send(parent);item=dict(id=notice['id'],version=1)
    for values in [dict(items=[]),dict(items=[item,item]),dict(items=[dict(item,id=uuid4().hex) for _ in range(51)]),dict(comment=' '),dict(action='withdraw'),dict(items=[dict(item,version=0)])]:
        assert teacher.post(url('/bulk/preview'),json=batch([notice])|values).status_code==422
    previews=[prepare(teacher,[notice]) for _ in range(30)]
    assert teacher.post(url('/bulk/preview'),json=batch([notice])).status_code==429
    # Rate limiting new previews must never prevent applying an already reviewed one.
    assert apply(teacher,previews[0]).status_code==200


def test_fifty_notice_bound_and_current_account_recheck(client,monkeypatch):
    _,person,parent,teacher=setup(client);notice=send(parent)
    with api.connect() as db:
        row=list(db.execute('SELECT * FROM absence_notices WHERE id=?',(notice['id'],)).fetchone())
        for _ in range(49):
            row[0]=uuid4().hex;row[13]=str(uuid4());db.execute('INSERT INTO absence_notices VALUES ('+','.join('?'*len(row))+')',row)
    notices=client.get(url()).json()['items'];assert len(notices)==50
    preview=prepare(teacher,notices)
    original=api.absences.recheck
    def changed(db,user,snap,workspace):
        db.execute('UPDATE accounts SET version=version+1 WHERE id=?',(person['id'],))
        original(db,user,snap,workspace)
    monkeypatch.setattr(api.absences,'recheck',changed)
    assert apply(teacher,preview).status_code==409
    monkeypatch.setattr(api.absences,'recheck',original)
    result=apply(teacher,preview);assert result.status_code==200,result.text
    assert result.json()['count']==50


def test_bulk_refuses_notices_in_closed_period_without_partial_writes(client,monkeypatch):
    # Imported here: test_journal_periods imports test_journal_coverage, which imports test_absences.
    from test_journal_periods import period,close
    from datetime import datetime,timezone
    stamp=datetime(2026,10,14,10,tzinfo=timezone.utc)
    monkeypatch.setattr('backend.journal_periods.now',lambda:stamp);monkeypatch.setattr('backend.journal_coverage.now',lambda:stamp)
    _,_,parent,teacher=setup(client);notices=two(parent);prepared=prepare(teacher,notices)
    close(client,period(client))
    before=counts();response=teacher.post(url('/bulk/preview'),json=batch(notices))
    assert response.status_code==403,response.text
    detail=response.json()['detail'];assert '2026-08-17 00:00' in detail and 'Первая четверть' in detail and '2026-08-18' not in detail
    assert counts()==before
    def untouched():
        for n in notices:
            item=client.get(url('/'+n['id'])).json();assert item['status']=='pending' and item['version']==1
    untouched();before=counts()
    # A preview prepared before the period closed is refused when applied.
    assert apply(teacher,prepared).status_code==403 and counts()==before
    # Rejection changes no figures, so the same notices can still be rejected in bulk.
    rejected=apply(teacher,prepare(teacher,notices,action='reject'));assert rejected.status_code==200,rejected.text
    assert [n['status'] for n in rejected.json()['items']]==['rejected','rejected']


def test_bulk_revoke_names_every_notice_in_closed_periods(client,monkeypatch):
    from test_journal_periods import period,close
    from datetime import datetime,timezone
    stamp=datetime(2026,10,14,10,tzinfo=timezone.utc)
    monkeypatch.setattr('backend.journal_periods.now',lambda:stamp);monkeypatch.setattr('backend.journal_coverage.now',lambda:stamp)
    _,_,parent,teacher=setup(client);approved=apply(teacher,prepare(teacher,two(parent))).json()['items']
    close(client,period(client));close(client,period(client,name='Вторая четверть',start='2026-08-18',end='2026-08-18'))
    before=counts();response=teacher.post(url('/bulk/preview'),json=batch(approved,action='revoke'))
    assert response.status_code==403,response.text
    detail=response.json()['detail']
    assert 'Общее решение не применено' in detail
    for fragment in ['2026-08-17 00:00','2026-08-18 00:00','Первая четверть','Вторая четверть']:assert fragment in detail,detail
    assert counts()==before
    for n in approved:
        item=client.get(url('/'+n['id'])).json();assert item['status']=='approved' and item['version']==n['version']
