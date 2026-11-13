"""The school menu hides six optional sections until an administrator turns them on; rights of the modules stay as they were."""
import pytest
from fastapi.testclient import TestClient
from backend import api
from backend.school_sections import IDS
from test_access import client, enable, create, signed, HEADERS

URL = '/api/school-sections'
REASON = 'Школа пользуется этими разделами'


def save(c, version, hidden, reason=REASON, **values):
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
        assert [i['id'] for i in body['items']] == ['payments', 'library', 'canteen', 'lost_found', 'admissions', 'site']
    assert TestClient(api.app, headers=HEADERS).get(URL).status_code == 401


def test_admin_turns_a_section_back_on(client):
    enable(client); create(client, 'parent')
    shown = [i for i in IDS if i != 'library']
    saved = save(client, 0, shown); assert saved.status_code == 200, saved.text
    assert saved.json()['version'] == 1 and saved.json()['hidden'] == shown
    body = signed('parent').get(URL).json()
    assert body['version'] == 1 and {i['id']: i['hidden'] for i in body['items']} == {i: i != 'library' for i in IDS}


@pytest.mark.parametrize('role', ['teacher', 'editor', 'parent'])
def test_only_an_administrator_changes_the_sections(client, role):
    enable(client); create(client, role)
    assert save(signed(role), 0, []).status_code == 403
    assert client.get(URL).json()['version'] == 0


def test_sections_cannot_be_changed_without_signing_in(client):
    enable(client)
    assert TestClient(api.app, headers=HEADERS).put(URL, json=dict(version=0, hidden=[], reason='x')).status_code == 401


def test_rejected_saves_change_nothing(client):
    enable(client); create(client, 'parent')
    shown = [i for i in IDS if i != 'library']
    assert save(client, 0, shown).status_code == 200
    # A stale version is a conflict, an unknown id and an empty reason are invalid.
    assert save(client, 0, []).status_code == 409
    assert save(client, 1, ['library', 'unknown']).status_code == 422
    assert save(client, 1, [], reason='').status_code == 422
    assert signed('parent').get(URL).json()['hidden'] == shown


def test_showing_every_section_is_a_choice_that_stays(client):
    enable(client); create(client, 'parent')
    assert save(client, 0, []).json()['hidden'] == []
    body = signed('parent').get(URL).json()
    assert body['version'] == 1 and body['hidden'] == [] and not any(item['hidden'] for item in body['items'])


def test_repeated_ids_are_saved_once(client):
    enable(client)
    assert save(client, 0, ['site', 'payments', 'payments']).json()['hidden'] == ['payments', 'site']


def test_every_save_is_audited_with_its_author(client):
    admin = enable(client)
    assert save(client, 0, ['site']).status_code == 200 and save(client, 1, []).status_code == 200
    audit = [row for row in client.get('/api/access/audit').json() if row['action'] == 'school.sections_updated']
    assert len(audit) == 2 and all(row['workspace'] == 'school' and row['actorId'] == admin['id'] for row in audit)


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
