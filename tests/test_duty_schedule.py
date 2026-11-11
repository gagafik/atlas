from datetime import date, datetime, timezone
from uuid import uuid4
import pytest
from backend import api, duty_reminders, duty_schedule
from backend.models import Commitment, Cycles, Event, Named, State, TeachingPeriod, TimetableRule
from test_access import client, enable, create, signed, HEADERS

MON = date(2026, 9, 7)


def rid(): return str(uuid4())


def clock(monkeypatch, day, hour=12, minute=0):
    """Fix the module clocks; times are UTC, school time is five hours ahead."""
    value = datetime(day.year, day.month, day.day, hour, minute, tzinfo=timezone.utc)
    monkeypatch.setattr(duty_schedule, 'now', lambda: value)
    monkeypatch.setattr(duty_reminders, 'now', lambda: value)


def duty(id='d1', day=MON, start='12:00', end='12:05', **values):
    return Commitment.model_validate(dict(id=id, name='Холл', kind='duty', onDate=day, startTime=start, endTime=end, location='Холл',
                                          requiredTeachers=1, profileId='secondary') | values)


def put_commitments(*items, events=None):
    state = api.read('demo')
    state.commitments = list(items)
    if events is not None: state.events = events
    return api.save('demo', state)


def template_body(**values):
    value = dict(name='Холл после 5 урока', weekday=0, profileId='secondary', afterPeriod=4, location='Холл 1 этажа', requiredTeachers=1) | values
    return dict(requestId=rid(), version=0, template=value, reason='')


def save_template(client, expect=200, **values):
    response = client.post('/api/duties/templates?workspace=demo', json=template_body(**values))
    assert response.status_code == expect, response.text
    return response.json()


def preview_body(template_ids, **values):
    return dict(revision=api.read('demo').revision, templateIds=template_ids, start='2026-09-07', end='2026-10-04') | values


def preview(client, template_ids, expect=200, **values):
    response = client.post('/api/duties/generate/preview?workspace=demo', json=preview_body(template_ids, **values))
    assert response.status_code == expect, response.text
    return response.json()


def apply(client, shown, expect=200, request_id=None):
    response = client.post('/api/duties/generate/apply?workspace=demo', json=dict(requestId=request_id or rid(), revision=shown['revision'], token=shown['token']))
    assert response.status_code == expect, response.text
    return response.json()


def calendar():
    return [Event(id='h', name='Праздник', kind='holiday', effect='note', start=MON.replace(day=14), end=MON.replace(day=14)),
            Event(id='x', name='Санитарный день', kind='extra', effect='cancel', start=MON.replace(day=21), end=MON.replace(day=21)),
            Event(id='p', name='Часть классов', kind='extra', effect='cancel', start=MON.replace(day=28), end=MON.replace(day=28), classIds=['c-demo-a'])]


def inbox(client, action=None):
    items = client.get('/api/notifications/inbox').json()['items']
    return [i for i in items if action is None or i['action'] == action]


def commitment_json(item): return item.model_dump(mode='json')


def test_teacher_options_name_the_role_and_own_teacher_record(client):
    enable(client); create(client, 'teacher')
    response = signed('teacher').get('/api/duties/options?workspace=demo')
    assert response.status_code == 200 and response.json()['role'] == 'teacher'
    assert response.json()['teachers'] == [dict(id='t2', name='Учитель английского')]


def test_roles(client):
    enable(client)
    create(client, 'parent'); create(client, 'staff'); create(client, 'editor', workspaces=['school'])
    assert signed('parent').get('/api/duties/options?workspace=demo').status_code == 403
    assert signed('staff').get('/api/duties/options?workspace=demo').status_code == 403
    editor = signed('editor')
    assert editor.get('/api/duties/options?workspace=demo').status_code == 403
    response = editor.get('/api/duties/options?workspace=school')
    assert response.status_code == 200 and response.json()['role'] == 'manager'
    assert client.get('/api/duties/options?workspace=demo').json()['settings']['remindAt'] == '18:00'


def test_local_user_is_a_manager_but_never_a_teacher(client):
    assert client.get('/api/duties/options?workspace=demo').json()['role'] == 'manager'
    body = dict(requestId=rid(), commitmentId='d1', fromTeacherId='t2')
    assert client.post('/api/duties/swaps?workspace=demo', json=body).status_code == 403
    assert client.get('/api/duties/mine?workspace=demo').status_code == 422


def test_unauthenticated_local_user_is_gone_once_accounts_exist(client):
    enable(client)
    from fastapi.testclient import TestClient
    assert TestClient(api.app, headers=HEADERS).get('/api/duties/options?workspace=demo').status_code == 401


def test_template_validation_history_and_archive(client):
    for bad in [dict(weekday=5), dict(afterPeriod=10), dict(afterPeriod=4, startTime='12:00', endTime='12:05'), dict(afterPeriod=None),
                dict(location='', roomIds=[]), dict(profileId='missing'), dict(roomIds=['nope']), dict(teacherIds=['t0', 't1'], requiredTeachers=1),
                dict(teacherIds=['t0'], eligibleTeacherIds=['t1']), dict(cycleIds=['nope'])]:
        save_template(client, expect=422, **bad)
    first = save_template(client, roomIds=['r0'])
    assert first['version'] == 1 and first['active'] and first['roomIds'] == ['r0']
    body = template_body(name='Новое имя', roomIds=['r0']) | dict(id=first['id'], version=1, reason='Переименование')
    second = client.post('/api/duties/templates?workspace=demo', json=body)
    assert second.status_code == 200 and second.json()['version'] == 2
    # Replaying the same request returns the stored answer; a stale version conflicts.
    assert client.post('/api/duties/templates?workspace=demo', json=body).json() == second.json()
    assert client.post('/api/duties/templates?workspace=demo', json=body | dict(requestId=rid())).status_code == 409
    assert client.post('/api/duties/templates?workspace=demo', json=body | dict(reason='Другое')).status_code == 409
    history = client.get(f"/api/duties/templates/{first['id']}/history?workspace=demo").json()
    assert [h['action'] for h in history] == ['updated', 'created'] and history[0]['reason'] == 'Переименование'
    archived = client.post(f"/api/duties/templates/{first['id']}/archive?workspace=demo", json=dict(requestId=rid(), version=2, reason='Не нужен'))
    assert archived.status_code == 200 and not archived.json()['active']
    assert client.get('/api/duties/templates?workspace=demo').json() == []
    assert [t['name'] for t in client.get('/api/duties/templates?workspace=demo&archived=true').json()] == ['Новое имя']
    assert client.post(f"/api/duties/templates/{first['id']}/archive?workspace=demo", json=dict(requestId=rid(), version=3)).status_code == 409
    assert client.post('/api/duties/generate/preview?workspace=demo', json=preview_body([first['id']])).status_code == 404


def test_listed_template_can_be_edited_and_posted_back_without_losing_fields(client):
    save_template(client, roomIds=['r0'])
    path = '/api/duties/templates?workspace=demo'
    listed = client.get(path).json()[0]
    def edit(shown, **changes):
        fields = {k: v for k, v in shown.items() if k not in ('id', 'version', 'active')}
        response = client.post(path, json=dict(requestId=rid(), id=shown['id'], version=shown['version'], template=fields | changes, reason='Правка'))
        assert response.status_code == 200, response.text
        return response.json()
    edited = edit(listed, name='Новое имя')
    again = edit(edited, requiredTeachers=2)
    # The answer of a save is what the list shows next, so an edit can start from either.
    relisted = client.get(path).json()[0]
    assert relisted == again and relisted['roomIds'] == ['r0']
    assert relisted == listed | dict(name='Новое имя', requiredTeachers=2, version=listed['version'] + 2)
    archived = client.post(f"/api/duties/templates/{listed['id']}/archive?workspace=demo", json=dict(requestId=rid(), version=again['version']))
    assert archived.status_code == 200
    assert client.get(path + '&archived=true').json() == [archived.json()]


def test_generation_skips_closed_days_and_stores_nothing_until_applied(client):
    put_commitments(events=calendar())
    tid = save_template(client)['id']
    shown = preview(client, [tid])
    assert shown['counts'] == dict(created=2, updated=0, existing=0, removed=0, duplicates=0)
    assert [c['id'] for c in shown['created']] == [f'duty-{tid}-20260907', f'duty-{tid}-20260928']
    assert {(c['start'], c['end']) for c in shown['created']} == {('12:00', '12:05')}
    assert {s['date']: s['reason'] for s in shown['skipped']} == {'2026-09-14': 'Праздник', '2026-09-21': 'Санитарный день'}
    assert shown['warnings'] and shown['canApply']
    assert api.read('demo').commitments == []
    before = api.read('demo').revision
    result = apply(client, shown)
    assert result['revision'] == before + 1 and result['counts']['created'] == 2
    assert [c['id'] for c in result['state']['commitments']] == [f'duty-{tid}-20260907', f'duty-{tid}-20260928']
    again = preview(client, [tid])
    assert again['counts'] == dict(created=0, updated=0, existing=2, removed=0, duplicates=0) and again['diff']['total'] == 0 and again['canApply']


def test_repeated_apply_answers_from_memory_and_a_spent_preview_is_gone(client):
    put_commitments()
    tid = save_template(client)['id']
    shown = preview(client, [tid])
    key = rid()
    result = apply(client, shown, request_id=key)
    revision = api.read('demo').revision
    replayed = apply(client, shown, request_id=key)
    assert replayed['revision'] == result['revision'] and replayed['counts'] == result['counts'] and api.read('demo').revision == revision
    assert client.post('/api/duties/generate/apply?workspace=demo', json=dict(requestId=key, revision=1, token='other')).status_code == 409
    apply(client, shown, expect=404)


def test_generation_follows_day_in_timetable(client):
    from backend.models import ScheduleDay
    state = api.read('demo')
    # Saturday runs Monday's timetable; the next Monday runs Tuesday's.
    state.scheduleDays = [ScheduleDay(id='sat', name='Отработка', onDate=MON.replace(day=12), day=0),
                          ScheduleDay(id='mon', name='Как вторник', onDate=MON.replace(day=14), day=1)]
    api.save('demo', state)
    tid = save_template(client)['id']
    shown = preview(client, [tid], end='2026-09-20')
    assert [c['id'] for c in shown['created']] == [f'duty-{tid}-20260907', f'duty-{tid}-20260912']
    tuesday = save_template(client, weekday=1, name='Холл, вторник')['id']
    assert f'duty-{tuesday}-20260914' in [c['id'] for c in preview(client, [tuesday], end='2026-09-20')['created']]


def test_skip_kinds_exclusions_and_reading_weeks(client):
    reading = Event(id='r', name='Неделя чтения', kind='reading', effect='note', start=MON.replace(day=14), end=MON.replace(day=18))
    put_commitments(events=[reading])
    tid = save_template(client)['id']
    assert preview(client, [tid])['counts']['created'] == 4
    shown = preview(client, [tid], skipKinds=['reading'], excludeDates=['2026-09-28'])
    assert shown['counts']['created'] == 2
    assert {s['date']: s['reason'] for s in shown['skipped']} == {'2026-09-14': 'Неделя чтения', '2026-09-28': 'Исключено вручную'}


def test_regeneration_keeps_manual_edits_and_deletions(client):
    put_commitments()
    tid = save_template(client)['id']
    apply(client, preview(client, [tid]))
    state = api.read('demo')
    assert len(state.commitments) == 4
    first, last = f'duty-{tid}-20260907', f'duty-{tid}-20260928'
    edited = commitment_json(next(c for c in state.commitments if c.id == first)) | dict(startTime='12:01')
    shown = client.post('/api/commitments/preview?workspace=demo', json=dict(revision=state.revision, value=edited)).json()
    assert client.post('/api/editor/apply?workspace=demo', json=dict(revision=state.revision, token=shown['token'])).status_code == 200
    state = api.read('demo')
    shown = client.post('/api/commitments/preview?workspace=demo', json=dict(revision=state.revision, removeIds=[last])).json()
    assert client.post('/api/editor/apply?workspace=demo', json=dict(revision=state.revision, token=shown['token'])).status_code == 200
    again = preview(client, [tid])
    assert again['counts'] == dict(created=0, updated=0, existing=3, removed=1, duplicates=0)
    assert [(r['date'], r['templateId']) for r in again['removed']] == [('2026-09-28', tid)] and again['removed'][0]['reason']
    kept = {c['id']: c for c in apply(client, again)['state']['commitments']}
    assert kept[first]['startTime'] == '12:01' and last not in kept
    restored = preview(client, [tid], restoreRemoved=True)
    assert restored['counts']['created'] == 1 and [c['id'] for c in restored['created']] == [last]
    # Changing the template with updateUntouched leaves the edited duty alone.
    renamed = client.post('/api/duties/templates?workspace=demo', json=template_body(location='Холл 2') | dict(id=tid, version=1)).json()
    assert renamed['version'] == 2
    # Without the flag nothing is rewritten.
    assert preview(client, [tid])['counts']['updated'] == 0
    changed = preview(client, [tid], updateUntouched=True)
    assert changed['counts']['updated'] == 2 and changed['counts']['existing'] == 1
    stored = {c['id']: c for c in apply(client, changed)['state']['commitments']}
    assert stored[first]['location'] == 'Холл 1 этажа' and stored[first]['startTime'] == '12:01'
    assert last not in stored and all(c['location'] == 'Холл 2' for id, c in stored.items() if id != first)


def test_update_untouched_changes_only_generated_duties(client):
    put_commitments()
    tid = save_template(client, teacherIds=['t3'])['id']
    apply(client, preview(client, [tid]))
    assert [c.teacherIds for c in api.read('demo').commitments] == [['t3']] * 4
    assert client.post('/api/duties/templates?workspace=demo', json=template_body(location='Холл 2', teacherIds=['t3'], eligibleTeacherIds=['t2', 't3']) | dict(id=tid, version=1)).status_code == 200
    changed = preview(client, [tid], updateUntouched=True)
    assert changed['counts']['updated'] == 4 and changed['counts']['existing'] == 0
    result = apply(client, changed)
    assert {(c['location'], tuple(c['eligibleTeacherIds']), tuple(c['teacherIds'])) for c in result['state']['commitments']} == {('Холл 2', ('t2', 't3'), ('t3',))}
    # A locked duty is never rewritten.
    state = api.read('demo'); state.commitments[0].locked = True; api.save('demo', state)
    client.post('/api/duties/templates?workspace=demo', json=template_body(location='Холл 3', teacherIds=['t3'], eligibleTeacherIds=['t2', 't3']) | dict(id=tid, version=2))
    assert preview(client, [tid], updateUntouched=True)['counts'] == dict(created=0, updated=3, existing=1, removed=0, duplicates=0)


def test_update_untouched_keeps_what_the_template_does_not_own(client):
    put_commitments()
    tid = save_template(client)['id']
    apply(client, preview(client, [tid]))
    first = f'duty-{tid}-20260907'
    group = api.read('demo').groups[0].id
    # Groups and the student count are set by hand in the duty form and are not part of the template.
    state = api.read('demo')
    edited = next(c for c in state.commitments if c.id == first)
    edited.groupIds, edited.studentCount = [group], 40
    api.save('demo', state)
    assert client.post('/api/duties/templates?workspace=demo', json=template_body(location='Холл 2') | dict(id=tid, version=1)).status_code == 200
    changed = preview(client, [tid], updateUntouched=True)
    assert changed['counts']['updated'] == 4
    result = apply(client, changed)
    kept = next(c for c in result['state']['commitments'] if c['id'] == first)
    assert (kept['location'], kept['groupIds'], kept['studentCount']) == ('Холл 2', [group], 40)
    assert all(c['location'] == 'Холл 2' for c in result['state']['commitments'])


def test_manual_duty_prevents_duplicates(client):
    put_commitments(duty(id='manual', day=MON.replace(day=14), start='12:02', end='12:10', location='холл 1 этажа'))
    tid = save_template(client)['id']
    shown = preview(client, [tid])
    assert shown['counts']['created'] == 3 and shown['counts']['duplicates'] == 1
    assert shown['duplicates'][0]['date'] == '2026-09-14'


def test_permanent_teacher_conflict_left_unfilled(client):
    put_commitments()
    monday = save_template(client, startTime='08:30', endTime='08:50', afterPeriod=None, teacherIds=['t2'], name='Утро, понедельник')['id']
    tuesday = save_template(client, startTime='08:30', endTime='08:50', afterPeriod=None, teacherIds=['t2'], weekday=1, name='Утро, вторник')['id']
    shown = preview(client, [monday, tuesday], end='2026-09-13')
    assert {c['id']: c['teachers'] for c in shown['created']} == {f'duty-{monday}-20260907': [], f'duty-{tuesday}-20260908': ['Учитель английского']}
    assert len(shown['warnings']) == 1 and 'Учитель английского' in shown['warnings'][0]


@pytest.mark.parametrize('period', [dict(start='2026-08-17', end='2027-09-01'), dict(start='2026-08-01', end='2026-08-30'), dict(start='2026-09-30', end='2026-09-07')],
                         ids=['longer-than-a-year', 'before-the-school-year', 'ends-before-it-starts'])
def test_generation_period_must_be_inside_the_school_year(client, period):
    put_commitments()
    tid = save_template(client)['id']
    preview(client, [tid], expect=422, **period)


def test_preview_of_a_stale_revision_is_refused(client):
    put_commitments()
    tid = save_template(client)['id']
    stale = preview_body([tid]) | dict(revision=api.read('demo').revision + 1)
    assert client.post('/api/duties/generate/preview?workspace=demo', json=stale).status_code == 409


def test_apply_of_an_unknown_token_is_refused(client):
    put_commitments()
    assert client.post('/api/duties/generate/apply?workspace=demo', json=dict(requestId=rid(), revision=1, token='nothing')).status_code == 404


def test_apply_after_another_save_is_refused(client):
    put_commitments()
    tid = save_template(client)['id']
    shown = preview(client, [tid])
    api.save('demo', api.read('demo'))
    apply(client, shown, expect=409)


def test_preview_lives_for_its_time_and_then_expires(client, monkeypatch):
    put_commitments()
    tid = save_template(client)['id']
    life = duty_schedule.PREVIEW_MINUTES
    clock(monkeypatch, MON, 8, 0)
    expired = preview(client, [tid])
    clock(monkeypatch, MON, 8, life + 1)
    apply(client, expired, expect=404)
    clock(monkeypatch, MON, 8, 0)
    alive = preview(client, [tid])
    clock(monkeypatch, MON, 8, life - 1)
    apply(client, alive)


def test_generation_is_for_managers_only(client):
    put_commitments()
    tid = save_template(client)['id']
    enable(client); create(client, 'teacher'); create(client, 'editor')
    assert signed('teacher').post('/api/duties/generate/preview?workspace=demo', json=preview_body([tid])).status_code == 403
    assert signed('teacher').get('/api/duties/templates?workspace=demo').status_code == 403
    assert signed('editor').post('/api/duties/generate/preview?workspace=demo', json=preview_body([tid])).status_code == 200


def test_generation_beyond_the_limit_is_refused(client, monkeypatch):
    put_commitments()
    tid = save_template(client)['id']
    monkeypatch.setattr(duty_schedule, 'MAX_NEW', 3)
    preview(client, [tid], expect=422)
    monkeypatch.setattr(duty_schedule, 'MAX_NEW', 4)
    assert preview(client, [tid])['counts']['created'] == 4


def test_generation_apply_is_atomic(client, monkeypatch):
    put_commitments()
    tid = save_template(client)['id']
    shown = preview(client, [tid])
    before = api.read('demo')
    key = rid()
    def failure(*args, **kwargs): raise RuntimeError('history failed')
    with monkeypatch.context() as patch:
        patch.setattr(api.duties, 'history', failure)
        with pytest.raises(RuntimeError): apply(client, shown, request_id=key)
    assert api.read('demo') == before
    assert preview(client, [tid])['counts']['created'] == 4
    # The failed request was not remembered: the same request id now applies.
    assert apply(client, shown, request_id=key)['counts']['created'] == 4
    assert len(api.read('demo').commitments) == 4


def test_generation_that_adds_violations_cannot_be_applied(client):
    put_commitments()
    # Room r0 is taken by the Monday lesson at 08:20-09:00, so a duty there collides.
    tid = save_template(client, afterPeriod=None, startTime='08:30', endTime='08:50', roomIds=['r0'], location='')['id']
    shown = preview(client, [tid], end='2026-09-13')
    assert shown['counts']['created'] == 1 and not shown['canApply'] and shown['newIssues']
    apply(client, shown, expect=422)
    assert api.read('demo').commitments == []


def test_reminder_worker_starts_and_stops_with_the_application(client):
    import threading
    from fastapi.testclient import TestClient
    alive = lambda: any(t.name == 'atlas-duty-reminders' and t.is_alive() for t in threading.enumerate())
    with TestClient(api.app, headers=HEADERS) as running:
        assert running.get('/api/health').status_code == 200 and alive()
    assert not alive()


def test_template_edit_after_preview_blocks_apply(client):
    put_commitments()
    tid = save_template(client)['id']
    shown = preview(client, [tid])
    client.post('/api/duties/templates?workspace=demo', json=template_body(location='Холл 2') | dict(id=tid, version=1))
    apply(client, shown, expect=409)
    assert api.read('demo').commitments == []


def test_week_patterns_follow_teaching_periods(client):
    state = api.read('demo')
    cycles = Cycles(patterns=[Named(id='weekly', name='А'), Named(id='b', name='Б')],
                    periods=[TeachingPeriod(id='t1', name='Первая четверть', start=date(2026, 8, 17), end=date(2026, 9, 4), anchor=date(2026, 8, 17), patternIds=['weekly', 'b'])])
    api.save('demo', State.model_validate(state.model_dump() | dict(events=[], commitments=[], cycles=cycles.model_dump())))
    own = dict(afterPeriod=None, startTime='12:00', endTime='12:10')
    second = save_template(client, name='Нечётные недели', cycleIds=['b'], **own)['id']
    first = save_template(client, name='Каждую неделю', cycleIds=['weekly'], **own)['id']
    shown = preview(client, [second], start='2026-08-17', end='2026-09-13')
    assert [c['id'] for c in shown['created']] == [f'duty-{second}-20260824']
    # The week after the period has no pattern at all.
    assert [s['date'] for s in shown['skipped']] == ['2026-09-07']
    assert [c['id'] for c in preview(client, [first], start='2026-08-17', end='2026-09-13')['created']] == [f'duty-{first}-20260817', f'duty-{first}-20260831']


def test_vanished_rooms_and_teachers_are_dropped_and_short_weeks_skipped(client):
    put_commitments(events=[])
    own = dict(afterPeriod=None, startTime='12:00', endTime='12:10')
    old = save_template(client, name='Старый', roomIds=['r0', 'r1'], location='', eligibleTeacherIds=['t0', 't3'], teacherIds=['t3'], **own)['id']
    nobody = save_template(client, name='Без кандидатов', weekday=1, location='Холл', eligibleTeacherIds=['t3'], **own)['id']
    friday = save_template(client, name='Пятница', weekday=4, location='Двор', **own)['id']
    state = api.read('demo').model_dump()
    state['settings']['days'] = state['settings']['days'][:4]
    state['rooms'] = [r for r in state['rooms'] if r['id'] != 'r1']
    state['teachers'] = [t for t in state['teachers'] if t['id'] != 't3']
    for w in state['workloads']:
        w['roomIds'] = [r for r in w['roomIds'] if r != 'r1']
        w['teacherId'] = 't0' if w['teacherId'] == 't3' else w['teacherId']
        w['additionalTeacherIds'] = [t for t in w['additionalTeacherIds'] if t != 't3']
    for p in state['placements']: p['roomId'] = 'r0' if p['roomId'] == 'r1' else p['roomId']
    api.save('demo', State.model_validate(state))
    shown = preview(client, [old, nobody, friday], end='2026-09-13')
    assert [c['id'] for c in shown['created']] == [f'duty-{old}-20260907']
    assert [s['templateId'] for s in shown['skipped']] == [nobody]
    assert len(shown['warnings']) == 3
    apply(client, shown)
    (kept,) = api.read('demo').commitments
    assert (kept.roomIds, kept.eligibleTeacherIds, kept.teacherIds) == (['r0'], ['t0'], [])


def my_duties_school(client, monkeypatch):
    enable(client)
    create(client, 'teacher', login='phys', grants=[dict(workspace='demo', view='teacher', entityIds=['t1'])])
    create(client, 'teacher'); create(client, 'parent'); create(client, 'staff')
    clock(monkeypatch, MON)
    tomorrow = MON.replace(day=8)
    put_commitments(duty('A', tomorrow, '10:00', '10:10', teacherIds=['t1'], note='Секрет', eligibleTeacherIds=['t1', 't2']),
                    duty('B', tomorrow, '11:00', '11:10', teacherIds=['t2']),
                    duty('C', MON.replace(day=1), teacherIds=['t1']),
                    duty('D', MON.replace(day=9), teacherIds=['t1', 't2'], roomIds=['r1'], location='', buildingId=None))
    return signed('phys'), signed('teacher')


def test_my_duties_lists_only_own_and_hides_internal_fields(client, monkeypatch):
    phys, eng = my_duties_school(client, monkeypatch)
    response = phys.get('/api/duties/mine?workspace=demo')
    assert response.status_code == 200, response.text
    data = response.json()
    assert [i['id'] for i in data['items']] == ['A', 'D'] and data['start'] == '2026-09-07' and data['teachers'] == [dict(id='t1', name='Учитель физики')]
    assert data['items'][1]['colleagues'] == ['Учитель английского'] and data['items'][1]['place'] == '302'
    assert data['items'][0]['canSwap'] and data['items'][0]['swap'] is None and data['items'][0]['colleagues'] == []
    assert all(not ({'note', 'eligibleTeacherIds', 'locked', 'groupIds', 'studentCount'} & set(i)) for i in data['items'])
    assert [i['id'] for i in eng.get('/api/duties/mine?workspace=demo').json()['items']] == ['B', 'D']


def test_my_duties_period_can_be_chosen(client, monkeypatch):
    phys, _ = my_duties_school(client, monkeypatch)
    past = phys.get('/api/duties/mine?workspace=demo&start=2026-09-01&end=2026-09-30').json()
    assert [i['id'] for i in past['items']] == ['C', 'A', 'D']


@pytest.mark.parametrize('period', ['start=2026-09-07&end=2027-10-12', 'start=2026-09-07&end=2026-09-01'], ids=['longer-than-a-year', 'ends-before-it-starts'])
def test_my_duties_period_must_be_sensible(client, monkeypatch, period):
    phys, _ = my_duties_school(client, monkeypatch)
    assert phys.get('/api/duties/mine?workspace=demo&' + period).status_code == 422


def test_my_duties_are_for_teachers_and_the_administration_picks_a_teacher(client, monkeypatch):
    phys, _ = my_duties_school(client, monkeypatch)
    assert phys.get('/api/duties/mine?workspace=demo&teacherId=t2').status_code == 404
    assert phys.get('/api/duties/mine?workspace=demo&teacherId=t1').status_code == 200
    assert signed('parent').get('/api/duties/mine?workspace=demo').status_code == 403
    assert signed('staff').get('/api/duties/mine?workspace=demo').status_code == 403
    assert [i['id'] for i in client.get('/api/duties/mine?workspace=demo&teacherId=t2').json()['items']] == ['B', 'D']
    assert client.get('/api/duties/mine?workspace=demo').status_code == 422
    assert client.get('/api/duties/mine?workspace=demo&teacherId=nobody').status_code == 404


def test_my_duty_on_a_closed_day_is_listed_and_flagged(client, monkeypatch):
    phys, _ = my_duties_school(client, monkeypatch)
    state = api.read('demo'); state.events = calendar()[:1]; state.commitments[0].onDate = MON.replace(day=14); api.save('demo', state)
    flagged = phys.get('/api/duties/mine?workspace=demo').json()['items']
    assert [(i['id'], i['closed']) for i in flagged] == [('D', ''), ('A', 'Праздник')]


def test_roster_print_data(client):
    put_commitments(duty('a', MON, requiredTeachers=2, teacherIds=['t0']), duty('b', MON.replace(day=8), requiredTeachers=2, teacherIds=['t0']),
                    duty('c', MON, location='2 этаж', requiredTeachers=2, teacherIds=['t1']),
                    duty('s', MON.replace(day=12), location='Двор', teacherIds=['t3']),
                    events=[Event(id='h', name='Праздник', kind='holiday', effect='note', start=MON.replace(day=9), end=MON.replace(day=9))])
    data = client.get('/api/duties/roster?workspace=demo&start=2026-09-07&end=2026-09-13').json()
    assert [d['date'] for d in data['days']] == ['2026-09-07', '2026-09-08', '2026-09-09', '2026-09-10', '2026-09-11', '2026-09-12']
    assert next(d for d in data['days'] if d['date'] == '2026-09-09')['closed'] == 'Праздник'
    assert [p['label'] for p in data['places']] == ['2 этаж', 'Двор', 'Холл']
    cell = next(c for c in data['cells'] if c['date'] == '2026-09-07' and c['place'] == 'холл')
    assert cell['items'] == [dict(id='a', start='12:00', end='12:05', name='Холл', teachers=['Учитель математики'], required=2, missing=1)]
    assert data['unfilled'] == 3 and data['school'] == 'Наша школа'
    person = next(t for t in data['teachers'] if t['teacherId'] == 't0')
    assert len(person['items']) == 2 and person['minutes'] == 10 and person['items'][0]['weekday'] == 'Понедельник'
    assert client.get('/api/duties/roster?workspace=demo&start=2026-09-07&end=2026-11-16').status_code == 422
    assert client.get('/api/duties/roster?workspace=demo&start=2026-09-07&end=2026-09-01').status_code == 422
    enable(client); create(client, 'teacher')
    assert signed('teacher').get('/api/duties/roster?workspace=demo&start=2026-09-07&end=2026-09-13').status_code == 403


def reminder_teacher(client, monkeypatch, day=MON.replace(day=8), **values):
    """A teacher with a duty on `day` (tomorrow, 12:00 school time); the clock is at noon of the day before, UTC."""
    enable(client); create(client, 'teacher')
    clock(monkeypatch, MON, 12, 0)
    put_commitments(duty('d1', day, teacherIds=['t2'], **values))
    return signed('teacher')


def test_reminder_comes_in_its_window_and_only_once(client, monkeypatch):
    teacher = reminder_teacher(client, monkeypatch)
    reminders = api.duties.reminders
    assert reminders.process_one() is False and inbox(teacher, 'duty.reminder') == []
    clock(monkeypatch, MON, 13, 30)
    assert reminders.process_one() is True
    sent = inbox(teacher, 'duty.reminder')
    assert len(sent) == 1 and '12:00' in sent[0]['title']
    assert reminders.process_one() is False and len(inbox(teacher, 'duty.reminder')) == 1


def test_no_reminder_while_reminders_are_switched_off(client, monkeypatch):
    teacher = reminder_teacher(client, monkeypatch)
    settings = dict(version=0, enabled=False, daysBefore=1, remindAt='18:00', notifyChanges=True)
    assert client.put('/api/duties/settings?workspace=demo', json=settings).status_code == 200
    clock(monkeypatch, MON, 13, 30)
    assert api.duties.reminders.process_one() is False and inbox(teacher, 'duty.reminder') == []
    assert client.put('/api/duties/settings?workspace=demo', json=settings | dict(version=1, enabled=True)).status_code == 200
    assert api.duties.reminders.process_one() is True and len(inbox(teacher, 'duty.reminder')) == 1


def test_reminder_on_the_day_of_the_duty_at_the_chosen_time(client, monkeypatch):
    teacher = reminder_teacher(client, monkeypatch, start='16:00', end='16:05')
    settings = dict(version=0, enabled=True, daysBefore=0, remindAt='07:00', notifyChanges=True)
    assert client.put('/api/duties/settings?workspace=demo', json=settings).status_code == 200
    clock(monkeypatch, MON.replace(day=8), 1, 0)
    assert api.duties.reminders.process_one() is False and inbox(teacher, 'duty.reminder') == []
    clock(monkeypatch, MON.replace(day=8), 2, 30)
    assert api.duties.reminders.process_one() is True
    sent = inbox(teacher, 'duty.reminder')
    assert len(sent) == 1 and '16:00' in sent[0]['title']


def test_reminder_without_account_is_handled_once(client, monkeypatch):
    clock(monkeypatch, MON, 12, 0)
    put_commitments(duty('d1', MON.replace(day=8), teacherIds=['t2']))
    clock(monkeypatch, MON, 13, 30)
    assert api.duties.reminders.process_one() is True
    assert api.duties.reminders.process_one() is False


def edit_duty(client, id='d1', **values):
    state = api.read('demo')
    shown = client.post('/api/commitments/preview?workspace=demo', json=dict(revision=state.revision, value=commitment_json(next(c for c in state.commitments if c.id == id)) | values))
    assert shown.status_code == 200, shown.text
    assert client.post('/api/editor/apply?workspace=demo', json=dict(revision=state.revision, token=shown.json()['token'])).status_code == 200


def test_one_change_notice_per_save_that_concerns_the_teacher(client, monkeypatch):
    enable(client); create(client, 'teacher')
    teacher = signed('teacher')
    clock(monkeypatch, MON, 8, 0)
    day = MON.replace(day=8)
    put_commitments(duty('d1', day, teacherIds=[]))
    edit_duty(client, teacherIds=['t2'])
    assert len(inbox(teacher, 'duty.changed')) == 1
    state = api.read('demo'); state.commitments.append(Commitment(id='b', name='Встреча', onDate=day, startTime='15:00', endTime='15:30', roomIds=['r2'])); api.save('demo', state)
    assert len(inbox(teacher, 'duty.changed')) == 1
    edit_duty(client, teacherIds=[])
    assert len(inbox(teacher, 'duty.changed')) == 2
    edit_duty(client, startTime='12:10', endTime='12:15')
    assert len(inbox(teacher, 'duty.changed')) == 2
    edit_duty(client, teacherIds=['t2'])
    assert len(inbox(teacher, 'duty.changed')) == 3
    edit_duty(client, startTime='13:00', endTime='13:05')
    changes = inbox(teacher, 'duty.changed')
    assert len(changes) == 4 and '13:00' in changes[0]['title']


def test_change_notice_inside_the_reminder_window_replaces_the_reminder(client, monkeypatch):
    enable(client); create(client, 'teacher')
    teacher = signed('teacher')
    clock(monkeypatch, MON, 13, 30)
    put_commitments(duty('d1', MON.replace(day=8), teacherIds=[]))
    edit_duty(client, teacherIds=['t2'])
    assert len(inbox(teacher, 'duty.changed')) == 1
    assert api.duties.reminders.process_one() is False and inbox(teacher, 'duty.reminder') == []
    # Moving it later the same evening is stated by a new change notice; still no extra reminder.
    edit_duty(client, startTime='13:00', endTime='13:05')
    assert len(inbox(teacher, 'duty.changed')) == 2
    assert api.duties.reminders.process_one() is False and inbox(teacher, 'duty.reminder') == []


def test_change_notifications_are_switchable_and_past_duties_are_silent(client, monkeypatch):
    enable(client); create(client, 'teacher')
    teacher = signed('teacher')
    clock(monkeypatch, MON, 8, 0)
    put_commitments(duty('past', MON.replace(day=1), teacherIds=['t2']))
    assert inbox(teacher, 'duty.changed') == []
    assert client.put('/api/duties/settings?workspace=demo', json=dict(version=0, notifyChanges=False)).status_code == 200
    state = api.read('demo'); state.commitments.append(duty('d1', MON.replace(day=8), teacherIds=['t2'])); api.save('demo', state)
    assert inbox(teacher, 'duty.changed') == []


def test_bulk_generation_sends_one_notice_per_teacher(client, monkeypatch):
    enable(client); create(client, 'teacher')
    teacher = signed('teacher')
    clock(monkeypatch, MON, 8, 0)
    put_commitments()
    tid = save_template(client, teacherIds=['t2'], afterPeriod=None, startTime='17:00', endTime='17:20', name='Вечернее')['id']
    shown = preview(client, [tid], end='2026-12-27')
    created = shown['counts']['created']
    assert created > 1
    apply(client, shown)
    changes = inbox(teacher, 'duty.changed')
    assert len(changes) == 1 and '17:00' in changes[0]['title'] and str(created - 1) in changes[0]['title']


def test_settings_version_conflict(client):
    assert client.get('/api/duties/options?workspace=demo').json()['settings'] == dict(enabled=True, daysBefore=1, remindAt='18:00', notifyChanges=True, version=0)
    body = dict(version=0, enabled=True, daysBefore=0, remindAt='07:30', notifyChanges=False, reason='Утром')
    assert client.put('/api/duties/settings?workspace=demo', json=body).json() == dict(enabled=True, daysBefore=0, remindAt='07:30', notifyChanges=False, version=1)
    assert client.put('/api/duties/settings?workspace=demo', json=body).status_code == 409
    assert client.put('/api/duties/settings?workspace=demo', json=body | dict(version=1, remindAt='25:00')).status_code == 422
    assert client.put('/api/duties/settings?workspace=demo', json=body | dict(version=1, daysBefore=2)).status_code == 422
    assert client.get('/api/duties/options?workspace=demo').json()['settings']['remindAt'] == '07:30'
    enable(client); create(client, 'teacher')
    assert signed('teacher').put('/api/duties/settings?workspace=demo', json=body | dict(version=1)).status_code == 403


def test_fairness_report(client):
    everyone = ['t0', 't1', 't2']
    put_commitments(duty('d1', MON, '12:00', '12:20', teacherIds=['t0'], eligibleTeacherIds=everyone),
                    duty('d2', MON.replace(day=8), '12:00', '12:05', teacherIds=['t0', 't1'], requiredTeachers=2, eligibleTeacherIds=everyone),
                    duty('d3', MON.replace(day=21), teacherIds=['t2'], eligibleTeacherIds=everyone))
    data = client.get('/api/duties/fairness?workspace=demo&start=2026-09-07&end=2026-09-13').json()
    rows = {r['teacherId']: r for r in data['rows']}
    assert (rows['t0']['minutes'], rows['t0']['duties'], rows['t0']['deviation'], rows['t0']['places']) == (25, 2, 15, 1)
    assert (rows['t1']['minutes'], rows['t2']['minutes'], rows['t2']['duties']) == (5, 0, 0) and rows['t2']['firstDate'] is None
    assert data['summary'] == dict(requiredMinutes=30, assignedMinutes=30, unfilledPlaces=0, mean=10.0, max=25, min=0, people=3)
    assert [r['teacherId'] for r in data['rows']] == ['t0', 't1', 't2']
    assert client.get('/api/duties/fairness?workspace=demo&start=2026-09-07&end=2027-10-12').status_code == 422
    empty = client.get('/api/duties/fairness?workspace=demo&start=2026-12-01&end=2026-12-02').json()
    assert empty['rows'] == [] and empty['summary']['mean'] == 0
    enable(client); create(client, 'teacher')
    assert signed('teacher').get('/api/duties/fairness?workspace=demo&start=2026-09-07&end=2026-09-13').status_code == 403


def swap_body(**values):
    return dict(requestId=rid(), commitmentId='d1', fromTeacherId='t2', toTeacherId='t0', reason='Болею') | values


def swap_school(client, monkeypatch):
    """An English teacher on duty d1 (09:50 school time, Monday), a maths colleague, the clock at midnight before."""
    enable(client); create(client, 'teacher'); create(client, 'teacher', login='math', grants=[dict(workspace='demo', view='teacher', entityIds=['t0'])])
    clock(monkeypatch, MON, 0, 0)
    put_commitments(duty('d1', MON, '09:50', '10:00', teacherIds=['t2']))
    return signed('teacher'), signed('math')


def decision(client, swap, **values):
    body = dict(requestId=rid(), revision=api.read('demo').revision, version=1, approve=True) | values
    return client.post(f"/api/duties/swaps/{swap['id']}/decide?workspace=demo", json=body)


def duty_teachers(): return [c.teacherIds for c in api.read('demo').commitments]


def pending(client): return [s['id'] for s in client.get('/api/duties/swaps?workspace=demo&status=pending').json()]


def test_teacher_requests_a_swap_that_the_administration_and_the_requester_see(client, monkeypatch):
    eng, math = swap_school(client, monkeypatch)
    request = swap_body()
    made = eng.post('/api/duties/swaps?workspace=demo', json=request)
    assert made.status_code == 200, made.text
    swap = made.json()
    assert swap['status'] == 'pending' and swap['fromTeacher']['id'] == 't2' and swap['toTeacher']['id'] == 't0' and 'note' not in swap
    assert len(inbox(client, 'duty.swap_requested')) == 1
    assert eng.post('/api/duties/swaps?workspace=demo', json=request).json() == swap
    assert eng.post('/api/duties/swaps?workspace=demo', json=swap_body()).status_code == 409
    assert math.post('/api/duties/swaps?workspace=demo', json=swap_body()).status_code == 404
    assert eng.post('/api/duties/swaps?workspace=demo', json=swap_body(commitmentId='missing')).status_code == 404
    assert [s['id'] for s in eng.get('/api/duties/swaps?workspace=demo').json()] == [swap['id']]
    assert math.get('/api/duties/swaps?workspace=demo').json() == []
    assert eng.get('/api/duties/mine?workspace=demo').json()['items'][0]['swap'] == 'pending'


@pytest.mark.parametrize('replacement,status', [('t0', 409), (None, 409), ('t2', 422), ('missing', 422)], ids=['busy-colleague', 'busy-colleague-from-the-request', 'the-same-teacher', 'unknown-teacher'])
def test_approval_with_a_wrong_replacement_changes_nothing(client, monkeypatch, replacement, status):
    eng, _ = swap_school(client, monkeypatch)
    # t0 teaches at that time, so the colleague named in the request is busy.
    swap = eng.post('/api/duties/swaps?workspace=demo', json=swap_body()).json()
    revision = api.read('demo').revision
    assert decision(client, swap, toTeacherId=replacement).status_code == status
    assert api.read('demo').revision == revision and duty_teachers() == [['t2']] and pending(client) == [swap['id']]


def test_approval_gives_the_duty_to_the_replacement_and_a_repeat_changes_nothing(client, monkeypatch):
    eng, _ = swap_school(client, monkeypatch)
    swap = eng.post('/api/duties/swaps?workspace=demo', json=swap_body()).json()
    revision = api.read('demo').revision
    key = rid()
    approved = decision(client, swap, toTeacherId='t3', requestId=key)
    assert approved.status_code == 200, approved.text
    assert approved.json()['swap']['status'] == 'approved' and approved.json()['swap']['toTeacher']['id'] == 't3'
    assert next(c for c in approved.json()['state']['commitments'] if c['id'] == 'd1')['teacherIds'] == ['t3']
    assert duty_teachers() == [['t3']] and pending(client) == []
    # The teacher was told of the duty when it was given and is told again when it is taken away.
    assert len(inbox(eng, 'duty.swap_decided')) == 1 and len(inbox(eng, 'duty.changed')) == 2
    again = decision(client, swap, toTeacherId='t3', requestId=key, revision=revision)
    assert again.status_code == 200 and again.json()['swap'] == approved.json()['swap'] and api.read('demo').revision == revision + 1
    assert decision(client, swap, toTeacherId='t3').status_code == 409


@pytest.mark.parametrize('who,values,status', [
    ('teacher', dict(commitmentId='d2'), 422), ('teacher', dict(commitmentId='d3'), 422), ('teacher', dict(commitmentId='d4'), 404),
    ('teacher', dict(toTeacherId='t0'), 422), ('teacher', dict(toTeacherId='t2'), 422), ('parent', dict(), 403)],
    ids=['locked-duty', 'duty-already-past', 'duty-of-a-colleague', 'replacement-not-eligible', 'replacement-is-the-requester', 'not-a-teacher'])
def test_swap_request_refusals(client, monkeypatch, who, values, status):
    enable(client); create(client, 'teacher'); create(client, 'parent')
    clock(monkeypatch, MON, 0, 0)
    put_commitments(duty('d1', MON, '09:50', '10:00', teacherIds=['t2'], eligibleTeacherIds=['t2', 't3']), duty('d2', MON, '11:50', '12:00', teacherIds=['t2'], locked=True),
                    duty('d3', MON.replace(day=1), teacherIds=['t2']), duty('d4', MON, '08:00', '08:05', teacherIds=['t1']))
    assert signed(who).post('/api/duties/swaps?workspace=demo', json=swap_body(**values)).status_code == status
    assert client.get('/api/duties/swaps?workspace=demo').json() == []


def test_requester_cancels_a_pending_swap_and_nobody_else_can(client, monkeypatch):
    eng, _ = swap_school(client, monkeypatch)
    swap = eng.post('/api/duties/swaps?workspace=demo', json=swap_body(toTeacherId=None)).json()
    assert swap['toTeacher'] is None
    path = f"/api/duties/swaps/{swap['id']}/cancel?workspace=demo"
    assert eng.post(path, json=dict(version=swap['version'] + 1)).status_code == 409
    assert client.post(path, json=dict(version=swap['version'])).status_code == 404
    assert eng.post(path, json=dict(version=swap['version'])).json()['status'] == 'cancelled'
    assert eng.get('/api/duties/mine?workspace=demo').json()['items'][0]['swap'] is None
    assert eng.post('/api/duties/swaps?workspace=demo', json=swap_body(toTeacherId=None)).status_code == 200


def test_administration_rejects_a_swap_and_tells_the_requester(client, monkeypatch):
    eng, _ = swap_school(client, monkeypatch)
    swap = eng.post('/api/duties/swaps?workspace=demo', json=swap_body(toTeacherId=None)).json()
    revision = api.read('demo').revision
    assert decision(client, swap).status_code == 422
    assert decision(eng, swap, approve=False).status_code == 403
    rejected = decision(client, swap, approve=False, reason='Нет замены')
    assert rejected.status_code == 200 and rejected.json()['swap']['status'] == 'rejected'
    assert api.read('demo').revision == revision and duty_teachers() == [['t2']]
    assert len(inbox(eng, 'duty.swap_decided')) == 1
    assert eng.get('/api/duties/mine?workspace=demo').json()['items'][0]['swap'] == 'rejected'
    # A rejected request may be made again.
    assert eng.post('/api/duties/swaps?workspace=demo', json=swap_body(toTeacherId='t3')).status_code == 200


@pytest.mark.parametrize('case', ['stale-revision', 'duty-given-to-someone-else', 'duty-locked', 'duty-started'])
def test_approval_of_a_swap_whose_duty_changed_is_refused(client, monkeypatch, case):
    eng, _ = swap_school(client, monkeypatch)
    swap = eng.post('/api/duties/swaps?workspace=demo', json=swap_body(toTeacherId='t3')).json()
    old_revision = api.read('demo').revision
    state = api.read('demo')
    if case == 'duty-given-to-someone-else': state.commitments[0].teacherIds = ['t3']
    if case == 'duty-locked': state.commitments[0].locked = True
    if case in ('stale-revision', 'duty-given-to-someone-else', 'duty-locked'): api.save('demo', state)
    if case == 'duty-started': clock(monkeypatch, MON, 6, 0)
    before = (api.read('demo').revision, duty_teachers())
    assert decision(client, swap, **(dict(revision=old_revision) if case == 'stale-revision' else {})).status_code == 409
    assert (api.read('demo').revision, duty_teachers()) == before and pending(client) == [swap['id']]


def test_swap_approval_is_refused_when_the_replacement_breaks_a_timetable_rule(client, monkeypatch):
    eng, _ = swap_school(client, monkeypatch)
    state = api.read('demo')
    # The teacher's lunch window is covered by this duty, which no lesson, absence or unavailability check notices.
    state.rules = [TimetableRule(id='lunch', name='Обед', kind='day_shape', scope='teacher', targetIds=['t0'], lunchStart='12:00', lunchEnd='13:00', lunchMinutes=30)]
    state.commitments = [duty('d1', MON, '12:00', '13:00', teacherIds=['t2'])]
    api.save('demo', state)
    swap = eng.post('/api/duties/swaps?workspace=demo', json=swap_body()).json()
    revision = api.read('demo').revision
    assert decision(client, swap, toTeacherId='t0').status_code == 409
    assert api.read('demo').revision == revision and duty_teachers() == [['t2']] and pending(client) == [swap['id']]
    # Another colleague is fine, so the request stays usable after the refusal.
    assert decision(client, swap, toTeacherId='t3').status_code == 200
    assert duty_teachers() == [['t3']]


def test_manager_only_routes_refuse_every_other_role(client, monkeypatch):
    enable(client)
    create(client, 'teacher'); create(client, 'parent'); create(client, 'staff'); create(client, 'editor', workspaces=['school'])
    clock(monkeypatch, MON, 0, 0)
    put_commitments(duty('d1', MON, '09:50', '10:00', teacherIds=['t2']))
    tid = save_template(client)['id']
    shown = preview(client, [tid])
    eng = signed('teacher')
    swap = eng.post('/api/duties/swaps?workspace=demo', json=swap_body(toTeacherId='t3')).json()
    revision = api.read('demo').revision
    before = (api.read('demo'), client.get('/api/duties/templates?workspace=demo').json())

    def attempts(who):
        decide = lambda approve: who.post(f"/api/duties/swaps/{swap['id']}/decide?workspace=demo", json=dict(requestId=rid(), revision=revision, version=1, approve=approve, toTeacherId='t3'))
        return {
            'save template': who.post('/api/duties/templates?workspace=demo', json=template_body()),
            'edit template': who.post('/api/duties/templates?workspace=demo', json=template_body(name='Чужое') | dict(id=tid, version=1)),
            'archive': who.post(f'/api/duties/templates/{tid}/archive?workspace=demo', json=dict(requestId=rid(), version=1)),
            'history': who.get(f'/api/duties/templates/{tid}/history?workspace=demo'),
            'templates': who.get('/api/duties/templates?workspace=demo'),
            'settings': who.put('/api/duties/settings?workspace=demo', json=dict(version=0, enabled=False)),
            'preview': who.post('/api/duties/generate/preview?workspace=demo', json=preview_body([tid])),
            'apply': who.post('/api/duties/generate/apply?workspace=demo', json=dict(requestId=rid(), revision=shown['revision'], token=shown['token'])),
            'roster': who.get('/api/duties/roster?workspace=demo&start=2026-09-07&end=2026-09-13'),
            'fairness': who.get('/api/duties/fairness?workspace=demo&start=2026-09-07&end=2026-09-13'),
            'approve': decide(True),
            'reject': decide(False),
        }
    for who in [eng, signed('parent'), signed('staff'), signed('editor')]:
        refused = {name: r.status_code for name, r in attempts(who).items()}
        assert set(refused.values()) == {403}, refused
    # The refused calls changed nothing.
    assert api.read('demo').commitments == before[0].commitments and client.get('/api/duties/templates?workspace=demo').json() == before[1]
    # The list of swap requests: other roles and other workspaces get nothing; a teacher only sees their own.
    for login in ['parent', 'staff', 'editor']: assert signed(login).get('/api/duties/swaps?workspace=demo').status_code == 403
    assert [s['id'] for s in eng.get('/api/duties/swaps?workspace=demo').json()] == [swap['id']]
    assert signed('parent').post('/api/duties/swaps?workspace=demo', json=swap_body()).status_code == 403
    assert signed('editor').post('/api/duties/swaps?workspace=demo', json=swap_body()).status_code == 403


def test_notices_name_only_the_duties_of_the_account_and_go_to_this_workspace(client, monkeypatch):
    enable(client)
    create(client, 'teacher', login='phys', grants=[dict(workspace='demo', view='teacher', entityIds=['t1'])])
    create(client, 'teacher')
    create(client, 'teacher', login='both', grants=[dict(workspace='demo', view='teacher', entityIds=['t1', 't2'])])
    create(client, 'editor', login='ed_demo'); create(client, 'editor', login='ed_school', workspaces=['school'])
    clock(monkeypatch, MON, 0, 0)
    tomorrow = MON.replace(day=8)
    # One save touches two teachers; each account may learn only about the duties of its own teacher records.
    put_commitments(duty('A', tomorrow, '10:00', '10:10', location='Холл А', teacherIds=['t1']), duty('B', tomorrow, '11:00', '11:10', location='Двор Б', teacherIds=['t2']))
    phys, eng, both = signed('phys'), signed('teacher'), signed('both')
    (mine,), (theirs,), (shared,) = inbox(phys, 'duty.changed'), inbox(eng, 'duty.changed'), inbox(both, 'duty.changed')
    assert 'Холл А' in mine['title'] and 'Двор Б' not in mine['title'] and 'ещё' not in mine['title']
    assert 'Двор Б' in theirs['title'] and 'Холл А' not in theirs['title'] and 'ещё' not in theirs['title']
    assert 'Холл А' in shared['title'] and 'и ещё 1' in shared['title']
    assert inbox(signed('ed_demo'), 'duty.changed') == [] and inbox(signed('ed_school'), 'duty.changed') == []
    # A swap request reaches the administration of this workspace only.
    made = eng.post('/api/duties/swaps?workspace=demo', json=swap_body(commitmentId='B', toTeacherId=None))
    assert made.status_code == 200, made.text
    assert len(inbox(client, 'duty.swap_requested')) == 1 and len(inbox(signed('ed_demo'), 'duty.swap_requested')) == 1
    assert inbox(signed('ed_school'), 'duty.swap_requested') == []
    assert inbox(phys, 'duty.swap_requested') == [] and inbox(both, 'duty.swap_requested') == []
