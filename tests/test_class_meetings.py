"""Class and parallel meetings: invitations by the dated roster, class-teacher rights, conflicts, marks and cancellation."""
from datetime import date,datetime,timedelta,timezone
import json
from zoneinfo import ZoneInfo
import pytest
from backend import api,meetings,meeting_routes,timetable_history
from backend.models import Event
from test_access import client,enable,create,signed
from test_absence_school import lead
from test_meetings import BASE,request,ok
from test_meeting_consultations import create_series,preview,slots,on_date,local,got,MONDAYS

ZONE=ZoneInfo('Asia/Qyzylorda')
BASES='/api/meetings/class-meetings'
FALL=datetime(2026,9,1,6,tzinfo=timezone.utc)  # 11:00 school time on 1 September 2026
CLOCK={}


def clock(monkeypatch,instant):
    """The school clock for meetings and for dated class moves; call again to move it."""
    CLOCK['now']=instant
    for module in [meetings,meeting_routes]:monkeypatch.setattr(module,'now',lambda:CLOCK['now'])
    monkeypatch.setattr(timetable_history,'today',lambda:CLOCK['now'].astimezone(ZONE).date())
def family(client,login,*students,role='parent'):return create(client,role,login=login,grants=[dict(workspace='demo',view='student',entityIds=list(students))])
def get(c,part='',**params):return c.get(BASES+part,params=dict(workspace='demo')|params)
def post(c,part='',**body):return c.post(BASES+part+'?workspace=demo',json=request(**body))
def form(day,start='15:00',end='16:00',**kw):
    year,month,number=map(int,day.split('-'))
    return dict(title='Родительское собрание',classIds=['c-demo-a'],startsAt=datetime(year,month,number,int(start[:2]),int(start[3:]),tzinfo=ZONE).isoformat(),endsAt=datetime(year,month,number,int(end[:2]),int(end[3:]),tzinfo=ZONE).isoformat(),location='Актовый зал')|kw
def resave(edit,effective=None):
    state=api.read('demo');edit(state);return api.save('demo',state,effective=effective)
def move(**placing):
    def edit(state):
        for student in state.students:
            if student.id in placing:student.classId=placing[student.id]
    return edit
def counts(db,user,action):return db.execute('SELECT count(*) FROM notifications WHERE user_id=? AND action=?',(user,action)).fetchone()[0]


def test_dated_membership_follows_the_meeting_date(client,monkeypatch):
    clock(monkeypatch,FALL);enable(client);p1,p2,p3=create(client,'parent'),family(client,'par2','s2'),family(client,'par3','s3');lead('t2')
    made=ok(post(client,**form('2026-09-07')));id=made['id']
    assert made['invited']==2
    with api.connect() as db:assert [counts(db,p['id'],'meeting.class_invited') for p in (p1,p2,p3)]==[1,1,0]
    clock(monkeypatch,datetime(2026,9,3,6,tzinfo=timezone.utc))
    resave(move(s1='c-demo-d',s3='c-demo-a'),date(2026,9,3))
    changed=ok(post(client,'/'+id+'/sync',version=1));assert changed['added']==1 and changed['removed']==1 and changed['version']==2
    with api.connect() as db:
        assert counts(db,p3['id'],'meeting.class_invited')==1 and counts(db,p1['id'],'meeting.class_changed')==1 and counts(db,p2['id'],'meeting.class_changed')==0
    assert {r['studentId'] for r in ok(get(client,'/'+id))['roster']}=={'s2','s3'}
    # The guardian of the pupil who left no longer opens the invitation; the guardian of the newcomer does.
    opens=lambda login,action:signed(login).post('/api/notifications/'+str(got(signed(login),action)[0]['id'])+'/open').status_code
    assert opens('parent','meeting.class_changed')==404 and opens('par3','meeting.class_invited')==200
    clock(monkeypatch,datetime(2026,9,10,6,tzinfo=timezone.utc))
    resave(move(s2='c-demo-d'),date(2026,9,10))
    idle=ok(post(client,'/'+id+'/sync',version=2));assert idle['added']==0 and idle['removed']==0 and idle['version']==2
    assert {r['studentId'] for r in ok(get(client,'/'+id))['roster']}=={'s2','s3'}
    marked=ok(post(client,'/'+id+'/marks',version=2,marks=[dict(studentId='s2',mark='present')]));assert marked['changed']==1
    assert {r['studentId']:r['mark'] for r in ok(get(client,'/'+id))['roster']}=={'s2':'present','s3':''}


def test_parallel_form_invites_guardians_and_keeps_rosters_private(client,monkeypatch):
    clock(monkeypatch,FALL);enable(client);p1=create(client,'parent');p3=family(client,'par3','s3');twin=family(client,'twin','s1','s2');p2=family(client,'par2','s2')
    parallel=form('2026-09-07',grade='10')|dict(classIds=[])
    state=api.read('demo');state.events=[Event(id='quarantine',name='Карантин 10D',start=date(2026,9,7),end=date(2026,9,7),kind='holiday',effect='cancel',classIds=['c-demo-d'])];api.save('demo',state)
    found=ok(client.post(BASES+'/preview?workspace=demo',json=parallel))
    assert found['pupils']==4 and found['guardians']==4 and found['withoutGuardianCount']==1 and found['withoutGuardian']==['Ученик 4 (пример)'] and found['conflicts']==[] and found['closedDay']=='Карантин 10D'
    made=ok(post(client,**parallel));assert made['invited']==4 and made['pupils']==4
    with api.connect() as db:assert [counts(db,p['id'],'meeting.class_invited') for p in (p1,p2,p3,twin)]==[1,1,1,1]
    entry=got(signed('twin'),'meeting.class_invited');assert len(entry)==1 and {s['id'] for s in entry[0]['students']}=={'s1','s2'}
    mine=signed('parent');item=ok(get(mine))['items'][0]
    assert item['studentIds']==['s1'] and 'roster' not in item and 'mark' not in json.dumps(item) and 'Ученик 2' not in json.dumps(item) and 'Ученик 3' not in json.dumps(item)
    assert ok(get(mine,'/'+made['id']))['students']==[dict(id='s1',name='Ученик 1 (пример)')]
    opened=signed('parent').post('/api/notifications/'+str(got(mine,'meeting.class_invited')[0]['id'])+'/open');assert opened.status_code==200 and opened.json()['module']=='class_meetings'
    only_a=ok(post(client,**form('2026-09-08')))['id']
    assert get(signed('par3'),'/'+only_a).status_code==404 and ok(get(signed('par3')))['items'][0]['id']==made['id'] and len(ok(get(signed('par3')))['items'])==1
    assert get(mine,'/'+only_a).status_code==200


def test_permissions_follow_current_class_teacher(client,monkeypatch):
    clock(monkeypatch,FALL);enable(client);create(client,'teacher');create(client,'teacher',login='physics',grants=[dict(workspace='demo',view='teacher',entityIds=['t1'])])
    create(client,'student');create(client,'parent');lead('t2');old,new=signed('teacher'),signed('physics')
    id=ok(post(old,**form('2026-09-07')))['id']
    assert post(old,**form('2026-09-07',classIds=['c-demo-d'])).status_code==403
    assert post(old,**(form('2026-09-07',grade='10')|dict(classIds=[]))).status_code==403
    assert post(signed('student'),**form('2026-09-07')).status_code==403 and post(signed('parent'),**form('2026-09-07')).status_code==403 and post(new,**form('2026-09-07')).status_code==403
    assert get(signed('student')).status_code==403 and ok(get(old))['items'][0]['id']==id and ok(get(new))['items']==[]
    assert get(new,'/'+id).status_code==404
    lead('t1')
    assert get(old,'/'+id).status_code==404 and ok(get(old))['items']==[] and post(old,'/'+id+'/cancel',version=1,reason='Не я').status_code==404
    assert ok(get(new,'/'+id))['canCancel'] is True
    assert post(new,'/'+id+'/cancel',version=1,reason='Другой классный руководитель').status_code==200
    assert post(client,**form('2026-09-07')|dict(roomId='nope')).status_code==422


def test_conflicts_with_timetable_booking_and_consultations(client,monkeypatch):
    clock(monkeypatch,BASE-timedelta(days=2));enable(client);create(client,'teacher');create(client,'parent')
    # Monday 5 April: t2 teaches English in room 301 (r0) from 08:20 to 09:00.
    room=post(client,**form(MONDAYS[0],'08:30','08:50',roomId='r0',location=''));assert room.status_code==409
    lead('t2');lesson=post(client,**form(MONDAYS[0],'08:30','08:50'));assert lesson.status_code==409
    meeting=ok(post(client,**form(MONDAYS[0],'10:30','11:00')))
    assert post(client,**form(MONDAYS[0],'10:45','11:15')).status_code==409
    tc=signed('teacher');skipped=ok(preview(tc,startDate=MONDAYS[0],endDate=MONDAYS[0],startTime='10:00',endTime='11:30'))
    assert skipped['skippedCount']>0
    series=create_series(tc,startDate=MONDAYS[0],endDate=MONDAYS[0],startTime='10:00',endTime='11:30')
    starts=sorted(local(s['startsAt']).strftime('%H:%M') for s in on_date(tc,series['id'],MONDAYS[0]))
    assert all(not '10:30'<=value<'11:00' for value in starts) and '10:15' in starts and '11:00' in starts
    over=post(client,**form(MONDAYS[0],'11:00','11:20'));assert over.status_code==409
    assert ok(get(client,'/'+meeting['id']))['status']=='scheduled'


def test_marks_only_after_start_and_cancellation_tells_guardians(client,monkeypatch):
    clock(monkeypatch,BASE-timedelta(days=2));enable(client);guardian=create(client,'parent');family(client,'par2','s2')
    first=ok(post(client,**form(MONDAYS[0],'13:00','14:00')))['id'];second=ok(post(client,**form(MONDAYS[1],'13:00','14:00')))['id']
    assert post(client,'/'+first+'/marks',version=1,marks=[dict(studentId='s1',mark='present')]).status_code==409
    clock(monkeypatch,datetime(2027,4,5,8,30,tzinfo=timezone.utc))
    body=request(version=1,marks=[dict(studentId='s1',mark='present'),dict(studentId='s2',mark='absent')])
    result=ok(client.post(BASES+'/'+first+'/marks?workspace=demo',json=body));assert result['changed']==2 and ok(client.post(BASES+'/'+first+'/marks?workspace=demo',json=body))==result
    assert {r['studentId']:r['mark'] for r in ok(get(client,'/'+first))['roster']}=={'s1':'present','s2':'absent'}
    assert post(client,'/'+first+'/marks',version=2,marks=[dict(studentId='s3',mark='present')]).status_code==422
    assert post(client,'/'+first+'/marks',version=1,marks=[dict(studentId='s1',mark='')]).status_code==409
    assert ok(post(client,'/'+first+'/marks',version=2,marks=[dict(studentId='s1',mark='')]))['changed']==1
    assert post(client,'/'+first+'/cancel',version=3,reason='Поздно').status_code==409
    assert post(client,'/'+second+'/cancel',version=1,reason='').status_code==422
    assert ok(post(client,'/'+second+'/cancel',version=1,reason='Зал отдан под экзамен'))['status']=='cancelled'
    mine=signed('parent');assert len(got(mine,'meeting.class_cancelled'))==1
    seen=next(i for i in ok(get(mine))['items'] if i['id']==second);assert seen['status']=='cancelled' and seen['reason']=='Зал отдан под экзамен'
    assert 'экзамен' not in got(mine,'meeting.class_cancelled')[0]['title']
    assert post(client,'/'+second+'/cancel',version=2,reason='Ещё раз').status_code==409


def test_create_rolls_back_when_notification_fails(client,monkeypatch):
    clock(monkeypatch,BASE-timedelta(days=2));enable(client);create(client,'parent')
    original=api.notifications.deliver
    def fail(*args,**kwargs):raise RuntimeError('synthetic notification failure')
    monkeypatch.setattr(api.notifications,'deliver',fail)
    with pytest.raises(RuntimeError):post(client,**form(MONDAYS[0],'13:00','14:00'))
    with api.connect() as db:assert db.execute('SELECT count(*) FROM class_meetings').fetchone()[0]==0 and db.execute('SELECT count(*) FROM class_meeting_invitees').fetchone()[0]==0
    monkeypatch.setattr(api.notifications,'deliver',original)
    assert post(client,**form(MONDAYS[0],'13:00','14:00')).status_code==200


def test_meeting_follows_a_replaced_class_teacher_in_the_day_view_and_in_conflicts(client,monkeypatch):
    clock(monkeypatch,BASE-timedelta(days=2));enable(client);create(client,'teacher');create(client,'teacher',login='physics',grants=[dict(workspace='demo',view='teacher',entityIds=['t1'])])
    lead('t2');old,new=signed('teacher'),signed('physics')
    meeting=ok(post(old,**form(MONDAYS[0],'10:30','11:00')))['id']
    shown=lambda c:[m['id'] for m in ok(c.get('/api/meetings/day',params=dict(workspace='demo',onDate=MONDAYS[0])))['classMeetings']]
    # Consultation hours 10:00-11:30 of a teacher: how many slots remain depends on whether the class meeting is theirs.
    free=lambda c,teacher:ok(preview(c,teacherId=teacher,roomId=None,location='Кабинет',startDate=MONDAYS[0],endDate=MONDAYS[0],startTime='10:00',endTime='11:30'))['slotCount']
    before=dict(t1=free(new,'t1'),t2=free(old,'t2'));assert shown(old)==[meeting] and shown(new)==[]
    lead('t1')
    assert shown(old)==[] and shown(new)==[meeting]
    assert free(new,'t1')<before['t1'] and free(old,'t2')>before['t2']
