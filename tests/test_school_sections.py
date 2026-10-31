"""The school menu hides six optional sections until an administrator turns them on; rights of the modules stay as they were."""
import sqlite3
from contextlib import closing

from fastapi.testclient import TestClient
from backend import api
from backend.school_sections import IDS, SECTIONS, tables, stored
from test_access import client, enable, create, signed, HEADERS

URL = '/api/school-sections'


def save(c, version, hidden, reason='Школа пользуется этими разделами', **values):
    return c.put(URL, json=dict(version=version, hidden=hidden, reason=reason) | values)


def test_new_key_defaults_to_hidden_for_every_role(client):
    # Without accounts the server answers the same default.
    assert client.get(URL).json()['hidden'] == IDS
    enable(client); create(client, 'teacher'); create(client, 'parent'); create(client, 'student'); create(client, 'staff')
    for account in (client, signed('teacher'), signed('parent'), signed('student'), signed('staff')):
        answer = account.get(URL)
        assert answer.status_code == 200, answer.text
        body = answer.json()
        assert body['version'] == 0 and body['hidden'] == IDS and all(item['hidden'] for item in body['items'])
        assert [i['id'] for i in body['items']] == IDS and set(body['items'][0]) == {'id', 'label', 'hidden'}
    assert TestClient(api.app, headers=HEADERS).get(URL).status_code == 401
    # Reading never creates the table: the read connection cannot, and a fresh database has no row.
    with closing(sqlite3.connect(api.DB)) as db:
        assert not db.execute("SELECT 1 FROM sqlite_master WHERE name='school_sections'").fetchone()


def test_admin_turns_a_section_back_on(client):
    enable(client); create(client, 'teacher'); create(client, 'parent'); create(client, 'editor')
    parent, teacher, editor = signed('parent'), signed('teacher'), signed('editor')
    shown = [i for i in IDS if i != 'library']
    saved = save(client, 0, shown); assert saved.status_code == 200, saved.text
    assert saved.json()['version'] == 1 and saved.json()['hidden'] == shown
    body = parent.get(URL).json()
    assert body['version'] == 1 and {i['id']: i['hidden'] for i in body['items']} == {i: i != 'library' for i in IDS}
    # Only an administrator changes it: the guard lets personal routes through, the handler decides.
    assert save(teacher, 1, []).status_code == 403 and save(editor, 1, []).status_code == 403 and save(parent, 1, []).status_code == 403
    assert TestClient(api.app, headers=HEADERS).put(URL, json=dict(version=1, hidden=[], reason='x')).status_code == 401
    # A stale version and an unknown id change nothing; a repeated request is a conflict, as with the message channels.
    assert save(client, 0, []).status_code == 409
    assert save(client, 1, ['library', 'unknown']).status_code == 422
    assert save(client, 1, [], reason='').status_code == 422
    assert parent.get(URL).json()['hidden'] == shown
    # Showing everything is a choice that stays: an empty list is saved as a row, not mistaken for the default.
    assert save(client, 1, []).json()['hidden'] == []
    assert parent.get(URL).json() == dict(version=2, hidden=[], items=[dict(s, hidden=False) for s in SECTIONS])
    assert save(client, 2, ['site', 'payments', 'payments']).json()['hidden'] == ['payments', 'site']
    audit = [row for row in client.get('/api/access/audit').json() if row['action'] == 'school.sections_updated']
    assert len(audit) == 3 and audit[0]['workspace'] == 'school' and 'Редакция 3' in audit[0]['detail'] and 'payments, site' in audit[0]['detail'] and 'Школа пользуется' in audit[0]['detail']
    assert 'Редакция 2 · скрыто: нет' in audit[1]['detail']


def test_hidden_section_api_keeps_existing_permissions(client):
    enable(client); create(client, 'parent')
    parent = signed('parent')
    probes = ['/api/library/loans?workspace=demo', '/api/canteen/report?workspace=demo', '/api/payments/report?workspace=demo', '/api/lost-found/options?workspace=demo']
    hidden = {url: (parent.get(url).status_code, parent.get(url).json()) for url in probes}
    assert hidden[probes[1]][0] == 403 and hidden[probes[2]][0] == 403
    assert save(client, 0, []).status_code == 200
    shown = {url: (parent.get(url).status_code, parent.get(url).json()) for url in probes}
    # The menu setting changes nothing the API answers.
    assert shown == hidden


def test_setting_survives_existing_database(client):
    enable(client)
    assert save(client, 0, ['site']).status_code == 200
    with closing(sqlite3.connect(api.DB)) as db:
        tables(db); tables(db)
        assert stored(db) == (1, ['site'])
    assert client.get(URL).json()['hidden'] == ['site']
