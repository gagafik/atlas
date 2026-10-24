"""aSc Timetables 2012 XML -> Atlas State for a separate temporary database.

Pure: no server, seed or school data is touched. aSc identifiers are kept as
`asc-<kind>-<id>`, so repeated imports give identical records. By default
(rosters='synthetic') division groups receive anonymous synthetic pupils that only
belong in a temporary database; exports with pupils or group rosters are refused.
The report holds counts only: names, contacts and group titles stay in the State.
"""
from collections import Counter, defaultdict
from hashlib import sha256
from itertools import combinations, product
from math import ceil
from random import Random
import re
from xml.etree import ElementTree as ET

from pydantic import ValidationError
from .models import State
from .tabular import xml_root


MAX_BYTES = 20_000_000
MAX_RECORDS = 200_000
SECTIONS = ('periods', 'breaks', 'daysdefs', 'weeksdefs', 'termsdefs', 'subjects', 'teachers', 'buildings', 'classrooms', 'grades',
            'classes', 'groups', 'students', 'studentsubjects', 'lessons', 'cards', 'classroomsupervisions')
KEYED = ('daysdefs', 'weeksdefs', 'termsdefs', 'subjects', 'teachers', 'buildings', 'classrooms', 'classes', 'groups', 'lessons')
DAY_NAMES = ['Понедельник', 'Вторник', 'Среда', 'Четверг', 'Пятница', 'Суббота', 'Воскресенье']
TIME = re.compile(r'([01]\d|2[0-3]):[0-5]\d')
SYNTHETIC = 'asc-xml-synthetic'
AscData = dict[str, list[dict[str, str]]]


def key(value): return (value or '').strip().lower()
def aid(kind, value): return f'asc-{kind}-{key(value)}'
def ids(value): return list(dict.fromkeys(v.strip() for v in (value or '').split(',') if v.strip()))
def refs(kind, value): return list(dict.fromkeys(aid(kind, v) for v in ids(value)))
def clock(value): return (value or '').strip().zfill(5)
def minutes(value): return int(value[:2]) * 60 + int(value[3:])
def entire(group): return group.get('entireclass', '').lower() in ('1', 'true')
def division(group): return group.get('divisiontag') or 'g:' + key(group.get('id'))
def day_options(daysdef): return [o.strip() for o in daysdef.get('days', '').split(',') if o.strip()]
def division_order(tag): return (not tag.isdigit(), int(tag) if tag.isdigit() else 0, tag)


def label(*values, fallback):
    return next((v.strip()[:160].strip() for v in values if v and v.strip()), fallback)


def capacity(value):
    value = (value or '').strip()
    return min(5000, int(value)) if value.isdigit() and int(value) > 0 else None


def bell_numbers(periods):
    """aSc period numbers in time order; schools without a zero lesson start at 1."""
    return sorted(int(p['period']) for p in periods)


def span(values):
    """[1, 2, 3, 5] -> '1-3,5'; an empty list means every period."""
    parts = []
    for v in sorted(values):
        if parts and v == parts[-1][1] + 1: parts[-1][1] = v
        else: parts.append([v, v])
    return ','.join(str(a) if a == b else f'{a}-{b}' for a, b in parts) or 'all'


def parse_asc(raw: bytes) -> AscData:
    if not raw or len(raw) > MAX_BYTES: raise ValueError('Файл aSc пустой или превышает 20 МБ.')
    try: root = xml_root(raw)
    except UnicodeError as exc: raise ValueError('XML aSc должен быть в кодировке UTF-8.') from exc
    except ET.ParseError as exc: raise ValueError(f'Не удалось прочитать XML aSc: {exc}') from exc
    if root.tag != 'timetable' or not root.get('displayname', '').startswith('aSc Timetables'):
        raise ValueError('Это не XML-экспорт aSc Timetables 2012: нужен корень timetable с displayname «aSc Timetables …».')
    data, count = {name: [] for name in SECTIONS}, 0
    for section in root:
        for item in section:
            count += 1
            if count > MAX_RECORDS: raise ValueError('В XML aSc больше 200 000 записей.')
            data.setdefault(section.tag, []).append({k: (v or '').strip() for k, v in item.attrib.items()})
    return data


def check_asc(data: AscData) -> list[str]:
    """Fatal errors only. Messages name aSc ids and values, never people or groups."""
    errors, tables = [], {}
    for section in KEYED:
        rows = data.get(section, [])
        keys = [key(r.get('id')) for r in rows]
        if any(not k or len(k) > 80 for k in keys): errors.append(f'Раздел {section}: пустой идентификатор или длиннее 80 символов.')
        repeated = sorted(k for k, n in Counter(keys).items() if k and n > 1)
        if repeated: errors.append(f'Раздел {section}: повторяются идентификаторы {", ".join(repeated[:5])}.')
        tables[section] = dict(zip(keys, rows))
    periods = data.get('periods', [])
    numbers = [p.get('period', '') for p in periods]
    if not 1 <= len(periods) <= 12 or not all(v.isdigit() and int(v) <= 99 for v in numbers) or len({int(v) for v in numbers if v.isdigit()}) != len(numbers):
        errors.append(f'Звонки: нужно от 1 до 12 уроков с разными целыми номерами, в файле {len(periods)}.'); numbers = set()
    else:
        numbers = set(bell_numbers(periods))
        times = [(clock(p.get('starttime')), clock(p.get('endtime'))) for p in sorted(periods, key=lambda p: int(p['period']))]
        if any(not TIME.fullmatch(a) or not TIME.fullmatch(b) or b <= a for a, b in times) or any(b[0] < a[1] for a, b in zip(times, times[1:])):
            errors.append('Звонки: время уроков нужно в формате Ч:ММ, по порядку и без пересечений.')
    options = [day_options(d) for d in data.get('daysdefs', [])]
    lengths = {len(o) for values in options for o in values}
    days = next(iter(lengths)) if len(lengths) == 1 else 0
    if not options or not all(options) or not 1 <= days <= 7 or any(set(o) - {'0', '1'} or '1' not in o for values in options for o in values):
        errors.append('Дни: daysdefs должны содержать строки из 0 и 1 одной длины (от 1 до 7 дней).'); days = 0
    for section, attr in [('weeksdefs', 'weeks'), ('termsdefs', 'terms')]:
        for d in data.get(section, []):
            if d.get(attr) != '1':
                errors.append(f'{section} {d.get("id", "")}: поддерживается одна неделя и один учебный период (значение «1»), найдено «{d.get(attr, "")[:20]}».')
    if data.get('students') or data.get('studentsubjects') or any(g.get('studentids') for g in data.get('groups', [])):
        errors.append('В файле есть ученики или составы групп aSc: их импорт пока не поддерживается.')
    for r in data.get('classrooms', []):
        if tables['buildings'] and r.get('buildingid') and key(r['buildingid']) not in tables['buildings']:
            errors.append(f'Кабинет {r.get("id", "")}: здание {r["buildingid"]} не найдено.')
    for g in data.get('groups', []):
        if key(g.get('classid')) not in tables['classes']: errors.append(f'Группа {g.get("id", "")}: класс не найден.')
    for l in data.get('lessons', []):
        lid = l.get('id', '')
        if key(l.get('subjectid')) not in tables['subjects']: errors.append(f'Урок {lid}: предмет не найден.')
        for section, attr in [('teachers', 'teacherids'), ('classrooms', 'classroomids'), ('classes', 'classids'), ('groups', 'groupids')]:
            missing = [v for v in ids(l.get(attr)) if key(v) not in tables[section]]
            if missing: errors.append(f'Урок {lid}: в разделе {section} нет {", ".join(missing[:5])}.')
        for section, attr in [('daysdefs', 'daysdefid'), ('weeksdefs', 'weeksdefid'), ('termsdefs', 'termsdefid')]:
            if l.get(attr) and key(l[attr]) not in tables[section]: errors.append(f'Урок {lid}: {attr} не найден.')
        classes = {key(c) for c in ids(l.get('classids'))}
        covered = {key(tables['groups'][key(g)].get('classid')) for g in ids(l.get('groupids')) if key(g) in tables['groups']}
        if not classes or classes != covered: errors.append(f'Урок {lid}: группы урока должны относиться ровно к его классам.')
        try: ppw = float(l.get('periodsperweek', '').replace(',', '.'))
        except ValueError: ppw = 0.5
        if not ppw.is_integer() or not 1 <= ppw <= 100:
            errors.append(f'Урок {lid}: уроков в неделю «{l.get("periodsperweek", "")[:20]}», нужно целое число от 1 до 100.')
        ppc = l.get('periodspercard') or '1'
        if not ppc.isdigit() or not 1 <= int(ppc) <= 3: errors.append(f'Урок {lid}: карточка длиной «{ppc[:20]}» не поддерживается (1–3 урока).')
    seen = set()
    for c in data.get('cards', []):
        lid, mask, period = c.get('lessonid', ''), c.get('days', ''), c.get('period', '')
        if key(lid) not in tables['lessons']:
            errors.append(f'Карточка ссылается на отсутствующий урок {lid[:80]}.'); continue
        if days and (len(mask) != days or set(mask) - {'0', '1'} or mask.count('1') != 1):
            errors.append(f'Карточка урока {lid}: дни «{mask[:20]}» должны указывать ровно один из {days} дней.')
        if not period.isdigit() or numbers and int(period) not in numbers: errors.append(f'Карточка урока {lid}: урок «{period[:20]}» вне звонков.')
        if (c.get('weeks') or '1') != '1' or (c.get('terms') or '1') != '1':
            errors.append(f'Карточка урока {lid}: поддерживается одна неделя и один учебный период.')
        missing = [v for v in ids(c.get('classroomids')) if key(v) not in tables['classrooms']]
        if missing: errors.append(f'Карточка урока {lid}: кабинеты не найдены: {", ".join(missing[:5])}.')
        slot = key(lid), mask, int(period) if period.isdigit() else period
        if slot in seen: errors.append(f'Карточка урока {lid}: повторяется день «{mask[:20]}», урок {period[:20]}.')
        seen.add(slot)
    return errors


def synthetic_roster(divisions: list[int], seed=0) -> list[tuple[int, ...]]:
    """Rows of anonymous pupils, one level per division (a strength-2 covering array).

    Groups of one division stay disjoint; any two groups of different divisions
    share a pupil. Exact product for up to two divisions, seeded greedy otherwise.
    """
    sizes = [int(n) for n in divisions]
    if any(n < 1 for n in sizes): raise ValueError('В делении aSc должна быть хотя бы одна группа.')
    if len(sizes) <= 2: return list(product(*(range(n) for n in sizes)))
    order = sorted(range(len(sizes)), key=lambda i: (-sizes[i], i))
    s, k, rng = [sizes[i] for i in order], len(sizes), Random(seed)
    uncovered = {(i, a, j, b) for i, j in combinations(range(k), 2) for a in range(s[i]) for b in range(s[j])}
    rows = []
    while uncovered:
        pending, best = sorted(uncovered), None
        for attempt in range(24):
            i, a, j, b = pending[0] if not attempt else rng.choice(pending)
            row = [None] * k
            row[i], row[j] = a, b
            rest = [x for x in range(k) if row[x] is None]
            if attempt: rng.shuffle(rest)
            for x in rest:
                gain = lambda v: sum(((y, row[y], x, v) if y < x else (x, v, y, row[y])) in uncovered for y in range(k) if row[y] is not None)
                row[x] = max(range(s[x]), key=lambda v: (gain(v), -v))
            covered = {(p, row[p], q, row[q]) for p, q in combinations(range(k), 2)} & uncovered
            if best is None or len(covered) > len(best[1]): best = row, covered
        rows.append(best[0])
        uncovered -= best[1]
    return [tuple(row[order.index(i)] for i in range(k)) for row in rows]


def build_state(data: AscData, filename: str, *, rosters='synthetic', period_policy='observed', only_used=False, rooms='allowed') -> tuple[State, dict]:
    if rosters not in ('synthetic', 'none') or period_policy not in ('observed', 'free') or rooms not in ('allowed', 'used'):
        raise ValueError('Неизвестный режим импорта aSc.')
    errors = check_asc(data)
    if errors: raise ValueError(f'XML aSc не импортирован, ошибок: {len(errors)}. ' + ' '.join(errors[:20]))
    get = lambda section: data.get(section, [])
    lessons, cards, xml_groups = get('lessons'), get('cards'), get('groups')
    groups_by_key = {key(g['id']): g for g in xml_groups}
    placeholders = Counter()

    def named(kind, fallback, *values):
        result = label(*values, fallback=fallback)
        placeholders[kind] += result == fallback
        return result

    bells = [dict(label=str(int(p['period'])), start=clock(p['starttime']), end=clock(p['endtime'])) for p in sorted(get('periods'), key=lambda p: int(p['period']))]
    n, index = len(bells), {number: i for i, number in enumerate(bell_numbers(get('periods')))}
    slot = lambda card: index[int(card['period'])]
    long_break = min(180, max([minutes(b['start']) - minutes(a['end']) for a, b in zip(bells, bells[1:])], default=0))
    options = {key(d['id']): day_options(d) for d in get('daysdefs')}
    days = len(next(iter(options.values()))[0])
    class_names = {key(c['id']): named('classes', f'Класс aSc {key(c["id"])[:6]}', c.get('name'), c.get('short')) for c in get('classes')}
    class_rows = {key(c['id']): c for c in get('classes')}

    # Observed periods: a period is dedicated (here the zero lesson) when a complete lesson
    # keeps at least two cards there and no lesson using it leaves it. Its complete lessons
    # stay in it, other lessons may use any period up to their classes' last one but not it.
    # A class with unplaced cards has no observed day, so its lessons keep every period.
    by_lesson = defaultdict(list)
    for c in cards: by_lesson[key(c['lessonid'])].append(c)
    hours_of = lambda l: int(float(l['periodsperweek'].replace(',', '.')))
    incomplete = {key(l['id']) for l in lessons if len(by_lesson[key(l['id'])]) < hours_of(l)}
    open_classes = {key(c) for l in lessons if key(l['id']) in incomplete for c in ids(l['classids'])}
    lesson_periods = {key(l['id']): {slot(c) for c in by_lesson[key(l['id'])]} for l in lessons}
    period_lessons = defaultdict(list)
    for lk, values in lesson_periods.items():
        for p in values: period_lessons[p].append(lk)
    dedicated = {p for p, keys in period_lessons.items() if all(lesson_periods[lk] == {p} for lk in keys)
                 and any(lk not in incomplete and len(by_lesson[lk]) > 1 for lk in keys)}
    class_top = {}
    for l in lessons:
        for c in ids(l['classids']):
            if lesson_periods[key(l['id'])]: class_top[key(c)] = max(class_top.get(key(c), 0), *lesson_periods[key(l['id'])])

    def allowed_periods(l):
        lk, classes = key(l['id']), [key(c) for c in ids(l['classids'])]
        own = lesson_periods[lk]
        if period_policy == 'free': return []
        if len(own) == 1 and own <= dedicated and lk not in incomplete: return sorted(own)
        if any(c in open_classes for c in classes): return []
        values = sorted({p for p in range(max(class_top.get(c, n - 1) for c in classes) + 1) if p not in dedicated} | own)
        return [] if len(values) == n else values

    used_divisions = {}
    for l in lessons:
        rows = [groups_by_key[key(g)] for g in ids(l['groupids'])]
        if all(entire(r) for r in rows): continue
        for r in rows:
            divisions = used_divisions.setdefault(key(r['classid']), {})
            if not entire(r): divisions[division(r)] = None
    members, students, lower_bound = {}, [], 0
    if rosters == 'synthetic':
        for c in get('classes'):
            ck = key(c['id'])
            if ck not in used_divisions: continue
            levels = [[key(g['id']) for g in xml_groups if key(g['classid']) == ck and not entire(g) and division(g) == d]
                      for d in sorted(used_divisions[ck], key=division_order)]
            sizes = sorted((len(x) for x in levels), reverse=True)
            lower_bound += sizes[0] * sizes[1] if len(sizes) > 1 else sizes[0] if sizes else 1
            pupils = []
            for number, row in enumerate(synthetic_roster([len(x) for x in levels]), 1):
                sid = f'asc-syn-{ck}-{number:02d}'
                students.append(dict(id=sid, name=label(f'Синтетический ученик {number:02d} · {class_names[ck]}', fallback=sid),
                                     classId=aid('c', ck), externalId=f'synthetic:asc:{ck}:{number:02d}'))
                pupils.append(sid)
                for d, level in enumerate(row): members.setdefault(levels[d][level], []).append(sid)
            members['class:' + ck] = pupils

    groups = [dict(id=aid('gw', g['id']), name=label(f'{class_names[key(g["classid"])]} · весь класс', fallback='Весь класс'),
                   classIds=[aid('c', g['classid'])], kind='whole') for g in xml_groups if entire(g)]
    lesson_groups, extra, kinds = {}, {}, Counter()
    for l in lessons:
        keys = [key(g) for g in ids(l['groupids'])]
        rows = [groups_by_key[k] for k in keys]
        if len(rows) == 1 and entire(rows[0]):
            lesson_groups[key(l['id'])] = aid('gw', keys[0]); continue
        gid = aid('g', keys[0]) if len(rows) == 1 else 'asc-g-' + sha256(','.join(sorted(dict.fromkeys(keys))).encode()).hexdigest()[:16]
        lesson_groups[key(l['id'])] = gid
        if gid in extra: continue
        whole = all(entire(r) for r in rows)
        kinds['singleDivision' if len(rows) == 1 else 'combinedWhole' if whole else 'combined'] += 1
        titles = ' / '.join(dict.fromkeys(named('groups', f'Группа aSc {key(r["id"])[:6]}', r.get('name')) for r in rows))
        group = dict(id=gid, name=label(f'{titles} · {", ".join(dict.fromkeys(class_names[key(r["classid"])] for r in rows))}', fallback=gid),
                     classIds=list(dict.fromkeys(aid('c', r['classid']) for r in rows)), kind='whole' if whole else 'imported')
        if not whole and rosters == 'synthetic':
            pupils = dict.fromkeys(s for r in rows for s in (members['class:' + key(r['classid'])] if entire(r) else members[key(r['id'])]))
            group.update(studentIds=list(pupils), rosterStatus='complete', rosterSources=[SYNTHETIC],
                         rosterNote='Синтетический состав временной базы: пересечения повторяют деления aSc, это не настоящие ученики.')
        elif not whole:
            group.update(rosterStatus='unknown', rosterNote='Состав группы в XML aSc не указан.')
        extra[gid] = group
    groups += extra.values()

    # A class teacher who teaches no lesson is still kept: the class refers to the teacher.
    used = dict(subjects={key(l['subjectid']) for l in lessons}, teachers={key(t) for l in lessons for t in ids(l.get('teacherids'))} | {key(t) for c in get('classes') for t in ids(c.get('teacherid'))[:1]},
                classrooms={key(r) for l in lessons for r in ids(l.get('classroomids'))} | {key(r) for c in cards for r in ids(c.get('classroomids'))[:1]})
    keep = lambda section, row: not only_used or key(row['id']) in used[section]
    subjects = [dict(id=aid('s', s['id']), name=named('subjects', f'Предмет aSc {key(s["id"])[:6]}', s.get('name'), s.get('short')))
                for s in get('subjects') if keep('subjects', s)]
    teachers = [dict(id=aid('t', t['id']), name=named('teachers', f'Учитель aSc {key(t["id"])[:6]}', t.get('name'),
                                                   ' '.join(filter(None, [t.get('firstname'), t.get('lastname')])), t.get('short')))
                for t in get('teachers') if keep('teachers', t)]
    teacher_refs = {t['id'] for t in teachers}
    def leader(row):
        # The first listed teacher leads the class; an id that matches no teacher is counted, not an error.
        found = [aid('t', v) for v in ids(row.get('teacherid'))[:1]]
        return found[0] if found and found[0] in teacher_refs else None
    buildings = [dict(id=aid('b', b['id']), name=named('buildings', f'Здание aSc {key(b["id"])[:6]}', b.get('name'), b.get('short'))) for b in get('buildings')]
    room_rows = [r for r in get('classrooms') if keep('classrooms', r)]
    # Synthetic group sizes are not real ones, so capacity checks would report invented overflows.
    room_records = [dict(id=aid('r', r['id']), name=named('rooms', f'Кабинет aSc {key(r["id"])[:6]}', r.get('name'), r.get('short')),
                         capacity=None if rosters == 'synthetic' else capacity(r.get('capacity')), buildingId=aid('b', r['buildingid']) if buildings and r.get('buildingid') else None)
                    for r in room_rows]

    workloads, placements, rules, stats, booleans = [], [], [], Counter(), Counter()
    for l in lessons:
        lk, own = key(l['id']), by_lesson[key(l['id'])]
        hours, ppc = hours_of(l), int(l.get('periodspercard') or 1)
        lengths = [] if ppc == 1 else [ppc] * (hours // ppc) + ([hours % ppc] if hours % ppc else [])
        teacher_list = refs('t', l.get('teacherids'))
        card_rooms = [refs('r', c.get('classroomids'))[:1] for c in own]
        allowed = refs('r', l.get('classroomids'))
        added = [r for r in dict.fromkeys(x for rs in card_rooms for x in rs) if r not in allowed]
        room_list = allowed + added
        used_rooms = [r for r in room_list if any(r in rs for rs in card_rooms)]
        if rooms == 'used' and used_rooms: room_list = used_rooms
        per_day = Counter(c['days'].index('1') for c in own)
        allowed_days = {i for o in options.get(key(l.get('daysdefid')), ['1' * days]) for i, ch in enumerate(o) if ch == '1'}
        # Enough per day for every hour or block on the lesson's own days, even with unplaced cards.
        spread = ppc * ceil(len(lengths) / len(allowed_days)) if lengths else ceil(hours / len(allowed_days))
        w = dict(id=aid('l', lk), groupId=lesson_groups[lk], subjectId=aid('s', l['subjectid']), teacherId=teacher_list[0] if teacher_list else None,
                 additionalTeacherIds=teacher_list[1:], teacherRequired=bool(teacher_list), hours=hours,
                 maxPerDay=min(12, max([*per_day.values(), *lengths, spread])), roomIds=room_list, roomRequired=bool(room_list),
                 profileId='primary', allowedPeriods=allowed_periods(l), lessonLengths=lengths)
        # aSc has no break list here: doubles may cross every gap of the bell profile.
        if ppc > 1: w['maxLessonBreak'] = long_break
        workloads.append(w)
        class_ids = refs('c', l['classids'])
        for c, rs in zip(own, card_rooms):
            day, period = c['days'].index('1'), slot(c)
            placements.append(dict(id=f'asc-p-{lk}-{day}-{period}', workloadId=w['id'], day=day, period=period, roomId=rs[0] if rs else None,
                                   classIds=class_ids, sourceCells=[f'asc-card:{c["lessonid"]}:{c["days"]}:{int(c["period"])}'], cycleId='weekly'))
        if len(allowed_days) < days:
            rules.append(dict(id=f'asc-rule-days-{lk}', name=f'Дни урока aSc {lk[:6]}', kind='availability', scope='workload', targetIds=[w['id']],
                              mode='hard', unavailable=[dict(day=d, start='00:00', end='23:59') for d in range(days) if d not in allowed_days]))
        flags = dict(teacherless=not teacher_list, roomless=not room_list, multiTeacher=len(teacher_list) > 1, multiRoom=len(room_list) > 1,
                     multiClass=len(class_ids) > 1, doubles=bool(lengths), cardCountMismatch=len(own) != hours, cardRoomsAdded=len(added),
                     cardsWithoutRoom=sum(not rs for rs in card_rooms), multiRoomCards=sum(len(ids(c.get('classroomids'))) > 1 for c in own))
        stats.update({k: int(v) for k, v in flags.items()})
        periods_count = len(w['allowedPeriods']) or n
        booleans.update(current=days * periods_count * max(1, len(room_list)), allPeriods=days * n * max(1, len(room_list)),
                        usedRooms=days * periods_count * max(1, len(used_rooms or room_list)))

    imported_groups = [g for g in groups if g['kind'] == 'imported']
    used_groups = {key(x) for l in lessons for x in ids(l['groupids'])}
    unused_divisions = sum(not entire(g) and key(g['id']) not in used_groups for g in xml_groups)
    unknown_capacity = sum(capacity(r.get('capacity')) is None for r in room_rows)
    ignored_capacity = len(room_rows) - unknown_capacity if rosters == 'synthetic' else 0
    roster_note = (f'Синтетические составы: {len(students)} учеников в {len(used_divisions)} классах, только для временной базы; пересечения групп повторяют деления aSc.'
                   if rosters == 'synthetic' else f'Составы групп делений в XML не указаны: {len(imported_groups)} групп без состава, генерация для них недоступна.')
    notes = [f'Импорт XML aSc Timetables 2012: {len(lessons)} уроков, {len(cards)} карточек; идентификаторы aSc сохранены с префиксом asc-.', roster_note,
             f'Допустимые уроки: {"выведены из расписания aSc" if period_policy == "observed" else "без ограничений"}; выделенных периодов: {len(dedicated) if period_policy == "observed" else 0}'
             + (f'; классов с неразмещёнными карточками, уроки которых не ограничены: {len(open_classes)}.' if period_policy == 'observed' and open_classes else '.'),
             f'Неиспользуемых групп делений: {unused_divisions}. Без учителя: {stats["teacherless"]}; без кабинета: {stats["roomless"]}; вместимость неизвестна у {unknown_capacity} кабинетов.'
             + (f' Вместимость {ignored_capacity} кабинетов не перенесена: синтетические составы не дают настоящего размера групп.' if ignored_capacity else ''),
             'Не перенесены: контакты, пол, цвет и краткие имена учителей, краткие названия, параллели и домашние кабинеты классов.']
    lost_leaders = sum(bool(ids(c.get('teacherid'))) and leader(c) is None for c in get('classes'))
    if lost_leaders: notes.append(f'Классный руководитель не найден среди учителей у {lost_leaders} классов.')
    document = dict(settings=dict(name='aSc import (temp)', days=DAY_NAMES[:days]), classes=[dict(id=aid('c', k), name=v, profileId='primary', classTeacherId=leader(class_rows[k])) for k, v in class_names.items()],
                    teachers=teachers, rooms=room_records, campus=dict(buildings=buildings), subjects=subjects, students=students, groups=groups,
                    profiles=[dict(id='primary', name='Звонки aSc', periods=bells)], workloads=workloads, placements=placements, rules=rules,
                    importInfo=dict(filename=re.split(r'[\\/]', filename)[-1][:250], sourceCells=len(cards), importedLessons=len(lessons), notes=notes))
    try: state = State.model_validate(document)
    except ValidationError as exc:
        # Raised without the original: its input values could contain names.
        raise ValueError('Импорт aSc не прошёл проверку модели: ' + '; '.join(f'{".".join(map(str, e["loc"])) or "State"}: {e["msg"]}' for e in exc.errors()[:10])) from None

    hist = lambda values: {str(k): v for k, v in sorted(Counter(values).items())}
    present = lambda section, attr, empty=('',): sum(r.get(attr, '') not in empty for r in get(section))
    report = dict(
        source={s: len(get(s)) for s in SECTIONS},
        counts=dict(profiles=1, days=days, periods=n, classes=len(state.classes), classTeachers=sum(c.classTeacherId is not None for c in state.classes), teachers=len(state.teachers), rooms=len(state.rooms), subjects=len(state.subjects),
                    students=len(state.students), groups=len(state.groups), workloads=len(state.workloads), placements=len(state.placements),
                    rules=len(state.rules), hours=sum(w.hours for w in state.workloads)),
        groups=dict(whole=sum(map(entire, xml_groups)), singleDivision=kinds['singleDivision'], combined=kinds['combined'], combinedWhole=kinds['combinedWhole'],
                    unusedDivisionGroups=unused_divisions, rosterComplete=sum(g.get('rosterStatus') == 'complete' for g in imported_groups),
                    rosterUnknown=sum(g.get('rosterStatus') == 'unknown' for g in imported_groups)),
        roster=dict(mode=rosters, classes=len(used_divisions) if rosters == 'synthetic' else 0, students=len(students),
                    maxPerClass=max(Counter(s['classId'] for s in students).values(), default=0), lowerBound=lower_bound),
        workloads=dict(**{k: stats[k] for k in ['teacherless', 'roomless', 'multiTeacher', 'multiRoom', 'multiClass', 'doubles', 'cardCountMismatch']},
                       periodsPerCard=hist(l.get('periodspercard') or '1' for l in lessons), maxPerDay=hist(w.maxPerDay for w in state.workloads),
                       allowedPeriods=hist(span(w.allowedPeriods) for w in state.workloads), dayRules=len(rules), dedicatedPeriods=len(dedicated) if period_policy == 'observed' else 0,
                       openClasses=len(open_classes) if period_policy == 'observed' else 0),
        placements={k: stats[k] for k in ['cardsWithoutRoom', 'multiRoomCards', 'cardRoomsAdded']},
        dropped=dict(teacherEmail=present('teachers', 'email'), teacherMobile=present('teachers', 'mobile'), teacherGender=present('teachers', 'gender'),
                     teacherColor=present('teachers', 'color'), teacherShort=present('teachers', 'short'), subjectShort=present('subjects', 'short'),
                     roomShort=present('classrooms', 'short'), roomBuilding=0 if buildings else present('classrooms', 'buildingid'),
                     classGrade=present('classes', 'grade'), classHomeRoom=present('classes', 'classroomids'), classTeacher=lost_leaders,
                     lessonSeminarGroup=present('lessons', 'seminargroup'), lessonCapacity=present('lessons', 'capacity', ('', '*')),
                     partnerId=sum(present(s, 'partner_id') for s in SECTIONS), grades=len(get('grades')), breaks=len(get('breaks')),
                     classroomSupervisions=len(get('classroomsupervisions'))),
        placeholders={k: placeholders[k] for k in ['classes', 'teachers', 'subjects', 'rooms', 'buildings', 'groups']},
        rooms=dict(capacityUnknown=unknown_capacity, capacityIgnoredSynthetic=ignored_capacity, capacityClamped=sum((r.get('capacity') or '').isdigit() and int(r['capacity']) > 5000 for r in room_rows)),
        skipped=dict(subjects=len(get('subjects')) - len(subjects), teachers=len(get('teachers')) - len(teachers), rooms=len(get('classrooms')) - len(room_records)),
        # Lower bound: placement booleans only, without the solver's auxiliary variables.
        solverBooleansEstimate={k: booleans[k] for k in ['current', 'allPeriods', 'usedRooms']},
        options=dict(rosters=rosters, periodPolicy=period_policy, rooms=rooms, onlyUsed=only_used))
    return state, report


def import_asc(raw: bytes, filename: str, **options) -> tuple[State, dict]:
    return build_state(parse_asc(raw), filename, **options)
