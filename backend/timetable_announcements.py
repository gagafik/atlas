"""Families hear about timetable changes only when an editor announces them.

Saving never notifies pupils or parents: the portal shows the working timetable at once, and a
half-finished edit must not wake every family. "Сообщить семьям" compares each pupil's visible
timetable with the one the last announcement covered and notifies only those who differ.
"""
import hashlib
import json
from typing import Literal

from fastapi import HTTPException, Request
from pydantic import Field
from .access import LOCAL, now
from .models import Model, State
from .notifications import encoded, personal_index, student_ids

DENIED = 'Сообщить семьям может администратор или составитель расписания.'


class Announce(Model):
    revision: int = Field(ge=0)


def tables(db):
    db.execute('CREATE TABLE IF NOT EXISTS timetable_announcements(workspace TEXT PRIMARY KEY,revision INTEGER,created TEXT,actor_name TEXT,digest TEXT)')


def digest(state):
    """The calendar and one fingerprint per pupil's personal weekly rows and dated changes (no private notes)."""
    def sha(value): return hashlib.sha256(encoded(value).encode()).hexdigest()
    calendar = sha([state.cycles.model_dump(mode='json'), str(state.settings.yearStart), str(state.settings.yearEnd), [d.model_dump(mode='json') for d in state.scheduleDays]])
    return dict(calendar=calendar, students={id: sha(fingerprints) for (_, id), fingerprints in personal_index(state, {'student'}).items()})


def ensure_baseline(db, workspace, state):
    """Before this feature every save notified families, so the state before a save is what they were last told about."""
    tables(db)
    if not db.execute('SELECT 1 FROM timetable_announcements WHERE workspace=?', (workspace,)).fetchone():
        db.execute('INSERT INTO timetable_announcements VALUES (?,?,?,?,?)', (workspace, state.revision, now().isoformat(), 'Система', encoded(digest(state))))


def register(app, notifications):
    access = notifications.access
    Workspace = Literal['school', 'demo']

    def manager(request, workspace):
        user = request.state.user or LOCAL
        if user['role'] != 'admin' and not (user['role'] == 'editor' and workspace in user['workspaces']): raise HTTPException(403, DENIED)
        return user

    def stored(workspace):
        with access.lock, access.connect() as db:
            row = db.execute('SELECT revision FROM workspaces WHERE id=?', (workspace,)).fetchone()
        # A school nobody has opened yet has no stored row; reading it creates the initial state.
        return row[0] if row else access.read(workspace).revision

    @app.get('/api/notifications/timetable/announce')
    def pending(request: Request, workspace: Workspace = 'school'):
        manager(request, workspace)
        revision = stored(workspace)
        with access.lock, access.connect() as db:
            tables(db)
            row = db.execute('SELECT revision,created FROM timetable_announcements WHERE workspace=?', (workspace,)).fetchone()
        # Without a baseline no save has happened yet, so there is nothing to announce.
        return dict(revision=revision, announcedRevision=row[0] if row else None, announcedAt=row[1] if row else None, pending=bool(row) and row[0] != revision)

    @app.post('/api/notifications/timetable/announce')
    def announce(body: Announce, request: Request, workspace: Workspace = 'school'):
        user = manager(request, workspace)
        stored(workspace)
        with access.lock, access.connect() as db:
            notifications.tables(db)
            tables(db)
            if not db.in_transaction: db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT revision,document FROM workspaces WHERE id=?', (workspace,)).fetchone()
            if not row or row[0] != body.revision: raise HTTPException(409, 'Расписание изменилось. Обновите страницу.')
            state = State.model_validate_json(row[1])
            new = digest(state)
            old = db.execute('SELECT digest FROM timetable_announcements WHERE workspace=?', (workspace,)).fetchone()
            users, changed = [], set()
            if old:
                old = json.loads(old[0])
                ids = set(old['students']) | set(new['students'])
                changed = {id for id in ids if old['students'].get(id) != new['students'].get(id)}
                # A new school calendar moves every day, so it concerns every pupil.
                if old['calendar'] != new['calendar']: changed = {s.id for s in state.students if s.active} | changed
                names = {s.id: s.name for s in state.students}
                users = notifications.granted_users(db, workspace, 'student', changed, ['student', 'parent'])
                topic = f'timetable:{workspace}'
                # The new entry replaces the unread one of the topic, so it keeps naming the children that one told about.
                unread = {id: {s['id'] for s in (json.loads(data) if data else {}).get('students') or []} for id, data in db.execute(
                    'SELECT user_id,target FROM notifications WHERE user_id IN (SELECT value FROM json_each(?)) AND topic=? AND read_at IS NULL', (encoded([u['id'] for u in users]), topic))}
                notifications.deliver(db, users, workspace, state.revision, 'timetable.published', topic=topic,
                                      target=lambda u: dict(module='timetable', students=[dict(id=k, name=names.get(k, '')) for k in sorted(student_ids(u, workspace) & (changed | unread.get(u['id'], set())))], title='Изменилось расписание'))
            db.execute('INSERT INTO timetable_announcements VALUES (?,?,?,?,?) ON CONFLICT(workspace) DO UPDATE SET revision=excluded.revision,created=excluded.created,actor_name=excluded.actor_name,digest=excluded.digest',
                       (workspace, state.revision, now().isoformat(), user['name'], encoded(new)))
            access.record(db, 'timetable.announced', workspace, state.revision, detail=f'Уведомлено аккаунтов: {len(users)}', principal=user)
        return dict(notified=len(users), students=len(changed), revision=state.revision)
