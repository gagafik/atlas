from datetime import date,datetime,timezone
from uuid import uuid4
import pytest
from backend import api,handover,safety
from backend.models import Commitment,DatedLesson,ScheduleDay
from test_access import client,enable,create,signed
from test_absences import setup as absence_setup,send,decide
from test_journal import DAY,lesson,entry,graded,write
from seeding import insert_row

TUESDAY='2026-08-18'
STAMP=dict(at=datetime(2026,10,15,6,0,tzinfo=timezone.utc))


@pytest.fixture(autouse=True)
def school_clock(monkeypatch):
    STAMP['at']=datetime(2026,10,15,6,0,tzinfo=timezone.utc)
    monkeypatch.setattr(safety,'now',lambda:STAMP['at'])


def change(edit):
    state=api.read('demo');edit(state);api.save('demo',state)


def drill(client,at=None,**extra):
    params=dict(workspace='demo',**extra)|(dict(at=at) if at else {})
    return client.get('/api/safety/drill',params=params)


def seen(payload):
    return {p['id']:(place['kind'],tuple(place['roomIds']),tuple(place['teacherIds'])) for c in payload['classes'] for place in c['places'] for p in place['pupils']}


def staff(payload):
    return {(p['teacherId'],p['kind']) for p in payload['staff']}


def away(payload):
    return {p['id'] for c in payload['classes'] for p in c['away']}


def substitute(s):s.datedLessons.append(DatedLesson(id='sub',name='Замена',kind='change',placementId='eng',sourceDate=date.fromisoformat(DAY),onDate=date.fromisoformat(DAY),teacherIds=['t3'],roomIds=['r2']))
def class_teacher(s):s.classes[0].classTeacherId='t1'
def cancel_maths(s):s.datedLessons.append(DatedLesson(id='cx',name='Отмена',kind='cancel',placementId='math',sourceDate=date.fromisoformat(DAY),onDate=date.fromisoformat(DAY)))
def swap(s):s.scheduleDays.append(ScheduleDay(id='swap',name='Как понедельник',onDate=date.fromisoformat(TUESDAY),day=0))
def duty(s):s.commitments.append(Commitment(id='d',name='Дежурство',kind='duty',onDate=date.fromisoformat(DAY),startTime='08:00',endTime='09:00',teacherIds=['t1'],location='Холл',requiredTeachers=1,profileId='secondary'))
def nothing(s):pass
ENGLISH={'s1':('lesson',('r0',),('t2',)),'s2':('lesson',('r0',),('t2',))}


@pytest.mark.parametrize('prepare,at,pupils,people',[
    (nothing,DAY+'T08:30',ENGLISH,{('t2','lesson')}),
    (substitute,DAY+'T08:30',{k:('lesson',('r2',),('t3',)) for k in ENGLISH},{('t3','lesson')}),
    (class_teacher,DAY+'T09:20',{'s1':('between',(),('t1',))},set()),
    (nothing,DAY+'T10:00',{'s1':('lesson',('r1',),('t0',)),'s3':('lesson',('r1',),('t0',))},{('t0','lesson')}),
    (cancel_maths,DAY+'T10:00',{},set()),
    (nothing,TUESDAY+'T08:30',{},set()),
    (swap,TUESDAY+'T08:30',ENGLISH,{('t2','lesson')}),
    (duty,DAY+'T08:30',ENGLISH,{('t2','lesson'),('t1','duty')}),
])
def test_drill_follows_the_timetable_of_the_moment(client,prepare,at,pupils,people):
    enable(client);change(prepare)
    response=drill(client,at);assert response.status_code==200,response.text
    assert seen(response.json())==pupils and staff(response.json())==people


def left_mark(client):
    record=graded(entry(client,lesson(client)['lessonKey']))
    for p in record['document']['content']['records']:
        if p['studentId']=='s1':p.update(attendance='left',leftAt='08:40')
    assert write(client,record,publish=True).status_code==200


def whole_day_note(client):
    _,_,parent,teacher=absence_setup(client)
    assert decide(teacher,send(parent)).status_code==200


def pickup(status):
    def seed(client):
        with api.connect() as db:
            handover.tables(db)
            insert_row(db,'handover_pickups',id='p1',workspace='demo',student_id='s1',status=status,day=DAY,completed_at='2026-08-17T03:45:00+00:00',returned_at='2026-08-17T03:55:00+00:00' if status=='returned' else None)
    return seed


@pytest.mark.parametrize('prepare,checks',[
    (whole_day_note,[('08:30','away')]),
    (left_mark,[('08:35','in'),('08:50','away')]),
    (pickup('completed'),[('08:40','in'),('08:50','away')]),
    (pickup('returned'),[('08:40','in'),('08:50','away'),('08:58','in')]),
])
def test_drill_leaves_out_recorded_absence_early_leave_and_pickup(client,prepare,checks):
    if prepare is not whole_day_note:enable(client)
    prepare(client)
    for clock,expected in checks:
        payload=drill(client,f'{DAY}T{clock}').json()
        assert ('s1' in seen(payload),'s1' in away(payload))==((True,False) if expected=='in' else (False,True))
        assert 's2' in seen(payload)


def test_drill_without_a_moment_uses_the_school_clock(client):
    enable(client);STAMP['at']=datetime(2026,8,17,3,30,tzinfo=timezone.utc)
    assert drill(client).json()==drill(client,DAY+'T08:30').json()


@pytest.mark.parametrize('at',['2027-07-01T09:00','2026-08-17T25:00','2026-08-17'])
def test_drill_refuses_a_moment_outside_the_year_or_malformed(client,at):
    enable(client)
    assert drill(client,at).status_code==422


def test_drill_names_only_what_an_evacuation_needs(client):
    enable(client)
    payload=drill(client,DAY+'T08:30').json()
    assert set(payload['classes'][0])=={'classId','className','classTeacher','expected','places','away'}
    assert set(payload['classes'][0]['places'][0])=={'kind','roomIds','rooms','teacherIds','responsible','pupils'}


# Safety briefings

def new(**values):
    return dict(requestId=uuid4().hex,kind='repeated',heldOn='2026-09-01',topic='Пожарная безопасность',conductorTeacherId='t3',audience='class',classId='c-demo-a',absentIds=['s2'])|values


def register(client,**values):
    response=client.post('/api/safety/briefings?workspace=demo',json=new(**values));assert response.status_code==200,response.text
    return response.json()


def overdue(client,audience='pupils',start='2026-08-17'):
    response=client.get('/api/safety/overdue',params=dict(workspace='demo',audience=audience,start=start,end='2026-10-31'));assert response.status_code==200,response.text
    return {p['id']:p['last'] for p in response.json()['people']}


ALL={'s1','s2','s3','s4'}
@pytest.mark.parametrize('body,expected,start',[
    (dict(),{'s2','s3','s4'},'2026-08-17'),
    (dict(kind='primary'),{'s2','s3','s4'},'2026-08-17'),
    (dict(kind='targeted'),ALL,'2026-08-17'),
    (dict(),ALL,'2026-09-02'),
    (dict(audience='staff',classId=None,staffLabel='Учителя',teacherIds=['t0','t1'],absentIds=['t1']),{'t1','t2','t3'},'2026-08-17'),
])
def test_briefing_attendance_and_overdue_list(client,body,expected,start):
    enable(client);register(client,**body)
    found=overdue(client,'staff' if body.get('audience')=='staff' else 'pupils',start)
    assert set(found)==expected
    if start=='2026-09-02':assert found['s1']=='2026-09-01'


def test_briefing_register_edit_withdraw_and_replay(client):
    enable(client);body=new()
    first=client.post('/api/safety/briefings?workspace=demo',json=body).json()
    again=client.post('/api/safety/briefings?workspace=demo',json=body).json()
    assert again['id']==first['id']
    assert client.post('/api/safety/briefings?workspace=demo',json=body|dict(topic='Другая тема')).status_code==409
    listed=client.get('/api/safety/briefings',params=dict(workspace='demo')).json()['items']
    assert [i['id'] for i in listed]==[first['id']]
    assert {p['id']:p['present'] for p in first['people']}=={'s1':True,'s2':False}
    assert first['conductor']==api.read('demo').teachers[3].name
    path=f"/api/safety/briefings/{first['id']}"
    edit=dict(requestId=uuid4().hex,version=1,kind='repeated',heldOn='2026-09-01',topic='Пожарная безопасность',conductorTeacherId='t3',absentIds=[])
    changed=client.put(path+'?workspace=demo',json=edit);assert changed.status_code==200,changed.text
    assert {p['id']:p['present'] for p in changed.json()['people']}=={'s1':True,'s2':True}
    assert [h['action'] for h in changed.json()['history']]==['updated','created']
    assert client.put(path+'?workspace=demo',json=edit|dict(requestId=uuid4().hex)).status_code==409
    gone=client.post(path+'/withdraw?workspace=demo',json=dict(requestId=uuid4().hex,version=changed.json()['version'],reason='Ошибочная запись'))
    assert gone.status_code==200,gone.text
    assert client.get('/api/safety/briefings',params=dict(workspace='demo')).json()['items']==[]
    assert 's1' in overdue(client)
    assert client.get(path+'?workspace=demo').json()['status']=='withdrawn'


@pytest.mark.parametrize('change_',[
    dict(heldOn='2026-10-16'),dict(absentIds=['s3']),dict(classId='nope'),dict(conductorTeacherId=None),
    dict(audience='staff',classId=None,staffLabel='Учителя',teacherIds=['tX'],absentIds=[]),dict(audience='staff',classId=None,staffLabel='Учителя',teacherIds=[],absentIds=[]),
])
def test_briefing_validation(client,change_):
    enable(client)
    assert client.post('/api/safety/briefings?workspace=demo',json=new(**change_)).status_code==422


@pytest.mark.parametrize('role',['editor','teacher','staff','parent','student'])
def test_safety_reports_are_for_the_administrator(client,role):
    enable(client);create(client,role);other=signed(role)
    paths=['/api/safety/options','/api/safety/drill','/api/safety/briefings','/api/safety/overdue?audience=pupils&start=2026-08-17&end=2026-10-31']
    for path in paths:
        assert other.get(path+('&' if '?' in path else '?')+'workspace=demo').status_code==403
        assert client.get(path+('&' if '?' in path else '?')+'workspace=demo').status_code==200
    assert other.post('/api/safety/briefings?workspace=demo',json=new()).status_code==403
