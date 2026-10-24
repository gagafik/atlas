import json
import os
import sqlite3
import threading
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Literal
from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from .models import State
from .seed import initial_state
from .scheduling import validate_schedule
from .solver_process import solve_isolated as solve
from .optimization import Optimization
from .editing import EditRequest, edit, inspect_edit
from uuid import uuid4

ROOT = Path(__file__).resolve().parent.parent
DB = Path(os.environ.get('TIMETABLE_DB', str(ROOT / 'data' / 'timetable.db')))
DB.parent.mkdir(parents=True, exist_ok=True)
app = FastAPI(title='Школьное расписание', docs_url='/api/docs')
db_lock = threading.Lock()
solve_lock = threading.Lock()


@app.middleware('http')
async def local_origin(request: Request, call_next):
    origin = request.headers.get('origin')
    allowed = ['http://127.0.0.1:5173', 'http://localhost:5173', 'http://127.0.0.1:8000', 'http://localhost:8000', str(request.base_url).rstrip('/')]
    allowed += [value.strip() for value in os.environ.get('TIMETABLE_ALLOWED_ORIGINS','').split(',') if value.strip()]
    if request.method not in ['GET', 'HEAD', 'OPTIONS'] and origin and origin not in allowed:
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=403, content={'detail': 'Запрос с другого сайта запрещён.'})
    return await call_next(request)


class Connection(sqlite3.Connection):
    """The SQLite transaction context alone does not close the connection."""
    def __exit__(self, *args):
        try:
            return super().__exit__(*args)
        finally:
            self.close()


def connect(read_only=False):
    if read_only:
        return sqlite3.connect(DB.resolve().as_uri()+'?mode=ro', uri=True, factory=Connection)
    db = sqlite3.connect(DB, factory=Connection)
    db.execute('PRAGMA journal_mode=WAL')
    db.execute('CREATE TABLE IF NOT EXISTS workspaces (id TEXT PRIMARY KEY, revision INTEGER, document TEXT)')
    db.execute('CREATE TABLE IF NOT EXISTS history (id INTEGER PRIMARY KEY, workspace TEXT, created TEXT, document TEXT)')
    db.execute('CREATE TABLE IF NOT EXISTS redo_history (id INTEGER PRIMARY KEY, workspace TEXT, created TEXT, document TEXT)')
    db.execute('CREATE TABLE IF NOT EXISTS variants (id TEXT PRIMARY KEY, workspace TEXT, name TEXT, created TEXT, document TEXT)')
    db.execute('CREATE TABLE IF NOT EXISTS previews (id TEXT PRIMARY KEY, workspace TEXT, revision INTEGER, created TEXT, document TEXT, can_apply INTEGER)')
    db.execute('CREATE TABLE IF NOT EXISTS generation_jobs (id TEXT PRIMARY KEY, workspace TEXT, created TEXT, document TEXT)')
    return db


def read(workspace):
    with db_lock, connect() as db:
        row = db.execute('SELECT document FROM workspaces WHERE id=?', (workspace,)).fetchone()
        if not row:
            state = initial_state(workspace)
            db.execute('INSERT INTO workspaces VALUES (?, ?, ?)', (workspace, state.revision, state.model_dump_json()))
            return state
    # Validation of a whole school must not hold the database write lock.
    return State.model_validate_json(row[0])


def portal_stamp(workspace):
    with connect(read_only=True) as db:
        row = db.execute('SELECT revision FROM workspaces WHERE id=?', (workspace,)).fetchone()
    revision = row[0] if row else read(workspace).revision
    # Include the database identity, including isolated test databases.
    return str(DB.resolve()), revision


def save(workspace, state, after_save=None):
    with db_lock, connect() as db:
        row = db.execute('SELECT revision, document FROM workspaces WHERE id=?', (workspace,)).fetchone()
        if row is None or state.revision != row[0]:
            raise HTTPException(409, 'Данные изменились в другом окне. Обновите страницу перед сохранением.')
        db.execute('INSERT INTO history (workspace,created,document) VALUES (?,?,?)', (workspace, datetime.now(timezone.utc).isoformat(), row[1]))
        db.execute('DELETE FROM redo_history WHERE workspace=?', (workspace,))
        state = state.model_copy(update={'revision': state.revision + 1})
        db.execute('UPDATE workspaces SET revision=?,document=? WHERE id=?', (state.revision, state.model_dump_json(), workspace))
        db.execute('DELETE FROM history WHERE workspace=? AND id NOT IN (SELECT id FROM history WHERE workspace=? ORDER BY id DESC LIMIT 30)', (workspace, workspace))
        before = State.model_validate_json(row[1])
        from .class_staff import record_teachers
        record_teachers(db, workspace, before, state, 'saved')
        access.record(db,'state.saved',workspace,state.revision)
        notifications.capture(db,workspace,before,state)
        if after_save is not None:
            after_save(db,state)
        return state


Workspace = Literal['school', 'demo']


@app.get('/api/state')
def get_state(workspace: Workspace = 'school'):
    return read(workspace)


@app.put('/api/state')
def put_state(state: State, workspace: Workspace = 'school'):
    from .calendar_ops import operational_dates, check_dates
    from .editing import new_issues
    current = require_revision(workspace, state.revision)
    days = operational_dates(current) | operational_dates(state)
    if days:
        added = new_issues({'issues': check_dates(current, days)}, {'issues': check_dates(state, days)})
        if added:
            raise HTTPException(422, 'Изменение создаёт проблемы в расписании по датам: ' + '; '.join(i['message'] for i in added[:3]))
    return save(workspace, state)


@app.get('/api/check')
def check(workspace: Workspace = 'school', cycleId: str | None = None):
    try:
        return validate_schedule(read(workspace), cycleId)
    except ValueError as exc:
        raise HTTPException(422, str(exc))


@app.get('/api/calendar-pattern')
def calendar_pattern(day: date, workspace: Workspace = 'school'):
    from .cycles import pattern_on
    state = read(workspace)
    id = pattern_on(state, day)
    return dict(day=day, cycleId=id, name=next((p.name for p in state.cycles.patterns if p.id == id), None))


class GenerateRequest(BaseModel):
    revision: int
    workloadIds: list[str] | None = None
    seconds: int = Field(default=15, ge=1, le=30)
    preview: bool = False
    cycleIds: list[str] | None = None
    optimization: Optimization = Field(default_factory=Optimization)


@app.post('/api/generate')
def generate(body: GenerateRequest, workspace: Workspace = 'school'):
    if not solve_lock.acquire(blocking=False):
        raise HTTPException(409, 'Генерация уже выполняется.')
    try:
        state = read(workspace)
        if state.revision != body.revision:
            raise HTTPException(409, 'Обновите страницу: данные уже изменились.')
        options = {'cycle_ids': body.cycleIds} if body.cycleIds is not None else {}
        options['optimization'] = body.optimization
        result = solve(state, body.workloadIds, body.seconds, **options)
        if result['status'] == 'success':
            candidate = state.model_copy(update={'placements': result.pop('placements')})
            if body.preview:
                result['preview'] = create_preview(workspace, state, candidate)
            else:
                if state.datedLessons or state.absences or state.commitments or any(e.effect == 'cancel' for e in state.events):
                    check = inspect_edit(state, candidate)
                    if not check['canApply']:
                        raise HTTPException(422, 'Генерация затрагивает изменения по датам. Запустите её с предпросмотром.')
                result['state'] = save(workspace, candidate)
        return result
    finally:
        solve_lock.release()


@app.post('/api/undo')
def undo(body: GenerateRequest, workspace: Workspace = 'school'):
    with db_lock, connect() as db:
        current = db.execute('SELECT revision,document FROM workspaces WHERE id=?', (workspace,)).fetchone()
        if not current or current[0] != body.revision:
            raise HTTPException(409, 'Обновите страницу перед отменой.')
        row = db.execute('SELECT id,document FROM history WHERE workspace=? ORDER BY id DESC LIMIT 1', (workspace,)).fetchone()
        if not row:
            raise HTTPException(400, 'Нет сохранённых действий для отмены.')
        previous = State.model_validate_json(row[1])
        db.execute('INSERT INTO redo_history (workspace,created,document) VALUES (?,?,?)', (workspace, datetime.now(timezone.utc).isoformat(), current[1]))
        previous.revision = current[0] + 1
        db.execute('UPDATE workspaces SET revision=?,document=? WHERE id=?', (previous.revision, previous.model_dump_json(), workspace))
        db.execute('DELETE FROM history WHERE id=?', (row[0],))
        from .class_staff import record_teachers
        record_teachers(db, workspace, State.model_validate_json(current[1]), previous, 'undo')
        access.record(db,'state.undo',workspace,previous.revision)
        notifications.capture(db,workspace,State.model_validate_json(current[1]),previous,'undo')
        return previous


@app.post('/api/redo')
def redo(body: GenerateRequest, workspace: Workspace = 'school'):
    with db_lock, connect() as db:
        current = db.execute('SELECT revision,document FROM workspaces WHERE id=?', (workspace,)).fetchone()
        if not current or current[0] != body.revision:
            raise HTTPException(409, 'Обновите страницу перед повтором.')
        row = db.execute('SELECT id,document FROM redo_history WHERE workspace=? ORDER BY id DESC LIMIT 1', (workspace,)).fetchone()
        if not row:
            raise HTTPException(400, 'Нет действий для повтора.')
        previous = State.model_validate_json(row[1])
        previous.revision = current[0] + 1
        db.execute('INSERT INTO history (workspace,created,document) VALUES (?,?,?)', (workspace, datetime.now(timezone.utc).isoformat(), current[1]))
        db.execute('UPDATE workspaces SET revision=?,document=? WHERE id=?', (previous.revision, previous.model_dump_json(), workspace))
        db.execute('DELETE FROM redo_history WHERE id=?', (row[0],))
        from .class_staff import record_teachers
        record_teachers(db, workspace, State.model_validate_json(current[1]), previous, 'redo')
        access.record(db,'state.redo',workspace,previous.revision)
        notifications.capture(db,workspace,State.model_validate_json(current[1]),previous,'redo')
        return previous


@app.get('/api/history')
def history_status(workspace: Workspace = 'school'):
    with db_lock, connect() as db:
        return {key: db.execute(f'SELECT count(*) FROM {table} WHERE workspace=?', (workspace,)).fetchone()[0]
                for key, table in [('undo', 'history'), ('redo', 'redo_history')]}


def require_revision(workspace, revision):
    state = read(workspace)
    if state.revision != revision:
        raise HTTPException(409, 'Данные изменились. Обновите страницу и повторите проверку.')
    return state


def create_preview(workspace, state, candidate, allow_issues=False):
    result = inspect_edit(state, candidate)
    # Named snapshots and rule configuration can intentionally introduce known
    # violations. Their UI displays them before an explicit application.
    if allow_issues:
        result['canApply'] = True
    token = str(uuid4())
    with db_lock, connect() as db:
        db.execute('INSERT INTO previews VALUES (?,?,?,?,?,?)', (token, workspace, state.revision, datetime.now(timezone.utc).isoformat(), candidate.model_dump_json(), int(result['canApply'])))
        db.execute('DELETE FROM previews WHERE workspace=? AND id NOT IN (SELECT id FROM previews WHERE workspace=? ORDER BY created DESC LIMIT 30)', (workspace, workspace))
    return dict(token=token, revision=state.revision, **result)


@app.post('/api/editor/preview')
def preview_edit(body: EditRequest, workspace: Workspace = 'school'):
    state = require_revision(workspace, body.revision)
    try:
        candidate = edit(state, body)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    return create_preview(workspace, state, candidate, allow_issues=body.action in ['rule', 'removeRule', 'campus', 'cycles'])


class ApplyPreview(BaseModel):
    revision: int
    token: str


@app.post('/api/editor/apply')
def apply_preview(body: ApplyPreview, workspace: Workspace = 'school'):
    with db_lock, connect() as db:
        row = db.execute('SELECT revision,document,can_apply FROM previews WHERE id=? AND workspace=?', (body.token, workspace)).fetchone()
    if not row:
        raise HTTPException(404, 'Предпросмотр не найден. Проверьте изменения заново.')
    if row[0] != body.revision:
        raise HTTPException(409, 'Предпросмотр относится к другой версии расписания.')
    if not row[2]:
        raise HTTPException(422, 'Изменение создаёт новые нарушения. Выберите другое размещение.')
    candidate = State.model_validate_json(row[1])
    candidate.revision = row[0]
    return save(workspace, candidate)


class SlotRequest(BaseModel):
    revision: int
    placementId: str
    linked: bool = True


@app.post('/api/editor/slots')
def available_slots(body: SlotRequest, workspace: Workspace = 'school'):
    state = require_revision(workspace, body.revision)
    placement = next((p for p in state.placements if p.id == body.placementId), None)
    if not placement:
        raise HTTPException(404, 'Занятие не найдено.')
    w = next(w for w in state.workloads if w.id == placement.workloadId)
    profile = next(p for p in state.profiles if p.id == w.profileId)
    from .editing import new_issues
    baseline = validate_schedule(state)
    slots = []
    for day in range(len(state.settings.days)):
        for period, slot in enumerate(profile.for_day(day)):
            try:
                candidate = edit(state, EditRequest(revision=body.revision, action='move', ids=[placement.id], day=day, period=period, linked=body.linked))
                issues = new_issues(baseline, validate_schedule(candidate))
                reasons = [i['message'] for i in issues]
            except ValueError as exc:
                reasons = [str(exc)]
            slots.append(dict(day=day, period=period, start=slot.start, end=slot.end, available=not reasons, reasons=reasons[:4]))
    return dict(revision=state.revision, slots=slots, completeChecks=baseline['completeChecks'])


class VariantRequest(BaseModel):
    revision: int
    name: str = Field(min_length=1, max_length=160)


@app.get('/api/variants')
def list_variants(workspace: Workspace = 'school'):
    with db_lock, connect() as db:
        rows = db.execute('SELECT id,name,created,document FROM variants WHERE workspace=? ORDER BY created DESC', (workspace,)).fetchall()
    return [dict(id=r[0], name=r[1], created=r[2], revision=json.loads(r[3])['revision']) for r in rows]


@app.post('/api/variants')
def create_variant(body: VariantRequest, workspace: Workspace = 'school'):
    state = require_revision(workspace, body.revision)
    name = body.name.strip()
    if not name:
        raise HTTPException(422, 'Введите название варианта.')
    token, created = str(uuid4()), datetime.now(timezone.utc).isoformat()
    with db_lock, connect() as db:
        db.execute('INSERT INTO variants VALUES (?,?,?,?,?)', (token, workspace, name, created, state.model_dump_json()))
    return dict(id=token, name=name, created=created, revision=state.revision)


@app.post('/api/variants/{variant_id}/preview')
def preview_variant(variant_id: str, body: GenerateRequest, workspace: Workspace = 'school'):
    state = require_revision(workspace, body.revision)
    with db_lock, connect() as db:
        row = db.execute('SELECT document FROM variants WHERE id=? AND workspace=?', (variant_id, workspace)).fetchone()
    if not row:
        raise HTTPException(404, 'Вариант не найден.')
    candidate = State.model_validate_json(row[0])
    candidate.revision = state.revision
    return create_preview(workspace, state, candidate, allow_issues=True)


@app.delete('/api/variants/{variant_id}')
def delete_variant(variant_id: str, workspace: Workspace = 'school'):
    with db_lock, connect() as db:
        cursor = db.execute('DELETE FROM variants WHERE id=? AND workspace=?', (variant_id, workspace))
        if not cursor.rowcount:
            raise HTTPException(404, 'Вариант не найден.')
    return {'status': 'deleted'}


@app.get('/api/health')
def health():
    return {'status': 'ok'}


from .jobs import register_jobs
shutdown_generation_jobs = register_jobs(app, Workspace, require_revision, lambda: DB, solve_lock)
from .courses import register_courses
register_courses(app, Workspace, require_revision, create_preview, solve_lock)
from .calendar_ops import register_calendar
register_calendar(app, Workspace, read, require_revision, create_preview)
from .commitments import register_commitments
register_commitments(app, Workspace, require_revision, create_preview, solve_lock)
from .exporting import register_exports
register_exports(app, Workspace, require_revision, create_preview)
from .importing import register_imports
register_imports(app, Workspace, require_revision, create_preview)
from .publications import register_publications
register_publications(app, Workspace, require_revision, connect, db_lock)
from .access import Access
access = Access(connect, db_lock, read, lambda: connect(read_only=True))
access.register(app)
from .operations import register as register_operations
register_operations(app, access, lambda: DB)
from .invitations import register_invitations
register_invitations(app, access)
from .notifications import Notifications
notifications = Notifications(access)
notifications.register(app)
from .families import Families
families = Families(access, notifications)
families.register(app, Workspace)
from .journal import Journal
journal = Journal(access, notifications, portal_stamp)
journal.register(app, Workspace)
from .homework import Homework
homework = Homework(journal, portal_stamp)
homework.register(app, Workspace)
from .teaching_materials import TeachingMaterials
teaching_materials = TeachingMaterials(journal, homework.snapshots)
teaching_materials.register(app, Workspace)
from .curriculum import Curriculum
curriculum = Curriculum(teaching_materials)
curriculum.register(app, Workspace)
from .quizzes import Quizzes
quizzes = Quizzes(journal, homework.snapshots)
quizzes.register(app, Workspace)
from .absences import Absences
absences = Absences(journal, homework.snapshots)
absences.register(app, Workspace)
absences.files.register(app, Workspace)
from .attendance_reports import AttendanceReports
attendance_reports = AttendanceReports(absences)
attendance_reports.register(app, Workspace)
from .journal_coverage import JournalCoverage
journal_coverage = JournalCoverage(attendance_reports)
journal_coverage.register(app, Workspace)
from .journal_periods import JournalPeriods
journal_periods = JournalPeriods(journal_coverage)
journal_periods.register(app, Workspace)
from .final_grades import FinalGrades
final_grades = FinalGrades(journal_periods)
final_grades.register(app, Workspace)
from .gradebook_jobs import register_jobs as register_gradebook_jobs
shutdown_gradebook_jobs = register_gradebook_jobs(app, final_grades, Workspace, lambda: DB, solve_lock)

from .messages import Messages
messages = Messages(access, notifications, portal_stamp)
messages.register(app, Workspace)
messages.files.register(app, Workspace)
messages.drafts.register(app, Workspace)
messages.groups.register(app, Workspace)
messages.moderation.register(app, Workspace)
messages.recovery.register(app, Workspace)
from .portal import register_portal
register_portal(app, Workspace, read, portal_stamp)
from .assistant import register_assistant
_assistant_requests = register_assistant(app, Workspace, access, read, require_revision, create_preview)
from .intake import register_intake
register_intake(app, Workspace, access, read, require_revision, save, connect, db_lock)
from .web_push import PushService
push_service = PushService(access)
push_service.register(app)
from .school_forms import SchoolForms
school_forms = SchoolForms(access, notifications, portal_stamp)
school_forms.register(app, Workspace)
from .meetings import Meetings
meetings = Meetings(access, notifications, portal_stamp)
meetings.register(app, Workspace)
from .staff_time import StaffTime
staff_time = StaffTime(access, notifications, portal_stamp)
staff_time.register(app, Workspace)
from .school_documents import SchoolDocuments
school_documents = SchoolDocuments(access, notifications, portal_stamp)
school_documents.register(app, Workspace)
from .library import Library
library = Library(access, notifications, portal_stamp)
library.register(app, Workspace)
from .lost_found import LostFound
lost_found = LostFound(access, notifications, portal_stamp)
lost_found.register(app, Workspace)
from .behaviour import Behaviour
behaviour = Behaviour(access, notifications, portal_stamp)
behaviour.register(app, Workspace)
from .clubs import Clubs
clubs = Clubs(access, notifications, portal_stamp)
clubs.register(app, Workspace)

from .school_site import SchoolSite
school_site = SchoolSite(access, portal_stamp)
school_site.register(app, Workspace)

from .admissions import Admissions
admissions = Admissions(access, portal_stamp)
admissions.register(app, Workspace)
from .payments import Payments
payments = Payments(access, notifications, portal_stamp)
payments.register(app, Workspace)
from .canteen import Canteen
canteen = Canteen(access, notifications, payments, portal_stamp)
canteen.register(app, Workspace)
from .handover import Handover
handover = Handover(access, notifications, portal_stamp)
handover.register(app, Workspace)
from .live_lessons import LiveLessons
live_lessons = LiveLessons(access, portal_stamp)
live_lessons.register(app, Workspace)

if (ROOT / 'dist').exists():
    app.mount('/', StaticFiles(directory=ROOT / 'dist', html=True), name='frontend')
