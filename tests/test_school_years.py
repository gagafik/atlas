from datetime import datetime,timezone
from uuid import uuid4
import pytest
from backend import api,families,school_years
from backend.models import Student
from backend.seed import initial_state
from test_access import client,enable,create,signed
from test_journal import DAY,entry,lesson,write,graded
from test_journal_periods import period,close,action,period_clock
from test_final_grades import seeded as final_book,stop_gradebook_jobs

BASE='/api/school-years'
SUMMER=datetime(2027,6,20,10,tzinfo=timezone.utc)
SEASON=datetime(2027,6,11,10,tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def after_the_last_day(monkeypatch):
    """The clock stands after the last day of 2026/27 unless a test says otherwise."""
    monkeypatch.setattr(school_years,'now',lambda:SUMMER)
    monkeypatch.setattr(families,'now',lambda:SUMMER)


def url(path,**params):
    return BASE+path+'?'+'&'.join(['workspace=demo']+[f'{k}={v}' for k,v in params.items()])


def draft(client,promote={'c-demo-a':'11A'}):
    """The default plan with the chosen classes promoted and all others graduating."""
    plan=client.get(url('/plan')).json()['plan']
    for step in plan['classes']:
        name=promote.get(step['classId'])
        step.update(action='promote' if name else 'graduate',name=name)
    return plan


def begin(client,plan,**extra):
    body=dict(requestId=uuid4().hex,revision=api.read('demo').revision,plan=plan)|extra
    return client.post(url('/start'),json=body)


def started(client,plan=None,**extra):
    response=begin(client,plan or draft(client),acknowledgeWarnings=True,**extra)
    assert response.status_code==200,response.text
    return response.json()


def years(client):
    return client.get(url('')).json()


def test_current_data_is_the_first_year_without_any_write(client):
    settings=initial_state('demo').settings
    first=years(client)
    assert first['years']==[dict(id=str(settings.yearStart.year),name='2026/27',start=settings.yearStart.isoformat(),end=settings.yearEnd.isoformat(),current=True,archived=False)]
    assert first['current']==first['years'][0]['id']
    state=api.read('demo')
    assert client.put('/api/state?workspace=demo',json=state.model_dump(mode='json')).status_code==200
    assert years(client)['years']==first['years']


@pytest.mark.parametrize('role',['admin','editor','teacher','student','parent'])
def test_every_signed_in_account_reads_the_year_list(client,role):
    enable(client)
    if role!='admin':create(client,role)
    reader=client if role=='admin' else signed(role)
    answer=reader.get(url(''))
    assert answer.status_code==200
    assert [y['current'] for y in answer.json()['years']]==[True]
    assert answer.json()['canManage']==(role=='admin')


@pytest.fixture
def rolled(client):
    """A published, graded and closed lesson of 2026/27, then the new year."""
    enable(client)
    record=write(client,graded(entry(client,lesson(client)['lessonKey'])),publish=True).json()
    create(client,'parent')
    closed=close(client,period(client))
    result=started(client)
    return dict(record=record,period=closed,result=result)


def unchanged(client,rolled):
    """What a refused write must leave as it was: the lesson, the schedule revision and the closed period."""
    current=entry(client,rolled['record']['lessonKey'])
    periods=client.get('/api/journal/periods?workspace=demo').json()['items']
    return current['version'],current['publishedVersion'],api.read('demo').revision,[(p['id'],p['status']) for p in periods if p['id']==rolled['period']['id']]


@pytest.mark.parametrize('call',['withdraw','entry','state','undo','reopen','prepare','note'])
def test_past_year_writes_are_refused(client,rolled,call):
    record,closed=rolled['record'],rolled['period']
    before=unchanged(client,rolled)
    state=api.read('demo').model_dump(mode='json')
    state['settings']['yearStart']=DAY
    calls=dict(
        withdraw=(lambda:client.post('/api/journal/withdraw?workspace=demo',json=dict(lessonKey=record['lessonKey'],version=record['version'])),409,'archivedYear'),
        entry=(lambda:write(client,entry(client,record['lessonKey'])),422,None),
        state=(lambda:client.put('/api/state?workspace=demo',json=state),409,'archivedYear'),
        undo=(lambda:client.post('/api/undo?workspace=demo',json=dict(revision=api.read('demo').revision)),409,'archivedYear'),
        reopen=(lambda:action(client,closed,'reopen'),409,'archivedYear'),
        prepare=(lambda:client.post(f"/api/final-grades/periods/{closed['id']}/prepare?workspace=demo",json=dict(version=closed['version'])),409,'archivedYear'),
        note=(lambda:signed('parent').post('/api/journal/absences?workspace=demo',json=dict(requestId=str(uuid4()),studentId='s1',startDate=DAY,endDate=DAY,reason='Личная причина отсутствия',fileIds=[])),422,None))
    send,status,reason=calls[call]
    response=send()
    assert response.status_code==status,response.text
    if reason:
        detail=response.json()['detail']
        assert detail['reason']==reason and detail['year']=='2026'
    assert unchanged(client,rolled)==before


def test_a_lesson_of_the_past_year_is_not_writable_and_names_the_archive(client,rolled):
    past=entry(client,rolled['record']['lessonKey'])
    assert past['writable'] is False and past['canWithdraw'] is False
    assert any(lock.get('archived') for lock in past['periodLocks'])


def test_preview_equals_the_started_year_and_the_past_stays_readable(client):
    enable(client)
    parent=create(client,'parent');parent=signed('parent')
    write(client,graded(entry(client,lesson(client)['lessonKey'])),publish=True).raise_for_status()
    span=dict(workspace='demo',studentId='s1',start=DAY,end='2027-06-11')
    before=parent.get('/api/journal/pupil-period',params=span).json()
    plan=draft(client)
    plan['periods'].append(dict(name='Первая четверть',kind='quarter',start=plan['start'],end=plan['start']))
    revision=api.read('demo').revision
    preview=client.post(url('/preview'),json=dict(revision=revision,plan=plan))
    assert preview.status_code==200 and preview.json()['canApply'] and preview.json()['canStart']
    done=begin(client,plan)
    assert done.status_code==200,done.text
    assert done.json()['summary']==preview.json()['summary']
    state=api.read('demo')
    names={c.id:c.name for c in state.classes}
    graduates=done.json()['summary']['graduatesClass']['id']
    assert names['c-demo-a']=='11A' and 'c-demo-d' not in names and names[graduates]
    assert {s.id:(s.classId,s.active) for s in state.students}=={'s1':('c-demo-a',True),'s2':('c-demo-a',True),'s3':(graduates,False),'s4':(graduates,False)}
    assert state.settings.yearStart.isoformat()==plan['start']
    kept=done.json()['summary']['kept']
    assert (len(state.workloads),len(state.placements),len(state.groups))==(kept['workloads'],kept['placements'],kept['groups'])
    listed=years(client)
    assert [y['id'] for y in listed['years']]==[done.json()['year']['id'],'2026'] and listed['years'][1]['archived'] and listed['years'][0]['current']
    assert parent.get('/api/journal/pupil-period',params=span).json()==before


def test_start_is_idempotent_by_request(client):
    enable(client)
    plan=draft(client);body=dict(requestId=uuid4().hex,revision=api.read('demo').revision,plan=plan,acknowledgeWarnings=True)
    first=client.post(url('/start'),json=body);assert first.status_code==200,first.text
    listing=years(client);revision=api.read('demo').revision
    assert client.post(url('/start'),json=body).json()==first.json() and years(client)==listing
    changed=dict(body,plan=dict(plan,timetable='empty'))
    assert client.post(url('/start'),json=changed).status_code==409
    assert client.post(url('/start'),json=dict(body,requestId=uuid4().hex)).status_code==409
    assert years(client)==listing and api.read('demo').revision==revision


def test_start_waits_for_the_end_of_the_year_and_acknowledged_warnings(client,monkeypatch):
    enable(client)
    monkeypatch.setattr(school_years,'now',lambda:SEASON)
    early=begin(client,draft(client))
    assert early.status_code==409 and early.json()['detail']['reason']=='yearNotFinished'
    monkeypatch.setattr(school_years,'now',lambda:SUMMER)
    period(client,name='Незакрытая четверть')
    blocked=begin(client,draft(client))
    assert blocked.status_code==422 and blocked.json()['detail']['reason']=='warnings'
    assert 'Незакрытая четверть' in blocked.json()['detail']['warnings']['openPeriods']
    assert begin(client,draft(client),acknowledgeWarnings=True).status_code==200


@pytest.mark.parametrize('problem',['duplicateName','classesChanged','dates'])
def test_plan_problems_block_the_start(client,problem):
    enable(client)
    plan=draft(client)
    if problem=='duplicateName':
        plan['classes']=[dict(classId='c-demo-a',action='promote',name='10D'),dict(classId='c-demo-d',action='keep',name=None)]
    if problem=='classesChanged':plan['classes']=[s for s in plan['classes'] if s['classId']!='c-demo-d']
    if problem=='dates':plan.update(start='2027-06-01',end='2028-06-01',periods=[])
    revision=api.read('demo').revision
    preview=client.post(url('/preview'),json=dict(revision=revision,plan=plan)).json()
    assert problem in [p['code'] for p in preview['problems']] and preview['canApply'] is False
    refused=begin(client,plan,acknowledgeWarnings=True)
    assert refused.status_code==422 and refused.json()['detail']['reason']=='plan'
    assert api.read('demo').revision==revision


def test_graduates_keep_their_own_past_year(client):
    enable(client)
    create(client,'parent',login='graduate-parent',grants=[dict(workspace='demo',view='student',entityIds=['s3'])])
    new=started(client)['year']
    assert [y['id'] for y in signed('graduate-parent').get(url('')).json()['years']]==[new['id'],'2026']


def test_a_published_lesson_keeps_its_roster_after_the_start(client):
    enable(client)
    record=write(client,graded(entry(client,lesson(client)['lessonKey'])),publish=True).json()
    def held():
        rows=client.get('/api/journal/day?workspace=demo&day='+DAY).json()['lessons']
        return next((r['studentCount'],r['hasPublished']) for r in rows if r['lessonKey']==record['lessonKey'])
    before=held()
    assert before[0]
    started(client)
    assert held()==before


def test_plan_draft_versions(client):
    enable(client)
    plan=draft(client)
    first=client.put(url('/plan'),json=dict(version=0,plan=plan))
    assert first.status_code==200 and first.json()['version']==1
    assert client.put(url('/plan'),json=dict(version=0,plan=plan)).status_code==409
    saved=client.get(url('/plan')).json()
    assert saved['saved'] is True and saved['version']==1 and saved['plan']['timetable']==plan['timetable']
    assert saved['canStart'] is True


@pytest.fixture
def people(client):
    enable(client)
    for role in ['parent','student','editor']:create(client,role)
    create(client,'teacher',login='unrelated',grants=[dict(workspace='demo',view='teacher',entityIds=['t1'])])
    create(client,'teacher',login='second',grants=[dict(workspace='demo',view='teacher',entityIds=['t2'])])
    return {login:signed(login) for login in ['parent','student','editor','unrelated','second']}


def test_years_and_archive_reads_by_role_and_dated_links(client,people):
    parent,student=people['parent'],people['student']
    write(client,graded(entry(client,lesson(client)['lessonKey'])),publish=True).raise_for_status()
    plan=draft(client);plan['periods']=[dict(name='Первая четверть',kind='quarter',start=plan['start'],end=plan['start'])]
    period(client)
    new=started(client,plan)['year']
    old=parent.get('/api/journal/options?workspace=demo&year=2026').json()
    assert old['yearStart']=='2026-08-17' and old['archived'] is True and [p['start'] for p in old['periods']]==[DAY]
    current=parent.get('/api/journal/options?workspace=demo').json()
    assert current['yearStart']==new['start'] and current['archived'] is False and [p['name'] for p in current['periods']]==['Первая четверть']
    assert parent.get('/api/journal/options?workspace=demo&year=1999').status_code==404
    assert [y['id'] for y in parent.get(url('')).json()['years']]==[new['id'],'2026']
    span=dict(workspace='demo',studentId='s1',start=DAY,end='2027-06-11')
    assert len(student.get('/api/journal/pupil-period',params=span).json()['marks'])==1
    # The teacher of the lesson reads its marks, an unrelated teacher does not.
    reads={login:people[login].get('/api/journal/pupil-period',params=span) for login in ['second','unrelated']}
    assert [len(r.json()['marks']) if r.status_code==200 else 0 for r in reads.values()].count(1)==1
    # Ending the family link ends the archive for that child.
    ended=client.get(f"/api/access/families/{next(u['id'] for u in client.get('/api/access/users').json() if u['login']=='parent')}/s1?workspace=demo").json()
    body={k:ended[k] for k in ['version','accountVersion','relationship','verified','evidence','reason','startDate','endDate','status']}
    saved=client.put(f"/api/access/families/{next(u['id'] for u in client.get('/api/access/users').json() if u['login']=='parent')}/s1?workspace=demo",json=body|dict(endDate='2027-06-19',reason='Опека прекращена'))
    assert saved.status_code==200,saved.text
    assert parent.get('/api/journal/pupil-period',params=span).status_code==403
    assert [y['id'] for y in parent.get(url('')).json()['years']]==[new['id']]


def test_a_pupil_who_joined_after_the_start_has_no_archive(client,people):
    started(client)
    state=api.read('demo');state.students+=[Student(id='n1',name='Новый ученик',classId='c-demo-a')];api.save('demo',state)
    create(client,'parent',login='newcomer',grants=[dict(workspace='demo',view='student',entityIds=['n1'])])
    assert [y['current'] for y in signed('newcomer').get(url('')).json()['years']]==[True]


@pytest.mark.parametrize('call',['start','preview','plan'])
@pytest.mark.parametrize('login',['editor','second','parent','student'])
def test_only_the_administrator_manages_the_new_year(client,people,login,call):
    reader=people[login]
    plan=draft(client)
    revision=api.read('demo').revision
    sends=dict(start=lambda:reader.post(url('/start'),json=dict(requestId=uuid4().hex,revision=revision,plan=plan)),
               preview=lambda:reader.post(url('/preview'),json=dict(revision=revision,plan=plan)),
               plan=lambda:reader.put(url('/plan'),json=dict(version=0,plan=plan)))
    assert sends[call]().status_code==403
    assert api.read('demo').revision==revision


def test_final_grade_options_by_year(client):
    closed,*_=final_book(client)
    started(client)
    def listed(**query):
        return [x['id'] for x in client.get('/api/final-grades/options',params=dict(workspace='demo')|query).json()['periods']]
    assert closed['id'] in listed(year='2026')
    assert closed['id'] not in listed(year=years(client)['current'])
    assert closed['id'] in listed()
