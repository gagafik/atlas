"""Reconcile extracted rosters with the live local school workspace.

Dry run by default; --apply saves through the revision-checked API and its undo
history. A JSON backup is written before any save. No timetable slot is moved.
"""
import argparse
from collections import defaultdict
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from backend.models import State
from tools.extract_rosters import clean, person_key


def ident(prefix, value):
    return prefix + '-' + hashlib.sha256(value.encode('utf8')).hexdigest()[:20]


def reconcile(original, bundle, rules=None):
    state = original.model_copy(deep=True)
    data = state.model_dump(mode='json')
    classes = {c['name']: c['id'] for c in data['classes']}
    subjects = {s['id']: s['name'] for s in data['subjects']}
    report = dict(sources=bundle['sources'], addedStudents=0, mappedGroups=[], splitGroups=[], unresolved=[], comparisons=bundle['comparisons'])
    # Explicit corrections supplied by the school, kept outside source code.
    rules = rules or {}
    for merge in rules.get('groupMerges', []):
        source = next((g for g in data['groups'] if g['id'] == merge['from']), None)
        target = next((g for g in data['groups'] if g['id'] == merge['into']), None)
        if target is None:
            raise ValueError('Нет целевой группы для подтверждённого объединения')
        if source is None:
            continue  # Already applied.
        if set(source['classIds']) != set(target['classIds']) or source['studentIds'] and set(source['studentIds']) != set(target['studentIds']):
            raise ValueError('Объединение групп требует сверки классов и существующих составов')
        for w in data['workloads']:
            if w['groupId'] == source['id']:
                w['groupId'] = target['id']
        data['groups'] = [g for g in data['groups'] if g['id'] != source['id']]
    for table in ['subjects', 'teachers']:
        for change in rules.get(table, []):
            item = next((i for i in data[table] if i['id'] == change['id']), None)
            if item is None or item['name'] not in (change['before'], change['name']):
                raise ValueError('Подтверждённое переименование не соответствует текущим данным')
            item['name'] = change['name']
    subjects = {s['id']: s['name'] for s in data['subjects']}
    courses = deepcopy(bundle['courses'])
    people = {s['id']: s for s in data['students']}
    by_external = {s['externalId']: s for s in data['students'] if s.get('externalId')}
    by_name = defaultdict(list)
    for s in data['students']:
        by_name[(clean(s['name']).casefold(), s['classId'])].append(s)
    aliases = {}
    for student in bundle['students']:
        if student['className'] not in classes:
            raise ValueError(f'Неизвестный класс {student["className"]}')
        class_id = classes[student['className']]
        matches = by_name[(clean(student['name']).casefold(), class_id)]
        existing = by_external.get(student.get('externalId'))
        if existing is None and len(matches) == 1:
            existing = matches[0]
        if existing is None and matches:
            raise ValueError('Неоднозначное имя ученика в приложении')
        if existing:
            if existing['classId'] != class_id or clean(existing['name']).casefold() != clean(student['name']).casefold():
                raise ValueError('Идентификатор ученика связан с другим именем или классом')
            if existing.get('externalId') and student.get('externalId') != existing['externalId']:
                raise ValueError('Нельзя объединить учеников с разными внешними идентификаторами')
            if existing.get('active', True) != student['active']:
                raise ValueError('Статус ученика изменился; нужна явная сверка')
            if student.get('externalId'):
                existing['externalId'] = student['externalId']
        else:
            existing = dict(id=ident('student', student['key']), name=student['name'], classId=class_id,
                            externalId=student.get('externalId'), active=student['active'])
            data['students'].append(existing)
            people[existing['id']] = existing
            by_name[(clean(student['name']).casefold(), class_id)].append(existing)
            report['addedStudents'] += 1
        if existing.get('externalId'):
            by_external[existing['externalId']] = existing
        aliases[student['key']] = existing['id']
    for course in courses:
        course['classIds'] = sorted(classes[c] for c in course['classNames'])
        course['studentIds'] = sorted(aliases[s] for s in course['members'])

    def matching_courses(group):
        loads = [w for w in data['workloads'] if w['groupId'] == group['id']]
        return [c for c in courses if c['name'] == group['name']
                and c['subject'] in {subjects[w['subjectId']] for w in loads}
                and set(c['classIds']) <= set(group['classIds'])]

    # The schedule export can reuse a group name across grades. Split only when
    # explicit roster scopes partition it AND every existing placement belongs
    # wholly to one scope. Never infer a split from lesson times alone.
    for group in list(data['groups']):
        if group['kind'] == 'whole':
            continue
        matches = matching_courses(group)
        if len(matches) <= 1:
            continue
        scopes = [set(c['classIds']) for c in matches]
        if any(a & b for i, a in enumerate(scopes) for b in scopes[i + 1:]) or set.union(*scopes) != set(group['classIds']):
            raise ValueError(f'Пересекающиеся варианты группы {group["name"]}')
        if group['studentIds']:
            raise ValueError(f'У {group["name"]} уже есть состав; разделение требует сверки')
        loads = [w for w in data['workloads'] if w['groupId'] == group['id']]
        load_ids = {w['id'] for w in loads}
        placements = [p for p in data['placements'] if p['workloadId'] in load_ids]
        scope_of = {}
        for p in placements:
            eligible = [i for i, scope in enumerate(scopes) if p['classIds'] and set(p['classIds']) <= scope]
            if len(eligible) != 1:
                raise ValueError(f'{group["name"]}: занятие нельзя однозначно отнести к параллели')
            scope_of[p['id']] = eligible[0]
        for w in loads:
            if w['hours'] != sum(p['workloadId'] == w['id'] for p in placements) or w['block']:
                raise ValueError(f'{group["name"]}: сначала распределите непоставленные часы / синхронный блок')
        new_groups, new_loads = [], []
        for i, course in enumerate(matches):
            g = deepcopy(group)
            g['id'] = ident('group', group['id'] + '|' + '|'.join(course['classIds']))
            g['classIds'] = course['classIds']
            new_groups.append(g)
            for w in loads:
                selected = [p for p in placements if p['workloadId'] == w['id'] and scope_of[p['id']] == i]
                if not selected:
                    continue
                new_w = deepcopy(w)
                new_w.update(id=ident('load', w['id'] + '|' + g['id']), groupId=g['id'], hours=len(selected))
                new_loads.append(new_w)
                for p in selected:
                    p['workloadId'] = new_w['id']
        data['groups'] = [g for g in data['groups'] if g['id'] != group['id']] + new_groups
        data['workloads'] = [w for w in data['workloads'] if w['id'] not in load_ids] + new_loads
        report['splitGroups'].append(dict(name=group['name'], classScopes=[c['classNames'] for c in matches]))

    for course in courses:
        matches = [g for g in data['groups'] if g['kind'] != 'whole' and set(g['classIds']) == set(course['classIds']) and course in matching_courses(g)]
        if len(matches) != 1:
            raise ValueError(f'{course["name"]} · {course["subject"]}: найдено {len(matches)} групп; расписание не изменено')
        group = matches[0]
        if group['studentIds'] and set(group['studentIds']) != set(course['studentIds']):
            raise ValueError(f'{group["name"]}: текущий состав отличается, автоматическая перезапись отменена')
        note = course.get('note', '')
        different = [c for c in bundle['comparisons'] if c['name'] == course['name'] and not c['sameMembers']]
        if different:
            note += f' Core взят из Excel: {len(course["studentIds"])} учеников; в MHTML состав отличается.'
        note += f' Учитель в источнике состава: {course["teacher"]}. Учитель в расписании сохранён без изменений.'
        group.update(kind=course['kind'], studentIds=course['studentIds'], rosterStatus=course.get('rosterStatus', 'complete'),
                     rosterSources=course['sources'], rosterNote=note.strip())
        report['mappedGroups'].append(dict(id=group['id'], name=group['name'], kind=group['kind'], classes=course['classNames'],
                                          students=len(group['studentIds']), status=group['rosterStatus'], sources=group['rosterSources'], note=group['rosterNote']))
    for g in data['groups']:
        if g['kind'] != 'whole' and (not g['studentIds'] or g.get('rosterStatus') in ('unknown', 'partial')):
            report['unresolved'].append(dict(id=g['id'], name=g['name'], note=g.get('rosterNote', ''),
                                            subjects=sorted({subjects[w['subjectId']] for w in data['workloads'] if w['groupId'] == g['id']})))
    notes = data['importInfo']['notes']
    stale = 'Состав смешанных групп не указан в файле.'
    data['importInfo']['notes'] = [n for n in notes if not n.startswith(stale) and not n.startswith('Импорт составов:')]
    for note in rules.get('notes', []):
        if note not in data['importInfo']['notes']:
            data['importInfo']['notes'].append(note)
    data['importInfo']['notes'].append(f'Импорт составов: {len(courses)} групп из Excel и MHTML; {len(report["unresolved"])} групп требуют уточнения. Core — по Excel, electives — по отметкам MHTML. Служебные строки исключены, выбывшие отмечены отдельно.')
    result = State.model_validate(data)
    # A roster import cannot move, duplicate or drop an existing lesson.
    before = {p.id: p.model_dump(exclude={'workloadId'}) for p in original.placements}
    after = {p.id: p.model_dump(exclude={'workloadId'}) for p in result.placements}
    assert before == after
    assert sum(w.hours for w in original.workloads) == sum(w.hours for w in result.workloads)
    return result, report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('bundle')
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--output-dir', default='data/roster-import')
    parser.add_argument('--rules', help='JSON с явно подтверждёнными школой исправлениями')
    args = parser.parse_args()
    endpoint = 'http://127.0.0.1:8000/api/state?workspace=school'
    original = State.model_validate_json(urlopen(endpoint).read())
    bundle = json.loads(Path(args.bundle).read_text(encoding='utf8'))
    rules = json.loads(Path(args.rules).read_text(encoding='utf8')) if args.rules else None
    candidate, report = reconcile(original, bundle, rules)
    folder = Path(args.output_dir)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f'before-revision-{original.revision}.json').write_text(original.model_dump_json(indent=2), encoding='utf8')
    (folder / 'candidate.json').write_text(candidate.model_dump_json(indent=2), encoding='utf8')
    (folder / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf8')
    if args.apply:
        request = Request(endpoint, data=candidate.model_dump_json().encode('utf8'), method='PUT', headers={'Content-Type': 'application/json'})
        saved = json.loads(urlopen(request).read())
        report['savedRevision'] = saved['revision']
    print(json.dumps({k: v for k, v in report.items() if k not in ['mappedGroups', 'comparisons']}, ensure_ascii=False))
