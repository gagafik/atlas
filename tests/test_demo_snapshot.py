"""The committed demo snapshots (tools/demo/snapshot) go to a public site: they must stay invented and safe."""
import gzip
import json
import re
import shutil
import subprocess
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urlencode

import pytest

from tools.demo import names, school

SNAPSHOTS = Path(__file__).resolve().parents[1] / 'tools' / 'demo' / 'snapshot'
ROLES = ['admin', 'teacher', 'parent', 'student']
SECRET_KEY = re.compile(r'token|hash|secret|password|session|cookie|endpoint|p256dh|recovery', re.I)
LEAKS = [re.compile(pattern, re.I) for pattern in [
    r'[A-Za-z]:\\', r'/home/', r'/Users/', r'127\.0\.0\.1', r'localhost', r'[\w.+-]+@[\w-]+\.[\w.-]+',
    r'scrypt|pbkdf2|\$argon|Bearer ', r'gh[pousr]_[A-Za-z0-9]{30,}', r'github_pat_[A-Za-z0-9_]{30,}',
    r'sb_secret_[A-Za-z0-9_-]{25,}', r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----', r'gagaf',
]]
DENIED = re.compile(r'^(GET|POST|PUT) /(api/access/(operations|audit|invitations|push)|api/auth/recovery|api/publications|api/hall|hall-api/|admissions-api/|api/diagnostics)')
# Administrator screens that read a denied route get an empty list, never the real answer.
EMPTY_STUBS = re.compile(r'^GET /api/access/(audit|invitations(/batches)?)$')
# Raw size is only the cost of parsing; what a visitor downloads is the gzip size (Pages compresses JSON).
PER_ROLE_BYTES = 6 * 1024 * 1024
TOTAL_BYTES = 12 * 1024 * 1024
PER_ROLE_GZIP_BYTES = 1024 * 1024


def load(role):
    return json.loads((SNAPSHOTS / f'{role}.json').read_text(encoding='utf-8'))


def get_key(path, **params):
    """The key the browser runtime and the capture derive for a GET (workspace left out, parameters sorted)."""
    query = urlencode(sorted(params.items()))
    return f'GET {path}' + (f'?{query}' if query else '')


def strings(value, secretish=False, path=''):
    """Every string in a document with the path to it and whether a secret-like key is above it."""
    if isinstance(value, dict):
        for key, inner in value.items():
            yield key, False, f'{path}/key'
            yield from strings(inner, secretish or bool(SECRET_KEY.search(key)), f'{path}/{key}')
    elif isinstance(value, list):
        for index, inner in enumerate(value):
            yield from strings(inner, secretish, f'{path}[{index}]')
    elif isinstance(value, str):
        yield value, secretish, path


@pytest.mark.parametrize('role', ROLES)
def test_snapshot_holds_no_secret_or_machine_specific_value(role):
    snapshot = load(role)
    for key, entry in snapshot['entries'].items():
        for text, secretish, path in [(key, False, 'request key'), *strings(entry['b'], False, key)]:
            if secretish:
                assert text in ('demo', ''), f'{role}: secret-like value at {path}'
            for pattern in LEAKS:
                assert not pattern.search(text), f'{role}: {pattern.pattern} matches at {path}'


@pytest.mark.parametrize('role', ROLES)
def test_session_is_the_role_with_one_workspace_and_no_setup(role):
    status = load(role)['entries']['GET /api/auth/status']['b']
    assert status['canSetup'] is False
    assert status['user']['role'] == role
    assert status['user']['workspaces'] == ['school']
    assert {grant['workspace'] for grant in status['user']['grants']} <= {'school'}


@pytest.mark.parametrize('role', ROLES)
def test_snapshot_never_holds_operations_audit_or_public_link_routes(role):
    snapshot = load(role)
    assert snapshot['capturedAt'] == school.DEMO_NOW
    for key, entry in snapshot['entries'].items():
        assert key.startswith(('GET /', 'POST /')), key
        if EMPTY_STUBS.match(key):
            assert entry['b'] in ([], {'items': [], 'nextBefore': None}), key
        else:
            assert not DENIED.search(key), key
        assert 'workspace=' not in key, key


# The showcase pupil has one account of their own; the showcase guardian has a second child, so the child switcher has two to show.
OWN_CHILDREN = {'parent': {school.SHOWCASE_PUPIL, school.SIBLING_PUPIL}, 'student': {school.SHOWCASE_PUPIL}}


@pytest.mark.parametrize('role', ['parent', 'student'])
def test_family_snapshots_hold_no_staff_data_and_only_the_pupils_of_the_account(role):
    entries = load(role)['entries']
    for key, entry in entries.items():
        if key.startswith('GET /api/state') or key.startswith('GET /api/access/'):
            assert entry['s'] >= 400, key
        match = re.search(r'studentId=([^&]+)', key)
        if match:
            assert match.group(1) in OWN_CHILDREN[role], key
    assert any(key.startswith('GET /api/journal/report') for key in entries)


def test_the_parent_can_switch_between_two_children_with_their_screens_in_the_snapshot():
    entries = load('parent')['entries']
    children = [child['id'] for child in entries[get_key('/api/journal/options')]['b']['students']]
    assert children == [school.SHOWCASE_PUPIL, school.SIBLING_PUPIL]
    for child in children:
        assert any(key.startswith('GET /api/journal/report') and f'studentId={child}' in key for key in entries), child
        assert any(key.startswith('GET /api/journal/pupil-period') and f'studentId={child}' in key for key in entries), child
        # The lists of the family hold the child on show only, so the visitor never sees the sibling's work among them.
        for path in ['/api/journal/homework-queue', '/api/journal/quizzes']:
            lists = [entry['b']['items'] for key, entry in entries.items() if key.startswith(f'GET {path}?') and f'studentId={child}' in key and entry['s'] == 200]
            assert lists, (child, path)
            assert all(item['studentId'] == child for items in lists for item in items), (child, path)


# The route each part of the journal opens with. The menu offers a part when `journal/options` says the account has it, so a part
# without any answer in the snapshot would lead a visitor to a screen of misses. The demo serves the snapshot through
# tools/demo/served.mjs, which withdraws such a part (the administrator's file is at the size limit, without the absence notes).
PART_ROUTES = {
    'absences': '/api/journal/absences', 'coverage': '/api/journal/coverage', 'attendance': '/api/journal/attendance',
    'attendanceGrid': '/api/journal/attendance', 'finalGrades': '/api/final-grades', 'periods': '/api/journal/periods',
    'courseTable': '/api/journal/course-table',
}


def served(role):
    """The snapshot of a role as the built demo serves it."""
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js is needed to build the demo')
    result = subprocess.run([node, str(SNAPSHOTS.parent / 'served.mjs'), role], capture_output=True, check=True, timeout=60)
    return json.loads(result.stdout.decode('utf-8'))


@pytest.mark.parametrize('role', ['admin', 'teacher'])
def test_every_part_of_the_journal_the_menu_offers_has_answers_in_the_snapshot(role):
    entries = served(role)['entries']
    panels = entries[get_key('/api/journal/options')]['b']['panels']
    for part, route in PART_ROUTES.items():
        if panels.get(part):
            assert any(key.split(' ', 1)[1].startswith(route) for key in entries), (role, part)


def test_people_and_school_name_are_invented():
    state = load('admin')['entries']['GET /api/state']['b']
    assert state['settings']['name'] == school.SCHOOL_NAME
    for person in state['students'] + state['teachers']:
        assert names.surname_of(person['name']) in names.SURNAMES, person['name']


def test_every_account_of_the_public_snapshot_is_a_person_of_names_py():
    people = {names.ADMIN_NAME, names.FAMILY_GUARDIAN, *names.TEACHERS, *names.PUPILS, *names.GUARDIANS}
    accounts = load('admin')['entries']['GET /api/access/users']['b']
    assert accounts
    for account in accounts:
        assert account['name'] in people, account['name']


def test_snapshot_size_stays_within_the_budget():
    sizes = [(SNAPSHOTS / f'{role}.json').stat().st_size for role in ROLES]
    assert all(size <= PER_ROLE_BYTES for size in sizes), sizes
    assert sum(sizes) <= TOTAL_BYTES, sizes
    zipped = [len(gzip.compress((SNAPSHOTS / f'{role}.json').read_bytes())) for role in ROLES]
    assert all(size <= PER_ROLE_GZIP_BYTES for size in zipped), zipped


# The tests below keep the snapshot usable: every list the demo shows has the details behind its items.
# A missing key makes the viewer meet an error banner after the first click.

def entries_of(role):
    return load(role)['entries']


def lists_under(entries, path):
    """The answers of one route (any query), not of the routes below it."""
    path = path.rstrip('?')
    return [entry['b'] for key, entry in entries.items() if key[4:].split('?')[0] == path and key.startswith('GET ') and entry['s'] == 200]


def test_every_lesson_of_the_staff_days_can_be_opened():
    for role, days in [('admin', ['2026-10-21', '2026-10-30', '2026-11-02']), ('teacher', ['2026-10-06', '2026-10-21', '2026-10-30', '2026-11-02'])]:
        entries = entries_of(role)
        for day in days:
            lessons = entries[get_key('/api/journal/day', day=day)]['b']['lessons']
            assert lessons, (role, day)
            for lesson in (row for row in lessons if not row['archived']):
                for path, params in [
                    ('/api/journal/entry', dict(day=day, lessonKey=lesson['lessonKey'], syncRoster='false')),
                    ('/api/journal/curriculum/lesson', dict(day=day, lessonKey=lesson['lessonKey'])),
                    ('/api/behaviour/lesson', dict(day=day, lessonKey=lesson['lessonKey'])),
                ]:
                    assert get_key(path, **params) in entries, (role, day, path)


@pytest.mark.parametrize('role', ROLES)
def test_every_message_thread_of_the_lists_can_be_opened(role):
    entries = entries_of(role)
    ids = {item['id'] for body in lists_under(entries, '/api/messages/threads?') for item in body['items']}
    assert ids, role
    for thread in ids:
        assert get_key(f'/api/messages/threads/{thread}') in entries, (role, thread)


@pytest.mark.parametrize('role', ['admin', 'teacher'])
def test_every_course_of_the_picker_has_its_tables(role):
    entries = entries_of(role)
    picker = entries[get_key('/api/journal/course-table/courses')]['b']
    assert picker['courses'] and picker['periods']
    # A card of the gradebook opens the course on the first active scale with thresholds and asks for the final grades of the period.
    scale = next(s for s in entries[get_key('/api/final-grades/options')]['b']['scales'] if s['active'] and s['kind'] == 'thresholds')
    finals = 0
    for course in picker['courses']:
        # «Новая работа» of a course the account teaches names the lesson to open.
        if course['canEdit']:
            assert get_key('/api/journal/course-table/latest', groupId=course['groupId'], subjectId=course['subjectId']) in entries, (role, course['group'], course['subject'])
        for period in picker['periods']:
            key = get_key('/api/journal/course-table', groupId=course['groupId'], subjectId=course['subjectId'], start=period['start'], end=period['end'], scaleId=scale['id'], periodId=period['id'])
            assert key in entries, (role, course['group'], course['subject'], period['name'])
            assert all('computed' in pupil for pupil in entries[key]['b']['pupils'])
            finals += len(entries[key]['b']['finals'])
            assert get_key('/api/behaviour/summary', start=period['start'], end=period['end'], subjectId=course['subjectId']) in entries, (role, course['group'], course['subject'], period['name'])
    # The closed first quarter has published final grades, so the column is not empty in the demo.
    assert finals, role


@pytest.mark.parametrize('role', ['parent', 'student'])
def test_every_homework_of_the_journal_can_be_opened(role):
    entries = entries_of(role)
    rows = [row for body in lists_under(entries, '/api/journal/report') for row in body['rows'] if row['content'].get('homework')]
    assert rows, role
    for row in rows:
        assert get_key(f'/api/journal/homework/{row["lessonKey"]}', studentId=row['student']['id']) in entries, (role, row['lessonKey'])


@pytest.mark.parametrize('role', ['parent', 'student'])
def test_every_week_and_final_report_of_the_family_journal_has_its_answer(role):
    # The answers for the school's periods are checked against the client's own period choices in tests/demoSnapshot.test.ts.
    entries = entries_of(role)
    for child in OWN_CHILDREN[role]:
        monday = date(2026, 10, 5)
        while monday <= date(2026, 11, 13):
            key = get_key('/api/journal/report', studentId=child, start=monday.isoformat(), end=(monday + timedelta(days=6)).isoformat(), limit='200')
            assert key in entries, (role, key)
            monday += timedelta(days=7)
        for period in entries[get_key('/api/final-grades/options')]['b']['periods']:
            assert get_key('/api/final-grades/report', studentId=child, periodId=period['id']) in entries, (child, period['id'])


def test_the_forms_of_the_administrator_show_their_results():
    entries = entries_of('admin')
    forms = [item for body in lists_under(entries, '/api/forms/items') for item in body['items']]
    assert forms
    for form in forms:
        assert get_key(f'/api/forms/items/{form["id"]}/responses', limit='20') in entries, form['id']
        assert get_key(f'/api/forms/items/{form["id"]}/pending', limit='50', offset='0') in entries, form['id']


def test_the_final_sheet_and_the_consultations_are_there():
    entries = entries_of('admin')
    book = entries[get_key('/api/final-grades/options')]['b']['books'][0]['id']
    assert get_key(f'/api/final-grades/books/{book}/matrix') in entries
    assert get_key(f'/api/final-grades/books/{book}', classId='', subjectId='', status='all', offset='0') in entries
    assert get_key('/api/meetings/consultations') in entries and get_key('/api/meetings/class-meetings') in entries


def test_the_showcase_pupil_has_marks_and_deadlines_avoid_weekends():
    parent = entries_of('parent')
    rows = [row for body in lists_under(parent, '/api/journal/report') for row in body['rows'] if row['student']['id'] == school.SHOWCASE_PUPIL]
    marks = [grade['score'] / 10 for row in rows for grade in row['record']['grades']]
    assert marks, 'the pupil the parent and the pupil see has no marks'
    for body in lists_under(parent, '/api/journal/homework-queue'):
        for item in body['items']:
            assert not item['dueDate'] or date.fromisoformat(item['dueDate']).weekday() < 5, item['dueDate']
