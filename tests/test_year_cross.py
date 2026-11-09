"""The commission exams and the safety briefings across the school-year rollover."""
from datetime import datetime,timezone
from uuid import uuid4
import pytest
from backend import api,families,journal,safety,school_years
from conftest import lesson_clock  # noqa: F401
from test_access import client,enable  # noqa: F401
from test_journal_periods import period_clock  # noqa: F401
from test_final_grades import stop_gradebook_jobs  # noqa: F401
from test_commission_exams import url,ok,school,plan,plan_body,result,decide,letter
from test_school_years import begin,draft,started
from test_safety import new,register

SUMMER=datetime(2027,6,20,10,tzinfo=timezone.utc)
OCT=datetime(2026,10,14,10,tzinfo=timezone.utc)


@pytest.fixture
def clocks(monkeypatch):
    now=dict(at=OCT)
    monkeypatch.setattr('backend.commission_exams.now',lambda:now['at'])
    monkeypatch.setattr('backend.behaviour.now',lambda:now['at'])
    monkeypatch.setattr(school_years,'now',lambda:SUMMER)
    monkeypatch.setattr(families,'now',lambda:SUMMER)
    monkeypatch.setattr(safety,'now',lambda:now['at'])
    monkeypatch.setattr(journal,'minute',lambda:'23:59')
    return now


def listing(client):
    return ok(client.get(url()))['items']


def archived(response):
    assert response.status_code==409 and response.json()['detail']['reason']=='archivedYear',response.text


def test_commission_writes_into_an_archived_year_are_refused(client,clocks):
    p,b,s,people=school(client)
    held=ok(result(people['member'],ok(plan(client,p)),s,'4'))
    planned=ok(plan(client,p,studentId='s2',chairmanId='t3',examinerId='t0',memberIds=['t2']))
    started(client);clocks['at']=SUMMER
    before=listing(client)
    edit={k:v for k,v in plan_body(p,studentId='s2',chairmanId='t3',examinerId='t0',memberIds=['t2'],day='2026-10-13').items() if k!='requestId'}
    archived(client.put(url(f"/{planned['id']}"),json=edit|dict(version=planned['version'])))
    archived(plan(client,p,studentId='s2',subjectId='sub0',chairmanId='t3',examinerId='t0',memberIds=['t2']))
    archived(result(people['other'],planned,s,'5'))
    archived(decide(client,held,writeFinal=False,reason='Оценка выставляется отдельно'))
    assert listing(client)==before
    assert not any(c['canEdit'] or c['canResult'] or c['canDecide'] for c in before)


def test_a_decision_made_before_the_start_keeps_its_letter_and_class_name(client,clocks):
    p,b,s,people=school(client)
    sent=ok(letter(client,ok(decide(client,ok(result(people['member'],ok(plan(client,p)),s,'4'))))))
    name=ok(people['chair'].get(url(f"/{sent['id']}/protocol")))['className']
    started(client);clocks['at']=SUMMER
    assert ok(people['chair'].get(url(f"/{sent['id']}/protocol")))['className']==name
    assert ok(people['mother'].get(url(f"/{sent['id']}/letter")))['className']==name


def test_a_letter_sent_after_the_start_keeps_the_class_name_and_a_held_exam_cannot_be_cancelled(client,clocks):
    p,b,s,people=school(client)
    decided=ok(decide(client,ok(result(people['member'],ok(plan(client,p)),s,'4'))))
    held=ok(result(people['other'],ok(plan(client,p,studentId='s2',chairmanId='t3',examinerId='t0',memberIds=['t2'])),s,'5'))
    name=ok(people['chair'].get(url(f"/{decided['id']}/protocol")))['className']
    before=ok(client.get(url(f"/{held['id']}/protocol")))['className']
    started(client);clocks['at']=SUMMER
    sent=ok(letter(client,decided))
    assert ok(people['mother'].get(url(f"/{sent['id']}/letter")))['className']==name
    assert ok(client.get(url(f"/{held['id']}/protocol")))['className']==before
    archived(client.post(url(f"/{held['id']}/cancel"),json=dict(version=held['version'],reason='Отмена после перехода')))
    assert not next(c for c in listing(client) if c['id']==held['id'])['canCancel']


def test_the_start_warns_about_undecided_commission_exams(client,clocks):
    p,b,s,people=school(client)
    ok(plan(client,p))
    preview=client.post('/api/school-years/preview?workspace=demo',json=dict(revision=api.read('demo').revision,plan=draft(client)))
    assert len(preview.json()['warnings']['commissionExams'])==1
    blocked=begin(client,draft(client))
    assert blocked.status_code==422 and blocked.json()['detail']['warnings']['commissionExams']
    started(client)


def test_briefings_of_an_archived_year_stay_and_summer_ones_are_recordable(client,clocks):
    enable(client);clocks['at']=datetime(2026,10,15,6,tzinfo=timezone.utc)
    old=register(client,heldOn='2026-10-01')
    started(client);clocks['at']=datetime(2027,8,10,6,tzinfo=timezone.utc)
    archived(client.post(f"/api/safety/briefings/{old['id']}/withdraw?workspace=demo",json=dict(requestId=uuid4().hex,version=old['version'],reason='Ошибочная запись')))
    archived(client.put(f"/api/safety/briefings/{old['id']}?workspace=demo",json=dict(requestId=uuid4().hex,version=old['version'],kind='repeated',heldOn='2026-10-01',topic='Другая тема',conductorTeacherId='t3',absentIds=[])))
    archived(client.post('/api/safety/briefings?workspace=demo',json=new(heldOn='2026-10-05')))
    summer=client.post('/api/safety/briefings?workspace=demo',json=new(heldOn='2027-08-10',audience='staff',classId=None,staffLabel='Учителя',teacherIds=['t0'],absentIds=[]))
    assert summer.status_code==200,summer.text
