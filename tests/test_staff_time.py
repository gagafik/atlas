from concurrent.futures import ThreadPoolExecutor
from datetime import date
from uuid import uuid4
import pytest
from backend import api,staff_time,staff_time_routes
from test_access import client,enable,create,signed

def body(**kw):return dict(requestId=str(uuid4()),**kw)
def path(part):return '/api/staff-time/'+part+('&' if '?' in part else '?')+'workspace=demo'
def ok(response):
    assert response.status_code==200,response.text
    return response.json()
@pytest.fixture
def school(client,monkeypatch):
    for module in [staff_time,staff_time_routes]:monkeypatch.setattr(module,'today',lambda:date(2026,10,15))
    enable(client);return create(client,'staff',name='Test Employee')
def save(client,person,day='2026-09-01',version=0,**doc):
    return client.put(path('days'),json=body(employeeId=person['id'],onDate=day,version=version,document=doc or dict(segments=[dict(start='09:00',end='17:00',code='work')])))
def action(client,item,act,kind='days',**kw):return client.post(path(kind+'/'+item['id']+'/action'),json=body(version=item['version'],action=act,**kw))
def report(client,**kw):return ok(client.get(path('report'),params=dict(workspace='demo',month='2026-09',**kw)))
def leave(client,person,start='2026-09-01',end='2026-09-01',**kw):return ok(client.post(path('leave'),json=body(employeeId=person['id'],startDate=start,endDate=end,**kw)))
def approve_leave(client,item):
    preview=ok(client.get(path('leave/'+item['id']+'/preview')))
    return action(client,item,'approve',kind='leave',previewHash=preview['previewHash'])
def close(client):
    p=ok(client.get(path('periods/2026-09/preview')))
    return client.post(path('periods/2026-09/action'),json=body(action='close',version=p['version'],previewHash=p['previewHash']))

def test_admin_time_employee_visibility_and_no_schedule_mutations(client,school):
    state=api.read('demo');item=ok(save(client,school));approved=ok(action(client,item,'approve'))
    employee=signed('staff');r=report(employee);assert r['summary'][0]['workMinutes']==480
    assert r['summary'][0]['recordedDays']==1
    assert len(r['days'])==1 and r['days'][0]['status']=='approved'
    assert save(employee,school,version=approved['version']).status_code==403
    assert api.read('demo')==state
    another=create(client,'staff',login='other',name='Other Secret');ok(save(client,another,'2026-09-02'))
    assert 'Other Secret' not in str(report(employee,employee=another['id']))
    assert 'Other Secret' not in employee.get(path('export?month=2026-09')).text
    assert signed('other').get(path('history/day/'+item['id'])).status_code==404

@pytest.mark.parametrize('role',['parent','student'])
def test_families_cannot_access_staff_time(client,school,role):
    create(client,role);c=signed(role)
    assert c.get(path('options')).status_code==403
    assert c.get(path('report?month=2026-09')).status_code==403
    assert c.post(path('leave'),json=body(employeeId=school['id'],startDate='2026-09-01',endDate='2026-09-01')).status_code==403

@pytest.mark.parametrize('doc',[
    dict(segments=[dict(start='09:00',end='12:00'),dict(start='11:00',end='17:00')]),
    dict(segments=[dict(start='17:00',end='09:00')]),
    dict(fullDay='sick',segments=[dict(start='09:00',end='10:00')]),dict(segments=[]),
])
def test_invalid_intervals(client,school,doc):assert save(client,school,**doc).status_code==422

def test_future_midnight_breaks_full_day_and_unfilled_days(client,school):
    assert save(client,school,'2026-10-16').status_code==422
    item=ok(save(client,school,segments=[dict(start='09:00',end='12:00'),dict(start='13:00',end='17:00'),dict(start='23:00',end='24:00',code='training')]))
    ok(action(client,item,'approve'));item=ok(save(client,school,'2026-09-02',fullDay='sick'));ok(action(client,item,'approve'))
    summary=report(client)['summary'][0]
    assert summary['workMinutes']==480 and summary['absenceMinutes']==0 and summary['fullAbsenceDays']==1 and summary['recordedDays']==2

def test_self_report_review_history_and_cas(client,school):
    employee=signed('staff');settings=body(version=0,selfReport=True,reason='Allow reporting')
    assert employee.put(path('settings'),json=settings).status_code==403
    ok(client.put(path('settings'),json=settings));assert client.put(path('settings'),json=settings).json()['version']==1
    item=ok(save(employee,school));submitted=ok(action(employee,item,'submit'))
    assert action(employee,submitted,'approve').status_code==403
    assert save(employee,school,version=submitted['version']).status_code==409
    assert action(client,item,'approve').status_code==409
    returned=ok(action(client,submitted,'return',reason='Check break'))
    edited=ok(save(employee,school,version=returned['version']));approved=ok(action(client,edited,'approve'))
    assert action(client,approved,'reopen').status_code==422
    reopened=ok(action(client,approved,'reopen',reason='Correction'))
    ok(action(client,reopened,'void',reason='Duplicate'))
    assert report(client)['summary'][0]['workMinutes']==0
    h=ok(employee.get(path('history/day/'+item['id']+'?limit=2')));assert len(h['items'])==2 and h['nextBefore']
    assert len(ok(employee.get(path('history/day/'+item['id']+'?before='+str(h['nextBefore']))))['items'])>=4

def test_replay_and_notification_rollback(client,school,monkeypatch):
    request=body(employeeId=school['id'],onDate='2026-09-01',version=0,document=dict(segments=[dict(start='09:00',end='17:00')]))
    item=ok(client.put(path('days'),json=request));assert ok(client.put(path('days'),json=request))==item
    assert client.put(path('days'),json=request|dict(onDate='2026-09-02')).status_code==409
    def fail(*args,**kw):raise RuntimeError('delivery failed')
    with monkeypatch.context() as scoped:
        scoped.setattr(api.staff_time.notifications,'deliver',fail)
        with pytest.raises(RuntimeError):action(client,item,'approve')
    assert report(client)['days'][0]['version']==1
    assert len(ok(client.get(path('history/day/'+item['id'])))['items'])==1
    ok(action(client,item,'approve'))

def test_leave_conflicts_both_directions_partial_intervals(client,school):
    item=ok(save(client,school));ok(action(client,item,'approve'))
    request=leave(signed('staff'),school,startTime='12:00',endTime='13:00')
    assert approve_leave(client,request).status_code==409
    outside=leave(client,school,startTime='17:00',endTime='18:00');ok(approve_leave(client,outside))
    nextday=leave(client,school,'2026-09-02','2026-09-02');ok(approve_leave(client,nextday))
    entry=ok(save(client,school,'2026-09-02'));assert action(client,entry,'approve').status_code==409
    duplicate=leave(client,school,'2026-09-02','2026-09-02');assert approve_leave(client,duplicate).status_code==409

def test_concurrent_leave_approval_preview_and_time_approval(client,school):
    request=leave(client,school);entry=ok(save(client,school));preview=ok(client.get(path('leave/'+request['id']+'/preview')))
    a=signed('admin');b=signed('admin')
    with ThreadPoolExecutor(2) as pool:
        results=list(pool.map(lambda f:f(),[lambda:action(a,entry,'approve'),lambda:action(b,request,'approve',kind='leave',previewHash=preview['previewHash'])]))
    assert sorted(r.status_code for r in results)==[200,409]

def test_close_pending_stale_preview_and_immutable_archives(client,school):
    item=ok(save(client,school));assert close(client).status_code==409
    approved=ok(action(client,item,'approve'));p=ok(client.get(path('periods/2026-09/preview')))
    another=ok(save(client,school,'2026-09-02'));ok(action(client,another,'approve'))
    assert client.post(path('periods/2026-09/action'),json=body(version=0,action='close',previewHash=p['previewHash'])).status_code==409
    closed=ok(close(client));assert save(client,school,'2026-09-03').status_code==409
    assert action(client,approved,'reopen',reason='Fix').status_code==409
    assert client.post(path('leave'),json=body(employeeId=school['id'],startDate='2026-09-30',endDate='2026-10-02')).status_code==409
    archived=report(signed('staff'),archive=closed['version']);assert archived['summary'][0]['workMinutes']==960
    assert client.post(path('periods/2026-09/action'),json=body(version=closed['version'],action='reopen')).status_code==422
    ok(client.post(path('periods/2026-09/action'),json=body(version=closed['version'],action='reopen',reason='Fix')))
    ok(action(client,approved,'void',reason='Wrong day'));assert report(client)['summary'][0]['workMinutes']==480
    assert report(signed('staff'),archive=closed['version'])['summary'][0]['workMinutes']==960
    second=ok(close(client));assert second['version']==3
    assert report(client)['archives']==[3,1]

def test_current_month_and_request_review_and_revocation(client,school):
    request=leave(signed('staff'),school);assert close(client).status_code==409
    assert action(client,request,'reject',kind='leave').status_code==422
    ok(action(client,request,'reject',kind='leave',reason='Wrong date'));ok(close(client))
    p=ok(client.get(path('periods/2026-10/preview')))
    assert client.post(path('periods/2026-10/action'),json=body(version=0,action='close',previewHash=p['previewHash'])).status_code==422
    c=signed('staff')
    with api.connect() as db:db.execute('UPDATE accounts SET active=0 WHERE id=?',(school['id'],))
    assert c.get(path('report?month=2026-09')).status_code==401

@pytest.mark.parametrize('role',['editor','teacher'])
def test_other_employees_only_own_scope(client,school,role):
    person=create(client,role);other=ok(save(client,school));own=ok(save(client,person));c=signed(role)
    assert report(c)['days'][0]['id']==own['id'] and len(report(c)['days'])==1
    assert action(c,other,'approve').status_code==404
    assert c.get(path('periods/2026-09/preview')).status_code==403

def test_cross_month_leave_cannot_change_after_end_month_closed(client,school,monkeypatch):
    for module in [staff_time,staff_time_routes]:monkeypatch.setattr(module,'today',lambda:date(2026,11,15))
    request=ok(approve_leave(client,leave(client,school,'2026-09-30','2026-10-02')))
    p=ok(client.get(path('periods/2026-10/preview')))
    ok(client.post(path('periods/2026-10/action'),json=body(version=0,action='close',previewHash=p['previewHash'])))
    assert action(client,request,'cancel',kind='leave',reason='Change').status_code==409
    assert report(client)['requests'][0]['status']=='approved'

def test_csv_formula_escape_utf8_and_current_access_rechecked_inside_transaction(client,school,monkeypatch):
    with api.connect() as db:db.execute('UPDATE accounts SET name=? WHERE id=?',('=SECRET()',school['id']))
    ok(save(client,school));export=client.get(path('export?month=2026-09'))
    assert export.content.startswith(b'\xef\xbb\xbf') and "'=SECRET()" in export.text
    employee=signed('staff');original=api.staff_time.prepare
    def revoke():
        original()
        with api.connect() as db:db.execute('UPDATE accounts SET version=version+1 WHERE id=?',(school['id'],))
    monkeypatch.setattr(api.staff_time,'prepare',revoke)
    assert employee.get(path('report?month=2026-09')).status_code==409
