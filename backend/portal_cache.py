"""Bounded, revision-scoped read models. Never cache accounts or permissions.

Portal and homework handlers use these snapshots; editor code keeps independent State
objects. New revisions (including undo) replace old snapshots, and authorization
is checked on every request before selecting an entity's projected rows.
"""
from collections import OrderedDict, defaultdict
from copy import deepcopy
from threading import RLock

from .calendar_ops import resolve_day
from .exporting import ExportRequest, schedule_rows, VIEWS


class Snapshot:
    def __init__(self, state):
        self.state = state
        self.entities = {view: {x.id: x for x in getattr(state, table)} for view, table in VIEWS.items()}
        self.days, self.weeks = OrderedDict(), OrderedDict()
        self.lesson_days = OrderedDict()
        self.lock = RLock()

    def current_lesson(self, lesson):
        from .journal import current_lesson
        # Only schedule rows are cached. Publication, account grants, answer and
        # review versions are still read for every request; writes recheck them.
        def rows_for_day(day):
            with self.lock:
                if day not in self.lesson_days:
                    self.lesson_days[day] = resolve_day(self.state,day)[0]
                    if len(self.lesson_days)>8: self.lesson_days.popitem(last=False)
                self.lesson_days.move_to_end(day)
                return self.lesson_days[day]
        return current_lesson(self.state,lesson,rows_for_day)

    def day_rows(self, day, view, entity_id):
        with self.lock:
            if day not in self.days:
                rows, _ = resolve_day(self.state, day)
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
                snapshot = Snapshot(self.read(workspace))
                self.cache[key] = snapshot
            self.cache.move_to_end(key)
            if len(self.cache)>2: self.cache.popitem(last=False)
            return snapshot
