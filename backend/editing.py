"""Atomic timetable edits and human-readable comparisons, without storage writes."""
import json
from collections import Counter
from typing import Literal
from uuid import uuid4

from pydantic import Field
from .models import Model, Placement, Profile, State, TimetableRule, Campus, Cycles, Course, CourseRequest
from .resources import room_ids
from .scheduling import validate_schedule


class EditRequest(Model):
    revision: int
    action: Literal['move', 'swap', 'copy', 'lock', 'unlock', 'unplace', 'workloads', 'placement', 'profile', 'removeProfile', 'rule', 'removeRule', 'campus', 'cycles', 'copyCycle', 'course', 'removeCourse', 'courseRequest', 'removeCourseRequest']
    ids: list[str] = Field(default_factory=list, max_length=5000)
    day: int | None = Field(default=None, ge=0, le=6)
    period: int | None = Field(default=None, ge=0, le=11)
    roomId: str | None = None
    linked: bool = True
    increaseHours: bool = False
    patch: dict = Field(default_factory=dict)
    placement: Placement | None = None
    profile: Profile | None = None
    rule: TimetableRule | None = None
    campus: Campus | None = None
    cycles: Cycles | None = None
    cycleId: str | None = None
    sourceCycleId: str | None = None
    replaceCycle: bool = False
    course: Course | None = None
    courseRequest: CourseRequest | None = None


def linked_ids(state, ids):
    loads = {w.id: w for w in state.workloads}
    anchors = {(loads[p.workloadId].block, p.day, p.period, p.cycleId) for p in state.placements
               if p.id in ids and loads[p.workloadId].block}
    return ids | {p.id for p in state.placements
                  if (loads[p.workloadId].block, p.day, p.period, p.cycleId) in anchors}


def edit(state: State, body: EditRequest):
    next_state = state.model_copy(deep=True)
    if body.action in ['course', 'courseRequest', 'removeCourse', 'removeCourseRequest']:
        field = 'courses' if body.action in ['course', 'removeCourse'] else 'courseRequests'
        rows = getattr(next_state, field)
        if body.action.startswith('remove'):
            if not body.ids or set(body.ids) - {r.id for r in rows}:
                raise ValueError('Выберите существующие записи.')
            setattr(next_state, field, [r for r in rows if r.id not in body.ids])
        else:
            value = body.course if field == 'courses' else body.courseRequest
            if value is None:
                raise ValueError('Укажите курс или заявку.')
            setattr(next_state, field, [value if r.id == value.id else r for r in rows] if any(r.id == value.id for r in rows) else rows + [value])
    elif body.action == 'cycles':
        if body.cycles is None:
            raise ValueError('Укажите недели и учебные периоды.')
        next_state.cycles = body.cycles
    elif body.action == 'copyCycle':
        patterns = {p.id for p in state.cycles.patterns}
        if body.cycleId not in patterns or body.sourceCycleId not in patterns or body.cycleId == body.sourceCycleId:
            raise ValueError('Выберите разные исходную и целевую недели.')
        old = [p for p in state.placements if p.cycleId == body.cycleId]
        if old and not body.replaceCycle:
            raise ValueError('Целевая неделя не пустая. Отметьте замену её занятий и проверьте изменения.')
        if any(p.locked for p in old):
            raise ValueError('Сначала снимите закрепления в целевой неделе.')
        copied = [p.model_copy(update={'id': str(uuid4()), 'cycleId': body.cycleId, 'sourceCells': [], 'locked': False}) for p in state.placements if p.cycleId == body.sourceCycleId]
        if not copied:
            raise ValueError('В исходной неделе нет занятий.')
        next_state.placements = [p for p in next_state.placements if p.cycleId != body.cycleId] + copied
    elif body.action == 'campus':
        if body.campus is None:
            raise ValueError('Укажите здания и переходы.')
        next_state.campus = body.campus
    elif body.action == 'rule':
        if body.rule is None:
            raise ValueError('Укажите правило.')
        if any(r.id == body.rule.id for r in next_state.rules):
            next_state.rules = [body.rule if r.id == body.rule.id else r for r in next_state.rules]
        else:
            next_state.rules.append(body.rule)
    elif body.action == 'removeRule':
        if len(body.ids) != 1 or body.ids[0] not in {r.id for r in state.rules}:
            raise ValueError('Выберите правило для удаления.')
        next_state.rules = [r for r in next_state.rules if r.id != body.ids[0]]
    elif body.action == 'profile':
        if body.profile is None:
            raise ValueError('Укажите профиль звонков.')
        if any(p.id == body.profile.id for p in next_state.profiles):
            next_state.profiles = [body.profile if p.id == body.profile.id else p for p in next_state.profiles]
        else:
            next_state.profiles.append(body.profile)
    elif body.action == 'removeProfile':
        if len(body.ids) != 1 or body.ids[0] not in {p.id for p in state.profiles}:
            raise ValueError('Выберите профиль для удаления.')
        next_state.profiles = [p for p in next_state.profiles if p.id != body.ids[0]]
    elif body.action == 'placement':
        if body.placement is None:
            raise ValueError('Укажите занятие.')
        old = next((p for p in state.placements if p.id == body.placement.id), None)
        if old and old.locked and body.placement.locked and any(
            getattr(old, k) != getattr(body.placement, k) for k in ['day', 'period', 'roomId', 'workloadId', 'cycleId']
        ):
            raise ValueError('Сначала снимите закрепление занятия.')
        next_state.placements = [p for p in next_state.placements if p.id != body.placement.id] + [body.placement]
    elif body.action == 'workloads':
        allowed = {'teacherId', 'additionalTeacherIds', 'teacherRequired', 'subjectId', 'hours', 'maxPerDay', 'roomIds', 'requiredRoomIds', 'roomRequired', 'studentCount', 'block', 'allowedPeriods', 'lessonLengths', 'maxLessonBreak', 'cycleIds', 'cycleHours', 'cycleLessonLengths'}
        if not body.patch or set(body.patch) - allowed:
            raise ValueError('Выберите поддерживаемые поля нагрузки.')
        ids = set(body.ids)
        if not ids or ids - {w.id for w in state.workloads}:
            raise ValueError('Нагрузка не найдена.')
        for w in next_state.workloads:
            if w.id in ids:
                for key, value in body.patch.items():
                    setattr(w, key, value)
    else:
        ids = set(body.ids)
        if not ids or ids - {p.id for p in state.placements}:
            raise ValueError('Выберите существующие занятия.')
        if body.action == 'swap' and len(ids) != 2:
            raise ValueError('Для обмена выберите два занятия.')
        anchors = [p for p in state.placements if p.id in ids]
        swap_sets = [linked_ids(state, {p.id}) if body.linked else {p.id} for p in anchors] if body.action == 'swap' else []
        if body.linked:
            ids = linked_ids(state, ids)
        chosen = [p for p in next_state.placements if p.id in ids]
        if body.action not in ['lock', 'unlock'] and any(p.locked for p in chosen):
            raise ValueError('Среди выбранных или связанных уроков есть закреплённые. Сначала снимите закрепление.')
        if body.action in ['move', 'copy']:
            if not {'day', 'period', 'roomId', 'cycleId'} & body.model_fields_set:
                raise ValueError('Укажите новое время или кабинет.')
            if body.action == 'copy':
                chosen = [p.model_copy(update={'id': str(uuid4()), 'sourceCells': [], 'locked': False}) for p in chosen]
                next_state.placements.extend(chosen)
                if body.increaseHours:
                    additions = Counter((p.workloadId, body.cycleId or p.cycleId) for p in chosen)
                    for w in next_state.workloads:
                        for cycle in state.cycles.patterns:
                            count = additions[w.id, cycle.id]
                            if count:
                                if len(state.cycles.patterns) == 1 and not w.cycleHours:
                                    w.hours += count
                                    if w.lessonLengths:
                                        w.lessonLengths += [1]*count
                                else:
                                    w.cycleHours[cycle.id] = w.cycleHours.get(cycle.id, w.hours) + count
                                    lengths = w.cycleLessonLengths.get(cycle.id, w.lessonLengths)
                                    w.cycleLessonLengths[cycle.id] = lengths + [1]*count if lengths else []
            for p in chosen:
                if body.day is not None:
                    p.day = body.day
                if body.period is not None:
                    p.period = body.period
                if 'roomId' in body.model_fields_set:
                    p.roomId = body.roomId
                if body.cycleId is not None:
                    p.cycleId = body.cycleId
        elif body.action == 'swap':
            if swap_sets[0] & swap_sets[1]:
                raise ValueError('Эти занятия принадлежат одному синхронному блоку в одно время.')
            for p in chosen:
                target = anchors[1] if p.id in swap_sets[0] else anchors[0]
                p.day, p.period, p.cycleId = target.day, target.period, target.cycleId
        elif body.action in ['lock', 'unlock']:
            for p in chosen:
                p.locked = body.action == 'lock'
        elif body.action == 'unplace':
            next_state.placements = [p for p in next_state.placements if p.id not in ids]
    # model_copy/assignment skip Pydantic reference and bounds checks.
    return State.model_validate(next_state.model_dump())


def issue_signature(issue):
    return json.dumps(issue, ensure_ascii=False, sort_keys=True)


# Excess counters that an edit may reduce step by step. An issue of these types
# is not new when the same workload already had it and no counter grew.
REDUCIBLE = {
    'daily': lambda old, new: all(d in old and n <= old[d] for d, n in new.items()),
    'hours': lambda old, new: new['placed'] <= old['placed'],
}


def issue_identity(issue):
    if issue['type'] in REDUCIBLE:
        issue = {k: v for k, v in issue.items() if k not in ('observed', 'message')}
    return issue_signature(issue)


def new_issues(before, after):
    previous = {}
    for issue in before['issues']:
        previous.setdefault(issue_identity(issue), []).append(issue)
    def known(issue):
        signature, reducible = issue_signature(issue), REDUCIBLE.get(issue['type'])
        return any(issue_signature(old) == signature or reducible and reducible(old['observed'], issue['observed'])
                   for old in previous.get(issue_identity(issue), []))
    return [i for i in after['issues'] if not known(i)]


def placement_label(state, p):
    if p is None:
        return None
    w = next(w for w in state.workloads if w.id == p.workloadId)
    group = next(g.name for g in state.groups if g.id == w.groupId)
    subject = next(s.name for s in state.subjects if s.id == w.subjectId)
    slot = next(profile for profile in state.profiles if profile.id == w.profileId).for_day(p.day)[p.period]
    room = ', '.join(r.name for r in state.rooms if r.id in room_ids(w, p.roomId)) or 'без кабинета'
    cycle = next(c.name for c in state.cycles.patterns if c.id == p.cycleId)
    prefix = cycle + ' · ' if len(state.cycles.patterns) > 1 else ''
    return prefix + f'{group} · {subject} · {state.settings.days[p.day]}, {slot.label} ({slot.start}–{slot.end}) · {room}' + (' · закреплён' if p.locked else '')


def display_value(state, field, value):
    if field == 'effect': return {'note': 'Информационная отметка', 'cancel': 'Отмена занятий'}.get(value, value)
    if field == 'sections' and isinstance(value, list):
        names = {g.id: g.name for g in state.groups}
        return '; '.join(f'{names.get(s["groupId"], s["groupId"])}: {s["minSize"]}–{s["maxSize"]}' for s in value)
    if field in ['cycleHours', 'cycleLessonLengths'] and isinstance(value, dict):
        names = {p.id: p.name for p in state.cycles.patterns}
        return '; '.join(f'{names.get(id, id)}: {" + ".join(map(str, v)) or "одиночные" if isinstance(v, list) else v}' for id, v in value.items()) or 'Общие параметры'
    if field in ['cycleId', 'cycleIds']:
        names = {p.id: p.name for p in state.cycles.patterns}
        return ', '.join(names.get(id, id) for id in value) if isinstance(value, list) and value else 'Все недели' if value == [] else names.get(value, value)
    if field == 'buildingId':
        return next((b.name for b in state.campus.buildings if b.id == value), 'Не задано')
    table = {'teacherId': 'teachers', 'teacherIds': 'teachers', 'eligibleTeacherIds': 'teachers', 'groupIds': 'groups', 'additionalTeacherIds': 'teachers', 'subjectId': 'subjects', 'roomIds': 'rooms', 'requiredRoomIds': 'rooms', 'classIds': 'classes',
             'studentIds': 'students', 'studentId': 'students', 'courseIds': 'courses', 'lockedGroupId': 'groups', 'groupId': 'groups', 'profileId': 'profiles', 'gapProfileId': 'profiles', 'subjectIds': 'subjects', 'otherSubjectIds': 'subjects'}.get(field)
    if table:
        names = {x.id: x.name for x in getattr(state, table)}
        if isinstance(value, list):
            return ', '.join(names.get(id, id) for id in value) or 'Не заданы'
        return names.get(value, value) or 'Не задан'
    return json.dumps(value, ensure_ascii=False) if value is not None else '—'


def compare(before: State, after: State):
    changes = []
    totals = Counter()
    for table in ['classes', 'teachers', 'rooms', 'subjects', 'students', 'groups', 'profiles', 'workloads', 'placements', 'events', 'rules', 'courses', 'courseRequests', 'datedLessons', 'absences', 'commitments']:
        old, new = ({x.id: x for x in getattr(s, table)} for s in [before, after])
        for id in sorted(old.keys() | new.keys()):
            a, b = old.get(id), new.get(id)
            if a == b:
                continue
            kind = 'added' if a is None else 'removed' if b is None else 'changed'
            totals[kind] += 1
            ad, bd = (x.model_dump(mode='json') if x else {} for x in [a, b])
            fields = [k for k in ad.keys() | bd.keys() if ad.get(k) != bd.get(k)]
            if table == 'placements':
                old_label, new_label = placement_label(before, a), placement_label(after, b)
            else:
                old_label = getattr(a, 'name', a.id) if a else None
                new_label = getattr(b, 'name', b.id) if b else None
                if table == 'courseRequests':
                    old_label = next(s.name for s in before.students if s.id == a.studentId) if a else None
                    new_label = next(s.name for s in after.students if s.id == b.studentId) if b else None
                if table == 'workloads':
                    for obj, source, side in [(a, before, 'before'), (b, after, 'after')]:
                        if obj:
                            label = next(g.name for g in source.groups if g.id == obj.groupId) + ' · ' + next(s.name for s in source.subjects if s.id == obj.subjectId)
                            if side == 'before': old_label = label
                            else: new_label = label
                if table in ['datedLessons', 'absences']:
                    def dated_label(source, obj):
                        if obj is None: return None
                        if table == 'absences':
                            name = next(t.name for t in source.teachers if t.id == obj.teacherId)
                            return f'{name} · {obj.start}–{obj.end} · {obj.startTime}–{obj.endTime} · {obj.name}'
                        p = next((p for p in source.placements if p.id == obj.placementId), None)
                        w = next((w for w in source.workloads if p and w.id == p.workloadId), None)
                        group = next((g.name for g in source.groups if g.id == (obj.groupId or (w.groupId if w else None))), 'Исходный урок не найден')
                        subject = next((s.name for s in source.subjects if s.id == (obj.subjectId or (w.subjectId if w else None))), '')
                        slot = next(pr for pr in source.profiles if pr.id == w.profileId).for_day(p.day)[p.period] if w else None
                        start, end = obj.startTime or (slot.start if slot else ''), obj.endTime or (slot.end if slot else '')
                        action = {'change': 'Изменение', 'cancel': 'Отмена', 'add': 'Разовый урок'}[obj.kind]
                        return f'{action} · {obj.onDate} · {group} · {subject} · {start}–{end} · {obj.name}'
                    old_label, new_label = dated_label(before, a), dated_label(after, b)
                if table == 'commitments':
                    def commitment_label(source, obj):
                        if obj is None: return None
                        rooms = ', '.join(r.name for r in source.rooms if r.id in obj.roomIds)
                        return f'{obj.name} · {obj.onDate} · {obj.startTime}–{obj.endTime} · {obj.location or rooms}'
                    old_label, new_label = commitment_label(before, a), commitment_label(after, b)
            def display(source, obj, field, value):
                if table == 'rules' and obj and field == 'targetIds':
                    from .rules import RuleContext
                    context = RuleContext(source)
                    return ', '.join(context.label(obj.scope, id) for id in value) if value else 'Все записи выбранного типа'
                translations = {'hard': 'Обязательное условие', 'soft': 'Пожелание', 'availability': 'Недоступность',
                                'daily_load': 'Нагрузка за день', 'day_shape': 'Структура дня', 'distribution': 'Распределение по неделе',
                                'relation': 'Связь между предметами', 'before': 'Первый набор раньше второго', 'not_following': 'Второй не сразу после первого', 'same_day': 'Одни дни', 'different_days': 'Разные дни',
                                'class': 'Классы', 'teacher': 'Учителя', 'student': 'Ученики', 'group': 'Группы', 'subject': 'Предметы', 'room': 'Кабинеты', 'workload': 'Нагрузки'}
                if table == 'rules' and isinstance(value, str) and field in ['mode', 'kind', 'scope', 'relation']:
                    return translations.get(value, value)
                return display_value(source, field, value)
            changes.append(dict(table=table, id=id, kind=kind, before=old_label, after=new_label,
                                fields={k: {'before': ad.get(k), 'after': bd.get(k),
                                            'displayBefore': display(before, a, k, ad.get(k)),
                                            'displayAfter': display(after, b, k, bd.get(k))} for k in sorted(fields)}))
    for key in ['settings', 'importInfo', 'campus', 'cycles']:
        if getattr(before, key) != getattr(after, key):
            totals['changed'] += 1
            if key == 'cycles':
                def cycle_display(source):
                    names = {p.id: p.name for p in source.cycles.patterns}
                    return dict(patterns=' → '.join(names.values()), periods='; '.join(f'{p.name}: {p.start}–{p.end}, начало цикла {p.anchor}, ' + ' → '.join(names[id] for id in p.patternIds) for p in source.cycles.periods) or 'Весь учебный год')
                a, b = before.cycles.model_dump(mode='json'), after.cycles.model_dump(mode='json')
                ad, bd = cycle_display(before), cycle_display(after)
                changes.append(dict(table=key, id=key, kind='changed', before='Учебные циклы', after='Учебные циклы',
                                    fields={field: dict(before=a[field], after=b[field], displayBefore=ad[field], displayAfter=bd[field]) for field in a if a[field] != b[field]}))
                continue
            if key == 'campus':
                def campus_fields(source):
                    campus = source.campus
                    names = {b.id: b.name for b in campus.buildings}
                    return dict(buildings=', '.join(b.name for b in campus.buildings) or 'Нет зданий',
                                transfers='; '.join(f'{names[t.fromId]} → {names[t.toId]}: {t.minutes} мин.' for t in campus.transfers) or 'Нет отдельных направлений',
                                defaultTravelMinutes=f'{campus.defaultTravelMinutes} мин.')
                a, b = before.campus.model_dump(mode='json'), after.campus.model_dump(mode='json')
                ad, bd = campus_fields(before), campus_fields(after)
                changes.append(dict(table=key, id=key, kind='changed', before='Здания и переходы', after='Здания и переходы',
                                    fields={field: dict(before=a[field], after=b[field], displayBefore=ad[field], displayAfter=bd[field]) for field in a if a[field] != b[field]}))
                continue
            changes.append(dict(table=key, id=key, kind='changed', before=key, after=key,
                                fields={'value': {'before': getattr(before, key).model_dump(mode='json'), 'after': getattr(after, key).model_dump(mode='json')}}))
    return dict(total=len(changes), added=totals['added'], removed=totals['removed'], changed=totals['changed'], changes=changes)


def inspect_edit(before, after):
    from .calendar_ops import operational_dates, check_dates, summarize_dates
    report = validate_schedule(after)
    added = new_issues(validate_schedule(before), report)
    days = operational_dates(before) | operational_dates(after)
    if days:
        summary = summarize_dates(after, days)
        dated = summary['issues']
        added += new_issues({'issues': check_dates(before, days)}, {'issues': dated})
        report['datedIssueCount'] = len(dated)
        report['datedIssues'] = dated
        report['datedWarnings'] = summary['warnings']
        report['datedPenalty'] = summary['penalty']
    return dict(diff=compare(before, after), report=report, newIssues=added, canApply=not added)
