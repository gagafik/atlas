"""Ranked substitutes, whole-day plans and monthly substitution totals."""
import re
from collections import Counter, defaultdict
from datetime import date, timedelta
from typing import Literal
from uuid import uuid4
from fastapi import HTTPException
from pydantic import Field
from .models import Model, State, DatedLesson, SubstitutionWeights
from .resources import teacher_ids
from .calendar_ops import TYPES, DayContext, day_report, resolve_day, dates, change_type, staffing_check, plain

# Substitutions, merges and self-study take a lesson from its planned teacher and count against fairness.
COUNTED = {'substitution', 'merge', 'self_study'}
CHECKED, ENOUGH = 15, 5


class SubstitutionProfile:
    """What each teacher teaches, derived from workloads: subjects, groups and classes."""
    def __init__(self, ctx):
        self.subjects, self.groups, self.classes = defaultdict(set), defaultdict(set), defaultdict(set)
        for w in ctx.maps['workloads'].values():
            classes = ctx.maps['groups'][w.groupId].classIds
            for t in teacher_ids(w):
                self.subjects[t].add(w.subjectId); self.groups[t].add(w.groupId); self.classes[t].update(classes)


def profile_of(ctx):
    if getattr(ctx, 'substitution_profile', None) is None: ctx.substitution_profile = SubstitutionProfile(ctx)
    return ctx.substitution_profile


def month_bounds(day):
    first = day.replace(day=1)
    return first, (first + timedelta(days=32)).replace(day=1) - timedelta(days=1)


def substitution_counts(state, ctx, first, last, exclude=None):
    """Lessons each teacher took over from the planned teachers between two dates."""
    counts = Counter()
    for c in state.datedLessons:
        if c.kind != 'change' or not first <= c.onDate <= last or c.id == exclude or change_type(c) not in COUNTED: continue
        p = ctx.maps['placements'].get(c.placementId)
        original = set(teacher_ids(ctx.maps['workloads'][p.workloadId])) if p else set()
        for t in set(c.teacherIds or []) - original: counts[t] += 1
    return counts


def building_of(ctx, row):
    rooms = [ctx.maps['rooms'][id] for id in row['roomIds']]
    ids = {r.buildingId for r in rooms if r.buildingId}
    return next(iter(ids)) if len(ids) == 1 and all(r.buildingId for r in rooms) else row.get('buildingId')


def factors(state, ctx, weights, profile, rows, row, teacher, counts, most):
    """Points per criterion with a plain-language explanation."""
    half = lambda x: (x + 1) // 2
    own = [r for r in rows if r['status'] != 'cancelled' and teacher in r['teacherIds'] and r['id'] != row['id']]
    before = max((r for r in own if r['end'] <= row['start']), key=lambda r: r['end'], default=None)
    after = min((r for r in own if r['start'] >= row['end']), key=lambda r: r['start'], default=None)
    result = []
    def add(key, label, points, maximum, detail): result.append(dict(key=key, label=label, points=points, max=maximum, detail=detail))
    teaches = row['subjectId'] in profile.subjects[teacher]
    add('subject', 'Ведёт предмет', weights.subject if teaches else 0, weights.subject, 'Ведёт этот предмет' if teaches else 'Не ведёт этот предмет')
    if set(row['groupIds']) & profile.groups[teacher]: points, detail = weights.group, 'Ведёт эту группу'
    elif set(row['originalClassIds']) & profile.classes[teacher]: points, detail = half(weights.group), 'Ведёт уроки в этом классе'
    else: points, detail = 0, 'Не ведёт уроки у этих учеников'
    add('group', 'Знает учеников', points, weights.group, detail)
    if before and after: points, detail = weights.presence, 'Уроки до и после — без лишнего прихода'
    elif before or after: points, detail = half(weights.presence), 'Есть урок до' if before else 'Есть урок после'
    else: points, detail = 0, 'В этот день других уроков нет'
    add('presence', 'Уже в школе', points, weights.presence, detail)
    count = counts[teacher]
    points = weights.fairness if most == 0 else (2 * weights.fairness * (most - count) + most) // (2 * most)
    add('fairness', 'Равномерность', points, weights.fairness, f'Замен в этом месяце: {count}')
    target, near = building_of(ctx, row), [r for r in (before, after) if r]
    if not state.campus.buildings or target is None: points, detail = 0, 'Здания кабинетов не заданы'
    elif not near: points, detail = 0, 'Соседних уроков нет'
    else:
        same = [building_of(ctx, r) == target for r in near]
        if all(same): points, detail = weights.building, 'Соседние уроки в том же здании'
        elif any(same): points, detail = half(weights.building), 'Один соседний урок в том же здании'
        else: points, detail = 0, 'Нужен переход в другое здание'
    add('building', 'Здание', points, weights.building, detail)
    return result


def rank(state, ctx, rows, row, retained, check, teachers=None, exclude=None):
    """Order candidates: free first, then by points, fewer lessons that day, name.

    `check(teacher_id)` returns the reasons a teacher cannot take the lesson;
    without it every teacher passes and only the points order the list."""
    weights, profile = state.settings.substitutionWeights, profile_of(ctx)
    counts = substitution_counts(state, ctx, *month_bounds(date.fromisoformat(row['date'])), exclude=exclude)
    most = max((counts[t.id] for t in state.teachers), default=0)
    result = []
    for teacher in state.teachers if teachers is None else teachers:
        if teacher.id in retained: continue
        reasons = check(teacher.id) if check else []
        found = factors(state, ctx, weights, profile, rows, row, teacher.id, counts, most)
        result.append(dict(id=teacher.id, name=teacher.name, available=not reasons, reasons=reasons,
                           lessons=sum(teacher.id in r['teacherIds'] and r['status'] != 'cancelled' for r in rows),
                           substitutions=counts[teacher.id], score=sum(f['points'] for f in found), factors=found))
    return sorted(result, key=lambda c: (not c['available'], -c['score'], c['lessons'], c['name']))


def merge_options(ctx, rows, row):
    """Lessons at the same time a group could join: same teachers and rooms, joint capacity of every group in the host."""
    if any(r.get('mergeWith') == row['id'] for r in rows): return []
    result = []
    for r in rows:
        if (r['id'] == row['id'] or r['status'] == 'cancelled' or r['activity'] != 'lesson' or r.get('mergeWith') or
                ctx.maps['groups'][r['groupId']].kind == 'staff' or not (r['start'] < row['end'] and row['start'] < r['end'])): continue
        rooms = [ctx.maps['rooms'][id] for id in r['roomIds']]
        capacity = sum(x.capacity for x in rooms) if rooms and all(x.capacity is not None for x in rooms) else None
        # Groups already merged into the host take their places too (this lesson's own current merge is not counted twice).
        sizes = [r['audienceSize'], row['audienceSize'], *(x['audienceSize'] for x in rows if x.get('mergeWith') == r['id'] and x['id'] != row['id'] and x['status'] != 'cancelled')]
        attendance = sum(sizes) if None not in sizes else None
        result.append(dict(rowId=r['id'], group=r['group'], subject=r['subject'], teachers=r['teachers'], rooms=r['rooms'], teacherIds=r['teacherIds'], roomIds=r['roomIds'],
                           start=r['start'], end=r['end'], capacity=capacity, attendance=attendance,
                           fits=attendance <= capacity if attendance is not None and capacity is not None else None))
    return sorted(result, key=lambda o: (o['subject'] != row['subject'], o['group'], o['rowId']))


class DayPlanRequest(Model):
    revision: int
    day: date
    teacherId: str = Field(min_length=1, max_length=100)


class DayAssignment(Model):
    rowId: str = Field(min_length=1, max_length=200)
    action: Literal['substitution', 'merge', 'self_study', 'cancel', 'none']
    teacherId: str | None = Field(default=None, max_length=100)
    mergeWith: str | None = Field(default=None, max_length=200)


class DayPlanPreview(DayPlanRequest):
    name: str = Field(default='Замена', min_length=1, max_length=160)
    assignments: list[DayAssignment] = Field(min_length=1, max_length=20)


def lesson_scope(state, row):
    """Which actions the whole-day plan may take for a lesson, from the stored change."""
    change = next((c for c in state.datedLessons if c.id == row['changeId']), None) if row['changeId'] else None
    if change and change.kind == 'add': return ['substitution', 'none'], 'Разовый урок: доступна только замена учителя'
    if change and change.onDate != change.sourceDate: return ['substitution', 'none'], 'Перенесённый урок: доступна только замена учителя'
    return ['substitution', 'merge', 'self_study', 'cancel', 'none'], None


def day_plan(state, body):
    """Greedy proposal of a replacement for every lesson of an absent teacher; stores nothing."""
    people = {t.id: t for t in state.teachers}
    if body.teacherId not in people: raise ValueError('Учитель не найден.')
    if not state.settings.yearStart <= body.day <= state.settings.yearEnd: raise ValueError('Дата вне учебного года.')
    ctx = DayContext(state)
    rows = [dict(r) for r in day_report(state, body.day, ctx)['rows']]
    original = [dict(r) for r in rows]
    absences = [a for a in state.absences if a.teacherId == body.teacherId and a.start <= body.day <= a.end]
    lessons = [r for r in rows if r['status'] != 'cancelled' and r['activity'] == 'lesson' and body.teacherId in r['teacherIds']
               and ctx.maps['groups'][r['groupId']].kind != 'staff']
    # A group merged into another lesson of the same teacher follows its host: plan hosts first.
    hosted = {r['id'] for r in lessons if r.get('mergeWith') in {x['id'] for x in lessons}}
    lessons.sort(key=lambda r: (r['id'] in hosted, r['start'], r['end'], r['id']))
    result = []
    for row in lessons:
        retained = [t for t in row['teacherIds'] if t != body.teacherId]
        others, busy = [r for r in rows if r['status'] != 'cancelled' and r['id'] != row['id']], {}
        for t in state.teachers:
            if t.id == body.teacherId or t.id in retained: continue
            clash = next((r for r in others if t.id in r['teacherIds'] and r['start'] < row['end'] and row['start'] < r['end']), None)
            gone = next((a for a in state.absences if a.teacherId == t.id and a.start <= body.day <= a.end and a.startTime < row['end'] and row['start'] < a.endTime), None)
            if clash: busy[t.id] = f'Занят: {clash["group"]} · {clash["subject"]} {clash["start"]}–{clash["end"]}'
            elif gone: busy[t.id] = f'Отсутствует: {gone.name}'
        free = [t for t in state.teachers if t.id not in busy and t.id != body.teacherId and t.id not in retained]
        ranked = rank(state, ctx, rows, row, retained, None, teachers=free, exclude=row['changeId'])
        # The full day check is costly: run it in point order on a few candidates only.
        check, tried, found = staffing_check(state, ctx, body.day, rows, row, retained), 0, 0
        for c in ranked:
            c['checked'] = False
            if tried >= CHECKED or found >= ENOUGH: continue
            tried += 1
            c.update(reasons=check(c['id']), checked=True)
            c['available'] = not c['reasons']
            found += c['available']
        follow = next((r for r in lessons if r['id'] == row.get('mergeWith')), None) if row['id'] in hosted else None
        # With a recorded absence the teacher still teaches the lessons it does not touch.
        outside = bool(absences) and not follow and not any(a.startTime < row['end'] and row['start'] < a.endTime for a in absences)
        proposal = None if outside else next((c for c in ranked if c['checked'] and c['available']), None)
        ranked.sort(key=lambda c: c['checked'] and not c['available'])
        actions, note = lesson_scope(state, row)
        result.append(dict(row=dict(id=row['id'], start=row['start'], end=row['end'], group=row['group'], subject=row['subject'], rooms=row['rooms'], teacherIds=row['teacherIds'],
                                    changeId=row['changeId'], changeType=row['changeType'], editable='cancel' in actions, actions=actions, note=note),
                           proposal=dict(action='merge', mergeWith=follow['id']) if follow else
                                    dict(action='substitution', teacherId=proposal['id'], name=proposal['name'], score=proposal['score']) if proposal else
                                    dict(action='none', reason='Урок вне времени отсутствия (' + '; '.join(f'{a.startTime}–{a.endTime}' for a in absences) + ') — учитель его проводит. Выберите действие, если замена всё же нужна.') if outside else
                                    dict(action='none', reason='Свободных учителей не найдено — выберите объединение, самостоятельную работу или отмену.'),
                           candidates=ranked[:10], busyCount=len(busy), mergeOptions=merge_options(ctx, original, row)))
        # Later lessons must see the proposed substitute as busy and already in school.
        if follow or proposal:
            row['teacherIds'] = follow['teacherIds'] if follow else retained + [proposal['id']]
            row['teachers'] = ', '.join(people[t].name for t in row['teacherIds'])
    result.sort(key=lambda x: (x['row']['start'], x['row']['end'], x['row']['id']))
    return dict(revision=state.revision, day=body.day.isoformat(), teacherId=body.teacherId, lessons=result,
                absences=[dict(id=a.id, name=a.name, start=a.start.isoformat(), end=a.end.isoformat(), startTime=a.startTime, endTime=a.endTime) for a in absences])


def plan_candidate(state, body):
    """Apply every chosen action to a copy of the school state; the editor previews and stores it as one change."""
    people = {t.id for t in state.teachers}
    if body.teacherId not in people: raise ValueError('Учитель не найден.')
    if not state.settings.yearStart <= body.day <= state.settings.yearEnd: raise ValueError('Дата вне учебного года.')
    if len({a.rowId for a in body.assignments}) != len(body.assignments): raise ValueError('Урок указан дважды.')
    chosen = [a for a in body.assignments if a.action != 'none']
    if not chosen: raise ValueError('Выберите хотя бы одно действие.')
    ctx = DayContext(state)
    rows = {r['id']: r for r in resolve_day(state, body.day, ctx)[0]}
    changes = {c.id: c for c in state.datedLessons}
    merging = {a.rowId for a in chosen if a.action == 'merge'}
    done, pending, replaced = {}, [], set()
    for a in chosen:
        row = rows.get(a.rowId)
        if (row is None or row['status'] == 'cancelled' or row['activity'] != 'lesson' or body.teacherId not in row['teacherIds'] or
                ctx.maps['groups'][row['groupId']].kind == 'staff'):
            label = f'{row["group"]} · {row["subject"]} {row["start"]}: ' if row else ''
            raise ValueError(label + 'урок больше не ведёт выбранный учитель. Обновите план.')
        retained = [t for t in row['teacherIds'] if t != body.teacherId]
        existing = changes.get(row['changeId']) if row['changeId'] else None
        extra, moved = bool(existing and existing.kind == 'add'), bool(existing and existing.kind == 'change' and existing.onDate != existing.sourceDate)
        if a.action == 'cancel' and (extra or moved): raise ValueError('Перенесённый или разовый урок отменяется в форме урока.')
        if extra and a.action != 'substitution': raise ValueError('Для разового урока доступна только замена учителя.')
        if moved and a.action != 'substitution': raise ValueError('Для перенесённого урока доступна только замена учителя.')
        if a.action in ['substitution', 'self_study'] and (a.teacherId not in people or a.teacherId == body.teacherId or a.action == 'substitution' and a.teacherId in retained):
            raise ValueError('Выберите учителя.')
        if existing: replaced.add(existing.id)
        if a.action == 'merge': pending.append((a, row, existing)); continue
        if a.action == 'substitution':
            updates = dict(teacherIds=retained + [a.teacherId], changeType='move' if moved else 'extra' if extra else 'substitution', mergeWith=None)
        elif a.action == 'self_study':
            updates = dict(teacherIds=[a.teacherId], changeType='self_study', mergeWith=None)
        else:
            updates = dict(kind='cancel', changeType='cancel', mergeWith=None, teacherIds=None, roomIds=None, subjectId=None, profileId=None, startTime=None, endTime=None)
        done[a.rowId] = (row, existing, updates)
    # A group joins the host as the plan leaves it: with the host's new teacher, if the plan replaces it.
    for a, row, existing in pending:
        host, assigned = rows.get(a.mergeWith), done.get(a.mergeWith)
        if (host is None or host['id'] == row['id'] or host['status'] == 'cancelled' or host['activity'] != 'lesson' or a.mergeWith in merging or
                host.get('mergeWith') and not assigned or assigned and assigned[2].get('kind') == 'cancel' or not (host['start'] < row['end'] and row['start'] < host['end'])):
            raise ValueError('Урок для объединения не найден.')
        done[a.rowId] = (row, existing, dict(teacherIds=assigned[2]['teacherIds'] if assigned else host['teacherIds'], roomIds=host['roomIds'], changeType='merge', mergeWith=host['id']))
    records = []
    for a in chosen:
        row, existing, updates = done[a.rowId]
        if existing:
            records.append(DatedLesson.model_validate(existing.model_dump() | updates))
        else:
            base = dict(id='sub-' + uuid4().hex, name=body.name, kind='change', placementId=row['placementId'], sourceDate=date.fromisoformat(row['sourceDate']), onDate=body.day)
            records.append(DatedLesson.model_validate(base | updates))
    candidate = state.model_copy(deep=True)
    candidate.datedLessons = [c for c in candidate.datedLessons if c.id not in replaced] + records
    return State.model_validate(candidate.model_dump())


def monthly_totals(state, month, teacher_id=None):
    """Per teacher: lessons by timetable, held, taken over by type, missed, cancelled, moved."""
    if not re.fullmatch(r'\d{4}-(0[1-9]|1[0-2])', month): raise ValueError('Укажите месяц в формате ГГГГ-ММ.')
    first, last = month_bounds(date(int(month[:4]), int(month[5:]), 1))
    first, last = max(first, state.settings.yearStart), min(last, state.settings.yearEnd)
    if first > last: raise ValueError('Месяц вне учебного года.')
    people = {t.id: t for t in state.teachers}
    if teacher_id is not None and teacher_id not in people: raise ValueError('Учитель не найден.')
    fields = ['planned', 'held', 'own', 'substitutions', 'extra', 'missed', 'missedAbsent', 'cancelled', 'moved']
    kinds = ['substitution', 'merge', 'self_study', 'move']
    totals = {t: {**dict.fromkeys(fields, 0), 'byType': dict.fromkeys(kinds, 0)} for t in people}
    ctx = DayContext(state)
    for day in dates(first, last):
        for r in resolve_day(state, day, ctx)[0]:
            if r['activity'] != 'lesson' or ctx.maps['groups'][r['groupId']].kind == 'staff': continue
            active = r['status'] != 'cancelled'
            # A calendar closure is neither planned nor missed.
            closed = not active and (not r['changeId'] or bool(r['excludedClassIds']) and not r['classIds'])
            if r['placementId'] and r['sourceDate'] == r['date'] and not closed:
                for t in r['originalTeacherIds']:
                    total = totals[t]; total['planned'] += 1
                    if active and t in r['teacherIds']: continue
                    if r.get('movedTo'): total['moved'] += 1; continue
                    total['missed'] += 1
                    if not active and r['changeId']: total['cancelled'] += 1
                    if any(a.teacherId == t and a.start <= day <= a.end and a.startTime < r['end'] and r['start'] < a.endTime for a in state.absences): total['missedAbsent'] += 1
            if not active: continue
            for t in r['teacherIds']:
                total = totals[t]
                if not r['placementId']: total['extra'] += 1
                elif t in r['originalTeacherIds']: total['own'] += 1
                else:
                    total['substitutions'] += 1
                    kind = r['changeType'] or 'substitution'
                    total['byType'][kind] = total['byType'].get(kind, 0) + 1
    for total in totals.values(): total['held'] = total['own'] + total['substitutions'] + total['extra']
    chosen = [t for t in people if t == teacher_id] if teacher_id else [t for t in people if any(v for k, v in totals[t].items() if k != 'byType')]
    teachers = sorted((dict(teacherId=t, name=people[t].name, **totals[t]) for t in chosen), key=lambda x: (x['name'], x['teacherId']))
    summary = {**dict.fromkeys(fields, 0), 'byType': dict.fromkeys(kinds, 0)}
    for item in teachers:
        for k in fields: summary[k] += item[k]
        for k, v in item['byType'].items(): summary['byType'][k] = summary['byType'].get(k, 0) + v
    return dict(revision=state.revision, month=month, first=first.isoformat(), last=last.isoformat(), teachers=teachers, totals=summary)


class WeightsRequest(Model):
    revision: int
    weights: SubstitutionWeights


def register(app, Workspace, read, require_revision, create_preview, save):
    @app.put('/api/calendar/substitution-weights')
    def weights(body: WeightsRequest, workspace: Workspace = 'school'):
        state = require_revision(workspace, body.revision)
        candidate = state.model_copy(update={'settings': state.settings.model_copy(update={'substitutionWeights': body.weights})})
        return save(workspace, candidate)

    @app.post('/api/calendar/day-plan')
    def plan(body: DayPlanRequest, workspace: Workspace = 'school'):
        state = require_revision(workspace, body.revision)
        try: return day_plan(state, body)
        except ValueError as exc: raise HTTPException(422, str(exc))

    @app.post('/api/calendar/day-plan/preview')
    def plan_preview(body: DayPlanPreview, workspace: Workspace = 'school'):
        state = require_revision(workspace, body.revision)
        try: candidate = plan_candidate(state, body)
        except ValueError as exc: raise HTTPException(422, plain(exc))
        return create_preview(workspace, state, candidate)

    @app.get('/api/calendar/substitution-report')
    def report(month: str, teacherId: str | None = None, workspace: Workspace = 'school'):
        try: return monthly_totals(read(workspace), month, teacherId)
        except ValueError as exc: raise HTTPException(422, str(exc))
