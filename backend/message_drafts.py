"""Account-private, versioned message drafts. Sending consumes a draft atomically."""
import json
from typing import Literal

from fastapi import HTTPException, Path, Query, Request
from pydantic import ConfigDict, Field, field_validator

from .access import now
from .messages import NewThread
from .models import Model
from .notifications import encoded
from . import message_files

UUID_PATTERN = r'^[a-f0-9-]{36}$'
MAX_DRAFTS = 50


class Content(NewThread):
    # Drafts preserve incomplete text, including whitespace and missing audience.
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=False)
    title: str = Field(default='', max_length=160)
    groupMode: Literal['contacts','audience'] = 'contacts'

    @field_validator('recipientId', 'studentId', 'recipientIds', 'groupIds', 'fileIds')
    @classmethod
    def bounded_ids(cls, value):
        ids = value if isinstance(value, list) else [value] if value else []
        if any(len(id) > 100 for id in ids):
            raise ValueError('Идентификатор слишком длинный.')
        return value


class Save(Model):
    version: int = Field(ge=0)
    threadId: str | None = Field(default=None, max_length=100)
    content: Content


def tables(db):
    db.execute('''CREATE TABLE IF NOT EXISTS message_drafts (
        workspace TEXT, owner_id TEXT, id TEXT, version INTEGER, status TEXT,
        target_thread TEXT, content TEXT, updated TEXT,
        PRIMARY KEY(workspace,owner_id,id))''')
    db.execute('CREATE INDEX IF NOT EXISTS message_drafts_owner ON message_drafts(workspace,owner_id,status,updated)')


def protects_file(db, id):
    tables(db)
    return db.execute('''SELECT 1 FROM message_files f JOIN message_drafts d
        ON d.workspace=f.workspace AND d.owner_id=f.owner_id AND d.id=f.draft_id
        WHERE f.id=? AND d.status='active'
        AND EXISTS(SELECT 1 FROM json_each(d.content,'$.fileIds') WHERE value=f.id)''', (id,)).fetchone() is not None


class MessageDrafts:
    def __init__(self, messages):
        self.messages = messages
        self.access = messages.access

    def row(self, db, user, workspace, id):
        return db.execute('SELECT version,status,target_thread,content,updated FROM message_drafts WHERE workspace=? AND owner_id=? AND id=?',
                          (workspace, user['id'], id)).fetchone()

    def consume(self, db, user, workspace, id, version, thread):
        tables(db)
        row = self.row(db, user, workspace, id) if id else None
        if not row and version is None:
            return  # Existing clients may send without saving a draft first.
        if not row or row[1] != 'active' or row[0] != version or row[2] != thread:
            raise HTTPException(409, 'Черновик изменён, удалён или уже отправлен с другого устройства. Откройте его заново из списка черновиков.')
        # A tombstone prevents a delayed save from recreating a sent/deleted draft.
        db.execute("UPDATE message_drafts SET status='sent',version=version+1,content='{}',updated=? WHERE workspace=? AND owner_id=? AND id=?",
                   (now().isoformat(), workspace, user['id'], id))

    def register(self, app, Workspace):
        @app.get('/api/messages/drafts')
        def listing(request: Request, workspace: Workspace = 'school'):
            user, directory = self.messages.context(request, workspace)
            with self.access.lock, self.access.connect() as db:
                tables(db)
                rows = db.execute("SELECT id,version,target_thread,content,updated FROM message_drafts WHERE workspace=? AND owner_id=? AND status='active' ORDER BY updated DESC,id LIMIT ?",
                                  (workspace, user['id'], MAX_DRAFTS)).fetchall()
                return dict(items=[dict(id=r[0], version=r[1], threadId=r[2], title=json.loads(r[3])['title'],
                                        kind=json.loads(r[3])['kind'], updated=r[4]) for r in rows], limit=MAX_DRAFTS)

        @app.get('/api/messages/drafts/{id}')
        def detail(request: Request, id: str = Path(pattern=UUID_PATTERN), workspace: Workspace = 'school'):
            user, directory = self.messages.context(request, workspace)
            with self.access.lock, self.access.connect() as db:
                tables(db); message_files.tables(db)
                row = self.row(db, user, workspace, id)
                if not row or row[1] != 'active':
                    raise HTTPException(404, 'Черновик не найден.')
                content = json.loads(row[3])
                files = db.execute("SELECT * FROM message_files WHERE workspace=? AND owner_id=? AND draft_id=? AND status='ready' AND post_id IS NULL AND id IN (SELECT value FROM json_each(?))",
                                   (workspace, user['id'], id, encoded(content['fileIds']))).fetchall()
                # Rehydrate contact labels only while the current relationship permits it.
                accounts = self.messages.accounts(db, user_ids=[content.get('recipientId'), *content['recipientIds']])
                people = [dict(id=p['id'], name=p['name'], role=p['role']) for p in accounts.values()
                          if directory.start(user, p, content.get('studentId'))]
                return dict(id=id, version=row[0], threadId=row[2], content=content, updated=row[4],
                            files=[message_files.meta(f) for f in files], people=people)

        @app.put('/api/messages/drafts/{id}')
        def save(body: Save, request: Request, id: str = Path(pattern=UUID_PATTERN), workspace: Workspace = 'school'):
            user, directory = self.messages.context(request, workspace)
            content = body.content.model_dump(exclude={'draftVersion'})
            content['draftId'] = id
            with self.access.lock, self.access.connect() as db:
                self.messages.tables(db); tables(db); message_files.tables(db)
                db.execute('BEGIN IMMEDIATE'); self.messages.recheck(db, user, directory)
                row = self.row(db, user, workspace, id)
                if row and row[1] == 'active' and row[0] == body.version + 1 and row[2] == body.threadId and row[3] == encoded(content):
                    return dict(id=id, version=row[0], updated=row[4])
                if row and (row[1] != 'active' or row[0] != body.version or row[2] != body.threadId) or not row and body.version != 0:
                    raise HTTPException(409, 'Черновик изменён на другом устройстве. Ваш текст остался в форме: скопируйте нужные изменения перед открытием актуального черновика.')
                if body.threadId:
                    thread, _ = self.messages.find(db, body.threadId, user, directory, self.messages.thread_accounts(db, body.threadId, user))
                    if thread['kind'] == 'announcement':
                        raise HTTPException(422, 'На объявление нельзя ответить.')
                if len(content['fileIds']) != len(set(content['fileIds'])):
                    raise HTTPException(422, 'Файл указан несколько раз.')
                for file_id in content['fileIds']:
                    file = db.execute("SELECT target_thread FROM message_files WHERE id=? AND workspace=? AND owner_id=? AND draft_id=? AND status='ready' AND post_id IS NULL",
                                      (file_id, workspace, user['id'], id)).fetchone()
                    if not file or file[0] != body.threadId:
                        raise HTTPException(422, 'Файл не найден или относится к другому сообщению.')
                if not row and db.execute("SELECT count(*) FROM message_drafts WHERE workspace=? AND owner_id=? AND status='active'", (workspace, user['id'])).fetchone()[0] >= MAX_DRAFTS:
                    raise HTTPException(413, 'Можно хранить до 50 черновиков. Удалите ненужные.')
                version = body.version + 1; updated = now().isoformat()
                db.execute('INSERT INTO message_drafts VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(workspace,owner_id,id) DO UPDATE SET version=excluded.version,content=excluded.content,updated=excluded.updated',
                           (workspace, user['id'], id, version, 'active', body.threadId, encoded(content), updated))
                self.access.record(db, 'message.draft_saved', workspace, detail=id)
            return dict(id=id, version=version, updated=updated)

        @app.delete('/api/messages/drafts/{id}')
        def remove(request: Request, id: str = Path(pattern=UUID_PATTERN), version: int = Query(ge=1), workspace: Workspace = 'school'):
            user, directory = self.messages.context(request, workspace)
            with self.access.lock, self.access.connect() as db:
                tables(db); db.execute('BEGIN IMMEDIATE'); self.messages.recheck(db, user, directory)
                row = self.row(db, user, workspace, id)
                if not row:
                    raise HTTPException(404, 'Черновик не найден.')
                if row[1] == 'deleted' and row[0] == version + 1:
                    return dict(ok=True)
                if row[1] != 'active' or row[0] != version:
                    raise HTTPException(409, 'Черновик изменён. Обновите список перед удалением.')
                db.execute("UPDATE message_drafts SET version=version+1,status='deleted',content='{}',updated=? WHERE workspace=? AND owner_id=? AND id=?",
                           (now().isoformat(), workspace, user['id'], id))
                self.access.record(db, 'message.draft_deleted', workspace, detail=id)
            return dict(ok=True)
