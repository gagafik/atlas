"""The EduPage marks importer, driven through the real journal endpoints on a temporary demo school.

The class journals are built here from the demo pupils in the format the importer reads; the importer
talks to the application through a TestClient, exactly as it talks to a server over HTTP."""
from datetime import date, datetime, timedelta, timezone
import gzip
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
import socket
import ssl
import threading
import time
from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from backend import api, journal, journal_periods
from backend.models import Placement
from test_access import client, enable, create, signed, HEADERS, PASSWORD
from tools import import_edupage_grades as importer
from tools.demo.names import MALE, SURNAME_PAIRS

TODAY = date(2026, 9, 30)
MATH, ENGLISH = 'Математика', 'Английский язык'
FORMATIVE, SECTION, TERM = 'Формативное оценивание', 'Суммативное оценивание за раздел', 'Суммативное оценивание за семестр'
D1, D0 = '2026-09-14', '2026-09-07'  # Mondays: the school has its two lessons on Monday.
OUTSIDER = f'{SURNAME_PAIRS[0][0]} {MALE[0]}'  # A pupil EduPage has and the demo school has not.


class Wire:
    """The importer's HTTP object over a TestClient. before_put runs before the first PUT goes through (a colleague saving in between)."""

    def __init__(self, session):
        self.session, self.verbs, self.before_put, self.drop_at = session, [], None, None

    def call(self, method, path, params=None, body=None):
        self.verbs.append(method)
        if method == 'PUT' and self.before_put:
            action, self.before_put = self.before_put, None
            action()
        if method == 'PUT' and self.drop_at == self.verbs.count('PUT'):
            raise importer.Stop('connection', 0, 'cut')  # The connection drops before this save goes through.
        response = self.session.request(method, path, params=params, json=body, headers=importer.HEADERS)
        try:
            return response.status_code, response.json()
        except ValueError:
            return response.status_code, None


class Journals:
    """Class gradebook files for the demo school: columns, marks and the pupils of each class."""

    def __init__(self, state):
        classes = {c.id: c.name for c in state.classes}
        self.people = {'e' + s.id: (classes[s.classId], s.name) for s in state.students}
        self.columns, self.marks, self.subjects, self.ended = {}, [], {}, set()
        self.categories = {FORMATIVE: 'k1', SECTION: 'k2', TERM: 'k3'}

    def stranger(self, klass, name=OUTSIDER):
        self.people['e-stranger'] = (klass, name)

    def column(self, uid, subject=ENGLISH, classes=('10A',), category=FORMATIVE, title='Работа', termin='', maximum='10', weight='20',
               every='0', only=(), edited='2026-09-14 12:00:00'):
        raw = dict(UdalostID=uid, PredmetID=self.subjects.setdefault(subject, f'sub{len(self.subjects)}') if subject else 'vsetky',
                   KategoriaID=self.categories.setdefault(category, f'k{len(self.categories) + 1}') if category else '', p_meno=title, p_termin=termin, timestamp=edited,
                   p_vaha_body=maximum, p_najskor_priemer=every)
        if weight is not None:
            raw['p_vaha'] = weight
        if only:
            raw['onlyStudentids'] = ['e' + p for p in only]
        self.columns[uid] = (raw, set(classes))

    def mark(self, uid, pupil, text, day=D1):
        stamp = day if ':' in day else day + ' 09:00:00'
        self.marks.append(dict(znamkaid=str(100 + len(self.marks)), studentid=pupil if pupil.startswith('e-') else 'e' + pupil,
                               udalostid=uid, data=text, datum=stamp, datum_vytvorenia=stamp, stav='o'))

    def document(self, klass):
        pupils = {sid: dict(p_priezvisko=name.split()[0], p_meno=' '.join(name.split()[1:]), p_koniec_studia='31.08.2026' if sid in self.ended else '')
                  for sid, (c, name) in self.people.items() if c == klass}
        pupils['0'] = dict(p_priezvisko='1 служебная строка', p_meno='', p_koniec_studia='')
        here = {uid: raw for uid, (raw, classes) in self.columns.items() if klass in classes}
        return dict(trieda=dict(Meno=klass, triedaid='t' + klass), studenti=pupils,
                    predmetyVTriede={pid: dict(p_meno=name) for name, pid in self.subjects.items()},
                    kategorie={cid: dict(p_meno=name) for name, cid in self.categories.items()},
                    udalosti=dict(edupage={f'key{uid}': raw for uid, raw in here.items()}),
                    vsetkyZnamky=[m for m in self.marks if m['udalostid'] in here and self.people[m['studentid']][0] == klass])

    def write(self, folder):
        folder.mkdir(exist_ok=True)
        for old in folder.iterdir():
            old.unlink()
        for klass in sorted({c for _, classes in self.columns.values() for c in classes}):
            (folder / f'{klass}_P1.json.gz').write_bytes(gzip.compress(json.dumps(self.document(klass)).encode('utf8')))


class School:
    def __init__(self, client, tmp):
        self.client, self.tmp, self.wire = client, tmp, None

    def journals(self):
        return Journals(api.read('demo'))

    def run(self, journals, *flags, login='admin', rules=None, before_put=None):
        """Run the command line over the importer's own session; returns the report the run wrote."""
        journals.write(self.tmp / 'source')
        (self.tmp / 'account.txt').write_text(f'{login}\n{PASSWORD}\n', encoding='utf8')
        self.wire = Wire(TestClient(api.app, headers=HEADERS))
        self.wire.before_put = before_put
        args = [str(self.tmp / 'source'), '--workspace', 'demo', '--account', str(self.tmp / 'account.txt'), '--report', str(self.tmp / 'report.json'), *flags]
        if rules:
            (self.tmp / 'rules.json').write_text(json.dumps(rules), encoding='utf8')
            args += ['--rules', str(self.tmp / 'rules.json')]
        self.code = importer.main(args, http=self.wire, minute='23:59')
        return json.loads((self.tmp / 'report.json').read_text(encoding='utf8'))

    def key(self, subject, day):
        rows = self.client.get('/api/journal/day', params=dict(workspace='demo', day=day)).json()['lessons']
        return next(r['lessonKey'] for r in rows if r['lesson']['subject'] == subject and not r['archived'])

    def entry(self, subject, day):
        response = self.client.get('/api/journal/entry', params=dict(workspace='demo', lessonKey=self.key(subject, day), day=day))
        assert response.status_code == 200, response.text
        return response.json()

    def works(self, subject, day):
        return {a['id']: a for a in self.entry(subject, day)['document']['content']['assessments']}

    def grade(self, subject, day, work, pupil='s1'):
        records = {r['studentId']: r for r in self.entry(subject, day)['document']['content']['records']}
        return next((g for g in records[pupil]['grades'] if g['assessmentId'] == work), None) if pupil in records else None

    def colleague(self):
        """Another administrator: what it saves is not the importer's work."""
        if not hasattr(self, 'other'):
            create(self.client, 'admin', name='Коллега по журналу', login='colleague')
            self.other = signed('colleague')
        return self.other

    def edit(self, subject, day, change, publish=False, stored=False):
        """A colleague changes the lesson content by hand and saves it. stored: the attendance the editor only showed as "present" is not saved."""
        rows = self.colleague().get('/api/journal/day', params=dict(workspace='demo', day=day)).json()['lessons']
        key = next(r['lessonKey'] for r in rows if r['lesson']['subject'] == subject and not r['archived'])
        entry = self.colleague().get('/api/journal/entry', params=dict(workspace='demo', lessonKey=key, day=day)).json()
        if stored and entry.get('attendanceDefaulted'):
            for record in entry['document']['content']['records']:
                record['attendance'] = 'unmarked'
        change(entry['document']['content'])
        payload = dict(lessonKey=key, day=day, revision=entry['revision'], version=entry['version'], content=entry['document']['content'], publish=publish)
        response = self.colleague().put('/api/journal/entry?workspace=demo', json=payload)
        assert response.status_code == 200, response.text

    def seen_by(self, pupil='s1'):
        """The marks a family sees: {work id: score} of the published rows."""
        if not hasattr(self, 'family'):
            create(self.client, 'student')
            self.family = signed('student')
        rows = self.family.get('/api/journal/report', params=dict(workspace='demo', studentId=pupil, start='2026-08-01', end='2026-10-01')).json()['rows']
        return {g['assessmentId']: g['score'] for row in rows for g in row['record']['grades']}

    def save(self, entry, publish=False):
        payload = dict(lessonKey=entry['lessonKey'], day=entry['document']['lesson']['date'], revision=entry['revision'], version=entry['version'],
                       content=entry['document']['content'], publish=publish)
        response = self.client.put('/api/journal/entry?workspace=demo', json=payload)
        assert response.status_code == 200, response.text
        return response.json()


@pytest.fixture
def school(client, monkeypatch, tmp_path):
    enable(client)
    monkeypatch.setattr(journal, 'today', lambda: TODAY)
    return School(client, tmp_path)


def english_marks(school, uids=('a',), text='8', **column):
    journals = school.journals()
    for uid in uids:
        journals.column(uid, **column)
        journals.mark(uid, 's1', text)
    return journals


def test_mixed_group_column_in_two_class_files_becomes_one_work(school):
    journals = school.journals()
    journals.column('m1', subject=MATH, classes=('10A', '10D'))
    journals.mark('m1', 's1', '8')
    journals.mark('m1', 's3', '6')
    report = school.run(journals, '--apply')
    works = school.works(MATH, D1)
    assert list(works) == ['ep-m1']
    assert (school.grade(MATH, D1, 'ep-m1', 's1')['score'], school.grade(MATH, D1, 'ep-m1', 's3')['score']) == (8, 6)
    assert report['works']['new'] == 1 and report['marks']['written'] == 2


def test_date_rules_and_nearest_lesson_day(school):
    journals = school.journals()
    # A Saturday: the Monday five days back. Before the school year: the first Monday forward. A term later than the first mark: the mark's day.
    journals.column('sat', termin='26.09.2026')
    journals.mark('sat', 's1', '5', '2026-09-26')
    journals.column('early', termin='15.08.2026')
    journals.mark('early', 's1', '6', '2026-08-15')
    journals.column('late', termin='29.09.2026')
    journals.mark('late', 's1', '7', D1)
    journals.column('plain')
    journals.mark('plain', 's1', '9', D0)
    report = school.run(journals, '--apply')
    assert set(school.works(ENGLISH, '2026-09-21')) == {'ep-sat'}
    assert set(school.works(ENGLISH, '2026-08-17')) == {'ep-early'}
    assert set(school.works(ENGLISH, D1)) == {'ep-late'}
    assert set(school.works(ENGLISH, D0)) == {'ep-plain'}
    assert report['moved']['anotherDay'] == 2 and report['moved']['forCapacity'] == 0


def test_the_days_back_are_searched_before_the_days_forward(school):
    journals = school.journals()
    journals.column('sunday', termin='20.09.2026')  # Six days after the Monday of the 14th, one day before the 21st.
    journals.mark('sunday', 's1', '5', '2026-09-20')
    school.run(journals, '--apply')
    assert set(school.works(ENGLISH, D1)) == {'ep-sunday'}
    assert not school.works(ENGLISH, '2026-09-21')


def test_a_column_with_no_lesson_within_a_week_is_reported_and_the_future_is_never_used(school):
    journals = school.journals()
    journals.column('today', termin='30.09.2026')  # Wednesday, today: the Monday before.
    journals.mark('today', 's1', '5', '2026-09-30')
    journals.column('early', termin='11.08.2026')  # Before the school year: the first Monday is six days ahead.
    journals.mark('early', 's1', '5', '2026-08-11')
    journals.column('lonely', termin='01.08.2026')  # Sixteen days before the first Monday.
    journals.mark('lonely', 's1', '5', '2026-08-01')
    journals.column('planned', termin='07.10.2026', only=['s1'])  # Not due yet: the Monday the 5th has not been held.
    report = school.run(journals, '--apply')
    assert set(school.works(ENGLISH, '2026-09-28')) == {'ep-today'} and set(school.works(ENGLISH, '2026-08-17')) == {'ep-early'}
    assert not school.works(ENGLISH, '2026-10-05')
    assert report['columns']['unresolved']['noLesson'] == [dict(subject=ENGLISH, classes=['10A'], columns=2)]


def test_eleven_columns_on_one_day_lose_nothing(school):
    uids = [f'c{n:02d}' for n in range(11)]
    report = school.run(english_marks(school, uids), '--apply')
    first, second = school.works(ENGLISH, D1), school.works(ENGLISH, D0)
    assert len(first) == 10 and len(second) == 1
    assert {f'ep-{u}' for u in uids} == set(first) | set(second)
    assert report['moved'] == dict(anotherDay=1, forCapacity=1)
    assert all(school.grade(ENGLISH, day, work)['score'] == 8 for day, works in [(D1, first), (D0, second)] for work in works)


def test_a_full_lesson_passes_works_to_another_lesson_of_the_same_day(school):
    state = api.read('demo')
    state.placements.append(Placement(id='eng2', workloadId='w0', day=0, period=3, roomId='r0'))
    api.save('demo', state)
    uids = [f'c{n:02d}' for n in range(11)]
    report = school.run(english_marks(school, uids), '--apply')
    rows = school.client.get('/api/journal/day', params=dict(workspace='demo', day=D1)).json()['lessons']
    counts = sorted(len(school.client.get('/api/journal/entry', params=dict(workspace='demo', lessonKey=r['lessonKey'], day=D1)).json()['document']['content']['assessments'])
                    for r in rows if r['lesson']['subject'] == ENGLISH)
    assert counts == [1, 10]
    assert report['moved'] == dict(anotherDay=0, forCapacity=1) and not school.works(ENGLISH, D0)


def test_works_already_in_the_lesson_take_its_places(school):
    entry = school.entry(ENGLISH, D1)
    entry['document']['content']['assessments'] = [dict(id=f'hand{n}', name='Своя', kind='points', maxScore=5, weight=1, category='fo') for n in range(9)]
    school.save(entry)
    school.run(english_marks(school, ['a', 'b', 'c']), '--apply')
    assert len(school.works(ENGLISH, D1)) == 10 and len(school.works(ENGLISH, D0)) == 2


def test_running_again_changes_nothing_and_writes_nothing(school):
    journals = english_marks(school, ['a', 'b'])
    journals.column('m', subject=MATH, classes=('10A', '10D'))
    journals.mark('m', 's3', '4')
    school.run(journals, '--apply')
    before = {s: school.entry(s, D1)['version'] for s in (ENGLISH, MATH)}
    report = school.run(journals, '--apply')
    assert report['lessons']['toSave'] == 0 and report['lessons']['saved'] == 0
    assert report['works'] == dict(new=0, updated=0, unchanged=3, companion=0) and report['marks']['written'] == 0
    assert 'PUT' not in school.wire.verbs
    assert {s: school.entry(s, D1)['version'] for s in (ENGLISH, MATH)} == before


def test_a_changed_mark_updates_the_same_work_where_it_is(school):
    school.run(english_marks(school, ['a']), '--apply')
    version = school.entry(ENGLISH, D1)['version']
    journals = english_marks(school, ['a'], text='9', termin='08.09.2026')  # EduPage moved the work to the 8th, a Tuesday: the work stays.
    report = school.run(journals, '--apply')
    assert report['works']['updated'] == 1 and report['works']['new'] == 0
    assert school.grade(ENGLISH, D1, 'ep-a')['score'] == 9
    assert list(school.works(ENGLISH, D1)) == ['ep-a'] and not school.works(ENGLISH, D0)
    assert school.entry(ENGLISH, D1)['version'] == version + 1
    assert report['lessons']['saved'] == 1


def test_a_changed_column_title_and_weight_update_in_place(school):
    school.run(english_marks(school, ['a'], title='Первое', weight='20'), '--apply')
    report = school.run(english_marks(school, ['a'], title='Второе', weight='40'), '--apply')
    work = school.works(ENGLISH, D1)['ep-a']
    assert (work['name'], work['weight']) == ('Второе', 2) and report['works']['updated'] == 1 and report['works']['new'] == 0


def test_hand_made_work_attendance_and_texts_survive_and_stay_unpublished(school):
    entry = school.entry(ENGLISH, D1)
    content = entry['document']['content']
    content['topic'], content['homework'] = 'Тема руки', 'Задание руки'
    content['assessments'] = [dict(id='hand', name='Своя', kind='points', maxScore=5, weight=1, category='fo')]
    content['records'][0].update(attendance='late', lateMinutes=7, comment='Личное', grades=[dict(assessmentId='hand', score=4, label='', comment='Своя оценка')])
    content['records'][1].update(attendance='absent')
    school.save(entry)
    report = school.run(english_marks(school, ['a']), '--apply')
    after = school.entry(ENGLISH, D1)['document']['content']
    assert (after['topic'], after['homework']) == ('Тема руки', 'Задание руки')
    assert [a['id'] for a in after['assessments']] == ['hand', 'ep-a']
    first, second = after['records'][0], after['records'][1]
    assert (first['attendance'], first['lateMinutes'], first['comment']) == ('late', 7, 'Личное') and second['attendance'] == 'absent'
    assert [(g['assessmentId'], g['score']) for g in first['grades']] == [('hand', 4), ('ep-a', 8)]
    # The draft has things the teacher has not published: the marks wait in the draft instead of publishing them with it.
    assert report['lessons']['keptAsDraft'] == 1 and after['records'][0]['grades'][0]['comment'] == 'Своя оценка'
    create(school.client, 'student')
    assert signed('student').get('/api/journal/report', params=dict(workspace='demo', studentId='s1', start='2026-08-01', end='2026-10-01')).json()['rows'] == []


def test_a_lesson_published_by_the_teacher_gets_the_marks_published(school):
    entry = school.entry(ENGLISH, D1)
    entry['document']['content']['topic'] = 'Опубликованная тема'
    school.save(entry, publish=True)
    school.run(english_marks(school, ['a']), '--apply')
    create(school.client, 'student')
    rows = signed('student').get('/api/journal/report', params=dict(workspace='demo', studentId='s1', start='2026-08-01', end='2026-10-01')).json()['rows']
    assert len(rows) == 1 and rows[0]['content']['topic'] == 'Опубликованная тема'
    assert [(a['id'], g['score']) for a in rows[0]['content']['assessments'] for g in rows[0]['record']['grades'] if g['assessmentId'] == a['id']] == [('ep-a', 8)]


def test_marks_reach_the_pupil_and_attendance_is_left_alone(school):
    create(school.client, 'student')
    pupil = signed('student')
    school.run(english_marks(school, ['a'], text='9 (молодец)'), '--apply')
    rows = pupil.get('/api/journal/report', params=dict(workspace='demo', studentId='s1', start='2026-08-01', end='2026-10-01')).json()['rows']
    assert len(rows) == 1
    record = rows[0]['record']
    assert record['attendance'] == 'unmarked' and record['lateMinutes'] == 0
    assert (record['grades'][0]['score'], record['grades'][0]['comment']) == (9, 'молодец')


def test_draft_mode_saves_marks_the_pupil_does_not_see(school):
    create(school.client, 'student')
    school.run(english_marks(school, ['a']), '--apply', '--draft')
    assert school.grade(ENGLISH, D1, 'ep-a')['score'] == 8
    assert signed('student').get('/api/journal/report', params=dict(workspace='demo', studentId='s1', start='2026-08-01', end='2026-10-01')).json()['rows'] == []
    # Running again with publication publishes the same marks without changing them.
    report = school.run(english_marks(school, ['a']), '--apply')
    assert report['works']['unchanged'] == 1 and report['lessons']['saved'] == 1
    assert len(signed('student').get('/api/journal/report', params=dict(workspace='demo', studentId='s1', start='2026-08-01', end='2026-10-01')).json()['rows']) == 1


def test_dry_run_writes_nothing(school):
    report = school.run(english_marks(school, ['a', 'b']))
    assert report['mode'] == 'dry-run' and report['works']['new'] == 2 and report['lessons']['toSave'] == 1 and report['lessons']['saved'] == 0
    assert 'PUT' not in school.wire.verbs
    assert school.entry(ENGLISH, D1)['version'] == 0 and not school.works(ENGLISH, D1)


def test_mark_texts_become_scores_and_comments(school):
    journals = school.journals()
    texts = dict(plain='9', comma='9,5', remark='9 (хорошо)', only='(молодец)', word='зачёт', plus='+', hash='9#', dot='· 9', over='12', negative='-3')
    for uid, text in texts.items():
        journals.column(uid)
        journals.mark(uid, 's1', text)
    report = school.run(journals, '--apply')
    got = {uid: school.grade(ENGLISH, D1, 'ep-' + uid) for uid in texts}
    assert [(g['score'], g['comment']) for g in (got['plain'], got['comma'])] == [(9, ''), (9.5, '')]
    assert (got['remark']['score'], got['remark']['comment']) == (9, 'хорошо')
    assert (got['only']['score'], got['only']['comment']) == (None, 'молодец')
    assert [(got[u]['score'], got[u]['comment']) for u in ('word', 'plus', 'dot', 'negative')] == [(None, 'зачёт'), (None, '+'), (None, '· 9'), (None, '-3')]
    assert (got['hash']['score'], got['hash']['comment']) == (9, '#')
    assert got['over']['score'] == 10 and '12' in got['over']['comment']
    assert report['marks']['scoresCut'] == 1 and report['marks']['textOnly'] == 5


def test_a_column_without_a_maximum_keeps_the_mark_as_a_label(school):
    journals = school.journals()
    journals.column('word', maximum='')
    journals.mark('word', 's1', 'зачёт')
    journals.column('long', maximum='0')
    journals.mark('long', 's1', 'а' * 45)
    school.run(journals, '--apply')
    works = school.works(ENGLISH, D1)
    assert works['ep-word']['kind'] == works['ep-long']['kind'] == 'label'
    assert school.grade(ENGLISH, D1, 'ep-word')['label'] == 'зачёт'
    long = school.grade(ENGLISH, D1, 'ep-long')
    assert len(long['label']) <= 30 and long['label'] + long['comment'] == 'а' * 45


def test_every_mark_counts_puts_later_attempts_in_companion_works(school):
    journals = school.journals()
    journals.column('t', title='Пересдача', category=SECTION, weight='40', maximum='20', every='0')
    journals.mark('t', 's1', '5', '2026-09-14 09:00:00')
    journals.mark('t', 's1', '12', '2026-09-14 10:00:00')
    journals.mark('t', 's1', '15', '2026-09-14 11:00:00')
    report = school.run(journals, '--apply')
    works = school.works(ENGLISH, D1)
    assert len(works) == 3 and 'ep-t' in works and report['works']['companion'] == 2
    assert sorted(school.grade(ENGLISH, D1, w)['score'] for w in works) == [5, 12, 15] and school.grade(ENGLISH, D1, 'ep-t')['score'] == 5
    assert all((works[w]['category'], works[w]['maxScore'], works[w]['weight']) == ('sor', 20, 2) for w in works)
    assert len({a['name'] for a in works.values()}) == 3 and all(a['name'].startswith('Пересдача') for a in works.values())
    assert school.run(journals, '--apply')['lessons']['toSave'] == 0


def test_only_the_best_attempt_counts_when_edupage_says_so(school):
    journals = school.journals()
    journals.column('t', every='1')
    journals.mark('t', 's1', '5', '2026-09-14 09:00:00')
    journals.mark('t', 's1', '9', '2026-09-14 10:00:00')
    journals.mark('t', 's1', '7', '2026-09-14 11:00:00')
    report = school.run(journals, '--apply')
    assert list(school.works(ENGLISH, D1)) == ['ep-t'] and report['works']['companion'] == 0
    grade = school.grade(ENGLISH, D1, 'ep-t')
    assert grade['score'] == 9 and '5' in grade['comment'] and '7' in grade['comment']


def test_categories_follow_the_school_catalog_and_weights_follow_edupage(school):
    journals = school.journals()
    for uid, category, weight in [('fo', FORMATIVE, '20'), ('sor', SECTION, '5'), ('soch', TERM, '40'), ('zero', FORMATIVE, '0'), ('none', FORMATIVE, None)]:
        journals.column(uid, category=category, weight=weight)
        journals.mark(uid, 's1', '5')
    journals.column('free', category='')
    journals.mark('free', 's1', '5')
    school.run(journals, '--apply')
    works = school.works(ENGLISH, D1)
    assert [(works['ep-' + u]['category'], works['ep-' + u]['weight']) for u in ('fo', 'sor', 'soch', 'zero', 'none')] == [('fo', 1), ('sor', .25), ('soch', 2), ('fo', 0), ('fo', 1)]
    # Without a category in EduPage the work counts nowhere there: the school's default category with no weight.
    assert (works['ep-free']['category'], works['ep-free']['weight']) == ('fo', 0)


def test_a_renamed_catalog_still_takes_the_calendar_kinds(school):
    catalog = school.client.get('/api/journal/categories', params=dict(workspace='demo')).json()
    for c in catalog['categories']:
        c.update(name={'sor': 'КР', 'soch': 'ИТОГ'}.get(c['id'], c['name']), title={'sor': 'Контрольная работа', 'soch': 'Итоговая за период'}.get(c['id'], c['title']))
    body = dict(version=catalog['version'], reason='Переименование', categories=catalog['categories'], defaultCategory=catalog['defaultCategory'], policy=catalog['policy'], rules=catalog['rules'])
    assert school.client.put('/api/journal/categories?workspace=demo', json=body).status_code == 200
    journals = school.journals()
    for uid, category in [('a', SECTION), ('b', TERM), ('c', 'Другая категория')]:
        journals.column(uid, category=category)
        journals.mark(uid, 's1', '5')
    report = school.run(journals, '--apply', rules=dict(categories={'Другая категория': 'ИТОГ'}))
    works = school.works(ENGLISH, D1)
    assert [works['ep-' + u]['category'] for u in 'abc'] == ['sor', 'soch', 'soch'] and not report['categoriesUnmapped']


def test_unknown_category_is_counted_and_the_default_is_used(school):
    journals = school.journals()
    journals.column('x', category='Кружок')
    journals.mark('x', 's1', '5')
    report = school.run(journals, '--apply')
    assert report['categoriesUnmapped'] == {'Кружок': 1} and school.works(ENGLISH, D1)['ep-x']['category'] == 'fo'


def test_titles_are_cut_or_taken_from_the_category(school):
    journals = school.journals()
    journals.column('long', title='Очень длинное название ' * 8)
    journals.mark('long', 's1', '5')
    journals.column('empty', title='', category=SECTION)
    journals.mark('empty', 's1', '5')
    report = school.run(journals, '--apply')
    works = school.works(ENGLISH, D1)
    assert len(works['ep-long']['name']) == 100 and works['ep-long']['name'].endswith('…') and report['titlesShortened'] == 1
    assert works['ep-empty']['name'] == SECTION


def test_pupils_without_a_match_and_columns_that_cannot_be_placed_are_counted(school):
    state = api.read('demo')
    twin = next(s for s in state.students if s.id == 's2').model_copy(update=dict(id='s2-twin'))
    state.students.append(twin)
    api.save('demo', state)
    journals = school.journals()
    journals.stranger('10A')
    journals.column('known')
    journals.mark('known', 's1', '5')
    journals.mark('known', '-stranger', '6')
    journals.mark('known', 's2', '7')  # Two Atlas pupils of 10A have this name.
    journals.column('chemistry', subject='Химия')
    journals.mark('chemistry', 's1', '5')
    journals.column('nobody')
    journals.column('general', subject=None)
    journals.column('split', classes=('10A', '10D'))
    journals.mark('split', 's1', '5')
    journals.mark('split', 's3', '5')  # No English group holds a pupil of 10A and one of 10D.
    report = school.run(journals, '--apply')
    assert set(school.works(ENGLISH, D1)) == {'ep-known'}
    assert school.grade(ENGLISH, D1, 'ep-known', 's1')['score'] == 5 and school.grade(ENGLISH, D1, 'ep-known', 's2') is None
    assert report['pupilsWithoutMatch'] == {'10A': dict(none=1, ambiguous=1)}
    assert report['columns']['unresolved']['noSubject'] == [dict(subject='Химия', classes=['10A'], columns=1)]
    assert report['columns']['unresolved']['noGroup'] == [dict(subject=ENGLISH, classes=['10A', '10D'], columns=1)]
    assert (report['columns']['withoutPupils'], report['columns']['notSubject']) == (1, 1)
    assert report['marks']['leftOut']['noPupilMatch'] == 2


def test_the_report_accounts_for_every_mark_read(school):
    journals = school.journals()
    journals.stranger('10A')
    journals.column('known')
    journals.mark('known', 's1', '5')
    journals.mark('known', '-stranger', '6')
    journals.column('chemistry', subject='Химия')
    journals.mark('chemistry', 's1', '5')
    journals.column('split', classes=('10A', '10D'))
    journals.mark('split', 's1', '5')
    journals.mark('split', 's3', '5')
    journals.column('best', every='1')
    journals.mark('best', 's1', '4')
    journals.mark('best', 's1', '9', day='2026-09-21')
    journals.column('each', every='0')
    journals.mark('each', 's1', '4')
    journals.mark('each', 's1', '9', day='2026-09-21')

    def whole(report):
        marks = report['marks']
        return marks['inSource'] == marks['inWorks'] + marks['otherAttempts'] + sum(marks['leftOut'].values())

    first = school.run(journals, '--apply')
    assert first['marks']['inSource'] == 9 and whole(first)
    assert first['marks']['inWorks'] == first['marks']['written'] == 4 and first['marks']['otherAttempts'] == 1
    assert first['marks']['leftOut']['columnNotPlanned'] == 3 and first['marks']['leftOut']['noPupilMatch'] == 1
    again = school.run(journals, '--apply')  # Nothing is written the second time, and every mark still has its fate.
    assert again['marks']['written'] == 0 and again['marks']['inWorks'] == 4 and whole(again)


def test_a_group_with_most_of_the_pupils_takes_the_column_and_the_others_are_counted(school):
    state = api.read('demo')
    for n in range(4):
        state.students.append(next(s for s in state.students if s.id == 's1').model_copy(update=dict(id=f'x{n}', name=f'{SURNAME_PAIRS[n][0]} {MALE[n]}')))
    api.save('demo', state)
    journals = school.journals()
    journals.people.update({f'ex{n}': ('10A', f'{SURNAME_PAIRS[n][0]} {MALE[n]}') for n in range(4)})
    journals.column('big', classes=('10A', '10D'))
    for pupil in ['s1', 's2', 'x0', 'x1', 'x2', 's3']:
        journals.mark('big', pupil, '6')
    report = school.run(journals, '--apply')
    assert school.grade(ENGLISH, D1, 'ep-big', 's1')['score'] == 6 and school.grade(ENGLISH, D1, 'ep-big', 'x2')['score'] == 6
    assert report['marks']['leftOut']['outsideGroup'] == 1 and report['marks']['written'] == 5


def test_rules_file_gives_subject_aliases_skips_and_categories(school):
    journals = school.journals()
    journals.column('alg', subject='Алгебра', classes=('10A', '10D'), category='Домашняя работа')
    journals.mark('alg', 's1', '5')
    journals.column('pe', subject='Физкультура')
    journals.mark('pe', 's1', '5')
    report = school.run(journals, '--apply', rules=dict(subjects={'Алгебра': 'Математика'}, skipSubjects=['Физкультура'], categories={'Домашняя работа': 'sor'}))
    assert school.works(MATH, D1)['ep-alg']['category'] == 'sor' and report['columns']['skippedByRules'] == 1
    assert 'noSubject' not in report['columns']['unresolved'] and not school.works(ENGLISH, D1)


def test_columns_gone_from_edupage_are_reported_and_kept(school):
    school.run(english_marks(school, ['a', 'b']), '--apply')
    report = school.run(english_marks(school, ['a']), '--apply')
    assert report['goneFromEdupage'] == 1 and set(school.works(ENGLISH, D1)) == {'ep-a', 'ep-b'}


def test_a_colleague_saving_in_between_is_read_again(school):
    def colleague():
        entry = school.entry(ENGLISH, D1)
        entry['document']['content']['topic'] = 'Тема коллеги'
        school.save(entry)

    # The importer has read the lesson when the colleague saves; its PUT meets a newer version, reads again and tries once more.
    report = school.run(english_marks(school, ['a']), '--apply', before_put=colleague)
    assert report['lessons']['saved'] == 1 and not report['lessons']['refused']
    content = school.entry(ENGLISH, D1)['document']['content']
    assert content['topic'] == 'Тема коллеги' and [a['id'] for a in content['assessments']] == ['ep-a']


def test_a_closed_period_is_refused_for_that_lesson_and_the_run_goes_on(school, monkeypatch):
    stamp = datetime(2026, 9, 29, 10, tzinfo=timezone.utc)
    monkeypatch.setattr(journal_periods, 'now', lambda: stamp)
    monkeypatch.setattr('backend.journal_coverage.now', lambda: stamp)
    period = school.client.put(f'/api/journal/periods/{uuid4().hex}?workspace=demo', json=dict(name='Первая четверть', start='2026-08-17', end='2026-08-17', kind='quarter', version=0, reason='Создание периода')).json()
    preview = school.client.post(f'/api/journal/periods/{period["id"]}/preview-close?workspace=demo', json=dict(version=period['version'])).json()
    closed = school.client.post(f'/api/journal/periods/{period["id"]}/close?workspace=demo', json=dict(version=period['version'], reason='Закрытие', previewId=preview['previewId'], acknowledgeIssues=True))
    assert closed.status_code == 200, closed.text
    journals = school.journals()
    for uid, day in [('shut', '2026-08-17'), ('open', D1)]:
        journals.column(uid)
        journals.mark(uid, 's1', '5', day)
    report = school.run(journals, '--apply')
    assert school.works(ENGLISH, D1) and not school.works(ENGLISH, '2026-08-17')
    assert report['lessons']['saved'] == 1 and len(report['lessons']['refused']) == 1
    refused = report['lessons']['refused'][0]
    assert (refused['day'], refused['status']) == ('2026-08-17', 403) and refused['reason']


def test_an_account_that_cannot_read_the_school_is_refused_cleanly(school):
    create(school.client, 'teacher')
    report = school.run(english_marks(school, ['a']), '--apply', login='teacher')
    assert school.code != 0 and report['aborted']['status'] == 403
    assert 'PUT' not in school.wire.verbs and not school.works(ENGLISH, D1)


def test_wrong_password_is_reported_without_crashing(school):
    journals = english_marks(school, ['a'])
    journals.write(school.tmp / 'source')
    (school.tmp / 'account.txt').write_text('admin\nnot the password\n', encoding='utf8')
    wire = Wire(TestClient(api.app, headers=HEADERS))
    code = importer.main([str(school.tmp / 'source'), '--workspace', 'demo', '--account', str(school.tmp / 'account.txt'), '--report', str(school.tmp / 'report.json')], http=wire)
    assert code != 0 and json.loads((school.tmp / 'report.json').read_text(encoding='utf8'))['aborted']['status'] == 401


def test_the_session_is_closed_at_the_end(school):
    school.run(english_marks(school, ['a']))
    assert school.wire.session.get('/api/state', params=dict(workspace='demo')).status_code == 401


def test_the_report_holds_no_names_titles_or_marks(school, capsys):
    journals = school.journals()
    journals.stranger('10A')
    journals.column('x', title='особоеназвание', subject='Химия')
    journals.mark('x', 's1', 'оченьособыйкомментарий')
    journals.column('y', title='особоеназвание')
    journals.mark('y', 's1', '7,77 (оченьособыйкомментарий)')
    journals.mark('y', '-stranger', '3,33')
    journals.mark('y', 's2', '12,34')
    school.run(journals, '--apply')
    text = capsys.readouterr().out + (school.tmp / 'report.json').read_text(encoding='utf8')
    names = [name for _, name in journals.people.values()] + [part for _, name in journals.people.values() for part in name.split() if len(part) > 3 and part != 'Ученик']
    for secret in [*names, 'особоеназвание', 'оченьособыйкомментарий', '7,77', '3,33', '12,34']:
        assert secret not in text
    assert json.loads((school.tmp / 'report.json').read_text(encoding='utf8'))['marks']['written'] == 2


# ----------------------------------------------------------------------------- what a repeated run must leave alone

def grades_of(school, prefix, day=D1, pupil='s1'):
    """The scores of a pupil on every work of one EduPage column in a lesson: {work id: score}."""
    found = {w: school.grade(ENGLISH, day, w, pupil) for w in school.works(ENGLISH, day) if w.startswith(prefix)}
    return {w: g['score'] for w, g in found.items() if g}


def test_two_edupage_records_of_one_pupil_import_neither_mark(school):
    journals = english_marks(school, ['a'], text='7')
    journals.people['e-dup'] = journals.people['es1']
    journals.mark('a', '-dup', '3')
    report = school.run(journals, '--apply')
    assert not school.works(ENGLISH, D1)
    assert report['pupilsWithoutMatch'] == {'10A': dict(none=0, ambiguous=2)}


def test_a_record_with_an_end_of_study_gives_way_to_the_live_one(school):
    journals = english_marks(school, ['a'], text='7')
    journals.people['e-dup'] = journals.people['es1']
    journals.ended.add('e-dup')
    journals.mark('a', '-dup', '3')
    report = school.run(journals, '--apply')
    assert school.grade(ENGLISH, D1, 'ep-a', 's1')['score'] == 7
    assert report['pupilsWithoutMatch'] == {'10A': dict(none=0, ambiguous=1)}


def test_a_deleted_attempt_never_makes_atlas_hold_a_mark_twice(school):
    journals = school.journals()
    journals.column('t', maximum='20', every='0')
    for text, hour in [('5', '09'), ('12', '10'), ('15', '11')]:
        journals.mark('t', 's1', text, f'2026-09-14 {hour}:00:00')
    school.run(journals, '--apply')
    journals.marks[0]['stav'] = 'x'  # EduPage dropped the first attempt.
    report = school.run(journals, '--apply')
    assert sorted(grades_of(school, 'ep-t').values()) == [5, 12, 15]
    assert report['marks']['attemptsKept'] == 1 and 'PUT' not in school.wire.verbs


def test_a_deleted_attempt_of_two_does_not_double_the_remaining_mark(school):
    journals = school.journals()
    journals.column('t', every='0')
    journals.mark('t', 's1', '3', '2026-09-14 09:00:00')
    journals.mark('t', 's1', '8', '2026-09-14 10:00:00')
    school.run(journals, '--apply')
    journals.marks[0]['stav'] = 'x'
    school.run(journals, '--apply')
    assert sorted(grades_of(school, 'ep-t').values()) == [3, 8]


def test_an_attempt_work_never_takes_the_id_of_another_column(school):
    journals = school.journals()
    journals.column('x', title='Первая', every='0')
    journals.mark('x', 's1', '3', '2026-09-14 09:00:00')
    journals.mark('x', 's1', '4', '2026-09-14 10:00:00')
    journals.column('x-2', title='Вторая')
    journals.mark('x-2', 's1', '9')
    journals.mark('x-2', 's2', '8')
    school.run(journals, '--apply')
    works = school.works(ENGLISH, D1)
    assert (school.grade(ENGLISH, D1, 'ep-x-2', 's1')['score'], school.grade(ENGLISH, D1, 'ep-x-2', 's2')['score']) == (9, 8)
    second = [w for w, a in works.items() if a['name'].startswith('Первая') and a['name'].endswith('попытка 2')]
    assert len(second) == 1 and school.grade(ENGLISH, D1, second[0], 's1')['score'] == 4
    again = school.run(journals, '--apply')
    assert again['lessons']['toSave'] == 0 and again['works']['new'] == 0


def test_a_lowered_maximum_and_a_dropped_mark_do_not_block_the_lesson(school):
    journals = school.journals()
    journals.column('a', maximum='20')
    journals.mark('a', 's1', '15')
    journals.mark('a', 's2', '18')
    journals.column('b')
    journals.mark('b', 's1', '5')
    school.run(journals, '--apply')
    journals = school.journals()
    journals.column('a', maximum='10')
    journals.mark('a', 's1', '15')
    journals.column('b')
    journals.mark('b', 's1', '6')
    report = school.run(journals, '--apply')
    assert not report['lessons']['refused']
    assert school.grade(ENGLISH, D1, 'ep-b')['score'] == 6
    kept = school.grade(ENGLISH, D1, 'ep-a', 's2')
    assert kept['score'] == 10 and '18' in kept['comment']
    assert school.works(ENGLISH, D1)['ep-a']['maxScore'] == 10


def test_a_negative_weight_becomes_no_weight(school):
    journals = school.journals()
    journals.column('h', weight='-20')
    journals.mark('h', 's1', '5')
    school.run(journals, '--apply')
    assert school.works(ENGLISH, D1)['ep-h']['weight'] == 0


def test_a_teachers_correction_survives_a_run_on_an_unchanged_source(school):
    journals = english_marks(school, ['a'])
    school.run(journals, '--apply')
    school.edit(ENGLISH, D1, lambda c: next(r for r in c['records'] if r['studentId'] == 's1')['grades'][0].update(score=3), publish=True)
    version = school.entry(ENGLISH, D1)['version']
    report = school.run(journals, '--apply')
    assert school.grade(ENGLISH, D1, 'ep-a')['score'] == 3 and school.seen_by() == {'ep-a': 3}
    assert 'PUT' not in school.wire.verbs and school.entry(ENGLISH, D1)['version'] == version
    assert report['keptAsInAtlas']['editedMarks'] == 1


def test_a_changed_mark_still_arrives_when_a_colleague_touched_something_else(school):
    school.run(english_marks(school, ['a']), '--apply')
    school.edit(ENGLISH, D1, lambda c: c.update(topic='Тема коллеги'))
    report = school.run(english_marks(school, ['a'], text='9'), '--apply')
    assert school.grade(ENGLISH, D1, 'ep-a')['score'] == 9 and report['keptAsInAtlas']['editedMarks'] == 0
    assert school.entry(ENGLISH, D1)['document']['content']['topic'] == 'Тема коллеги'


def test_a_teachers_correction_wins_when_edupage_changed_too(school):
    school.run(english_marks(school, ['a']), '--apply')
    school.edit(ENGLISH, D1, lambda c: next(r for r in c['records'] if r['studentId'] == 's1')['grades'][0].update(score=3))
    report = school.run(english_marks(school, ['a'], text='9'), '--apply')
    assert school.grade(ENGLISH, D1, 'ep-a')['score'] == 3 and report['keptAsInAtlas']['editedMarks'] == 1


def test_a_teachers_edit_of_the_work_itself_is_kept(school):
    journals = english_marks(school, ['a'])
    school.run(journals, '--apply')
    school.edit(ENGLISH, D1, lambda c: c['assessments'][0].update(weight=3, name='Своё название'))
    report = school.run(journals, '--apply')
    work = school.works(ENGLISH, D1)['ep-a']
    assert (work['weight'], work['name']) == (3, 'Своё название') and 'PUT' not in school.wire.verbs
    assert report['keptAsInAtlas']['editedWorks'] == 1


def test_a_mark_the_teacher_removed_does_not_come_back(school):
    journals = english_marks(school, ['a'])
    journals.mark('a', 's2', '6')
    school.run(journals, '--apply')
    school.edit(ENGLISH, D1, lambda c: next(r for r in c['records'] if r['studentId'] == 's2').update(grades=[]))
    report = school.run(journals, '--apply')
    assert school.grade(ENGLISH, D1, 'ep-a', 's2') is None and 'PUT' not in school.wire.verbs
    assert report['keptAsInAtlas']['editedMarks'] == 1


def test_a_work_the_teacher_deleted_is_not_created_again(school):
    journals = english_marks(school, ['a', 'b'])
    school.run(journals, '--apply')

    def delete(content):
        content['assessments'] = [a for a in content['assessments'] if a['id'] != 'ep-a']
        for record in content['records']:
            record['grades'] = [g for g in record['grades'] if g['assessmentId'] != 'ep-a']

    school.edit(ENGLISH, D1, delete, publish=True)
    report = school.run(journals, '--apply')
    assert list(school.works(ENGLISH, D1)) == ['ep-b'] and 'PUT' not in school.wire.verbs
    assert report['keptAsInAtlas']['removedWorks'] == 1 and report['works']['new'] == 0


def test_a_new_column_still_lands_in_a_lesson_with_history(school):
    school.run(english_marks(school, ['a']), '--apply')
    school.edit(ENGLISH, D1, lambda c: c.update(topic='Тема'))
    school.run(english_marks(school, ['a', 'b']), '--apply')
    assert set(school.works(ENGLISH, D1)) == {'ep-a', 'ep-b'}


def test_a_large_publication_waits_for_an_explicit_yes(school, monkeypatch):
    monkeypatch.setattr(importer, 'PUBLISH_LIMIT', 1)
    journals = school.journals()
    for uid, day in [('a', D1), ('b', D0)]:
        journals.column(uid)
        journals.mark(uid, 's1', '5', day)
    report = school.run(journals, '--apply')
    assert report['aborted']['step'] == 'publish' and school.code != 0
    assert 'PUT' not in school.wire.verbs and report['lessons']['toPublish'] == 2 and not school.works(ENGLISH, D1)
    drafts = school.run(journals, '--apply', '--draft')
    assert drafts['lessons']['saved'] == 2 and school.seen_by() == {}
    again = school.run(journals, '--apply', '--publish')
    assert again['lessons']['saved'] == 2 and set(school.seen_by()) == {'ep-a', 'ep-b'}


def test_marks_saved_as_a_draft_can_be_published_later_in_a_lesson_the_teacher_published(school):
    entry = school.entry(ENGLISH, D1)
    entry['document']['content']['topic'] = 'Опубликованная тема'
    school.save(entry, publish=True)
    journals = english_marks(school, ['a'])
    school.run(journals, '--apply', '--draft')
    assert school.seen_by() == {}
    report = school.run(journals, '--apply')
    assert school.seen_by() == {'ep-a': 8} and report['lessons']['saved'] == 1 and report['lessons']['keptAsDraft'] == 0


def test_a_draft_with_the_teachers_own_changes_is_not_published_with_the_marks(school):
    entry = school.entry(ENGLISH, D1)
    entry['document']['content']['topic'] = 'Опубликованная тема'
    school.save(entry, publish=True)
    journals = english_marks(school, ['a'])
    school.run(journals, '--apply', '--draft')
    school.edit(ENGLISH, D1, lambda c: c.update(topic='Тема ещё не для семей'))
    report = school.run(journals, '--apply')
    assert school.seen_by() == {} and report['lessons']['keptAsDraft'] == 1


def test_a_run_cut_short_still_reports_what_it_saved(school):
    journals = school.journals()
    for uid, day in [('a', D1), ('b', D0)]:
        journals.column(uid)
        journals.mark(uid, 's1', '5', day)
    journals.write(school.tmp / 'source')
    (school.tmp / 'account.txt').write_text(f'admin\n{PASSWORD}\n', encoding='utf8')
    wire = Wire(TestClient(api.app, headers=HEADERS))
    wire.drop_at = 2
    importer.main([str(school.tmp / 'source'), '--workspace', 'demo', '--account', str(school.tmp / 'account.txt'), '--report', str(school.tmp / 'cut.json'), '--apply'], http=wire, minute='23:59')
    report = json.loads((school.tmp / 'cut.json').read_text(encoding='utf8'))
    assert report['aborted']['step'] == 'connection' and report['lessons']['saved'] == 1
    assert school.run(journals, '--apply')['lessons']['saved'] == 1 and school.run(journals, '--apply')['lessons']['toSave'] == 0
    assert set(school.works(ENGLISH, D1)) == {'ep-a'} and set(school.works(ENGLISH, D0)) == {'ep-b'}



# ----------------------------------------------------------------------------- a teacher's unpublished edit of an imported work or mark

def own_grade(content, pupil='s1', work='ep-a'):
    record = next(r for r in content['records'] if r['studentId'] == pupil)
    return next(g for g in record['grades'] if g['assessmentId'] == work)


def drop_mark(content):
    record = next(r for r in content['records'] if r['studentId'] == 's1')
    record['grades'] = [g for g in record['grades'] if g['assessmentId'] != 'ep-a']


def drop_work(content):
    content['assessments'] = [a for a in content['assessments'] if a['id'] != 'ep-a']
    for record in content['records']:
        record['grades'] = [g for g in record['grades'] if g['assessmentId'] != 'ep-a']


@pytest.mark.parametrize('change', [
    lambda c: own_grade(c).update(score=3),
    lambda c: own_grade(c).update(comment='Пересдача'),
    drop_mark,
    lambda c: next(a for a in c['assessments'] if a['id'] == 'ep-a').update(weight=3),
    drop_work,
], ids=['score', 'comment', 'mark deleted', 'work edited', 'work deleted'])
def test_a_teachers_unpublished_edit_of_an_imported_work_is_never_published_by_a_rerun(school, change):
    journals = english_marks(school, ['a', 'b'])
    school.run(journals, '--apply')
    school.edit(ENGLISH, D1, change, stored=True)
    before = school.entry(ENGLISH, D1)
    seen = school.seen_by()
    report = school.run(journals, '--apply')
    after = school.entry(ENGLISH, D1)
    assert (after['version'], after['publishedVersion']) == (before['version'], before['publishedVersion']) and before['version'] > before['publishedVersion']
    assert after['document']['content'] == before['document']['content']
    assert 'PUT' not in school.wire.verbs and school.seen_by() == seen == {'ep-a': 8, 'ep-b': 8}
    assert report['lessons']['toPublish'] == 0 and report['lessons']['saved'] == 0 and report['lessons']['keptAsDraft'] == 0


def test_the_importers_own_save_waits_in_the_draft_beside_a_teachers_unpublished_edit(school):
    journals = english_marks(school, ['a'])
    journals.mark('a', 's2', '8')
    school.run(journals, '--apply')
    school.edit(ENGLISH, D1, lambda c: own_grade(c).update(score=3), stored=True)
    changed = english_marks(school, ['a'])
    changed.mark('a', 's2', '9')
    report = school.run(changed, '--apply')
    entry = school.entry(ENGLISH, D1)
    assert entry['publishedVersion'] < entry['version'] and report['lessons']['saved'] == 1 and report['lessons']['keptAsDraft'] == 1
    assert (school.grade(ENGLISH, D1, 'ep-a', 's1')['score'], school.grade(ENGLISH, D1, 'ep-a', 's2')['score']) == (3, 9)
    assert school.seen_by() == {'ep-a': 8}
    assert school.run(changed, '--apply')['lessons']['saved'] == 0 and school.seen_by() == {'ep-a': 8}


# ----------------------------------------------------------------------------- the ids of attempt works

NASTY = ['x', 'x-2', 'x~2', 'x-2~2', '-12', '-12-2', '-12~2', '', ' ', 'х 1', 'a/b', 'ep-', '~', '~2', 'L' * 100, 'L' * 100 + '-2', 'L' * 61, 'L' * 60]


def test_an_attempt_id_is_never_the_id_of_any_column_and_fits_the_limit():
    bases = {importer.work_id(uid) for uid in NASTY}
    assert len(bases) == len(NASTY)
    attempts = {importer.work_id(uid, k) for uid in NASTY for k in (2, 3, 10, 99, 100)}
    assert len(attempts) == len(NASTY) * 5 and not attempts & bases
    assert all(i.startswith(importer.PREFIX) and 0 < len(i) <= importer.ID_LIMIT for i in attempts | bases)


def test_attempt_works_never_depend_on_which_columns_exist_in_the_source(school):
    wanted = {'Alpha': ('x', '34'), 'Bravo': ('x-2', '9'), 'Charlie': ('x~2', '7'), 'Delta': ('-12', '12'), 'Echo': ('L' * 100, '56'), 'Foxtrot': ('-12-2', '8')}

    def build(titles):
        journals = school.journals()
        for title in titles:
            uid, texts = wanted[title]
            journals.column(uid, title=title, every='0')
            for n, text in enumerate(texts):
                journals.mark(uid, 's1', text, f'2026-09-14 {9 + n:02d}:00:00')
        return journals

    def held():
        works = school.works(ENGLISH, D1)
        return {title: sorted(school.grade(ENGLISH, D1, w)['score'] for w, a in works.items() if a['name'].startswith(title) and school.grade(ENGLISH, D1, w)) for title in wanted}

    # The first run knows only the first column; the others come with later runs, and each later run finds the attempt works where they are.
    school.run(build(['Alpha']), '--apply')
    school.run(build(['Alpha', 'Bravo']), '--apply')
    school.run(build(['Alpha', 'Bravo', 'Charlie']), '--apply')
    journals = build(list(wanted))
    school.run(journals, '--apply')
    assert held() == dict(Alpha=[3, 4], Bravo=[9], Charlie=[7], Delta=[1, 2], Echo=[5, 6], Foxtrot=[8])
    assert len(school.works(ENGLISH, D1)) == 9 and all(len(w) <= importer.ID_LIMIT for w in school.works(ENGLISH, D1))
    again = school.run(journals, '--apply')
    assert again['lessons']['toSave'] == 0 and again['works']['new'] == 0 and again['goneFromEdupage'] == 0 and 'PUT' not in school.wire.verbs


# ----------------------------------------------------------------------------- the real HTTP class against a small local server

class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'  # Connections stay open between requests.

    def log_message(self, *args):
        pass

    def answer(self):
        stub = self.server.stub
        length = int(self.headers.get('Content-Length') or 0)
        body = self.rfile.read(length) if length else b''
        with stub.lock:
            stub.seen.append(dict(method=self.command, target=self.path, headers={k.lower(): v for k, v in self.headers.items()}, port=self.client_address[1]))
            action = stub.script.get(len(stub.seen))
        if action == 'drop':  # Close the connection without an answer.
            self.close_connection = True
            return
        if action:  # Answer late.
            time.sleep(action)
        payload = json.dumps(dict(echo=json.loads(body) if body else None, cookie=self.headers.get('Cookie'))).encode()
        self.send_response(404 if self.path.startswith('/missing') else 200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(payload)))
        if self.path.startswith('/login'):
            self.send_header('Set-Cookie', 'session=abc123; Path=/; HttpOnly')
        self.end_headers()
        self.wfile.write(payload)

    do_GET = do_PUT = do_POST = answer


class Stub:
    """A local HTTP/1.1 server on a free port (not the Atlas app): remembers every request; script maps the number of a request to 'drop' or to a delay."""

    def __init__(self, tls=None):
        self.seen, self.script, self.lock = [], {}, threading.Lock()
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.daemon_threads, self.server.stub = True, self
        if tls:
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(*tls)
            self.server.socket = context.wrap_socket(self.server.socket, server_side=True)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def stub():
    found = []

    def start(**options):
        found.append(Stub(**options))
        return found[-1]

    yield start
    for one in found:
        one.close()


def test_http_keeps_one_connection_the_cookie_and_the_request_header(stub):
    server = stub()
    http = importer.Http(f'http://127.0.0.1:{server.port}')
    assert http.call('POST', '/login', None, dict(login='x'))[0] == 200
    status, data = http.call('GET', '/api/ping', dict(workspace='demo', day='2026-09-14'))
    assert status == 200 and data['cookie'] == 'session=abc123'
    status, data = http.call('PUT', '/api/ping', None, dict(version=3, text='Привет'))
    assert (status, data['echo']) == (200, dict(version=3, text='Привет')) and data['cookie'] == 'session=abc123'
    assert http.call('GET', '/missing')[0] == 404
    assert [r['target'] for r in server.seen] == ['/login', '/api/ping?workspace=demo&day=2026-09-14', '/api/ping', '/missing']
    assert len({r['port'] for r in server.seen}) == 1
    assert all(r['headers'].get('x-atlas-request') == importer.HEADERS['X-Atlas-Request'] and r['headers'].get('connection', '').lower() != 'close' for r in server.seen)


@pytest.mark.parametrize('method', ['GET', 'PUT'])
def test_http_asks_again_when_the_server_closed_the_connection(stub, method):
    server = stub()
    http = importer.Http(f'http://127.0.0.1:{server.port}')
    assert http.call('POST', '/login')[0] == 200
    server.script[2] = 'drop'  # The next request meets a closed connection.
    status, data = http.call(method, '/api/ping', None, dict(n=1) if method == 'PUT' else None)
    assert status == 200 and data['cookie'] == 'session=abc123' and (data['echo'] == dict(n=1)) == (method == 'PUT')
    assert len(server.seen) == 3 and server.seen[1]['port'] != server.seen[2]['port']


def test_http_does_not_wait_for_a_stalled_answer(stub):
    server = stub()
    http = importer.Http(f'http://127.0.0.1:{server.port}', timeout=0.4)
    assert http.call('GET', '/login')[0] == 200
    server.script[2] = 5
    started = time.monotonic()
    assert http.call('GET', '/api/ping')[0] == 200
    assert time.monotonic() - started < 4 and len(server.seen) == 3


def test_http_reports_a_server_that_is_not_there(monkeypatch):
    monkeypatch.setattr(importer.time, 'sleep', lambda seconds: None)
    with socket.socket() as free:
        free.bind(('127.0.0.1', 0))
        port = free.getsockname()[1]
    with pytest.raises(importer.Stop) as stopped:
        importer.Http(f'http://127.0.0.1:{port}').call('GET', '/api/ping')
    assert stopped.value.step == 'connection'


def certificate(folder):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'atlas test')])
    start = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(start - timedelta(days=1)).not_valid_after(start + timedelta(days=1))
            .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address('127.0.0.1'))]), critical=False)
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
            .sign(key, hashes.SHA256()))
    (folder / 'cert.pem').write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    (folder / 'key.pem').write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    return str(folder / 'cert.pem'), str(folder / 'key.pem')


def test_http_works_over_https_and_keeps_the_connection(stub, tmp_path):
    cert, key = certificate(tmp_path)
    server = stub(tls=(cert, key))
    http = importer.Http(f'https://127.0.0.1:{server.port}', context=ssl.create_default_context(cafile=cert))
    assert http.call('POST', '/login', None, dict(login='x'))[0] == 200
    status, data = http.call('PUT', '/api/ping', None, dict(n=2))
    assert (status, data['echo'], data['cookie']) == (200, dict(n=2), 'session=abc123')
    assert len({r['port'] for r in server.seen}) == 1


def test_http_refuses_a_certificate_it_does_not_trust(stub, tmp_path, monkeypatch):
    monkeypatch.setattr(importer.time, 'sleep', lambda seconds: None)
    server = stub(tls=certificate(tmp_path))
    with pytest.raises(importer.Stop) as stopped:
        importer.Http(f'https://127.0.0.1:{server.port}', context=ssl.create_default_context()).call('GET', '/api/ping')
    assert stopped.value.step == 'connection' and not server.seen


def system_proxy(monkeypatch, port):
    for name in ['NO_PROXY', 'no_proxy', 'HTTPS_PROXY', 'https_proxy']:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv('http_proxy', f'http://127.0.0.1:{port}')


def test_a_system_proxy_is_not_used_for_a_loopback_address(stub, monkeypatch):
    server, proxy = stub(), stub()
    system_proxy(monkeypatch, proxy.port)
    assert importer.Http(f'http://127.0.0.1:{server.port}').call('GET', '/api/ping')[0] == 200
    assert len(server.seen) == 1 and not proxy.seen


def test_a_system_proxy_is_used_for_another_host(stub, monkeypatch):
    proxy = stub()
    system_proxy(monkeypatch, proxy.port)
    status, data = importer.Http('http://atlas.invalid:8123/prefix').call('GET', '/api/ping', dict(workspace='demo'))
    assert status == 200 and [r['target'] for r in proxy.seen] == ['http://atlas.invalid:8123/prefix/api/ping?workspace=demo']


def test_progress_goes_to_stderr_as_counts_and_the_report_stays_on_stdout(school, capsys):
    journals = school.journals()
    journals.column('x', title='особоеназвание')
    journals.mark('x', 's1', 'оченьособыйкомментарий')
    report = school.run(journals, '--apply')
    out, err = capsys.readouterr()
    assert json.loads(out) == report == json.loads((school.tmp / 'report.json').read_text(encoding='utf8'))
    assert err.strip() and all(any(ch.isdigit() for ch in line) for line in err.splitlines())
    names = [name for _, name in journals.people.values()] + [part for _, name in journals.people.values() for part in name.split() if len(part) > 3]
    assert not [secret for secret in [*names, 'особоеназвание', 'оченьособыйкомментарий', 'ep-x', ENGLISH] if secret in err]
