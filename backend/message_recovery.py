"""Restore an unavailable group manager without granting an admin conversation access."""
from fastapi import HTTPException, Query, Request
from pydantic import Field
import json

from .models import Model


class Recover(Model):
    requestId: str = Field(pattern=r'^[a-f0-9-]{36}$')
    version: int = Field(ge=1)
    userId: str = Field(min_length=1,max_length=100)
    reason: str = Field(min_length=1,max_length=1000)


class MessageRecovery:
    def __init__(self, messages):
        self.messages, self.access = messages, messages.access

    def eligible(self, db, thread, account, directory, accounts):
        groups = self.messages.groups
        if not groups.eligible(thread,account,directory,groups.scope(db,thread),manager=True,family=lambda: groups.family_roles(db,thread,accounts)): return False
        row = db.execute('''SELECT role,scope_students FROM message_members m WHERE thread_id=? AND user_id=?
            AND NOT EXISTS(SELECT 1 FROM message_exclusions x WHERE x.thread_id=m.thread_id AND x.user_id=m.user_id)''',(thread['id'],account['id'])).fetchone()
        if not row or row[0]!=account['role']: return False
        if account['role'] not in {'parent','student'}: return True
        scope = self.messages.groups.scope(db,thread)
        # A parent of the same class belongs to a family's group without owning its pupil.
        return bool(set(json.loads(row[1])) & directory.pupils(account) & self.messages.groups.pupils(thread,directory,scope)) or bool(scope and scope[0]=='contacts' and directory.peer(account,thread['studentId']))

    def candidates(self, db, thread, directory, accounts):
        if self.eligible(db,thread,accounts.get(thread['managerId']),directory,accounts): return None
        ids = {r[0] for r in db.execute('SELECT user_id FROM message_members WHERE thread_id=?',(thread['id'],))}
        return [dict(id=u['id'],name=u['name'],role=u['role']) for uid in ids if (u:=accounts.get(uid)) and self.eligible(db,thread,u,directory,accounts)]

    def register(self, app, Workspace):
        @app.get('/api/messages/recovery')
        def unavailable(request: Request, workspace: Workspace='school', offset: int=Query(default=0,ge=0,le=100000)):
            user, directory = self.messages.context(request,workspace); self.messages.moderation.admin(user)
            with self.access.lock, self.access.connect() as db:
                self.messages.tables(db)
                rows = db.execute("SELECT * FROM message_threads WHERE workspace=? AND kind='group' ORDER BY created,id LIMIT 51 OFFSET ?",(workspace,offset)).fetchall()
                accounts = self.messages.accounts(db,thread_ids=[r[0] for r in rows[:50]])
                items = []
                for row in rows[:50]:
                    thread = self.messages.thread(db,row)
                    candidates = self.candidates(db,thread,directory,accounts)
                    if candidates is not None:
                        items.append(dict(id=thread['id'],title=thread['title'],version=thread['version'],status=thread['status'],candidates=candidates))
                return dict(items=items,nextOffset=offset+50 if len(rows)>50 else None)

        @app.post('/api/messages/recovery/{id}')
        def recover(id: str, body: Recover, request: Request, workspace: Workspace='school'):
            user, directory = self.messages.context(request,workspace); self.messages.moderation.admin(user)
            if not body.reason.strip(): raise HTTPException(422,'Укажите причину восстановления управления.')
            with self.access.lock, self.access.connect() as db:
                self.messages.tables(db); db.execute('BEGIN IMMEDIATE'); self.messages.recheck(db,user,directory)
                row = db.execute("SELECT * FROM message_threads WHERE id=? AND workspace=? AND kind='group'",(id,workspace)).fetchone()
                if not row: raise HTTPException(404,'Обсуждение недоступно.')
                thread = self.messages.thread(db,row)
                prior, signature = self.messages.replay(db,user,body.requestId,dict(workspace=workspace,id=id,action='recover',body=body.model_dump()))
                if prior: return dict(ok=True)
                if thread['version']!=body.version: raise HTTPException(409,'Обсуждение изменилось. Обновите список.')
                accounts = self.messages.accounts(db,thread_ids=[id])
                candidates = self.candidates(db,thread,directory,accounts)
                if candidates is None: raise HTTPException(409,'Текущий управляющий имеет доступ. Он может передать управление самостоятельно.')
                if body.userId not in {c['id'] for c in candidates}: raise HTTPException(422,'Выберите действующего участника с подходящей учебной связью.')
                db.execute('INSERT INTO message_group_managers VALUES (?,?) ON CONFLICT(thread_id) DO UPDATE SET manager_id=excluded.manager_id',(id,body.userId))
                thread['managerId'] = body.userId
                post = self.messages.post(db,thread,user,'Администратор восстановил управление обсуждением. Управляющий: '+accounts[body.userId]['name']+'. Причина: '+body.reason,limit=False)
                db.execute('UPDATE message_threads SET version=version+1 WHERE id=?',(id,))
                db.execute('INSERT INTO message_requests VALUES (?,?,?,?)',(user['id'],body.requestId,signature,id))
                live = [u for u in accounts.values() if self.messages.member(db,thread,u,directory,accounts)]
                self.messages.advance(db,id,post,live)
                self.messages.notifications.deliver(db,live,workspace,thread['version']+1,'message.received',topic=f'message:{workspace}:{id}',target=self.messages.notice(thread,user,directory))
                self.access.record(db,'message.manager_recovered',workspace,detail=id+' · '+body.userId)
            return dict(ok=True)
