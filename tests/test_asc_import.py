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


def test_counts_ids_and_determinism():
    state, report = imported()
    again, again_report = import_asc(FIXTURE.read_bytes(), str(FIXTURE))
    assert state.model_dump_json() == again.model_dump_json() and report == again_report
    assert {k: len(getattr(state, k)) for k in ['classes', 'teachers', 'rooms', 'subjects', 'students', 'groups', 'workloads', 'placements', 'profiles', 'rules']} == \
        dict(classes=3, teachers=4, rooms=3, subjects=5, students=6, groups=7, workloads=10, placements=16, profiles=1, rules=0)
    assert report['counts']['hours'] == 16 and report['groups'] == dict(whole=3, singleDivision=2, combined=2, combinedWhole=0, unusedDivisionGroups=2, rosterComplete=4, rosterUnknown=0)
    assert state.importInfo.model_dump(exclude={'notes'}) == dict(filename='asc-sample.xml', sourceCells=16, importedLessons=10)
    assert [c.id for c in state.classes] == ['asc-c-c1abcdefabcdef01', 'asc-c-c1abcdefabcdef02', 'asc-c-c1abcdefabcdef03']
    assert {p.id for p in state.placements if p.workloadId == load(state, 2).id} == {'asc-p-1eabcdefabcdef02-0-2', 'asc-p-1eabcdefabcdef02-0-3'}
    assert next(p for p in state.placements if p.id == 'asc-p-1eabcdefabcdef02-0-2').sourceCells == ['asc-card:1EABCDEFABCDEF02:10000:2']
    assert all(x.id.startswith(prefix) for prefix, rows in [('asc-t-', state.teachers), ('asc-r-', state.rooms), ('asc-s-', state.subjects), ('asc-l-', state.workloads), ('asc-syn-', state.students)] for x in rows)
    counts_only = json.dumps(report, ensure_ascii=False) + json.dumps(state.importInfo.notes, ensure_ascii=False)
    assert not any(name in counts_only for name in ['Alpha', 'Delta', 'Stream', 'Lang', '7X', 'Room 101', 'Hall', 'Mathematics', 'Science', 'abcdef'])
    used = imported(only_used=True)
    assert len(used[0].subjects) == 4 and used[1]['skipped'] == dict(subjects=1, teachers=0, rooms=0)


def test_contacts_are_never_copied_and_empty_name_gets_placeholder():
    state, report = imported()
    dump = state.model_dump_json()
    # Day bitstrings such as 10000 legitimately contain 000; the phone value must not.
    assert 'example.invalid' not in dump and '"000"' not in dump and '#123456' not in dump and 'ALP' not in dump
    assert not any(field in dump for field in ['email', 'mobile', 'gender', 'firstname'])
    assert [t.name for t in state.teachers] == ['Teacher Alpha', 'Teacher Beta', 'Учитель aSc 7eabcd', 'Teacher Delta']
    assert report['placeholders']['teachers'] == 1 and report['dropped']['teacherEmail'] == report['dropped']['teacherMobile'] == 1


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
    assert (placement.id, placement.period, placement.sourceCells) == ('asc-p-l1-0-1', 1, ['asc-card:L1:10000:3'])
    assert validate_schedule(state)['issueCount'] == 0
    with pytest.raises(ValueError, match='вне звонков'): import_asc(mini(periods='<period period="1" starttime="8:00" endtime="8:45"/>'), 'mini.xml')
    with pytest.raises(ValueError, match='разными целыми'):
        import_asc(mini(periods='<period period="0" starttime="8:00" endtime="8:45"/><period period="0" starttime="9:00" endtime="9:45"/>'), 'mini.xml')


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


def test_synthetic_rosters_follow_asc_divisions():
    state, report = imported()
    groups = {g.name: g for g in state.groups}
    a, b, lang = groups['Stream A · 7X, 7Y'], groups['Stream B · 7X, 7Y'], groups['Lang 1 · 7X']
    assert not set(a.studentIds) & set(b.studentIds) and set(a.studentIds) & set(lang.studentIds)
    assert not set(lang.studentIds) & set(groups['Lang 2 · 7X'].studentIds)
    assert a.kind == 'imported' and a.rosterStatus == 'complete' and a.rosterSources == ['asc-xml-synthetic']
    assert a.classIds == ['asc-c-c1abcdefabcdef01', 'asc-c-c1abcdefabcdef02'] and groups['7X · весь класс'].kind == 'whole'
    assert all(s.externalId.startswith('synthetic:asc:') for s in state.students) and report['roster']['students'] == 6
    check = validate_schedule(state)
    assert check['issueCount'] == 0 and check['unknownGroups'] == 0 and check['placed'] == check['required'] == 16
    control, control_report = imported(rosters='none')
    assert not control.students and validate_schedule(control)['unknownGroups'] == 4 and control_report['groups']['rosterUnknown'] == 4
    sizes = [6, 6, 4, 4, 2, 2]
    rows = synthetic_roster(sizes)
    assert rows == synthetic_roster(sizes) and len(rows) >= 36
    assert all(any(r[i] == x and r[j] == y for r in rows) for i, j in combinations(range(6), 2) for x in range(sizes[i]) for y in range(sizes[j]))
    assert synthetic_roster([]) == [()] and synthetic_roster([3]) == [(0,), (1,), (2,)] and len(synthetic_roster([2, 3])) == 6


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


def test_rejections_and_fixed_day_rule():
    assert check_asc(parse_asc(mini())) == []
    state, report = import_asc(mini(), 'mini.xml')
    assert len(state.placements) == 1 and not state.rules and report['workloads']['teacherless'] == 0
    fixed, _ = import_asc(mini(daysdef='D2'), 'mini.xml')
    rule, = fixed.rules
    assert (rule.kind, rule.scope, rule.mode, rule.targetIds) == ('availability', 'workload', 'hard', ['asc-l-l1'])
    assert [w.day for w in rule.unavailable] == [1, 2, 3, 4] and validate_schedule(fixed)['issueCount'] == 0
    assert [i['type'] for i in validate_schedule(moved(fixed, fixed.placements[0].id, 2, 0))['issues']] == ['rule']
    thirteen = ''.join(f'<period period="{i}" starttime="{7 + i}:00" endtime="{7 + i}:40"/>' for i in range(13))
    cases = [(b'<!DOCTYPE timetable [<!ENTITY x "y">]><timetable displayname="aSc Timetables 2012 XML"/>', 'DTD'),
             (b'<!ENTITY x "y"><timetable displayname="aSc Timetables 2012 XML"/>', 'DTD'),
             (b'<atlas version="1"/>', 'aSc Timetables'), (b'<timetable displayname="Other"/>', 'aSc Timetables'),
             (mini(lesson='L9'), 'отсутствующий урок'), (mini(days='11000'), 'ровно один'), (mini(weeks='11'), 'одна неделя'),
             (mini(ppw='1.5'), 'целое число'), (mini(ppc='4'), '1–3'), (mini(periods=thirteen), 'от 1 до 12'), (b'', 'пустой')]
    for raw, message in cases:
        with pytest.raises(ValueError, match=message): import_asc(raw, 'bad.xml')


def test_solver_rebuilds_the_imported_week_from_scratch():
    state, _ = imported()
    result = solve(state.model_copy(update={'placements': []}), seconds=10)
    assert result['status'] == 'success'
    check = validate_schedule(state.model_copy(update={'placements': result['placements']}))
    assert check['placed'] == check['required'] == 16 and check['issueCount'] == 0


def test_cli_writes_only_temporary_databases_without_backend_api(tmp_path):
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
    for args, target in [(['--db', ROOT / 'tests' / 'asc-refused.db'], ROOT / 'tests' / 'asc-refused.db'),
                         (['--db', tmp_path / 'y.db', '--json', ROOT / 'asc-refused.json'], ROOT / 'asc-refused.json'),
                         (['--db', tmp_path / 'checkout' / 'data' / 'asc.db'], tmp_path / 'checkout' / 'data'),
                         (['--db', '\\\\?\\' + str(ROOT / 'asc-refused.db')], ROOT / 'asc-refused.db')]:
        (tmp_path / 'checkout' / '.git').mkdir(parents=True, exist_ok=True)
        refused = cli(FIXTURE, *args, cwd=ROOT)
        assert refused.returncode != 0 and ('репозитория' in refused.stderr or 'сетевые' in refused.stderr)
        assert not target.exists() and not (tmp_path / 'y.db').exists()


def test_cli_never_overwrites_databases_it_did_not_create(tmp_path):
    atlas = tmp_path / 'atlas.db'
    with closing(sqlite3.connect(atlas)) as connection, connection:
        connection.execute('CREATE TABLE workspaces (id TEXT PRIMARY KEY, revision INTEGER, document TEXT)')
        connection.execute("INSERT INTO workspaces VALUES ('school', 7, '{\"settings\": {\"name\": \"School\"}}')")
    for flags in [[], ['--force'], ['--force', '--rosters', 'none']]:
        refused = cli(FIXTURE, '--db', atlas, *flags, cwd=tmp_path)
        assert refused.returncode != 0 and 'не этим импортом' in refused.stderr
    with closing(sqlite3.connect(atlas)) as connection:
        assert connection.execute('SELECT id, revision FROM workspaces').fetchall() == [('school', 7)]
    (tmp_path / 'text.db').write_text('not a database', encoding='utf-8')
    assert 'SQLite' in cli(FIXTURE, '--db', tmp_path / 'text.db', cwd=tmp_path).stderr
    own = tmp_path / 'own.db'
    assert cli(FIXTURE, '--db', own, cwd=tmp_path).returncode == 0
    with closing(sqlite3.connect(own)) as connection, connection:
        connection.execute("UPDATE workspaces SET document = json_set(document, '$.settings.name', 'Renamed') WHERE id = 'school'")
    refused = cli(FIXTURE, '--db', own, '--force', cwd=tmp_path)
    assert refused.returncode != 0 and 'не похожа' in refused.stderr
    # Outputs never replace a database, the source or each other; other files only with --force.
    for args, message in [(['--db', tmp_path / 'n1.db', '--report', atlas, '--force'], 'SQLite'),
                          (['--db', tmp_path / 'n2.db', '--json', tmp_path / 'n2.db'], 'совпадает'),
                          (['--db', tmp_path / 'n3.db', '--json', tmp_path / 'o.json', '--report', tmp_path / 'o.json'], 'совпадает'),
                          (['--db', tmp_path / 'n4.db', '--json', tmp_path / 'source.xml', '--force'], 'совпадает')]:
        (tmp_path / 'source.xml').write_bytes(FIXTURE.read_bytes())
        refused = cli(tmp_path / 'source.xml', *args, cwd=tmp_path)
        assert refused.returncode != 0 and message in refused.stderr
    assert (tmp_path / 'source.xml').read_bytes() == FIXTURE.read_bytes()
    with closing(sqlite3.connect(atlas)) as connection:
        assert connection.execute('SELECT id, revision FROM workspaces').fetchall() == [('school', 7)]
    assert not any((tmp_path / f'n{i}.db').exists() for i in range(1, 5)) and not (tmp_path / 'o.json').exists()
    (tmp_path / 'old.json').write_text('{}', encoding='utf-8')
    assert 'существует' in cli(FIXTURE, '--db', tmp_path / 'n5.db', '--report', tmp_path / 'old.json', cwd=tmp_path).stderr
    assert cli(FIXTURE, '--db', tmp_path / 'n6.db', '--report', tmp_path / 'old.json', '--force', cwd=tmp_path).returncode == 0
    assert json.loads((tmp_path / 'old.json').read_text(encoding='utf-8'))['counts']['placements'] == 16
    code = ('import runpy, sys; script, *args = sys.argv[1:]; sys.argv = [script, *args]; runpy.run_path(script, run_name="__main__"); '
            'print("LOADED", sorted(m for m in ["backend.api", "backend.seed"] if m in sys.modules))')
    done = subprocess.run([sys.executable, '-I', '-c', code, str(CLI), str(FIXTURE), '--db', str(tmp_path / 'z.db')],
                          cwd=tmp_path, capture_output=True, text=True, encoding='utf-8', timeout=120)
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip().splitlines()[-1] == 'LOADED []'


def test_cli_refuses_to_replace_a_workspace_that_keeps_timetable_history(tmp_path):
    db = tmp_path / 'dated.db'
    assert cli(FIXTURE, '--db', db, cwd=tmp_path).returncode == 0
    with closing(sqlite3.connect(db)) as connection, connection:
        # The shape the application leaves after a save: older versions would overlay the replaced document.
        connection.execute('CREATE TABLE timetable_current (workspace TEXT PRIMARY KEY, valid_from TEXT)')
        connection.execute("INSERT INTO timetable_current VALUES ('school', '2026-08-17')")
    refused = cli(FIXTURE, '--db', db, '--force', cwd=tmp_path)
    assert refused.returncode != 0 and 'историю расписания' in refused.stderr
    with closing(sqlite3.connect(db)) as connection:
        assert connection.execute("SELECT revision FROM workspaces WHERE id='school'").fetchone() == (0,)
    with closing(sqlite3.connect(db)) as connection, connection:
        connection.execute("UPDATE timetable_current SET workspace='other'")
    assert cli(FIXTURE, '--db', db, '--force', cwd=tmp_path).returncode == 0
