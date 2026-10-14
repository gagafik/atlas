"""Shared rule semantics for independent validation and CP-SAT construction."""
from collections import Counter, defaultdict
from functools import lru_cache
from itertools import combinations
from .resources import teacher_ids, room_ids, room_choices
from .relationships import gap_metrics, relation_violations


TABLES = {'class': 'classes', 'teacher': 'teachers', 'student': 'students', 'group': 'groups',
          'subject': 'subjects', 'room': 'rooms', 'workload': 'workloads'}


class RulesCancelled(Exception):
    pass


def minutes(clock):
    h, m = map(int, clock.split(':'))
    return h * 60 + m


class RuleContext:
    def __init__(self, state):
        self.state = state
        self.idx = {table: {x.id: x for x in getattr(state, table)} for table in TABLES.values()}
        self.profiles = {p.id: p for p in state.profiles}
        self.class_students = {c.id: {s.id for s in state.students if s.classId == c.id and s.active} for c in state.classes}
        self.member_cache = {}
        self.binding_cache = {}
        self.target_cache = {}

    def members(self, w, class_ids=()):
        key = w.groupId, tuple(class_ids)
        if key not in self.member_cache:
            g = self.idx['groups'][w.groupId]
            classes = set(class_ids or g.classIds) & set(g.classIds)
            self.member_cache[key] = (set(g.studentIds) if g.kind != 'whole' else
                                      {s.id for s in self.state.students if s.classId in classes})
        return self.member_cache[key]

    def classes(self, w, class_ids=()):
        g = self.idx['groups'][w.groupId]
        if g.kind == 'whole':
            return set(class_ids or g.classIds) & set(g.classIds)
        if g.studentIds:
            return {self.idx['students'][id].classId for id in g.studentIds}
        return set(g.classIds)

    def targets(self, rule):
        if rule.id not in self.target_cache:
            self.target_cache[rule.id] = (set(rule.targetIds) if rule.targetIds else
                                           {s.id for s in self.state.students if s.active} if rule.scope == 'student' else
                                           set(self.idx[TABLES[rule.scope]]))
        return self.target_cache[rule.id]

    def matches(self, rule, row):
        w, targets = row['w'], self.targets(rule)
        return bool({
            'class': lambda: self.classes(w, row.get('classIds', ())) & targets,
            'student': lambda: self.members(w, row.get('classIds', ())) & targets,
            'teacher': lambda: set(teacher_ids(w)) & targets,
            'group': lambda: w.groupId in targets,
            'subject': lambda: w.subjectId in targets,
            'workload': lambda: w.id in targets,
            'room': lambda: set(room_ids(w, row['roomId'])) & targets,
        }[rule.scope]())

    def label(self, scope, id):
        if scope == 'workload':
            w = self.idx['workloads'][id]
            return self.idx['groups'][w.groupId].name + ' · ' + self.idx['subjects'][w.subjectId].name
        value = self.idx[TABLES[scope]][id]
        if scope == 'student':
            return value.name + ' · ' + self.idx['classes'][value.classId].name
        return value.name

    def bucket_names(self, rule):
        result = {}
        if rule.scope == 'class':
            for id in sorted(self.targets(rule)):
                if self.class_students[id]:
                    for student in self.class_students[id]:
                        result['student:' + student] = (self.label('student', student), [student])
                else:
                    result['class:' + id] = (self.label('class', id), [])
        elif rule.scope == 'subject':
            for w in self.state.workloads:
                for key in self.bindings(rule, w, None):
                    if key[1] == 'student':
                        label = self.label('subject', key[0]) + ' · ' + self.label('student', key[2])
                        result[key] = (label, [key[2]])
                    else:
                        result[key] = (self.label('subject', key[0]) + ' · ' + self.label('class', key[2]), [])
        else:
            for id in sorted(self.targets(rule)):
                result[id] = (self.label(rule.scope, id), [id] if rule.scope == 'student' else [])
        return result

    def bindings(self, rule, w, room, class_ids=()):
        key = rule.id, w.id, room if rule.scope == 'room' else None, tuple(class_ids)
        if key in self.binding_cache:
            return self.binding_cache[key]
        targets = self.targets(rule)
        if rule.scope in ['class', 'subject']:
            members = self.members(w, class_ids)
            classes = self.classes(w, class_ids)
            selected_classes = classes & targets if rule.scope == 'class' else classes if w.subjectId in targets else set()
            result = []
            for c in selected_classes:
                if self.class_students[c]:
                    result += ['student:' + id if rule.scope == 'class' else (w.subjectId, 'student', id)
                               for id in sorted(members & self.class_students[c])]
                else:
                    result.append('class:' + c if rule.scope == 'class' else (w.subjectId, 'class', c))
        elif rule.scope == 'student':
            result = sorted(self.members(w, class_ids) & targets)
        elif rule.scope == 'teacher':
            result = sorted(set(teacher_ids(w)) & targets)
        elif rule.scope == 'room':
            result = sorted(set(room_ids(w, room)) & targets)
        else:
            value = {'group': w.groupId, 'workload': w.id}[rule.scope]
            result = [value] if value in targets else []
        self.binding_cache[key] = result
        return result

    def row(self, w, day, period, room, var=1, placement=None):
        slot = self.profiles[w.profileId].for_day(day)[period]
        return dict(w=w, day=day, period=period, roomId=room, var=var,
                    start=minutes(slot.start), end=minutes(slot.end), id=placement.id if placement else w.id,
                    classIds=placement.classIds if placement else [])

    def skip_empty(self, rule, bucket, day):
        return False


def lunch_available(rows, start, end, duration):
    cursor = start
    for row in sorted(rows, key=lambda r: r['start']):
        if row['end'] <= start or row['start'] >= end:
            continue
        if row['start'] - cursor >= duration:
            return True
        cursor = max(cursor, row['end'])
    return end - cursor >= duration


def evaluate_rules(state, context=None, rows=None):
    if not any(r.enabled for r in state.rules):
        return [], [], 0
    context = context or RuleContext(state)
    if rows is None:
        rows = [context.row(context.idx['workloads'][p.workloadId], p.day, p.period, p.roomId, placement=p) for p in state.placements]
    issues, warnings, penalty = [], [], 0
    for rule in state.rules:
        if not rule.enabled:
            continue
        days = rule.days or list(range(len(state.settings.days)))
        def add(message, affected, amount, students=(), observed=None):
            nonlocal penalty
            entry = dict(type='rule', ruleId=rule.id, ids=sorted({r['id'] for r in affected}),
                         message=f'{rule.name}: {message}', observed=observed, amount=amount)
            if students:
                entry['affectedStudentIds'] = sorted(students)
            if rule.mode == 'soft':
                entry['penalty'] = amount * rule.weight
                penalty += entry['penalty']
                warnings.append(entry)
            else:
                issues.append(entry)
        if rule.kind == 'availability':
            for row in rows:
                if row['day'] in days and context.matches(rule, row) and any(
                    window.day == row['day'] and row['start'] < minutes(window.end) and minutes(window.start) < row['end']
                    for window in rule.unavailable
                ):
                    students = (context.members(row['w'], row['classIds']) & context.targets(rule) if rule.scope == 'student' else
                                {id for id in context.members(row['w'], row['classIds']) if context.idx['students'][id].classId in context.targets(rule)} if rule.scope == 'class' else [])
                    add(f'{state.settings.days[row["day"]]}, урок {row["period"]} попадает в недоступное время.', [row], 1,
                        students, observed=[row['day'], row['start'], row['end']])
            continue
        names = context.bucket_names(rule)
        buckets = defaultdict(list)
        for row in rows:
            if row['day'] in days:
                for bucket in context.bindings(rule, row['w'], row['roomId'], row['classIds']):
                    buckets[bucket].append(row)
        for bucket, (label, students) in names.items():
            all_rows = buckets[bucket]
            active_days = sorted({r['day'] for r in all_rows})
            if rule.kind == 'relation':
                titles = {'before': 'нарушен порядок предметов: все уроки первого набора должны завершиться до второго',
                          'not_following': 'предмет второго набора следует непосредственно за первым',
                          'same_day': 'предметы двух наборов должны встречаться в одни дни',
                          'different_days': 'предметы двух наборов должны проходить в разные дни'}
                for violated, affected, day in relation_violations(rule, all_rows):
                    if violated:
                        add(f'{label} · {state.settings.days[day]}: {titles[rule.relation]}.', affected, 1, students,
                            [day, rule.relation, [(r['w'].id, r['period']) for r in affected]])
                continue
            weekly_gaps = 0
            if rule.kind == 'distribution':
                if rule.minDays is not None and len(active_days) < rule.minDays:
                    add(f'{label}: занятий в {len(active_days)} днях, минимум {rule.minDays}.', all_rows, rule.minDays-len(active_days), students, active_days)
                if rule.maxDays is not None and len(active_days) > rule.maxDays:
                    add(f'{label}: занятий в {len(active_days)} днях, максимум {rule.maxDays}.', all_rows, len(active_days)-rule.maxDays, students, active_days)
                for a, b in combinations(active_days, 2):
                    if b-a < rule.minDayDistance:
                        add(f'{label}: дни {state.settings.days[a]} и {state.settings.days[b]} расположены слишком близко.',
                            [r for r in all_rows if r['day'] in [a, b]], 1, students, [a, b])
                continue
            for day in days:
                lessons = sorted([r for r in all_rows if r['day'] == day], key=lambda r: (r['start'], r['end']))
                prefix = f'{label} · {state.settings.days[day]}'
                if rule.kind == 'daily_load':
                    count = len(lessons)
                    if rule.minLessons is not None and (count or rule.includeEmptyDays and not context.skip_empty(rule, bucket, day)) and count < rule.minLessons:
                        add(f'{prefix}: {count} уроков, минимум {rule.minLessons}.', lessons, rule.minLessons-count, students, [day, count])
                    if rule.maxLessons is not None and count > rule.maxLessons:
                        add(f'{prefix}: {count} уроков, максимум {rule.maxLessons}.', lessons, count-rule.maxLessons, students, [day, count])
                    continue
                if not lessons:
                    continue
                if rule.earliestStart:
                    for row in lessons:
                        amount = max(0, minutes(rule.earliestStart)-row['start'])
                        if amount:
                            add(f'{prefix}: урок начинается раньше {rule.earliestStart}.', [row], amount, students, row['start'])
                if rule.latestEnd:
                    for row in lessons:
                        amount = max(0, row['end']-minutes(rule.latestEnd))
                        if amount:
                            add(f'{prefix}: урок заканчивается позже {rule.latestEnd}.', [row], amount, students, row['end'])
                idle = max(0, max(r['end'] for r in lessons)-lessons[0]['start']-sum(r['end']-r['start'] for r in lessons))
                if rule.maxIdleMinutes is not None and idle > rule.maxIdleMinutes:
                    add(f'{prefix}: {idle} минут между уроками, максимум {rule.maxIdleMinutes} (включая перемены).', lessons, idle-rule.maxIdleMinutes, students, [day, idle])
                gaps, gap_blocks = gap_metrics(rule, day, lessons, context.profiles)
                weekly_gaps += sum(gaps)
                for key, value, title in [('maxIdlePeriods', sum(gaps), 'пустых учебных периодов'), ('maxIdleBlocks', sum(gap_blocks), 'окон')]:
                    limit = getattr(rule, key)
                    if limit is not None and value > limit:
                        add(f'{prefix}: {title} {value}, максимум {limit}.', lessons, value-limit, students, [day, key, value])
                longest = run = 1
                for a, b in zip(lessons, lessons[1:]):
                    run = run+1 if b['start']-a['end'] <= rule.shortBreakMinutes else 1
                    longest = max(longest, run)
                if rule.maxConsecutive is not None and longest > rule.maxConsecutive:
                    add(f'{prefix}: серия из {longest} уроков, максимум {rule.maxConsecutive}.', lessons, longest-rule.maxConsecutive, students, [day, longest])
                if rule.lunchMinutes and not lunch_available(lessons, minutes(rule.lunchStart), minutes(rule.lunchEnd), rule.lunchMinutes):
                    add(f'{prefix}: нет перерыва {rule.lunchMinutes} минут в окне {rule.lunchStart}–{rule.lunchEnd}.', lessons, 1, students, day)
            if rule.kind == 'day_shape' and rule.maxWeeklyIdlePeriods is not None and weekly_gaps > rule.maxWeeklyIdlePeriods:
                add(f'{label}: пустых периодов за выбранные дни {weekly_gaps}, максимум {rule.maxWeeklyIdlePeriods}.',
                    all_rows, weekly_gaps-rule.maxWeeklyIdlePeriods, students, weekly_gaps)
    return issues, warnings, penalty


def pattern_valid(state, w, placements):
    if not w.lessonLengths or len(placements) != w.hours:
        return True
    profile = next(p for p in state.profiles if p.id == w.profileId)
    runs = []
    previous = None
    for p in sorted(placements, key=lambda p: (p.day, p.period, p.id)):
        adjacent = (previous and p.day == previous.day and p.period == previous.period+1 and p.roomId == previous.roomId
                    and minutes(profile.for_day(p.day)[p.period].start)-minutes(profile.for_day(p.day)[previous.period].end) <= w.maxLessonBreak)
        if adjacent:
            runs[-1] += 1
        else:
            runs.append(1)
        previous = p
    counts = Counter(w.lessonLengths)
    @lru_cache(None)
    def fill(remaining_runs, ones, twos, threes):
        if not remaining_runs:
            return ones == twos == threes == 0
        head, *tail = remaining_runs
        for size, count in [(1, ones), (2, twos), (3, threes)]:
            if count and head >= size:
                next_runs = tuple(([head-size] if head > size else []) + tail)
                if fill(next_runs, ones-(size == 1), twos-(size == 2), threes-(size == 3)):
                    return True
        return False
    return fill(tuple(runs), counts[1], counts[2], counts[3])


def add_patterns(model, state, selected, variables, idx, diagnostics=None):
    for w in selected:
        if not w.lessonLengths:
            continue
        counts = Counter(w.lessonLengths)
        covered = defaultdict(list)
        by_length = defaultdict(list)
        profile = idx['profiles'][w.profileId]
        def record(constraint):
            if diagnostics is not None:
                diagnostics.add(constraint,'pattern',w.id,f'{idx["groups"][w.groupId].name} · {idx["subjects"][w.subjectId].name}: длины занятий ' + ' + '.join(map(str,w.lessonLengths)),workloadIds=[w.id])
        for size in counts:
            for day in range(len(state.settings.days)):
                periods = profile.for_day(day)
                for start in range(len(periods)-size+1):
                    if any(minutes(periods[i+1].start)-minutes(periods[i].end) > w.maxLessonBreak for i in range(start, start+size-1)):
                        continue
                    for room in room_choices(w):
                        keys = [(w.id, day, p, room) for p in range(start, start+size)]
                        if not all(key in variables for key in keys):
                            continue
                        chunk = model.new_bool_var(f'pattern:{w.id}:{day}:{start}:{size}:{room}')
                        by_length[size].append(chunk)
                        for key in keys:
                            covered[key].append(chunk)
            record(model.add(sum(by_length[size]) == counts[size]))
        for key, var in variables.items():
            if key[0] == w.id:
                record(model.add(var == sum(covered[key])))


def add_rules(model, state, variables, target_ids, objective, cancel=None, progress=None):
    """Return assumption-index -> user rule for an infeasibility explanation."""
    if not any(r.enabled for r in state.rules):
        return {}
    context = RuleContext(state)
    rows = [context.row(context.idx['workloads'][w], day, period, room, var) for (w, day, period, room), var in variables.items()]
    rows += [context.row(context.idx['workloads'][p.workloadId], p.day, p.period, p.roomId, placement=p)
             for p in state.placements if p.workloadId not in target_ids]
    assumptions = {}
    serial = 0
    def boolean_any(values):
        nonlocal serial
        if any(isinstance(v, int) and v == 1 for v in values):
            return 1
        if not values:
            return 0
        serial += 1
        var = model.new_bool_var(f'occupied:{serial}')
        model.add_max_equality(var, values)
        return var
    def neg(value):
        return 1-value if isinstance(value, int) else value.Not()
    for ri, rule in enumerate(state.rules):
        if cancel is not None and cancel.is_set():
            raise RulesCancelled()
        if progress:
            progress(dict(phase='building_rules', completed=ri, total=len(state.rules)))
        if not rule.enabled:
            continue
        days = rule.days or list(range(len(state.settings.days)))
        gate = None
        if rule.mode == 'hard':
            gate = model.new_bool_var('rule:' + rule.id)
            model.add_assumption(gate)
            assumptions[gate.index] = rule
        def at_most(expression, limit):
            nonlocal serial
            if gate is not None:
                model.add(expression <= limit).only_enforce_if(gate)
            else:
                serial += 1
                excess = model.new_int_var(0, 1000000, f'penalty:{serial}')
                model.add_max_equality(excess, [0, expression-limit])
                objective.append(excess * rule.weight)
        if rule.kind == 'availability':
            forbidden = [r['var'] for r in rows if r['day'] in days and context.matches(rule, r) and any(
                window.day == r['day'] and r['start'] < minutes(window.end) and minutes(window.start) < r['end'] for window in rule.unavailable)]
            at_most(sum(forbidden), 0)
            continue
        names = context.bucket_names(rule)
        buckets = defaultdict(list)
        for row in rows:
            if row['day'] in days:
                for bucket in context.bindings(rule, row['w'], row['roomId'], row['classIds']):
                    buckets[bucket].append(row)
        for bucket in names:
            if cancel is not None and cancel.is_set():
                raise RulesCancelled()
            if rule.kind == 'relation':
                for violated, _, _ in relation_violations(rule, buckets[bucket], boolean_any,
                                                         lambda values: neg(boolean_any([neg(v) for v in values])), neg, cancel=cancel):
                    at_most(violated, 0)
                continue
            weekly_gaps = []
            by_day = {day: [r for r in buckets[bucket] if r['day'] == day] for day in days}
            active = {day: boolean_any([r['var'] for r in values]) for day, values in by_day.items()}
            if rule.kind == 'distribution':
                if rule.minDays is not None:
                    at_most(rule.minDays-sum(active.values()), 0)
                if rule.maxDays is not None:
                    at_most(sum(active.values()), rule.maxDays)
                for a, b in combinations(sorted(days), 2):
                    if b-a < rule.minDayDistance:
                        at_most(active[a]+active[b], 1)
                continue
            for day, lessons in by_day.items():
                if rule.kind == 'daily_load':
                    count = sum(r['var'] for r in lessons)
                    if rule.minLessons is not None:
                        at_most(rule.minLessons*(1 if rule.includeEmptyDays else active[day])-count, 0)
                    if rule.maxLessons is not None:
                        at_most(count, rule.maxLessons)
                    continue
                if not lessons:
                    continue
                if rule.earliestStart:
                    at_most(sum(r['var']*max(0, minutes(rule.earliestStart)-r['start']) for r in lessons), 0)
                if rule.latestEnd:
                    at_most(sum(r['var']*max(0, r['end']-minutes(rule.latestEnd)) for r in lessons), 0)
                if rule.maxIdleMinutes is not None:
                    serial += 1
                    first = model.new_int_var(0, 1440, f'first:{serial}')
                    last = model.new_int_var(0, 1440, f'last:{serial}')
                    model.add_min_equality(first, [1440-(1440-r['start'])*r['var'] for r in lessons] + [1440])
                    model.add_max_equality(last, [r['end']*r['var'] for r in lessons] + [0])
                    at_most(last-first-sum((r['end']-r['start'])*r['var'] for r in lessons), rule.maxIdleMinutes)
                gaps, gap_blocks = gap_metrics(rule, day, lessons, context.profiles, boolean_any,
                                               lambda values: neg(boolean_any([neg(v) for v in values])), neg)
                weekly_gaps.extend(gaps)
                if rule.maxIdlePeriods is not None:
                    at_most(sum(gaps), rule.maxIdlePeriods)
                if rule.maxIdleBlocks is not None:
                    at_most(sum(gap_blocks), rule.maxIdleBlocks)
                if rule.maxConsecutive is not None:
                    slots = defaultdict(list)
                    for row in lessons:
                        slots[row['start'], row['end']].append(row['var'])
                    occupancy = [(a, b, boolean_any(values)) for (a, b), values in sorted(slots.items())]
                    runs = []
                    for i, (start, end, var) in enumerate(occupancy):
                        serial += 1
                        run = model.new_int_var(0, len(occupancy), f'run:{serial}')
                        model.add(run == 0).only_enforce_if(neg(var))
                        model.add(run >= 1).only_enforce_if(var)
                        for j in range(i):
                            if 0 <= start-occupancy[j][1] <= rule.shortBreakMinutes:
                                model.add(run >= runs[j]+1).only_enforce_if([var, occupancy[j][2]])
                        runs.append(run)
                    longest = model.new_int_var(0, len(occupancy), f'longest:{serial}')
                    model.add_max_equality(longest, runs + [0])
                    at_most(longest, rule.maxConsecutive)
                if rule.lunchMinutes:
                    start, end = minutes(rule.lunchStart), minutes(rule.lunchEnd)
                    starts = sorted({start} | {r['end'] for r in lessons if start <= r['end'] <= end-rule.lunchMinutes})
                    free = [neg(boolean_any([r['var'] for r in lessons if r['start'] < t+rule.lunchMinutes and t < r['end']])) for t in starts]
                    at_most(1-boolean_any(free), 0)
            if rule.kind == 'day_shape' and rule.maxWeeklyIdlePeriods is not None:
                at_most(sum(weekly_gaps), rule.maxWeeklyIdlePeriods)
    return assumptions
