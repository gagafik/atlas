"""Hall TV screens: administrator-only management, tokenised public display, privacy whitelist, windows and limits."""
from datetime import date, datetime, timedelta
from uuid import uuid4
import hashlib
import json
import pytest
from fastapi.testclient import TestClient
from backend import api, hall_screen
from backend.models import DatedLesson, Event, Group, Named, Placement, SchoolClass, TeacherAbsence, Workload
from test_access import client, enable, create, signed, HEADERS

MON, TUE = date(2026, 11, 2), date(2026, 11, 3)
SCREENS = '/api/hall/screens'
FORBIDDEN = ['s1', 's2', 'Ученик', 'Больничный', 'болезнь', 'группа', 'g-math', 'eng', 't2', 'r0', 'c-demo-a', 'reasons', 'studentIds', 'Администратор', 'Выезд']


@pytest.fixture(autouse=True)
def reset():
    api.hall_screen.limiter.reset()
    api.hall_screen.cache.clear()


def at(monkeypatch, iso):
    moment = datetime.fromisoformat(iso)
    monkeypatch.setattr(hall_screen, 'now', lambda: moment)


def setup_changes(buildings=False, extra=False):
    """Monday 2026-11-02: English substituted and moved to another room, maths moved to Tuesday, teacher absence on file."""
    s = api.read('demo')
    s.datedLessons = [
        DatedLesson(id='sub', name='Замена: болезнь', kind='change', placementId='eng', sourceDate=MON, onDate=MON, teacherIds=['t3'], roomIds=['r2']),
        DatedLesson(id='mv', name='Перенос: конференция', kind='change', placementId='math', sourceDate=MON, onDate=TUE)]
    s.absences = [TeacherAbsence(id='abs', name='Больничный', teacherId='t2', start=MON, end=MON)]
    if buildings:
        s.campus.buildings = [Named(id='b1', name='Корпус 1'), Named(id='b2', name='Корпус 2')]
        for room in s.rooms: room.buildingId = 'b2' if room.id == 'r2' else 'b1'
    if extra:
        s.placements.append(Placement(id='eng-d', workloadId='w1', day=0, period=3, roomId='r1'))
        s.events = [Event(id='trip', name='Выезд 10D', start=MON, end=MON, kind='extra', effect='cancel', classIds=['c-demo-d'])]
    return api.save('demo', s)


def screen(c, **values):
    body = dict(requestId=str(uuid4()), name='Холл', buildingId=None, teacherNames='full', tomorrowFrom=14, pageSeconds=15) | values
    response = c.post(SCREENS + '?workspace=demo', json=body)
    assert response.status_code == 200, response.text
    return response.json()


def link(c, s, **values):
    response = c.post(f"{SCREENS}/{s['id']}/link?workspace=demo", json=dict(version=s['version']) | values)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body['path'].startswith('/?hall#')
    return body['path'].split('#', 1)[1], body['screen']


def display(token, **headers):
    return TestClient(api.app).get('/hall-api/display', headers={'X-Hall-Token': token} | headers)


def ready(client, monkeypatch, iso='2026-11-02T02:00:00+00:00', **values):
    """An administrator, the Monday changes, one screen with a link and the clock set."""
    enable(client); setup_changes(buildings=values.pop('buildings', False), extra=values.pop('extra', False))
    s = screen(client, **values); token, s = link(client, s)
    at(monkeypatch, iso)
    return s, token


def put_note(id, starts, ends, status='active'):
    """An announcement straight into the table, for volumes the API would take minutes to build."""
    document = json.dumps(dict(title=id, text='', visible=True, screenIds=[]))
    with api.connect() as db:
        db.execute("INSERT INTO hall_announcements(id,workspace,version,status,starts,ends,author_id,author_name,created,updated,document) VALUES (?,'demo',1,?,?,?,'x','Администратор',?,?,?)",
                   (id, status, hall_screen.iso(starts), hall_screen.iso(ends), hall_screen.iso(starts), hall_screen.iso(starts), document))


def items(response, index=0, kind=None):
    assert response.status_code == 200, response.text
    found = response.json()['days'][index]['items']
    return [i for i in found if i['kind'] == kind] if kind else found


def test_short_name():
    assert hall_screen.short_name('Иванов Алексей Борисович') == 'Иванов А. Б.'
    assert hall_screen.short_name('Иванов Алексей') == 'Иванов А.'
    assert hall_screen.short_name('Учитель') == 'Учитель'


def announce(client, **values):
    return client.post('/api/hall/announcements?workspace=demo', json=dict(requestId=str(uuid4()), title='Т', startsAt='2026-11-02T09:00', endsAt='2026-11-02T10:00') | values)


@pytest.mark.parametrize('window', [dict(endsAt='2027-12-31T09:00'), dict(startsAt='2026-11-02T12:00', endsAt='2026-11-02T12:00'), dict(startsAt='2026-11-02T12:00', endsAt='2026-11-02T11:00')],
                         ids=['longer-than-a-year', 'empty', 'ends-before-it-starts'])
def test_announcement_window_must_be_a_sensible_span(client, monkeypatch, window):
    enable(client)
    at(monkeypatch, '2026-11-02T03:00:00+00:00')
    # The end is still in the future here, so only the window itself can be what is wrong.
    assert announce(client, **window).status_code == 422


def test_announcement_screens_do_not_repeat(client, monkeypatch):
    enable(client); s = screen(client)
    at(monkeypatch, '2026-11-02T03:00:00+00:00')
    assert announce(client, screenIds=[s['id'], s['id']]).status_code == 422
    assert announce(client, screenIds=[s['id']]).status_code == 200


def test_announcement_time_with_a_zone_keeps_it(client, monkeypatch):
    enable(client)
    at(monkeypatch, '2026-11-02T03:00:00+00:00')
    zoned = announce(client, startsAt='2026-11-02T09:00Z', endsAt='2026-11-02T10:00Z')
    assert zoned.status_code == 200 and datetime.fromisoformat(zoned.json()['startsAt']) == datetime.fromisoformat('2026-11-02T09:00+00:00')
    # A time without a zone is school time.
    plain = announce(client)
    assert datetime.fromisoformat(plain.json()['startsAt']) == datetime.fromisoformat('2026-11-02T04:00+00:00')


def test_unknown_token_on_a_fresh_database_is_not_found_and_options_open_repeatedly(client):
    assert display('A' * 43).status_code == 404
    enable(client)
    assert client.get('/api/hall/options?workspace=demo').status_code == 200
    assert client.get('/api/hall/options?workspace=demo').status_code == 200


def test_admin_only_management(client):
    enable(client)
    for role in ['editor', 'teacher', 'staff', 'parent', 'student']:
        create(client, role, login=role)
    body = dict(requestId=str(uuid4()), name='Холл')
    for role in ['editor', 'teacher', 'staff', 'parent', 'student']:
        c = signed(role)
        assert c.get('/api/hall/options?workspace=demo').status_code == 403, role
        assert c.post(SCREENS + '?workspace=demo', json=body).status_code == 403, role
        assert c.post('/api/hall/announcements?workspace=demo', json=dict(requestId=str(uuid4()), title='Т', startsAt='2026-11-02T09:00', endsAt='2026-11-02T10:00')).status_code == 403, role
        assert c.get('/api/hall/history?workspace=demo&entity=screen&id=x').status_code == 403, role
    anonymous = TestClient(api.app, headers=HEADERS)
    assert anonymous.get('/api/hall/options?workspace=demo').status_code == 401
    assert anonymous.post(SCREENS + '?workspace=demo', json=body).status_code == 401
    assert client.get('/api/hall/options?workspace=demo').status_code == 200
    # Nobody but an administrator can read or change an existing screen or announcement. The editor passes the session guard,
    # so for the editor the routes' own check is the only protection.
    s = screen(client)
    note = client.post('/api/hall/announcements?workspace=demo', json=dict(requestId=str(uuid4()), title='Собрание', startsAt='2099-01-01T09:00', endsAt='2099-01-02T09:00'))
    assert note.status_code == 200, note.text
    base, note_path = f"{SCREENS}/{s['id']}", f"/api/hall/announcements/{note.json()['id']}"
    edit = dict(version=1, title='Чужое', startsAt='2099-01-01T09:00', endsAt='2099-01-02T09:00')
    for role in ['editor', 'teacher', 'staff', 'parent', 'student']:
        c = signed(role)
        calls = [c.get(base + '/preview?workspace=demo'), c.put(base + '?workspace=demo', json=dict(version=1, name='Чужой')),
                 *(c.post(base + path + '?workspace=demo', json=dict(version=1) | values) for path, values in [('/link', {}), ('/revoke', dict(reason='x')), ('/archive', dict(reason='x'))]),
                 c.put(note_path + '?workspace=demo', json=edit), c.post(note_path + '/withdraw?workspace=demo', json=dict(version=1, reason='x')),
                 c.get(f"/api/hall/history?workspace=demo&entity=announcement&id={note.json()['id']}")]
        assert [r.status_code for r in calls] == [403] * len(calls), role
    # Nothing was touched.
    listed = client.get('/api/hall/options?workspace=demo').json()
    assert [(n['title'], n['state'], n['version']) for n in listed['announcements']] == [('Собрание', 'scheduled', 1)]
    assert [(x['name'], x['version'], x['status'], x['link']['status']) for x in listed['screens']] == [('Холл', 1, 'active', 'none')]


def test_local_mode_without_accounts_is_closed(client):
    assert client.get('/api/hall/options?workspace=demo').status_code == 403


def new_screen_body(**values):
    return dict(requestId=str(uuid4()), name='Главный холл', buildingId=None, teacherNames='short', tomorrowFrom=14, pageSeconds=15) | values


def test_screen_create_replays_by_request_id(client):
    enable(client)
    body = new_screen_body()
    first = client.post(SCREENS + '?workspace=demo', json=body)
    assert first.status_code == 200, first.text
    assert client.post(SCREENS + '?workspace=demo', json=body).json() == first.json()
    assert client.post(SCREENS + '?workspace=demo', json=body | dict(name='Другое')).status_code == 409
    s = first.json()
    assert s['version'] == 1 and s['status'] == 'active' and s['link']['status'] == 'none' and s['lastSeen'] is None
    assert len(client.get('/api/hall/options?workspace=demo').json()['screens']) == 1
    assert client.post(SCREENS + '?workspace=demo', json=new_screen_body(buildingId='nope')).status_code == 422


def test_screen_edits_are_versioned(client):
    enable(client)
    s = screen(client)
    edit = dict(name='Столовая', buildingId=None, teacherNames='hidden', tomorrowFrom=None, pageSeconds=20)
    path = f"{SCREENS}/{s['id']}?workspace=demo"
    assert client.put(path, json=edit | dict(version=s['version'] + 1)).status_code == 409
    assert client.put(path, json=edit | dict(version=0)).status_code == 422
    assert client.put(path, json=edit | dict(version=1, buildingId='nope')).status_code == 422
    assert client.put(f"{SCREENS}/unknown?workspace=demo", json=edit | dict(version=1)).status_code == 404
    changed = client.put(path, json=edit | dict(version=1))
    assert changed.status_code == 200 and changed.json()['version'] == 2 and changed.json()['name'] == 'Столовая' and changed.json()['tomorrowFrom'] is None
    assert client.put(path, json=edit | dict(version=1)).status_code == 409


def test_screen_limit_is_lifted_by_archiving_a_screen(client):
    enable(client)
    first = screen(client, name='Первый')
    for n in range(hall_screen.MAX_SCREENS - 1): screen(client, name=f'Экран {n}')
    assert client.post(SCREENS + '?workspace=demo', json=new_screen_body(name='Лишний')).status_code == 422
    assert client.post(f"{SCREENS}/{first['id']}/archive?workspace=demo", json=dict(version=first['version'], reason='Телевизор снят')).status_code == 200
    assert client.post(SCREENS + '?workspace=demo', json=new_screen_body(name='Новый')).status_code == 200


def test_screen_archive_needs_a_reason_and_closes_the_screen(client):
    enable(client)
    s = screen(client)
    archive = f"{SCREENS}/{s['id']}/archive?workspace=demo"
    assert client.post(archive, json=dict(version=1)).status_code == 422
    assert client.post(archive, json=dict(version=1, reason='   ')).status_code == 422
    done = client.post(archive, json=dict(version=1, reason='Телевизор снят'))
    assert done.status_code == 200 and done.json()['status'] == 'archived'
    edit = dict(name='Столовая', buildingId=None, teacherNames='hidden', tomorrowFrom=None, pageSeconds=20)
    assert client.put(f"{SCREENS}/{s['id']}?workspace=demo", json=edit | dict(version=2)).status_code == 409
    assert client.post(f"{SCREENS}/{s['id']}/link?workspace=demo", json=dict(version=2)).status_code == 409


def issued_link(client, monkeypatch, iso='2026-11-02T04:00:00+00:00'):
    enable(client)
    s = screen(client)
    at(monkeypatch, iso)
    token, s = link(client, s)
    return token, s


def test_link_is_issued_and_a_new_one_retires_the_old(client, monkeypatch):
    first, s = issued_link(client, monkeypatch)
    assert s['link']['status'] == 'active' and s['link']['expires'] is None and s['version'] == 2
    assert display(first).status_code == 200
    second, s = link(client, s)
    assert second != first and display(first).status_code == 404 and display(second).status_code == 200
    # The old version cannot issue another link: a retry never silently doubles a link.
    assert client.post(f"{SCREENS}/{s['id']}/link?workspace=demo", json=dict(version=s['version'] - 1)).status_code == 409


def test_link_revoke(client, monkeypatch):
    token, s = issued_link(client, monkeypatch)
    revoke = f"{SCREENS}/{s['id']}/revoke?workspace=demo"
    assert client.post(revoke, json=dict(version=s['version'] - 1)).status_code == 409
    revoked = client.post(revoke, json=dict(version=s['version']))
    assert revoked.status_code == 200 and revoked.json()['link']['status'] == 'none' and display(token).status_code == 404
    assert client.post(revoke, json=dict(version=revoked.json()['version'])).status_code == 409


def test_link_expires_at_the_end_of_its_day_by_school_time(client, monkeypatch):
    _, s = issued_link(client, monkeypatch)
    for wrong in [date(2026, 11, 1), date(2099, 1, 1)]:
        assert client.post(f"{SCREENS}/{s['id']}/link?workspace=demo", json=dict(version=s['version'], expiresOn=wrong.isoformat())).status_code == 422
    dated, s = link(client, s, expiresOn='2026-11-02')
    assert s['link']['expiresOn'] == '2026-11-02'
    at(monkeypatch, '2026-11-02T18:59:00+00:00')
    assert display(dated).status_code == 200
    at(monkeypatch, '2026-11-02T19:00:00+00:00')
    assert display(dated).status_code == 404
    assert client.get('/api/hall/options?workspace=demo').json()['screens'][0]['link']['status'] == 'expired'


def test_bad_tokens_are_all_alike_not_found(client, monkeypatch):
    token, s = issued_link(client, monkeypatch)
    revoked = client.post(f"{SCREENS}/{s['id']}/revoke?workspace=demo", json=dict(version=s['version'])).json()
    answers = [display(t) for t in ['short', 'x' * 101, 'B' * 43, 'bad token with spaces ' * 2, token]]
    answers.append(TestClient(api.app).get('/hall-api/display'))
    assert revoked['link']['status'] == 'none'
    assert {r.status_code for r in answers} == {404} and len({r.text for r in answers}) == 1
    assert answers[0].headers['cache-control'] == 'no-store'


def test_the_raw_token_is_not_stored(client, monkeypatch):
    token, _ = issued_link(client, monkeypatch)
    with api.connect() as db:
        assert token not in json.dumps([list(r) for r in db.execute('SELECT * FROM hall_screens')])


def test_display_is_open_to_a_tv_even_when_auth_is_required_and_archiving_ends_it(client, monkeypatch):
    token, s = issued_link(client, monkeypatch)
    monkeypatch.setenv('TIMETABLE_REQUIRE_AUTH', '1')
    assert display(token).status_code == 200
    monkeypatch.delenv('TIMETABLE_REQUIRE_AUTH')
    shown = client.get('/api/hall/options?workspace=demo').json()['screens'][0]
    assert shown['link']['status'] == 'active' and shown['lastSeen'] == '2026-11-02T04:00:00+00:00'
    archived = client.post(f"{SCREENS}/{s['id']}/archive?workspace=demo", json=dict(version=shown['version'], reason='Снят'))
    assert archived.status_code == 200 and archived.json()['link']['status'] == 'none'
    assert display(token).status_code == 404


def test_payload_contains_only_allowed_fields(client, monkeypatch):
    s, token = ready(client, monkeypatch)
    response = display(token)
    data = response.json()
    assert set(data) == {'school', 'screen', 'serverTime', 'utcOffsetMinutes', 'pageSeconds', 'refreshSeconds', 'days', 'announcements'}
    assert data['screen'] == 'Холл' and data['utcOffsetMinutes'] == 300 and data['announcements'] == []
    assert [set(d) for d in data['days']] == [{'date', 'label', 'items', 'more'}] and data['days'][0]['date'] == '2026-11-02'
    found = items(response)
    assert {i['kind'] for i in found} == {'substitution', 'moved_out'}
    allowed = {'kind', 'period', 'start', 'end', 'classes', 'subject', 'previousSubject', 'absent', 'teachers', 'rooms', 'previousRooms', 'movedTo', 'movedFrom'}
    for item in found: assert set(item) <= allowed
    for word in FORBIDDEN: assert word not in response.text, word
    sub = items(response, kind='substitution')[0]
    assert (sub['absent'], sub['teachers'], sub['rooms'], sub['previousRooms'], sub['period']) == ('Учитель английского', 'Учитель истории', '303', '301', '1')
    assert (sub['start'], sub['end'], sub['classes'], sub['subject']) == ('08:20', '09:00', '10A', 'Английский язык')
    moved = items(response, kind='moved_out')[0]
    assert moved['movedTo'] == '2026-11-03' and moved['classes'] == '10A, 10D' and moved['subject'] == 'Математика'
    # The administrator's preview is the same payload without a token.
    preview = client.get(f"{SCREENS}/{s['id']}/preview?workspace=demo")
    assert preview.status_code == 200 and preview.json()['days'] == data['days']


def test_teacher_name_modes(client, monkeypatch):
    enable(client); setup_changes()
    short, hidden, full = (link(client, screen(client, name=n, teacherNames=m))[0] for n, m in [('Короткий', 'short'), ('Скрытый', 'hidden'), ('Полный', 'full')])
    at(monkeypatch, '2026-11-02T02:00:00+00:00')
    sub = items(display(short), kind='substitution')[0]
    assert sub['absent'] == 'Учитель А.' and sub['teachers'] == 'Учитель И.'
    response = display(hidden)
    sub = items(response, kind='substitution')[0]
    assert 'absent' not in sub and 'teachers' not in sub and sub['classes'] == '10A'
    assert 'Учитель' not in response.text and 'английского' not in response.text
    assert items(display(full), kind='substitution')[0]['absent'] == 'Учитель английского'


def test_today_vs_tomorrow_switching(client, monkeypatch):
    enable(client); setup_changes()
    token, _ = link(client, screen(client, tomorrowFrom=14))
    never, _ = link(client, screen(client, name='Только сегодня', tomorrowFrom=None))
    at(monkeypatch, '2026-11-02T08:59:00+00:00')
    assert len(display(token).json()['days']) == 1
    at(monkeypatch, '2026-11-02T09:00:00+00:00')
    days = display(token).json()['days']
    assert [d['date'] for d in days] == ['2026-11-02', '2026-11-03']
    moved = [i for i in days[1]['items'] if i['kind'] == 'moved_in']
    assert len(moved) == 1 and moved[0]['movedFrom'] == '2026-11-02' and moved[0]['period'] == '' and moved[0]['start'] == '09:45'
    # Tomorrow's list is not cut by the clock; today's is.
    at(monkeypatch, '2026-11-02T18:00:00+00:00')
    days = display(token).json()['days']
    assert days[0]['items'] == [] and len(days[1]['items']) == 1
    assert len(display(never).json()['days']) == 1
    at(monkeypatch, '2026-11-06T10:00:00+00:00')
    days = display(token).json()['days']
    assert days[1]['date'] == '2026-11-09'
    # A weekend: nothing today, Monday next.
    at(monkeypatch, '2026-11-07T10:00:00+00:00')
    days = display(token).json()['days']
    assert days[0]['items'] == [] and days[1]['date'] == '2026-11-09'


def test_finished_lessons_drop_and_closures(client, monkeypatch):
    s, token = ready(client, monkeypatch, iso='2026-11-02T04:10:00+00:00', extra=True)
    response = display(token)
    kinds = sorted(i['kind'] for i in items(response))
    # The 08:20 substitution has ended; the 09:45 move and the 10D closure remain.
    assert kinds == ['closed', 'moved_out']
    closed = items(response, kind='closed')[0]
    assert closed['classes'] == '10D' and closed['start'] == '10:30' and closed['end'] == '11:10' and closed['subject'] == '' and 'rooms' in closed
    assert 'Выезд' not in response.text and 'trip' not in response.text
    at(monkeypatch, '2026-11-02T06:30:00+00:00')
    assert items(display(token)) == []
    at(monkeypatch, '2026-11-02T02:00:00+00:00')
    assert sorted(i['kind'] for i in items(display(token))) == ['closed', 'moved_out', 'substitution']


def test_other_kinds_and_teacher_variants(client, monkeypatch):
    enable(client)
    s = api.read('demo')
    s.workloads[0].additionalTeacherIds = ['t1']
    s.placements += [Placement(id='eng3', workloadId='w0', day=0, period=3, roomId='r0'), Placement(id='eng-d', workloadId='w1', day=0, period=4, roomId='r1')]
    s.datedLessons = [
        DatedLesson(id='drop', name='Один ведёт', kind='change', placementId='eng', sourceDate=MON, onDate=MON, teacherIds=['t1']),
        DatedLesson(id='extra', name='Третий', kind='change', placementId='eng3', sourceDate=MON, onDate=MON, teacherIds=['t2', 't1', 't3']),
        DatedLesson(id='time', name='Позже', kind='change', placementId='math', sourceDate=MON, onDate=MON, startTime='10:00', endTime='10:40'),
        DatedLesson(id='room', name='Кабинет', kind='change', placementId='eng-d', sourceDate=MON, onDate=MON, roomIds=['r2']),
        DatedLesson(id='added', name='Факультатив', kind='add', onDate=MON, groupId='g-a', profileId='secondary', subjectId='sub1', startTime='14:00', endTime='14:40', teacherIds=['t1'], roomIds=['r1'])]
    api.save('demo', s)
    token, _ = link(client, screen(client, tomorrowFrom=None))
    at(monkeypatch, '2026-11-02T02:00:00+00:00')
    found = {i['kind'] + ':' + i['start']: i for i in items(display(token))}
    assert sorted(found) == ['added:14:00', 'room:11:20', 'substitution:08:20', 'substitution:10:30', 'time:10:00']
    drop = found['substitution:08:20']
    assert drop['absent'] == 'Учитель английского' and drop['teachers'] == ''
    extra = found['substitution:10:30']
    assert extra['absent'] == '' and extra['teachers'] == 'Учитель истории'
    assert found['time:10:00']['subject'] == 'Математика' and found['time:10:00']['period'] == ''
    assert found['room:11:20']['previousRooms'] == '302' and found['room:11:20']['rooms'] == '303' and found['room:11:20']['classes'] == '10D'
    added = found['added:14:00']
    assert added['subject'] == 'Физика' and added['teachers'] == 'Учитель физики' and added['rooms'] == '302' and added['period'] == '' and added['classes'] == '10A'
    s = api.read('demo')
    s.datedLessons = [DatedLesson(id='topic', name='Предмет', kind='change', placementId='eng', sourceDate=MON, onDate=MON, subjectId='sub3')]
    api.save('demo', s)
    topic = items(display(token))
    assert [i['kind'] for i in topic] == ['subject'] and topic[0]['subject'] == 'История' and topic[0]['previousSubject'] == 'Английский язык'


def test_partial_closure_and_substitution_both_show(client, monkeypatch):
    enable(client)
    s = api.read('demo')
    s.datedLessons = [DatedLesson(id='sub', name='Замена', kind='change', placementId='math', sourceDate=MON, onDate=MON, teacherIds=['t3'])]
    s.events = [Event(id='trip', name='Выезд 10D', start=MON, end=MON, kind='extra', effect='cancel', classIds=['c-demo-d'])]
    api.save('demo', s)
    token, _ = link(client, screen(client, tomorrowFrom=None))
    at(monkeypatch, '2026-11-02T02:00:00+00:00')
    found = {i['kind']: i for i in items(display(token))}
    assert sorted(found) == ['closed', 'substitution']
    assert found['closed']['classes'] == '10D' and found['substitution']['classes'] == '10A, 10D'


def test_a_day_with_too_many_items_is_capped_and_counts_the_rest(client, monkeypatch):
    enable(client)
    s = api.read('demo')
    s.classes += [SchoolClass(id=f'c{n}', name=f'5{"ABCDEF"[n]}') for n in range(6)]
    s.groups.append(Group(id='g6', name='Параллель', classIds=[f'c{n}' for n in range(6)], kind='whole'))
    s.workloads.append(Workload(id='w6', groupId='g6', subjectId='sub0', teacherId='t0', hours=1, profileId='secondary'))
    s.placements += [Placement(id=f'big{n}', workloadId='w6', day=0, period=n + 4, roomId='r1') for n in range(3)]
    s.datedLessons = [DatedLesson(id=f'cut{n}', name='Отмена', kind='cancel', placementId=f'big{n}', sourceDate=MON, onDate=MON) for n in range(3)]
    api.save('demo', s)
    token, _ = link(client, screen(client, tomorrowFrom=None))
    at(monkeypatch, '2026-11-02T02:00:00+00:00')
    total = len(items(display(token)))
    assert total == 3
    monkeypatch.setattr(hall_screen, 'MAX_ITEMS', total - 1)
    api.hall_screen.cache.clear()
    day = display(token).json()['days'][0]
    assert len(day['items']) == total - 1 and day['more'] == 1


def test_school_timezone(client, monkeypatch):
    s, token = ready(client, monkeypatch, iso='2026-11-01T19:30:00+00:00')
    day = display(token).json()['days'][0]
    assert day['date'] == '2026-11-02' and len(day['items']) == 2
    assert display(token).json()['utcOffsetMinutes'] == 300
    at(monkeypatch, '2026-11-01T18:59:00+00:00')
    day = display(token).json()['days'][0]
    assert day['date'] == '2026-11-01' and day['items'] == []


def test_building_filter_and_server_cache(client, monkeypatch):
    enable(client); setup_changes(buildings=True)
    second, _ = link(client, screen(client, name='Корпус 2', buildingId='b2', tomorrowFrom=None))
    first, _ = link(client, screen(client, name='Корпус 1', buildingId='b1', tomorrowFrom=None))
    every, _ = link(client, screen(client, name='Все', tomorrowFrom=None))
    at(monkeypatch, '2026-11-02T02:00:00+00:00')
    # The substitution touches both buildings (303 in b2, 301 in b1); the move is only in b1.
    assert [i['kind'] for i in items(display(second))] == ['substitution']
    assert sorted(i['kind'] for i in items(display(first))) == ['moved_out', 'substitution']
    assert len(items(display(every))) == 2
    # A building removed from the school after the screen was saved no longer filters.
    state = api.read('demo'); state.campus.buildings = [b for b in state.campus.buildings if b.id != 'b2']
    for room in state.rooms: room.buildingId = None if room.id == 'r2' else room.buildingId
    api.save('demo', state)
    assert len(items(display(second))) == 2
    options = client.get('/api/hall/options?workspace=demo').json()
    assert [s['buildingMissing'] for s in options['screens'] if s['name'] == 'Корпус 2'] == [True]
    assert options['buildings'] == [{'id': 'b1', 'name': 'Корпус 1'}]


def test_building_filter_keeps_closures_and_lessons_without_a_building(client, monkeypatch):
    enable(client); setup_changes(buildings=True, extra=True)
    state = api.read('demo')
    for room in state.rooms:
        if room.id in ('r0', 'r2'): room.buildingId = None
    api.save('demo', state)
    one, two, every = (link(client, screen(client, name=name, buildingId=building, tomorrowFrom=None))[0] for name, building in [('Корпус 1', 'b1'), ('Корпус 2', 'b2'), ('Все', None)])
    at(monkeypatch, '2026-11-02T02:00:00+00:00')
    kinds = lambda token: sorted(i['kind'] for i in items(display(token)))
    # The closure belongs to no room and the substitution's rooms have no building, so both show on every screen; the move is only in building 1.
    assert kinds(one) == ['closed', 'moved_out', 'substitution']
    assert kinds(two) == ['closed', 'substitution']
    assert kinds(every) == ['closed', 'moved_out', 'substitution']
    assert items(display(two), kind='closed')[0]['classes'] == '10D'


def test_etag_and_cache_headers(client, monkeypatch):
    s, token = ready(client, monkeypatch)
    response = display(token)
    etag = response.headers['etag']
    directives = lambda name: {d.strip() for d in response.headers[name].lower().split(',')}
    assert etag.startswith('"') and {'private', 'no-cache'} <= directives('cache-control') and 'x-hall-token' in directives('vary')
    assert response.headers['x-atlas-server-time'].startswith('2026-11-02T02:00:00') and response.headers['referrer-policy'] == 'no-referrer'
    assert 'noindex' in directives('x-robots-tag') and response.headers['x-content-type-options'] == 'nosniff'
    cached = display(token, **{'If-None-Match': etag})
    assert cached.status_code == 304 and cached.content == b'' and cached.headers['etag'] == etag and 'x-atlas-server-time' in cached.headers
    # The clock alone does not change the tag; a new change does.
    at(monkeypatch, '2026-11-02T02:00:30+00:00')
    assert display(token).headers['etag'] == etag
    state = api.read('demo')
    state.datedLessons.append(DatedLesson(id='cx', name='Отмена', kind='cancel', placementId='math', sourceDate=MON, onDate=MON))
    state.datedLessons = [d for d in state.datedLessons if d.id != 'mv']
    api.save('demo', state)
    changed = display(token, **{'If-None-Match': etag})
    assert changed.status_code == 200 and changed.headers['etag'] != etag
    assert [i['kind'] for i in items(changed)] == ['substitution', 'cancelled']


@pytest.fixture
def limiter_clock(monkeypatch):
    """Fake clock of the public limiter; moving it a long way forgets every earlier request."""
    moment = [1000.0]
    monkeypatch.setattr(api.hall_screen.limiter, 'clock', lambda: moment[0])
    return moment


def first_refusal(token, bound=1000):
    for _ in range(bound):
        response = display(token)
        if response.status_code == 429: return response
        assert response.status_code in (200, 404)
    raise AssertionError('the link was never limited')


def test_a_link_polled_too_often_is_limited_for_a_while(client, monkeypatch, limiter_clock):
    s, token = ready(client, monkeypatch)
    other, _ = link(client, screen(client, name='Второй экран'))
    limited = first_refusal(token)
    assert int(limited.headers['retry-after']) > 0 and token not in limited.text
    # The limit belongs to the link: another TV on the same address is served.
    assert display(other).status_code == 200
    limiter_clock[0] += 3600
    assert display(token).status_code == 200


def test_unknown_tokens_are_limited_per_address_without_holding_back_valid_links(client, monkeypatch, limiter_clock):
    s, token = ready(client, monkeypatch)
    other, _ = link(client, screen(client, name='Второй экран'))
    refused = first_refusal('C' * 43)
    assert int(refused.headers['retry-after']) > 0 and display('short').status_code == 429
    assert display(token).status_code == 200 and display(other).status_code == 200
    limiter_clock[0] += 3600
    assert display('C' * 43).status_code == 404


def test_dead_link_never_holds_back_other_screens(client, monkeypatch):
    """A TV left on a revoked link keeps asking; every TV of the school shares one address."""
    enable(client); setup_changes()
    live, _ = link(client, screen(client, name='Живой'))
    dead, old = link(client, screen(client, name='Отозванный'))
    assert client.post(f"{SCREENS}/{old['id']}/revoke?workspace=demo", json=dict(version=old['version'])).status_code == 200
    at(monkeypatch, '2026-11-02T02:00:00+00:00')
    moment = [1000.0]
    monkeypatch.setattr(api.hall_screen.limiter, 'clock', lambda: moment[0])
    codes = []
    for tick in range(180):
        moment[0] += 20
        codes.append(display(dead).status_code)
        if tick % 3 == 0: assert display(live).status_code == 200, tick
    # The dead link is only ever told that it is gone or to wait; it never gets in and never costs the live link its turn.
    assert codes[0] == 404 and set(codes) <= {404, 429}
    # Slow polling, as the TV does after a 404, never reaches the limit at all.
    api.hall_screen.limiter.reset()
    codes = []
    for minute in range(60):
        moment[0] += 600
        codes.append(display(dead).status_code)
    assert set(codes) == {404}


def two_screens(client):
    a, b = screen(client, name='A', tomorrowFrom=None), screen(client, name='B', tomorrowFrom=None)
    return a, b, link(client, a)[0], link(client, b)[0]


MEETING = dict(title='Собрание', text='Актовый зал,\nвход свободный', startsAt='2026-11-02T09:00', endsAt='2026-11-02T12:00', visible=True, screenIds=[])


def note_state(client, note):
    return next(n for n in client.get('/api/hall/options?workspace=demo').json()['announcements'] if n['id'] == note['id'])['state']


def test_announcement_goes_from_scheduled_to_showing_to_ended_by_the_clock(client, monkeypatch):
    enable(client); setup_changes()
    a, b, ta, tb = two_screens(client)
    at(monkeypatch, '2026-11-02T03:00:00+00:00')
    body = dict(requestId=str(uuid4())) | MEETING
    created = client.post('/api/hall/announcements?workspace=demo', json=body)
    assert created.status_code == 200, created.text
    note = created.json()
    assert client.post('/api/hall/announcements?workspace=demo', json=body).json() == note
    assert note['state'] == 'scheduled' and note['startsAt'] == '2026-11-02T04:00:00+00:00' and note['author'] == 'Администратор'
    assert display(ta).json()['announcements'] == []
    at(monkeypatch, '2026-11-02T04:00:00+00:00')
    shown = display(ta)
    assert shown.json()['announcements'] == [{'title': 'Собрание', 'text': 'Актовый зал,\nвход свободный'}]
    assert 'Администратор' not in shown.text and note['id'] not in shown.text
    assert note_state(client, note) == 'showing'
    at(monkeypatch, '2026-11-02T06:59:59+00:00')
    assert len(display(ta).json()['announcements']) == 1
    at(monkeypatch, '2026-11-02T07:00:00+00:00')
    assert display(ta).json()['announcements'] == [] and note_state(client, note) == 'ended'


def test_announcement_can_be_hidden_limited_to_screens_and_withdrawn(client, monkeypatch):
    enable(client); setup_changes()
    a, b, ta, tb = two_screens(client)
    at(monkeypatch, '2026-11-02T05:00:00+00:00')
    note = client.post('/api/hall/announcements?workspace=demo', json=dict(requestId=str(uuid4())) | MEETING | dict(text='Актовый зал')).json()
    edit = dict(title='Собрание', text='Актовый зал', startsAt='2026-11-02T09:00', endsAt='2026-11-02T12:00', visible=False, screenIds=[])
    path = f"/api/hall/announcements/{note['id']}?workspace=demo"
    assert client.put(path, json=edit | dict(version=note['version'] + 1)).status_code == 409
    hidden = client.put(path, json=edit | dict(version=note['version']))
    assert hidden.status_code == 200 and hidden.json()['state'] == 'hidden' and hidden.json()['version'] == note['version'] + 1
    assert display(ta).json()['announcements'] == []
    only_b = client.put(path, json=edit | dict(version=hidden.json()['version'], visible=True, screenIds=[b['id']]))
    assert only_b.status_code == 200 and only_b.json()['state'] == 'showing'
    assert display(ta).json()['announcements'] == [] and display(tb).json()['announcements'] == [{'title': 'Собрание', 'text': 'Актовый зал'}]
    version = only_b.json()['version']
    assert client.put(path, json=edit | dict(version=version, screenIds=['unknown'])).status_code == 422
    withdraw = f"/api/hall/announcements/{note['id']}/withdraw?workspace=demo"
    assert client.post(withdraw, json=dict(version=version)).status_code == 422
    done = client.post(withdraw, json=dict(version=version, reason='Собрание отменено'))
    assert done.status_code == 200 and done.json()['state'] == 'withdrawn' and done.json()['withdrawReason'] == 'Собрание отменено'
    assert display(tb).json()['announcements'] == []
    assert client.put(path, json=edit | dict(version=version + 1)).status_code == 409 and client.post(withdraw, json=dict(version=version + 1, reason='ещё')).status_code == 409


def length_limit(name):
    return next(m.max_length for m in hall_screen.AnnouncementData.model_fields[name].metadata if hasattr(m, 'max_length'))


def test_announcement_content_is_validated(client, monkeypatch):
    enable(client)
    a = screen(client, name='A', tomorrowFrom=None); b = screen(client, name='B', tomorrowFrom=None)
    at(monkeypatch, '2026-11-02T03:00:00+00:00')
    def post(**values): return client.post('/api/hall/announcements?workspace=demo', json=dict(requestId=str(uuid4())) | MEETING | values)
    # A window that has already ended or runs backwards is refused; screens are real and active.
    assert post(startsAt='2026-11-01T09:00', endsAt='2026-11-02T07:00').status_code == 422
    assert post(startsAt='2026-11-02T12:00', endsAt='2026-11-02T09:00').status_code == 422
    assert post(screenIds=['unknown']).status_code == 422
    client.post(f"{SCREENS}/{a['id']}/archive?workspace=demo", json=dict(version=a['version'], reason='Снят'))
    assert post(screenIds=[a['id']]).status_code == 422
    assert post(screenIds=[b['id']]).status_code == 200
    assert post(title='x' * (length_limit('title') + 1)).status_code == 422 and post(text='x' * (length_limit('text') + 1)).status_code == 422
    assert post(title='x' * length_limit('title'), text='x' * length_limit('text')).status_code == 200


def test_old_announcements_leave_the_list_after_a_while(client, monkeypatch):
    enable(client); setup_changes()
    at(monkeypatch, '2026-11-02T03:00:00+00:00')
    note = client.post('/api/hall/announcements?workspace=demo', json=dict(requestId=str(uuid4())) | MEETING).json()
    listed = lambda: {n['id'] for n in client.get('/api/hall/options?workspace=demo').json()['announcements']}
    at(monkeypatch, '2026-11-03T05:00:00+00:00')
    assert note['id'] in listed()
    at(monkeypatch, '2027-11-02T05:00:00+00:00')
    assert note['id'] not in listed()


def test_announcement_limit_and_order(client, monkeypatch):
    enable(client); setup_changes()
    token, _ = link(client, screen(client, tomorrowFrom=None))
    at(monkeypatch, '2026-11-02T04:00:00+00:00')
    moment = datetime.fromisoformat('2026-11-02T04:00:00+00:00')
    names = []
    # Add running announcements one by one until the screen stops showing more; the loop bound is generous.
    for n in range(150):
        put_note(f'Объявление {n}', moment - timedelta(hours=5) + timedelta(minutes=n), moment + timedelta(days=1))
        names.append(f'Объявление {n}')
        titles = [a['title'] for a in display(token).json()['announcements']]
        # Whatever is shown is the newest-first prefix of what was created.
        assert titles == names[::-1][:len(titles)]
        if len(titles) < len(names): break
    assert 0 < len(titles) < len(names)


def test_running_announcement_stays_listed_among_many_finished(client, monkeypatch):
    enable(client); setup_changes()
    token, _ = link(client, screen(client, tomorrowFrom=None))
    at(monkeypatch, '2026-09-22T03:00:00+00:00')
    long = client.post('/api/hall/announcements?workspace=demo', json=dict(requestId=str(uuid4()), title='Долгое', startsAt='2026-09-22T09:00', endsAt='2026-12-10T09:00'))
    assert long.status_code == 200, long.text
    at(monkeypatch, '2026-11-01T05:00:00+00:00')
    first = datetime.fromisoformat('2026-10-05T04:00:00+00:00')
    ended = lambda: [n for n in client.get('/api/hall/options?workspace=demo').json()['announcements'] if n['state'] == 'ended']
    # Add finished announcements, all later than the long one, until the list stops growing; the loop bound is generous.
    for n in range(150):
        put_note(f'old{n}', first + timedelta(hours=3 * n), first + timedelta(hours=3 * n + 2))
        if len(ended()) < n + 1: break
    assert 0 < len(ended()) < n + 1
    listed = client.get('/api/hall/options?workspace=demo').json()['announcements']
    # It started earlier than all the finished ones, yet it is listed (last, by start); only the finished ones are cut.
    assert [n['title'] for n in listed][-1] == 'Долгое' and listed[-1]['state'] == 'showing'
    assert display(token).json()['announcements'] == [{'title': 'Долгое', 'text': ''}]
    done = client.post(f"/api/hall/announcements/{long.json()['id']}/withdraw?workspace=demo", json=dict(version=1, reason='Ошибка'))
    assert done.status_code == 200 and done.json()['state'] == 'withdrawn'
    assert display(token).json()['announcements'] == []


def test_announcement_places_count_new_and_revived(client, monkeypatch):
    enable(client); screen(client)
    at(monkeypatch, '2026-11-02T03:00:00+00:00')
    moment = datetime.fromisoformat('2026-11-02T03:00:00+00:00')
    for n in range(hall_screen.MAX_ANNOUNCEMENTS): put_note(f'live{n}', moment - timedelta(hours=1), moment + timedelta(days=1))
    put_note('done', moment - timedelta(hours=3), moment - timedelta(hours=1))
    new = lambda: client.post('/api/hall/announcements?workspace=demo', json=dict(requestId=str(uuid4()), title='Лишнее', startsAt='2026-11-02T09:00', endsAt='2026-11-02T12:00'))
    assert new().status_code == 422
    path = '/api/hall/announcements/done?workspace=demo'
    edit = dict(version=1, title='Старое', startsAt=hall_screen.iso(moment - timedelta(hours=3)), endsAt=hall_screen.iso(moment - timedelta(hours=1)))
    # A finished one may be edited as long as it stays finished; bringing it back needs a place like a new one.
    assert client.put(path, json=edit | dict(title='Другое')).status_code == 200
    revived = edit | dict(version=2, endsAt=hall_screen.iso(moment + timedelta(hours=2)))
    assert client.put(path, json=revived).status_code == 422
    assert client.post('/api/hall/announcements/live0/withdraw?workspace=demo', json=dict(version=1, reason='Лишнее')).status_code == 200
    assert client.put(path, json=revived).status_code == 200
    assert new().status_code == 422


def test_history_and_audit(client, monkeypatch):
    admin = enable(client); second = create(client, 'admin', login='admin2', workspaces=['demo', 'school']); other = signed('admin2')
    s = screen(client)
    base = f"{SCREENS}/{s['id']}"
    changed = client.put(base + '?workspace=demo', json=dict(version=1, name='Новый холл', buildingId=None, teacherNames='full', tomorrowFrom=15, pageSeconds=15)).json()
    raw, s = link(client, changed)
    assert other.get('/api/notifications/inbox').json()['unread'] == 1 and client.get('/api/notifications/inbox').json()['unread'] == 0
    revoked = client.post(base + '/revoke?workspace=demo', json=dict(version=s['version'], reason='Утерян')).json()
    assert other.get('/api/notifications/inbox').json()['unread'] == 2
    assert client.post(base + '/archive?workspace=demo', json=dict(version=revoked['version'], reason='Закрыт')).status_code == 200
    history = client.get(f"/api/hall/history?workspace=demo&entity=screen&id={s['id']}").json()['items']
    assert [h['action'] for h in reversed(history)] == ['created', 'updated', 'link_issued', 'link_revoked', 'archived']
    assert history[0]['reason'] == 'Закрыт' and history[0]['actor'] == 'Администратор' and history[1]['reason'] == 'Утерян'
    with api.connect() as db:
        stored = json.dumps([list(r) for r in db.execute('SELECT * FROM hall_history')]) + json.dumps([list(r) for r in db.execute('SELECT * FROM audit')])
        actions = {r[0] for r in db.execute("SELECT action FROM audit WHERE action LIKE 'hall.%'")}
    assert raw not in stored and hashlib.sha256(raw.encode()).hexdigest() not in stored
    assert {'hall.created', 'hall.updated', 'hall.link_issued', 'hall.link_revoked', 'hall.archived'} <= actions
    note = client.post('/api/hall/announcements?workspace=demo', json=dict(requestId=str(uuid4()), title='Объявление', startsAt='2099-01-01T09:00', endsAt='2099-01-02T09:00'))
    assert note.status_code == 200
    assert [h['action'] for h in client.get(f"/api/hall/history?workspace=demo&entity=announcement&id={note.json()['id']}").json()['items']] == ['created']
    assert client.get('/api/hall/history?workspace=demo&entity=screen&id=nope').status_code == 404
    assert client.get(f"/api/hall/history?workspace=demo&entity=announcement&id={s['id']}").status_code == 404
    assert client.get(f"/api/hall/history?workspace=demo&entity=other&id={s['id']}").status_code == 422
