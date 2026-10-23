from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
import json, secrets, sqlite3
from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from backend import api, admissions, admission_files, homework_files
from backend.access import now, digest
from test_access import client, enable, create, signed, HEADERS, PASSWORD

def body(**kw):return dict(requestId=str(uuid4()),**kw)
def path(part):return '/api/admissions/'+part+('&' if '?' in part else '?')+'workspace=demo'
def own(part):return '/admissions-api/'+part
def ok(r):
    assert r.status_code==200,r.text
    return r.json()
def get(c,p):return ok(c.get(path(p)))
def data(**kw):
    return dict(title='Приём в демошколу',year='2027/28',description='Демонстрация',contact='Приёмная комиссия, 555-0100',privacyNotice='Демонстрационные условия обработки.',grades=[0,1,8],opensAt=(now()-timedelta(days=1)).isoformat(),closesAt=(now()+timedelta(days=30)).isoformat(),documents=[dict(id='identity',title='Документ ребёнка',required=True)],questions=[dict(id='language',title='Язык обучения',kind='single',choices=['Русский','Казахский'],required=True)])|kw
def campaign(c,**kw):return ok(c.post(path('campaigns'),json=body(version=0,data=data(**kw))))
def action(c,r,name='open'):return ok(c.post(path('campaigns/'+r['id']+'/action'),json=body(version=r['version'],action=name,reason='Демонстрационное решение')))
def latest(c,r):return get(c,'campaigns/'+r['id'])
def assign(c,r,*ids):return ok(c.put(path('campaigns/'+r['id']+'/reviewers'),json=body(version=latest(c,r)['version'],accountIds=list(ids))))
def signup(r,login=None):
    f=TestClient(api.app,headers=HEADERS);login=login or 'family'+secrets.token_hex(3);code=secrets.token_hex(24)
    result=ok(f.post(own('register'),json=dict(campaignId=r['id'],login=login,name='Демо родитель',password=PASSWORD,recoveryCode=code)))
    return f,result['user'],code
def new(f,c):return ok(f.post(own('applications'),json=body(version=0,campaignId=c['id'])))
def upload(f,r,content=b'%PDF-1.4\n synthetic document',**kw):return f.post(own('applications/'+r['id']+'/files'),params=dict(applicationVersion=r['version'],filename='document.pdf')|kw,content=content,headers={'Content-Type':'application/octet-stream'})
def filled(r,f=None):return r['data']|dict(childName='Демо Ребёнок',birthDate='2020-01-02',grade=1,guardianName='Демо Родитель',email='family@example.invalid',phone='555-0101',consent=True,answers={'language':'Русский'},files=[dict(id=f['id'],kind='identity')] if f else [])
def save(f,r,d=None,action='save'):return ok(f.put(own('applications/'+r['id']),json=body(version=r['version'],data=d or r['data'],action=action)))
def submit(f,c):
    r=new(f,c);file=ok(upload(f,r));return save(f,r,filled(r,file),'submit'),file
def detail(f,r):return ok(f.get(own('applications/'+r['id'])))
def review(c,r,action='returned',message='Дополните документы',**kw):return c.post(path('applications/'+r['id']+'/review'),json=body(version=r['version'],action=action,message=message,**kw))

@pytest.fixture
def setup(client):
    enable(client);teacher=create(client,'teacher');staff=create(client,'staff');create(client,'parent')
    c=action(client,campaign(client));return c,teacher,staff

def test_separate_identity_cookies_public_routes_and_no_school_access(client,setup):
    c,*_=setup;before=api.read('demo');f,u,code=signup(c)
    assert f.get('/api/state?workspace=demo').status_code==401
    assert f.get(path('campaigns')).status_code==401
    assert signed('parent').get(path('options')).status_code==403
    assert signed('parent').get(own('applications')).status_code==401
    assert client.get(own('applications')).status_code==401
    cookie=f.cookies.get(admissions.COOKIE);assert cookie
    with api.connect() as db:
        assert not db.execute('SELECT 1 FROM accounts WHERE id=?',(u['id'],)).fetchone()
        row=db.execute('SELECT password,recovery FROM admission_families WHERE id=?',(u['id'],)).fetchone()
        assert PASSWORD not in row[0] and code not in row[1] and row[1]==digest(code)
        assert db.execute('SELECT token FROM admission_sessions WHERE user_id=?',(u['id'],)).fetchone()[0]!=cookie
    assert api.read('demo')==before
    assert f.get(own('campaigns')).headers['cache-control']=='no-store'
    assert f.post(own('logout'),headers={'X-Atlas-Request':'0'}).status_code==403
    assert f.post(own('logout'),headers={'Origin':'https://evil.example'}).status_code==403
    ok(f.post(own('logout')));assert f.get(own('applications')).status_code==401

def test_unconfigured_catalog_and_staff_requires_account(client):
    f=TestClient(api.app,headers=HEADERS)
    assert ok(f.get(own('campaigns')))==dict(items=[])
    assert ok(f.get(own('session')))==dict(user=None)
    assert client.get(path('options')).status_code==403

def test_auth_validation_rate_limit_and_recovery_revokes_all_sessions(client,setup):
    c,*_=setup;f,u,code=signup(c,'test-family');other=TestClient(api.app,headers=HEADERS)
    r=other.post(own('login'),json=dict(login='test-family',password=PASSWORD));ok(r)
    assert 'HttpOnly' in r.headers['set-cookie'] and 'SameSite=strict' in r.headers['set-cookie'] and 'Path=/admissions-api' in r.headers['set-cookie']
    invalid=other.post(own('recover'),json=dict(login='test-family',recoveryCode='sensitive-short',password='sensitive-pass',newRecoveryCode='also-secret'))
    assert invalid.status_code==422 and all(secret not in invalid.text for secret in ['sensitive-short','sensitive-pass','also-secret'])
    newcode=secrets.token_hex(24)
    ok(f.post(own('recover'),json=dict(login='test-family',recoveryCode=code,password='replacement test password',newRecoveryCode=newcode)))
    assert other.get(own('applications')).status_code==401
    assert f.post(own('recover'),json=dict(login='test-family',recoveryCode=code,password=PASSWORD,newRecoveryCode=secrets.token_hex(24))).status_code==401
    for _ in range(10):assert other.post(own('login'),json=dict(login='absent',password=PASSWORD)).status_code==401
    assert other.post(own('login'),json=dict(login='absent',password=PASSWORD)).status_code==429
    assert other.post(own('login'),content=b' '*65537).status_code==413

def test_campaign_publish_freeze_assignment_and_revocation(client,setup):
    c,t,staff=setup;teacher=signed('teacher');assert not get(teacher,'campaigns')['items']
    draft=campaign(client,title='Ещё не открытая кампания')
    assert client.post(path('campaigns/'+draft['id']+'/action'),json=body(version=draft['version'],action='close',reason='Too early')).status_code==409
    assert teacher.get(path('campaigns/'+c['id'])).status_code==403
    assign(client,c,t['id']);assert get(teacher,'campaigns')['items'][0]['id']==c['id']
    assert teacher.post(path('campaigns'),json=body(version=0,data=data())).status_code==403
    c=latest(client,c);assert client.put(path('campaigns/'+c['id']),json=body(version=c['version'],data=data(title='Changed'))).status_code==409
    with api.connect() as db:db.execute('UPDATE accounts SET version=version+1,role=? WHERE id=?',('staff',t['id']))
    assert teacher.get(path('campaigns/'+c['id'])).status_code==403
    assert client.get('/api/admissions/campaigns/'+c['id']+'?workspace=school').status_code==404
    c=action(client,c,'close');f=TestClient(api.app,headers=HEADERS)
    assert f.post(own('register'),json=dict(campaignId=c['id'],login='closed',name='N',password=PASSWORD,recoveryCode=secrets.token_hex(24))).status_code==409

def test_drafts_and_returned_edits_never_leak_to_commission(client,setup):
    c,*_=setup;f,_,_=signup(c);r=new(f,c);file=ok(upload(f,r));r=save(f,r,filled(r,file))
    assert get(client,'campaigns/'+c['id']+'/applications')['items']==[]
    assert client.get(path('applications/'+r['id'])).status_code==404
    assert client.get(path('files/'+file['id'])).status_code==404
    r=save(f,r,action='submit');assert get(client,'applications/'+r['id'])['data']['childName']=='Демо Ребёнок'
    ok(review(client,r,replyUntil=(now()+timedelta(days=3)).isoformat()))
    r=detail(f,r);r=save(f,r,r['data']|dict(childName='Несохранённая для школы версия'))
    assert get(client,'applications/'+r['id'])['data']['childName']=='Демо Ребёнок'
    assert 'Несохранённая' not in client.get(path('campaigns/'+c['id']+'/export')).text
    r=save(f,r,action='submit');assert get(client,'applications/'+r['id'])['data']['childName']=='Несохранённая для школы версия'
    history=ok(f.get(own('applications/'+r['id']+'/history')))['items']
    assert [h['data']['childName'] for h in history if h['data']]==['Несохранённая для школы версия','Демо Ребёнок']

def test_submit_validation_required_documents_consent_and_questions(client,setup):
    c,*_=setup;f,_,_=signup(c);r=new(f,c)
    assert f.put(own('applications/'+r['id']),json=body(version=r['version'],data=r['data'],action='submit')).status_code==422
    assert f.put(own('applications/'+r['id']),json=body(version=r['version'],data=filled(r),action='submit')).status_code==422
    file=ok(upload(f,r));d=filled(r,file)
    for change in [dict(consent=False),dict(grade=11),dict(birthDate='2999-01-01'),dict(answers={'language':'Unknown'}),dict(files=[dict(id=file['id'],kind='bad')])]:
        assert f.put(own('applications/'+r['id']),json=body(version=r['version'],data=d|change,action='submit')).status_code==422
    save(f,r,d,'submit')

def test_other_family_cannot_read_write_history_or_attach_files(client,setup):
    c,*_=setup;f,_,_=signup(c);other,_,_=signup(c);r,file=submit(f,c);r2=new(other,c)
    for suffix in ['', '/history', '/files']:assert other.get(own('applications/'+r['id']+suffix)).status_code==404
    assert other.get(own('files/'+file['id'])).status_code==404
    assert other.put(own('applications/'+r2['id']),json=body(version=r2['version'],data=filled(r2,file),action='submit')).status_code==422
    assert other.post(own('applications/'+r['id']+'/withdraw'),json=body(version=r['version'],reason='Foreign')).status_code==404

def test_reviewer_notes_and_final_decisions_are_scoped(client,setup):
    c,t,_=setup;assign(client,c,t['id']);teacher=signed('teacher');f,_,_=signup(c);r,file=submit(f,c)
    r=ok(review(teacher,r,action='note',message='Скрытая заметка комиссии'))
    assert 'Скрытая' not in f.get(own('applications/'+r['id']+'/history')).text
    assert 'Скрытая' in teacher.get(path('applications/'+r['id']+'/history')).text
    assert review(teacher,r,action='accepted').status_code==403
    r=ok(review(client,r,action='accepted',message='Добро пожаловать'))
    assert detail(f,r)['status']=='accepted'
    assert review(client,r,action='rejected').status_code==409
    r=ok(review(client,r,action='reopen',message='Исправляем решение'))
    r=ok(review(client,r,action='rejected',message='Нет свободных мест'))
    assert detail(f,r)['status']=='rejected'
    assign(client,c);assert teacher.get(path('files/'+file['id'])).status_code==403

def test_closing_new_intake_preserves_explicit_revision_deadline(client,setup):
    c,*_=setup;f,_,_=signup(c);r,_=submit(f,c);pending=new(f,c)
    r=ok(review(client,r,replyUntil=(now()+timedelta(days=1)).isoformat()));c=action(client,latest(client,c),'close')
    assert f.post(own('applications'),json=body(version=0,campaignId=c['id'])).status_code==409
    assert f.put(own('applications/'+pending['id']),json=body(version=pending['version'],data=filled(pending),action='submit')).status_code==409
    r=save(f,r,action='submit');r=ok(review(client,r,replyUntil=(now()+timedelta(hours=1)).isoformat()))
    with api.connect() as db:db.execute('UPDATE admission_applications SET reply_until=? WHERE id=?',((now()-timedelta(seconds=1)).isoformat(),r['id']))
    assert f.put(own('applications/'+r['id']),json=body(version=r['version'],data=r['data'],action='submit')).status_code==409
    assert client.post(path('campaigns/'+c['id']+'/action'),json=body(version=c['version'],action='archive',reason='Archive')).status_code==409

def test_replay_concurrent_submission_duplicate_child_and_withdrawal(client,setup):
    c,*_=setup;f,_,_=signup(c);r=new(f,c);file=ok(upload(f,r));req=body(version=r['version'],data=filled(r,file),action='submit')
    with ThreadPoolExecutor(2) as pool:responses=list(pool.map(lambda _:f.put(own('applications/'+r['id']),json=req),range(2)))
    assert all(x.status_code==200 for x in responses) and responses[0].json()==responses[1].json()
    r=responses[0].json();req['data']['childName']='Changed';assert f.put(own('applications/'+r['id']),json=req).status_code==409
    r2=new(f,c);file2=ok(upload(f,r2));assert f.put(own('applications/'+r2['id']),json=body(version=r2['version'],data=filled(r2,file2),action='submit')).status_code==409
    r=ok(f.post(own('applications/'+r['id']+'/withdraw'),json=body(version=r['version'],reason='Ошибка в заявке')))
    assert r['status']=='withdrawn';save(f,r2,filled(r2,file2),'submit')
    with api.connect() as db:assert db.execute("SELECT count(*) FROM admission_history WHERE application_id=? AND action='submitted'",(r['id'],)).fetchone()[0]==1

def test_racing_decisions_and_history_failure_roll_back(client,setup,monkeypatch):
    c,*_=setup;f,_,_=signup(c);r,_=submit(f,c)
    with ThreadPoolExecutor(2) as pool:responses=list(pool.map(lambda a:review(client,r,action=a),['accepted','rejected']))
    assert sorted(x.status_code for x in responses)==[200,409]
    r=detail(f,r);r=ok(review(client,r,action='reopen'))
    def fail(*args,**kw):raise RuntimeError('synthetic history failure')
    monkeypatch.setattr(api.admissions,'history',fail)
    with pytest.raises(RuntimeError):review(client,r,action='accepted')
    assert get(client,'applications/'+r['id'])['status']=='in_review'

def test_file_validation_quota_draft_protection_and_retained_submission(client,setup,monkeypatch):
    c,*_=setup;f,_,_=signup(c);r=new(f,c)
    assert upload(f,r,b'<html>not pdf</html>').status_code==422
    assert upload(f,r,filename='bad.html').status_code==422
    file=ok(upload(f,r));r=save(f,r,filled(r,file))
    assert f.delete(own('files/'+file['id'])).status_code==409
    with api.connect() as db:db.execute('UPDATE admission_files SET created=?',((now()-timedelta(days=5)).isoformat(),))
    other=ok(upload(f,r));assert f.get(own('files/'+file['id'])).status_code==200
    ok(f.delete(own('files/'+other['id'])));r=save(f,r,action='submit')
    response=client.get(path('files/'+file['id']));assert response.status_code==200 and 'attachment;' in response.headers['content-disposition'] and response.headers['x-content-type-options']=='nosniff'
    assert f.delete(own('files/'+file['id'])).status_code==409
    r2=new(f,c);monkeypatch.setattr(homework_files,'total_limit',lambda:10)
    assert upload(f,r2).status_code==413

def test_unused_rows_limit_counts_only_deletable_files(client,setup):
    c,*_=setup;f,_,_=signup(c);r=new(f,c)
    kept=[ok(upload(f,r)) for _ in range(3)];r=save(f,r,filled(r)|dict(files=[dict(id=x['id'],kind='identity') for x in kept]))
    loose=ok(upload(f,r))
    with api.connect() as db:
        db.execute('UPDATE admission_files SET created=?',((now()-timedelta(days=5)).isoformat(),))
        db.execute('UPDATE admission_files SET created=? WHERE id=?',((now()-timedelta(days=2)).isoformat(),loose['id']))
        # The older draft files come first by age but are in use: the limit must not be spent on them.
        assert admission_files.unused_rows(db,1)==[(loose['id'],loose['size'])]
        assert [x[0] for x in admission_files.unused_rows(db,-1)]==[loose['id']]
        admission_files.collect_unused(db)
        assert {x[0] for x in db.execute('SELECT id FROM admission_files')}=={x['id'] for x in kept}
        assert not (admission_files.root_for(db)/(loose['id']+'.bin')).exists()

def test_upload_rechecks_session_and_application_version(client,setup,monkeypatch):
    c,*_=setup;f,u,_=signup(c);r=new(f,c);validate=homework_files.validate_file
    def changed(path,name):
        with api.connect() as db:db.execute('UPDATE admission_applications SET version=version+1 WHERE id=?',(r['id'],))
        return validate(path,name)
    monkeypatch.setattr(homework_files,'validate_file',changed);assert upload(f,r).status_code==409
    assert ok(f.get(own('applications/'+r['id']+'/files')))['items']==[]
    r=detail(f,r)
    def revoked(path,name):
        with api.connect() as db:db.execute('DELETE FROM admission_sessions WHERE user_id=?',(u['id'],))
        return validate(path,name)
    monkeypatch.setattr(homework_files,'validate_file',revoked);assert upload(f,r).status_code==401
    with api.connect() as db:assert db.execute('SELECT count(*) FROM admission_files').fetchone()[0]==0

def test_search_csv_formula_protection_and_paging(client,setup):
    c,*_=setup;f,_,_=signup(c);r,file=submit(f,c);r=ok(review(client,r,replyUntil=(now()+timedelta(days=1)).isoformat()));r=save(f,r,r['data']|dict(childName='=Демо'),action='submit')
    assert get(client,'campaigns/'+c['id']+'/applications?q=ДЕМО')['items'][0]['id']==r['id']
    assert not get(client,'campaigns/'+c['id']+'/applications?offset=30')['items']
    assert "'=Демо" in client.get(path('campaigns/'+c['id']+'/export')).text
    assert 'Документ ребёнка' not in client.get(path('campaigns/'+c['id']+'/export')).text

def test_backup_restore_family_access_history_and_documents(client,setup,tmp_path,monkeypatch):
    from tools.backup_school import create_backup
    from tools.restore_school import verify_backup,restore_backup
    c,*_=setup;f,_,_=signup(c);r,file=submit(f,c);ok(review(client,r,action='accepted'))
    before=api.read('demo');backup=tmp_path/'backup';create_backup(api.DB,backup);assert verify_backup(backup)['files']==1
    destination=tmp_path/'restored';restore_backup(backup,destination);monkeypatch.setattr(api,'DB',destination/api.DB.name)
    assert detail(f,r)['status']=='accepted' and api.read('demo')==before
    assert f.get(own('files/'+file['id'])).content==b'%PDF-1.4\n synthetic document'
    assert get(client,'applications/'+r['id']+'/history')['items'][0]['action']=='accepted'
    verify_backup(backup)

def test_revoked_reviewer_cannot_replay_and_withdrawn_case_stays_withdrawn(client,setup):
    c,t,_=setup;assign(client,c,t['id']);teacher=signed('teacher');f,_,_=signup(c);r,_=submit(f,c)
    req=body(version=r['version'],action='message',message='Получили вашу заявку')
    r=ok(teacher.post(path('applications/'+r['id']+'/review'),json=req));assign(client,c)
    assert teacher.post(path('applications/'+r['id']+'/review'),json=req).status_code==403
    r=ok(f.post(own('applications/'+r['id']+'/withdraw'),json=body(version=r['version'],reason='Передумали')))
    r=ok(review(client,r,action='message',message='Отзыв получен'));assert r['status']=='withdrawn'
    assert review(client,r,action='accepted').status_code==409
    c=action(client,latest(client,c),'archive')
    assert f.get(own('campaigns/'+c['id'])).status_code==404
    assert detail(f,r)['campaign']['status']=='archived'
    assert review(client,r,action='note').status_code==409

def test_current_school_identity_and_hash_concurrency_gate(client,setup,monkeypatch):
    import threading
    from backend import admission_auth
    c,*_=setup;f,_,_=signup(c)
    monkeypatch.setattr(admission_auth,'HASH_SLOTS',threading.BoundedSemaphore(0))
    assert f.post(own('login'),json=dict(login='missing',password=PASSWORD)).status_code==429
    original=api.admissions.snapshots.get
    def changed(workspace):
        result=original(workspace)
        with api.connect() as db:db.execute("UPDATE accounts SET version=version+1 WHERE login='admin'")
        return result
    monkeypatch.setattr(api.admissions.snapshots,'get',changed)
    assert client.get(path('campaigns/'+c['id'])).status_code==409
