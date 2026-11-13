"""Parent register import: matching, outcomes, opt-ins, limits, atomic issue and replays."""
import base64
from io import BytesIO
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from backend import api
from test_access import client, enable, create, signed
from test_invitations import register, visitor, issue as issue_one
from test_invitation_batches import count

BASE = '/api/access/invitations/registers'
BATCHES = '/api/access/invitations/batches'
HEADER = 'Фамилия;Имя;Дети'

# Invented pupils (surnames from tools/demo/names.py): id, name, class, active.
PUPILS = [
    ('p1', 'Примеров Александр', '7A', True), ('p2', 'Образцова Анна', '3D', True), ('p3', 'Вымыслов Дмитрий', '7A', True),
    ('p4', 'Макетова Мария', '5B', True), ('p5', 'Эскизов Максим', '5B', True), ('p6', 'Эскизов Максим', '5B', True),
    ('p7', 'Шаблонов Иван', '7A', False), ('p8', 'Пробникова Дарья', '3D', True),
]


@pytest.fixture
def school(client):
    enable(client)
    state = api.read('demo')
    template = state.classes[0]
    for name in ['7A', '3D', '5B']: state.classes.append(template.model_copy(update=dict(id='c' + name, name=name)))
    for id, name, class_name, active in PUPILS:
        state.students.append(state.students[0].model_copy(update=dict(id=id, name=name, classId='c' + class_name, active=active, externalId=None)))
    api.save('demo', state)
    return client


def csv_file(*rows, header=HEADER, name='parents.csv'):
    text = '\r\n'.join([header, *rows]) if header else '\r\n'.join(rows)
    return name, text.encode('utf-8-sig')


def source(file, **extra):
    name, raw = file
    return dict(workspace='demo', filename=name, content=base64.b64encode(raw).decode(), days=7) | extra


def preview(c, file, expect=200, **extra):
    response = c.post(BASE + '/preview', json=source(file, **extra))
    assert response.status_code == expect, response.text
    return response.json()


def issue(c, file, expect=200, requestId=None, **extra):
    body = source(file, **{k: v for k, v in extra.items() if k not in ('includePartial', 'includeCovered')})
    checked = preview(c, file, **{k: v for k, v in extra.items() if k not in ('includePartial', 'includeCovered')})
    payload = body | dict(previewToken=checked['previewToken'], requestId=requestId or uuid4().hex) | {
        k: v for k, v in extra.items() if k in ('includePartial', 'includeCovered')}
    response = c.post(BASE, json=payload)
    assert response.status_code == expect, response.text
    return response.json(), payload


def recipients(receipt): return sorted(i['recipient'] for i in receipt['items'])


def test_children_match_by_class_and_name_ignoring_case_spaces_and_word_order(school):
    file = csv_file('Родитель;Первый;Примеров Александр (7A), Образцова Анна (3D)',
                    'Родитель;Второй;  АЛЕКСАНДР   примеров (7a)',
                    'Родитель;Третий;вымыслов дмитрий (7А)',  # Cyrillic А in the class name
                    'Родитель;Четвёртый;Примеров Александр (3D)')  # right pupil, wrong class
    result = preview(school, file)
    assert result['counts']['ready'] == 3 and result['counts']['notFound'] == 1
    issued, _ = issue(school, file)
    by_name = {i['recipient']: [t['id'] for t in i['targets']] for i in issued['items']}
    assert by_name == {'Родитель Первый': ['p1', 'p2'], 'Родитель Второй': ['p1'], 'Родитель Третий': ['p3']}


def test_every_outcome_is_counted_and_the_attention_list_gives_reasons(school):
    file = csv_file('Один;Готов;Примеров Александр (7A)',
                    'Два;Частично;Образцова Анна (3D), Несуществующий Ученик (3D)',
                    'Три;Никого;Неизвестный Ребёнок (7A)',
                    'Четыре;Двое;Эскизов Максим (5B)',
                    'Пять;Выбыл;Шаблонов Иван (7A)',
                    'Шесть;Без детей;',
                    'Шесть;Прочерк;-',
                    'Один;Готов;Примеров Александр (7A)',
                    ';;Макетова Мария (5B)')
    result = preview(school, file)
    assert result['counts'] == dict(rows=9, ready=1, readyCovered=0, partial=1, partialCovered=0, notFound=2, ambiguous=1,
                                    noChildren=2, noName=1, repeated=1)
    reasons = {row['recipient']: row for row in result['attention']}
    assert [t['name'] for t in reasons['Два Частично']['found']] == ['Образцова Анна'] and reasons['Два Частично']['missing']
    assert reasons['Три Никого']['missing'] and reasons['Четыре Двое']['ambiguous']
    assert reasons['Пять Выбыл']['missing']  # a pupil who left is not an active match
    assert {row['outcome'] for row in result['attention']} == {'partial', 'notFound', 'ambiguous', 'noName'}


def test_partly_found_rows_are_issued_only_when_the_request_says_so(school):
    file = csv_file('Один;Готов;Примеров Александр (7A)', 'Два;Частично;Образцова Анна (3D), Никто Неизвестный (3D)')
    first, _ = issue(school, file)
    assert recipients(first) == ['Один Готов']
    second, _ = issue(school, file, includePartial=True)
    assert recipients(second) == ['Два Частично']  # the first row now has a pending code and is skipped as covered
    assert [t['id'] for t in next(i for i in second['items'] if i['recipient'] == 'Два Частично')['targets']] == ['p2']
    nothing = csv_file('Два;Частично;Образцова Анна (3D), Никто Неизвестный (3D)')
    issue(school, nothing, expect=422)


def test_existing_access_or_a_pending_code_skips_the_row_unless_asked(school):
    issue_one(school, ['p1'])
    file = csv_file('Один;Покрыт;Примеров Александр (7A)', 'Два;Свободен;Макетова Мария (5B)')
    result = preview(school, file)
    assert result['counts']['readyCovered'] == 1 and result['counts']['ready'] == 1
    covered = next(r for r in result['attention'] if r['recipient'] == 'Один Покрыт')
    assert {w['kind'] for w in covered['warnings']} == {'invitations'}
    assert recipients(issue(school, file)[0]) == ['Два Свободен']
    assert recipients(issue(school, file, includeCovered=True)[0]) == ['Два Свободен', 'Один Покрыт']


def test_a_registered_parent_sees_exactly_the_matched_children(school):
    file = csv_file('Родитель;Семья;Примеров Александр (7A), Образцова Анна (3D), Неизвестный Ученик (5B), Макетова Мария (5B)')
    issued, _ = issue(school, file, includePartial=True)
    assert [t['id'] for t in issued['items'][0]['targets']] == ['p1', 'p2', 'p4']
    response = register(visitor(), issued['items'][0]['code'], login='register-family')
    assert response.status_code == 200, response.text
    user = response.json()['user']
    assert user['role'] == 'parent' and sorted(user['grants'][0]['entityIds']) == ['p1', 'p2', 'p4']


def test_file_with_more_than_the_limit_is_refused_with_the_number_in_the_message(school):
    rows = [f'Родитель;Номер {i};-' for i in range(3001)]
    refused = school.post(BASE + '/preview', json=source(csv_file(*rows)))
    assert refused.status_code == 422 and '3000' in refused.json()['detail']
    accepted = preview(school, csv_file(*rows[:3000]))
    assert accepted['counts']['rows'] == 3000 and accepted['counts']['noChildren'] == 3000
    body = source(csv_file(*rows))
    assert school.post(BASE, json=body | dict(previewToken='0' * 64, requestId=uuid4().hex)).status_code == 422


def test_attention_list_is_paged_while_counts_cover_the_whole_file(school):
    file = csv_file(*[f'Родитель;Номер {i};Нет Такого{i} (7A)' for i in range(130)])
    first = preview(school, file)
    second = preview(school, file, offset=100)
    assert first['counts']['notFound'] == 130 and first['attentionTotal'] == 130
    assert len(first['attention']) + len(second['attention']) == 130
    assert not {r['row'] for r in first['attention']} & {r['row'] for r in second['attention']}
    assert first['previewToken'] == second['previewToken']


def test_columns_are_detected_by_header_and_can_be_chosen(school):
    swapped = csv_file('Примеров Александр (7A);Родитель;Один', header='Дети;Имя;Фамилия')
    detected = preview(school, swapped)
    assert detected['columns'] == dict(surname=2, name=1, children=0) and detected['counts']['ready'] == 1
    unknown = csv_file('Родитель Один;Примеров Александр (7A)', header='Колонка А;Колонка Б')
    assert preview(school, unknown)['needsColumns']
    chosen = preview(school, unknown, columns=dict(surname=0, name=None, children=1))
    assert chosen['counts']['ready'] == 1 and not chosen['needsColumns']
    issued, _ = issue(school, unknown, columns=dict(surname=0, name=None, children=1))
    assert recipients(issued) == ['Родитель Один']
    assert school.post(BASE + '/preview', json=source(unknown, columns=dict(surname=0, name=0, children=1))).status_code == 422
    assert school.post(BASE + '/preview', json=source(unknown, columns=dict(surname=0, name=None, children=7))).status_code == 422


def test_xlsx_and_csv_give_the_same_result(school):
    from openpyxl import Workbook
    book = Workbook();sheet = book.active
    for row in [('Фамилия', 'Имя', 'Дети'), ('Родитель', 'Один', 'Примеров Александр (7A), Образцова Анна (3D)'), ('Родитель', 'Два', '-')]:
        sheet.append(row)
    out = BytesIO();book.save(out)
    from_xlsx = preview(school, ('parents.xlsx', out.getvalue()))
    from_csv = preview(school, csv_file('Родитель;Один;Примеров Александр (7A), Образцова Анна (3D)', 'Родитель;Два;-'))
    assert from_xlsx['counts'] == from_csv['counts'] and from_xlsx['columns'] == from_csv['columns']
    assert issue(school, ('parents.xlsx', out.getvalue()))[0]['items'][0]['targets'][1]['id'] == 'p2'


def test_a_repeat_after_a_lost_answer_returns_the_same_batch_without_new_codes(school):
    file = csv_file('Один;Готов;Примеров Александр (7A)', 'Два;Готов;Макетова Мария (5B)')
    issued, payload = issue(school, file)
    codes = {i['code'] for i in issued['items']}
    retry = school.post(BASE, json=payload)
    assert retry.status_code == 200 and retry.json()['id'] == issued['id'] and retry.json()['replayed']
    assert not any('code' in item for item in retry.json()['items'])
    with api.connect() as db:
        assert db.execute('SELECT count(*) FROM access_invitations').fetchone()[0] == 2
        assert db.execute('SELECT count(*) FROM invitation_batches').fetchone()[0] == 1
    register(visitor(), next(iter(codes)), login='first-family').raise_for_status()
    assert school.post(BASE, json=payload).json()['replayed']
    assert school.post(BASE, json=payload | dict(includePartial=True)).status_code == 409
    create(school, 'admin', login='second-admin')
    assert signed('second-admin').post(BASE, json=payload).status_code == 409


@pytest.mark.parametrize('change', ['school', 'pending', 'account', 'file'])
def test_a_stale_preview_is_refused_and_nothing_is_created(school, change):
    file = csv_file('Один;Готов;Примеров Александр (7A)', 'Два;Готов;Макетова Мария (5B)')
    checked = preview(school, file)
    if change == 'pending': issue_one(school, ['p4'])
    elif change == 'account': create(school, 'parent', grants=[dict(workspace='demo', view='student', entityIds=['p1'])])
    elif change == 'school':
        state = api.read('demo');next(s for s in state.students if s.id == 'p4').active = False;api.save('demo', state)
    else: file = csv_file('Один;Готов;Примеров Александр (7A)')
    response = school.post(BASE, json=source(file) | dict(previewToken=checked['previewToken'], requestId=uuid4().hex))
    assert response.status_code == 409, response.text
    with api.connect() as db:
        assert count(db, 'invitation_batches') == 0
        assert count(db, 'access_invitations') == (1 if change == 'pending' else 0)


def test_a_failure_in_the_middle_rolls_back_the_whole_register(school, monkeypatch):
    file = csv_file('Один;Готов;Примеров Александр (7A)', 'Два;Готов;Макетова Мария (5B)')
    checked = preview(school, file)
    payload = source(file) | dict(previewToken=checked['previewToken'], requestId=uuid4().hex)
    original = api.access.record
    def broken(db, action, *args, **kwargs):
        if action == 'invitations.register.created': raise RuntimeError('simulated audit failure')
        return original(db, action, *args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(api.access, 'record', broken)
        with pytest.raises(RuntimeError, match='simulated audit failure'): school.post(BASE, json=payload)
    assert school.get(BATCHES + '?workspace=demo').json()['items'] == [] and school.get('/api/access/invitations').json()['items'] == []
    assert school.post(BASE, json=payload).status_code == 200


def test_a_register_is_one_history_entry_without_codes_and_revoke_keeps_registered_families(school):
    file = csv_file('Один;Готов;Примеров Александр (7A)', 'Два;Готов;Макетова Мария (5B)', 'Три;Готов;Образцова Анна (3D)')
    issued, _ = issue(school, file)
    listing = school.get(BATCHES + '?workspace=demo').json()['items']
    assert len(listing) == 1 and listing[0]['id'] == issued['id'] and listing[0]['count'] == 3 and listing[0]['kind'] == 'register'
    assert listing[0]['source'] == 'parents.csv'
    codes = {i['code'] for i in issued['items']}
    with api.connect() as db:
        dump = '\n'.join(db.iterdump());assert not any(code in dump for code in codes)
    assert not any(code in str(school.get('/api/access/audit').json()) for code in codes)
    detail = school.get(BATCHES + '/' + str(issued['id'])).json()
    assert detail['kind'] == 'register' and detail['summary']['issued'] == 3 and not any('code' in i for i in detail['items'])
    register(visitor(), issued['items'][0]['code'], login='kept-family').raise_for_status()
    revoked = school.post(BATCHES + f"/{issued['id']}/revoke").json()
    assert revoked['revokedCount'] == 2 and sorted(i['status'] for i in revoked['items']) == ['accepted', 'revoked', 'revoked']
    assert register(visitor(), issued['items'][1]['code'], login='late-family').status_code == 400
    assert any(r['action'] == 'invitations.register.created' for r in school.get('/api/access/audit').json())


def test_only_the_administrator_can_preview_or_issue(school):
    create(school);create(school, 'teacher');create(school, 'parent', login='some-parent')
    file = csv_file('Один;Готов;Примеров Александр (7A)')
    body = source(file);full = body | dict(previewToken='0' * 64, requestId=uuid4().hex)
    for login in ['editor', 'teacher', 'some-parent']:
        other = signed(login)
        assert other.post(BASE + '/preview', json=body).status_code == 403
        assert other.post(BASE, json=full).status_code == 403
    assert visitor().post(BASE + '/preview', json=body).status_code == 401
    assert visitor().post(BASE, json=full).status_code == 401
    bare = TestClient(api.app, cookies=school.cookies)
    assert bare.post(BASE + '/preview', json=body).status_code == 403
    assert bare.post(BASE, json=full).status_code == 403
    with api.connect() as db: assert count(db, 'access_invitations') == 0


def test_the_hand_made_batch_keeps_its_limit_of_fifty(school):
    rows = [dict(key=f'row{i}', recipient=f'Родитель {i}', role='parent', entityIds=['p1']) for i in range(51)]
    assert school.post(BATCHES + '/preview', json=dict(workspace='demo', days=7, rows=rows)).status_code == 422
