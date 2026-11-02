"""Seed the fictional demo school through the real API on a walking frozen clock.

    python -m tools.demo.seed_school --workdir DIR

Everything runs in-process (fastapi TestClient) against DIR/demo.db, a database
that is deleted and recreated. Nothing here reads school data: the school comes
from tools/demo/school.py. Side effects live under __main__ because the
gradebook worker is a spawned process that imports this module again.
"""
import argparse
import json
import random
import secrets
import shutil
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from . import content, names, school
from .stages import Stages
from .stages_social import Social

TZ = '+05:00'
START_DAY = '2026-10-05'
CLOSE_FROM, CLOSE_TO = '2026-10-05', '2026-10-16'
LAST_WEEK_OF_PERIOD = ('2026-10-12', '2026-10-16')
HOMEWORK_FROM = '2026-10-19'
LAST_FULL_DAY = '2026-10-30'
QUIZ_DAYS = dict(open='2026-10-26', attempt='2026-10-27', release='2026-10-28')
BEHAVIOUR = {
    '2026-10-21': [('activity', [0, 5], 'Активная работа на уроке, верные ответы у доски')],
    '2026-10-23': [('help', [2], 'Помог однокласснику разобраться с задачей')],
    '2026-10-27': [('unprepared', [9], 'Не выполнено домашнее задание'), ('activity', [1, 7], 'Хорошая работа в группе')],
    '2026-10-28': [('discipline', [11], 'Разговоры во время объяснения, сделано устное замечание')],
    '2026-10-30': [('activity', [0], 'Лучшая работа на уроке недели'), ('help', [4, 8], 'Помогли подготовить класс к экскурсии')],
}
SCALE_BANDS = [('2', 0), ('3', 40), ('4', 60), ('5', 80)]


def school_days(first, last):
    day = date.fromisoformat(first)
    while day <= date.fromisoformat(last):
        if day.weekday() < 5:
            yield day.isoformat()
        day += timedelta(days=1)


def due_day(day):
    """Two school days after the lesson: a deadline never falls on a weekend."""
    result, left = date.fromisoformat(day), 2
    while left:
        result += timedelta(days=1)
        if result.weekday() < 5:
            left -= 1
    return result.isoformat()


def rid():
    return str(uuid4())


class Seed(Stages, Social):
    tz = TZ

    def __init__(self, api, clock, boot, work, started):
        self.api, self.clock, self.boot = api, clock, boot
        self.work = work
        self.started = started
        self.rng = random.Random(school.DEMO_NOW)
        self.admin = boot.client(api)
        self.clients = {}
        self.passwords = {}
        self.account_ids = {}
        self.now = None
        self.ability = {}
        for index in range(60):
            self.ability[school.pupil_id(index)] = .45 + .5 * self.rng.random()
        # The showcase pupil (the parent's and the pupil's own view) is a good, steady learner.
        self.ability[school.SHOWCASE_PUPIL] = .86
        self.absent_days = {}
        self.counts = dict(lessons=0, homework=0, reviewed=0)
        self.topic_counter = {}
        self.homework_lessons = {}
        self.homework_waiting = []
        self.behaviour_plan = BEHAVIOUR
        self.soch_done = set()

    # ---- infrastructure
    def log(self, text):
        print(f'[{time.time() - self.started:6.1f}s] {text}', flush=True)

    @staticmethod
    def ok(response, code=200):
        assert response.status_code == code, (response.request.method, str(response.request.url)[:120], response.status_code, response.text[:600])
        return response.json() if response.content else None

    def at(self, day, clock_time):
        """Move the clock; sessions last 12 hours of frozen time, so everybody signs in again."""
        self.clock.set_time(f'{day}T{clock_time}:00{TZ}')
        self.now = (day, clock_time)
        self.clients.clear()
        if 'admin' in self.passwords:
            self.sign_in(self.admin, 'demo-admin')

    def sign_in(self, client, login):
        self.ok(client.post('/api/auth/login', json=dict(login=login, password=self.passwords[login])))

    def as_(self, login):
        if login == 'demo-admin':
            return self.admin
        if login not in self.clients:
            client = self.boot.client(self.api)
            self.sign_in(client, login)
            self.clients[login] = client
        return self.clients[login]

    def teacher(self, teacher_id):
        return self.as_(school.logins()['teachers'][int(teacher_id[1:])])

    def pupil(self, index):
        return self.as_(school.logins()['pupils'][index])

    def parent(self, index):
        return self.as_(school.logins()['parents'][index])

    def password(self):
        return secrets.token_urlsafe(18)

    # ---- setup
    def setup(self):
        self.at('2026-10-02', '12:00')
        logins = school.logins()
        self.ok(self.admin.get('/api/state'))
        admin_password = self.password()
        self.passwords['demo-admin'] = admin_password
        self.ok(self.admin.post('/api/auth/setup', json=dict(name=names.ADMIN_NAME, login='demo-admin', password=admin_password)))
        # The notification tables are created by the first read; a publish needs them.
        self.ok(self.admin.get('/api/notifications/inbox'))
        self.passwords['admin'] = admin_password
        self.sign_in(self.admin, 'demo-admin')

        def create(role, login, name, entity=None, view=None):
            password = self.password()
            self.passwords[login] = password
            grants = [dict(workspace='school', view=view, entityIds=[entity])] if entity else []
            made = self.ok(self.admin.post('/api/access/users', json=dict(name=name, login=login, role=role, workspaces=['school'], password=password, grants=grants)))
            self.account_ids[login] = made['id']

        for index, login in enumerate(logins['teachers']):
            create('teacher', login, names.TEACHERS[index], f't{index}', 'teacher')
        for index in range(school.PUPILS_PER_CLASS):
            create('parent', logins['parents'][index], names.GUARDIANS[index], school.pupil_id(index), 'student')
            create('student', logins['pupils'][index], names.PUPILS[index], school.pupil_id(index), 'student')
        self.ok(self.admin.get('/api/forms/options'))
        self.log(f'{len(self.account_ids) + 1} accounts')
        self.verify_families()
        periods = [('Первая четверть', CLOSE_FROM, CLOSE_TO), ('Вторая четверть', '2026-10-19', '2026-12-25')]
        self.periods = []
        for label, start, end in periods:
            made = self.ok(self.admin.put(f'/api/journal/periods/{uuid4().hex}', json=dict(name=label, start=start, end=end, kind='quarter', version=0, reason='Начало учебного периода')))
            self.periods.append(made)
        self.scale = self.ok(self.admin.put(f'/api/final-grades/scales/{uuid4().hex}', json=dict(
            version=0, name='Шкала 2–5', bands=[dict(label=label, minPercent=percent) for label, percent in SCALE_BANDS])))

    # ---- the journal
    def lessons_of(self, day):
        return [row for row in self.ok(self.admin.get('/api/journal/day', params=dict(day=day)))['lessons'] if not row['archived']]

    def fill(self, day, until=None, skip=0.0, leave=(), drafts=()):
        """Write and publish the lessons of a day. `until` (HH:MM) stops at lessons that have not begun."""
        self.at(day, '18:00' if until is None else until)
        number = 0
        for row in self.lessons_of(day):
            lesson = row['lesson']
            if until is not None and lesson['start'] >= until:
                continue
            number += 1
            if (number in leave) or (skip and self.rng.random() < skip):
                continue
            self.write_lesson(row, day, publish=number not in drafts)

    def write_lesson(self, row, day, publish=True):
        key = row['lessonKey']
        entry = self.ok(self.admin.get('/api/journal/entry', params=dict(lessonKey=key, day=day)))
        document = entry['document']
        subject, group = document['lesson']['subject'], document['lesson']['group']
        writer_ids = document['lesson']['teacherIds']
        writer = self.teacher(writer_ids[0]) if writer_ids else self.admin
        counter = self.topic_counter.setdefault((group, subject), 0)
        self.topic_counter[(group, subject)] = counter + 1
        topics = content.TOPICS[subject]
        body = document['content']
        body['topic'] = topics[counter % len(topics)]
        number = 3 + counter
        body['homework'] = content.HOMEWORK[subject].format(n=number, a=number * 2, b=number * 2 + 3)
        body['dueDate'] = due_day(day)
        category, name = self.category(day, group, subject, counter)
        body['assessments'] = [dict(id='w1', name=name, kind='points', maxScore=10, weight=1, category=category)]
        for record in body['records']:
            roll = self.rng.random()
            student = record['studentId']
            absent_chance = .06 if int(student[1:]) % 7 else .14
            record['attendance'] = 'absent' if roll < absent_chance else 'late' if roll < absent_chance + .05 else 'present'
            record['lateMinutes'] = self.rng.choice([3, 5, 10]) if record['attendance'] == 'late' else 0
            if record['attendance'] == 'absent':
                record['grades'] = []
                self.absent_days.setdefault(student, []).append(day)
                continue
            score = round(10 * (self.ability[student] + self.rng.uniform(-.18, .12)))
            record['grades'] = [dict(assessmentId='w1', score=max(3, min(10, score)), label='', comment='')]
        payload = dict(lessonKey=key, day=day, revision=entry['revision'], version=entry['version'], content=body, publish=publish)
        saved = self.ok(writer.put('/api/journal/entry', json=payload))
        self.counts['lessons'] += 1
        if publish and day >= HOMEWORK_FROM and group == '7А' and writer_ids:
            self.homework_lessons.setdefault(day, []).append((key, school.course_teacher(0, subject)))
        return saved

    def category(self, day, group, subject, counter):
        if LAST_WEEK_OF_PERIOD[0] <= day <= LAST_WEEK_OF_PERIOD[1] and (group, subject) not in self.soch_done:
            self.soch_done.add((group, subject))
            return 'soch', 'Суммативное оценивание за четверть'
        if counter % 8 == 5:
            return 'sor', 'Суммативное оценивание за раздел'
        return 'fo', 'Работа на уроке'

    @staticmethod
    def previous_school_day(day):
        days = list(school_days(START_DAY, '2026-11-06'))
        return days[days.index(day) - 1]

    # ---- the closed period and the final table
    def close_first_period(self):
        self.at('2026-10-17', '10:00')
        period = self.periods[0]
        preview = self.ok(self.admin.post(f'/api/journal/periods/{period["id"]}/preview-close', json=dict(version=period['version'])))
        closed = self.ok(self.admin.post(f'/api/journal/periods/{period["id"]}/close', json=dict(
            version=period['version'], previewId=preview['previewId'], acknowledgeIssues=True, reason='Завершение первой четверти')))
        self.periods[0] = closed
        self.log(f'period closed: {preview["counts"]}')
        job = self.ok(self.admin.post(f'/api/final-grades/periods/{closed["id"]}/prepare', json=dict(version=closed['version'])), 202)
        deadline = time.monotonic() + 120
        while job['status'] in ['queued', 'running', 'cancelling'] and time.monotonic() < deadline:
            time.sleep(.2)
            job = self.ok(self.admin.get('/api/final-grades/preparations/' + job['id']))
        assert job['status'] == 'succeeded', job
        self.book_id = job['bookId']
        self.log(f'gradebook prepared: {job.get("message")}')

    def sheet_rows(self, client, **params):
        rows, offset = [], 0
        while offset is not None:
            page = self.ok(client.get(f'/api/final-grades/books/{self.book_id}', params=dict(offset=offset, **params)))
            rows += page['items']
            offset = page['nextOffset']
        return rows

    def finals(self):
        """Teachers propose, the administrator approves and publishes 7А and 7Б; 8А and 8Б stay proposed."""
        from backend.final_grades import recommendation
        self.at('2026-10-19', '08:00')
        scale = self.ok(self.admin.get('/api/final-grades/options'))['scales'][0]
        left_open = {('c3', 'b6'), ('c3', 'b7')}  # a teacher has not proposed yet
        proposed = 0
        for index in range(school.TEACHER_COUNT):
            client = self.teacher(f't{index}')
            rows = [r for r in self.sheet_rows(client) if r['status'] == 'draft' and (r['classId'], r['subjectId']) not in left_open]
            for start in range(0, len(rows), 100):
                chunk = rows[start:start + 100]
                items = []
                for row in chunk:
                    label = recommendation(row['source'], scale)['recommended']
                    items.append(dict(id=row['id'], version=row['version'], label=label) if label else dict(
                        id=row['id'], version=row['version'], outcome='not_assessed', explanation='Недостаточно оценок'))
                self.ok(client.post(f'/api/final-grades/books/{self.book_id}/proposals', json=dict(
                    requestId=rid(), scaleId=scale['id'], scaleVersion=scale['version'], submit=True, rows=items)))
                proposed += len(chunk)
        self.log(f'final grades proposed: {proposed}')
        self.at('2026-10-19', '08:40')
        published = 0
        for class_id in ['c0', 'c1']:
            rows = [r for r in self.sheet_rows(self.admin, classId=class_id) if r['status'] == 'submitted']
            for start in range(0, len(rows), 100):
                chunk = [dict(id=r['id'], version=r['version']) for r in rows[start:start + 100]]
                approved = self.ok(self.admin.post('/api/final-grades/actions', json=dict(rows=chunk, action='approve')))
                self.ok(self.admin.post('/api/final-grades/actions', json=dict(
                    rows=[dict(id=r['id'], version=r['version']) for r in approved['items']], action='publish')))
                published += len(chunk)
        self.log(f'final grades published: {published}')


def parse(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workdir', required=True)
    parser.add_argument('--stop-after', default='', help='development: stop after a named stage')
    return parser.parse_args(argv)


def main(argv=None):
    args = parse(argv)
    started = time.time()
    work = Path(args.workdir).resolve()
    import os
    os.environ.update(TIMETABLE_DB=str(work / 'demo.db'), ATLAS_WEB_PUSH_ENABLED='0', ATLAS_BACKUP_DIR='')
    from . import clock
    clock.assert_safe_database()  # refuses a repository path or a data/ directory before anything is deleted
    work.mkdir(parents=True, exist_ok=True)
    for stale in work.glob('demo.db*'):
        shutil.rmtree(stale) if stale.is_dir() else stale.unlink()
    for stale in work.glob('demo.files'):
        shutil.rmtree(stale)
    from . import boot
    api = boot.boot(work, '2026-10-02T12:00:00' + TZ)
    seed = Seed(api, clock, boot, work, started)
    try:
        seed.run(args.stop_after)
    finally:
        api.shutdown_gradebook_jobs()
    meta = dict(now=school.DEMO_NOW, showcase=dict(classId=school.SHOWCASE_CLASS, pupilId=school.SHOWCASE_PUPIL, teacherId=school.SHOWCASE_TEACHER),
                roles={role: dict(login=login, password=seed.passwords[login]) for role, login in dict(
                    admin='demo-admin', teacher=school.logins()['teachers'][0], parent=school.logins()['parents'][0], student=school.logins()['pupils'][0]).items()})
    (work / 'meta.json').write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding='utf-8')
    seed.log('done')
    return 0


def run(self, stop=''):
    self.setup()
    if stop == 'setup':
        return
    for day in school_days(START_DAY, CLOSE_TO):
        self.fill(day)
    self.log(f'period 1 written: {self.counts}')
    self.close_first_period()
    self.finals()
    if stop == 'finals':
        return
    for day in school_days('2026-10-19', LAST_FULL_DAY):
        if day == QUIZ_DAYS['open']:
            self.course_test_create(day)
        if day == QUIZ_DAYS['attempt']:
            self.course_test_attempts(day)
        if day == QUIZ_DAYS['release']:
            self.course_test_release(day)
        if day == '2026-10-28':
            self.substitute(day, [f't{i}' for i in range(1, school.TEACHER_COUNT)], 'Курсы повышения квалификации')
        self.homework_round(day)
        # Gaps that the coverage report should show: lessons never written, and a draft left unpublished.
        self.fill(day, skip=.0, leave=(2, 7) if day == '2026-10-21' else (4,) if day == LAST_FULL_DAY else (),
                  drafts=(9,) if day == LAST_FULL_DAY else ())
        if day == QUIZ_DAYS['release']:
            self.course_test_transfer(day)
        self.behaviour_notes(day)
    self.log(f'period 2 written: {self.counts}')
    self.at(LAST_FULL_DAY, '18:55')
    self.absence_notes()
    self.at(LAST_FULL_DAY, '19:00')
    self.messages()
    self.parents_form()
    self.at(LAST_FULL_DAY, '19:10')
    self.consultations()
    self.class_meeting()
    self.assessment_bookings()
    if stop == 'social':
        return
    self.substitute('2026-11-02', [f't{i}' for i in range(1, school.TEACHER_COUNT)], 'Болезнь')
    self.fill('2026-11-02', until='11:30', leave=(3, 8))
    self.at('2026-11-02', '11:30')
    self.mark_read()


Seed.run = run

if __name__ == '__main__':
    sys.exit(main())
