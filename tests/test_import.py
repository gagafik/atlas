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
