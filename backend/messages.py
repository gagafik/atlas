"""Private school conversations and fixed-recipient announcements."""
from collections import defaultdict
from datetime import timedelta
import hashlib
import json
from typing import Literal
from uuid import uuid4

from fastapi import HTTPException, Query, Request
from pydantic import Field
from .access import now
from .journal import user_ids
from .models import Model
from .notifications import encoded
from .portal_cache import PortalSnapshots
from .resources import teacher_ids

STAFF={'admin','editor','teacher','viewer'}
FAMILY={'student','parent'}


class NewThread(Model):
    requestId: str = Field(pattern=r'^[a-f0-9-]{36}$')
    kind: Literal['direct','group','announcement'] = 'direct'
    title: str = Field(min_length=1,max_length=160)
    text: str = Field(default='',max_length=10000)
    recipientId: str | None = None
    recipientIds: list[str] = Field(default_factory=list,max_length=199)
    studentId: str | None = None
    groupIds: list[str] = Field(default_factory=list,max_length=200)
    roles: list[Literal['student','parent','teacher','editor','viewer','admin']] = Field(default_factory=list,max_length=6)
    schoolwide: bool = False
    requireAck: bool = False
    draftId: str | None = Field(default=None,pattern=r'^[a-f0-9-]{36}$')
    fileIds: list[str] = Field(default_factory=list,max_length=5)
    draftVersion: int | None = Field(default=None,ge=1)


class Reply(Model):
    requestId: str = Field(pattern=r'^[a-f0-9-]{36}$')
    version: int | None = Field(default=None,ge=1)  # old clients still send it; replies do not lock the conversation
    text: str = Field(default='',max_length=10000)
    draftId: str | None = Field(default=None,pattern=r'^[a-f0-9-]{36}$')
    fileIds: list[str] = Field(default_factory=list,max_length=5)
    draftVersion: int | None = Field(default=None,ge=1)


class ParticipantChange(Model):
    version: int = Field(ge=1)
    userId: str
    action: Literal['leave','remove']


class Change(Model):
    version: int = Field(ge=1)
    action: Literal['close','reopen','withdraw','acknowledge']


class Read(Model):
    throughId: int = Field(ge=1)


class Directory:
    def __init__(self,state,workspace):
        self.state,self.workspace=state,workspace
        self.students={s.id:s for s in state.students if s.active}
        by_class=defaultdict(set)
        for s in self.students.values(): by_class[s.classId].add(s.id)
        self.members={g.id:(set().union(*(by_class[c] for c in g.classIds)) if g.kind=='whole' else set(g.studentIds)&self.students.keys()) for g in state.groups}
        self.teacher_groups=defaultdict(set)
        for w in state.workloads:
            for teacher in teacher_ids(w): self.teacher_groups[teacher].add(w.groupId)

    def enabled(self,user):
        if not user or not user['active']: return False
        if user['role']=='admin': return True
        if user['role']=='editor': return self.workspace in user['workspaces']
        valid=dict(student=self.students,teacher={t.id for t in self.state.teachers},class_={c.id for c in self.state.classes},group=self.members,room={r.id for r in self.state.rooms})
        return any(g['workspace']==self.workspace and set(g['entityIds']) & set(valid.get('class_' if g['view']=='class' else g['view'],())) for g in user['grants'])

    def groups(self,user):
        if user['role']=='admin': return set(self.members)
        if user['role']!='teacher': return set()
        return set().union(*(self.teacher_groups[t] for t in user_ids(user,self.workspace,'teacher')))

    def pupils(self,user):
        if user['role']=='admin': return set(self.students)
        if user['role']=='teacher': return set().union(*(self.members[g] for g in self.groups(user)))
        return user_ids(user,self.workspace,'student') & self.students.keys() if user['role'] in FAMILY else set()

    def pair(self,a,b,student):
        if not self.enabled(a) or not self.enabled(b) or a['id']==b['id']: return False
        if a['role'] in STAFF and b['role'] in STAFF: return student is None
        if (a['role'] in FAMILY)==(b['role'] in FAMILY): return False
        family,staff=(a,b) if a['role'] in FAMILY else (b,a)
        return student in self.pupils(family) and staff['role'] in ['admin','teacher'] and student in self.pupils(staff)


class Messages:
    def __init__(self,access,notifications,stamp):
        self.access,self.notifications=access,notifications
        self.snapshots=PortalSnapshots(access.read,stamp)
        from .message_files import MessageFiles
        self.files=MessageFiles(self)
        from .message_drafts import MessageDrafts
        self.drafts=MessageDrafts(self)
        from .message_groups import MessageGroups
        self.groups=MessageGroups(self)
        from .message_moderation import MessageModeration
        self.moderation=MessageModeration(self)
        from .message_recovery import MessageRecovery
        self.recovery=MessageRecovery(self)

    def tables(self,db):
        self.access.tables(db)
        db.execute('''CREATE TABLE IF NOT EXISTS message_threads (
            id TEXT PRIMARY KEY,workspace TEXT,kind TEXT,title TEXT,author_id TEXT,student_id TEXT,
            version INTEGER,status TEXT,require_ack INTEGER,created TEXT,latest_id INTEGER)''')
        db.execute('''CREATE TABLE IF NOT EXISTS message_members (
            thread_id TEXT,user_id TEXT,role TEXT,scope_students TEXT,read_id INTEGER DEFAULT 0,ack_version INTEGER,
            PRIMARY KEY(thread_id,user_id))''')
        db.execute('CREATE INDEX IF NOT EXISTS message_member_owner ON message_members(user_id,thread_id)')
        # Last post published while the participant had a live link; NULL marks a row from before this column (no read-only history).
        if 'live_through' not in {r[1] for r in db.execute('PRAGMA table_info(message_members)')}:
            db.execute('ALTER TABLE message_members ADD COLUMN live_through INTEGER')
        db.execute('''CREATE TABLE IF NOT EXISTS message_posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,thread_id TEXT,author_id TEXT,author_name TEXT,created TEXT,body TEXT)''')
        db.execute('CREATE INDEX IF NOT EXISTS message_post_thread ON message_posts(thread_id,id)')
        db.execute('CREATE INDEX IF NOT EXISTS message_post_rate ON message_posts(author_id,created)')
        db.execute('CREATE TABLE IF NOT EXISTS message_requests (user_id TEXT,request_id TEXT,body_hash TEXT,thread_id TEXT,PRIMARY KEY(user_id,request_id))')
        db.execute('CREATE TABLE IF NOT EXISTS message_group_scopes (thread_id TEXT PRIMARY KEY,kind TEXT,group_ids TEXT)')
        db.execute('CREATE TABLE IF NOT EXISTS message_exclusions (thread_id TEXT,user_id TEXT,action TEXT,created TEXT,PRIMARY KEY(thread_id,user_id))')
        db.execute('CREATE TABLE IF NOT EXISTS message_group_managers (thread_id TEXT PRIMARY KEY,manager_id TEXT)')
        self.moderation.tables(db)

    def context(self,request,workspace):
        user=request.state.user
        if not user: raise HTTPException(403,'Для переписки сначала создайте учётные записи школы.')
        directory=Directory(self.snapshots.get(workspace).state,workspace)
        if not directory.enabled(user): raise HTTPException(403,'Нет доступа к сообщениям этого пространства.')
        return user,directory

    def accounts(self,db,thread_ids=None,user_ids=None):
        clause='active=1';params=[]
        if thread_ids is not None:
            clause+=' AND id IN (SELECT m.user_id FROM message_members m WHERE m.thread_id IN (SELECT value FROM json_each(?)) AND NOT EXISTS (SELECT 1 FROM message_exclusions x WHERE x.thread_id=m.thread_id AND x.user_id=m.user_id))';params.append(encoded(thread_ids))
        if user_ids is not None:
            clause+=' AND id IN (SELECT value FROM json_each(?))';params.append(encoded(user_ids))
        return {row[0]:self.access.public(row) for row in db.execute('SELECT * FROM accounts WHERE '+clause,params)}

    def recheck(self,db,user,directory):
        row=db.execute('SELECT * FROM accounts WHERE id=?',(user['id'],)).fetchone()
        revision=db.execute('SELECT revision FROM workspaces WHERE id=?',(directory.workspace,)).fetchone()
        if not row or not row[4] or self.access.public(row)!=user or not revision or revision[0]!=directory.state.revision:
            raise HTTPException(409,'Права или школьные данные изменились. Обновите сообщения.')

    def recipient_accounts(self,db,body,user,directory):
        if body.kind=='direct':return self.accounts(db,user_ids=[user['id'],body.recipientId])
        if body.kind=='group' and body.recipientIds:return self.accounts(db,user_ids=[user['id'],*body.recipientIds])
        # Narrow SQL candidates before decoding account permissions; a class
        # discussion must not deserialize every school family's account.
        params=[user['id'],encoded(body.roles)]
        clause='role IN (SELECT value FROM json_each(?))'
        if not body.schoolwide:
            pupils=set().union(*(directory.members.get(g,set()) for g in body.groupIds))
            clause+=''' AND role IN ('student','parent') AND EXISTS (
                SELECT 1 FROM json_each(accounts.permissions,'$.grants') grant_row
                JOIN json_each(grant_row.value,'$.entityIds') entity
                WHERE json_extract(grant_row.value,'$.workspace')=?
                AND json_extract(grant_row.value,'$.view')='student'
                AND entity.value IN (SELECT value FROM json_each(?)))'''
            params.extend([directory.workspace,encoded(sorted(pupils))])
        rows=db.execute('SELECT * FROM accounts WHERE active=1 AND (id=? OR ('+clause+'))',params)
        # recipients() still validates roles, current assignments and every grant.
        return {row[0]:self.access.public(row) for row in rows}

    def thread_accounts(self,db,id,user):
        row=db.execute('SELECT kind,author_id FROM message_threads WHERE id=?',(id,)).fetchone()
        # An announcement recipient has no need to load the other families.
        if row and row[0]=='announcement' and row[1]!=user['id']: return {}
        return self.accounts(db,thread_ids=[id])

    def member(self,db,thread,user,directory,accounts):
        row=db.execute('SELECT role,scope_students,read_id,ack_version FROM message_members WHERE thread_id=? AND user_id=?',(thread['id'],user['id'])).fetchone()
        if not row or row[0]!=user['role'] or not directory.enabled(user): return None
        if db.execute('SELECT 1 FROM message_exclusions WHERE thread_id=? AND user_id=?',(thread['id'],user['id'])).fetchone():return None
        if thread['kind']=='direct':
            other=db.execute('SELECT user_id FROM message_members WHERE thread_id=? AND user_id!=?',(thread['id'],user['id'])).fetchone()
            if not other or not directory.pair(user,accounts.get(other[0]),thread['studentId']): return None
        elif thread['kind']=='group':
            scope=self.groups.scope(db,thread)
            manager=accounts.get(thread['managerId'])
            if not self.groups.eligible(thread,manager,directory,scope,manager=True):return None
            manager_row=db.execute('SELECT role FROM message_members WHERE thread_id=? AND user_id=?',(thread['id'],thread['managerId'])).fetchone()
            if not manager_row or manager_row[0]!=manager['role']:return None
            if not self.groups.eligible(thread,user,directory,scope):return None
            if user['role'] in FAMILY and not set(json.loads(row[1])) & directory.pupils(user) & self.groups.pupils(thread,directory,scope):return None
        elif thread['status']=='withdrawn' and user['id']!=thread['authorId']: return None
        elif user['role'] in FAMILY and not set(json.loads(row[1])) & directory.pupils(user): return None
        return row

    @staticmethod
    def thread(db,row):
        result=dict(zip(['id','workspace','kind','title','authorId','studentId','version','status','requireAck','created','latestId'],row)) | dict(requireAck=bool(row[8]))
        manager=db.execute('SELECT manager_id FROM message_group_managers WHERE thread_id=?',(row[0],)).fetchone() if row[2]=='group' else None
        return result | dict(managerId=manager[0] if manager else row[4])

    def find(self,db,id,user,directory,accounts):
        row=db.execute('SELECT * FROM message_threads WHERE id=? AND workspace=?',(id,directory.workspace)).fetchone()
        thread=self.thread(db,row) if row else None
        member=self.member(db,thread,user,directory,accounts) if thread else None
        if not member: raise HTTPException(404,'Переписка недоступна.')
        return thread,member

    def visible(self,db,thread,user,directory,accounts):
        """(member row, None) with a live link; (row, last readable post) for kept history; None without access."""
        if row:=self.member(db,thread,user,directory,accounts): return row,None
        row=db.execute('SELECT role,scope_students,read_id,ack_version,live_through FROM message_members WHERE thread_id=? AND user_id=?',(thread['id'],user['id'])).fetchone()
        # Past messages stay with the people who took part; a switch between family and staff roles is a different person's account.
        if not row or not row[4] or (row[0] in FAMILY)!=(user['role'] in FAMILY) or not directory.enabled(user): return None
        if thread['kind']=='announcement' and thread['status']=='withdrawn' and user['id']!=thread['authorId']: return None
        return row[:4],min(row[4],thread['latestId'])

    def readable(self,db,id,user,directory,accounts):
        row=db.execute('SELECT * FROM message_threads WHERE id=? AND workspace=?',(id,directory.workspace)).fetchone()
        thread=self.thread(db,row) if row else None
        found=self.visible(db,thread,user,directory,accounts) if thread else None
        if not found: raise HTTPException(404,'Переписка недоступна.')
        return thread,*found

    def rate(self,db,user):
        count=db.execute('SELECT count(*) FROM message_posts WHERE author_id=? AND created>?',(user['id'],(now()-timedelta(minutes=5)).isoformat())).fetchone()[0]
        if count>=30: raise HTTPException(429,'Слишком много сообщений. Попробуйте через несколько минут.')

    def replay(self,db,user,request_id,body):
        signature=hashlib.sha256(encoded(body).encode()).hexdigest()
        row=db.execute('SELECT body_hash,thread_id FROM message_requests WHERE user_id=? AND request_id=?',(user['id'],request_id)).fetchone()
        if row and row[0]!=signature: raise HTTPException(409,'Повторный запрос отличается от отправленного. Обновите переписку.')
        return row[1] if row else None,signature

    def post(self,db,thread,user,text,*,limit=True):
        if limit:self.rate(db,user)
        id=db.execute('INSERT INTO message_posts (thread_id,author_id,author_name,created,body) VALUES (?,?,?,?,?)',
                      (thread['id'],user['id'],user['name'],now().isoformat(),text)).lastrowid
        db.execute('UPDATE message_threads SET latest_id=? WHERE id=?',(id,thread['id']))
        return id

    def advance(self,db,thread_id,post_id,users):
        # Linked participants may later read the history up to this post, even after losing the link.
        db.execute('UPDATE message_members SET live_through=? WHERE thread_id=? AND (live_through IS NULL OR live_through<?) AND user_id IN (SELECT value FROM json_each(?))',(post_id,thread_id,post_id,encoded([u['id'] for u in users])))

    def body_valid(self,body):
        if not body.text and not body.fileIds:raise HTTPException(422,'Напишите сообщение или приложите файл.')
        if body.fileIds and not body.draftId:raise HTTPException(422,'Откройте форму вложений заново.')

    def recipients(self,body,user,directory,accounts):
        if body.kind=='group' and body.recipientIds:
            if body.recipientId or body.schoolwide or body.groupIds or body.roles or body.requireAck:raise HTTPException(422,'Выберите собеседников или учебную аудиторию группы.')
            if len(set(body.recipientIds))!=len(body.recipientIds) or len(body.recipientIds)<2:raise HTTPException(422,'Выберите от двух разных собеседников.')
            selected=[]
            for id in body.recipientIds:
                target=accounts.get(id)
                if not directory.pair(user,target,body.studentId):raise HTTPException(403,'Один из собеседников недоступен.')
                selected.append((target,[body.studentId] if body.studentId else []))
            return selected
        if body.kind!='group' and body.recipientIds:raise HTTPException(422,'Несколько собеседников доступны в групповом диалоге.')
        if body.kind=='direct':
            target=accounts.get(body.recipientId)
            if not directory.pair(user,target,body.studentId): raise HTTPException(403,'Выберите доступного собеседника и ученика.')
            if body.requireAck or body.schoolwide or body.groupIds or body.roles: raise HTTPException(422,'Параметры объявления не относятся к личной переписке.')
            return [(target,[body.studentId] if body.studentId else [])]
        if user['role'] not in ['admin','teacher']: raise HTTPException(403,'Объявления публикуют администраторы и учителя.')
        if body.kind=='group' and (body.requireAck or body.schoolwide):raise HTTPException(422,'Выберите учебные группы; подтверждение ознакомления относится к объявлениям.')
        if body.recipientId or body.studentId: raise HTTPException(422,'Выберите аудиторию объявления.')
        if not body.roles or len(set(body.roles))!=len(body.roles): raise HTTPException(422,'Выберите роли получателей без повторов.')
        if body.schoolwide:
            if user['role']!='admin': raise HTTPException(403,'Объявление всей школе доступно администратору.')
            if body.groupIds: raise HTTPException(422,'Выберите всю школу или отдельные группы.')
            pupils=set(directory.students)
        else:
            if not body.groupIds or not set(body.groupIds)<=directory.groups(user): raise HTTPException(403,'Выберите доступные учебные группы.')
            pupils=set().union(*(directory.members[g] for g in body.groupIds))
            if not set(body.roles)<=FAMILY: raise HTTPException(422,'Для учебной группы доступны ученики и родители.')
        selected=[]
        for candidate in accounts.values():
            if candidate['id']==user['id'] or candidate['role'] not in body.roles or not directory.enabled(candidate): continue
            scopes=directory.pupils(candidate)&pupils if candidate['role'] in FAMILY else set()
            if candidate['role'] not in FAMILY or scopes: selected.append((candidate,sorted(scopes)))
        if not selected: raise HTTPException(422,'В выбранной аудитории нет активных аккаунтов.')
        if body.kind=='group' and not 2<=len(selected)<=199:raise HTTPException(422,'В групповом диалоге нужно от 3 до 200 участников вместе с автором.')
        return selected

    def register(self,app,Workspace):
        @app.get('/api/messages/options')
        def options(request:Request,workspace:Workspace='school'):
            user,directory=self.context(request,workspace)
            pupils=directory.pupils(user)
            return dict(role=user['role'],userId=user['id'],students=[dict(id=s.id,name=s.name) for s in directory.students.values() if s.id in pupils],
                        groups=[dict(id=g.id,name=g.name) for g in directory.state.groups if g.id in directory.groups(user)],canAnnounce=user['role'] in ['admin','teacher'])

        @app.get('/api/messages/contacts')
        def contacts(request:Request,workspace:Workspace='school',studentId:str|None=None,q:str=Query(default='',max_length=100),offset:int=Query(default=0,ge=0,le=100000)):
            user,directory=self.context(request,workspace)
            if studentId and studentId not in directory.pupils(user): raise HTTPException(403,'Ученик недоступен.')
            with self.access.lock,self.access.connect() as db:
                self.tables(db)
                items=[dict(id=u['id'],name=u['name'],role=u['role']) for u in self.accounts(db).values() if q.casefold() in u['name'].casefold() and directory.pair(user,u,studentId)]
            items.sort(key=lambda u:(u['name'],u['id']))
            return dict(items=items[offset:offset+50],nextOffset=offset+50 if len(items)>offset+50 else None)

        @app.post('/api/messages/preview')
        def preview(body:NewThread,request:Request,workspace:Workspace='school'):
            user,directory=self.context(request,workspace)
            with self.access.lock,self.access.connect() as db:
                self.tables(db)
                selected=self.recipients(body,user,directory,self.recipient_accounts(db,body,user,directory))
            return dict(count=len(selected),byRole={role:sum(u['role']==role for u,_ in selected) for role in {u['role'] for u,_ in selected}})

        @app.post('/api/messages/threads')
        def create(body:NewThread,request:Request,workspace:Workspace='school'):
            user,directory=self.context(request,workspace)
            self.body_valid(body)
            with self.access.lock,self.access.connect() as db:
                self.tables(db);db.execute('BEGIN IMMEDIATE');self.recheck(db,user,directory)
                accounts=self.recipient_accounts(db,body,user,directory)
                prior,signature=self.replay(db,user,body.requestId,dict(workspace=workspace,body=body.model_dump(exclude={'draftVersion'} if body.draftVersion is None else set())))
                if prior:
                    self.find(db,prior,user,directory,self.thread_accounts(db,prior,user))
                    return dict(id=prior)
                recipients=self.recipients(body,user,directory,accounts)
                self.drafts.consume(db,user,workspace,body.draftId,body.draftVersion,None)
                self.rate(db,user)
                id=uuid4().hex;created=now().isoformat()
                db.execute('INSERT INTO message_threads VALUES (?,?,?,?,?,?,1,?,?,?,0)',(id,workspace,body.kind,body.title,user['id'],body.studentId,'open',int(body.requireAck),created))
                scopes=[body.studentId] if body.studentId else []
                db.executemany('INSERT INTO message_members (thread_id,user_id,role,scope_students) VALUES (?,?,?,?)',[(id,u['id'],u['role'],encoded(ids)) for u,ids in [(user,scopes),*recipients]])
                if body.kind=='group':db.execute('INSERT INTO message_group_scopes VALUES (?,?,?)',(id,'contacts' if body.recipientIds else 'audience',encoded(body.groupIds)))
                post=self.post(db,dict(id=id),user,body.text)
                self.advance(db,id,post,[user,*(u for u,_ in recipients)])
                self.files.bind(db,body.fileIds,user,workspace,body.draftId,id,post,new=True)
                db.execute('UPDATE message_members SET read_id=? WHERE thread_id=? AND user_id=?',(post,id,user['id']))
                db.execute('INSERT INTO message_requests VALUES (?,?,?,?)',(user['id'],body.requestId,signature,id))
                self.notifications.deliver(db,[u for u,_ in recipients],workspace,1,'message.received')
                self.access.record(db,'message.created',workspace,detail=id)
            return dict(id=id)

        @app.get('/api/messages/threads')
        def inbox(request:Request,workspace:Workspace='school',before:int|None=Query(default=None,ge=1),q:str=Query(default='',max_length=100),unreadOnly:bool=False):
            user,directory=self.context(request,workspace)
            with self.access.lock,self.access.connect() as db:
                self.tables(db)
                db.create_function('casefold',1,lambda value:value.casefold(),deterministic=True)
                # Rank and search by the post each participant may read, so a thread kept as history does not show later activity.
                rows=db.execute('''SELECT t.*,coalesce(m.live_through,t.latest_id) FROM message_threads t JOIN message_members m ON m.thread_id=t.id AND m.user_id=?
                    WHERE t.workspace=? AND (? IS NULL OR coalesce(m.live_through,t.latest_id)<?) AND (?=0 OR t.latest_id>m.read_id)
                    AND (instr(casefold(t.title),?)>0 OR EXISTS(SELECT 1 FROM message_posts p WHERE p.thread_id=t.id AND p.id<=coalesce(m.live_through,t.latest_id) AND instr(casefold(p.body),?)>0
                        AND NOT EXISTS(SELECT 1 FROM message_moderation h WHERE h.post_id=p.id AND h.hidden=1)))
                    ORDER BY coalesce(m.live_through,t.latest_id) DESC LIMIT 101''',(user['id'],workspace,before,before,int(unreadOnly),q.casefold(),q.casefold())).fetchall()
                accounts=self.accounts(db,thread_ids=[r[0] for r in rows[:100] if r[2] in ['direct','group']])
                items=[]
                for row in rows[:100]:
                    thread=self.thread(db,row[:11]);found=self.visible(db,thread,user,directory,accounts)
                    if not found: continue
                    member,through=found;latest=thread['latestId'] if through is None else through
                    if unreadOnly and latest<=member[2]: continue
                    items.append(thread|dict(latestId=latest,unread=latest>member[2],acknowledged=member[3]==thread['version'],readOnly=through is not None))
            return dict(items=items,nextBefore=rows[99][11] if len(rows)>100 else None)

        @app.get('/api/messages/threads/{id}')
        def detail(id:str,request:Request,workspace:Workspace='school',before:int|None=Query(default=None,ge=1)):
            user,directory=self.context(request,workspace)
            with self.access.lock,self.access.connect() as db:
                self.tables(db);accounts=self.thread_accounts(db,id,user)
                thread,member,through=self.readable(db,id,user,directory,accounts)
                bound=thread['latestId'] if through is None else through
                posts=db.execute('SELECT id,author_id,author_name,created,body FROM message_posts WHERE thread_id=? AND id<=? AND (? IS NULL OR id<?) ORDER BY id DESC LIMIT 51',(id,bound,before,before)).fetchall()
                peers=[]
                # Read receipts of a former participant would reveal activity after their access ended.
                if through is None and (thread['authorId']==user['id'] or thread['kind'] in ['direct','group']):
                    for uid,read_id,ack in db.execute('SELECT user_id,read_id,ack_version FROM message_members WHERE thread_id=?',(id,)):
                        if uid!=user['id'] and (peer:=accounts.get(uid)) and self.member(db,thread,peer,directory,accounts):
                            peers.append(dict(id=uid,name=peer['name'],read=read_id>=thread['latestId'],acknowledged=ack==thread['version']))
                attachments=self.files.for_posts(db,[r[0] for r in posts[:50]])
                hidden={r[0] for r in db.execute('SELECT post_id FROM message_moderation WHERE hidden=1 AND post_id IN (SELECT value FROM json_each(?))',(encoded([r[0] for r in posts[:50]]),))}
                return thread|dict(userId=user['id'],latestId=bound,readOnly=through is not None,canReply=through is None and thread['kind'] in ['direct','group'] and thread['status']=='open',canManage=through is None and thread['managerId']==user['id'],
                    acknowledged=member[3]==thread['version'],participants=peers,posts=[dict(zip(['id','authorId','author','created','text'],row))|dict(text='Сообщение скрыто администратором.' if row[0] in hidden else row[4],hidden=row[0] in hidden,files=[] if row[0] in hidden else attachments[row[0]]) for row in reversed(posts[:50])],
                    nextBefore=posts[49][0] if len(posts)>50 else None)

        @app.post('/api/messages/threads/{id}/reply')
        def reply(id:str,body:Reply,request:Request,workspace:Workspace='school'):
            user,directory=self.context(request,workspace)
            self.body_valid(body)
            with self.access.lock,self.access.connect() as db:
                self.tables(db);db.execute('BEGIN IMMEDIATE');self.recheck(db,user,directory)
                accounts=self.thread_accounts(db,id,user);thread,_=self.find(db,id,user,directory,accounts)
                prior,signature=self.replay(db,user,body.requestId,dict(workspace=workspace,id=id,body=body.model_dump(exclude={'draftVersion'} if body.draftVersion is None else set())))
                if prior: return dict(id=prior)
                # Simultaneous replies are all kept in commit order; only closing, membership and management check versions.
                if thread['kind'] not in ['direct','group'] or thread['status']!='open': raise HTTPException(409,'Ответы в этой переписке закрыты.')
                self.drafts.consume(db,user,workspace,body.draftId,body.draftVersion,id)
                post=self.post(db,thread,user,body.text)
                self.files.bind(db,body.fileIds,user,workspace,body.draftId,id,post)
                db.execute('UPDATE message_members SET read_id=? WHERE thread_id=? AND user_id=?',(post,id,user['id']))
                db.execute('INSERT INTO message_requests VALUES (?,?,?,?)',(user['id'],body.requestId,signature,id))
                live=[u for u in accounts.values() if self.member(db,thread,u,directory,accounts)]
                self.advance(db,id,post,live)
                self.notifications.deliver(db,[u for u in live if u['id']!=user['id']],workspace,thread['version'],'message.received')
                self.access.record(db,'message.replied',workspace,detail=id)
            return dict(id=id)

        @app.put('/api/messages/threads/{id}/read')
        def read(id:str,body:Read,request:Request,workspace:Workspace='school'):
            user,directory=self.context(request,workspace)
            with self.access.lock,self.access.connect() as db:
                self.tables(db);db.execute('BEGIN IMMEDIATE');self.recheck(db,user,directory)
                thread,_,through=self.readable(db,id,user,directory,self.thread_accounts(db,id,user))
                if through is not None and body.throughId>through or not db.execute('SELECT 1 FROM message_posts WHERE thread_id=? AND id=?',(id,body.throughId)).fetchone(): raise HTTPException(422,'Укажите сообщение из этой переписки.')
                # Opening proves the live link: a row from before live_through, or one left stale, may read everything up to now.
                if through is None: self.advance(db,id,thread['latestId'],[user])
                db.execute('UPDATE message_members SET read_id=max(read_id,?) WHERE thread_id=? AND user_id=?',(body.throughId,id,user['id']))
            return dict(ok=True)

        @app.post('/api/messages/threads/{id}/change')
        def change(id:str,body:Change,request:Request,workspace:Workspace='school'):
            user,directory=self.context(request,workspace)
            with self.access.lock,self.access.connect() as db:
                self.tables(db);db.execute('BEGIN IMMEDIATE');self.recheck(db,user,directory)
                thread,_=self.find(db,id,user,directory,self.thread_accounts(db,id,user))
                if thread['version']!=body.version: raise HTTPException(409,'Переписка изменилась. Обновите её.')
                if body.action=='acknowledge':
                    if thread['kind']!='announcement' or not thread['requireAck'] or thread['authorId']==user['id']: raise HTTPException(422,'Подтверждение не требуется.')
                    db.execute('UPDATE message_members SET ack_version=?,read_id=? WHERE thread_id=? AND user_id=?',(body.version,thread['latestId'],id,user['id']))
                else:
                    if thread['managerId']!=user['id']: raise HTTPException(403,'Изменять статус может управляющий обсуждением или автор личного диалога/объявления.')
                    if body.action=='withdraw' and thread['kind']=='announcement': status='withdrawn'
                    elif body.action in ['close','reopen'] and thread['kind'] in ['direct','group']: status='closed' if body.action=='close' else 'open'
                    else: raise HTTPException(422,'Действие не подходит к этой переписке.')
                    if thread['status']==status: return dict(ok=True)
                    db.execute('UPDATE message_threads SET status=?,version=version+1 WHERE id=?',(status,id))
                self.access.record(db,'message.'+body.action,workspace,detail=id)
            return dict(ok=True)

        @app.post('/api/messages/threads/{id}/participants')
        def participants(id:str,body:ParticipantChange,request:Request,workspace:Workspace='school'):
            user,directory=self.context(request,workspace)
            with self.access.lock,self.access.connect() as db:
                self.tables(db);db.execute('BEGIN IMMEDIATE');self.recheck(db,user,directory)
                accounts=self.thread_accounts(db,id,user);thread,_=self.find(db,id,user,directory,accounts)
                if thread['kind']!='group':raise HTTPException(422,'Участники меняются только в групповом обсуждении.')
                if thread['version']!=body.version:raise HTTPException(409,'Обсуждение изменилось. Обновите его.')
                if body.action=='leave' and body.userId!=user['id'] or body.action=='remove' and user['id']!=thread['managerId']:raise HTTPException(403,'Нет права изменить этого участника.')
                if body.userId==thread['managerId']:raise HTTPException(422,'Перед выходом передайте управление другому участнику или закройте обсуждение.')
                # A manager can remove a now-inactive account to free a place.
                if not db.execute('SELECT 1 FROM message_members m WHERE m.thread_id=? AND m.user_id=? AND NOT EXISTS (SELECT 1 FROM message_exclusions x WHERE x.thread_id=m.thread_id AND x.user_id=m.user_id)',(id,body.userId)).fetchone():raise HTTPException(404,'Участник недоступен.')
                # Record what the leaver or removed member may keep before the exclusion closes the link; rows from before live_through get their bound here too.
                if (target:=accounts.get(body.userId)) and self.member(db,thread,target,directory,accounts): self.advance(db,id,thread['latestId'],[target])
                db.execute('INSERT INTO message_exclusions VALUES (?,?,?,?)',(id,body.userId,body.action,now().isoformat()))
                # Access revocation must remain possible after the posting limit is reached.
                post=self.post(db,thread,user,'Участник вышел из обсуждения.' if body.action=='leave' else 'Управляющий изменил состав обсуждения.',limit=False)
                db.execute('UPDATE message_threads SET version=version+1 WHERE id=?',(id,))
                live=[u for u in accounts.values() if self.member(db,thread,u,directory,accounts)]
                self.advance(db,id,post,live)
                self.notifications.deliver(db,[u for u in live if u['id']!=user['id']],workspace,thread['version']+1,'message.received')
                self.access.record(db,'message.participant_'+body.action,workspace,detail=id+' · '+body.userId)
            return dict(ok=True)
