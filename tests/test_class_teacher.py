"""The class teacher is a property of the class: default duties, admin-only changes, notes switch, personal reports."""
import base64
from uuid import uuid4
import pytest
from pydantic import ValidationError
from backend import api
from backend.models import State
from backend.seed import initial_state
from test_access import client, enable, create, signed
from test_absences import mark, send, decide, body, upload, file_url, url
from test_attendance_reports import report
from test_class_staff import BASE, CAPS, A, D, item, settings, assign, payload, coverage, own
from test_final_grades import seeded, publish, report as final_report, opts, propose, stop_gradebook_jobs  # noqa: F401
from test_behaviour import url as behaviour_url, note as behaviour_note
from test_tabular_import import mapped, csv

HISTORY = '/api/access/class-staff/teachers/history?workspace=demo'
STAFF = 'Классный руководитель'


def lead(teacher_id, class_id=A):
    """Set the class teacher outside any request: the actor is the local administrator."""
    school = api.read('demo')
    school.classes = [c.model_copy(update=dict(classTeacherId=teacher_id)) if c.id == class_id else c for c in school.classes]
    api.save('demo', school)


def revoke(client, teacher, *caps, class_id=A):
    return assign(client, {class_id: [dict(accountId=teacher['id'], capabilities=[], revoked=list(caps))]})


def staffed(client):
    """Parent of s1 and the teacher account of t2 (English); nobody is a class teacher yet."""
    enable(client)
    create(client, 'parent')
    teacher = create(client, 'teacher')
    return teacher, signed('parent'), signed('teacher')


def test_state_validates_class_teacher_reference():
    school = initial_state('demo')
    assert all(c.classTeacherId is None for c in school.classes)
    data = school.model_dump(mode='json')
    data['classes'][0]['classTeacherId'] = 't2'
    assert State.model_validate(data).classes[0].classTeacherId == 't2'
    data['classes'][0]['classTeacherId'] = 'missing'
    with pytest.raises(ValidationError) as error:
        State.model_validate(data)
    assert 'учителя' in str(error.value)
    # A document stored before the field existed still loads.
    old = school.model_dump(mode='json')
    for c in old['classes']:
        del c['classTeacherId']
    assert all(c.classTeacherId is None for c in State.model_validate(old).classes)
    data['classes'][0]['classTeacherId'] = ''
    with pytest.raises(ValidationError):
        State.model_validate(data)


def test_class_teacher_is_a_reference_column_of_the_table_import():
    from backend.importing import metadata
    classes = next(t for t in metadata() if t['key'] == 'classes')
    field = next(f for f in classes['fields'] if f['key'] == 'classTeacherId')
    assert field['label'] == STAFF and 'classTeacherId' not in classes['common']


def table_import(c, cell):
    """Upload an `id;Учитель` row for 10A, preview the import into `classes`, return (result, revision)."""
    raw = f'id;Учитель\n{A};{cell}\n'.encode()
    info = c.post('/api/exchange/inspect?workspace=demo', json=dict(filename='classes.csv', content=base64.b64encode(raw).decode(), delimiter=';'))
    assert info.status_code == 200, info.text
    revision = api.read('demo').revision
    r = c.post('/api/exchange/import-preview?workspace=demo', json=dict(token=info.json()['token'], revision=revision, table='classes', mapping=dict(id=0, classTeacherId=1), mode='update'))
    assert r.status_code == 200, r.text
    return r.json(), revision


def test_table_import_resolves_the_class_teacher_by_id_and_by_name():
    school = initial_state('demo')
    for cell in ['t2', 'Учитель английского']:
        result = mapped(school, csv(f'id;Учитель\n{A};{cell}\n'), 'classes', dict(id=0, classTeacherId=1), mode='update')
        assert result['status'] == 'success', result
        assert {c.id: c.classTeacherId for c in result['candidate'].classes} == {A: 't2', D: None}
    # A name nobody carries is a row error, not a silent clear or a dangling reference.
    result = mapped(school, csv(f'id;Учитель\n{A};Никто\n'), 'classes', dict(id=0, classTeacherId=1), mode='update')
    assert result['status'] != 'success' and 'candidate' not in result and result['errors']


def test_table_import_of_the_class_teacher_is_admin_only_and_shows_the_name(client):
    enable(client)
    create(client)  # editor
    editor = signed('editor')
    result, revision = table_import(editor, 'Учитель английского')
    assert result['status'] == 'success'
    change = next(c for c in result['preview']['diff']['changes'] if 'classTeacherId' in c['fields'])
    assert change['fields']['classTeacherId']['displayAfter'] == 'Учитель английского'
    refused = editor.post('/api/editor/apply?workspace=demo', json=dict(revision=revision, token=result['preview']['token']))
    assert refused.status_code == 403 and 'администратор' in refused.json()['detail']
    assert api.read('demo').revision == revision and all(c.classTeacherId is None for c in api.read('demo').classes)
    result, revision = table_import(client, 'Учитель английского')
    assert client.post('/api/editor/apply?workspace=demo', json=dict(revision=revision, token=result['preview']['token'])).status_code == 200
    assert {c.id: c.classTeacherId for c in api.read('demo').classes}[A] == 't2'
    assert client.get(HISTORY).json()['items'][0]['after']['name'] == 'Учитель английского'


def test_unknown_or_deleted_class_teacher_is_refused_and_leaves_the_school_unchanged(client):
    state = client.get('/api/state?workspace=demo').json()
    before = api.read('demo')
    state['classes'][0]['classTeacherId'] = 'missing'
    assert client.put('/api/state?workspace=demo', json=state).status_code == 422
    lead('t2')
    state = client.get('/api/state?workspace=demo').json()
    state['teachers'] = [t for t in state['teachers'] if t['id'] != 't2']
    r = client.put('/api/state?workspace=demo', json=state)
    assert r.status_code == 422 and 'учителя' in r.text
    assert api.read('demo').revision == before.revision + 1


def test_only_admin_changes_class_teacher_and_history(client):
    enable(client)
    create(client)  # editor
    editor = signed('editor')
    state = editor.get('/api/state?workspace=demo').json()
    revision = api.read('demo').revision
    state['classes'][0]['classTeacherId'] = 't2'
    refused = editor.put('/api/state?workspace=demo', json=state)
    assert refused.status_code == 403 and 'администратор' in refused.json()['detail']
    assert api.read('demo').revision == revision and {k: client.get('/api/history?workspace=demo').json()[k] for k in ('undo', 'redo')} == dict(undo=1, redo=0)
    # The same value as stored and a rename are ordinary editor work.
    state['classes'][0]['classTeacherId'] = None
    state['classes'][0]['name'] += ' !'
    assert editor.put('/api/state?workspace=demo', json=state).status_code == 200
    assert client.get(HISTORY).json()['items'] == []
    state = client.get('/api/state?workspace=demo').json()
    state['classes'][0]['classTeacherId'] = 't2'
    assert client.put('/api/state?workspace=demo', json=state).status_code == 200
    first = client.get(HISTORY).json()['items'][0]
    assert first['classId'] == A and first['before'] is None and first['after'] == dict(id='t2', name='Учитель английского')
    assert first['action'] == 'saved' and first['actor'] == 'Администратор'
    # Undo and redo are writes too.
    revision = api.read('demo').revision
    assert editor.post('/api/undo?workspace=demo', json=dict(revision=revision)).status_code == 403
    assert api.read('demo').revision == revision
    assert client.post('/api/undo?workspace=demo', json=dict(revision=revision)).status_code == 200
    undone = client.get(HISTORY).json()['items'][0]
    assert undone['action'] == 'undo' and undone['before']['id'] == 't2' and undone['after'] is None
    revision = api.read('demo').revision
    assert editor.post('/api/redo?workspace=demo', json=dict(revision=revision)).status_code == 403
    assert client.post('/api/redo?workspace=demo', json=dict(revision=revision)).status_code == 200
    assert [i['action'] for i in client.get(HISTORY).json()['items']] == ['redo', 'undo', 'saved']
    # The editor may step back over a change that did not touch the class teacher.
    assert editor.get(HISTORY).status_code == 403


def test_editor_cannot_delete_a_class_with_a_class_teacher_but_admin_can_and_it_is_recorded(client):
    enable(client)
    create(client)
    editor = signed('editor')
    state = client.get('/api/state?workspace=demo').json()
    state['classes'].append(dict(id='c-extra', name='5Z', maxDaily=8, profileId='secondary', classTeacherId='t1'))
    assert client.put('/api/state?workspace=demo', json=state).status_code == 200
    added = client.get(HISTORY).json()['items'][0]
    assert added['classId'] == 'c-extra' and added['before'] is None and added['after']['id'] == 't1'
    state = editor.get('/api/state?workspace=demo').json()
    state['classes'] = [c for c in state['classes'] if c['id'] != 'c-extra']
    revision = api.read('demo').revision
    assert editor.put('/api/state?workspace=demo', json=state).status_code == 403
    assert api.read('demo').revision == revision
    assert client.put('/api/state?workspace=demo', json=state).status_code == 200
    removed = client.get(HISTORY + '&classId=c-extra').json()['items'][0]
    assert removed['before']['id'] == 't1' and removed['after'] is None and removed['className'] == '5Z'
    assert client.get(HISTORY + '&classId=' + A).json()['items'] == []


def test_history_is_paged_and_filtered_by_class(client):
    enable(client)
    for i in range(23):
        lead('t2' if i % 2 == 0 else None)
    first = client.get(HISTORY).json()
    assert len(first['items']) == 20 and first['nextBefore']
    second = client.get(HISTORY + '&before=' + str(first['nextBefore'])).json()
    assert len(second['items']) == 3 and second['nextBefore'] is None
    assert client.get(HISTORY + '&classId=' + D).json()['items'] == []
    assert client.get(HISTORY + '&before=0').status_code == 422


def test_a_rejected_class_teacher_change_leaves_no_history_and_no_notifications(client):
    enable(client)
    create(client)
    editor = signed('editor')
    unread = client.get('/api/notifications/inbox').json()['unread']
    state = editor.get('/api/state?workspace=demo').json()
    state['classes'][0]['classTeacherId'] = 't2'
    assert editor.put('/api/state?workspace=demo', json=state).status_code == 403
    assert client.get(HISTORY).json()['items'] == []
    assert client.get('/api/notifications/inbox').json()['unread'] == unread
    with api.connect() as db:
        assert db.execute("SELECT count(*) FROM audit WHERE action='state.saved' AND revision=?", (state['revision'] + 1,)).fetchone()[0] == 0


def test_class_teacher_gets_class_duties_without_registry_row(client):
    teacher, parent, t = staffed(client)
    mark(client)
    notice = send(parent)
    assert own(t) == [] and t.get(url('/' + notice['id'])).status_code == 404 and report(t).status_code == 403
    lead('t2')
    mine = own(t)
    assert mine[0]['classes'] == [dict(id=A, name='10A', capabilities=CAPS)] and mine[0]['classTeacherOf'] == [A]
    assert t.get(url('/' + notice['id'])).status_code == 200
    assert report(t).status_code == 200 and report(t, classId=D).status_code == 404 and coverage(t).status_code == 200
    assert coverage(t, classId=D).status_code == 404
    assert t.get('/api/journal/absences/options?workspace=demo').json()['canReview']
    assert decide(t, notice).status_code == 200
    assert settings(client)['assignments'] == {}
    with api.connect() as db:
        assert db.execute('SELECT count(*) FROM class_staff_history').fetchone()[0] == 0


def test_class_teacher_admin_view_lists_teacher_and_accounts(client):
    teacher, _, _ = staffed(client)
    create(client, 'teacher', login='other', grants=[dict(workspace='demo', view='teacher', entityIds=['t1'])])
    lead('t2')
    row = next(c for c in settings(client)['classes'] if c['id'] == A)
    assert row['classTeacherId'] == 't2' and row['classTeacherName'] == 'Учитель английского' and row['classTeacherAccounts'] == [teacher['id']]
    other = next(c for c in settings(client)['classes'] if c['id'] == D)
    assert other['classTeacherId'] is None and other['classTeacherName'] is None and other['classTeacherAccounts'] == []


def test_explicit_revoke_wins_and_follows_live_link(client):
    teacher, parent, t = staffed(client)
    mark(client)
    notice = send(parent)
    lead('t2')
    revoke(client, teacher, 'absence_review')
    assert t.get(url('/' + notice['id'])).status_code == 404 and decide(t, notice).status_code == 404
    assert report(t).status_code == 200
    assert own(t)[0]['classes'][0]['capabilities'] == ['attendance_read', 'journal_coverage', 'behaviour_notes']
    assert settings(client)['assignments'] == {A: [dict(accountId=teacher['id'], capabilities=[], revoked=['absence_review'])]}
    lead(None)
    assert own(t) == []
    # The registry row stays and the same document still saves.
    assign(client, settings(client)['assignments'])
    lead('t1')
    assert own(t) == [] and report(t).status_code == 403
    lead('t2')
    assert t.get(url('/' + notice['id'])).status_code == 404  # an old revocation applies again to the same teacher


def test_new_class_teacher_gets_rights_and_the_old_one_loses_them(client):
    teacher, parent, t = staffed(client)
    other = create(client, 'teacher', login='other', grants=[dict(workspace='demo', view='teacher', entityIds=['t1'])])
    o = signed('other')
    lead('t2')
    revoke(client, teacher, 'absence_review')
    lead('t1')
    assert own(t) == [] and own(o)[0]['classes'][0]['capabilities'] == CAPS
    assert report(o).status_code == 200 and report(t).status_code == 403


def test_registry_validation_of_revocations(client):
    teacher, _, _ = staffed(client)
    staff = create(client, 'staff')
    assert client.put(BASE, json=payload(client, {A: [dict(accountId=teacher['id'], capabilities=['absence_review'], revoked=['absence_review'])]})).status_code == 422
    assert client.put(BASE, json=payload(client, {A: [dict(accountId=teacher['id'], capabilities=[], revoked=[])]})).status_code == 422
    assert client.put(BASE, json=payload(client, {A: [dict(accountId=teacher['id'], capabilities=[], revoked=['absence_review', 'absence_review'])]})).status_code == 422
    assert client.put(BASE, json=payload(client, {A: [dict(accountId=teacher['id'], capabilities=[])]})).status_code == 422
    # Taking a duty away needs no right to the space; granting one still does.
    state = client.get('/api/access/users').json()
    off = next(u for u in state if u['id'] == teacher['id'])
    client.put('/api/access/users/' + teacher['id'], json={k: v for k, v in off.items() if k != 'id'} | dict(active=False))
    assign(client, {A: [dict(accountId=teacher['id'], capabilities=[], revoked=['behaviour_notes'])]})
    assert client.put(BASE, json=payload(client, {A: [dict(accountId=teacher['id'], capabilities=['behaviour_notes'])]})).status_code == 422
    assert client.put(BASE, json=payload(client, {'missing': [dict(accountId=staff['id'], capabilities=[], revoked=['behaviour_notes'])]})).status_code == 422


def test_class_teacher_rights_close_when_account_loses_teacher_grant(client):
    teacher, parent, t = staffed(client)
    mark(client)
    notice = send(parent)
    lead('t2')
    assert report(t).status_code == 200
    current = next(u for u in client.get('/api/access/users').json() if u['id'] == teacher['id'])
    r = client.put('/api/access/users/' + teacher['id'], json={k: v for k, v in current.items() if k != 'id'} | dict(grants=[dict(workspace='demo', view='teacher', entityIds=['t1'])]))
    assert r.status_code == 200, r.text
    assert t.get(url('/' + notice['id'])).status_code in [401, 403, 404, 409]
    again = signed('teacher')
    assert own(again) == [] and report(again).status_code == 403


def test_disabled_class_teacher_account_has_no_rights(client):
    teacher, parent, t = staffed(client)
    lead('t2')
    current = next(u for u in client.get('/api/access/users').json() if u['id'] == teacher['id'])
    assert client.put('/api/access/users/' + teacher['id'], json={k: v for k, v in current.items() if k != 'id'} | dict(active=False)).status_code == 200
    assert t.get('/api/journal/class-staff/options').status_code in [401, 403]


def test_changing_class_teacher_invalidates_report_tokens(client):
    teacher, parent, t = staffed(client)
    mark(client)
    lead('t2')
    token = report(t).json()['token']
    lead('t1')
    assert report(t, token=token).status_code in [403, 409]


def test_class_teacher_can_record_praise_for_own_class_only(client):
    teacher, _, t = staffed(client)
    assert behaviour_note(t, ['s1'], 'class-praise').status_code == 403
    lead('t2')
    options = t.get(behaviour_url('options')).json()
    assert 'class_teacher' in options['myLevels']
    assert behaviour_note(t, ['s1', 's2'], 'class-praise').status_code == 200
    assert behaviour_note(t, ['s3'], 'class-praise').status_code in [403, 404]


def test_staff_role_is_never_a_class_teacher_and_old_registry_still_works(client):
    enable(client)
    staff = create(client, 'staff')
    worker = signed('staff')
    lead('t2')
    assert own(worker) == []
    assign(client, {A: [item(staff, 'attendance_read')]})
    assert own(worker)[0]['classes'] == [dict(id=A, name='10A', capabilities=['attendance_read'])] and own(worker)[0]['classTeacherOf'] == []


def actions(c):return [n['action'] for n in c.get('/api/notifications/inbox').json()['items']]


def test_class_teacher_is_default_reviewer_notified(client):
    teacher, parent, t = staffed(client)
    lead('t2')
    first = send(parent)
    assert actions(t).count('absence.submitted') == 1
    revoke(client, teacher, 'absence_review')
    send(parent, body(startDate='2026-08-18', endDate='2026-08-18'))
    assert actions(t).count('absence.submitted') == 1
    assign(client, {})
    # A decision by the administrator reaches the class teacher, a single one through a batch.
    second = send(parent, body(startDate='2026-08-19', endDate='2026-08-19'))
    assert actions(t).count('absence.submitted') == 2
    before = actions(t).count('absence.updated')
    preview = client.post(url('/bulk/preview'), json=dict(requestId=str(uuid4()), items=[dict(id=second['id'], version=second['version'])], action='approve', comment='Проверено')).json()
    assert client.post(url('/bulk/' + preview['id'] + '/apply')).status_code == 200
    assert actions(t).count('absence.updated') == before + 1
    assert decide(client, first, 'reject').status_code == 200
    assert actions(t).count('absence.updated') == before + 2


def test_unrelated_teacher_gets_no_notice_when_there_is_no_class_teacher(client):
    teacher, parent, t = staffed(client)
    send(parent)
    assert 'absence.submitted' not in actions(t)


def switch(c, class_id, enabled, version, rid=None, **values):
    return c.put(f'/api/journal/absences/classes/{class_id}/electronic?workspace=demo', json=dict(requestId=rid or str(uuid4()), version=version, enabled=enabled) | values)


def test_electronic_notes_are_enabled_until_somebody_switches_them(client):
    _, parent, _ = staffed(client)
    assert parent.get('/api/journal/absences/options?workspace=demo').json()['students'][0]['electronicNotes'] is True
    send(parent)
    with api.connect() as db:
        assert db.execute("SELECT count(*) FROM sqlite_master WHERE name LIKE 'absence_class_%'").fetchone()[0] >= 2
        assert db.execute('SELECT count(*) FROM absence_class_settings').fetchone()[0] == 0


def test_class_teacher_switches_electronic_notes(client):
    teacher, parent, t = staffed(client)
    lead('t2')
    off = switch(t, A, False, 0)
    assert off.status_code == 200 and off.json()['version'] == 1 and off.json()['enabled'] is False and off.json()['actor'] == 'Сотрудник'
    refused = parent.post(url(), json=body())
    assert refused.status_code == 403 and 'классным руководителем' in refused.json()['detail']
    assert parent.post(url('/preview'), json=body()).status_code == 403
    payload = body()
    assert upload(parent, payload).status_code == 403
    options = parent.get('/api/journal/absences/options?workspace=demo').json()['students'][0]
    assert options['electronicNotes'] is False and options['classTeacher'] == 'Учитель английского'
    assert switch(t, D, False, 0).status_code == 403
    assert switch(t, A, True, 0).status_code == 409
    rid = str(uuid4())
    assert switch(t, A, True, 1, rid).status_code == 200
    assert switch(t, A, True, 1, rid).json()['version'] == 2  # replay returns the stored result
    assert switch(t, A, False, 2, rid).status_code == 409
    assert switch(client, A, False, 2).status_code == 200
    assert switch(client, A, True, 3).status_code == 200
    assert switch(client, 'missing', True, 0).status_code == 404
    assert switch(client, A, False, 3).status_code == 409  # stale
    assert switch(client, A, True, 4).json()['version'] == 4  # already enabled: nothing is written
    assert send(parent)['status'] == 'pending'
    with api.connect() as db:
        rows = db.execute('SELECT version,enabled,actor_name FROM absence_class_history WHERE class_id=? ORDER BY id', (A,)).fetchall()
    assert rows == [(1, 0, 'Сотрудник'), (2, 1, 'Сотрудник'), (3, 0, 'Администратор'), (4, 1, 'Администратор')]


def test_notes_sent_before_switch_off_stay_reviewable_and_can_be_withdrawn(client):
    teacher, parent, t = staffed(client)
    lead('t2')
    mark(client)
    first = send(parent)
    second = send(parent, body(startDate='2026-08-18', endDate='2026-08-18'))
    assert switch(t, A, False, 0).status_code == 200
    assert t.get(url('/' + first['id'])).status_code == 200
    assert decide(t, first).status_code == 200
    assert decide(parent, second, 'withdraw').status_code == 200
    # The same request is still answered after the switch, not refused.
    request = body(startDate='2026-08-20', endDate='2026-08-20')
    assert parent.post(url(), json=request).status_code == 403
    assert switch(client, A, True, 1).status_code == 200
    created = send(parent, request)
    assert switch(client, A, False, 2).status_code == 200
    assert parent.post(url(), json=request).json()['id'] == created['id']


def test_unsent_files_can_still_be_removed_after_switch_off(client):
    teacher, parent, t = staffed(client)
    payload = body()
    file = upload(parent, payload).json()
    assert switch(client, A, False, 0).status_code == 200
    assert parent.delete(file_url(file['id'])).status_code == 200


def test_only_current_class_teacher_with_review_duty_or_admin_switches(client):
    teacher, parent, t = staffed(client)
    staff = create(client, 'staff')
    assign(client, {A: [item(staff, 'absence_review')]})
    deputy = signed('staff')
    assert switch(deputy, A, False, 0).status_code == 403
    assert switch(t, A, False, 0).status_code == 403  # not the class teacher yet
    lead('t2')
    assign(client, {A: [item(staff, 'absence_review'), dict(accountId=teacher['id'], capabilities=[], revoked=['absence_review'])]})
    assert switch(t, A, False, 0).status_code == 403
    assign(client, {A: [item(staff, 'absence_review')]})
    assert switch(t, A, False, 0).status_code == 200
    assert switch(signed('parent'), A, True, 1).status_code == 403
    with api.connect() as db:
        assert db.execute('SELECT version FROM absence_class_settings WHERE class_id=?', (A,)).fetchone()[0] == 1


def test_switch_list_for_reviewers_shows_only_changeable_classes_to_class_teacher(client):
    teacher, parent, t = staffed(client)
    lead('t2')
    rows = t.get('/api/journal/absences/options?workspace=demo').json()['classSettings']
    assert [(r['id'], r['classTeacher'], r['enabled'], r['canChange']) for r in rows] == [(A, 'Учитель английского', True, True)]
    everything = client.get('/api/journal/absences/options?workspace=demo').json()['classSettings']
    assert {r['id'] for r in everything} == {A, D} and all(r['canChange'] for r in everything)
    assert 'classSettings' not in parent.get('/api/journal/absences/options?workspace=demo').json()


def test_switch_list_offers_no_button_to_registry_deputies_or_a_revoked_class_teacher(client):
    teacher, parent, t = staffed(client)
    lead('t2')
    staff = create(client, 'staff')
    assign(client, {A: [item(staff, 'absence_review')]})
    deputy = signed('staff')
    rows = deputy.get('/api/journal/absences/options?workspace=demo').json()['classSettings']
    assert [(r['id'], r['canChange']) for r in rows] == [(A, False)]
    assert switch(deputy, A, False, 0).status_code == 403
    # The class teacher of 10A with the review duty revoked there but a registry row for 10D: only 10D is listed and it is not theirs to switch.
    assign(client, {A: [dict(accountId=teacher['id'], capabilities=[], revoked=['absence_review'])], D: [item(teacher, 'absence_review')]})
    rows = t.get('/api/journal/absences/options?workspace=demo').json()['classSettings']
    assert [(r['id'], r['canChange']) for r in rows] == [(D, False)]
    assert switch(t, A, False, 0).status_code == 403 and switch(t, D, False, 0).status_code == 403


def test_concurrent_switches_have_one_winner(client):
    from concurrent.futures import ThreadPoolExecutor
    teacher, parent, t = staffed(client)
    lead('t2')
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda enabled: switch(t, A, enabled, 0), [False, True]))
    assert sorted(r.status_code for r in results) in ([200, 200], [200, 409])
    with api.connect() as db:
        assert db.execute('SELECT count(*) FROM absence_class_history').fetchone()[0] <= 1


def test_class_teacher_reads_published_class_report(client):
    p, b, s, r, _ = seeded(client)
    t = signed('teacher')
    publish(client, propose(t, r, s).json())
    outsider = create(client, 'teacher', login='other', grants=[dict(workspace='demo', view='teacher', entityIds=['t1'])])
    o = signed('other')
    assert final_report(t, p).status_code == 403 and opts(t)['classTeacher'] is False and opts(t)['students'] == []
    lead('t2')
    mine = final_report(t, p)
    assert mine.status_code == 200 and len(mine.json()['items']) == 1 and 'calculation' not in mine.json()['items'][0]
    assert final_report(t, p, 's3').status_code == 403
    assert opts(t)['classTeacher'] is True and {x['id'] for x in opts(t)['students']} == {'s1', 's2'} and len(opts(t)['periods']) == 1
    # Another teacher, who does not lead a class, reads nothing and sees no pupils.
    assert final_report(o, p).status_code == 403 and opts(o)['classTeacher'] is False and opts(o)['students'] == [] and opts(o)['periods'] == []
    # The administrator view does not change.
    assert len(opts(client)['students']) > 2 and opts(client)['classTeacher'] is False


def test_class_teacher_loses_the_report_with_the_class_or_the_pupil(client):
    p, b, s, r, _ = seeded(client)
    t = signed('teacher')
    publish(client, propose(t, r, s).json())
    lead('t2')
    assert final_report(t, p).status_code == 200
    school = api.read('demo')
    school.students = [x.model_copy(update=dict(active=False)) if x.id == 's1' else x for x in school.students]
    api.save('demo', school)
    assert final_report(t, p).status_code == 403 and {x['id'] for x in opts(t)['students']} == {'s2'}
    school = api.read('demo')
    school.students = [x.model_copy(update=dict(active=True, classId=D)) if x.id == 's1' else x for x in school.students]
    api.save('demo', school)
    assert final_report(t, p).status_code == 403
    lead('t1')
    assert final_report(t, p).status_code == 403 and final_report(client, p).status_code == 200


def test_portal_shows_class_teacher(client):
    enable(client)
    create(client, 'parent')
    p = signed('parent')
    path = '/api/portal/schedule?workspace=demo&view=student&entityId=s1'
    assert p.get(path).json()['classTeacher'] is None
    lead('t2')
    assert p.get(path).json()['classTeacher'] == 'Учитель английского'
    assert p.get(path + '&day=2026-08-17').json()['classTeacher'] == 'Учитель английского'
    assert p.get('/api/portal/schedule?workspace=demo&view=student&entityId=s2').status_code == 403
    # Views of a teacher or a class carry the field too; a class without a class teacher has none.
    create(client, 'viewer', login='pupil', grants=[dict(workspace='demo', view='class', entityIds=[A, D])])
    q = signed('pupil')
    assert q.get(f'/api/portal/schedule?workspace=demo&view=class&entityId={A}').json()['classTeacher'] == 'Учитель английского'
    assert q.get(f'/api/portal/schedule?workspace=demo&view=class&entityId={D}').json()['classTeacher'] is None
    assert client.get('/api/portal/schedule?workspace=demo&view=teacher&entityId=t2').json()['classTeacher'] is None
