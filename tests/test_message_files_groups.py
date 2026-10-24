from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from uuid import uuid4

import pytest
from fastapi import HTTPException
from backend import api,homework_files,message_files
from backend.access import now
from test_access import client,signed,create
from test_messages import setup,url,payload,detail


def upload(client,draft,thread=None,body=b'Private attachment',filename='answer.txt',**headers):
    params=dict(draftId=draft,filename=filename)
    if thread:params['threadId']=thread
    return client.post(url('files',**params),content=body,headers=headers)


def download(client,id):return client.get(url('files/'+id))


def group(teacher):
    result=teacher.post(url(),json=dict(requestId=str(uuid4()),kind='group',title='Обсуждение класса',text='Общий вопрос.',groupIds=['g-a'],roles=['parent','student']))
    assert result.status_code==200,result.text
    return result.json()['id']


def test_attachments_private_then_shared_and_not_reusable(client):
    people=setup(client);parent=signed('parent');teacher=signed('teacher');other=signed('other');draft=str(uuid4())
    file=upload(parent,draft).json();id=file['id']
    assert download(parent,id).content==b'Private attachment'
    assert download(teacher,id).status_code==404 and download(other,id).status_code==404
    assert teacher.get(url('files',draftId=draft)).json()['items']==[]
    body=payload(people['teacher']['id'])|dict(text='',draftId=draft,fileIds=[id])
    sent=parent.post(url(),json=body);assert sent.status_code==200,sent.text
    thread=sent.json()['id'];value=detail(teacher,thread)
    assert value['posts'][0]['files'][0]['id']==id
    downloaded=download(teacher,id);assert downloaded.content==b'Private attachment'
    assert downloaded.headers['content-type']=='application/octet-stream'
    assert downloaded.headers['x-content-type-options']=='nosniff'
    assert downloaded.headers['content-disposition'].startswith('attachment;')
    assert download(other,id).status_code==404
    assert parent.delete(url('files/'+id)).status_code==409
    assert parent.post(url(),json=body).json()['id']==thread
    assert parent.post(url(),json=body|dict(requestId=str(uuid4()))).status_code==422
    assert parent.post(url('threads/'+thread+'/reply'),json=dict(requestId=str(uuid4()),version=1,text='',draftId=draft,fileIds=[id])).status_code==422


def test_attachment_binding_owner_draft_workspace_and_target(client):
    people=setup(client);parent=signed('parent');other=signed('other');draft=str(uuid4())
    id=upload(parent,draft).json()['id']
    body=payload(people['teacher']['id'])|dict(draftId=draft,fileIds=[id])
    assert other.post(url(),json=body|dict(studentId='s2')).status_code==422
    assert parent.post(url(),json=body|dict(draftId=str(uuid4()))).status_code==422
    assert parent.post(url(),json=body|dict(fileIds=[id,id])).status_code==422
    thread=parent.post(url(),json=payload(people['teacher']['id'])).json()['id']
    assert parent.post(url('threads/'+thread+'/reply'),json=dict(requestId=str(uuid4()),version=1,text='x',draftId=draft,fileIds=[id])).status_code==422
    assert other.delete(url('files/'+id)).status_code==404
    assert parent.get('/api/messages/files/'+id+'?workspace=school').status_code==403
    assert parent.delete(url('files/'+id)).status_code==200
    assert download(parent,id).status_code==404


def test_group_class_replies_members_and_removal_revoke_file_access(client):
    people=setup(client);teacher=signed('teacher');parent=signed('parent');other=signed('other')
    id=group(teacher);before=api.read('demo').model_dump()
    first=detail(parent,id);assert first['kind']=='group' and first['canReply']
    assert len(first['participants'])==3
    assert client.get(url('threads/'+id)).status_code==404
    draft=str(uuid4());file=upload(teacher,draft,id).json()['id']
    assert teacher.post(url('threads/'+id+'/reply'),json=dict(requestId=str(uuid4()),version=1,text='Материал',draftId=draft,fileIds=[file])).status_code==200
    assert download(parent,file).status_code==200
    current=detail(teacher,id)
    assert parent.post(url('threads/'+id+'/participants'),json=dict(version=current['version'],userId=people['other']['id'],action='remove')).status_code==403
    assert teacher.post(url('threads/'+id+'/participants'),json=dict(version=current['version'],userId=people['parent']['id'],action='remove')).status_code==200
    assert parent.get(url('threads/'+id)).status_code==404 and download(parent,file).status_code==404
    assert parent.get(url()).json()['items']==[]
    assert download(other,file).status_code==200
    current=detail(other,id)
    assert other.post(url('threads/'+id+'/participants'),json=dict(version=current['version'],userId=people['other']['id'],action='leave')).status_code==200
    assert other.get(url('threads/'+id)).status_code==404
    assert api.read('demo').model_dump()==before


def test_group_contacts_scope_and_assignment_change(client):
    people=setup(client);parent=signed('parent');teacher=signed('teacher')
    admin=client.get('/api/auth/status').json()['user']
    body=dict(requestId=str(uuid4()),kind='group',title='Вопрос семье',text='Обсудим.',recipientIds=[people['teacher']['id'],admin['id']],studentId='s1')
    sent=parent.post(url(),json=body);assert sent.status_code==200,sent.text
    id=sent.json()['id'];assert len(detail(teacher,id)['participants'])==2
    assert parent.post(url(),json=body|dict(requestId=str(uuid4()),recipientIds=[people['other']['id'],admin['id']])).status_code==403
    assert parent.post(url(),json=body|dict(requestId=str(uuid4()),recipientIds=[admin['id'],admin['id']])).status_code==422
    state=api.read('demo')
    for w in state.workloads:
        if w.teacherId=='t2':w.teacherId='t1'
    api.save('demo',state)
    assert teacher.get(url('threads/'+id)).status_code==404
    assert detail(client,id)['canReply']


def test_upload_interrupted_by_group_removal_and_closed_thread(client,monkeypatch):
    people=setup(client);teacher=signed('teacher');parent=signed('parent');id=group(teacher)
    original=homework_files.validate_file
    def remove(path,name):
        assert teacher.post(url('threads/'+id+'/participants'),json=dict(version=1,userId=people['parent']['id'],action='remove')).status_code==200
        return original(path,name)
    monkeypatch.setattr(homework_files,'validate_file',remove)
    assert upload(parent,str(uuid4()),id).status_code==404
    with api.connect() as db:
        assert db.execute('SELECT count(*) FROM message_files').fetchone()[0]==0
        assert not list(message_files.root_for(db).glob('*.bin'))
    monkeypatch.setattr(homework_files,'validate_file',original)
    value=detail(teacher,id)
    assert teacher.post(url('threads/'+id+'/change'),json=dict(version=value['version'],action='close')).status_code==200
    assert upload(teacher,str(uuid4()),id).status_code==409


def test_announcement_file_hidden_on_withdraw_and_backup_restores_group(client,tmp_path,monkeypatch):
    from tools.backup_school import create_backup
    people=setup(client);teacher=signed('teacher');parent=signed('parent');draft=str(uuid4())
    file=upload(teacher,draft,filename='../папка/материал.txt').json()
    assert file['filename']=='материал.txt'
    body=dict(requestId=str(uuid4()),kind='announcement',title='Материал',text='',roles=['parent'],groupIds=['g-a'],draftId=draft,fileIds=[file['id']])
    id=teacher.post(url(),json=body).json()['id']
    assert download(parent,file['id']).status_code==200
    source=api.DB;destination=tmp_path/'group-files-backup'
    report=create_backup(source,destination);assert len(report['files'])==1 and '/messages/' in report['files'][0]['path']
    monkeypatch.setattr(api,'DB',destination/source.name)
    assert download(parent,file['id']).content==b'Private attachment'
    assert teacher.post(url('threads/'+id+'/change'),json=dict(version=1,action='withdraw')).status_code==200
    assert download(parent,file['id']).status_code==404
    assert download(teacher,file['id']).status_code==200


def test_shared_quota_invalid_uploads_and_gc(client,monkeypatch):
    people=setup(client);parent=signed('parent');draft=str(uuid4())
    for name,body in [('a.exe',b'bad'),('a.png',b'fake'),('a.txt',b'')]:
        assert upload(parent,draft,body=body,filename=name).status_code==422
    assert upload(parent,draft,body=b'x',**{'Content-Length':str(homework_files.MAX_FILE+1)}).status_code==413
    monkeypatch.setattr(homework_files,'MAX_FILE',8)
    assert upload(parent,draft,body=b'123456789',**{'Content-Length':'1'}).status_code==413
    file=upload(parent,draft,body=b'abc').json()['id']
    monkeypatch.setattr(homework_files,'total_limit',lambda:8)
    assert upload(parent,draft,body=b'abc').status_code==413
    with api.connect() as db:
        homework_files.tables(db)
        with pytest.raises(HTTPException) as exc:homework_files.reserve(db,'demo','test','s1',people['parent']['id'],'a.txt')
        assert exc.value.status_code==413
        db.execute('UPDATE message_files SET created=?',((now()-timedelta(days=2)).isoformat(),))
        message_files.collect_unused(db)
        assert db.execute('SELECT count(*) FROM message_files').fetchone()[0]==0
        assert not (message_files.root_for(db)/(file+'.bin')).exists()


def test_group_concurrent_removal_and_reply_are_atomic(client):
    people=setup(client);teacher=signed('teacher');parent=signed('parent');id=group(teacher)
    draft=str(uuid4());file=upload(parent,draft,id).json()['id']
    with ThreadPoolExecutor(max_workers=2) as pool:
        remove=pool.submit(teacher.post,url('threads/'+id+'/participants'),json=dict(version=1,userId=people['parent']['id'],action='remove'))
        reply=pool.submit(parent.post,url('threads/'+id+'/reply'),json=dict(requestId=str(uuid4()),version=1,text='Concurrent reply',draftId=draft,fileIds=[file]))
        codes=[remove.result().status_code,reply.result().status_code]
    assert codes in ([200,404],[409,200]),codes
    value=detail(teacher,id);assert value['version']==2 and len(value['posts'])==2
    with api.connect() as db:
        linked=db.execute('SELECT post_id FROM message_files WHERE id=?',(file,)).fetchone()[0]
        assert (linked is not None)==(codes[1]==200)


def test_group_scope_changes_and_revocation_after_post_limit(client):
    people=setup(client);teacher=signed('teacher');parent=signed('parent');id=group(teacher)
    with api.connect() as db:
        for i in range(30):
            db.execute('INSERT INTO message_posts (thread_id,author_id,author_name,created,body) VALUES (?,?,?,?,?)',(id,people['teacher']['id'],'Teacher',now().isoformat(),'Rate fixture'))
    assert teacher.post(url('threads/'+id+'/reply'),json=dict(requestId=str(uuid4()),version=1,text='Over limit')).status_code==429
    assert teacher.post(url('threads/'+id+'/participants'),json=dict(version=1,userId=people['other']['id'],action='remove')).status_code==200
    state=api.read('demo')
    for workload in state.workloads:
        if workload.teacherId=='t2':workload.teacherId='t1'
    api.save('demo',state)
    assert teacher.get(url('threads/'+id)).status_code==404
    assert parent.get(url('threads/'+id)).status_code==404


def test_published_attachments_survive_orphan_cleanup(client):
    people=setup(client);parent=signed('parent');draft=str(uuid4())
    file=upload(parent,draft).json()['id']
    result=parent.post(url(),json=payload(people['teacher']['id'])|dict(draftId=draft,fileIds=[file]))
    assert result.status_code==200,result.text
    with api.connect() as db:
        db.execute('UPDATE message_files SET created=?',((now()-timedelta(days=3)).isoformat(),))
        message_files.collect_unused(db)
        assert db.execute('SELECT count(*) FROM message_files').fetchone()[0]==1
    assert download(signed('teacher'),file).content==b'Private attachment'


def test_message_quota_is_rolling_and_counts_unsent_files(client,monkeypatch):
    import json
    people=setup(client);parent=signed('parent');teacher=signed('teacher')
    monkeypatch.setattr(homework_files,'MAX_FILE',8);monkeypatch.setattr(message_files,'MAX_OWNER',12)
    first=str(uuid4());shared=upload(parent,first,body=b'abc').json()['id']
    sent=parent.post(url(),json=payload(people['teacher']['id'])|dict(text='',draftId=first,fileIds=[shared]))
    assert sent.status_code==200,sent.text
    second=str(uuid4());kept=upload(parent,second,body=b'abc');assert kept.status_code==200,kept.text
    refused=upload(parent,second,body=b'abc')  # 3 sent + 3 unsent + 8 > 12
    assert refused.status_code==413 and '30 дней' in refused.json()['detail'],refused.text
    old=(now()-timedelta(days=31)).isoformat()
    with api.connect() as db:db.execute('UPDATE message_files SET created=? WHERE post_id IS NOT NULL',(old,))
    again=upload(parent,second,body=b'abc');assert again.status_code==200,again.text  # the sent file left the window: 3 + 8 <= 12
    assert download(teacher,shared).content==b'abc'  # it is only no longer counted, never deleted
    with api.connect() as db:
        from backend.message_drafts import tables as draft_tables
        draft_tables(db)
        db.execute("INSERT INTO message_drafts VALUES (?,?,?,?,?,?,?,?)",('demo',people['parent']['id'],second,1,'active',None,json.dumps(dict(fileIds=[kept.json()['id'],again.json()['id']])),now().isoformat()))
        db.execute('UPDATE message_files SET created=? WHERE post_id IS NULL',(old,))
    blocked=upload(parent,second,body=b'abc')  # unsent files count at any age while a draft keeps them
    assert blocked.status_code==413 and '30 дней' in blocked.json()['detail'],blocked.text
    with api.connect() as db:assert db.execute('SELECT count(*) FROM message_files').fetchone()[0]==3
