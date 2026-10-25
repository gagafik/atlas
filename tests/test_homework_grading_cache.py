"""Reuse resolved lessons without retaining stale school, account or journal access."""
from copy import deepcopy
from datetime import date

import pytest
from backend import api,journal,portal_cache
from backend.homework_grading import HomeworkGrading
from backend.models import DatedLesson
from test_access import client,signed
from test_homework import get
from test_homework_grading import ready,options,payload,grade
from test_journal import entry,write,report,DAY
from test_journal_periods import period_clock


def test_warm_grading_does_not_recompute_a_school_day(client,monkeypatch):
    record,sent,_,parent,teacher=ready(client);key=record['lessonKey']
    initial=options(teacher,key)
    def repeated(*args,**kwargs):raise AssertionError('Cached grading recomputed all lessons of the school day.')
    with monkeypatch.context() as patch:
        patch.setattr(journal,'resolve_day',repeated)
        patch.setattr(portal_cache,'resolve_day',repeated)
        assert options(teacher,key)==initial
        response=grade(teacher,key,sent,payload(initial));response.raise_for_status()
        assert response.json()['submitted']['grading']['grade']['score']==9
        assert get(parent,key)['submitted']['grading']['grade']['score']==9
        assert options(teacher,key)['journalVersion']>initial['journalVersion']
    assert report(parent).json()['rows'][0]['record']['grades'][0]['score']==9


@pytest.mark.parametrize('persisted',[False,True])
def test_journal_entry_resolves_only_the_needed_day(client,monkeypatch,persisted):
    record,*_=ready(client);key=record['lessonKey'];expected=DAY
    if persisted:
        state=api.read('demo');expected='2026-08-18'
        state.datedLessons=[DatedLesson(id='move',name='Перенос',kind='change',placementId='eng',sourceDate=DAY,onDate=expected)]
        api.save('demo',state)
    else:
        rows=client.get('/api/journal/day',params=dict(workspace='demo',day=DAY)).json()['lessons']
        key=next(r['lessonKey'] for r in rows if r['lessonKey']!=key)
    original=journal.resolve_day;days=[]
    def counted(state,day,*args,**kwargs):
        days.append(day.isoformat());return original(state,day,*args,**kwargs)
    # Reads resolve through the shared schedule cache (portal_cache); start it cold to count the days it computes.
    monkeypatch.setattr(journal,'resolve_day',counted);monkeypatch.setattr(portal_cache,'resolve_day',counted)
    api.journal.snapshots.get('demo').lesson_days.clear()
    result=entry(client,key)
    assert days==[expected] and result['currentDate']==expected
    assert bool(result['version'])==persisted
    assert result['needsSync']==persisted


def test_changed_teacher_invalidates_warm_lesson_cache(client,monkeypatch):
    # On the lesson day a new teacher must still sync the publication; later days keep the teachers it was published with.
    monkeypatch.setattr(journal,'today',lambda:date(2026,8,17))
    record,sent,_,parent,teacher=ready(client);key=record['lessonKey'];stale=payload(options(teacher,key))
    state=api.read('demo');state.datedLessons=[DatedLesson(id='replace',name='Замена',kind='change',placementId='eng',sourceDate=DAY,onDate=DAY,teacherIds=['t1'])]
    api.save('demo',state)
    # The course teacher still reads the stored lesson, but the journal entry is now the substitute's.
    changed=options(teacher,key);assert not changed['allowed'] and 'только для просмотра' in changed['reason']
    assert grade(teacher,key,sent,stale).status_code==409
    assert signed('unrelated').get(f'/api/journal/homework/{key}/grading?workspace=demo&studentId=s1').status_code==403
    state=api.read('demo');state.datedLessons=[];api.save('demo',state)
    fresh=options(teacher,key);assert fresh['allowed'] and fresh['revision']>stale['revision']
    grade(teacher,key,sent,payload(fresh)).raise_for_status()
    assert get(parent,key)['submitted']['grading']['grade']['score']==9


@pytest.mark.parametrize('change',['journal','teacher','account'])
def test_change_after_context_before_preparation_cannot_publish_stale_grade(client,monkeypatch,change):
    record,sent,_,parent,teacher=ready(client);key=record['lessonKey'];stale=payload(options(teacher,key));original=HomeworkGrading.prepare
    def altered(self,ctx):
        if change=='journal':
            newer=deepcopy(record);newer['document']['content']['topic']='Concurrent teacher draft'
            write(client,newer).raise_for_status()
        elif change=='teacher':
            state=api.read('demo');state.datedLessons=[DatedLesson(id='replace',name='Замена',kind='change',placementId='eng',sourceDate=DAY,onDate=DAY,teacherIds=['t1'])]
            api.save('demo',state)
        else:
            with api.connect() as db:db.execute("UPDATE accounts SET version=version+1 WHERE login='teacher'")
        return original(self,ctx)
    monkeypatch.setattr(HomeworkGrading,'prepare',altered)
    assert grade(teacher,key,sent,stale).status_code==409
    assert report(parent).json()['rows'][0]['record']['grades'][0]['score']==8
    assert get(parent,key)['status']=='submitted'
    if change=='journal':assert entry(client,key)['document']['content']['topic']=='Concurrent teacher draft'
