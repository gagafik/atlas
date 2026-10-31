"""A held lesson is one save: everybody present by default, early leave, lateness as a share, teacher's types."""
import csv
import io
import json
from datetime import date, datetime, timezone
from uuid import uuid4
import pytest
from backend import api, journal, journal_periods
from backend.models import Student
from backend.attendance_projection import LATE_SHARE_FROM
from test_access import client, create, signed
from test_absences import setup, send, decide, body
from test_journal import DAY, lesson, entry, write, graded
from test_attendance_reports import report as school_report, details, export, pupil
from test_journal_coverage import report as coverage, english, known
from test_journal_periods import period, close
from test_grade_feed import family


def inbox(c):return c.get('/api/notifications/inbox').json()
def told(c):return [i for i in inbox(c)['items'] if i['action']=='journal.updated']
def family_report(c,student='s1'):return c.get('/api/journal/report',params=dict(workspace='demo',studentId=student,start=DAY,end=DAY)).json()
def marks(record):return {r['studentId']:r for r in record['document']['content']['records']}
def mark(record,student,**values):marks(record)[student].update(values);return record
def clock(total):return f'{total//60:02}:{total%60:02}'
def minutes(text):return int(text[:2])*60+int(text[3:])
def length(record):l=record['document']['lesson'];return minutes(l['end'])-minutes(l['start'])
def inside(record,offset):return clock(minutes(record['document']['lesson']['start'])+offset)
def save(c,record,**values):
    response=write(c,record,publish=True,**values);assert response.status_code==200,response.text
    return response.json()
def history(c,key,**params):return c.get('/api/journal/history',params=dict(workspace='demo',lessonKey=key)|params).json()
def stored():
    with api.connect() as db:return db.execute('SELECT count(*) FROM journal_entries').fetchone()[0]
def rewrite(key,change):
    """Edit the stored documents directly: how a lesson saved by an older version looks."""
    with api.connect() as db:
        for field in ['document','published_document']:
            raw=db.execute(f'SELECT {field} FROM journal_entries WHERE lesson_key=?',(key,)).fetchone()[0]
            if raw is None:continue
            document=json.loads(raw);change(document['content']['records'])
            db.execute(f'UPDATE journal_entries SET {field}=? WHERE lesson_key=?',(json.dumps(document,ensure_ascii=False),key))
def set_codes(c,active):
    codes=c.get('/api/journal/absences/codes?workspace=demo').json()
    items=[dict(item,active=item['id'] in active) for item in codes['items']]
    response=c.put('/api/journal/absences/codes?workspace=demo',json=dict(requestId=str(uuid4()),version=codes['version'],reason='Проверка',items=items))
    assert response.status_code==200,response.text


def test_opening_a_held_lesson_shows_everyone_present_but_stores_nothing(client):
    _,_,parent,teacher=setup(client);known()
    key=lesson(teacher)['lessonKey'];opened=entry(teacher,key)
    assert opened['lessonBegun'] and opened['attendanceDefaulted']
    assert {r['attendance'] for r in opened['document']['content']['records']}=={'present'}
    assert entry(teacher,key)['version']==0 and stored()==0
    assert family_report(parent)['rows']==[]
    assert english(coverage(client).json())['status']=='missing'


def test_one_save_reaches_the_family_at_once(client):
    family(client);known();parent,pupil_account,teacher=signed('parent'),signed('student'),signed('teacher')
    key=lesson(teacher)['lessonKey'];opened=entry(teacher,key)
    saved=save(teacher,opened)
    assert not saved['attendanceDefaulted'] and saved['hasPublished']
    assert family_report(parent)['summary']['attendance']['present']==1
    # Everybody present is a lesson held as usual: nobody is told about it.
    assert inbox(parent)['unread']==0 and inbox(pupil_account)['unread']==0
    assert school_report(client).json()['totals']['attendance']['present']==2
    assert english(coverage(client).json())['unmarkedPupils']==0
    rows=history(teacher,key);assert [r['action'] for r in rows]==['journal.published']


def test_correction_after_saving_is_told_and_kept_in_history(client):
    family(client);parent,teacher=signed('parent'),signed('teacher')
    key=lesson(teacher)['lessonKey'];first=save(teacher,entry(teacher,key))
    saved=save(teacher,mark(first,'s1',attendance='absent'))
    (notice,)=told(parent);assert 'отсутствие' in notice['title']
    assert len(history(teacher,key))==2
    first_edition=history(teacher,key,version=1)[0]['document']['content']['records']
    assert next(r for r in first_edition if r['studentId']=='s1')['attendance']=='present' and marks(saved)['s1']['attendance']=='absent'


def test_early_leave_notice(client):
    family(client);parent,other,teacher=signed('parent'),signed('other-parent'),signed('teacher')
    key=lesson(teacher)['lessonKey'];opened=entry(teacher,key);leave=inside(opened,length(opened)//2)
    save(teacher,mark(opened,'s1',attendance='left',leftAt=leave))
    (notice,)=told(parent);assert leave in notice['title']
    assert told(other)==[]


def test_plan_then_present_is_silent(client,monkeypatch):
    family(client);parent,teacher=signed('parent'),signed('teacher')
    monkeypatch.setattr(journal,'today',lambda:date(2026,8,16))
    key=lesson(teacher)['lessonKey'];plan=entry(teacher,key)
    plan['document']['content'].update(topic='План урока',homework='Стр. 12',dueDate='2026-08-19')
    save(teacher,plan)
    before={i['id'] for i in inbox(parent)['items']}
    monkeypatch.setattr(journal,'today',lambda:date(2026,8,17))
    held=entry(teacher,key);assert held['attendanceDefaulted']
    save(teacher,held)
    assert {i['id'] for i in inbox(parent)['items']}==before
    assert family_report(parent)['summary']['attendance']['present']==1


WORK=dict(id='work',name='Контрольная',kind='points',maxScore=20,weight=1)
def pupil_record(content,student='s1'):return next(r for r in content['records'] if r['studentId']==student)
def actions(c):return [i['action'] for i in inbox(c)['items']]


@pytest.mark.parametrize('news,parent_hears,other_hears',[
    (lambda c:c.update(topic='Тема урока'),[],[]),
    (lambda c:c.update(topic='Тема урока',homework='Стр. 12',dueDate='2026-08-19'),['journal.homework'],['journal.homework']),
    (lambda c:c.update(assessments=[WORK]),['journal.updated'],['journal.updated']),
    (lambda c:pupil_record(c).update(comment='Отлично работал на уроке'),['journal.updated'],[]),
])
def test_first_save_of_a_held_lesson_tells_only_what_is_news(client,news,parent_hears,other_hears):
    # Presence and a topic are silent; new homework, a planned work and a personal comment are told, to the families they concern.
    family(client);parent,other,teacher=signed('parent'),signed('other-parent'),signed('teacher')
    opened=entry(teacher,lesson(teacher)['lessonKey']);news(opened['document']['content']);save(teacher,opened)
    assert actions(parent)==parent_hears and actions(other)==other_hears


@pytest.mark.parametrize('assessments,heard',[([],[]),([WORK],['journal.updated'])])
def test_plan_published_ahead_is_told_when_it_names_a_work(client,monkeypatch,assessments,heard):
    family(client);parent,teacher=signed('parent'),signed('teacher')
    monkeypatch.setattr(journal,'today',lambda:date(2026,8,16))
    plan=entry(teacher,lesson(teacher)['lessonKey']);plan['document']['content'].update(topic='План урока',assessments=assessments)
    assert not plan['attendanceDefaulted'];save(teacher,plan)
    assert actions(parent)==heard


@pytest.mark.parametrize('change',[
    lambda c:c.update(topic='Другая тема'),
    lambda c:pupil_record(c).update(comment='Хорошо поработал'),
])
def test_held_lesson_differing_from_the_plan_is_told(client,monkeypatch,change):
    family(client);parent,teacher=signed('parent'),signed('teacher')
    monkeypatch.setattr(journal,'today',lambda:date(2026,8,16))
    key=lesson(teacher)['lessonKey'];plan=entry(teacher,key);plan['document']['content'].update(topic='План')
    save(teacher,plan);assert told(parent)==[]
    monkeypatch.setattr(journal,'today',lambda:date(2026,8,17))
    held=entry(teacher,key);assert held['attendanceDefaulted'];change(held['document']['content'])
    save(teacher,held);assert len(told(parent))==1


def test_mark_corrected_back_to_present_is_told(client):
    family(client);parent,teacher=signed('parent'),signed('teacher')
    key=lesson(teacher)['lessonKey'];absent=save(teacher,mark(entry(teacher,key),'s1',attendance='absent'))
    (first,)=told(parent);assert 'отсутствие' in first['title']
    save(teacher,mark(absent,'s1',attendance='present'))
    (second,)=told(parent);assert 'присутствие' in second['title']


def test_lesson_not_yet_begun_keeps_unmarked(client,monkeypatch):
    key=lesson(client)['lessonKey']
    monkeypatch.setattr(journal,'today',lambda:date(2026,8,17));monkeypatch.setattr(journal,'minute',lambda:'00:01')
    early=entry(client,key);assert not early['lessonBegun'] and not early['attendanceDefaulted']
    assert {r['attendance'] for r in early['document']['content']['records']}=={'unmarked'}
    monkeypatch.setattr(journal,'today',lambda:date(2026,8,16))
    eve=entry(client,key);assert not eve['lessonBegun'] and not eve['attendanceDefaulted']
    assert {r['attendance'] for r in eve['document']['content']['records']}=={'unmarked'}


def test_plan_published_ahead_defaults_on_the_day(client,monkeypatch):
    monkeypatch.setattr(journal,'today',lambda:date(2026,8,16))
    plan=entry(client,lesson(client)['lessonKey']);plan['document']['content'].update(topic='План',homework='Стр. 12',dueDate='2026-08-19')
    saved=save(client,plan);assert saved['hasPublished'] and not saved['attendanceDefaulted']
    monkeypatch.setattr(journal,'today',lambda:date(2026,8,17))
    opened=entry(client,saved['lessonKey']);assert opened['attendanceDefaulted']
    assert {r['attendance'] for r in opened['document']['content']['records']}=={'present'}


def test_partly_marked_lesson_is_not_defaulted(client,monkeypatch):
    # Pinned to the evening of the lesson day: later days keep the roster a lesson was published with.
    monkeypatch.setattr(journal,'today',lambda:date(2026,8,17))
    key=lesson(client)['lessonKey'];save(client,graded(entry(client,key)))
    state=api.read('demo')
    state.students.append(Student(id='new',name='New pupil',classId='c-demo-a'));api.save('demo',state)
    synced=entry(client,key,syncRoster=True)
    assert not synced['attendanceDefaulted'] and marks(synced)['new']['attendance']=='unmarked'


def test_approved_note_defaults_to_absent(client):
    _,_,parent,teacher=setup(client);notice=send(parent);assert decide(teacher,notice).status_code==200
    opened=entry(teacher,lesson(teacher)['lessonKey'])
    assert marks(opened)['s1']['attendance']=='absent' and marks(opened)['s2']['attendance']=='present'
    save(teacher,opened)
    assert family_report(parent)['summary']['attendance']['excused']==1


def test_closed_period_shows_stored_marks(client,monkeypatch):
    stamp=datetime(2026,10,14,10,tzinfo=timezone.utc)
    monkeypatch.setattr(journal_periods,'now',lambda:stamp);monkeypatch.setattr('backend.journal_coverage.now',lambda:stamp)
    known();close(client,period(client))
    locked=entry(client,lesson(client)['lessonKey'])
    assert not locked['writable'] and not locked['attendanceDefaulted']
    assert {r['attendance'] for r in locked['document']['content']['records']}=={'unmarked'}


def test_editor_default_skips_former_pupils_and_a_roster_waiting_for_review(client,monkeypatch):
    monkeypatch.setattr(journal,'today',lambda:date(2026,8,17))
    key=lesson(client,'Математика')['lessonKey'];plan=entry(client,key)
    for r in plan['document']['content']['records']:r['attendance']='unmarked'
    assert write(client,plan).status_code==200
    state=api.read('demo');next(g for g in state.groups if g.id=='g-math').studentIds=['s2','s3'];api.save('demo',state)
    stale=entry(client,key)
    assert stale['needsSync'] and not stale['attendanceDefaulted']
    assert {r['attendance'] for r in stale['document']['content']['records']}=={'unmarked'}
    synced=entry(client,key,syncRoster=True)
    assert {s['id']:s['enrolled'] for s in synced['document']['lesson']['students']}=={'s1':False,'s2':True,'s3':True}
    assert synced['attendanceDefaulted']
    assert {i:r['attendance'] for i,r in marks(synced).items()}=={'s1':'unmarked','s2':'present','s3':'present'}


def test_editor_offers_the_absence_types_of_the_school(client):
    key=lesson(client)['lessonKey']
    assert {c['id']:c['active'] for c in entry(client,key)['absenceCodes']}=={'illness':True,'family':True,'school':True,'other':True}
    set_codes(client,active={'family','school','other'})
    codes=entry(client,key)['absenceCodes']
    assert {c['id']:c['active'] for c in codes}=={'illness':False,'family':True,'school':True,'other':True}
    assert all(c['name'] for c in codes)


def test_excused_mark_comes_only_from_a_note(client):
    _,_,parent,teacher=setup(client)
    key=lesson(teacher)['lessonKey'];opened=mark(entry(teacher,key),'s1',attendance='excused')
    for account in [teacher,client]:assert write(account,opened,publish=True).status_code==422
    assert entry(teacher,key)['version']==0 and stored()==0
    # A mark saved by an older version still round-trips, and can be replaced but not chosen again.
    save(teacher,entry(teacher,key))
    def old_excused(records):
        for r in records:
            r.pop('leftAt');r.pop('absenceCode')
            if r['studentId']=='s1':r['attendance']='excused'
    rewrite(key,old_excused)
    assert family_report(parent)['summary']['attendance']['excused']==1
    kept=entry(teacher,key);assert marks(kept)['s1']['attendance']=='excused'
    kept['document']['content']['topic']='Другая тема';kept=save(teacher,kept)
    assert family_report(parent)['summary']['attendance']['excused']==1
    changed=save(teacher,mark(kept,'s1',attendance='absent'))
    assert family_report(parent)['summary']['attendance']['excused']==0
    assert write(teacher,mark(changed,'s1',attendance='excused'),publish=True).status_code==422


def test_early_leave_needs_a_time_inside_the_lesson(client):
    key=lesson(client)['lessonKey'];opened=entry(client,key);ls=opened['document']['lesson']
    for values in [dict(attendance='left',leftAt=None),dict(attendance='present',leftAt=inside(opened,10)),
                   dict(attendance='left',leftAt=ls['start']),dict(attendance='left',leftAt=ls['end']),dict(attendance='left',leftAt=clock(minutes(ls['end'])+30)),
                   dict(attendance='left',leftAt='25:00')]:
        assert write(client,mark(entry(client,key),'s1',**values),publish=True).status_code==422,values
    assert stored()==0
    fine=mark(entry(client,key),'s1',attendance='left',leftAt=inside(opened,length(opened)-5));assert write(client,fine,publish=True).status_code==200


def test_absence_codes_on_marks(client):
    _,_,parent,teacher=setup(client)
    key=lesson(teacher)['lessonKey'];opened=entry(teacher,key)
    assert write(teacher,mark(opened,'s1',attendance='absent',absenceCode='no-such-type'),publish=True).status_code==422
    assert write(teacher,mark(opened,'s1',attendance='present',absenceCode='illness'),publish=True).status_code==422
    saved=save(teacher,mark(opened,'s1',attendance='absent',absenceCode='illness'))
    assert family_report(parent)['rows'][0]['record']['absenceCode']=='illness'
    set_codes(client,active={'family','school','other'})
    # The type stays on the lesson it was chosen for, also after the school switched it off ...
    again=save(teacher,saved);assert marks(again)['s1']['absenceCode']=='illness'
    # ... but nobody can choose it anew.
    assert write(teacher,mark(again,'s2',attendance='absent',absenceCode='illness'),publish=True).status_code==422
    assert write(teacher,mark(again,'s2',attendance='absent',absenceCode='family'),publish=True).status_code==200


def test_old_document_republished_tells_nobody(client):
    family(client);parent,teacher=signed('parent'),signed('teacher')
    key=lesson(teacher)['lessonKey'];save(teacher,graded(entry(teacher,key)))
    rewrite(key,lambda records:[[r.pop(k) for k in ['leftAt','absenceCode']] for r in records])
    before={i['id'] for i in inbox(parent)['items']};unread=inbox(parent)['unread']
    save(teacher,entry(teacher,key))
    assert {i['id'] for i in inbox(parent)['items']}==before and inbox(parent)['unread']==unread


def test_early_leave_counts_in_reports_and_coverage(client):
    _,_,parent,teacher=setup(client);known()
    key=lesson(teacher)['lessonKey'];opened=entry(teacher,key);leave=inside(opened,length(opened)//2)
    opened['document']['content']['topic']='Тема урока'
    save(teacher,mark(opened,'s1',attendance='left',leftAt=leave))
    totals=school_report(teacher).json()['totals']['attendance'];assert totals['left']==1 and totals['absent']==0
    assert details(teacher).json()['items'][0]['leftAt']==leave
    assert [i['rawAttendance'] for i in details(teacher,attendance='left').json()['items']]==['left']
    assert details(teacher,studentId='s2',attendance='left').json()['items']==[]
    row=english(coverage(client).json());assert row['unmarkedPupils']==0 and row['status']=='complete'


def test_late_share_in_both_reports(client):
    _,_,parent,teacher=setup(client)
    key=lesson(teacher)['lessonKey'];opened=entry(teacher,key);total=length(opened)
    assert total%4==0
    saved=opened
    for late,share in [(total//4,0.25),(LATE_SHARE_FROM-1,0),(LATE_SHARE_FROM,round(LATE_SHARE_FROM/total,2)),(total+30,1)]:
        saved=save(teacher,mark(saved,'s1',attendance='late',lateMinutes=late))
        school=school_report(teacher).json()['totals'];personal=family_report(parent)['summary']
        assert school['lateMinutes']==personal['lateMinutes']==late
        assert school['lateShare']==personal['lateShare']==share
        assert pupil(school_report(teacher).json())['lateShare']==share
    save(teacher,mark(saved,'s1',attendance='late',lateMinutes=total//4))
    rows=list(csv.reader(io.StringIO(export(teacher).content.decode('utf-8-sig')),delimiter=';'))
    assert {r[rows[0].index('ID ученика')]:float(r[rows[0].index('Опоздания в долях урока')]) for r in rows[1:]}=={'s1':0.25,'s2':0}


def test_note_code_wins_over_teacher_code(client):
    _,_,parent,teacher=setup(client)
    key=lesson(teacher)['lessonKey'];save(teacher,mark(entry(teacher,key),'s1',attendance='absent',absenceCode='family'))
    assert pupil(school_report(teacher).json())['absentCodes']=={'family':1}
    approved=decide(teacher,send(parent,body(codeId='illness')));assert approved.status_code==200
    value=school_report(teacher).json();p=pupil(value)
    assert p['attendance']['excused']==1 and p['attendance']['absent']==0 and p['excusedCodes']=={'illness':1}
    assert p['absentCodes']=={} and value['totals']['absentCodes']=={}
    item=details(teacher).json()['items'][0];assert item['absenceCode']=='illness' and item['markCode']=='family'
    assert decide(teacher,approved.json(),'revoke').status_code==200
    value=school_report(teacher).json();assert pupil(value)['absentCodes']=={'family':1} and pupil(value)['attendance']['absent']==1


def test_projection_rebuilds_from_old_version(client):
    _,_,parent,teacher=setup(client)
    key=lesson(teacher)['lessonKey'];save(teacher,graded(entry(teacher,key)))
    with api.connect() as db:
        db.execute('DROP TABLE journal_attendance')
        db.execute('''CREATE TABLE journal_attendance (workspace TEXT,lesson_key TEXT,student_id TEXT,attendance TEXT,late_minutes INTEGER,
            day TEXT,start TEXT,end TEXT,subject_id TEXT,PRIMARY KEY(workspace,lesson_key,student_id)) WITHOUT ROWID''')
        db.execute("UPDATE journal_projection_versions SET version=2 WHERE name='attendance'")
    assert school_report(teacher).json()['totals']['records']==2
    assert family_report(parent)['summary']['attendance']['present']==1


def test_csv_has_early_leave_and_code_columns(client):
    _,_,parent,teacher=setup(client)
    key=lesson(teacher)['lessonKey'];opened=entry(teacher,key);leave=inside(opened,length(opened)//2)
    save(teacher,mark(mark(opened,'s1',attendance='left',leftAt=leave),'s2',attendance='absent',absenceCode='family'))
    rows=list(csv.reader(io.StringIO(export(teacher).content.decode('utf-8-sig')),delimiter=';'));header=rows[0]
    def column(name):return {r[header.index('ID ученика')]:r[header.index(name)] for r in rows[1:]}
    assert column('Ушёл раньше')=={'s1':'1','s2':'0'}
    assert column('Отсутствует без записки: Семейные обстоятельства')=={'s1':'0','s2':'1'}
    assert header[-1]=='Уважительная причина: без типа' and header.index('Минут опоздания')<header.index('Ушёл раньше')
    lessons=list(csv.reader(io.StringIO(export(teacher,studentId='s1',view='lessons').content.decode('utf-8-sig')),delimiter=';'))
    assert lessons[1][lessons[0].index('Ушёл в')]==leave and lessons[0][-1]=='Тип отсутствия'
    kept=list(csv.reader(io.StringIO(export(teacher,studentId='s2',view='lessons').content.decode('utf-8-sig')),delimiter=';'))
    assert kept[1][kept[0].index('Тип по отметке учителя')]=='Семейные обстоятельства'


def test_family_report_shows_own_mark_only(client):
    family(client);parent,other,teacher=signed('parent'),signed('other-parent'),signed('teacher')
    key=lesson(teacher)['lessonKey'];opened=entry(teacher,key);leave=inside(opened,length(opened)//2)
    save(teacher,mark(mark(opened,'s1',attendance='left',leftAt=leave),'s2',attendance='absent',absenceCode='family'))
    own=family_report(parent);assert own['rows'][0]['record']['leftAt']==leave and {r['student']['id'] for r in own['rows']}=={'s1'}
    theirs=family_report(other,'s2');assert theirs['rows'][0]['record']['absenceCode']=='family' and {r['student']['id'] for r in theirs['rows']}=={'s2'}
    assert 'family' in theirs['absenceCodes'] and own['summary']['attendance']['left']==1
    # A teacher of another lesson still gets nothing about this one.
    assert family_report(signed('unrelated'))['rows']==[]
