from collections import Counter, defaultdict
from itertools import combinations
from uuid import uuid4
import threading
from ortools.sat.python import cp_model
from .models import State, Placement
from .resources import teacher_ids, room_ids, room_choices, ResourceContext, travel_pairs, add_travel
from .rules import evaluate_rules, pattern_valid, add_patterns, add_rules, RulesCancelled


def minutes(value):
    a, b = value.split(':')
    return int(a) * 60 + int(b)


def indexes(state):
    return {key: {x.id: x for x in getattr(state, key)} for key in ['groups', 'workloads', 'teachers', 'rooms', 'subjects', 'profiles', 'classes', 'students']}


def times(p, idx):
    w = idx['workloads'][p.workloadId]
    slot = idx['profiles'][w.profileId].for_day(p.day)[p.period]
    return minutes(slot.start), minutes(slot.end)


def overlap(a, b):
    return a[0] < b[1] and b[0] < a[1]


def roster_complete(group):
    # None preserves manually entered rosters in older saved documents.
    return group.kind in ('whole', 'staff') or group.rosterStatus == 'complete' or bool(group.studentIds) and group.rosterStatus is None


def group_overlap(a, b, student_classes=None):
    if a.kind == 'staff' or b.kind == 'staff':
        return False
    if a.id == b.id:
        return True
    def actual_classes(g):
        if g.kind != 'whole' and roster_complete(g) and student_classes is not None:
            return {student_classes[s] for s in g.studentIds}
        return set(g.classIds)
    if not actual_classes(a) & actual_classes(b):
        return False
    if a.kind == 'whole' or b.kind == 'whole':
        whole, mixed = (a, b) if a.kind == 'whole' else (b, a)
        if roster_complete(mixed):
            return True
        if student_classes is not None and any(student_classes[s] in whole.classIds for s in mixed.studentIds):
            return True
        return None
    if set(a.studentIds) & set(b.studentIds):
        return True
    if roster_complete(a) and roster_complete(b):
        return False
    return None  # Unknown composition is never a proof of no conflict.


def _validate_week(state):
    idx = indexes(state)
    resource_context = ResourceContext(state)
    student_classes = {s.id: s.classId for s in state.students}
    members = {g.id: set(g.studentIds) if g.kind != 'whole' else
               {s.id for s in state.students if s.classId in g.classIds} for g in state.groups}
    issues = []
    buckets = defaultdict(list)
    counts = Counter()
    per_day = Counter()
    per_load = defaultdict(list)
    cached = {}
    travel_rows = []
    unknown_capacity = 0
    unknown_building = 0
    for p in state.placements:
        w = idx['workloads'][p.workloadId]
        counts[w.id] += 1
        per_load[w.id].append(p)
        per_day[w.id, p.day] += 1
        buckets[p.day].append(p)
        for teacher_id in teacher_ids(w):
            teacher = idx['teachers'][teacher_id]
            if f'{p.day}:{p.period}' in teacher.unavailable:
                issues.append(dict(type='availability', ids=[p.id], message=f'{teacher.name}: недоступен на этом уроке.'))
        room_check = resource_context.room_check(w, p.roomId)
        for message in room_check['errors']:
            issues.append(dict(type='room_capacity' if message.startswith('Вместимость') else 'room_building', ids=[p.id], message=message, observed=[room_check['size'], room_check['capacity'], room_ids(w, p.roomId)]))
        unknown_capacity += room_check['capacityUnknown']
        unknown_building += bool(resource_context.max_travel and resource_context.participants(w) and not room_check['building'])
        start, end = times(p, idx)
        travel_rows.append(dict(key=p.id, w=w, day=p.day, start=start, end=end, building=room_check['building']))
        if w.roomIds and p.roomId not in w.roomIds:
            issues.append(dict(type='room', ids=[p.id], message='Кабинет не входит в список допустимых для занятия.'))
        if w.allowedPeriods and p.period not in w.allowedPeriods:
            issues.append(dict(type='period', ids=[p.id], message='Этот номер урока запрещён в настройках нагрузки.'))
    for day, placements in buckets.items():
        ordered = sorted(placements, key=lambda p: times(p, idx)[0])
        for ai, a in enumerate(ordered):
            wa = idx['workloads'][a.workloadId]
            ta = times(a, idx)
            for b in ordered[ai + 1:]:
                tb = times(b, idx)
                if tb[0] >= ta[1]:
                    break
                if not overlap(ta, tb):
                    continue
                wb = idx['workloads'][b.workloadId]
                details = []
                shared_teachers = sorted(set(teacher_ids(wa)) & set(teacher_ids(wb)))
                shared_rooms = sorted(set(room_ids(wa, a.roomId)) & set(room_ids(wb, b.roomId)))
                details += ['учитель ' + idx['teachers'][id].name for id in shared_teachers]
                details += ['кабинет ' + idx['rooms'][id].name for id in shared_rooms]
                key = tuple(sorted([wa.groupId, wb.groupId]))
                if key not in cached:
                    cached[key] = group_overlap(idx['groups'][wa.groupId], idx['groups'][wb.groupId], student_classes)
                if cached[key]:
                    details.append('учащиеся / класс')
                if details:
                    ga, gb = idx['groups'][wa.groupId], idx['groups'][wb.groupId]
                    shared_students = members[ga.id] & members[gb.id]
                    # Whole-class conflicts can exist before pupil lists are entered.
                    # Preserve the class evidence without inventing missing pupil names.
                    shared_classes = (set(ga.classIds) & set(gb.classIds)
                                      if ga.kind == gb.kind == 'whole' else
                                      {student_classes[s] for s in shared_students}
                                      if ga.kind == 'whole' or gb.kind == 'whole' else set())
                    clock = lambda value: f'{value // 60:02d}:{value % 60:02d}'
                    collision = dict(day=day, start=clock(max(ta[0], tb[0])), end=clock(min(ta[1], tb[1])),
                                     studentIds=sorted(shared_students), classIds=sorted(shared_classes),
                                     teacherIds=shared_teachers, roomIds=shared_rooms)
                    issues.append(dict(type='collision', ids=sorted([a.id, b.id]), collision=collision, message=f'{state.settings.days[day]}, уроки {a.period} / {b.period}: {", ".join(details)}. {ga.name} · {idx["subjects"][wa.subjectId].name} ↔ {gb.name} · {idx["subjects"][wb.subjectId].name}.'))
    for a, b, required, people, _ in travel_pairs(resource_context, travel_rows):
        names = [idx['teachers'][x[8:]].name for x in people if x.startswith('teacher:')]
        pupils = sorted({x[8:] for x in people if x.startswith('student:')})
        classes = sorted({x[6:] for x in people if x.startswith('class:')})
        gap = b['start'] - a['end']
        who = ', '.join(names) or ('ученики' if pupils else 'класс')
        issues.append(dict(type='travel', ids=[a['key'], b['key']],
                           affectedStudentIds=pupils if not names else [],
                           message=f'{state.settings.days[a["day"]]}: {who}, на переход {gap} мин., нужно {required} мин.',
                           observed=[a['day'], a['end'], b['start'], required, classes]))
    for w in state.workloads:
        placements = per_load[w.id]
        if not pattern_valid(state, w, placements):
            issues.append(dict(type='lesson_pattern', ids=[w.id] + [p.id for p in placements],
                               message=f'{idx["groups"][w.groupId].name} · {idx["subjects"][w.subjectId].name}: уроки не образуют заданные занятия ({" + ".join(map(str, w.lessonLengths))}).',
                               observed=[(p.day, p.period, p.roomId) for p in placements]))
        if counts[w.id] > w.hours:
            issues.append(dict(type='hours', ids=[w.id], observed={'placed': counts[w.id], 'required': w.hours}, message=f'{idx["groups"][w.groupId].name}: размещено больше уроков, чем в нагрузке.'))
        daily_excess = {d: per_day[w.id, d] for d in range(len(state.settings.days)) if per_day[w.id, d] > w.maxPerDay}
        if daily_excess:
            issues.append(dict(type='daily', ids=[w.id], observed=daily_excess, message=f'{idx["groups"][w.groupId].name} · {idx["subjects"][w.subjectId].name}: превышен дневной предел предмета.'))
    incomplete_groups = [g for g in state.groups if not roster_complete(g) and any(w.groupId == g.id for w in state.workloads)]
    blocks = defaultdict(list)
    for w in state.workloads:
        if w.block:
            blocks[w.block].append(w)
    for name, loads in blocks.items():
        if len({(w.hours, w.profileId) for w in loads}) > 1:
            issues.append(dict(type='block_config', ids=[w.id for w in loads], message=f'В блоке «{name}» должны совпадать недельные часы и профиль звонков.'))
        slots = [{(p.day, *times(p, idx)) for p in state.placements if p.workloadId == w.id} for w in loads]
        if any(slot != slots[0] for slot in slots[1:]):
            issues.append(dict(type='block', ids=[w.id for w in loads], observed=[sorted(slot) for slot in slots], message=f'Занятия блока «{name}» должны проходить одновременно.'))
    rule_issues, warnings, penalty = evaluate_rules(state)
    issues.extend(rule_issues)
    return dict(issues=issues, issueCount=len(issues), warnings=warnings, warningCount=len(warnings), qualityPenalty=penalty, unknownGroups=len(incomplete_groups), unknownCapacity=unknown_capacity, unknownBuildings=unknown_building, missingTeachers=sum(w.teacherRequired and not teacher_ids(w) for w in state.workloads), missingRooms=sum(idx['workloads'][p.workloadId].roomRequired and not room_ids(idx['workloads'][p.workloadId], p.roomId) for p in state.placements), placed=len(state.placements), required=sum(w.hours for w in state.workloads), completeChecks=not incomplete_groups and all(not w.teacherRequired or teacher_ids(w) for w in state.workloads) and all(not idx['workloads'][p.workloadId].roomRequired or room_ids(idx['workloads'][p.workloadId], p.roomId) for p in state.placements) and not unknown_building and not unknown_capacity)


def issue_keys(report):
    return {(i['type'], tuple(sorted(i['ids']))) for i in report['issues']}


def _solve_week(state: State, workload_ids=None, seconds=15, cancel=None, progress=None, optimization=None):
    from .optimization import Optimization, measure
    weights = Optimization.model_validate(optimization or {})
    def cancelled():
        return dict(status='cancelled', message='Поиск отменён. Расписание не изменено.', details=[])
    if cancel is not None and cancel.is_set():
        return cancelled()
    idx = indexes(state)
    resource_context = ResourceContext(state)
    target_ids = set(workload_ids) if workload_ids is not None else {w.id for w in state.workloads}
    selected = [w for w in state.workloads if w.id in target_ids]
    if not selected:
        return dict(status='blocked', message='Добавьте учебную нагрузку для генерации.', details=[])
    blockers = []
    for w in selected:
        if cancel is not None and cancel.is_set():
            return cancelled()
        g = idx['groups'][w.groupId]
        label = f'{g.name} · {idx["subjects"][w.subjectId].name}'
        if not roster_complete(g):
            blockers.append(f'{label}: состав группы не заполнен полностью.')
        if w.teacherRequired and not teacher_ids(w):
            blockers.append(f'{label}: не указан учитель.')
        if w.roomRequired and not w.roomIds and not w.requiredRoomIds:
            blockers.append(f'{label}: задайте допустимые кабинеты.')
        if all(resource_context.room_check(w, room)['errors'] for room in room_choices(w)):
            blockers.append(f'{label}: нет подходящего сочетания кабинетов — ' + '; '.join(dict.fromkeys(message for room in room_choices(w) for message in resource_context.room_check(w, room)['errors'])))
        if w.hours > len(state.settings.days) * w.maxPerDay:
            blockers.append(f'{label}: недельная нагрузка больше дневного лимита × число дней.')
        if w.lessonLengths and max(w.lessonLengths) > w.maxPerDay:
            blockers.append(f'{label}: длина занятия больше дневного предела этой нагрузки.')
    fixed = [p for p in state.placements if p.workloadId not in target_ids or p.locked]
    # An unresolved external group could share students with a selected group.
    student_classes = {s.id: s.classId for s in state.students}
    checked_groups = set()
    for p in fixed:
        other = idx['groups'][idx['workloads'][p.workloadId].groupId]
        if other.id in checked_groups or roster_complete(other):
            continue
        checked_groups.add(other.id)
        if any(group_overlap(other, idx['groups'][w.groupId], student_classes) is None for w in selected):
            blockers.append(f'{other.name}: уточните состав группы, связанной с закреплёнными занятиями.')
            break
    if blockers:
        return dict(status='blocked', message=f'Нужно уточнить данные: {len(blockers)} пунктов.', details=blockers[:30])
    model = cp_model.CpModel()
    from .diagnostics import Constraints
    diagnostics = Constraints()
    def load_name(w):
        return f'{idx["groups"][w.groupId].name} · {idx["subjects"][w.subjectId].name}'
    def person_name(resource):
        kind, id = resource.split(':',1)
        table = {'student':'students','teacher':'teachers','class':'classes','room':'rooms'}[kind]
        prefix = {'student':'Ученик','teacher':'Учитель','class':'Класс','room':'Кабинет'}[kind]
        return prefix + ' ' + idx[table][id].name
    variables = {}
    intervals = defaultdict(list)
    fixed_windows = defaultdict(list)
    by_slot = defaultdict(list)
    by_load = defaultdict(list)
    by_day = defaultdict(list)
    objective = []
    resource_cache = {}

    def resources(w, room):
        key = w.id, room
        if key in resource_cache:
            return resource_cache[key]
        values = resource_context.participants(w) + ['room:' + id for id in room_ids(w, room)]
        resource_cache[key] = values
        return values

    # Outside-scope lessons stay immutable and reserve actual clock intervals.
    for p in fixed:
        if p.workloadId in target_ids:
            continue
        w = idx['workloads'][p.workloadId]
        start, end = times(p, idx)
        for resource in resources(w, p.roomId):
            fixed_windows[resource].append((p.day * 1440 + start, p.day * 1440 + end))
    old_positions = {(p.workloadId, p.day, p.period, p.roomId) for p in state.placements}
    for wi, w in enumerate(selected):
        if cancel is not None and cancel.is_set():
            return cancelled()
        if progress:
            progress(dict(phase='building', completed=wi, total=len(selected)))
        profile = idx['profiles'][w.profileId]
        teachers = [idx['teachers'][id] for id in teacher_ids(w)]
        for day in range(len(state.settings.days)):
            for period, slot in enumerate(profile.for_day(day)):
                if (w.allowedPeriods and period not in w.allowedPeriods) or any(f'{day}:{period}' in teacher.unavailable for teacher in teachers):
                    continue
                for room in room_choices(w):
                    if resource_context.room_check(w, room)['errors']:
                        continue
                    key = w.id, day, period, room
                    var = model.new_bool_var(str(key))
                    variables[key] = var
                    if key in old_positions:
                        model.add_hint(var,1)
                    by_load[w.id].append(var)
                    by_day[w.id, day].append(var)
                    by_slot[w.id, day, period].append(var)
                    start, end = minutes(slot.start), minutes(slot.end)
                    interval = (day * 1440 + start, day * 1440 + end, var)
                    for resource in resources(w, room):
                        intervals[resource].append(interval)
                        if any(overlap((day * 1440 + start, day * 1440 + end), window) for window in fixed_windows[resource]):
                            diagnostics.add(model.add(var == 0),'fixed_resource',resource,person_name(resource) + ': занятость уроками вне выбранной нагрузки',resource=resource)
                    objective.append(var * (period * weights.early + (0 if key in old_positions else weights.stability)))
        count = sum(bool(by_slot[w.id,day,period]) for day in range(len(state.settings.days)) for period in range(len(profile.periods)))
        diagnostics.add(model.add(sum(by_load[w.id]) == w.hours),'hours',w.id,f'{load_name(w)}: {w.hours} ч.; {count} доступных позиций по дням/периодам с учётом учителей и кабинетов',workloadIds=[w.id])
        for day in range(len(state.settings.days)):
            diagnostics.add(model.add(sum(by_day[w.id, day]) <= w.maxPerDay),'daily',w.id,f'{load_name(w)}: максимум {w.maxPerDay} ч. в день',workloadIds=[w.id])
            for period in range(len(profile.periods)):
                model.add(sum(by_slot[w.id, day, period]) <= 1)
    # Pupils with identical group memberships have the same interval list. One
    # family of clique constraints enforces those identical calendars without
    # weakening them; no duplicate full-school calendars are needed.
    shared_intervals = {}
    for resource, values in intervals.items():
        key = tuple(i[2].index for i in values)
        if key in shared_intervals:
            shared_intervals[key][1].append(resource)
        else:
            shared_intervals[key] = (values,[resource])
    for values, peers in shared_intervals.values():
        resource=peers[0]
        suffix=f' (такая же занятость ещё у {len(peers)-1})' if len(peers)>1 else ''
        from .intervals import overlap_cliques
        for clique in overlap_cliques(values):
            diagnostics.add(model.add(sum(clique)<=1),'resource',resource,person_name(resource) + ': уроки не могут пересекаться по времени' + suffix,resource=resource,equivalentResources=peers)
    for p in fixed:
        if p.workloadId in target_ids:
            key = p.workloadId, p.day, p.period, p.roomId
            if key not in variables:
                return dict(status='blocked', message='Закреплённый урок противоречит доступности учителя или кабинета.', details=[f'{load_name(idx["workloads"][p.workloadId])}: {state.settings.days[p.day]}, период {p.period}; проверьте разрешённые периоды, доступность учителей и допустимые кабинеты.'], placementIds=[p.id])
            diagnostics.add(model.add(variables[key] == 1),'lock',p.id,f'{load_name(idx["workloads"][p.workloadId])}: закрепление — {state.settings.days[p.day]}, период {p.period}',workloadIds=[p.workloadId],placementIds=[p.id])
    blocks = defaultdict(list)
    for w in selected:
        if w.block:
            blocks[w.block].append(w)
    for name, loads in blocks.items():
        all_block_ids = {w.id for w in state.workloads if w.block == name}
        if not all_block_ids <= target_ids:
            return dict(status='blocked', message=f'Выберите весь синхронный блок «{name}» для генерации.', details=[])
        if len({(w.hours, w.profileId) for w in loads}) > 1:
            return dict(status='blocked', message=f'В блоке «{name}» должны совпадать часы и профиль звонков.', details=[])
        for a, b in zip(loads, loads[1:]):
            for day in range(len(state.settings.days)):
                for period in range(len(idx['profiles'][a.profileId].periods)):
                    diagnostics.add(model.add(sum(by_slot[a.id, day, period]) == sum(by_slot[b.id, day, period])),'block',name,f'Синхронный блок «{name}»: одинаковое время всех групп',workloadIds=sorted(all_block_ids))
    add_patterns(model, state, selected, variables, idx, diagnostics)
    try:
        add_travel(model, state, variables, target_ids, resource_context, idx, cancel, diagnostics)
        preferences = []
        assumptions = add_rules(model, state, variables, target_ids, preferences, cancel=cancel, progress=progress)
        objective.extend(term * weights.preferences for term in preferences)
    except RulesCancelled:
        return cancelled()
    model.minimize(sum(objective))
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = seconds
    from .solver_process import configured_workers
    solver.parameters.num_search_workers = configured_workers()
    if progress:
        progress(dict(phase='searching', completed=len(selected), total=len(selected)))
    done = threading.Event()
    def watch_cancel():
        while not done.wait(0.1):
            if cancel is not None and cancel.is_set():
                solver.stop_search()
    watcher = threading.Thread(target=watch_cancel, daemon=True) if cancel is not None else None
    class Solutions(cp_model.CpSolverSolutionCallback):
        def __init__(self):
            super().__init__()
            self.count = 0
        def on_solution_callback(self):
            self.count += 1
            if progress:
                progress(dict(phase='searching', solutions=self.count, objective=self.objective_value, bound=self.best_objective_bound))
    if watcher:
        watcher.start()
    try:
        status = solver.solve(model, Solutions())
    finally:
        done.set()
        if watcher:
            watcher.join(timeout=1)
    if cancel is not None and cancel.is_set():
        return cancelled()
    if status not in [cp_model.OPTIMAL, cp_model.FEASIBLE]:
        conflicting = [assumptions[index] for index in solver.sufficient_assumptions_for_infeasibility() if index in assumptions] if status == cp_model.INFEASIBLE else []
        details = ['Проверьте доступность учителей, часы нагрузки, кабинеты и закреплённые уроки. Текущее расписание сохранено.']
        if conflicting:
            details = ['Несовместимый набор обязательных правил вместе с нагрузкой и закреплёнными уроками (набор не обязательно минимальный):'] + [r.name for r in conflicting]
        explanation = dict(status='not_needed',reasons=[])
        if status == cp_model.INFEASIBLE:
            if progress: progress(dict(phase='diagnosing'))
            explanation = diagnostics.explain(model,assumptions,seconds=min(10,seconds),cancel=cancel)
            if explanation['status']=='cancelled': return cancelled()
            if explanation['reasons']:
                details = ['Эти условия несовместимы с допустимыми периодами и кабинетами. Набор достаточный, но не обязательно минимальный; это не предложение отключить каждое условие:'] + [r['name'] for r in explanation['reasons']]
            elif explanation['status'] in ['timeout','size_limit','unavailable']:
                details += ['Подробный разбор не завершён в пределах времени/размера модели. Невозможность доказана основным поиском; конкретный набор причин не подтверждён.']
        return dict(status='infeasible' if status == cp_model.INFEASIBLE else 'timeout', message='Нет решения при заданных ограничениях.' if status == cp_model.INFEASIBLE else 'За отведённое время решение не найдено. Это не доказывает невозможность расписания.', details=details, conflictingRuleIds=[r.id for r in conflicting], diagnosis=explanation)
    placements = [p for p in fixed if p.workloadId not in target_ids]
    existing = {(p.workloadId, p.day, p.period, p.roomId): p for p in state.placements}
    chosen_keys = [key for key, var in variables.items() if solver.value(var)]
    preserved_ids = {existing[key].id for key in chosen_keys if key in existing}
    remaining = defaultdict(list)
    for p in sorted(state.placements, key=lambda p: (p.day, p.period, p.id)):
        if p.workloadId in target_ids and p.id not in preserved_ids:
            remaining[p.workloadId].append(p)
    for key in chosen_keys:
        w_id, day, period, room = key
        old = existing.get(key)
        if old is not None:
            placements.append(old)
        elif remaining[w_id]:
            # A moved lesson retains identity and import provenance. This also
            # makes comparisons show moves, rather than unrelated delete/add pairs.
            old = remaining[w_id].pop(0)
            placements.append(old.model_copy(update={'day': day, 'period': period, 'roomId': room}))
        else:
            placements.append(Placement(id=str(uuid4()), workloadId=w_id, day=day, period=period, roomId=room))
    candidate = state.model_copy(update={'placements': placements})
    report = _validate_week(candidate)
    old_report = _validate_week(state)
    new_keys = issue_keys(report) - issue_keys(old_report)
    if new_keys:
        return dict(status='blocked', message='Результат не прошёл независимую проверку. Исходное расписание сохранено.', details=[i['message'] for i in report['issues'] if (i['type'], tuple(sorted(i['ids']))) in new_keys][:10])
    return dict(status='success', message=f'Размещено {sum(w.hours for w in selected)} уроков.', placements=placements, details=[], optimal=status == cp_model.OPTIMAL,
                qualityPenalty=report['qualityPenalty'], previousQualityPenalty=old_report['qualityPenalty'],
                optimization=dict(weights=weights.model_dump(), before=measure(state,state,target_ids,old_report['qualityPenalty'],weights),
                                  after=measure(candidate,state,target_ids,report['qualityPenalty'],weights)))


def validate_schedule(state, cycle_id=None):
    from .cycles import week_state
    patterns = [p for p in state.cycles.patterns if cycle_id is None or p.id == cycle_id]
    if not patterns:
        raise ValueError('Неделя расписания не найдена.')
    result = dict(issues=[], warnings=[], completeChecks=True)
    counters = ['issueCount', 'warningCount', 'qualityPenalty', 'unknownGroups', 'unknownCapacity', 'unknownBuildings', 'missingTeachers', 'missingRooms', 'placed', 'required']
    result.update({key: 0 for key in counters})
    for pattern in patterns:
        report = _validate_week(week_state(state, pattern.id))
        for key in counters:
            result[key] += report[key]
        result['completeChecks'] = result['completeChecks'] and report['completeChecks']
        for key in ['issues', 'warnings']:
            for issue in report[key]:
                if len(state.cycles.patterns) > 1 or pattern.id != 'weekly':
                    issue = {**issue, 'cycleId': pattern.id, 'cycleName': pattern.name, 'message': pattern.name + ': ' + issue['message']}
                result[key].append(issue)
    return result


def solve(state: State, workload_ids=None, seconds=15, cancel=None, progress=None, cycle_ids=None, optimization=None):
    from .cycles import week_state
    import json
    patterns = [p for p in state.cycles.patterns if cycle_ids is None or p.id in cycle_ids]
    if not patterns or cycle_ids is not None and set(cycle_ids) - {p.id for p in patterns}:
        return dict(status='blocked', message='Выберите существующие недели расписания.', details=[])
    if workload_ids is not None and (not workload_ids or set(workload_ids)-{w.id for w in state.workloads}):
        return dict(status='blocked', message='Выберите существующие нагрузки.', details=[])
    tasks = []
    for pattern in patterns:
        view = week_state(state, pattern.id)
        ids = [w.id for w in view.workloads if workload_ids is None or w.id in workload_ids]
        if ids:
            tasks.append((pattern, view, ids))
    if not tasks:
        return dict(status='blocked', message='В выбранных неделях нет активной нагрузки.', details=[])
    placements = list(state.placements)
    optimal = True
    total = 0
    scores = None
    for i, (pattern, view, ids) in enumerate(tasks):
        def on_progress(value):
            if progress:
                progress({**value, 'cycleId': pattern.id, 'cycleName': pattern.name, 'cycleIndex': i+1, 'cycleTotal': len(tasks)})
        result = _solve_week(view, ids, seconds/max(1,len(tasks)), cancel=cancel, progress=on_progress, optimization=optimization)
        if result['status'] != 'success':
            if len(state.cycles.patterns) > 1:
                result['message'] = pattern.name + ': ' + result['message']
            return result  # Never apply partial results of a multi-week search.
        placements = [p for p in placements if p.cycleId != pattern.id] + [p.model_copy(update={'cycleId': pattern.id}) for p in result['placements']]
        optimal = optimal and result['optimal']
        total += sum(w.hours for w in view.workloads if w.id in ids)
        if scores is None:
            scores = result['optimization']
        else:
            for side in ['before','after']:
                for key, value in result['optimization'][side].items():
                    scores[side][key] += value
    candidate = State.model_validate({**state.model_dump(), 'placements': placements})
    report, before = validate_schedule(candidate), validate_schedule(state)
    signatures = {json.dumps(issue, ensure_ascii=False, sort_keys=True) for issue in before['issues']}
    added = [issue for issue in report['issues'] if json.dumps(issue, ensure_ascii=False, sort_keys=True) not in signatures]
    if added:
        return dict(status='blocked', message='Результат не прошёл независимую проверку. Исходное расписание сохранено.', details=[x['message'] for x in added][:10])
    return dict(status='success', message=f'Размещено {total} уроков в {len(tasks)} неделях расписания.', placements=placements,
                details=[], optimal=optimal, qualityPenalty=report['qualityPenalty'], previousQualityPenalty=before['qualityPenalty'], optimization=scores)
