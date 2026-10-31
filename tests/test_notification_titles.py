"""Titles and checked links for the modules that used to leave only a category: what the entry says, what it never says, and the answer once access is gone."""
import json
import re
from uuid import uuid4

from backend import api
from test_access import client, enable, create, signed
from test_messages import url, detail, payload
from test_message_feed import announce
from test_message_moderation import seed, report, decision, case
import test_absences as absences
import test_absence_bulk as bulk
import test_absence_school as school
import test_meetings as meet
import test_meeting_waitlist as waits
import test_school_forms as forms
import test_families as families
import test_clubs as club_tests
import test_homework as hw
import test_quizzes as quiz_tests
import test_assessment_calendar as assessments
import test_behaviour as behaviour
import pytest
from datetime import date
from backend import clubs, clubs_routes, clubs_sessions, quizzes, quiz_bank, quiz_attempts, quiz_catalog
from test_journal import DAY


def inbox(c, query=''):
    response = c.get('/api/notifications/inbox' + query)
    assert response.status_code == 200, response.text
    return response.json()


def items(c, action):
    return [i for i in inbox(c)['items'] if i['action'] == action]


def open_notice(c, id):
    return c.post(f'/api/notifications/{id}/open')


def read(c, id):
    return next(i for i in inbox(c)['items'] if i['id'] == id)['read']


def restrict(client, account, **values):
    """Change the account the way an administrator does; the session ends, so the caller signs in again."""
    current = next(u for u in client.get('/api/access/users').json() if u['id'] == account['id'])
    update = {k: v for k, v in current.items() if k != 'id'} | values
    response = client.put('/api/access/users/' + account['id'], json=update)
    assert response.status_code == 200, response.text


def named(client):
    """setup() with distinctive names, because every test account is called «Сотрудник» otherwise."""
    enable(client)
    parent = create(client, 'parent', name='Иванова Анна')
    teacher = create(client, 'teacher', name='Петрова Мария')
    other = create(client, 'parent', login='other', name='Сидорова Ольга', grants=[dict(workspace='demo', view='student', entityIds=['s2'])])
    return dict(parent=parent, teacher=teacher, other=other)


def test_message_title_open_and_lost_access(client):
    people = named(client); parent = signed('parent'); teacher = signed('teacher')
    secret = 'Здравствуйте, уточните задание.'
    id = parent.post(url(), json=payload(people['teacher']['id'])).json()['id']
    [entry] = items(teacher, 'message.received')
    assert 'Иванова Анна' in entry['title'] and 'Вопрос по уроку' in entry['title'] and entry['link'] is True and entry['category'] == 'messages'
    assert entry['students'][0]['id'] == 's1' and secret not in teacher.get('/api/notifications/inbox').text
    opened = open_notice(teacher, entry['id'])
    assert opened.status_code == 200 and opened.json() == dict(module='messages', workspace='demo', recordId=id, studentId='s1', day=None)
    # Several replies of one conversation leave one unread entry, not a pile.
    reply = lambda text: parent.post(url('threads/' + id + '/reply'), json=dict(requestId=str(uuid4()), version=1, text=text)).status_code
    assert reply('Ещё раз.') == reply('И ещё.') == 200
    unread = [i for i in items(teacher, 'message.received') if not i['read']]
    assert len(unread) == 1 and secret not in teacher.get('/api/notifications/inbox').text
    # The teacher stops teaching the pupil: the conversation is kept history, which still opens (the same answer as reading the thread).
    restrict(client, people['teacher'], grants=[dict(workspace='demo', view='teacher', entityIds=['t1'])])
    teacher = signed('teacher')
    again = items(teacher, 'message.received')[0]
    assert open_notice(teacher, again['id']).status_code == 200 and detail(teacher, id)['readOnly']
    # A stranger never gets the entry, and an entry id of somebody else is not found.
    assert items(signed('other'), 'message.received') == [] and open_notice(signed('other'), again['id']).status_code == 404


def test_withdrawn_announcement_loses_title(client):
    people = named(client); teacher = signed('teacher'); parent = signed('parent')
    create(client, 'parent', login='second', name='Второй родитель')
    second = signed('second')
    id = announce(teacher)
    [mine] = items(parent, 'message.received'); [unread] = items(second, 'message.received')
    assert 'Экскурсия' in mine['title'] and 'Петрова Мария' in mine['title']
    assert parent.post('/api/notifications/read', json=dict(ids=[mine['id']])).status_code == 200
    assert teacher.post(url('threads/' + id + '/change'), json=dict(version=1, action='withdraw')).status_code == 200
    [after] = items(parent, 'message.received')
    assert 'title' not in after and 'link' not in after and open_notice(parent, after['id']).status_code == 404
    assert items(second, 'message.received') == [] and open_notice(second, unread['id']).status_code == 404


def test_family_lost_child_message(client):
    people = named(client); teacher = signed('teacher'); parent = signed('parent')
    sent = teacher.post(url(), json=payload(people['parent']['id'])); assert sent.status_code == 200, sent.text
    [entry] = items(parent, 'message.received')
    assert entry['students'][0]['id'] == 's1' and entry['link'] is True
    restrict(client, people['parent'], grants=[dict(workspace='demo', view='student', entityIds=['s2'])])
    parent = signed('parent')
    [shut] = items(parent, 'message.received')
    assert shut['restricted'] is True and 'title' not in shut and not shut['read']
    assert open_notice(parent, shut['id']).status_code == 404
    # A refused open still reads the entry, so it cannot stay unread for ever.
    assert read(parent, shut['id']) is True


def test_report_and_moderation_titles(client):
    people, parent, teacher, thread, post, file = seed(client)
    made = report(teacher, post); assert made.status_code == 200, made.text
    id = made.json()['id']
    [asked] = items(client, 'message.reported')
    assert asked['link'] is True and asked['title'].startswith('Жалоба от') and 'Проверьте это сообщение' not in client.get('/api/notifications/inbox').text
    assert 'Уникальный спорный текст' not in client.get('/api/notifications/inbox').text
    opened = open_notice(client, asked['id']); assert opened.status_code == 200 and opened.json()['module'] == 'message_reports' and opened.json()['recordId'] == id
    assert decision(client, id, 'hide').status_code == 200
    # Only the person the entry was written for can open it.
    assert open_notice(teacher, asked['id']).status_code == 404
    for user in (teacher, parent):
        [verdict] = items(user, 'message.moderated')
        assert verdict['title'] == 'Сообщение скрыто администратором'
        text = user.get('/api/notifications/inbox').text
        assert 'Проверено администратором' not in text and 'Уникальный спорный текст' not in text


def dated(day=DAY):
    return f'{day[8:10]}.{day[5:7]}'


def test_absence_title_and_lost_access(client):
    person, aide, parent, teacher = absences.setup(client)
    notice = absences.send(parent)
    [asked] = items(teacher, 'absence.submitted')
    assert dated() in asked['title'] and asked['students'][0]['id'] == 's1' and asked['link'] is True and asked['category'] == 'absences'
    for account in (parent, teacher): assert 'Личная причина' not in account.get('/api/notifications/inbox').text
    assert absences.decide(teacher, notice, 'approve', comment='Скрытый комментарий').status_code == 200
    [decided] = [i for i in items(parent, 'absence.updated') if 'title' in i]
    assert 'одобрено' in decided['title'] and 'Скрытый комментарий' not in parent.get('/api/notifications/inbox').text
    opened = open_notice(parent, decided['id'])
    assert opened.status_code == 200 and opened.json() == dict(module='absences', workspace='demo', recordId=notice['id'], studentId='s1', day=None)
    # The reviewer is taken off the class: the entry stays, the record does not open any more.
    [shown] = [i for i in items(teacher, 'absence.updated') if 'title' in i]
    absences.reviewers({})
    assert open_notice(teacher, shown['id']).status_code == 404 and read(teacher, shown['id']) is True
    # The family loses the child: restricted entry, closed record.
    restrict(client, person, grants=[dict(workspace='demo', view='student', entityIds=['s2'])])
    parent = signed('parent')
    [shut] = [i for i in items(parent, 'absence.updated') if i['id'] == decided['id']]
    assert shut['restricted'] is True and 'title' not in shut and open_notice(parent, shut['id']).status_code == 404


def test_decision_supersedes_older_title(client):
    _, _, parent, teacher = absences.setup(client)
    notice = absences.send(parent)
    mine = lambda c: [i for i in inbox(c)['items'] if i['action'].startswith('absence.') and 'title' in i]
    assert len(mine(parent)) == len(mine(teacher)) == 1
    approved = absences.decide(teacher, notice); assert approved.status_code == 200
    assert parent.post('/api/notifications/read', json=dict(throughId=inbox(parent)['latestId'])).status_code == 200
    assert absences.decide(teacher, approved.json(), 'revoke').status_code == 200
    for account in (parent, teacher):
        [only] = mine(account)
        assert 'решение отменено' in only['title']


def test_bulk_and_school_note_titles(client):
    _, _, parent, teacher = absences.setup(client)
    notices = bulk.two(parent)
    prepared = bulk.prepare(teacher, notices)
    assert bulk.apply(teacher, prepared).status_code == 200
    for account in (teacher, parent):
        [decided] = items(account, 'absence.updated')
        assert decided['title'] == 'Решения по заявлениям об отсутствии: 2' and 'Проверены все выбранные' not in account.get('/api/notifications/inbox').text
        opened = open_notice(account, decided['id'])
        assert opened.status_code == 200 and opened.json()['recordId'] == notices[0]['id']


def test_school_note_titles(client):
    parent_account, _, parent, teacher, pupil = school.staff_setup(client)
    school.note(teacher)
    for account in (pupil, parent):
        [recorded] = items(account, 'absence.recorded')
        assert recorded['title'] == f'Школа внесла записку об отсутствии: {dated()}' and recorded['students'][0]['id'] == 's1'
        opened = open_notice(account, recorded['id'])
        assert opened.status_code == 200 and opened.json()['module'] == 'absences' and opened.json()['studentId'] == 's1'
        assert 'Олимпиада' not in account.get('/api/notifications/inbox').text


def test_other_guardian_learns_only_attendance(client):
    _, _, parent, teacher = absences.setup(client)
    notice = absences.send(parent)
    assert absences.decide(teacher, notice).status_code == 200
    other = signed('other-parent')
    [seen] = items(other, 'journal.updated')
    assert seen['title'] == 'Изменились отметки посещаемости' and 'link' not in seen and seen['students'][0]['id'] == 's1'
    text = other.get('/api/notifications/inbox').text
    assert 'Личная причина' not in text and 'заявлен' not in text.lower()
    assert items(other, 'absence.updated') == [] and open_notice(other, seen['id']).status_code == 404


GUARDIAN = dict(relationship='father', verified=True, evidence='Свидетельство о рождении')


def save_link(client, account, student='s1', **values):
    response = families.save(client, account['id'], student, **values); assert response.status_code == 200, response.text


def second_guardian(client):
    return families.account(client, 'other-parent')


@pytest.mark.parametrize('downgrade', [dict(relationship='representative'), dict(verified=False)])
def test_absence_titles_close_when_guardian_status_goes(client, downgrade):
    _, _, parent, teacher = absences.setup(client)
    account = second_guardian(client); save_link(client, account, **GUARDIAN)
    notice = absences.send(parent); assert absences.decide(teacher, notice, 'reject').status_code == 200
    other = signed('other-parent')
    [before] = items(other, 'absence.updated')
    assert dated() in before['title'] and 'отклонено' in before['title'] and before['link'] is True
    # The grant stays, only the legal status goes: the notice is no longer theirs to know about.
    save_link(client, account, **GUARDIAN | downgrade)
    other = signed('other-parent')
    [after] = items(other, 'absence.updated')
    assert after['id'] == before['id'] and not {'title', 'students', 'link', 'restricted'} & set(after)
    assert 'Ученик 1' not in other.get('/api/notifications/inbox').text and open_notice(other, after['id']).status_code == 404
    assert read(other, after['id']) is True


def test_absence_titles_stay_for_what_the_link_does_not_decide(client):
    person, _, parent, teacher = absences.setup(client)
    account = second_guardian(client); save_link(client, account, **GUARDIAN)
    notice = absences.send(parent); assert absences.decide(teacher, notice).status_code == 200
    school.note(client, studentIds=['s1'], codeId='school', startDate='2026-08-18', endDate='2026-08-18')
    other = signed('other-parent')
    assert len(items(other, 'absence.updated')) == 1 and len(items(other, 'absence.recorded')) == 1
    # The second guardian drops to an unverified link; the first one's own note keeps its entry, and so does a note of the school.
    save_link(client, account, relationship='unspecified', verified=False, evidence='')
    save_link(client, person, relationship='representative', verified=False, evidence='')
    other = signed('other-parent'); parent = signed('parent')
    [note] = items(other, 'absence.recorded')
    assert 'Школа внесла записку' in note['title'] and note['link'] is True and open_notice(other, note['id']).status_code == 200
    [shut] = items(other, 'absence.updated'); assert 'title' not in shut and open_notice(other, shut['id']).status_code == 404
    [own] = [i for i in items(parent, 'absence.updated') if 'title' in i]
    assert 'одобрено' in own['title'] and own['link'] is True and open_notice(parent, own['id']).status_code == 200


def test_absence_titles_of_another_child_stay(client):
    _, _, parent, teacher = absences.setup(client)
    account = second_guardian(client); save_link(client, account, **GUARDIAN)
    notice = absences.send(parent); assert absences.decide(teacher, notice).status_code == 200
    # A link for a different pupil is saved, and it is not a legal one.
    save_link(client, account, 's2', relationship='unspecified', verified=False, evidence='')
    other = signed('other-parent')
    [kept] = items(other, 'absence.updated')
    assert 'одобрено' in kept['title'] and kept['link'] is True and open_notice(other, kept['id']).status_code == 200


def test_meeting_published_title_and_access(client, monkeypatch):
    id, teacher_account, parent_account = meet.setup(client, monkeypatch)
    late = create(client, 'parent', login='late')
    meet.publish(client, id)
    parent = signed('parent'); teacher = signed('teacher')
    for account in (parent, teacher):
        [entry] = items(account, 'meeting.published')
        assert entry['title'] == 'Открыта запись: «Встречи с родителями»' and entry['link'] is True
        opened = open_notice(account, entry['id'])
        assert opened.status_code == 200 and opened.json() == dict(module='meeting_events', workspace='demo', recordId=id, studentId=None, day=None)
    # The family loses the child: the event is no longer theirs.
    [kept] = items(signed('late'), 'meeting.published')
    restrict(client, late, grants=[])
    assert open_notice(signed('late'), kept['id']).status_code == 404
    # A cancelled event is closed to families, whatever they once saw; the administrator still reaches it.
    [entry] = items(parent, 'meeting.published')
    assert meet.transition(client, id, 'cancel', reason='Школа закрыта').status_code == 200
    assert open_notice(parent, entry['id']).status_code == 404
    assert read(parent, entry['id']) is True


def test_waitlist_offer_title(client, monkeypatch):
    eid, teacher_account, parent_account, parent, wid = waits.prepared(client, monkeypatch)
    waits.offered(client, parent, eid, wid, reason='Секретная причина предложения')
    [offer] = items(parent, 'meeting.waitlist_offer')
    assert re.fullmatch(r'Предложено время: «Встречи с родителями», \d\d\.\d\d \d\d:\d\d', offer['title']) and offer['link'] is True and offer['students'][0]['id'] == 's1'
    text = parent.get('/api/notifications/inbox').text
    assert 'Секретная причина' not in text and 'После 17:00' not in text
    opened = open_notice(parent, offer['id'])
    assert opened.status_code == 200 and opened.json()['module'] == 'meeting_waitlist' and opened.json()['recordId'] == wid
    # The administrator takes the offer back: a new entry about the request, still without the reason.
    response, _ = waits.change(client, eid, wid, 'revoke', reason='Секретный отзыв'); assert response.status_code == 200
    [changed] = items(parent, 'meeting.waitlist_changed')
    assert changed['title'] == 'Изменилась заявка в очереди: «Встречи с родителями»' and 'Секретный отзыв' not in parent.get('/api/notifications/inbox').text
    assert open_notice(parent, changed['id']).status_code == 200
    # Another family's account cannot open it; the family that lost the child cannot either.
    create(client, 'parent', login='stranger', grants=[dict(workspace='demo', view='student', entityIds=['s2'])])
    assert open_notice(signed('stranger'), changed['id']).status_code == 404
    restrict(client, parent_account, grants=[dict(workspace='demo', view='student', entityIds=['s2'])])
    parent = signed('parent')
    [shut] = items(parent, 'meeting.waitlist_offer')
    assert shut['restricted'] is True and open_notice(parent, shut['id']).status_code == 404


def test_form_published_assigned_submitted_titles(client):
    enable(client)
    teacher_account = create(client, 'teacher', name='Петрова Мария'); parent_account = create(client, 'parent')
    id = forms.new(client); forms.configure(client, id); forms.assign(client, id, teacher_account)
    teacher = signed('teacher')
    [assigned] = items(teacher, 'forms.assigned')
    assert assigned['title'] == 'Назначена форма: «School form»' and assigned['link'] is True
    forms.publish(client, id)
    parent = signed('parent')
    [published] = items(parent, 'forms.published')
    assert published['title'] == 'Новая форма: «School form»' and published['students'][0]['id'] == 's1' and published['link'] is True
    opened = open_notice(parent, published['id'])
    assert opened.status_code == 200 and opened.json() == dict(module='form_items', workspace='demo', recordId=id, studentId='s1', day=None)
    assert open_notice(teacher, assigned['id']).status_code == 200
    # The delegate learns that an answer arrived, not what it says or who wrote it.
    assert forms.answer(parent, id).status_code == 200
    [arrived] = [i for i in items(teacher, 'forms.submitted') if not i['read']]
    assert arrived['title'] == 'Новый ответ: «School form»' and 'First' not in teacher.get('/api/notifications/inbox').text and 'Сотрудник' not in arrived['title']
    assert open_notice(teacher, arrived['id']).status_code == 200
    # Unassigned: the form is no longer theirs.
    cleared = client.put(forms.route(id, '/access'), json=forms.request(version=forms.get(client, id)['version'], collaborators=[]))
    assert cleared.status_code == 200, cleared.text
    assert open_notice(teacher, arrived['id']).status_code == 404 and open_notice(teacher, assigned['id']).status_code == 404
    # The family loses the child: the entry is restricted and the form closed to them.
    restrict(client, parent_account, grants=[])
    parent = signed('parent')
    [shut] = items(parent, 'forms.published')
    assert shut['restricted'] is True and open_notice(parent, shut['id']).status_code == 404


def test_club_titles_and_access(client, monkeypatch):
    for module in (clubs, clubs_routes, clubs_sessions): monkeypatch.setattr(module, 'today', lambda: '2026-10-15')
    enable(client)
    staff_account = create(client, 'teacher'); create(client, 'parent'); same = create(client, 'parent', login='same')
    parent = signed('parent'); guardian = signed('same'); teacher = signed('teacher')
    published = club_tests.published(client)
    assert club_tests.join(parent, published).status_code == 200
    [joined] = items(guardian, 'clubs.changed')
    assert joined['title'] == 'Кружок «Шахматный клуб»: изменилась запись или информация' and joined['students'][0]['id'] == 's1' and joined['link'] is True
    opened = open_notice(guardian, joined['id'])
    assert opened.status_code == 200 and opened.json() == dict(module='clubs', workspace='demo', recordId=published['id'], studentId='s1', day=None)
    club_tests.session(client, published, title='Секретная заметка занятия')
    [planned] = items(guardian, 'clubs.session')
    assert planned['title'] == 'Кружок «Шахматный клуб»: изменилось занятие или посещение' and open_notice(guardian, planned['id']).status_code == 200
    assert 'Секретная заметка' not in guardian.get('/api/notifications/inbox').text
    # A guardian whose link to the child is gone loses the entry's link and the club.
    restrict(client, same, grants=[])
    guardian = signed('same')
    [shut] = items(guardian, 'clubs.changed')
    assert shut['restricted'] is True and open_notice(guardian, shut['id']).status_code == 404
    # A staff member assigned to a draft club opens it; unassigned, the club is not theirs.
    draft = club_tests.club(client)
    club_tests.assign(client, draft, staff_account)
    [assigned] = items(teacher, 'clubs.changed')
    assert assigned['title'] == 'Кружок «Шахматный клуб»: вы назначены ответственным' and assigned['link'] is True
    assert open_notice(teacher, assigned['id']).status_code == 200
    club_tests.assign(client, draft)
    assert open_notice(teacher, assigned['id']).status_code == 404


def test_homework_titles_and_access(client):
    record, pupil, parent, teacher = hw.setup(client)
    key = record['lessonKey']
    sent = hw.answer(pupil, key, hw.get(pupil, key), text='Мой тайный ответ'); assert sent.status_code == 200, sent.text
    [submitted] = items(teacher, 'homework.submitted')
    assert submitted['title'] == f'Английский язык, {dated()}: работа отправлена на проверку' and submitted['students'][0]['id'] == 's1' and submitted['link'] is True
    assert 'Мой тайный ответ' not in teacher.get('/api/notifications/inbox').text
    opened = open_notice(teacher, submitted['id'])
    assert opened.status_code == 200 and opened.json() == dict(module='homework', workspace='demo', recordId=key, studentId='s1', day=None)
    assert hw.review(teacher, key, sent.json(), comment='Тайный комментарий проверки').status_code == 200
    for family_account in (pupil, parent):
        [result] = items(family_account, 'homework.reviewed')
        assert result['title'] == f'Английский язык, {dated()}: есть результат проверки' and result['link'] is True
        assert 'Тайный комментарий' not in family_account.get('/api/notifications/inbox').text
        assert open_notice(family_account, result['id']).json()['studentId'] == 's1'
    # The teacher's link to the lesson is gone: the entry stays, the lesson does not open.
    account = next(u for u in client.get('/api/access/users').json() if u['login'] == 'teacher')
    restrict(client, account, grants=[dict(workspace='demo', view='teacher', entityIds=['t1'])])
    assert open_notice(signed('teacher'), submitted['id']).status_code == 404
    # The homework is withdrawn from the journal: nothing is left to open for the family either.
    assert client.post('/api/journal/withdraw?workspace=demo', json=dict(lessonKey=key, version=record['version'])).status_code == 200
    for family_account in (pupil, parent):
        [result] = items(family_account, 'homework.reviewed')
        assert open_notice(family_account, result['id']).status_code == 404


@pytest.fixture
def quiz_clock(monkeypatch):
    for module in [quizzes, quiz_bank, quiz_attempts, quiz_catalog]: monkeypatch.setattr(module, 'now', lambda: quiz_tests.NOW)


@pytest.fixture
def assessment_clock(monkeypatch):
    monkeypatch.setattr('backend.assessment_calendar_routes.today', lambda: date(2026, 8, 10))


def test_quiz_submitted_and_assessment_staff_titles(client, quiz_clock, assessment_clock):
    quiz, pupil, parent, teacher, _ = quiz_tests.ready(client)
    begun = quiz_tests.start(pupil, quiz).json()
    quiz_tests.save(pupil, begun, {'choice': dict(selected=['a'])}, True).raise_for_status()
    [handed] = items(teacher, 'quiz.submitted')
    assert handed['title'] == 'Тест «Контрольная»: работа отправлена на проверку' and handed['link'] is True and 'students' not in handed
    opened = open_notice(teacher, handed['id'])
    assert opened.status_code == 200 and opened.json()['module'] == 'quizzes' and opened.json()['recordId'] == 'test-one'
    # A work changed by the administrator tells the teachers of the pair what moved, not why.
    assessments.guardian(client, 'mother', 's1')
    work = assessments.ok(assessments.english(teacher))
    moved = client.post(assessments.url(f"bookings/{work['id']}/move"), json=dict(requestId=str(uuid4()), version=work['version'], day=assessments.D24, lessonId='2026-08-24:eng', reason='Секретная причина переноса'))
    assert moved.status_code == 200, moved.text
    [changed] = items(teacher, 'assessment.staff_changed')
    assert changed['title'] == 'Изменена работа СОР: Английский язык, 24.08.2026' and changed['link'] is True
    assert 'Секретная причина' not in teacher.get('/api/notifications/inbox').text
    opened = open_notice(teacher, changed['id'])
    assert opened.status_code == 200 and opened.json() == dict(module='assessments', workspace='demo', recordId=work['id'], studentId=None, day='2026-08-24')


def test_cancelled_work_keeps_the_teachers_link_but_not_the_familys(client, assessment_clock):
    enable(client); create(client, 'teacher'); assessments.guardian(client, 'mother', 's1')
    teacher, mother = signed('teacher'), signed('mother')
    work = assessments.ok(assessments.english(teacher))
    cancelled = client.post(assessments.url(f"bookings/{work['id']}/cancel"), json=dict(version=work['version'], reason='Секретная причина отмены'))
    assert cancelled.status_code == 200, cancelled.text
    [staff] = items(teacher, 'assessment.staff_changed')
    assert staff['title'] == 'Изменена работа СОР: Английский язык, 17.08.2026' and staff['link'] is True
    opened = open_notice(teacher, staff['id'])
    assert opened.status_code == 200 and opened.json() == dict(module='assessments', workspace='demo', recordId=work['id'], studentId=None, day='2026-08-17')
    [gone] = items(mother, 'assessment.cancelled')
    assert gone['title'] == 'Отменена работа СОР: Английский язык, 17.08.2026' and 'link' not in gone
    assert open_notice(mother, gone['id']).status_code == 404
    assert 'Секретная причина' not in teacher.get('/api/notifications/inbox').text + mother.get('/api/notifications/inbox').text


def test_behaviour_staff_titles(client):
    enable(client); class_teacher = behaviour.teacher(client, 'class', 't1'); behaviour.guardian(client, 'mother', 's2')
    author = signed('class'); behaviour.assign_class_teacher(client, class_teacher)
    created = behaviour.ok(behaviour.note(author, ['s2'], 'performance-warning', 'Тайный текст замечания'))
    [pending] = items(client, 'behaviour.pending')
    assert pending['title'].startswith('Ждёт согласования: ') and pending['students'][0]['id'] == 's2' and pending['link'] is True
    assert 'Тайный текст' not in client.get('/api/notifications/inbox').text
    opened = open_notice(client, pending['id'])
    assert opened.status_code == 200 and opened.json()['module'] == 'behaviour_staff' and opened.json()['recordId'] == created['ids'][0]
    queued = behaviour.ok(client.get(behaviour.url('queue')))['items'][0]
    rejected = client.post(behaviour.url(f'notes/{queued["id"]}/reject'), json=dict(version=queued['version'], reason='Скрытая причина отказа'))
    assert rejected.status_code == 200, rejected.text
    [verdict] = items(author, 'behaviour.decided')
    assert verdict['title'].startswith('Мера отклонена: ') and verdict['link'] is True and 'Скрытая причина' not in author.get('/api/notifications/inbox').text
    assert open_notice(author, verdict['id']).status_code == 200
    # The author no longer has the pupil in scope: the entry stays, the note does not open.
    current = behaviour.ok(client.get('/api/access/class-staff?workspace=demo'))
    behaviour.ok(client.put('/api/access/class-staff?workspace=demo', json=dict(version=current['version'], revision=current['revision'], reason='Снято', assignments={})))
    state = api.read('demo')
    for w in state.workloads:
        if w.teacherId == 't1': w.teacherId = 't0'
    api.save('demo', state)
    assert open_notice(author, verdict['id']).status_code == 404
