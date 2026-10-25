"""Dated timetable versions and pupil enrollment periods behind the journal.

capture() runs inside the save transaction of api.py. History.context(day) is the
one place that decides which template and which pupils apply to a date; days from
the last boundary on resolve against the unchanged current State.
"""
from bisect import bisect_right
from collections import OrderedDict, defaultdict
from contextlib import closing
from datetime import date, timedelta
import json
import sqlite3
from pathlib import Path
from threading import RLock
from zoneinfo import ZoneInfo
from fastapi import HTTPException
from .access import now
from .calendar_ops import DayContext
from .models import Cycles, Group, Placement, Profile, Room, SchoolClass, Subject, Teacher, Workload
from .notifications import encoded

# The version's lists replace the current ones.
REPLACED = dict(placements=Placement, workloads=Workload)
# The version's items, plus ids that exist only now.
VERSION_WINS = dict(groups=Group, profiles=Profile)
# The current items, plus ids that exist only in the version (deleted since).
CURRENT_WINS = dict(subjects=Subject, teachers=Teacher, rooms=Room, classes=SchoolClass)
CONTEXTS = 16
BASES = 8


def today(): return now().astimezone(ZoneInfo('Asia/Qyzylorda')).date()


def tables(db):
    db.execute('CREATE TABLE IF NOT EXISTS timetable_versions (workspace TEXT,valid_from TEXT,valid_to TEXT,revision INTEGER,created TEXT,document TEXT,PRIMARY KEY(workspace,valid_from))')
    db.execute('CREATE TABLE IF NOT EXISTS timetable_current (workspace TEXT PRIMARY KEY,valid_from TEXT)')
    # kind is class or group; end_day is inclusive and NULL while the membership lasts.
    db.execute('CREATE TABLE IF NOT EXISTS pupil_enrollments (workspace TEXT,student_id TEXT,kind TEXT,target_id TEXT,start_day TEXT,end_day TEXT,PRIMARY KEY(workspace,student_id,kind,target_id,start_day))')
    db.execute('CREATE INDEX IF NOT EXISTS pupil_enrollment_open ON pupil_enrollments(workspace,kind,end_day)')


def memberships(state):
    """Inactive pupils get no class period, which matches DayContext."""
    return ({(s.id, 'class', s.classId) for s in state.students if s.active} |
            {(sid, 'group', g.id) for g in state.groups if g.kind not in ('whole', 'staff') for sid in g.studentIds})


def template(state):
    result = {key: [x.model_dump(mode='json') for x in getattr(state, key)] for key in [*REPLACED, *VERSION_WINS, *CURRENT_WINS]}
    return result | dict(cycles=state.cycles.model_dump(mode='json'))


def changed(before, after):
    """Renames and group rosters are not versions: membership has its own periods."""
    return (any(getattr(before, key) != getattr(after, key) for key in ['placements', 'workloads', 'profiles', 'cycles']) or
            [(g.id, g.kind, g.classIds) for g in before.groups] != [(g.id, g.kind, g.classIds) for g in after.groups])


def backfill(db, workspace, state):
    if db.execute('SELECT 1 FROM timetable_current WHERE workspace=?', (workspace,)).fetchone(): return
    start = state.settings.yearStart.isoformat()
    db.execute('INSERT INTO timetable_current VALUES (?,?)', (workspace, start))
    db.executemany('INSERT OR IGNORE INTO pupil_enrollments VALUES (?,?,?,?,?,NULL)', [(workspace, sid, kind, target, start) for sid, kind, target in sorted(memberships(state))])


def place(db, workspace, student, kind, target, day, member):
    """From day on the pupil is (or is not) in target; a class change leaves every other class."""
    where = 'workspace=? AND student_id=? AND kind=?' + ('' if kind == 'class' else ' AND target_id=?')
    args = [workspace, student, kind] + ([] if kind == 'class' else [target])
    before = (day - timedelta(days=1)).isoformat()
    # Periods that start on or after the day never took effect.
    db.execute(f'DELETE FROM pupil_enrollments WHERE {where} AND start_day>=?', args + [day.isoformat()])
    db.execute(f'UPDATE pupil_enrollments SET end_day=? WHERE {where} AND (end_day IS NULL OR end_day>=?)', [before] + args + [day.isoformat()])
    if member and not db.execute('UPDATE pupil_enrollments SET end_day=NULL WHERE workspace=? AND student_id=? AND kind=? AND target_id=? AND end_day=?', (workspace, student, kind, target, before)).rowcount:
        db.execute('INSERT INTO pupil_enrollments VALUES (?,?,?,?,?,NULL)', (workspace, student, kind, target, day.isoformat()))


def capture(db, workspace, before, after, effective=None):
    """Store the replaced template and date enrollment changes in the caller's write transaction."""
    tables(db);backfill(db, workspace, before)
    current = date.fromisoformat(db.execute('SELECT valid_from FROM timetable_current WHERE workspace=?', (workspace,)).fetchone()[0])
    if effective is not None:
        if not after.settings.yearStart <= effective <= after.settings.yearEnd:
            raise HTTPException(422, 'Дата начала действия должна быть внутри учебного года.')
        if effective < current:
            raise HTTPException(422, f'Изменения не могут действовать раньше текущей версии расписания ({current:%d.%m.%Y}).')
        if effective > max(today(), current):
            raise HTTPException(422, 'Дата начала действия не может быть позже сегодняшнего дня.')
    day, stored = effective or current, None
    if day > current and changed(before, after):
        db.execute('INSERT INTO timetable_versions (workspace,valid_from,valid_to,revision,created,document) VALUES (?,?,?,?,?,?)',
                   (workspace, current.isoformat(), day.isoformat(), after.revision, now().isoformat(), encoded(template(before))))
        db.execute('UPDATE timetable_current SET valid_from=? WHERE workspace=?', (day.isoformat(), workspace))
        stored = current, day
    # Compare with the periods in the database, not with the previous State.
    open_class = dict(db.execute("SELECT student_id,target_id FROM pupil_enrollments WHERE workspace=? AND kind='class' AND end_day IS NULL", (workspace,)))
    desired = {s.id: s.classId for s in after.students if s.active}
    for sid in sorted(set(open_class) | set(desired)):
        if open_class.get(sid) != desired.get(sid):
            place(db, workspace, sid, 'class', desired.get(sid) or open_class[sid], day, sid in desired)
    open_groups = set(db.execute("SELECT student_id,target_id FROM pupil_enrollments WHERE workspace=? AND kind='group' AND end_day IS NULL", (workspace,)))
    groups = {(sid, target) for sid, kind, target in memberships(after) if kind == 'group'}
    for sid, target in sorted(open_groups - groups): place(db, workspace, sid, 'group', target, day, False)
    for sid, target in sorted(groups - open_groups): place(db, workspace, sid, 'group', target, day, True)
    return stored


class Stale(Exception):
    """The database holds another revision than the state."""


def overlay(state, doc):
    """The current State with the template of an earlier version; references across versions are intentionally mixed."""
    update = {key: [model.model_validate(x) for x in doc[key]] for key, model in REPLACED.items()}
    for key, model in VERSION_WINS.items():
        items = [model.model_validate(x) for x in doc[key]]
        ids = {x.id for x in items}
        update[key] = items + [x for x in getattr(state, key) if x.id not in ids]
    for key, model in CURRENT_WINS.items():
        current = getattr(state, key)
        ids = {x.id for x in current}
        update[key] = current + [model.model_validate(x) for x in doc[key] if x['id'] not in ids]
    return state.model_copy(update=update | dict(cycles=Cycles.model_validate(doc['cycles'])))


class History:
    """stints are (student, kind, target, start, end) with an inclusive end; document maps a version start to its stored template."""
    def __init__(self, state, current_from=None, versions=(), stints=(), document=None):
        self.state, self.current_from, self.document = state, current_from, document
        self.versions, self.stints = sorted(versions), list(stints)
        self.first = state.settings.yearStart
        self.bounds = sorted({to for _, to in self.versions} | {s[3] for s in self.stints if s[3] > self.first} |
                             {s[4] + timedelta(days=1) for s in self.stints if s[4]})
        self.lock, self.contexts, self.bases_cache = RLock(), OrderedDict(), OrderedDict()

    def segment(self, day): return bisect_right(self.bounds, day)

    def version(self, day):
        if not self.versions or self.current_from is None or day >= self.current_from: return None
        return next((start for start, to in self.versions if day < to), self.versions[0][0])

    def base(self, valid_from):
        if valid_from is None: return self.state
        with self.lock:
            if valid_from not in self.bases_cache:
                self.bases_cache[valid_from] = overlay(self.state, self.document(valid_from))
                while len(self.bases_cache) > BASES: self.bases_cache.popitem(last=False)
            self.bases_cache.move_to_end(valid_from)
            return self.bases_cache[valid_from]

    def view(self, day):
        base = self.base(self.version(day))
        known = {c.id for c in base.classes}
        classes, groups = {}, defaultdict(set)
        for student, kind, target, start, end in self.stints:
            if (start <= day or start <= self.first) and (end is None or day <= end):
                if kind == 'class': classes[student] = target
                else: groups[target].add(student)
        students = [s.model_copy(update=dict(classId=classes[s.id], active=True)) if classes.get(s.id) in known else s.model_copy(update=dict(active=False)) for s in base.students]
        order = [s.id for s in base.students]
        return base.model_copy(update=dict(students=students, groups=[
            g if g.kind in ('whole', 'staff') else g.model_copy(update=dict(studentIds=[id for id in order if id in groups[g.id]])) for g in base.groups]))

    def context(self, day):
        """The one place that decides which template and pupils apply to a date."""
        index = self.segment(day)
        with self.lock:
            if index not in self.contexts:
                view = self.state if index == len(self.bounds) else self.view(day)
                self.contexts[index] = view, DayContext(view)
                while len(self.contexts) > CONTEXTS: self.contexts.popitem(last=False)
            self.contexts.move_to_end(index)
            return self.contexts[index]

    def bases(self, start, end):
        """Templates in force at some time within start..end."""
        found = False
        for valid_from, to in self.versions:
            if valid_from <= end and start < to:
                found = True
                yield self.base(valid_from)
        if self.current_from is None or end >= self.current_from or not found: yield self.state


def annotate(rows, view):
    """Rows of a past segment carry the class each pupil had that day."""
    people, names = {s.id: s.classId for s in view.students}, {c.id: c.name for c in view.classes}
    for row in rows:
        row['studentClasses'] = {sid: [people[sid], names.get(people[sid], '')] for sid in row['studentIds']}


def load(database, workspace, state):
    """The History of one revision; a database without history tables behaves as before."""
    if not database or not Path(database).is_file(): return History(state)
    uri = Path(database).resolve().as_uri() + '?mode=ro'
    with closing(sqlite3.connect(uri, uri=True)) as db:
        db.execute('BEGIN')
        try: current = db.execute('SELECT valid_from FROM timetable_current WHERE workspace=?', (workspace,)).fetchone()
        except sqlite3.OperationalError as error:
            if 'no such table' not in str(error): raise
            return History(state)
        if db.execute('SELECT revision FROM workspaces WHERE id=?', (workspace,)).fetchone() != (state.revision,): raise Stale
        if current is None: return History(state)
        versions = [(date.fromisoformat(a), date.fromisoformat(b)) for a, b in db.execute('SELECT valid_from,valid_to FROM timetable_versions WHERE workspace=? ORDER BY valid_from', (workspace,))]
        stints = [(sid, kind, target, date.fromisoformat(a), date.fromisoformat(b) if b else None)
                  for sid, kind, target, a, b in db.execute('SELECT student_id,kind,target_id,start_day,end_day FROM pupil_enrollments WHERE workspace=?', (workspace,))]

    def document(valid_from):
        # Version rows are immutable, so a separate read-only connection per load is consistent.
        with closing(sqlite3.connect(uri, uri=True)) as db:
            row = db.execute('SELECT document FROM timetable_versions WHERE workspace=? AND valid_from=?', (workspace, valid_from.isoformat())).fetchone()
        if row is None: raise HTTPException(409, 'Версия расписания не найдена. Обновите страницу.')
        return json.loads(row[0])
    return History(state, date.fromisoformat(current[0]), versions, stints, document)


def current_from(db, workspace):
    """ISO date from which the current template applies, or None without history."""
    try: row = db.execute('SELECT valid_from FROM timetable_current WHERE workspace=?', (workspace,)).fetchone()
    except sqlite3.OperationalError as error:
        if 'no such table' not in str(error): raise
        return None
    return row[0] if row else None
