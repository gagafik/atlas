"""Commission exams: the cycle from the plan to the letter, the final grade it writes and who may see what."""
from datetime import datetime,timezone
from urllib.parse import urlencode
from uuid import uuid4
import pytest
from test_access import client,enable,create,signed  # noqa: F401
from test_journal_periods import period,close,action,period_clock  # noqa: F401
from test_final_grades import BASE as FINALS,prepare,report,stop_gradebook_jobs  # noqa: F401
from test_final_table import english,pair,ENGLISH
from test_final_reports import points_scale,finish,keys
from test_behaviour import teacher,guardian

BASE='/api/behaviour/commission-exams'
HIDDEN={'members','chairman','examiner','reason','tasks','notes','result','scale'}


@pytest.fixture(autouse=True)
def exam_clock(monkeypatch):
    stamp=datetime(2026,10,14,10,tzinfo=timezone.utc)
    monkeypatch.setattr('backend.commission_exams.now',lambda:stamp)
    monkeypatch.setattr('backend.behaviour.now',lambda:stamp)


def url(part='',**query):return BASE+part+'?'+urlencode(dict(workspace='demo')|query)
def ok(response):
    assert response.status_code==200,response.text
    return response.json()


def staffed(c):
    """A commission (chair t0, examiner t2, member t1), a teacher outside it (t3), a mother and a pupil of s1 and the guardian of another child."""
    english(c)
    for login,entity in [('chair','t0'),('member','t1'),('examiner','t2'),('other','t3')]:teacher(c,login,entity)
    guardian(c,'mother','s1');guardian(c,'stranger','s2');create(c,'student')
    return {login:signed(login) for login in ['chair','member','examiner','other','mother','stranger','student']}


def school(c,grade=None,prepared=True):
    """A closed period with a book (unless unprepared), a points scale and, with `grade`, English of s1 already published."""
    people=staffed(c);p=close(c,period(c));b=prepare(c,p) if prepared else None;s=points_scale(c)
    if grade:finish(c,pair(c,b,'s1'),s,label=grade,explanation='Решение педсовета')
    return p,b,s,people


def plan_body(p,**extra):
    return dict(requestId=str(uuid4()),studentId='s1',subjectId='sub2',periodId=p['id'],kind='reexam',reason='Пересдача итоговой оценки',day='2026-10-12',start='09:00',end='10:30',
                roomId='r0',chairmanId='t0',examinerId='t2',memberIds=['t1'])|extra
def plan(c,p,**extra):return c.post(url(),json=plan_body(p,**extra))
def result(c,e,s,label='4',**extra):
    return c.post(url(f"/{e['id']}/result"),json=dict(version=e['version'],outcome='graded',scaleId=s['id'],scaleVersion=s['version'],label=label,passed=True,tasks='Билет 7',notes='Ответил уверенно')|extra)
def decide(c,e,**extra):return c.post(url(f"/{e['id']}/decide"),json=dict(requestId=str(uuid4()),version=e['version'])|extra)
def letter(c,e):return c.post(url(f"/{e['id']}/letter"),json=dict(version=e['version']))
def family(c,student='s1'):return c.get(url('/family',studentId=student))
def final_history(c,row_id):return [h['action'] for h in ok(c.get(f'{FINALS}/rows/{row_id}/history?workspace=demo'))['items']]
def english_item(c,p):return next((i for i in ok(report(c,p))['items'] if i['subject']==ENGLISH),None)
def held(c,people,p,s,label='4'):
    e=ok(plan(c,p));return ok(result(people['member'],e,s,label))


def test_cycle_writes_the_final_grade_and_sends_the_letter(client):
    p,b,s,people=school(client);mother=people['mother']
    e=ok(plan(client,p));assert e['status']=='planned'
    ok(plan(client,p,studentId='s2',chairmanId='t3',examinerId='t0',memberIds=['t2']))
    assert [x['id'] for x in ok(people['member'].get(url()))['items']]==[e['id']]
    done=ok(result(people['member'],e,s,'4'));assert done['status']=='held' and done['result']['label']=='4'
    assert ok(family(mother))['items']==[]
    decided=ok(decide(client,done));assert decided['status']=='decided' and decided['final']['status']=='written'
    assert english_item(mother,p)['label']=='4'
    assert final_history(client,decided['final']['rowId'])[:3]==['publish','approve','submitted']
    sent=ok(letter(client,decided));assert sent['status']=='letter_sent' and sent['letter']['number']
    shown=ok(family(mother))['items']
    assert len(shown)==1 and shown[0]['decision']['label']=='4' and shown[0]['decision']['passed'] and shown[0]['letter']['number']==sent['letter']['number']
    assert not keys(shown)&HIDDEN
    assert ok(family(people['student']))['items']==shown
    text=ok(mother.get(url(f"/{e['id']}/letter")));assert text['label']=='4' and text['number']==sent['letter']['number']
    assert 'behaviour.commission_letter' in {i['action'] for i in ok(mother.get('/api/notifications/inbox'))['items']}
    assert [h['action'] for h in ok(client.get(url(f"/{e['id']}/history")))['items']]==['created','result','decided','letter_sent']
    protocol=ok(people['chair'].get(url(f"/{e['id']}/protocol")))
    assert protocol['result']['label']=='4' and [m['id'] for m in protocol['members']]==['t1']


def test_a_different_published_grade_changes_only_on_request_with_reason(client):
    p,b,s,people=school(client,grade='3');done=held(client,people,p,s)
    refused=decide(client,done);assert refused.status_code==409
    assert refused.json()['detail']['code']=='grade_exists' and refused.json()['detail']['current']['label']=='3'
    assert english_item(people['mother'],p)['label']=='3'
    assert ok(client.get(url()))['items'][0]['status']=='held'
    assert decide(client,done,replace=True).status_code==422
    reason='Решение комиссии выше оценки педсовета'
    written=ok(decide(client,done,replace=True,reason=reason))['final']
    assert written['status']=='written' and written['previous']=='3'
    assert english_item(people['mother'],p)['label']=='4'
    history=ok(client.get(f"{FINALS}/rows/{written['rowId']}/history?workspace=demo"))['items']
    assert [h['action'] for h in history][:4]==['publish','approve','submitted','reopen'] and history[3]['reason']==reason


def test_the_same_grade_leaves_final_grades_untouched(client):
    p,b,s,people=school(client,grade='4');done=held(client,people,p,s)
    row=pair(client,b,'s1')['id'];before=final_history(client,row)
    final=ok(decide(client,done))['final']
    assert final['status']=='unchanged' and final_history(client,row)==before and english_item(people['mother'],p)['label']=='4'


@pytest.mark.parametrize('setup',['reopened','unprepared'])
def test_without_a_current_book_nothing_is_written(client,setup):
    p,b,s,people=school(client,prepared=setup=='reopened')
    done=held(client,people,p,s)
    if setup=='reopened':action(client,p,'reopen').raise_for_status()
    refused=decide(client,done);assert refused.status_code==409 and refused.json()['detail']['code']=='no_book'
    assert ok(client.get(url()))['items'][0]['status']=='held'
    assert decide(client,done,writeFinal=False).status_code==422
    assert ok(decide(client,done,writeFinal=False,reason='Оценка выставляется отдельно'))['final']['status']=='skipped'
    assert english_item(people['mother'],p) is None


@pytest.mark.parametrize('login,call,status',[
    ('member','create',403),('member','decide',403),('member','cancel',403),
    ('admin','result',403),
    ('other','list',403),('other','result',404),('other','protocol',404),
    ('mother','list',403),('mother','result',404),('mother','protocol',404),
    ('stranger','family',404),('student','options',403)])
def test_each_role_reaches_only_its_part(client,login,call,status):
    people=staffed(client)|dict(admin=client);p=period(client);s=points_scale(client);e=ok(plan(client,p))
    calls=dict(
        create=lambda c:plan(c,p,studentId='s2'),
        decide=lambda c:decide(c,e),
        cancel=lambda c:c.post(url(f"/{e['id']}/cancel"),json=dict(version=e['version'],reason='Не нужен')),
        result=lambda c:result(c,e,s),
        list=lambda c:c.get(url()),
        protocol=lambda c:c.get(url(f"/{e['id']}/protocol")),
        family=lambda c:family(c,'s1'),
        options=lambda c:c.get(url('/options')))
    assert calls[call](people[login]).status_code==status


def test_membership_follows_the_current_grant(client):
    people=staffed(client);p=period(client);s=points_scale(client);e=ok(plan(client,p))
    assert ok(people['member'].get('/api/behaviour/options?workspace=demo'))['commission']['member']
    account=next(u for u in ok(client.get('/api/access/users')) if u['login']=='member')
    body={k:v for k,v in account.items() if k!='id'};body['grants']=[dict(workspace='demo',view='teacher',entityIds=['t3'])]
    assert client.put('/api/access/users/'+account['id'],json=body).status_code==200
    moved=signed('member')
    assert result(moved,e,s).status_code==404 and moved.get(url()).status_code==403
    assert ok(moved.get('/api/behaviour/options?workspace=demo'))['commission'] is None


def test_replays_and_status_rules(client):
    people=staffed(client);p=period(client);s=points_scale(client);body=plan_body(p)
    first=ok(client.post(url(),json=body));assert ok(client.post(url(),json=body))['id']==first['id']
    assert client.post(url(),json=body|dict(reason='Другая причина')).status_code==409
    done=ok(result(people['member'],first,s))
    request=dict(requestId=str(uuid4()),version=done['version'],writeFinal=False,reason='Оценка выставляется отдельно')
    path=url(f"/{first['id']}/decide");decided=ok(client.post(path,json=request))
    assert ok(client.post(path,json=request))==decided
    sent=ok(letter(client,decided))
    again=letter(client,sent);assert again.status_code==409 and again.json()['detail']['code']=='status'
    late=result(people['member'],sent,s);assert late.status_code==409 and late.json()['detail']['code']=='status'
    other=ok(plan(client,p,studentId='s2'))
    stale=client.post(url(f"/{other['id']}/cancel"),json=dict(version=other['version']+1,reason='Не нужен'))
    assert stale.status_code==409 and stale.json()['detail']['code']=='version'


@pytest.mark.parametrize('extra,status',[
    (dict(examinerId='t0'),422),(dict(memberIds=['t0']),422),(dict(memberIds=['t1','t1']),422),(dict(roomId='nope'),422),
    (dict(start='11:00',end='10:00'),422),(dict(subjectId='nope'),422),(dict(studentId='nope'),422),(dict(periodId=uuid4().hex),404)])
def test_invalid_schedule_is_refused(client,extra,status):
    staffed(client);p=period(client)
    assert plan(client,p,**extra).status_code==status


def test_result_rules(client):
    people=staffed(client);p=period(client);s=points_scale(client)
    future=ok(plan(client,p,day='2026-10-20'))
    early=result(people['member'],future,s);assert early.status_code==409 and early.json()['detail']['code']=='not_yet'
    again=plan(client,p,day='2026-10-21');assert again.status_code==409 and again.json()['detail']['code']=='duplicate'
    due=ok(plan(client,p,studentId='s2'))
    assert result(people['member'],due,s,'9').status_code==422
    absent=ok(people['member'].post(url(f"/{due['id']}/result"),json=dict(version=due['version'],outcome='absent')))
    assert decide(client,absent).status_code==422
