from uuid import uuid4

from backend import api
from backend.access import now
from test_access import client,create,signed
from test_message_channels import matrix
from test_messages import setup,url,detail,payload


def announce(teacher,**values):
    body=dict(requestId=str(uuid4()),kind='announcement',title='Экскурсия',text='Едем в музей.',groupIds=['g-a'],roles=['parent'])|values
    sent=teacher.post(url(),json=body);assert sent.status_code==200,sent.text
    return sent.json()['id']


def feed(user,**params):
    response=user.get(url('feed',**params));assert response.status_code==200,response.text
    return response.json()['items']


def item(user,id):return next((i for i in feed(user) if i['id']==id),None)


def respond(user,id,text='Отвечаю.',**values):return user.post(url('threads/'+id+'/replies'),json=dict(requestId=str(uuid4()),text=text)|values)


def replies(user,id,**params):
    response=user.get(url('threads/'+id+'/replies',**params));assert response.status_code==200,response.text
    return response.json()['items']


def like(user,post,liked=True):return user.put(url('posts/'+str(post)+'/like'),json=dict(liked=liked))


def hide(client,reporter,post,action='hide'):
    """The reporter complains about the post and the administrator decides on it; returns the report id."""
    made=reporter.post(url('posts/'+str(post)+'/report'),json=dict(requestId=str(uuid4()),reason='Нарушение правил',shareEvidence=True));assert made.status_code==200,made.text
    id=made.json()['id'];report=client.get(url('reports/'+id)).json()
    decided=client.post(url('reports/'+id+'/decision'),json=dict(requestId=str(uuid4()),version=report['version'],postVersion=report['postVersion'],action=action,note='Решение'));assert decided.status_code==200,decided.text
    return id


def test_feed_lists_only_own_announcements_with_scope(client):
    people=setup(client);teacher=signed('teacher');parent=signed('parent');other=signed('other');pupil=signed('student')
    create(client,'parent',login='far',grants=[dict(workspace='demo',view='student',entityIds=['s3'])])
    id=announce(teacher)
    assert parent.post(url(),json=payload(people['teacher']['id'])).status_code==200
    for user in [parent,other]:
        shown=item(user,id);assert shown and shown['text']=='Едем в музей.' and shown['unread'] and not shown['readOnly'] and shown['repliesMode']=='off' and not shown['canRespond']
    assert feed(signed('far'))==[] and feed(pupil)==[]
    assert [i['id'] for i in feed(teacher)]==[id] and item(teacher,id)['canManage']
    assert parent.put(url('threads/'+id+'/read'),json=dict(throughId=item(parent,id)['latestId'])).status_code==200
    assert not item(parent,id)['unread'] and item(other,id)['unread']
    second=announce(teacher,title='Второе');assert [i['id'] for i in feed(parent)]==[second,id]
    page=parent.get(url('feed',before=feed(parent)[0]['latestId'])).json();assert [i['id'] for i in page['items']]==[id]


def test_replies_author_mode_private_between_replier_and_author(client):
    people=setup(client);teacher=signed('teacher');parent=signed('parent');other=signed('other')
    id=announce(teacher,replies='author')
    mine=respond(parent,id,'Мы поедем.');assert mine.status_code==200,mine.text
    assert respond(other,id,'Мы нет.').status_code==200
    for user in [parent,other]:
        shown=item(user,id);assert shown['replyCount']==1 and len(shown['lastReplies'])==1 and shown['canRespond']
        assert len(replies(user,id))==1
    assert replies(parent,id)[0]['text']=='Мы поедем.' and replies(other,id)[0]['text']=='Мы нет.'
    assert item(teacher,id)['replyCount']==2 and len(replies(teacher,id))==2 and not item(teacher,id)['canRespond']
    assert any(n['action']=='message.feed_reply' for n in teacher.get('/api/notifications/inbox').json()['items'])
    assert respond(teacher,id).status_code==422
    # Another recipient cannot reach, and so not report, a reply they may not read.
    theirs=next(r for r in replies(teacher,id) if r['authorId']==people['parent']['id'])['id']
    assert other.post(url('posts/'+str(theirs)+'/report'),json=dict(requestId=str(uuid4()),reason='Нарушение',shareEvidence=True)).status_code==404
    closed=announce(teacher);assert respond(parent,closed).status_code==409
    assert respond(parent,id,'').status_code==422
    # Dialogs take no replies of this kind.
    dialog=parent.post(url(),json=payload(people['teacher']['id'])).json()['id']
    assert respond(parent,dialog).status_code==422
    assert teacher.post(url(),json=dict(requestId=str(uuid4()),kind='direct',title='',text='Привет',recipientId=people['parent']['id'],studentId='s1',replies='author')).status_code==422


def test_replies_all_mode_gated_by_matrix(client):
    people=setup(client);teacher=signed('teacher');parent=signed('parent');other=signed('other')
    body=dict(requestId=str(uuid4()),kind='announcement',title='Вопрос',text='Что взять?',groupIds=['g-a'],roles=['parent'],replies='all')
    assert teacher.post(url(),json=body).status_code==403
    matrix(client,add=['feed_public_replies'])
    assert teacher.get(url('options')).json()['publicReplies']
    id=teacher.post(url(),json=body).json()['id']
    assert respond(parent,id,'Тетради.').status_code==200
    seen=replies(other,id);assert [(r['text'],r['authorId']) for r in seen]==[('Тетради.',people['parent']['id'])]
    assert item(other,id)['replyCount']==1 and item(other,id)['repliesMode']=='all'
    assert respond(other,id,'И ручки.').status_code==200 and len(replies(parent,id))==2
    matrix(client,remove=['feed_public_replies'])
    assert [r['text'] for r in replies(other,id)]==['И ручки.'] and item(other,id)['repliesMode']=='author' and item(other,id)['replyCount']==1
    assert len(replies(teacher,id))==2


def test_reply_visibility_is_fixed_when_written(client):
    people=setup(client);teacher=signed('teacher');parent=signed('parent');other=signed('other')
    matrix(client,add=['feed_public_replies'])
    id=announce(teacher,replies='all')
    assert respond(parent,id,'Открыто.').status_code==200
    # Without the channel replies go to the author alone, and switching it on again does not publish what was written meanwhile.
    matrix(client,remove=['feed_public_replies'])
    assert respond(parent,id,'Личное.').status_code==200
    assert replies(other,id)==[]
    matrix(client,add=['feed_public_replies'])
    assert [r['text'] for r in replies(other,id)]==['Открыто.'] and item(other,id)['replyCount']==1
    assert [r['text'] for r in replies(teacher,id)]==['Открыто.','Личное.'] and item(teacher,id)['replyCount']==2
    assert [r['text'] for r in replies(parent,id)]==['Открыто.','Личное.']
    # What the other family may not read it cannot report either.
    report=lambda post:other.post(url('posts/'+str(post)+'/report'),json=dict(requestId=str(uuid4()),reason='Нарушение',shareEvidence=True))
    secret,open_=(next(r for r in replies(teacher,id) if r['text']==text)['id'] for text in ['Личное.','Открыто.'])
    assert report(secret).status_code==404 and report(open_).status_code==200


def test_a_reply_is_never_a_read_marker(client):
    people=setup(client);teacher=signed('teacher');parent=signed('parent');other=signed('other')
    id=announce(teacher,replies='author')
    assert respond(other,id,'Ответ.').status_code==200
    reply_id=replies(other,id)[0]['id'];root=item(parent,id)['postId']
    assert parent.put(url('threads/'+id+'/read'),json=dict(throughId=reply_id)).status_code==422
    assert item(parent,id)['unread'] and parent.put(url('threads/'+id+'/read'),json=dict(throughId=root)).status_code==200


def test_reply_keeps_read_state_ack_and_history(client):
    people=setup(client);teacher=signed('teacher');parent=signed('parent');other=signed('other')
    id=announce(teacher,replies='author',requireAck=True)
    shown=item(parent,id)
    assert parent.put(url('threads/'+id+'/read'),json=dict(throughId=shown['latestId'])).status_code==200
    assert parent.post(url('threads/'+id+'/change'),json=dict(version=shown['version'],action='acknowledge')).status_code==200
    before=detail(teacher,id)
    answered=respond(other,id,'Согласны.').json()
    after=item(parent,id);assert not after['unread'] and after['acknowledged'] and after['version']==shown['version']
    assert item(other,id)['unread'] and detail(teacher,id)['latestId']==before['latestId'] and len(detail(teacher,id)['posts'])==1
    assert len(detail(other,id)['posts'])==1 and detail(other,id)['repliesMode']=='author'
    hide(client,teacher,answered['id'])
    masked=replies(teacher,id)[0];assert masked['hidden'] and masked['text']!='Согласны.'
    assert item(parent,id)['acknowledged'] and item(parent,id)['version']==shown['version']
    report=client.get(url('reports')).json()['items'][0]
    state=client.get(url('reports/'+report['id'])).json()
    restored=client.post(url('reports/'+report['id']+'/decision'),json=dict(requestId=str(uuid4()),version=state['version'],postVersion=state['postVersion'],action='restore',note='Ошибка'));assert restored.status_code==200,restored.text
    assert replies(teacher,id)[0]['text']=='Согласны.' and item(parent,id)['acknowledged']


def test_former_recipient_read_only(client):
    people=setup(client);teacher=signed('teacher');other=signed('other')
    matrix(client,add=['feed_public_replies'])
    id=announce(teacher,replies='all')
    assert respond(other,id,'Раньше.').status_code==200
    parent=signed('parent');mine=respond(parent,id,'Моё.').json()['id']
    account=people['parent'];update={k:v for k,v in account.items() if k!='id'};update['grants'][0]['entityIds']=['s3']
    assert client.put('/api/access/users/'+account['id'],json=update).status_code==200
    assert respond(other,id,'Позже.').status_code==200
    former=signed('parent');shown=item(former,id)
    assert shown['readOnly'] and not shown['canRespond'] and not shown['canLike'] and shown['replyCount']==1
    assert [r['text'] for r in replies(former,id)]==['Моё.']
    assert respond(former,id).status_code==404
    post=shown['postId'];assert like(former,post).status_code==404
    assert len(replies(teacher,id))==3


def test_likes_idempotent_and_anonymous(client):
    people=setup(client);teacher=signed('teacher');parent=signed('parent');other=signed('other')
    id=announce(teacher);post=item(parent,id)['postId']
    assert like(parent,post).json()=={'likes':1,'liked':True} and like(parent,post).json()=={'likes':1,'liked':True}
    assert like(other,post).json()=={'likes':2,'liked':True}
    assert item(parent,id)['liked'] and item(parent,id)['likes']==2 and item(teacher,id)['likes']==2 and not item(teacher,id)['liked']
    assert like(parent,post,False).json()=={'likes':1,'liked':False} and not item(parent,id)['liked'] and item(other,id)['liked']
    assert like(parent,post+1000).status_code==404
    dialog=parent.post(url(),json=payload(people['teacher']['id'])).json()['id']
    assert like(parent,detail(parent,dialog)['posts'][0]['id']).status_code==422
    reply=respond(other,announce(teacher,replies='author'),'Да.')
    assert reply.status_code==200 and like(teacher,reply.json()['id']).status_code==422
    assert teacher.post(url('threads/'+id+'/change'),json=dict(version=1,action='withdraw')).status_code==200
    assert like(teacher,post).status_code==409 and like(parent,post).status_code==404 and item(parent,id) is None
    assert item(teacher,id)['status']=='withdrawn' and not item(teacher,id)['canLike']


def test_reply_idempotency_and_old_database(client,tmp_path):
    people=setup(client);teacher=signed('teacher');parent=signed('parent')
    id=announce(teacher,replies='author');request=str(uuid4())
    first=parent.post(url('threads/'+id+'/replies'),json=dict(requestId=request,text='Один раз.'))
    again=parent.post(url('threads/'+id+'/replies'),json=dict(requestId=request,text='Один раз.'))
    assert first.status_code==200 and again.json()==first.json() and len(replies(parent,id))==1
    assert parent.post(url('threads/'+id+'/replies'),json=dict(requestId=request,text='Другое.')).status_code==409
    import sqlite3
    from contextlib import closing
    with closing(sqlite3.connect(tmp_path/'old.db')) as db:
        db.execute('CREATE TABLE message_posts (id INTEGER PRIMARY KEY AUTOINCREMENT,thread_id TEXT,author_id TEXT,author_name TEXT,created TEXT,body TEXT)')
        db.execute("INSERT INTO message_posts (thread_id,author_id,author_name,created,body) VALUES ('t','u','Имя','2026-01-01','Старое')")
        api.messages.tables(db);api.messages.tables(db)
        assert {'reply_to','reply_public'}<={r[1] for r in db.execute('PRAGMA table_info(message_posts)')}
        assert db.execute('SELECT thread_id,body,reply_to,reply_public FROM message_posts').fetchall()==[('t','Старое',None,None)]


def test_replies_are_rate_limited_like_posts(client):
    people=setup(client);teacher=signed('teacher');parent=signed('parent')
    id=announce(teacher,replies='author')
    with api.connect() as db:
        for i in range(30):db.execute('INSERT INTO message_posts (thread_id,author_id,author_name,created,body,reply_to) VALUES (?,?,?,?,?,1)',(id,people['parent']['id'],'Родитель',now().isoformat(),str(i)))
    assert respond(parent,id,'Ещё').status_code==429
