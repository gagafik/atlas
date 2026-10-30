from contextlib import closing
from datetime import date,datetime,timezone
import csv,io,sqlite3,threading
import pytest
from fastapi.testclient import TestClient
from backend import api,coverage_reminders,journal_coverage
from backend.coverage_reminders import DEFAULT_SETTINGS,reminder_day,school_day,settings,tables,windows
from backend.models import DatedLesson,Event,Placement,ScheduleDay,Student
from backend.seed import initial_state
from test_access import client,enable,create,signed,HEADERS
from test_journal import DAY,lesson,entry,write,graded
from test_journal_coverage import query,report,details,export,known,english

URL='/api/journal/coverage/reminders?workspace=demo'
MON,FRI=date(2026,8,17),date(2026,8,21)


def clock(monkeypatch,day,hour=12,minute=0):
    """Fix the module clocks; times are UTC, school time is five hours ahead."""
    value=datetime(day.year,day.month,day.day,hour,minute,tzinfo=timezone.utc)
    monkeypatch.setattr(coverage_reminders,'now',lambda:value)
    monkeypatch.setattr(journal_coverage,'now',lambda:value)


def settings_body(version=0,**values):return dict(version=version,enabled=True)|values
def turn_on(client,monkeypatch,version=0,**values):
    clock(monkeypatch,MON,2,0);response=client.put(URL,json=settings_body(version,**values));assert response.status_code==200,response.text;return response.json()
def inbox(client,action):return [i for i in client.get('/api/notifications/inbox').json()['items'] if i['action']==action]
def people(client):
    """The admin (client), the English teacher t2 and the physics teacher t1; the maths teacher t0 has no account."""
    enable(client);create(client,'teacher');create(client,'teacher',login='other-teacher',grants=[dict(workspace='demo',view='teacher',entityIds=['t1'])])
    return signed('teacher'),signed('other-teacher')
def stored(sql,*args):
    with api.connect() as db:return db.execute(sql,args).fetchall()
def friday_only(*events):
    state=api.read('demo');state.placements=[Placement(id='fri',workloadId='w0',day=4,period=1,roomId='r0')];state.events=list(events);api.save('demo',state)
def calendar(*events,**values):
    state=initial_state('demo');state.events=list(events)
    for k,v in values.items():setattr(state,k,v)
    return state


def test_settings_admin_only_versioned_history(client,monkeypatch):
    enable(client)
    for role in ['teacher','staff','parent','student']:
        create(client,role);other=signed(role)
        assert other.get(URL).status_code==403 and other.put(URL,json=settings_body()).status_code==403,role
    first=client.get(URL).json();assert first['enabled'] is False and first['afterDays']==2 and first['remindAt']=='16:00' and first['version']==0 and first['since'] is None and first['lastRuns']==[]
    clock(monkeypatch,MON,2,0)
    saved=client.put(URL,json=settings_body(0,reason='Начинаем'));assert saved.status_code==200,saved.text
    assert saved.json()['version']==1 and saved.json()['since']=='2026-08-17' and saved.json()['enabled']
    assert client.put(URL,json=settings_body(0)).status_code==409
    assert client.put(URL,json=settings_body(1,afterDays=0)).status_code==422
    assert client.put(URL,json=settings_body(1,remindAt='25:00')).status_code==422
    assert client.put(URL,json=settings_body(1,lessonReminders=False,weeklyDigest=False)).status_code==422
    history=stored('SELECT version,actor_name,reason,before_document,after_document FROM journal_coverage_settings_history')
    assert len(history)==1 and history[0][0]==1 and history[0][2]=='Начинаем' and '"enabled":false' in history[0][3] and '"enabled":true' in history[0][4]
    audit=[e for e in client.get('/api/access/audit').json() if e['action']=='journal.coverage_reminders_saved'];assert len(audit)==1
    assert client.get(URL).json()['version']==1
    # Each workspace has its own settings.
    assert client.get('/api/journal/coverage/reminders?workspace=school').json()['version']==0


def test_reenabling_resets_since(client,monkeypatch):
    enable(client);turn_on(client,monkeypatch)
    clock(monkeypatch,date(2026,9,1),2,0);assert client.put(URL,json=settings_body(1,afterDays=3)).json()['since']=='2026-08-17'
    assert client.put(URL,json=settings_body(2,enabled=False)).json()['since']=='2026-08-17'
    clock(monkeypatch,date(2026,9,10),2,0);assert client.put(URL,json=settings_body(3)).json()['since']=='2026-09-10'


def test_migration_is_idempotent(tmp_path):
    with closing(sqlite3.connect(tmp_path/'empty.db')) as db:
        assert settings(db,'demo')==DEFAULT_SETTINGS|dict(version=0)
        tables(db);tables(db)
        assert settings(db,'demo')==DEFAULT_SETTINGS|dict(version=0)
        db.execute("INSERT INTO journal_coverage_reminders VALUES ('demo','k','t2','2026-08-19','sent','x')");tables(db)
        assert db.execute('SELECT count(*) FROM journal_coverage_reminders').fetchone()==(1,)
    assert DEFAULT_SETTINGS['enabled'] is False and DEFAULT_SETTINGS['afterDays']==2 and DEFAULT_SETTINGS['remindAt']=='16:00' and DEFAULT_SETTINGS['digestWeekday']==4


def test_school_days_skip_weekends_holidays_and_follow_schedule_days():
    plain=calendar()
    assert reminder_day(plain,FRI,2)==date(2026,8,25) and reminder_day(plain,MON,2)==date(2026,8,19)
    assert not school_day(plain,date(2026,8,22)) and school_day(plain,date(2026,8,24)) and not school_day(plain,date(2026,8,16))
    holiday=calendar(Event(id='h',name='Праздник',kind='holiday',effect='note',start=date(2026,8,24),end=date(2026,8,24)))
    assert reminder_day(holiday,FRI,2)==date(2026,8,26)
    winter=calendar(Event(id='w',name='Каникулы',kind='winter',effect='note',start=date(2026,8,24),end=date(2026,8,25)))
    assert reminder_day(winter,FRI,1)==date(2026,8,26)
    closed=calendar(Event(id='x',name='Санитарный день',kind='extra',effect='cancel',start=date(2026,8,24),end=date(2026,8,24)))
    assert reminder_day(closed,FRI,2)==date(2026,8,26)
    # Closing some classes only keeps the day a school day.
    partial=calendar(Event(id='p',name='Часть классов',kind='extra',effect='cancel',start=date(2026,8,24),end=date(2026,8,24),classIds=['c-demo-a']))
    assert reminder_day(partial,FRI,2)==date(2026,8,25)
    saturday=calendar(scheduleDays=[ScheduleDay(id='sd',name='Суббота по расписанию понедельника',onDate=date(2026,8,22),day=0)])
    assert school_day(saturday,date(2026,8,22)) and reminder_day(saturday,FRI,1)==date(2026,8,22)
    assert reminder_day(plain,plain.settings.yearEnd-(date(2026,8,18)-date(2026,8,17)),5) is None


def test_windows_follow_since_grace_and_digest_range():
    state=calendar();config=DEFAULT_SETTINGS|dict(version=1,enabled=True)
    assert windows(state,date(2026,8,19),config)==(set(),set())
    lessons,digest=windows(state,date(2026,8,19),config|dict(since='2026-08-17'));assert lessons=={MON} and digest=={MON}
    lessons,digest=windows(state,date(2026,8,30),config|dict(since='2026-08-17'));assert MON not in lessons and MON in digest
    assert windows(state,date(2026,8,19),config|dict(since='2026-08-18'))==(set(),set())
    # A long gap is cut to the scan window and never reaches before the year.
    lessons,digest=windows(state,date(2026,12,1),config|dict(since='2026-08-17'));assert min(digest)>=date(2026,11,3) and min(lessons)>=date(2026,11,18)


def test_internal_fields_never_leave_the_server(client):
    api.journal_coverage.cache.clear();known();value=report(client).json();row=english(value)
    assert {'classIds','teacherIds','sourceDate'}.isdisjoint(row) and set(row)==set(journal_coverage.KEYS)
    assert set(details(client,row['lessonKey']).json()['lesson'])==set(journal_coverage.KEYS)
    text=export(client).content.decode('utf-8-sig');header=next(csv.reader(io.StringIO(text),delimiter=';'))
    assert len(header)==15 and 'classIds' not in text and 'teacherIds' not in text and 'sourceDate' not in text
    cached=next(r for r in next(iter(api.journal_coverage.cache.values()))[1]['rows'] if r[0]==row['lessonKey'])
    assert len(cached)==len(journal_coverage.KEYS)+3 and cached[journal_coverage.CLASS_IDS]==('c-demo-a',) and cached[journal_coverage.TEACHER_IDS]==('t2',) and cached[journal_coverage.SOURCE]==DAY


def test_reminder_after_n_school_days_once(client,monkeypatch):
    teacher,_=people(client);turn_on(client,monkeypatch);worker=api.journal_coverage.reminders
    clock(monkeypatch,date(2026,8,18));assert not worker.process_one() and not inbox(teacher,'journal.coverage_reminder')
    clock(monkeypatch,date(2026,8,19),10,30);assert not worker.process_one()
    clock(monkeypatch,date(2026,8,19),11,30);assert worker.process_one()
    notices=inbox(teacher,'journal.coverage_reminder');assert len(notices)==1
    text=notices[0]['title'];assert '17.08.2026' in text and '10A' in text and 'Английский язык' in text and 'Ученик' not in text and 'Не заполнено уроков: 1' in text
    assert not worker.process_one()
    clock(monkeypatch,date(2026,8,20));assert not worker.process_one() and len(inbox(teacher,'journal.coverage_reminder'))==1
    assert dict(stored('SELECT teacher_id,status FROM journal_coverage_reminders'))=={'t2':'sent','t0':'skipped'}
    assert len(stored('SELECT 1 FROM journal_coverage_reminders'))==2
    runs=stored('SELECT on_date,kind,detail FROM journal_coverage_runs ORDER BY on_date');assert ('2026-08-19','lessons','{"lessons":2,"sent":1,"skipped":1}') in runs
    audit=[e for e in client.get('/api/access/audit').json() if e['action']=='journal.coverage_reminders_sent'];assert len(audit)==1 and audit[0]['actorName']=='Атлас'
    assert not inbox(signed('other-teacher'),'journal.coverage_reminder')


def test_weekend_waits_and_monday_catches_up(client,monkeypatch):
    teacher,_=people(client);turn_on(client,monkeypatch);worker=api.journal_coverage.reminders
    clock(monkeypatch,date(2026,8,22));assert not worker.process_one() and not inbox(teacher,'journal.coverage_reminder')
    assert stored('SELECT kind,detail FROM journal_coverage_runs')==[('lessons','{"note":"not a school day"}')]
    clock(monkeypatch,date(2026,8,24),11,30);assert worker.process_one()
    assert '17.08.2026' in inbox(teacher,'journal.coverage_reminder')[0]['title']


def test_friday_lesson_is_reminded_after_the_weekend(client,monkeypatch):
    teacher,_=people(client);turn_on(client,monkeypatch);friday_only();worker=api.journal_coverage.reminders
    for day in [date(2026,8,21),date(2026,8,22),date(2026,8,23),date(2026,8,24)]:
        clock(monkeypatch,day,11,30);assert not worker.process_one() and not inbox(teacher,'journal.coverage_reminder'),day
    clock(monkeypatch,date(2026,8,25),11,30);assert worker.process_one()
    assert '21.08.2026' in inbox(teacher,'journal.coverage_reminder')[0]['title']


def test_holiday_moves_the_reminder(client,monkeypatch):
    teacher,_=people(client);turn_on(client,monkeypatch)
    friday_only(Event(id='h',name='Праздник',kind='holiday',effect='note',start=date(2026,8,24),end=date(2026,8,24)));worker=api.journal_coverage.reminders
    clock(monkeypatch,date(2026,8,25),11,30);assert not worker.process_one() and not inbox(teacher,'journal.coverage_reminder')
    clock(monkeypatch,date(2026,8,26),11,30);assert worker.process_one()
    assert '21.08.2026' in inbox(teacher,'journal.coverage_reminder')[0]['title']


def test_filled_cancelled_and_closed_lessons_are_not_reminded(client,monkeypatch):
    teacher,_=people(client);known();turn_on(client,monkeypatch);worker=api.journal_coverage.reminders
    key=lesson(client)['lessonKey'];write(client,graded(entry(client,key)),publish=True)
    clock(monkeypatch,date(2026,8,19),11,30);assert worker.process_one()
    assert not inbox(teacher,'journal.coverage_reminder') and stored('SELECT teacher_id,status FROM journal_coverage_reminders')==[('t0','skipped')]
    # Both lessons of a whole-school cancellation vanish.
    state=api.read('demo');state.events=[Event(id='c',name='Отмена',kind='extra',effect='cancel',start=MON,end=MON,classIds=[])];api.save('demo',state)
    with api.connect() as db:db.execute('DELETE FROM journal_coverage_reminders');db.execute('DELETE FROM journal_coverage_runs')
    assert not worker.process_one() and not stored('SELECT 1 FROM journal_coverage_reminders')
    # A closed period holds its lessons; a lesson moved out of it is held by its source day.
    with api.connect() as db:db.execute('DELETE FROM journal_coverage_runs')
    state=api.read('demo');state.events=[];state.datedLessons=[DatedLesson(id='mv',name='Перенос',kind='change',placementId='eng',sourceDate=MON,onDate=date(2026,8,18),startTime='14:00',endTime='14:40')];api.save('demo',state)
    with api.connect() as db:db.execute('DELETE FROM journal_entries');db.execute('DELETE FROM journal_coverage_documents')
    clock(monkeypatch,date(2026,8,20),11,30);snap=api.journal_coverage.absences.snapshots.get('demo')
    moved=worker.overdue(snap,'demo',date(2026,8,18),date(2026,8,18));assert [(r['subject'],r['sourceDate']) for r in moved]==[('Английский язык','2026-08-17')]
    with api.connect() as db:db.execute("INSERT INTO journal_periods VALUES ('demo','p1','Q1',?,?,'quarter',1,'closed',1,'x')",(DAY,DAY))
    assert worker.overdue(snap,'demo',date(2026,8,18),date(2026,8,18))==[] and worker.overdue(snap,'demo',MON,MON)==[]
    assert not worker.process_one() and not stored('SELECT 1 FROM journal_coverage_reminders')
    with api.connect() as db:db.execute("UPDATE journal_periods SET start_day='2026-08-18',end_day='2026-08-18'")
    assert [r['subject'] for r in worker.overdue(snap,'demo',date(2026,8,18),date(2026,8,18))]==[] and [r['subject'] for r in worker.overdue(snap,'demo',MON,MON)]==['Математика']


def test_unknown_class_size_alone_never_reminds(client,monkeypatch):
    teacher,_=people(client);turn_on(client,monkeypatch);worker=api.journal_coverage.reminders
    key=lesson(client)['lessonKey'];assert write(client,graded(entry(client,key)),publish=True).status_code==200
    clock(monkeypatch,date(2026,8,19),11,30);row=english(report(client).json());assert row['status']=='incomplete' and row['rosterUnknown'] and row['topicFilled'] and not row['missingPupils']
    worker.process_one()
    assert not inbox(teacher,'journal.coverage_reminder') and [r[0] for r in stored('SELECT teacher_id FROM journal_coverage_reminders')]==['t0']


def test_missing_topic_or_marks_still_remind(client,monkeypatch):
    teacher,_=people(client);known();turn_on(client,monkeypatch);worker=api.journal_coverage.reminders
    key=lesson(client)['lessonKey'];assert write(client,entry(client,key),publish=True).status_code==200
    clock(monkeypatch,date(2026,8,19),11,30);assert english(report(client).json())['status']=='incomplete'
    assert worker.process_one()
    assert len(inbox(teacher,'journal.coverage_reminder'))==1


def test_substitute_receives_reminder(client,monkeypatch):
    teacher,other=people(client);turn_on(client,monkeypatch);worker=api.journal_coverage.reminders
    state=api.read('demo');state.datedLessons=[DatedLesson(id='sub',name='Замена',kind='change',placementId='eng',sourceDate=MON,onDate=MON,teacherIds=['t1'])];api.save('demo',state)
    clock(monkeypatch,date(2026,8,19),11,30);assert worker.process_one()
    assert len(inbox(other,'journal.coverage_reminder'))==1 and not inbox(teacher,'journal.coverage_reminder')
    assert dict(stored('SELECT teacher_id,status FROM journal_coverage_reminders'))=={'t1':'sent','t0':'skipped'}


def test_frozen_publication_is_not_reminded_after_roster_change(client,monkeypatch):
    teacher,_=people(client);known();turn_on(client,monkeypatch);worker=api.journal_coverage.reminders
    key=lesson(client)['lessonKey'];write(client,graded(entry(client,key)),publish=True)
    state=api.read('demo');state.students.append(Student(id='new',name='New pupil',classId='c-demo-a'));api.save('demo',state)
    clock(monkeypatch,date(2026,8,19),11,30);worker.process_one()
    assert english(report(client,asOf='2026-08-19T11:30:00Z').json())['status']=='complete'
    assert not inbox(teacher,'journal.coverage_reminder') and [r[0] for r in stored('SELECT teacher_id FROM journal_coverage_reminders')]==['t0']


def test_lesson_published_between_read_and_write_is_skipped(client,monkeypatch):
    teacher,_=people(client);known();turn_on(client,monkeypatch);worker=api.journal_coverage.reminders
    key=lesson(client)['lessonKey'];original=api.journal_coverage.calculate;calls=[]
    def publish_meanwhile(*args,**kwargs):
        value=original(*args,**kwargs)
        if not calls:calls.append(1);assert write(client,graded(entry(client,key)),publish=True).status_code==200
        return value
    monkeypatch.setattr(api.journal_coverage,'calculate',publish_meanwhile)
    clock(monkeypatch,date(2026,8,19),11,30);worker.process_one()
    assert calls and not inbox(teacher,'journal.coverage_reminder') and [r[0] for r in stored('SELECT teacher_id FROM journal_coverage_reminders')]==['t0']
    clock(monkeypatch,date(2026,8,20),11,30);worker.process_one()
    assert not inbox(teacher,'journal.coverage_reminder') and [r[0] for r in stored('SELECT teacher_id FROM journal_coverage_reminders')]==['t0']


def test_changed_revision_or_settings_between_read_and_write_sends_nothing(client,monkeypatch):
    teacher,_=people(client);turn_on(client,monkeypatch);worker=api.journal_coverage.reminders;original=api.journal_coverage.calculate;changes=[]
    def change_meanwhile(*args,**kwargs):
        value=original(*args,**kwargs)
        if changes:changes.pop()()
        return value
    monkeypatch.setattr(api.journal_coverage,'calculate',change_meanwhile)
    clock(monkeypatch,date(2026,8,19),11,30)
    def rename():
        state=api.read('demo');state.settings.name='Другое название';api.save('demo',state)
    changes.append(rename);assert not worker.process_one() and not stored('SELECT 1 FROM journal_coverage_reminders') and not stored('SELECT 1 FROM journal_coverage_runs')
    changes.append(lambda:client.put(URL,json=settings_body(1,afterDays=3)));assert not worker.process_one() and not stored('SELECT 1 FROM journal_coverage_reminders')
    changes.clear();clock(monkeypatch,date(2026,8,21),11,30);assert worker.process_one() and len(inbox(teacher,'journal.coverage_reminder'))==1


def test_worker_starts_and_stops_with_the_application(client):
    alive=lambda:any(t.name=='atlas-coverage-reminders' and t.is_alive() for t in threading.enumerate())
    with TestClient(api.app,headers=HEADERS) as running:
        assert running.get('/api/health').status_code==200 and alive()
    assert not alive()


def test_disabled_or_before_since(client,monkeypatch):
    teacher,_=people(client);worker=api.journal_coverage.reminders
    clock(monkeypatch,date(2026,8,19),11,30);assert not worker.process_one() and not stored('SELECT 1 FROM journal_coverage_reminders')
    clock(monkeypatch,date(2026,8,25),2,0);assert client.put(URL,json=settings_body(0)).json()['since']=='2026-08-25'
    clock(monkeypatch,date(2026,8,26),11,30);assert not worker.process_one() and not stored('SELECT 1 FROM journal_coverage_reminders')
    clock(monkeypatch,date(2026,9,2),11,30);assert worker.process_one()
    assert '31.08.2026' in inbox(teacher,'journal.coverage_reminder')[0]['title'] and not stored("SELECT 1 FROM journal_coverage_reminders WHERE lesson_key IN (SELECT lesson_key FROM journal_coverage_reminders WHERE day<'2026-09-02')")
    # Switching off stops everything.
    assert client.put(URL,json=settings_body(1,enabled=False)).status_code==200
    clock(monkeypatch,date(2026,9,9),11,30);assert not worker.process_one()


def test_lesson_reminders_off_writes_no_lesson_rows(client,monkeypatch):
    teacher,_=people(client);turn_on(client,monkeypatch,lessonReminders=False,weeklyDigest=True);worker=api.journal_coverage.reminders
    clock(monkeypatch,date(2026,8,19),11,30);assert not worker.process_one() and not stored('SELECT 1 FROM journal_coverage_reminders')


def test_weekly_digest(client,monkeypatch):
    teacher,_=people(client);turn_on(client,monkeypatch,lessonReminders=False,weeklyDigest=True,digestWeekday=4);worker=api.journal_coverage.reminders
    clock(monkeypatch,date(2026,8,20),11,30);assert not worker.process_one() and not inbox(teacher,'journal.coverage_digest')
    clock(monkeypatch,date(2026,8,21),11,30);assert worker.process_one()
    digest=inbox(teacher,'journal.coverage_digest');assert len(digest)==1 and 'уроков: 1' in digest[0]['title'] and '17.08.2026' in digest[0]['title'] and 'За 4 недели' in digest[0]['title']
    assert not inbox(teacher,'journal.coverage_reminder') and not stored('SELECT 1 FROM journal_coverage_reminders') and not worker.process_one()
    assert stored('SELECT kind,detail FROM journal_coverage_runs')==[('digest','{"lessons":2,"sent":1,"withoutAccount":1}')]
    clock(monkeypatch,date(2026,8,28),11,30);assert worker.process_one()
    again=inbox(teacher,'journal.coverage_digest');assert len(again)==1 and 'уроков: 2' in again[0]['title'] and again[0]['id']!=digest[0]['id']
    # The two kinds are independent: a teacher without lessons due gets no digest.
    assert not inbox(signed('other-teacher'),'journal.coverage_digest')


def test_lessons_and_digest_on_the_same_day(client,monkeypatch):
    teacher,_=people(client);turn_on(client,monkeypatch,weeklyDigest=True,digestWeekday=2);worker=api.journal_coverage.reminders
    clock(monkeypatch,date(2026,8,19),11,30);assert worker.process_one()
    assert len(inbox(teacher,'journal.coverage_reminder'))==1 and len(inbox(teacher,'journal.coverage_digest'))==1
    assert {r[0] for r in stored('SELECT kind FROM journal_coverage_runs')}=={'lessons','digest'}


def test_options_expose_reminder_state(client,monkeypatch):
    teacher,_=people(client)
    admin=client.get('/api/journal/coverage/options?workspace=demo').json();assert admin['canConfigure'] is True and admin['reminders']==dict(enabled=False,afterDays=2,weeklyDigest=False)
    turn_on(client,monkeypatch,afterDays=3)
    assert signed('teacher').get('/api/journal/coverage/options?workspace=demo').json()['reminders']==dict(enabled=True,afterDays=3,weeklyDigest=False)
    assert signed('teacher').get('/api/journal/coverage/options?workspace=demo').json()['canConfigure'] is False


def summary(client,**extra):return client.post('/api/journal/coverage/classes?workspace=demo',json=query(mode='classes',asOf='2026-08-17T18:00:00Z')|extra)
def by_class(value):return {c['className']:c for c in value['classes']}


def test_class_summary_numbers_scope_and_shared_snapshot(client,monkeypatch):
    people(client);known();value=summary(client);assert value.status_code==200,value.text;value=value.json();classes=by_class(value)
    a,d=classes['10A'],classes['10D']
    assert [c['className'] for c in value['classes']]==['10A','10D'] and a['classTeacher'] is None
    assert (a['expected'],a['unfilled'],a['missing'],a['unbound'],a['lastRecord'],a['unfilledSince'])==(2,2,2,1,None,'2026-08-17')
    assert (d['expected'],d['unfilled'],d['unbound'])==(1,1,1)
    assert value['unbound']==[dict(teacherId='t0',teacher='Учитель математики',lessons=1,since='2026-08-17',classNames=['10A','10D'])]
    key=lesson(client)['lessonKey'];write(client,graded(entry(client,key)),publish=True)
    fresh=summary(client).json();a=by_class(fresh)['10A'];assert (a['unfilled'],a['complete'],a['lastRecord'],a['unfilledSince'])==(1,1,'2026-08-17','2026-08-17')
    text=summary(client).text;assert '"s1"' not in text and 'Ученик' not in text and 'lessonKey' not in text
    audit=[e for e in client.get('/api/access/audit').json() if e['action']=='journal.coverage_classes_read'];assert audit
    # The report of the same filters is served from the summary's snapshot.
    shown=report(client,token=fresh['token'],asOf=fresh['asOf']);assert shown.status_code==200 and shown.json()['token']==fresh['token'] and shown.json()['counts']==fresh['counts']


def test_class_summary_matches_the_lesson_report(client):
    people(client);known();value=summary(client).json();issues=report(client,status='issues',asOf='2026-08-17T18:00:00Z').json()
    assert sum(c['unfilled'] for c in value['classes'])==3 and issues['total']==2 and value['counts']['expected']==issues['counts']['expected']


def test_class_summary_for_class_teacher_only_own_class(client):
    teacher,other=people(client);create(client,'parent');state=api.read('demo');state.classes[0].classTeacherId='t2';api.save('demo',state)
    value=summary(teacher);assert value.status_code==200,value.text;value=value.json()
    assert [c['className'] for c in value['classes']]==['10A'] and 'unbound' not in value and value['classes'][0]['classTeacher']=='Учитель английского'
    assert 'Учитель математики' not in str(value)
    assert summary(other).status_code==403 and summary(signed('parent')).status_code==403
    assert summary(teacher,mode='teaching').status_code==422 and summary(client,mode='teaching').status_code==422
    assert teacher.post('/api/journal/coverage/classes?workspace=school',json=query(mode='classes')).status_code==403
    assert summary(client,end='2027-08-19').status_code==422


def test_unbound_warning_clears_after_binding(client):
    people(client);known();before=summary(client).json();assert before['unbound'][0]['teacherId']=='t0'
    create(client,'teacher',login='math',grants=[dict(workspace='demo',view='teacher',entityIds=['t0'])])
    after=summary(client).json();assert by_class(after)['10A']['unbound']==0 and by_class(after)['10D']['unbound']==0 and after['unbound']==[]


def test_lesson_without_any_teacher_is_unbound(client):
    people(client);state=api.read('demo');state.datedLessons=[DatedLesson(id='none',name='Без учителя',kind='change',placementId='eng',sourceDate=MON,onDate=MON,teacherIds=[])];api.save('demo',state)
    value=summary(client).json();assert any(g['teacherId'] is None and g['teacher']=='Учитель не назначен' for g in value['unbound'])


def test_drill_down_uses_report_with_class(client):
    people(client);known();key=lesson(client)['lessonKey'];write(client,graded(entry(client,key)),publish=True);value=summary(client).json()
    rows=report(client,mode='classes',classId='c-demo-d',status='issues',asOf=value['asOf']);assert rows.status_code==200,rows.text
    assert [r['subject'] for r in rows.json()['items']]==['Математика']
    # The summary's token is not the class's: without a cached snapshot of that class it is stale.
    api.journal_coverage.cache.clear()
    assert client.post('/api/journal/coverage/report?workspace=demo',json=query(mode='classes',classId='c-demo-d',status='issues',asOf=value['asOf'],token=value['token'])).status_code==409


def test_summary_ignores_the_teacher_filter_and_the_drill_down_lists_what_the_card_counted(client):
    people(client);known();plain=summary(client).json();picked=summary(client,teacherId='t2').json();card=by_class(picked)['10A']
    assert by_class(picked)==by_class(plain) and card['unfilled']==2
    # The drill-down is the summary's own filters narrowed to the class: no teacher, the issues of that class.
    rows=report(client,mode='classes',classId='c-demo-a',teacherId='',status='issues',asOf=picked['asOf']);assert rows.status_code==200,rows.text
    assert rows.json()['total']==card['unfilled'] and {r['subject'] for r in rows.json()['items']}=={'Английский язык','Математика'}
    # A teacher left in the form would cut the list below the card, which counts every teacher.
    assert report(client,mode='classes',classId='c-demo-a',teacherId='t2',status='issues',asOf=picked['asOf']).json()['total']==1
