"""Hall TV screens: administrator-only management, tokenised public display, privacy whitelist, windows and limits."""
from datetime import date, datetime, timedelta
from time import monotonic
from uuid import uuid4
import hashlib
import json
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
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


def test_short_name_and_models():
    assert hall_screen.short_name('Иванов Алексей Борисович') == 'Иванов А. Б.'
    assert hall_screen.short_name('Иванов Алексей') == 'Иванов А.'
    assert hall_screen.short_name('Учитель') == 'Учитель'
    ok = hall_screen.AnnouncementData(title='Родительское собрание', startsAt='2026-11-02T09:00', endsAt='2026-11-02T12:00')
    assert ok.startsAt.utcoffset() == timedelta(hours=5) and ok.endsAt.utcoffset() == timedelta(hours=5)
    assert hall_screen.AnnouncementData(title='Т', startsAt='2026-11-02T09:00Z', endsAt='2026-11-02T10:00Z').startsAt.utcoffset() == timedelta(0)
    assert hall_screen.AnnouncementData(title='Т', startsAt='2026-11-02T09:00', endsAt='2027-02-02T09:00')
    for start, end, extra in [('2026-11-02T09:00', '2026-11-02T09:00', {}), ('2026-11-02T09:00', '2026-11-02T08:00', {}), ('2026-11-02T09:00', '2027-02-03T09:00', {}),
                              ('2026-11-02T09:00', '2026-11-02T10:00', dict(screenIds=['a', 'a']))]:
        with pytest.raises(ValidationError): hall_screen.AnnouncementData(title='Т', startsAt=start, endsAt=end, **extra)
    for values in [dict(name=''), dict(pageSeconds=7), dict(pageSeconds=61), dict(tomorrowFrom=24), dict(teacherNames='other')]:
        with pytest.raises(ValidationError): hall_screen.ScreenData(**({'name': 'Холл'} | values))
    with pytest.raises(ValidationError): hall_screen.ScreenData(name='Холл', secret='x')
    assert hall_screen.ScreenData(name='Холл', tomorrowFrom=None).tomorrowFrom is None


def test_tables_are_created_once_and_public_display_never_creates_them(client):
    assert display('A' * 43).status_code == 404
    with api.connect() as db:
        assert db.execute("SELECT 1 FROM sqlite_master WHERE name LIKE 'hall_%'").fetchone() is None
    enable(client)
    assert client.get('/api/hall/options?workspace=demo').status_code == 200
    with api.connect() as db:
        hall_screen.tables(db); hall_screen.tables(db)
        names = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE name LIKE 'hall_%'")}
    assert {'hall_screens', 'hall_announcements', 'hall_history', 'hall_requests', 'hall_screens_ws', 'hall_announcements_window', 'hall_history_entity'} <= names


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


def test_screen_create_replay_versions_limits(client):
    enable(client)
    body = dict(requestId=str(uuid4()), name='Главный холл', buildingId=None, teacherNames='short', tomorrowFrom=14, pageSeconds=15)
    first = client.post(SCREENS + '?workspace=demo', json=body)
    assert first.status_code == 200, first.text
    assert client.post(SCREENS + '?workspace=demo', json=body).json() == first.json()
    assert client.post(SCREENS + '?workspace=demo', json=body | dict(name='Другое')).status_code == 409
    s = first.json()
    assert s['version'] == 1 and s['status'] == 'active' and s['link']['status'] == 'none' and s['lastSeen'] is None
    assert len(client.get('/api/hall/options?workspace=demo').json()['screens']) == 1
    edit = dict(name='Столовая', buildingId=None, teacherNames='hidden', tomorrowFrom=None, pageSeconds=20)
    path = f"{SCREENS}/{s['id']}?workspace=demo"
    assert client.put(path, json=edit | dict(version=2)).status_code == 409
    assert client.put(path, json=edit | dict(version=0)).status_code == 422
    assert client.put(path, json=edit | dict(version=1, buildingId='nope')).status_code == 422
    assert client.put(f"{SCREENS}/unknown?workspace=demo", json=edit | dict(version=1)).status_code == 404
    changed = client.put(path, json=edit | dict(version=1))
    assert changed.status_code == 200 and changed.json()['version'] == 2 and changed.json()['name'] == 'Столовая' and changed.json()['tomorrowFrom'] is None
    assert client.put(path, json=edit | dict(version=1)).status_code == 409
    assert client.post(SCREENS + '?workspace=demo', json=body | dict(requestId=str(uuid4()), buildingId='nope')).status_code == 422
    for n in range(19): screen(client, name=f'Экран {n}')
    over = client.post(SCREENS + '?workspace=demo', json=body | dict(requestId=str(uuid4()), name='Лишний'))
    assert over.status_code == 422 and '20' in over.json()['detail']
    archive = f"{SCREENS}/{s['id']}/archive?workspace=demo"
    assert client.post(archive, json=dict(version=2)).status_code == 422
    assert client.post(archive, json=dict(version=2, reason='   ')).status_code == 422
    done = client.post(archive, json=dict(version=2, reason='Телевизор снят'))
    assert done.status_code == 200 and done.json()['status'] == 'archived'
    assert client.put(path, json=edit | dict(version=3)).status_code == 409
    assert client.post(f"{SCREENS}/{s['id']}/link?workspace=demo", json=dict(version=3)).status_code == 409
    # Removing a screen frees its place.
    assert client.post(SCREENS + '?workspace=demo', json=body | dict(requestId=str(uuid4()), name='Новый')).status_code == 200


def test_link_issue_rotate_revoke_expiry(client, monkeypatch):
    enable(client)
    s = screen(client)
    at(monkeypatch, '2026-11-02T04:00:00+00:00')
    first, s = link(client, s)
    assert len(first) >= 32 and s['link']['status'] == 'active' and s['link']['expires'] is None and s['version'] == 2
    with api.connect() as db:
        assert db.execute('SELECT token FROM hall_screens').fetchone()[0] == hashlib.sha256(first.encode()).hexdigest()
        assert first not in json.dumps(db.execute('SELECT * FROM hall_screens').fetchone())
    assert display(first).status_code == 200
    second, s = link(client, s)
    assert second != first and display(first).status_code == 404 and display(second).status_code == 200
    # The old version cannot issue another link: a retry never silently doubles a link.
    assert client.post(f"{SCREENS}/{s['id']}/link?workspace=demo", json=dict(version=2)).status_code == 409
    revoke = f"{SCREENS}/{s['id']}/revoke?workspace=demo"
    assert client.post(revoke, json=dict(version=s['version'] - 1)).status_code == 409
    revoked = client.post(revoke, json=dict(version=s['version']))
    assert revoked.status_code == 200 and revoked.json()['link']['status'] == 'none' and display(second).status_code == 404
    assert client.post(revoke, json=dict(version=revoked.json()['version'])).status_code == 409
    s = revoked.json()
    for wrong in [date(2026, 11, 1), date(2027, 11, 4)]:
        assert client.post(f"{SCREENS}/{s['id']}/link?workspace=demo", json=dict(version=s['version'], expiresOn=wrong.isoformat())).status_code == 422
    dated, s = link(client, s, expiresOn='2026-11-02')
    assert s['link']['expiresOn'] == '2026-11-02'
    at(monkeypatch, '2026-11-02T18:59:00+00:00')
    assert display(dated).status_code == 200
    at(monkeypatch, '2026-11-02T19:00:00+00:00')
    assert display(dated).status_code == 404
    api.hall_screen.limiter.reset()
    assert client.get('/api/hall/options?workspace=demo').json()['screens'][0]['link']['status'] == 'expired'
    at(monkeypatch, '2026-11-02T04:00:00+00:00')
    api.hall_screen.limiter.reset()
    token, s = link(client, s)
    assert display(token).status_code == 200
    # Malformed, unknown and missing tokens are indistinguishable from a revoked one.
    answers = [display(t) for t in ['short', 'x' * 101, 'B' * 43, 'bad token with spaces ' * 2, dated]]
    answers.append(TestClient(api.app).get('/hall-api/display'))
    assert {r.status_code for r in answers} == {404} and len({r.text for r in answers}) == 1
    assert answers[0].json() == {'detail': 'Экран недоступен.'} and answers[0].headers['cache-control'] == 'no-store'
    api.hall_screen.limiter.reset()
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
    assert data['screen'] == 'Холл' and data['utcOffsetMinutes'] == 300 and data['refreshSeconds'] == 60 and data['announcements'] == []
    assert [set(d) for d in data['days']] == [{'date', 'label', 'items', 'more'}] and data['days'][0]['label'] == 'Сегодня'
    found = items(response)
    assert {i['kind'] for i in found} == {'substitution', 'moved_out'}
    for item in found: assert set(item) <= hall_screen.ITEM_KEYS
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
    assert [d['date'] for d in days] == ['2026-11-02', '2026-11-03'] and days[1]['label'] == 'Завтра'
    moved = [i for i in days[1]['items'] if i['kind'] == 'moved_in']
    assert len(moved) == 1 and moved[0]['movedFrom'] == '2026-11-02' and moved[0]['period'] == '' and moved[0]['start'] == '09:45'
    # Tomorrow's list is not cut by the clock; today's is.
    at(monkeypatch, '2026-11-02T18:00:00+00:00')
    days = display(token).json()['days']
    assert days[0]['items'] == [] and len(days[1]['items']) == 1
    assert len(display(never).json()['days']) == 1
    at(monkeypatch, '2026-11-06T10:00:00+00:00')
    days = display(token).json()['days']
    assert days[1]['date'] == '2026-11-09' and days[1]['label'] == 'Понедельник'
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


def test_long_class_lists_are_shortened_and_days_are_capped(client, monkeypatch):
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
    assert {i['classes'] for i in items(display(token))} == {'5A, 5B, 5C, 5D +2'}
    monkeypatch.setattr(hall_screen, 'MAX_ITEMS', 2)
    api.hall_screen.cache.clear()
    day = display(token).json()['days'][0]
    assert len(day['items']) == 2 and day['more'] == 1


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
    calls = []
    real = hall_screen.day_items
    def counting(state, day, rows):
        calls.append(day); return real(state, day, rows)
    monkeypatch.setattr(hall_screen, 'day_items', counting)
    api.hall_screen.cache.clear()
    display(every); display(every); display(first)
    assert len(calls) == 1
    api.save('demo', api.read('demo'))
    display(every); display(every)
    assert len(calls) == 2


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
    assert etag.startswith('"') and response.headers['cache-control'] == 'private, no-cache' and response.headers['vary'] == 'X-Hall-Token'
    assert response.headers['x-atlas-server-time'].startswith('2026-11-02T02:00:00') and response.headers['referrer-policy'] == 'no-referrer'
    assert response.headers['x-robots-tag'] == 'noindex, nofollow' and response.headers['x-content-type-options'] == 'nosniff'
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


def test_rate_limit(client, monkeypatch):
    s, token = ready(client, monkeypatch)
    other, _ = link(client, screen(client, name='Второй экран'))
    http = TestClient(api.app)
    for n in range(30): assert http.get('/hall-api/display', headers={'X-Hall-Token': token}).status_code == 200, n
    limited = http.get('/hall-api/display', headers={'X-Hall-Token': token})
    assert limited.status_code == 429 and limited.headers['retry-after'] == '60' and token not in limited.text
    # The minute limit belongs to the link: another TV on the same address is served.
    assert display(other).status_code == 200
    api.hall_screen.limiter.reset()
    # Unknown tokens are counted per address. The eleventh is told to wait, and so is a malformed one...
    for n in range(10): assert display('C' * 43).status_code == 404
    refused = display('C' * 43)
    assert refused.status_code == 429 and refused.headers['retry-after'] == '60' and display('short').status_code == 429
    # ...but a valid link on the same address is not held back by them.
    assert display(token).status_code == 200 and display(other).status_code == 200
    monkeypatch.setattr(api.hall_screen.limiter, 'clock', lambda: monotonic() + 601)
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
    # The dead link is told 404 ten times, then to wait; it never gets in and never costs the live link its turn.
    assert codes[:10] == [404] * 10 and set(codes) == {404, 429}
    # Slow polling, as the TV does after a 404, never reaches the limit at all.
    api.hall_screen.limiter.reset()
    codes = []
    for minute in range(60):
        moment[0] += 600
        codes.append(display(dead).status_code)
    assert set(codes) == {404}


def test_announcement_windows(client, monkeypatch):
    enable(client); setup_changes()
    a, b = screen(client, name='A', tomorrowFrom=None), screen(client, name='B', tomorrowFrom=None)
    ta, _ = link(client, a); tb, _ = link(client, b)
    at(monkeypatch, '2026-11-02T03:00:00+00:00')
    body = dict(requestId=str(uuid4()), title='Собрание', text='Актовый зал,\nвход свободный', startsAt='2026-11-02T09:00', endsAt='2026-11-02T12:00', visible=True, screenIds=[])
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
    state = lambda: next(n for n in client.get('/api/hall/options?workspace=demo').json()['announcements'] if n['id'] == note['id'])
    assert state()['state'] == 'showing'
    at(monkeypatch, '2026-11-02T06:59:59+00:00')
    assert len(display(ta).json()['announcements']) == 1
    at(monkeypatch, '2026-11-02T07:00:00+00:00')
    assert display(ta).json()['announcements'] == [] and state()['state'] == 'ended'
    # Edits: hidden for the window, shown on chosen screens only, withdrawn.
    at(monkeypatch, '2026-11-02T05:00:00+00:00')
    edit = dict(title='Собрание', text='Актовый зал', startsAt='2026-11-02T09:00', endsAt='2026-11-02T12:00', visible=False, screenIds=[])
    path = f"/api/hall/announcements/{note['id']}?workspace=demo"
    assert client.put(path, json=edit | dict(version=5)).status_code == 409
    hidden = client.put(path, json=edit | dict(version=note['version']))
    assert hidden.status_code == 200 and hidden.json()['state'] == 'hidden' and hidden.json()['version'] == 2
    assert display(ta).json()['announcements'] == []
    only_b = client.put(path, json=edit | dict(version=2, visible=True, screenIds=[b['id']]))
    assert only_b.status_code == 200 and only_b.json()['state'] == 'showing'
    assert display(ta).json()['announcements'] == [] and display(tb).json()['announcements'] == [{'title': 'Собрание', 'text': 'Актовый зал'}]
    assert client.put(path, json=edit | dict(version=3, screenIds=['unknown'])).status_code == 422
    withdraw = f"/api/hall/announcements/{note['id']}/withdraw?workspace=demo"
    assert client.post(withdraw, json=dict(version=3)).status_code == 422
    done = client.post(withdraw, json=dict(version=3, reason='Собрание отменено'))
    assert done.status_code == 200 and done.json()['state'] == 'withdrawn' and done.json()['withdrawReason'] == 'Собрание отменено'
    assert display(tb).json()['announcements'] == []
    assert client.put(path, json=edit | dict(version=4)).status_code == 409 and client.post(withdraw, json=dict(version=4, reason='ещё')).status_code == 409
    # Validation: the end is in the future and after the start; screens are real and active.
    def post(**values): return client.post('/api/hall/announcements?workspace=demo', json=body | dict(requestId=str(uuid4())) | values)
    assert post(startsAt='2026-11-01T09:00', endsAt='2026-11-02T09:00').status_code == 422
    assert post(startsAt='2026-11-02T12:00', endsAt='2026-11-02T09:00').status_code == 422
    assert post(screenIds=['unknown']).status_code == 422
    client.post(f"{SCREENS}/{a['id']}/archive?workspace=demo", json=dict(version=a['version'] + 1, reason='Снят'))
    assert post(screenIds=[a['id']]).status_code == 422
    assert post(screenIds=[b['id']]).status_code == 200
    assert post(title='x' * 121).status_code == 422 and post(text='x' * 601).status_code == 422
    # Old announcements stay in the list for thirty days only.
    at(monkeypatch, '2026-12-05T05:00:00+00:00')
    assert note['id'] not in {n['id'] for n in client.get('/api/hall/options?workspace=demo').json()['announcements']}


def test_announcement_limit_and_order(client, monkeypatch):
    enable(client); setup_changes()
    token, _ = link(client, screen(client, tomorrowFrom=None))
    at(monkeypatch, '2026-11-02T03:00:00+00:00')
    def post(n, **values):
        return client.post('/api/hall/announcements?workspace=demo', json=dict(requestId=str(uuid4()), title=f'Объявление {n}', startsAt='2026-11-02T08:00', endsAt='2026-11-02T20:00') | values)
    for n in range(12):
        at(monkeypatch, '2026-11-02T03:00:00+00:00')
        assert post(n, startsAt=f'2026-11-02T08:{n:02d}').status_code == 200
    at(monkeypatch, '2026-11-02T04:00:00+00:00')
    titles = [a['title'] for a in display(token).json()['announcements']]
    assert len(titles) == 10 and titles[0] == 'Объявление 11' and titles[-1] == 'Объявление 2'


def test_running_announcement_stays_listed_among_many_finished(client, monkeypatch):
    enable(client); setup_changes()
    token, _ = link(client, screen(client, tomorrowFrom=None))
    at(monkeypatch, '2026-09-22T03:00:00+00:00')
    long = client.post('/api/hall/announcements?workspace=demo', json=dict(requestId=str(uuid4()), title='Долгое', startsAt='2026-09-22T09:00', endsAt='2026-12-10T09:00'))
    assert long.status_code == 200, long.text
    at(monkeypatch, '2026-11-01T05:00:00+00:00')
    first = datetime.fromisoformat('2026-10-05T04:00:00+00:00')
    for n in range(120): put_note(f'old{n}', first + timedelta(hours=5 * n), first + timedelta(hours=5 * n + 2))
    listed = client.get('/api/hall/options?workspace=demo').json()['announcements']
    # It started earlier than all 120 finished ones, yet it is listed (last, by start); only the finished ones are cut.
    assert [n['title'] for n in listed][-1] == 'Долгое' and listed[-1]['state'] == 'showing'
    assert len(listed) == 101 and [n['state'] for n in listed].count('ended') == 100
    assert display(token).json()['announcements'] == [{'title': 'Долгое', 'text': ''}]
    done = client.post(f"/api/hall/announcements/{long.json()['id']}/withdraw?workspace=demo", json=dict(version=1, reason='Ошибка'))
    assert done.status_code == 200 and done.json()['state'] == 'withdrawn'
    assert display(token).json()['announcements'] == []


def test_announcement_places_count_new_and_revived(client, monkeypatch):
    enable(client); screen(client)
    at(monkeypatch, '2026-11-02T03:00:00+00:00')
    moment = datetime.fromisoformat('2026-11-02T03:00:00+00:00')
    for n in range(100): put_note(f'live{n}', moment - timedelta(hours=1), moment + timedelta(days=1))
    put_note('done', moment - timedelta(hours=3), moment - timedelta(hours=1))
    over = client.post('/api/hall/announcements?workspace=demo', json=dict(requestId=str(uuid4()), title='Лишнее', startsAt='2026-11-02T09:00', endsAt='2026-11-02T12:00'))
    assert over.status_code == 422 and '100' in over.json()['detail']
    path = '/api/hall/announcements/done?workspace=demo'
    edit = dict(version=1, title='Старое', startsAt=hall_screen.iso(moment - timedelta(hours=3)), endsAt=hall_screen.iso(moment - timedelta(hours=1)))
    # A finished one may be edited as long as it stays finished; bringing it back needs a place like a new one.
    assert client.put(path, json=edit | dict(title='Другое')).status_code == 200
    revived = edit | dict(version=2, endsAt=hall_screen.iso(moment + timedelta(hours=2)))
    refused = client.put(path, json=revived)
    assert refused.status_code == 422 and '100' in refused.json()['detail']
    with api.connect() as db: db.execute("UPDATE hall_announcements SET status='withdrawn' WHERE id='live0'")
    assert client.put(path, json=revived).status_code == 200
    assert client.post('/api/hall/announcements?workspace=demo', json=dict(requestId=str(uuid4()), title='Лишнее', startsAt='2026-11-02T09:00', endsAt='2026-11-02T12:00')).status_code == 422


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
