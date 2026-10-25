"""Resolve weekly patterns into dated lessons, without mutating the template."""
import json
from collections import defaultdict, Counter
from datetime import date, timedelta
from typing import Literal
from pydantic import Field, ValidationError
from fastapi import HTTPException
from .models import Model, State, Event, DatedLesson, TeacherAbsence, ScheduleDay
from .cycles import pattern_on, active_in
from .resources import ResourceContext, teacher_ids, room_ids
from .scheduling import group_overlap, roster_complete, minutes


def dates(start, end):
    while start <= end:
        yield start
        start += timedelta(days=1)


TYPES = dict(substitution='Замена', merge='Объединение', move='Перенос', self_study='Самостоятельная работа', cancel='Отмена', extra='Дополнительный урок')


def plain(exc):
    """Readable text of a validation error: the rule's own sentence without pydantic's wrapper."""
    if not isinstance(exc, ValidationError): return str(exc)
    found = [(e['msg'].removeprefix('Value error, ') if e['type'] == 'value_error' else '.'.join(map(str, e['loc'])) + ': ' + e['msg']) for e in exc.errors()]
    return '; '.join(dict.fromkeys(found))


def schedule_day(state, day):
    return next((d for d in state.scheduleDays if d.onDate == day), None)


def cycle_on(state, day):
    d = schedule_day(state, day)
    return d.cycleId if d and d.cycleId and not d.classIds else pattern_on(state, day)


def weekday_on(state, day, class_ids=None):
    d = schedule_day(state, day)
    return d.day if d and (not d.classIds or class_ids is not None and set(class_ids) & set(d.classIds)) else day.weekday()


def template_on(state, day, maps):
    """Weekly placements a date runs: its own weekday or the one chosen as «день в расписании»."""
    override, cycle = schedule_day(state, day), pattern_on(state, day)
    own = lambda p: p.cycleId == cycle and p.day == day.weekday()
    if override is None: return [p for p in state.placements if own(p)]
    other = lambda p: p.cycleId == (override.cycleId or cycle) and p.day == override.day
    if not override.classIds: return [p for p in state.placements if other(p)]
    chosen = set(override.classIds)
    moved = lambda p: bool(chosen & set(maps['groups'][maps['workloads'][p.workloadId].groupId].classIds))
    return [p for p in state.placements if other(p) and moved(p) or own(p) and not moved(p)]


def change_type(change):
    """Stored type of a dated change, derived for records made before types existed."""
    if change is None: return None
    return change.changeType or ('cancel' if change.kind == 'cancel' else 'extra' if change.kind == 'add' else 'move' if change.onDate != change.sourceDate else 'substitution')


class DayContext:
    def __init__(self, state):
        self.state = state
        self.maps = {key: {r.id: r for r in getattr(state, key)} for key in
                     ['placements', 'workloads', 'groups', 'subjects', 'teachers', 'rooms', 'profiles', 'classes', 'students']}
        self.resources = ResourceContext(state)
        # Reuse ordered, active group membership across a multi-day report.
        self.group_students = {g.id:[s.id for s in state.students if s.active and
            (s.classId in g.classIds if g.kind=='whole' else s.id in g.studentIds)] for g in state.groups}
        self.templates = {}

    def template(self, day):
        if day not in self.templates:
            self.templates[day] = {p.id for p in template_on(self.state, day, self.maps)}
        return self.templates[day]

    def origin(self, change):
        p = self.maps['placements'].get(change.placementId)
        return p if p and p.id in self.template(change.sourceDate) else None

    def row(self, day, p=None, change=None):
        w = self.maps['workloads'][p.workloadId] if p else None
        g = self.maps['groups'][w.groupId if w else change.groupId]
        slot = self.maps['profiles'][w.profileId].for_day(p.day)[p.period] if p else None
        students = self.group_students[g.id][:]
        original_students = students[:]
        original_classes = g.classIds if g.kind == 'whole' or not roster_complete(g) else sorted({self.maps['students'][s].classId for s in students})
        subjects = change.subjectId if change and change.subjectId else w.subjectId if w else None
        teachers = change.teacherIds if change and change.teacherIds is not None else teacher_ids(w) if w else []
        rooms = change.roomIds if change and change.roomIds is not None else room_ids(w, p.roomId) if w else []
        # A moved lesson retains the source day's clock unless explicitly edited.
        start = change.startTime if change and change.startTime else slot.start
        end = change.endTime if change and change.endTime else slot.end
        closures = [e for e in self.state.events if e.effect == 'cancel' and e.start <= day <= e.end]
        closed = {c for e in closures for c in (e.classIds or list(self.maps['classes']))}
        removed = sorted(set(original_classes) & closed)
        classes = [c for c in original_classes if c not in closed]
        students = [s for s in students if self.maps['students'][s].classId not in closed]
        cancelled = bool(original_classes) and not classes
        if g.kind not in ['whole', 'staff'] and roster_complete(g) and not students and removed:
            cancelled = True
        reasons = [e.name for e in closures if set(e.classIds or list(self.maps['classes'])) & set(removed)]
        if change and change.kind == 'cancel':
            cancelled = True
        if change:
            reasons.insert(0, change.name)
        row_id = f'{change.sourceDate}:{p.id}' if p and change else f'{day}:{p.id}' if p else 'extra:' + change.id
        size = self.resources.audience_size(w) if w else None
        if g.kind == 'staff': size = len(teachers)
        elif g.kind != 'whole' and roster_complete(g): size = len(students) if removed or w is None else max(size or 0, len(students))
        elif g.kind == 'whole' and all(self.maps['classes'][c].studentCount is not None for c in classes):
            calculated = sum(max(self.maps['classes'][c].studentCount, sum(self.maps['students'][s].classId == c for s in students)) for c in classes)
            size = calculated if removed or w is None else max(size or 0, calculated)
        elif removed: size = None
        return dict(id=row_id, date=day.isoformat(), placementId=p.id if p else None,
                    sourceDate=(change.sourceDate if change else day).isoformat() if p else None,
                    changeId=change.id if change else None, workloadId=w.id if w else None,
                    groupId=g.id, groupIds=[g.id], group=g.name, subjectId=subjects, subject=self.maps['subjects'][subjects].name,
                    activity='lesson', wholeClassIds=classes if g.kind == 'whole' else [],
                    classIds=classes, studentIds=students, originalClassIds=original_classes, originalStudentIds=original_students, excludedClassIds=removed,
                    teacherIds=teachers, originalTeacherIds=teacher_ids(w) if w else teachers, roomIds=rooms, start=start, end=end,
                    teachers=', '.join(self.maps['teachers'][t].name for t in teachers),
                    rooms=', '.join(self.maps['rooms'][r].name for r in rooms),
                    status='cancelled' if cancelled else 'added' if not p else 'changed' if change else 'scheduled',
                    reasons=reasons, teacherRequired=w.teacherRequired if w else True, roomRequired=w.roomRequired if w else True,
                    audienceSize=size, rosterComplete=roster_complete(g), profileId=change.profileId if change and change.profileId else w.profileId if w else None,
                    changeType=change_type(change), mergeWith=change.mergeWith if change else None, movedTo=None)


def resolve_day(state, day, context=None):
    ctx = context or DayContext(state)
    overrides = {(c.placementId, c.sourceDate): c for c in state.datedLessons if c.placementId}
    rows, issues = [], []
    for p in template_on(state, day, ctx.maps):
        change = overrides.get((p.id, day))
        row = ctx.row(day, p, change if not change or change.onDate == day else None)
        if change and change.onDate != day:
            row['status'] = 'cancelled'
            row['changeId'] = change.id
            row['reasons'] = [f'Перенесено на {change.onDate}: {change.name}']
            row['changeType'] = change_type(change)
            row['movedTo'] = change.onDate.isoformat()
        rows.append(row)
    split = schedule_day(state, day)
    if split and split.classIds:
        chosen, cycle = set(split.classIds), pattern_on(state, day)
        for p in state.placements:
            if not (p.cycleId == cycle and p.day == day.weekday() or p.cycleId == (split.cycleId or cycle) and p.day == split.day): continue
            g = ctx.maps['groups'][ctx.maps['workloads'][p.workloadId].groupId]
            if set(g.classIds) & chosen and not set(g.classIds) <= chosen:
                issues.append(dict(type='dated_day_split', ids=[f'{day}:{p.id}'], date=day.isoformat(), message=f'{g.name}: группа объединяет выбранные и невыбранные классы «дня в расписании». Выберите все её классы или ни одного.'))
    for change in state.datedLessons:
        if change.onDate != day and change.sourceDate != day: continue
        p = ctx.origin(change) if change.placementId else None
        if change.placementId and p is None:
            issues.append(dict(type='dated_orphan', ids=[change.id], date=day.isoformat(), message=f'{change.name}: исходный урок на {change.sourceDate} отсутствует или его неделя/день изменились. Удалите либо переназначьте изменение.'))
        elif change.onDate == day and (change.kind == 'add' or change.onDate != change.sourceDate):
            rows.append(ctx.row(day, p, change))
    from .commitments import commitment_rows
    rows.extend(commitment_rows(state, day, ctx))
    rows.sort(key=lambda r: (r['start'], r['end'], r['group'], r['id']))
    return rows, issues


def day_report(state, day, context=None, resolved=None):
    ctx = context or DayContext(state)
    rows, issues = resolve_day(state, day, ctx) if resolved is None else resolved
    active = [r for r in rows if r['status'] != 'cancelled']
    unknown_capacity = unknown_groups = missing_teachers = missing_rooms = unknown_buildings = unfilled_duties = 0
    commitment_warnings = []
    student_classes = {s.id: s.classId for s in state.students}
    groups, people, buildings = {}, {}, {}
    def add(kind, ids, message, **extra):
        issues.append(dict(type='dated_' + kind, ids=sorted(ids), date=day.isoformat(), message=message, **extra))
    def attendance_of(r): return r['audienceSize'] if r['audienceSize'] is not None else r.get('audienceLowerBound', len(r['studentIds']))
    # A merged group sits in the host lesson: same teachers and rooms, joint capacity.
    by_id, joined, merged = {r['id']: r for r in active}, defaultdict(int), set()
    for r in active:
        if not r.get('mergeWith'): continue
        host = by_id.get(r['mergeWith'])
        if host is None or host['activity'] != 'lesson' or host.get('mergeWith') or not (host['start'] < r['end'] and r['start'] < host['end']):
            add('merge', [r['id']], f'{r["group"]} · {r["subject"]}: урок для объединения не найден, отменён или не совпадает по времени.')
        elif set(r['teacherIds']) != set(host['teacherIds']) or set(r['roomIds']) != set(host['roomIds']):
            add('merge', [r['id'], host['id']], f'{r["group"]}: состав объединения устарел — учителя или кабинеты урока изменились. Пересоздайте объединение.')
        else:
            joined[host['id']] += attendance_of(r)
            merged.add(r['id'])
    for r in active:
        groups[r['id']] = [g.model_copy(update={'classIds': [c for c in g.classIds if c in r['classIds']], 'studentIds': [s for s in r['studentIds'] if g.kind == 'whole' or s in g.studentIds]}) for g in (ctx.maps['groups'][id] for id in r['groupIds'])]
        people[r['id']] = {f'student:{s}' for s in r['studentIds']} | {f'teacher:{t}' for t in r['teacherIds']}
        people[r['id']] |= {f'class:{c}' for c in r['wholeClassIds'] if not ctx.resources.class_members[c]}
        unknown_groups += not r['rosterComplete']
        missing_teachers += r['teacherRequired'] and not r['teacherIds']
        unfilled_duties += max(0, r.get('requiredTeachers', 0)-len(r['teacherIds']))
        missing_rooms += r['roomRequired'] and not r['roomIds']
        rooms = [ctx.maps['rooms'][id] for id in r['roomIds']]
        capacity = sum(x.capacity for x in rooms) if rooms and all(x.capacity is not None for x in rooms) else None
        bs = {x.buildingId for x in rooms if x.buildingId}
        if r.get('buildingId'): bs.add(r['buildingId'])
        buildings[r['id']] = next(iter(bs)) if len(bs) == 1 and (r.get('buildingId') or rooms and all(x.buildingId for x in rooms)) else None
        unknown_capacity += bool(rooms) and (capacity is None or r['audienceSize'] is None)
        unknown_buildings += bool(ctx.resources.max_travel and people[r['id']] and not buildings[r['id']])
        if len(bs) > 1: add('building', [r['id']], f'{r["group"]}: кабинеты урока находятся в разных зданиях.')
        attendance = attendance_of(r) + joined[r['id']]
        if capacity is not None and r['id'] not in merged and attendance > capacity:
            add('capacity', [r['id']], f'{r["group"]}: вместимость {capacity}, участников {attendance}' + (' (не меньше).' if r['audienceSize'] is None else '.'))
        for a in state.absences:
            if a.teacherId in r['teacherIds'] and a.start <= day <= a.end and r['start'] < a.endTime and a.startTime < r['end']:
                add('absence', [r['id'], a.id], f'{r["start"]}–{r["end"]} · {r["group"]} · {r["subject"]}: {ctx.maps["teachers"][a.teacherId].name} отсутствует ({a.name}), нужна замена.', teacherIds=[a.teacherId])
        if r['profileId']:
            wd = weekday_on(state, day, r['originalClassIds'])
            slots = ctx.maps['profiles'][r['profileId']].for_day(wd)
            for t in r['teacherIds']:
                if any(f'{wd}:{i}' in ctx.maps['teachers'][t].unavailable and r['start'] < slot.end and slot.start < r['end'] for i, slot in enumerate(slots)):
                    add('availability', [r['id']], f'{ctx.maps["teachers"][t].name}: недоступен в это время.')
        if r['activity'] != 'lesson':
            targets = dict(teacher=set(r['teacherIds']), room=set(r['roomIds']), student=set(r['studentIds']), class_=set(r['classIds']), group=set(r['groupIds']))
            wd, cycle = weekday_on(state, day), cycle_on(state, day)
            for rule in state.rules:
                if rule.kind != 'availability' or not rule.enabled or not active_in(rule, cycle) or rule.scope in ['subject', 'workload']: continue
                if rule.days and wd not in rule.days: continue
                values = targets['class_' if rule.scope == 'class' else rule.scope]
                if values and (not rule.targetIds or values & set(rule.targetIds)) and any(w.day == wd and r['start'] < w.end and w.start < r['end'] for w in rule.unavailable):
                    issue = dict(type='dated_rule', ids=[r['id']], date=day.isoformat(), ruleId=rule.id, message=f'{rule.name}: {r["subject"]} попадает в недоступное время.')
                    if rule.mode == 'hard': issues.append(issue)
                    else: commitment_warnings.append({**issue, 'penalty': rule.weight})
    for i, a in enumerate(active):
        for b in active[i+1:]:
            if b['start'] >= a['end']: break
            joint = a['id'] in merged and a['mergeWith'] == b['id'] or b['id'] in merged and b['mergeWith'] == a['id'] or a['id'] in merged and b['id'] in merged and a['mergeWith'] == b['mergeWith']
            teachers = [] if joint else sorted(set(a['teacherIds']) & set(b['teacherIds']))
            rooms = [] if joint else sorted(set(a['roomIds']) & set(b['roomIds']))
            def attends(g): return g.kind == 'whole' or g.kind != 'staff' and (g.studentIds or not roster_complete(g))
            audience = any(group_overlap(ga, gb, student_classes) for ga in groups[a['id']] for gb in groups[b['id']] if attends(ga) and attends(gb))
            students = sorted(set(a['studentIds']) & set(b['studentIds']))
            if teachers or rooms or audience:
                details = [ctx.maps['teachers'][t].name for t in teachers] + [ctx.maps['rooms'][r].name for r in rooms]
                if audience: details.append('ученики / класс')
                add('collision', [a['id'], b['id']], f'{a["group"]} · {a["subject"]} и {b["group"]} · {b["subject"]}: ' + ', '.join(details), affectedStudentIds=students, teacherIds=teachers, roomIds=rooms)
    if ctx.resources.max_travel:
        buckets = defaultdict(list)
        for r in active:
            for person in people[r['id']]: buckets[person].append(r)
        pairs = defaultdict(set)
        for person, lessons in buckets.items():
            for i, a in enumerate(lessons):
                for j in range(i+1, len(lessons)):
                    b = lessons[j]
                    gap = minutes(b['start']) - minutes(a['end'])
                    if gap >= ctx.resources.max_travel: break
                    if gap >= 0 and gap < ctx.resources.travel(buildings[a['id']], buildings[b['id']]) and not any(x['start'] >= a['end'] and x['end'] <= b['start'] for x in lessons[i+1:j]):
                        pairs[a['id'], b['id']].add(person)
        for pair, persons in pairs.items():
            add('travel', list(pair), 'Недостаточно времени на переход между зданиями.', affectedStudentIds=sorted(p[8:] for p in persons if p.startswith('student:')))
    for workload, count in Counter(r['workloadId'] for r in active if r['workloadId']).items():
        w = ctx.maps['workloads'][workload]
        if count > w.maxPerDay:
            add('daily_load', [r['id'] for r in active if r['workloadId'] == workload], f'{ctx.maps["groups"][w.groupId].name} · {ctx.maps["subjects"][w.subjectId].name}: {count} уроков, максимум {w.maxPerDay} за день.')
    from .dated_rules import evaluate_date_rules
    rule_issues, warnings, penalty = evaluate_date_rules(state, day, [r for r in active if r['activity'] == 'lesson'])
    from .commitments import occupied_lunch_rules
    occupied_issues, occupied_warnings = occupied_lunch_rules(state, day, active)
    issues.extend(occupied_issues)
    commitment_warnings += occupied_warnings
    warnings += commitment_warnings
    penalty += sum(w['penalty'] for w in commitment_warnings)
    issues.extend(rule_issues)
    return dict(date=day.isoformat(), cycleId=cycle_on(state, day), scheduleDay=d.model_dump(mode='json') if (d := schedule_day(state, day)) else None, rows=rows,
                events=[e.model_dump(mode='json') for e in state.events if e.start <= day <= e.end],
                issues=issues, issueCount=len(issues), warnings=warnings, warningCount=len(warnings), qualityPenalty=penalty, unknownGroups=unknown_groups, unknownCapacity=unknown_capacity,
                unknownBuildings=unknown_buildings, missingTeachers=missing_teachers, missingRooms=missing_rooms, unfilledDuties=unfilled_duties,
                completeChecks=not any([unknown_groups, unknown_capacity, unknown_buildings, missing_teachers, missing_rooms, unfilled_duties]))


def operational_dates(state):
    result = {d for c in state.datedLessons for d in [c.onDate, c.sourceDate] if d}
    result.update(c.onDate for c in state.commitments)
    result.update(d.onDate for d in state.scheduleDays)
    for item in [*state.absences, *(e for e in state.events if e.effect == 'cancel')]:
        result.update(dates(max(item.start, state.settings.yearStart), min(item.end, state.settings.yearEnd)))
    return result


def summarize_dates(state, days):
    ctx = DayContext(state)
    issues, warnings, penalty = [], [], 0
    for day in sorted(days):
        report = day_report(state, day, ctx)
        issues.extend(report['issues']); warnings.extend(report['warnings']); penalty += report['qualityPenalty']
    return dict(issues=issues, warnings=warnings, penalty=penalty)


def check_dates(state, days):
    return summarize_dates(state, days)['issues']


class CalendarEdit(Model):
    revision: int
    table: Literal['events', 'datedLessons', 'absences', 'scheduleDays']
    value: dict | None = None
    removeId: str | None = None


class SubstituteRequest(Model):
    revision: int
    day: date
    rowId: str
    teacherId: str | None = None


def staffing_check(state, ctx, day, rows, row, retained):
    """Return check(teacher_id) -> reasons the teacher would add to `row` if put next to `retained`.

    Reports only problems the proposed teacher adds; issues the lesson already has
    without the replaced teacher (pupils, rooms, retained co-teachers) remain in
    the day's report and final edit preview."""
    def staffed(teachers):
        replacement = {**row, 'teacherIds': teachers}
        if row['groupIds'] and all(ctx.maps['groups'][id].kind == 'staff' for id in row['groupIds']): replacement['audienceSize'] = len(teachers)
        resolved = ([replacement if r['id'] == row['id'] else r for r in rows], [])
        return day_report(state, day, ctx, resolved=resolved)['issues']
    signature = lambda issue: json.dumps(issue, ensure_ascii=False, sort_keys=True)
    vacated = {signature(i) for i in staffed(retained)}
    def check(teacher_id):
        reasons = [i['message'] for i in staffed(retained + [teacher_id]) if row['id'] in i['ids'] and signature(i) not in vacated]
        return list(dict.fromkeys(reasons))
    return check


def substitute_options(state, body):
    from .substitutions import rank, merge_options
    ctx = DayContext(state)
    baseline = day_report(state, body.day, ctx)
    row = next((r for r in baseline['rows'] if r['id'] == body.rowId and r['status'] != 'cancelled'), None)
    if row is None: raise ValueError('Действующий урок не найден.')
    if body.teacherId and body.teacherId not in row['teacherIds']: raise ValueError('Заменяемый учитель не ведёт этот урок.')
    retained = [t for t in row['teacherIds'] if t != body.teacherId]
    check = staffing_check(state, ctx, body.day, baseline['rows'], row, retained)
    candidates = rank(state, ctx, baseline['rows'], row, retained, check, exclude=row['changeId'])
    return dict(revision=state.revision, candidates=candidates, completeChecks=baseline['completeChecks'],
                weights=state.settings.substitutionWeights.model_dump(), mergeOptions=merge_options(ctx, baseline['rows'], row))


def register_calendar(app, Workspace, read, require_revision, create_preview):
    @app.get('/api/calendar/day')
    def day(day: date, workspace: Workspace = 'school'):
        state = read(workspace)
        return dict(revision=state.revision, **day_report(state, day))

    @app.post('/api/calendar/substitutes')
    def substitutes(body: SubstituteRequest, workspace: Workspace = 'school'):
        state = require_revision(workspace, body.revision)
        try: return substitute_options(state, body)
        except ValueError as exc: raise HTTPException(422, str(exc))

    @app.post('/api/calendar/preview')
    def preview(body: CalendarEdit, workspace: Workspace = 'school'):
        state = require_revision(workspace, body.revision)
        if (body.value is None) == (body.removeId is None): raise HTTPException(422, 'Укажите запись или удаление.')
        try:
            candidate = state.model_copy(deep=True)
            rows = getattr(candidate, body.table)
            if body.removeId:
                if not any(x.id == body.removeId for x in rows): raise ValueError('Запись не найдена.')
                setattr(candidate, body.table, [x for x in rows if x.id != body.removeId])
            else:
                model = dict(events=Event, datedLessons=DatedLesson, absences=TeacherAbsence, scheduleDays=ScheduleDay)[body.table]
                value = model.model_validate(body.value)
                setattr(candidate, body.table, [x for x in rows if x.id != value.id] + [value])
            candidate = State.model_validate(candidate.model_dump())
        except ValueError as exc:
            raise HTTPException(422, plain(exc))
        # Absence is a fact that may introduce uncovered lessons. It is visible
        # before saving, and substitutions are then checked against that baseline.
        return create_preview(workspace, state, candidate, allow_issues=body.table == 'absences')
