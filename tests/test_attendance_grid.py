"""Class attendance grid: dated cells, class-teacher edits through the journal's own revisions and the daily confirmation."""
from datetime import date,datetime,timedelta,timezone
import json
from uuid import uuid4
import pytest
from backend import api,attendance_grid,coverage_reminders,journal,journal_coverage,journal_periods,timetable_history
from backend.models import DatedLesson,Placement,Teacher
from test_access import client,enable,create,signed
from test_absences import body,decide,send
from test_journal import DAY,lesson,entry,write,graded,report as pupil_report
from test_journal_history import resave,pupil
from test_journal_periods import period as make_period,close as close_period,action as period_action
from test_teaching_materials import save as save_material,attach

A,D='c-demo-a','c-demo-d'
ENGLISH,MATHS='Английский язык','Математика'
GRID='/api/journal/attendance/grid'
NEXT='2026-08-24'
LATER='2026-09-01'


@pytest.fixture
def clock(monkeypatch):
    """The school clock: the evening of the fixture Monday, so its lessons are over but the day is still «today»."""
    return fix(monkeypatch,DAY,14)


def fix(monkeypatch,day,hour=14,minute=0):
    moment=datetime.fromisoformat(day).replace(hour=hour,minute=minute,tzinfo=timezone.utc);local=moment.date()
    monkeypatch.setattr(journal,'today',lambda:local)
    monkeypatch.setattr(timetable_history,'today',lambda:local)
    monkeypatch.setattr(attendance_grid,'moment',lambda:moment.astimezone(attendance_grid.ZONE))
    monkeypatch.setattr(coverage_reminders,'now',lambda:moment)
    monkeypatch.setattr(journal_coverage,'now',lambda:moment)
    monkeypatch.setattr(journal_periods,'now',lambda:moment)
    return moment


def lead(teacher_id,class_id=A):
    """Set the class teacher outside any request: the actor is the local administrator."""
    school=api.read('demo');school.classes=[c.model_copy(update=dict(classTeacherId=teacher_id)) if c.id==class_id else c for c in school.classes]
    api.save('demo',school)


def staffed(client):
    """The administrator (client), the parent and pupil of s1, the English teacher t2, the maths teacher t0 and the class teacher of 10A (t1, no lessons that day)."""
    enable(client);create(client,'parent');create(client,'student');create(client,'teacher',name='Учитель английского')
    create(client,'teacher',login='maths',name='Учитель математики',grants=[dict(workspace='demo',view='teacher',entityIds=['t0'])])
    leader=create(client,'teacher',login='leader',name='Классный руководитель',grants=[dict(workspace='demo',view='teacher',entityIds=['t1'])])
    lead('t1')
    return dict(admin=client,english=signed('teacher'),maths=signed('maths'),leader=signed('leader'),parent=signed('parent'),student=signed('student'),leaderId=leader['id'])


def assign(client,assignments):
    current=good(client.get('/api/access/class-staff?workspace=demo'))
    return good(client.put('/api/access/class-staff?workspace=demo',json=dict(version=current['version'],reason='Назначение для проверки',assignments=assignments)))


def grid(c,class_id=A,start=DAY,end=None,**extra):
    return c.get(GRID,params=dict(workspace='demo',classId=class_id,start=start,end=end or start)|extra)


def good(response):
    assert response.status_code==200,response.text;return response.json()


def cell(key,student,expected=None,late=0,day=DAY):return dict(lessonKey=key,day=day,studentId=student,expected=expected,expectedLate=late)
def post(c,cells,attendance='absent',class_id=A,**values):
    return c.post(GRID+'/marks?workspace=demo',json=dict(requestId=str(uuid4()),classId=class_id,cells=cells,attendance=attendance,lateMinutes=0)|values)


def key_of(c,subject=ENGLISH,day=DAY):return lesson(c,subject,day)['lessonKey']
def lesson_of(value,subject,day=DAY):return next(l for d in value['days'] if d['date']==day for l in d['lessons'] if l['subject']==subject)
def day_of(value,day=DAY):return next(d for d in value['days'] if d['date']==day)


def publish(c,subject=ENGLISH,day=DAY,**marks):
    """The lesson's teacher publishes a graded journal; marks maps a pupil to (attendance, late minutes)."""
    record=graded(entry(c,key_of(c,subject,day),day=day))
    for r in record['document']['content']['records']:
        if r['studentId'] in marks:r['attendance'],r['lateMinutes']=marks[r['studentId']]
    return good(write(c,record,publish=True))


def confirm(c,days=(DAY,),action='confirm',class_id=A,versions=None,**values):
    return c.post(GRID+'/days?workspace=demo',json=dict(requestId=str(uuid4()),classId=class_id,days=[dict(date=d,version=(versions or {}).get(d,0)) for d in days],action=action)|values)


def inbox(c,action='journal.updated'):return [i for i in c.get('/api/notifications/inbox').json()['items'] if i['action']==action]
def history(c,key):return good(c.get('/api/journal/history',params=dict(workspace='demo',lessonKey=key)))
def close_day(c,day=DAY):
    """Close a period of one day through the periods API; the result can be reopened with period_action."""
    return close_period(c,make_period(c,start=day,end=day))


def test_tables_are_added_idempotently(client,clock):
    people=staffed(client);publish(people['english'],s1=('present',0),s2=('present',0))
    good(confirm(people['leader'],acknowledge=True))
    with api.connect() as db:
        api.journal.tables(db);api.journal.tables(db)
    assert state_of(people['leader'])['status']=='confirmed' and len(trail(people['leader']))==1
    assert client.get('/api/journal/day?workspace=demo&day='+DAY).status_code==200


def test_grid_cells_counts_and_percent(client,clock):
    people=staffed(client);publish(people['english'],s1=('absent',0),s2=('late',10))
    value=good(grid(people['leader']))
    assert value['level']=='edit' and value['canEdit'] and value['classId']==A and value['className']=='10A'
    english,maths=lesson_of(value,ENGLISH),lesson_of(value,MATHS)
    assert english['state']=='published' and maths['state']=='none' and english['canEdit'] and maths['canEdit']
    cells=value['cells']
    assert cells[english['lessonKey']]=={'s1':['absent','absent',0,None],'s2':['late','late',10,None]}
    # The shared maths group also holds s3 of 10D: that pupil is never in this grid. s1 has no record there yet.
    assert cells[maths['lessonKey']]=={'s1':[None,None,0,None]}
    day=day_of(value);assert day['counts']==dict(present=0,absent=1,late=1,excused=0,left=0,unmarked=0,noRecord=1) and day['openLessons']==1
    assert day['rates']==dict(marked=2,attendedPercent=50.0,absentPercent=50.0,excusedPercent=0.0)
    by={p['id']:p for p in value['pupils']};assert list(by)==['s1','s2']
    assert by['s2']['rates']['attendedPercent']==100.0 and by['s1']['rates']['attendedPercent']==0.0 and by['s1']['counts']['noRecord']==1
    other=good(grid(people['admin'],D))
    assert set(other['cells'][lesson_of(other,MATHS)['lessonKey']])=={'s3'} and 's3' not in value['cells'][maths['lessonKey']]
    # The same numbers as the summary report for the class and day.
    summary=good(people['admin'].post('/api/journal/attendance/report?workspace=demo',json=dict(start=DAY,end=DAY,classId=A)))['totals']['attendance']
    assert {k:v for k,v in value['totals']['counts'].items() if k!='noRecord'}==summary


def test_a_period_in_the_future_has_lessons_but_no_gaps_and_no_editable_cells(client,clock):
    people=staffed(client)
    value=good(grid(people['leader'],start=DAY,end=NEXT))
    assert [d['date'] for d in value['days']]==[DAY,NEXT]
    later=day_of(value,NEXT);assert later['counts']['noRecord']==0 and not any(l['canEdit'] for l in later['lessons']) and not later['confirmation']['expected']
    assert grid(people['leader'],start=DAY,end='2026-11-30').status_code==422
    assert grid(people['leader'],start='2026-08-01',end=DAY).status_code==422


def test_note_shows_effective_status_and_code_to_class_staff_only(client,clock):
    people=staffed(client);publish(people['english'],s1=('absent',0),s2=('present',0))
    notice=send(people['parent'],body(codeId='illness'))
    assert decide(people['leader'],notice).status_code==200
    mine=good(grid(people['leader']));key=lesson_of(mine,ENGLISH)['lessonKey']
    assert mine['cells'][key]['s1']==['absent','excused',0,'illness'] and mine['codes']
    teacher=good(grid(people['english']))
    assert teacher['level']=='own' and teacher['cells'][key]['s1']==['absent','excused',0,None] and teacher['codes']==[]
    assert 'Личная причина' not in json.dumps(mine,ensure_ascii=False)


def test_note_type_needs_the_report_or_the_notes(client,clock):
    people=staffed(client);key=publish(people['english'],s1=('absent',0),s2=('present',0))['lessonKey']
    assert decide(people['leader'],send(people['parent'],body(codeId='illness'))).status_code==200
    seen=lambda c:good(grid(c));report=lambda c:c.post('/api/journal/attendance/report?workspace=demo',json=dict(start=DAY,end=DAY))
    assert seen(people['leader'])['cells'][key]['s1'][3]=='illness'
    # A deputy who only edits the grid sees the accepted result, never the type of the note and no list of types.
    staff=create(client,'staff',login='deputy');deputy=signed('deputy')
    assign(client,{A:[dict(accountId=staff['id'],capabilities=['attendance_grid'])]})
    shown=seen(deputy);assert shown['level']=='edit' and shown['cells'][key]['s1']==['absent','excused',0,None] and shown['codes']==[]
    assert good(deputy.get(GRID+'/options?workspace=demo'))['codes']==[] and report(deputy).status_code==403
    # With the report the types come back.
    assign(client,{A:[dict(accountId=staff['id'],capabilities=['attendance_grid','attendance_read'])]})
    shown=seen(deputy);assert shown['cells'][key]['s1'][3]=='illness' and shown['codes'] and good(deputy.get(GRID+'/options?workspace=demo'))['codes']
    # The class teacher who loses both duties keeps editing and loses the types.
    assign(client,{A:[dict(accountId=people['leaderId'],capabilities=[],revoked=['attendance_read','absence_review'])]})
    mine=seen(people['leader']);assert mine['level']=='edit' and mine['cells'][key]['s1']==['absent','excused',0,None] and mine['codes']==[]
    assert good(people['leader'].get(GRID+'/options?workspace=demo'))['codes']==[] and report(people['leader']).status_code==403
    # Either duty alone is enough.
    assign(client,{A:[dict(accountId=people['leaderId'],capabilities=[],revoked=['attendance_read'])]})
    assert seen(people['leader'])['cells'][key]['s1'][3]=='illness'


def test_draft_marks_never_shown(client,clock):
    people=staffed(client);record=graded(entry(people['english'],key_of(people['english'])))
    saved=good(write(people['english'],record))
    value=good(grid(people['leader']));english=lesson_of(value,ENGLISH)
    assert english['state']=='draft' and value['cells'][english['lessonKey']]['s1']==[None,None,0,None]
    text=json.dumps(value,ensure_ascii=False)
    assert all(secret not in text for secret in ['Тема урока','Комментарий','Индивидуальная','Прочитать','assessments','grades'])
    assert saved['version']==1


def test_dated_rosters(client,monkeypatch):
    fix(monkeypatch,LATER)
    people=staffed(client)
    resave(lambda s:s.placements.append(Placement(id='eng-d',workloadId='w1',day=0,period=3,roomId='r1')))
    publish(people['admin'],ENGLISH,DAY,s1=('absent',0))
    resave(lambda s:setattr(pupil(s,'s1'),'classId',D),date.fromisoformat(NEXT))
    old=good(grid(people['admin'],A,DAY,NEXT));new=good(grid(people['admin'],D,DAY,NEXT))
    cells=lambda value,day,subject=ENGLISH:{p for p in value['cells'][lesson_of(value,subject,day)['lessonKey']]}
    assert cells(old,DAY)=={'s1','s2'} and cells(old,NEXT)=={'s2'}
    assert cells(new,DAY)=={'s3','s4'} and cells(new,NEXT)=={'s1','s3','s4'}
    # The publication of the earlier day stays with the class it was made for.
    assert old['cells'][lesson_of(old,ENGLISH)['lessonKey']]['s1'][0]=='absent'
    assert lesson_of(old,ENGLISH)['frozen']
    assert {p['id'] for p in new['pupils']}=={'s1','s3','s4'}
    pupils_a={d['date']:{id for l in d['lessons'] for id in old['cells'][l['lessonKey']]} for d in old['days']}
    pupils_d={d['date']:{id for l in d['lessons'] for id in new['cells'][l['lessonKey']]} for d in new['days']}
    assert not any(pupils_a[day]&pupils_d[day] for day in pupils_a)


def test_subject_teacher_reads_only_own_lessons(client,clock):
    people=staffed(client);publish(people['english'],s1=('absent',0))
    maths=good(grid(people['maths']))
    assert maths['level']=='own' and not maths['canEdit']
    assert [l['subject'] for d in maths['days'] for l in d['lessons']]==[MATHS] and not any(l['canEdit'] for d in maths['days'] for l in d['lessons'])
    assert good(grid(people['maths'],D))['level']=='own'
    options=good(people['maths'].get(GRID+'/options?workspace=demo'))
    assert options['available'] and {c['id']:c['level'] for c in options['classes']}=={A:'own',D:'own'} and options['codes']==[]
    mine=good(people['leader'].get(GRID+'/options?workspace=demo'))
    assert {c['id']:(c['level'],c['classTeacher']) for c in mine['classes']}=={A:('edit',True),D:('own',False)} and mine['defaultClassId']==A
    # A substitute sees the substituted lesson.
    resave(lambda s:setattr(s,'datedLessons',[DatedLesson(id='sub',name='Замена',kind='change',placementId='math',sourceDate=date.fromisoformat(DAY),onDate=date.fromisoformat(DAY),teacherIds=['t3'])]))
    create(client,'teacher',login='history',grants=[dict(workspace='demo',view='teacher',entityIds=['t3'])])
    substitute=good(grid(signed('history')));assert [l['subject'] for d in substitute['days'] for l in d['lessons']]==[MATHS]
    assert not [l for d in good(grid(people['maths']))['days'] for l in d['lessons']]
    for account in ['parent','student']:assert grid(people[account]).status_code==403
    resave(lambda s:s.teachers.append(Teacher(id='t9',name='Посторонний')));lead('t9',D)
    create(client,'teacher',login='outsider',grants=[dict(workspace='demo',view='teacher',entityIds=['t9'])])
    assert grid(signed('outsider')).status_code==404 and grid(signed('outsider'),D).status_code==200
    assert grid(people['admin'],'missing').status_code==404


def test_cell_history_lists_published_changes(client,clock):
    people=staffed(client);first=publish(people['english'],s1=('present',0),s2=('present',0))
    key=first['lessonKey'];second=good(post(people['leader'],[cell(key,'s1','present')],'late',lateMinutes=7))
    assert second['status']=='applied'
    third=good(post(people['leader'],[cell(key,'s1','late',7)],'absent'))
    path=GRID+'/history'
    items=good(people['leader'].get(path,params=dict(workspace='demo',classId=A,lessonKey=key,studentId='s1',day=DAY)))['items']
    assert [(i['attendance'],i['lateMinutes']) for i in items]==[('absent',0),('late',7),('present',0)] and items[0]['actor']=='Классный руководитель' and items[2]['actor']=='Учитель английского'
    assert 'Тема урока' not in json.dumps(items,ensure_ascii=False)
    # s2 never changed: one entry.
    assert len(good(people['leader'].get(path,params=dict(workspace='demo',classId=A,lessonKey=key,studentId='s2',day=DAY)))['items'])==1
    assert people['leader'].get(path,params=dict(workspace='demo',classId=D,lessonKey=key,studentId='s1',day=DAY)).status_code in (403,404)
    assert people['leader'].get(path,params=dict(workspace='demo',classId=A,lessonKey=key,studentId='s3',day=DAY)).status_code==404
    assert people['parent'].get(path,params=dict(workspace='demo',classId=A,lessonKey=key,studentId='s1',day=DAY)).status_code==403
    assert third['changed']==1


def test_multi_select_writes_through_journal(client,clock):
    people=staffed(client);english,maths=key_of(client,ENGLISH),key_of(client,MATHS)
    result=good(post(people['leader'],[cell(english,'s1'),cell(maths,'s1')],'absent',reason='Не пришёл'))
    assert result['status']=='applied' and result['changed']==2 and result['skipped']==0 and len(result['lessons'])==2
    rows_=good(pupil_report(people['parent']))['rows']
    assert sorted(r['lesson']['subject'] for r in rows_)==[ENGLISH,MATHS] and {r['record']['attendance'] for r in rows_}=={'absent'}
    assert len(inbox(people['parent']))==2
    entries=history(client,english);assert [(h['action'],h['actor']) for h in entries]==[('journal.published','Классный руководитель')]
    totals=good(client.post('/api/journal/attendance/report?workspace=demo',json=dict(start=DAY,end=DAY,classId=A)))['totals']['attendance']
    assert totals['absent']==2
    # Setting the same mark again changes nothing and writes nothing.
    again=good(post(people['leader'],[cell(english,'s1','absent'),cell(maths,'s1','absent')],'absent'))
    assert again['changed']==0 and again['skipped']==2 and again['lessons']==[] and len(history(client,english))==1


def test_teacher_draft_stays_private_and_keeps_the_edit(client,clock):
    people=staffed(client);key=key_of(people['maths'],MATHS)
    saved=good(write(people['maths'],graded(entry(people['maths'],key))))
    result=good(post(people['leader'],[cell(key,'s1')],'absent'))
    assert result['changed']==1 and result['lessons'][0]['version']==3 and result['lessons'][0]['publishedVersion']==2
    shown=good(pupil_report(people['parent']))['rows'];assert [r['lesson']['subject'] for r in shown]==[MATHS]
    assert shown[0]['record']['attendance']=='absent' and shown[0]['record']['grades']==[] and shown[0]['content']['topic']=='' and shown[0]['record']['comment']==''
    # Both pupils of the shared lesson are in the publication: s1 marked, s3 left unmarked.
    assert good(grid(people['leader']))['cells'][key]['s1'][0]=='absent' and good(grid(client,D))['cells'][key]['s3'][0]=='unmarked'
    mine=entry(people['maths'],key);records={r['studentId']:r for r in mine['document']['content']['records']}
    assert mine['document']['content']['topic']=='Тема урока' and records['s1']['attendance']=='absent' and records['s1']['grades'][0]['score']==8 and records['s3']['attendance']=='present'
    assert records['s1']['comment']=='Комментарий только s1'
    # The teacher's open page is stale: the ordinary version check applies, a reload saves.
    assert write(people['maths'],saved).status_code==409
    assert write(people['maths'],mine).status_code==200


def test_never_opened_lesson_gets_attendance_only_publication(client,clock):
    people=staffed(client);key=key_of(client,MATHS)
    good(post(people['leader'],[cell(key,'s1')],'absent'))
    coverage=good(client.post('/api/journal/coverage/report?workspace=demo',json=dict(start=DAY,end=DAY,status='all',classId=A)))
    row=next(r for r in coverage['items'] if r['subject']==MATHS);assert row['status']=='incomplete' and not row['topicFilled'] and row['publishedVersion']==1
    other=good(grid(client,D));assert other['cells'][lesson_of(other,MATHS)['lessonKey']]=={'s3':['unmarked','unmarked',0,None]}
    assert good(pupil_report(people['parent']))['rows'][0]['record']['attendance']=='absent'


def test_conflict_is_cell_level_and_atomic(client,clock):
    people=staffed(client);english=publish(people['admin'],ENGLISH,s1=('present',0),s2=('present',0))['lessonKey'];maths=key_of(client,MATHS)
    record=entry(people['english'],english);next(r for r in record['document']['content']['records'] if r['studentId']=='s1')['attendance']='absent'
    good(write(people['english'],record,publish=True))
    refused=post(people['leader'],[cell(english,'s1','present'),cell(maths,'s1')],'late',lateMinutes=5)
    assert refused.status_code==409 and ENGLISH in refused.json()['detail'] and '17.08' in refused.json()['detail']
    value=good(grid(people['leader']))
    assert lesson_of(value,MATHS)['state']=='none' and value['cells'][english]['s1'][0]=='absent'
    # Another pupil of the same lesson is not a conflict.
    assert good(post(people['leader'],[cell(english,'s2','present')],'late',lateMinutes=3))['changed']==1


def test_a_publication_that_leaves_the_pupil_unmarked_is_no_conflict(client,clock):
    people=staffed(client);english=key_of(client,ENGLISH)
    seen=good(grid(people['leader']))['cells'][english]['s1'];assert seen==[None,None,0,None]
    # The teacher clears the editor's default and publishes a topic only: every pupil is an explicit «unmarked» record now, which the grid showed as «no record».
    record=entry(people['english'],english);record['document']['content']['topic']='Только тема'
    for mark in record['document']['content']['records']:mark['attendance']='unmarked'
    good(write(people['english'],record,publish=True))
    assert good(grid(people['leader']))['cells'][english]['s1'][0]=='unmarked'
    assert good(post(people['leader'],[cell(english,'s1',seen[0],seen[2])],'absent'))['changed']==1
    assert good(grid(people['leader']))['cells'][english]['s1'][0]=='absent'


def test_a_first_publication_by_someone_else_is_no_conflict(client,clock):
    people=staffed(client);english=key_of(client,ENGLISH)
    stale=good(grid(people['leader']))['cells'][english]
    # Another editor marks s2 and thereby creates the lesson's first publication; s1 becomes an explicit unmarked record.
    good(post(people['admin'],[cell(english,'s2')],'late',lateMinutes=4))
    assert good(post(people['leader'],[cell(english,'s1',stale['s1'][0])],'absent'))['changed']==1
    # What the class teacher saw of s2 is out of date: that cell is a conflict.
    assert post(people['leader'],[cell(english,'s2',stale['s2'][0])],'present').status_code==409


def test_early_leave_from_the_lesson_shows_in_the_grid_and_the_grid_replaces_it_cleanly(client,clock):
    people=staffed(client);english=key_of(client,ENGLISH)
    record=entry(people['english'],english);lesson_=record['document']['lesson']
    start,end=[int(t[:2])*60+int(t[3:]) for t in (lesson_['start'],lesson_['end'])];middle=(start+end)//2
    for pupil_ in record['document']['content']['records']:pupil_['attendance']='present'
    mark=next(r for r in record['document']['content']['records'] if r['studentId']=='s1')
    mark|=dict(attendance='left',leftAt=f'{middle//60:02d}:{middle%60:02d}')
    good(write(people['english'],record,publish=True))
    value=good(grid(people['leader']));assert value['cells'][english]['s1'][:2]==['left','left']
    # An early leave was at the lesson: it counts among the marked lessons as attended.
    day=day_of(value);assert day['counts']['left']==1 and day['rates']['marked']==day['counts']['present']+1
    assert day['rates']['attendedPercent']==100.0
    assert good(post(people['leader'],[cell(english,'s1','left')],'absent'))['changed']==1
    saved=next(r for r in entry(people['english'],english)['document']['content']['records'] if r['studentId']=='s1')
    assert saved['attendance']=='absent' and saved['leftAt'] is None


@pytest.mark.parametrize('mark',['excused','left'])
def test_the_grid_cannot_set_a_mark_that_needs_a_note_or_a_time(client,clock,mark):
    people=staffed(client);english=key_of(client,ENGLISH)
    assert post(people['leader'],[cell(english,'s1')],mark).status_code==422
    assert good(grid(people['leader']))['cells'][english]['s1'][0] is None


def test_replay_is_idempotent(client,clock):
    people=staffed(client);english=key_of(client,ENGLISH);rid=str(uuid4())
    first=good(post(people['leader'],[cell(english,'s1')],'absent',requestId=rid))
    notices=len(inbox(people['parent']))
    assert good(post(people['leader'],[cell(english,'s1')],'absent',requestId=rid))==first
    assert len(history(client,english))==1 and len(inbox(people['parent']))==notices
    assert post(people['leader'],[cell(english,'s2')],'absent',requestId=rid).status_code==409
    assert post(people['leader'],[cell(english,'s1','absent')],'present').status_code==200


def test_permissions(client,clock):
    people=staffed(client);english,maths=key_of(client,ENGLISH),key_of(client,MATHS)
    for name in ['parent','student','english','maths']:
        assert post(people[name],[cell(english,'s1')]).status_code==403,name
    staff=create(client,'staff',login='deputy');reader=signed('deputy')
    assign(client,{A:[dict(accountId=staff['id'],capabilities=['attendance_read'])]})
    assert good(grid(reader))['level']=='read' and not good(grid(reader))['canEdit'] and post(reader,[cell(english,'s1')]).status_code==403
    assert confirm(reader,acknowledge=True).status_code==403
    resave(lambda s:s.teachers.append(Teacher(id='t9',name='Посторонний')));lead('t9',D)
    create(client,'teacher',login='outsider',grants=[dict(workspace='demo',view='teacher',entityIds=['t9'])])
    assert post(signed('outsider'),[cell(english,'s1')]).status_code==404
    # The maths group is shared with 10D: its pupil is not in this class.
    assert post(people['leader'],[cell(maths,'s3')]).status_code==404
    assign(client,{A:[dict(accountId=people['leaderId'],capabilities=[],revoked=['attendance_grid'])]})
    assert good(grid(people['leader']))['level']=='read' and post(people['leader'],[cell(english,'s1')]).status_code==403
    assign(client,{A:[dict(accountId=staff['id'],capabilities=['attendance_grid'])]})
    assert good(grid(reader))['level']=='edit' and post(reader,[cell(english,'s1')]).status_code==200
    assert post(people['admin'],[cell(english,'s2','unmarked')]).status_code==200
    # In 10D the same teacher only teaches (physics): reading, no edit.
    assert post(people['leader'],[cell(english,'s1','absent')],class_id=D).status_code==403


def test_closed_future_and_cancelled_lessons_are_refused_and_nothing_is_written(client,clock):
    people=staffed(client);english,maths=key_of(client,ENGLISH),key_of(client,MATHS);untouched=lambda:good(grid(people['leader']))['cells'][maths]['s1'][0] is None
    period=close_day(client)
    closed=post(people['leader'],[cell(english,'s1'),cell(maths,'s1')])
    assert closed.status_code==403 and period['name'] in closed.json()['detail'] and untouched()
    shown=good(grid(people['leader']))['days'][0];assert not any(l['canEdit'] for l in shown['lessons']) and shown['locked']==[period['name']]
    assert period_action(client,period,'reopen',reason='Исправление отметок').status_code==200
    future=key_of(client,ENGLISH,NEXT)
    assert post(people['leader'],[cell(maths,'s1'),cell(future,'s1',day=NEXT)]).status_code==422 and untouched()
    resave(lambda s:setattr(s,'datedLessons',[DatedLesson(id='cancel',name='Отмена',kind='cancel',placementId='eng',sourceDate=date.fromisoformat(DAY),onDate=date.fromisoformat(DAY))]))
    cancelled=post(people['leader'],[cell(maths,'s1'),cell(english,'s1')])
    assert cancelled.status_code==409 and untouched()
    shown=good(grid(people['leader']));assert lesson_of(shown,ENGLISH)['status']=='cancelled' and not lesson_of(shown,ENGLISH)['canEdit'] and shown['cells'][english]=={}


def test_a_moved_lesson_is_refused_and_unsettles_the_day_it_left(client,monkeypatch):
    fix(monkeypatch,'2026-08-19')
    people=staffed(client);leader=people['leader'];english=publish(people['english'],s1=('present',0),s2=('present',0))['lessonKey']
    good(confirm(leader,acknowledge=True));assert state_of(leader)['status']=='confirmed'
    resave(lambda s:setattr(s,'datedLessons',[DatedLesson(id='move',name='Перенос',kind='change',placementId='eng',sourceDate=date.fromisoformat(DAY),onDate=date(2026,8,18),startTime='14:00',endTime='14:40')]))
    # A grid that still shows the old day must not write marks onto a lesson that has gone elsewhere.
    assert post(leader,[cell(english,'s1','present')],'absent').status_code==409
    # Until the teacher moves the publication it stays on the old day, shown and read-only.
    old=lesson_of(good(grid(leader)),ENGLISH);assert old['status']=='cancelled' and not old['canEdit'] and day_of(good(grid(leader)))['counts']['present']==2
    # The teacher moves the publication to the new date with the very same marks: the old day no longer holds them.
    synced=entry(people['english'],english,syncRoster=True);assert write(people['english'],synced,publish=True,syncRoster=True).status_code==200
    assert state_of(leader)['status']=='changed' and state_of(leader)['version']==2 and day_of(good(grid(leader)))['counts']['present']==0
    moved=good(grid(leader,start='2026-08-18'));assert moved['cells'][lesson_of(moved,ENGLISH,'2026-08-18')['lessonKey']]['s1'][0]=='present'


def test_material_stays_after_class_teacher_edit(client,clock):
    people=staffed(client);material=save_material(people['english']).json()
    record=graded(entry(people['english'],key_of(people['english'],ENGLISH)));key=record['lessonKey']
    assert attach(people['english'],record,material,publish=True).status_code==200
    assert people['leader'].get('/api/journal/library/'+material['id']+'?workspace=demo').status_code in (403,404)
    good(post(people['leader'],[cell(key,'s1','present')],'absent'))
    shown=good(pupil_report(people['parent']))['rows'][0]
    assert shown['record']['attendance']=='absent' and shown['record']['grades'][0]['score']==8
    assert [m['id'] for m in shown['content']['materials']]==[material['id']] and shown['content']['topic']=='Тема урока'


def test_lesson_teacher_is_told_about_a_correction(client,clock):
    people=staffed(client);key=publish(people['english'],s1=('absent',0),s2=('present',0))['lessonKey'];maths=key_of(client,MATHS)
    good(post(people['leader'],[cell(key,'s1','absent')],'present'))
    told=inbox(people['english'],'journal.attendance_corrected')
    assert len(told)==1 and 'Классный руководитель' in told[0]['title'] and 'Ученик' not in told[0]['title'] and ENGLISH in told[0]['title']
    assert not inbox(people['leader'],'journal.attendance_corrected')
    # A mark nobody had published before is no correction.
    good(post(people['leader'],[cell(maths,'s1')],'absent'))
    assert not inbox(people['maths'],'journal.attendance_corrected')


def test_the_one_who_corrects_is_not_told(client,clock):
    people=staffed(client);lead('t2')
    # The English teacher now leads 10A and teaches its English lesson.
    english=publish(people['english'],s1=('absent',0),s2=('present',0))['lessonKey'];maths=publish(people['maths'],MATHS,s1=('present',0),s3=('present',0))['lessonKey']
    good(post(people['english'],[cell(english,'s1','absent')],'present'))
    assert not inbox(people['english'],'journal.attendance_corrected')
    # Correcting a colleague's lesson does tell that colleague, and still not the author of the correction.
    good(post(people['english'],[cell(maths,'s1','present')],'absent'))
    assert len(inbox(people['maths'],'journal.attendance_corrected'))==1 and not inbox(people['english'],'journal.attendance_corrected')


def test_failure_rolls_back_every_lesson(client,clock,monkeypatch):
    people=staffed(client);english,maths=key_of(client,ENGLISH),key_of(client,MATHS);calls=[]
    original=api.journal.notify
    def fail(*args,**kwargs):
        calls.append(1)
        if len(calls)>1:raise RuntimeError('notification failed')
        return original(*args,**kwargs)
    monkeypatch.setattr(api.journal,'notify',fail)
    with pytest.raises(RuntimeError):post(people['leader'],[cell(english,'s1'),cell(maths,'s1')])
    monkeypatch.setattr(api.journal,'notify',original)
    value=good(grid(people['leader']))
    assert lesson_of(value,ENGLISH)['state']=='none' and lesson_of(value,MATHS)['state']=='none' and not good(pupil_report(people['parent']))['rows']
    assert good(post(people['leader'],[cell(english,'s1'),cell(maths,'s1')]))['changed']==2


def state_of(c,day=DAY,class_id=A):return day_of(good(grid(c,class_id,day)),day)['confirmation']
def trail(c,day=DAY,class_id=A):return good(c.get(GRID+'/days/history',params=dict(workspace='demo',classId=class_id,day=day)))['items']


def test_confirmation_lifecycle(client,clock):
    people=staffed(client);leader=people['leader'];english=publish(people['english'],s1=('present',0),s2=('present',0))['lessonKey']
    assert state_of(leader)['status']=='none' and state_of(leader)['expected'] and state_of(leader)['canConfirm']
    # The maths lesson was never filled in: the class teacher has to say that was checked.
    refused=confirm(leader);assert refused.status_code==409 and '17.08.2026' in refused.json()['detail'] and not trail(leader)
    done=good(confirm(leader,acknowledge=True));assert [(d['date'],d['status'],d['version'],d['actor']) for d in done['days']]==[(DAY,'confirmed',1,'Классный руководитель')]
    shown=state_of(leader);assert shown['status']=='confirmed' and shown['version']==1 and shown['actor']=='Классный руководитель' and shown['updated']
    assert trail(leader)[0]['comment']
    assert good(confirm(leader,versions={DAY:1}))['days'][0]['version']==1  # already confirmed: nothing changes
    # A new topic is no new mark: the day stays confirmed.
    record=entry(people['english'],english);record['document']['content']['topic']='Другая тема';good(write(people['english'],record,publish=True))
    assert state_of(leader)['status']=='confirmed' and state_of(leader)['version']==1
    # A changed mark opens the day again.
    record=entry(people['english'],english);next(r for r in record['document']['content']['records'] if r['studentId']=='s2')['attendance']='absent';good(write(people['english'],record,publish=True))
    assert state_of(leader)['status']=='changed' and state_of(leader)['version']==2
    # The header names whoever changed the day, not the one who confirmed it; the confirmation stays in the history.
    assert state_of(leader)['actor']=='Учитель английского' and trail(leader)[1]['actor']=='Классный руководитель'
    assert confirm(leader,versions={DAY:1},acknowledge=True).status_code==409
    assert good(confirm(leader,versions={DAY:2},acknowledge=True))['days'][0]['version']==3 and state_of(leader)['actor']=='Классный руководитель'
    assert confirm(leader,action='revoke',versions={DAY:3}).status_code==422
    revoked=good(confirm(leader,action='revoke',versions={DAY:3},comment='Нашли ошибку'));assert revoked['days'][0]['status']=='revoked' and revoked['days'][0]['version']==4
    assert confirm(leader,action='revoke',versions={DAY:4},comment='Ещё раз').status_code==409
    assert [(i['version'],i['action']) for i in trail(leader)]==[(4,'revoked'),(3,'confirmed'),(2,'changed'),(1,'confirmed')] and trail(leader)[0]['comment']=='Нашли ошибку'
    assert good(people['english'].get(GRID+'/days/history',params=dict(workspace='demo',classId=A,day=DAY)))['items']
    assert state_of(leader)['status']=='revoked'
    audit=[e['action'] for e in good(client.get('/api/access/audit'))];assert 'attendance.days_confirmed' in audit and 'attendance.days_revoked' in audit


def test_confirmation_refusals(client,monkeypatch):
    fix(monkeypatch,DAY,3)
    people=staffed(client);leader=people['leader']
    assert confirm(leader,acknowledge=True).status_code==409
    assert not state_of(leader)['expected']
    fix(monkeypatch,DAY,14)
    assert confirm(leader,[NEXT],acknowledge=True).status_code==422
    assert confirm(leader,['2026-08-01'],acknowledge=True).status_code==422
    fix(monkeypatch,LATER)
    empty=confirm(leader,['2026-08-18'],acknowledge=True);assert empty.status_code==422 and '18.08.2026' in empty.json()['detail']
    period=close_day(client)
    locked=confirm(leader,acknowledge=True);assert locked.status_code==403 and 'Первая четверть' in locked.json()['detail']
    assert period_action(client,period,'reopen',reason='Проверка').status_code==200
    # One bad day leaves the whole week unwritten.
    week=confirm(leader,[DAY,'2026-08-18'],acknowledge=True);assert week.status_code==422
    assert state_of(leader)['status']=='none' and not trail(leader)
    assert good(confirm(leader,[DAY,NEXT],acknowledge=True))['days'][1]['status']=='confirmed'
    # Taking the confirmation back is a change of the journal's day as well: a closed period refuses it.
    close_day(client)
    assert confirm(leader,action='revoke',versions={DAY:1},comment='Ошибка').status_code==403 and state_of(leader)['status']=='confirmed'


def test_withdrawal_and_grid_edit_flip_confirmation(client,clock):
    people=staffed(client);leader,admin=people['leader'],people['admin']
    english=publish(people['english'],s1=('present',0),s2=('present',0))['lessonKey'];maths=publish(people['maths'],MATHS,s1=('present',0),s3=('present',0))['lessonKey']
    good(confirm(leader));good(confirm(admin,class_id=D))
    assert state_of(leader)['status']=='confirmed' and state_of(admin,class_id=D)['status']=='confirmed'
    # s1 belongs to 10A: the shared lesson flips only 10A's day.
    publish(people['maths'],MATHS,s1=('absent',0),s3=('present',0))
    assert state_of(leader)['status']=='changed' and state_of(admin,class_id=D)['status']=='confirmed'
    good(confirm(leader,versions={DAY:2}))
    publish(people['maths'],MATHS,s1=('absent',0),s3=('late',4))
    assert state_of(leader)['status']=='confirmed' and state_of(admin,class_id=D)['status']=='changed'
    good(confirm(admin,class_id=D,versions={DAY:2}))
    # The class teacher's own edit through the grid counts like any other change.
    good(post(leader,[cell(english,'s1','present')],'late',lateMinutes=2))
    assert state_of(leader)['status']=='changed' and state_of(admin,class_id=D)['status']=='confirmed'
    good(confirm(leader,versions={DAY:4}))
    # So does withdrawing the publication: its marks are gone.
    record=entry(people['english'],english)
    assert people['english'].post('/api/journal/withdraw?workspace=demo',json=dict(lessonKey=english,version=record['version'])).status_code==200
    assert state_of(leader)['status']=='changed' and state_of(leader)['version']==6
    assert [i['action'] for i in trail(leader)][:2]==['changed','confirmed']


def test_an_accepted_note_does_not_touch_a_confirmation(client,clock):
    people=staffed(client);publish(people['english'],s1=('absent',0),s2=('present',0));publish(people['maths'],MATHS,s1=('absent',0),s3=('present',0))
    good(confirm(people['leader']));notice=send(people['parent'],body(codeId='illness'))
    assert decide(people['leader'],notice).status_code==200
    assert state_of(people['leader'])['status']=='confirmed' and state_of(people['leader'])['version']==1


def test_confirmation_permissions_and_replay(client,clock):
    people=staffed(client);publish(people['english'],s1=('present',0),s2=('present',0));publish(people['maths'],MATHS,s1=('present',0),s3=('present',0))
    for name in ['parent','student','english','maths']:assert confirm(people[name]).status_code==403,name
    staff=create(client,'staff',login='deputy');deputy=signed('deputy')
    assign(client,{A:[dict(accountId=staff['id'],capabilities=['attendance_grid'])]})
    assert confirm(deputy).status_code==200
    assert confirm(people['leader'],action='revoke',versions={DAY:1},comment='Проверка').status_code==200
    assign(client,{A:[dict(accountId=people['leaderId'],capabilities=[],revoked=['attendance_grid'])]})
    assert confirm(people['leader'],versions={DAY:2}).status_code==403
    assign(client,{})
    # A new class teacher confirms at once; the old one keeps reading the lessons he teaches but no longer confirms.
    lead('t0');rid=str(uuid4())
    assert confirm(people['leader'],versions={DAY:2}).status_code==403
    first=good(confirm(people['maths'],versions={DAY:2},requestId=rid))
    assert good(confirm(people['maths'],versions={DAY:2},requestId=rid))==first and len(trail(people['maths']))==3
    assert confirm(people['maths'],versions={DAY:2},requestId=rid,comment='другое').status_code==409
    assert [i['actor'] for i in trail(people['maths'])][:1]==['Учитель математики'] and trail(people['maths'])[-1]['actor']=='Сотрудник'


def test_day_history_is_only_for_classes_the_account_can_open(client,clock):
    people=staffed(client);publish(people['english'],s1=('present',0),s2=('present',0))
    good(confirm(people['leader'],acknowledge=True));good(confirm(people['leader'],action='revoke',versions={DAY:1},comment='Нашли ошибку'))
    resave(lambda s:s.teachers.append(Teacher(id='t9',name='Посторонний')));lead('t9',D)
    create(client,'teacher',login='outsider',grants=[dict(workspace='demo',view='teacher',entityIds=['t9'])])
    seen=lambda c,class_id=A:c.get(GRID+'/days/history',params=dict(workspace='demo',classId=class_id,day=DAY))
    # The comment and the author of a confirmation are not for a teacher who has nothing to do with the class.
    assert seen(signed('outsider')).status_code==404 and seen(people['admin'],'missing').status_code==404
    assert seen(people['parent']).status_code==403 and seen(people['student']).status_code==403
    assert [i['action'] for i in good(seen(people['leader']))['items']]==['revoked','confirmed'] and good(seen(people['english']))['items']
    assert good(seen(signed('outsider'),D))['items']==[]


def test_report_shows_confirmation_and_missing_lessons(client,clock):
    people=staffed(client);publish(people['english'],s1=('present',0),s2=('present',0));leader=people['leader']
    report=lambda c,**extra:c.post('/api/journal/attendance/report?workspace=demo',json=dict(start=DAY,end=DAY)|extra)
    first=good(report(leader));assert first['missingJournals']==1
    block=first['dayConfirmation']['classes'][0];assert (block['classId'],block['className'],block['expectedDays'],block['confirmedDays'],block['pendingDays'],block['pendingSince'])==(A,'10A',1,0,None,None)
    good(confirm(leader,acknowledge=True))
    # The page token ignores what changes beside the figures.
    assert report(leader,token=first['token']).status_code==200
    after=good(report(leader));block=after['dayConfirmation']['classes'][0]
    assert (block['confirmedDays'],block['pendingDays'],block['pendingSince'])==(1,0,None) and after['token']==first['token'] and after['totals']==first['totals']
    assert good(report(people['admin']))['missingJournals']=='not_measured' and good(report(people['admin']))['dayConfirmation'] is None
    assert good(report(people['admin'],classId=A))['missingJournals']==1
    assert good(report(leader,studentId='s1'))['missingJournals']=='not_measured'
    # A period entirely in the future has nothing to count.
    future=good(report(leader,start=NEXT,end=NEXT));assert future['missingJournals']==0 and future['dayConfirmation']['classes'][0]['expectedDays']==0
    # A subject filter narrows the uncovered lessons, not the confirmation.
    assert good(report(leader,subjectId='sub2'))['missingJournals']==0 and good(report(leader,subjectId='sub0'))['missingJournals']==1


def test_coverage_card_shows_pending_days(client,clock):
    people=staffed(client);admin=people['admin'];publish(people['english'],s1=('present',0),s2=('present',0))
    summary=lambda:good(admin.post('/api/journal/coverage/classes?workspace=demo',json=dict(start=DAY,end=DAY,mode='classes')))
    card=lambda value:next(c for c in value['classes'] if c['classId']==A)
    before=summary();assert card(before)['expectedDays']==1 and card(before)['pendingDays'] is None and card(before)['confirmedDays']==0
    settings=good(admin.get('/api/journal/coverage/reminders?workspace=demo'))
    saved=good(admin.put('/api/journal/coverage/reminders?workspace=demo',json=dict(version=settings['version'],enabled=True,confirmationReminders=True)))
    assert saved['confirmSince']==DAY
    owed=summary();assert (card(owed)['pendingDays'],card(owed)['pendingSince'])==(1,DAY)
    good(confirm(people['leader'],acknowledge=True))
    done=summary();assert (card(done)['pendingDays'],card(done)['pendingSince'],card(done)['confirmedDays'])==(0,None,1)
    assert done['token']==owed['token'] and done['counts']==owed['counts']
    text=json.dumps(done,ensure_ascii=False);assert '"s1"' not in text and 'Ученик' not in text and 'lessonKey' not in text


def test_coverage_card_does_not_judge_days_by_a_subject_filter(client,monkeypatch):
    # 09:01 at school: English (to 09:00) is over, Maths (from 09:45) is not, so the day of 10A is not over.
    fix(monkeypatch,DAY,4,1)
    people=staffed(client);admin=people['admin']
    settings=good(admin.get('/api/journal/coverage/reminders?workspace=demo'))
    good(admin.put('/api/journal/coverage/reminders?workspace=demo',json=dict(version=settings['version'],enabled=True,confirmationReminders=True)))
    summary=lambda **extra:good(admin.post('/api/journal/coverage/classes?workspace=demo',json=dict(start=DAY,end=DAY,mode='classes')|extra))
    card=lambda value:next(c for c in value['classes'] if c['classId']==A)
    assert not state_of(people['leader'])['expected']
    whole=card(summary());assert (whole['expectedDays'],whole['pendingDays'])==(0,0)
    for subject in ('sub2','sub0'):
        narrowed=card(summary(subjectId=subject))
        # The lesson counts follow the filter; the days cannot be judged from one subject's lessons, so they are not measured.
        assert [narrowed[k] for k in ('expectedDays','confirmedDays','changedDays','pendingDays','pendingSince')]==[None]*5
        assert narrowed['expected']<=whole['expected']
