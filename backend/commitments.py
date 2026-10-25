"""Dated room reservations and staff duty assignments."""
from datetime import timedelta
from time import monotonic
from collections import defaultdict
from uuid import uuid4
from fastapi import HTTPException
from pydantic import Field
from .models import Model, State, Commitment
from .scheduling import roster_complete


def commitment_rows(state, day, context):
    rows = []
    for item in state.commitments:
        if item.onDate != day: continue
        groups = [context.maps['groups'][g] for g in item.groupIds]
        whole_classes = {c for g in groups if g.kind == 'whole' for c in g.classIds}
        students = sorted({s.id for s in state.students if s.active and (s.classId in whole_classes or any(s.id in g.studentIds for g in groups))})
        classes = sorted(whole_classes | {context.maps['students'][s].classId for s in students} |
                         {c for g in groups if not roster_complete(g) for c in g.classIds})
        complete = all(roster_complete(g) for g in groups)
        lower = len(students) if any(g.kind != 'staff' for g in groups) else len(item.teacherIds)
        size = None
        if item.studentCount is not None: size = max(item.studentCount, lower)
        elif groups and complete and all(context.maps['classes'][c].studentCount is not None for c in whole_classes):
            size = sum(max(context.maps['classes'][c].studentCount, len(context.resources.class_members[c])) for c in whole_classes)
            size += sum(context.maps['students'][s].classId not in whole_classes for s in students)
            if all(g.kind == 'staff' for g in groups): size = len(item.teacherIds)
        group_label = ', '.join(g.name for g in groups) or item.location or ', '.join(context.maps['rooms'][r].name for r in item.roomIds)
        rows.append(dict(id='commitment:' + item.id, commitmentId=item.id, activity=item.kind,
            date=day.isoformat(), placementId=None, sourceDate=None, changeId=None, workloadId=None,
            groupId=next(iter(item.groupIds), None), groupIds=item.groupIds, group=group_label,
            subjectId=None, subject=item.name, classIds=classes, studentIds=students,
            originalClassIds=classes, originalStudentIds=students, excludedClassIds=[], wholeClassIds=sorted(whole_classes),
            teacherIds=item.teacherIds, originalTeacherIds=item.teacherIds, roomIds=item.roomIds,
            start=item.startTime, end=item.endTime, teachers=', '.join(context.maps['teachers'][t].name for t in item.teacherIds),
            rooms=', '.join(context.maps['rooms'][r].name for r in item.roomIds), status='scheduled',
            reasons=([item.note] if item.note else []) + ([f'Дежурных: {len(item.teacherIds)} из {item.requiredTeachers}'] if item.kind == 'duty' else []),
            teacherRequired=item.kind == 'duty', requiredTeachers=item.requiredTeachers,
            roomRequired=item.kind == 'booking', audienceSize=size, rosterComplete=complete,
            audienceLowerBound=lower,
            profileId=item.profileId, buildingId=item.buildingId))
    return rows


def occupied_lunch_rules(state, day, rows):
    """Bookings and supervision occupy breaks without becoming taught lessons."""
    from .rules import RuleContext, minutes, lunch_available
    from .cycles import active_in, pattern_on
    if not any(r['activity'] != 'lesson' for r in rows): return [], []
    context = RuleContext(state)
    issues, warnings = [], []
    for rule in state.rules:
        if not (rule.enabled and rule.kind == 'day_shape' and rule.lunchMinutes and
                rule.scope not in ['subject', 'workload'] and active_in(rule, pattern_on(state, day)) and
                (not rule.days or day.weekday() in rule.days)): continue
        targets = context.targets(rule)
        buckets = defaultdict(list)
        for row in rows:
            if rule.scope == 'class':
                keys = ['student:' + s for s in row['studentIds'] if context.idx['students'][s].classId in targets]
                keys += ['class:' + c for c in row['classIds'] if c in targets and not context.class_students[c]]
            else:
                field = {'teacher':'teacherIds', 'student':'studentIds', 'room':'roomIds', 'group':'groupIds'}[rule.scope]
                keys = sorted(set(row[field]) & targets)
            for key in keys: buckets[key].append({**row, 'start': minutes(row['start']), 'end': minutes(row['end'])})
        start, end = minutes(rule.lunchStart), minutes(rule.lunchEnd)
        for key, (label, students) in context.bucket_names(rule).items():
            busy = buckets[key]
            if not any(r['activity'] != 'lesson' for r in busy): continue
            # The shared lesson evaluator already reports a missing lesson-only break.
            if not lunch_available([r for r in busy if r['activity'] == 'lesson'], start, end, rule.lunchMinutes): continue
            if lunch_available(busy, start, end, rule.lunchMinutes): continue
            entry = dict(type='dated_lunch', ruleId=rule.id, date=day.isoformat(), ids=sorted(r['id'] for r in busy),
                         message=f'{rule.name}: {label} — с учётом событий и дежурств нет перерыва {rule.lunchMinutes} минут в окне {rule.lunchStart}–{rule.lunchEnd}.',
                         affectedStudentIds=students, teacherIds=[key] if rule.scope == 'teacher' else [], amount=1)
            if rule.mode == 'soft': warnings.append({**entry, 'penalty': rule.weight})
            else: issues.append(entry)
    return issues, warnings


class CommitmentEdit(Model):
    revision: int
    value: Commitment | None = None
    removeIds: list[str] = Field(default_factory=list, max_length=500)
    copies: int = Field(default=1, ge=1, le=53)
    weekStep: int = Field(default=1, ge=1, le=8)


def edit_commitments(state, body):
    if bool(body.value) == bool(body.removeIds): raise ValueError('Укажите запись или список для удаления.')
    candidate = state.model_copy(deep=True)
    if body.removeIds:
        chosen = [x for x in state.commitments if x.id in body.removeIds]
        if len(chosen) != len(set(body.removeIds)): raise ValueError('Одна из записей не найдена.')
        if any(x.locked for x in chosen): raise ValueError('Сначала снимите закрепление выбранных записей.')
        candidate.commitments = [x for x in candidate.commitments if x.id not in body.removeIds]
    else:
        value = body.value
        previous = next((x for x in state.commitments if x.id == value.id), None)
        if previous and body.copies != 1: raise ValueError('Повторение доступно при создании новой записи.')
        if previous and previous.locked and value.locked and previous != value:
            raise ValueError('Сначала снимите закрепление записи.')
        candidate.commitments = [x for x in candidate.commitments if x.id != value.id] + [value]
        for number in range(1, body.copies):
            candidate.commitments.append(value.model_copy(update={'id': str(uuid4()), 'onDate': value.onDate + timedelta(weeks=number*body.weekStep)}))
    return State.model_validate(candidate.model_dump())


def duty_conflicts(state, ctx, rows, c, teacher):
    """Why a teacher cannot cover duty c; rows are that day's other non-cancelled rows."""
    from .cycles import active_in, pattern_on
    reasons = []
    if any(teacher in r['teacherIds'] and c.startTime < r['end'] and r['start'] < c.endTime for r in rows):
        reasons.append('урок, бронирование или другое дежурство')
    if any(a.teacherId == teacher and a.start <= c.onDate <= a.end and c.startTime < a.endTime and a.startTime < c.endTime for a in state.absences):
        reasons.append('отсутствие')
    bells = ctx.maps['profiles'][c.profileId].for_day(c.onDate.weekday())
    if any(f'{c.onDate.weekday()}:{i}' in ctx.maps['teachers'][teacher].unavailable and c.startTime < p.end and p.start < c.endTime for i,p in enumerate(bells)):
        reasons.append('запрет по номеру урока')
    if any(rule.enabled and rule.kind == 'availability' and rule.mode == 'hard' and rule.scope == 'teacher'
           and (not rule.targetIds or teacher in rule.targetIds) and (not rule.days or c.onDate.weekday() in rule.days)
           and active_in(rule, pattern_on(state, c.onDate))
           and any(w.day == c.onDate.weekday() and c.startTime < w.end and w.start < c.endTime for w in rule.unavailable) for rule in state.rules):
        reasons.append('обязательная недоступность')
    return reasons


class DutyRequest(Model):
    revision: int
    commitmentIds: list[str] = Field(min_length=1, max_length=200)
    seconds: int = Field(default=10, ge=1, le=30)
    maxMinutesPerTeacher: int = Field(default=0, ge=0, le=10000)


def allocate_duties(state, body):
    from ortools.sat.python import cp_model
    from .calendar_ops import DayContext, day_report, check_dates
    from .cycles import active_in, pattern_on
    from .editing import new_issues
    from .rules import minutes
    began = monotonic()
    chosen = [c for c in state.commitments if c.id in body.commitmentIds]
    if len(chosen) != len(set(body.commitmentIds)) or any(c.kind != 'duty' for c in chosen):
        raise ValueError('Выберите существующие дежурства без бронирований.')
    if any(c.locked and len(c.teacherIds) < c.requiredTeachers for c in chosen):
        raise ValueError('Закреплённое дежурство заполнено не полностью. Снимите закрепление.')
    slots = [c for c in chosen if not c.locked]
    if not slots: raise ValueError('Все выбранные дежурства закреплены.')
    teachers = {t.id: t for t in state.teachers}
    pool = sorted({id for c in slots for id in (c.eligibleTeacherIds or list(teachers))})
    if len(pool) > 300: raise ValueError('Для одного поиска выберите не больше 300 кандидатов.')
    slot_ids = {c.id for c in slots}
    empty = state.model_copy(update={'commitments': [c.model_copy(update={'teacherIds': []}) if c.id in slot_ids else c for c in state.commitments]})
    ctx = DayContext(empty)
    days = sorted({c.onDate for c in slots})
    reports = {d: day_report(empty, d, ctx) for d in days}
    fixed = {d: [r for r in reports[d]['rows'] if r['status'] != 'cancelled'] for d in days}
    by_id = {r['commitmentId']: r for report in reports.values() for r in report['rows'] if r.get('commitmentId')}
    model = cp_model.CpModel()
    variables, unavailable = {}, {}
    for c in slots:
        choices = c.eligibleTeacherIds or list(teachers)
        for teacher in choices:
            reasons = duty_conflicts(state, ctx, fixed[c.onDate], c, teacher)
            if reasons:
                unavailable[c.id, teacher] = ', '.join(reasons)
            else: variables[c.id, teacher] = model.new_bool_var(f'duty:{c.id}:{teacher}')
        available = [v for (id,_),v in variables.items() if id == c.id]
        if len(available) < c.requiredTeachers:
            return dict(status='infeasible', message=f'{c.name} · {c.onDate}: нужно {c.requiredTeachers}, доступно по времени {len(available)}.',
                        details=[f'{teachers[t].name}: {unavailable.get((c.id,t), "доступен")}' for t in choices])
        model.add(sum(available) == c.requiredTeachers)

    def building(row):
        values = {ctx.maps['rooms'][r].buildingId for r in row['roomIds'] if ctx.maps['rooms'][r].buildingId}
        if row.get('buildingId'): values.add(row['buildingId'])
        return next(iter(values)) if len(values) == 1 and (row.get('buildingId') or row['roomIds'] and all(ctx.maps['rooms'][r].buildingId for r in row['roomIds'])) else None

    for teacher in pool:
        for d in days:
            nodes = [(r, 1, False) for r in fixed[d] if teacher in r['teacherIds']]
            nodes += [(by_id[c.id], variables[c.id,teacher], True) for c in slots if c.onDate == d and (c.id,teacher) in variables]
            nodes.sort(key=lambda x: (x[0]['start'], x[0]['end'], x[0]['id']))
            for rule in state.rules:
                if not (rule.enabled and rule.mode == 'hard' and rule.kind == 'day_shape' and rule.scope == 'teacher'
                        and rule.lunchMinutes and (not rule.targetIds or teacher in rule.targetIds)
                        and (not rule.days or d.weekday() in rule.days) and active_in(rule, pattern_on(state, d))): continue
                start, end = minutes(rule.lunchStart), minutes(rule.lunchEnd)
                starts = {start} | {minutes(r['end']) for r,_,_ in nodes if start <= minutes(r['end']) <= end-rule.lunchMinutes}
                viable = [t for t in starts if not any(not selected and minutes(r['start']) < t+rule.lunchMinutes and t < minutes(r['end']) for r,_,selected in nodes)]
                # An already broken fixed day cannot be repaired by adding staff.
                if not viable: continue
                breaks = []
                for t in viable:
                    free = model.new_bool_var(f'break:{teacher}:{d}:{rule.id}:{t}')
                    for r, v, selected in nodes:
                        if selected and minutes(r['start']) < t+rule.lunchMinutes and t < minutes(r['end']): model.add(v + free <= 1)
                    breaks.append(free)
                model.add(sum(breaks) >= 1)
            for i,(a, va, selected_a) in enumerate(nodes):
                for j in range(i+1,len(nodes)):
                    b, vb, selected_b = nodes[j]
                    if not (selected_a or selected_b): continue
                    gap = minutes(b['start'])-minutes(a['end'])
                    if gap < 0:
                        model.add(va + vb <= 1)
                    elif gap < ctx.resources.travel(building(a),building(b)):
                        between = [v for r,v,_ in nodes[i+1:j] if r['start'] >= a['end'] and r['end'] <= b['start']]
                        model.add(va + vb <= 1 + sum(between))

    first_day, last_day = min(c.onDate for c in chosen), max(c.onDate for c in chosen)
    existing = defaultdict(int)
    for c in state.commitments:
        if c.kind == 'duty' and c.id not in slot_ids and first_day <= c.onDate <= last_day:
            for teacher in c.teacherIds: existing[teacher] += minutes(c.endTime)-minutes(c.startTime)
    scores, changed = [], []
    minutes_total = {}
    for teacher in pool:
        terms = [(minutes(c.endTime)-minutes(c.startTime),variables[c.id,teacher]) for c in slots if (c.id,teacher) in variables]
        new_minutes = sum(n*v for n,v in terms)
        if body.maxMinutesPerTeacher:
            model.add(new_minutes <= max(0,body.maxMinutesPerTeacher-existing[teacher]))
        bound = existing[teacher]+sum(n for n,_ in terms)
        total = model.new_int_var(existing[teacher],bound,f'minutes:{teacher}')
        model.add(total == existing[teacher]+new_minutes)
        square = model.new_int_var(0,bound*bound,f'fairness:{teacher}')
        model.add_multiplication_equality(square,[total,total]); scores.append(square)
        minutes_total[teacher]=total
    for c in slots:
        changed += [v for (id,t),v in variables.items() if id == c.id and t not in c.teacherIds]
    model.minimize(sum(scores))
    solver=cp_model.CpSolver(); solver.parameters.num_search_workers=4
    solver.parameters.max_time_in_seconds=max(.01,body.seconds-(monotonic()-began))
    status=solver.solve(model)
    if status not in [cp_model.OPTIMAL,cp_model.FEASIBLE]:
        return dict(status='infeasible' if status == cp_model.INFEASIBLE else 'timeout', message='Не удалось назначить всех дежурных с выбранными ограничениями.', details=['Проверьте пересекающиеся дежурства, переходы, список кандидатов и лимит минут.'])
    fairness_optimal = status == cp_model.OPTIMAL
    remaining = body.seconds-(monotonic()-began)
    if fairness_optimal and remaining > .1 and changed:
        optimum=solver.value(sum(scores)); model.add(sum(scores)==optimum); model.minimize(sum(changed))
        secondary=cp_model.CpSolver(); secondary.parameters.num_search_workers=4; secondary.parameters.max_time_in_seconds=remaining
        if secondary.solve(model) in [cp_model.OPTIMAL,cp_model.FEASIBLE]: solver=secondary
    assignments={c.id: sorted(t for (id,t),v in variables.items() if id==c.id and solver.value(v)) for c in slots}
    candidate=state.model_copy(update={'commitments':[c.model_copy(update={'teacherIds':assignments[c.id]}) if c.id in assignments else c for c in state.commitments]})
    candidate=State.model_validate(candidate.model_dump())
    problems=check_dates(candidate,days)
    added=new_issues({'issues':check_dates(state,days)}, {'issues':problems})
    if added:
        return dict(status='blocked',message='Назначения создают нарушения при общей проверке. Они не сохранены.',details=[i['message'] for i in added[:30]])
    return dict(status='success', candidate=candidate, assigned=len(slots), locked=len(chosen)-len(slots),
                fairnessOptimal=fairness_optimal, issues=problems, firstDate=first_day, lastDate=last_day,
                minutes=[dict(teacherId=t,previous=existing[t],total=solver.value(minutes_total[t])) for t in pool])


def register_commitments(app, Workspace, require_revision, create_preview, solve_lock):
    @app.post('/api/commitments/preview')
    def preview(body: CommitmentEdit, workspace: Workspace = 'school'):
        state = require_revision(workspace, body.revision)
        try: candidate = edit_commitments(state, body)
        except ValueError as exc: raise HTTPException(422, str(exc))
        return create_preview(workspace, state, candidate)

    @app.post('/api/commitments/allocate')
    def allocate(body: DutyRequest, workspace: Workspace = 'school'):
        if not solve_lock.acquire(blocking=False): raise HTTPException(409,'Другой поиск уже выполняется.')
        try:
            state=require_revision(workspace,body.revision)
            result=allocate_duties(state,body)
            if result['status']=='success': result['preview']=create_preview(workspace,state,result.pop('candidate'))
            return result
        except ValueError as exc: raise HTTPException(422,str(exc))
        finally: solve_lock.release()
