from pathlib import Path
from tools.import_timetable import import_file
from tools.inspect_source import TableReader, expand


def test_rowspan_and_colspan_preserve_coordinates():
    reader = TableReader()
    reader.feed('<table><tr><td rowspan="2">1A</td><td colspan="2">Math</td></tr><tr><td>Physics</td><td>English</td></tr></table>')
    grid, cells = expand(reader.rows)
    assert grid[1, 0]['text'] == '1A'
    assert grid[1, 1]['text'] == 'Physics'
    assert grid[0, 2]['text'] == 'Math'
    assert len(cells) == 4


def test_import_groups_deduplicates_without_losing_source_cells(tmp_path):
    rows = ['<tr><td></td></tr>', '<tr><td>Класс</td></tr>']
    for name in ['10A', '10D']:
        rows.append(f'<tr><td>{name}</td><td></td><td>Physics<br>Teacher<br>301<br>Physics 1<br>8:20 - 9:00</td></tr>')
    source = tmp_path / 'example.xls'
    source.write_bytes(('MIME-Version: 1.0\nContent-Type: text/html; charset=utf-8\nContent-Transfer-Encoding: 8bit\n\n<table>'+''.join(rows)+'</table>').encode('utf-8'))
    s = import_file(source)
    assert s.importInfo.sourceCells == 2
    assert len(s.placements) == 1
    assert len(s.placements[0].sourceCells) == 2
    assert s.placements[0].classIds == ['c-10A', 'c-10D']
    assert len(s.teachers) == 1
    assert s.groups[-1].studentIds == []


def summary(tmp_path, lessons):
    """Import a Classes Summary where `lessons` maps a class to its two first periods ('' is a free period)."""
    rows = ['<tr><td></td></tr>', '<tr><td>Класс</td></tr>']
    rows += [f'<tr><td>{name}</td><td></td><td>{first}</td><td>{second}</td></tr>' for name, (first, second) in lessons.items()]
    head = ['MIME-Version: 1.0', 'Content-Type: text/html; charset=utf-8', 'Content-Transfer-Encoding: 8bit', '', '<table>']
    source = tmp_path / 'example.xls'
    source.write_bytes(('\n'.join(head) + ''.join(rows) + '</table>').encode('utf-8'))
    return import_file(source)


def test_same_named_groups_of_classes_that_never_meet_stay_apart(tmp_path):
    cell = 'Business<br>Teacher<br>301<br>Business 2<br>{}'
    first, second = cell.format('8:20 - 9:00'), cell.format('9:10 - 9:50')
    s = summary(tmp_path, {'8A': (first, ''), '8D': (first, ''), '10A': ('', second), '10D': ('', second)})
    named = {g.id: g for g in s.groups if g.name == 'Business 2'}
    assert sorted(g.classIds for g in named.values()) == [['c-10A', 'c-10D'], ['c-8A', 'c-8D']]
    loads = {w.id: w for w in s.workloads if w.groupId in named}
    assert sorted(w.groupId for w in loads.values()) == sorted(named)
    assert [w.hours for w in loads.values()] == [1, 1]
    # Every lesson belongs to the group of exactly the classes that sit in it.
    assert all(lesson.classIds == named[loads[lesson.workloadId].groupId].classIds for lesson in s.placements)


def test_a_group_whose_classes_meet_in_turns_is_one_group(tmp_path):
    cell = 'IELTS<br>Teacher<br>301<br>IELTS 1<br>{}'
    first, second = cell.format('8:20 - 9:00'), cell.format('9:10 - 9:50')
    s = summary(tmp_path, {'10A': (first, ''), '10D': (first, second), '11A': ('', second)})
    assert [sorted(g.classIds) for g in s.groups if g.name == 'IELTS 1'] == [['c-10A', 'c-10D', 'c-11A']]


def test_the_first_set_of_classes_keeps_the_group_id_of_a_single_group(tmp_path):
    cell = 'Business<br>Teacher<br>301<br>Business 2<br>{}'
    first, second = cell.format('8:20 - 9:00'), cell.format('9:10 - 9:50')
    alone = summary(tmp_path, {'8A': (first, ''), '8D': (first, '')})
    both = summary(tmp_path, {'8A': (first, ''), '8D': (first, ''), '10A': ('', second), '10D': ('', second)})
    single = next(g for g in alone.groups if g.name == 'Business 2')
    assert next(g for g in both.groups if g.name == 'Business 2' and g.classIds == single.classIds).id == single.id

