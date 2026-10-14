"""Shared participants, room capacity and directed inter-building transfers."""
from collections import defaultdict


def teacher_ids(w):
    return ([w.teacherId] if w.teacherId else []) + w.additionalTeacherIds


def room_ids(w, primary):
    return list(dict.fromkeys(([primary] if primary else []) + w.requiredRoomIds))


def room_choices(w):
    return w.roomIds or [None]


class ResourceContext:
    def __init__(self, state):
        self.state = state
        self.rooms = {r.id: r for r in state.rooms}
        self.groups = {g.id: g for g in state.groups}
        self.classes = {c.id: c for c in state.classes}
        self.class_members = {c.id: {s.id for s in state.students if s.classId == c.id} for c in state.classes}
        self.active = {s.id for s in state.students if s.active}
        self.routes = {(t.fromId, t.toId): t.minutes for t in state.campus.transfers}
        self.max_travel = max([state.campus.defaultTravelMinutes, *self.routes.values()])
        self.cache = {}

    def participants(self, w):
        key = ('people', w.id)
        if key not in self.cache:
            g = self.groups[w.groupId]
            members = set(g.studentIds) if g.kind != 'whole' else set().union(*(self.class_members[c] for c in g.classIds))
            values = ['student:' + s for s in sorted(members)] + ['teacher:' + t for t in teacher_ids(w)]
            if g.kind == 'whole':
                values += ['class:' + c for c in g.classIds if not self.class_members[c]]
            self.cache[key] = values
        return self.cache[key]

    def audience_size(self, w):
        key = ('size', w.id)
        if key not in self.cache:
            g = self.groups[w.groupId]
            known = len([p for p in self.participants(w) if p.startswith('student:') and p[8:] in self.active])
            if g.kind == 'staff':
                result = len(teacher_ids(w))
            elif w.studentCount is not None:
                result = max(known, w.studentCount)
            elif g.kind == 'whole':
                # A partial imported roster is not evidence of total class size.
                result = sum(max(len(self.class_members[c]), self.classes[c].studentCount or 0) for c in g.classIds) if all(self.classes[c].studentCount is not None for c in g.classIds) else None
            else:
                result = known if g.rosterStatus == 'complete' or g.studentIds and g.rosterStatus is None else None
            self.cache[key] = result
        return self.cache[key]

    def room_check(self, w, primary):
        key = ('room', w.id, primary)
        if key not in self.cache:
            rooms = [self.rooms[id] for id in room_ids(w, primary)]
            size = self.audience_size(w)
            buildings = {r.buildingId for r in rooms if r.buildingId}
            capacity = sum(r.capacity for r in rooms) if rooms and all(r.capacity is not None for r in rooms) else None
            errors = []
            if len(buildings) > 1:
                errors.append('Кабинеты одного занятия находятся в разных зданиях.')
            if size is not None and capacity is not None and size > capacity:
                errors.append(f'Вместимость кабинетов {capacity}, участников {size}.')
            self.cache[key] = dict(errors=errors, capacity=capacity, size=size,
                                   capacityUnknown=bool(rooms) and (capacity is None or size is None),
                                   building=next(iter(buildings)) if len(buildings) == 1 and all(r.buildingId for r in rooms) else None)
        return self.cache[key]

    def travel(self, source, target):
        if not source or not target or source == target:
            return 0
        return self.routes.get((source, target), self.state.campus.defaultTravelMinutes)


def travel_pairs(context, rows, cancel=None, candidates=False):
    """Yield each conflicting lesson pair once, with all shared participants.

    Rows are clock intervals within one day, with stable unique keys. Candidate
    room alternatives are collapsed by the caller, avoiding a room cross product.
    """
    if not context.max_travel:
        return
    buckets = defaultdict(list)
    for row in rows:
        for person in context.participants(row['w']):
            buckets[person, row['day']].append(row)
    found = {}
    for (person, _), values in buckets.items():
        if cancel is not None and cancel.is_set():
            from .rules import RulesCancelled
            raise RulesCancelled()
        ordered = sorted(values, key=lambda r: (r['start'], r['end'], str(r['key'])))
        for i, a in enumerate(ordered):
            if cancel is not None and cancel.is_set():
                from .rules import RulesCancelled
                raise RulesCancelled()
            for j in range(i+1, len(ordered)):
                b = ordered[j]
                if b['start'] >= a['end'] + context.max_travel:
                    break
                gap = b['start'] - a['end']
                required = context.travel(a['building'], b['building'])
                if gap >= 0 and gap < required:
                    between = [r for r in ordered[i+1:j] if r['start'] >= a['end'] and r['end'] <= b['start']]
                    if between and not candidates:
                        continue
                    # Transfer time applies to consecutive lessons for each
                    # person. A real intervening lesson replaces this journey.
                    key = a['key'], b['key'], tuple(sorted((str(r['key']) for r in between)))
                    if key not in found:
                        found[key] = (a, b, required, [], between)
                    found[key][3].append(person)
    yield from found.values()


def add_travel(model, state, variables, target_ids, context, idx, cancel=None, diagnostics=None):
    if not context.max_travel:
        return
    rows, grouped = [], defaultdict(list)
    for (wid, day, period, room), var in variables.items():
        if cancel is not None and cancel.is_set():
            from .rules import RulesCancelled
            raise RulesCancelled()
        w = idx['workloads'][wid]
        building = context.room_check(w, room)['building']
        grouped[wid, day, period, building].append(var)
    for key, choices in grouped.items():
        wid, day, period, building = key
        w = idx['workloads'][wid]
        slot = idx['profiles'][w.profileId].for_day(day)[period]
        from .scheduling import minutes
        rows.append(dict(key=key, w=w, day=day, start=minutes(slot.start), end=minutes(slot.end), building=building, var=sum(choices)))
    for p in state.placements:
        if p.workloadId not in target_ids:
            w = idx['workloads'][p.workloadId]
            from .scheduling import times
            start, end = times(p, idx)
            rows.append(dict(key=p.id, w=w, day=p.day, start=start, end=end, building=context.room_check(w, p.roomId)['building'], var=1))
    for a, b, _, _, between in travel_pairs(context, rows, cancel, candidates=True):
        # Existing out-of-scope problems remain visible, but do not prevent
        # fixing an unrelated selection.
        if a['w'].id in target_ids or b['w'].id in target_ids:
            constraint = model.add(a['var'] + b['var'] <= 1 + sum(r['var'] for r in between))
            if diagnostics is not None:
                pair = sorted({a['w'].id,b['w'].id})
                names = [idx['groups'][idx['workloads'][id].groupId].name + ' · ' + idx['subjects'][idx['workloads'][id].subjectId].name for id in pair]
                diagnostics.add(constraint,'travel',':'.join(pair),'Время перехода между зданиями: ' + ' / '.join(names),workloadIds=pair)
