"""Apply a teacher-only aSc export revision without reimporting group rosters.

Reads both source files and matches their original cells. Other source changes,
ambiguous teacher splits and unrelated local teacher reassignments stop the
update. Saving uses the API revision check and its ordinary undo history.
"""
import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import sys
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from backend.models import State, Teacher
from backend.scheduling import validate_schedule
from tools.inspect_source import read_source, expand
from tools.import_timetable import ident


def source_records(path):
    grid, cells = expand(read_source(path))
    records = {}
    for cell in cells:
        if cell['row'] <= 1 or cell['col'] <= 0 or not cell['text']:
            continue
        parts = [v.strip() for v in cell['text'].split('\n')]
        if len(parts) != 5:
            raise ValueError('Неизвестный формат ячейки расписания')
        subject, teacher, room, group, clock = parts
        ref = f'R{cell["row"] + 1}C{cell["col"] + 1}'
        day, period = divmod(cell['col'] - 1, 11)
        records[ref] = dict(className=grid[cell['row'], 0]['text'], subject=subject,
                            teacher=teacher, room=room, group=group, clock=clock,
                            day=day, period=period)
    return records


def update_teacher_records(state, previous, incoming, filename):
    if set(previous) != set(incoming):
        raise ValueError('Изменился набор ячеек; нужен полный разбор изменений расписания')
    changed = []
    for ref, before in previous.items():
        after = incoming[ref]
        if {k: v for k, v in before.items() if k != 'teacher'} != {k: v for k, v in after.items() if k != 'teacher'}:
            raise ValueError(f'{ref}: изменились не только учителя; расписание не перезаписано')
        if before['teacher'] != after['teacher']:
            changed.append(ref)
    if not changed:
        return state.model_copy(deep=True), dict(changedCells=0, renamed=[], split=[], affectedWorkloads=0)

    by_load = defaultdict(list)
    cell_counts = Counter()
    for p in state.placements:
        by_load[p.workloadId].append(p)
        cell_counts.update(p.sourceCells)
    if any(cell_counts[ref] != 1 for ref in changed):
        raise ValueError('Изменённая ячейка отсутствует или повторяется в рабочей сетке')
    current_teachers = {t.id: t for t in state.teachers}
    by_name = defaultdict(list)
    for t in state.teachers:
        by_name[t.name].append(t)
    updates, label_map = {}, defaultdict(set)
    changed_set = set(changed)
    for w in state.workloads:
        placements = by_load[w.id]
        refs = [c for p in placements for c in p.sourceCells]
        if not changed_set.intersection(refs):
            continue
        if any(not p.sourceCells for p in placements) or any(c not in incoming for c in refs):
            raise ValueError('У изменяемой нагрузки есть занятия без исходных ячеек; нужна сверка')
        before = {previous[c]['teacher'] for c in refs}
        after = {incoming[c]['teacher'] for c in refs}
        if len(before) != 1 or len(after) != 1:
            raise ValueError('Нагрузка содержит нескольких учителей; требуется разделение по занятиям')
        old_name, new_name = next(iter(before)), next(iter(after))
        if not new_name:
            raise ValueError('Обновление имён не может очистить назначенного учителя')
        current = current_teachers.get(w.teacherId)
        if current is None and old_name:
            raise ValueError('В приложении уже снято назначение учителя; нужна сверка')
        if current and current.id != ident('t', old_name) and current.name not in (old_name, new_name):
            raise ValueError('Учитель нагрузки уже изменён в приложении; нужна сверка с файлом')
        updates[w.id] = new_name
        label_map[old_name].add(new_name)

    targets = defaultdict(set)
    for w in state.workloads:
        if w.id in updates:
            targets[w.teacherId].add(updates[w.id])
    name_origins = defaultdict(set)
    for teacher_id, names in targets.items():
        for name in names:
            name_origins[name].add(teacher_id)
    if any(len(ids) > 1 for ids in name_origins.values()):
        raise ValueError('Несколько прежних учителей получили одно имя; подтвердите объединение отдельно')
    untouched = {w.teacherId for w in state.workloads if w.id not in updates}
    candidate = state.model_copy(deep=True)
    teachers = {t.id: t for t in candidate.teachers}
    resolved = {}
    for teacher_id, names in targets.items():
        old = current_teachers.get(teacher_id)
        split = len(names) > 1 or teacher_id in untouched and old and old.name not in names
        if split and old and old.unavailable:
            raise ValueError(f'{old.name}: при разделении нужно уточнить, кому относятся ограничения доступности')
        for name in sorted(names):
            existing = by_name.get(name, [])
            if len(existing) > 1:
                raise ValueError(f'Несколько записей учителя {name}; автоматическое объединение запрещено')
            if existing:
                target = teachers[existing[0].id]
                if teacher_id and target.id != teacher_id:
                    raise ValueError(f'{name}: уже существует отдельный учитель; сначала подтвердите объединение')
            elif old and len(names) == 1 and teacher_id not in untouched:
                target = teachers[teacher_id]
                target.name = name
            else:
                new_id = ident('t', name)
                if new_id in teachers:
                    raise ValueError('Идентификатор нового учителя уже занят')
                target = Teacher(id=new_id, name=name)
                teachers[new_id] = target
            resolved[teacher_id, name] = target.id
    for w in candidate.workloads:
        if w.id in updates:
            w.teacherId = resolved[w.teacherId, updates[w.id]]
    used = {w.teacherId for w in candidate.workloads}
    candidate.teachers = [t for tid, t in teachers.items() if tid in used or tid not in targets]
    candidate.importInfo.filename = filename
    candidate.importInfo.notes = [n for n in candidate.importInfo.notes if not n.startswith('Обновление учителей:')]
    candidate.importInfo.notes.append(f'Обновление учителей: {filename}, изменены подписи в {len(changed)} исходных ячейках. Назначения обновлены по исходным ячейкам; составы групп, позиции уроков, кабинеты и ограничения нагрузки сохранены.')
    candidate = State.model_validate(candidate.model_dump())
    report = dict(changedCells=len(changed), affectedWorkloads=len(updates),
                  teachersBefore=len(state.teachers), teachersAfter=len(candidate.teachers),
                  renamed=[dict(before=k, after=sorted(v)) for k, v in sorted(label_map.items())], split=[])
    # Include unchanged source labels when describing a split.
    all_names = defaultdict(set)
    for ref, row in previous.items():
        all_names[row['teacher']].add(incoming[ref]['teacher'])
    report['split'] = [dict(before=k, after=sorted(v)) for k, v in sorted(all_names.items()) if len(v) > 1]
    assert candidate.placements == state.placements
    assert candidate.groups == state.groups
    assert candidate.students == state.students
    assert [w.model_dump(exclude={'teacherId'}) for w in candidate.workloads] == [w.model_dump(exclude={'teacherId'}) for w in state.workloads]
    return candidate, report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--previous', required=True)
    parser.add_argument('--source', required=True)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--output-dir', default='data/teacher-update')
    args = parser.parse_args()
    endpoint = 'http://127.0.0.1:8000/api/state?workspace=school'
    original = State.model_validate_json(urlopen(endpoint).read())
    candidate, report = update_teacher_records(original, source_records(args.previous), source_records(args.source), Path(args.source).name)
    folder = Path(args.output_dir)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f'before-revision-{original.revision}.json').write_text(original.model_dump_json(indent=2), encoding='utf8')
    (folder / 'candidate.json').write_text(candidate.model_dump_json(indent=2), encoding='utf8')
    report['beforeCheck'] = validate_schedule(original)
    report['afterCheck'] = validate_schedule(candidate)
    report['sourceSha256'] = hashlib.sha256(Path(args.source).read_bytes()).hexdigest()
    if args.apply and candidate != original:
        request = Request(endpoint, data=candidate.model_dump_json().encode('utf8'), method='PUT', headers={'Content-Type': 'application/json'})
        saved = json.loads(urlopen(request).read())
        report['savedRevision'] = saved['revision']
    (folder / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf8')
    print(json.dumps({k: v for k, v in report.items() if k not in ['renamed', 'beforeCheck', 'afterCheck']}, ensure_ascii=False))
    print('Issues:', report['beforeCheck']['issueCount'], '->', report['afterCheck']['issueCount'])
