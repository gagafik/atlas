"""Bounded, revision-scoped read models. Never cache accounts or permissions.

Portal and homework handlers use these snapshots; editor code keeps independent State
objects. New revisions (including undo) replace old snapshots, and authorization
is checked on every request before selecting an entity's projected rows.
"""
from collections import OrderedDict, defaultdict
from copy import deepcopy
from threading import RLock

from fastapi import HTTPException

from .calendar_ops import resolve_day
from .exporting import ExportRequest, schedule_rows, VIEWS
from .timetable_history import History, Stale, annotate, load


class Snapshot:
    def __init__(self, state, history=None):
        self.state = state
        # Past days resolve against the timetable and rosters valid then; the last segment is the plain state.
        self.history = history or History(state)
        self.entities = {view: {x.id: x for x in getattr(state, table)} for view, table in VIEWS.items()}
        self.days, self.weeks = OrderedDict(), OrderedDict()
        self.lesson_days = OrderedDict()
        self.lock = RLock()

    def current_lesson(self, lesson):
        from .journal import current_lesson
        # Only schedule rows are cached. Publication, account grants, answer and
        # review versions are still read for every request; writes recheck them.
        return current_lesson(self.state,lesson,self.lesson_rows)

    def lesson_rows(self, day):
        with self.lock:
            if day not in self.lesson_days:
                view,ctx = self.history.context(day);rows = resolve_day(view,day,ctx)[0]
                if view is not self.state: annotate(rows,view)
                self.lesson_days[day] = rows
                if len(self.lesson_days)>8: self.lesson_days.popitem(last=False)
            self.lesson_days.move_to_end(day)
            return self.lesson_days[day]

    def student_lessons(self, day, student):
        # Notices cover lessons the pupil actually attends, excluding partial
        # class closures that the ordinary timetable also shows as changes.
        return [{k:r[k] for k in ['date','start','end','subject','group','status','activity']}
                for r in self.lesson_rows(day) if r['activity']=='lesson' and r['status']!='cancelled' and student in r['studentIds']]

    def day_rows(self, day, view, entity_id):
        with self.lock:
            if day not in self.days:
                state, ctx = self.history.context(day)
                rows, _ = resolve_day(state, day, ctx)
                index = defaultdict(list)
                for row in rows:
                    projected = {k: row[k] for k in ['start','end','subject','group','teachers','rooms','status','activity']}
                    projected.update(day=day.weekday(),date=day.isoformat())
                    targets = dict(class_=row['originalClassIds'],student=row['originalStudentIds'],
                                   teacher=set(row['originalTeacherIds']) | set(row['teacherIds']),
                                   room=row['roomIds'],group=row['groupIds'])
                    for kind, ids in targets.items():
                        for id in set(ids): index[kind,id].append(projected)
                self.days[day] = dict(index)
                if len(self.days)>8: self.days.popitem(last=False)
            self.days.move_to_end(day)
            return deepcopy(self.days[day].get((view,entity_id),[]))

    def week_rows(self, cycle, view, entity_id):
        key = cycle, view, entity_id
        with self.lock:
            if key not in self.weeks:
                _,_,rows = schedule_rows(self.state,ExportRequest(revision=self.state.revision,view='class' if view=='class_' else view,entityIds=[entity_id],cycleIds=[cycle]))
                self.weeks[key] = [{k:r[k] for k in ['start','end','subject','group','teachers','rooms','day','dayName']} | dict(status='scheduled',activity='lesson') for r in rows]
                if len(self.weeks)>128: self.weeks.popitem(last=False)
            self.weeks.move_to_end(key)
            return deepcopy(self.weeks[key])


class PortalSnapshots:
    def __init__(self, read, stamp):
        self.read, self.stamp = read, stamp
        self.cache, self.lock = OrderedDict(), RLock()

    def get(self, workspace):
        with self.lock:
            database, revision = self.stamp(workspace)
            key = database, workspace
            snapshot = self.cache.get(key)
            if snapshot is None or snapshot.state.revision != revision:
                snapshot = self.build(workspace, database)
                self.cache[key] = snapshot
            self.cache.move_to_end(key)
            if len(self.cache)>2: self.cache.popitem(last=False)
            return snapshot

    def build(self, workspace, database):
        # A save between reading the State and its history makes them disagree: read again.
        for _ in range(3):
            state = self.read(workspace)
            try: return Snapshot(state, load(database, workspace, state))
            except Stale: continue
        raise HTTPException(409, 'Расписание изменилось. Обновите страницу.')
