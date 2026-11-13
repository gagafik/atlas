"""Parent register import: matching, outcomes, opt-ins, limits, atomic issue and replays."""
import base64
import unicodedata
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
                    ';;Макетова Мария (5B)',  # no parent name: matched like any row, labelled by the child
                    'Я' * 161 + ';Длинное;Образцова Анна (3D)',
                    'Семь;Смешанный;Примеров Александр (7A), Эскизов Максим (5B)')  # one child found, one ambiguous
    result = preview(school, file)
    assert result['counts'] == dict(rows=11, ready=2, readyCovered=0, partial=1, partialCovered=0, notFound=2, ambiguous=2,
                                    noChildren=2, noName=1, repeated=1, formula=0, overlap=0, unnamed=1)
    reasons = {row['recipient']: row for row in result['attention']}
    assert [t['name'] for t in reasons['Два Частично']['found']] == ['Образцова Анна'] and reasons['Два Частично']['missing']
    assert reasons['Три Никого']['missing'] and reasons['Четыре Двое']['ambiguous']
    assert reasons['Семь Смешанный']['outcome'] == 'ambiguous' and reasons['Семь Смешанный']['ambiguous']
    mixed = csv_file('Семь;Смешанный;Примеров Александр (7A), Эскизов Максим (5B)')
    for flags in [{}, dict(includePartial=True), dict(includeCovered=True), dict(includePartial=True, includeCovered=True)]:
        issue(school, mixed, expect=422, **flags)  # a row with an ambiguous child is never issued, found child or not
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


@pytest.mark.parametrize('cover', ['pending', 'account'])
def test_a_partly_found_row_whose_child_has_access_needs_both_opt_ins(school, cover):
    if cover == 'pending': issue_one(school, ['p2'])
    else: create(school, 'parent', grants=[dict(workspace='demo', view='student', entityIds=['p2'])])
    file = csv_file('Два;Частично;Образцова Анна (3D), Никто Неизвестный (3D)')
    row = preview(school, file)['attention'][0]
    assert row['outcome'] == 'partial' and row['warnings']
    for flags in [{}, dict(includePartial=True), dict(includeCovered=True)]:
        issue(school, file, expect=422, **flags)
    both, _ = issue(school, file, includePartial=True, includeCovered=True)
    assert recipients(both) == ['Два Частично'] and [t['id'] for t in both['items'][0]['targets']] == ['p2']


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


def test_a_register_of_hundreds_of_rows_opens_and_revokes_completely(school):
    state = api.read('demo')
    for i in range(300):
        state.students.append(state.students[0].model_copy(update=dict(id=f'bulk{i}', name=f'Массов Ученик{i}', classId='c7A', active=True, externalId=None)))
    api.save('demo', state)
    file = csv_file(*[f'Родитель;Номер {i};Массов Ученик{i} (7A)' for i in range(300)])
    issued, _ = issue(school, file)
    assert len(issued['items']) == 300
    detail = school.get(BATCHES + '/' + str(issued['id'])).json()
    assert len(detail['items']) == 300 and not any('code' in i for i in detail['items'])
    assert {i['recipient']: [t['id'] for t in i['targets']] for i in detail['items']} == {f'Родитель Номер {i}': [f'bulk{i}'] for i in range(300)}
    assert {i['status'] for i in detail['items']} == {'pending'}
    register(visitor(), issued['items'][7]['code'], login='bulk-family').raise_for_status()
    revoked = school.post(BATCHES + f"/{issued['id']}/revoke").json()
    assert revoked['revokedCount'] == 299 and len(revoked['items']) == 300
    assert sorted({i['status'] for i in revoked['items']}) == ['accepted', 'revoked']


def add_pupils(*items):
    """Extra invented pupils for one test: (id, name, class name), all active; unknown classes are created."""
    state = api.read('demo')
    for id, name, class_name in items:
        if not any(c.id == 'c' + class_name for c in state.classes):
            state.classes.append(state.classes[0].model_copy(update=dict(id='c' + class_name, name=class_name)))
        state.students.append(state.students[0].model_copy(update=dict(id=id, name=name, classId='c' + class_name, active=True, externalId=None)))
    api.save('demo', state)


def only_attention(result):
    assert len(result['attention']) == 1, result['attention']
    return result['attention'][0]


def test_a_name_without_patronymic_is_not_ready_while_a_longer_namesake_sits_in_the_class(school):
    add_pupils(('q10', 'Примеров Александр Сергеевич', '7A'))
    short = csv_file('Р;17;Примеров Александр (7A)')
    result = preview(school, short)
    assert result['counts']['ready'] == 0 and result['counts']['ambiguous'] == 1
    assert only_attention(result)['ambiguous']
    issue(school, short, expect=422)
    full, _ = issue(school, csv_file('Р;18;Примеров Александр Сергеевич (7A)'))
    assert [t['id'] for t in full['items'][0]['targets']] == ['q10']
    # The same short name is still ready in a class where no longer namesake exists.
    add_pupils(('q11', 'Примеров Александр', '8A'), ('q12', 'Примеров Александр Иванович', '9A'))
    assert preview(school, csv_file('Р;19;Примеров Александр (8A)'))['counts']['ready'] == 1


@pytest.mark.parametrize('school_classes, written', [
    (('7B', '7C'), '7В'),  # Cyrillic В after Latin classes: the third parallel is 7C, not 7B
    (('7B', '7C'), '7С'),
    (('8Б', '8В'), '8B'),  # Latin B after Cyrillic classes: 8B is the second parallel, 8Б
])
def test_a_class_that_matches_only_after_changing_alphabet_is_never_ready(school, school_classes, written):
    add_pupils(('n1', 'Эскизов Тимур', school_classes[0]), ('n2', 'Эскизов Тимур', school_classes[1]))
    file = csv_file(f'Эскизова;Диана;Эскизов Тимур ({written})')
    result = preview(school, file)
    assert result['counts']['ready'] == 0
    assert only_attention(result)['lookalike']
    issue(school, file, expect=422)
    same_letters = csv_file(f'Эскизова;Диана;Эскизов Тимур ({school_classes[0]})')
    assert recipients(issue(school, same_letters)[0]) == ['Эскизова Диана']


@pytest.mark.parametrize('label', ['Имя Отчество', 'Имя, отчество', 'Имя и отчество', 'Имя Отчество родителя'])
def test_a_name_and_patronymic_header_is_the_name_column_and_parents_stay_separate(school, label):
    file = csv_file('Ким;Анна Петровна;Макетова Мария (5B)', 'Ким;Сергей Иванович;Макетова Мария (5B)', header=f'Фамилия;{label};Дети')
    result = preview(school, file)
    assert result['columns']['name'] == 1 and result['counts']['ready'] == 2 and result['counts']['repeated'] == 0
    assert recipients(issue(school, file)[0]) == ['Ким Анна Петровна', 'Ким Сергей Иванович']


def test_a_surname_column_without_a_name_column_is_announced_to_the_administrator(school):
    file = csv_file('Ким;Макетова Мария (5B)', 'Ким;Макетова Мария (5B)', header='Фамилия;Дети')
    result = preview(school, file)
    assert result['counts']['repeated'] == 1 and result['notes']


def test_cells_with_formulas_are_held_back_and_the_warning_is_shown(school):
    from openpyxl import Workbook
    book = Workbook();sheet = book.active
    for row in [('Фамилия', 'Имя', 'Родитель', 'Дети'),
                ('Примерова', 'Светлана', '=A2&" "&B2', 'Примеров Александр (7A)'),
                ('Макетов', 'Игорь', 'Макетов Игорь', 'Макетова Мария (5B)'),
                ('Образцов', 'Пётр', 'Образцов Пётр', '=B4&" (3D)"')]:
        sheet.append(row)
    out = BytesIO();book.save(out)
    file = ('parents.xlsx', out.getvalue())
    columns = dict(surname=2, name=None, children=3)
    result = preview(school, file, columns=columns)
    assert result['counts']['formula'] == 2 and result['counts']['ready'] == 1 and result['notes']
    assert {r['row'] for r in result['attention'] if r['outcome'] == 'formula'} == {2, 4}
    assert recipients(issue(school, file, columns=columns)[0]) == ['Макетов Игорь']


@pytest.mark.parametrize('separator', ['auto', ','])
def test_a_comma_separated_file_with_quoted_children_is_read(school, separator):
    file = csv_file('Р,1,"Примеров Александр (7A), Образцова Анна (3D)"', 'Р,2,Макетова Мария (5B)', header='Фамилия,Имя,Дети')
    result = preview(school, file, delimiter=separator)
    assert not result['needsColumns'] and result['counts']['ready'] == 2
    issued, _ = issue(school, file, delimiter=separator)
    assert sorted(len(i['targets']) for i in issued['items']) == [1, 2]


def test_a_name_made_of_invisible_characters_is_no_name_and_the_row_is_labelled_by_the_child(school):
    file = csv_file('​;​;Примеров Александр (7A)', '​;Один;Макетова Мария (5B)')
    result = preview(school, file)
    assert result['counts']['noName'] == 0 and result['counts']['ready'] == 2 and result['counts']['unnamed'] == 1
    assert recipients(issue(school, file)[0]) == ['Один', 'Родитель: Примеров Александр (7A)']


def test_a_parent_name_longer_than_the_limit_is_no_name_and_is_not_issued(school):
    file = csv_file('Я' * 161 + ';Один;Примеров Александр (7A)')
    result = preview(school, file)
    assert result['counts']['noName'] == 1 and result['counts']['ready'] == 0 and result['counts']['unnamed'] == 0
    issue(school, file, expect=422)


@pytest.mark.parametrize('pupil, written', [
    (('v1', 'Примерова Алёна', '3D'), unicodedata.normalize('NFD', 'Примерова Алёна (3D)')),
    (('v2', "О'Примеров Ильяс", '7A'), 'О’Примеров Ильяс (7A)'),
    (('v3', 'Макетова-Образцова Алина', '7A'), 'Макетова - Образцова Алина (7A)'),
    (('v3', 'Макетова-Образцова Алина', '7A'), 'Макетова Образцова Алина (7A)'),
    (('v3', 'Макетова-Образцова Алина', '7A'), 'Макетова–Образцова Алина (7A)'),
    (('p1', 'Примеров Александр', '7A'), 'Примеров Александр (7-A)'),
])
def test_harmless_spelling_variants_still_find_the_pupil(school, pupil, written):
    if pupil[0] != 'p1': add_pupils(pupil)
    file = csv_file(f'Родитель;Один;{written}')
    assert preview(school, file)['counts']['ready'] == 1
    assert [t['id'] for t in issue(school, file)[0]['items'][0]['targets']] == [pupil[0]]


@pytest.mark.parametrize('pupil, written', [
    (('p1', 'Примеров Александр', '7A'), 'Примеров Aлександр (7A)'),  # Latin A inside a Russian name
    (('v4', 'Образцова Әйгерім', '3D'), 'Образцова Әйгерiм (3D)'),  # Latin i inside a Kazakh name
])
def test_letters_of_another_alphabet_inside_a_name_are_reported_not_folded(school, pupil, written):
    if pupil[0] != 'p1': add_pupils(pupil)
    file = csv_file(f'Родитель;Один;{written}')
    result = preview(school, file)
    assert result['counts']['ready'] == 0 and only_attention(result)['lookalike']
    issue(school, file, expect=422)


def test_one_parent_on_two_rows_with_a_shared_child_gets_no_code_until_the_file_is_fixed(school):
    file = csv_file('Примерова;Светлана;Примеров Александр (7A)',
                    'Примерова;Светлана;"Примеров Александр (7A), Образцова Анна (3D)"',
                    'Примерова;Светлана;Макетова Мария (5B)',  # same name, no shared child: not held back
                    'Образцов;Пётр;Вымыслов Дмитрий (7A)')
    result = preview(school, file)
    assert result['counts']['overlap'] == 2 and result['counts']['ready'] == 2
    assert {r['row'] for r in result['attention'] if r['outcome'] == 'overlap'} == {2, 3}
    issued, _ = issue(school, file)
    assert recipients(issued) == ['Образцов Пётр', 'Примерова Светлана']
    assert [t['id'] for t in next(i for i in issued['items'] if i['recipient'] == 'Примерова Светлана')['targets']] == ['p4']


def test_a_row_without_a_parent_name_is_labelled_by_its_only_child(school):
    file = csv_file(';;Примеров Александр (7A)')
    result = preview(school, file)
    assert result['counts']['ready'] == 1 and result['counts']['unnamed'] == 1 and result['counts']['noName'] == 0
    assert result['notes']
    issued, _ = issue(school, file)
    assert recipients(issued) == ['Родитель: Примеров Александр (7A)']
    assert [t['id'] for t in issued['items'][0]['targets']] == ['p1']


def test_a_label_names_the_first_matched_child_and_counts_the_rest(school):
    file = csv_file(';;Неизвестный Ребёнок (7A), Макетова Мария (5B), Примеров Александр (7A), Образцова Анна (3D)')
    issued, _ = issue(school, file, includePartial=True)
    assert recipients(issued) == ['Родитель: Макетова Мария (5B) и ещё 2']
    assert [t['id'] for t in issued['items'][0]['targets']] == ['p4', 'p1', 'p2']


def test_two_rows_without_names_and_with_the_same_child_are_two_parents_who_each_see_that_child(school):
    file = csv_file(';;Примеров Александр (7A)', ';;Примеров Александр (7A)', ';;Примеров Александр (7A)')
    result = preview(school, file)
    assert result['counts']['ready'] == 3 and result['counts']['repeated'] == 0 and result['counts']['overlap'] == 0
    issued, _ = issue(school, file)
    assert recipients(issued) == ['Родитель 2: Примеров Александр (7A)', 'Родитель 3: Примеров Александр (7A)',
                                  'Родитель: Примеров Александр (7A)']
    for number, item in enumerate(issued['items']):
        response = register(visitor(), item['code'], login=f'nameless-{number}')
        assert response.status_code == 200, response.text
        assert [g['entityIds'] for g in response.json()['user']['grants']] == [['p1']]


def test_labels_stay_distinct_when_the_same_children_come_in_another_order_or_overlap(school):
    file = csv_file(';;Примеров Александр (7A), Образцова Анна (3D)', ';;Образцова Анна (3D), Примеров Александр (7A)',
                    ';;Примеров Александр (7A), Макетова Мария (5B)')
    issued, _ = issue(school, file)
    labels = recipients(issued)
    assert len(set(labels)) == 3 and all(len(label) <= 160 for label in labels)
    assert 'Родитель 2: Образцова Анна (3D) и ещё 1' in labels


def test_a_named_and_a_nameless_row_for_the_same_child_are_both_issued(school):
    file = csv_file('Примерова;Светлана;Примеров Александр (7A)', ';;Примеров Александр (7A)')
    result = preview(school, file)
    assert result['counts']['ready'] == 2 and result['counts']['overlap'] == 0 and result['counts']['repeated'] == 0
    # The rule against one parent on two rows still holds between named rows only.
    mixed = csv_file('Примерова;Светлана;Примеров Александр (7A)', ';;Примеров Александр (7A)',
                     'Примерова;Светлана;Примеров Александр (7A), Образцова Анна (3D)')
    counts = preview(school, mixed)['counts']
    assert counts['overlap'] == 2 and counts['ready'] == 1 and counts['unnamed'] == 1
    assert recipients(issue(school, file)[0]) == ['Примерова Светлана', 'Родитель: Примеров Александр (7A)']


def test_a_parent_really_named_like_a_label_keeps_the_name_and_the_label_moves_on(school):
    # The surname column holds "Родитель:" and the name column the rest: the recipient text equals the generated label.
    file = csv_file('Родитель:;Примеров Александр (7A);Примеров Александр (7A)', ';;Примеров Александр (7A)')
    result = preview(school, file)
    assert result['counts']['ready'] == 2 and result['counts']['overlap'] == 0 and result['counts']['unnamed'] == 1
    issued, _ = issue(school, file)
    assert recipients(issued) == ['Родитель 2: Примеров Александр (7A)', 'Родитель: Примеров Александр (7A)']
    assert all([t['id'] for t in item['targets']] == ['p1'] for item in issued['items'])


def test_a_row_without_a_name_and_with_an_unknown_child_is_not_found_and_not_issued(school):
    file = csv_file(';;Неизвестный Ребёнок (7A)')
    result = preview(school, file)
    assert result['counts']['notFound'] == 1 and result['counts']['unnamed'] == 1 and result['counts']['ready'] == 0
    assert only_attention(result)['recipient'] == '' and only_attention(result)['outcome'] == 'notFound'
    issue(school, file, expect=422)


def test_the_numbering_is_the_same_for_the_preview_and_the_issue(school):
    file = csv_file(';;Примеров Александр (7A)', 'Образцов;Пётр;Вымыслов Дмитрий (7A)', ';;Примеров Александр (7A)')
    first, second = preview(school, file), preview(school, file)
    assert first['previewToken'] == second['previewToken']
    issued, _ = issue(school, file)  # the issue evaluates again and must accept the preview token
    assert {i['key']: i['recipient'] for i in issued['items']} == {
        'r2': 'Родитель: Примеров Александр (7A)', 'r3': 'Образцов Пётр', 'r4': 'Родитель 2: Примеров Александр (7A)'}


def test_a_generated_label_never_exceeds_the_limit(school):
    long_name, longest = 'Длинов ' + 'А' * 140, 'Ж' * 160
    add_pupils(('w1', long_name, '7A'), ('w2', longest, '7A'), ('w3', 'Короткова Ева', '7A'))
    file = csv_file(f';;{long_name} (7A), Короткова Ева (7A)', f';;{longest} (7A)', f';;{longest} (7A)', f';;{longest} (7A), {long_name} (7A)')
    issued, _ = issue(school, file)
    labels = [i['recipient'] for i in issued['items']]
    assert len(labels) == 4 and len(set(labels)) == 4 and all(0 < len(label) <= 160 for label in labels)
