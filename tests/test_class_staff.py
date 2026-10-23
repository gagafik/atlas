"""Class duties are independent permissions, never a shortcut into journal grades."""
import json
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from backend import api, class_staff
from test_access import client, enable, create, signed
from test_absences import setup, mark, send, decide, body, upload, file_url, url
from test_journal import DAY, lesson
from test_attendance_reports import report, details, export

BASE='/api/access/class-staff?workspace=demo'
CAPS=class_staff.CAPABILITIES
A='c-demo-a';D='c-demo-d'


def item(account,*caps):return dict(accountId=account['id'],capabilities=list(caps))
def settings(client):
    response=client.get(BASE);response.raise_for_status();return response.json()
def payload(client,assignments,**values):
    current=settings(client)
    return dict(version=current['version'],revision=current['revision'],reason='Проверенное назначение школы',assignments=assignments)|values
def assign(client,assignments):
    response=client.put(BASE,json=payload(client,assignments));assert response.status_code==200,response.text;return response.json()
def coverage(client,**values):return client.post('/api/journal/coverage/report?workspace=demo',json=dict(start=DAY,end=DAY,mode='classes')|values)
def own(client):return client.get('/api/journal/class-staff/options').json()['workspaces']


def test_legacy_assignment_keeps_real_rights_until_explicit_split(client):
    _,teacher,parent,reader=setup(client);mark(client);notice=send(parent)
    current=settings(client)
    assert current['legacy'] and current['version']==1
    assert current['assignments']=={A:[item(teacher,*class_staff.LEGACY)]}
    assert report(reader).status_code==200 and coverage(reader).status_code==200
    assign(client,{A:[item(teacher,'attendance_read')]})
    assert report(reader).status_code==200
    assert coverage(reader).status_code==403
    assert reader.get(url('/'+notice['id'])).status_code==404
    # Old UI/API cannot silently restore the formerly bundled rights.
    assert client.put(url('/reviewers'),json=dict(version=2,assignments={A:[teacher['id']]})).status_code in [404,405]
    assert settings(client)['assignments']=={A:[item(teacher,'attendance_read')]}
    assert reader.get('/api/journal/coverage/options?workspace=demo').json()['modes']==['teaching']


@pytest.mark.parametrize('cap',CAPS)
def test_each_staff_capability_is_independent_and_scoped(client,cap):
    _,_,parent,_=setup(client);staff=create(client,'staff');worker=signed('staff');mark(client)
    private=body();file=upload(parent,private).json();notice=send(parent,private|dict(fileIds=[file['id']]))
    school=api.read('demo');assign(client,{A:[item(staff,cap)]})
    assert own(worker)[0]['classes']==[dict(id=A,name='10A',capabilities=[cap])]
    assert worker.get(url('/'+notice['id'])).status_code==(200 if cap=='absence_review' else 404)
    assert worker.get(file_url(file['id'])).status_code==(200 if cap=='absence_review' else 404)
    assert report(worker).status_code==(200 if cap=='attendance_read' else 403)
    assert coverage(worker).status_code==(200 if cap=='journal_coverage' else 403)
    assert worker.get('/api/journal/absences/options?workspace=demo').json()['canReview']==(cap=='absence_review')
    if cap=='absence_review':
        assert decide(worker,notice).status_code==200
        assert report(worker).status_code==403
    if cap=='attendance_read':
        assert details(worker,studentId='s3').status_code==404
        assert report(worker,classId=D).status_code==404 and export(worker,classId=D).status_code==404
        assert 'Личная причина' not in json.dumps(details(worker).json(),ensure_ascii=False)
        assert report(worker,historicalOnly=True).status_code==403
    if cap=='journal_coverage':
        value=coverage(worker,status='all').json()
        assert value['items'] and all(not r['canOpen'] for r in value['items'])
        assert coverage(worker,classId=D).status_code==404 and coverage(worker,mode='teaching').status_code==403
    assert api.read('demo')==school


def test_staff_never_inherits_teacher_grades_editor_or_family_access(client):
    _,_,parent,_=setup(client);staff=create(client,'staff');worker=signed('staff');record=mark(client)
    assign(client,{A:[item(staff,*CAPS)]})
    assert worker.get('/api/state?workspace=demo').status_code==403
    assert worker.get('/api/access/users').status_code==403
    assert worker.get('/api/portal/options').json()==[]
    assert worker.get('/api/portal/schedule?workspace=demo&view=class&entityId='+A).status_code==403
    for path in ['/api/journal/options','/api/journal/day?day='+DAY,'/api/journal/report?studentId=s1&start='+DAY+'&end='+DAY,
                 '/api/journal/entry?lessonKey='+record['lessonKey']+'&day='+DAY,
                 '/api/journal/homework/options','/api/journal/curriculum/options','/api/journal/library/options',
                 '/api/final-grades/options','/api/intake/options','/api/messages/options']:
        r=worker.get(path+('&' if '?' in path else '?')+'workspace=demo')
        assert r.status_code in [403,404],(path,r.status_code,r.text)
    assert worker.post(url(),json=body()).status_code==403
    assert worker.get(url().replace('demo','school')).status_code==403
    assert worker.put(BASE,json=payload(client,{})).status_code==403
    assert signed('parent').get('/api/journal/class-staff/options').status_code==403
    assert create(client,'staff',login='unassigned')['grants']==[]
    assert own(signed('unassigned'))==[]


def test_different_capabilities_on_different_classes_do_not_merge(client):
    _,_,parent,_=setup(client);staff=create(client,'staff');worker=signed('staff');mark(client)
    notice=send(parent)
    assign(client,{A:[item(staff,'attendance_read')],D:[item(staff,'absence_review','journal_coverage')]})
    assert worker.get(url('/'+notice['id'])).status_code==404
    assert worker.get(url()).json()['items']==[]
    assert report(worker).status_code==200 and report(worker,classId=D).status_code==404
    assert coverage(worker).status_code==200 and coverage(worker,classId=A).status_code==404
    # The active class of the child controls access, not the class at submission.
    state=api.read('demo');next(s for s in state.students if s.id=='s1').classId=D;api.save('demo',state)
    assert worker.get(url('/'+notice['id'])).status_code==200
    assert report(worker,studentId='s1').status_code==404


def test_revocation_invalidates_cached_reports_exports_and_pending_bulk(client):
    _,_,parent,_=setup(client);staff=create(client,'staff');worker=signed('staff');mark(client);notice=send(parent)
    assign(client,{A:[item(staff,*CAPS)]})
    before=report(worker).json();check=coverage(worker).json()
    preview=worker.post(url('/bulk/preview'),json=dict(requestId=str(uuid4()),items=[dict(id=notice['id'],version=notice['version'])],action='approve',comment='Проверено'))
    assert preview.status_code==200,preview.text
    assign(client,{})
    assert own(worker)==[]
    assert report(worker).status_code==403 and export(worker).status_code==403
    assert report(worker,token=before['token']).status_code==409
    assert coverage(worker,token=check['token']).status_code==409
    apply=worker.post(url('/bulk/'+preview.json()['id']+'/apply'))
    assert apply.status_code==404,apply.text  # missing scope does not reveal the notice
    assert parent.get(url('/'+notice['id'])).json()['status']=='pending'


def test_revocation_after_report_calculation_prevents_sending_result(client,monkeypatch):
    setup(client);staff=create(client,'staff');worker=signed('staff');mark(client)
    assign(client,{A:[item(staff,'attendance_read')]})
    original=api.attendance_reports.summary
    def race(*args,**kwargs):
        value=original(*args,**kwargs);assign(client,{});return value
    monkeypatch.setattr(api.attendance_reports,'summary',race)
    r=report(worker);assert r.status_code==409 and 'students' not in r.json()


def test_stale_versions_school_changes_and_invalid_assignments_are_atomic(client):
    enable(client);staff=create(client,'staff');teacher=create(client,'teacher')
    stale=payload(client,{A:[item(staff,'attendance_read')]})
    assign(client,{A:[item(teacher,'journal_coverage')]})
    assert client.put(BASE,json=stale).status_code==409
    stale=payload(client,{})
    state=api.read('demo');state.classes[0].name+=' updated';api.save('demo',state)
    assert client.put(BASE,json=stale).status_code==409
    invalid=[{A:[item(staff,'absence_review','absence_review')]},{A:[item(staff,'everything')]},{A:[item(staff)]},
             {A:[item(staff,'attendance_read'),item(staff,'absence_review')]},{'missing':[item(staff,'attendance_read')]},
             {A:[dict(accountId='missing',capabilities=['attendance_read'])]}]
    for value in invalid:assert client.put(BASE,json=payload(client,value)).status_code==422
    assert settings(client)['assignments']=={A:[item(teacher,'journal_coverage')]}
    with api.connect() as db:assert db.execute('SELECT count(*) FROM class_staff_history').fetchone()[0]==1


def test_current_account_and_existing_teacher_record_are_required(client):
    _,teacher,_,reader=setup(client);assign(client,{A:[item(teacher,*CAPS)]})
    state=api.read('demo');state.teachers=[t for t in state.teachers if t.id!='t2']
    for workload in state.workloads:
        if workload.teacherId=='t2':workload.teacherId='t1'
        workload.additionalTeacherIds=[id for id in workload.additionalTeacherIds if id!='t2']
    api.save('demo',state)
    assert own(reader)==[] and report(reader).status_code==403
    assert client.put(BASE,json=payload(client,{A:[item(teacher,*CAPS)]})).status_code==422
    staff=create(client,'staff');assign(client,{A:[item(staff,*CAPS)]});worker=signed('staff')
    response=client.put('/api/access/users/'+staff['id'],json={k:v for k,v in staff.items() if k!='id'}|dict(workspaces=['school']))
    assert response.status_code==200,response.text
    assert worker.get('/api/journal/class-staff/options').status_code==401
    assert own(signed('staff'))==[]


def test_concurrent_writers_history_and_backup_restore(client,tmp_path,monkeypatch):
    from tools.backup_school import create_backup
    enable(client);staff=create(client,'staff');p=payload(client,{A:[item(staff,'attendance_read')]})
    with ThreadPoolExecutor(max_workers=2) as pool: results=list(pool.map(lambda _:client.put(BASE,json=p),range(2)))
    assert sorted(r.status_code for r in results)==[200,409]
    for i in range(20):assign(client,{A:[item(staff,'attendance_read' if i%2 else 'journal_coverage')]})
    path='/api/access/class-staff/history?workspace=demo';first=client.get(path).json();second=client.get(path+'&before='+str(first['nextBefore'])).json()
    assert len(first['items'])==20 and len(second['items'])==1
    assert first['items'][0]['actor']=='Администратор' and first['items'][0]['reason']=='Проверенное назначение школы'
    assert signed('staff').get(path).status_code==403
    backup=tmp_path/'backup';source=api.DB;create_backup(source,backup);monkeypatch.setattr(api,'DB',backup/source.name)
    assert settings(client)['version']==21
    assert own(signed('staff'))[0]['classes'][0]['capabilities']==['attendance_read']


def test_admin_rechecked_in_transaction_and_csrf_required(client,monkeypatch):
    admin=enable(client);staff=create(client,'staff');p=payload(client,{A:[item(staff,'attendance_read')]})
    bare=TestClient(api.app,cookies=client.cookies)
    assert bare.put(BASE,json=p).status_code==403
    original=api.absences.context
    def race(request,workspace):
        result=original(request,workspace)
        with api.connect() as db:db.execute('UPDATE accounts SET active=0,version=version+1 WHERE id=?',(admin['id'],))
        return result
    monkeypatch.setattr(api.absences,'context',race)
    assert client.put(BASE,json=p).status_code==409
    with api.connect() as db:assert db.execute('SELECT count(*) FROM class_staff_permissions').fetchone()[0]==0


def test_deleted_class_assignment_can_be_explicitly_removed(client):
    enable(client);staff=create(client,'staff');assign(client,{A:[item(staff,'attendance_read')]})
    # The application may have archived/removed a class after removing its other
    # references. Simulate the remaining duty record without changing real data.
    with api.connect() as db:
        db.execute('UPDATE class_staff_permissions SET document=? WHERE workspace=?',(json.dumps({'removed-class':[item(staff,'attendance_read')]}),'demo'))
    assert own(signed('staff'))==[]
    assign(client,{'removed-class':[]})
    assert settings(client)['assignments']=={}


def test_staff_accounts_cannot_receive_schedule_grants(client):
    enable(client)
    r=client.post('/api/access/users',json=dict(name='Staff',login='staff',role='staff',password='Synthetic-only-password!',workspaces=['demo'],grants=[dict(workspace='demo',view='student',entityIds=['s1'])]))
    assert r.status_code==422
