"""Explicitly reviewed, immutable timetable documents with revocable local links."""
from datetime import date, datetime, timedelta, timezone
from html import escape
import json
import secrets
from typing import Literal

from fastapi import HTTPException
from fastapi.responses import HTMLResponse
from pydantic import Field, model_validator
from .models import Model
from .exporting import ExportRequest, html_document, schedule_rows


class PublicationRequest(ExportRequest):
    name: str = Field(min_length=1, max_length=160)
    kind: Literal['weekly', 'dated'] = 'weekly'
    firstDate: date | None = None
    lastDate: date | None = None
    expiresInDays: int = Field(default=30, ge=1, le=365)

    @model_validator(mode='after')
    def selection(self):
        if not self.entityIds or len(self.entityIds) > 200 or len(set(self.entityIds)) != len(self.entityIds):
            raise ValueError('Выберите от 1 до 200 записей без повторений.')
        if self.format != 'html': raise ValueError('Публикуется только документ расписания.')
        if self.kind == 'weekly' and not self.cycleIds: raise ValueError('Выберите недели расписания.')
        if self.kind == 'dated' and (not self.firstDate or not self.lastDate or self.firstDate > self.lastDate or (self.lastDate-self.firstDate).days > 30):
            raise ValueError('Для публикации по датам выберите период от 1 до 31 дня.')
        return self


class PublishRequest(Model):
    revision: int
    token: str = Field(min_length=20, max_length=100)


def public_document(state, body):
    """Return only selected presentation fields, never a serialized State."""
    entities, cycles, rows = schedule_rows(state, body)
    if body.kind == 'weekly':
        document = html_document(state, body)
        meta = 'Повторяющиеся недели. Изменения по отдельным датам сюда не входят.'
        count = len(rows)
    else:
        from .calendar_ops import DayContext, resolve_day, dates, TYPES
        if body.firstDate < state.settings.yearStart or body.lastDate > state.settings.yearEnd:
            raise ValueError('Период публикации должен находиться в учебном году.')
        ctx = DayContext(state)
        content, count = [], 0
        field = {'class':'originalClassIds','teacher':'originalTeacherIds','room':'roomIds','group':'groupIds','student':'originalStudentIds'}[body.view]
        dated = [(day, resolve_day(state, day, ctx)[0]) for day in dates(body.firstDate, body.lastDate)]
        for entity in entities:
            items = []
            for day, day_rows in dated:
                selected = [r for r in day_rows if entity.id in r[field] or body.view == 'teacher' and entity.id in r['teacherIds']]
                if not selected: continue
                items.append('<h3>' + str(day) + '</h3>')
                for row in selected:
                    count += 1
                    status = (TYPES[row['changeType']] if body.view == 'teacher' and row.get('changeType') and row['status'] != 'cancelled' else
                              'Отменено' if row['status'] == 'cancelled' else 'Дежурство' if row['activity'] == 'duty' else 'Бронирование' if row['activity'] == 'booking' else 'Изменено' if row['status'] == 'changed' else 'Разовое занятие' if row['status'] == 'added' else 'По расписанию')
                    values = [row['subject'], row['group'], status]
                    if body.showTimes: values.insert(0, row['start'] + '–' + row['end'])
                    if body.showTeachers: values.append(row['teachers'])
                    if body.showRooms: values.append(row['rooms'])
                    # Absence explanations, notes, rosters and source IDs are private.
                    items.append('<p class="event' + (' cancelled' if row['status'] == 'cancelled' else '') + '">' + ' · '.join(escape(v) for v in values if v) + '</p>')
            content.append('<section><h2>' + escape(entity.name) + '</h2>' + (''.join(items) or '<p>Занятий в выбранные даты нет.</p>') + '</section>')
        title = escape(body.title or state.settings.name)
        document = '<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>' + title + '</title><style>body{font:16px system-ui;margin:24px;color:#123c34}section{margin:24px 0;break-inside:avoid}.event{padding:12px;border:1px solid #d8e3dc;border-radius:8px;overflow-wrap:anywhere}.cancelled{color:#796e6a;background:#f8f2ee}h3{margin-bottom:8px}@media print{section{break-before:page}}</style></head><body><h1>' + title + '</h1>' + ''.join(content) + '</body></html>'
        meta = f'Фактические занятия {body.firstDate} — {body.lastDate}. Изменения и отмены включены на момент публикации.'
    notice = '<aside style="padding:16px;background:#edf4ef;border:1px solid #b7cec2;margin:12px 0;font:14px system-ui;overflow-wrap:anywhere"><strong>' + escape(body.name) + '</strong><p>' + escape(meta) + '</p><p>Снимок версии ' + str(state.revision) + '. Последующие правки составителя не изменяют этот документ.</p></aside>'
    document = document.replace('<body>', '<body>' + notice, 1)
    if len(document.encode('utf-8')) > 10_000_000: raise ValueError('Слишком большой документ. Выберите меньше записей или дат.')
    return document, dict(kind=body.kind, view=body.view, records=len(entities), rows=count, names=[e.name for e in entities],
                          weeks=[c.name for c in cycles] if body.kind == 'weekly' else [],
                          firstDate=body.firstDate.isoformat() if body.firstDate else None,
                          lastDate=body.lastDate.isoformat() if body.lastDate else None)


def register_publications(app, Workspace, require_revision, connect, db_lock):
    def tables(db):
        db.execute('CREATE TABLE IF NOT EXISTS publication_previews (token TEXT PRIMARY KEY,workspace TEXT,revision INTEGER,created TEXT,name TEXT,document TEXT,metadata TEXT,lifetime INTEGER)')
        db.execute('CREATE TABLE IF NOT EXISTS publications (id TEXT PRIMARY KEY,workspace TEXT,revision INTEGER,created TEXT,expires TEXT,revoked TEXT,name TEXT,document TEXT,metadata TEXT)')

    def summary(row):
        id, revision, created, expires, revoked, name, metadata = row
        return dict(id=id, revision=revision, created=created, expires=expires, revoked=revoked, name=name,
                    status='revoked' if revoked else 'expired' if datetime.fromisoformat(expires) <= datetime.now(timezone.utc) else 'active',
                    url='/published/' + id, **json.loads(metadata))

    @app.post('/api/publications/preview')
    def preview(body: PublicationRequest, workspace: Workspace = 'school'):
        state = require_revision(workspace, body.revision)
        try: document, metadata = public_document(state, body)
        except ValueError as exc: raise HTTPException(422, str(exc))
        token, now = secrets.token_urlsafe(32), datetime.now(timezone.utc)
        with db_lock, connect() as db:
            tables(db)
            db.execute('INSERT INTO publication_previews VALUES (?,?,?,?,?,?,?,?)', (token,workspace,state.revision,now.isoformat(),body.name,document,json.dumps(metadata,ensure_ascii=False),body.expiresInDays))
            db.execute('DELETE FROM publication_previews WHERE created<?', ((now-timedelta(minutes=30)).isoformat(),))
            db.execute('DELETE FROM publication_previews WHERE workspace=? AND token NOT IN (SELECT token FROM publication_previews WHERE workspace=? ORDER BY created DESC LIMIT 20)', (workspace,workspace))
        return dict(token=token, revision=state.revision, html=document, expiresInDays=body.expiresInDays, **metadata)

    @app.post('/api/publications')
    def publish(body: PublishRequest, workspace: Workspace = 'school'):
        now = datetime.now(timezone.utc)
        with db_lock, connect() as db:
            tables(db)
            db.execute('BEGIN IMMEDIATE')
            current = db.execute('SELECT revision FROM workspaces WHERE id=?', (workspace,)).fetchone()
            if not current or current[0] != body.revision: raise HTTPException(409, 'Расписание изменилось. Подготовьте публикацию заново.')
            row = db.execute('SELECT revision,created,name,document,metadata,lifetime FROM publication_previews WHERE token=? AND workspace=?', (body.token,workspace)).fetchone()
            if not row or datetime.fromisoformat(row[1]) <= now-timedelta(minutes=30): raise HTTPException(404, 'Предпросмотр истёк или уже использован.')
            if row[0] != body.revision: raise HTTPException(409, 'Предпросмотр относится к другой версии.')
            id = secrets.token_urlsafe(32)
            expires = (now+timedelta(days=row[5])).isoformat()
            db.execute('INSERT INTO publications VALUES (?,?,?,?,?,?,?,?,?)', (id,workspace,row[0],now.isoformat(),expires,None,row[2],row[3],row[4]))
            db.execute('DELETE FROM publication_previews WHERE token=?', (body.token,))
        return summary((id,row[0],now.isoformat(),expires,None,row[2],row[4]))

    @app.get('/api/publications')
    def listing(workspace: Workspace = 'school'):
        with db_lock, connect() as db:
            tables(db)
            rows = db.execute('SELECT id,revision,created,expires,revoked,name,metadata FROM publications WHERE workspace=? ORDER BY created DESC', (workspace,)).fetchall()
        return [summary(r) for r in rows]

    @app.delete('/api/publications/{id}')
    def revoke(id: str, workspace: Workspace = 'school'):
        with db_lock, connect() as db:
            tables(db)
            result = db.execute('UPDATE publications SET revoked=COALESCE(revoked,?),document=? WHERE id=? AND workspace=?', (datetime.now(timezone.utc).isoformat(),'',id,workspace))
            if not result.rowcount: raise HTTPException(404, 'Публикация не найдена.')
        return dict(status='revoked')

    @app.get('/published/{id}', response_class=HTMLResponse)
    def document(id: str):
        with db_lock, connect() as db:
            tables(db)
            row = db.execute('SELECT document,expires,revoked FROM publications WHERE id=?', (id,)).fetchone()
        headers = {'Cache-Control':'no-store', 'Referrer-Policy':'no-referrer', 'X-Content-Type-Options':'nosniff',
                   'X-Robots-Tag':'noindex, nofollow', 'Content-Security-Policy':"default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'; frame-ancestors 'self'"}
        if not row or row[2] or datetime.fromisoformat(row[1]) <= datetime.now(timezone.utc):
            return HTMLResponse('<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Расписание недоступно</title><body><h1>Расписание недоступно</h1><p>Ссылка истекла, отозвана или не существует.</p></body></html>',status_code=404,headers=headers)
        return HTMLResponse(row[0], headers=headers)
