"""The committed demo snapshots (tools/demo/snapshot) go to a public site: they must stay invented and safe."""
import gzip
import json
import re
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


@pytest.mark.parametrize('role', ['parent', 'student'])
def test_family_snapshots_hold_no_staff_data_and_only_the_showcase_pupil(role):
    entries = load(role)['entries']
    for key, entry in entries.items():
        if key.startswith('GET /api/state') or key.startswith('GET /api/access/'):
            assert entry['s'] >= 400, key
        match = re.search(r'studentId=([^&]+)', key)
        if match:
            assert match.group(1) == school.SHOWCASE_PUPIL, key
    assert any(key.startswith('GET /api/journal/report') for key in entries)


def test_people_and_school_name_are_invented():
    state = load('admin')['entries']['GET /api/state']['b']
    assert 'Пример' in state['settings']['name']
    for person in state['students'] + state['teachers']:
        assert names.surname_of(person['name']) in names.SURNAMES, person['name']
    assert len(state['students']) == 4 * school.PUPILS_PER_CLASS


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


def coverage_months(entries):
    """(mode, class id, first day, last day) of every calendar request the day view of the journal made."""
    found = set()
    for key in entries:
        if key.startswith('POST /api/journal/coverage/days#'):
            body = json.loads(key.split('#', 1)[1])
            found.add((body['mode'], body['classId'], body['start'], body['end']))
    return found


@pytest.mark.parametrize('role', ['admin', 'teacher'])
def test_the_calendar_of_the_day_view_has_its_months(role):
    entries = entries_of(role)
    options = entries[get_key('/api/journal/options')]['b']
    asked = coverage_months(entries)
    if role == 'teacher':
        scopes = [('teaching', '')]
    else:
        scopes = [('classes', c['id']) for c in entries[get_key('/api/journal/coverage/options')]['b']['classes']['classes']]
    assert scopes, role
    for mode, class_id in scopes:
        for month in ['2026-09-01', '2026-10-01', '2026-11-01', '2026-12-01']:
            # The calendar shows six whole weeks, Monday first, cut to the school year.
            first = date.fromisoformat(month) - timedelta(days=date.fromisoformat(month).weekday())
            start, end = max(first.isoformat(), options['yearStart']), min((first + timedelta(days=41)).isoformat(), options['yearEnd'])
            assert (mode, class_id, start, end) in asked, (role, mode, class_id, start, end)


@pytest.mark.parametrize('role', ['parent', 'student'])
def test_every_homework_of_the_journal_can_be_opened(role):
    entries = entries_of(role)
    rows = [row for body in lists_under(entries, '/api/journal/report') for row in body['rows'] if row['content'].get('homework')]
    assert len(rows) > 20, role
    for row in rows:
        assert get_key(f'/api/journal/homework/{row["lessonKey"]}', studentId=school.SHOWCASE_PUPIL) in entries, (role, row['lessonKey'])


@pytest.mark.parametrize('role', ['parent', 'student'])
def test_every_period_and_week_of_the_family_journal_has_its_answer(role):
    entries = entries_of(role)
    options = entries[get_key('/api/journal/options')]['b']
    year_end = min(date.fromisoformat(options['yearEnd']), date.fromisoformat(options['yearStart']) + timedelta(days=366)).isoformat()
    ranges = [(p['start'], p['end']) for p in options['periods']]
    # The same choices as periodChoices in src/pupilJournal.ts: the school's periods and the school year.
    if not any(p['kind'] == 'year' and p['start'] <= options['yearStart'] and p['end'] >= year_end for p in options['periods']):
        ranges.append((options['yearStart'], year_end))
    assert len(ranges) >= 3, ranges
    for start, end in ranges:
        for path in ['/api/journal/pupil-period', '/api/behaviour/family']:
            assert get_key(path, studentId=school.SHOWCASE_PUPIL, start=start, end=end) in entries, (role, path, start, end)
    monday = date(2026, 10, 5)
    while monday <= date(2026, 11, 13):
        key = get_key('/api/journal/report', studentId=school.SHOWCASE_PUPIL, start=monday.isoformat(), end=(monday + timedelta(days=6)).isoformat(), limit='200')
        assert key in entries, (role, key)
        monday += timedelta(days=7)
    for period in entries[get_key('/api/final-grades/options')]['b']['periods']:
        assert get_key('/api/final-grades/report', studentId=school.SHOWCASE_PUPIL, periodId=period['id']) in entries, period['id']


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


def test_the_showcase_pupil_is_a_good_learner_and_deadlines_avoid_weekends():
    parent = entries_of('parent')
    rows = [row for body in lists_under(parent, '/api/journal/report') for row in body['rows']]
    marks = [grade['score'] / 10 for row in rows for grade in row['record']['grades']]
    assert marks and sum(marks) / len(marks) >= .7, 'the pupil the parent and the pupil see has no good marks'
    for body in lists_under(parent, '/api/journal/homework-queue'):
        for item in body['items']:
            assert not item['dueDate'] or date.fromisoformat(item['dueDate']).weekday() < 5, item['dueDate']
