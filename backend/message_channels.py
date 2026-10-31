"""Admin-editable channels of the message directory.

A channel is one directed way of starting a conversation. Without a stored row the defaults apply: they reproduce
the rules from before the matrix existed, so every channel that widens who may write to whom is off until an
administrator turns it on. Administrators themselves always reach the people they are linked with."""
from contextlib import closing
import json
import sqlite3

from fastapi import HTTPException, Query, Request
from pydantic import Field

from .access import now
from .models import Model
from .notifications import encoded

ALWAYS = 'admin'  # link kind of an administrator as sender; it is not a channel and cannot be switched off
CHANNELS = [
    dict(id='student_teachers', sender='student', recipient='teacher', label='Ученик пишет учителям, которые его учат', default=True),
    dict(id='student_admin', sender='student', recipient='admin', label='Ученик пишет администрации', default=True),
    dict(id='student_class_teacher', sender='student', recipient='class_teacher', label='Ученик пишет своему классному руководителю', default=False),
    dict(id='student_any_teacher', sender='student', recipient='teacher', label='Ученик пишет любому учителю школы', default=False),
    dict(id='student_classmates', sender='student', recipient='student', label='Ученик пишет одноклассникам', default=False),
    dict(id='parent_teachers', sender='parent', recipient='teacher', label='Родитель пишет учителям своего ребёнка', default=True),
    dict(id='parent_admin', sender='parent', recipient='admin', label='Родитель пишет администрации', default=True),
    dict(id='parent_class_teacher', sender='parent', recipient='class_teacher', label='Родитель пишет классному руководителю ребёнка', default=False),
    dict(id='parent_any_teacher', sender='parent', recipient='teacher', label='Родитель пишет любому учителю школы', default=False),
    dict(id='parent_class_parents', sender='parent', recipient='parent', label='Родитель пишет другим родителям класса', default=False),
    dict(id='teacher_students', sender='teacher', recipient='student', label='Учитель пишет ученикам, которых учит', default=True),
    dict(id='teacher_parents', sender='teacher', recipient='parent', label='Учитель пишет родителям своих учеников', default=True),
    dict(id='class_teacher_families', sender='teacher', recipient='family', label='Классный руководитель пишет ученикам и родителям своего класса', default=False),
    dict(id='teacher_colleagues_all', sender='teacher', recipient='staff', label='Учитель объявляет всем учителям, классным руководителям и сотрудникам', default=False),
    dict(id='teacher_school', sender='teacher', recipient='school', label='Учитель публикует объявление всей школе', default=False),
    dict(id='staff_staff', sender='staff', recipient='staff', label='Сотрудники пишут друг другу', default=True),
    dict(id='feed_public_replies', sender='feed', recipient='recipients', label='Автор объявления может открыть ответы всем получателям', default=False),
]
IDS = [c['id'] for c in CHANNELS]
DEFAULT_CHANNELS = frozenset(c['id'] for c in CHANNELS if c['default'])
CLOSED = 'Этот канал сообщений отключён администратором.'


def tables(db):
    db.execute('CREATE TABLE IF NOT EXISTS message_channels (workspace TEXT PRIMARY KEY,version INTEGER NOT NULL,document TEXT NOT NULL)')
    db.execute('''CREATE TABLE IF NOT EXISTS message_channel_history (
        id INTEGER PRIMARY KEY,workspace TEXT NOT NULL,version INTEGER NOT NULL,created TEXT NOT NULL,
        actor_id TEXT NOT NULL,actor_name TEXT NOT NULL,reason TEXT NOT NULL,before_document TEXT NOT NULL,after_document TEXT NOT NULL)''')
    db.execute('CREATE INDEX IF NOT EXISTS message_channel_history_workspace ON message_channel_history(workspace,id)')


def stored(db, workspace):
    """(version, enabled channel ids). Version 0 is the untouched default; a table that does not exist yet means the defaults too."""
    try: row = db.execute('SELECT version,document FROM message_channels WHERE workspace=?', (workspace,)).fetchone()
    except sqlite3.OperationalError: row = None
    return (row[0], frozenset(json.loads(row[1])) & frozenset(IDS)) if row else (0, DEFAULT_CHANNELS)


def load(db, workspace):
    return stored(db, workspace)[1]


class Save(Model):
    version: int = Field(ge=0)
    reason: str = Field(min_length=1, max_length=500)
    enabled: list[str] = Field(max_length=len(CHANNELS))


class MessageChannels:
    def __init__(self, messages):
        self.messages, self.access = messages, messages.access

    def tables(self, db): tables(db)

    def current(self, workspace):
        """The matrix for a request. Read connections cannot create tables, so a missing one means the defaults."""
        try:
            with closing(self.access.read_connect()) as db: return load(db, workspace)
        except sqlite3.OperationalError: return DEFAULT_CHANNELS

    def administrator(self, request, workspace):
        user, directory = self.messages.context(request, workspace)
        if user['role'] != 'admin': raise HTTPException(403, 'Каналы сообщений настраивает администратор.')
        return user, directory

    def register(self, app, Workspace):
        @app.get('/api/messages/channels')
        def channels(request: Request, workspace: Workspace='school'):
            self.administrator(request, workspace)
            with self.access.lock, self.access.connect() as db:
                self.messages.tables(db)
                version, enabled = stored(db, workspace)
            return dict(version=version, items=[dict(c, enabled=c['id'] in enabled) for c in CHANNELS])

        @app.put('/api/messages/channels')
        def save(body: Save, request: Request, workspace: Workspace='school'):
            user, directory = self.administrator(request, workspace)
            if set(body.enabled) - set(IDS): raise HTTPException(422, 'Неизвестный канал сообщений.')
            with self.access.lock, self.access.connect() as db:
                self.messages.tables(db); db.execute('BEGIN IMMEDIATE'); self.messages.recheck(db, user, directory)
                version, before = stored(db, workspace)
                if version != body.version: raise HTTPException(409, 'Настройка изменилась. Обновите её.')
                value = [id for id in IDS if id in body.enabled]
                version += 1
                db.execute('INSERT INTO message_channels VALUES (?,?,?) ON CONFLICT(workspace) DO UPDATE SET version=excluded.version,document=excluded.document', (workspace, version, encoded(value)))
                db.execute('INSERT INTO message_channel_history(workspace,version,created,actor_id,actor_name,reason,before_document,after_document) VALUES (?,?,?,?,?,?,?,?)',
                           (workspace, version, now().isoformat(), user['id'], user['name'], body.reason, encoded([id for id in IDS if id in before]), encoded(value)))
                self.access.record(db, 'message.channels_updated', workspace, detail=f'Редакция {version} · {body.reason}')
            return dict(version=version)

        @app.get('/api/messages/channels/history')
        def history(request: Request, workspace: Workspace='school', before: int | None=Query(default=None, ge=1)):
            self.administrator(request, workspace)
            labels = {c['id']: c['label'] for c in CHANNELS}
            with self.access.lock, self.access.connect() as db:
                self.messages.tables(db)
                rows = db.execute('SELECT id,version,created,actor_name,reason,before_document,after_document FROM message_channel_history WHERE workspace=? AND (? IS NULL OR id<?) ORDER BY id DESC LIMIT 21', (workspace, before, before)).fetchall()
            def changes(old, new):
                old, new = set(json.loads(old)), set(json.loads(new))
                return [dict(id=id, label=labels[id], enabled=id in new) for id in IDS if (id in old) != (id in new)]
            return dict(items=[dict(id=r[0], version=r[1], created=r[2], actor=r[3], reason=r[4], changes=changes(r[5], r[6])) for r in rows[:20]], nextBefore=rows[19][0] if len(rows) > 20 else None)
