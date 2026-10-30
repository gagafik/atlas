from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from uuid import uuid4

import pytest
from backend import api, message_files
from backend.access import now
from test_access import client, signed
from test_messages import setup, url, payload, detail
from test_message_files_groups import upload, download, group
from seeding import insert_row


def save(user, id, content, version=0, thread=None):
    return user.put(url('drafts/'+id), json=dict(version=version, threadId=thread, content=content))


def get(user, id):
    return user.get(url('drafts/'+id))


def test_private_cross_session_incomplete_drafts_preserve_text_and_school(client):
    people=setup(client);parent=signed('parent');other=signed('other');before=api.read('demo').model_dump()
    id=str(uuid4());content=payload(None)|dict(title='',text='  Несохранённая мысль\n\n')
    assert save(parent,id,content).json()['version']==1
    restored=get(signed('parent'),id).json()
    assert restored['content']['text']==content['text'] and restored['content']['title']==''
    assert restored['version']==1 and restored['threadId'] is None
    for user in [client,other,signed('teacher')]:
        assert get(user,id).status_code==404
        assert user.get(url('drafts')).json()['items']==[]
        assert user.delete(url('drafts/'+id,version=1)).status_code==404
    assert parent.get('/api/messages/drafts/'+id+'?workspace=school').status_code==403
    assert parent.get('/api/notifications/inbox').json()['items']==[]
    assert api.read('demo').model_dump()==before


def test_two_devices_cannot_overwrite_delete_or_resurrect(client):
    people=setup(client);one=signed('parent');two=signed('parent');id=str(uuid4());body=payload(people['teacher']['id'])
    assert save(one,id,body).status_code==200
    # Lost save response can be retried without changing the version.
    assert save(one,id,body).json()['version']==1
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda args:save(args[0],id,body|dict(text=args[1]),1),[(one,'Первое'),(two,'Второе')]))
    assert sorted(r.status_code for r in results)==[200,409]
    assert get(one,id).json()['version']==2
    assert one.delete(url('drafts/'+id,version=1)).status_code==409
    assert one.delete(url('drafts/'+id,version=2)).status_code==200
    assert one.delete(url('drafts/'+id,version=2)).status_code==200
    assert get(one,id).status_code==404
    assert save(two,id,body,2).status_code==409
    assert save(two,id,body,0).status_code==409


def test_send_consumes_version_atomically_and_deduplicates(client,monkeypatch):
    people=setup(client);parent=signed('parent');id=str(uuid4())
    body=payload(people['teacher']['id'])|dict(draftId=id,draftVersion=1)
    assert save(parent,id,body).status_code==200
    assert parent.post(url(),json=body|dict(draftVersion=None)).status_code==409
    with monkeypatch.context() as patch:
        def fail(*args,**kwargs):raise RuntimeError('notification rollback')
        patch.setattr(api.notifications,'deliver',fail)
        with pytest.raises(RuntimeError,match='notification rollback'):parent.post(url(),json=body)
    assert get(parent,id).json()['version']==1
    with api.connect() as db:assert db.execute('SELECT count(*) FROM message_posts').fetchone()[0]==0
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda _:parent.post(url(),json=body),range(2)))
    assert all(r.status_code==200 for r in results) and results[0].json()==results[1].json()
    assert get(parent,id).status_code==404
    assert parent.get(url('drafts')).json()['items']==[]
    assert parent.post(url(),json=body|dict(requestId=str(uuid4()))).status_code==409
    assert save(parent,id,body,1).status_code==409
    assert len(detail(signed('teacher'),results[0].json()['id'])['posts'])==1


def test_send_racing_save_never_discards_newer_text(client):
    people=setup(client);parent=signed('parent');id=str(uuid4())
    body=payload(people['teacher']['id'])|dict(draftId=id,draftVersion=1)
    save(parent,id,body)
    with ThreadPoolExecutor(max_workers=2) as pool:
        sent=pool.submit(parent.post,url(),json=body)
        saved=pool.submit(save,signed('parent'),id,body|dict(text='New device text'),1)
        a,b=sent.result(),saved.result()
    assert sorted([a.status_code,b.status_code])==[200,409]
    if b.status_code==200:assert get(parent,id).json()['content']['text']=='New device text'
    else:assert get(parent,id).status_code==404


def test_saved_files_survive_cleanup_restore_and_publication(client,tmp_path,monkeypatch):
    from tools.backup_school import create_backup
    people=setup(client);parent=signed('parent');id=str(uuid4())
    file=upload(parent,id).json();orphan=upload(parent,str(uuid4())).json()
    body=payload(people['teacher']['id'])|dict(draftId=id,fileIds=[file['id']],draftVersion=1)
    assert save(parent,id,body).status_code==200
    with api.connect() as db:
        db.execute('UPDATE message_files SET created=?',((now()-timedelta(days=3)).isoformat(),))
        message_files.collect_unused(db)
    assert download(parent,file['id']).status_code==200
    assert download(parent,orphan['id']).status_code==404
    assert parent.delete(url('files/'+file['id'])).status_code==409
    assert len(get(parent,id).json()['files'])==1
    source=api.DB;target=tmp_path/'draft-backup'
    create_backup(source,target)
    monkeypatch.setattr(api,'DB',target/source.name)
    assert get(parent,id).json()['content']['fileIds']==[file['id']]
    assert download(parent,file['id']).content==b'Private attachment'
    assert download(signed('teacher'),file['id']).status_code==404
    sent=parent.post(url(),json=body);assert sent.status_code==200,sent.text
    with api.connect() as db:message_files.collect_unused(db)
    assert download(signed('teacher'),file['id']).status_code==200


def test_draft_files_cannot_cross_account_form_or_reply_target(client):
    people=setup(client);parent=signed('parent');teacher=signed('teacher');id=str(uuid4())
    file=upload(parent,id).json()['id'];body=payload(people['teacher']['id'])|dict(fileIds=[file])
    assert save(teacher,id,body).status_code==422
    assert save(parent,str(uuid4()),body).status_code==422
    assert save(parent,id,body|dict(fileIds=[file,file])).status_code==422
    thread=parent.post(url(),json=payload(people['teacher']['id'])).json()['id']
    assert save(parent,id,body,thread=thread).status_code==422
    assert save(parent,id,body).status_code==200
    assert save(parent,id,body|dict(fileIds=[]),1).status_code==200
    assert parent.delete(url('files/'+file)).status_code==200


def test_reply_drafts_closed_group_revocation_and_send(client):
    people=setup(client);teacher=signed('teacher');parent=signed('parent');thread=group(teacher)
    id=str(uuid4());body=dict(requestId=str(uuid4()),title='Reply',text='Ответ',draftId=id)
    assert save(parent,id,body,thread=thread).status_code==200
    assert teacher.post(url('threads/'+thread+'/change'),json=dict(version=1,action='close')).status_code==200
    assert save(parent,id,body|dict(text='Продолжение'),1,thread).status_code==200
    reply=dict(requestId=str(uuid4()),version=2,text='Продолжение',draftId=id,draftVersion=2)
    assert parent.post(url('threads/'+thread+'/reply'),json=reply).status_code==409
    assert get(parent,id).json()['version']==2
    teacher.post(url('threads/'+thread+'/change'),json=dict(version=2,action='reopen'))
    assert parent.post(url('threads/'+thread+'/reply'),json=reply|dict(version=3)).status_code==200
    assert get(parent,id).status_code==404
    second=str(uuid4());save(parent,second,body,thread=thread)
    # A reply does not change the conversation version: the reopened thread is still at version 3.
    teacher.post(url('threads/'+thread+'/participants'),json=dict(version=3,userId=people['parent']['id'],action='remove'))
    assert save(parent,second,body,1,thread).status_code==404
    assert parent.post(url('threads/'+thread+'/reply'),json=reply|dict(requestId=str(uuid4()),version=4,draftId=second,draftVersion=1)).status_code==404
    # Private unsent words remain the author's; no thread content is returned.
    assert get(parent,second).json()['content']['text']=='Ответ'
    assert parent.delete(url('drafts/'+second,version=1)).status_code==200


def test_quota_and_current_recipient_relationship_checked_on_send(client,monkeypatch):
    from backend import message_drafts
    people=setup(client);parent=signed('parent');id=str(uuid4());body=payload(people['teacher']['id'])
    monkeypatch.setattr(message_drafts,'MAX_DRAFTS',2)
    assert save(parent,id,body).status_code==200
    assert save(parent,str(uuid4()),body).status_code==200
    assert save(parent,str(uuid4()),body).status_code==413
    state=api.read('demo')
    for w in state.workloads:
        if w.teacherId=='t2':w.teacherId='t1'
        w.additionalTeacherIds=[t for t in w.additionalTeacherIds if t!='t2']
    api.save('demo',state)
    restored=get(parent,id).json();assert restored['people']==[]
    assert parent.post(url(),json=body|dict(draftId=id,draftVersion=1)).status_code==403
    assert get(parent,id).json()['version']==1


@pytest.mark.parametrize('kind',['direct','group','announcement'])
def test_all_message_kinds_restore_audience_and_send(client,kind):
    people=setup(client);teacher=signed('teacher');id=str(uuid4())
    body=dict(requestId=str(uuid4()),kind=kind,title='Учебная тема',text='Материал',draftId=id,draftVersion=1)
    if kind=='direct':body.update(recipientId=people['parent']['id'],studentId='s1')
    else:body.update(groupIds=['g-a'],roles=['parent','student'],requireAck=kind=='announcement')
    assert save(teacher,id,body|dict(groupMode='audience' if kind!='direct' else 'contacts')).status_code==200
    restored=get(signed('teacher'),id).json()['content']
    for key in ['kind','title','text','groupIds','roles','requireAck','recipientId','studentId']:
        if key in body:assert restored[key]==body[key]
    restored.pop('groupMode')
    restored['draftVersion']=1
    sent=teacher.post(url(),json=restored);assert sent.status_code==200,sent.text
    assert detail(signed('parent'),sent.json()['id'])['kind']==kind


def test_pre_draft_release_request_hashes_still_replay(client):
    import hashlib,json
    # Rows stored before drafts existed hash these exact bodies; a retry sent after the upgrade must still match them.
    digest=lambda value:hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    people=setup(client);parent=signed('parent');teacher=signed('teacher')
    thread=parent.post(url(),json=payload(people['teacher']['id'])).json()['id']
    created=dict(requestId='11111111-1111-4111-8111-111111111111',kind='direct',title='Вопрос до обновления',text='Отправлено раньше',recipientId=people['teacher']['id'],recipientIds=[],studentId='s1',groupIds=[],roles=[],schoolwide=False,requireAck=False,draftId=None,fileIds=[])
    answer=dict(requestId='22222222-2222-4222-8222-222222222222',version=1,text='Ответ до обновления',draftId=None,fileIds=[])
    with api.connect() as db:
        insert_row(db,'message_requests',user_id=people['parent']['id'],request_id=created['requestId'],body_hash=digest(dict(workspace='demo',body=created)),thread_id=thread)
        insert_row(db,'message_requests',user_id=people['teacher']['id'],request_id=answer['requestId'],body_hash=digest(dict(workspace='demo',id=thread,body=answer)),thread_id=thread)
    assert parent.post(url(),json=created).json()['id']==thread
    assert parent.post(url(),json=created|dict(text='Другой текст')).status_code==409
    reply=url('threads/'+thread+'/reply')
    assert teacher.post(reply,json=answer).json()['id']==thread
    assert teacher.post(reply,json=answer|dict(text='Другой ответ')).status_code==409
    # Replays store nothing new: one conversation with its single original message.
    assert [t['id'] for t in parent.get(url()).json()['items']]==[thread] and len(detail(teacher,thread)['posts'])==1
