"""Lossless cell import of the supplied aSc Classes Summary (MIME HTML .xls)."""
from pathlib import Path
import sys
import json
import hashlib
import re
from collections import Counter, defaultdict

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tools.inspect_source import read_source, expand
from backend.seed import base_data
from backend.models import State


def ident(prefix, value):
    return prefix + '-' + hashlib.sha256(value.encode('utf-8')).hexdigest()[:14]


def import_file(path):
    rows = read_source(path)
    grid, origins = expand(rows)
    data = base_data()
    class_map = {c['name']: c for c in data['classes']}
    records = []
    class_times = defaultdict(lambda: defaultdict(set))
    for cell in origins:
        if cell['row'] <= 1 or cell['col'] <= 0 or not cell['text']:
            continue
        name = grid[cell['row'], 0]['text']
        parts = [p.strip() for p in cell['text'].split('\n')]
        if len(parts) != 5 or name not in class_map:
            raise ValueError(f'Не распознана ячейка R{cell["row"] + 1}C{cell["col"] + 1}')
        subject, teacher, room, group, times = parts
        match = re.fullmatch(r'(\d{1,2}:\d{2})\s*-\s*(\d{1,2}:\d{2})', times)
        if not match:
            raise ValueError(f'Не распознано время: {times}')
        start, end = (v.zfill(5) for v in match.groups())
        day, period = divmod(cell['col'] - 1, 11)
        class_times[name][period].add((start, end))
        records.append(dict(className=name, subject=subject, teacher=teacher, room=room, group=group, start=start, end=end, day=day, period=period, source=f'R{cell["row"] + 1}C{cell["col"] + 1}'))
    profiles = {p['id']: p for p in data['profiles']}
    for name, slots in class_times.items():
        c = class_map[name]
        profile = json.loads(json.dumps(profiles[c['profileId']]))
        for slot, values in slots.items():
            if len(values) != 1:
                raise ValueError(f'У {name}, урок {slot}, несколько вариантов звонков: {values}')
            start, end = next(iter(values))
            profile['periods'][slot].update(start=start, end=end)
        matching = next((p for p in profiles.values() if p['periods'] == profile['periods']), None)
        if matching:
            c['profileId'] = matching['id']
        else:
            profile['id'] = ident('bells', json.dumps(profile['periods']))
            profile['name'] = f'Звонки {name}'
            profiles[profile['id']] = profile
            c['profileId'] = profile['id']
    data['profiles'] = list(profiles.values())
    colors = ['#dceae2', '#e5e1f2', '#f5e5ce', '#dce9f4', '#f3dddd', '#e6ecd5', '#ddebed', '#eee1d3']
    subjects, teachers, rooms, groups, loads, placements = {}, {}, {}, {g['id']: g for g in data['groups']}, {}, {}
    for r in records:
        c = class_map[r['className']]
        subject_id = ident('sub', r['subject'])
        if subject_id not in subjects:
            subjects[subject_id] = dict(id=subject_id, name=r['subject'], color=colors[len(subjects) % len(colors)])
        teacher_id = ident('t', r['teacher']) if r['teacher'] else None
        room_id = ident('r', r['room']) if r['room'] else None
        if teacher_id:
            teachers[teacher_id] = dict(id=teacher_id, name=r['teacher'])
        if room_id:
            rooms[room_id] = dict(id=room_id, name=r['room'])
        group_id = ident('g', '|'.join([r['group'], r['subject'], r['teacher']])) if r['group'] else f'g-{c["id"]}'
        if group_id not in groups:
            groups[group_id] = dict(id=group_id, name=r['group'], classIds=[], studentIds=[], kind='imported')
        if c['id'] not in groups[group_id]['classIds']:
            groups[group_id]['classIds'].append(c['id'])
        load_id = ident('w', '|'.join([group_id, subject_id, teacher_id or '', c['profileId']]))
        if load_id not in loads:
            loads[load_id] = dict(id=load_id, groupId=group_id, subjectId=subject_id, teacherId=teacher_id, profileId=c['profileId'], hours=0, roomIds=[], maxPerDay=1, allowedPeriods=[0] if r['period'] == 0 else list(range(1, 11)))
        load = loads[load_id]
        if room_id and room_id not in load['roomIds']:
            load['roomIds'].append(room_id)
        key = (load_id, r['day'], r['period'], room_id)
        if key not in placements:
            placements[key] = dict(id=ident('p', str(key)), workloadId=load_id, day=r['day'], period=r['period'], roomId=room_id, locked=r['period'] == 0, classIds=[], sourceCells=[])
        p = placements[key]
        if c['id'] not in p['classIds']:
            p['classIds'].append(c['id'])
        p['sourceCells'].append(r['source'])
    per_day = Counter((p['workloadId'], p['day']) for p in placements.values())
    for w in loads.values():
        w['hours'] = sum(v for (wid, day), v in per_day.items() if wid == w['id'])
        w['maxPerDay'] = max(v for (wid, day), v in per_day.items() if wid == w['id'])
    data.update(subjects=list(subjects.values()), teachers=list(teachers.values()), rooms=list(rooms.values()), groups=list(groups.values()), workloads=list(loads.values()), placements=list(placements.values()))
    data['importInfo'] = dict(filename=Path(path).name, sourceCells=len(records), importedLessons=len(placements), notes=[
        'Все заполненные ячейки сохранены. Одинаковые записи именованной группы объединены по предмету, учителю, профилю звонков, дню, уроку и кабинету.',
        'Состав смешанных групп не указан в файле. До заполнения списков учеников полная проверка их пересечений и генерация недоступны.',
        'Пустые учителя и кабинеты оставлены незаданными. Одноимённые записи учителей не разделялись на разных людей.',
        '0 kaz и 0 rus добавлены из вашего списка; их расписания нет в файле.',
        'Нулевой урок CCircle закреплён. Лимит 8 уроков для младших классов не включает нулевой урок.',
        'Календарные периоды перенесены с изображения и требуют проверки. Они пока не изменяют недельную сетку.'
    ])
    state = State.model_validate(data)
    assert sum(len(p.sourceCells) for p in state.placements) == len(records)
    return state


if __name__ == '__main__':
    state = import_file(sys.argv[1])
    output = Path('data/imported-school.json')
    output.parent.mkdir(exist_ok=True)
    output.write_text(state.model_dump_json(indent=2), encoding='utf-8')
    print(json.dumps({key: len(getattr(state, key)) for key in ['classes', 'teachers', 'rooms', 'subjects', 'groups', 'workloads', 'placements', 'profiles']}, ensure_ascii=False))
    print('Source cells:', state.importInfo.sourceCells)
