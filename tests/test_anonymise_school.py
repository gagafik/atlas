"""The pseudonymised copy of a school: a school with canary names is built through the application in a temporary database,
the tool runs on it, and what is left in the copy is looked at from outside (raw bytes, the application on the copy)."""
from dataclasses import dataclass
from datetime import datetime, timezone
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from backend import api
from backend.models import Placement, TeacherAbsence
from test_access import HEADERS, PASSWORD, create, enable, signed
from test_final_grades import prepare, scale
from test_journal import DAY, entry, lesson, write
from test_journal_periods import close, period
from tools import anonymise_school as tool
from tools.anonymise_policy import ACCOUNT_TABLES, POLICY
from tools import anonymise_names

# Distinctive invented names: none of these words may be found in any file of the copy.
CANARIES = ['Канарейкин', 'Феофан', 'Снегирёв', 'Аристарх', 'Щеглова', 'Аглая', 'Снегирёва', 'Пелагея', 'Попугаев', 'Никодим',
            'Скворцова', 'Агата', 'Синицына', 'Мелания', 'Воробьёв', 'Пантелеймон', 'Ястребов', 'Климентий']
OTHER_SECRETS = ['pelagea.snegireva', 'example.org', '234 56 78', '234-56-78', 'феофан-из-личного-дела', 'расписание-щеглова']
HARMLESS = 'Квадратные уравнения'
PHONE_TEXT = 'Позвонить по номеру 8 (701) 234-56-78 и узнать задание'


@dataclass
class School:
    source: Path
    counts: dict


def numbers(client):
    """The figures a school reads off its own application: classes, pupils, lessons, marks, attendance."""
    state = client.get('/api/state?workspace=demo').json()
    day = client.get('/api/journal/day?workspace=demo&day=' + DAY).json()['lessons']
    marks, marks_sum, present = 0, 0, 0
    for item in day:
        record = client.get('/api/journal/entry', params=dict(workspace='demo', lessonKey=item['lessonKey'], day=DAY)).json()['document']
        for row in record['content']['records']:
            present += row['attendance'] == 'present'
            for grade in row['grades']:
                marks += 1
                marks_sum += grade['score'] or 0
    return dict(classes=len(state['classes']), pupils=len(state['students']), teachers=len(state['teachers']), placements=len(state['placements']),
                lessons=len(day), marks=marks, marksSum=marks_sum, present=present, **final_grades(client))


def final_grades(client):
    """The closed period of the school: how many books and rows of final grades the administrator sees."""
    books = client.get('/api/final-grades/options?workspace=demo').json()['books']
    return dict(books=len(books), finalRows=sum(client.get(f'/api/final-grades/books/{b["id"]}?workspace=demo').json()['total'] for b in books))


PERIOD = dict(workspace='demo', start='2026-08-01', end='2026-09-30')


def pupil_view(client, student):
    """What a pupil's screens read: the published marks and the attendance of the period."""
    answer = client.get('/api/journal/pupil-period', params=dict(PERIOD, studentId=student))
    assert answer.status_code == 200, answer.text
    body = answer.json()
    return dict(marks=len(body['marks']), marksSum=sum(m['score'] or 0 for m in body['marks']), attendance=len(body['attendance']))


def signed_in(login, password):
    client = TestClient(api.app, headers=HEADERS)
    answer = client.post('/api/auth/login', json=dict(login=login, password=password))
    assert answer.status_code == 200, answer.status_code
    return client


def walkthrough(accounts):
    """Each of the four accounts of a copy signs in and reads its own start screens; the figures are returned.
    Runs in a process that was started with TIMETABLE_DB of the copy, the way a colleague's server is."""
    passwords = {a['login']: a['password'] for a in accounts}
    admin, teacher = signed_in('copy-admin', passwords['copy-admin']), signed_in('copy-teacher', passwords['copy-teacher'])
    pupil, parent = signed_in('copy-pupil', passwords['copy-pupil']), signed_in('copy-parent', passwords['copy-parent'])
    own = pupil.get('/api/portal/options').json()[0]['records']
    student = next(r['id'] for r in own if r['view'] == 'student')
    other = next(s['id'] for s in admin.get('/api/state?workspace=demo').json()['students'] if s['id'] != student)
    day = lambda client: sorted(r['lessonKey'] for r in client.get('/api/journal/day?workspace=demo&day=' + DAY).json()['lessons'])
    family = parent.get('/api/portal/family').json()['items']
    schedule = pupil.get('/api/portal/schedule', params=dict(workspace='demo', view='student', entityId=student)).json()
    return dict(numbers=numbers(admin), student=student, pupil=pupil_view(pupil, student), parent=pupil_view(parent, student),
                teacherDay=day(teacher), adminDay=day(admin), family=[(i['studentId'], i['verified'], i['status']) for i in family],
                scheduleRows=len(schedule['rows']), pupilOther=pupil.get('/api/journal/pupil-period', params=dict(PERIOD, studentId=other)).status_code,
                parentOther=parent.get('/api/journal/pupil-period', params=dict(PERIOD, studentId=other)).status_code,
                adminsUsers=sorted(u['login'] for u in admin.get('/api/access/users').json()), teacherUsers=teacher.get('/api/access/users').status_code,
                finals=final_grades(teacher))


@pytest.fixture(scope='module')
def school(tmp_path_factory):
    """A small school with canary people, built through the application (a TestClient on a temporary database)."""
    patch = pytest.MonkeyPatch()
    folder = tmp_path_factory.mktemp('source')
    patch.setattr(api, 'DB', folder / 'school.db')
    client = TestClient(api.app, headers=HEADERS)
    client.get('/api/state?workspace=demo')
    state = api.read('demo')
    state.settings.name = 'Гимназия имени Щеглова'
    for teacher, name in zip(state.teachers, ['Попугаев Никодим', 'Скворцова Агата', 'Щеглова Аглая', 'Ястребов Климентий']):
        teacher.name = name
    for pupil, name in zip(state.students, ['Канарейкин Феофан', 'Снегирёв Аристарх', 'Синицына Мелания', 'Воробьёв Пантелеймон']):
        pupil.name = name
    state.students[0].externalId = 'феофан-из-личного-дела-77015550123'
    state.importInfo.filename = 'расписание-щеглова.xlsx'
    state.importInfo.notes = ['Проверено Скворцовой Агатой']
    state.groups[0].rosterNote = 'Список прислал Попугаев Никодим'
    state.absences = [TeacherAbsence(id='away', name='Болезнь Щегловой', teacherId='t2', start='2026-08-18', end='2026-08-19')]
    state.placements = [Placement(id='eng', workloadId='w0', day=0, period=1, roomId='r0'), Placement(id='math', workloadId='w4', day=0, period=2, roomId='r1')]
    api.save('demo', state)
    enable(client)
    create(client, 'teacher', login='aglaya', name='Щеглова Аглая')
    parent = create(client, 'parent', login='pelagea', name='Снегирёва Пелагея', grants=[dict(workspace='demo', view='student', entityIds=['s2'])])
    create(client, 'student', login='feofan', name='Канарейкин Феофан', grants=[dict(workspace='demo', view='student', entityIds=['s1'])])
    # The journal: a topic that names a pupil, a harmless one, comments on children, a phone number in a text.
    english = entry(client, lesson(client, 'Английский язык')['lessonKey'])
    content = english['document']['content']
    content.update(topic='Опрос: у доски Канарейкин', homework='Прочитать страницу 12',
                   assessments=[dict(id='work', name='Проверочная', kind='points', maxScore=10, weight=1)])
    for number, row in enumerate(content['records']):
        row.update(attendance=['present', 'late'][number % 2], lateMinutes=[0, 10][number % 2], comment='Феофан сегодня расстроен',
                   grades=[dict(assessmentId='work', score=8 - number, label='', comment='Молодец, Канарейкин!')])
    assert write(client, english, publish=True).status_code == 200
    math = entry(client, lesson(client, 'Математика')['lessonKey'])
    content = math['document']['content']
    content.update(topic=HARMLESS, homework=PHONE_TEXT, assessments=[dict(id='quiz', name='Самостоятельная', kind='points', maxScore=5, weight=2)])
    for number, row in enumerate(content['records']):
        row.update(attendance='present', grades=[dict(assessmentId='quiz', score=5 - number, label='', comment='')])
    assert write(client, math, publish=True).status_code == 200
    # A closed period with its book of final grades: rows are assigned to a teacher account of the school, which the copy does not have.
    patch.setattr('backend.journal_periods.now', lambda: datetime(2026, 10, 14, 10, tzinfo=timezone.utc))
    patch.setattr('backend.journal_coverage.now', lambda: datetime(2026, 10, 14, 10, tzinfo=timezone.utc))
    closed = close(client, period(client))
    book = prepare(client, closed)
    scale(client)
    api.shutdown_gradebook_jobs()
    assert client.get(f'/api/final-grades/books/{book["id"]}?workspace=demo').json()['total'] >= 2
    # Things that must never reach the copy: a message with contacts, an absence notice, a note about a child.
    teacher = next(u for u in client.get('/api/access/users').json() if u['login'] == 'aglaya')
    mother = signed('pelagea')
    sent = mother.post('/api/messages/threads?workspace=demo', json=dict(requestId=str(uuid4()), title='Вопрос', recipientId=teacher['id'], studentId='s2',
                       text='Это Пелагея Снегирёва, телефон +7 701 234 56 78, почта pelagea.snegireva@example.org'))
    assert sent.status_code == 200, sent.text
    absence = mother.post('/api/journal/absences?workspace=demo', json=dict(requestId=str(uuid4()), studentId='s2', startDate=DAY, endDate=DAY,
                          reason='Аристарх Снегирёв болен', fileIds=[]))
    assert absence.status_code == 200, absence.text
    note = client.post('/api/behaviour/notes?workspace=demo', json=dict(requestId=str(uuid4()), studentIds=['s1'], categoryId='activity', day=DAY,
                       text='Феофан Канарейкин помог, пишите на feofan.k@example.org'))
    assert note.status_code == 200, note.text
    # The undo history still holds the placeholder names of the test school ("Учитель математики"): those words would be names to look for.
    with sqlite3.connect(api.DB) as db:
        db.execute('DELETE FROM history')
    counts = numbers(client)
    assert counts['lessons'] == 2 and counts['marks'] == 4 and counts['pupils'] == 4 and counts['books'] == 1 and counts['finalRows'] >= 2
    client.close()
    yield School(api.DB, counts)
    patch.undo()


def run_cli(source, output):
    """The tool as a colleague runs it: a separate process; its whole console output is returned."""
    done = subprocess.run([sys.executable, '-X', 'utf8', '-m', 'tools.anonymise_school', '--database', str(source), '--output', str(output)],
                          cwd=tool.ROOT, capture_output=True, env={**os.environ, 'PYTHONUTF8': '1'})
    return done.returncode, done.stdout.decode('utf-8') + done.stderr.decode('utf-8')


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def leaks(text):
    low = text.casefold().replace('ё', 'е')
    return [w for w in CANARIES + OTHER_SECRETS if w.casefold().replace('ё', 'е') in low]


@pytest.fixture(scope='module')
def copy(school, tmp_path_factory):
    """One successful run of the real command; the source is hashed before and after."""
    output = tmp_path_factory.mktemp('copy') / 'result'
    before = digest(school.source)
    code, console = run_cli(school.source, output)
    return dict(code=code, console=console, output=output, same_source=digest(school.source) == before)


def test_a_successful_run_leaves_the_source_untouched_and_prints_only_counts(copy):
    assert copy['code'] == 0, copy['console']
    assert copy['same_source']
    assert leaks(copy['console']) == []
    report = json.loads((copy['output'] / 'report.json').read_text(encoding='utf-8'))
    assert report['tables']['journal_entries'] == 2 and report['gate']['findings'] == 0 and report['pseudonymsIssued'] >= 8
    assert report['hiddenCells']['homework_queue_tasks.preview'] == 1 and report['hiddenCells']['journal_entries.document'] >= 2
    assert all(isinstance(n, int) for n in report['hiddenCells'].values())
    # Tables that were left out show up as counts only.
    assert report['notCopied']['accounts'] == 4 and 'message_posts' in report['notCopied'] and 'accounts' not in report['tables']
    assert set(p.name for p in copy['output'].iterdir()) == {'timetable.db', 'report.json', 'accounts.json', 'README.txt'}
    assert report['accounts']['logins'] == ['copy-admin', 'copy-teacher', 'copy-parent', 'copy-pupil']
    assert all(a['password'] not in copy['console'] and a['login'] not in copy['console'] for a in accounts_of(copy))


def test_no_file_of_the_copy_holds_a_canary_in_its_raw_bytes(copy):
    assert copy['code'] == 0
    for path in copy['output'].rglob('*'):
        if path.is_file():
            assert leaks(path.read_bytes().decode('utf-8', 'ignore')) == [], path.name
            assert not path.name.endswith(('-wal', '-shm', '-journal'))
    raw = (copy['output'] / 'timetable.db').read_bytes().decode('utf-8', 'ignore')
    assert '@example' not in raw and 'Позвонить' not in raw and 'Квадратные уравнения' in raw


def served(copy, folder):
    """A working copy of the copy's database: signing in writes sessions, so the delivered file itself stays as the tool made it."""
    target = folder / 'served.db'
    shutil.copyfile(copy['output'] / 'timetable.db', target)
    return target


def accounts_of(copy):
    return json.loads((copy['output'] / 'accounts.json').read_text(encoding='utf-8'))['accounts']


def test_the_application_serves_the_copy_with_the_same_numbers(school, copy, monkeypatch, tmp_path):
    assert copy['code'] == 0
    monkeypatch.setattr(api, 'DB', served(copy, tmp_path))
    admin = next(a for a in accounts_of(copy) if a['login'] == 'copy-admin')
    client = signed_in(admin['login'], admin['password'])
    assert numbers(client) == school.counts
    state = client.get('/api/state?workspace=demo').json()
    names = {s['name'] for s in state['students']} | {t['name'] for t in state['teachers']}
    assert len(names) == 8 and state['settings']['name'] == 'Школа (обезличенная копия)'
    assert all(not s.get('externalId') for s in state['students']) and state['importInfo'] == dict(filename='', sourceCells=0, importedLessons=0, notes=[])
    assert [a['name'] for a in state['absences']] == ['Отсутствие'] and state['groups'][0]['rosterNote'] == ''
    # The same pupil has the same pseudonym in the state, the journal and the lesson document.
    day = client.get('/api/journal/day?workspace=demo&day=' + DAY).json()['lessons']
    in_lessons = {s['name'] for row in day for s in client.get('/api/journal/entry', params=dict(workspace='demo', lessonKey=row['lessonKey'], day=DAY)).json()['document']['lesson']['students']}
    assert in_lessons and in_lessons <= names
    report = client.get('/api/journal/report', params=dict(workspace='demo', studentId='s1', start='2026-08-01', end='2026-09-01'))
    assert report.status_code == 200 and report.json()['rows']
    client.close()


def test_each_of_the_four_accounts_signs_in_to_a_server_started_on_the_copy(school, copy, tmp_path):
    """A separate process with TIMETABLE_DB of the copy, as a colleague starts the application; the source is read for comparison."""
    assert copy['code'] == 0
    accounts = accounts_of(copy)
    assert sorted(a['login'] for a in accounts) == ['copy-admin', 'copy-parent', 'copy-pupil', 'copy-teacher'] and all(len(a['password']) >= 16 for a in accounts)
    done = subprocess.run([sys.executable, '-X', 'utf8', str(Path(__file__)), str(copy['output'] / 'accounts.json')], cwd=tool.ROOT, capture_output=True,
                          env={**os.environ, 'PYTHONUTF8': '1', 'PYTHONPATH': str(tool.ROOT), 'TIMETABLE_DB': str(served(copy, tmp_path)), 'ATLAS_WEB_PUSH_ENABLED': '0'})
    assert done.returncode == 0, done.stderr.decode('utf-8', 'replace')[-1500:]
    seen = json.loads(done.stdout.decode('utf-8'))
    assert seen['numbers'] == school.counts
    # The pupil and the parent read the same marks and attendance of the pupil as the school's own administrator does in the source.
    source = signed_in('admin', PASSWORD)
    assert seen['pupil'] == seen['parent'] == pupil_view(source, seen['student']) and seen['pupil']['marks'] >= 2
    assert seen['family'] == [[seen['student'], True, 'active']]
    # The teacher sees lessons of their own, the family sees nobody else's pupil, and only the administrator manages accounts.
    assert seen['teacherDay'] and set(seen['teacherDay']) <= set(seen['adminDay'])
    # Final grades: the rows of the teacher's own classes and subjects are assigned to the copy's teacher account.
    assert seen['finals']['books'] == 1 and 1 <= seen['finals']['finalRows'] <= school.counts['finalRows']
    assert seen['pupilOther'] == seen['parentOther'] == 403 and seen['teacherUsers'] == 403
    assert seen['adminsUsers'] == ['copy-admin', 'copy-parent', 'copy-pupil', 'copy-teacher'] and seen['scheduleRows']
    source.close()


def test_a_harmless_topic_survives_and_a_topic_naming_a_pupil_is_hidden(copy, monkeypatch, tmp_path):
    assert copy['code'] == 0
    monkeypatch.setattr(api, 'DB', served(copy, tmp_path))
    admin = next(a for a in accounts_of(copy) if a['login'] == 'copy-admin')
    client = signed_in(admin['login'], admin['password'])
    topics, homework = {}, {}
    for item in client.get('/api/journal/day?workspace=demo&day=' + DAY).json()['lessons']:
        content = client.get('/api/journal/entry', params=dict(workspace='demo', lessonKey=item['lessonKey'], day=DAY)).json()['document']['content']
        topics[item['lesson']['subject']], homework[item['lesson']['subject']] = content['topic'], content['homework']
        assert all(not r['comment'] and all(not g['comment'] for g in r['grades']) for r in content['records'])
    assert topics['Математика'] == HARMLESS and topics['Английский язык'] == tool.HIDDEN
    assert homework['Английский язык'] == 'Прочитать страницу 12' and homework['Математика'] == tool.HIDDEN
    client.close()


def test_what_the_policy_does_not_name_is_not_copied(copy):
    with sqlite3.connect(copy['output'] / 'timetable.db') as db:
        tables = [name for (name,) in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        filled = {t for t in tables if db.execute(f'SELECT 1 FROM "{t}" LIMIT 1').fetchone()}
        assert filled and filled <= set(POLICY.tables) | ACCOUNT_TABLES
        # The four accounts and their family link are all that the school's own accounts, messages, notes and histories are replaced by.
        assert filled & ACCOUNT_TABLES == {'accounts', 'family_links'} and db.execute('SELECT count(*) FROM accounts').fetchone()[0] == 4
        assert not filled & {'sessions', 'audit', 'login_journal', 'notifications', 'message_posts', 'absence_notices', 'behaviour_notes', 'family_history', 'journal_history',
                             'history', 'homework_submissions'}
        assert db.execute('PRAGMA freelist_count').fetchone()[0] == 0


# ---- fail closed (in this process, so that the policy can be changed; the schema step is still a child process)

def attempt(source, output, capsys, policy=POLICY):
    code = tool.main(['--database', str(source), '--output', str(output)], policy)
    shown = capsys.readouterr()
    return code, shown.out + shown.err


def test_a_column_without_a_rule_stops_the_tool_before_it_writes_anything(school, tmp_path, capsys):
    output = tmp_path / 'result'
    code, shown = attempt(school.source, output, capsys, POLICY.without('journal_members', 'name'))
    assert code == 2 and 'journal_members.name' in shown and not output.exists() and leaks(shown) == []


def test_a_policy_that_wrongly_keeps_a_name_makes_the_gate_remove_the_output(school, tmp_path, capsys):
    output = tmp_path / 'result'
    code, shown = attempt(school.source, output, capsys, POLICY.with_rule('journal_members', 'name', 'keep'))
    assert code == 3 and 'journal_members.name' in shown and not output.exists() and leaks(shown) == []
    assert list(tmp_path.iterdir()) == []


def test_a_table_filled_by_the_accounts_step_that_is_not_allow_listed_fails_the_gate(school, tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(tool, 'ACCOUNT_TABLES', frozenset({'accounts'}))
    output = tmp_path / 'result'
    code, shown = attempt(school.source, output, capsys)
    assert code == 3 and 'family_links.(table): not allow-listed' in shown and not output.exists() and leaks(shown) == []


def test_a_contact_that_is_kept_by_mistake_fails_the_gate(school, tmp_path, capsys):
    output = tmp_path / 'result'
    code, shown = attempt(school.source, output, capsys, POLICY.with_rule('homework_queue_tasks', 'preview', 'keep'))
    assert code == 3 and 'homework_queue_tasks.preview: phone' in shown and not output.exists() and leaks(shown) == []


def test_a_person_id_that_is_not_opaque_stops_the_tool_with_shapes_only(school, tmp_path, capsys):
    variant = tmp_path / 'variant.db'
    shutil.copyfile(school.source, variant)
    with sqlite3.connect(variant) as db:
        db.execute("UPDATE workspaces SET document=replace(document,'\"id\":\"s1\"','\"id\":\"Канарейкин-Феофан-4\"')")
    output = tmp_path / 'result'
    code, shown = attempt(variant, output, capsys)
    assert code == 2 and 'aaaaaaaaaa-aaaaaa-9' in shown and not output.exists() and leaks(shown) == []


def test_an_output_that_exists_is_refused_and_left_as_it_was(school, tmp_path, capsys):
    output = tmp_path / 'result'
    output.mkdir()
    (output / 'mine.txt').write_text('keep', encoding='utf-8')
    code, shown = attempt(school.source, output, capsys)
    assert code == 2 and [p.name for p in output.iterdir()] == ['mine.txt']


def test_an_output_inside_the_repository_is_refused(school, capsys):
    output = tool.ROOT / 'atlas-anonymised-test-output'
    code, shown = attempt(school.source, output, capsys)
    assert code == 2 and not output.exists()
    code, shown = attempt(school.source, tool.ROOT / 'data' / 'copy', capsys)
    assert code == 2 and not (tool.ROOT / 'data' / 'copy').exists()


def test_a_source_that_is_not_a_file_is_refused(tmp_path, capsys):
    code, shown = attempt(tmp_path, tmp_path / 'result', capsys)
    assert code == 2 and not (tmp_path / 'result').exists()
    (tmp_path / 'not-a-database.db').write_text('just text', encoding='utf-8')
    code, shown = attempt(tmp_path / 'not-a-database.db', tmp_path / 'result', capsys)
    assert code == 2 and not (tmp_path / 'result').exists()


# ---- the policy itself

def test_every_column_of_every_allow_listed_table_has_a_rule_and_every_rule_names_a_column(tmp_path):
    fresh = tmp_path / 'fresh.db'
    tool.run_child('schema', fresh)
    columns = tool.columns_of(fresh, POLICY.tables)
    assert set(columns) == set(POLICY.tables)
    tool.check_rules(POLICY, columns, {})
    assert POLICY.problems() == []
    with pytest.raises(tool.Refused):
        tool.check_rules(POLICY.without('grade_events', 'document'), columns, {})
    with pytest.raises(tool.Refused):
        tool.check_rules(POLICY.with_rule('grade_events', 'no_such_column', 'keep'), columns, {})


def test_accounts_go_to_the_busiest_teacher_and_a_pupil_of_the_class_with_the_most_marks(tmp_path):
    state = dict(teachers=[dict(id='t1', name='Первый Учитель'), dict(id='t2', name='Второй Учитель')], students=[
        dict(id='s1', name='Ученик Один', classId='c1'), dict(id='s2', name='Ученик Два', classId='c1'), dict(id='s3', name='Ученик Три', classId='c2'),
        dict(id='s4', name='Ученик Четыре', classId='c2'), dict(id='s5', name='Ушедший Ученик', classId='c2', active=False)],
        groups=[dict(id='g1', classIds=['c1']), dict(id='g2', classIds=['c2'])],
        workloads=[dict(id='w1', groupId='g1', subjectId='math', teacherId='t1'), dict(id='w2', groupId='g2', subjectId='art', teacherId='t2', additionalTeacherIds=['t1'])],
        placements=[dict(workloadId='w1'), dict(workloadId='w2'), dict(workloadId='w2')])
    path = tmp_path / 'copy.db'
    with closing(sqlite3.connect(path)) as db:
        db.execute('CREATE TABLE workspaces(id TEXT, document TEXT)')
        db.execute('CREATE TABLE grade_events(workspace TEXT, student_id TEXT, hidden INTEGER)')
        db.execute('INSERT INTO workspaces VALUES (?,?)', ('school', json.dumps(state, ensure_ascii=False)))
        # c1 has 3 marks (one hidden), c2 has 4: the pupil of c2 with most marks is s4, and s5 has left.
        db.executemany('INSERT INTO grade_events VALUES (?,?,?)', [('school', s, h) for s, h in
                       [('s1', 0), ('s1', 0), ('s2', 1), ('s3', 0), ('s4', 0), ('s4', 0), ('s4', 0), ('s5', 0), ('s5', 0), ('s5', 0)]])
        db.commit()
    plan = tool.choose_people(path)
    assert plan['workspace'] == 'school' and plan['teacher']['id'] == 't1' and plan['pupil']['id'] == 's4'
    assert plan['teaching'] == [('c1', 'math'), ('c2', 'art')]


def test_a_child_process_works_only_on_the_database_it_was_made_for(tmp_path, monkeypatch):
    monkeypatch.setenv('TIMETABLE_DB', str(tmp_path / 'other.db'))
    with pytest.raises(tool.Refused):
        tool.child_guard(tmp_path / 'mine.db')
    monkeypatch.setenv('TIMETABLE_DB', str(tmp_path / 'mine.db'))
    tool.child_guard(tmp_path / 'mine.db')


def test_pseudonyms_are_many_stable_and_never_a_real_name():
    assert anonymise_names.POOL_SIZE >= 3000
    registry = tool.Registry()
    for name in ['Иванов Пётр', 'Иванова Анна']:
        registry.add(name)
    issued = tool.Pseudonyms(registry)
    people = [f'Реальный Человек{i}' for i in range(3000)]
    first = [issued.issue(p) for p in people]
    assert len(set(first)) == 3000 and [issued.issue(p) for p in people] == first
    assert not set(first) & registry.names and issued.issue('Иванов Пётр') != 'Иванов Пётр'


def test_names_are_found_as_whole_words_in_any_case_and_texts_with_contacts_are_hidden():
    registry = tool.Registry()
    registry.add('Канарейкин Феофан')
    assert registry.hit('опрос: КАНАРЕЙКИН у доски') and registry.hit('ответ Феофану') and registry.hit('Канарейкина')
    assert not registry.hit('Квадратные уравнения') and not registry.hit('Фео')
    assert tool.has_phone('звоните +7 701 234 56 78') and tool.has_phone('87012345678') and not tool.has_phone('2026-10-05 10:00:00 и 57.77777777777777')
    assert tool.has_email('a.b@example.org') and not tool.has_email('вопрос @ ответ')
    assert tool.mask('Иван-12') == 'aaaa-99'


if __name__ == '__main__':
    # The process of a colleague's server on a copy: python tests/test_anonymise_school.py accounts.json
    print(json.dumps(walkthrough(json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))['accounts']), ensure_ascii=False))
