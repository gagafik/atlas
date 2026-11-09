from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from uuid import uuid4

import pytest
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


def removed_parent(client):
    """A group where the parent is removed between a post with a file and a later one with another file."""
    people=setup(client);teacher=signed('teacher');parent=signed('parent');id=group(teacher)
    draft=str(uuid4());file=upload(teacher,draft,id).json()['id']
    assert teacher.post(url('threads/'+id+'/reply'),json=dict(requestId=str(uuid4()),text='Материал',draftId=draft,fileIds=[file])).status_code==200
    current=detail(teacher,id);material=current['latestId']
    removed=teacher.post(url('threads/'+id+'/participants'),json=dict(version=current['version'],userId=people['parent']['id'],action='remove'));assert removed.status_code==200,removed.text
    draft2=str(uuid4());file2=upload(teacher,draft2,id).json()['id']
    assert teacher.post(url('threads/'+id+'/reply'),json=dict(requestId=str(uuid4()),text='После исключения',draftId=draft2,fileIds=[file2])).status_code==200
    return people,teacher,parent,id,file,file2,material


def test_only_the_manager_removes_a_group_member(client):
    people=setup(client);teacher=signed('teacher');parent=signed('parent');id=group(teacher)
    first=detail(parent,id);assert first['kind']=='group' and first['canReply']
    assert len(first['participants'])==3
    assert client.get(url('threads/'+id)).status_code==404
    current=detail(teacher,id)
    assert parent.post(url('threads/'+id+'/participants'),json=dict(version=current['version'],userId=people['other']['id'],action='remove')).status_code==403
    assert teacher.post(url('threads/'+id+'/participants'),json=dict(version=current['version'],userId=people['parent']['id'],action='remove')).status_code==200


def test_removed_member_reads_only_what_was_published_before_the_removal(client):
    people,teacher,parent,id,file,file2,material=removed_parent(client)
    value=detail(parent,id);texts=[p['text'] for p in value['posts']]
    assert value['readOnly'] and not value['canReply'] and not value['canManage'] and value['participants']==[]
    assert 'Материал' in texts and 'После исключения' not in texts and value['latestId']==material
    assert download(parent,file).status_code==200 and download(parent,file2).status_code==404
    assert parent.post(url('threads/'+id+'/reply'),json=dict(requestId=str(uuid4()),text='Нельзя')).status_code==404
    assert download(signed('other'),file).status_code==200


def test_removed_member_search_and_list_stop_at_the_removal(client):
    people,teacher,parent,id,file,file2,material=removed_parent(client)
    assert parent.get(url(q='После')).json()['items']==[]
    found=parent.get(url(q='Материал')).json()['items'];assert len(found)==1 and found[0]['readOnly'] and found[0]['latestId']==material


def test_removed_member_can_mark_read_only_up_to_the_removal(client):
    people,teacher,parent,id,file,file2,material=removed_parent(client)
    assert parent.put(url('threads/'+id+'/read'),json=dict(throughId=detail(teacher,id)['latestId'])).status_code==422
    assert parent.get(url(unreadOnly=True)).json()['items'][0]['id']==id
    assert parent.put(url('threads/'+id+'/read'),json=dict(throughId=material)).status_code==200
    assert teacher.post(url('threads/'+id+'/reply'),json=dict(requestId=str(uuid4()),text='И ещё')).status_code==200
    assert parent.get(url(unreadOnly=True)).json()['items']==[]
    assert [i['id'] for i in parent.get(url()).json()['items']]==[id]


def test_member_who_leaves_keeps_reading_the_history(client):
    people=setup(client);teacher=signed('teacher');other=signed('other');id=group(teacher)
    current=detail(other,id)
    assert other.post(url('threads/'+id+'/participants'),json=dict(version=current['version'],userId=people['other']['id'],action='leave')).status_code==200
    value=detail(other,id);assert value['readOnly'] and [p['text'] for p in value['posts']]==['Общий вопрос.']


def test_frozen_thread_ranks_by_last_readable_post(client):
    people=setup(client);teacher=signed('teacher');parent=signed('parent');id=group(teacher)
    assert teacher.post(url('threads/'+id+'/participants'),json=dict(version=1,userId=people['parent']['id'],action='remove')).status_code==200
    direct=teacher.post(url(),json=payload(people['parent']['id'])).json()['id']
    assert teacher.post(url('threads/'+id+'/reply'),json=dict(requestId=str(uuid4()),text='Позже')).status_code==200
    # The group kept its place from before the removal, so later activity does not push it above the new dialogue.
    assert [t['id'] for t in parent.get(url()).json()['items']]==[direct,id]


def test_class_move_freezes_group_but_keeps_direct_dialog(client):
    people=setup(client);teacher=signed('teacher');parent=signed('parent');id=group(teacher)
    direct=parent.post(url(),json=payload(people['teacher']['id'])).json()['id']
    state=api.read('demo')
    for pupil in state.students:
        if pupil.id=='s1':pupil.classId='c-demo-d'
    api.save('demo',state)
    unread=lambda:parent.get('/api/notifications/inbox').json()['unread']
    before=unread()
    for user in [parent,signed('student')]:assert detail(user,id)['readOnly']
    assert detail(parent,direct)['canReply'] and not detail(parent,direct)['readOnly']
    assert teacher.post(url('threads/'+id+'/reply'),json=dict(requestId=str(uuid4()),text='Только для класса')).status_code==200
    assert 'Только для класса' not in [p['text'] for p in detail(parent,id)['posts']] and unread()==before


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
    value=detail(teacher,id);assert value['readOnly'] and not value['canReply']
    assert detail(client,id)['canReply']
    assert client.post(url('threads/'+id+'/reply'),json=dict(requestId=str(uuid4()),text='Новое')).status_code==200
    assert 'Новое' not in [p['text'] for p in detail(teacher,id)['posts']]


def test_upload_interrupted_by_group_removal_leaves_nothing_behind(client,monkeypatch):
    people=setup(client);teacher=signed('teacher');parent=signed('parent');id=group(teacher);draft=str(uuid4())
    original=message_files.storage.validate_file
    def remove(path,name):
        assert teacher.post(url('threads/'+id+'/participants'),json=dict(version=detail(teacher,id)['version'],userId=people['parent']['id'],action='remove')).status_code==200
        return original(path,name)
    monkeypatch.setattr(message_files.storage,'validate_file',remove)
    assert upload(parent,draft,id).status_code==404
    monkeypatch.setattr(message_files.storage,'validate_file',original)
    listed=parent.get(url('files',draftId=draft))
    assert listed.status_code!=200 or listed.json()['items']==[]


def test_upload_into_a_closed_thread_is_refused(client):
    setup(client);teacher=signed('teacher');id=group(teacher)
    assert teacher.post(url('threads/'+id+'/change'),json=dict(version=detail(teacher,id)['version'],action='close')).status_code==200
    assert upload(teacher,str(uuid4()),id).status_code==409


def announcement_with_file(teacher):
    draft=str(uuid4());file=upload(teacher,draft,filename='../папка/материал.txt').json()
    body=dict(requestId=str(uuid4()),kind='announcement',title='Материал',text='',roles=['parent'],groupIds=['g-a'],draftId=draft,fileIds=[file['id']])
    return file,teacher.post(url(),json=body).json()['id']


def test_upload_keeps_only_the_file_name_of_a_path(client):
    setup(client)
    assert upload(signed('teacher'),str(uuid4()),filename='../папка/материал.txt').json()['filename']=='материал.txt'


def test_withdrawn_announcement_hides_its_file_from_recipients_but_not_the_author(client):
    setup(client);teacher=signed('teacher');parent=signed('parent')
    file,id=announcement_with_file(teacher)
    assert download(parent,file['id']).status_code==200
    assert teacher.post(url('threads/'+id+'/change'),json=dict(version=detail(teacher,id)['version'],action='withdraw')).status_code==200
    assert download(parent,file['id']).status_code==404
    assert download(teacher,file['id']).status_code==200


@pytest.mark.parametrize('name,body',[('a.exe',b'bad'),('a.png',b'fake'),('a.txt',b'')])
def test_invalid_uploads_are_refused(client,name,body):
    setup(client)
    assert upload(signed('parent'),str(uuid4()),body=body,filename=name).status_code==422


def test_oversized_uploads_are_refused_even_with_a_false_content_length(client,monkeypatch):
    setup(client);parent=signed('parent');draft=str(uuid4())
    assert upload(parent,draft,body=b'x',**{'Content-Length':str(homework_files.MAX_FILE+1)}).status_code==413
    monkeypatch.setattr(homework_files,'MAX_FILE',8)
    assert upload(parent,draft,body=b'123456789',**{'Content-Length':'1'}).status_code==413


def test_shared_storage_limit_refuses_further_uploads(client,monkeypatch):
    setup(client);parent=signed('parent');draft=str(uuid4())
    monkeypatch.setattr(homework_files,'MAX_FILE',8)
    assert upload(parent,draft,body=b'abc').status_code==200
    monkeypatch.setattr(homework_files,'total_limit',lambda:8)
    assert upload(parent,draft,body=b'abc').status_code==413


def test_next_upload_removes_an_abandoned_old_file(client,monkeypatch):
    setup(client);parent=signed('parent')
    abandoned=upload(parent,str(uuid4())).json()['id']
    later=now()+timedelta(days=2);monkeypatch.setattr(homework_files,'now',lambda:later)
    assert download(parent,abandoned).status_code==200
    assert upload(parent,str(uuid4())).status_code==200
    assert download(parent,abandoned).status_code==404


def test_group_concurrent_removal_and_reply_are_atomic(client):
    people=setup(client);teacher=signed('teacher');parent=signed('parent');id=group(teacher)
    draft=str(uuid4());file=upload(parent,draft,id).json()['id'];before=detail(teacher,id)
    with ThreadPoolExecutor(max_workers=2) as pool:
        remove=pool.submit(teacher.post,url('threads/'+id+'/participants'),json=dict(version=before['version'],userId=people['parent']['id'],action='remove'))
        reply=pool.submit(parent.post,url('threads/'+id+'/reply'),json=dict(requestId=str(uuid4()),version=before['version'],text='Concurrent reply',draftId=draft,fileIds=[file]))
        codes=[remove.result().status_code,reply.result().status_code]
    assert codes in ([200,404],[200,200]),codes
    # The removal posts its own notice; the reply is stored only if it won the race.
    value=detail(teacher,id);assert value['version']>before['version'] and len(value['posts'])==len(before['posts'])+1+(codes[1]==200)
    attached=[f['id'] for post in value['posts'] for f in post.get('files',[])]
    assert (file in attached)==(codes[1]==200) and (download(teacher,file).status_code==200)==(codes[1]==200)


def test_removing_a_member_is_not_blocked_by_the_post_limit(client,monkeypatch):
    fixed=now();monkeypatch.setattr('backend.messages.now',lambda:fixed)
    people=setup(client);teacher=signed('teacher');id=group(teacher);codes=[]
    for i in range(300):
        codes.append(teacher.post(url('threads/'+id+'/reply'),json=dict(requestId=str(uuid4()),text=f'Flood {i}')).status_code)
        if codes[-1]==429:break
    assert codes[0]==200 and codes[-1]==429
    assert teacher.post(url('threads/'+id+'/participants'),json=dict(version=detail(teacher,id)['version'],userId=people['other']['id'],action='remove')).status_code==200
    assert detail(signed('other'),id)['readOnly']


def test_message_quota_is_rolling_and_counts_unsent_files(client,monkeypatch):
    people=setup(client);parent=signed('parent');teacher=signed('teacher')
    monkeypatch.setattr(homework_files,'MAX_FILE',8);monkeypatch.setattr(message_files,'MAX_OWNER',12)
    first=str(uuid4());shared=upload(parent,first,body=b'abc').json()['id']
    sent=parent.post(url(),json=payload(people['teacher']['id'])|dict(text='',draftId=first,fileIds=[shared]))
    assert sent.status_code==200,sent.text
    second=str(uuid4());kept=upload(parent,second,body=b'abc');assert kept.status_code==200,kept.text
    assert upload(parent,second,body=b'abc').status_code==413  # 3 sent + 3 unsent + the largest file allowed > the quota
    start=now();past=lambda days:start+timedelta(days=days)
    monkeypatch.setattr(message_files,'now',lambda:past(message_files.QUOTA_DAYS+1))
    again=upload(parent,second,body=b'abc');assert again.status_code==200,again.text  # the sent file left the window
    assert download(teacher,shared).content==b'abc'  # it is only no longer counted, never deleted
    draft=parent.put(url('drafts/'+second),json=dict(version=0,threadId=None,content=payload(people['teacher']['id'])|dict(fileIds=[kept.json()['id'],again.json()['id']])))
    assert draft.status_code==200,draft.text
    monkeypatch.setattr(homework_files,'now',lambda:past(message_files.QUOTA_DAYS+1))
    assert upload(parent,second,body=b'abc').status_code==413  # unsent files count at any age while a draft keeps them
    assert download(parent,kept.json()['id']).status_code==200 and download(parent,again.json()['id']).status_code==200


HEIC=b'\x00\x00\x00\x18ftypheic'+b'\x00'*16
OLE=bytes.fromhex('d0cf11e0a1b11ae1')+b'\x00'*32
JPEG=b'\xff\xd8\xff\xe0'+b'\x00'*16


def sent(user,teacher_id,files):
    """Upload (name, bytes) pairs as one draft and send them to the teacher; returns thread id and file ids."""
    draft=str(uuid4());ids=[]
    for name,body in files:
        uploaded=upload(user,draft,filename=name,body=body);assert uploaded.status_code==200,(name,uploaded.text);ids.append(uploaded.json()['id'])
    created=user.post(url(),json=payload(teacher_id)|dict(text='',draftId=draft,fileIds=ids));assert created.status_code==200,created.text
    return created.json()['id'],ids


def test_message_attachments_accept_phone_photos_and_old_office(client):
    people=setup(client);parent=signed('parent');teacher=signed('teacher');draft=str(uuid4())
    thread,ids=sent(parent,people['teacher']['id'],[('photo.HEIC',HEIC),('letter.doc',OLE),('table.xls',OLE)])
    assert [f['filename'] for f in detail(teacher,thread)['posts'][0]['files']]==['photo.HEIC','letter.doc','table.xls']
    for id in ids:assert download(teacher,id).status_code==200
    # The content has to be what the name says, and other types stay refused.
    for name in ['photo.heic','photo.heif','letter.doc','table.xls']:
        wrong=upload(parent,draft,filename=name,body=b'%PDF-1.4 fake');assert wrong.status_code==422,name
    assert upload(parent,draft,filename='photo.heic',body=b'\x00\x00\x00\x18ftypmp42'+b'\x00'*16).status_code==422
    assert upload(parent,draft,filename='run.exe',body=b'MZ'+b'\x00'*16).status_code==422
    assert upload(parent,draft,filename='photo.heif',body=b'\x00\x00\x00\x18ftypmif1'+b'\x00'*16).status_code==200


def test_inline_only_for_validated_images(client):
    people=setup(client);parent=signed('parent');teacher=signed('teacher');other=signed('other')
    thread,(photo,note)=sent(parent,people['teacher']['id'],[('look.JPG',JPEG),('note.txt',b'Text')])
    shown=teacher.get(url('files/'+photo,inline='true'))
    assert shown.status_code==200 and shown.headers['content-type']=='image/jpeg' and shown.headers['content-disposition'].startswith('inline;')
    assert shown.headers['x-content-type-options']=='nosniff' and shown.headers['cache-control']=='no-store' and 'sandbox' in shown.headers['content-security-policy']
    plain=download(teacher,photo);assert plain.headers['content-type']=='application/octet-stream' and plain.headers['content-disposition'].startswith('attachment;')
    text=teacher.get(url('files/'+note,inline='true'));assert text.headers['content-type']=='application/octet-stream' and text.headers['content-disposition'].startswith('attachment;')
    assert other.get(url('files/'+photo,inline='true')).status_code==404
    # A hidden post takes its pictures with it.
    report=teacher.post(url('posts/'+str(detail(teacher,thread)['posts'][0]['id'])+'/report'),json=dict(requestId=str(uuid4()),reason='Нарушение',shareEvidence=True)).json()['id']
    state=client.get(url('reports/'+report)).json()
    assert client.post(url('reports/'+report+'/decision'),json=dict(requestId=str(uuid4()),version=state['version'],postVersion=state['postVersion'],action='hide',note='Скрыто')).status_code==200
    assert teacher.get(url('files/'+photo,inline='true')).status_code==404 and teacher.get(url('files/'+photo)).status_code==404


def test_other_stores_keep_their_file_types(client):
    from test_homework import setup as homework_setup,get as homework,upload as homework_upload
    record,pupil,_,_=homework_setup(client);key=record['lessonKey'];assignment=homework(pupil,key)['assignment']
    assert homework_upload(pupil,key,assignment,body=HEIC,filename='photo.heic').status_code==422
    assert homework_upload(pupil,key,assignment,body=OLE,filename='letter.doc').status_code==422
    assert homework_upload(pupil,key,assignment,body=b'%PDF-1.4',filename='letter.pdf').status_code==200
