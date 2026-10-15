"""Reviewed issuance, one-time secrets, retry receipts and batch revocation."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import sqlite3
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from backend import api, invitation_batches
from backend.access import digest
from test_access import client, enable, create, signed
from test_invitations import register, visitor, issue as issue_one

BASE='/api/access/invitations/batches'


def count(db,table):
    exists=db.execute('SELECT 1 FROM sqlite_master WHERE type=\'table\' AND name=?',(table,)).fetchone()
    return db.execute(f'SELECT count(*) FROM {table}').fetchone()[0] if exists else 0


def draft(*rows,**extra):
    return dict(workspace='demo',days=7,rows=list(rows) or [dict(key='parent',recipient='Verified parent',role='parent',entityIds=['s1'])])|extra


def row(key='parent',**extra): return dict(key=key,recipient=key,role='parent',entityIds=['s1'])|extra


def checked(c,body=None):
    body=body or draft()
    r=c.post(BASE+'/preview',json=body);assert r.status_code==200,r.text
    p=r.json()
    return body|dict(previewToken=p['previewToken'],requestId=uuid4().hex,acknowledgedRows=[r['key'] for r in p['items'] if r['warnings']])


def issue(c,body=None):
    payload=checked(c,body);r=c.post(BASE,json=payload);assert r.status_code==200,r.text
    return r.json(),payload


def test_review_then_issue_exact_roles_once_without_changing_school(client):
    enable(client);before=api.read('demo')
    body=draft(row(),row('student',role='student'),row('teacher',role='teacher',entityIds=['t2']))
    preview=client.post(BASE+'/preview',json=body).json()
    assert preview['items'][0]['targets'][0]['id']=='s1' and not preview['items'][0]['warnings']
    with api.connect() as db: assert db.execute('SELECT count(*) FROM access_invitations').fetchone()[0]==0
    issued,payload=issue(client,body)
    assert len(issued['items'])==3 and not issued['replayed']
    codes={item['code'] for item in issued['items']};assert len(codes)==3
    for item in issued['items']:
        response=register(visitor(),item['code'],login='new-'+item['key']);assert response.status_code==200,response.text
        user=response.json()['user'];assert user['role']==item['role']
        assert user['grants'][0]['entityIds']==next(r['entityIds'] for r in body['rows'] if r['key']==item['key'])
    with api.connect() as db:
        assert {r[0] for r in db.execute('SELECT token_hash FROM access_invitations')}=={digest(code) for code in codes}
        dump='\n'.join(db.iterdump());assert not any(code in dump for code in codes)
    detail=client.get(BASE+'/'+str(issued['id'])).json()
    assert {i['status'] for i in detail['items']}=={'accepted'}
    assert not any('code' in i for i in detail['items']) and api.read('demo')==before


def test_retry_returns_receipt_without_secrets_even_after_acceptance(client):
    enable(client);issued,payload=issue(client)
    code=issued['items'][0]['code'];register(visitor(),code).raise_for_status()
    retry=client.post(BASE,json=payload);assert retry.status_code==200
    assert retry.json()['id']==issued['id'] and retry.json()['replayed']
    assert retry.json()['items'][0]['status']=='accepted' and 'code' not in retry.json()['items'][0]
    altered=deepcopy(payload);altered['rows'][0]['recipient']='Changed'
    assert client.post(BASE,json=altered).status_code==409
    create(client,'admin',login='second-admin');other=signed('second-admin')
    assert other.post(BASE,json=payload).status_code==409
    with api.connect() as db: assert db.execute('SELECT count(*) FROM access_invitations').fetchone()[0]==1


def test_existing_access_and_pending_codes_require_per_row_review(client):
    enable(client);create(client,'parent');issue_one(client)
    body=draft(row(),row('second-representative',recipient='Another verified parent'))
    response=client.post(BASE+'/preview',json=body).json()
    assert {w['kind'] for w in response['items'][0]['warnings']}=={'accounts','invitations'}
    payload=checked(client,body)
    for acknowledged in [[],['parent'],['parent','second-representative','forged'],['parent','parent','second-representative']]:
        assert client.post(BASE,json=payload|dict(acknowledgedRows=acknowledged)).status_code==422
    issued=client.post(BASE,json=payload);assert issued.status_code==200,issued.text
    assert len(issued.json()['items'])==2  # several representatives of the same child are valid


@pytest.mark.parametrize('change',['school','pending','account','inactive'])
def test_changed_review_fails_without_partial_issuance(client,change):
    enable(client);payload=checked(client,draft(row(),row('second',entityIds=['s2'])))
    if change=='pending': issue_one(client,['s2'])
    elif change=='account': create(client,'parent')
    else:
        state=api.read('demo')
        if change=='school': state.classes[0].name+=' changed'
        else: next(s for s in state.students if s.id=='s2').active=False
        api.save('demo',state)
    r=client.post(BASE,json=payload);assert r.status_code==(422 if change=='inactive' else 409),r.text
    with api.connect() as db:
        assert count(db,'invitation_batches')==0
        assert db.execute('SELECT count(*) FROM access_invitations').fetchone()[0]==(1 if change=='pending' else 0)


def test_simultaneous_identical_requests_issue_one_batch(client):
    enable(client);payload=checked(client)
    with ThreadPoolExecutor(max_workers=2) as pool: results=list(pool.map(lambda _: client.post(BASE,json=payload),range(2)))
    assert [r.status_code for r in results]==[200,200]
    assert sorted(r.json()['replayed'] for r in results)==[False,True]
    assert len({r.json()['id'] for r in results})==1
    with api.connect() as db: assert db.execute('SELECT count(*) FROM access_invitations').fetchone()[0]==1


def test_simultaneous_distinct_requests_require_new_review(client):
    enable(client);payload=checked(client)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda id: client.post(BASE,json=payload|dict(requestId=id)),[uuid4().hex,uuid4().hex]))
    assert sorted(r.status_code for r in results)==[200,409]


def test_mid_batch_failure_rolls_back_receipt_invites_and_audit(client,monkeypatch):
    enable(client);payload=checked(client,draft(row(),row('other',entityIds=['s2'])))
    monkeypatch.setattr(invitation_batches.secrets,'token_urlsafe',lambda _: 'same-code-for-collision')
    with pytest.raises(sqlite3.IntegrityError): client.post(BASE,json=payload)
    with api.connect() as db:
        for table in ['invitation_batches','invitation_batch_items','access_invitations']:
            assert count(db,table)==0
        assert 'invitations.batch.created' not in str(db.execute('SELECT * FROM audit').fetchall())


def test_revoke_only_unaccepted_codes_and_replay_cannot_reissue_them(client):
    enable(client);issued,payload=issue(client,draft(row(),row('other',entityIds=['s2'])))
    registered=register(visitor(),issued['items'][0]['code']).json()['user']
    result=client.post(BASE+f"/{issued['id']}/revoke").json()
    assert result['revokedCount']==1 and [i['status'] for i in result['items']]==['accepted','revoked']
    assert not any('code' in i for i in result['items'])
    assert client.post(BASE+f"/{issued['id']}/revoke").json()['revokedCount']==0
    assert register(visitor(),issued['items'][1]['code'],login='other-family').status_code==400
    retry=client.post(BASE,json=payload).json();assert retry['replayed'] and retry['items'][1]['status']=='revoked'
    with api.connect() as db: assert db.execute('SELECT active FROM accounts WHERE id=?',(registered['id'],)).fetchone()[0]==1


def test_bounds_roles_csrf_and_inactive_targets(client):
    enable(client);create(client);editor=signed('editor')
    for c in [editor,visitor()]:
        expected=403 if c is editor else 401
        assert c.post(BASE+'/preview',json=draft()).status_code==expected
        assert c.get(BASE+'?workspace=demo').status_code==expected
        assert c.get(BASE+'/1').status_code==expected
        assert c.post(BASE+'/1/revoke').status_code==expected
    bare=TestClient(api.app,cookies=client.cookies)
    assert bare.post(BASE+'/preview',json=draft()).status_code==403
    malformed=[draft(rows=[]),draft(*[row(str(i)) for i in range(51)]),draft(row(),row()),draft(row('a',role='student'),row('b',role='student')),
               draft(row(role='admin')),draft(row(entityIds=['s1','s1'])),draft(row(role='student',entityIds=['s1','s2'])),draft(days=31)]
    for body in malformed: assert client.post(BASE+'/preview',json=body).status_code==422
    old=issue_one(client);state=api.read('demo');next(s for s in state.students if s.id=='s1').active=False;api.save('demo',state)
    assert client.post(BASE+'/preview',json=draft()).status_code==422
    assert register(visitor(),old['code']).status_code==422
    assert client.post('/api/access/invitations',json=dict(recipient='X',role='parent',grants=[dict(workspace='demo',view='student',entityIds=['s1'])])).status_code==422


def test_history_pagination_and_backup_restore_never_restore_raw_codes(client,tmp_path,monkeypatch):
    from tools.backup_school import create_backup
    enable(client);first,payload=issue(client)
    for i in range(21): issue(client,draft(row(str(i))))
    first_page=client.get(BASE+'?workspace=demo').json()
    second_page=client.get(BASE+'?workspace=demo&before='+str(first_page['nextBefore'])).json()
    assert len(first_page['items'])==20 and len(second_page['items'])==2 and second_page['nextBefore'] is None
    assert client.get(BASE+'?workspace=school').json()['items']==[]
    backup=tmp_path/'restore';source=api.DB;create_backup(source,backup)
    monkeypatch.setattr(api,'DB',backup/source.name)
    result=client.get(BASE+'/'+str(first['id']));assert result.status_code==200
    assert 'code' not in result.json()['items'][0]
    register(visitor(),first['items'][0]['code']).raise_for_status()
