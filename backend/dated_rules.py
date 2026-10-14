"""Use the same rule evaluator for actual dated rows and weekly templates."""
from .rules import RuleContext, evaluate_rules, minutes
from .models import Workload
from .cycles import pattern_on, active_in
from .resources import teacher_ids, room_ids


class DatedRuleContext(RuleContext):
    def __init__(self, state, day, dated, original):
        super().__init__(state)
        self.day, self.original = day, original
        self.dated, self.rows = {}, []
        self.closed = {id for e in original.events if e.effect == 'cancel' and e.start <= day <= e.end
                       for id in (e.classIds or list(self.idx['classes']))}
        cycle = pattern_on(original, day)
        self.template = [p for p in original.placements if p.day == day.weekday() and p.cycleId == cycle]
        prefix = 'actual-row:'
        while any(w.id.startswith(prefix) for w in original.workloads): prefix = '_' + prefix
        for row in dated:
            w = Workload(id=prefix + row['id'], groupId=row['groupId'], subjectId=row['subjectId'],
                         profileId=row['profileId'] or '', hours=1, teacherId=next(iter(row['teacherIds']), None),
                         additionalTeacherIds=row['teacherIds'][1:], requiredRoomIds=row['roomIds'],
                         teacherRequired=bool(row['teacherIds']), roomRequired=bool(row['roomIds']))
            self.dated[id(w)] = row
            self.rows.append(dict(w=w, day=day.weekday(), period=f'{row["start"]}–{row["end"]}', roomId=None, var=1,
                                  start=minutes(row['start']), end=minutes(row['end']), id=row['id'], classIds=row['classIds']))

    def members(self, w, class_ids=()):
        row = self.dated.get(id(w))
        return set(row['studentIds']) if row else super().members(w, class_ids)

    def classes(self, w, class_ids=()):
        row = self.dated.get(id(w))
        if not row: return super().classes(w, class_ids)
        g = self.idx['groups'][w.groupId]
        if g.kind == 'whole' or not row['rosterComplete']: return set(row['classIds'])
        return {self.idx['students'][id].classId for id in row['studentIds']}

    def bindings(self, rule, w, room, class_ids=()):
        row = self.dated.get(id(w))
        if row and rule.scope == 'workload':
            return [row['workloadId']] if row['workloadId'] in self.targets(rule) else []
        return super().bindings(rule, w, room, class_ids)

    def matches(self, rule, row):
        if rule.scope == 'workload': return bool(self.bindings(rule, row['w'], row['roomId']))
        return super().matches(rule, row)

    def bucket_names(self, rule):
        result = super().bucket_names(rule)
        if rule.scope == 'subject':
            # A once-off lesson can introduce a subject/group combination absent
            # from weekly workloads. Include its individual pupils as targets.
            for row in self.rows:
                for key in self.bindings(rule, row['w'], None):
                    subject, scope, id = key
                    result[key] = (self.label('subject', subject) + ' · ' + self.label(scope, id), [id] if scope == 'student' else [])
        return result

    def group_closed(self, group_id):
        g = self.idx['groups'][group_id]
        if g.kind == 'staff': return False
        if g.kind != 'whole' and (g.rosterStatus == 'complete' or g.rosterStatus is None and g.studentIds):
            return bool(g.studentIds) and all(self.idx['students'][s].classId in self.closed for s in g.studentIds)
        return bool(g.classIds) and set(g.classIds) <= self.closed

    def skip_empty(self, rule, bucket, day):
        if rule.scope == 'class':
            kind, id = bucket.split(':', 1)
            return (self.idx['students'][id].classId if kind == 'student' else id) in self.closed
        if rule.scope == 'student': return self.idx['students'][bucket].classId in self.closed
        if rule.scope == 'subject':
            _, kind, id = bucket
            return (self.idx['students'][id].classId if kind == 'student' else id) in self.closed
        if rule.scope == 'group': return self.group_closed(bucket)
        if rule.scope == 'workload': return self.group_closed(self.idx['workloads'][bucket].groupId)
        if rule.scope == 'teacher':
            absences = [a for a in self.original.absences if a.teacherId == bucket and a.start <= self.day <= a.end]
            if any(a.startTime == '00:00' and a.endTime == '23:59' for a in absences): return True
        else: absences = []
        relevant = []
        for p in self.template:
            w = self.idx['workloads'][p.workloadId]
            values = teacher_ids(w) if rule.scope == 'teacher' else room_ids(w, p.roomId)
            if bucket not in values: continue
            slot = self.profiles[w.profileId].for_day(p.day)[p.period]
            relevant.append(self.group_closed(w.groupId) or any(a.startTime <= slot.start and a.endTime >= slot.end for a in absences))
        return bool(relevant) and all(relevant) or bool(self.closed) and set(self.idx['classes']) <= self.closed


def evaluate_date_rules(state, day, rows):
    cycle = pattern_on(state, day)
    rules = []
    for r in state.rules:
        if not r.enabled or r.kind == 'distribution' or not active_in(r, cycle) or r.days and day.weekday() not in r.days: continue
        rules.append(r.model_copy(update=dict(days=[day.weekday()], maxWeeklyIdlePeriods=None,
            includeEmptyDays=r.includeEmptyDays and day.weekday() < len(state.settings.days) and cycle is not None)))
    if not rules: return [], [], 0
    settings = state.settings.model_copy(update={'days': ['Понедельник', 'Вторник', 'Среда', 'Четверг', 'Пятница', 'Суббота', 'Воскресенье']})
    dated_state = state.model_copy(update={'rules': rules, 'settings': settings})
    context = DatedRuleContext(dated_state, day, rows, state)
    issues, warnings, penalty = evaluate_rules(dated_state, context=context, rows=context.rows)
    for issue in [*issues, *warnings]:
        issue.update(type='dated_rule', date=day.isoformat())
    return issues, warnings, penalty
