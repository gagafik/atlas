"""Place pupil requests in existing sections; never write or move timetable cards."""
from collections import Counter, defaultdict
from time import monotonic

from ortools.sat.python import cp_model
from pydantic import Field

from .models import Model, State
from .cycles import active_in
from .editing import new_issues
from .scheduling import validate_schedule
from .resources import ResourceContext


class AllocationRequest(Model):
    revision: int
    requestIds: list[str] | None = None
    seconds: int = Field(default=10, ge=1, le=60)


def allocate(state: State, body: AllocationRequest):
    started = monotonic()
    requests = [r for r in state.courseRequests if body.requestIds is None or r.id in body.requestIds]
    if not requests or body.requestIds is not None and set(body.requestIds) - {r.id for r in requests}:
        return dict(status='blocked', message='Выберите существующие заявки.')
    students = {s.id: s for s in state.students}
    if any(not students[r.studentId].active for r in requests):
        return dict(status='blocked', message='В выбранных заявках есть архивные ученики. Исключите их из распределения.')
    groups = {g.id: g for g in state.groups}
    courses = {c.id: c for c in state.courses}
    selected_courses = {id for r in requests for id in r.courseIds}
    sections = {s.groupId: s for c in state.courses if c.id in selected_courses for s in c.sections}
    group_course = {s.groupId: c.id for c in state.courses for s in c.sections}
    pupils = {r.studentId for r in requests}
    chosen_ids = {r.id for r in requests}
    overlapping = {r.studentId for r in state.courseRequests if r.id not in chosen_ids and r.studentId in pupils
                   and any(q.studentId == r.studentId and set(q.courseIds) & set(r.courseIds) for q in requests)}
    if overlapping:
        return dict(status='blocked', message='Выберите вместе все заявки с общими альтернативами для учеников: ' + ', '.join(students[id].name for id in sorted(overlapping)))
    classes = {students[id].classId for id in pupils}
    incomplete = [g.name for g in state.groups if g.kind not in ['whole', 'staff']
                  and set(g.classIds) & classes
                  and (g.rosterStatus in ['unknown', 'partial'] or g.rosterStatus is None and not g.studentIds)]
    if incomplete:
        return dict(status='blocked', message='Сначала подтвердите составы групп этих классов: ' + ', '.join(incomplete))

    # Only memberships in the alternatives of selected requests may change.
    managed = {id: {s.groupId for r in requests if r.studentId == id for c in r.courseIds for s in courses[c].sections} for id in pupils}
    fixed_members = {g.id: [id for id in g.studentIds if g.id not in managed.get(id, set())] for g in state.groups}
    fixed_groups = {id: {g.id for g in state.groups if (g.kind == 'whole' and students[id].classId in g.classIds)
                       or id in fixed_members[g.id]} for id in pupils}
    active = {s.id for s in state.students if s.active}
    fixed_count = {id: len(set(members) & active) for id, members in fixed_members.items()}
    fixed_courses = Counter((id, group_course[gid]) for gid, members in fixed_members.items() if gid in sections for id in members if id in active)
    duplicates = [(id, cid) for (id, cid), count in fixed_courses.items() if count > 1]
    if duplicates:
        return dict(status='blocked', message='Без выбранной заявки ученик уже состоит в нескольких секциях одного курса: ' + '; '.join(f'{students[id].name} — {courses[cid].name}' for id, cid in duplicates))

    profiles = {p.id: p for p in state.profiles}
    loads = {w.id: w for w in state.workloads}
    intervals = defaultdict(list)
    room_limits = defaultdict(list)
    resources = ResourceContext(state)
    for p in state.placements:
        w = loads[p.workloadId]
        slot = profiles[w.profileId].for_day(p.day)[p.period]
        intervals[w.groupId].append((p.cycleId, p.day, slot.start, slot.end, p.id))
        capacity = resources.room_check(w, p.roomId)['capacity']
        if capacity is not None:
            room_limits[w.groupId].append(capacity)
    overlap_cache = {}

    def overlaps(a, b):
        key = tuple(sorted((a, b)))
        if key not in overlap_cache:
            overlap_cache[key] = any(x[0:2] == y[0:2] and x[4] != y[4] and x[2] < y[3] and y[2] < x[3]
                                     for x in intervals[a] for y in intervals[b])
        return overlap_cache[key]

    model = cp_model.CpModel()
    variables, reasons, by_request, by_student, by_section, by_course = {}, {}, defaultdict(list), defaultdict(list), defaultdict(list), defaultdict(list)
    ranks, changes = [], []
    for request in requests:
        reasons[request.id] = []
        student = students[request.studentId]
        for rank, cid in enumerate(request.courseIds):
            for section in courses[cid].sections:
                gid = section.groupId
                reason = None
                if student.classId not in groups[gid].classIds:
                    reason = 'секция не принимает этот класс'
                elif request.lockedGroupId and gid != request.lockedGroupId:
                    reason = 'выбор закреплён в другой секции'
                elif overlaps(gid, gid):
                    reason = 'уроки самой секции пересекаются'
                else:
                    conflicts = [groups[f].name for f in sorted(fixed_groups[student.id]) if overlaps(gid, f)]
                    if conflicts:
                        reason = 'пересекается с ' + ', '.join(conflicts)
                if reason:
                    reasons[request.id].append(f'{groups[gid].name}: {reason}')
                    continue
                var = model.new_bool_var(f'{request.id}:{gid}')
                variables[request.id, gid] = var
                by_request[request.id].append(var)
                by_student[student.id].append((gid, var))
                by_section[gid].append(var)
                by_course[student.id, cid].append(var)
                ranks.append(rank * var)
                changes.append(int(student.id not in groups[gid].studentIds) * var)
        total = sum(by_request[request.id])
        model.add(total == 1 if request.required or request.lockedGroupId else total <= 1)
    for values in by_course.values():
        model.add(sum(values) <= 1)
    for values in by_student.values():
        for i, (a, va) in enumerate(values):
            for b, vb in values[i+1:]:
                if a != b and overlaps(a, b):
                    model.add(va + vb <= 1)
    for gid, section in sections.items():
        count = fixed_count[gid] + sum(by_section[gid])
        model.add(count >= section.minSize)
        model.add(count <= section.maxSize)
        if room_limits[gid]:
            model.add(count <= min(room_limits[gid]))

    # Lexicographic integer bounds: served requests, course ranks, old section.
    n = len(requests)
    rank_weight = n + 1
    served_weight = (19 * n + 1) * rank_weight
    model.minimize(-served_weight * sum(variables.values()) + rank_weight * sum(ranks) + sum(changes))
    baseline = validate_schedule(state)
    last_issues = []
    attempts = 0
    while monotonic() - started < body.seconds and attempts < 30:
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = max(.001, body.seconds - (monotonic() - started))
        solver.parameters.num_search_workers = 4
        status = solver.solve(model)
        if status not in [cp_model.OPTIMAL, cp_model.FEASIBLE]:
            return dict(status='infeasible' if status == cp_model.INFEASIBLE else 'timeout',
                        message='Нет допустимого распределения для обязательных заявок, закреплений и границ секций.' if status == cp_model.INFEASIBLE else 'Лимит поиска истёк до нахождения проверенного распределения.',
                        reasons=reasons, issues=last_issues)
        attempts += 1
        chosen = {key for key, var in variables.items() if solver.value(var)}
        assignments = {rid: gid for rid, gid in chosen}
        next_state = state.model_copy(deep=True)
        for g in next_state.groups:
            if g.id in sections:
                additions = {r.studentId for r in requests if assignments.get(r.id) == g.id}
                retained = set(fixed_members[g.id])
                # Stable order and source notes survive unchanged rosters.
                g.studentIds = [id for id in g.studentIds if id in retained or id in additions]
                g.studentIds += sorted(additions - set(g.studentIds))
        candidate = State.model_validate(next_state.model_dump())
        report = validate_schedule(candidate)
        last_issues = new_issues(baseline, report)
        if not last_issues:
            rows = []
            for r in requests:
                gid = assignments.get(r.id)
                cid = group_course[gid] if gid else None
                rows.append(dict(requestId=r.id, studentId=r.studentId, groupId=gid, courseId=cid,
                                 rank=r.courseIds.index(cid)+1 if cid else None,
                                 previousGroupIds=[g for g in sorted(managed[r.studentId]) if r.studentId in groups[g].studentIds],
                                 reasons=reasons[r.id] if not gid else []))
            placed = Counter((p.workloadId, p.cycleId) for p in state.placements)
            pending = sum(max(0, w.cycleHours.get(c.id, w.hours) - placed[w.id, c.id]) for w in state.workloads if w.groupId in sections for c in state.cycles.patterns if active_in(w, c.id))
            changed = {id for id in pupils if any((id in g.studentIds) != (id in groups[g.id].studentIds) for g in candidate.groups)}
            return dict(status='success', candidate=candidate, rows=rows, assigned=len(chosen), unassigned=n-len(chosen),
                        firstChoices=sum(r['rank'] == 1 for r in rows), changedStudents=len(changed), unplacedHours=pending,
                        optimal=status == cp_model.OPTIMAL, attempts=attempts,
                        sections=[dict(groupId=gid, courseId=group_course[gid], count=fixed_count[gid]+sum(solver.value(v) for v in by_section[gid]),
                                       minSize=section.minSize, maxSize=section.maxSize) for gid, section in sections.items()])
        # The shared checker covers personal rules, room sizes and travel too.
        # Reject this complete assignment; never apply an unchecked incumbent.
        model.add(sum(var if key in chosen else 1-var for key, var in variables.items()) <= len(variables)-1)
    return dict(status='timeout', message='За отведённое время не найдено распределение без новых нарушений расписания. Составы сохранены.',
                issues=last_issues, reasons=reasons)


def register_courses(app, Workspace, require_revision, create_preview, solve_lock):
    from fastapi import HTTPException

    @app.post('/api/courses/allocate')
    def preview_allocation(body: AllocationRequest, workspace: Workspace = 'school'):
        state = require_revision(workspace, body.revision)
        if not solve_lock.acquire(blocking=False):
            raise HTTPException(409, 'Другой поиск уже выполняется. Дождитесь результата или отмените его.')
        try:
            result = allocate(state, body)
            if result['status'] == 'success':
                candidate = result.pop('candidate')
                result['preview'] = create_preview(workspace, state, candidate)
            return result
        finally:
            solve_lock.release()
