from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
from uuid import uuid4

import pytest
from backend import api, families
from test_access import client, enable, create, signed
from test_invitations import issue, register, visitor, link as accept_invitation
from test_messages import url as messages_url, detail
from test_message_files_groups import group, upload, download
from test_homework import setup as homework_setup, get as homework_get, answer, target
from test_journal import report as journal_report


def path(parent,student='s1',space='demo'):
    return f'/api/access/families/{parent}/{student}?workspace={space}'


def get(client,parent,student='s1'):
    result=client.get(path(parent,student));assert result.status_code==200,result.text
    return result.json()


def save(client,parent,student='s1',**updates):
    record=get(client,parent,student)
    body={k:record[k] for k in ['version','accountVersion','relationship','verified','evidence','reason','startDate','endDate','status']}
    return client.put(path(parent,student),json=body|dict(reason='Проверено ответственным сотрудником')|updates)


def account(client,login='parent'):
    return next(u for u in client.get('/api/access/users').json() if u['login']==login)


def schedule(user,student='s1'):
    return user.get('/api/portal/schedule',params=dict(workspace='demo',view='student',entityId=student))


def test_family_verification_history_and_parent_privacy(client):
    enable(client);parent=create(client,'parent');create(client,'parent',login='other-family');create(client,'teacher')
    before=api.read('demo').model_dump();reader=signed('parent');other=signed('other-family')
    assert get(client,parent['id'])['verified'] is False
    assert save(client,parent['id'],verified=True).status_code==422
    assert save(client,parent['id'],verified=True,relationship='mother',evidence='Проверка оригинала: внутренний реестр 123').status_code==200
    mine=reader.get('/api/portal/family').json()['items'];assert len(mine)==1 and mine[0]['verified']
    assert not {'evidence','reason','login'} & mine[0].keys()
    assert 'внутренний реестр' not in json.dumps(reader.get('/api/auth/status').json(),ensure_ascii=False)
    assert all(x['parentId']!=parent['id'] for x in other.get('/api/portal/family').json()['items'])
    for user in [reader,other,signed('teacher')]:
        assert user.get(path(parent['id'])).status_code==403
        assert user.get('/api/access/families?workspace=demo').status_code==403
        assert user.put(path(parent['id']),json={}).status_code==403
    history=client.get(f"/api/access/families/{parent['id']}/s1/history?workspace=demo").json()['items']
    assert len(history)==2 and history[0]['document']['verified'] and history[0]['document']['evidence'].endswith('123')
    assert not history[1]['document']['verified']
    with api.connect() as db:
        assert not db.execute("SELECT 1 FROM audit WHERE detail LIKE '%внутренний%'").fetchone()
    assert api.read('demo').model_dump()==before


def test_revoke_and_restore_every_parent_surface_without_relogin(client):
    record,pupil,parent,teacher=homework_setup(client);parent_id=account(client)['id'];key=record['lessonKey']
    answer(pupil,key,homework_get(pupil,key));thread=group(teacher)
    draft=str(uuid4());file=upload(teacher,draft,thread).json()['id']
    assert teacher.post(messages_url('threads/'+thread+'/reply'),json=dict(requestId=str(uuid4()),version=1,text='Файл',draftId=draft,fileIds=[file])).status_code==200
    assert schedule(parent).status_code==200 and journal_report(parent).status_code==200 and download(parent,file).status_code==200
    assert parent.get(target(key)).status_code==200
    assert parent.get('/api/intake/student?workspace=demo&studentId=s1').status_code==200
    assert save(client,parent_id,status='revoked').status_code==200
    assert schedule(parent).status_code==403 and journal_report(parent).status_code==403 and parent.get(target(key)).status_code==403
    assert parent.get('/api/intake/student?workspace=demo&studentId=s1').status_code==403
    assert parent.get(messages_url('threads/'+thread)).status_code==403 and download(parent,file).status_code==403
    assert parent.get('/api/portal/options').json()==[]
    mine=parent.get('/api/portal/family').json()['items'];assert mine[0]['activity']=='revoked'
    assert parent.get('/api/notifications/inbox').json()['items'][0]['action']=='family.updated'
    assert schedule(pupil).status_code==200 and detail(teacher,thread)['canManage']
    assert save(client,parent_id,status='active').status_code==200
    assert schedule(parent).status_code==200 and journal_report(parent).status_code==200 and download(parent,file).status_code==200
    assert parent.get(target(key)).status_code==200


def test_dates_follow_school_midnight_inclusive_and_no_restart(client,monkeypatch):
    enable(client);person=create(client,'parent');parent=signed('parent')
    moment=datetime(2026,10,14,18,59,tzinfo=timezone.utc)
    monkeypatch.setattr(families,'now',lambda:moment)
    assert save(client,person['id'],startDate='2026-10-14',endDate='2026-10-14').status_code==200
    assert schedule(parent).status_code==200
    moment=datetime(2026,10,14,19,0,tzinfo=timezone.utc)
    assert schedule(parent).status_code==403
    assert parent.get('/api/portal/family').json()['items'][0]['activity']=='expired'
    assert account(client)['grants'][0]['entityIds']==['s1']  # Admin sees configured dates/grants, not an empty transient projection.
    assert save(client,person['id'],startDate='2026-10-16',endDate=None).status_code==200
    assert schedule(parent).status_code==403
    moment=datetime(2026,10,15,19,0,tzinfo=timezone.utc)
    assert schedule(parent).status_code==200
    assert save(client,person['id'],startDate='2026-10-20',endDate='2026-10-19').status_code==422


def test_multi_child_multi_guardian_scope_and_private_evidence(client):
    enable(client);first=create(client,'parent');second=create(client,'parent',login='second');a=signed('parent');b=signed('second')
    assert save(client,first['id'],'s2',relationship='father',evidence='Внутренние сведения').status_code==200
    assert schedule(a,'s2').status_code==200 and schedule(b,'s2').status_code==403
    assert save(client,first['id'],status='revoked').status_code==200
    assert schedule(a).status_code==403 and schedule(a,'s2').status_code==200 and schedule(b).status_code==200
    assert len(a.get('/api/portal/family').json()['items'])==2
    # Revocation retains history and keeps the other child's grant.
    assert {s for g in account(client)['grants'] for s in g['entityIds']}=={'s2'}
    assert client.get(path(first['id'],space='school')).status_code==404


def test_old_invitation_and_account_editor_cannot_undo_revocation(client):
    enable(client);person=create(client,'parent');parent=signed('parent');invite=issue(client,['s1','s2'])
    assert save(client,person['id'],status='revoked').status_code==200
    assert accept_invitation(parent,invite['code']).status_code==409
    fresh=account(client)
    body={k:v for k,v in fresh.items() if k!='id'}|dict(grants=person['grants'])
    assert client.put('/api/access/users/'+person['id'],json=body).status_code==409
    assert schedule(parent,'s2').status_code==403
    assert client.get('/api/access/invitations').json()['items'][0]['status']=='pending'
    assert save(client,person['id'],status='active').status_code==200
    assert accept_invitation(parent,invite['code']).status_code==200
    assert schedule(parent,'s2').status_code==200
    assert get(client,person['id'],'s2')['verified'] is False


def test_invitations_create_unverified_records_and_preserve_time_limits(client):
    enable(client);invite=issue(client,['s1']);parent=visitor()
    registered=register(parent,invite['code']);assert registered.status_code==200
    id=registered.json()['user']['id']
    assert parent.get('/api/portal/family').json()['items'][0]['verified'] is False
    assert save(client,id,startDate='2099-01-01').status_code==200
    later=issue(client,['s1','s2']);assert accept_invitation(parent,later['code']).status_code==200
    assert schedule(parent).status_code==403 and schedule(parent,'s2').status_code==200
    history=client.get(f'/api/access/families/{id}/s1/history?workspace=demo').json()['items']
    assert history[-1]['source']==f"invitation:{invite['id']}"


def test_legacy_read_does_not_mutate_accounts_then_explicit_verification(client):
    enable(client);person=create(client,'parent');parent=signed('parent')
    with api.connect() as db:
        db.execute('DELETE FROM family_links');db.execute('DELETE FROM family_history')
        raw=json.loads(db.execute('SELECT permissions FROM accounts WHERE id=?',(person['id'],)).fetchone()[0]);raw.pop('familyRules')
        db.execute('UPDATE accounts SET permissions=? WHERE id=?',(json.dumps(raw),person['id']))
        original=db.execute('SELECT * FROM accounts WHERE id=?',(person['id'],)).fetchone()
    assert client.get('/api/access/families?workspace=demo').json()['items'][0]['legacy']
    assert parent.get('/api/portal/family').json()['items'][0]['legacy']
    assert schedule(parent).status_code==200
    with api.connect() as db:
        assert db.execute('SELECT * FROM accounts WHERE id=?',(person['id'],)).fetchone()==original
        assert db.execute('SELECT count(*) FROM family_links').fetchone()[0]==0
    assert save(client,person['id'],relationship='guardian',verified=True,evidence='Проверил администратор').status_code==200
    assert not get(client,person['id'])['legacy']
    older=create(client,'parent',login='legacy-other')
    with api.connect() as db:
        db.execute('DELETE FROM family_links WHERE parent_id=?',(older['id'],));db.execute('DELETE FROM family_history WHERE parent_id=?',(older['id'],))
    assert client.put('/api/access/users/'+older['id'],json={k:v for k,v in older.items() if k!='id'}|dict(grants=[])).status_code==200
    history=client.get(f"/api/access/families/{older['id']}/s1/history?workspace=demo").json()['items']
    assert [(h['version'],h['document']['status']) for h in history]==[(1,'revoked'),(0,'active')]


def test_concurrent_account_or_relationship_edits_and_notification_rollback(client,monkeypatch):
    enable(client);person=create(client,'parent');id=person['id'];before=get(client,id)
    body={k:before[k] for k in ['version','accountVersion','relationship','verified','evidence','reason','startDate','endDate','status']}
    with ThreadPoolExecutor(max_workers=2) as pool:
        codes=list(pool.map(lambda status:client.put(path(id),json=body|dict(status=status)).status_code,['active','revoked']))
    assert sorted(codes)==[200,409]
    snapshot=get(client,id)
    def fail(*args,**kwargs):raise RuntimeError('notification failed')
    monkeypatch.setattr(api.notifications,'deliver',fail)
    with pytest.raises(RuntimeError):save(client,id,status='active',reason='Не должно сохраниться')
    assert get(client,id)==snapshot
    current=account(client);changed={k:v for k,v in current.items() if k!='id'}|dict(name='Новое имя')
    assert client.put('/api/access/users/'+id,json=changed).status_code==200
    assert client.put(path(id),json=body).status_code==409


def test_role_change_revokes_relationship_and_empty_parent_account_can_be_linked(client):
    enable(client);person=create(client,'parent',grants=[]);assert save(client,person['id']).status_code==200
    current=account(client);body={k:v for k,v in current.items() if k!='id'}|dict(role='editor',grants=[],workspaces=['demo'])
    assert client.put('/api/access/users/'+person['id'],json=body).status_code==200
    record=get(client,person['id']);assert record['status']=='revoked' and not record['accountActive']
    assert save(client,person['id']).status_code==422


def test_backup_restores_family_history_and_current_permissions(client,tmp_path,monkeypatch):
    from tools.backup_school import create_backup
    enable(client);person=create(client,'parent');parent=signed('parent')
    assert save(client,person['id'],relationship='mother',verified=True,evidence='Закрытое основание',status='revoked').status_code==200
    original=api.DB;target_dir=tmp_path/'family-backup';create_backup(original,target_dir);monkeypatch.setattr(api,'DB',target_dir/original.name)
    assert schedule(parent).status_code==403 and get(client,person['id'])['evidence']=='Закрытое основание'
    history=client.get(f"/api/access/families/{person['id']}/s1/history?workspace=demo").json()['items'];assert len(history)==2
    assert save(client,person['id'],status='active').status_code==200 and schedule(parent).status_code==200


def test_paginated_registry_and_private_history_do_not_drop_old_relationships(client):
    enable(client);person=create(client,'parent');parent=signed('parent')
    with api.connect() as db:
        doc=families.base()|dict(status='revoked',evidence='Личное основание')
        db.executemany('INSERT INTO family_links VALUES (?,?,?,?,?,?)',[('demo',person['id'],f'historical{i:03d}',f'Прежний ученик {i}',1,json.dumps(doc)) for i in range(520)])
    first=client.get('/api/access/families?workspace=demo').json();assert len(first['items'])==50 and first['nextOffset']==50
    last=client.get('/api/access/families?workspace=demo&offset=500').json();assert len(last['items'])==21 and last['nextOffset'] is None
    assert len(client.get('/api/access/families?workspace=demo&q=Прежний%20ученик%20519').json()['items'])==1
    assert get(client,person['id'],'historical519')['evidence']=='Личное основание'
    own=parent.get('/api/portal/family?offset=500').json();assert len(own['items'])==21 and own['nextOffset'] is None
    assert 'Личное основание' not in json.dumps(own,ensure_ascii=False)


def test_message_write_rechecks_expiry_at_commit(client,monkeypatch):
    from test_messages import setup, payload
    people=setup(client);parent=signed('parent');teacher=signed('teacher')
    moment=datetime(2026,10,14,18,59,tzinfo=timezone.utc);monkeypatch.setattr(families,'now',lambda:moment)
    assert save(client,people['parent']['id'],endDate='2026-10-14').status_code==200
    thread=parent.post(messages_url(),json=payload(people['teacher']['id'])).json()['id']
    original=api.messages.recheck
    def expire(*args):
        nonlocal moment
        moment=datetime(2026,10,14,19,0,tzinfo=timezone.utc)
        original(*args)
    monkeypatch.setattr(api.messages,'recheck',expire)
    response=parent.post(messages_url('threads/'+thread+'/reply'),json=dict(requestId=str(uuid4()),version=1,text='После полуночи'))
    assert response.status_code==409
    with api.connect() as db:assert db.execute('SELECT count(*) FROM message_posts WHERE thread_id=?',(thread,)).fetchone()[0]==1
