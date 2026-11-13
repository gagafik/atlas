from datetime import date,datetime,timezone
import pytest
from backend import api,journal,journal_coverage,quiz_attempts,quiz_bank,quiz_catalog,quizzes,timetable_history
from backend.models import DatedLesson,Placement,Student
from backend.seed import initial_state
from test_access import client,enable,create,signed
from test_homework import get as homework,answer
from test_journal import DAY,lesson,entry,write,graded,report
from test_quizzes import NOW,detail,ready,start

TODAY=date(2026,9,1)
NEXT='2026-08-24'
ENGLISH='Английский язык'


@pytest.fixture
def clock(monkeypatch):
    """The school clock: 1 September 2026, so every lesson of the fixture week is in the past."""
    monkeypatch.setattr(timetable_history,'today',lambda:TODAY)
    monkeypatch.setattr(journal,'today',lambda:TODAY)
    monkeypatch.setattr(journal_coverage,'now',lambda:datetime(2026,9,1,12,tzinfo=timezone.utc))


def resave(edit,effective=None):
    state=api.read('demo');edit(state);return api.save('demo',state,effective=effective)


def swap_english(state):
    """eng becomes eng2 in the third period: another key for the same weekly lesson."""
    state.placements=[Placement(id='eng2',workloadId='w0',day=0,period=3,roomId='r0')]+[p for p in state.placements if p.id!='eng']


def pupil(state,id):return next(s for s in state.students if s.id==id)


def lessons(client,day=DAY):
    response=client.get('/api/journal/day',params=dict(workspace='demo',day=day));assert response.status_code==200,response.text
    return response.json()['lessons']


def card(client,day=DAY,subject=ENGLISH,group='10A'):
    return next(r for r in lessons(client,day) if r['lesson']['subject']==subject and r['lesson']['group']==group)


def opened(client,key,day=DAY,**params):return entry(client,key,day=day,**params)


def roster(record):return {s['id'] for s in record['document']['lesson']['students']}


def cov(client,action='report',**extra):
    return client.post(f'/api/journal/coverage/{action}?workspace=demo',json=dict(start=DAY,end=DAY,status='all')|extra)


def english_row(value,**match):
    return next(r for r in value['items'] if r['subject']==ENGLISH and all(r[k]==v for k,v in match.items()))


def pairs(value):return sorted((r['lessonKey'],r['status'],r['pupils'],r['missingPupils']) for r in value['items'])


def english_d(state,period=3):
    """10D gets its own English lesson, so both classes have a journal to compare."""
    state.placements.append(Placement(id='eng-d',workloadId='w1',day=0,period=period,roomId='r1'))


def current_since(client):return client.get('/api/history?workspace=demo').json()['currentFrom']


def members(client,day=DAY,group='10A',subject=ENGLISH):return roster(opened(client,card(client,day,subject,group)['lessonKey'],day))


def covered(client,day,class_id,group):
    row=english_row(cov(client,start=day,end=day,classId=class_id).json(),group=group)
    return {p['id'] for p in cov(client,'details',start=day,end=day,classId=class_id,lessonKey=row['lessonKey'],day=day).json()['pupils']}


def rosters(client):
    """Who sits in the English lessons of 10A and 10D on DAY and NEXT, as the journal opens them; the coverage report must list the same pupils."""
    result={}
    for day in [DAY,NEXT]:
        for group,class_id in [('10A','c-demo-a'),('10D','c-demo-d')]:
            result[day,group]=members(client,day,group);assert covered(client,day,class_id,group)==result[day,group],(day,group)
    return result


START={(day,group):pupils for day in [DAY,NEXT] for group,pupils in [('10A',{'s1','s2'}),('10D',{'s3','s4'})]}


def moved(*days):
    """START, but s1 sits in 10D instead of 10A on the given days."""
    return START|{(day,group):pupils for day in days for group,pupils in [('10A',{'s2'}),('10D',{'s1','s3','s4'})]}


def test_backfill_and_dateless_save_keep_current_behaviour(client,clock):
    math=lambda day:members(client,day,'Математика · группа A','Математика')
    # Nothing was saved with a date: the first read gives every pupil their current class and group for the whole year.
    for day in [DAY,NEXT]:assert members(client,day)=={'s1','s2'} and math(day)=={'s1','s3'}
    assert current_since(client)==DAY
    def edit(state):
        next(p for p in state.placements if p.id=='eng').period=3;english_d(state,4)
    resave(edit)
    # A dateless save changes the template for the whole year and leaves everybody where they were.
    assert [card(client,day)['lesson']['start'] for day in [DAY,NEXT]]==['10:30','10:30']
    assert rosters(client)==START and math(DAY)==math(NEXT)=={'s1','s3'}
    assert current_since(client)==DAY


def test_capture_merges_and_trims_periods(client,clock):
    resave(english_d)
    def move(class_id):resave(lambda s:setattr(pupil(s,'s1'),'classId',class_id),date(2026,8,24))
    move('c-demo-d');assert rosters(client)==moved(NEXT)
    move('c-demo-a');assert rosters(client)==START
    move('c-demo-d');assert rosters(client)==moved(NEXT)
    def remove(state):
        state.students=[s for s in state.students if s.id!='s1']
        for group in state.groups:group.studentIds=[id for id in group.studentIds if id!='s1']
    resave(remove)
    # A pupil removed from the school is in no lesson of any day.
    assert rosters(client)=={key:pupils-{'s1'} for key,pupils in START.items()}
    for day in [DAY,NEXT]:assert members(client,day,'Математика · группа A','Математика')=={'s3'}
    assert current_since(client)==DAY


def unchanged(client):
    """What a refused save must leave alone: the school revision, the template date and the audit trail."""
    return (client.get('/api/state?workspace=demo').json()['revision'],current_since(client),len(client.get('/api/access/audit').json()))


def put_dated(client,state,value):return client.put(f'/api/state?workspace=demo&effectiveFrom={value}',json=state)


def test_effective_date_after_today_is_rejected_and_changes_nothing(client,clock):
    enable(client)
    state=client.get('/api/state?workspace=demo').json();state['placements'][0]['period']=3
    before=unchanged(client)
    assert put_dated(client,state,'2026-09-02').status_code==422
    assert unchanged(client)==before


def test_effective_date_before_the_current_version_is_rejected_and_changes_nothing(client,clock):
    enable(client)
    state=client.get('/api/state?workspace=demo').json();state['placements'][0]['period']=3
    saved=put_dated(client,state,NEXT);assert saved.status_code==200,saved.text
    state=saved.json();state['placements'][0]['period']=4
    before=unchanged(client)
    assert put_dated(client,state,'2026-08-20').status_code==422
    assert unchanged(client)==before


def test_effective_date_after_the_school_year_is_rejected_and_changes_nothing(client,clock,monkeypatch):
    enable(client)
    monkeypatch.setattr(timetable_history,'today',lambda:date(2027,7,1))
    state=client.get('/api/state?workspace=demo').json();state['placements'][0]['period']=3
    before=unchanged(client)
    assert put_dated(client,state,'2027-06-20').status_code==422
    assert unchanged(client)==before
    assert put_dated(client,state,'2027-06-10').status_code==200


def drop_history_tables():
    with api.connect() as db:
        for table in ['timetable_versions','timetable_current','pupil_enrollments']:db.execute(f'DROP TABLE {table}')


def test_journal_day_reads_without_history_tables(client,clock):
    expected=[(r['lesson']['start'],r['lesson']['subject']) for r in lessons(client)]
    drop_history_tables()
    api.journal.snapshots.cache.clear()
    assert [(r['lesson']['start'],r['lesson']['subject']) for r in lessons(client)]==expected
    resave(lambda s:None)
    assert current_since(client)==DAY and members(client)=={'s1','s2'} and members(client,NEXT)=={'s1','s2'}


def test_journal_day_rereads_when_a_save_lands_between_the_state_and_its_history(client,clock,monkeypatch):
    api.journal.snapshots.cache.clear()
    original=api.journal.snapshots.read;saved=[]
    def racing(workspace):
        value=original(workspace)
        if not saved:
            saved.append(1);resave(lambda s:setattr(next(p for p in s.placements if p.id=='eng'),'period',3))
        return value
    monkeypatch.setattr(api.journal.snapshots,'read',racing)
    assert [r['lesson']['start'] for r in lessons(client) if r['lesson']['subject']==ENGLISH]==['10:30']


def test_journal_day_is_refused_while_the_schedule_keeps_changing(client,clock,monkeypatch):
    api.journal.snapshots.cache.clear()
    original=api.journal.snapshots.read
    def racing(workspace):
        value=original(workspace);resave(lambda s:None);return value
    monkeypatch.setattr(api.journal.snapshots,'read',racing)
    assert client.get('/api/journal/day',params=dict(workspace='demo',day=DAY)).status_code==409


def test_dated_template_change_stores_version_and_audit(client,clock):
    enable(client)
    state=client.get('/api/state?workspace=demo').json()
    state['placements'][0]=dict(state['placements'][0],id='eng2',period=3)
    saved=client.put(f'/api/state?workspace=demo&effectiveFrom={NEXT}',json=state);assert saved.status_code==200,saved.text
    # The earlier days keep the template that was valid then.
    assert (card(client)['lesson']['start'],card(client,NEXT)['lesson']['start'])==('08:20','10:30')
    assert current_since(client)==NEXT
    assert any(e['action']=='timetable.versioned' for e in client.get('/api/access/audit').json())
    undone=client.post('/api/undo?workspace=demo',json=dict(revision=saved.json()['revision']));assert undone.status_code==200,undone.text
    assert [p.id for p in api.read('demo').placements]==['eng','math']
    assert current_since(client)==NEXT
    assert (card(client)['lesson']['start'],card(client,NEXT)['lesson']['start'])==('08:20','08:20')


def test_editor_apply_accepts_effective_date(client,clock):
    revision=api.read('demo').revision
    preview=client.post('/api/editor/preview?workspace=demo',json=dict(revision=revision,action='move',ids=['eng'],day=1,period=1)).json()
    assert preview['canApply']
    applied=client.post(f'/api/editor/apply?workspace=demo&effectiveFrom={NEXT}',json=dict(revision=revision,token=preview['token']))
    assert applied.status_code==200,applied.text
    assert current_since(client)==NEXT
    assert card(client)['lesson']['start']=='08:20'
    assert not [r for r in lessons(client,NEXT) if r['lesson']['subject']==ENGLISH]
    assert card(client,'2026-08-25')['lesson']['start']=='08:20'


def test_portal_day_schedule_follows_versions(client,clock):
    enable(client);resave(swap_english,date(2026,8,24))
    def english(day):
        response=client.get('/api/portal/schedule',params=dict(workspace='demo',view='student',entityId='s1',day=day));assert response.status_code==200,response.text
        return [r['start'] for r in response.json()['rows'] if r['subject']==ENGLISH]
    assert english(DAY)==['08:20'] and english(NEXT)==['10:30']


def test_version_keeps_teachers_deleted_since(client,clock):
    def retire(state):
        for w in state.workloads:
            if w.teacherId=='t2':w.teacherId='t3'
        state.teachers=[t for t in state.teachers if t.id!='t2']
    resave(retire,date(2026,8,24))
    assert card(client)['lesson']['teachers']=='Учитель английского'
    assert card(client,NEXT)['lesson']['teachers']=='Учитель истории'


def test_published_roster_and_time_frozen_after_retroactive_change(client,clock,monkeypatch):
    enable(client);create(client,'parent')
    parent=signed('parent');key=lesson(client)['lessonKey']
    assert write(client,graded(entry(client,key)),publish=True).status_code==200
    shown=report(parent).json()
    def retroactive(state):
        state.students.append(Student(id='new',name='Новый ученик',classId='c-demo-a'))
        next(p for p in state.placements if p.id=='eng').period=3
    resave(retroactive)
    fresh=entry(client,key)
    assert fresh['rosterFrozen'] and not fresh['needsSync'] and fresh['writable']
    assert roster(fresh)=={'s1','s2'} and fresh['document']['lesson']['start']=='08:20' and fresh['currentDate']==DAY
    assert report(parent).json()==shown
    shown_card=card(client)
    assert shown_card['lessonKey']==key and shown_card['lesson']['start']=='08:20' and shown_card['studentCount']==2 and shown_card['frozen']
    fresh['document']['content']['records'][0]['grades'][0]['score']=3
    assert write(client,fresh,publish=True).status_code==200
    assert report(parent).json()['rows'][0]['record']['grades'][0]['score']==3
    # On the lesson day itself the old review flow is kept.
    monkeypatch.setattr(journal,'today',lambda:date(2026,8,17))
    same_day=entry(client,key);assert same_day['needsSync'] and not same_day['rosterFrozen']


def test_frozen_publication_ignores_sync_until_withdrawn(client,clock):
    key=lesson(client)['lessonKey']
    assert write(client,graded(entry(client,key)),publish=True).status_code==200
    resave(lambda s:s.students.append(Student(id='new',name='Новый ученик',classId='c-demo-a')))
    synced=entry(client,key,syncRoster=True)
    assert not synced['needsSync'] and roster(synced)=={'s1','s2'}
    saved=write(client,synced,publish=True,syncRoster=True);assert saved.status_code==200,saved.text
    assert roster(saved.json())=={'s1','s2'}
    withdrawn=client.post('/api/journal/withdraw?workspace=demo',json=dict(lessonKey=key,version=saved.json()['version']));assert withdrawn.status_code==200,withdrawn.text
    reopened=entry(client,key)
    assert reopened['needsSync'] and not reopened['rosterFrozen']
    assert roster(entry(client,key,syncRoster=True))=={'s1','s2','new'}


def test_moved_publication_is_not_frozen(client,clock):
    key=lesson(client)['lessonKey']
    assert write(client,graded(entry(client,key)),publish=True).status_code==200
    resave(lambda s:setattr(s,'datedLessons',[DatedLesson(id='move',name='Перенос',kind='change',placementId='eng',sourceDate=date(2026,8,17),onDate=date(2026,8,18),startTime='14:00',endTime='14:40')]))
    moved=entry(client,key);assert moved['needsSync'] and not moved['rosterFrozen'] and moved['currentDate']=='2026-08-18'


def test_removed_placement_leaves_a_read_only_frozen_archive(client,clock):
    key=lesson(client)['lessonKey']
    assert write(client,graded(entry(client,key)),publish=True).status_code==200
    resave(lambda s:setattr(s,'placements',[p for p in s.placements if p.id!='eng']))
    archive=entry(client,key)
    assert archive['rosterFrozen'] and not archive['needsSync'] and not archive['writable'] and archive['currentDate'] is None
    assert archive['canWithdraw']


def test_draft_past_lesson_resolves_to_old_version(client,clock):
    key=lesson(client)['lessonKey']
    saved=write(client,entry(client,key));assert saved.status_code==200,saved.text
    resave(swap_english,date(2026,8,24))
    old=card(client)
    assert old['lessonKey']==key and old['lesson']['start']=='08:20' and not old['archived'] and old['version']==1
    record=entry(client,key);assert not record['needsSync'] and record['writable'] and not record['rosterFrozen']
    assert write(client,record).status_code==200
    later=lessons(client,NEXT)
    assert key not in {r['lessonKey'] for r in later}
    assert [r['lesson']['start'] for r in later if r['lesson']['subject']==ENGLISH]==['10:30']


def test_transfer_keeps_class_of_the_day(client,clock):
    resave(lambda s:s.placements.append(Placement(id='eng-d',workloadId='w1',day=0,period=3,roomId='r1')))
    resave(lambda s:setattr(pupil(s,'s1'),'classId','c-demo-d'),date(2026,8,24))
    first=opened(client,card(client)['lessonKey'])
    names={s['id']:(s['classId'],s['className']) for s in first['document']['lesson']['students']}
    assert names=={'s1':('c-demo-a','10A'),'s2':('c-demo-a','10A')}
    assert roster(opened(client,card(client,NEXT)['lessonKey'],NEXT))=={'s2'}
    assert roster(opened(client,card(client,NEXT,group='10D')['lessonKey'],NEXT))=={'s1','s3','s4'}
    assert roster(opened(client,card(client,group='10D')['lessonKey']))=={'s3','s4'}


def test_mid_year_pupil_and_withdrawal(client,clock):
    def arrive(state):
        state.students.append(Student(id='new',name='Новый ученик',classId='c-demo-a'));pupil(state,'s2').active=False
    resave(arrive,date(2026,8,24))
    first=card(client);assert first['studentCount']==2
    assert roster(opened(client,first['lessonKey']))=={'s1','s2'}
    assert roster(opened(client,card(client,NEXT)['lessonKey'],NEXT))=={'s1','new'}


def test_coverage_and_attendance_stable_across_timetable_change(client,clock):
    enable(client);create(client,'teacher')
    for subject in [ENGLISH,'Математика']:
        assert write(client,graded(entry(client,lesson(client,subject)['lessonKey'])),publish=True).status_code==200
    period=dict(end='2026-08-23');attendance=lambda:client.post('/api/journal/attendance/report?workspace=demo',json=dict(start=DAY,end='2026-08-23')).json()
    before,totals=cov(client,**period).json(),attendance()
    assert before['counts']['complete']+before['counts']['incomplete']==2 and before['counts']['stale']==0
    def new_template(state):
        state.placements=[Placement(id='eng3',workloadId='w0',day=0,period=1,roomId='r0'),Placement(id='math3',workloadId='w4',day=0,period=2,roomId='r1')]
        state.workloads[0].teacherId='t3'
    resave(new_template,date(2026,8,24))
    resave(lambda s:s.students.append(Student(id='new',name='Новый ученик',classId='c-demo-a')))
    after=cov(client,**period).json()
    assert after['counts']==before['counts'] and pairs(after)==pairs(before)
    assert attendance()['totals']==totals['totals']
    teacher=cov(signed('teacher'),mode='teaching').json()
    assert english_row(teacher)['canOpen'] and teacher['counts']['orphaned']==0
    later=cov(client,start=NEXT,end=NEXT).json()
    assert len(later['items'])==2 and {r['status'] for r in later['items']}=={'missing'}


def test_mid_year_pupil_coverage(client,clock):
    resave(lambda s:s.students.append(Student(id='new',name='Новый ученик',classId='c-demo-a')),date(2026,8,24))
    row=english_row(cov(client).json());assert row['pupils']==2 and row['missingPupils']==2
    assert {p['id'] for p in cov(client,'details',lessonKey=row['lessonKey'],day=DAY).json()['pupils']}=={'s1','s2'}
    later=english_row(cov(client,start=NEXT,end=NEXT).json());assert later['pupils']==3
    assert 'new' in {p['id'] for p in cov(client,'details',start=NEXT,end=NEXT,lessonKey=later['lessonKey'],day=NEXT).json()['pupils']}


def test_coverage_period_spanning_a_boundary_resolves_each_segment(client,clock):
    resave(swap_english,date(2026,8,24))
    value=cov(client,start=DAY,end='2026-08-31').json()
    english=sorted((r['date'],r['start']) for r in value['items'] if r['subject']==ENGLISH)
    assert english==[('2026-08-17','08:20'),('2026-08-24','10:30'),('2026-08-31','10:30')]


def test_group_roster_change_is_dated(client,clock):
    resave(lambda s:setattr(next(g for g in s.groups if g.id=='g-math'),'studentIds',['s2','s3']),date(2026,8,24))
    math=lambda day:card(client,day,'Математика','Математика · группа A')['lessonKey']
    assert roster(opened(client,math(DAY)))=={'s1','s3'}
    assert roster(opened(client,math(NEXT),NEXT))=={'s2','s3'}


def test_undo_and_redo_restore_enrollment_periods(client,clock):
    resave(english_d)
    resave(lambda s:setattr(pupil(s,'s1'),'classId','c-demo-d'),date(2026,8,24))
    assert rosters(client)==moved(NEXT)
    undone=client.post('/api/undo?workspace=demo',json=dict(revision=api.read('demo').revision));assert undone.status_code==200,undone.text
    assert pupil(api.read('demo'),'s1').classId=='c-demo-a' and rosters(client)==START
    redone=client.post('/api/redo?workspace=demo',json=dict(revision=undone.json()['revision']));assert redone.status_code==200,redone.text
    # Redo is retroactive within the current version, like a dateless save: it does not remember the date.
    assert pupil(api.read('demo'),'s1').classId=='c-demo-d' and rosters(client)==moved(DAY,NEXT)
    assert current_since(client)==DAY
    # With no date bound the periods are not consulted; a later dated save for someone else makes DAY read them, and s1 must still be in 10D there.
    resave(lambda s:setattr(pupil(s,'s4'),'classId','c-demo-a'),date(2026,8,24))
    assert members(client,DAY,'10A')=={'s2'} and members(client,DAY,'10D')=={'s1','s3','s4'}


def test_first_dated_save_on_an_upgraded_database_backfills_from_the_state_before(client,clock):
    drop_history_tables()
    api.journal.snapshots.cache.clear()
    resave(lambda s:setattr(pupil(s,'s1'),'classId','c-demo-d'),date(2026,8,24))
    assert current_since(client)==DAY
    assert members(client,DAY)=={'s1','s2'} and members(client,NEXT)=={'s2'}
    math=lambda day:members(client,day,'Математика · группа A','Математика')
    assert math(DAY)==math(NEXT)=={'s1','s3'}


def test_effective_from_checks_earlier_days_against_the_replaced_template(client,clock):
    resave(lambda s:setattr(s,'datedLessons',[DatedLesson(id='move',name='Перенос',kind='change',placementId='eng',sourceDate=date(2026,8,17),onDate=date(2026,8,18),startTime='14:00',endTime='14:40')]))
    state=client.get('/api/state?workspace=demo').json()
    state['placements']=[dict(state['placements'][0],id='eng2',period=3)]+state['placements'][1:]
    # Dateless, the past move would point at a placement that no longer exists; dated, the archived template still has it.
    assert client.put('/api/state?workspace=demo',json=state).status_code==422
    assert current_since(client)==DAY
    saved=client.put(f'/api/state?workspace=demo&effectiveFrom={NEXT}',json=state);assert saved.status_code==200,saved.text
    assert current_since(client)==NEXT
    assert (card(client)['lesson']['start'],card(client,NEXT)['lesson']['start'])==('08:20','10:30')


def test_effective_from_still_rejects_new_conflicts_on_earlier_days(client,clock):
    state=api.read('demo');eng=next(p for p in state.placements if p.id=='eng');w0=next(w for w in state.workloads if w.id==eng.workloadId)
    state.datedLessons=[DatedLesson(id='double',name='Двойной урок',kind='add',onDate=date(2026,8,17),groupId='g-d',profileId='primary',subjectId=w0.subjectId,
                                    startTime='08:20',endTime='09:05',teacherIds=[w0.teacherId],roomIds=[eng.roomId])]
    body=state.model_dump(mode='json')
    revision=api.read('demo').revision
    def put(value=None,**edit):
        return client.put('/api/state?workspace=demo'+(f'&effectiveFrom={value}' if value else ''),json=body|edit)
    # No template change is stored, so the earlier day is checked against the candidate itself.
    for value in [None,'2026-09-01',NEXT]:
        rejected=put(value);assert rejected.status_code==422,(value,rejected.text)
    # With a template change the earlier day is checked against the replaced template.
    swapped=[dict(body['placements'][0],id='eng2',period=3)]+body['placements'][1:]
    rejected=put(NEXT,placements=swapped);assert rejected.status_code==422,rejected.text
    assert api.read('demo').revision==revision and current_since(client)==DAY and not api.read('demo').datedLessons


def test_dated_deactivation_stops_pupil_writes_but_a_transfer_does_not(client,clock,monkeypatch):
    for module in [quizzes,quiz_bank,quiz_attempts,quiz_catalog]:monkeypatch.setattr(module,'now',lambda:NOW)
    quiz,student,parent,teacher,_=ready(client);key=lesson(client)['lessonKey']
    assert homework(student,key)['canSubmit'] and detail(student)['canStart'] and len(student.get('/api/journal/quizzes?workspace=demo').json()['items'])==1
    # A transferred pupil attended the old lesson and still acts on it.
    resave(lambda s:setattr(pupil(s,'s1'),'classId','c-demo-d'),date(2026,8,24))
    assert homework(student,key)['canSubmit'] and detail(student)['canStart']
    resave(lambda s:setattr(pupil(s,'s1'),'active',False),date(2026,8,24))
    # Journal rosters keep the pupil for the days before the departure ...
    assert roster(opened(client,key))=={'s1','s2'}
    # ... but a pupil who has left the school no longer writes to it.
    current=homework(student,key);assert not current['canSubmit']
    refused=answer(student,key,current);assert refused.status_code==403,refused.text
    assert not detail(student)['canStart']
    refused=start(student,quiz);assert refused.status_code==403,refused.text
    assert student.get('/api/journal/quizzes?workspace=demo').json()['items']==[]
    assert student.get('/api/journal/quizzes?workspace=demo&history=true').json()['items'][0]['readOnly']
    assert homework(parent,key)['canSubmit'] is False


def test_teaching_scope_includes_classes_taught_before_a_dated_change(client,clock):
    enable(client);create(client,'teacher')
    def retire(state):
        for w in state.workloads:
            if w.teacherId=='t2':w.teacherId='t3'
    resave(retire,date(2026,8,24))
    teacher=signed('teacher')
    # The teacher taught English in both classes before the change and has no lessons after it.
    value=teacher.get('/api/journal/coverage/options?workspace=demo');assert value.status_code==200,value.text
    assert [c['id'] for c in value.json()['classes']['teaching']]==['c-demo-a','c-demo-d']
