"""Independent reconciliation of every imported cell against the supplied file."""
import json
from pathlib import Path
from collections import Counter

rows = json.loads(Path('data/source-rows.json').read_text(encoding='utf-8'))
state = json.loads(Path('data/imported-school.json').read_text(encoding='utf-8'))
from inspect_source import expand
grid, origins = expand(rows)
original = {f'R{c["row"]+1}C{c["col"]+1}': c for c in origins if c['row'] > 1 and c['col'] > 0 and c['text']}
maps = {key: {x['id']: x for x in state[key]} for key in ['workloads', 'groups', 'subjects', 'teachers', 'rooms', 'profiles', 'classes']}
seen = Counter()
for p in state['placements']:
    w = maps['workloads'][p['workloadId']]
    for source in p['sourceCells']:
        seen[source] += 1
        cell = original[source]
        subject, teacher, room, group, clock = [part.strip() for part in cell['text'].split('\n')]
        assert maps['subjects'][w['subjectId']]['name'] == subject, source
        assert (maps['teachers'][w['teacherId']]['name'] if w['teacherId'] else '') == teacher, source
        assert (maps['rooms'][p['roomId']]['name'] if p['roomId'] else '') == room, source
        if group:
            assert maps['groups'][w['groupId']]['name'] == group, source
        assert grid[cell['row'], 0]['text'] in [maps['classes'][id]['name'] for id in p['classIds']], source
        assert (p['day'], p['period']) == divmod(cell['col']-1, 11), source
        start, end = [part.strip().zfill(5) for part in clock.split('-')]
        slot = maps['profiles'][w['profileId']]['periods'][p['period']]
        assert (slot['start'], slot['end']) == (start, end), source
assert set(seen) == set(original)
assert all(v == 1 for v in seen.values())
print(f'PASS: {len(seen)} source cells reconciled by class, subject, teacher, room, group, day, period, and time.')
