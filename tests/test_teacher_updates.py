from copy import deepcopy
import pytest
from backend.models import State
from backend.seed import base_data
from tools.import_timetable import ident
from tools.update_teachers import update_teacher_records


def fixture():
    data = base_data()
    tid = ident('t', 'Общее имя')
    data['teachers'] = [dict(id=tid, name='Общее имя')]
    data['subjects'] = [dict(id='s', name='Math')]
    data['rooms'] = [dict(id='r', name='101')]
    data['groups'] = [dict(id='g', name='Math A', classIds=['c-10A'], kind='math', studentIds=['pupil'], rosterStatus='complete', rosterSources=['core.xlsx'])]
    data['students'] = [dict(id='pupil', name='Test Student', classId='c-10A')]
    data['workloads'] = [dict(id=f'w{i}', groupId='g', subjectId='s', teacherId=tid, hours=1, profileId='secondary', roomIds=['r']) for i in range(2)]
    data['placements'] = [dict(id=f'p{i}', workloadId=f'w{i}', day=i, period=1, roomId='r', locked=True, sourceCells=[f'R3C{i+2}'], classIds=['c-10A']) for i in range(2)]
    old = {f'R3C{i+2}': dict(className='10A', subject='Math', teacher='Общее имя', room='101', group='Math A', clock='8:20-9:00', day=i, period=1) for i in range(2)}
    incoming = deepcopy(old)
    incoming['R3C2']['teacher'] = 'Первый ПолноеИмя'
    incoming['R3C3']['teacher'] = 'Второй ПолноеИмя'
    return State.model_validate(data), old, incoming


def test_splits_teacher_without_recreating_lessons_or_rosters_and_repeats_safely():
    state, old, incoming = fixture()
    original = state.model_dump_json()
    result, report = update_teacher_records(state, old, incoming, 'new.xls')
    assert len(result.teachers) == 2
    assert len({w.teacherId for w in result.workloads}) == 2
    assert result.groups == state.groups
    assert result.students == state.students
    assert result.placements == state.placements
    assert state.model_dump_json() == original
    assert report['changedCells'] == 2
    repeated, _ = update_teacher_records(result, old, incoming, 'new.xls')
    assert repeated == result


def test_one_to_one_rename_preserves_teacher_availability():
    state, old, incoming = fixture()
    state.teachers[0].unavailable = ['1:2']
    incoming['R3C3']['teacher'] = incoming['R3C2']['teacher']
    result, _ = update_teacher_records(state, old, incoming, 'new.xls')
    assert result.teachers[0].id == state.teachers[0].id
    assert result.teachers[0].unavailable == ['1:2']


def test_split_refuses_to_guess_ownership_of_availability():
    state, old, incoming = fixture()
    state.teachers[0].unavailable = ['1:2']
    with pytest.raises(ValueError, match='ограничения доступности'):
        update_teacher_records(state, old, incoming, 'new.xls')


def test_non_teacher_source_edit_is_not_applied_as_teacher_update():
    state, old, incoming = fixture()
    incoming['R3C3']['room'] = '102'
    with pytest.raises(ValueError, match='не только учителя'):
        update_teacher_records(state, old, incoming, 'new.xls')


def test_unchanged_source_label_does_not_regress_previously_clarified_name():
    state, old, incoming = fixture()
    state.teachers[0].name = 'Уточнённая Фамилия'
    incoming['R3C3']['teacher'] = old['R3C3']['teacher']
    result, _ = update_teacher_records(state, old, incoming, 'new.xls')
    unchanged_id = result.workloads[1].teacherId
    assert next(t.name for t in result.teachers if t.id == unchanged_id) == 'Уточнённая Фамилия'
    assert result.workloads[0].teacherId != unchanged_id


def test_ambiguous_teacher_inside_one_workload_requires_review():
    state, old, incoming = fixture()
    state.placements[1].workloadId = 'w0'
    with pytest.raises(ValueError, match='нескольких учителей'):
        update_teacher_records(state, old, incoming, 'new.xls')


def test_locally_reassigned_teacher_is_preserved_by_rejecting_update():
    state, old, incoming = fixture()
    state.teachers[0].id = 'local-person'
    state.teachers[0].name = 'Other Person'
    for w in state.workloads:
        w.teacherId = 'local-person'
    with pytest.raises(ValueError, match='уже изменён'):
        update_teacher_records(state, old, incoming, 'new.xls')


def test_no_implicit_merging_of_two_original_teacher_records():
    state, old, incoming = fixture()
    new_id = ident('t', 'Другой учитель')
    state.teachers.append(state.teachers[0].model_copy(update={'id':new_id, 'name':'Другой учитель'}))
    state.workloads[1].teacherId = new_id
    old['R3C3']['teacher'] = 'Другой учитель'
    incoming['R3C3']['teacher'] = incoming['R3C2']['teacher']
    with pytest.raises(ValueError, match='одно имя'):
        update_teacher_records(state, old, incoming, 'new.xls')
