import csv
import io
import json
import hashlib
from copy import deepcopy
from datetime import date,datetime,timedelta,timezone
from uuid import uuid4
import pytest
from backend import api,attendance_projection
from backend.attendance_reports import PAGE
from backend.models import SchoolClass,Student,DatedLesson
from seeding import read_row,insert_row
from test_access import client,create,signed
from test_absences import setup,mark,send,decide,body,url,reviewers
from test_journal import DAY,lesson,entry,write,graded


def query(**values):return dict(start=DAY,end=DAY)|values
def report(client,**values):return client.post('/api/journal/attendance/report?workspace=demo',json=query(**values))
def details(client,**values):return client.post('/api/journal/attendance/details?workspace=demo',json=query(**(dict(studentId='s1')|values)))
def export(client,**values):return client.post('/api/journal/attendance/export?workspace=demo',json=query(**values))
def pupil(result,id='s1'):return next(s for s in result['students'] if s['id']==id)


def test_published_only_zero_records_unmarked_late_and_subject_class_totals(client):
    _,_,parent,teacher=setup(client)
    record=mark(client,publish=False)
    empty=report(teacher).json();assert empty['totals']['records']==0 and empty['withoutRecords']==2
    # The class has two ended lessons on that day and none of them is published (English is only a draft): they are counted, not hidden.
    assert empty['source']=='published' and empty['missingJournals']==2
    rows=record['document']['content']['records'];rows[0].update(attendance='late',lateMinutes=7);rows[1]['attendance']='unmarked'
    published=write(client,record,publish=True);assert published.status_code==200,published.text
    value=report(teacher).json();assert value['totals']['attendance']==dict(unmarked=1,present=0,absent=0,late=1,excused=0,left=0)
    assert value['totals']['lateMinutes']==7 and value['classes'][0]['records']==2
    assert value['subjects'][0]['attendance']==value['totals']['attendance']
    assert {s['id'] for s in value['students']}=={'s1','s2'}
    assert report(client).json()['withoutRecords']==2
    assert report(teacher,subjectId='no-such-subject').json()['totals']['records']==0
    assert pupil(report(teacher,studentId='s1').json())['records']==1
    assert parent.post('/api/journal/attendance/report?workspace=demo',json=query()).status_code==403


def test_current_roster_scope_does_not_open_grades_or_other_classes(client):
    _,_,parent,teacher=setup(client)
    record=graded(entry(client,lesson(client,'Математика')['lessonKey']))
    result=write(client,record,publish=True);assert result.status_code==200,result.text
    own=details(teacher);assert own.status_code==200,own.text
    assert report(teacher).json()['totals']['records']==1  # mixed group also includes s3 in another class
    assert own.json()['items'] and all(r['subject']=='Математика' for r in own.json()['items'])
    assert teacher.get('/api/journal/entry',params=dict(workspace='demo',lessonKey=record['lessonKey'],day=DAY)).status_code==403
    serialized=json.dumps(own.json(),ensure_ascii=False)
    assert all(secret not in serialized for secret in ['grades','Тема урока','Комментарий','Индивидуальная','assessments','homework'])
    for account in [parent,signed('student'),signed('other-teacher')]:
        assert report(account).status_code==403
        assert export(account).status_code==403
    assert report(teacher,classId='c-demo-d').status_code==404
    assert report(teacher,studentId='s3').status_code==404
    assert report(teacher,historicalOnly=True).status_code==403
    assert teacher.post('/api/journal/attendance/report?workspace=school',json=query()).status_code==403
    assert client.post('/api/journal/attendance/report?workspace=school',json=query()).json()['totals']['records']==0


def test_notice_approval_reversal_only_effective_absence_and_no_private_fields(client):
    _,_,parent,teacher=setup(client);mark(client);notice=send(parent)
    before=report(teacher).json();assert pupil(before)['attendance']['absent']==1
    accepted=decide(teacher,notice).json()
    assert report(teacher,token=before['token']).status_code==200
    api.attendance_reports.cache.clear();assert report(teacher,token=before['token']).status_code==409
    after=report(teacher).json();assert pupil(after)['attendance']['excused']==1
    lesson_row=details(teacher).json()['items'][0]
    assert lesson_row['rawAttendance']=='absent' and lesson_row['attendance']=='excused' and lesson_row['excusedByNotice']
    assert 'Личная причина' not in json.dumps(after,ensure_ascii=False) and 'absenceApproval' not in json.dumps(lesson_row)
    assert details(teacher,attendance='absent').json()['items']==[]
    assert len(details(teacher,attendance='excused').json()['items'])==1
    assert decide(teacher,accepted,'revoke').status_code==200
    assert details(teacher).json()['items'][0]['attendance']=='absent'
    # Partial coverage must not excuse the saved lesson.
    partial=send(parent,body(startTime='08:21'));assert decide(teacher,partial).status_code==200
    assert pupil(report(teacher).json())['attendance']['absent']==1


def test_draft_edits_publication_withdrawal_and_replication_are_atomic(client,monkeypatch):
    _,_,parent,teacher=setup(client);saved=mark(client);first=report(teacher).json()
    saved['document']['content']['records'][0]['attendance']='present'
    draft=write(client,saved).json()
    assert report(teacher,token=first['token']).status_code==200
    assert pupil(report(teacher).json())['attendance']['absent']==1
    original=api.journal.notify
    def fail(*args):raise RuntimeError('notification failed')
    monkeypatch.setattr(api.journal,'notify',fail)
    with pytest.raises(RuntimeError):write(client,draft,publish=True)
    assert report(teacher,token=first['token']).status_code==200
    assert pupil(report(teacher).json())['attendance']['absent']==1
    monkeypatch.setattr(api.journal,'notify',original)
    published=write(client,draft,publish=True).json()
    assert pupil(report(teacher,token=first['token']).json())['attendance']['absent']==1
    api.attendance_reports.cache.clear();assert report(teacher,token=first['token']).status_code==409
    assert pupil(report(teacher).json())['attendance']['present']==1
    assert client.post('/api/journal/withdraw?workspace=demo',json=dict(lessonKey=published['lessonKey'],version=published['version'])).status_code==200
    assert report(teacher).json()['totals']['records']==0
    restored=entry(client,published['lessonKey']);assert write(client,restored,publish=True).status_code==200
    assert pupil(report(teacher).json())['attendance']['present']==1


def test_projection_backfill_failure_retry_and_backup(client,tmp_path,monkeypatch):
    from tools.backup_school import create_backup
    _,_,parent,teacher=setup(client);saved=mark(client)
    with api.connect() as db:
        for table in ['journal_projection_versions','journal_attendance_epoch','journal_attendance','journal_attendance_lessons']:db.execute('DROP TABLE '+table)
    populate=attendance_projection.populate
    def fail(db,*args):populate(db,*args);raise RuntimeError('interrupted migration')
    monkeypatch.setattr(attendance_projection,'populate',fail)
    with pytest.raises(RuntimeError):report(teacher)
    with api.connect() as db:assert not db.execute("SELECT 1 FROM journal_projection_versions WHERE name='attendance'").fetchone()
    monkeypatch.setattr(attendance_projection,'populate',populate)
    value=report(teacher).json();assert value['totals']['records']==2
    destination=tmp_path/'attendance-backup';source=api.DB;create_backup(source,destination)
    monkeypatch.setattr(api,'DB',destination/source.name)
    assert report(teacher).json()['totals']==value['totals']
    assert entry(client,saved['lessonKey'])['document']==saved['document']


def test_class_transfer_current_names_inactive_and_deleted_student_archive(client):
    _,_,parent,teacher=setup(client);mark(client)
    state=api.read('demo');s=next(s for s in state.students if s.id=='s1');s.classId='c-demo-d';s.name='Новое имя';s.active=False;api.save('demo',state)
    assert {s['id'] for s in report(teacher).json()['students']}=={'s2'}
    value=report(client,classId='c-demo-d').json();assert pupil(value)['name']=='Новое имя' and pupil(value)['active'] is False and pupil(value)['records']==1
    state=api.read('demo');state.students=[s for s in state.students if s.id!='s1']
    for group in state.groups:group.studentIds=[id for id in group.studentIds if id!='s1']
    api.save('demo',state)
    archived=report(client,historicalOnly=True).json();assert archived['studentCount']==1 and pupil(archived)['classId'] is None
    assert pupil(archived)['name']=='Ученик 1 (пример)'
    assert details(client,historicalOnly=True).json()['items']
    assert details(teacher).status_code==404


def test_student_pagination_token_catches_changed_publication_and_reviewer_scope(client):
    _,person,parent,teacher=setup(client);state=api.read('demo')
    state.students += [Student(id=f'extra-{i}',name=f'Ученик {i+10}',classId='c-demo-a') for i in range(PAGE+15)]
    api.save('demo',state)
    everyone={s.id for s in state.students if s.classId=='c-demo-a'}
    first=report(teacher).json();assert len(first['students'])==PAGE and first['nextOffset']==PAGE
    second=report(teacher,offset=PAGE,token=first['token']).json();assert second['nextOffset'] is None
    shown=[s['id'] for s in first['students']+second['students']];assert len(shown)==len(set(shown)) and set(shown)==everyone
    assert len(list(csv.reader(io.StringIO(export(teacher).content.decode('utf-8-sig')),delimiter=';')))==len(everyone)+1
    record=entry(client,lesson(client)['lessonKey']);assert write(client,record,publish=True).status_code==200
    again=report(teacher,offset=PAGE,token=first['token']);assert again.status_code==200 and again.json()['students']==second['students']
    api.attendance_reports.cache.clear();assert report(teacher,offset=PAGE,token=first['token']).status_code==409
    current=report(teacher).json()
    reviewers({})
    assert report(teacher,token=current['token']).status_code==409
    assert report(teacher).status_code==403
    assert teacher.get('/api/journal/attendance/options?workspace=demo').json()['students']==[]


def test_csv_all_rows_formulas_scopes_and_current_attendance(client):
    _,_,parent,teacher=setup(client);state=api.read('demo');state.students[0].name='=SUM(1,2)';api.save('demo',state)
    mark(client);notice=send(parent);assert decide(teacher,notice).status_code==200
    value=report(teacher).json();response=export(teacher,token=value['token'])
    assert response.status_code==200 and response.headers['cache-control']=='no-store'
    rows=list(csv.reader(io.StringIO(response.content.decode('utf-8-sig')),delimiter=';'))
    assert len(rows)==3 and any("'=SUM(1,2)" in row for row in rows)
    assert 'Личная причина' not in response.content.decode('utf-8-sig')
    lesson_rows=export(teacher,studentId='s1',view='lessons').content.decode('utf-8-sig')
    assert 'Уважительная причина;Отсутствует' in lesson_rows
    assert export(teacher,view='lessons').status_code==422
    assert export(teacher,view='classes').status_code==200
    assert export(teacher,studentId='s3',view='lessons').status_code==404


def test_period_filters_and_access_change_during_report(client,monkeypatch):
    _,_,parent,teacher=setup(client);mark(client)
    for values in [dict(end='2026-08-16'),dict(end='2027-08-18'),dict(historicalOnly=True,classId='c-demo-a'),dict(offset=-1)]:
        assert report(client,**values).status_code==422
    assert details(teacher,attendance='invalid').status_code==422
    original=api.attendance_reports.summary
    def revoke(db,user,snap,workspace,body):
        result=original(db,user,snap,workspace,body)
        with api.connect() as change:change.execute("UPDATE absence_reviewers SET version=version+1,document='{}' WHERE workspace='demo'")
        return result
    monkeypatch.setattr(api.attendance_reports,'summary',revoke)
    assert report(teacher).status_code==409


def test_lesson_pagination_and_complete_csv_use_published_date_time(client):
    _,_,parent,teacher=setup(client);saved=mark(client);copies=PAGE+12;total=copies+1
    end=(date.fromisoformat(DAY)+timedelta(days=copies)).isoformat()
    with api.connect() as db:
        template=read_row(db,'journal_entries',workspace='demo',lesson_key=saved['lessonKey'])
        for i in range(1,copies+1):
            doc=deepcopy(saved['document']);day=(date.fromisoformat(DAY)+timedelta(days=i)).isoformat()
            key=hashlib.sha256(day.encode()).hexdigest();doc['lesson']['date']=day;text=json.dumps(doc,ensure_ascii=False)
            insert_row(db,'journal_entries',template,lesson_key=key,document=text,published_document=text)
            api.journal.members(db,'demo',key,doc,'published')
    first=details(teacher,end=end).json();assert len(first['items'])==PAGE and first['nextOffset']==PAGE
    second=details(teacher,end=end,offset=PAGE,token=first['token']).json()
    assert len(second['items'])==total-PAGE and second['nextOffset'] is None
    assert not {r['lessonKey'] for r in first['items']} & {r['lessonKey'] for r in second['items']}
    response=export(teacher,view='lessons',studentId='s1',end=end,token=first['token'])
    assert len(list(csv.reader(io.StringIO(response.content.decode('utf-8-sig')),delimiter=';')))==total+1


def test_moved_and_cancelled_timetable_does_not_silently_rewrite_published_report(client):
    _,_,parent,teacher=setup(client);saved=mark(client)
    state=api.read('demo');state.datedLessons=[DatedLesson(id='move',name='Synthetic move',kind='change',placementId='eng',sourceDate=date(2026,8,17),onDate=date(2026,8,18),startTime='14:00',endTime='14:40')]
    api.save('demo',state)
    assert pupil(report(teacher).json())['records']==1
    assert report(teacher,start='2026-08-18',end='2026-08-18').json()['totals']['records']==0
    synced=entry(client,saved['lessonKey'],syncRoster=True);published=write(client,synced,publish=True,syncRoster=True)
    assert published.status_code==200,published.text
    assert report(teacher).json()['totals']['records']==0
    new=details(teacher,start='2026-08-18',end='2026-08-18').json()['items'][0]
    assert new['start']=='14:00' and new['date']=='2026-08-18'
    state=api.read('demo');state.datedLessons=[DatedLesson(id='cancel',name='Synthetic cancellation',kind='cancel',placementId='eng',sourceDate=date(2026,8,17),onDate=date(2026,8,17))]
    api.save('demo',state)
    assert details(teacher,start='2026-08-18',end='2026-08-18').json()['items']
    assert client.post('/api/journal/withdraw?workspace=demo',json=dict(lessonKey=saved['lessonKey'],version=published.json()['version'])).status_code==200
    assert details(teacher,start='2026-08-18',end='2026-08-18').json()['items']==[]


def kept(account,token,**values):
    # Every check starts without a snapshot, so the live token is compared with the sent one.
    api.attendance_reports.cache.clear();return report(account,token=token,**values).status_code


def test_unrelated_changes_keep_class_report_token(client):
    _,_,parent,teacher=setup(client);first=report(teacher).json()
    state=api.read('demo');state.settings.name='Другое название школы';api.save('demo',state)
    assert kept(teacher,first['token'])==200
    other='2026-08-24'
    write(client,graded(entry(client,lesson(client,day=other)['lessonKey'],day=other)),publish=True).raise_for_status()
    assert kept(teacher,first['token'])==200
    far=report(client,classId='c-demo-d').json()
    mark(client)
    assert kept(client,far['token'],classId='c-demo-d')==200
    assert kept(teacher,first['token'])==409


def test_admin_whole_school_token_limited_to_selected_days(client):
    _,_,parent,teacher=setup(client);whole=report(client).json()
    other='2026-08-24'
    write(client,graded(entry(client,lesson(client,day=other)['lessonKey'],day=other)),publish=True).raise_for_status()
    assert kept(client,whole['token'])==200
    mark(client)
    assert kept(client,whole['token'])==409


def test_pupil_rename_requires_new_summary_and_snapshot_keeps_its_time(client):
    _,_,parent,teacher=setup(client);mark(client);first=report(teacher).json()
    state=api.read('demo');next(s for s in state.students if s.id=='s1').name='Новое имя';api.save('demo',state)
    snapshot=report(teacher,token=first['token']);assert snapshot.status_code==200 and snapshot.json()['generatedAt']==first['generatedAt']
    assert pupil(snapshot.json())['name']!='Новое имя'
    assert kept(teacher,first['token'])==409


def test_admin_token_ignores_roster_changes_on_report_details_and_export(client):
    _,_,parent,teacher=setup(client);mark(client);first=report(client).json()
    state=api.read('demo');state.classes.append(SchoolClass(id='c-new',name='Новый класс'));state.students.append(Student(id='newcomer',name='Новый ученик',classId='c-new'));api.save('demo',state)
    assert report(client,token=first['token']).status_code==200
    assert details(client,token=first['token']).status_code==200
    assert export(client,token=first['token']).status_code==200
    assert export(client,token=first['token'],view='lessons',studentId='s1').status_code==200
    assert kept(client,first['token'])==409


def test_token_bound_to_account_even_with_the_same_scope(client):
    _,person,parent,teacher=setup(client);mark(client)
    with api.connect() as db:other_id=db.execute("SELECT id FROM accounts WHERE login='other-teacher'").fetchone()[0]
    reviewers({'c-demo-a':[person['id'],other_id]});twin=signed('other-teacher');create(client,'admin',login='second-admin');second=signed('second-admin')
    for owner,stranger in [(teacher,twin),(client,second)]:
        first=report(owner).json()
        for call,extra in [(report,{}),(details,dict(studentId='s1')),(export,dict(view='lessons',studentId='s1')),(export,{})]:
            rejected=call(stranger,token=first['token'],**extra);assert rejected.status_code==409 and 'students' not in rejected.json() and 'items' not in rejected.json()
        assert report(stranger).json()['token']!=first['token']


def test_other_account_token_and_scope_on_details_and_export(client):
    _,person,parent,teacher=setup(client);mark(client)
    with api.connect() as db:other_id=db.execute("SELECT id FROM accounts WHERE login='other-teacher'").fetchone()[0]
    reviewers({'c-demo-a':[person['id']],'c-demo-d':[other_id]});other=signed('other-teacher')
    first=report(teacher).json();own=report(other).json()
    for call,extra in [(report,{}),(details,dict(studentId='s1')),(export,dict(view='lessons',studentId='s1')),(export,{})]:
        assert call(other,token=first['token'],**extra).status_code==409
    assert details(other,token=own['token'],studentId='s1').status_code==404
    assert details(other,token=own['token'],studentId='s3').status_code==200
    assert details(teacher,token=first['token'],studentId='s3').status_code==404
    assert export(teacher,token=first['token'],view='lessons',studentId='s3').status_code==404
    assert details(teacher,token=first['token'],studentId='s1').status_code==200


def test_options_report_today_and_current_period(client,monkeypatch):
    _,_,parent,teacher=setup(client)
    stamp=datetime(2026,10,14,20,30,tzinfo=timezone.utc);monkeypatch.setattr('backend.attendance_reports.now',lambda:stamp)
    def options(account):return account.get('/api/journal/attendance/options?workspace=demo').json()
    def create_period(name,start,end):
        response=client.put(f'/api/journal/periods/{uuid4().hex}?workspace=demo',json=dict(name=name,start=start,end=end,kind='custom',version=0,reason='Создание периода'));assert response.status_code==200,response.text
    value=options(client);assert value['today']=='2026-10-15' and value['period'] is None
    create_period('Первая четверть','2026-09-01','2026-10-31');create_period('Прошлая неделя','2026-10-05','2026-10-11')
    assert options(teacher)['period']==dict(name='Первая четверть',start='2026-09-01',end='2026-10-31')
    create_period('Текущая неделя','2026-10-12','2026-10-18')
    assert options(client)['period']==dict(name='Текущая неделя',start='2026-10-12',end='2026-10-18')

