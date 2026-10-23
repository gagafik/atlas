from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timedelta,timezone
import json
from uuid import uuid4
import pytest
from backend import api,absence_files,homework_files
from test_access import client,enable,create,signed
from test_journal import DAY,lesson,entry,write,graded,report


def url(path=''):return '/api/journal/absences'+path+'?workspace=demo'
def body(**values):return dict(requestId=str(uuid4()),studentId='s1',startDate=DAY,endDate=DAY,reason='Личная причина отсутствия',fileIds=[])|values
def setup(client):
    enable(client);parent=create(client,'parent');teacher=create(client,'teacher');create(client,'student');create(client,'parent',login='other-parent');create(client,'teacher',login='other-teacher',grants=[dict(workspace='demo',view='teacher',entityIds=['t1'])])
    assigned=client.put(url('/reviewers'),json=dict(version=0,assignments={'c-demo-a':[teacher['id']]}));assert assigned.status_code==200,assigned.text
    return parent,teacher,signed('parent'),signed('teacher')
def send(parent,payload=None):
    r=parent.post(url(),json=payload or body());assert r.status_code==200,r.text;return r.json()
def decide(client,item,action='approve',**values):return client.post(url('/'+item['id']+'/decision'),json=dict(requestId=str(uuid4()),version=item['version'],action=action,comment='Решение сотрудника')|values)
def upload(parent,payload,content=b'Private evidence',name='evidence.txt'):
    return parent.post('/api/journal/absence-files',params=dict(workspace='demo',studentId=payload['studentId'],draftId=payload['requestId'],filename=name),content=content,headers={'Content-Type':'application/octet-stream'})
def file_url(id):return '/api/journal/absence-files/'+id+'?workspace=demo'
def mark(client,attendance='absent',publish=True):
    r=graded(entry(client,lesson(client)['lessonKey']))
    for p in r['document']['content']['records']:p['attendance']=attendance
    result=write(client,r,publish=publish);assert result.status_code==200,result.text;return result.json()


def test_submit_review_revoke_changes_only_effective_attendance(client):
    _,_,parent,teacher=setup(client);school=api.read('demo');record=mark(client)
    notice=send(parent)
    assert report(parent).json()['summary']['attendance']['absent']==1
    approved=decide(teacher,notice);assert approved.status_code==200,approved.text
    projected=report(parent).json();assert projected['summary']['attendance']['excused']==1
    assert projected['rows'][0]['absenceApproval']['id']==notice['id']
    assert 'Личная причина' not in json.dumps(projected,ensure_ascii=False)
    current=entry(client,record['lessonKey']);assert current['document']==record['document'] and current['version']==record['version']
    assert current['attendanceApprovals']['s1']['id']==notice['id']
    assert 's2' not in current['attendanceApprovals']
    assert decide(teacher,approved.json(),'revoke').status_code==200
    assert report(parent).json()['rows'][0]['record']['attendance']=='absent'
    assert entry(client,record['lessonKey'])['attendanceApprovals']=={}
    history=parent.get(url('/'+notice['id']+'/history')).json()['items']
    assert [h['action'] for h in history]==['revoked','approved','submitted']
    assert api.read('demo')==school


def test_private_evidence_author_other_guardian_student_and_teacher_scopes(client):
    _,_,parent,teacher=setup(client);payload=body();file=upload(parent,payload);assert file.status_code==200,file.text
    id=file.json()['id'];assert teacher.get(file_url(id)).status_code==404
    notice=send(parent,payload|dict(fileIds=[id]));path=url('/'+notice['id'])
    assert parent.get(file_url(id)).content==b'Private evidence' and teacher.get(file_url(id)).content==b'Private evidence'
    for account in [signed('other-parent'),signed('other-teacher'),signed('student')]:
        assert account.get(path).status_code in [403,404]
        assert account.get(file_url(id)).status_code in [403,404]
        assert account.get(url('/'+notice['id']+'/history')).status_code in [403,404]
    assert signed('other-parent').get(url()).json()['items']==[]
    assert signed('other-teacher').get(url()).json()['items']==[]
    assert 'reason' not in parent.get(url()).json()['items'][0]
    assert client.get(path.replace('demo','school')).status_code==404
    assert teacher.get('/api/journal/report?workspace=demo&studentId=s1&start=2026-08-01&end=2026-09-01').json()['rows']==[]
    assert parent.delete(file_url(id)).status_code==409


def test_current_coordinator_assignment_is_required_and_does_not_grant_other_grades(client):
    _,_,parent,teacher=setup(client);notice=send(parent);mark(client)
    other=signed('other-teacher');assert decide(other,notice).status_code==404
    assert client.put(url('/reviewers'),json=dict(version=1,assignments={})).status_code==200
    assert teacher.get(url('/'+notice['id'])).status_code==404
    assert decide(teacher,notice).status_code==404
    assert teacher.put(url('/reviewers'),json=dict(version=2,assignments={})).status_code==403
    assert client.put(url('/reviewers'),json=dict(version=1,assignments={})).status_code==409
    assert client.put(url('/reviewers'),json=dict(version=2,assignments={'unknown':['bad']})).status_code==422
    assert decide(client,notice).status_code==200


def test_parent_revocation_and_expiry_close_private_notice_and_files(client,monkeypatch):
    person,_,parent,teacher=setup(client);payload=body();file=upload(parent,payload).json();notice=send(parent,payload|dict(fileIds=[file['id']]))
    target=f"/api/access/families/{person['id']}/s1?workspace=demo";link=client.get(target).json()
    value={k:link[k] for k in ['version','accountVersion','relationship','verified','evidence','reason','startDate','endDate','status']}
    assert client.put(target,json=value|dict(status='revoked')).status_code==200
    assert parent.get(url()).json()['items']==[]
    assert parent.get(url('/'+notice['id'])).status_code==404
    assert parent.get(file_url(file['id'])).status_code==404
    assert decide(parent,notice,'withdraw').status_code==404
    assert decide(teacher,notice).status_code==200
    from backend import families
    link=client.get(target).json();value.update(version=link['version'],accountVersion=link['accountVersion'],endDate='2026-10-14',status='active')
    monkeypatch.setattr(families,'now',lambda:datetime(2026,10,14,18,59,tzinfo=timezone.utc))
    assert client.put(target,json=value).status_code==200 and parent.get(url('/'+notice['id'])).status_code==200
    monkeypatch.setattr(families,'now',lambda:datetime(2026,10,14,19,0,tzinfo=timezone.utc))
    assert parent.get(file_url(file['id'])).status_code==404


def test_period_validation_preview_boundaries_and_future_journal(client):
    _,_,parent,teacher=setup(client)
    for values in [dict(endDate='2026-09-18'),dict(endDate='2026-08-16'),dict(startDate='2025-08-17'),dict(startTime='10:00',endTime='09:00'),dict(reason=' '),dict(startTime='26:00')]:
        assert parent.post(url(),json=body(**values)).status_code==422
    preview=parent.post(url('/preview'),json=body()).json();assert preview['lessons']
    english=next(r for r in preview['lessons'] if r['subject']=='Английский язык')
    notice=send(parent,body(startTime=english['start'],endTime=english['end']))
    assert decide(teacher,notice).status_code==200
    record=mark(client,publish=False);assert report(parent).json()['rows']==[]
    published=write(client,record,publish=True);assert published.status_code==200
    assert report(parent).json()['summary']['attendance']['excused']==1
    # A partial lesson interval must not excuse the whole lesson.
    revoked=client.get(url('/'+notice['id'])).json();assert decide(teacher,revoked,'revoke').status_code==200
    parts=send(parent,body(startTime='08:21',endTime=english['end']));assert decide(teacher,parts).status_code==200
    assert report(parent).json()['summary']['attendance']['absent']==1


@pytest.mark.parametrize('attendance',['present','late','unmarked','excused'])
def test_explanation_does_not_override_teacher_observation(client,attendance):
    _,_,parent,teacher=setup(client);mark(client,attendance);notice=send(parent);assert decide(teacher,notice).status_code==200
    assert report(parent).json()['rows'][0]['record']['attendance']==attendance


def test_replay_duplicate_overlap_and_no_mutation_on_changed_payload(client):
    _,_,parent,teacher=setup(client);payload=body();notice=send(parent,payload)
    assert send(parent,payload)['id']==notice['id']
    assert parent.post(url(),json=payload|dict(reason='Другое содержание')).status_code==409
    assert parent.post(url(),json=body(startTime='08:00',endTime='09:00')).status_code==409
    assert signed('other-parent').post(url(),json=body()).status_code==409
    decision=dict(requestId=str(uuid4()),version=1,action='approve',comment='Согласовано')
    for _ in range(2):assert teacher.post(url('/'+notice['id']+'/decision'),json=decision).status_code==200
    assert teacher.post(url('/'+notice['id']+'/decision'),json=decision|dict(action='reject')).status_code==409
    with api.connect() as db:
        assert db.execute('SELECT count(*) FROM absence_notices').fetchone()[0]==1
        assert db.execute('SELECT count(*) FROM absence_history').fetchone()[0]==2


def test_parent_withdrawal_rejection_and_explicit_reversal(client):
    _,_,parent,teacher=setup(client);notice=send(parent)
    assert decide(parent,notice,'approve').status_code==403
    assert decide(parent,notice,'withdraw').status_code==200
    assert decide(teacher,notice).status_code==409
    notice=send(parent);rejected=decide(teacher,notice,'reject');assert rejected.status_code==200
    assert decide(teacher,rejected.json(),'approve').status_code==409
    notice=send(parent);approved=decide(teacher,notice).json()
    assert decide(parent,approved,'withdraw').status_code==409
    assert decide(teacher,approved,'revoke').status_code==200


def test_decision_concurrency_and_transactional_notification_rollback(client,monkeypatch):
    _,_,parent,teacher=setup(client);notice=send(parent)
    with ThreadPoolExecutor(max_workers=2) as pool:codes=list(pool.map(lambda action:decide(teacher,notice,action).status_code,['approve','reject']))
    assert sorted(codes)==[200,409]
    before=client.get(url('/'+notice['id'])).json()
    if before['status']=='approved':assert decide(teacher,before,'revoke').status_code==200
    next_notice=send(parent)
    def fail(*args,**kwargs):raise RuntimeError('notification failed')
    monkeypatch.setattr(api.notifications,'deliver',fail)
    with pytest.raises(RuntimeError):decide(teacher,next_notice)
    assert client.get(url('/'+next_notice['id'])).json()['version']==1
    with api.connect() as db:assert db.execute('SELECT count(*) FROM absence_history WHERE notice_id=?',(next_notice['id'],)).fetchone()[0]==1


def test_file_binding_integrity_cleanup_and_shared_capacity(client,monkeypatch):
    _,_,parent,teacher=setup(client);payload=body();first=upload(parent,payload).json()
    assert parent.post(url(),json=payload|dict(fileIds=[first['id'],'not-a-file'])).status_code==422
    with api.connect() as db:assert db.execute('SELECT count(*) FROM absence_notices').fetchone()[0]==0
    different=body();assert parent.post(url(),json=different|dict(fileIds=[first['id']])).status_code==422
    notice=send(parent,payload|dict(fileIds=[first['id']]))
    assert parent.post(url(),json=body(startDate='2026-08-18',endDate='2026-08-18',fileIds=[first['id']])).status_code==422
    assert upload(parent,body(),b'not pdf','invalid.pdf').status_code==422
    assert upload(parent,body(),b'x','bad.html').status_code==422
    pending=upload(parent,body()).json()
    with api.connect() as db:
        db.execute('UPDATE absence_files SET created=?',((datetime.now(timezone.utc)-timedelta(days=2)).isoformat(),))
        absence_files.cleanup(db)
        assert db.execute('SELECT id FROM absence_files').fetchall()==[(first['id'],)]
        assert homework_files.total_stored(db)==len(b'Private evidence')
    assert teacher.get(file_url(first['id'])).content==b'Private evidence'
    assert parent.get(file_url(pending['id'])).status_code==404
    monkeypatch.setattr(homework_files,'total_limit',lambda:homework_files.MAX_FILE)
    assert upload(parent,body()).status_code==413


def test_upload_rechecks_relationship_and_cleans_reservation(client,monkeypatch):
    person,_,parent,_=setup(client);original=homework_files.validate_file
    def revoke(path,name):
        digest=original(path,name)
        with api.connect() as db:db.execute('UPDATE accounts SET active=0 WHERE id=?',(person['id'],))
        return digest
    monkeypatch.setattr(homework_files,'validate_file',revoke)
    assert upload(parent,body()).status_code==409
    with api.connect() as db:assert db.execute('SELECT count(*) FROM absence_files').fetchone()[0]==0


def test_backup_restores_evidence_history_and_effective_attendance(client,tmp_path,monkeypatch):
    from tools.backup_school import create_backup
    _,_,parent,teacher=setup(client);mark(client);payload=body();file=upload(parent,payload).json();notice=send(parent,payload|dict(fileIds=[file['id']]))
    approved=decide(teacher,notice).json();original=api.DB;backup=tmp_path/'absence-backup';manifest=create_backup(original,backup)
    assert len(manifest['files'])==1 and '/absences/' in '/'+manifest['files'][0]['path']
    monkeypatch.setattr(api,'DB',backup/original.name)
    assert report(parent).json()['summary']['attendance']['excused']==1
    assert teacher.get(file_url(file['id'])).content==b'Private evidence'
    assert len(parent.get(url('/'+notice['id']+'/history')).json()['items'])==2
    assert decide(teacher,approved,'revoke').status_code==200
    assert report(parent).json()['summary']['attendance']['absent']==1


def test_pagination_limits_candidates_and_hides_private_reasons(client):
    _,_,parent,teacher=setup(client);notice=send(parent)
    with api.connect() as db:
        row=list(db.execute('SELECT * FROM absence_notices').fetchone())
        for i in range(65):
            clone=row.copy();clone[0]=uuid4().hex;clone[9]='withdrawn';clone[13]=str(uuid4());db.execute('INSERT INTO absence_notices VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',clone)
    page=teacher.get(url()).json();assert len(page['items'])==50 and page['nextBefore']
    last=teacher.get(url()+'&before='+str(page['nextBefore'])).json();assert len(last['items'])==16 and last['nextBefore'] is None
    assert not {r['id'] for r in page['items']} & {r['id'] for r in last['items']}
    assert len(teacher.get(url()+'&status=pending').json()['items'])==1
    assert 'Личная причина' not in json.dumps(page,ensure_ascii=False)


def test_rate_limit_and_role_or_school_change_at_commit(client,monkeypatch):
    person,_,parent,teacher=setup(client)
    for i in range(10):send(parent,body(startDate=f'2026-08-{17+i}',endDate=f'2026-08-{17+i}'))
    assert parent.post(url(),json=body(startDate='2026-08-27',endDate='2026-08-27')).status_code==429
    original=api.absences.recheck
    def changed(db,user,snap,workspace):
        db.execute('UPDATE workspaces SET revision=revision+1 WHERE id=?',(workspace,))
        original(db,user,snap,workspace)
    monkeypatch.setattr(api.absences,'recheck',changed)
    with api.connect() as db:before=db.execute('SELECT count(*) FROM absence_notices').fetchone()[0]
    assert parent.post(url(),json=body(startDate='2026-08-28',endDate='2026-08-28')).status_code==409
    with api.connect() as db:assert db.execute('SELECT count(*) FROM absence_notices').fetchone()[0]==before


def test_preview_respects_partial_class_closures_and_cancelled_lessons(client):
    from datetime import date
    from backend.models import Event
    _,_,parent,_=setup(client)
    state=api.read('demo');state.events=[Event(id='closed',name='Class closure',kind='extra',start=date.fromisoformat(DAY),end=date.fromisoformat(DAY),effect='cancel',classIds=['c-demo-a'])]
    api.save('demo',state)
    result=parent.post(url('/preview'),json=body());assert result.status_code==200,result.text
    assert result.json()['lessons']==[]


def test_same_child_other_guardian_sees_only_attendance_result(client):
    _,_,parent,teacher=setup(client);mark(client);notice=send(parent)
    assert decide(teacher,notice).status_code==200
    other=signed('other-parent');result=report(other).json()
    assert result['summary']['attendance']['excused']==1
    assert 'Личная причина' not in json.dumps(result,ensure_ascii=False)
    assert other.get(url('/'+notice['id'])).status_code==404
    assert any(r['action']=='journal.updated' for r in other.get('/api/notifications/inbox').json()['items'])
    assert not any(r['action']=='absence.updated' for r in other.get('/api/notifications/inbox').json()['items'])


def test_decisions_refused_inside_closed_period(client,monkeypatch):
    # Imported here: test_journal_periods imports test_journal_coverage, which imports this module.
    from test_journal_periods import period,close,action
    stamp=datetime(2026,10,14,10,tzinfo=timezone.utc)
    monkeypatch.setattr('backend.journal_periods.now',lambda:stamp);monkeypatch.setattr('backend.journal_coverage.now',lambda:stamp)
    _,_,parent,teacher=setup(client);mark(client)
    approved=decide(teacher,send(parent)).json();pending=send(parent,body(startDate='2026-08-18',endDate='2026-08-18'))
    assert client.get(url('/'+pending['id'])).json()['periodLocks']==[]
    p=close(client,period(client,end='2026-08-18'))
    def counts():
        with api.connect() as db:return [db.execute(f'SELECT count(*) FROM {t}').fetchone()[0] for t in ['absence_history','notifications','audit']]
    before=counts()
    for item,operation in [(approved,'revoke'),(pending,'approve')]:
        refused=decide(teacher,item,operation);assert refused.status_code==403 and 'Первая четверть' in refused.json()['detail']
    assert counts()==before
    for item in [approved,pending]:
        current=client.get(url('/'+item['id'])).json();assert current['version']==item['version'] and current['periodLocks']==['Первая четверть']
    assert parent.get(url('/'+pending['id'])).json()['periodLocks']==[]
    # Reject and withdraw change no figures, so a closed period does not block them.
    assert decide(teacher,pending,'reject').status_code==200
    again=send(parent,body(startDate='2026-08-18',endDate='2026-08-18'));assert decide(parent,again,'withdraw').status_code==200
    action(client,p,'reopen').raise_for_status()
    assert decide(teacher,approved,'revoke').status_code==200


def test_period_lookup_tolerates_database_without_journal_tables():
    import sqlite3
    from backend.journal_periods import range_locks
    assert range_locks(sqlite3.connect(':memory:'),'demo','2026-08-17','2026-08-17')==[]


def test_replayed_decision_still_succeeds_after_period_closes(client,monkeypatch):
    from test_journal_periods import period,close
    stamp=datetime(2026,10,14,10,tzinfo=timezone.utc)
    monkeypatch.setattr('backend.journal_periods.now',lambda:stamp);monkeypatch.setattr('backend.journal_coverage.now',lambda:stamp)
    _,_,parent,teacher=setup(client);mark(client);notice=send(parent)
    decision=dict(requestId=str(uuid4()),version=1,action='approve',comment='Согласовано')
    first=teacher.post(url('/'+notice['id']+'/decision'),json=decision);assert first.status_code==200
    close(client,period(client))
    replay=teacher.post(url('/'+notice['id']+'/decision'),json=decision);assert replay.status_code==200
    assert replay.json()==first.json()|dict(periodLocks=['Первая четверть'])
