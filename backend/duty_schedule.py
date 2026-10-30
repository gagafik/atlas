"""School duty roster over dated duties: weekly templates, idempotent generation, personal lists, print data, reminders, fairness."""
from collections import defaultdict
from contextlib import closing, contextmanager
from datetime import date, datetime, time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo
import hashlib, json, sqlite3

from fastapi import HTTPException
from pydantic import Field, model_validator
from .access import LOCAL, now
from .models import Model, State, Commitment
from .notifications import encoded
from .portal_cache import PortalSnapshots
from .rules import minutes
from .school_forms import enabled, grant_ids

ZONE = ZoneInfo('Asia/Qyzylorda')
HHMM = r'^([01]\d|2[0-3]):[0-5]\d$'
REQUEST = r'^[a-f0-9-]{36}$'
DEFAULT_SETTINGS = dict(enabled=True, daysBefore=1, remindAt='18:00', notifyChanges=True)
MAX_NEW = 3000
PREVIEW_MINUTES = 30
TABLES = [
    'CREATE TABLE IF NOT EXISTS duty_templates(workspace TEXT,id TEXT,version INTEGER,active INTEGER,created TEXT,updated TEXT,document TEXT,PRIMARY KEY(workspace,id))',
    'CREATE TABLE IF NOT EXISTS duty_generated(workspace TEXT,template_id TEXT,on_date TEXT,commitment_id TEXT,fingerprint TEXT,revision INTEGER,created TEXT,PRIMARY KEY(workspace,template_id,on_date))',
    'CREATE TABLE IF NOT EXISTS duty_previews(token TEXT PRIMARY KEY,workspace TEXT,created TEXT,document TEXT)',
    'CREATE TABLE IF NOT EXISTS duty_settings(workspace TEXT PRIMARY KEY,version INTEGER,document TEXT)',
    'CREATE TABLE IF NOT EXISTS duty_reminders(workspace TEXT,commitment_id TEXT,teacher_id TEXT,on_date TEXT,status TEXT,created TEXT,PRIMARY KEY(workspace,commitment_id,teacher_id,on_date))',
    'CREATE TABLE IF NOT EXISTS duty_swaps(seq INTEGER PRIMARY KEY AUTOINCREMENT,id TEXT UNIQUE,workspace TEXT,commitment_id TEXT,on_date TEXT,from_teacher TEXT,to_teacher TEXT,requester_id TEXT,status TEXT,version INTEGER,created TEXT,updated TEXT,document TEXT)',
    'CREATE INDEX IF NOT EXISTS duty_swaps_status ON duty_swaps(workspace,status,seq)',
    'CREATE INDEX IF NOT EXISTS duty_swaps_requester ON duty_swaps(workspace,requester_id,seq)',
    'CREATE TABLE IF NOT EXISTS duty_history(seq INTEGER PRIMARY KEY AUTOINCREMENT,workspace TEXT,entity TEXT,entity_id TEXT,actor_id TEXT,actor_name TEXT,created TEXT,action TEXT,reason TEXT,document TEXT)',
    'CREATE INDEX IF NOT EXISTS duty_history_item ON duty_history(workspace,entity,entity_id,seq)',
    'CREATE TABLE IF NOT EXISTS duty_requests(seq INTEGER PRIMARY KEY AUTOINCREMENT,user_id TEXT,request_id TEXT,fingerprint TEXT,result TEXT,UNIQUE(user_id,request_id))',
]


def local_now(): return now().astimezone(ZONE)
def stamp(): return now().isoformat()
def preview_cutoff(): return (now() - timedelta(minutes=PREVIEW_MINUTES)).isoformat()
def fingerprint(value): return hashlib.sha256(encoded(value).encode()).hexdigest()


def tables(db):
    # Plain statements, not executescript: the change notifier calls this inside a save transaction.
    for sql in TABLES: db.execute(sql)


def reason_of(exc):
    """The message of a failed model validation without pydantic's technical prefix."""
    errors = exc.errors() if hasattr(exc, 'errors') else []
    return str(errors[0]['msg'] if errors else exc).removeprefix('Value error, ')


class DutyTemplate(Model):
    name: str = Field(min_length=1, max_length=160)
    weekday: int = Field(ge=0, le=6)
    profileId: str = Field(min_length=1, max_length=100)
    afterPeriod: int | None = Field(default=None, ge=0, le=11)
    startTime: str | None = Field(default=None, pattern=HHMM)
    endTime: str | None = Field(default=None, pattern=HHMM)
    location: str = Field(default='', max_length=160)
    roomIds: list[str] = Field(default_factory=list, max_length=100)
    buildingId: str | None = None
    requiredTeachers: int = Field(default=1, ge=1, le=50)
    eligibleTeacherIds: list[str] = Field(default_factory=list, max_length=300)
    teacherIds: list[str] = Field(default_factory=list, max_length=50)
    cycleIds: list[str] = Field(default_factory=list, max_length=8)
    note: str = Field(default='', max_length=4000)

    @model_validator(mode='after')
    def valid_template(self):
        if self.afterPeriod is not None:
            if self.startTime or self.endTime: raise ValueError('Укажите либо перемену после урока, либо время дежурства.')
        elif not (self.startTime and self.endTime): raise ValueError('Укажите время дежурства или перемену после урока.')
        elif self.endTime <= self.startTime: raise ValueError('Конец дежурства должен быть позже начала.')
        if not self.location and not self.roomIds: raise ValueError('Укажите место дежурства: название или кабинеты.')
        for values in [self.roomIds, self.eligibleTeacherIds, self.teacherIds, self.cycleIds]:
            if len(values) != len(set(values)): raise ValueError('Списки шаблона не должны содержать повторов.')
        if self.eligibleTeacherIds and not set(self.teacherIds) <= set(self.eligibleTeacherIds):
            raise ValueError('Постоянные дежурные должны входить в список кандидатов.')
        if len(self.teacherIds) > self.requiredTeachers: raise ValueError('Постоянных дежурных не может быть больше, чем требуется.')
        return self


class StoredTemplate(DutyTemplate):
    id: str


class TemplateSave(Model):
    requestId: str = Field(pattern=REQUEST)
    id: str | None = Field(default=None, pattern=r'^[a-f0-9]{16}$')
    version: int = Field(default=0, ge=0)
    template: DutyTemplate
    reason: str = Field(default='', max_length=500)


class TemplateArchive(Model):
    requestId: str = Field(pattern=REQUEST)
    version: int = Field(ge=1)
    reason: str = Field(default='', max_length=500)


class GeneratePreview(Model):
    revision: int
    templateIds: list[str] = Field(min_length=1, max_length=100)
    start: date
    end: date
    skipKinds: list[Literal['holiday', 'winter', 'reading', 'extra', 'exam']] = Field(default_factory=lambda: ['holiday', 'winter'], max_length=5)
    excludeDates: list[date] = Field(default_factory=list, max_length=60)
    restoreRemoved: bool = False
    updateUntouched: bool = False


class GenerateApply(Model):
    requestId: str = Field(pattern=REQUEST)
    revision: int
    token: str = Field(min_length=1, max_length=100)


class SettingsSave(Model):
    version: int = Field(ge=0)
    enabled: bool = True
    daysBefore: Literal[0, 1] = 1
    remindAt: str = Field(default='18:00', pattern=HHMM)
    notifyChanges: bool = True
    reason: str = Field(default='', max_length=500)


def settings(db, workspace):
    row = db.execute('SELECT version,document FROM duty_settings WHERE workspace=?', (workspace,)).fetchone()
    return DEFAULT_SETTINGS | (json.loads(row[1]) if row else {}) | dict(version=row[0] if row else 0)


def labeller(state):
    """Place text of a duty: its own name or the rooms, then the building."""
    rooms = {r.id: r.name for r in state.rooms}
    buildings = {b.id: b.name for b in state.campus.buildings}
    def label(c):
        text = c.location or ', '.join(rooms[r] for r in c.roomIds if r in rooms)
        return text + (' · ' + buildings[c.buildingId] if c.buildingId in buildings else '')
    return label


def place_key(c):
    key = c.location.casefold() if c.location else 'rooms:' + ','.join(sorted(c.roomIds))
    return key + ('|' + c.buildingId if c.buildingId else '')


def duty_line(c, label):
    return f'{c.onDate:%d.%m.%Y}, {c.startTime}–{c.endTime}, {label(c)}'


def duty_text(state, c, current, label=None):
    """Notice text of one duty; never the private note."""
    today = current.date()
    prefix = 'Сегодня: ' if c.onDate == today else 'Завтра: ' if c.onDate == today + timedelta(days=1) else ''
    return prefix + duty_line(c, label or labeller(state))


def starts_at(c):
    return datetime.combine(c.onDate, time.fromisoformat(c.startTime), ZONE)


def reminder_due(config, c, current):
    """From the configured moment until the duty starts."""
    due = datetime.combine(c.onDate - timedelta(days=config['daysBefore']), time.fromisoformat(config['remindAt']), ZONE)
    return due <= current < starts_at(c)


def closure_on(state, day, kinds, everyone=None):
    """The event that closes the whole school that day: a cancellation, or a chosen kind, for every class."""
    everyone = everyone if everyone is not None else {c.id for c in state.classes}
    return next((e for e in state.events if e.start <= day <= e.end and (e.effect == 'cancel' or e.kind in kinds)
                 and (not e.classIds or set(e.classIds) >= everyone)), None)


def template_times(profile, t, weekday):
    """Clock times of a template on a weekday; None when the break after the lesson no longer exists."""
    if t.afterPeriod is None: return t.startTime, t.endTime
    bells = profile.for_day(weekday)
    if t.afterPeriod + 1 >= len(bells) or bells[t.afterPeriod].end >= bells[t.afterPeriod + 1].start: return None
    return bells[t.afterPeriod].end, bells[t.afterPeriod + 1].start


def template_fields(t, day, start, end, rooms, eligible, building):
    return dict(name=t.name, onDate=day, startTime=start, endTime=end, location=t.location, roomIds=sorted(rooms), buildingId=building,
                requiredTeachers=t.requiredTeachers, eligibleTeacherIds=sorted(eligible), profileId=t.profileId, note=t.note)


def commitment_fields(c):
    return dict(name=c.name, onDate=c.onDate, startTime=c.startTime, endTime=c.endTime, location=c.location, roomIds=sorted(c.roomIds), buildingId=c.buildingId,
                requiredTeachers=c.requiredTeachers, eligibleTeacherIds=sorted(c.eligibleTeacherIds), profileId=c.profileId, note=c.note)


def plan_generation(state, templates, start, end, generated, body):
    """Duties the templates would add for the dates. Never touches the state it is given.

    generated maps (templateId, isoDate) to dict(commitmentId, fingerprint) of earlier runs.
    """
    from .calendar_ops import DayContext, cycle_on, resolve_day, weekday_on
    from .commitments import duty_conflicts
    if end < start: raise ValueError('Конец периода раньше начала.')
    if (end - start).days > 366: raise ValueError('Период не длиннее 366 дней.')
    if start < state.settings.yearStart or end > state.settings.yearEnd: raise ValueError('Период должен быть внутри учебного года.')
    kinds, excluded = set(body.skipKinds), set(body.excludeDates)
    everyone = {c.id for c in state.classes}
    profiles = {p.id: p for p in state.profiles}
    names = {t.id: t.name for t in state.teachers}
    rooms_known, buildings = {r.id for r in state.rooms}, {b.id for b in state.campus.buildings}
    by_id = {c.id: c for c in state.commitments}
    duties_on = defaultdict(list)
    for c in state.commitments:
        if c.kind == 'duty': duties_on[c.onDate].append(c)
    planned_rows, busy_rows, holder = defaultdict(list), {}, {}
    created, updated, removed, duplicates, skipped, warnings, keys, replaced = [], [], [], [], [], [], [], {}
    existing, partial = 0, set()

    def context():
        if 'ctx' not in holder: holder['ctx'] = DayContext(state)
        return holder['ctx']

    def rows_for(day):
        if day not in busy_rows: busy_rows[day] = [r for r in resolve_day(state, day, context())[0] if r['status'] != 'cancelled']
        return busy_rows[day] + planned_rows[day]

    for t in sorted(templates, key=lambda t: (t.name.casefold(), t.id)):
        profile = profiles.get(t.profileId)
        if t.weekday >= len(state.settings.days):
            warnings.append(f'Шаблон «{t.name}»: день недели вне учебной недели школы, дежурства не созданы.')
            continue
        gone = (set(t.roomIds) - rooms_known) | ((set(t.eligibleTeacherIds) | set(t.teacherIds)) - names.keys())
        if gone: warnings.append(f'Шаблон «{t.name}»: кабинеты или сотрудники шаблона удалены из расписания и пропущены.')
        for offset in range((end - start).days + 1):
            day = start + timedelta(days=offset)
            # A school-wide «день в расписании» gives the date another weekday's duties, bells and week.
            wd = weekday_on(state, day)
            if wd != t.weekday: continue
            row = dict(date=day.isoformat(), templateId=t.id, template=t.name)
            if day in excluded: skipped.append(row | dict(reason='Исключено вручную')); continue
            pattern = cycle_on(state, day)
            if pattern is None: skipped.append(row | dict(reason='Вне учебного периода')); continue
            if t.cycleIds and pattern not in t.cycleIds: continue
            closure = closure_on(state, day, kinds, everyone)
            if closure: skipped.append(row | dict(reason=closure.name)); continue
            for e in state.events:
                if e.effect == 'cancel' and e.start <= day <= e.end and (e.id, day) not in partial:
                    partial.add((e.id, day))
                    warnings.append(f'Частичная отмена: {e.name} · {day:%d.%m.%Y}. Проверьте классы события.')
            if profile is None: skipped.append(row | dict(reason='Профиль звонков удалён')); continue
            times = template_times(profile, t, wd)
            if times is None: skipped.append(row | dict(reason='Нет перемены')); continue
            begin, finish = times
            rooms = [r for r in t.roomIds if r in rooms_known]
            eligible = [x for x in t.eligibleTeacherIds if x in names]
            if t.eligibleTeacherIds and not eligible: skipped.append(row | dict(reason='Кандидаты шаблона больше не существуют')); continue
            permanent = [x for x in t.teacherIds if x in names and (not eligible or x in eligible)]
            if not t.location and not rooms: skipped.append(row | dict(reason='Место шаблона больше не существует')); continue
            building = t.buildingId if t.buildingId in buildings else None
            cid, key = f'duty-{t.id}-{day:%Y%m%d}', (t.id, day.isoformat())
            fields = template_fields(t, day, begin, finish, rooms, eligible, building)
            digest = fingerprint(fields)
            current = by_id.get(cid)
            if current is not None:
                made = generated.get(key)
                if (body.updateUntouched and made and current.kind == 'duty' and not current.locked and made['fingerprint'] != digest
                        and fingerprint(commitment_fields(current)) == made['fingerprint']):
                    # Only the template's own fields change; groups, student count and the like are kept as the administration set them.
                    try: replacement = Commitment.model_validate(current.model_dump() | fields | dict(teacherIds=[x for x in current.teacherIds if not eligible or x in eligible]))
                    except ValueError as exc: skipped.append(row | dict(reason=reason_of(exc))); continue
                    updated.append(replacement); replaced[cid] = replacement; keys.append((t.id, day.isoformat(), cid, digest))
                else: existing += 1
                continue
            if key in generated and not body.restoreRemoved: removed.append(row | dict(reason='Удалено вручную после прошлого создания')); continue
            clash = next((d for d in duties_on[day] if d.startTime < finish and begin < d.endTime
                          and (t.location and d.location.casefold() == t.location.casefold() or set(rooms) & set(d.roomIds))), None)
            if clash: duplicates.append(row | dict(reason=f'Уже есть дежурство «{clash.name}» в это время и месте')); continue
            try:
                probe = Commitment(id=cid, kind='duty', teacherIds=[], **fields)
                assigned = []
                for teacher in permanent:
                    reasons = duty_conflicts(state, context(), rows_for(day), probe, teacher)
                    if reasons: warnings.append(f'{names[teacher]} · {day:%d.%m.%Y}: {", ".join(reasons)}; место осталось незаполненным')
                    else: assigned.append(teacher)
                duty = Commitment(id=cid, kind='duty', teacherIds=assigned, **fields)
            except ValueError as exc:
                skipped.append(row | dict(reason=reason_of(exc))); continue
            created.append(duty); duties_on[day].append(duty); keys.append((t.id, day.isoformat(), cid, digest))
            planned_rows[day].append(dict(teacherIds=assigned, start=begin, end=finish))
            if len(created) > MAX_NEW: raise ValueError(f'За один раз можно создать не больше {MAX_NEW} дежурств. Сократите период.')
    if len(state.commitments) + len(created) > 20000: raise ValueError('В плане не может быть больше 20000 бронирований и дежурств.')
    created.sort(key=lambda c: (c.onDate, c.startTime, c.name, c.id))
    commitments = [replaced.get(c.id, c) for c in state.commitments] + created
    candidate = State.model_validate(state.model_dump() | dict(commitments=[c.model_dump() for c in commitments]))
    order = lambda r: (r['date'], r['template'], r['templateId'])
    if len(warnings) > 100: warnings = warnings[:100] + [f'И ещё предупреждений: {len(warnings) - 100}.']
    return dict(candidate=candidate, created=created, updated=updated, existing=existing, removed=sorted(removed, key=order), duplicates=sorted(duplicates, key=order),
                skipped=sorted(skipped, key=order), warnings=warnings, keys=keys)


def roster(state, start, end):
    """Print data: duties of a short period as a place by day grid and as a list per teacher."""
    label = labeller(state)
    names = {t.id: t.name for t in state.teachers}
    duties = sorted((c for c in state.commitments if c.kind == 'duty' and start <= c.onDate <= end), key=lambda c: (c.onDate, c.startTime, c.name, c.id))
    day_names = state.settings.days
    busy = {c.onDate for c in duties}
    days, day = [], start
    while day <= end:
        if day.weekday() < len(day_names) or day in busy:
            closure = closure_on(state, day, ['holiday', 'winter'])
            days.append(dict(date=day.isoformat(), weekday=day.weekday(), name=day_names[day.weekday()] if day.weekday() < len(day_names) else '', closed=closure.name if closure else ''))
        day += timedelta(days=1)
    places, cells, people = {}, defaultdict(list), {}
    for c in duties:
        key = place_key(c)
        places.setdefault(key, label(c))
        missing = max(0, c.requiredTeachers - len(c.teacherIds))
        cells[key, c.onDate.isoformat()].append(dict(id=c.id, start=c.startTime, end=c.endTime, name=c.name, teachers=[names[t] for t in c.teacherIds], required=c.requiredTeachers, missing=missing))
        for t in c.teacherIds:
            person = people.setdefault(t, dict(teacherId=t, name=names[t], minutes=0, items=[]))
            person['minutes'] += minutes(c.endTime) - minutes(c.startTime)
            person['items'].append(dict(date=c.onDate.isoformat(), weekday=day_names[c.onDate.weekday()] if c.onDate.weekday() < len(day_names) else '', start=c.startTime, end=c.endTime, place=label(c)))
    return dict(school=state.settings.name, start=start.isoformat(), end=end.isoformat(), days=days,
                places=[dict(key=k, label=v) for k, v in sorted(places.items(), key=lambda x: (x[1].casefold(), x[0]))],
                cells=[dict(place=k[0], date=k[1], items=v) for k, v in sorted(cells.items())],
                teachers=sorted(people.values(), key=lambda p: (p['name'].casefold(), p['teacherId'])),
                unfilled=sum(i['missing'] for v in cells.values() for i in v))


def fairness(state, start, end):
    """Duty minutes per teacher, including eligible teachers who have none."""
    if end < start or (end - start).days > 366: raise ValueError('Период не длиннее 366 дней.')
    names = {t.id: t.name for t in state.teachers}
    duties = [c for c in state.commitments if c.kind == 'duty' and start <= c.onDate <= end]
    people = {t for c in duties for t in [*c.teacherIds, *(c.eligibleTeacherIds or names)]} & names.keys()
    rows = {t: dict(teacherId=t, name=names[t], duties=0, minutes=0, places=set(), firstDate=None, lastDate=None) for t in people}
    required = assigned = unfilled = 0
    for c in duties:
        length = minutes(c.endTime) - minutes(c.startTime)
        required += c.requiredTeachers * length; assigned += len(c.teacherIds) * length; unfilled += max(0, c.requiredTeachers - len(c.teacherIds))
        for t in c.teacherIds:
            row, day = rows[t], c.onDate.isoformat()
            row['duties'] += 1; row['minutes'] += length; row['places'].add(place_key(c))
            row['firstDate'] = min(row['firstDate'] or day, day); row['lastDate'] = max(row['lastDate'] or day, day)
    mean = round(assigned / len(people), 1) if people else 0
    result = sorted(({**r, 'places': len(r['places']), 'deviation': round(r['minutes'] - mean)} for r in rows.values()), key=lambda r: (-r['minutes'], r['name'].casefold(), r['teacherId']))
    return dict(start=start.isoformat(), end=end.isoformat(), rows=result,
                summary=dict(requiredMinutes=required, assignedMinutes=assigned, unfilledPlaces=unfilled, mean=mean,
                             max=max((r['minutes'] for r in result), default=0), min=min((r['minutes'] for r in result), default=0), people=len(people)))


def notify_changes(notifications, db, workspace, before, after):
    """Runs inside the save transaction: one contentful notice per affected account and save."""
    if before.commitments == after.commitments: return
    tables(db)
    config = settings(db, workspace)
    if not config['notifyChanges']: return
    current = local_now()
    today = current.date()
    old = {c.id: c for c in before.commitments if c.kind == 'duty' and c.onDate >= today}
    new = {c.id: c for c in after.commitments if c.kind == 'duty' and c.onDate >= today}
    shape = lambda c: (c.onDate, c.startTime, c.endTime, c.location, sorted(c.roomIds), c.buildingId)
    changes = defaultdict(lambda: dict(added=[], moved=[], removed=[]))
    for id in sorted(old.keys() | new.keys()):
        a, b = old.get(id), new.get(id)
        had, has = set(a.teacherIds) if a else set(), set(b.teacherIds) if b else set()
        for teacher in has - had: changes[teacher]['added'].append(b)
        for teacher in had - has: changes[teacher]['removed'].append(a)
        if a and b and shape(a) != shape(b):
            for teacher in had & has: changes[teacher]['moved'].append(b)
    # Without accounts (the local user) there is nobody to tell and nothing to remind.
    if not changes or not db.execute('SELECT 1 FROM accounts LIMIT 1').fetchone(): return
    labels = dict(old=labeller(before), new=labeller(after))
    if config['enabled']:
        # The notice below already states these duties, so the worker must not repeat them.
        for teacher, parts in changes.items():
            for c in parts['added'] + parts['moved']:
                if reminder_due(config, c, current):
                    db.execute("INSERT OR IGNORE INTO duty_reminders VALUES (?,?,?,?,'sent',?)", (workspace, c.id, teacher, c.onDate.isoformat(), stamp()))
    for user in notifications.granted_users(db, workspace, 'teacher', list(changes), ['teacher']):
        mine = grant_ids(user, workspace, 'teacher') & changes.keys()
        merged = {kind: {c.id: c for teacher in sorted(mine) for c in changes[teacher][kind]} for kind in ['added', 'moved', 'removed']}
        sentences = []
        for kind, title, source in [('added', 'Назначено', 'new'), ('moved', 'Изменено', 'new'), ('removed', 'Снято', 'old')]:
            items = sorted(merged[kind].values(), key=lambda c: (c.onDate, c.startTime, c.id))
            if items: sentences.append(f'{title}: {duty_line(items[0], labels[source])}' + (f' (и ещё {len(items) - 1})' if len(items) > 1 else '') + '.')
        notifications.deliver(db, [user], workspace, after.revision, 'duty.changed', target=dict(module='duty', title=' '.join(sentences)))


class DutySchedule:
    def __init__(self, access, notifications, stamp, read, save):
        self.access, self.notifications = access, notifications
        self.snapshots = PortalSnapshots(access.read, stamp)
        self.read, self.save = read, save

    def prepare(self):
        with closing(self.access.read_connect()) as db:
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='duty_swaps_requester'").fetchone(): return
        with self.access.lock, self.access.connect() as db: tables(db); self.access.tables(db); self.notifications.tables(db)

    def identify(self, request):
        user = request.state.user or (None if getattr(request.state, 'auth_enabled', True) else LOCAL)
        if not user: raise HTTPException(401, 'Войдите в учётную запись.')
        return user

    def recheck(self, db, user):
        """Same account (or still no accounts at all for the local user) inside the writing transaction."""
        if user['id'] == 'local':
            if db.execute('SELECT 1 FROM accounts LIMIT 1').fetchone(): raise HTTPException(409, 'Доступ или данные школы изменились. Обновите страницу.')
            return
        row = db.execute('SELECT * FROM accounts WHERE id=? AND active=1', (user['id'],)).fetchone()
        if not row or self.access.public(row) != user: raise HTTPException(409, 'Доступ или данные школы изменились. Обновите страницу.')

    @contextmanager
    def context(self, request, workspace, write=False):
        user = self.identify(request)
        snap = self.snapshots.get(workspace)
        if user['role'] not in ['admin', 'editor', 'teacher']: raise HTTPException(403, 'Раздел доступен администрации расписания и учителям.')
        if user['id'] != 'local' and not enabled(user, snap, workspace): raise HTTPException(403, 'Нет доступа к этому пространству.')
        self.prepare()
        def check(db):
            self.recheck(db, user)
            rev = db.execute('SELECT revision FROM workspaces WHERE id=?', (workspace,)).fetchone()
            if not rev or rev[0] != snap.state.revision: raise HTTPException(409, 'Доступ или данные школы изменились. Обновите страницу.')
            db.row_factory = sqlite3.Row
        if write:
            with self.access.lock, self.access.connect() as db: db.execute('BEGIN IMMEDIATE'); check(db); yield db, user, snap
        else:
            with closing(self.access.read_connect()) as db: db.execute('BEGIN'); check(db); yield db, user, snap

    def manager(self, user, workspace):
        return user['role'] == 'admin' or user['role'] == 'editor' and workspace in user['workspaces']

    def require_manager(self, user, workspace):
        if not self.manager(user, workspace): raise HTTPException(403, 'Раздел ведёт администрация расписания.')

    def own_teachers(self, user, snap, workspace):
        return grant_ids(user, workspace, 'teacher') & snap.entities['teacher'].keys() if user['role'] == 'teacher' else set()

    def replay(self, db, user, body, operation):
        key = fingerprint([operation, body.model_dump(mode='json')])
        row = db.execute('SELECT fingerprint,result FROM duty_requests WHERE user_id=? AND request_id=?', (user['id'], body.requestId)).fetchone()
        if row:
            if row['fingerprint'] != key: raise HTTPException(409, 'Этот идентификатор запроса уже использован.')
            return key, json.loads(row['result'])
        return key, None

    def remember(self, db, user, body, key, result):
        db.execute('INSERT INTO duty_requests(user_id,request_id,fingerprint,result) VALUES (?,?,?,?)', (user['id'], body.requestId, key, encoded(result)))
        db.execute('DELETE FROM duty_requests WHERE user_id=? AND seq NOT IN (SELECT seq FROM duty_requests WHERE user_id=? ORDER BY seq DESC LIMIT 100)', (user['id'], user['id']))
        return result

    def history(self, db, workspace, user, entity, id, action, reason, document):
        db.execute('INSERT INTO duty_history(workspace,entity,entity_id,actor_id,actor_name,created,action,reason,document) VALUES (?,?,?,?,?,?,?,?,?)',
                   (workspace, entity, id, user['id'], user['name'], stamp(), action, reason, encoded(document)))

    def public_template(self, row):
        # Exactly the template fields plus id, version and active: the client posts the fields back when editing.
        return dict(id=row['id'], version=row['version'], active=bool(row['active'])) | json.loads(row['document'])

    def register(self, app, Workspace):
        from .duty_schedule_routes import register
        from .duty_swaps import register as register_swaps
        from .duty_reminders import DutyReminders
        register(self, app, Workspace)
        register_swaps(self, app, Workspace)
        self.reminders = DutyReminders(self)
        self.reminders.register(app)
