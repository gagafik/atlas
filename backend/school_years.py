"""School years: the current year, frozen past years and the move to a new one.

A workspace still has one State. The current year is State.settings.yearStart..yearEnd. A past year is a row of
school_years that holds the State document from the moment it was archived. Dated data (journal entries, attendance,
final results, journal periods, timetable versions, pupil enrolments) is never copied or rewritten: it stays where it
is, and the days up to the end of the latest archived year become read-only for every writer (journal_periods.locks,
timetable_history.capture, final grades). Nothing is written on read: a database without a stored current row has the
one virtual year derived from State, which is how every existing database starts (2026/27)."""
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import re
import sqlite3
from typing import Literal
from uuid import uuid4
from zoneinfo import ZoneInfo
from fastapi import HTTPException, Request
from pydantic import Field, ValidationError, model_validator
from .access import LOCAL, now
from .models import Model, SchoolClass, State
from .natural import natural
from .notifications import encoded
from . import journal_periods, timetable_history

GRADUATES = 'graduates-'


def tables(db):
    db.execute('''CREATE TABLE IF NOT EXISTS school_years (workspace TEXT,id TEXT,name TEXT,start_day TEXT,end_day TEXT,status TEXT,
        state_revision INTEGER,document TEXT,archived_at TEXT,actor TEXT,request_id TEXT,fingerprint TEXT,result TEXT,PRIMARY KEY(workspace,id))''')
    db.execute('CREATE INDEX IF NOT EXISTS school_year_request ON school_years(workspace,request_id)')
    db.execute('CREATE TABLE IF NOT EXISTS school_year_plans (workspace TEXT PRIMARY KEY,version INTEGER,updated TEXT,actor TEXT,document TEXT)')


def today(): return now().astimezone(ZoneInfo('Asia/Qyzylorda')).date()


def label(start, end): return f'{start.year}/{end.year % 100:02d}'


def missing(error):
    """Databases from before school years have neither table; reads treat that as no archive."""
    if 'no such table' not in str(error): raise error


def archived(db, workspace):
    try:
        rows = db.execute("SELECT id,name,start_day,end_day FROM school_years WHERE workspace=? AND status='archived' ORDER BY start_day DESC", (workspace,)).fetchall()
    except sqlite3.OperationalError as error:
        missing(error); return []
    return [dict(id=r[0], name=r[1], start=r[2], end=r[3]) for r in rows]


def archived_until(db, workspace):
    years = archived(db, workspace)
    return max((y['end'] for y in years), default=None)


def current(db, workspace, state):
    try: row = db.execute("SELECT id,name FROM school_years WHERE workspace=? AND status='current'", (workspace,)).fetchone()
    except sqlite3.OperationalError as error:
        missing(error); row = None
    start, end = state.settings.yearStart, state.settings.yearEnd
    return dict(id=row[0] if row else str(start.year), name=row[1] if row else label(start, end), start=start.isoformat(), end=end.isoformat())


def refusal(status, message, reason, **extra):
    return HTTPException(status, detail=dict(message=message, reason=reason, **extra))


def archived_error(year):
    return refusal(409, f"Учебный год {year['name']} в архиве: изменения запрещены.", 'archivedYear', year=year['id'])


def year_of(years, day):
    """The archived year holding the day; a day between years belongs to the latest one."""
    return next((y for y in years if y['start'] <= day <= y['end']), years[0] if years else None)


def require_current(db, workspace, start, end=None):
    years = archived(db, workspace)
    if years and start <= max(y['end'] for y in years): raise archived_error(year_of(years, start))


def refuse_into_archive(db, workspace, after):
    require_current(db, workspace, after.settings.yearStart.isoformat())


def lock(y):
    return dict(id='year:' + y['id'], name=y['name'] + ' (архив)', start=y['start'], end=y['end'], version=0, archived=True, year=y['id'], yearName=y['name'])


def archive_locks(db, workspace, days):
    """One lock per archived year that holds one of the ISO days (or lies before the end of the latest year)."""
    years = archived(db, workspace)
    if not years: return []
    until = max(y['end'] for y in years)
    found = {year_of(years, d)['id']: year_of(years, d) for d in days if d <= until}
    return [lock(y) for y in reversed(years) if y['id'] in found]


def archive_range_locks(db, workspace, start, end):
    years = archived(db, workspace)
    if not years or start > max(y['end'] for y in years): return []
    hit = [y for y in years if y['start'] <= end and y['end'] >= start] or [year_of(years, start)]
    return [lock(y) for y in reversed(hit)]


def bounds(db, workspace, state, year_id):
    """(start, end, archived) of the current year or of an archived one."""
    now_year = current(db, workspace, state)
    if year_id is None or year_id == now_year['id']: return state.settings.yearStart.isoformat(), state.settings.yearEnd.isoformat(), False
    year = next((y for y in archived(db, workspace) if y['id'] == year_id), None)
    if not year: raise HTTPException(404, 'Учебный год не найден.')
    return year['start'], year['end'], True


class YearPeriod(Model):
    name: str = Field(min_length=1, max_length=120)
    kind: Literal['quarter', 'semester', 'year', 'custom'] = 'custom'
    start: date
    end: date


class ClassStep(Model):
    classId: str = Field(min_length=1, max_length=100)
    action: Literal['promote', 'graduate', 'keep']
    name: str | None = Field(default=None, min_length=1, max_length=160)


class YearPlan(Model):
    start: date
    end: date
    periods: list[YearPeriod] = Field(default_factory=list, max_length=12)
    classes: list[ClassStep] = Field(default_factory=list, max_length=500)
    timetable: Literal['copy', 'empty'] = 'copy'

    @model_validator(mode='after')
    def valid(self):
        if not 0 < (self.end - self.start).days <= 366: raise ValueError('Учебный год должен длиться от 2 до 367 дней.')
        if any(p.end < p.start or p.start < self.start or p.end > self.end for p in self.periods): raise ValueError('Периоды должны входить в учебный год.')
        if len({s.classId for s in self.classes}) != len(self.classes): raise ValueError('Класс указан дважды.')
        if any(s.action == 'promote' and not s.name for s in self.classes): raise ValueError('Укажите новое название переводимого класса.')
        return self


class SavePlan(Model):
    version: int = Field(ge=0)
    plan: YearPlan


class PreviewYear(Model):
    revision: int = Field(ge=0)
    plan: YearPlan


class StartYear(PreviewYear):
    requestId: str = Field(pattern=r'^[a-f0-9]{32}$')
    acknowledgeWarnings: bool = False


def default_steps(state):
    """Every class moves up one grade; the top grade graduates; a class without a number stays."""
    grades = {c.id: re.match(r'^\s*(\d+)(.*)$', c.name) for c in state.classes}
    top = max((int(m.group(1)) for m in grades.values() if m), default=None)
    steps = []
    for c in sorted(state.classes, key=lambda c: (natural(c.name), c.id)):
        m = grades[c.id]
        if not m: steps.append(ClassStep(classId=c.id, action='keep'))
        elif int(m.group(1)) == top: steps.append(ClassStep(classId=c.id, action='graduate'))
        else: steps.append(ClassStep(classId=c.id, action='promote', name=f'{int(m.group(1)) + 1}{m.group(2)}'.strip()[:160]))
    return steps


def default_plan(state, periods):
    shift = timedelta(days=364)
    return YearPlan(start=state.settings.yearStart + shift, end=state.settings.yearEnd + shift, classes=default_steps(state), timetable='copy',
                    periods=[YearPeriod(name=name, kind=kind, start=start + shift, end=end + shift) for name, kind, start, end in periods[:12]])


def next_state(state, plan):
    """(candidate State, summary, problems). Nothing is written: the preview and the start run the same function."""
    problems = []
    if plan.start <= state.settings.yearEnd: problems.append(dict(code='dates'))
    steps = {s.classId: s for s in plan.classes}
    ids = {c.id for c in state.classes}
    absent = [c.name for c in state.classes if c.id not in steps] + sorted(i for i in steps if i not in ids)
    if absent: return None, None, problems + [dict(code='classesChanged', names=absent)]
    gone = {c.id for c in state.classes if steps[c.id].action == 'graduate'}
    year = state.settings.yearEnd.year
    classes = [c.model_copy(update=dict(name=steps[c.id].name)) if steps[c.id].action == 'promote' else c for c in state.classes if c.id not in gone]
    graduates = None
    if gone:
        first = min((c for c in state.classes if c.id in gone), key=lambda c: (natural(c.name), c.id))
        graduates = SchoolClass(id=f'{GRADUATES}{year}', name=f'Выпуск {year}', profileId=first.profileId)
        classes.append(graduates)
    names = [c.name.strip().casefold() for c in classes]
    duplicates = sorted({c.name for c in classes if names.count(c.name.strip().casefold()) > 1})
    if duplicates: problems.append(dict(code='duplicateName', names=duplicates))
    leaving = {s.id for s in state.students if s.classId in gone}
    moved = sum(1 for s in state.students if s.classId in gone and s.active)
    students = [s.model_copy(update=dict(classId=graduates.id, active=False)) if s.id in leaving else s for s in state.students]
    groups, dropped = [], set()
    for g in state.groups:
        if g.kind == 'staff': groups.append(g); continue
        left = [c for c in g.classIds if c not in gone]
        if left: groups.append(g.model_copy(update=dict(classIds=left, studentIds=[i for i in g.studentIds if i not in leaving])))
        else: dropped.add(g.id)
    workloads = [w for w in state.workloads if w.groupId not in dropped]
    loads = {w.id for w in workloads}
    placements = [] if plan.timetable == 'empty' else [p.model_copy(update=dict(classIds=[c for c in p.classIds if c not in gone])) for p in state.placements if p.workloadId in loads]
    valid = {'class': {c.id for c in classes}, 'teacher': {t.id for t in state.teachers}, 'student': {s.id for s in students}, 'group': {g.id for g in groups},
             'subject': {s.id for s in state.subjects}, 'room': {r.id for r in state.rooms}, 'workload': loads}
    rules = []
    for r in state.rules:
        targets = [t for t in r.targetIds if t in valid[r.scope]]
        if targets or not r.targetIds: rules.append(r.model_copy(update=dict(targetIds=targets)))
    courses = []
    for course in state.courses:
        sections = [s for s in course.sections if s.groupId not in dropped]
        if sections: courses.append(course.model_copy(update=dict(sections=sections)))
    events = []
    for e in state.events:
        kept = [c for c in e.classIds if c not in gone]
        if e.end >= plan.start and (kept or not e.classIds): events.append(e.model_copy(update=dict(classIds=kept)))
    candidate = state.model_copy(update=dict(
        settings=state.settings.model_copy(update=dict(yearStart=plan.start, yearEnd=plan.end)), cycles=state.cycles.model_copy(update=dict(periods=[])),
        classes=classes, students=students, groups=groups, workloads=workloads, placements=placements, rules=rules, courses=courses, events=events,
        courseRequests=[], datedLessons=[], absences=[], commitments=[], scheduleDays=[]))
    try: candidate = State.model_validate(candidate.model_dump())
    except ValidationError as error: problems.append(dict(code='invalidState', message=str(error.errors()[0]['msg'])))
    pupils = {c.id: sum(1 for s in state.students if s.classId == c.id and s.active) for c in state.classes}
    summary = dict(
        year=dict(id=str(plan.start.year), name=label(plan.start, plan.end), start=plan.start.isoformat(), end=plan.end.isoformat()),
        periods=[p.model_dump(mode='json') for p in plan.periods],
        classes=[dict(id=c.id, name=c.name, action=steps[c.id].action, newName=steps[c.id].name if steps[c.id].action == 'promote' else None, pupils=pupils[c.id])
                 for c in sorted(state.classes, key=lambda c: (natural(c.name), c.id))],
        graduates=moved, graduatesClass=dict(id=graduates.id, name=graduates.name) if graduates else None, timetable=plan.timetable,
        kept=dict(teachers=len(candidate.teachers), subjects=len(candidate.subjects), rooms=len(candidate.rooms), classes=len(candidate.classes), students=len(candidate.students),
                  activeStudents=sum(1 for s in candidate.students if s.active), groups=len(candidate.groups), workloads=len(candidate.workloads), placements=len(candidate.placements), events=len(candidate.events)),
        dropped=dict(groups=len(state.groups) - len(candidate.groups), workloads=len(state.workloads) - len(candidate.workloads), placements=len(state.placements) - len(candidate.placements),
                     datedLessons=len(state.datedLessons), absences=len(state.absences), commitments=len(state.commitments), scheduleDays=len(state.scheduleDays),
                     events=len(state.events) - len(candidate.events), courseRequests=len(state.courseRequests), teachingPeriods=len(state.cycles.periods)))
    return candidate, summary, problems


class SchoolYears:
    def __init__(self, access, journal):
        self.access, self.journal = access, journal

    def admin(self, request):
        user = request.state.user or LOCAL
        if user['role'] != 'admin': raise HTTPException(403, 'Новый учебный год начинает администратор.')
        return user

    def periods(self, db, workspace, state):
        # Only the running year: the periods of archived years stay in the table and must not shift into the next plan.
        try: return [(r[0], r[1], date.fromisoformat(r[2]), date.fromisoformat(r[3])) for r in db.execute('SELECT name,kind,start_day,end_day FROM journal_periods WHERE workspace=? AND start_day>=? AND end_day<=? ORDER BY start_day,end_day DESC,id', (workspace, state.settings.yearStart.isoformat(), state.settings.yearEnd.isoformat()))]
        except sqlite3.OperationalError as error:
            missing(error); return []

    def open_periods(self, db, workspace, state):
        try: return [r[0] for r in db.execute("SELECT name FROM journal_periods WHERE workspace=? AND status='open' AND start_day>=? AND end_day<=? ORDER BY start_day,id", (workspace, state.settings.yearStart.isoformat(), state.settings.yearEnd.isoformat()))]
        except sqlite3.OperationalError as error:
            missing(error); return []

    def draft(self, db, workspace):
        try: row = db.execute('SELECT version,document FROM school_year_plans WHERE workspace=?', (workspace,)).fetchone()
        except sqlite3.OperationalError as error:
            missing(error); return 0, None
        return (row[0], YearPlan.model_validate_json(row[1])) if row else (0, None)

    def enrolled(self, db, workspace, user, year):
        pupils = sorted(self.journal_ids(user, workspace))
        if not pupils: return False
        try:
            return bool(db.execute("SELECT 1 FROM pupil_enrollments WHERE workspace=? AND kind='class' AND student_id IN (SELECT value FROM json_each(?)) AND start_day<=? AND (end_day IS NULL OR end_day>=?)",
                                   (workspace, encoded(pupils), year['end'], year['start'])).fetchone())
        except sqlite3.OperationalError as error:
            missing(error); return False

    def journal_ids(self, user, workspace):
        from .journal import user_ids
        return user_ids(user, workspace, 'student')

    def register(self, app, Workspace):
        @app.get('/api/school-years')
        def listing(request: Request, workspace: Workspace = 'school'):
            user = request.state.user or LOCAL
            state = self.access.read(workspace)
            with self.access.read_connect() as db:
                db.execute('BEGIN')
                now_year = current(db, workspace, state)
                past = archived(db, workspace)
                # A family sees the years its children were in a class; an ended link is no link (effective grants).
                if user['role'] in ('student', 'parent'): past = [y for y in past if self.enrolled(db, workspace, user, y)]
            years = [dict(now_year, current=True, archived=False)] + [dict(y, current=False, archived=True) for y in past]
            return dict(current=now_year['id'], today=today().isoformat(), canManage=user['role'] == 'admin', years=years)

        @app.get('/api/school-years/plan')
        def get_plan(request: Request, workspace: Workspace = 'school'):
            self.admin(request)
            state = self.access.read(workspace)
            with self.access.read_connect() as db:
                db.execute('BEGIN')
                version, stored = self.draft(db, workspace)
                periods = self.periods(db, workspace, state)
                now_year = current(db, workspace, state)
            defaults = default_plan(state, periods)
            plan = stored or defaults
            have = {s.classId: s for s in plan.classes}
            plan = plan.model_copy(update=dict(classes=[have.get(s.classId, s) for s in defaults.classes]))
            pupils = {c.id: sum(1 for s in state.students if s.classId == c.id and s.active) for c in state.classes}
            return dict(version=version, saved=stored is not None, revision=state.revision, plan=plan, defaults=defaults, current=now_year, today=today().isoformat(), canStart=today() > state.settings.yearEnd,
                        classes=[dict(id=c.id, name=c.name, pupils=pupils[c.id]) for c in sorted(state.classes, key=lambda c: (natural(c.name), c.id))],
                        counts=dict(teachers=len(state.teachers), subjects=len(state.subjects), rooms=len(state.rooms), workloads=len(state.workloads), placements=len(state.placements)))

        @app.put('/api/school-years/plan')
        def save_plan(body: SavePlan, request: Request, workspace: Workspace = 'school'):
            user = self.admin(request)
            with self.access.lock, self.access.connect() as db:
                tables(db); db.execute('BEGIN IMMEDIATE'); self.journal.recheck_account(db, user)
                version, _ = self.draft(db, workspace)
                if version != body.version: raise HTTPException(409, 'Черновик изменён в другом окне. Обновите страницу.')
                db.execute('INSERT INTO school_year_plans VALUES (?,?,?,?,?) ON CONFLICT(workspace) DO UPDATE SET version=excluded.version,updated=excluded.updated,actor=excluded.actor,document=excluded.document',
                           (workspace, version + 1, now().isoformat(), user['name'], encoded(body.plan.model_dump(mode='json'))))
                self.access.record(db, 'school_year.plan_saved', workspace)
            return dict(version=version + 1, saved=True, plan=body.plan)

        @app.post('/api/school-years/preview')
        def preview(body: PreviewYear, request: Request, workspace: Workspace = 'school'):
            self.admin(request)
            state = self.access.read(workspace)
            if state.revision != body.revision: raise HTTPException(409, 'Данные изменились в другом окне. Обновите страницу.')
            with self.access.read_connect() as db:
                db.execute('BEGIN'); warnings = dict(openPeriods=self.open_periods(db, workspace, state))
            candidate, summary, problems = next_state(state, body.plan)
            return dict(summary=summary, problems=problems, warnings=warnings, canApply=not problems, canStart=today() > state.settings.yearEnd)

        @app.post('/api/school-years/start')
        def start(body: StartYear, request: Request, workspace: Workspace = 'school'):
            from .class_staff import record_teachers
            user = self.admin(request)
            key = hashlib.sha256(encoded(body.model_dump(mode='json')).encode()).hexdigest()
            with self.access.lock, self.access.connect() as db:
                tables(db); journal_periods.tables(db); timetable_history.tables(db)
                db.execute('BEGIN IMMEDIATE'); self.journal.recheck_account(db, user)
                row = db.execute('SELECT fingerprint,result FROM school_years WHERE workspace=? AND request_id=?', (workspace, body.requestId)).fetchone()
                if row:
                    if row[0] != key: raise HTTPException(409, 'Идентификатор уже использован другим действием.')
                    return json.loads(row[1])
                found = db.execute('SELECT revision,document FROM workspaces WHERE id=?', (workspace,)).fetchone()
                if not found or found[0] != body.revision: raise HTTPException(409, 'Данные изменились в другом окне. Обновите страницу.')
                before = State.model_validate_json(found[1])
                if today() <= before.settings.yearEnd: raise refusal(409, f'Новый учебный год можно начать после {before.settings.yearEnd:%d.%m.%Y}.', 'yearNotFinished')
                new_id = str(body.plan.start.year)
                if db.execute('SELECT 1 FROM school_years WHERE workspace=? AND id=?', (workspace, new_id)).fetchone(): raise refusal(409, 'Этот учебный год уже начат.', 'yearExists')
                candidate, summary, problems = next_state(before, body.plan)
                if problems: raise refusal(422, 'План нового года не готов к запуску.', 'plan', problems=problems)
                open_names = self.open_periods(db, workspace, before)
                if open_names and not body.acknowledgeWarnings: raise refusal(422, 'Есть открытые периоды текущего года.', 'warnings', warnings=dict(openPeriods=open_names))
                old = current(db, workspace, before)
                stamp = now().isoformat()
                db.execute('INSERT INTO history (workspace,created,document) VALUES (?,?,?)', (workspace, datetime.now(timezone.utc).isoformat(), found[1]))
                db.execute('DELETE FROM redo_history WHERE workspace=?', (workspace,))
                after = candidate.model_copy(update=dict(revision=before.revision + 1))
                db.execute('UPDATE workspaces SET revision=?,document=? WHERE id=?', (after.revision, after.model_dump_json(), workspace))
                db.execute('DELETE FROM history WHERE workspace=? AND id NOT IN (SELECT id FROM history WHERE workspace=? ORDER BY id DESC LIMIT 30)', (workspace, workspace))
                timetable_history.roll(db, workspace, before, after, body.plan.start)
                record_teachers(db, workspace, before, after, 'rollover')
                for p in body.plan.periods:
                    id = uuid4().hex
                    journal_periods.store(db, workspace, id, p.name, p.start.isoformat(), p.end.isoformat(), p.kind, 1, stamp)
                    journal_periods.log(db, self.access, workspace, journal_periods.find_item(db, workspace, id), 'created', user, f"Новый учебный год {summary['year']['name']}")
                db.execute("INSERT OR IGNORE INTO school_years (workspace,id,name,start_day,end_day,status,state_revision) VALUES (?,?,?,?,?,'current',?)", (workspace, old['id'], old['name'], old['start'], old['end'], before.revision))
                db.execute("UPDATE school_years SET status='archived',start_day=?,end_day=?,state_revision=?,document=?,archived_at=?,actor=? WHERE workspace=? AND id=?",
                           (old['start'], old['end'], before.revision, found[1], stamp, user['name'], workspace, old['id']))
                result = dict(year=summary['year'], archived=dict(id=old['id'], name=old['name'], start=old['start'], end=old['end']), revision=after.revision, summary=summary)
                db.execute("INSERT INTO school_years VALUES (?,?,?,?,?,'current',?,NULL,NULL,?,?,?,?)",
                           (workspace, new_id, summary['year']['name'], summary['year']['start'], summary['year']['end'], after.revision, user['name'], body.requestId, key, encoded(result)))
                db.execute('DELETE FROM school_year_plans WHERE workspace=?', (workspace,))
                self.access.record(db, 'school_year.started', workspace, after.revision, detail=f"{old['name']} в архиве · начат {summary['year']['name']}")
            return result
