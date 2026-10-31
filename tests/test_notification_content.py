"""Notifications carry a safe per-recipient title and a link that is checked again when opened."""
import json
import sqlite3
from pathlib import Path
from contextlib import closing
from datetime import date, datetime, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from backend import api, quizzes, quiz_bank, quiz_attempts, quiz_catalog
from backend.notifications import CATEGORIES, Notifications, PUSH_LABELS, SYSTEM, category_of, quiet_until, safe_target
from test_access import client, enable, create, signed
from test_grade_feed import family, published, republish
from test_journal import DAY, lesson, entry, write, graded
import test_assessment_calendar as assessments
import test_behaviour as behaviour
import test_meetings as meet
import test_meeting_consultations as consult
import test_notifications as saves
import test_school_forms as forms
import test_quizzes as quiz_tests


def inbox(c, query=''):
    response = c.get('/api/notifications/inbox' + query)
    assert response.status_code == 200, response.text
    return response.json()


def items(c, action):
    return [i for i in inbox(c)['items'] if i['action'] == action]


def open_notice(c, id):
    return c.post(f'/api/notifications/{id}/open')


def pupil_name(id):
    return next(s.name for s in api.read('demo').students if s.id == id)


def test_migration_adds_target_and_category_idempotently(tmp_path):
    with closing(sqlite3.connect(tmp_path / 'old.db')) as db:
        db.execute('CREATE TABLE notifications (id INTEGER PRIMARY KEY AUTOINCREMENT,user_id TEXT,permissions TEXT,workspace TEXT,revision INTEGER,created TEXT,action TEXT,read_at TEXT,topic TEXT,detail TEXT)')
        db.execute("INSERT INTO notifications(user_id,permissions,workspace,revision,created,action) VALUES ('u','p','demo',1,'2026-01-01T00:00:00','saved')")
        db.execute("INSERT INTO notifications(user_id,permissions,workspace,revision,created,action,detail) VALUES ('u','p','demo',2,'2026-01-01T00:00:00','duty.reminder','Завтра: 08.09.2026, 12:00–12:05, Холл')")
        Notifications(None).tables(db)
        columns = {r[1] for r in db.execute('PRAGMA table_info(notifications)')}
        assert {'target', 'category', 'detail', 'topic'} <= columns
        rows = db.execute("SELECT category,json_extract(target,'$.title'),target,detail FROM notifications ORDER BY id").fetchall()
        assert [r[0] for r in rows] == ['timetable', 'duty']
        assert rows[1][1] == rows[1][3] == 'Завтра: 08.09.2026, 12:00–12:05, Холл' and rows[0][2] is None
        # Nothing is backfilled again: a row stored later keeps what it has.
        db.execute("UPDATE notifications SET category=NULL,target=NULL")
        Notifications(None).tables(db)
        assert db.execute('SELECT count(*),count(category),count(target) FROM notifications').fetchone() == (2, 0, 0)


def test_preference_columns_are_added_and_old_opt_outs_survive(tmp_path):
    with closing(sqlite3.connect(tmp_path / 'old.db')) as db:
        db.execute('CREATE TABLE notification_preferences (user_id TEXT PRIMARY KEY,enabled INTEGER)')
        db.execute("INSERT INTO notification_preferences VALUES ('muted',0),('on',1)")
        Notifications(None).tables(db)
        Notifications(None).tables(db)
        rows = {r[0]: r for r in db.execute('SELECT user_id,enabled,muted,quiet_start,quiet_end FROM notification_preferences')}
        assert set(json.loads(rows['muted'][2])) == set(CATEGORIES) and rows['on'][2] is None


def test_actions_map_to_categories():
    assert category_of('journal.grade') == 'grades' and category_of('journal.finals_published') == 'grades'
    assert category_of('journal.homework') == category_of('homework.reviewed') == 'homework'
    assert category_of('journal.updated') == category_of('journal.withdrawn') == 'journal'
    assert [category_of(a) for a in ['saved', 'undo', 'redo', 'timetable.published']] == ['timetable'] * 4
    assert category_of('push.test') == SYSTEM and category_of('canteen:ordered') == 'other' and category_of('unknown') == 'other'
    assert {category_of(a) for a in ['absence.updated', 'quiz.published', 'assessment.staff_changed', 'behaviour.published', 'message.received',
                                     'meeting.booked', 'forms.reviewed', 'duty.changed']} == {'absences', 'quizzes', 'assessments', 'behaviour', 'messages', 'meetings', 'forms', 'duty'}
    assert set(PUSH_LABELS) == set(CATEGORIES) | {SYSTEM}


def test_safe_target_whitelists_and_clips():
    target = safe_target(dict(module='journal', recordId='k', secret='x', day='2026-08-17', title='  Тема   с\nпробелами ',
                              students=[dict(id=f's{i}', name='Имя', note='x') for i in range(11)], empty=None))
    assert set(target) == {'module', 'recordId', 'day', 'title', 'students'} and len(target['students']) == 10
    assert target['title'] == 'Тема с пробелами' and set(target['students'][0]) == {'id', 'name'}
    long = safe_target(dict(title='я' * 500))['title']
    assert long.endswith('…') and len(long) <= 200
    assert safe_target(dict(module='journal', title='   ')) == dict(module='journal')
    assert safe_target(dict(day='2026-13-40', title=None)) is None and safe_target(None) is None and safe_target('x') is None


def test_quiet_hours_cross_midnight():
    zone = ZoneInfo('Asia/Qyzylorda')
    pref = dict(quietStart='22:00', quietEnd='07:00')
    def at(day, hour, minute):
        return datetime(2026, 10, day, hour, minute, tzinfo=zone).timestamp()
    assert quiet_until(pref, at(25, 23, 30)) == at(26, 7, 0)
    assert quiet_until(pref, at(26, 6, 59)) == at(26, 7, 0)
    assert quiet_until(pref, at(26, 7, 0)) is None and quiet_until(pref, at(26, 12, 0)) is None
    assert quiet_until(dict(quietStart='13:00', quietEnd='14:00'), at(25, 13, 30)) == at(25, 14, 0)
    assert quiet_until(dict(quietStart=None, quietEnd=None), at(25, 23, 30)) is None


def test_grade_and_homework_titles_for_parent(client):
    family(client)
    parent, other = signed('parent'), signed('other-parent')
    published(client)
    (grade,) = items(parent, 'journal.grade')
    assert grade['title'] == 'Английский язык: Проверочная 8 из 10, Индивидуальная обратная связь s1'
    assert grade['students'] == [dict(id='s1', name=pupil_name('s1'))] and grade['link'] is True and grade['category'] == 'grades'
    (homework,) = items(parent, 'journal.homework')
    assert homework['title'] == 'Новое ДЗ: Английский язык, до 19.08' and homework['category'] == 'homework'
    text = json.dumps(inbox(parent), ensure_ascii=False)
    assert 'Комментарий только' not in text and 'Прочитать страницу' not in text and 'Тема урока' not in text
    assert 'topic' not in grade and 'permissions' not in grade
    theirs = json.dumps(inbox(other), ensure_ascii=False)
    assert '7 из 10' in theirs and '8 из 10' not in theirs and pupil_name('s1') not in theirs


def test_sibling_family_gets_one_homework_entry(client):
    family(client)
    create(client, 'parent', login='siblings', grants=[dict(workspace='demo', view='student', entityIds=['s1', 's2'])])
    published(client)
    parent = signed('siblings')
    (homework,) = items(parent, 'journal.homework')
    assert {s['id'] for s in homework['students']} == {'s1', 's2'}
    grades = items(parent, 'journal.grade')
    assert sorted((g['students'][0]['id'], '8 из 10' in g['title'], '7 из 10' in g['title']) for g in grades) == [('s1', True, False), ('s2', False, True)]
    assert inbox(parent)['unread'] == 3


def test_regrade_replaces_unread_and_blanks_read_titles(client):
    family(client)
    parent = signed('parent')
    saved = published(client)
    saved['document']['content']['records'][0]['grades'][0]['score'] = 9
    saved = republish(client, saved)
    (grade,) = items(parent, 'journal.grade')
    assert 'теперь 9 из 10' in grade['title'] and inbox(parent)['unread'] == 2
    parent.post('/api/notifications/read', json=dict(ids=[grade['id']])).raise_for_status()
    saved['document']['content']['records'][0]['grades'][0]['score'] = 10
    republish(client, saved)
    entries = {i['id']: i for i in items(parent, 'journal.grade')}
    assert len(entries) == 2 and 'title' not in entries[grade['id']] and 'link' not in entries[grade['id']]
    newest = entries[max(entries)]
    assert 'теперь 10 из 10' in newest['title'] and not newest['read']


def test_republishing_the_same_content_notifies_nobody(client):
    family(client)
    parent = signed('parent')
    saved = published(client)
    before = inbox(parent)
    republish(client, saved)
    assert inbox(parent)['latestId'] == before['latestId']


def test_withdraw_redacts_and_notifies(client):
    family(client)
    parent = signed('parent')
    saved = published(client)
    withdrawn = client.post('/api/journal/withdraw?workspace=demo', json=dict(lessonKey=saved['lessonKey'], version=saved['version']))
    assert withdrawn.status_code == 200, withdrawn.text
    (notice,) = items(parent, 'journal.withdrawn')
    assert notice['title'] == 'Английский язык, 17.08: запись урока скрыта'
    for item in inbox(parent)['items']:
        assert item is notice or item['action'] == 'journal.withdrawn' or 'title' not in item
    assert '8 из 10' not in json.dumps(inbox(parent), ensure_ascii=False)


def test_open_rechecks_access_and_returns_only_navigation(client):
    accounts = family(client)
    parent, other = signed('parent'), signed('other-parent')
    saved = published(client)
    (grade,) = items(parent, 'journal.grade')
    assert grade['link'] is True
    response = open_notice(parent, grade['id'])
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) <= {'module', 'workspace', 'recordId', 'studentId', 'day'}
    assert body == dict(module='journal', workspace='demo', recordId=saved['lessonKey'], studentId='s1', day=DAY)
    assert 'Индивидуальная' not in response.text and '8 из 10' not in response.text
    assert items(parent, 'journal.grade')[0]['read'] is True
    # Another account's entry is as missing as a nonexistent one, and nothing is marked read for either.
    before = inbox(other)['unread']
    foreign = open_notice(other, grade['id'])
    assert foreign.status_code == 404 and foreign.json() == open_notice(other, 999999).json()
    assert inbox(other)['unread'] == before
    update = {k: v for k, v in accounts['parent'].items() if k != 'id'}
    update['grants'] = [dict(workspace='demo', view='student', entityIds=['s3'])]
    assert client.put('/api/access/users/' + accounts['parent']['id'], json=update).status_code == 200
    fresh = signed('parent')
    (restricted,) = items(fresh, 'journal.grade')
    assert restricted['restricted'] is True and not {'title', 'students', 'link'} & set(restricted)
    refused = open_notice(fresh, restricted['id'])
    assert refused.status_code == 404 and refused.json()['detail'] == 'Запись больше недоступна.'


def test_open_after_withdraw_is_refused(client):
    family(client)
    parent = signed('parent')
    saved = published(client)
    (grade,) = items(parent, 'journal.grade')
    client.post('/api/journal/withdraw?workspace=demo', json=dict(lessonKey=saved['lessonKey'], version=saved['version'])).raise_for_status()
    assert open_notice(parent, grade['id']).status_code == 404
    (old,) = [i for i in inbox(parent)['items'] if i['id'] == grade['id']]
    assert 'title' not in old and old['read'] is True


def test_dead_entry_is_marked_read_when_opened(client):
    family(client)
    parent = signed('parent')
    published(client)
    (grade,) = items(parent, 'journal.grade')
    with api.access.lock, api.connect() as db:
        db.execute("DELETE FROM journal_members WHERE kind='published'")
    unread = inbox(parent)['unread']
    assert open_notice(parent, grade['id']).status_code == 404
    assert inbox(parent)['unread'] == unread - 1


def test_category_settings_mute_inbox_and_push(client):
    family(client)
    parent = signed('parent')
    settings = parent.get('/api/notifications/preferences')
    assert settings.status_code == 200
    assert [c['id'] for c in settings.json()['categories']] == list(CATEGORIES) and all(c['enabled'] for c in settings.json()['categories'])
    assert settings.json()['zone'] == 'Asia/Qyzylorda' and settings.json()['quietStart'] is None
    changed = parent.put('/api/notifications/preferences', json=dict(muted=['grades']))
    assert changed.status_code == 200 and changed.json() == parent.get('/api/notifications/preferences').json()
    assert {c['id']: c['enabled'] for c in changed.json()['categories']}['grades'] is False
    assert sum(not c['enabled'] for c in changed.json()['categories']) == 1
    published(client)
    assert items(parent, 'journal.grade') == [] and len(items(parent, 'journal.homework')) == 1
    assert inbox(parent)['enabled'] is True
    for body in [dict(muted=['nope']), dict(quietStart='22:00', quietEnd='22:00'), {}, dict(quietStart='22:00'), dict(quietStart='25:00', quietEnd='07:00'),
                 dict(enabled=True, muted=[])]:
        assert parent.put('/api/notifications/preferences', json=body).status_code == 422, body
    # Quiet hours alone keep the muted list; a null pair clears them.
    quiet = parent.put('/api/notifications/preferences', json=dict(quietStart='22:00', quietEnd='07:00')).json()
    assert quiet['quietStart'] == '22:00' and quiet['quietEnd'] == '07:00' and sum(not c['enabled'] for c in quiet['categories']) == 1
    cleared = parent.put('/api/notifications/preferences', json=dict(quietStart=None, quietEnd=None)).json()
    assert cleared['quietStart'] is None and sum(not c['enabled'] for c in cleared['categories']) == 1


def test_legacy_switch_and_migrated_opt_out(client):
    family(client)
    parent = signed('parent')
    assert parent.put('/api/notifications/preferences', json=dict(enabled=False)).status_code == 200
    assert inbox(parent)['enabled'] is False
    assert all(not c['enabled'] for c in parent.get('/api/notifications/preferences').json()['categories'])
    published(client)
    assert inbox(parent)['items'] == []
    assert parent.put('/api/notifications/preferences', json=dict(enabled=True)).status_code == 200
    assert inbox(parent)['enabled'] is True and all(c['enabled'] for c in parent.get('/api/notifications/preferences').json()['categories'])
    # A row written before categories existed still mutes everything.
    with api.access.lock, api.connect() as db:
        db.execute('UPDATE notification_preferences SET muted=NULL,enabled=0')
    assert inbox(parent)['enabled'] is False


@pytest.fixture
def quiz_clock(monkeypatch):
    for module in [quizzes, quiz_bank, quiz_attempts, quiz_catalog]: monkeypatch.setattr(module, 'now', lambda: quiz_tests.NOW)


def test_quiz_titles(client, quiz_clock):
    quiz, pupil, parent, teacher, _ = quiz_tests.ready(client)
    other = signed('other-pupil')
    (published,) = items(parent, 'quiz.published')
    assert published['title'] == 'Новый тест «Контрольная»: Английский язык, до 17.08 15:00' and published['category'] == 'quizzes'
    assert published['students'] == [dict(id='s1', name=pupil_name('s1'))] and published['link'] is True
    begun = quiz_tests.start(pupil, quiz).json()
    quiz_tests.save(pupil, begun, {'choice': dict(selected=['a'])}, True).raise_for_status()
    closed = quiz_tests.action(teacher, quiz, 'close').json()
    (ended,) = items(parent, 'quiz.close')
    assert ended['title'] == 'Тест «Контрольная»: приём ответов завершён' and items(parent, 'quiz.published') == []
    assert quiz_tests.action(teacher, closed, 'release').status_code == 200
    (result,) = items(parent, 'quiz.release')
    assert result['title'] == 'Результат теста «Контрольная»: 2 из 2' and [s['id'] for s in result['students']] == ['s1']
    # A pupil who never tried gets the plain notice, and nobody sees another pupil's score.
    (plain,) = items(other, 'quiz.release')
    assert plain['title'] == 'Открыт разбор теста «Контрольная»' and '2 из 2' not in json.dumps(inbox(other), ensure_ascii=False)
    opened = open_notice(parent, result['id'])
    assert opened.status_code == 200 and opened.json() == dict(module='quizzes', workspace='demo', recordId='test-one', studentId='s1', day=None)
    assert open_notice(other, result['id']).status_code == 404
    assert quiz_tests.action(teacher, quiz_tests.detail(teacher), 'withdraw').status_code == 200
    assert open_notice(parent, result['id']).status_code == 404
    text = json.dumps(inbox(parent), ensure_ascii=False)
    assert '2 из 2' not in text and 'Результат теста' not in text
    (gone,) = items(parent, 'quiz.withdraw')
    assert gone['title'] == 'Тест «Контрольная» отозван'
    # Nothing is left to open, so the entry offers no button (open would only answer that the record is gone).
    assert 'link' not in gone and open_notice(parent, gone['id']).status_code == 404


def test_behaviour_titles(client):
    enable(client); behaviour.teacher(client, 'math', 't1'); behaviour.guardian(client, 'mother', 's2')
    teacher, mother = signed('math'), signed('mother')
    note = behaviour.ok(behaviour.note(teacher, ['s2'], 'unprepared', 'Тайный текст замечания'))['ids'][0]
    (published,) = items(mother, 'behaviour.published')
    assert published['title'] == 'Замечание: Не готов к уроку' and published['students'] == [dict(id='s2', name=pupil_name('s2'))]
    assert 'Тайный текст' not in json.dumps(inbox(mother), ensure_ascii=False)
    opened = open_notice(mother, published['id'])
    assert opened.status_code == 200 and opened.json()['recordId'] == note and opened.json()['module'] == 'behaviour'
    current = behaviour.staff_list(teacher)[0]
    edited = teacher.post(behaviour.url(f'notes/{note}/edit'), json=dict(version=current['version'], categoryId='discipline', text='Иначе', reason='Уточнение'))
    assert edited.status_code == 200, edited.text
    (changed,) = items(mother, 'behaviour.changed')
    assert changed['title'] == 'Изменена запись: Замечание — Нарушение дисциплины'
    assert [i['id'] for i in items(mother, 'behaviour.published')] == [published['id']] and 'title' not in items(mother, 'behaviour.published')[0]
    current = behaviour.staff_list(teacher)[0]
    assert client.post(behaviour.url(f'notes/{note}/withdraw'), json=dict(version=current['version'], reason='Личная причина')).status_code == 200
    (withdrawn,) = items(mother, 'behaviour.withdrawn')
    assert withdrawn['title'] == 'Запись о поведении отозвана' and 'link' not in withdrawn
    # The unread correction is replaced by the withdrawal; the read original loses its title.
    assert 'title' not in items(mother, 'behaviour.published')[0] and items(mother, 'behaviour.changed') == []
    assert open_notice(mother, published['id']).status_code == 404 and open_notice(mother, changed['id']).status_code == 404
    text = json.dumps(inbox(mother), ensure_ascii=False)
    assert 'Личная причина' not in text and 'Тайный текст' not in text


@pytest.fixture
def assessment_clock(monkeypatch):
    monkeypatch.setattr('backend.assessment_calendar_routes.today', lambda: date(2026, 8, 10))


def test_assessment_booking_title(client, assessment_clock):
    enable(client); create(client, 'teacher'); assessments.guardian(client, 'mother', 's1')
    teacher, mother = signed('teacher'), signed('mother')
    work = assessments.ok(assessments.english(teacher))
    (published,) = items(mother, 'assessment.published')
    assert published['title'] == 'СОР: Английский язык, 17.08.2026' and published['students'] == [dict(id='s1', name=pupil_name('s1'))]
    assert 'Дроби' not in json.dumps(inbox(mother), ensure_ascii=False)
    opened = open_notice(mother, published['id'])
    assert opened.status_code == 200 and opened.json() == dict(module='assessments', workspace='demo', recordId=work['id'], studentId='s1', day='2026-08-17')
    moved = teacher.post(assessments.url(f"bookings/{work['id']}/move"), json=dict(requestId=str(uuid4()), version=work['version'], day=assessments.D24, lessonId='2026-08-24:eng', reason='Болезнь'))
    assert moved.status_code == 200, moved.text
    (shifted,) = items(mother, 'assessment.moved')
    assert shifted['title'] == 'Перенесена работа СОР: Английский язык, теперь 24.08.2026'
    # The read original loses its date; the unread correction replaced nothing else.
    assert '17.08.2026' not in json.dumps(inbox(mother), ensure_ascii=False) and 'title' not in items(mother, 'assessment.published')[0]
    cancelled = teacher.post(assessments.url(f"bookings/{work['id']}/cancel"), json=dict(version=moved.json()['version'], reason='Отмена'))
    assert cancelled.status_code == 200, cancelled.text
    (gone,) = items(mother, 'assessment.cancelled')
    assert gone['title'] == 'Отменена работа СОР: Английский язык, 24.08.2026' and 'link' not in gone
    assert open_notice(mother, shifted['id']).status_code == 404


def test_meeting_booking_titles(client, monkeypatch):
    id, host, _ = meet.setup(client, monkeypatch)
    meet.publish(client, id)
    parent, teacher = signed('parent'), signed('teacher')
    booking = meet.ok(meet.book(parent, id, meet.slots(parent, id)[0], note='Личный вопрос'))['id']
    (booked,) = items(parent, 'meeting.booked')
    assert booked['title'].startswith('Запись на встречу: «Встречи с родителями», 05.04 17:00, ') and booked['category'] == 'meetings'
    assert booked['students'] == [dict(id='s1', name=pupil_name('s1'))]
    # The host reads the same title; the parent's private note stays in the booking.
    assert items(teacher, 'meeting.booked')[0]['title'] == booked['title']
    assert 'Личный вопрос' not in json.dumps([inbox(parent), inbox(teacher)], ensure_ascii=False)
    opened = open_notice(parent, booked['id'])
    assert opened.status_code == 200, opened.text
    assert opened.json() == dict(module='meetings', workspace='demo', recordId=booking, studentId='s1', day='2027-04-05', eventId=id)
    assert open_notice(teacher, items(teacher, 'meeting.booked')[0]['id']).json()['eventId'] == id
    cancelled = parent.post(meet.path('bookings/' + booking), json=meet.request(version=1, action='cancel', reason='Личная причина'))
    assert cancelled.status_code == 200, cancelled.text
    (gone,) = items(parent, 'meeting.cancelled')
    assert gone['title'].startswith('Встреча отменена: «Встречи с родителями», 05.04 17:00')
    assert 'Личная причина' not in json.dumps([inbox(parent), inbox(teacher)], ensure_ascii=False)


def test_form_decision_title(client):
    enable(client); create(client, 'parent'); reviewer = create(client, 'staff')
    id = forms.new(client, 'application')
    forms.configure(client, id, questions=[dict(id='q', title='Reason', kind='text')])
    forms.assign(client, id, reviewer, 'review'); forms.publish(client, id)
    parent, staff = signed('parent'), signed('staff')
    sent = forms.answer(parent, id, answers={'q': 'Личные обстоятельства'}).json()
    review = forms.route(id, '/responses/' + sent['id'] + '/review')
    returned = staff.post(review, json=forms.request(version=sent['version'], formVersion=forms.get(staff, id)['version'], action='returned', comment='Приложите справку врача'))
    assert returned.status_code == 200, returned.text
    (decision,) = items(parent, 'forms.reviewed')
    assert decision['title'] == '«School form»: возвращено на исправление' and decision['students'] == [dict(id='s1', name=pupil_name('s1'))]
    assert 'справку' not in json.dumps(inbox(parent), ensure_ascii=False)
    opened = open_notice(parent, decision['id'])
    assert opened.status_code == 200 and opened.json()['module'] == 'forms' and opened.json()['formId'] == id and opened.json()['recordId'] == sent['id']
    # Nobody but the applicant can follow the entry.
    assert open_notice(staff, decision['id']).status_code == 404


def test_timetable_save_is_silent_for_families_until_announced(client):
    enable(client); create(client, 'student'); create(client, 'parent')
    create(client, 'parent', login='far', grants=[dict(workspace='demo', view='student', entityIds=['s3'])])
    create(client, 'editor', login='school-editor', workspaces=['school'])
    pupil, parent, far, outsider = signed('student'), signed('parent'), signed('far'), signed('school-editor')
    saves.rebase(client)
    state = api.read('demo'); state.placements[0].period = 0
    api.save('demo', state)
    assert [inbox(c)['unread'] for c in (pupil, parent, far)] == [0, 0, 0] and inbox(client)['unread'] == 1
    pending = client.get('/api/notifications/timetable/announce?workspace=demo')
    assert pending.status_code == 200 and pending.json()['pending'] is True and pending.json()['revision'] == api.read('demo').revision
    stale = client.post('/api/notifications/timetable/announce?workspace=demo', json=dict(revision=api.read('demo').revision - 1))
    assert stale.status_code == 409
    done = client.post('/api/notifications/timetable/announce?workspace=demo', json=dict(revision=api.read('demo').revision))
    assert done.status_code == 200 and done.json()['notified'] == 2, done.text
    for account in (pupil, parent):
        (notice,) = items(account, 'timetable.published')
        assert notice['title'] == 'Изменилось расписание' and notice['students'] == [dict(id='s1', name=pupil_name('s1'))] and notice['category'] == 'timetable'
        assert open_notice(account, notice['id']).json()['module'] == 'timetable'
    assert inbox(far)['unread'] == 0
    again = saves.announce(client)
    assert again['notified'] == 0 and len(items(pupil, 'timetable.published')) == 1
    assert client.get('/api/notifications/timetable/announce?workspace=demo').json()['pending'] is False
    for account in (parent, outsider):
        assert account.get('/api/notifications/timetable/announce?workspace=demo').status_code == 403
        assert account.post('/api/notifications/timetable/announce?workspace=demo', json=dict(revision=api.read('demo').revision)).status_code == 403


def test_calendar_change_reaches_every_family_and_undo_is_announced(client):
    enable(client); create(client, 'parent')
    create(client, 'parent', login='far', grants=[dict(workspace='demo', view='student', entityIds=['s3'])])
    parent, far = signed('parent'), signed('far')
    saves.rebase(client)
    state = api.read('demo'); state.settings.yearEnd = state.settings.yearEnd + timedelta(days=1)
    api.save('demo', state)
    assert saves.announce(client)['notified'] == 2
    assert items(far, 'timetable.published')[0]['students'][0]['id'] == 's3'
    # Undo is a save as well: silent until announced, then the families hear of it once more (the unread entry is replaced).
    assert client.post('/api/undo?workspace=demo', json=dict(revision=api.read('demo').revision)).status_code == 200
    assert inbox(parent)['unread'] == 1 and saves.announce(client)['notified'] == 2
    assert inbox(parent)['unread'] == 1 and inbox(far)['unread'] == 1


def test_baseline_created_on_first_save(client):
    enable(client)
    with api.connect() as db:
        db.execute('DELETE FROM timetable_announcements')
    revision = api.read('demo').revision
    state = api.read('demo'); state.placements[0].period = 0
    api.save('demo', state)
    with api.connect() as db:
        assert db.execute("SELECT revision,actor_name FROM timetable_announcements WHERE workspace='demo'").fetchone() == (revision, 'Система')
    api.save('demo', api.read('demo'))
    with api.connect() as db:
        assert db.execute("SELECT revision FROM timetable_announcements WHERE workspace='demo'").fetchone() == (revision,)


def test_sibling_announcement_keeps_the_children_of_the_unread_one(client):
    enable(client)
    create(client, 'parent', login='siblings', grants=[dict(workspace='demo', view='student', entityIds=['s2', 's3'])])
    siblings = signed('siblings')
    saves.rebase(client)
    # The first lesson concerns s2 (and s1), the second concerns s3 (and s1).
    state = api.read('demo'); state.placements[0].period = 3
    api.save('demo', state)
    assert saves.announce(client)['notified'] == 1
    (first,) = items(siblings, 'timetable.published')
    assert [s['id'] for s in first['students']] == ['s2']
    state = api.read('demo'); state.placements[1].period = 4
    api.save('demo', state)
    assert saves.announce(client)['notified'] == 1
    # The unread entry is replaced by one that still names s2 beside s3.
    (second,) = items(siblings, 'timetable.published')
    assert second['id'] != first['id'] and [s['id'] for s in second['students']] == ['s2', 's3'] and inbox(siblings)['unread'] == 1
    # A read entry is history: the next one names only what changed since, and the old one keeps its text.
    read(siblings, second['id'])
    state = api.read('demo'); state.placements[0].period = 0
    api.save('demo', state)
    assert saves.announce(client)['notified'] == 1
    latest, older = sorted(items(siblings, 'timetable.published'), key=lambda i: -i['id'])
    assert [s['id'] for s in latest['students']] == ['s2'] and [s['id'] for s in older['students']] == ['s2', 's3']


def test_service_worker_labels_match_categories():
    # The lock screen text lives in the worker (the payload carries only an id), so the two lists must not drift apart.
    worker = (Path(__file__).resolve().parent.parent / 'src' / 'service-worker.js').read_text(encoding='utf-8')
    for id, push in PUSH_LABELS.items(): assert f'{id}: "{push}"' in worker, id
    client_side = (Path(__file__).resolve().parent.parent / 'src' / 'notifications.ts').read_text(encoding='utf-8')
    for id, push in PUSH_LABELS.items(): assert f'{id}: "{push}"' in client_side, id


def attend(record, student, mark, minutes=0):
    for row in record['document']['content']['records']:
        if row['studentId'] == student: row.update(attendance=mark, lateMinutes=minutes)
    return record


def read(c, *ids):
    c.post('/api/notifications/read', json=dict(ids=list(ids))).raise_for_status()


def regrant(client, account, *students):
    update = {k: v for k, v in account.items() if k != 'id'}
    update['grants'] = [dict(workspace='demo', view='student', entityIds=list(students))]
    assert client.put('/api/access/users/' + account['id'], json=update).status_code == 200


def test_absence_is_told_beside_grade_and_homework(client):
    family(client)
    parent, other = signed('parent'), signed('other-parent')
    record = attend(graded(entry(client, lesson(client)['lessonKey'])), 's1', 'absent')
    assert write(client, record, publish=True).status_code == 200
    (absence,) = items(parent, 'journal.updated')
    assert absence['title'] == 'Английский язык, 17.08: отмечено отсутствие' and absence['students'][0]['id'] == 's1'
    assert len(items(parent, 'journal.grade')) == 1 and len(items(parent, 'journal.homework')) == 1
    # The ordinary first 'present' mark is not worth an entry beside the grade and the homework.
    assert items(other, 'journal.updated') == [] and len(items(other, 'journal.grade')) == 1


def test_changed_attendance_replaces_the_older_mark_beside_other_news(client):
    family(client)
    parent = signed('parent')
    key = lesson(client)['lessonKey']
    saved = republish(client, attend(entry(client, key), 's1', 'late', 7))
    (late,) = items(parent, 'journal.updated')
    assert late['title'] == 'Английский язык, 17.08: отмечено опоздание (7 мин)'
    # The lateness becomes an absence in a publication that also sets homework: one mark remains, and it is the new one.
    saved['document']['content'].update(homework='Прочитать страницу 12', dueDate='2026-08-19')
    saved = republish(client, attend(saved, 's1', 'absent'))
    (absent,) = items(parent, 'journal.updated')
    assert absent['title'] == 'Английский язык, 17.08: отмечено отсутствие' and absent['id'] != late['id']
    assert len(items(parent, 'journal.homework')) == 1 and 'опоздание' not in json.dumps(inbox(parent), ensure_ascii=False)
    # Read, then corrected to 'present' in a publication that also brings a grade: the old text goes, the correction is told.
    read(parent, absent['id'])
    republish(client, attend(graded(saved), 's1', 'present'))
    old, new = sorted(items(parent, 'journal.updated'), key=lambda i: i['id'])
    assert old['id'] == absent['id'] and 'title' not in old and new['title'] == 'Английский язык, 17.08: отмечено присутствие'
    assert 'отсутствие' not in json.dumps(inbox(parent), ensure_ascii=False)


def test_absence_survives_a_plain_update_of_the_lesson(client):
    family(client)
    parent = signed('parent')
    key = lesson(client)['lessonKey']
    saved = republish(client, attend(entry(client, key), 's1', 'absent'))
    (absence,) = items(parent, 'journal.updated')
    assert absence['title'] == 'Английский язык, 17.08: отмечено отсутствие'
    # Only the topic changes: the lesson is told as updated, and the unread absence stays beside it.
    saved['document']['content']['topic'] = 'Другая тема урока'
    saved = republish(client, saved)
    titles = {i['id']: i['title'] for i in items(parent, 'journal.updated')}
    assert titles[absence['id']] == 'Английский язык, 17.08: отмечено отсутствие' and sorted(titles.values()) == ['Английский язык, 17.08: обновлена запись урока', 'Английский язык, 17.08: отмечено отсутствие']
    assert items(parent, 'journal.updated')[0]['read'] is False and inbox(parent)['unread'] == 2
    # A second plain update replaces only the plain one; a read absence keeps its text too.
    read(parent, absence['id'])
    saved['document']['content']['topic'] = 'И ещё одна тема'
    saved = republish(client, saved)
    again = {i['id']: i.get('title') for i in items(parent, 'journal.updated')}
    assert len(again) == 2 and again[absence['id']] == 'Английский язык, 17.08: отмечено отсутствие'
    # The teacher takes the mark back: what was told about it goes, and nothing states the absence any more.
    republish(client, attend(saved, 's1', 'unmarked'))
    assert 'отсутствие' not in json.dumps(inbox(parent), ensure_ascii=False)
    assert 'title' not in next(i for i in items(parent, 'journal.updated') if i['id'] == absence['id'])


def test_hidden_lesson_notice_goes_when_the_lesson_is_published_again(client):
    family(client)
    parent = signed('parent')
    saved = published(client)

    def withdraw(record):
        client.post('/api/journal/withdraw?workspace=demo', json=dict(lessonKey=record['lessonKey'], version=record['version'])).raise_for_status()

    withdraw(saved)
    assert len(items(parent, 'journal.withdrawn')) == 1
    saved = republish(client, entry(client, saved['lessonKey']))
    # The unread notice is gone and the new grade is there.
    assert items(parent, 'journal.withdrawn') == [] and len(items(parent, 'journal.grade')) == 1
    withdraw(saved)
    (notice,) = items(parent, 'journal.withdrawn')
    read(parent, notice['id'])
    republish(client, entry(client, saved['lessonKey']))
    (old,) = items(parent, 'journal.withdrawn')
    assert old['id'] == notice['id'] and 'title' not in old and 'запись урока скрыта' not in json.dumps(inbox(parent), ensure_ascii=False)


@pytest.mark.parametrize('seen', [False, True])
def test_muted_category_still_retracts_older_grade_titles(client, seen):
    family(client)
    parent = signed('parent')
    saved = published(client)
    (grade,) = items(parent, 'journal.grade')
    if seen: read(parent, grade['id'])
    parent.put('/api/notifications/preferences', json=dict(muted=['grades'])).raise_for_status()
    saved['document']['content']['records'][0]['grades'][0]['score'] = 3
    republish(client, saved)
    text = json.dumps(inbox(parent), ensure_ascii=False)
    assert '8 из 10' not in text and '3 из 10' not in text
    old = items(parent, 'journal.grade')
    if seen:
        # Read: still listed, with no text and no way in.
        assert len(old) == 1 and 'title' not in old[0] and 'link' not in old[0] and open_notice(parent, grade['id']).status_code == 404
    else:
        assert old == []


@pytest.mark.parametrize('seen', [False, True])
def test_muted_category_still_retracts_withdrawn_behaviour_note(client, seen):
    enable(client); behaviour.teacher(client, 'math', 't1'); behaviour.guardian(client, 'mother', 's2')
    teacher, mother = signed('math'), signed('mother')
    note = behaviour.ok(behaviour.note(teacher, ['s2'], 'unprepared', 'Тайный текст замечания'))['ids'][0]
    (published,) = items(mother, 'behaviour.published')
    if seen: read(mother, published['id'])
    mother.put('/api/notifications/preferences', json=dict(muted=['behaviour'])).raise_for_status()
    current = behaviour.staff_list(teacher)[0]
    assert client.post(behaviour.url(f'notes/{note}/withdraw'), json=dict(version=current['version'], reason='Не тому ученику')).status_code == 200
    assert items(mother, 'behaviour.withdrawn') == []
    old = items(mother, 'behaviour.published')
    if seen:
        assert len(old) == 1 and not {'title', 'students', 'link'} & set(old[0]) and open_notice(mother, published['id']).status_code == 404
    else:
        assert old == []
    assert 'Не готов к уроку' not in json.dumps(inbox(mother), ensure_ascii=False)


def test_redacted_entry_leads_nowhere_even_for_a_family_that_lost_the_child(client, assessment_clock):
    enable(client); create(client, 'teacher'); account = assessments.guardian(client, 'mother', 's1')
    teacher, mother = signed('teacher'), signed('mother')
    work = assessments.ok(assessments.english(teacher))
    (published,) = items(mother, 'assessment.published')
    read(mother, published['id'])
    moved = teacher.post(assessments.url(f"bookings/{work['id']}/move"), json=dict(requestId=str(uuid4()), version=work['version'], day=assessments.D24, lessonId='2026-08-24:eng', reason='Болезнь'))
    assert moved.status_code == 200, moved.text
    (old,) = items(mother, 'assessment.published')
    assert 'title' not in old and 'link' not in old
    # Two locks: the blanked entry has lost its module and the family guard wants a child, so either alone keeps it shut.
    with api.connect() as db:
        assert db.execute("SELECT json_extract(target,'$.module') FROM notifications WHERE id=?", (old['id'],)).fetchone()[0] is None
    # The corrected entry still opens; the blanked one never does, whatever the account's children are.
    (shifted,) = items(mother, 'assessment.moved')
    assert open_notice(mother, shifted['id']).status_code == 200 and open_notice(mother, old['id']).status_code == 404
    regrant(client, account, 's3')
    fresh = signed('mother')
    assert open_notice(fresh, old['id']).status_code == 404 and open_notice(fresh, shifted['id']).status_code == 404


def test_only_the_owner_can_open_an_entry(client):
    family(client)
    parent, teacher = signed('parent'), signed('teacher')
    published(client)
    (grade,) = items(parent, 'journal.grade')
    unread = inbox(parent)['unread']
    # The administrator and a teacher of the lesson see the lesson, but an entry id is no key to somebody else's inbox.
    for other in (client, teacher): assert open_notice(other, grade['id']).status_code == 404
    assert inbox(parent)['unread'] == unread and items(parent, 'journal.grade')[0]['read'] is False


def test_timetable_entry_is_refused_once_the_child_is_lost(client):
    enable(client); account = create(client, 'parent')
    parent = signed('parent')
    saves.rebase(client)
    state = api.read('demo'); state.placements[0].period = 0
    api.save('demo', state)
    assert saves.announce(client)['notified'] == 1
    (notice,) = items(parent, 'timetable.published')
    assert open_notice(parent, notice['id']).status_code == 200
    regrant(client, account, 's3')
    fresh = signed('parent')
    assert open_notice(fresh, notice['id']).status_code == 404


def test_sibling_that_lost_one_child_sees_only_the_other(client):
    family(client)
    account = create(client, 'parent', login='siblings', grants=[dict(workspace='demo', view='student', entityIds=['s1', 's2'])])
    published(client)
    regrant(client, account, 's1')
    parent = signed('siblings')
    (homework,) = items(parent, 'journal.homework')
    assert homework['students'] == [dict(id='s1', name=pupil_name('s1'))] and open_notice(parent, homework['id']).json()['studentId'] == 's1'
    lost = [g for g in items(parent, 'journal.grade') if g.get('restricted')]
    assert len(lost) == 1 and not {'title', 'students', 'link'} & set(lost[0])
    assert pupil_name('s2') not in json.dumps(inbox(parent), ensure_ascii=False)


def test_journal_entry_needs_the_pupil_on_the_published_roster(client):
    family(client)
    parent, other = signed('parent'), signed('other-parent')
    published(client)
    (grade,), (theirs,) = items(parent, 'journal.grade'), items(other, 'journal.grade')
    with api.access.lock, api.connect() as db:
        db.execute("DELETE FROM journal_members WHERE kind='published' AND student_id='s1'")
    assert open_notice(parent, grade['id']).status_code == 404 and open_notice(other, theirs['id']).status_code == 200


def test_quiz_entry_needs_a_quiz_member(client, quiz_clock):
    quiz, pupil, parent, teacher, _ = quiz_tests.ready(client)
    (published,) = items(parent, 'quiz.published')
    assert open_notice(parent, published['id']).status_code == 200
    with api.access.lock, api.connect() as db:
        db.execute("DELETE FROM quiz_members WHERE student_id='s1'")
    assert open_notice(parent, published['id']).status_code == 404


def test_behaviour_entry_needs_a_visible_published_note_of_the_child(client):
    enable(client); behaviour.teacher(client, 'math', 't1'); behaviour.guardian(client, 'mother', 's2')
    teacher, mother = signed('math'), signed('mother')
    note = behaviour.ok(behaviour.note(teacher, ['s2'], 'unprepared', 'Текст'))['ids'][0]
    (published,) = items(mother, 'behaviour.published')

    def change(column, value):
        with api.access.lock, api.connect() as db: db.execute(f'UPDATE behaviour_notes SET {column}=? WHERE id=?', (value, note))

    assert open_notice(mother, published['id']).status_code == 200
    for column, wrong, right in [('visible', 0, 1), ('status', 'withdrawn', 'published'), ('student_id', 's3', 's2')]:
        change(column, wrong)
        assert open_notice(mother, published['id']).status_code == 404, column
        change(column, right)
    assert open_notice(mother, published['id']).status_code == 200


def test_assessment_entry_needs_a_published_work(client, assessment_clock):
    enable(client); create(client, 'teacher'); assessments.guardian(client, 'mother', 's1')
    teacher, mother = signed('teacher'), signed('mother')
    work = assessments.ok(assessments.english(teacher))
    (published,) = items(mother, 'assessment.published')

    def status(value):
        with api.access.lock, api.connect() as db: db.execute('UPDATE assessment_bookings SET status=? WHERE id=?', (value, work['id']))

    status('cancelled')
    assert open_notice(mother, published['id']).status_code == 404
    status('published')
    assert open_notice(mother, published['id']).status_code == 200


def test_meeting_entry_needs_the_booking_parent_and_child(client, monkeypatch):
    id, host, account = meet.setup(client, monkeypatch)
    meet.publish(client, id)
    parent = signed('parent')
    booking = meet.ok(meet.book(parent, id, meet.slots(parent, id)[0], note='Личный вопрос'))['id']
    (booked,) = items(parent, 'meeting.booked')

    def change(column, value):
        with api.access.lock, api.connect() as db: db.execute(f'UPDATE meeting_bookings SET {column}=? WHERE id=?', (value, booking))

    for column, wrong, right in [('parent_id', 'somebody-else', account['id']), ('student_id', 's3', 's1')]:
        change(column, wrong)
        assert open_notice(parent, booked['id']).status_code == 404, column
        change(column, right)
    assert open_notice(parent, booked['id']).status_code == 200


def test_consultation_booking_reaches_the_pupil_and_guardians_but_parent_meetings_stay_private(client, monkeypatch):
    host, _, _ = consult.prepared(client, monkeypatch)
    consult.other_family(client, 'guardian', 's1')
    meet.ok(consult.switch(client, True))
    teacher, pupil, guardian, parent = signed('teacher'), signed('student'), signed('guardian'), signed('parent')
    series = consult.create_series(teacher)['id']
    booking = meet.ok(consult.book(pupil, series, consult.on_date(pupil, series, consult.MONDAYS[0])[0]))['id']
    (booked,) = items(guardian, 'meeting.booked')
    assert booked['title'].startswith('Запись на встречу: «Консультации», 05.04 08:00, ') and booked['students'] == [dict(id='s1', name=pupil_name('s1'))]
    opened = open_notice(guardian, booked['id'])
    assert opened.status_code == 200 and opened.json()['eventId'] == series and opened.json()['recordId'] == booking
    assert open_notice(pupil, items(pupil, 'meeting.booked')[0]['id']).json()['eventId'] == series
    # A booking made by the parent still shows to the pupil by title, but the pupil is not its author and cannot open it.
    meet.ok(consult.book(parent, series, consult.on_date(parent, series, consult.MONDAYS[1])[0]))
    (told,) = [i for i in items(pupil, 'meeting.booked') if i['title'].startswith('Запись на встречу: «Консультации», 12.04')]
    assert open_notice(pupil, told['id']).status_code == 404 and open_notice(guardian, items(guardian, 'meeting.booked')[0]['id']).status_code == 200
    # In a parent meeting no other guardian of the child is told, and a hand-made entry for one does not open the booking.
    id = meet.new(client, [meet.hosts(host)])
    meet.publish(client, id)
    private = meet.ok(meet.book(parent, id, meet.slots(parent, id)[0]))['id']
    name = pupil_name('s1')
    with api.access.lock, api.connect() as db:
        other = next(u for u in api.notifications.granted_users(db, 'demo', 'student', {'s1'}, ['parent']) if u['login'] == 'guardian')
        api.notifications.deliver(db, [other], 'demo', 1, 'meeting.booked', target=dict(module='meetings', recordId=private, students=[dict(id='s1', name=name)], title='Запись на встречу'))
    assert open_notice(guardian, items(guardian, 'meeting.booked')[0]['id']).status_code == 404
    # Of the guardian's entries only the hand-made one is not about consultations: the parent meeting told nobody else.
    assert [i['title'] for i in items(guardian, 'meeting.booked') if 'Консультации' not in i['title']] == ['Запись на встречу']


def test_form_entry_needs_the_applicant(client):
    enable(client); create(client, 'parent'); reviewer = create(client, 'staff')
    id = forms.new(client, 'application')
    forms.configure(client, id, questions=[dict(id='q', title='Reason', kind='text')])
    forms.assign(client, id, reviewer, 'review'); forms.publish(client, id)
    parent, staff = signed('parent'), signed('staff')
    sent = forms.answer(parent, id, answers={'q': 'Личные обстоятельства'}).json()
    review = forms.route(id, '/responses/' + sent['id'] + '/review')
    decided = staff.post(review, json=forms.request(version=sent['version'], formVersion=forms.get(staff, id)['version'], action='approved', comment='Да'))
    assert decided.status_code == 200, decided.text
    (decision,) = items(parent, 'forms.reviewed')
    assert open_notice(parent, decision['id']).status_code == 200
    with api.access.lock, api.connect() as db: db.execute("UPDATE school_form_answers SET user_id='somebody-else' WHERE id=?", (sent['id'],))
    assert open_notice(parent, decision['id']).status_code == 404


def test_form_entry_is_shut_once_the_child_is_lost(client):
    enable(client); account = create(client, 'parent'); reviewer = create(client, 'staff')
    id = forms.new(client, 'application')
    forms.configure(client, id, questions=[dict(id='q', title='Reason', kind='text')])
    forms.assign(client, id, reviewer, 'review'); forms.publish(client, id)
    parent, staff = signed('parent'), signed('staff')
    sent = forms.answer(parent, id, answers={'q': 'Личные обстоятельства'}).json()
    review = forms.route(id, '/responses/' + sent['id'] + '/review')
    assert staff.post(review, json=forms.request(version=sent['version'], formVersion=forms.get(staff, id)['version'], action='approved', comment='Да')).status_code == 200
    (decision,) = items(parent, 'forms.reviewed')
    assert decision['link'] is True
    regrant(client, account, 's3')
    fresh = signed('parent')
    # The answer is still the applicant's own, but the entry is about a child the account has lost.
    (lost,) = items(fresh, 'forms.reviewed')
    assert lost['restricted'] is True and not {'title', 'students', 'link'} & set(lost)
    assert open_notice(fresh, decision['id']).status_code == 404


def test_form_of_no_child_stays_open_to_its_applicant(client):
    admin = enable(client); applicant = create(client, 'staff')
    id = forms.new(client, 'application')
    forms.configure(client, id, roles=['staff'], questions=[dict(id='q', title='Reason', kind='text')])
    forms.assign(client, id, applicant, 'review'); forms.publish(client, id)
    staff = signed('staff')
    sent = forms.answer(staff, id, entity='', answers={'q': 'Мой отпуск'}).json()
    review = forms.route(id, '/responses/' + sent['id'] + '/review')
    assert client.post(review, json=forms.request(version=sent['version'], formVersion=forms.get(client, id)['version'], action='approved', comment='')).status_code == 200
    (decision,) = items(staff, 'forms.reviewed')
    assert 'students' not in decision and decision['link'] is True
    opened = open_notice(staff, decision['id'])
    assert opened.status_code == 200 and opened.json()['formId'] == id and opened.json()['studentId'] is None


def test_duty_notice_keeps_its_whole_text_and_has_no_link_for_managers(client):
    enable(client); create(client, 'teacher'); create(client, 'editor', login='school-editor', workspaces=['demo'])
    teacher, editor = signed('teacher'), signed('school-editor')
    text = ' '.join(f'Назначено: {n:02d}.09.2026, 12:00–12:05, Спортивный зал №2 (большой), 2 этаж.' for n in range(1, 5))
    assert 200 < len(text) <= 300
    with api.access.lock, api.connect() as db:
        users = [api.access.public(r) for r in db.execute("SELECT * FROM accounts WHERE login IN ('teacher','school-editor')")]
        api.notifications.deliver(db, users, 'demo', 1, 'duty.changed', target=dict(module='duty', title=text))
    (own,) = items(teacher, 'duty.changed')
    assert own['title'] == text and own['link'] is True and open_notice(teacher, own['id']).json()['module'] == 'duty'
    # The administrator and editor shells have no duty section, so no button is offered and nothing is opened.
    (managed,) = items(editor, 'duty.changed')
    assert managed['title'] == text and 'link' not in managed and open_notice(editor, managed['id']).status_code == 404
    too_long = safe_target(dict(module='duty', title='я' * 400))['title']
    assert len(too_long) == 300 and too_long.endswith('…')
