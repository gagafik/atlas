from decimal import Decimal
from uuid import uuid4
import json
import pytest
from backend import api
from backend.final_grades import recommendation, Scale
from test_access import client,enable,create,signed
from test_journal import entry,lesson,write,graded
from test_journal_periods import period,close,action,period_clock

BASE='/api/final-grades'

def opts(c):return c.get(BASE+'/options?workspace=demo').json()
def scale(c,**extra):
    r=c.put(BASE+'/scales/'+uuid4().hex+'?workspace=demo',json=dict(version=0,name='Тестовая шкала',bands=[dict(label='Ниже',minPercent=0),dict(label='Выше',minPercent=75)])|extra)
    assert r.status_code==200,r.text
    return r.json()
def prepare(c,p):
    r=c.post(BASE+'/periods/'+p['id']+'/prepare?workspace=demo',json=dict(version=p['version']))
    assert r.status_code==200,r.text
    return r.json()
def sheet(c,b,**params):return c.get(BASE+'/books/'+b['id'],params=dict(workspace='demo')|params)
def batch(c,rows,op,**extra):return c.post(BASE+'/actions?workspace=demo',json=dict(rows=[dict(id=r['id'],version=r['version']) for r in rows],action=op)|extra)
def propose(c,r,s,**extra):
    return c.put(BASE+'/rows/'+r['id']+'?workspace=demo',json=dict(version=r['version'],scaleId=s['id'],scaleVersion=s['version'],label='Выше',submit=True)|extra)
def report(c,p,student='s1'):return c.get(BASE+'/report',params=dict(workspace='demo',periodId=p['id'],studentId=student))
def seeded(c):
    enable(c);teacher=create(c,'teacher');create(c,'parent');create(c,'student')
    record=write(c,graded(entry(c,lesson(c)['lessonKey'])),publish=True).json()
    p=close(c,period(c));b=prepare(c,p);s=scale(c)
    rows=sheet(c,b).json()['items'];r=next(r for r in rows if r['studentId']=='s1' and r['source']['published'])
    r=batch(c,[r],'assign',teacherId=teacher['id']).json()['items'][0]
    return p,b,s,r,record
def publish(c,r):
    r=batch(c,[r],'approve');assert r.status_code==200,r.text
    r=batch(c,r.json()['items'],'publish');assert r.status_code==200,r.text
    return r.json()['items'][0]

def test_workflow_assignment_privacy_and_personal_publication(client):
    p,b,s,r,_=seeded(client);t=signed('teacher');parent=signed('parent')
    assert len(sheet(t,b).json()['items'])==1
    assert report(parent,p).json()['items']==[]
    evidence=t.get(BASE+'/rows/'+r['id']+'/evidence?workspace=demo').json()
    assert evidence['items'][0]['score']==8
    assert 'comment' not in evidence['items'][0] and 'attendance' not in evidence['items'][0]
    assert batch(t,[r],'approve').status_code==403
    r=propose(t,r,s);assert r.status_code==200,r.text
    r=publish(client,r.json())
    personal=report(parent,p).json()['items'];assert len(personal)==1 and personal[0]['label']=='Выше'
    assert 'explanation' not in personal[0] and 'author' not in personal[0] and personal[0]['current']
    assert report(parent,p,'s2').status_code==403
    assert parent.get(BASE+'/rows/'+r['id']+'/evidence?workspace=demo').status_code==403
    assert parent.get(BASE+'/rows/'+r['id']+'/history?workspace=demo').status_code==403
    assert 'journal.finals_published' in [n['action'] for n in parent.get('/api/notifications/inbox').json()['items']]
    assert opts(parent)['scales']==[] and len(opts(parent)['periods'])==1
    other=create(client,'teacher',login='other');u=signed('other')
    assert opts(u)['books']==[] and sheet(u,b).status_code==404
    assert propose(u,r,s).status_code==404
    assert t.get(BASE+'/options?workspace=school').status_code==403

def test_expected_unmarked_and_explicit_no_grade(client):
    p,b,s,r,_=seeded(client)
    empty=next(v for v in sheet(client,b).json()['items'] if not v['source']['published'])
    assert empty['source']['expected']==1 and empty['source']['numeric']==0
    assert propose(client,empty,s).status_code==422
    proposal=propose(client,empty,s,label='',outcome='not_assessed',explanation='Недостаточно работ')
    assert proposal.status_code==200,proposal.text
    assert proposal.json()['proposal']['calculation']['rawPercent'] is None
    assert prepare(client,p)['id']==b['id']

def test_rounding_weight_missing_and_text_do_not_become_zero(client):
    source=dict(weightedSum='149.99',weight='2',numeric=2)
    s=Scale(version=0,name='Шкала',rounding='0',bands=[dict(label='Ниже',minPercent=0),dict(label='Выше',minPercent=75)]).model_dump(mode='json')
    assert recommendation(source,s)['recommended']=='Выше'
    s['rounding']='none';assert recommendation(source,s)['recommended']=='Ниже'
    s['minCount']=3;assert recommendation(source,s)['recommended'] is None
    s['minCount']=1;s['rounding']='none'
    assert recommendation(dict(weightedSum='29999/400',weight='1',numeric=1),s)['recommended']=='Ниже'
    s['rounding']='2'
    assert recommendation(dict(weightedSum='29999/400',weight='1',numeric=1),s)['recommended']=='Выше'
    assert recommendation(dict(weightedSum='3/2',weight='1/50',numeric=1),s)['recommended']=='Выше'
    assert scale(client,rounding='1')['rounding']=='1'
    for bands in [[dict(label='A',minPercent=1),dict(label='B',minPercent=50)],[dict(label='A',minPercent=0),dict(label='A',minPercent=50)],[dict(label='A',minPercent=0),dict(label='B',minPercent=0)]]:
        assert client.put(BASE+'/scales/'+uuid4().hex+'?workspace=demo',json=dict(version=0,name='Bad',bands=bands)).status_code==422
    record=graded(entry(client,lesson(client)['lessonKey']))
    content=record['document']['content'];content['assessments'] += [dict(id='zero',name='Нулевой вес',maxScore=10,weight=0),dict(id='text',name='Текст',kind='label'),dict(id='missing',name='Нет оценки')]
    content['records'][0]['grades'] += [dict(assessmentId='zero',score=0),dict(assessmentId='text',label='Отлично')]
    write(client,record,publish=True).raise_for_status();b=prepare(client,close(client,period(client)))
    r=next(r for r in sheet(client,b).json()['items'] if r['studentId']=='s1' and r['source']['published']);source=r['source']
    assert [source[k] for k in ['numeric','text','missing','zeroWeight']]==[1,1,1,1]
    assert Decimal(source['weightedSum'])/Decimal(source['weight'])==80

def test_correction_preserves_publication_until_reapproved_and_withdrawal(client):
    p,b,s,r,_=seeded(client);r=publish(client,propose(client,r,s).json());parent=signed('parent')
    assert batch(client,[r],'reopen',reason=' ').status_code==422
    r=batch(client,[r],'reopen',reason='Проверка итоговой оценки').json()['items'][0]
    assert report(parent,p).json()['items'][0]['label']=='Выше'
    assert batch(client,[r],'publish').status_code==409
    assert propose(client,r,s,label='Ниже').status_code==422
    r=propose(client,r,s,label='Ниже',explanation='Решение после проверки').json();r=publish(client,r)
    assert report(parent,p).json()['items'][0]['label']=='Ниже'
    history=client.get(BASE+'/rows/'+r['id']+'/history?workspace=demo').json()['items']
    assert sum(h['action']=='publish' for h in history)==2 and history[-1]['action']=='assign'
    r=batch(client,[r],'withdraw',reason='Ошибочная публикация');assert r.status_code==200,r.text
    assert report(parent,p).json()['items']==[]

def test_reclosed_source_requires_new_book_and_prevents_old_withdrawal(client):
    p,b,s,r,record=seeded(client);r=publish(client,propose(client,r,s).json());parent=signed('parent')
    reopened=action(client,p,'reopen').json()
    assert not report(parent,p).json()['items'][0]['current']
    assert batch(client,[r],'reopen',reason='Исправление').status_code==409
    changed=entry(client,record['lessonKey']);changed['document']['content']['records'][0]['grades'][0]['score']=5
    write(client,changed,publish=True).raise_for_status();p=close(client,reopened);b2=prepare(client,p)
    assert b['id']!=b2['id'] and not sheet(client,b).json()['book']['current']
    r2=next(v for v in sheet(client,b2).json()['items'] if v['studentId']==r['studentId'] and v['subjectId']==r['subjectId'])
    r2=publish(client,propose(client,r2,s,label='Ниже').json())
    assert batch(client,[r],'withdraw',reason='Старая ведомость').status_code==409
    assert len(report(parent,p).json()['items'])==1 and report(parent,p).json()['items'][0]['current']

def test_atomic_batch_stale_rows_and_assignment_revocation(client):
    p,b,s,r,_=seeded(client);t=signed('teacher')
    stale=dict(r,version=999)
    assert batch(client,[r,dict(stale,id='missing')],'assign',teacherId='').status_code==404
    assert len(sheet(t,b).json()['items'])==1
    assert propose(t,stale,s).status_code==409
    batch(client,[r],'assign',teacherId='').raise_for_status()
    assert t.get(BASE+'/rows/'+r['id']+'/evidence?workspace=demo').status_code==404
    assert propose(t,r,s).status_code==404
    create(client,'editor');assert opts(signed('editor'))['detail']

def test_scale_snapshot_and_concurrent_change(client):
    p,b,s,r,_=seeded(client);draft=propose(client,r,s).json()
    data={k:v for k,v in s.items() if k!='id'};data['bands'][1]['label']='Новая'
    response=client.put(BASE+'/scales/'+s['id']+'?workspace=demo',json=data);assert response.status_code==200,response.text
    r=publish(client,draft);assert report(signed('parent'),p).json()['items'][0]['scale']['bands'][1]['label']=='Выше'
    r=batch(client,[r],'reopen',reason='Проверка новой шкалы').json()['items'][0]
    assert propose(client,r,s).status_code==409

def test_permissions_rechecked_before_write(client,monkeypatch):
    p,b,s,r,_=seeded(client);t=signed('teacher');original=api.final_grades.context
    def revoke(*args,**kwargs):
        result=original(*args,**kwargs)
        with api.connect() as db:db.execute("UPDATE accounts SET active=0 WHERE login='teacher'")
        return result
    monkeypatch.setattr(api.final_grades,'context',revoke)
    assert propose(t,r,s).status_code==409
    with api.connect() as db:assert db.execute('SELECT status FROM grading_rows WHERE id=?',(r['id'],)).fetchone()[0]=='draft'


def test_report_read_does_not_wait_for_writer_and_family_revocation(client):
    from concurrent.futures import ThreadPoolExecutor
    p,b,s,r,_=seeded(client);r=publish(client,propose(client,r,s).json());parent=signed('parent')
    with ThreadPoolExecutor(max_workers=1) as pool:
        with api.access.lock:
            future=pool.submit(report,parent,p)
            response=future.result(timeout=5)
        assert response.status_code==200 and response.json()['items']
    with api.connect() as db:
        row=db.execute("SELECT id,permissions FROM accounts WHERE login='parent'").fetchone();permissions=json.loads(row[1]);permissions['grants']=[]
        db.execute('UPDATE accounts SET permissions=?,version=version+1 WHERE id=?',(json.dumps(permissions),row[0]))
    assert report(parent,p).status_code==403


def test_evidence_pagination_and_atomic_publication_rollback(client):
    p,b,s,r,_=seeded(client);r=propose(client,r,s).json();r=batch(client,[r],'approve').json()['items'][0]
    with api.connect() as db:
        db.executemany('INSERT INTO grading_marks(workspace,row_id,document) VALUES (?,?,?)',[('demo',r['id'],json.dumps(dict(assessment=f'Work {i}',score=i))) for i in range(60)])
        db.execute("CREATE TRIGGER reject_final_audit BEFORE INSERT ON grading_history WHEN NEW.action='publish' BEGIN SELECT RAISE(ABORT,'synthetic failure'); END")
    first=client.get(BASE+'/rows/'+r['id']+'/evidence?workspace=demo').json();assert len(first['items'])==50 and first['nextAfter']
    rest=client.get(BASE+'/rows/'+r['id']+f'/evidence?workspace=demo&after={first["nextAfter"]}').json()
    assert len(rest['items'])==11 and not rest['nextAfter'] and not ({x['id'] for x in first['items']} & {x['id'] for x in rest['items']})
    import sqlite3
    with pytest.raises(sqlite3.IntegrityError):batch(client,[r],'publish')
    assert report(signed('parent'),p).json()['items']==[]
    with api.connect() as db:assert db.execute('SELECT version,status FROM grading_rows WHERE id=?',(r['id'],)).fetchone()==(r['version'],'approved')
