from datetime import date,datetime,timezone
import csv,io,json
from copy import deepcopy
import pytest
from backend import api,journal_coverage
from backend.models import DatedLesson,Event,Student,Placement
from test_access import client,enable,create,signed
from test_absences import setup
from test_journal import DAY,lesson,entry,write,graded


def query(**extra):return dict(start=DAY,end=DAY,status='all')|extra
def report(client,**extra):return client.post('/api/journal/coverage/report?workspace=demo',json=query(**extra))
def details(client,key,**extra):return client.post('/api/journal/coverage/details?workspace=demo',json=query(lessonKey=key,day=DAY,**extra))
def export(client,**extra):return client.post('/api/journal/coverage/export?workspace=demo',json=query(**extra))
def english(value):return next(r for r in value['items'] if r['subject']=='Английский язык')
def known():
    state=api.read('demo')
    for c in state.classes:c.studentCount=sum(s.active and s.classId==c.id for s in state.students)
    api.save('demo',state)


def test_missing_draft_incomplete_complete_and_raw_unmarked_pupils(client):
    known();value=report(client).json();assert value['counts']['expected']==2 and value['counts']['missing']==2
    row=english(value);assert row['missingPupils']==2 and row['canOpen']
    pupils=details(client,row['lessonKey']).json()['pupils'];assert {s['id'] for s in pupils}=={'s1','s2'}
    draft=entry(client,row['lessonKey']);saved=write(client,draft).json()
    assert english(report(client).json())['status']=='draft'
    published=write(client,saved,publish=True).json()
    partial=english(report(client).json());assert partial['status']=='incomplete' and partial['unmarkedPupils']==2 and not partial['topicFilled']
    complete=write(client,graded(published),publish=True).json()
    value=report(client).json();assert english(value)['status']=='complete'
    assert details(client,row['lessonKey']).json()['pupils']==[]
    assert 'Комментарий' not in json.dumps(value,ensure_ascii=False) and 'grades' not in str(value)
    complete['document']['content']['topic']='Unpublished private draft';write(client,complete)
    current=english(report(client).json());assert current['status']=='complete' and current['hasDraftChanges']
    assert 'Unpublished private draft' not in str(current)


def test_teacher_own_and_coordinator_modes_do_not_open_other_journals(client):
    _,_,parent,teacher=setup(client);known()
    own=report(teacher,mode='teaching').json();assert own['counts']['expected']==1 and english(own)['canOpen']
    assigned=report(teacher).json();assert assigned['counts']['expected']==2
    math=next(r for r in assigned['items'] if r['subject']=='Математика')
    assert not math['canOpen'] and math['missingPupils']==1
    value=details(teacher,math['lessonKey']).json();assert [p['id'] for p in value['pupils']]==['s1']
    assert 's3' not in str(value) and 'Ученик 3' not in str(value)
    assert report(teacher,classId='c-demo-d').status_code==404
    assert report(teacher,mode='teaching',teacherId='t0').status_code==404
    other=signed('other-teacher');assert report(other).status_code==403
    assert report(parent).status_code==403 and export(parent).status_code==403
    assert report(signed('student')).status_code==403
    assert teacher.post('/api/journal/coverage/report?workspace=school',json=query()).status_code==403


def test_calendar_closures_moves_extra_lessons_and_old_publications(client):
    known();key=lesson(client)['lessonKey'];saved=write(client,graded(entry(client,key)),publish=True).json()
    state=api.read('demo');state.events=[Event(id='close',name='Closed',start=date(2026,8,17),end=date(2026,8,17),kind='holiday',effect='cancel',classIds=['c-demo-a'])];api.save('demo',state)
    value=report(client).json();assert value['counts']['expected']==1 and value['counts']['cancelled']==1
    assert value['counts']['orphaned']==1 and english(value)['status']=='orphaned'
    state=api.read('demo');state.events=[];state.datedLessons=[DatedLesson(id='move',name='Move',kind='change',placementId='eng',sourceDate=date(2026,8,17),onDate=date(2026,8,18),startTime='14:00',endTime='14:40')];api.save('demo',state)
    old=report(client).json();assert old['counts']['orphaned']==1
    moved=report(client,start='2026-08-18',end='2026-08-18').json();assert english(moved)['status']=='stale'
    combined=report(client,end='2026-08-18').json();assert combined['counts']['orphaned']==0 and combined['counts']['stale']==1
    synced=entry(client,key,syncRoster=True);assert write(client,synced,publish=True,syncRoster=True).status_code==200
    assert english(report(client,end='2026-08-18').json())['status']=='complete'
    state=api.read('demo');state.datedLessons.append(DatedLesson(id='extra',name='Extra',kind='add',onDate=date(2026,8,18),subjectId='sub2',groupId=state.workloads[0].groupId,profileId=state.workloads[0].profileId,teacherIds=['t2'],roomIds=['r0'],startTime='15:00',endTime='15:40'));api.save('demo',state)
    assert report(client,start='2026-08-18',end='2026-08-18').json()['counts']['expected']==2


def test_current_roster_new_pupil_inactive_and_partial_roster_never_green(client):
    known();key=lesson(client)['lessonKey'];write(client,graded(entry(client,key)),publish=True)
    state=api.read('demo');state.students.append(Student(id='new',name='New pupil',classId='c-demo-a'));api.save('demo',state)
    value=english(report(client).json());assert value['status']=='stale' and value['missingPupils']==1
    assert details(client,key).json()['pupils'][0]['id']=='new'
    synced=entry(client,key,syncRoster=True);synced['document']['content']['records'][-1]['attendance']='present';write(client,synced,publish=True,syncRoster=True)
    assert english(report(client).json())['status']=='complete'
    state=api.read('demo');next(s for s in state.students if s.id=='new').active=False;api.save('demo',state)
    assert english(report(client).json())['status']=='stale'
    state=api.read('demo');state.classes[0].studentCount=30;api.save('demo',state)
    assert english(report(client).json())['rosterUnknown']
    state=api.read('demo');state.classes[0].studentCount=None;api.save('demo',state)
    assert english(report(client).json())['rosterUnknown']


def test_school_clock_cutoff_future_lessons_and_validation(client,monkeypatch):
    clock=datetime(2026,8,17,3,59,tzinfo=timezone.utc);monkeypatch.setattr(journal_coverage,'now',lambda:clock)
    before=report(client).json();assert before['counts']['upcoming']==2
    clock=datetime(2026,8,17,4,0,tzinfo=timezone.utc)
    after=report(client).json();assert english(after)['status']=='missing' and after['counts']['upcoming']==1
    pinned=report(client,asOf=before['asOf']).json();assert pinned['counts']['upcoming']==2
    for extra in [dict(start='2026-08-16'),dict(end='2027-08-19'),dict(asOf='2026-08-17T10:00:00'),dict(asOf='2027-01-01T00:00:00Z'),dict(end='2026-08-15')]:assert report(client,**extra).status_code==422


def test_stale_pages_after_draft_withdrawal_roster_and_scope_change(client,monkeypatch):
    _,_,parent,teacher=setup(client);known();first=report(teacher).json()
    key=lesson(client)['lessonKey'];saved=write(client,entry(client,key)).json()
    # A page of the report already calculated is served from its snapshot; once the
    # snapshot is gone, a draft of the same pupils and days requires a new check.
    again=report(teacher,token=first['token'],asOf=first['asOf']);assert again.status_code==200 and again.json()['items']==first['items']
    api.journal_coverage.cache.clear();assert report(teacher,token=first['token'],asOf=first['asOf']).status_code==409
    published=write(client,graded(saved),publish=True).json();current=report(teacher).json()
    client.post('/api/journal/withdraw?workspace=demo',json=dict(lessonKey=key,version=published['version'])).raise_for_status()
    assert export(teacher,token=current['token'],asOf=current['asOf']).status_code==200
    api.journal_coverage.cache.clear();assert export(teacher,token=current['token'],asOf=current['asOf']).status_code==409
    original=api.journal_coverage.result
    def revoke(db,user,snap,workspace,body,token):
        value=original(db,user,snap,workspace,body,token)
        with api.connect() as changed:changed.execute("UPDATE absence_reviewers SET version=version+1,document='{}' WHERE workspace='demo'")
        return value
    monkeypatch.setattr(api.journal_coverage,'result',revoke)
    assert report(teacher).status_code==409


def counted(monkeypatch):
    calls=[];original=api.journal_coverage.calculate
    def wrapper(*args,**kwargs):calls.append(1);return original(*args,**kwargs)
    monkeypatch.setattr(api.journal_coverage,'calculate',wrapper);return calls


def test_snapshot_paging_ignores_saves_and_timetable_changes(client,monkeypatch):
    known();calls=counted(monkeypatch)
    first=report(client,end='2027-06-11').json();assert first['total']>50 and len(first['token'])==64
    assert write(client,entry(client,lesson(client)['lessonKey'])).status_code==200
    state=api.read('demo');state.settings.name='Другое название школы';api.save('demo',state)
    second=report(client,end='2027-06-11',offset=50,token=first['token'],asOf=first['asOf'])
    assert second.status_code==200,second.text;second=second.json()
    assert not {r['lessonKey'] for r in first['items']}&{r['lessonKey'] for r in second['items']}
    assert second['total']==first['total'] and second['token']==first['token'] and len(calls)==1
    assert export(client,end='2027-06-11',token=first['token'],asOf=first['asOf']).status_code==200 and len(calls)==1
    # Without the snapshot the same sent token is stale: today lies inside the range.
    api.journal_coverage.cache.clear()
    assert report(client,end='2027-06-11',offset=50,token=first['token'],asOf=first['asOf']).status_code==409


def test_live_token_limited_to_range_and_pupils(client):
    _,_,parent,teacher=setup(client);known();key=lesson(client)['lessonKey']
    first=report(teacher).json();api.journal_coverage.cache.clear()
    other='2026-08-24';write(client,graded(entry(client,lesson(client,day=other)['lessonKey'],day=other)),publish=True).raise_for_status()
    assert report(teacher,token=first['token'],asOf=first['asOf']).status_code==200
    far=report(client,classId='c-demo-d').json();near=report(client,classId='c-demo-a').json();api.journal_coverage.cache.clear()
    assert write(client,entry(client,key)).status_code==200
    assert report(client,classId='c-demo-d',token=far['token'],asOf=far['asOf']).status_code==200
    assert report(client,classId='c-demo-a',token=near['token'],asOf=near['asOf']).status_code==409


def test_token_bound_to_account_and_scope(client):
    _,_,parent,teacher=setup(client);known();first=report(teacher).json()
    assert len(first['token'])==64 and report(client).json()['token'][:32]!=first['token'][:32]
    other=report(signed('other-teacher'),token=first['token'],asOf=first['asOf'])
    assert other.status_code==409 and 'items' not in other.json()
    assert export(signed('other-teacher'),token=first['token'],asOf=first['asOf']).status_code==409
    assert report(teacher,classId='c-demo-d',token=first['token'],asOf=first['asOf']).status_code==404


def test_cache_row_cap(client,monkeypatch):
    known();calls=counted(monkeypatch)
    first=report(client).json();assert report(client,offset=50,token=first['token'],asOf=first['asOf']).status_code==200 and len(calls)==1
    api.journal_coverage.cache.clear();calls.clear();monkeypatch.setattr(journal_coverage,'CACHE_ROWS',1)
    first=report(client).json();assert first['total']==2
    assert report(client,offset=50,token=first['token'],asOf=first['asOf']).status_code==200 and len(calls)==2


def test_pagination_full_csv_filters_and_formula_protection(client):
    state=api.read('demo');state.subjects[2].name='=1+1';api.save('demo',state)
    first=report(client,end='2027-06-11').json();assert len(first['items'])==50 and first['nextOffset']==50
    second=report(client,end='2027-06-11',offset=50,token=first['token'],asOf=first['asOf']).json()
    assert not {r['lessonKey'] for r in first['items']}&{r['lessonKey'] for r in second['items']}
    data=export(client,end='2027-06-11',token=first['token'],asOf=first['asOf'])
    rows=list(csv.reader(io.StringIO(data.content.decode('utf-8-sig')),delimiter=';'))
    assert len(rows)==first['total']+1 and any("'=1+1" in row for row in rows)
    assert report(client,status='complete').json()['items']==[]
    assert report(client,status='issues').json()['counts']['expected']==2


def test_detail_pagination_and_class_scope_after_transfer(client):
    _,_,parent,teacher=setup(client);state=api.read('demo')
    state.students += [Student(id=f'extra{i}',name=f'New {i:02}',classId='c-demo-a') for i in range(55)]
    api.save('demo',state);key=lesson(client)['lessonKey']
    first=details(teacher,key).json();assert first['total']==57 and len(first['pupils'])==50
    second=details(teacher,key,pupilOffset=50,token=first['token'],asOf=first['asOf']).json();assert len(second['pupils'])==7
    state=api.read('demo');state.students[0].classId='c-demo-d';api.save('demo',state)
    assert details(teacher,key,token=first['token'],asOf=first['asOf']).status_code==409
    assert 's1' not in [s['id'] for s in details(teacher,key,pupilOffset=50).json()['pupils']]


def test_publication_transaction_failure_keeps_control_unchanged(client,monkeypatch):
    known();key=lesson(client)['lessonKey'];before=report(client).json()
    record=graded(entry(client,key))
    def fail(*args):raise RuntimeError('Failed notification')
    monkeypatch.setattr(api.journal,'notify',fail)
    with pytest.raises(RuntimeError):write(client,record,publish=True)
    after=report(client,token=before['token'],asOf=before['asOf']).json()
    assert after['counts']==before['counts']


def test_substitute_teacher_scope_and_deleted_roster_publication(client):
    _,_,parent,teacher=setup(client);other=signed('other-teacher');known()
    key=lesson(client)['lessonKey'];write(client,graded(entry(client,key)),publish=True)
    state=api.read('demo');state.datedLessons=[DatedLesson(id='substitute',name='Substitute',kind='change',placementId='eng',sourceDate=date(2026,8,17),onDate=date(2026,8,17),teacherIds=['t1'])];api.save('demo',state)
    assert report(teacher,mode='teaching').json()['counts']['orphaned']==1
    replacement=report(other,mode='teaching').json();assert replacement['counts']['stale']==1 and english(replacement)['canOpen']
    assert details(other,key,mode='teaching').status_code==200
    # Whole-class membership disappears; the old publication must remain visible
    # for administrators to inspect or withdraw, without inventing attendance.
    state=api.read('demo');state.placements=[];state.datedLessons=[];state.students=[]
    for group in state.groups:group.studentIds=[]
    api.save('demo',state)
    value=report(client).json();assert value['counts']['expected']==0 and value['counts']['orphaned']==1
    assert details(teacher,key).status_code==404
    assert report(client,mode='teaching',classId='c-demo-a').json()['counts']['orphaned']==0
    state=api.read('demo');state.classes=[];state.groups=[];state.workloads=[];api.save('demo',state)
    archived=report(client).json();assert archived['counts']['orphaned']==1
    assert archived['items'][0]['classNames']==['Вне текущего состава']


def test_coverage_index_backfill_failure_retry_and_restored_backup(client,tmp_path,monkeypatch):
    from backend import coverage_projection
    from tools.backup_school import create_backup
    known();key=lesson(client)['lessonKey'];write(client,graded(entry(client,key)),publish=True)
    with api.connect() as db:db.execute("DELETE FROM journal_projection_versions WHERE name='coverage'")
    original=coverage_projection.populate
    def fail(db,*args):original(db,*args);raise RuntimeError('Migration interrupted')
    monkeypatch.setattr(coverage_projection,'populate',fail)
    with pytest.raises(RuntimeError):report(client)
    with api.connect() as db:assert not db.execute("SELECT 1 FROM journal_projection_versions WHERE name='coverage'").fetchone()
    monkeypatch.setattr(coverage_projection,'populate',original)
    value=report(client).json();assert english(value)['status']=='complete'
    destination=tmp_path/'coverage-backup';source=api.DB;create_backup(source,destination)
    monkeypatch.setattr(api,'DB',destination/source.name)
    assert report(client,asOf=value['asOf']).json()['counts']==value['counts']
