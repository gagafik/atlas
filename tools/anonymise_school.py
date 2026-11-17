"""Pseudonymised copy of a school database, to hand to colleagues.

    python -m tools.anonymise_school --database data/timetable.db --output ../atlas-copy

The copy keeps the classes, timetable, journal, marks and attendance and gives
every person an invented name. It has no contacts, accounts, messages, notes
about children, files or histories. The tool fails closed: the copy is a NEW
database whose tables come from the application's own code, only tables of the
allow-list in tools/anonymise_policy.py are filled, and a last step reads the
finished file back and removes the whole output if any real name, e-mail
address or phone number is left. The source is only read, from a snapshot.
Console output and report.json hold counts, never a name or a text value.
"""
import argparse
from collections import Counter
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import sqlite3
import string
import subprocess
import sys
import tempfile
import unicodedata

from tools import anonymise_names as invented
from tools.anonymise_policy import ACCOUNT_CLEARED, ACCOUNT_TABLES, HIDDEN, IGNORED_NAMES, NAME_COLUMNS, NAME_LEAF_KEYS, NAME_PARENT_KEYS, NAME_SUFFIXES, NAME_TABLES, POLICY, \
    STATE_DOCUMENTS, STUDENT_ID_COLUMNS, TEACHER_ID_COLUMNS, Policy

ROOT = Path(__file__).resolve().parent.parent
DATABASE = 'timetable.db'
UNVERIFIED = 'НЕ-ПЕРЕДАВАТЬ-не-проверено.txt'
LETTERS = re.compile(r'[^\W\d_]+')
VOWELS = 'аяйьыиеоуюэ'
EMAIL = re.compile(r'[\w.+-]+@[\w-]+(?:\.[\w-]+)+')
STAMP = re.compile(r'\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?)?')
# The digits after a decimal point are a number, not a phone: `57.77777777777`.
PHONE = re.compile(r'(?<!\w)(?<!\d[.,])\+?[\d(][\d\s\-()]{8,}(?!\w)')
# The shapes of the ids that the import tools and the application make: a short letter prefix with digits, a hash or a uuid.
OPAQUE = re.compile(r'[a-z]{1,4}\d{1,9}(?:-\d{1,6}){0,3}|[a-z]{1,12}(?:-[a-z]{1,4})?-[0-9a-f]{8,64}|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}'
                    r'|[0-9a-f]{16,64}|asc-(?:[a-z]{1,4})-(?=[0-9a-z*_.-]*\d)[0-9a-z*_.-]{1,40}')
CHUNK = 500
STOP = 'Остановлено: '
CHILD_MARK = ' [шаг]'
HEADERS = {'X-Atlas-Request': '1'}
ROLES = {'admin': 'admin', 'teacher': 'teacher', 'parent': 'parent', 'pupil': 'student'}
LOGINS = {role: f'copy-{role}' for role in ROLES}


class Refused(Exception):
    """The tool stops. The message is for people and never holds a name or a text value of the school."""


class GateFailed(Exception):
    def __init__(self, findings):
        super().__init__('gate')
        self.findings = findings


def norm(text):
    """One spelling of a letter with a mark (a decomposed й is two characters otherwise)."""
    return text if text.isascii() else unicodedata.normalize('NFKC', text)


def fold(word):
    return word.casefold().replace('ё', 'е')


def quote(identifier):
    return '"' + identifier.replace('"', '""') + '"'


def mask(value):
    """Every letter becomes a, every digit 9: the shape of an id without its content."""
    return ''.join('a' if c.isalpha() else '9' if c.isdigit() else c for c in value)


def has_email(text):
    return '@' in text and bool(EMAIL.search(text))


def has_phone(text):
    """A run of 10 digits or more, spaces, dashes and brackets allowed inside it; dates and times do not count."""
    return sum(c.isdigit() for c in text) >= 10 and any(sum(c.isdigit() for c in m.group()) >= 10 for m in PHONE.finditer(STAMP.sub(' ', text)))


def contact(text):
    return has_email(text) or has_phone(text)


class NameIndex:
    """Exact full names found in a text, by their first word; the longest name wins."""

    def __init__(self, names=()):
        self.first = {}
        for name in names:
            self.add(name)

    def add(self, name):
        found = LETTERS.search(name)
        if found:
            bucket = self.first.setdefault(found.group(), [])
            if name not in bucket:
                bucket.append(name)
                bucket.sort(key=len, reverse=True)

    def split(self, text):
        """[(segment, is_name)] with the names found; one segment of the text when none is found."""
        if not self.first:
            return [(text, False)]
        parts, last = [], 0
        for m in LETTERS.finditer(text):
            if m.start() < last:
                continue
            for name in self.first.get(m.group(), ()):
                end = m.start() + len(name)
                if text.startswith(name, m.start()) and (end == len(text) or not text[end].isalnum()) and (m.start() == 0 or not text[m.start() - 1].isalnum()):
                    if m.start() > last:
                        parts.append((text[last:m.start()], False))
                    parts.append((name, True))
                    last = end
                    break
        if last < len(text):
            parts.append((text[last:], False))
        return parts or [(text, False)]


class Registry:
    """The real names of the school: full names to replace and the words (tokens) that must not survive anywhere."""

    def __init__(self):
        self.names, self.tokens, self.stems = set(), set(), set()
        self.index = NameIndex()
        self.words = {}

    def add(self, value):
        if not isinstance(value, str):
            return
        value = value.strip()
        if not value or fold(value) in IGNORED_NAMES:
            return
        words = {fold(w) for w in LETTERS.findall(norm(value))}
        for word in words:
            if len(word) >= 3:
                self.tokens.add(word)
                if len(word) >= 5:
                    self.stems.add(word[:-1] if word[-1] in VOWELS else word)
        if any(len(w) >= 3 for w in words) and len(value) >= 5:
            self.names.add(value)
            self.index.add(value)

    def hit(self, text):
        """Whether the text holds a real-name token as a whole word (or with an ending of the language: a name in another case)."""
        for word in LETTERS.findall(norm(text)):
            known = self.words.get(word)
            if known is None:
                f = fold(word)
                n = len(f)
                known = n >= 3 and (f in self.tokens or any(f[:k] in self.stems for k in range(max(4, n - 4), n)))
                if len(self.words) < 500000:
                    self.words[word] = known
            if known:
                return True
        return False


class Pseudonyms:
    """One real full name, one invented name, for the whole run. Never written anywhere."""

    def __init__(self, registry):
        self.registry, self.map, self.used = registry, {}, set()

    def issue(self, real):
        known = self.map.get(real)
        if known:
            return known
        female = invented.is_female(real)
        for _ in range(200000):
            name = invented.invent(female)
            if name not in self.used and name not in self.registry.names and fold(name.split()[0]) not in self.registry.tokens:
                break
        else:
            raise Refused('Запас вымышленных имён исчерпан.')
        self.used.add(name)
        self.map[real] = name
        return name


class Transformer:
    """The column rules applied to one value; counts what was hidden, per table.column."""

    def __init__(self, registry, pseudonyms, policy):
        self.registry, self.pseudonyms, self.policy = registry, pseudonyms, policy
        self.hidden = Counter()
        self.salt = secrets.token_bytes(16)
        self.memo = {}

    def person(self, value):
        return self.pseudonyms.issue(value) if isinstance(value, str) and value.strip() else value

    def text(self, value, where):
        if not isinstance(value, str) or not value:
            return value
        if self.registry.hit(value) or contact(value):
            self.hidden[where] += 1
            return HIDDEN
        return value

    def names(self, value, where):
        """Known full names become pseudonyms; the rest of the text is then checked like any text."""
        if not isinstance(value, str) or not value:
            return value
        known = self.memo.get(value)
        if known is None:
            parts = self.registry.index.split(value)
            rest = '\x00'.join(p for p, is_name in parts if not is_name)
            hidden = self.registry.hit(rest) or contact(rest)
            known = (HIDDEN if hidden else ''.join(self.pseudonyms.issue(p) if is_name else p for p, is_name in parts), hidden)
            if len(self.memo) < 200000:
                self.memo[value] = known
        if known[1]:
            self.hidden[where] += 1
        return known[0]

    def value(self, rule, value, where, column_empty):
        if value is None or rule in ('keep', 'number'):
            return value
        if rule == 'drop':
            return column_empty
        if rule == 'person':
            return self.person(value)
        if rule == 'text':
            return self.text(value, where)
        if rule == 'names':
            return self.names(value, where)
        if rule == 'hash':
            return hashlib.sha256(self.salt + str(value).encode('utf-8')).hexdigest() if isinstance(value, str) else value
        return self.document(value, self.policy.profile(rule), where)

    def document(self, raw, profile, where):
        if not isinstance(raw, str):
            raise Refused(f'{where}: ожидался документ JSON.')
        try:
            node = json.loads(raw)
        except ValueError:
            raise Refused(f'{where}: значение не является документом JSON.') from None
        return json.dumps(self.walk(node, (), profile, where), ensure_ascii=False, separators=(',', ':'))

    def walk(self, node, path, profile, where):
        if isinstance(node, dict):
            result = {}
            for key, child in node.items():
                here = path + (key,)
                dotted = '.'.join(here)
                if key in profile.drop:
                    result[key] = profile.drop[key]
                elif dotted in profile.const:
                    result[key] = profile.const[dotted]
                else:
                    result[key] = self.walk(child, here, profile, where)
            return result
        if isinstance(node, list):
            return [self.walk(child, path, profile, where) for child in node]
        if isinstance(node, str):
            dotted = '.'.join(path)
            if dotted in profile.people:
                return self.person(node)
            if dotted in profile.text:
                return self.text(node, where)
            return self.names(node, where)
        return node


# ---- the registry and the ids, from the snapshot of the source

def table_columns(db, table):
    return [row[1] for row in db.execute(f'PRAGMA table_info({quote(table)})')]


def source_tables(db):
    return sorted(name for (name,) in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"))


def is_name_column(table, column):
    c = column.lower()
    return c in NAME_COLUMNS or c.endswith(NAME_SUFFIXES) or (c == 'name' and table in NAME_TABLES)


def collect_names(node, found, parent=''):
    """Names of people in a JSON document: a `name` under a key that holds people, and the keys that are names themselves."""
    if isinstance(node, dict):
        for key, child in node.items():
            if isinstance(child, str):
                if (key == 'name' and parent in NAME_PARENT_KEYS) or key in NAME_LEAF_KEYS:
                    found.append(child)
            else:
                collect_names(child, found, key if isinstance(child, (dict, list)) else parent)
    elif isinstance(node, list):
        for child in node:
            collect_names(child, found, parent)


def documents(db, table, column):
    cursor = db.execute(f'SELECT {quote(column)} FROM {quote(table)} WHERE typeof({quote(column)})=\'text\' AND substr({quote(column)},1,1) IN (\'{{\',\'[\')')
    while rows := cursor.fetchmany(CHUNK):
        for (raw,) in rows:
            try:
                yield json.loads(raw)
            except ValueError:
                continue


def build_registry(src):
    registry = Registry()
    names = []
    for table in source_tables(src):
        columns = table_columns(src, table)
        for column in columns:
            if is_name_column(table, column):
                names += [v for (v,) in src.execute(f'SELECT DISTINCT {quote(column)} FROM {quote(table)}')]
            for document in documents(src, table, column):
                collect_names(document, names)
    for name in names:
        registry.add(name)
    return registry


def state_ids(db, kind):
    found = []
    for table, column in STATE_DOCUMENTS:
        if column in table_columns(db, table):
            for document in documents(db, table, column):
                walk_ids(document, kind, found)
    return found


def walk_ids(node, kind, found):
    if isinstance(node, dict):
        for key, child in node.items():
            if key == kind and isinstance(child, list):
                found += [i['id'] for i in child if isinstance(i, dict) and isinstance(i.get('id'), str)]
            walk_ids(child, kind, found)
    elif isinstance(node, list):
        for child in node:
            walk_ids(child, kind, found)


def check_ids(src):
    """Person ids are kept as they are, so they must be opaque. Anything else stops the tool, shown only as a shape."""
    tables = set(source_tables(src))
    ids = set(state_ids(src, 'students')) | set(state_ids(src, 'teachers'))
    for table, column in STUDENT_ID_COLUMNS + TEACHER_ID_COLUMNS:
        if table in tables and column in table_columns(src, table):
            ids |= {v for (v,) in src.execute(f'SELECT DISTINCT {quote(column)} FROM {quote(table)}') if isinstance(v, str)}
    bad = Counter(mask(i) for i in ids if not OPAQUE.fullmatch(i))
    if bad:
        shapes = '; '.join(f'{shape} x{count}' for shape, count in sorted(bad.items()))
        raise Refused(f'Идентификаторы людей не непрозрачны (буква = a, цифра = 9): {shapes}. Копия не создана.')


# ---- the schema of the copy, made by the application itself

def child_guard(expected):
    """A child process works only on the database it was started for: the application reads TIMETABLE_DB at import."""
    given = os.environ.get('TIMETABLE_DB')
    if not given or os.path.normcase(os.path.realpath(given)) != os.path.normcase(os.path.realpath(expected)):
        raise Refused('TIMETABLE_DB не указывает на базу копии.')


def child_schema(path):
    child_guard(path)
    from backend import api
    from backend.final_grades import tables as grading_tables
    from backend.timetable_history import tables as timetable_tables
    with api.connect() as db:
        api.journal.prepare(db)
        grading_tables(db)
        timetable_tables(db)
    with closing(sqlite3.connect(path)) as db:
        db.execute('PRAGMA journal_mode=DELETE')


def run_child(kind, database, **paths):
    environment = {**os.environ, 'TIMETABLE_DB': str(database), 'PYTHONUTF8': '1', 'ATLAS_WEB_PUSH_ENABLED': '0', 'ATLAS_BACKUP_DIR': ''}
    command = [sys.executable, '-X', 'utf8', '-m', 'tools.anonymise_school', '--child', kind, '--database', str(database)]
    for name, value in paths.items():
        command += [f'--{name}', str(value)]
    done = subprocess.run(command, cwd=ROOT, env=environment, capture_output=True)
    if done.returncode:
        # The child output may hold anything: only the code is shown, and the child's own stop message (written by this tool, without values).
        lines = done.stderr.decode('utf-8', 'replace').splitlines() if done.returncode == 2 else []
        reason = next((l for l in reversed(lines) if l.startswith(STOP) and l.endswith(CHILD_MARK)), '')
        raise Refused(f'Шаг «{kind}» завершился с ошибкой (код {done.returncode}).' + (' ' + reason[len(STOP):-len(CHILD_MARK)] if reason else ''))


def check_rules(policy, fresh, src):
    """Every column of every allow-listed table has a rule, and every rule names a column of the application's schema."""
    problems = policy.problems()
    if problems:
        raise Refused('Правила неизвестного вида: ' + ', '.join(problems) + '.')
    without, stale = [], []
    for table, rules in policy.tables.items():
        if table not in fresh:
            stale.append(table)
            continue
        without += [f'{table}.{c}' for c in [*fresh[table], *src.get(table, [])] if c not in rules]
        stale += [f'{table}.{c}' for c in rules if c not in fresh[table]]
    if without:
        raise Refused('Столбцы без правила, копия не создана: ' + ', '.join(sorted(set(without))) + '.')
    if stale:
        raise Refused('Правила для того, чего нет в схеме приложения: ' + ', '.join(sorted(set(stale))) + '.')


def columns_of(path, tables):
    with closing(sqlite3.connect(path)) as db:
        return {t: table_columns(db, t) for t in tables if table_columns(db, t)}


# ---- copying

def copy_tables(src, out, policy, transformer, report):
    present = set(source_tables(src))
    for table, rules in policy.tables.items():
        if table not in present:
            report['missingInSource'].append(table)
            continue
        wanted = [c for c in rules if c in table_columns(src, table)]
        empty = {row[1]: ('' if row[3] else None) for row in out.execute(f'PRAGMA table_info({quote(table)})')}
        select = ','.join(map(quote, wanted))
        insert = f'INSERT OR REPLACE INTO {quote(table)} ({select}) VALUES ({",".join("?" * len(wanted))})'
        count = 0
        cursor = src.execute(f'SELECT {select} FROM {quote(table)}')
        while rows := cursor.fetchmany(CHUNK):
            out.executemany(insert, [[transformer.value(rules[c], v, f'{table}.{c}', empty[c]) for c, v in zip(wanted, row)] for row in rows])
            count += len(rows)
        report['tables'][table] = count


def check_projections(src, out):
    """Projections are copied as they are, so the source must have been made by the same version of the application."""
    if 'journal_projection_versions' not in source_tables(src):
        return
    wanted = set(out.execute('SELECT name,version FROM journal_projection_versions'))
    if not wanted <= set(src.execute('SELECT name,version FROM journal_projection_versions')):
        raise Refused('База сделана другой версией Атласа: откройте её в текущей версии один раз и повторите.')


# ---- the accounts of the copy

def choose_people(path):
    """Who gets an account, read from the finished copy (its names are already invented): the school with the most lessons, the teacher with
    the most lessons, a pupil of the class with the most marks. Ids and invented names only."""
    with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)) as db:
        states = {name: json.loads(document) for name, document in db.execute('SELECT id,document FROM workspaces ORDER BY id')}
        if not states:
            raise Refused('В копии нет школы: аккаунты создавать не для чего.')
        workspace = max(states, key=lambda w: (len(states[w].get('placements', [])), w))
        state = states[workspace]
        marks = Counter(student for (student,) in db.execute('SELECT student_id FROM grade_events WHERE workspace=? AND hidden=0', (workspace,)))
    workloads = {w['id']: w for w in state.get('workloads', [])}
    lessons = Counter()
    for placement in state.get('placements', []):
        w = workloads.get(placement.get('workloadId'))
        for teacher in ([w.get('teacherId'), *w.get('additionalTeacherIds', [])] if w else []):
            if teacher:
                lessons[teacher] += 1
    teachers = {t['id']: t for t in state.get('teachers', [])}
    teacher = min((t for t in lessons if t in teachers), key=lambda t: (-lessons[t], t), default=None)
    pupils = [s for s in state.get('students', []) if s.get('active', True)]
    if not teacher or not pupils:
        raise Refused('В копии нет учителя с уроками или ученика: аккаунты создавать не для чего.')
    size, total = Counter(s['classId'] for s in pupils), Counter()
    for s in pupils:
        total[s['classId']] += marks[s['id']]
    school_class = min(size, key=lambda c: (-total[c], -size[c], c))
    pupil = min((s for s in pupils if s['classId'] == school_class), key=lambda s: (-marks[s['id']], s['id']))
    groups = {g['id']: g for g in state.get('groups', [])}
    pairs = sorted({(c, w['subjectId']) for w in workloads.values() if teacher in [w.get('teacherId'), *w.get('additionalTeacherIds', [])]
                    for c in groups.get(w.get('groupId'), {}).get('classIds', [])})
    return dict(workspace=workspace, workspaces=sorted(states), teacher=dict(id=teacher, name=teachers[teacher]['name']), pupil=dict(id=pupil['id'], name=pupil['name']),
                teaching=pairs)


def new_password():
    """20 letters and digits: no dash, so that the gate never takes a password for a phone number."""
    while True:
        password = ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(20))
        if not contact(password):
            return password


def child_accounts(path, plan_file, result_file):
    """In a child process whose TIMETABLE_DB is the copy: the application itself makes the accounts, the way the first start of a school does."""
    child_guard(path)
    plan = json.loads(Path(plan_file).read_text(encoding='utf-8'))
    from fastapi.testclient import TestClient
    from backend import api
    space = plan['workspace']

    def call(client, method, url, label, **options):
        # Only the label and the code are ever shown: an answer of the application may quote a value.
        answer = client.request(method, url, **options)
        if answer.status_code != 200:
            raise Refused(f'Аккаунты, {label}: ответ {answer.status_code}.')
        return answer.json() if answer.content else None

    admin = TestClient(api.app, headers=HEADERS)
    passwords = {role: new_password() for role in ROLES}
    people = dict(admin=plan['adminName'], teacher=plan['teacher']['name'], parent=plan['parentName'], pupil=plan['pupil']['name'])
    call(admin, 'POST', '/api/auth/setup', 'первый вход', json=dict(name=people['admin'], login=LOGINS['admin'], password=passwords['admin']))
    created = {}
    for role, view, ids in [('teacher', 'teacher', [plan['teacher']['id']]), ('pupil', 'student', [plan['pupil']['id']]), ('parent', 'student', [plan['pupil']['id']])]:
        made = call(admin, 'POST', '/api/access/users', f'создание ({role})', json=dict(
            name=people[role], login=LOGINS[role], role=ROLES[role], workspaces=[space], password=passwords[role], grants=[dict(workspace=space, view=view, entityIds=ids)]))
        created[role] = made['id']
    # The school records who the guardian is, so that the family screens show a verified relationship.
    where = f'/api/access/families/{created["parent"]}/{plan["pupil"]["id"]}'
    link = call(admin, 'GET', where, 'связь семьи', params=dict(workspace=space))
    call(admin, 'PUT', where, 'связь семьи (запись)', params=dict(workspace=space), json=dict(
        version=link['version'], accountVersion=link['accountVersion'], relationship='guardian', verified=True,
        evidence='Учётная запись обезличенной копии', status='active', reason='Обезличенная копия'))
    with api.connect() as db:
        # The final grades of the teacher's classes and subjects are the ones the teacher's own screen shows.
        for school_class, subject in plan['teaching']:
            db.execute('UPDATE grading_rows SET assigned=? WHERE workspace=? AND class_id=? AND subject_id=?', (created['teacher'], space, school_class, subject))
    # Every account signs in once; the sessions made here are not kept.
    for role in ROLES:
        client = TestClient(api.app, headers=HEADERS)
        call(client, 'POST', '/api/auth/login', f'вход ({role})', json=dict(login=LOGINS[role], password=passwords[role]))
        status = call(client, 'GET', '/api/auth/status', f'состояние ({role})')
        if (status.get('user') or {}).get('role') != ROLES[role]:
            raise Refused(f'Аккаунты, роль {role}: вход под другой ролью.')
        client.close()
    admin.close()
    with api.connect() as db:
        for table in ACCOUNT_CLEARED:
            db.execute(f'DELETE FROM {quote(table)}')
    about = 'Учётные записи обезличенной копии. Пароли случайные и действуют только в этой копии. Не публикуйте этот файл.'
    names = dict(admin='администратор', teacher='учитель', parent='родитель', pupil='ученик')
    Path(result_file).write_text(json.dumps(dict(about=about, workspace=space, accounts=[dict(role=names[role], login=LOGINS[role], password=passwords[role], name=people[role])
                                                                                       for role in ROLES]), ensure_ascii=False, indent=1), encoding='utf-8')


README = """Обезличенная копия школьной базы «Атлас»

Что это. Копия настоящей школы с вымышленными именами всех людей: классы, расписание, журнал,
оценки и посещаемость. В ней нет контактов, переписки, заметок о детях, файлов, историй изменений
и настоящих учётных записей. Копия обезличена, но не анонимна: тот, кто знает школу, может узнать
ученика по классу, расписанию и оценкам. Текст, который прошёл проверку, писали учителя, и его
никто не читал глазами.

Как открыть. Нужны Git, Python 3.12 и Node.js 22 и клон репозитория https://github.com/gagafik/atlas
(подробно: CONTRIBUTING.md). Положите timetable.db в надёжное место вне клона и укажите её серверу.

  Windows (PowerShell, в каталоге клона):
    $env:TIMETABLE_DB = 'C:\\путь\\к\\копии\\timetable.db'
    ./start.ps1

  Linux/macOS (в каталоге клона, после установки из CONTRIBUTING.md):
    TIMETABLE_DB=/путь/к/копии/timetable.db python -m uvicorn backend.api:app --host 127.0.0.1 --port 8000

Откройте http://127.0.0.1:8000. Вход под четырьмя ролями (логины и пароли лежат в accounts.json):
администратор copy-admin, учитель copy-teacher, родитель copy-parent, ученик copy-pupil.
Изменения записываются в этот файл. Чтобы начать заново, возьмите файл из переданной папки ещё раз.

Чего нельзя. Не добавляйте копию и accounts.json в Git, PR, Docker-образ, журналы CI и публичные
хранилища. Передавайте их только лично и удалите, когда они больше не нужны.
Что оставлено и что убрано: ANONYMISED_COPY.md в репозитории и report.json рядом с этим файлом.
"""


def create_accounts(output, report, pseudonyms, work):
    """Four accounts, made by the application in a child process that works only on the copy; then README.txt."""
    plan = choose_people(output / DATABASE)
    plan.update(adminName='Администратор копии', parentName=pseudonyms.issue('\x00parent of ' + plan['pupil']['id']))
    plan_file = work / 'accounts-plan.json'
    plan_file.write_text(json.dumps(plan, ensure_ascii=False), encoding='utf-8')
    run_child('accounts', output / DATABASE, plan=plan_file, result=output / 'accounts.json')
    (output / 'README.txt').write_text(README, encoding='utf-8')
    report['accounts'] = dict(roles=list(ROLES), logins=list(LOGINS.values()))


def finish(path):
    with closing(sqlite3.connect(path)) as db:
        db.execute('PRAGMA journal_mode=DELETE')
        db.execute('VACUUM')


# ---- the gate

def strings_of(node, out):
    if isinstance(node, dict):
        for key, child in node.items():
            out.append(str(key))
            strings_of(child, out)
    elif isinstance(node, list):
        for child in node:
            strings_of(child, out)
    elif isinstance(node, str):
        out.append(node)


def decoded(text):
    """The text of a cell; the strings of a JSON document, one after another."""
    if text[:1] in ('{', '['):
        try:
            node = json.loads(text)
        except ValueError:
            return text
        found = []
        strings_of(node, found)
        return '\x00'.join(found)
    return text


class Gate:
    def __init__(self, registry, pseudonyms):
        self.registry, self.issued = registry, NameIndex(pseudonyms.used)
        self.cache, self.cells = {}, 0

    def kinds(self, text, technical):
        """What is wrong with a text: real-name tokens, an e-mail address, a phone-like run of digits."""
        key = (text, technical)
        known = self.cache.get(key)
        if known is None:
            rest = '\x00'.join(p for p, is_name in self.issued.split(decoded(text)) if not is_name)
            known = tuple(kind for kind, bad in [('name', self.registry.hit(rest)), ('email', has_email(rest)),
                                                 ('phone', not technical and has_phone(rest))] if bad)
            if len(text) <= 300 and len(self.cache) < 300000:
                self.cache[key] = known
        return known

    def database(self, path, policy, findings):
        with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)) as db:
            if db.execute('PRAGMA freelist_count').fetchone()[0]:
                findings[('(file)', 'free pages', 'leftover')] += 1
            for table in source_tables(db):
                columns = table_columns(db, table)
                allowed = table in policy.tables or table in ACCOUNT_TABLES
                if not allowed and db.execute(f'SELECT 1 FROM {quote(table)} LIMIT 1').fetchone():
                    findings[(table, '(table)', 'not allow-listed')] += 1
                    continue
                for column in columns:
                    technical = policy.rule(table, column) == 'number'
                    for (value,) in db.execute(f'SELECT {quote(column)} FROM {quote(table)}'):
                        self.cells += 1
                        if isinstance(value, bytes):
                            findings[(table, column, 'blob')] += 1
                        elif isinstance(value, str):
                            for kind in self.kinds(value, technical):
                                findings[(table, column, kind)] += 1
            for (sql,) in db.execute('SELECT sql FROM sqlite_master WHERE sql IS NOT NULL'):
                for kind in self.kinds(sql, False):
                    findings[('sqlite_master', 'sql', kind)] += 1

    def file(self, path, findings):
        text = path.read_text(encoding='utf-8', errors='replace')
        self.cells += 1
        for kind in self.kinds(text, False):
            findings[(path.name, '(file)', kind)] += 1


def run_gate(output, registry, pseudonyms, policy):
    gate, findings = Gate(registry, pseudonyms), Counter()
    gate.database(output / DATABASE, policy, findings)
    for path in sorted(output.iterdir()):
        if path.name in (DATABASE, 'README.txt', UNVERIFIED):
            continue
        if path.suffix in ('-wal', '-shm', '-journal') or path.name.endswith(('-wal', '-shm', '-journal')):
            findings[(path.name, '(file)', 'leftover')] += 1
        elif path.is_file():
            gate.file(path, findings)
        else:
            findings[(path.name, '(directory)', 'leftover')] += 1
    if findings:
        raise GateFailed(findings)
    return gate.cells


# ---- the run

def check_paths(source, output):
    if not source.is_file():
        raise Refused('Источник должен быть существующим файлом базы данных.')
    if output.exists():
        raise Refused('Папка результата уже существует: выберите новую.')
    where = output.resolve()
    root = ROOT.resolve()
    if where == root or root in where.parents or any((parent / '.git').exists() for parent in where.parents):
        raise Refused('Результат нельзя класть в репозиторий Git: копия не должна попасть в историю.')
    if source.resolve() == where or where in source.resolve().parents:
        raise Refused('Папка результата не может содержать источник.')


def snapshot(source, target):
    """A consistent snapshot through the backup API from a read-only connection; the source is never opened for writing."""
    try:
        with closing(sqlite3.connect(source.resolve().as_uri() + '?mode=ro', uri=True, timeout=30)) as read, closing(sqlite3.connect(target)) as copy:
            read.backup(copy)
            copy.execute('PRAGMA journal_mode=DELETE')
    except sqlite3.Error:
        raise Refused('Источник не удалось прочитать как базу SQLite.') from None


def run(source, output, policy=POLICY, log=print):
    source, output = Path(source), Path(output)
    check_paths(source, output)
    work = Path(tempfile.mkdtemp(prefix='atlas-anonymise-'))
    created = False
    try:
        snap, fresh = work / 'snapshot.db', work / 'schema.db'
        snapshot(source, snap)
        with closing(sqlite3.connect(snap.as_uri() + '?mode=ro', uri=True)) as src:
            src.execute('PRAGMA query_only=ON')
            registry = build_registry(src)
            check_ids(src)
            run_child('schema', fresh)
            check_rules(policy, columns_of(fresh, policy.tables), {t: table_columns(src, t) for t in source_tables(src) if t in policy.tables})
            output.mkdir(parents=True, exist_ok=False)
            created = True
            # Present until the gate has passed: a run that is killed half way leaves a folder that says it must not be shared.
            (output / UNVERIFIED).write_text('Эта папка не проверена и не закончена. Не передавайте её, удалите.\n', encoding='utf-8')
            target = output / DATABASE
            shutil.copyfile(fresh, target)
            pseudonyms = Pseudonyms(registry)
            transformer = Transformer(registry, pseudonyms, policy)
            report = dict(format=1, tables={}, missingInSource=[], notCopied={}, hiddenCells={})
            with closing(sqlite3.connect(target)) as out:
                out.execute('PRAGMA journal_mode=MEMORY')
                out.execute('PRAGMA synchronous=OFF')
                check_projections(src, out)
                copy_tables(src, out, policy, transformer, report)
                out.commit()
            report['notCopied'] = {t: n for t in source_tables(src) if t not in policy.tables
                                   if (n := src.execute(f'SELECT count(*) FROM {quote(t)}').fetchone()[0])}
        create_accounts(output, report, pseudonyms, work)
        finish(target)
        report['hiddenCells'] = dict(sorted(transformer.hidden.items()))
        report['pseudonymsIssued'] = len(pseudonyms.used)
        (output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding='utf-8')
        cells = run_gate(output, registry, pseudonyms, policy)
        (output / UNVERIFIED).unlink()
        report['gate'] = dict(cellsChecked=cells, findings=0)
        (output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding='utf-8')
        return report
    except BaseException:
        if created:
            shutil.rmtree(output, ignore_errors=True)
        raise
    finally:
        shutil.rmtree(work, ignore_errors=True)


def describe(report):
    lines = [f'Таблиц скопировано: {len(report["tables"])}, строк: {sum(report["tables"].values())}']
    lines += [f'  {t}: {n}' for t, n in report['tables'].items()]
    if report['missingInSource']:
        lines.append('Нет в источнике: ' + ', '.join(report['missingInSource']))
    lines.append('Не копировалось (есть строки):')
    lines += [f'  {t}: {n}' for t, n in report['notCopied'].items()]
    lines.append('Текст скрыт в ячейках:' + ('' if report['hiddenCells'] else ' нигде'))
    lines += [f'  {c}: {n}' for c, n in report['hiddenCells'].items()]
    lines.append(f'Вымышленных имён выдано: {report["pseudonymsIssued"]}')
    lines.append('Учётные записи для входа (администратор, учитель, родитель, ученик): accounts.json в папке результата; пароли только там.')
    lines.append('Как открыть копию: README.txt в папке результата.')
    lines.append(f'Проверка пройдена: ячеек {report["gate"]["cellsChecked"]}, находок 0')
    return '\n'.join(lines)


def main(argv=None, policy=POLICY):
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--database', type=Path, required=True, help='the school database (only read)')
    parser.add_argument('--output', type=Path, help='a NEW directory outside the repository')
    parser.add_argument('--child', choices=['schema', 'accounts'], help=argparse.SUPPRESS)
    parser.add_argument('--plan', type=Path, help=argparse.SUPPRESS)
    parser.add_argument('--result', type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    try:
        if args.child:
            child_schema(args.database) if args.child == 'schema' else child_accounts(args.database, args.plan, args.result)
            return 0
        if not args.output:
            parser.error('--output is required')
        report = run(args.database, args.output, policy)
        print(f'Обезличенная копия создана: {args.output.resolve()}')
        print(describe(report))
        print('Копия обезличена, но не анонимна: передавайте её только лично и никогда не публикуйте.')
        return 0
    except Refused as stop:
        print(f'{STOP}{stop}{CHILD_MARK if args.child else ""}', file=sys.stderr)
        return 2
    except GateFailed as failed:
        print('Проверка не пройдена, результат удалён. Найдено (таблица, столбец, вид: число):', file=sys.stderr)
        for (table, column, kind), count in sorted(failed.findings.items()):
            print(f'  {table}.{column}: {kind} x{count}', file=sys.stderr)
        return 3
    except Exception as error:
        # Never the message: it may quote a value of the school.
        print(f'Остановлено: непредвиденная ошибка ({type(error).__name__}). Результат удалён.', file=sys.stderr)
        return 4


if __name__ == '__main__':
    sys.exit(main())
