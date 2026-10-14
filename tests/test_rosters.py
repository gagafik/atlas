from copy import deepcopy
from email.message import EmailMessage
import sys
from types import SimpleNamespace
import pytest
from backend.models import State, Group
from backend.seed import base_data, initial_state
from backend.scheduling import group_overlap, solve, validate_schedule
from tools.extract_rosters import read_mhtml, read_xlsx, extract
from tools.import_rosters import reconcile


def snapshot(tmp_path, count=1):
    def row(name, sid, checked=False, inactive=False):
        return f'''<tr class="{'studentIsOutTr' if inactive else ''}"><td>{name}</td><td>8A</td>
        <td data-planid="7" class="otherCourseCell {'cellSelected' if checked else ''}">
        <input data-studentid="{sid}" data-planid="7" disabled {'checked' if checked else ''}></td>
        <td><input data-studentid="{sid}" data-planid="na:1" checked></td></tr>'''
    # Fake aggregate is unchecked, including its non-attendance cell.
    aggregate = row('Все 1Присутствуют', 'fake').replace(' checked>', '>')
    html = f'''<meta charset="utf-8"><table class="studentPredmetTable">
    <tr class="header"><th><div><div title="Класс: 8A">8A</div>
    <div title="Группа: Business 1">Business 1</div><div title="Business">B</div></div>
    <div title="Teacher">Teacher</div><input data-planid="7"></th></tr>
    {aggregate}{row('Тестовый Ученик', 'a', True)}{row('Архивный Ученик', 'b', inactive=True)}
    <tr><td class="footerCell" data-planid="7">{count}</td></tr></table>'''
    message = EmailMessage()
    message['Snapshot-Content-Location'] = 'https://example.edupage.org/elearning/'
    message.set_content(html, subtype='html', charset='utf-8')
    path = tmp_path / 'groups.mhtml'
    path.write_bytes(message.as_bytes())
    return path


def test_snapshot_reads_disabled_checked_cells_and_skips_aggregate(tmp_path):
    data = read_mhtml(snapshot(tmp_path))
    assert len(data['students']) == 2
    assert data['skipped'] == 1
    assert data['courses'][0]['members'] == ['example.edupage.org:a']
    assert data['students'][0]['name'] == 'Тестовый Ученик'
    assert data['students'][1]['active'] is False


def test_snapshot_rejects_incomplete_or_inconsistent_column(tmp_path):
    with pytest.raises(ValueError, match='итог колонки'):
        read_mhtml(snapshot(tmp_path, count=2))


def test_workbook_footer_mismatch_keeps_marked_members_but_flags_partial(monkeypatch):
    rows = [('Имя', 'Класс', ''), ('', '', 'Economics 1A'), ('', '', 'Econ'),
            ('', '', 'Teacher'), ('Тестовый  Ученик', '11A', 1), ('', '#', 2)]
    sheet = SimpleNamespace(title='11', iter_rows=lambda **kwargs: iter(rows))
    class Workbook(list):
        def close(self):
            pass
    monkeypatch.setitem(sys.modules, 'openpyxl', SimpleNamespace(load_workbook=lambda *a, **kw: Workbook([sheet])))
    data = read_xlsx('unused.xlsx')
    assert len(data['courses'][0]['members']) == 1
    assert data['courses'][0]['rosterStatus'] == 'partial'
    assert data['students'][0]['name'] == 'Тестовый Ученик'


def small_school():
    data = base_data()
    data['groups'] = [dict(id='shared', name='Business 2', kind='imported', classIds=['c-8A', 'c-10A'])]
    data['teachers'] = [dict(id='t', name='Teacher')]
    data['subjects'] = [dict(id='b', name='B')]
    data['rooms'] = [dict(id='r', name='101')]
    data['workloads'] = [dict(id='w', groupId='shared', subjectId='b', teacherId='t', hours=2, maxPerDay=1, roomIds=['r'], profileId='secondary')]
    data['placements'] = [dict(id='p8', workloadId='w', day=0, period=1, classIds=['c-8A'], sourceCells=['R1C2'], roomId='r'),
                          dict(id='p10', workloadId='w', day=1, period=1, classIds=['c-10A'], sourceCells=['R2C2'], roomId='r')]
    bundle = dict(students=[], courses=[], sources=[], comparisons=[])
    for grade in [8, 10]:
        bundle['students'].append(dict(key=str(grade), name=f'Student {grade}', externalId=f'school:{grade}', className=f'{grade}A', active=True))
        bundle['courses'].append(dict(name='Business 2', subject='B', teacher='Teacher', classNames=[f'{grade}A'], members=[str(grade)],
                                      kind='elective' if grade == 8 else 'core', sources=['source']))
    return State.model_validate(data), bundle


def test_scope_split_preserves_every_lesson_and_is_idempotent():
    school, bundle = small_school()
    original = school.model_dump_json()
    imported, report = reconcile(school, bundle)
    assert school.model_dump_json() == original
    assert len(imported.groups) == 2
    assert len(imported.students) == 2
    assert len(report['splitGroups']) == 1
    assert all(w.hours == 1 for w in imported.workloads)
    for old, new in zip(school.placements, imported.placements):
        assert old.model_dump(exclude={'workloadId'}) == new.model_dump(exclude={'workloadId'})
    repeated, report = reconcile(imported, bundle)
    assert repeated == imported
    assert report['addedStudents'] == 0


def test_ambiguous_joint_lesson_cannot_be_split():
    school, bundle = small_school()
    school.placements[0].classIds.append('c-10A')
    original = school.model_dump_json()
    with pytest.raises(ValueError, match='однозначно'):
        reconcile(school, bundle)
    assert school.model_dump_json() == original


def test_existing_user_membership_is_not_silently_replaced():
    school, bundle = small_school()
    imported, _ = reconcile(school, bundle)
    changed = deepcopy(bundle)
    changed['courses'][0]['members'] = []
    with pytest.raises(ValueError, match='перезапись отменена'):
        reconcile(imported, changed)


def test_partial_group_remains_unknown_and_blocks_generation():
    school = initial_state('demo')
    school.groups[-1].rosterStatus = 'partial'
    assert validate_schedule(school)['unknownGroups'] == 1
    assert solve(school)['status'] == 'blocked'
    a = Group(id='a', name='A', kind='core', classIds=['c1', 'c2'], studentIds=['s1'], rosterStatus='partial')
    b = Group(id='b', name='B', kind='elective', classIds=['c1'], studentIds=['s2'], rosterStatus='complete')
    mapping = {'s1': 'c2', 's2': 'c1'}
    assert group_overlap(a, b, mapping) is None
    whole = Group(id='whole', name='Class', kind='whole', classIds=['c1'])
    assert group_overlap(a, whole, mapping) is None
    a.studentIds.append('s2')
    assert group_overlap(a, b, mapping) is True


def test_repeated_snapshots_do_not_double_memberships_and_core_wins(tmp_path, monkeypatch):
    path = snapshot(tmp_path)
    core_student = dict(key='xlsx:a', externalId=None, name='Тестовый  Ученик', className='8A', active=True)
    core_course = dict(name='Business 1', subject='B', teacher='Teacher', classNames=['8A'], members=['xlsx:a'],
                       sources=['core.xlsx'], sourceType='xlsx', kind='core')
    monkeypatch.setattr('tools.extract_rosters.read_xlsx', lambda p: dict(students=[core_student], courses=[deepcopy(core_course)]))
    result = extract('core.xlsx', [path, path])
    assert len(result['students']) == 2
    assert len(result['courses']) == 1
    assert result['courses'][0]['members'] == ['example.edupage.org:a']
    assert result['courses'][0]['sources'] == ['core.xlsx']
