"""The committed demo snapshots (tools/demo/snapshot) go to a public site: they must stay invented and safe."""
import json
import re
from pathlib import Path

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
PER_ROLE_BYTES = 2_621_440
TOTAL_BYTES = 8 * 1024 * 1024


def load(role):
    return json.loads((SNAPSHOTS / f'{role}.json').read_text(encoding='utf-8'))


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
    for key in snapshot['entries']:
        assert key.startswith(('GET /', 'POST /')), key
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


def test_snapshot_size_stays_within_the_budget():
    sizes = [(SNAPSHOTS / f'{role}.json').stat().st_size for role in ROLES]
    assert all(size <= PER_ROLE_BYTES for size in sizes), sizes
    assert sum(sizes) <= TOTAL_BYTES, sizes
