from datetime import date,datetime,timezone
import json
import pytest
from backend import api,journal,journal_coverage,portal_cache,quiz_attempts,quiz_bank,quiz_catalog,quizzes,timetable_history
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


def stints(student,kind='class'):
    with api.connect() as db:
        return db.execute('SELECT target_id,start_day,end_day FROM pupil_enrollments WHERE workspace=? AND student_id=? AND kind=? ORDER BY start_day',('demo',student,kind)).fetchall()


def versions():
    with api.connect() as db:
        return db.execute('SELECT valid_from,valid_to,document FROM timetable_versions WHERE workspace=? ORDER BY valid_from',('demo',)).fetchall()


def current_from():
    with api.connect() as db:return db.execute("SELECT valid_from FROM timetable_current WHERE workspace='demo'").fetchone()


def cov(client,action='report',**extra):
    return client.post(f'/api/journal/coverage/{action}?workspace=demo',json=dict(start=DAY,end=DAY,status='all')|extra)


def english_row(value,**match):
    return next(r for r in value['items'] if r['subject']==ENGLISH and all(r[k]==v for k,v in match.items()))


def pairs(value):return sorted((r['lessonKey'],r['status'],r['pupils'],r['missingPupils']) for r in value['items'])


def test_backfill_and_dateless_save_keep_current_behaviour(client):
    with api.connect() as db:
        classes=db.execute("SELECT student_id,target_id,start_day,end_day FROM pupil_enrollments WHERE workspace='demo' AND kind='class' ORDER BY student_id").fetchall()
        groups=db.execute("SELECT target_id,student_id,start_day,end_day FROM pupil_enrollments WHERE workspace='demo' AND kind='group' ORDER BY target_id,student_id").fetchall()
    assert classes==[('s1','c-demo-a',DAY,None),('s2','c-demo-a',DAY,None),('s3','c-demo-d',DAY,None),('s4','c-demo-d',DAY,None)]
    assert groups==[('g-core','s2',DAY,None),('g-core','s4',DAY,None),('g-math','s1',DAY,None),('g-math','s3',DAY,None)]
    assert current_from()==(DAY,) and versions()==[]
    assert api.journal.snapshots.get('demo').history.bounds==[]
    resave(lambda s:setattr(next(p for p in s.placements if p.id=='eng'),'period',3))
    assert card(client)['lesson']['start']=='10:30'
    assert current_from()==(DAY,) and versions()==[]


def test_capture_merges_and_trims_periods(client,clock):
    def move(class_id,effective=None):
        resave(lambda s:setattr(pupil(s,'s1'),'classId',class_id),effective)
    effective=date(2026,8,24)
    move('c-demo-d',effective)
    assert stints('s1')==[('c-demo-a',DAY,'2026-08-23'),('c-demo-d',NEXT,None)]
    move('c-demo-a',effective)
    assert stints('s1')==[('c-demo-a',DAY,None)]
    move('c-demo-d',effective)
    def remove(state):
        state.students=[s for s in state.students if s.id!='s1']
        for group in state.groups:group.studentIds=[id for id in group.studentIds if id!='s1']
    resave(remove)
    assert stints('s1')==[] and stints('s1','group')==[]
    assert current_from()==(DAY,) and versions()==[]


def test_effective_date_validation_rolls_back(client,clock):
    def stored():
        with api.connect() as db:
            return (db.execute("SELECT revision FROM workspaces WHERE id='demo'").fetchone(),db.execute('SELECT count(*) FROM timetable_versions').fetchone(),
                    db.execute('SELECT * FROM pupil_enrollments ORDER BY 1,2,3,4,5').fetchall(),db.execute('SELECT count(*) FROM audit').fetchone())
    def put(state,value):return client.put(f'/api/state?workspace=demo&effectiveFrom={value}',json=state)
    state=client.get('/api/state?workspace=demo').json()
    state['placements'][0]['period']=3
    before=stored()
    late=put(state,'2026-09-02');assert late.status_code==422 and 'позже сегодняшнего' in late.json()['detail']
    assert stored()==before
    saved=put(state,NEXT);assert saved.status_code==200,saved.text
    state=saved.json();state['placements'][0]['period']=4
    before=stored()
    for value,text in [('2026-08-20','раньше текущей версии'),('2027-07-01','внутри учебного года'),('2026-08-10','внутри учебного года')]:
        rejected=put(state,value);assert rejected.status_code==422 and text in rejected.json()['detail'],rejected.text
        assert stored()==before


def test_history_reader_tolerates_missing_tables_and_stale_revision(client):
    expected=[(r['lesson']['start'],r['lesson']['subject']) for r in lessons(client)]
    with api.connect() as db:
        for table in ['timetable_versions','timetable_current','pupil_enrollments']:db.execute(f'DROP TABLE {table}')
    api.journal.snapshots.cache.clear()
    assert [(r['lesson']['start'],r['lesson']['subject']) for r in lessons(client)]==expected
    resave(lambda s:None)
    assert current_from()==(DAY,) and stints('s1')==[('c-demo-a',DAY,None)]
    state=api.read('demo')
    with pytest.raises(timetable_history.Stale):
        timetable_history.load(str(api.DB.resolve()),'demo',state.model_copy(update=dict(revision=state.revision-1)))
    assert timetable_history.load(str(api.DB.resolve()),'demo',state).state is state
    plain=timetable_history.History(state)
    assert plain.context(date(2026,8,17))[0] is state and plain.context(date(2027,1,1))[0] is state and plain.bounds==[]


def test_dated_template_change_stores_version_and_audit(client,clock):
    state=client.get('/api/state?workspace=demo').json()
    state['placements'][0]=dict(state['placements'][0],id='eng2',period=3)
    saved=client.put(f'/api/state?workspace=demo&effectiveFrom={NEXT}',json=state);assert saved.status_code==200,saved.text
    [(valid_from,valid_to,document)]=versions()
    assert (valid_from,valid_to)==(DAY,NEXT) and 'eng' in {p['id'] for p in json.loads(document)['placements']}
    assert current_from()==(NEXT,)
    with api.connect() as db:detail=db.execute("SELECT detail FROM audit WHERE action='timetable.versioned'").fetchone()
    assert detail and '17.08.2026' in detail[0] and '23.08.2026' in detail[0]
    assert client.get('/api/history?workspace=demo').json()['currentFrom']==NEXT
    undone=client.post('/api/undo?workspace=demo',json=dict(revision=saved.json()['revision']));assert undone.status_code==200,undone.text
    assert [p.id for p in api.read('demo').placements]==['eng','math']
    assert len(versions())==1 and current_from()==(NEXT,)
    assert (card(client)['lesson']['start'],card(client,NEXT)['lesson']['start'])==('08:20','08:20')


def test_editor_apply_accepts_effective_date(client,clock):
    revision=api.read('demo').revision
    preview=client.post('/api/editor/preview?workspace=demo',json=dict(revision=revision,action='move',ids=['eng'],day=1,period=1)).json()
    assert preview['canApply']
    applied=client.post(f'/api/editor/apply?workspace=demo&effectiveFrom={NEXT}',json=dict(revision=revision,token=preview['token']))
    assert applied.status_code==200,applied.text
    assert [(v[0],v[1]) for v in versions()]==[(DAY,NEXT)]
    assert card(client)['lesson']['start']=='08:20'
    assert not [r for r in lessons(client,NEXT) if r['lesson']['subject']==ENGLISH]
    assert card(client,'2026-08-25')['lesson']['start']=='08:20'


def test_snapshot_rows_follow_versions(client,clock):
    resave(swap_english,date(2026,8,24))
    snap=api.journal.snapshots.get('demo')
    first={r['id']:r for r in snap.lesson_rows(date(2026,8,17))};later={r['id']:r for r in snap.lesson_rows(date(2026,8,24))}
    assert first['2026-08-17:eng']['start']=='08:20' and '2026-08-17:eng2' not in first
    assert later['2026-08-24:eng2']['start']=='10:30' and '2026-08-24:eng' not in later
    assert [r['start'] for r in snap.day_rows(date(2026,8,17),'student','s1') if r['subject']==ENGLISH]==['08:20']
    assert [r['start'] for r in snap.day_rows(date(2026,8,24),'student','s1') if r['subject']==ENGLISH]==['10:30']
    assert snap.history.context(date(2026,8,24))[0] is snap.state


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
    assert stints('s1')==[('c-demo-a',DAY,'2026-08-23'),('c-demo-d',NEXT,None)]


def test_mid_year_pupil_and_withdrawal(client,clock):
    def arrive(state):
        state.students.append(Student(id='new',name='Новый ученик',classId='c-demo-a'));pupil(state,'s2').active=False
    resave(arrive,date(2026,8,24))
    first=card(client);assert first['studentCount']==2
    assert roster(opened(client,first['lessonKey']))=={'s1','s2'}
    assert roster(opened(client,card(client,NEXT)['lessonKey'],NEXT))=={'s1','new'}
    assert stints('s2')==[('c-demo-a',DAY,'2026-08-23')]


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


def test_published_past_lesson_is_never_stale_with_default_clock(client,clock):
    key=lesson(client)['lessonKey']
    assert write(client,graded(entry(client,key)),publish=True).status_code==200
    resave(lambda s:s.students.append(Student(id='new',name='Новый ученик',classId='c-demo-a')))
    row=english_row(cov(client).json());assert row['status'] in ['complete','incomplete'] and not row['needsSync'] and row['pupils']==2
    pinned=english_row(cov(client,asOf='2026-08-17T18:00:00Z').json());assert pinned['status']=='stale' and pinned['pupils']==3


def test_mid_year_pupil_coverage(client,clock):
    resave(lambda s:s.students.append(Student(id='new',name='Новый ученик',classId='c-demo-a')),date(2026,8,24))
    row=english_row(cov(client).json());assert row['pupils']==2 and row['missingPupils']==2
    assert {p['id'] for p in cov(client,'details',lessonKey=row['lessonKey'],day=DAY).json()['pupils']}=={'s1','s2'}
    later=english_row(cov(client,start=NEXT,end=NEXT).json());assert later['pupils']==3
    assert 'new' in {p['id'] for p in cov(client,'details',start=NEXT,end=NEXT,lessonKey=later['lessonKey'],day=NEXT).json()['pupils']}


def test_transfer_coverage_by_class_of_the_day(client,clock):
    resave(lambda s:s.placements.append(Placement(id='eng-d',workloadId='w1',day=0,period=3,roomId='r1')))
    resave(lambda s:setattr(pupil(s,'s1'),'classId','c-demo-d'),date(2026,8,24))
    row=english_row(cov(client,classId='c-demo-a').json(),group='10A');assert row['pupils']==2
    assert {p['id'] for p in cov(client,'details',classId='c-demo-a',lessonKey=row['lessonKey'],day=DAY).json()['pupils']}=={'s1','s2'}
    row=english_row(cov(client,start=NEXT,end=NEXT,classId='c-demo-d').json(),group='10D');assert row['pupils']==3
    assert 's1' in {p['id'] for p in cov(client,'details',start=NEXT,end=NEXT,classId='c-demo-d',lessonKey=row['lessonKey'],day=NEXT).json()['pupils']}
    assert english_row(cov(client,classId='c-demo-d').json(),group='10D')['pupils']==2


def test_coverage_period_spanning_a_boundary_resolves_each_segment(client,clock):
    resave(swap_english,date(2026,8,24))
    value=cov(client,start=DAY,end='2026-08-31').json()
    english=sorted((r['date'],r['start']) for r in value['items'] if r['subject']==ENGLISH)
    assert english==[('2026-08-17','08:20'),('2026-08-24','10:30'),('2026-08-31','10:30')]


def test_group_roster_change_is_dated(client,clock):
    resave(lambda s:setattr(next(g for g in s.groups if g.id=='g-math'),'studentIds',['s2','s3']),date(2026,8,24))
    assert stints('s1','group')==[('g-math',DAY,'2026-08-23')] and stints('s2','group')==[('g-core',DAY,None),('g-math',NEXT,None)]
    math=lambda day:card(client,day,'Математика','Математика · группа A')['lessonKey']
    assert roster(opened(client,math(DAY)))=={'s1','s3'}
    assert roster(opened(client,math(NEXT),NEXT))=={'s2','s3'}


def test_undo_and_redo_restore_enrollment_periods(client,clock):
    resave(lambda s:setattr(pupil(s,'s1'),'classId','c-demo-d'),date(2026,8,24))
    assert stints('s1')==[('c-demo-a',DAY,'2026-08-23'),('c-demo-d',NEXT,None)]
    undone=client.post('/api/undo?workspace=demo',json=dict(revision=api.read('demo').revision));assert undone.status_code==200,undone.text
    assert pupil(api.read('demo'),'s1').classId=='c-demo-a' and stints('s1')==[('c-demo-a',DAY,None)]
    redone=client.post('/api/redo?workspace=demo',json=dict(revision=undone.json()['revision']));assert redone.status_code==200,redone.text
    # Redo is retroactive within the current version, like a dateless save: it does not remember the date.
    assert pupil(api.read('demo'),'s1').classId=='c-demo-d' and stints('s1')==[('c-demo-d',DAY,None)]
    assert current_from()==(DAY,) and versions()==[]


def test_first_dated_save_on_an_upgraded_database_backfills_from_the_state_before(client,clock):
    with api.connect() as db:
        for table in ['timetable_versions','timetable_current','pupil_enrollments']:db.execute(f'DROP TABLE {table}')
    api.journal.snapshots.cache.clear()
    resave(lambda s:setattr(pupil(s,'s1'),'classId','c-demo-d'),date(2026,8,24))
    assert stints('s1')==[('c-demo-a',DAY,'2026-08-23'),('c-demo-d',NEXT,None)] and current_from()==(DAY,)
    assert stints('s3')==[('c-demo-d',DAY,None)] and stints('s1','group')==[('g-math',DAY,None)]
    assert roster(opened(client,card(client)['lessonKey']))=={'s1','s2'}


def test_effective_from_checks_earlier_days_against_the_replaced_template(client,clock):
    resave(lambda s:setattr(s,'datedLessons',[DatedLesson(id='move',name='Перенос',kind='change',placementId='eng',sourceDate=date(2026,8,17),onDate=date(2026,8,18),startTime='14:00',endTime='14:40')]))
    state=client.get('/api/state?workspace=demo').json()
    state['placements']=[dict(state['placements'][0],id='eng2',period=3)]+state['placements'][1:]
    # Dateless, the past move would point at a placement that no longer exists; dated, the archived template still has it.
    rejected=client.put('/api/state?workspace=demo',json=state);assert rejected.status_code==422 and 'проблемы в расписании по датам' in rejected.json()['detail']
    assert versions()==[]
    saved=client.put(f'/api/state?workspace=demo&effectiveFrom={NEXT}',json=state);assert saved.status_code==200,saved.text
    assert [(v[0],v[1]) for v in versions()]==[(DAY,NEXT)]


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
        rejected=put(value);assert rejected.status_code==422 and 'проблемы в расписании по датам' in rejected.json()['detail'],(value,rejected.text)
    # With a template change the earlier day is checked against the replaced template.
    swapped=[dict(body['placements'][0],id='eng2',period=3)]+body['placements'][1:]
    rejected=put(NEXT,placements=swapped);assert rejected.status_code==422 and 'проблемы в расписании по датам' in rejected.json()['detail'],rejected.text
    assert api.read('demo').revision==revision and versions()==[] and not api.read('demo').datedLessons


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


def test_teaching_scope_keeps_an_index_for_every_version(client,clock,monkeypatch):
    enable(client);create(client,'teacher')
    for i in range(10):
        resave(lambda s,i=i:setattr(next(p for p in s.placements if p.id=='eng'),'period',2+i%2),date(2026,8,18+i))
    def retire(state):
        for w in state.workloads:
            if w.teacherId=='t2':w.teacherId='t3'
    resave(retire,date(2026,8,28))
    assert len(versions())==11
    overlays,reads,real=[],[],timetable_history.load
    def counting(*args):
        history=real(*args);document=history.document
        if document:history.document=lambda valid_from:(reads.append(valid_from),document(valid_from))[1]
        return history
    monkeypatch.setattr(portal_cache,'load',counting)
    monkeypatch.setattr(timetable_history,'overlay',lambda *args:(overlays.append(1),None)[1])
    teacher=signed('teacher')
    options=lambda:teacher.get('/api/journal/coverage/options?workspace=demo')
    value=options();assert value.status_code==200,value.text
    # The teacher taught English in both classes in the early versions only, and every version is read once, without rebuilding a template.
    assert [c['id'] for c in value.json()['classes']['teaching']]==['c-demo-a','c-demo-d']
    assert overlays==[] and len(reads)==11
    for _ in range(3):assert options().status_code==200
    assert len(reads)==11
    history=api.journal.snapshots.get('demo').history
    assert history.taught({'t2'},date(2026,8,28),date(2026,8,31))==set() and history.taught({'t2'},date(2026,8,17),date(2026,8,31))=={'c-demo-a','c-demo-d'}
    assert len(reads)==11 and overlays==[]
