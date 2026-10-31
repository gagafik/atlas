"""Recipient groups of an announcement: ready-made audiences and the sender's own saved groups.

Every audience is resolved again on the server for each preview and each send, from the current school data,
account permissions and channel matrix. A saved group stores only account ids and never widens access."""
import json

from fastapi import HTTPException, Path, Query, Request
from pydantic import Field

from .access import now
from .class_staff import led
from .message_channels import CLOSED
from .messages import FAMILY
from .models import Model
from .notifications import encoded

ANNOUNCEMENT_LIMIT = 5000
GROUP_LIMIT = 199  # a group conversation holds 200 accounts with its author
MAX_SAVED = 20  # saved groups per owner and workspace
MAX_SAVED_MEMBERS = 500
UUID_PATTERN = r'^[a-f0-9-]{36}$'
STAFF_SIDE = ['staff', 'editor', 'viewer']


def limit_for(kind):
    return ANNOUNCEMENT_LIMIT if kind == 'announcement' else GROUP_LIMIT if kind == 'group' else 1


class SavedGroup(Model):
    version: int = Field(ge=0)
    name: str = Field(min_length=1, max_length=120)
    userIds: list[str] = Field(min_length=1, max_length=MAX_SAVED_MEMBERS)


class MessageAudiences:
    def __init__(self, messages):
        self.messages, self.access = messages, messages.access

    def tables(self, db):
        db.execute('''CREATE TABLE IF NOT EXISTS message_saved_groups (
            id TEXT,workspace TEXT,owner_id TEXT,name TEXT,version INTEGER,members TEXT,created TEXT,updated TEXT,
            PRIMARY KEY(workspace,owner_id,id))''')

    @staticmethod
    def limit(kind):
        return limit_for(kind)

    @staticmethod
    def channels_for(user, directory, roles):
        """A teacher writes to pupils or to parents only through the channel of that role."""
        if user['role'] != 'teacher': return
        for role in FAMILY & set(roles):
            if 'teacher_' + ('students' if role == 'student' else 'parents') not in directory.channels: raise HTTPException(403, CLOSED)

    def summary(self, kind, selected, unavailable):
        limit = limit_for(kind)
        people = sorted(selected, key=lambda r: (r[0]['name'], r[0]['id']))[:20]
        return dict(count=len(selected), byRole={role: sum(u['role'] == role for u, _ in selected) for role in {u['role'] for u, _ in selected}},
                    limit=limit, overLimit=len(selected) > limit, sample=[dict(name=u['name'], role=u['role']) for u, _ in people],
                    more=len(selected) - len(people), unavailable=unavailable)

    def select(self, db, body, user, directory):
        """(recipients, saved-group members that cannot be reached now) for an announcement audience."""
        m = self.messages
        if user['role'] not in ['admin', 'teacher']: raise HTTPException(403, 'Объявления публикуют администраторы и учителя.')
        if body.recipientId or body.recipientIds or body.studentId or body.schoolwide: raise HTTPException(422, 'Выберите аудиторию объявления.')
        if body.audience not in directory.audiences(user): raise HTTPException(403, 'Эта аудитория вам недоступна.')
        if len(set(body.roles)) != len(body.roles): raise HTTPException(422, 'Выберите роли получателей без повторов.')
        preset = body.audience
        if preset == 'saved':
            if body.roles or body.groupIds: raise HTTPException(422, 'Для сохранённой группы роли и учебные группы не выбираются.')
            return self.saved(db, body, user, directory)
        if body.savedGroupId: raise HTTPException(422, 'Сохранённая группа относится к аудитории «Сохранённая группа».')
        if preset in ['teachers', 'class_teachers', 'staff']:
            if body.roles or body.groupIds: raise HTTPException(422, 'Для этой аудитории роли и учебные группы не выбираются.')
            roles = ['teacher'] if preset != 'staff' else STAFF_SIDE
            accounts = m.candidates(db, directory, user, roles, None)
            selected = [(c, s) for c, s in m.pick(accounts, user, directory, roles, set())
                        if preset != 'class_teachers' or led(c, directory, directory.workspace)]
        else:
            if not body.roles: raise HTTPException(422, 'Выберите роли получателей.')
            self.channels_for(user, directory, body.roles)
            if preset == 'school':
                if body.groupIds: raise HTTPException(422, 'Выберите всю школу или отдельные группы.')
                if user['role'] != 'admin' and not set(body.roles) <= FAMILY | {'teacher'}: raise HTTPException(403, 'Эти роли недоступны для объявления всей школе.')
                pupils = set(directory.students)
                narrow = None
            else:
                if not set(body.roles) <= FAMILY: raise HTTPException(422, 'Для классов и курсов доступны ученики и родители.')
                if preset == 'my_classes':
                    if body.groupIds: raise HTTPException(422, 'Для классов группы не выбираются.')
                    classes = directory.classes(user)
                    pupils = {id for id, s in directory.students.items() if s.classId in classes}
                else:
                    if body.groupIds and not set(body.groupIds) <= directory.groups(user): raise HTTPException(403, 'Выберите доступные учебные группы.')
                    pupils = set().union(*(directory.members.get(g, set()) for g in (body.groupIds or directory.groups(user))))
                narrow = pupils
            accounts = m.candidates(db, directory, user, body.roles, narrow)
            selected = m.pick(accounts, user, directory, body.roles, pupils)
        if not selected: raise HTTPException(422, 'В выбранной аудитории нет активных аккаунтов.')
        return selected, 0

    def saved(self, db, body, user, directory):
        if not body.savedGroupId: raise HTTPException(422, 'Выберите сохранённую группу.')
        row = db.execute('SELECT members FROM message_saved_groups WHERE workspace=? AND owner_id=? AND id=?', (directory.workspace, user['id'], body.savedGroupId)).fetchone()
        if not row: raise HTTPException(422, 'Сохранённая группа недоступна.')
        ids = json.loads(row[0]); accounts = self.messages.accounts(db, user_ids=ids)
        selected = [(c, scopes) for id in ids if (c := accounts.get(id)) and (scopes := directory.reach(user, c)) is not None]
        if not selected: raise HTTPException(422, 'В выбранной аудитории нет активных аккаунтов.')
        return selected, len(ids) - len(selected)

    def register(self, app, Workspace):
        def owner(request, workspace):
            user, directory = self.messages.context(request, workspace)
            if user['role'] not in ['admin', 'teacher']: raise HTTPException(403, 'Группы получателей доступны администраторам и учителям.')
            return user, directory

        def people(directory, user, ids, accounts):
            return [dict(id=c['id'], name=c['name'], role=c['role']) for id in ids if (c := accounts.get(id)) and directory.reach(user, c) is not None]

        @app.get('/api/messages/saved-groups')
        def listing(request: Request, workspace: Workspace='school'):
            user, directory = owner(request, workspace)
            with self.access.lock, self.access.connect() as db:
                self.messages.tables(db)
                rows = db.execute('SELECT id,name,version,members FROM message_saved_groups WHERE workspace=? AND owner_id=? ORDER BY name,id', (workspace, user['id'])).fetchall()
                members = [json.loads(r[3]) for r in rows]
                accounts = self.messages.accounts(db, user_ids=sorted({id for ids in members for id in ids})) if rows else {}
            return dict(items=[dict(id=r[0], name=r[1], version=r[2], total=len(ids), available=len(people(directory, user, ids, accounts))) for r, ids in zip(rows, members)], limit=MAX_SAVED)

        # Declared before the {id} route: «candidates» is not a group id.
        @app.get('/api/messages/saved-groups/candidates')
        def candidates(request: Request, workspace: Workspace='school', q: str=Query(default='', max_length=100), offset: int=Query(default=0, ge=0, le=100000)):
            user, directory = owner(request, workspace)
            with self.access.lock, self.access.connect() as db:
                self.messages.tables(db)
                db.create_function('casefold', 1, lambda value: value.casefold(), deterministic=True)
                clause, params = 'active=1 AND instr(casefold(name),?)>0', [q.casefold()]
                if user['role'] != 'admin':
                    more, args = self.messages.narrow(workspace, directory.reachable_students(user))
                    clause += " AND (role IN ('admin','teacher','editor','viewer','staff') OR (" + more + '))'; params.extend(args)
                rows = db.execute('SELECT * FROM accounts WHERE ' + clause + ' ORDER BY name,id LIMIT 51 OFFSET ?', [*params, offset]).fetchall()
                found = {r[0]: self.access.public(r) for r in rows[:50]}
            return dict(items=people(directory, user, found, found), nextOffset=offset + 50 if len(rows) > 50 else None)

        @app.get('/api/messages/saved-groups/{id}')
        def detail(request: Request, id: str=Path(pattern=UUID_PATTERN), workspace: Workspace='school'):
            user, directory = owner(request, workspace)
            with self.access.lock, self.access.connect() as db:
                self.messages.tables(db)
                row = db.execute('SELECT name,version,members FROM message_saved_groups WHERE workspace=? AND owner_id=? AND id=?', (workspace, user['id'], id)).fetchone()
                if not row: raise HTTPException(404, 'Группа не найдена.')
                ids = json.loads(row[2]); found = people(directory, user, ids, self.messages.accounts(db, user_ids=ids))
            return dict(id=id, name=row[0], version=row[1], people=found, unavailable=len(ids) - len(found))

        @app.put('/api/messages/saved-groups/{id}')
        def save(body: SavedGroup, request: Request, id: str=Path(pattern=UUID_PATTERN), workspace: Workspace='school'):
            user, directory = owner(request, workspace)
            if len(set(body.userIds)) != len(body.userIds) or any(len(i) > 100 for i in body.userIds): raise HTTPException(422, 'Выберите разных получателей.')
            with self.access.lock, self.access.connect() as db:
                self.messages.tables(db); db.execute('BEGIN IMMEDIATE'); self.messages.recheck(db, user, directory)
                row = db.execute('SELECT version,name,members FROM message_saved_groups WHERE workspace=? AND owner_id=? AND id=?', (workspace, user['id'], id)).fetchone()
                members = encoded(body.userIds)
                if row and row[0] == body.version + 1 and row[1] == body.name and row[2] == members: return dict(id=id, version=row[0])
                if row and row[0] != body.version or not row and body.version != 0:
                    raise HTTPException(409, 'Группа изменена на другом устройстве. Обновите список групп.')
                if not row and db.execute('SELECT count(*) FROM message_saved_groups WHERE workspace=? AND owner_id=?', (workspace, user['id'])).fetchone()[0] >= MAX_SAVED:
                    raise HTTPException(413, f'Можно хранить до {MAX_SAVED} групп получателей. Удалите ненужные.')
                accounts = self.messages.accounts(db, user_ids=body.userIds)
                if len(accounts) != len(body.userIds) or any(directory.reach(user, c) is None for c in accounts.values()):
                    raise HTTPException(403, 'Один из выбранных получателей вам недоступен.')
                version, stamp = body.version + 1, now().isoformat()
                db.execute('INSERT INTO message_saved_groups VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(workspace,owner_id,id) DO UPDATE SET name=excluded.name,version=excluded.version,members=excluded.members,updated=excluded.updated',
                           (id, workspace, user['id'], body.name, version, members, stamp, stamp))
                self.access.record(db, 'message.saved_group_saved', workspace, detail=id)
            return dict(id=id, version=version)

        @app.delete('/api/messages/saved-groups/{id}')
        def remove(request: Request, id: str=Path(pattern=UUID_PATTERN), version: int=Query(ge=1), workspace: Workspace='school'):
            user, directory = owner(request, workspace)
            with self.access.lock, self.access.connect() as db:
                self.messages.tables(db); db.execute('BEGIN IMMEDIATE'); self.messages.recheck(db, user, directory)
                row = db.execute('SELECT version FROM message_saved_groups WHERE workspace=? AND owner_id=? AND id=?', (workspace, user['id'], id)).fetchone()
                if not row: raise HTTPException(404, 'Группа не найдена.')
                if row[0] != version: raise HTTPException(409, 'Группа изменена. Обновите список перед удалением.')
                db.execute('DELETE FROM message_saved_groups WHERE workspace=? AND owner_id=? AND id=?', (workspace, user['id'], id))
                self.access.record(db, 'message.saved_group_deleted', workspace, detail=id)
            return dict(ok=True)
