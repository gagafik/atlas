"""The next real lesson of a class, and one published assignment given to the same subject in other classes."""
from datetime import date, timedelta

import pytest
from backend import api, assignment_files
from backend.access import now
from backend.models import DatedLesson, Event, Placement, ScheduleDay
from backend.portal_cache import Snapshot
from backend.homework_parallel import next_lesson
from test_access import client, enable, create, signed
from test_assignment_files import upload, download, save_materials
from test_homework import get, target
from test_journal import DAY, entry, lesson, report, write
from test_journal_periods import period, close, period_clock

FIRST='00000000-0000-4000-8000-0000000000a1'
SECOND='00000000-0000-4000-8000-0000000000a2'
PARALLEL='/api/journal/homework-parallel'
NEXT='/api/journal/homework-next-lesson'


def timetable(extra=()):
    """English of 10A on Monday and Wednesday, of 10D on Monday (third lesson) and Thursday."""
    state=api.read('demo')
    state.placements+=[Placement(id='eng-wed',workloadId='w0',day=2,period=1,roomId='r0'),Placement(id='eng-d',workloadId='w1',day=0,period=3,roomId='r2'),
                       Placement(id='eng-d-thu',workloadId='w1',day=3,period=1,roomId='r0'),*extra]
    api.save('demo',state)


def prepare(client, twin=False, **fields):
    """10A English of DAY is published with a task and one material; returns the saved record and the signed-in teacher.
    With twin the material is a second upload of the same bytes: its row points to the first upload's stored file."""
    enable(client);create(client,'teacher');create(client,'teacher',login='unrelated',grants=[dict(workspace='demo',view='teacher',entityIds=['t1'])])
    create(client,'student',login='pupil-d',grants=[dict(workspace='demo',view='student',entityIds=['s3'])])
    timetable();teacher=signed('teacher')
    record=entry(teacher,lesson(teacher)['lessonKey'])
    if twin: upload(teacher,record).raise_for_status()
    material=upload(teacher,record).json()
    saved=save_materials(teacher,record,[material['id']],homework='Упражнение 5',dueDate='2026-08-19',homeworkSubmission='online',**fields)
    return saved,material,teacher,signed('pupil-d')


def candidates(client, source, **params):
    response=client.get(PARALLEL,params=dict(workspace='demo',lessonKey=source['lessonKey'],day=DAY)|params)
    assert response.status_code==200,response.text
    return response.json()


def publish(client, source, payload, targets, request=FIRST, **extra):
    body=dict(requestId=request,lessonKey=source['lessonKey'],day=DAY,revision=payload['revision'],sourceVersion=source['version'],targets=targets)|extra
    return client.post(PARALLEL+'?workspace=demo',json=body)


def choose(candidate, due=None):
    return dict(lessonKey=candidate['lessonKey'],day=candidate['day'],version=candidate['version'],dueDate=due)


def counts():
    with api.connect() as db:
        return tuple(db.execute(f'SELECT count(*) FROM {table}').fetchone()[0] for table in ['journal_history','journal_entries','assignment_files','homework_requests'])


def following(client, source, **params):
    return client.get(NEXT,params=dict(workspace='demo',lessonKey=source['lessonKey'],day=DAY)|params)


def test_candidates_list_own_parallel_course(client):
    source,_,teacher,pupil=prepare(client)
    listing=candidates(teacher,source)
    assert listing['source']['nextLessonDate']=='2026-08-19' and listing['source']['version']==1 and listing['source']['publishedVersion']==1
    assert len(listing['targets'])==1
    item=listing['targets'][0]
    assert (item['group'],item['classes'],item['parallel'],item['day'],item['available'],item['reason'])==('10D',['10D'],True,'2026-08-17',True,'')
    assert item['nextLessonDate']=='2026-08-20' and item['version']==0 and item['teachers']
    for denied in [signed('unrelated'),pupil]:
        assert denied.get(PARALLEL,params=dict(workspace='demo',lessonKey=source['lessonKey'],day=DAY)).status_code==403
    # An administrator sees the classes of the course teachers.
    assert len(candidates(client,source)['targets'])==1
    assert following(teacher,source).status_code==200


def test_publish_reuses_files_and_creates_separate_homework(client):
    source,material,teacher,pupil=prepare(client)
    item=candidates(teacher,source)['targets'][0]
    response=publish(teacher,source,candidates(teacher,source),[choose(item,'2026-08-20')])
    assert response.status_code==200,response.text
    done=response.json()['published'][0];assert done['group']=='10D' and done['version']==1 and done['publishedVersion']==1
    record=entry(teacher,item['lessonKey'])
    assert record['publishedVersion']==1 and record['document']['content']['homework']=='Упражнение 5'
    work=get(pupil,item['lessonKey'],'s3')
    assert work['homework']=='Упражнение 5' and work['dueDate']=='2026-08-20' and work['submission']=='online'
    copied=work['assignmentFiles'];assert len(copied)==1 and copied[0]['filename']==material['filename'] and copied[0]['id']!=material['id']
    with api.connect() as db:
        copy=db.execute('SELECT lesson_key,blob_id,owner_id,day FROM assignment_files WHERE id=?',(copied[0]['id'],)).fetchone()
        original=db.execute('SELECT lesson_key,blob_id FROM assignment_files WHERE id=?',(material['id'],)).fetchone()
        assert copy[0]==item['lessonKey'] and copy[1]==(original[1] or material['id']) and copy[3]==DAY
        assert len(list(assignment_files.root_for(db).glob('*.bin')))==1
    assert download(pupil,copied[0]['id'],'s3').content==b'Teacher materials'
    assert download(teacher,copied[0]['id']).content==b'Teacher materials'
    assert download(pupil,material['id'],'s3').status_code==404
    # Each lesson has its own homework and its own submissions.
    answer=dict(studentId='s3',version=0,assignment=work['assignment'],action='submit',text='Решение 10D',fileIds=[])
    assert pupil.put(target(item['lessonKey'],'s3'),json=answer).status_code==200
    view=teacher.get(f"/api/journal/homework/{source['lessonKey']}?workspace=demo").json()
    assert [s['status'] for s in view['students']]==['not_submitted','not_submitted'] and view['dueDate']=='2026-08-19'
    assert teacher.get(f"/api/journal/homework/{item['lessonKey']}?workspace=demo").json()['students'][0]['status']=='submitted'
    # The source lesson itself is untouched.
    assert entry(teacher,source['lessonKey'])['version']==source['version']
    with api.connect() as db:
        assert db.execute("SELECT count(*) FROM audit WHERE action='homework.parallel_published'").fetchone()[0]==1


@pytest.mark.parametrize('twin',[False,True])
def test_copied_files_stay_bound_after_collection_and_share_the_stored_file(client, twin):
    source,material,teacher,pupil=prepare(client,twin=twin)
    item=candidates(teacher,source)['targets'][0]
    assert publish(teacher,source,candidates(teacher,source),[choose(item,'2026-08-20')]).status_code==200
    copied=get(pupil,item['lessonKey'],'s3')['assignmentFiles'][0]['id']
    with api.connect() as db:
        blob=db.execute('SELECT coalesce(blob_id,id) FROM assignment_files WHERE id=?',(material['id'],)).fetchone()[0]
        assert (blob!=material['id'])==twin
        assert db.execute('SELECT coalesce(blob_id,id) FROM assignment_files WHERE id=?',(copied,)).fetchone()[0]==blob
        assert [p.name for p in assignment_files.root_for(db).glob('*.bin')]==[blob+'.bin']
        # A day later only unsaved uploads are collected: the copy is bound to its lesson like a saved file, and so is the source.
        db.execute('UPDATE assignment_files SET created=?',((now()-timedelta(days=2)).isoformat(),))
        assignment_files.collect_unused(db)
        assert {material['id'],copied}<={r[0] for r in db.execute('SELECT id FROM assignment_files')}
        assert (assignment_files.root_for(db)/(blob+'.bin')).is_file()
    assert download(pupil,copied,'s3').content==b'Teacher materials'
    assert download(teacher,material['id']).content==b'Teacher materials'


def test_publish_refuses_when_the_stored_file_is_gone_or_the_lesson_is_full(client, monkeypatch):
    source,material,teacher,pupil=prepare(client)
    listing=candidates(teacher,source);item=listing['targets'][0];before=counts()
    with api.connect() as db: path=assignment_files.root_for(db)/(material['id']+'.bin')
    stored=path.read_bytes();path.unlink()
    gone=publish(teacher,source,listing,[choose(item,'2026-08-20')]);assert gone.status_code==409 and 'недоступен' in gone.text
    assert counts()==before
    path.write_bytes(stored)
    with monkeypatch.context() as limited:
        limited.setattr(assignment_files,'MAX_TASK',0)
        full=publish(teacher,source,listing,[choose(item,'2026-08-20')],request=SECOND);assert full.status_code==413 and 'лимит' in full.text
    assert counts()==before
    assert publish(teacher,source,listing,[choose(item,'2026-08-20')],request=SECOND).status_code==200
    assert download(pupil,get(pupil,item['lessonKey'],'s3')['assignmentFiles'][0]['id'],'s3').content==stored


def test_publish_keeps_target_draft_marks_private(client):
    source,_,teacher,pupil=prepare(client)
    item=candidates(teacher,source)['targets'][0]
    draft=entry(teacher,item['lessonKey']);draft['document']['content']['records'][0]['attendance']='present'
    saved=write(teacher,draft).json();assert saved['version']==1 and saved['publishedVersion'] is None
    item=candidates(teacher,source)['targets'][0];assert item['version']==1 and item['available']
    assert publish(teacher,source,candidates(teacher,source),[choose(item)]).status_code==200
    record=entry(teacher,item['lessonKey'])
    # Two revisions: the publication first, then the draft with its private marks.
    assert record['version']==3 and record['publishedVersion']==2
    assert record['document']['content']['records'][0]['attendance']=='present' and record['document']['content']['homework']=='Упражнение 5'
    row=report(pupil,'s3').json()['rows'][0]
    assert row['content']['homework']=='Упражнение 5' and row['record']['attendance']=='unmarked'
    with api.connect() as db:
        assert db.execute("SELECT count(*) FROM journal_history WHERE lesson_key=?",(item['lessonKey'],)).fetchone()[0]==3


def test_publish_refusals_write_nothing(client):
    source,_,teacher,pupil=prepare(client,homeworkLatePolicy='block')
    listing=candidates(teacher,source);item=listing['targets'][0];before=counts()
    # A block on late work needs a due date in every lesson.
    refused=publish(teacher,source,listing,[choose(item)]);assert refused.status_code==422 and 'срок' in refused.text
    assert counts()==before
    # Stale target version.
    other=entry(teacher,item['lessonKey']);other['document']['content']['topic']='Тема';write(teacher,other).raise_for_status()
    before=counts();stale=publish(teacher,source,candidates(teacher,source),[choose(item,'2026-08-20')]);assert stale.status_code==409
    assert counts()==before
    # The target already has homework.
    again=entry(teacher,item['lessonKey']);again['document']['content']['homework']='Своё задание';write(teacher,again).raise_for_status()
    listing=candidates(teacher,source);item=listing['targets'][0]
    assert not item['available'] and 'уже есть домашнее задание' in item['reason']
    before=counts();taken=publish(teacher,source,listing,[choose(item,'2026-08-20')]);assert taken.status_code==409 and 'уже есть' in taken.text
    assert counts()==before
    # A lesson that is not the first one of an own class is not offered.
    wednesday=lesson(teacher,day='2026-08-19')['lessonKey']
    elsewhere=publish(teacher,source,listing,[dict(lessonKey=wednesday,day='2026-08-19',version=0,dueDate='2026-08-20')]);assert elsewhere.status_code==403
    assert publish(teacher,source,listing,[choose(item),choose(item)]).status_code==422
    assert publish(teacher,source,listing,[dict(choose(item),lessonKey=source['lessonKey'])]).status_code==422
    assert counts()==before


def test_publish_requires_a_published_source_and_matching_version(client):
    source,_,teacher,pupil=prepare(client)
    listing=candidates(teacher,source);item=listing['targets'][0];before=counts()
    edited=entry(teacher,source['lessonKey']);edited['document']['content']['homework']='Новая формулировка'
    changed=write(teacher,edited).json()
    # The draft moved on: the old version is stale, and the new one is not published yet.
    assert publish(teacher,source,listing,[choose(item,'2026-08-20')]).status_code==409
    unpublished=publish(teacher,changed,candidates(teacher,changed),[choose(item,'2026-08-20')])
    assert unpublished.status_code==409 and 'Сначала опубликуйте' in unpublished.text
    assert counts()[2:]==before[2:]
    republished=write(teacher,changed,publish=True).json()
    assert publish(teacher,republished,candidates(teacher,republished),[choose(item,'2026-08-20')],request=SECOND).status_code==200
    assert get(pupil,item['lessonKey'],'s3')['homework']=='Новая формулировка'


def test_publish_refuses_other_teachers_courses_and_substitutes(client):
    source,_,teacher,pupil=prepare(client)
    listing=candidates(teacher,source);item=listing['targets'][0];before=counts()
    # A substitute holds the 10D lesson that day: it is listed, but not available.
    state=api.read('demo')
    state.datedLessons=[DatedLesson(id='sub',name='Замена',kind='change',placementId='eng-d',sourceDate=DAY,onDate=DAY,teacherIds=['t1'])]
    api.save('demo',state)
    listing=candidates(teacher,source);item=listing['targets'][0]
    assert not item['available'] and 'другой учитель' in item['reason']
    assert publish(teacher,source,listing,[choose(item,'2026-08-20')]).status_code==409
    assert counts()==before
    # Another teacher takes the 10D course: it is no longer an own class.
    state=api.read('demo');state.datedLessons=[];next(w for w in state.workloads if w.id=='w1').teacherId='t1';api.save('demo',state)
    listing=candidates(teacher,source);assert listing['targets']==[]
    assert publish(teacher,source,listing,[choose(item,'2026-08-20')]).status_code==403
    assert counts()==before


def test_a_substitute_at_the_source_lesson_is_not_a_course_teacher(client):
    source,_,teacher,pupil=prepare(client)
    item=candidates(teacher,source)['targets'][0];before=counts()
    state=api.read('demo')
    state.datedLessons=[DatedLesson(id='sub',name='Замена',kind='change',placementId='eng',sourceDate=DAY,onDate=DAY,teacherIds=['t1'])]
    api.save('demo',state)
    substitute=signed('unrelated');revision=api.read('demo').revision
    # The substitute may open the lesson, but giving the course's homework to other classes is the course teacher's.
    assert entry(substitute,source['lessonKey'])['document']['lesson']['groupId']=='g-a'
    for denied in [substitute.get(PARALLEL,params=dict(workspace='demo',lessonKey=source['lessonKey'],day=DAY)),
                   publish(substitute,source,dict(revision=revision),[choose(item,'2026-08-20')])]:
        assert denied.status_code==403 and 'учитель курса' in denied.text
    assert counts()==before
    # The same teacher is offered the classes once the courses are theirs.
    state=api.read('demo');state.datedLessons=[]
    for w in state.workloads:
        if w.id in ('w0','w1'): w.teacherId='t1'
    api.save('demo',state)
    listing=candidates(substitute,source);assert [t['group'] for t in listing['targets']]==['10D'] and listing['targets'][0]['available']
    assert publish(substitute,source,listing,[choose(listing['targets'][0],'2026-08-20')]).status_code==200
    assert teacher.get(PARALLEL,params=dict(workspace='demo',lessonKey=source['lessonKey'],day=DAY)).status_code==403


def test_the_course_lesson_is_never_taken_from_another_subject_of_the_class(client):
    source,_,teacher,pupil=prepare(client)
    # History comes first on Monday in 10D and before the English on Wednesday in 10A.
    state=api.read('demo')
    next(p for p in state.placements if p.id=='eng-wed').period=2
    state.placements+=[Placement(id='hist-d',workloadId='w3',day=0,period=1,roomId='r1'),Placement(id='hist-wed',workloadId='w2',day=2,period=1,roomId='r1')]
    api.save('demo',state)
    item=candidates(teacher,source)['targets'][0]
    assert (item['day'],item['available'])==('2026-08-17',True)
    held=entry(teacher,item['lessonKey'])['document']['lesson'];assert (held['groupId'],held['subjectId'])==('g-d','sub2')
    assert publish(teacher,source,candidates(teacher,source),[choose(item,'2026-08-20')]).status_code==200
    english=lesson(teacher,day='2026-08-19')['lesson']
    response=following(teacher,source).json()
    assert (response['date'],response['start'])==('2026-08-19',english['start'])
    found=next_lesson(Snapshot(api.read('demo')),'g-a','sub2',date(2026,8,17))
    assert (found['subjectId'],found['start'])==('sub2',english['start'])


def test_publish_to_a_closed_period_is_refused(client):
    source,_,teacher,pupil=prepare(client)
    item=candidates(teacher,source)['targets'][0]
    close(client,period(client))
    listing=candidates(teacher,source);item=listing['targets'][0]
    assert not item['available'] and 'закрыт' in item['reason']
    before=counts();assert publish(teacher,source,listing,[choose(item,'2026-08-20')]).status_code==409
    assert counts()==before


def test_publish_is_idempotent_per_request_id(client):
    source,_,teacher,pupil=prepare(client)
    listing=candidates(teacher,source);item=listing['targets'][0]
    first=publish(teacher,source,listing,[choose(item,'2026-08-20')]);assert first.status_code==200
    before=counts()
    again=publish(teacher,source,listing,[choose(item,'2026-08-20')]);assert again.status_code==200 and again.json()==first.json()
    assert counts()==before
    different=publish(teacher,source,listing,[choose(item,'2026-08-21')]);assert different.status_code==409 and 'другим действием' in different.text
    assert counts()==before


def test_publish_offline_mode_is_copied(client):
    source,_,teacher,pupil=prepare(client)
    edited=dict(source);edited['document']['content']['homeworkSubmission']='offline'
    source=write(teacher,edited,publish=True).json()
    listing=candidates(teacher,source);item=listing['targets'][0]
    assert publish(teacher,source,listing,[choose(item,'2026-08-20')]).status_code==200
    work=get(pupil,item['lessonKey'],'s3');assert work['submission']=='offline' and not work['canSubmit']
    answer=dict(studentId='s3',version=0,assignment=work['assignment'],action='submit',text='Решение',fileIds=[])
    refused=pupil.put(target(item['lessonKey'],'s3'),json=answer);assert refused.status_code==409 and 'без онлайн-отправки' in refused.text


def test_next_lesson_by_dated_timetable(client):
    source,_,teacher,pupil=prepare(client)
    def answer():
        response=following(teacher,source);assert response.status_code==200,response.text
        return response.json()
    assert answer()['date']=='2026-08-19' and answer()['start'] and answer()['searchedUntil']=='2026-10-16'
    def set_state(**values):
        state=api.read('demo')
        for name,value in values.items(): setattr(state,name,value)
        api.save('demo',state)
    set_state(events=[Event(id='h',name='Праздник',start=date(2026,8,19),end=date(2026,8,19),kind='holiday',effect='cancel')])
    assert answer()['date']=='2026-08-24'
    set_state(events=[],scheduleDays=[ScheduleDay(id='sd',name='Среда во вторник',onDate=date(2026,8,18),day=2)])
    assert answer()['date']=='2026-08-18'
    # A date that runs a weekday without the course takes the lesson away.
    set_state(scheduleDays=[ScheduleDay(id='sd',name='Пятница в среду',onDate=date(2026,8,19),day=4)])
    assert answer()['date']=='2026-08-24'
    set_state(scheduleDays=[],datedLessons=[DatedLesson(id='mv',name='Перенос',kind='change',changeType='move',placementId='eng-wed',sourceDate=date(2026,8,19),onDate=date(2026,8,21))])
    assert answer()['date']=='2026-08-21'
    set_state(datedLessons=[DatedLesson(id='cx',name='Отмена',kind='cancel',placementId='eng-wed',sourceDate=date(2026,8,19),onDate=date(2026,8,19))])
    assert answer()['date']=='2026-08-24'
    # A lesson on the same day is not "the next one".
    set_state(datedLessons=[],placements=[*api.read('demo').placements,Placement(id='eng-late',workloadId='w0',day=0,period=4,roomId='r0')])
    assert answer()['date']=='2026-08-19'


def test_next_lesson_none_and_permissions(client):
    source,_,teacher,pupil=prepare(client)
    state=api.read('demo');state.placements=[p for p in state.placements if p.id!='eng-wed'];state.settings.yearEnd=date(2026,8,20);api.save('demo',state)
    response=following(teacher,source);assert response.status_code==200
    assert response.json()['date'] is None and response.json()['searchedUntil']=='2026-08-20'
    assert following(signed('unrelated'),source).status_code==403
    assert following(pupil,source).status_code==403
    assert following(teacher,dict(lessonKey='0'*64)).status_code==404
    assert following(client,source).status_code==200


def test_next_lesson_unit_scan_matches_the_course(client):
    prepare(client)
    snap=Snapshot(api.read('demo'))
    found=next_lesson(snap,'g-a','sub2',date(2026,8,17))
    assert (found['groupId'],found['subjectId'],found['date'])==('g-a','sub2','2026-08-19')
    assert next_lesson(snap,'g-d','sub2',date(2026,8,20))['date']=='2026-08-24'
    assert next_lesson(snap,'g-d','sub1',date(2026,8,17)) is None
    assert next_lesson(snap,'g-a','sub2',date(2027,6,11)) is None
