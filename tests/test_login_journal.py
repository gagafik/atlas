import csv,io
from datetime import datetime,timedelta,timezone
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from backend import api,login_journal
from seeding import insert_row
from test_access import client,enable,create,signed,HEADERS,PASSWORD

EDGE='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36 Edg/120.0'
SECRET='Тайный-пароль-в-поле-логина'
WRONG='wrong password value'
CLOCK=dict(at=datetime(2026,10,15,6,0,tzinfo=timezone.utc))


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    CLOCK['at']=datetime(2026,10,15,6,0,tzinfo=timezone.utc)
    monkeypatch.setattr(login_journal,'now',lambda:CLOCK['at'])


def remote(host='203.0.113.77',agent=EDGE):
    guest=TestClient(api.app,client=(host,4321),headers=HEADERS)
    guest.headers['User-Agent']=agent
    return guest


def attempt(who,login,password=PASSWORD):
    return who.post('/api/auth/login',json=dict(login=login,password=password)).status_code


def listed(admin,**params):
    response=admin.get('/api/access/logins',params=params)
    assert response.status_code==200,response.text
    return response.json()


def exported(admin,**params):
    response=admin.get('/api/access/logins/export',params=params)
    assert response.status_code==200,response.text
    return list(csv.reader(io.StringIO(response.text.lstrip('﻿')),delimiter=';'))[1:]


def test_sign_ins_are_recorded_without_secrets(client):
    enable(client);create(client,'teacher')
    guest=remote()
    assert attempt(guest,'teacher')==200
    assert attempt(guest,'teacher',WRONG)==401
    assert attempt(guest,SECRET)==401
    items=listed(client)['items']
    assert [(i['outcome'],i['accountLogin'],i['address'],i['browser']) for i in items]==[
        ('failed',None,'203.0.113.0/24','edge'),('failed','teacher','203.0.113.0/24','edge'),('success','teacher','203.0.113.0/24','edge')]
    texts=[client.get(path).text for path in ['/api/access/logins','/api/access/logins/export','/api/access/audit']]
    stored=b''.join(Path(str(api.DB)+suffix).read_bytes() for suffix in ['','-wal'] if Path(str(api.DB)+suffix).exists())
    for secret in [SECRET,'203.0.113.77',PASSWORD,WRONG]:
        assert not any(secret in text for text in texts)
        assert secret.encode() not in stored


@pytest.mark.parametrize('host,agent,browser,network',[
    ('198.51.100.9',EDGE,'edge','198.51.100.0/24'),
    ('198.51.100.9','Mozilla/5.0 Chrome/120.0 Safari/537.36 YaBrowser/24.1','yandex','198.51.100.0/24'),
    ('198.51.100.9','Mozilla/5.0 Gecko/20100101 Firefox/121.0','firefox','198.51.100.0/24'),
    ('198.51.100.9','Mozilla/5.0 (iPhone) AppleWebKit/605 Version/17.0 Mobile Safari/604.1','safari','198.51.100.0/24'),
    ('198.51.100.9','','other','198.51.100.0/24'),
    ('2001:db8:abcd:12::1',EDGE,'edge','2001:db8:abcd::/48'),
])
def test_browser_family_and_address(client,host,agent,browser,network):
    enable(client);create(client,'teacher')
    assert attempt(remote(host,agent),'teacher')==200
    newest=listed(client)['items'][0]
    assert (newest['browser'],newest['address'])==(browser,network)


@pytest.mark.parametrize('offset,kept',[(-1,True),(1,False)])
def test_retention(client,offset,kept):
    enable(client);create(client,'teacher')
    assert attempt(remote(),'teacher')==200
    days=listed(client)['retentionDays']
    CLOCK['at']+=timedelta(days=days+offset)
    if kept:
        assert [i['accountLogin'] for i in listed(client)['items']]==['teacher']
    else:
        assert listed(client)['items']==[]
        assert attempt(remote(),'teacher')==200
        assert [i['accountLogin'] for i in listed(client)['items']]==['teacher']


@pytest.mark.parametrize('filters,expected',[
    (dict(outcome='failed'),['','teacher']),
    (dict(account='<teacher>'),['teacher','teacher']),
    (dict(account='unknown'),['']),
    (dict(start='2026-10-15',end='2026-10-15'),['parent','teacher']),
])
def test_filters_and_csv(client,filters,expected):
    enable(client);teacher=create(client,'teacher');create(client,'parent')
    filters={k:teacher['id'] if v=='<teacher>' else v for k,v in filters.items()}
    CLOCK['at']=datetime(2026,10,14,6,0,tzinfo=timezone.utc)
    assert attempt(remote(),'teacher')==200
    assert attempt(remote(),'ghost')==401
    CLOCK['at']=datetime(2026,10,15,6,0,tzinfo=timezone.utc)
    assert attempt(remote(),'parent')==200
    assert attempt(remote(),'teacher',WRONG)==401
    assert sorted(i['accountLogin'] or '' for i in listed(client,**filters)['items'])==expected
    assert sorted(r[3] for r in exported(client,**filters))==expected


def test_export_guards_names_that_look_like_formulas(client):
    enable(client);create(client,'staff',name='=HYPERLINK(1)')
    assert attempt(remote(),'staff')==200
    assert [r[2] for r in exported(client)]==["'=HYPERLINK(1)"]


@pytest.mark.parametrize('role',['editor','teacher','staff','parent','student'])
def test_login_journal_is_for_the_administrator(client,role):
    enable(client);create(client,role)
    other=signed(role)
    for path in ['/api/access/logins','/api/access/logins/export']:
        assert other.get(path).status_code==403
        assert client.get(path).status_code==200


def test_end_before_start_is_refused(client):
    enable(client)
    assert client.get('/api/access/logins',params=dict(start='2026-10-15',end='2026-10-14')).status_code==422


def test_upgrade_blanks_typed_names_in_old_failed_sign_ins(client):
    enable(client)
    with api.connect() as db:
        for detail in ['Тайный-пароль','admin']:
            insert_row(db,'audit',created='2026-10-01T00:00:00+00:00',actor_id='anonymous',actor_name='Неавторизованный вход',action='account.login_failed',detail=detail)
    listed(client)
    details={e['detail'] for e in client.get('/api/access/audit').json() if e['action']=='account.login_failed'}
    assert details=={'','admin'}


def kinds_school(client):
    """One successful sign-in per role, plus a failed one for the teacher and one for a login nobody has."""
    enable(client)
    for role in ['teacher','student','parent','editor','staff']:create(client,role)
    create(client,'viewer',grants=[dict(workspace='demo',view='teacher',entityIds=['t2'])])
    for role in ['teacher','student','parent','editor','staff','viewer']:assert attempt(remote(),role)==200
    assert attempt(remote(),'admin')==200
    assert attempt(remote(),'teacher',WRONG)==401
    assert attempt(remote(),'ghost')==401


@pytest.mark.parametrize('kind,expected',[
    ('',['','admin','editor','parent','staff','student','teacher','teacher','viewer']),
    ('teacher',['teacher','teacher']),
    ('student',['student']),
    ('parent',['parent']),
    ('staff',['admin','editor','staff','viewer']),
    ('unknown',['']),
])
def test_kind_returns_exactly_the_rows_of_that_kind(client,kind,expected):
    kinds_school(client)
    assert sorted(i['accountLogin'] or '' for i in listed(client,kind=kind)['items'])==expected
    assert sorted(r[3] for r in exported(client,kind=kind))==expected


def test_kind_unknown_is_the_same_as_the_unknown_account(client):
    kinds_school(client)
    assert listed(client,kind='unknown')['items']==listed(client,account='unknown')['items']


def test_kind_combines_with_outcome_and_account(client):
    kinds_school(client)
    assert [i['outcome'] for i in listed(client,kind='teacher',outcome='failed')['items']]==['failed']
    assert [r[1] for r in exported(client,kind='teacher',outcome='success')]==['Вход']
    assert listed(client,kind='student',outcome='failed')['items']==[]
    teacher=next(u for u in client.get('/api/access/users').json() if u['login']=='teacher')
    assert len(listed(client,kind='teacher',account=teacher['id'])['items'])==2
    assert listed(client,kind='parent',account=teacher['id'])['items']==[]


def test_kind_pages_with_before(client):
    kinds_school(client)
    seen=[];before=None
    for _ in range(10):
        page=listed(client,kind='staff',limit=1,**(dict(before=before) if before else {}))
        assert len(page['items'])==1
        seen+=[i['accountLogin'] for i in page['items']]
        before=page['nextBefore']
        if not before:break
    assert sorted(seen)==['admin','editor','staff','viewer']


def test_kind_combines_with_the_period(client):
    enable(client);create(client,'teacher')
    CLOCK['at']=datetime(2026,10,14,6,0,tzinfo=timezone.utc)
    assert attempt(remote(),'teacher')==200
    CLOCK['at']=datetime(2026,10,16,6,0,tzinfo=timezone.utc)
    assert attempt(remote(),'teacher',WRONG)==401
    period=dict(kind='teacher',start='2026-10-16',end='2026-10-16')
    assert [i['outcome'] for i in listed(client,**period)['items']]==['failed']
    assert [r[1] for r in exported(client,**period)]==['Неудачная попытка']
    assert listed(client,kind='student',start='2026-10-16',end='2026-10-16')['items']==[]


@pytest.mark.parametrize('path',['/api/access/logins','/api/access/logins/export'])
def test_unknown_kind_value_is_refused(client,path):
    enable(client)
    for bad in ['admin','Teacher','everyone']:
        assert client.get(path,params=dict(kind=bad)).status_code==422


@pytest.mark.parametrize('role',['editor','teacher','staff','parent','student'])
def test_kind_does_not_open_the_journal_to_others(client,role):
    enable(client);create(client,role)
    other=signed(role)
    for path in ['/api/access/logins','/api/access/logins/export']:
        assert other.get(path,params=dict(kind=role)).status_code==403
