"""Which of the optional school sections the menu shows.

Six sections are not used by every school. Without a stored row all six are hidden, so a database from before this
setting hides them until an administrator turns each one on. Hiding only removes the menu button: the modules, their
data, their notifications, the public site and the admission link keep working, and every API keeps its own rights."""
from contextlib import closing
import json
import sqlite3

from fastapi import HTTPException, Request
from pydantic import Field

from .models import Model
from .notifications import encoded

WORKSPACE = 'school'  # the menu belongs to the school, not to a timetable workspace
SECTIONS = [
    dict(id='payments', label='Платежи'),
    dict(id='library', label='Библиотека и учебники'),
    dict(id='canteen', label='Столовая'),
    dict(id='lost_found', label='Потерянные вещи'),
    dict(id='admissions', label='Приём в школу'),
    dict(id='site', label='Сайт школы'),
]
IDS = [s['id'] for s in SECTIONS]


def tables(db):
    db.execute('CREATE TABLE IF NOT EXISTS school_sections (workspace TEXT PRIMARY KEY,version INTEGER NOT NULL,document TEXT NOT NULL)')


def stored(db):
    """(version, hidden section ids). Version 0 is the untouched default: everything hidden; a table that does not exist yet means the same."""
    try: row = db.execute('SELECT version,document FROM school_sections WHERE workspace=?', (WORKSPACE,)).fetchone()
    except sqlite3.OperationalError: row = None
    # A saved empty list is a choice (everything shown), not the default.
    return (row[0], [id for id in IDS if id in json.loads(row[1])]) if row else (0, list(IDS))


class Save(Model):
    version: int = Field(ge=0)
    hidden: list[str] = Field(max_length=len(SECTIONS))
    reason: str = Field(min_length=1, max_length=500)


def register(app, access):
    def answer(version, hidden):
        return dict(version=version, hidden=hidden, items=[dict(s, hidden=s['id'] in hidden) for s in SECTIONS])

    @app.get('/api/school-sections')
    def sections(request: Request):
        # Ids and menu labels only: nothing private, so every signed-in account may read them.
        with closing(access.read_connect()) as db: version, hidden = stored(db)
        return answer(version, hidden)

    @app.put('/api/school-sections')
    def save(body: Save, request: Request):
        user = request.state.user
        if not user or user['role'] != 'admin': raise HTTPException(403, 'Разделы школы настраивает администратор.')
        if set(body.hidden) - set(IDS): raise HTTPException(422, 'Неизвестный раздел.')
        with access.lock, access.connect() as db:
            tables(db); db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM accounts WHERE id=? AND active=1', (user['id'],)).fetchone()
            if not row or access.public(row) != user: raise HTTPException(409, 'Права изменились. Войдите заново.')
            version, _ = stored(db)
            if version != body.version: raise HTTPException(409, 'Настройка изменилась. Обновите её.')
            hidden = [id for id in IDS if id in body.hidden]
            version += 1
            db.execute('INSERT INTO school_sections VALUES (?,?,?) ON CONFLICT(workspace) DO UPDATE SET version=excluded.version,document=excluded.document', (WORKSPACE, version, encoded(hidden)))
            access.record(db, 'school.sections_updated', WORKSPACE, detail=f'Редакция {version} · скрыто: {", ".join(hidden) or "нет"} · {body.reason}')
        return answer(version, hidden)
