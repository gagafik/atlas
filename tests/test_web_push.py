"""Synthetic subscriptions and an injected transport; never contacts push providers."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta, datetime, timezone
from zoneinfo import ZoneInfo
import json
import time
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from backend import api, web_push
from backend.access import digest, now
from backend.notifications import permission_key
from test_access import client, enable, create, signed, PASSWORD, HEADERS
from test_grade_feed import family, published

BASE='/api/notifications/push'


@pytest.fixture
def push(monkeypatch):
    monkeypatch.setenv('ATLAS_WEB_PUSH_ENABLED','1')
    monkeypatch.setenv('ATLAS_WEB_PUSH_SUBJECT','mailto:admin@example.org')
    clock=[time.time()]
    calls=[]
    def send(subscription,key,subject,payload=web_push.PAYLOAD):
        calls.append((subscription,key,subject,payload))
        return 201,None
    runner=web_push.PushService(api.access,send=send,clock=lambda:clock[0])
    return runner,clock,calls


def body(client, **values):
    private=ec.generate_private_key(ec.SECP256R1())
    public=private.public_key().public_bytes(Encoding.X962,PublicFormat.UncompressedPoint)
    return dict(endpoint='https://fcm.googleapis.com/fcm/send/'+uuid4().hex,
        keys=dict(p256dh=web_push.b64(public),auth=web_push.b64(b'0123456789abcdef')),
        name='Учебный телефон',publicKey=client.get(BASE+'/options').json()['publicKey'],version=0,**values)


def subscribe(client, data=None):
    data=data or body(client)
    response=client.post(BASE+'/devices',json=data)
    assert response.status_code==200,response.text
    return response.json(),data


def notify(user, action='message.received'):
    with api.access.lock,api.connect() as db:
        api.notifications.deliver(db,[user],'demo',1,action)


def queue():
    with api.connect() as db:
        return db.execute('SELECT device_id,status,attempts,available,last_error,generation FROM push_outbox ORDER BY device_id').fetchall()


@pytest.mark.parametrize('role',['admin','teacher','student','parent','staff','editor'])
def test_every_account_can_opt_in_and_secrets_are_not_returned(client,push,role):
    account=enable(client)
    user=client
    if role!='admin':account=create(client,role);user=signed(role)
    before=api.read('demo')
    device,data=subscribe(user)
    options=user.get(BASE+'/options')
    assert options.status_code==200 and options.headers['cache-control']=='no-store'
    assert options.json()['configured'] and len(options.json()['devices'])==1
    assert device['active'] and device['fingerprint']==digest(data['endpoint'])
    with api.connect() as db:
        private=db.execute('SELECT private_key FROM push_config').fetchone()[0]
        audit=json.dumps(db.execute('SELECT * FROM audit').fetchall())
    for secret in [data['endpoint'],data['keys']['auth'],data['keys']['p256dh'],private]:
        assert secret not in options.text and secret not in audit and secret not in json.dumps(device)
    assert api.read('demo')==before
    assert TestClient(api.app,headers=HEADERS).get(BASE+'/options').status_code==401
    if role!='admin':assert user.get('/api/access/push/status').status_code==403


def test_disabled_and_local_mode_do_not_generate_keys(client,monkeypatch):
    assert client.get(BASE+'/options').json()['reason']=='login'
    enable(client)
    monkeypatch.delenv('ATLAS_WEB_PUSH_ENABLED',raising=False)
    result=client.get(BASE+'/options').json()
    assert not result['configured'] and result['publicKey'] is None
    with api.connect() as db:assert not db.execute('SELECT * FROM push_config').fetchall()
    monkeypatch.setenv('ATLAS_WEB_PUSH_ENABLED','1')
    monkeypatch.setenv('ATLAS_WEB_PUSH_SUBJECT','not a contact')
    with pytest.raises(ValueError,match='ATLAS_WEB_PUSH_SUBJECT'):web_push.PushService(api.access).start()


def test_coalescing_commits_with_inbox_and_no_network_on_save(client,push):
    account=enable(client);device,data=subscribe(client)
    service,clock,calls=push
    for _ in range(20):notify(account)
    assert len(queue())==1 and queue()[0][1]=='queued' and queue()[0][5]==20
    assert calls==[]
    with pytest.raises(RuntimeError):
        with api.access.lock,api.connect() as db:
            api.notifications.deliver(db,[account],'demo',2,'message.received')
            raise RuntimeError('rollback')
    assert queue()[0][5]==20
    assert len(client.get('/api/notifications/inbox').json()['items'])==20
    clock[0]+=3
    assert service.process_one() and queue()[0][1]=='sent'
    assert len(calls)==1 and calls[0][0]['endpoint']==data['endpoint']
    assert client.get('/api/notifications/inbox').json()['unread']==20
    assert not service.process_one()


@pytest.mark.parametrize('change',['logout','logout_after_login','password','disabled','permission','muted','read','device_removed','expired'])
def test_revocation_and_current_access_are_rechecked_before_send(client,push,change):
    enable(client);account=create(client)
    user=signed('editor');device,data=subscribe(user)
    notify(account)
    service,clock,calls=push;clock[0]+=3
    job=service.claim()
    if change=='logout':user.post('/api/auth/logout').raise_for_status()
    elif change=='logout_after_login':
        current=signed('editor')
        current.post('/api/auth/logout',json=dict(pushEndpointHash=digest(data['endpoint']))).raise_for_status()
    elif change=='password':user.post('/api/auth/password',json=dict(currentPassword=PASSWORD,password='new password for push')).raise_for_status()
    elif change in ['disabled','permission']:
        update={k:v for k,v in account.items() if k!='id'}
        update.update(active=False) if change=='disabled' else update.update(workspaces=['school'])
        client.put('/api/access/users/'+account['id'],json=update).raise_for_status()
    elif change=='muted':user.put('/api/notifications/preferences',json=dict(enabled=False)).raise_for_status()
    elif change=='read':
        inbox=user.get('/api/notifications/inbox').json()
        user.post('/api/notifications/read',json=dict(throughId=inbox['latestId'])).raise_for_status()
    elif change=='device_removed':user.post(BASE+'/devices/'+device['id']+'/remove',json=dict(version=device['version'])).raise_for_status()
    else:clock[0]+=181*86400
    assert service.prepare(job) is None
    service.finish(job,None)
    assert calls==[]


def test_natural_session_expiry_does_not_end_explicit_opt_in(client,push):
    account=enable(client);subscribe(client);notify(account)
    with api.connect() as db:db.execute('UPDATE sessions SET expires=?',((now()-timedelta(seconds=1)).isoformat(),))
    service,clock,calls=push;clock[0]+=3
    assert service.process_one() and len(calls)==1


def test_permissions_expire_without_account_version_change(client,push,monkeypatch):
    enable(client);parent=create(client,'parent');user=signed('parent');subscribe(user);notify(parent)
    original=api.access.public
    def expired(row,**kwargs):
        value=original(row,**kwargs)
        if value['id']==parent['id']:value['grants']=[]
        return value
    monkeypatch.setattr(api.access,'public',expired)
    service,clock,calls=push;clock[0]+=3
    assert service.process_one() and not calls and queue()[0][1]=='cancelled'


def test_owner_isolation_versions_limits_and_rebinding(client,push):
    enable(client);create(client);user=signed('editor')
    device,data=subscribe(client)
    assert user.post(BASE+'/devices',json=data).status_code==409
    assert not user.get(BASE+'/options').json()['devices']
    for suffix in ['remove','test']:
        assert user.post(BASE+'/devices/'+device['id']+'/'+suffix,json=dict(version=1)).status_code==404
        assert client.post(BASE+'/devices/'+device['id']+'/'+suffix,json=dict(version=99)).status_code==409
    assert client.post(BASE+'/devices',json=data).status_code==409
    updated=client.post(BASE+'/devices',json=data|dict(version=1,name='Новое имя'))
    assert updated.status_code==200 and updated.json()['version']==2
    for _ in range(9):subscribe(client)
    assert client.post(BASE+'/devices',json=body(client)).status_code==409
    client.post(BASE+'/devices/'+device['id']+'/remove',json=dict(version=2)).raise_for_status()
    assert client.post(BASE+'/devices',json=data).status_code==200


def test_device_limit_counts_only_active_subscriptions(client,push):
    enable(client);device,data=subscribe(client)
    for _ in range(9):subscribe(client)
    with api.access.lock,api.connect() as db:db.execute('UPDATE push_devices SET active=0,version=version+1 WHERE id=?',(device['id'],))
    newest,_=subscribe(client)
    refused=client.post(BASE+'/devices',json=data|dict(version=2));assert refused.status_code==409 and '10 устройств' in refused.json()['detail']
    listed=client.get(BASE+'/options').json()['devices']
    assert len(listed)==11 and sum(d['active'] for d in listed)==10
    # Inactive entries stay visible for removal but are bounded to ten per account.
    with api.access.lock,api.connect() as db:
        template=db.execute('SELECT * FROM push_devices WHERE id=?',(device['id'],)).fetchone()
        for i in range(12):
            db.execute('INSERT INTO push_devices VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,0,NULL,?,NULL)',(uuid4().hex,uuid4().hex,template[2],template[3],template[4],template[5],template[6],template[7],1,'Старое',template[10],template[11]-1-i,template[12],'enabled'))
    client.post(BASE+'/devices/'+newest['id']+'/remove',json=dict(version=newest['version'])).raise_for_status()
    latest,_=subscribe(client)
    with api.connect() as db:
        rows=db.execute('SELECT id,active FROM push_devices WHERE user_id=?',(template[5],)).fetchall()
        assert sum(not r[1] for r in rows)==10 and latest['id'] in {r[0] for r in rows} and device['id'] in {r[0] for r in rows}
        assert sum(r[1] for r in rows)==10


def test_device_limit_ignores_expired_and_account_changed_devices_and_pruning_clears_queue(client,push):
    enable(client);devices=[subscribe(client) for _ in range(10)]
    assert client.post(BASE+'/devices',json=body(client)).status_code==409
    (expired,expired_data),(changed,_)=devices[0],devices[1]
    with api.access.lock,api.connect() as db:
        db.execute('UPDATE push_devices SET expires=1 WHERE id=?',(expired['id'],))
        db.execute('UPDATE push_devices SET account_version=account_version-1 WHERE id=?',(changed['id'],))
    # A device expired by time or left behind by an account change no longer holds a slot.
    first,_=subscribe(client);second,_=subscribe(client)
    refused=client.post(BASE+'/devices',json=body(client));assert refused.status_code==409 and '10 устройств' in refused.json()['detail']
    revived=client.post(BASE+'/devices',json=expired_data|dict(version=expired['version']));assert revived.status_code==409 and '10 устройств' in revived.json()['detail']
    listed=client.get(BASE+'/options').json()['devices']
    assert len(listed)==12 and sum(d['active'] for d in listed)==10
    with api.access.lock,api.connect() as db:
        template=db.execute('SELECT * FROM push_devices WHERE id=?',(expired['id'],)).fetchone();extra=[]
        for i in range(12):
            id=uuid4().hex;extra.append(id)
            db.execute('INSERT INTO push_devices VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,0,NULL,?,NULL)',(id,uuid4().hex,template[2],template[3],template[4],template[5],template[6],template[7],1,'Старое',template[10],template[11]-1-i,template[12],'enabled'))
            db.execute("INSERT INTO push_outbox VALUES (?,1,1,1,'queued',0,0,?,NULL,NULL,NULL,NULL)",(id,time.time()+86400))
    client.post(BASE+'/devices/'+first['id']+'/remove',json=dict(version=first['version'])).raise_for_status()
    latest,_=subscribe(client)
    with api.connect() as db:
        kept=[id for id in extra if db.execute('SELECT 1 FROM push_devices WHERE id=?',(id,)).fetchone()]
        queued=[id for id in extra if db.execute('SELECT 1 FROM push_outbox WHERE device_id=?',(id,)).fetchone()]
        orphans=db.execute('SELECT count(*) FROM push_outbox WHERE device_id NOT IN (SELECT id FROM push_devices)').fetchone()[0]
    # Fourteen inactive entries shrink to ten; the pruned devices leave no queue rows behind.
    assert kept==extra[:8] and queued==kept and orphans==0
    listed={d['id']:d for d in client.get(BASE+'/options').json()['devices']}
    assert latest['id'] in listed and sum(not d['active'] for d in listed.values())==10 and sum(d['active'] for d in listed.values())==10


def test_topic_notifications_keep_only_the_newest_push_target(client,push):
    account=enable(client);subscribe(client)
    def topic():
        with api.access.lock,api.connect() as db:api.notifications.deliver(db,[account],'demo',1,'forms.submitted',topic='forms.submitted:one')
    topic();topic()
    with api.connect() as db:
        newest=db.execute('SELECT max(id),count(*) FROM notifications WHERE topic=?',('forms.submitted:one',)).fetchone()
        assert newest[1]==1 and db.execute('SELECT notification_id FROM push_outbox').fetchone()[0]==newest[0]


@pytest.mark.parametrize('endpoint',[
    'http://fcm.googleapis.com/fcm/send/secret',
    'https://127.0.0.1/private','https://169.254.169.254/metadata',
    'https://fcm.googleapis.com.evil.example/x','https://fcm.googleapis.com@evil.example/x',
    'https://user:password@fcm.googleapis.com/x','https://fcm.googleapis.com:8443/x',
    'https://fcm.googleapis.com/x#secret','https://fcm.googleapis.com/x\nsecret',
    'https://fcm.googleapis.com./x','https://fcm.googleapis.com\\evil.example/x',
])
def test_endpoints_cannot_be_used_for_arbitrary_requests_or_leak_validation_values(client,push,endpoint):
    enable(client);data=body(client);data['endpoint']=endpoint
    result=client.post(BASE+'/devices',json=data)
    assert result.status_code==422
    assert endpoint not in result.text and 'input' not in result.text


def test_bad_crypto_keys_expiry_and_server_key_rotation_are_rejected(client,push):
    enable(client);data=body(client)
    for keys in [dict(auth='a'*22,p256dh='b'*87),data['keys']|dict(auth='c'*24)]:
        response=client.post(BASE+'/devices',json=data|dict(keys=keys))
        assert response.status_code==422 and all(value not in response.text for value in keys.values())
    assert client.post(BASE+'/devices',json=data|dict(expirationTime=1000)).status_code==422
    assert client.post(BASE+'/devices',json=data|dict(publicKey='a'*87)).status_code==409
    assert client.post(BASE+'/devices',json=data,headers={'Origin':'https://evil.example'}).status_code==403
    assert client.post(BASE+'/devices',json=data,headers={'X-Atlas-Request':'0'}).status_code==403


def test_claim_is_exclusive_and_expired_worker_cannot_publish(client,push):
    account=enable(client);subscribe(client);notify(account)
    service,clock,calls=push;clock[0]+=3
    with ThreadPoolExecutor(max_workers=2) as pool:jobs=list(pool.map(lambda _:service.claim(),range(2)))
    assert sum(job is not None for job in jobs)==1
    first=next(job for job in jobs if job)
    clock[0]+=web_push.LEASE+1
    restarted=web_push.PushService(api.access,clock=lambda:clock[0],send=service.send)
    second=restarted.claim();assert second and second['token']!=first['token']
    assert service.prepare(first) is None
    service.finish(first,201)
    assert queue()[0][1]=='sending'
    restarted.finish(second,201)
    assert queue()[0][1]=='sent'


def test_new_event_while_sending_is_not_lost_and_does_not_hold_database_lock(client,push):
    account=enable(client);subscribe(client);notify(account)
    service,clock,calls=push;clock[0]+=3
    def send(*args):
        notify(account)
        return 201,None
    service.send=send
    assert service.process_one() and queue()[0][1]=='queued'
    service.send=lambda *args:(201,None)
    clock[0]+=3
    assert service.process_one() and queue()[0][1]=='sent'


@pytest.mark.parametrize('status',[0,408,429,503])
def test_transient_failures_retry_with_backoff_and_stop_after_six(client,push,status):
    account=enable(client);subscribe(client);notify(account)
    service,clock,calls=push;clock[0]+=3
    service.send=lambda *args:(status,'120')
    for attempt in range(1,7):
        assert service.process_one()
        row=queue()[0]
        assert row[2]==attempt
        assert row[1]==('queued' if attempt<6 else 'failed')
        assert row[3]>=clock[0]+120
        assert not service.process_one()
        clock[0]=row[3]+1
    assert not service.process_one()


@pytest.mark.parametrize('status',[301,400,401,403,404,410])
def test_permanent_failures_and_gone_subscription_are_not_retried(client,push,status):
    account=enable(client);device,_=subscribe(client);notify(account)
    service,clock,calls=push;clock[0]+=3;service.send=lambda *args:(status,None)
    assert service.process_one() and queue()[0][1]=='failed'
    assert not service.process_one()
    current=client.get(BASE+'/options').json()['devices'][0]
    assert current['active']==(status not in [404,410])


def test_retry_after_http_date_and_new_events_keep_backoff(client,push):
    from email.utils import format_datetime
    account=enable(client);subscribe(client);notify(account)
    service,clock,calls=push;clock[0]+=3
    after=format_datetime(datetime.fromtimestamp(clock[0]+900,timezone.utc),usegmt=True)
    service.send=lambda *args:(429,after)
    service.process_one();due=queue()[0][3]
    notify(account)
    assert queue()[0][3]==due and queue()[0][2]==1
    assert due>=clock[0]+899 and not service.process_one()


def test_test_notification_is_owner_only_throttled_and_does_not_reach_other_devices(client,push):
    enable(client);first,_=subscribe(client);second,_=subscribe(client)
    response=client.post(BASE+'/devices/'+first['id']+'/test',json=dict(version=first['version']))
    assert response.status_code==202
    assert len(queue())==1 and queue()[0][0]==first['id']
    assert client.post(BASE+'/devices/'+first['id']+'/test',json=dict(version=first['version'])).status_code==429
    summary=client.get('/api/access/push/status').json()
    assert summary['devices']==2 and summary['queue']=={'queued':1}


def test_test_notifications_respect_inbox_retention(client,push):
    account=enable(client);device,_=subscribe(client)
    with api.connect() as db:
        db.executemany('INSERT INTO notifications (user_id,permissions,workspace,revision,created,action) VALUES (?,?,?,?,?,?)',
            [(account['id'],permission_key(account),'demo',0,now().isoformat(),'message.received')]*500)
    client.post(BASE+'/devices/'+device['id']+'/test',json=dict(version=device['version'])).raise_for_status()
    with api.connect() as db:
        assert db.execute('SELECT count(*) FROM notifications WHERE user_id=?',(account['id'],)).fetchone()[0]==500


def test_workers_start_once_deliver_and_stop_without_logging_provider_secrets(client,push,caplog):
    import threading
    account=enable(client);subscribe(client);notify(account)
    service,clock,calls=push;clock[0]+=3
    received=threading.Event()
    def send(*args):
        received.set()
        raise ValueError('PRIVATE_ENDPOINT_AND_KEY')
    service.send=send
    try:
        service.start();threads=list(service.threads);service.start()
        assert len(threads)==4 and service.threads==threads and received.wait(3)
    finally:service.shutdown()
    assert not service.threads and all(not t.is_alive() for t in threads)
    assert 'PRIVATE_ENDPOINT_AND_KEY' not in caplog.text
    assert queue()[0][1]=='queued' and queue()[0][4]=='network'


def test_revocation_during_network_io_cannot_reactivate_device(client,push):
    account=enable(client);device,_=subscribe(client);notify(account)
    service,clock,calls=push;clock[0]+=3
    def send(*args):
        client.post('/api/auth/logout').raise_for_status()
        return 201,None
    service.send=send
    assert service.process_one()
    assert queue()[0][1]=='cancelled'
    assert signed('admin').get(BASE+'/options').json()['devices'][0]['active'] is False


def test_new_generation_survives_reading_old_notice_after_claim(client,push):
    account=enable(client);subscribe(client);notify(account)
    service,clock,calls=push;clock[0]+=3
    job=service.claim()
    with api.connect() as db:db.execute('UPDATE notifications SET read_at=?',(now().isoformat(),))
    notify(account)
    assert service.prepare(job) is None
    service.finish(job,None)
    assert queue()[0][1]=='queued'
    assert service.process_one() and len(calls)==1 and queue()[0][1]=='sent'


def test_transport_encrypts_generic_payload_and_refuses_redirects_without_real_network(client,push,monkeypatch):
    import requests
    from http_ece import decrypt
    enable(client)
    data=body(client)
    receiver=ec.generate_private_key(ec.SECP256R1())
    data['keys']['p256dh']=web_push.b64(receiver.public_key().public_bytes(Encoding.X962,PublicFormat.UncompressedPoint))
    device,data=subscribe(client,data)
    with api.connect() as db:private=db.execute('SELECT private_key FROM push_config').fetchone()[0]
    calls=[]
    def send(session,request,**kwargs):
        calls.append((request,kwargs,session.trust_env))
        response=requests.Response();response.status_code=307;response.headers['Location']='http://127.0.0.1/private'
        response._content=b'not read';response._content_consumed=True;return response
    monkeypatch.setattr(requests.Session,'send',send)
    status,after=web_push.transport({k:data[k] for k in ['endpoint','keys']},private,'mailto:admin@example.org')
    assert status==307 and len(calls)==1
    request,kwargs,trust_env=calls[0]
    assert kwargs['allow_redirects'] is False and kwargs['stream'] is True and kwargs['timeout']==(3,8) and trust_env is False
    assert request.headers['Content-Encoding']=='aes128gcm' and request.headers['TTL']=='300'
    assert 'vapid ' in request.headers['Authorization']
    plain=decrypt(request.body,private_key=receiver,auth_secret=web_push.decode(data['keys']['auth']),version='aes128gcm')
    assert plain.decode()==web_push.PAYLOAD
    web_push.transport({k:data[k] for k in ['endpoint','keys']},private,'mailto:admin@example.org',web_push.push_payload('grades'))
    plain=decrypt(calls[1][0].body,private_key=receiver,auth_secret=web_push.decode(data['keys']['auth']),version='aes128gcm')
    assert json.loads(plain)=={'type':'atlas-update','category':'grades'}


def test_config_subscription_and_queue_survive_verified_backup(client,push,tmp_path,monkeypatch):
    from tools.backup_school import create_backup
    account=enable(client);device,_=subscribe(client);notify(account)
    public=client.get(BASE+'/options').json()['publicKey']
    target=tmp_path/'backup';manifest=create_backup(api.DB,target)
    monkeypatch.setattr(api,'DB',target/manifest['database']['path'])
    assert client.get(BASE+'/options').json()['publicKey']==public
    service,clock,calls=push;clock[0]+=3
    assert service.process_one() and queue()[0][1]=='sent' and len(calls)==1


def quiet_around(user,clock):
    """Quiet hours that began an hour ago and end within the next hour of school time."""
    local=datetime.fromtimestamp(clock,ZoneInfo('Asia/Qyzylorda'))
    user.put('/api/notifications/preferences',json=dict(quietStart=(local-timedelta(hours=1)).strftime('%H:%M'),quietEnd=(local+timedelta(hours=1)).strftime('%H:%M'))).raise_for_status()
    return local.replace(second=0,microsecond=0)+timedelta(hours=1)


def test_quiet_hours_defer_push_without_losing_it(client,push):
    account=enable(client);subscribe(client)
    service,clock,calls=push
    end=quiet_around(client,clock[0]).timestamp()
    notify(account)
    # The inbox entry is there at once; only the push waits for the end of the quiet hours.
    assert client.get('/api/notifications/inbox').json()['unread']==1
    assert queue()[0][1]=='queued' and queue()[0][3]>=end
    clock[0]+=3
    assert not service.process_one() and not calls
    notify(account)
    assert queue()[0][3]>=end and queue()[0][5]==2
    clock[0]=end+3
    assert service.process_one() and len(calls)==1 and queue()[0][1]=='sent'


def test_quiet_hours_set_after_enqueue_defer_at_send_time(client,push):
    account=enable(client);subscribe(client);notify(account)
    service,clock,calls=push;clock[0]+=3
    end=quiet_around(client,clock[0]).timestamp()
    job=service.claim()
    prepared=service.prepare(job)
    assert prepared=={'defer':end}
    service.defer(job,prepared['defer'])
    row=queue()[0]
    assert row[1]=='queued' and row[3]==end and row[2]==0
    assert not service.process_one() and not calls
    clock[0]=end+3
    assert service.process_one() and len(calls)==1


def test_test_push_ignores_muting_and_quiet_hours(client,push):
    enable(client);device,_=subscribe(client)
    client.put('/api/notifications/preferences',json=dict(enabled=False)).raise_for_status()
    service,clock,calls=push
    quiet_around(client,clock[0])
    assert client.post(BASE+'/devices/'+device['id']+'/test',json=dict(version=device['version'])).status_code==202
    clock[0]+=3
    assert service.process_one() and len(calls)==1
    assert json.loads(calls[0][3])=={'type':'atlas-update','category':'system'}


def test_payload_carries_only_category(client,push):
    family(client)
    parent=signed('parent');subscribe(parent)
    service,clock,calls=push
    published(client)
    clock[0]+=3
    assert service.process_one() and len(calls)==1
    payload=calls[-1][3]
    assert set(json.loads(payload))=={'type','category'} and json.loads(payload)['type']=='atlas-update'
    # Newest unread entry of the publication names the category: homework follows the grade.
    assert json.loads(payload)['category']=='homework'
    for private in ['Английский','обратная связь','s1','Проверочная','Комментарий']:
        assert private not in payload


def test_push_label_follows_the_newest_unmuted_notice(client,push):
    family(client)
    parent=signed('parent');subscribe(parent)
    parent.put('/api/notifications/preferences',json=dict(muted=['homework'])).raise_for_status()
    service,clock,calls=push
    published(client)
    clock[0]+=3
    assert service.process_one()
    assert json.loads(calls[0][3])['category']=='grades'
