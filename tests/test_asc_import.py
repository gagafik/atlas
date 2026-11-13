"""aSc Timetables 2012 XML import on an invented fixture; databases only under tmp_path."""
from contextlib import closing
from itertools import combinations
from pathlib import Path
import json
import sqlite3
import subprocess
import sys

import pytest

from backend.asc_import import check_asc, import_asc, parse_asc, synthetic_roster
from backend.models import State
from backend.scheduling import solve, validate_schedule

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / 'tests' / 'fixtures' / 'asc-sample.xml'
CLI = ROOT / 'tools' / 'import_asc_xml.py'
ALPHA, DELTA = 'asc-t-7eabcdefabcdef01', 'asc-t-7eabcdefabcdef04'
MINI = '''<?xml version="1.0" encoding="UTF-8"?>
<timetable importtype="database" displayname="aSc Timetables 2012 XML">
<periods>{periods}</periods>
<daysdefs><daysdef id="D1" days="10000,01000,00100,00010,00001"/><daysdef id="D2" days="10000"/></daysdefs>
<weeksdefs><weeksdef id="W1" weeks="{weeks}"/></weeksdefs><termsdefs><termsdef id="T1" terms="1"/></termsdefs>
<subjects><subject id="S1" name="Subject"/></subjects><teachers><teacher id="P1" name="Person"/></teachers>
<classrooms><classroom id="R1" name="Room" capacity="*"/><classroom id="R2" name="Other" capacity="*"/></classrooms><classes><class id="C1" name="Class"/></classes>
<groups><group id="G1" classid="C1" name="Whole" entireclass="1" divisiontag="0"/></groups>
<lessons><lesson id="L1" classids="C1" groupids="G1" subjectid="S1" teacherids="P1" classroomids="{rooms}" periodsperweek="{ppw}" periodspercard="{ppc}" daysdefid="{daysdef}" weeksdefid="W1" termsdefid="T1"/></lessons>
<cards><card lessonid="{lesson}" days="{days}" period="{period}" classroomids="{room}" weeks="1" terms="1"/></cards>
</timetable>'''


def imported(**options): return import_asc(FIXTURE.read_bytes(), 'asc-sample.xml', **options)
def variant(old, new, **options):
    raw = FIXTURE.read_bytes()
    assert raw.count(old.encode()) == 1
    return import_asc(raw.replace(old.encode(), new.encode()), 'variant.xml', **options)
def load(state, n): return next(w for w in state.workloads if w.id == f'asc-l-1eabcdefabcdef{n:02x}')
def moved(state, placement_id, day, period):
    return state.model_copy(update={'placements': [p.model_copy(update={'day': day, 'period': period}) if p.id == placement_id else p for p in state.placements]})


def mini(**changes):
    values = dict(periods='<period period="0" starttime="8:00" endtime="8:45"/>', weeks='1', ppw='1.0', ppc='1', daysdef='D1', lesson='L1', days='10000',
                  period='0', rooms='R1', room='R1')
    return MINI.format(**{**values, **changes}).encode('utf-8')


def cli(*args, cwd):
    return subprocess.run([sys.executable, '-I', str(CLI), *map(str, args)], cwd=cwd, capture_output=True, text=True, encoding='utf-8', timeout=120)


def test_import_is_deterministic():
    state, report = imported()
    again, again_report = import_asc(FIXTURE.read_bytes(), str(FIXTURE))
    assert state.model_dump_json() == again.model_dump_json() and report == again_report


def test_counts_on_the_fixture():
    state, report = imported()
    assert {k: len(getattr(state, k)) for k in ['classes', 'teachers', 'rooms', 'subjects', 'students', 'groups', 'workloads', 'placements', 'profiles', 'rules']} == \
        dict(classes=3, teachers=4, rooms=3, subjects=5, students=6, groups=7, workloads=10, placements=16, profiles=1, rules=0)
    assert report['counts']['hours'] == 16 and report['groups'] == dict(whole=3, singleDivision=2, combined=2, combinedWhole=0, unusedDivisionGroups=2, rosterComplete=4, rosterUnknown=0)
    assert state.importInfo.model_dump(exclude={'notes'}) == dict(filename='asc-sample.xml', sourceCells=16, importedLessons=10)
    assert {p.period for p in state.placements if p.workloadId == load(state, 2).id} == {2, 3}
    assert all(len(p.sourceCells) == 1 for p in state.placements)


def test_report_and_notes_carry_no_names_from_the_source():
    state, report = imported()
    counts_only = json.dumps(report, ensure_ascii=False) + json.dumps(state.importInfo.notes, ensure_ascii=False)
    assert not any(name in counts_only for name in ['Alpha', 'Delta', 'Stream', 'Lang', '7X', 'Room 101', 'Hall', 'Mathematics', 'Science', 'abcdef'])


def test_only_used_leaves_out_what_no_lesson_uses():
    full, _ = imported()
    used, report = imported(only_used=True)
    assert len(used.subjects) == len(full.subjects) - 1 and report['skipped'] == dict(subjects=1, teachers=0, rooms=0)


def test_contacts_are_never_copied():
    state, report = imported()
    dump = state.model_dump_json()
    # Day bitstrings such as 10000 legitimately contain 000; the phone value must not.
    assert 'example.invalid' not in dump and '"000"' not in dump and '#123456' not in dump and 'ALP' not in dump
    assert not any(field in dump for field in ['email', 'mobile', 'gender', 'firstname'])
    assert report['dropped']['teacherEmail'] == report['dropped']['teacherMobile'] == 1


def test_a_teacher_without_a_name_gets_a_unique_placeholder():
    state, report = imported()
    names = [t.name for t in state.teachers]
    named = [n for n in names if n in ('Teacher Alpha', 'Teacher Beta', 'Teacher Delta')]
    (placeholder,) = [n for n in names if n not in named]
    assert placeholder.strip() and len(set(names)) == len(names) and report['placeholders']['teachers'] == 1


def test_class_teacher_is_mapped():
    state, report = imported()
    assert {c.id: c.classTeacherId for c in state.classes} == {'asc-c-c1abcdefabcdef01': ALPHA, 'asc-c-c1abcdefabcdef02': None, 'asc-c-c1abcdefabcdef03': None}
    assert report['counts']['classTeachers'] == 1 and report['dropped']['classTeacher'] == 0
    assert imported(only_used=True)[0].classes[0].classTeacherId == ALPHA


def test_class_teacher_that_matches_no_teacher_is_counted_and_not_leaked():
    lost, lost_report = variant('teacherid="7EABCDEFABCDEF01"', 'teacherid="7EFFFFFFFFFFFF99"')
    assert lost.classes[0].classTeacherId is None and lost_report['dropped']['classTeacher'] == 1 and lost_report['counts']['classTeachers'] == 0
    assert '7EFFFF' not in json.dumps(lost_report) + json.dumps(lost.importInfo.notes)


def test_first_of_several_class_teachers_leads_the_class():
    several, _ = variant('teacherid="7EABCDEFABCDEF01"', 'teacherid="7EABCDEFABCDEF02,7EABCDEFABCDEF01"')
    assert several.classes[0].classTeacherId == 'asc-t-7eabcdefabcdef02'


def test_class_teacher_without_lessons_survives_only_used():
    raw = mini().replace(b'<teacher id="P1" name="Person"/>', b'<teacher id="P1" name="Person"/><teacher id="P2" name="Leader"/>').replace(b'<class id="C1" name="Class"/>', b'<class id="C1" name="Class" teacherid="P2"/>')
    full, _ = import_asc(raw, 'mini.xml')
    used, report = import_asc(raw, 'mini.xml', only_used=True)
    assert full.classes[0].classTeacherId == used.classes[0].classTeacherId == 'asc-t-p2'
    assert [t.id for t in used.teachers] == ['asc-t-p1', 'asc-t-p2'] and report['skipped']['teachers'] == 0
    assert import_asc(mini(), 'mini.xml')[0].classes[0].classTeacherId is None


def test_bells_are_zero_padded_in_primary_profile_and_period_zero_is_dedicated():
    state, report = imported()
    profile, = state.profiles
    assert profile.id == 'primary' and [(p.label, p.start, p.end) for p in profile.periods[:2]] == [('0', '07:00', '07:45'), ('1', '08:00', '08:40')]
    assert {c.profileId for c in state.classes} == {w.profileId for w in state.workloads} == {'primary'}
    assert state.settings.name == 'aSc import (temp)' and len(state.settings.days) == 5 and [p.id for p in state.cycles.patterns] == ['weekly']
    assert load(state, 10).allowedPeriods == [0] and load(state, 1).allowedPeriods == [1, 2, 3] and load(state, 3).allowedPeriods == [1, 2]
    assert report['workloads']['allowedPeriods'] == {'0': 1, '1-2': 2, '1-3': 7}
    assert not any(w.allowedPeriods for w in imported(period_policy='free')[0].workloads)


def test_periods_numbered_from_one_map_to_profile_indexes():
    state, _ = import_asc(mini(periods='<period period="1" starttime="8:00" endtime="8:45"/><period period="3" starttime="9:00" endtime="9:45"/>', period='3'), 'mini.xml')
    assert [p.label for p in state.profiles[0].periods] == ['1', '3']
    placement, = state.placements
    assert (placement.period, placement.sourceCells) == (1, ['asc-card:L1:10000:3'])
    assert validate_schedule(state)['issueCount'] == 0


@pytest.mark.parametrize('periods', ['<period period="1" starttime="8:00" endtime="8:45"/>',
                                     '<period period="0" starttime="8:00" endtime="8:45"/><period period="0" starttime="9:00" endtime="9:45"/>'], ids=['lesson-outside-the-bells', 'repeated-period-number'])
def test_periods_that_do_not_fit_the_cards_are_rejected(periods):
    with pytest.raises(ValueError): import_asc(mini(periods=periods), 'mini.xml')


def test_unplaced_cards_keep_the_whole_day_and_blocks_fit_the_week():
    # Lesson 03 of class 1Z asks for 9 hours but has only its 2 cards.
    state, report = variant('subjectid="5BABCDEFABCDEF02" periodspercard="1" periodsperweek="2.0"', 'subjectid="5BABCDEFABCDEF02" periodspercard="1" periodsperweek="9.0"')
    assert load(state, 3).allowedPeriods == load(state, 4).allowedPeriods == [] and load(state, 1).allowedPeriods == [1, 2, 3]
    assert load(state, 10).allowedPeriods == [0] and report['workloads']['dedicatedPeriods'] == 1
    assert report['workloads']['openClasses'] == 1 and report['workloads']['cardCountMismatch'] == 1
    assert solve(state.model_copy(update={'placements': []}), seconds=10)['status'] == 'success'
    # Six doubles in five days: one day must hold two of them.
    state, _ = variant('periodspercard="2" periodsperweek="2.0"', 'periodspercard="2" periodsperweek="12.0"', period_policy='free')
    assert load(state, 2).lessonLengths == [2] * 6 and load(state, 2).maxPerDay == 4
    assert solve(state.model_copy(update={'placements': []}), seconds=10)['status'] == 'success'


def test_day_restricted_lessons_with_unplaced_cards_fit_their_days():
    four = ''.join(f'<period period="{i}" starttime="{8 + i}:00" endtime="{8 + i}:45"/>' for i in range(4))
    for options, max_per_day in [(dict(ppw='2.0'), 2), (dict(ppw='4.0', ppc='2'), 4)]:
        state, report = import_asc(mini(periods=four, daysdef='D2', **options), 'mini.xml')
        assert state.workloads[0].maxPerDay == max_per_day and report['workloads']['dayRules'] == 1
        assert solve(state.model_copy(update={'placements': []}), seconds=10)['status'] == 'success'


def test_only_repeated_complete_lessons_make_a_period_dedicated():
    # Lesson 09 (one hour of 7Y) alone in the otherwise unused last period.
    state, report = variant('period="3" weeks="1" terms="1" days="01000"/>\n    <card lessonid="1EABCDEFABCDEF0A"',
                            'period="4" weeks="1" terms="1" days="01000"/>\n    <card lessonid="1EABCDEFABCDEF0A"')
    assert report['workloads']['dedicatedPeriods'] == 1 and load(state, 10).allowedPeriods == [0]
    assert load(state, 9).allowedPeriods == load(state, 2).allowedPeriods == [1, 2, 3, 4]


def test_double_across_long_break_keeps_its_lesson_pattern():
    state, _ = imported()
    double = load(state, 2)
    assert double.lessonLengths == [2] and double.maxLessonBreak == 55 and double.maxPerDay == 2
    assert load(state, 1).lessonLengths == [] and load(state, 1).maxPerDay == 1 and load(state, 1).maxLessonBreak == 20
    # Two single cards of one lesson on the same day: the observed day count decides.
    assert load(state, 4).lessonLengths == [] and load(state, 4).maxPerDay == 2
    assert not [i for i in validate_schedule(state)['issues'] if i['type'] == 'lesson_pattern']
    strict = state.model_copy(update={'workloads': [w.model_copy(update={'maxLessonBreak': 20}) if w.id == double.id else w for w in state.workloads]})
    assert [i['type'] for i in validate_schedule(strict)['issues']] == ['lesson_pattern']


def test_synthetic_roster_covers_every_pair_of_pupils_across_divisions():
    sizes = [6, 6, 4, 4, 2, 2]
    rows = synthetic_roster(sizes)
    assert rows == synthetic_roster(sizes) and len(rows) >= sorted(sizes)[-1] * sorted(sizes)[-2]
    assert all(any(r[i] == x and r[j] == y for r in rows) for i, j in combinations(range(6), 2) for x in range(sizes[i]) for y in range(sizes[j]))
    assert synthetic_roster([]) == [()] and synthetic_roster([3]) == [(0,), (1,), (2,)] and len(synthetic_roster([2, 3])) == 6


def test_synthetic_rosters_of_one_division_do_not_overlap_and_the_schedule_is_clean():
    state, report = imported()
    imported_groups = [g for g in state.groups if g.kind == 'imported']
    streams = [g for g in imported_groups if len(g.classIds) == 2]
    langs = [g for g in imported_groups if len(g.classIds) == 1]
    a, b = streams
    assert not set(a.studentIds) & set(b.studentIds)
    first, second = langs
    assert not set(first.studentIds) & set(second.studentIds)
    assert all(any(set(l.studentIds) & set(s.studentIds) for s in streams) for l in langs)
    assert all(g.rosterStatus == 'complete' and g.rosterSources == ['asc-xml-synthetic'] for g in streams)
    assert a.classIds == ['asc-c-c1abcdefabcdef01', 'asc-c-c1abcdefabcdef02']
    assert len([g for g in state.groups if g.kind == 'whole']) == report['groups']['whole']
    assert all(s.externalId.startswith('synthetic:asc:') for s in state.students) and report['roster']['students'] == len(state.students)
    check = validate_schedule(state)
    assert check['issueCount'] == 0 and check['unknownGroups'] == 0 and check['placed'] == check['required'] == 16


def test_without_synthetic_rosters_the_divided_groups_are_unknown():
    control, report = imported(rosters='none')
    assert not control.students and validate_schedule(control)['unknownGroups'] == 4 and report['groups']['rosterUnknown'] == 4


def test_moved_cards_produce_teacher_and_pupil_collisions():
    state, _ = imported()
    report = validate_schedule(moved(state, 'asc-p-1eabcdefabcdef09-1-3', 0, 1))
    assert [i['type'] for i in report['issues']] == ['collision']
    collision = report['issues'][0]['collision']
    assert collision['teacherIds'] == [ALPHA] and not collision['studentIds'] and not collision['roomIds']
    report = validate_schedule(moved(state, 'asc-p-1eabcdefabcdef05-3-3', 0, 1))
    assert [i['type'] for i in report['issues']] == ['collision']
    collision = report['issues'][0]['collision']
    assert collision['studentIds'] and collision['classIds'] == ['asc-c-c1abcdefabcdef01'] and not collision['teacherIds']


def test_teacherless_and_roomless_lessons_are_not_required():
    state, report = imported()
    teacherless, roomless = load(state, 3), load(state, 4)
    assert not teacherless.teacherRequired and teacherless.teacherId is None and not teacherless.additionalTeacherIds
    assert not roomless.roomRequired and not roomless.roomIds and all(p.roomId is None for p in state.placements if p.workloadId == roomless.id)
    assert load(state, 1).roomIds == ['asc-r-2cabcdefabcdef01', 'asc-r-2cabcdefabcdef02'] and load(state, 1).roomRequired
    assert report['workloads']['teacherless'] == report['workloads']['roomless'] == 1
    check = validate_schedule(state)
    assert check['missingTeachers'] == check['missingRooms'] == 0


def test_room_capacity_is_kept_only_with_real_group_sizes():
    synthetic, report = imported()
    assert [r.capacity for r in synthetic.rooms] == [None, None, None]
    assert report['rooms'] == dict(capacityUnknown=2, capacityIgnoredSynthetic=1, capacityClamped=0)
    control, report = imported(rosters='none')
    assert [r.capacity for r in control.rooms] == [None, None, 30]
    assert report['rooms'] == dict(capacityUnknown=2, capacityIgnoredSynthetic=0, capacityClamped=0)


def test_card_rooms_join_the_lesson_and_used_mode_keeps_only_them():
    state, report = import_asc(mini(room='R2'), 'mini.xml')
    assert state.workloads[0].roomIds == ['asc-r-r1', 'asc-r-r2'] and report['placements']['cardRoomsAdded'] == 1
    assert not [i for i in validate_schedule(state)['issues'] if i['type'] == 'room']
    used, _ = import_asc(mini(rooms='R1,R2'), 'mini.xml', rooms='used')
    assert used.workloads[0].roomIds == ['asc-r-r1']
    assert import_asc(mini(rooms='R1,R2'), 'mini.xml')[0].workloads[0].roomIds == ['asc-r-r1', 'asc-r-r2']


def test_two_teacher_lesson_keeps_lead_and_co_teacher():
    lesson = load(imported()[0], 9)
    assert lesson.teacherId == ALPHA and lesson.additionalTeacherIds == [DELTA] and lesson.teacherRequired


def test_a_minimal_import_passes_the_check():
    assert check_asc(parse_asc(mini())) == []
    state, report = import_asc(mini(), 'mini.xml')
    assert len(state.placements) == 1 and not state.rules and report['workloads']['teacherless'] == 0


def test_lesson_fixed_to_one_day_becomes_a_hard_rule():
    fixed, _ = import_asc(mini(daysdef='D2'), 'mini.xml')
    rule, = fixed.rules
    assert (rule.kind, rule.scope, rule.mode, rule.targetIds) == ('availability', 'workload', 'hard', ['asc-l-l1'])
    assert [w.day for w in rule.unavailable] == [1, 2, 3, 4] and validate_schedule(fixed)['issueCount'] == 0
    assert [i['type'] for i in validate_schedule(moved(fixed, fixed.placements[0].id, 2, 0))['issues']] == ['rule']


REJECTED = {
    'dtd-before-the-root': b'<!DOCTYPE timetable [<!ENTITY x "y">]><timetable displayname="aSc Timetables 2012 XML"/>',
    'entity-without-a-doctype': b'<!ENTITY x "y"><timetable displayname="aSc Timetables 2012 XML"/>',
    'another-format': b'<atlas version="1"/>',
    'another-program': b'<timetable displayname="Other"/>',
    'card-of-a-missing-lesson': mini(lesson='L9'),
    'card-on-several-days': mini(days='11000'),
    'card-in-several-weeks': mini(weeks='11'),
    'fractional-hours-per-week': mini(ppw='1.5'),
    'periods-per-card-out-of-range': mini(ppc='4'),
    'too-many-periods': mini(periods=''.join(f'<period period="{i}" starttime="{7 + i}:00" endtime="{7 + i}:40"/>' for i in range(13))),
    'empty-file': b'',
}


@pytest.mark.parametrize('raw', REJECTED.values(), ids=REJECTED)
def test_files_that_are_not_a_usable_asc_export_are_rejected(raw):
    with pytest.raises(ValueError): import_asc(raw, 'bad.xml')


def test_solver_rebuilds_the_imported_week_from_scratch():
    state, _ = imported()
    result = solve(state.model_copy(update={'placements': []}), seconds=10)
    assert result['status'] == 'success'
    check = validate_schedule(state.model_copy(update={'placements': result['placements']}))
    assert check['placed'] == check['required'] == 16 and check['issueCount'] == 0


def test_cli_writes_database_report_and_json_and_replaces_its_own_database_only_with_force(tmp_path):
    db = tmp_path / 'x.db'
    done = cli(FIXTURE, '--db', db, '--report', tmp_path / 'r.json', '--json', tmp_path / 's.json', cwd=tmp_path)
    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout) == json.loads((tmp_path / 'r.json').read_text(encoding='utf-8')) and json.loads(done.stdout)['counts']['placements'] == 16
    with closing(sqlite3.connect(db)) as connection:
        revision, document = connection.execute("SELECT revision, document FROM workspaces WHERE id='school'").fetchone()
    # Another process with its own hash seed must produce the same document.
    assert revision == 0 and State.model_validate_json(document).model_dump_json() == imported()[0].model_dump_json()
    assert cli(FIXTURE, '--db', db, cwd=tmp_path).returncode != 0
    assert cli(FIXTURE, '--db', db, '--force', cwd=tmp_path).returncode == 0
    with closing(sqlite3.connect(db)) as connection:
        assert connection.execute("SELECT revision FROM workspaces WHERE id='school'").fetchone() == (1,)


@pytest.mark.parametrize('where', ['database', 'database-folder', 'state-json', 'network-path'])
def test_cli_refuses_paths_inside_a_git_checkout_and_network_paths(tmp_path, where):
    checkout = tmp_path / 'checkout'
    (checkout / '.git').mkdir(parents=True)
    args, target = {'database': (['--db', checkout / 'asc.db'], checkout / 'asc.db'),
                    'database-folder': (['--db', checkout / 'data' / 'asc.db'], checkout / 'data'),
                    'state-json': (['--db', tmp_path / 'y.db', '--json', checkout / 'state.json'], checkout / 'state.json'),
                    'network-path': (['--db', '\\\\?\\' + str(tmp_path / 'asc.db')], tmp_path / 'asc.db')}[where]
    assert cli(FIXTURE, *args, cwd=tmp_path).returncode != 0
    assert not target.exists() and not (tmp_path / 'y.db').exists()


def foreign_database(path):
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute('CREATE TABLE workspaces (id TEXT PRIMARY KEY, revision INTEGER, document TEXT)')
        connection.execute("INSERT INTO workspaces VALUES ('school', 7, '{\"settings\": {\"name\": \"aSc import (temp)\"}}')")
    return path


def workspaces(path):
    with closing(sqlite3.connect(path)) as connection: return connection.execute('SELECT id, revision, document FROM workspaces').fetchall()


@pytest.mark.parametrize('flags', [[], ['--force'], ['--force', '--rosters', 'none']], ids=['plain', 'force', 'force-without-rosters'])
def test_cli_does_not_touch_a_database_it_did_not_create(tmp_path, flags):
    atlas = foreign_database(tmp_path / 'atlas.db')
    before = workspaces(atlas)
    assert cli(FIXTURE, '--db', atlas, *flags, cwd=tmp_path).returncode != 0
    assert workspaces(atlas) == before


def test_cli_does_not_overwrite_a_file_that_is_not_a_database(tmp_path):
    text = tmp_path / 'text.db'
    text.write_text('not a database', encoding='utf-8')
    assert cli(FIXTURE, '--db', text, cwd=tmp_path).returncode != 0
    assert text.read_text(encoding='utf-8') == 'not a database'


def test_cli_does_not_replace_an_import_that_was_edited_afterwards(tmp_path):
    own = tmp_path / 'own.db'
    assert cli(FIXTURE, '--db', own, cwd=tmp_path).returncode == 0
    with closing(sqlite3.connect(own)) as connection, connection:
        connection.execute("UPDATE workspaces SET document = json_set(document, '$.settings.name', 'Renamed') WHERE id = 'school'")
    before = workspaces(own)
    assert cli(FIXTURE, '--db', own, '--force', cwd=tmp_path).returncode != 0
    assert workspaces(own) == before


@pytest.mark.parametrize('where', ['report-is-a-database', 'json-is-the-database', 'report-is-the-json', 'json-is-the-source'])
def test_cli_outputs_never_replace_a_database_the_source_or_each_other(tmp_path, where):
    atlas = foreign_database(tmp_path / 'atlas.db')
    source = tmp_path / 'source.xml'
    source.write_bytes(FIXTURE.read_bytes())
    args = {'report-is-a-database': ['--db', tmp_path / 'n1.db', '--report', atlas, '--force'],
            'json-is-the-database': ['--db', tmp_path / 'n2.db', '--json', tmp_path / 'n2.db'],
            'report-is-the-json': ['--db', tmp_path / 'n3.db', '--json', tmp_path / 'o.json', '--report', tmp_path / 'o.json'],
            'json-is-the-source': ['--db', tmp_path / 'n4.db', '--json', source, '--force']}[where]
    before = workspaces(atlas)
    assert cli(source, *args, cwd=tmp_path).returncode != 0
    assert source.read_bytes() == FIXTURE.read_bytes() and workspaces(atlas) == before
    assert not any((tmp_path / name).exists() for name in ['n1.db', 'n2.db', 'n3.db', 'n4.db', 'o.json'])


def test_cli_replaces_an_existing_output_file_only_with_force(tmp_path):
    old = tmp_path / 'old.json'
    old.write_text('{}', encoding='utf-8')
    assert cli(FIXTURE, '--db', tmp_path / 'n5.db', '--report', old, cwd=tmp_path).returncode != 0
    assert old.read_text(encoding='utf-8') == '{}' and not (tmp_path / 'n5.db').exists()
    assert cli(FIXTURE, '--db', tmp_path / 'n6.db', '--report', old, '--force', cwd=tmp_path).returncode == 0
    assert json.loads(old.read_text(encoding='utf-8'))['counts']['placements'] == 16


def test_cli_does_not_import_the_application_modules(tmp_path):
    # Importing backend.api binds the default school database, so the CLI must stay clear of it.
    code = ('import runpy, sys; script, *args = sys.argv[1:]; sys.argv = [script, *args]; runpy.run_path(script, run_name="__main__"); '
            'print("LOADED", sorted(m for m in ["backend.api", "backend.seed"] if m in sys.modules))')
    done = subprocess.run([sys.executable, '-I', '-c', code, str(CLI), str(FIXTURE), '--db', str(tmp_path / 'z.db')],
                          cwd=tmp_path, capture_output=True, text=True, encoding='utf-8', timeout=120)
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip().splitlines()[-1] == 'LOADED []'


def test_cli_refuses_to_replace_a_workspace_that_keeps_timetable_history(tmp_path, monkeypatch):
    from datetime import date
    from fastapi.testclient import TestClient
    from backend import api
    db = tmp_path / 'dated.db'
    assert cli(FIXTURE, '--db', db, cwd=tmp_path).returncode == 0
    monkeypatch.setattr(api, 'DB', db)
    TestClient(api.app).get('/api/state?workspace=school')
    # The application saves the school with a date from which the new timetable holds: the older one is kept as history.
    saved = api.save('school', api.read('school'), effective=date(2026, 8, 17))
    refused = cli(FIXTURE, '--db', db, '--force', cwd=tmp_path)
    assert refused.returncode != 0
    assert api.read('school').revision == saved.revision
