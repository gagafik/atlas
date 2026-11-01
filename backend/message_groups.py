"""Explicit additions and management handoffs within a group's fixed school scope."""
import json
from fastapi import HTTPException, Query, Request
from pydantic import Field

from .models import Model
from .message_channels import CLOSED
from .messages import FAMILY, STAFF
from .notifications import encoded

MAX_MEMBERS = 200


class Add(Model):
    requestId: str = Field(pattern=r'^[a-f0-9-]{36}$')
    version: int = Field(ge=1)
    userIds: list[str] = Field(min_length=1, max_length=199)
    shareHistory: bool = False


class Transfer(Model):
    requestId: str = Field(pattern=r'^[a-f0-9-]{36}$')
    version: int = Field(ge=1)
    userId: str = Field(min_length=1, max_length=100)


class MessageGroups:
    def __init__(self, messages):
        self.messages = messages
        self.access = messages.access

    def scope(self, db, thread):
        row = db.execute('SELECT kind,group_ids FROM message_group_scopes WHERE thread_id=?', (thread['id'],)).fetchone()
        return (row[0], set(json.loads(row[1]))) if row else None

    def pupils(self, thread, directory, scope):
        if not scope: return set()
        if scope[0] == 'contacts': return {thread['studentId']} if thread['studentId'] else set()
        return set().union(*(directory.members.get(g, set()) for g in scope[1]))

    def family_roles(self, db, thread, accounts, joining=()):
        """Roles of the family accounts taking part in the conversation, with those about to join."""
        ids = [r[0] for r in db.execute('''SELECT user_id FROM message_members m WHERE thread_id=?
            AND NOT EXISTS(SELECT 1 FROM message_exclusions x WHERE x.thread_id=m.thread_id AND x.user_id=m.user_id)''', (thread['id'],))]
        return {a['role'] for uid in ids if (a := accounts.get(uid)) and a['role'] in FAMILY} | {a['role'] for a in joining if a and a['role'] in FAMILY}

    def eligible(self, thread, user, directory, scope, *, manager=False, family=None):
        if not scope or not directory.enabled(user): return False
        if scope[0] == 'contacts':
            if not thread['studentId']: return user['role'] in STAFF
            return user['role'] in FAMILY | {'admin', 'teacher'} and directory.related(user, thread['studentId'], family)
        if not scope[1] or not scope[1] <= directory.members.keys(): return False
        if user['role'] in {'teacher', 'admin'}: return scope[1] <= directory.groups(user)
        return not manager and user['role'] in FAMILY and bool(directory.pupils(user) & self.pupils(thread, directory, scope))

    @staticmethod
    def may_add(thread, user, target, directory):
        """The manager adds only people they may write to, or the other family of the same pupil, as before the channel matrix.
        A teacher brings in a colleague only if that colleague reaches the pupil too: the any-teacher channels let a family write to any teacher, they do not let a teacher pull one in."""
        if user['role'] == 'admin': return True
        if user['role'] == 'teacher' and target['role'] == 'teacher' and thread['studentId'] and thread['studentId'] not in directory.reachable_students(target): return False
        if directory.start_about(user, target, thread['studentId']): return True
        return user['role'] in FAMILY and target['role'] in FAMILY and thread['studentId'] in directory.pupils(user) & directory.pupils(target)

    def current(self, db, id, user, directory, *, manage=True):
        accounts = self.messages.thread_accounts(db, id, user)
        thread, _ = self.messages.find(db, id, user, directory, accounts)
        if thread['kind'] != 'group': raise HTTPException(422, 'Участники меняются только в групповом обсуждении.')
        if manage and thread['managerId'] != user['id']: raise HTTPException(403, 'Добавлять участников и передавать управление может управляющий обсуждением.')
        return thread, accounts, self.scope(db, thread)

    def event(self, db, thread, user, text, action, signature, request_id, directory):
        post = self.messages.post(db, thread, user, text)
        db.execute('UPDATE message_threads SET version=version+1 WHERE id=?', (thread['id'],))
        db.execute('INSERT INTO message_requests VALUES (?,?,?,?)', (user['id'], request_id, signature, thread['id']))
        accounts = self.messages.thread_accounts(db, thread['id'], user)
        live = [u for u in accounts.values() if self.messages.member(db, thread, u, directory, accounts)]
        self.messages.advance(db, thread['id'], post, live)
        self.messages.notifications.deliver(db, [u for u in live if u['id'] != user['id']], directory.workspace, thread['version']+1, 'message.received',
                                                topic=f"message:{directory.workspace}:{thread['id']}", target=self.messages.notice(db, thread, user, directory))
        self.access.record(db, action, directory.workspace, detail=thread['id'])

    def register(self, app, Workspace):
        @app.get('/api/messages/threads/{id}/management')
        def roster(id: str, request: Request, workspace: Workspace='school'):
            user, directory = self.messages.context(request, workspace)
            with self.access.lock, self.access.connect() as db:
                self.messages.tables(db)
                thread, accounts, scope = self.current(db, id, user, directory)
                family = lambda: self.family_roles(db, thread, accounts)
                rows = db.execute('''SELECT m.user_id,a.name,a.role FROM message_members m LEFT JOIN accounts a ON a.id=m.user_id
                    WHERE m.thread_id=? AND NOT EXISTS(SELECT 1 FROM message_exclusions x WHERE x.thread_id=m.thread_id AND x.user_id=m.user_id)
                    ORDER BY a.name,m.user_id''', (id,)).fetchall()
                members = []
                for uid, name, role in rows:
                    account = accounts.get(uid)
                    found = self.messages.visible(db, thread, account, directory, accounts) if account else None
                    accessible = bool(found and found[1] is None)
                    # history: no live link, but the member still reads what was published while linked.
                    members.append(dict(id=uid, name=name or 'Недоступный аккаунт', role=role, accessible=accessible, history=bool(found) and not accessible,
                                        canManage=accessible and self.eligible(thread, account, directory, scope, manager=True, family=family)))
                return dict(version=thread['version'], managerId=thread['managerId'], members=members, limit=MAX_MEMBERS)

        @app.get('/api/messages/threads/{id}/candidates')
        def candidates(id: str, request: Request, workspace: Workspace='school', q: str=Query(default='',max_length=100), offset: int=Query(default=0,ge=0,le=100000)):
            user, directory = self.messages.context(request, workspace)
            with self.access.lock, self.access.connect() as db:
                self.messages.tables(db)
                thread, members, scope = self.current(db, id, user, directory)
                family = lambda: self.family_roles(db, thread, members)
                db.create_function('casefold', 1, lambda value:value.casefold(), deterministic=True)
                params = [id, q.casefold()]
                clause = '''active=1 AND NOT EXISTS(SELECT 1 FROM message_members m WHERE m.thread_id=? AND m.user_id=accounts.id
                    AND NOT EXISTS(SELECT 1 FROM message_exclusions x WHERE x.thread_id=m.thread_id AND x.user_id=m.user_id))
                    AND instr(casefold(name),?)>0'''
                if scope[0] == 'contacts' and not thread['studentId']:
                    clause += " AND role IN ('admin','teacher','editor','viewer','staff')"
                else:
                    clause += ''' AND (role IN ('admin','teacher') OR (role IN ('parent','student') AND EXISTS (
                        SELECT 1 FROM json_each(accounts.permissions,'$.grants') grant_row JOIN json_each(grant_row.value,'$.entityIds') entity
                        WHERE json_extract(grant_row.value,'$.workspace')=? AND json_extract(grant_row.value,'$.view')='student'
                        AND entity.value IN (SELECT value FROM json_each(?)))))'''
                    pupils = self.pupils(thread, directory, scope)
                    # Families of the same class belong to a family's group while the channel between them is open.
                    if scope[0] == 'contacts' and directory.peer(user, thread['studentId']):
                        pupils = {p for p, s in directory.students.items() if s.classId == directory.students[thread['studentId']].classId}
                    params.extend([workspace, encoded(sorted(pupils))])
                rows = db.execute('SELECT * FROM accounts WHERE '+clause+' ORDER BY name,id LIMIT 101 OFFSET ?', [*params, offset]).fetchall()
                items = []
                for row in rows[:100]:
                    candidate = self.access.public(row)
                    if self.eligible(thread, candidate, directory, scope, family=family) and self.may_add(thread, user, candidate, directory):
                        items.append(dict(id=candidate['id'], name=candidate['name'], role=candidate['role']))
                return dict(items=items, nextOffset=offset+100 if len(rows)>100 else None, version=thread['version'])

        @app.post('/api/messages/threads/{id}/participants/add')
        def add(id: str, body: Add, request: Request, workspace: Workspace='school'):
            user, directory = self.messages.context(request, workspace)
            if not body.shareHistory: raise HTTPException(422, 'Подтвердите доступ новых участников ко всей истории и файлам.')
            if len(body.userIds) != len(set(body.userIds)) or any(len(uid)>100 for uid in body.userIds): raise HTTPException(422, 'Выберите разных участников.')
            with self.access.lock, self.access.connect() as db:
                self.messages.tables(db); db.execute('BEGIN IMMEDIATE'); self.messages.recheck(db, user, directory)
                thread, members, scope = self.current(db, id, user, directory, manage=False)
                prior, signature = self.messages.replay(db, user, body.requestId, dict(workspace=workspace,id=id,action='add',body=body.model_dump()))
                if prior: return dict(ok=True)
                if thread['managerId'] != user['id']: raise HTTPException(403, 'Добавлять участников может управляющий обсуждением.')
                if thread['version'] != body.version: raise HTTPException(409, 'Обсуждение изменилось. Обновите его и проверьте участников.')
                current = {r[0] for r in db.execute('''SELECT user_id FROM message_members m WHERE thread_id=?
                    AND NOT EXISTS(SELECT 1 FROM message_exclusions x WHERE x.thread_id=m.thread_id AND x.user_id=m.user_id)''', (id,))}
                if current & set(body.userIds): raise HTTPException(409, 'Один из выбранных аккаунтов уже участвует в обсуждении.')
                if len(current)+len(body.userIds) > MAX_MEMBERS: raise HTTPException(422, 'В обсуждении может быть не больше 200 участников. Сначала исключите ненужные аккаунты.')
                accounts = self.messages.accounts(db, user_ids=body.userIds)
                family = lambda: self.family_roles(db, thread, members, accounts.values())
                for uid in body.userIds:
                    target = accounts.get(uid)
                    if not self.eligible(thread, target, directory, scope, family=family): raise HTTPException(403, 'Один из выбранных участников больше не имеет подходящей учебной связи.')
                    if not self.may_add(thread, user, target, directory): raise HTTPException(403, CLOSED)
                for uid in body.userIds:
                    account = accounts[uid]
                    scopes = sorted(directory.pupils(account) & self.pupils(thread, directory, scope)) if account['role'] in FAMILY else []
                    db.execute('INSERT INTO message_members (thread_id,user_id,role,scope_students) VALUES (?,?,?,?) ON CONFLICT(thread_id,user_id) DO UPDATE SET role=excluded.role,scope_students=excluded.scope_students,read_id=0,ack_version=NULL',
                               (id, uid, account['role'], encoded(scopes)))
                    db.execute('DELETE FROM message_exclusions WHERE thread_id=? AND user_id=?', (id,uid))
                self.event(db, thread, user, 'Добавлены участники: '+', '.join(accounts[uid]['name'] for uid in body.userIds)+'. Им доступна вся история обсуждения.', 'message.participants_added', signature, body.requestId, directory)
            return dict(ok=True)

        @app.post('/api/messages/threads/{id}/manager')
        def transfer(id: str, body: Transfer, request: Request, workspace: Workspace='school'):
            user, directory = self.messages.context(request, workspace)
            with self.access.lock, self.access.connect() as db:
                self.messages.tables(db); db.execute('BEGIN IMMEDIATE'); self.messages.recheck(db, user, directory)
                thread, accounts, scope = self.current(db, id, user, directory, manage=False)
                prior, signature = self.messages.replay(db, user, body.requestId, dict(workspace=workspace,id=id,action='transfer',body=body.model_dump()))
                if prior: return dict(ok=True)
                if thread['managerId'] != user['id']: raise HTTPException(403, 'Передать управление может только текущий управляющий обсуждением.')
                if thread['version'] != body.version: raise HTTPException(409, 'Обсуждение изменилось. Обновите его перед передачей управления.')
                target = accounts.get(body.userId)
                if body.userId == user['id'] or not target or not self.messages.member(db, thread, target, directory, accounts) or not self.eligible(thread, target, directory, scope, manager=True, family=lambda: self.family_roles(db, thread, accounts)):
                    raise HTTPException(422, 'Выберите действующего участника, который может управлять этой учебной аудиторией.')
                db.execute('INSERT INTO message_group_managers VALUES (?,?) ON CONFLICT(thread_id) DO UPDATE SET manager_id=excluded.manager_id', (id, body.userId))
                thread['managerId'] = body.userId
                self.event(db, thread, user, 'Управление обсуждением передано: '+target['name']+'.', 'message.manager_transferred', signature, body.requestId, directory)
            return dict(ok=True)
