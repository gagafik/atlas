"""School-entered notes, bulk notes, single-subject notes, absence types and the family read levels."""
import csv
import io
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from uuid import uuid4
import pytest
from backend import api, absence_bulk
from backend.absences import tables as absence_tables, approvals
from backend.access import now
from test_access import client, enable, create, signed
from test_absences import setup, body, send, decide, url, mark, upload, file_url
from test_journal import DAY, lesson, entry, write, graded, report as journal_report
from test_attendance_reports import report, details, export, pupil

A = 'c-demo-a'
D = 'c-demo-d'


def school(**values):return dict(requestId=str(uuid4()),studentIds=['s1'],startDate=DAY,endDate=DAY,reason='Олимпиада',codeId='school')|values
def preview(account,**values):return account.post(url('/school/preview'),json=school(**values))
def apply(account,id):return account.post(url('/school/'+id+'/apply'))
def prepared(account,**values):
    response=preview(account,**values);assert response.status_code==200,response.text
    assert response.json()['status']=='preview',response.text;return response.json()
def note(account,**values):
    response=apply(account,prepared(account,**values)['id']);assert response.status_code==200,response.text;return response.json()
def lead(teacher_id,class_id=A):
    """Set the class teacher outside any request: the actor is the local administrator."""
    state=api.read('demo')
    state.classes=[c.model_copy(update=dict(classTeacherId=teacher_id)) if c.id==class_id else c for c in state.classes]
    api.save('demo',state)
def staff_setup(client):
    """Parent, class teacher (t2, class A, no registry entries), student account, and a published absent lesson."""
    enable(client);parent=create(client,'parent');teacher=create(client,'teacher');create(client,'student')
    create(client,'teacher',login='other-teacher',grants=[dict(workspace='demo',view='teacher',entityIds=['t1'])])
    lead('t2');return parent,teacher,signed('parent'),signed('teacher'),signed('student')
def counts():
    with api.connect() as db:
        absence_tables(db);absence_bulk.tables(db)
        return {t:db.execute(f'SELECT count(*) FROM {t}').fetchone()[0] for t in ['absence_notices','absence_history','notifications','audit','absence_note_batches']}
def inbox(account):return [n['action'] for n in account.get('/api/notifications/inbox').json()['items']]
def admin_id():
    with api.connect() as db:return db.execute("SELECT id FROM accounts WHERE role='admin'").fetchone()[0]
def put_codes(client,items,**values):
    current=client.get(url('/codes')).json()
    return client.put(url('/codes'),json=dict(requestId=str(uuid4()),version=current['version'],reason='Проверка списка',items=items)|values)
def family(client,account,**values):
    target=f"/api/access/families/{account['id']}/s1?workspace=demo";link=client.get(target).json()
    value={k:link[k] for k in ['version','accountVersion','relationship','verified','evidence','reason','startDate','endDate','status']}
    response=client.put(target,json=value|dict(reason='Проверка школой')|values);assert response.status_code==200,response.text


def test_migration_appends_columns_and_backfills_author(tmp_path):
    db=sqlite3.connect(tmp_path/'old.db')
    db.execute('''CREATE TABLE absence_notices (id TEXT PRIMARY KEY,workspace TEXT,author_id TEXT,student_id TEXT,class_id TEXT,student_name TEXT,created TEXT,updated TEXT,
        version INTEGER,status TEXT,start_at TEXT,end_at TEXT,reason TEXT,request_id TEXT,request_hash TEXT,UNIQUE(workspace,author_id,request_id))''')
    db.execute('''CREATE TABLE absence_history (id INTEGER PRIMARY KEY,notice_id TEXT,version INTEGER,created TEXT,actor_id TEXT,actor_name TEXT,
        action TEXT,comment TEXT,request_id TEXT,request_hash TEXT,UNIQUE(notice_id,actor_id,request_id))''')
    db.execute("INSERT INTO absence_notices VALUES ('n1','demo','p1','s1','c-demo-a','Ученик 1','2026-08-01','2026-08-01',1,'pending','2026-08-17T00:00','2026-08-17T23:59','Причина','r1','h')")
    db.execute("INSERT INTO absence_history(notice_id,version,created,actor_id,actor_name,action,comment,request_id,request_hash) VALUES ('n1',1,'2026-08-01','p1','Мама','submitted','','r1','h')")
    db.commit();absence_tables(db)
    # Endpoints start BEGIN IMMEDIATE right after tables(): the backfill must not leave a transaction open.
    assert not db.in_transaction
    columns=lambda:[r[1] for r in db.execute('PRAGMA table_info(absence_notices)')]
    assert columns()[-4:]==['origin','author_name','code_id','subject_id'] and len(columns())==19
    assert db.execute('SELECT origin,author_name,code_id,subject_id FROM absence_notices').fetchone()==('parent','Мама',None,None)
    # Running it again adds no column and repeats no backfill.
    db.execute("UPDATE absence_notices SET author_name='Изменено'");before=columns()
    absence_tables(db)
    assert columns()==before and db.execute('SELECT author_name FROM absence_notices').fetchone()==('Изменено',)


def test_approvals_tolerate_a_database_not_migrated_yet(tmp_path):
    db=sqlite3.connect(tmp_path/'old.db')
    db.execute('''CREATE TABLE absence_notices (id TEXT PRIMARY KEY,workspace TEXT,author_id TEXT,student_id TEXT,class_id TEXT,student_name TEXT,created TEXT,updated TEXT,
        version INTEGER,status TEXT,start_at TEXT,end_at TEXT,reason TEXT,request_id TEXT,request_hash TEXT,UNIQUE(workspace,author_id,request_id))''')
    db.execute("INSERT INTO absence_notices VALUES ('n1','demo','p1','s1','c-demo-a','Ученик 1','2026-08-01','2026-08-01',2,'approved','2026-08-17T00:00','2026-08-17T23:59','Причина','r1','h')")
    lesson_row=dict(date=DAY,start='08:30',end='09:10',status='scheduled',subjectId='sub2',students=[dict(id='s1')])
    assert approvals(db,'demo',[lesson_row])=={0:{'s1':dict(id='n1',version=2)}}
    # The read-only snapshot must not have been changed.
    assert 'subject_id' not in [r[1] for r in db.execute('PRAGMA table_info(absence_notices)')]


def test_codes_admin_versioned_history_and_validation(client):
    setup(client);create(client,'staff',login='staff')
    first=client.get(url('/codes'));assert first.status_code==200 and first.json()['version']==0 and len(first.json()['items'])==4
    items=first.json()['items']
    for login in ['teacher','parent','staff','student']:
        account=signed(login);assert account.get(url('/codes')).status_code==200
        assert put_codes(account,items).status_code==403 and account.get(url('/codes/history')).status_code==403
    renamed=[dict(c,name='Болезнь (справка)') if c['id']=='illness' else dict(c,active=False) if c['id']=='other' else c for c in items]+[dict(id='c-trip',name='Поездка',parents=True,active=True)]
    request=str(uuid4());saved=put_codes(client,renamed,requestId=request)
    assert saved.status_code==200 and saved.json()['version']==1,saved.text
    history=client.get(url('/codes/history')).json()['items']
    assert len(history)==1 and history[0]['reason']=='Проверка списка' and history[0]['before']==items and history[0]['after']==renamed
    # The same request replays; a different list under the same request id is refused; neither writes history.
    assert client.put(url('/codes'),json=dict(requestId=request,version=0,reason='Проверка списка',items=renamed)).json()['version']==1
    assert put_codes(client,items,requestId=request,version=0).status_code==409
    assert len(client.get(url('/codes/history')).json()['items'])==1
    assert put_codes(client,renamed,version=0).status_code==409  # stale version
    assert put_codes(client,renamed).json()['version']==1  # identical list writes nothing
    assert len(client.get(url('/codes/history')).json()['items'])==1
    assert put_codes(client,[c for c in renamed if c['id']!='family']).status_code==422
    assert put_codes(client,renamed+[dict(id='c-copy',name='болезнь (СПРАВКА)')]).status_code==422
    assert put_codes(client,[dict(c,active=False) for c in renamed]).status_code==422
    assert put_codes(client,renamed,reason='no').status_code==422
    assert put_codes(client,[dict(id='Bad Id',name='x')]+renamed[1:]).status_code==422
    assert client.get(url('/options')).json()['codesVersion']==1


def test_parent_chooses_only_active_codes_for_parents(client):
    _,_,parent,teacher=setup(client)
    items=client.get(url('/codes')).json()['items']
    assert put_codes(client,[dict(c,active=False) if c['id']=='other' else c for c in items]).status_code==200
    options=parent.get(url('/options')).json();assert {c['id'] for c in options['codes']}>={'illness','school'}
    for code in ['school','other','nope']:assert parent.post(url(),json=body(codeId=code)).status_code==422
    with api.connect() as db:assert db.execute('SELECT count(*) FROM absence_notices').fetchone()[0]==0
    notice=send(parent,body(codeId='illness'));assert notice['codeId']=='illness' and notice['origin']=='parent'
    assert decide(teacher,notice,'approve').status_code==200


def both(client):
    """English (mark) and Mathematics lessons of DAY published with every pupil absent."""
    english=mark(client)
    math=graded(entry(client,lesson(client,'Математика')['lessonKey']))
    for p in math['document']['content']['records']:p['attendance']='absent'
    assert write(client,math,publish=True).status_code==200
    return english,math


def test_single_subject_note_excuses_only_that_subject(client):
    _,_,parent,teacher=setup(client);english,math=both(client)
    notice=send(parent,body(subjectId='sub2'));assert notice['subjectId']=='sub2' and decide(teacher,notice).status_code==200
    rows={r['lesson']['subject']:r['record']['attendance'] for r in journal_report(parent).json()['rows']}
    assert rows=={'Английский язык':'excused','Математика':'absent'}
    assert 's1' in entry(client,english['lessonKey'])['attendanceApprovals']
    assert entry(client,math['lessonKey'])['attendanceApprovals']=={}
    value=report(teacher,studentId='s1').json();by_subject={s['name']:s['attendance'] for s in value['subjects']}
    assert by_subject['Английский язык']['excused']==1 and by_subject['Математика']['absent']==1 and by_subject['Математика']['excused']==0
    assert pupil(value)['attendance']['excused']==1 and pupil(value)['attendance']['absent']==1
    lessons={r['subject']:r for r in details(teacher).json()['items']}
    assert lessons['Английский язык']['excusedByNotice'] and not lessons['Математика']['excusedByNotice']
    # Different subjects may overlap in time; a note for every subject may not overlap a subject note.
    assert parent.post(url(),json=body(subjectId='sub0')).status_code==200
    assert parent.post(url(),json=body(subjectId='sub0')).status_code==409
    assert parent.post(url(),json=body()).status_code==409


def test_subject_preview_and_lesson_range_note(client):
    _,_,parent,teacher=setup(client);english,math=both(client)
    every=parent.post(url('/preview'),json=body()).json()['lessons'];assert {r['subject'] for r in every}>={'Английский язык','Математика'}
    only=parent.post(url('/preview'),json=body(subjectId='sub2')).json()['lessons']
    assert only and {r['subject'] for r in only}=={'Английский язык'} and all(r['subjectId']=='sub2' for r in only)
    assert parent.post(url('/preview'),json=body(subjectId='nope')).status_code==422
    assert parent.post(url(),json=body(subjectId='nope')).status_code==422
    first=next(r for r in every if r['subject']=='Английский язык')
    assert decide(teacher,send(parent,body(startTime=first['start'],endTime=first['end']))).status_code==200
    rows={r['lesson']['subject']:r['record']['attendance'] for r in journal_report(parent).json()['rows']}
    assert rows=={'Английский язык':'excused','Математика':'absent'}


def test_class_teacher_enters_note_author_recorded_family_notified(client):
    _,_,parent,teacher,student=staff_setup(client);mark(client)
    prepared_note=prepared(teacher,codeId='illness')
    assert prepared_note['items'][0]['lessonCount']>=1 and prepared_note['items'][0]['problem'] is None
    result=apply(teacher,prepared_note['id']).json();item=result['items'][0];assert item['status']=='approved' and result['count']==1
    saved=client.get(url('/'+item['id'])).json()
    assert saved['origin']=='school' and saved['authorName']=='Сотрудник' and saved['status']=='approved' and saved['codeId']=='illness' and saved['version']==1
    history=client.get(url('/'+item['id']+'/history')).json()['items']
    assert [(h['action'],h['actor'],h['batchId']) for h in history]==[('recorded','Сотрудник',prepared_note['id'])]
    assert report(teacher).json()['totals']['attendance']['excused']==1
    assert journal_report(parent).json()['summary']['attendance']['excused']==1
    assert 'absence.recorded' in inbox(parent) and 'journal.updated' in inbox(parent) and 'absence.recorded' in inbox(student)
    assert 'absence.recorded' not in inbox(teacher) and inbox(client).count('absence.recorded')==1
    # The family can read the summary; the parent cannot withdraw a note of the school.
    shown=parent.get(url('/'+item['id']));assert shown.status_code==200 and shown.json()['summary'] is True and 'reason' not in shown.json()
    assert decide(parent,saved,'withdraw').status_code==403
    assert parent.get(url('/'+item['id']+'/history')).status_code==404
    revoked=decide(teacher,saved,'revoke');assert revoked.status_code==200 and revoked.json()['status']=='revoked'
    assert report(teacher).json()['totals']['attendance']['absent']==2
    with api.connect() as db:
        assert db.execute("SELECT count(*) FROM audit WHERE action='absence.recorded'").fetchone()[0]==1
        assert 'Олимпиада' not in json.dumps(db.execute('SELECT detail FROM audit').fetchall(),ensure_ascii=False)


def test_school_note_access_only_for_own_classes(client):
    from test_class_staff import assign, item as duty, A as class_a, D as class_d
    parent,teacher,parent_client,teacher_client,student=staff_setup(client)
    staff=create(client,'staff',login='staff')
    before=counts()
    assert preview(teacher_client,studentIds=['s3']).status_code==404
    assert preview(teacher_client,studentIds=['s1','s3']).status_code==404 and counts()==before
    assert preview(teacher_client,studentIds=['nobody']).status_code==404
    for account in [parent_client,student]:assert preview(account).status_code==403
    assert preview(signed('other-teacher')).status_code==404
    assign(client,{class_d:[duty(staff,'absence_review')]})
    assert prepared(signed('staff'),studentIds=['s3'])['items'][0]['studentId']=='s3'
    assert preview(signed('staff'),studentIds=['s1']).status_code==404
    # An explicit revocation takes the duty from the class teacher.
    assign(client,{class_d:[duty(staff,'absence_review')],class_a:[dict(accountId=teacher['id'],capabilities=[],revoked=['absence_review'])]})
    assert preview(teacher_client).status_code==404
    assert prepared(client,studentIds=['s1','s3'])['status']=='preview'
    options=signed('staff').get(url('/options')).json();assert [p['id'] for p in options['schoolPupils']]==['s3','s4']


def test_school_note_refused_in_closed_period(client,monkeypatch):
    from test_journal_periods import period, close, action
    stamp=datetime(2026,10,14,10,tzinfo=timezone.utc)
    monkeypatch.setattr('backend.journal_periods.now',lambda:stamp);monkeypatch.setattr('backend.journal_coverage.now',lambda:stamp)
    _,_,_,teacher,_=staff_setup(client);mark(client)
    p=close(client,period(client));before=counts()
    refused=preview(teacher);assert refused.status_code==403 and 'Первая четверть' in refused.json()['detail']
    assert counts()==before
    # A preview prepared while the period was open is refused when applied after it closed again.
    reopened=action(client,p,'reopen');reopened.raise_for_status();ready=prepared(teacher)
    close(client,reopened.json());before=counts()
    assert apply(teacher,ready['id']).status_code==403 and counts()==before


def test_school_note_ignores_the_class_electronic_notes_switch(client):
    _,_,parent,teacher,_=staff_setup(client)
    off=client.put(url('/classes/c-demo-a/electronic'),json=dict(requestId=str(uuid4()),version=0,enabled=False,reason=''));assert off.status_code==200
    assert parent.post(url(),json=body()).status_code==403
    assert note(teacher)['count']==1


def test_bulk_preview_per_pupil_then_all_or_nothing_apply(client):
    _,_,parent,_=setup(client)
    create(client,'parent',login='parent2',grants=[dict(workspace='demo',view='student',entityIds=['s2'])])
    send(signed('parent2'),body(studentId='s2'));before=counts()
    blocked=preview(client,studentIds=['s1','s2','s3'])
    assert blocked.status_code==200 and blocked.json()['status']=='blocked' and 'id' not in blocked.json()
    by_id={i['studentId']:i for i in blocked.json()['items']}
    assert 'на рассмотрении' in by_id['s2']['problem'] and by_id['s1']['problem'] is None and by_id['s3']['problem'] is None
    assert by_id['s1']['lessonCount']>=1 and by_id['s3']['lessonCount']>=1 and by_id['s3']['className']=='10D'
    after=counts();assert after==before and after['absence_note_batches']==0
    ready=prepared(client,studentIds=['s1','s3']);assert [i['studentId'] for i in ready['items']]==['s1','s3']
    send(parent)
    refused=apply(client,ready['id']);assert refused.status_code==409 and 'Ученик 1' in refused.json()['detail']
    assert counts()['absence_notices']==before['absence_notices']+1
    again=preview(client,studentIds=['s1','s3']);assert again.json()['status']=='blocked'
    assert {i['studentId']:i['problem'] for i in again.json()['items']}['s3'] is None


def test_bulk_notes_replay_and_receipt(client):
    setup(client);request=school(studentIds=['s1','s3'],codeId='school')
    ready=client.post(url('/school/preview'),json=request).json()
    assert client.post(url('/school/preview'),json=request).json()==ready
    result=apply(client,ready['id']).json()
    assert result['count']==2 and all(i['status']=='approved' for i in result['items'])
    for i in result['items']:
        h=client.get(url('/'+i['id']+'/history')).json()['items'];assert len(h)==1 and h[0]['batchId']==ready['id'] and h[0]['action']=='recorded'
    done=counts()
    assert apply(client,ready['id']).json()==result
    assert client.post(url('/school/preview'),json=request).json()==result
    assert counts()==done
    assert client.post(url('/school/preview'),json=request|dict(reason='Другой текст')).status_code==409
    assert apply(signed('other-teacher'),ready['id']).status_code==404
    assert apply(client,'missing').status_code==404
    assert client.post('/api/journal/absences/school/'+ready['id']+'/apply?workspace=school').status_code==404


def test_bulk_notes_bounds_expiry_and_rate_limit(client,monkeypatch):
    setup(client)
    for values in [dict(studentIds=[f's{i}' for i in range(51)]),dict(studentIds=['s1','s1']),dict(studentIds=[]),dict(codeId=''),dict(codeId='nope'),dict(startDate='2026-08-10'),
                   dict(endDate='2026-09-30'),dict(subjectId='nope'),dict(reason=' ')]:
        assert preview(client,**values).status_code==422,values
    assert counts()['absence_note_batches']==0
    ready=prepared(client,studentIds=['s1','s3'])
    clock=absence_bulk.now;monkeypatch.setattr(absence_bulk,'now',lambda:clock()+timedelta(minutes=16))
    expired=apply(client,ready['id']);assert expired.status_code==409 and 'истёк' in expired.json()['detail']
    monkeypatch.setattr(absence_bulk,'now',clock)
    with api.connect() as db:
        for _ in range(29):
            db.execute("INSERT INTO absence_note_batches VALUES (?,?,?,?,?,?,?,?,?,?)",(uuid4().hex,'demo',admin_id(),str(uuid4()),'x',now().isoformat(),now().isoformat(),'{}','preview',None))
    assert preview(client,studentIds=['s2']).status_code==429
    # Rate limiting new previews never blocks applying one that was already checked.
    assert apply(client,ready['id']).status_code==200


def test_bulk_notes_notification_failure_rolls_back(client,monkeypatch):
    setup(client);ready=prepared(client,studentIds=['s1','s3']);before=counts()
    original=api.notifications.deliver
    def fail(*args,**kwargs):raise RuntimeError('notification failed')
    monkeypatch.setattr(api.notifications,'deliver',fail)
    with pytest.raises(RuntimeError):apply(client,ready['id'])
    assert counts()==before
    with api.connect() as db:assert db.execute('SELECT status FROM absence_note_batches').fetchone()[0]=='preview'
    monkeypatch.setattr(api.notifications,'deliver',original)
    assert apply(client,ready['id']).json()['count']==2


def test_changes_between_preview_and_apply_are_rechecked(client):
    _,_,parent,_=setup(client)
    ready=prepared(client,studentIds=['s1','s3']);before=counts()
    # Code switched off, pupil left the school: every change is refused and nothing is written.
    items=client.get(url('/codes')).json()['items']
    assert put_codes(client,[dict(c,active=False) if c['id']=='school' else c for c in items]).status_code==200
    stale=apply(client,ready['id']);assert stale.status_code==409
    assert counts()['absence_notices']==before['absence_notices']
    again=prepared(client,studentIds=['s1','s3'],codeId='illness')
    state=api.read('demo');state.students=[s.model_copy(update=dict(active=False)) if s.id=='s3' else s for s in state.students];api.save('demo',state)
    gone=apply(client,again['id']);assert gone.status_code==409 and 'Ученик 3' in gone.json()['detail'] and counts()['absence_notices']==before['absence_notices']
    blocked=preview(client,studentIds=['s3'],codeId='illness').json();assert blocked['status']=='blocked' and 'выбыл' in blocked['items'][0]['problem']
    assert prepared(client,studentIds=['s1'],codeId='illness')['items'][0]['studentId']=='s1'


def test_verified_legal_guardian_sees_full_note_and_is_notified(client):
    _,_,parent,teacher=setup(client);second=create(client,'parent',login='second-parent');other=signed('second-parent')
    payload=body();file=upload(parent,payload).json();notice=send(parent,payload|dict(fileIds=[file['id']]));path=url('/'+notice['id'])
    assert other.get(path).status_code==404 and other.get(url()).json()['items']==[]
    family(client,second,relationship='father',verified=True,evidence='Свидетельство о рождении')
    listed=other.get(url()).json()['items'];assert [i['id'] for i in listed]==[notice['id']] and listed[0]['mine'] is False and 'reason' not in listed[0]
    seen=other.get(path).json();assert seen['reason']==payload['reason'] and seen['files'][0]['id']==file['id'] and seen['canWithdraw'] is False
    assert other.get(file_url(file['id'])).content==b'Private evidence' and other.get(url('/'+notice['id']+'/history')).status_code==200
    assert decide(other,notice,'withdraw').status_code==403
    assert decide(teacher,notice).status_code==200
    assert 'absence.updated' in inbox(other)
    family(client,second,relationship='representative');assert other.get(path).status_code==404 and other.get(file_url(file['id'])).status_code==404
    family(client,second,relationship='father');assert other.get(path).status_code==200
    family(client,second,status='revoked');assert other.get(path).status_code==404 and other.get(url()).json()['items']==[]
    # Unverified links keep seeing only the attendance result (test_same_child_other_guardian_sees_only_attendance_result).


def test_pupil_sees_summary_only(client):
    _,_,parent,teacher=setup(client);pupil_client=signed('student')
    def panel():return pupil_client.get('/api/journal/options?workspace=demo').json()['panels']['absences']
    assert panel() is False and pupil_client.get(url()).json()['items']==[]
    payload=body();file=upload(parent,payload).json();notice=send(parent,payload|dict(fileIds=[file['id']]))
    assert panel() is True
    listed=pupil_client.get(url()).json()['items'];assert [i['id'] for i in listed]==[notice['id']] and 'reason' not in listed[0] and listed[0]['mine'] is False
    seen=pupil_client.get(url('/'+notice['id']))
    assert seen.status_code==200 and seen.json()['summary'] is True and seen.json()['files']==[] and not {'reason','authorName'}&set(seen.json())
    assert 'Личная причина' not in json.dumps(listed,ensure_ascii=False)+seen.text
    assert pupil_client.get(url('/'+notice['id']+'/history')).status_code==404 and pupil_client.get(file_url(file['id'])).status_code==404
    assert decide(pupil_client,notice,'withdraw').status_code in [403,404] and decide(pupil_client,notice,'approve').status_code in [403,404]
    assert pupil_client.post(url('/preview'),json=body()).status_code==403 and pupil_client.post(url(),json=body()).status_code==403
    assert pupil_client.post(url('/school/preview'),json=school()).status_code==403
    assert pupil_client.get(url('/options')).json()['pupil'] is True and decide(teacher,notice).status_code==200


def test_other_guardian_sees_school_notes_at_summary_level(client):
    _,_,parent,teacher=setup(client);other=signed('other-parent')
    result=note(client,studentIds=['s1'],codeId='school')
    item=other.get(url()).json()['items'];assert [i['id'] for i in item]==[result['items'][0]['id']] and 'reason' not in item[0]
    shown=other.get(url('/'+result['items'][0]['id'])).json();assert shown['summary'] is True and 'reason' not in shown
    assert 'absence.recorded' in inbox(other) and 'absence.recorded' in inbox(signed('student'))
    assert signed('other-teacher').get(url()).json()['items']==[]


def test_reviewer_queue_filters_by_class_and_date(client):
    _,_,parent,teacher=setup(client)
    note(client,studentIds=['s1'],startDate=DAY,endDate=DAY)
    note(client,studentIds=['s3'],startDate='2026-08-18',endDate='2026-08-18')
    send(parent,body(startDate='2026-08-18',endDate='2026-08-18'))
    def ids(account,query):
        r=account.get(url()+query);assert r.status_code==200,r.text;return sorted(i['studentName'] for i in r.json()['items'])
    assert ids(client,'&classId=c-demo-a')==['Ученик 1 (пример)']*2
    assert ids(client,'&classId=c-demo-d&start=2026-08-18&end=2026-08-18')==['Ученик 3 (пример)']
    assert len(ids(client,'&start=2026-08-18'))==2 and len(ids(client,'&end=2026-08-17'))==1 and len(ids(client,''))==3
    assert client.get(url()+'&start=2026-08-19&end=2026-08-18').status_code==422
    assert client.get(url()+'&classId=nope').status_code==404
    assert teacher.get(url()+'&classId=c-demo-d').status_code==404
    assert ids(teacher,'&classId=c-demo-a')==['Ученик 1 (пример)']*2
    assert ids(parent,'&classId=c-demo-d')==ids(parent,'')==['Ученик 1 (пример)']*2
    page=client.get(url()+'&classId=c-demo-a').json();assert page['nextBefore'] is None


def test_codes_in_attendance_reports(client):
    _,_,parent,teacher=setup(client);mark(client)
    sick=send(parent,body(codeId='illness'));assert decide(teacher,sick).status_code==200
    note(client,studentIds=['s2'],codeId='school')
    value=report(teacher).json();names={c['id']:c['name'] for c in value['codes']}
    assert names['illness']=='Болезнь' and value['totals']['excusedCodes']=={'illness':1,'school':1} and value['totals']['attendance']['excused']==2
    assert pupil(value)['excusedCodes']=={'illness':1} and pupil(value,'s2')['excusedCodes']=={'school':1}
    assert value['classes'][0]['excusedCodes']=={'illness':1,'school':1} and value['subjects'][0]['excusedCodes']=={'illness':1,'school':1}
    assert details(teacher).json()['items'][0]['absenceCode']=='illness'
    rows=list(csv.reader(io.StringIO(export(teacher,token=value['token']).content.decode('utf-8-sig')),delimiter=';'))
    column=rows[0].index('Уважительная причина: Болезнь');assert rows[0][-1]=='Уважительная причина: без типа'
    assert {r[rows[0].index('ID ученика')]:r[column] for r in rows[1:]}=={'s1':'1','s2':'0'}
    lessons=export(teacher,studentId='s1',view='lessons').content.decode('utf-8-sig');assert 'Болезнь' in lessons and lessons.splitlines()[0].endswith('Тип отсутствия')
    renamed=[dict(c,name='Болезнь (справка)') if c['id']=='illness' else c for c in value['codes']]
    assert put_codes(client,renamed).status_code==200
    again=report(teacher,token=value['token']);assert again.status_code==200
    assert {c['id']:c['name'] for c in again.json()['codes']}['illness']=='Болезнь (справка)'
    assert 'Болезнь (справка)' in export(teacher,studentId='s1',view='lessons').content.decode('utf-8-sig')
    assert 'Личная причина' not in json.dumps(again.json(),ensure_ascii=False)


def test_notice_without_a_type_is_counted_as_no_type(client):
    _,_,parent,teacher=setup(client);mark(client)
    assert decide(teacher,send(parent)).status_code==200
    value=report(teacher).json();assert value['totals']['excusedCodes']=={'':1}
    rows=list(csv.reader(io.StringIO(export(teacher).content.decode('utf-8-sig')),delimiter=';'))
    column=rows[0].index('Уважительная причина: без типа');assert {r[rows[0].index('ID ученика')]:r[column] for r in rows[1:]}=={'s1':'1','s2':'0'}
    assert 'Без типа' in export(teacher,studentId='s1',view='lessons').content.decode('utf-8-sig')


def test_overlapping_notes_count_a_lesson_once(client):
    # A pending note and an accepted note may overlap only by direct database edits; the totals must still count each lesson once.
    _,_,parent,teacher=setup(client);mark(client);notice=send(parent,body(codeId='illness'))
    assert decide(teacher,notice).status_code==200
    with api.connect() as db:
        row=list(db.execute('SELECT * FROM absence_notices').fetchone());row[0]=uuid4().hex;row[13]=str(uuid4());row[17]='family'
        db.execute('INSERT INTO absence_notices VALUES ('+','.join('?'*len(row))+')',row)
    value=report(teacher).json();assert pupil(value)['attendance']['excused']==1 and pupil(value)['attendance']['absent']==0
    assert sum(pupil(value)['excusedCodes'].values())==1
