from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from backend import api, handover, families
from test_access import client, enable, create, signed


def path(part):return '/api/handover/'+part+('&' if '?' in part else '?')+'workspace=demo'
def command(**kw):return dict(requestId=str(uuid4()),**kw)
def ok(r):assert r.status_code==200,r.text;return r.json()
def get(c,part):return ok(c.get(path(part)))
def post(c,part,**kw):return c.post(path(part),json=command(**kw))
def settings(c,**kw):
    cfg=get(c,'options')['config'];cfg.update(kw)
    return ok(c.put(path('config'),json=command(**cfg,reason='Synthetic school policy')))
def proposal(c,student='s1',**kw):
    data=dict(studentId=student,kind='representative',name='Synthetic adult',relationship='Relative',contact='+7 000 000 0000',startDate='2026-10-15',endDate='2027-06-11',consentConfirmed=True)
    data.update(kw);return ok(post(c,'adults',**data))
def approve(c,id,**kw):
    d=dict(version=get(c,'adults/'+id)['version'],action='approve',reason='Consent and identity checked by school',consentChecked=True,identityChecked=True);d.update(kw)
    return post(c,'adults/'+id+'/decision',**d)
def adult(c,parent=None,**kw):
    a=proposal(parent or signed('parent'),**kw);ok(approve(c,a['id']));return get(c,'adults/'+a['id'])
def pickup(c,a,**kw):
    d=dict(adultId=a['id'],adultVersion=a['version'],day='2026-10-15',expectedTime='17:30',locationId='main',note='Synthetic request');d.update(kw)
    return ok(post(c,'pickups',**d))
def action(c,id,name,**kw):
    d=dict(version=get(c,'pickups/'+id)['version'],action=name);d.update(kw)
    return post(c,'pickups/'+id+'/action',**d)
def review(c,id,**kw):
    d=dict(version=get(c,'pickups/'+id)['version'],identityMethod='known-person',identityChecked=True,childPresent=True,reason='');d.update(kw)
    return d,ok(post(c,'pickups/'+id+'/review',**d))
def complete(c,id):
    data,p=review(c,id)
    return post(c,'pickups/'+id+'/complete',**data,previewHash=p['previewHash'],reviewedAt=p['reviewedAt'],handedOver=True)


@pytest.fixture
def setup(client,monkeypatch):
    clock=[datetime(2026,10,15,12,tzinfo=timezone.utc)]
    monkeypatch.setattr(handover,'now',lambda:clock[0]);monkeypatch.setattr(families,'now',lambda:clock[0])
    enable(client);people={r:create(client,r) for r in ['parent','student','teacher','staff']}
    people['other']=create(client,'parent',login='other',grants=[dict(workspace='demo',view='student',entityIds=['s3'])])
    settings(client,enabled=True,policy='Synthetic school process: employee checks the approved adult and confirms physical handover.')
    return people,clock


def test_disabled_by_default_and_explicit_school_configuration(client):
    enable(client);create(client,'parent');p=signed('parent')
    assert not get(client,'options')['config']['enabled']
    assert post(p,'adults',studentId='s1',kind='parent',startDate='2026-10-15',endDate='2027-06-11',consentConfirmed=True).status_code==409
    cfg=get(client,'options')['config'];cfg['enabled']=True
    assert client.put(path('config'),json=command(**cfg,reason='Missing procedure')).status_code==422
    assert p.put(path('config'),json=command(**cfg,reason='Cannot self enable')).status_code in {403,422}


def test_parent_proposal_cannot_self_approve_and_private_details(client,setup):
    p=signed('parent');a=proposal(p);teacher=signed('teacher');other=signed('other')
    assert approve(p,a['id']).status_code==403
    assert not get(other,'adults')['items'] and other.get(path('adults/'+a['id'])).status_code==404
    assert teacher.get(path('adults/'+a['id'])).status_code==404
    assert post(p,'pickups',adultId=a['id'],adultVersion=1,day='2026-10-15',expectedTime='17:30',locationId='main').status_code==409
    assert approve(client,a['id'],identityChecked=False).status_code==422
    ok(approve(client,a['id']));a=get(p,'adults/'+a['id']);assert a['status']=='approved'
    second=create(client,'parent',login='second');c=signed('second');other_adult=get(c,'adults/'+a['id'])
    assert 'contact' not in other_adult['detail'] and not other_adult['canRevoke'] and a['canRevoke']
    assert 'contact' in a['detail'];assert client.get(path('adults/'+a['id'])).status_code==200


def test_parent_self_uses_canonical_account_name_and_no_sponsor_spoof(client,setup):
    people,_=setup;p=signed('parent');a=proposal(p,kind='parent',name='Forged name')
    assert get(p,'adults/'+a['id'])['detail']['name']==people['parent']['name']
    assert post(p,'adults',studentId='s1',kind='parent',sponsorId=people['other']['id'],startDate='2026-10-15',endDate='2027-06-11',consentConfirmed=True).status_code==403
    assert post(p,'adults',studentId='s3',kind='parent',startDate='2026-10-15',endDate='2027-06-11',consentConfirmed=True).status_code==404


def test_two_guardians_cannot_create_two_departures(client,setup):
    a=adult(client);create(client,'parent',login='second');clients=[signed('parent'),signed('second')]
    payload=dict(adultId=a['id'],adultVersion=2,day='2026-10-15',expectedTime='17:30',locationId='main')
    with ThreadPoolExecutor(2) as pool:results=list(pool.map(lambda c:post(c,'pickups',**payload),clients))
    assert sorted(r.status_code for r in results)==[200,409]
    assert len(get(client,'pickups')['items'])==1


def test_lost_response_replay_and_changed_payload(client,setup):
    a=adult(client);p=signed('parent');body=command(adultId=a['id'],adultVersion=2,day='2026-10-15',expectedTime='17:30',locationId='main')
    first=ok(p.post(path('pickups'),json=body));assert ok(p.post(path('pickups'),json=body))==first
    body['expectedTime']='18:00';assert p.post(path('pickups'),json=body).status_code==409
    assert len(get(client,'pickups')['items'])==1


def test_physical_handover_requires_staff_and_current_review(client,setup):
    p=signed('parent');o=pickup(p,adult(client));id=o['id']
    create(client,'parent',login='second');peer=get(signed('second'),'pickups/'+id)
    assert not peer['canArrive'] and not peer['canCancel']
    mine=get(p,'pickups/'+id);assert mine['canArrive'] and mine['canCancel']
    body=dict(version=1,identityMethod='known-person',identityChecked=True,childPresent=True)
    assert post(p,'pickups/'+id+'/review',**body).status_code==404
    assert post(client,'pickups/'+id+'/review',**(body|dict(childPresent=False))).status_code==422
    d,pv=review(client,id);before=get(client,'history?pickupId='+id)
    assert get(client,'pickups/'+id)['status']=='requested' and get(client,'history?pickupId='+id)==before
    assert post(client,'pickups/'+id+'/complete',**d,previewHash=pv['previewHash'],reviewedAt=pv['reviewedAt'],handedOver=False).status_code==422
    body=command(**d,previewHash=pv['previewHash'],reviewedAt=pv['reviewedAt'],handedOver=True)
    first=ok(client.post(path('pickups/'+id+'/complete'),json=body));assert first['status']=='completed'
    assert ok(client.post(path('pickups/'+id+'/complete'),json=body))==first
    assert action(p,id,'cancel',reason='Too late').status_code==409


def test_concurrent_confirmations_only_one_physical_event(client,setup):
    o=pickup(signed('parent'),adult(client));d,p=review(client,o['id'])
    def call(_):return post(client,'pickups/'+o['id']+'/complete',**d,previewHash=p['previewHash'],reviewedAt=p['reviewedAt'],handedOver=True)
    with ThreadPoolExecutor(2) as pool:rs=list(pool.map(call,range(2)))
    assert sorted(r.status_code for r in rs)==[200,409]
    assert sum(x['action']=='completed' for x in get(client,'history?pickupId='+o['id'])['items'])==1


def test_revoke_adult_cancels_all_pending_and_prevents_stale_confirm(client,setup):
    a=adult(client);p=signed('parent');o=pickup(p,a);pickup(p,a,day='2026-10-16');d,pv=review(client,o['id'])
    ok(post(p,'adults/'+a['id']+'/decision',version=2,action='revoke',reason='Family revoked authorization'))
    assert all(x['status']=='cancelled' for x in get(client,'pickups?end=2026-10-16')['items'])
    assert post(client,'pickups/'+o['id']+'/complete',**d,previewHash=pv['previewHash'],reviewedAt=pv['reviewedAt'],handedOver=True).status_code==409
    assert post(p,'pickups',adultId=a['id'],adultVersion=3,day='2026-10-15',expectedTime='18:00',locationId='main').status_code==409


def test_family_link_revocation_blocks_reads_replay_and_handover(client,setup):
    people,_=setup;p=signed('parent');a=adult(client);body=command(adultId=a['id'],adultVersion=2,day='2026-10-15',expectedTime='17:30',locationId='main');o=ok(p.post(path('pickups'),json=body));d,pv=review(client,o['id'])
    with api.connect() as db:db.execute('UPDATE accounts SET permissions=?,version=version+1 WHERE id=?',(json.dumps(dict(workspaces=['demo'],grants=[])),people['parent']['id']))
    assert p.get(path('pickups/'+o['id'])).status_code==403
    assert p.post(path('pickups'),json=body).status_code==403
    assert post(p,'pickups/'+o['id']+'/action',version=1,action='cancel',reason='No access').status_code==403
    assert post(client,'pickups/'+o['id']+'/complete',**d,previewHash=pv['previewHash'],reviewedAt=pv['reviewedAt'],handedOver=True).status_code==409


def test_family_date_expiry_after_future_request(client,setup):
    people,clock=setup
    with api.connect() as db:
        row=db.execute('SELECT permissions FROM accounts WHERE id=?',(people['parent']['id'],)).fetchone();perms=json.loads(row[0]);perms['familyRules']=[dict(workspace='demo',studentId='s1',startDate='2026-10-01',endDate='2026-10-15')]
        db.execute('UPDATE accounts SET permissions=? WHERE id=?',(json.dumps(perms),people['parent']['id']))
    a=adult(client);o=pickup(signed('parent'),a,day='2026-10-16');clock[0]+=timedelta(days=1)
    assert post(client,'pickups/'+o['id']+'/review',version=1,identityMethod='known-person',identityChecked=True,childPresent=True).status_code==409
    assert signed('parent').get(path('adults')).status_code==403


def test_issuer_scope_revocation_and_manager_request_origin(client,setup):
    people,_=setup;snap=api.read('demo');own=snap.students[0].classId;other=next(x.classId for x in snap.students if x.classId!=own)
    settings(client,delegates=[dict(userId=people['teacher']['id'],role='issuer',classIds=[own]),dict(userId=people['staff']['id'],role='manager',classIds=[own])])
    a=adult(client);o=pickup(signed('staff'),a);t=signed('teacher');d,pv=review(t,o['id'])
    assert approve(t,proposal(signed('parent'))['id']).status_code==404
    settings(client,delegates=[dict(userId=people['teacher']['id'],role='issuer',classIds=[other])])
    assert post(t,'pickups/'+o['id']+'/complete',**d,previewHash=pv['previewHash'],reviewedAt=pv['reviewedAt'],handedOver=True).status_code==404
    assert post(client,'pickups/'+o['id']+'/review',version=1,identityMethod='known-person',identityChecked=True,childPresent=True).status_code==409


def test_preview_expires_and_configuration_changes_invalidate_it(client,setup):
    _,clock=setup;o=pickup(signed('parent'),adult(client));d,p=review(client,o['id']);clock[0]+=timedelta(minutes=6)
    assert post(client,'pickups/'+o['id']+'/complete',**d,previewHash=p['previewHash'],reviewedAt=p['reviewedAt'],handedOver=True).status_code==409
    d,p=review(client,o['id']);settings(client,policy='Updated synthetic policy with a different school verification process.')
    assert post(client,'pickups/'+o['id']+'/complete',**d,previewHash=p['previewHash'],reviewedAt=p['reviewedAt'],handedOver=True).status_code==409
    settings(client,enabled=False)
    assert post(client,'pickups/'+o['id']+'/review',**d).status_code==409
    ok(action(signed('parent'),o['id'],'cancel',reason='Cancel while disabled'))


def test_review_is_bound_to_employee(client,setup):
    create(client,'admin',login='second-admin');other=signed('second-admin');o=pickup(signed('parent'),adult(client));d,p=review(client,o['id'])
    assert post(other,'pickups/'+o['id']+'/complete',**d,previewHash=p['previewHash'],reviewedAt=p['reviewedAt'],handedOver=True).status_code==409
    ok(complete(other,o['id']))


def test_return_is_explicit_and_preserves_prior_handover(client,setup):
    a=adult(client);p=signed('parent');o=pickup(p,a);ok(complete(client,o['id']))
    assert post(p,'pickups',adultId=a['id'],adultVersion=2,day='2026-10-15',expectedTime='18:00',locationId='main').status_code==409
    assert action(client,o['id'],'returned',reason='Returned').status_code==422
    ok(action(client,o['id'],'returned',reason='Employee physically received child back',childReturned=True))
    row=get(client,'pickups/'+o['id']);assert row['completed_at'] and row['returned_at'] and row['status']=='returned'
    assert pickup(p,a)['id']!=o['id']


def test_arrival_ready_dates_notes_and_family_history_redaction(client,setup):
    p=signed('parent');a=adult(client);o=pickup(p,a,day='2026-10-16')
    assert action(p,o['id'],'arrive').status_code==409
    ok(action(p,o['id'],'cancel',reason='Change date'));o=pickup(p,a)
    ok(action(p,o['id'],'arrive'));ok(action(client,o['id'],'ready'))
    assert action(p,o['id'],'ready').status_code==404
    ok(action(client,o['id'],'note',reason='Internal staff correction note'))
    hist=get(p,'history?pickupId='+o['id']);assert all('reason' not in x and 'actor_name' not in x for x in hist['items'])
    assert 'Internal staff' not in json.dumps(hist)


def test_notification_failure_rolls_back_physical_confirmation(client,setup,monkeypatch):
    o=pickup(signed('parent'),adult(client));d,p=review(client,o['id']);before=get(client,'history')
    def fail(*args,**kwargs):raise RuntimeError('Synthetic notification failure')
    monkeypatch.setattr(api.handover,'notify',fail)
    with pytest.raises(RuntimeError):post(client,'pickups/'+o['id']+'/complete',**d,previewHash=p['previewHash'],reviewedAt=p['reviewedAt'],handedOver=True)
    assert get(client,'pickups/'+o['id'])['status']=='requested' and get(client,'history')==before


def test_school_revision_and_same_named_adult_ids(client,setup):
    p=signed('parent');a=adult(client);a2=adult(client);assert a['id']!=a2['id'];o=pickup(p,a);d,pv=review(client,o['id'])
    state=api.read('demo');state.students[0].name='Updated synthetic child';api.save('demo',state)
    assert post(client,'pickups/'+o['id']+'/complete',**d,previewHash=pv['previewHash'],reviewedAt=pv['reviewedAt'],handedOver=True).status_code==409
    ok(post(p,'adults/'+a2['id']+'/decision',version=2,action='revoke',reason='Revoke different same-name adult'))
    assert get(client,'pickups/'+o['id'])['status']=='requested';ok(complete(client,o['id']))


def test_csv_unicode_scope_and_unchanged_school_document(client,setup):
    state=api.read('demo');a=adult(client,name='=HYPERLINK("test")');pickup(signed('parent'),a)
    r=client.get(path('pickups/export'));assert r.status_code==200 and "'=HYPERLINK" in r.text and r.headers['cache-control']=='no-store'
    assert signed('parent').get(path('pickups/export')).status_code==403
    assert not get(signed('other'),'pickups')['items'] and api.read('demo')==state
    a2=adult(client,name='Әли Қаныш');assert len(get(client,'adults?q=әли қаныш')['items'])==1


def test_csrf_strict_confirmation_and_no_student_access(client,setup):
    a=adult(client);assert not get(signed('student'),'adults')['items']
    assert signed('student').get(path('adults/'+a['id'])).status_code==404
    o=pickup(signed('parent'),a)
    assert post(client,'pickups/'+o['id']+'/review',version=1,identityMethod='known-person',identityChecked='true',childPresent=True).status_code==422
    assert client.post(path('pickups/'+o['id']+'/action'),headers={'X-Atlas-Request':'0'},json=command(version=1,action='ready')).status_code==403
    assert TestClient(api.app).get(path('options')).status_code==401


def test_backup_restores_approvals_pickups_history_and_workspaces(client,setup,tmp_path):
    from tools.backup_school import create_backup
    from tools.restore_school import verify_backup,restore_backup
    import sqlite3
    o=pickup(signed('parent'),adult(client));ok(complete(client,o['id']))
    backup=tmp_path/'backup';target=tmp_path/'restored';create_backup(api.DB,backup);verify_backup(backup);restore_backup(backup,target)
    with api.connect() as db,sqlite3.connect(target/api.DB.name) as restored:
        names=[r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND (name LIKE 'handover_%' OR name='workspaces')")]
        for name in names:assert sorted([tuple(x) for x in db.execute('SELECT * FROM '+name)],key=repr)==sorted(restored.execute('SELECT * FROM '+name).fetchall(),key=repr)


def regrant(account_id,ids):
    # Rewrites the account's pupil grants and bumps its version, as any edit of the rights does.
    with api.connect() as db:
        raw=json.loads(db.execute('SELECT permissions FROM accounts WHERE id=?',(account_id,)).fetchone()[0]);raw['grants']=[dict(workspace='demo',view='student',entityIds=ids)]
        db.execute('UPDATE accounts SET permissions=?,version=version+1 WHERE id=?',(json.dumps(raw),account_id))


def test_second_child_keeps_approved_adult_and_request(client,setup):
    people,_=setup;p=signed('parent');a=adult(client);o=pickup(p,a)
    regrant(people['parent']['id'],['s1','s2'])
    assert get(p,'adults/'+a['id'])['unavailableReason']=='' and get(client,'pickups/'+o['id'])['unavailableReason']==''
    assert complete(client,o['id']).status_code==200


def test_unrelated_change_before_approval_does_not_block_it(client,setup):
    people,_=setup;a=proposal(signed('parent'))
    regrant(people['parent']['id'],['s1','s2'])
    ok(approve(client,a['id']))


def test_real_link_loss_still_blocks(client,setup):
    people,_=setup;a=adult(client);o=pickup(signed('parent'),a)
    regrant(people['parent']['id'],['s2'])
    assert get(client,'adults/'+a['id'])['unavailableReason'] and get(client,'pickups/'+o['id'])['unavailableReason']
    assert post(client,'pickups',adultId=a['id'],adultVersion=a['version'],day='2026-10-15',expectedTime='17:30',locationId='main').status_code==409
