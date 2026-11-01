from datetime import date
import pytest
from backend import absence_feed, api, staff_time, staff_time_routes
from backend.models import TeacherAbsence
from test_access import client, enable, create, signed
from test_staff_time import leave, approve_leave, action, ok, path

LEAVE_DAY = '2026-08-17'
TODAY = date(2026, 8, 10)
NOTE, CODE = 'грипп и высокая температура', 'sick'


@pytest.fixture
def staff(client, monkeypatch):
    for module in [staff_time, staff_time_routes, absence_feed]: monkeypatch.setattr(module, 'today', lambda: TODAY)
    enable(client); create(client, 'teacher'); create(client, 'editor')
    return client


def feed(c):
    response = c.get('/api/calendar/absence-feed?workspace=demo')
    assert response.status_code == 200, response.text
    return response.json()


def own(c):
    return ok(c.get(path('options')))['employees'][0]


def approved(admin, teacher=None, start=LEAVE_DAY, end=LEAVE_DAY, **values):
    teacher = teacher or signed('teacher')
    item = leave(teacher, own(teacher), start=start, end=end, code=CODE, note=NOTE, **values)
    return ok(approve_leave(admin, item))


def apply(c, table, value=None, remove=None):
    revision = api.read('demo').revision
    preview = c.post('/api/calendar/preview?workspace=demo', json=dict(revision=revision, table=table, value=value, removeId=remove))
    assert preview.status_code == 200, preview.text
    assert preview.json()['canApply']
    applied = c.post('/api/editor/apply?workspace=demo', json=dict(revision=revision, token=preview.json()['token']))
    assert applied.status_code == 200, applied.text
    return applied.json()


def lessons_of(c, teacher, day=LEAVE_DAY):
    rows = c.get(f'/api/calendar/day?workspace=demo&day={day}').json()['rows']
    return sum(r['activity'] == 'lesson' and r['status'] != 'cancelled' and teacher in r['teacherIds'] for r in rows)


def inbox(c):
    return c.get('/api/notifications/inbox').json()['items']


def test_an_approved_leave_is_offered_without_its_reason(staff):
    teacher, editor = signed('teacher'), signed('editor')
    item = leave(teacher, own(teacher), start=LEAVE_DAY, end=LEAVE_DAY, code=CODE, note=NOTE)
    before = api.read('demo')
    assert feed(staff)['pending'] == []
    ok(approve_leave(staff, item))
    # Approval alone never touches the school document.
    assert api.read('demo').revision == before.revision and api.read('demo').absences == []
    for viewer in [staff, editor]:
        pending = feed(viewer)['pending']
        assert len(pending) == 1 and pending[0]['teacherId'] == 't2' and pending[0]['leaveId'] == item['id']
        assert pending[0]['lessons'] == lessons_of(staff, 't2') > 0
        assert (pending[0]['start'], pending[0]['end'], pending[0]['startTime'], pending[0]['endTime']) == (LEAVE_DAY, LEAVE_DAY, '00:00', '23:59')
        value = pending[0]['absence']
        assert value['id'] == 'leave-' + item['id'] and value['teacherId'] == 't2'
    text = editor.get('/api/calendar/absence-feed?workspace=demo').text
    assert NOTE not in text and CODE not in text and 'Сотрудник' not in text
    assert feed(staff)['today'] == '2026-08-10' and feed(staff)['revision'] == before.revision


def test_the_timetable_absence_is_named_alike_whatever_the_kind_of_leave(staff):
    teacher = signed('teacher')
    for code, day in [('sick', LEAVE_DAY), ('leave', '2026-08-24')]:
        ok(approve_leave(staff, leave(teacher, own(teacher), start=day, end=day, code=code, note=NOTE)))
    pending = feed(staff)['pending']
    assert len(pending) == 2
    assert pending[0]['absence']['name'] == pending[1]['absence']['name']


def test_one_click_makes_a_neutral_absence_with_history_and_a_plan(staff):
    approved(staff)
    pending = feed(staff)['pending'][0]
    state = apply(staff, 'absences', pending['absence'])
    assert [(a['id'], a['name'], a['teacherId']) for a in state['absences']] == [(pending['absence']['id'], pending['absence']['name'], 't2')]
    after = feed(staff)
    assert after['pending'] == [] and after['revision'] == state['revision']
    assert after['uncovered'] == [dict(absenceId=pending['absence']['id'], teacherId='t2', teacher='Учитель английского', days=[dict(date=LEAVE_DAY, lessons=pending['lessons'])])]
    plan = staff.post('/api/calendar/day-plan?workspace=demo', json=dict(revision=state['revision'], day=LEAVE_DAY, teacherId='t2'))
    assert plan.status_code == 200 and plan.json()['lessons'][0]['proposal']['action'] == 'substitution'
    # The ordinary history can take it back, and the leave is offered again.
    assert staff.post('/api/undo?workspace=demo', json=dict(revision=state['revision'])).status_code == 200
    assert len(feed(staff)['pending']) == 1
    apply(staff, 'absences', feed(staff)['pending'][0]['absence'])
    # Dates edited by hand keep the absence linked to the leave through its id.
    edited = api.read('demo')
    edited.absences = [a.model_copy(update=dict(end=date(2026, 8, 18))) for a in edited.absences]
    api.save('demo', edited)
    assert feed(staff)['pending'] == []
    # Even cut short, so that it no longer covers the leave, it is still the absence of that leave and is not offered a second time.
    edited = api.read('demo')
    edited.absences = [a.model_copy(update=dict(endTime='09:00')) for a in edited.absences]
    api.save('demo', edited)
    assert feed(staff)['pending'] == []


def test_a_replaced_lesson_is_no_longer_uncovered(staff):
    approved(staff)
    apply(staff, 'absences', feed(staff)['pending'][0]['absence'])
    revision = api.read('demo').revision
    plan = staff.post('/api/calendar/day-plan?workspace=demo', json=dict(revision=revision, day=LEAVE_DAY, teacherId='t2')).json()
    proposal = plan['lessons'][0]['proposal']
    body = dict(revision=revision, day=LEAVE_DAY, teacherId='t2', assignments=[dict(rowId=plan['lessons'][0]['row']['id'], action='substitution', teacherId=proposal['teacherId'])])
    preview = staff.post('/api/calendar/day-plan/preview?workspace=demo', json=body).json()
    assert staff.post('/api/editor/apply?workspace=demo', json=dict(revision=revision, token=preview['token'])).status_code == 200
    assert feed(staff)['uncovered'] == []


def test_a_cancelled_leave_asks_to_remove_the_absence_it_made(staff):
    first = approved(staff)
    second = approved(staff, start='2026-08-24', end='2026-08-24')
    apply(staff, 'absences', next(p for p in feed(staff)['pending'] if p['leaveId'] == first['id'])['absence'])
    ok(action(staff, first, 'cancel', kind='leave', reason='Ошибка'))
    ok(action(staff, second, 'cancel', kind='leave', reason='Ошибка'))
    result = feed(staff)
    # The cancelled leave that never became an absence produces nothing.
    assert result['pending'] == [] and len(result['withdrawn']) == 1
    withdrawn = result['withdrawn'][0]
    assert withdrawn['leaveId'] == first['id'] and withdrawn['absenceId'] == 'leave-' + first['id'] and withdrawn['teacherId'] == 't2' and withdrawn['start'] == LEAVE_DAY
    apply(staff, 'absences', remove=withdrawn['absenceId'])
    assert feed(staff)['withdrawn'] == [] and api.read('demo').absences == []
    assert 'Ошибка' not in staff.get('/api/calendar/absence-feed?workspace=demo').text


def regrant(c, login, **values):
    """Change an account the way the access panel does: the stored user with some fields replaced."""
    user = next(u for u in c.get('/api/access/users').json() if u['login'] == login)
    response = c.put('/api/access/users/' + user['id'], json={k: v for k, v in user.items() if k != 'id'} | values)
    assert response.status_code == 200, response.text


def test_a_cancelled_leave_finds_its_absences_whatever_became_of_the_account(staff):
    create(staff, 'teacher', login='double', grants=[dict(workspace='demo', view='teacher', entityIds=['t2', 't3'])])
    item = approved(staff, signed('double'))
    for pending in feed(staff)['pending']: apply(staff, 'absences', pending['absence'])
    ok(action(staff, item, 'cancel', kind='leave', reason='Ошибка'))
    # One teacher is left on the account, so a single absence would now be named without an index; the stored ones are still found.
    regrant(staff, 'double', grants=[dict(workspace='demo', view='teacher', entityIds=['t2'])])
    assert sorted((w['absenceId'], w['teacherId']) for w in feed(staff)['withdrawn']) == [(f'leave-{item["id"]}-0', 't2'), (f'leave-{item["id"]}-1', 't3')]
    regrant(staff, 'double', role='staff', grants=[])
    assert len(feed(staff)['withdrawn']) == 2
    regrant(staff, 'double', active=False)
    withdrawn = feed(staff)['withdrawn']
    assert len(withdrawn) == 2
    for stale in withdrawn: apply(staff, 'absences', remove=stale['absenceId'])
    assert feed(staff)['withdrawn'] == [] and api.read('demo').absences == []


def test_partial_days_keep_their_hours_and_midnight_becomes_the_last_minute(staff):
    short = approved(staff, startTime='10:00', endTime='11:00')
    [item] = feed(staff)['pending']
    assert (item['startTime'], item['endTime'], item['lessons']) == ('10:00', '11:00', 0)
    ok(action(staff, short, 'cancel', kind='leave', reason='Ошибка'))
    approved(staff, startTime='09:00', endTime='24:00')
    [late] = feed(staff)['pending']
    assert (late['startTime'], late['endTime']) == ('09:00', '23:59') and late['lessons'] == 0
    apply(staff, 'absences', late['absence'])
    assert api.read('demo').absences[0].endTime == '23:59'


def test_a_leave_across_the_end_of_the_year_is_clipped(staff):
    approved(staff, start='2027-06-10', end='2027-06-14')
    [item] = feed(staff)['pending']
    assert (item['start'], item['end']) == ('2027-06-10', '2027-06-11')
    apply(staff, 'absences', item['absence'])
    assert api.read('demo').absences[0].end == date(2027, 6, 11)


def test_a_leave_wholly_outside_the_year_is_skipped(staff):
    approved(staff, start='2027-06-12', end='2027-06-14')
    assert feed(staff)['pending'] == []


def test_a_manual_absence_that_covers_the_leave_is_not_offered_again(staff):
    approved(staff)
    s = api.read('demo')
    s.absences = [TeacherAbsence(id='manual', name='Командировка', teacherId='t2', start=date(2026, 8, 17), end=date(2026, 8, 17), startTime='00:00', endTime='10:00')]
    api.save('demo', s)
    assert len(feed(staff)['pending']) == 1
    s = api.read('demo')
    s.absences = [a.model_copy(update=dict(endTime='23:59')) for a in s.absences]
    api.save('demo', s)
    assert feed(staff)['pending'] == []


def test_an_account_with_two_teachers_gets_one_absence_each(staff):
    create(staff, 'teacher', login='double', grants=[dict(workspace='demo', view='teacher', entityIds=['t2', 't3'])])
    both = signed('double')
    item = approved(staff, both)
    pending = feed(staff)['pending']
    assert [(p['teacherId'], p['absence']['id']) for p in pending] == [('t2', f'leave-{item["id"]}-0'), ('t3', f'leave-{item["id"]}-1')]
    apply(staff, 'absences', pending[1]['absence'])
    assert [p['teacherId'] for p in feed(staff)['pending']] == ['t2']


def test_uncovered_lessons_start_from_today(staff, monkeypatch):
    s = api.read('demo')
    s.absences = [TeacherAbsence(id='manual', name='Командировка', teacherId='t2', start=date(2026, 8, 17), end=date(2026, 8, 17)),
                  TeacherAbsence(id='running', name='Больничный', teacherId='t2', start=date(2026, 8, 17), end=date(2026, 8, 31))]
    api.save('demo', s)
    days = lambda: {u['absenceId']: [(d['date'], d['lessons']) for d in u['days']] for u in feed(staff)['uncovered']}
    assert days() == {'manual': [(LEAVE_DAY, 1)], 'running': [(LEAVE_DAY, 1), ('2026-08-24', 1), ('2026-08-31', 1)]}
    monkeypatch.setattr(absence_feed, 'today', lambda: date(2026, 8, 25))
    # One absence is over, the other began a week ago and goes on: the days already over are not offered for substitution.
    assert days() == {'running': [('2026-08-31', 1)]}


def test_staff_without_a_teaching_link_produce_nothing_and_notify_nobody(staff):
    create(staff, 'staff', name='Секретарь')
    item = leave(signed('staff'), own(signed('staff')), start=LEAVE_DAY, end=LEAVE_DAY)
    ok(approve_leave(staff, item))
    assert feed(staff)['pending'] == []
    assert not [n for n in inbox(signed('editor')) if n['action'].startswith('timetable.leave')]


def test_schedulers_hear_of_a_teachers_leave_without_names_or_reasons(staff):
    editor = signed('editor')
    item = approved(staff)
    notices = [n for n in inbox(editor) if n['action'].startswith('timetable.leave')]
    assert [n['action'] for n in notices] == ['timetable.leave_approved'] and notices[0]['category'] == 'timetable'
    text = str(notices)
    for private in [NOTE, CODE, 'Сотрудник', 'Учитель английского', item['id']]: assert private not in text
    ok(action(staff, item, 'cancel', kind='leave', reason='Ошибка'))
    assert [n['action'] for n in inbox(editor) if n['action'].startswith('timetable.leave')] == ['timetable.leave_cancelled', 'timetable.leave_approved']
    # The administrator who decided is not told about their own decision; the teacher gets the ordinary staff-time notice.
    assert not [n for n in inbox(staff) if n['action'].startswith('timetable.leave')]
    assert 'staff-time.changed' in [n['action'] for n in inbox(signed('teacher'))] and not [n for n in inbox(signed('teacher')) if n['action'].startswith('timetable.leave')]


def test_only_active_schedulers_of_the_workspace_are_told(staff):
    create(staff, 'editor', login='away'); create(staff, 'editor', login='other', workspaces=['school'])
    regrant(staff, 'away', active=False)
    approved(staff)
    # Switched on again afterwards, the account that was off at the time still has nothing; an editor of another workspace never does.
    regrant(staff, 'away', active=True)
    for login in ['away', 'other']: assert not [n for n in inbox(signed(login)) if n['action'].startswith('timetable.leave')], login
    assert [n['action'] for n in inbox(signed('editor')) if n['action'].startswith('timetable.leave')] == ['timetable.leave_approved']


def test_a_database_that_never_saw_a_leave_has_an_empty_feed(client):
    assert feed(client) == dict(revision=api.read('demo').revision, today=feed(client)['today'], pending=[], withdrawn=[], uncovered=[])
    with api.connect() as db:
        assert db.execute("SELECT 1 FROM sqlite_master WHERE name='staff_time_leave'").fetchone() is None
