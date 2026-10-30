"""Private school conversations and fixed-recipient announcements."""
from collections import defaultdict
from contextlib import closing
from datetime import timedelta
import hashlib
import json
import sqlite3
from typing import Literal
from uuid import uuid4

from fastapi import HTTPException, Query, Request
from pydantic import Field
from .access import now
from .class_staff import led
from .journal import user_ids
from .message_files import tables as file_tables
from .message_channels import ALWAYS, CLOSED, DEFAULT_CHANNELS, load
from .models import Model
from .notifications import clip, encoded
from .portal_cache import PortalSnapshots
from .resources import teacher_ids

STAFF={'admin','editor','teacher','viewer','staff'}
FAMILY={'student','parent'}
PEER={'student':'student_classmates','parent':'parent_class_parents'}  # the channel between families of one class, by role


class NewThread(Model):
    requestId: str = Field(pattern=r'^[a-f0-9-]{36}$')
    kind: Literal['direct','group','announcement'] = 'direct'
    title: str = Field(default='',max_length=160)  # a dialog needs none; an announcement is checked in create()
    text: str = Field(default='',max_length=10000)
    recipientId: str | None = None
    recipientIds: list[str] = Field(default_factory=list,max_length=199)
    studentId: str | None = None
    groupIds: list[str] = Field(default_factory=list,max_length=200)
    roles: list[Literal['student','parent','teacher','editor','viewer','admin','staff']] = Field(default_factory=list,max_length=7)
    schoolwide: bool = False
    audience: Literal['school','teachers','class_teachers','staff','my_classes','my_courses','saved'] | None = None
    savedGroupId: str | None = Field(default=None,max_length=100)
    replies: Literal['off','author','all'] = 'off'
    requireAck: bool = False
    draftId: str | None = Field(default=None,pattern=r'^[a-f0-9-]{36}$')
    fileIds: list[str] = Field(default_factory=list,max_length=5)
    draftVersion: int | None = Field(default=None,ge=1)

    def signature(self):
        """What a retry is compared with. Fields added later stay out while they hold their defaults, so requests stored earlier still replay."""
        unset=[n for n,v in [('draftVersion',self.draftVersion is None),('audience',self.audience is None),('savedGroupId',self.savedGroupId is None),('replies',self.replies=='off')] if v]
        return self.model_dump(exclude=set(unset))


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
        self.channels=DEFAULT_CHANNELS  # replaced by the stored matrix of the workspace in Messages.context

    def enabled(self,user):
        if not user or not user['active']: return False
        if user['role']=='admin': return True
        if user['role'] in ('editor','staff'): return self.workspace in user['workspaces']
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

    def led_pupils(self,user):
        """Active pupils of the classes this teacher leads."""
        classes=led(user,self,self.workspace)
        return {id for id,s in self.students.items() if s.classId in classes} if classes else set()

    def reachable_students(self,user):
        pupils=self.pupils(user)
        return pupils|self.led_pupils(user) if user['role']=='teacher' and 'class_teacher_families' in self.channels else pupils

    def links(self,a,b,student):
        """Channels that connect a to b about a pupil, whatever the matrix switched off."""
        ar,br=a['role'],b['role']
        if ar in STAFF and br in STAFF: return set() if student else {ALWAYS} if ar=='admin' else {'staff_staff'}
        if ar in FAMILY and br in FAMILY:
            # Families of one class: the same role, and the pupil of a studies with some pupil of b.
            if ar!=br or student not in self.pupils(a): return set()
            return {PEER[ar]} if any(self.students[p].classId==self.students[student].classId for p in self.pupils(b)) else set()
        if ar in FAMILY:
            if student not in self.pupils(a): return set()
            if br=='admin': return {ar+'_admin'}
            if br!='teacher': return set()
            return {ar+'_any_teacher'}|({ar+'_teachers'} if student in self.pupils(b) else set())|({ar+'_class_teacher'} if student in self.led_pupils(b) else set())
        if student not in self.pupils(b): return set()
        if ar=='admin': return {ALWAYS}
        if ar!='teacher': return set()
        return ({'teacher_'+('students' if br=='student' else 'parents')} if student in self.pupils(a) else set())|({'class_teacher_families'} if student in self.led_pupils(a) else set())

    def start(self,a,b,student):
        """May a open a conversation with b about the pupil? An administrator sender needs no channel."""
        if not self.enabled(a) or not self.enabled(b) or a['id']==b['id']: return False
        return bool(self.links(a,b,student)&(self.channels|{ALWAYS}))

    def closed(self,a,b,student):
        """The two accounts are linked, but only through channels the administrator switched off."""
        return self.enabled(a) and self.enabled(b) and a['id']!=b['id'] and any(l and not l&(self.channels|{ALWAYS}) for l in (self.links(a,b,student),self.links(b,a,student)))

    def pair(self,a,b,student):
        """A live link: a channel is open in either direction. Replying is judged by may_reply, starting by start."""
        return self.start(a,b,student) or self.start(b,a,student)

    def start_about(self,a,b,student):
        if a['role'] in STAFF and b['role'] in STAFF: return self.start(a,b,None)
        if student: return self.start(a,b,student)
        return any(self.start(a,b,s) for s in self.pupils(a if a['role'] in FAMILY else b))

    def peer(self,user,student):
        """A family account whose pupil studies in the class of this one, while the channel between those families is open."""
        if user['role'] not in FAMILY or PEER[user['role']] not in self.channels or student not in self.students: return False
        return any(self.students[p].classId==self.students[student].classId for p in self.pupils(user))

    def related(self,user,student):
        if student in self.reachable_students(user) or self.peer(user,student): return True
        return user['role']=='teacher' and student in self.students and bool({'student_any_teacher','parent_any_teacher'}&self.channels)

    def reach(self,user,c):
        """Pupils through which the user may write to account c (empty for staff); None when c is out of reach."""
        if c['id']==user['id'] or not self.enabled(c): return None
        if c['role'] in FAMILY:
            scopes=sorted(s for s in self.pupils(c) if self.start(user,c,s))
            return scopes or None
        return [] if c['role'] in STAFF and self.start(user,c,None) else None

    def audiences(self,user):
        if user['role']=='admin': return ['school','teachers','class_teachers','staff','saved']
        if user['role']!='teacher': return []
        return ['my_classes','my_courses']+(['school'] if 'teacher_school' in self.channels else [])+(['teachers','class_teachers','staff'] if 'teacher_colleagues_all' in self.channels else [])+['saved']

    def classes(self,user):
        if user['role']=='admin': return {c.id for c in self.state.classes}
        if user['role']!='teacher': return set()
        mine=self.groups(user)
        whole={c for g in self.state.groups if g.id in mine and g.kind=='whole' for c in g.classIds}
        return whole|led(user,self,self.workspace) if 'class_teacher_families' in self.channels else whole


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
        from .message_channels import MessageChannels
        self.channels=MessageChannels(self)
        from .message_audiences import MessageAudiences
        self.audiences=MessageAudiences(self)
        from .message_feed import MessageFeed
        self.feed=MessageFeed(self)

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
        # A feed reply under an announcement points at its root post. It never moves latest_id, so history, search and read markers ignore it.
        if 'reply_to' not in {r[1] for r in db.execute('PRAGMA table_info(message_posts)')}:
            db.execute('ALTER TABLE message_posts ADD COLUMN reply_to INTEGER')
        db.execute('CREATE INDEX IF NOT EXISTS message_post_thread ON message_posts(thread_id,id)')
        db.execute('CREATE INDEX IF NOT EXISTS message_post_replies ON message_posts(reply_to,id) WHERE reply_to IS NOT NULL')
        db.execute('CREATE INDEX IF NOT EXISTS message_post_rate ON message_posts(author_id,created)')
        db.execute('CREATE TABLE IF NOT EXISTS message_requests (user_id TEXT,request_id TEXT,body_hash TEXT,thread_id TEXT,PRIMARY KEY(user_id,request_id))')
        db.execute('CREATE TABLE IF NOT EXISTS message_group_scopes (thread_id TEXT PRIMARY KEY,kind TEXT,group_ids TEXT)')
        db.execute('CREATE TABLE IF NOT EXISTS message_exclusions (thread_id TEXT,user_id TEXT,action TEXT,created TEXT,PRIMARY KEY(thread_id,user_id))')
        db.execute('CREATE TABLE IF NOT EXISTS message_group_managers (thread_id TEXT PRIMARY KEY,manager_id TEXT)')
        self.moderation.tables(db)
        self.channels.tables(db)
        self.audiences.tables(db)
        self.feed.tables(db)

    def context(self,request,workspace):
        user=request.state.user
        if not user: raise HTTPException(403,'Для переписки сначала создайте учётные записи школы.')
        directory=Directory(self.snapshots.get(workspace).state,workspace)
        if not directory.enabled(user): raise HTTPException(403,'Нет доступа к сообщениям этого пространства.')
        directory.channels=self.channels.current(workspace)
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
        # Writes see the matrix as it is inside the transaction, not as it was when the request began.
        directory.channels=load(db,directory.workspace)

    @staticmethod
    def narrow(workspace,pupils):
        """SQL clause and parameters: a family account whose grants name one of the pupils."""
        return '''role IN ('student','parent') AND EXISTS (
                SELECT 1 FROM json_each(accounts.permissions,'$.grants') grant_row
                JOIN json_each(grant_row.value,'$.entityIds') entity
                WHERE json_extract(grant_row.value,'$.workspace')=?
                AND json_extract(grant_row.value,'$.view')='student'
                AND entity.value IN (SELECT value FROM json_each(?)))''',[workspace,encoded(sorted(pupils))]

    def candidates(self,db,directory,user,roles,pupils):
        """The author and active accounts of the roles; pupils=None takes every account of those roles, otherwise only families of these pupils."""
        # Narrow SQL candidates before decoding account permissions; a class
        # discussion must not deserialize every school family's account.
        params=[user['id'],encoded(roles)]
        clause='role IN (SELECT value FROM json_each(?))'
        if pupils is not None:
            more,args=self.narrow(directory.workspace,pupils);clause+=' AND '+more;params.extend(args)
        rows=db.execute('SELECT * FROM accounts WHERE active=1 AND (id=? OR ('+clause+'))',params)
        # recipients() still validates roles, current assignments and every grant.
        return {row[0]:self.access.public(row) for row in rows}

    def recipient_accounts(self,db,body,user,directory):
        if body.kind=='direct':return self.accounts(db,user_ids=[user['id'],body.recipientId])
        if body.kind=='group' and body.recipientIds:return self.accounts(db,user_ids=[user['id'],*body.recipientIds])
        return self.candidates(db,directory,user,body.roles,None if body.schoolwide else set().union(*(directory.members.get(g,set()) for g in body.groupIds)))

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
            # A parent of the same class may be in a family's own group: the pupil of the thread is not theirs.
            if user['role'] in FAMILY and not set(json.loads(row[1])) & directory.pupils(user) & self.groups.pupils(thread,directory,scope) and not (scope[0]=='contacts' and directory.peer(user,thread['studentId'])):return None
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
                if not directory.start(user,target,body.studentId):raise HTTPException(403,CLOSED if target and directory.closed(user,target,body.studentId) else 'Один из собеседников недоступен.')
                selected.append((target,[body.studentId] if body.studentId else []))
            return selected
        if body.kind!='group' and body.recipientIds:raise HTTPException(422,'Несколько собеседников доступны в групповом диалоге.')
        if body.kind=='direct':
            target=accounts.get(body.recipientId)
            if not directory.start(user,target,body.studentId): raise HTTPException(403,CLOSED if target and directory.closed(user,target,body.studentId) else 'Выберите доступного собеседника и ученика.')
            if body.requireAck or body.schoolwide or body.groupIds or body.roles: raise HTTPException(422,'Параметры объявления не относятся к личной переписке.')
            return [(target,[body.studentId] if body.studentId else [])]
        if user['role'] not in ['admin','teacher']: raise HTTPException(403,'Объявления публикуют администраторы и учителя.')
        if body.kind=='group' and (body.requireAck or body.schoolwide):raise HTTPException(422,'Выберите учебные группы; подтверждение ознакомления относится к объявлениям.')
        if body.recipientId or body.studentId: raise HTTPException(422,'Выберите аудиторию объявления.')
        if not body.roles or len(set(body.roles))!=len(body.roles): raise HTTPException(422,'Выберите роли получателей без повторов.')
        self.audiences.channels_for(user,directory,body.roles)
        if body.schoolwide:
            if user['role']!='admin': raise HTTPException(403,'Объявление всей школе доступно администратору.')
            if body.groupIds: raise HTTPException(422,'Выберите всю школу или отдельные группы.')
            pupils=set(directory.students)
        else:
            if not body.groupIds or not set(body.groupIds)<=directory.groups(user): raise HTTPException(403,'Выберите доступные учебные группы.')
            pupils=set().union(*(directory.members[g] for g in body.groupIds))
            if not set(body.roles)<=FAMILY: raise HTTPException(422,'Для учебной группы доступны ученики и родители.')
        selected=self.pick(accounts,user,directory,body.roles,pupils)
        if not selected: raise HTTPException(422,'В выбранной аудитории нет активных аккаунтов.')
        if body.kind=='group' and not 2<=len(selected)<=199:raise HTTPException(422,'В групповом диалоге нужно от 3 до 200 участников вместе с автором.')
        return selected

    @staticmethod
    def pick(accounts,user,directory,roles,pupils):
        """Accounts of these roles with a current assignment; a family account keeps only its pupils among `pupils`."""
        selected=[]
        for candidate in accounts.values():
            if candidate['id']==user['id'] or candidate['role'] not in roles or not directory.enabled(candidate): continue
            scopes=directory.pupils(candidate)&pupils if candidate['role'] in FAMILY else set()
            if candidate['role'] not in FAMILY or scopes: selected.append((candidate,sorted(scopes)))
        return selected

    def selection(self,db,body,user,directory):
        """(recipients, saved-group members out of reach now). Preview and send resolve alike, on the links and matrix of this moment."""
        if body.audience or body.savedGroupId:
            if body.kind!='announcement': raise HTTPException(422,'Аудитория выбирается только для объявления.')
            if not body.audience: raise HTTPException(422,'Выберите аудиторию объявления.')
            return self.audiences.select(db,body,user,directory)
        return self.recipients(body,user,directory,self.recipient_accounts(db,body,user,directory)),0

    def may_reply(self,thread,user,directory,accounts,live):
        """A reply needs a channel open from the replier to someone still in the conversation, or from its author to the replier."""
        if user['role']=='admin': return True
        peers=[u for u in live if u['id']!=user['id']]
        if not peers: return True
        author=accounts.get(thread['authorId'])
        return any(directory.start_about(user,p,thread['studentId']) for p in peers) or bool(author and author['id']!=user['id'] and directory.start_about(author,user,thread['studentId']))

    def unread(self,db,user,directory):
        """(threads with something new that the account may read, whether the count stopped at its limit of threads to examine)."""
        rows=db.execute('''SELECT t.*,m.read_id FROM message_threads t JOIN message_members m ON m.thread_id=t.id AND m.user_id=?
            WHERE t.workspace=? AND t.latest_id>m.read_id ORDER BY t.latest_id DESC LIMIT 100''',(user['id'],directory.workspace)).fetchall()
        accounts=self.accounts(db,thread_ids=[r[0] for r in rows if r[2] in ['direct','group']])
        count=0
        for row in rows:
            thread=self.thread(db,row[:11]);found=self.visible(db,thread,user,directory,accounts)
            if found and (thread['latestId'] if found[1] is None else found[1])>found[0][2]: count+=1
        return count,len(rows)==100

    @staticmethod
    def children(thread,member,own,names):
        """Pupils a conversation is about. A family account (own = its pupils) learns only the names of its own children."""
        ids=[thread['studentId']] if thread['studentId'] else sorted(json.loads(member[1])) if own is not None else []
        return [dict(id=i,name=names[i]) for i in ids if i in names and (own is None or i in own)]

    def previews(self,db,items):
        """Sender, date, text and attachment flag of the last post each account may read: a former participant sees no later one."""
        file_tables(db);ids=encoded([item['latestId'] for item in items])
        posts={r[0]:r for r in db.execute('SELECT id,author_name,created,body FROM message_posts WHERE id IN (SELECT value FROM json_each(?))',(ids,))}
        hidden={r[0] for r in db.execute('SELECT post_id FROM message_moderation WHERE hidden=1 AND post_id IN (SELECT value FROM json_each(?))',(ids,))}
        files={r[0] for r in db.execute("SELECT post_id FROM message_files WHERE status='ready' AND post_id IN (SELECT value FROM json_each(?))",(ids,))}
        for item in items:
            post=posts.get(item['latestId']);masked=item['latestId'] in hidden
            item.update(sender=post[1] if post else '',date=post[2] if post else item['created'],hasFiles=item['latestId'] in files and not masked,
                        preview='Сообщение скрыто администратором.' if masked else clip(post[3],140) if post else '')

    def class_teachers(self,user,directory):
        """For each pupil of a family account: the class teacher's account that may be written to now, by the class teacher on record."""
        classes={c.id:c for c in directory.state.classes}
        leaders={sid:classes[directory.students[sid].classId].classTeacherId for sid in directory.pupils(user) if directory.students[sid].classId in classes}
        leaders={sid:t for sid,t in leaders.items() if t}
        if not leaders: return []
        with self.access.lock,self.access.connect() as db:
            self.tables(db)
            teachers=[self.access.public(r) for r in db.execute("SELECT * FROM accounts WHERE active=1 AND role='teacher'")]
        return [dict(studentId=sid,id=u['id'],name=u['name']) for sid,t in sorted(leaders.items()) for u in teachers if t in user_ids(u,directory.workspace,'teacher') and directory.start(user,u,sid)]

    def register(self,app,Workspace):
        @app.get('/api/messages/options')
        def options(request:Request,workspace:Workspace='school'):
            user,directory=self.context(request,workspace)
            pupils=directory.reachable_students(user);mine=directory.groups(user);classes=directory.classes(user)
            result=dict(role=user['role'],userId=user['id'],students=[dict(id=s.id,name=s.name) for s in directory.students.values() if s.id in pupils],
                        groups=[dict(id=g.id,name=g.name) for g in directory.state.groups if g.id in mine],canAnnounce=user['role'] in ['admin','teacher'],
                        audiences=directory.audiences(user),classes=[dict(id=c.id,name=c.name) for c in directory.state.classes if c.id in classes] if user['role']=='teacher' else [],
                        publicReplies='feed_public_replies' in directory.channels)
            # The class teacher of each pupil, from the class record on every request: one click for the family, no searching.
            return result|dict(classTeachers=self.class_teachers(user,directory)) if user['role'] in FAMILY else result

        @app.get('/api/messages/contacts')
        def contacts(request:Request,workspace:Workspace='school',studentId:str|None=None,q:str=Query(default='',max_length=100),offset:int=Query(default=0,ge=0,le=100000)):
            user,directory=self.context(request,workspace)
            if studentId and studentId not in directory.reachable_students(user): raise HTTPException(403,'Ученик недоступен.')
            with self.access.lock,self.access.connect() as db:
                self.tables(db)
                items=[dict(id=u['id'],name=u['name'],role=u['role']) for u in self.accounts(db).values() if q.casefold() in u['name'].casefold() and directory.start(user,u,studentId)]
            items.sort(key=lambda u:(u['name'],u['id']))
            return dict(items=items[offset:offset+50],nextOffset=offset+50 if len(items)>offset+50 else None)

        @app.post('/api/messages/preview')
        def preview(body:NewThread,request:Request,workspace:Workspace='school'):
            user,directory=self.context(request,workspace)
            with self.access.lock,self.access.connect() as db:
                self.tables(db)
                selected,unavailable=self.selection(db,body,user,directory)
            return self.audiences.summary(body.kind,selected,unavailable)

        @app.post('/api/messages/threads')
        def create(body:NewThread,request:Request,workspace:Workspace='school'):
            user,directory=self.context(request,workspace)
            self.body_valid(body)
            if body.kind=='announcement' and not body.title: raise HTTPException(422,'Укажите тему объявления.')
            if body.replies!='off' and body.kind!='announcement': raise HTTPException(422,'Ответы настраиваются только под объявлением.')
            with self.access.lock,self.access.connect() as db:
                self.tables(db);db.execute('BEGIN IMMEDIATE');self.recheck(db,user,directory)
                prior,signature=self.replay(db,user,body.requestId,dict(workspace=workspace,body=body.signature()))
                if prior:
                    self.find(db,prior,user,directory,self.thread_accounts(db,prior,user))
                    return dict(id=prior)
                if body.replies=='all' and 'feed_public_replies' not in directory.channels: raise HTTPException(403,CLOSED)
                recipients,_=self.selection(db,body,user,directory)
                if body.kind=='announcement' and len(recipients)>self.audiences.limit(body.kind): raise HTTPException(422,'В аудитории больше допустимого числа получателей.')
                self.drafts.consume(db,user,workspace,body.draftId,body.draftVersion,None)
                self.rate(db,user)
                id=uuid4().hex;created=now().isoformat()
                db.execute('INSERT INTO message_threads VALUES (?,?,?,?,?,?,1,?,?,?,0)',(id,workspace,body.kind,body.title,user['id'],body.studentId,'open',int(body.requireAck),created))
                if body.replies!='off': self.feed.set_mode(db,id,body.replies)
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
                items=[];names={s.id:s.name for s in directory.state.students};own=directory.pupils(user) if user['role'] in FAMILY else None
                for row in rows[:100]:
                    thread=self.thread(db,row[:11]);found=self.visible(db,thread,user,directory,accounts)
                    if not found: continue
                    member,through=found;latest=thread['latestId'] if through is None else through
                    if unreadOnly and latest<=member[2]: continue
                    items.append(thread|dict(latestId=latest,unread=latest>member[2],acknowledged=member[3]==thread['version'],readOnly=through is not None,
                                             children=self.children(thread,member,own,names)))
                self.previews(db,items)
                # Only the first page tells the unread total; later pages are older rows the account has scrolled to.
                # A first page that holds every conversation of the account already contains the answer.
                if before is not None: total=(None,False)
                elif not q and len(rows)<=100: total=(sum(item['unread'] for item in items),False)
                else: total=self.unread(db,user,directory)
            return dict(items=items,nextBefore=rows[99][11] if len(rows)>100 else None,unreadCount=total[0],unreadMore=total[1])

        @app.get('/api/messages/unread')
        def unread_total(request:Request,workspace:Workspace='school'):
            user,directory=self.context(request,workspace)
            try:
                with closing(self.access.read_connect()) as db: count,more=self.unread(db,user,directory)
            except sqlite3.OperationalError: count,more=0,False  # no conversations stored yet
            return dict(count=count,more=more)

        @app.get('/api/messages/threads/{id}')
        def detail(id:str,request:Request,workspace:Workspace='school',before:int|None=Query(default=None,ge=1)):
            user,directory=self.context(request,workspace)
            with self.access.lock,self.access.connect() as db:
                self.tables(db);accounts=self.thread_accounts(db,id,user)
                thread,member,through=self.readable(db,id,user,directory,accounts)
                bound=thread['latestId'] if through is None else through
                posts=db.execute('SELECT id,author_id,author_name,created,body FROM message_posts WHERE thread_id=? AND id<=? AND (? IS NULL OR id<?) ORDER BY id DESC LIMIT 51',(id,bound,before,before)).fetchall()
                peers=[];live=[]
                # Read receipts of a former participant would reveal activity after their access ended.
                if through is None and (thread['authorId']==user['id'] or thread['kind'] in ['direct','group']):
                    for uid,read_id,ack in db.execute('SELECT user_id,read_id,ack_version FROM message_members WHERE thread_id=?',(id,)):
                        if uid!=user['id'] and (peer:=accounts.get(uid)) and self.member(db,thread,peer,directory,accounts):
                            live.append(peer);peers.append(dict(id=uid,name=peer['name'],read=read_id>=thread['latestId'],acknowledged=ack==thread['version']))
                attachments=self.files.for_posts(db,[r[0] for r in posts[:50]])
                hidden={r[0] for r in db.execute('SELECT post_id FROM message_moderation WHERE hidden=1 AND post_id IN (SELECT value FROM json_each(?))',(encoded([r[0] for r in posts[:50]]),))}
                return thread|self.feed.detail(db,thread,user,directory,through)|dict(userId=user['id'],latestId=bound,readOnly=through is not None,canReply=through is None and thread['kind'] in ['direct','group'] and thread['status']=='open' and self.may_reply(thread,user,directory,accounts,live),canManage=through is None and thread['managerId']==user['id'],
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
                live=[u for u in accounts.values() if self.member(db,thread,u,directory,accounts)]
                if not self.may_reply(thread,user,directory,accounts,live): raise HTTPException(403,CLOSED)
                self.drafts.consume(db,user,workspace,body.draftId,body.draftVersion,id)
                post=self.post(db,thread,user,body.text)
                self.files.bind(db,body.fileIds,user,workspace,body.draftId,id,post)
                db.execute('UPDATE message_members SET read_id=? WHERE thread_id=? AND user_id=?',(post,id,user['id']))
                db.execute('INSERT INTO message_requests VALUES (?,?,?,?)',(user['id'],body.requestId,signature,id))
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
                if through is not None and body.throughId>through or not db.execute('SELECT 1 FROM message_posts WHERE thread_id=? AND id=? AND reply_to IS NULL',(id,body.throughId)).fetchone(): raise HTTPException(422,'Укажите сообщение из этой переписки.')
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
