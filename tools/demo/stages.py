"""Seed stages that need other modules than the journal: homework, tests, messages, meetings.

The recipes follow the integration tests (tests/test_homework.py, test_quizzes.py,
test_quiz_transfer.py, test_school_forms.py, test_meeting_consultations.py).
"""
from datetime import date
from uuid import uuid4

from . import content, school

TZ = '+05:00'
QUIZ_ID = 'demo-test-fractions'


def rid():
    return str(uuid4())


class Stages:
    # ---- absences and substitutions (saved through the module API, as the timetable editor does)
    def substitute(self, day, candidates, reason):
        from backend.models import DatedLesson, TeacherAbsence
        self.at(day, '07:30')
        state = self.api.read('school')
        weekday = date.fromisoformat(day).weekday()
        teacher_of = {w.id: w.teacherId for w in state.workloads}

        def lessons(teacher):
            return sorted((p for p in state.placements if p.day == weekday and teacher_of[p.workloadId] == teacher), key=lambda p: p.period)

        absent = max(candidates, key=lambda t: len(lessons(t)))
        assert lessons(absent), f'nobody of {candidates} teaches on {day}'
        taken = {}
        changes = []
        for placement in lessons(absent):
            busy = {teacher_of[p.workloadId] for p in state.placements if p.day == weekday and p.period == placement.period}
            free = [t.id for t in state.teachers if t.id not in busy | {school.SHOWCASE_TEACHER, absent} and taken.get(placement.period) != t.id]
            taken[placement.period] = free[0]
            changes.append(DatedLesson(id=f'sub-{day}-{placement.period}', name='Замена', kind='change', placementId=placement.id,
                                       sourceDate=date.fromisoformat(day), onDate=date.fromisoformat(day), teacherIds=[free[0]], changeType='substitution'))
        state.absences = list(state.absences) + [TeacherAbsence(id=f'absence-{day}', name=reason, teacherId=absent,
                                                                start=date.fromisoformat(day), end=date.fromisoformat(day))]
        state.datedLessons = list(state.datedLessons) + changes
        self.api.save('school', state)
        self.log(f'{day}: {absent} absent, {len(changes)} substitutions')

    # ---- homework: pupils submit on the day after the lesson, teachers review about half
    def homework_round(self, day):
        pending = self.homework_lessons.pop(self.previous_school_day(day), [])
        if not pending:
            return
        self.at(day, '16:00')
        for key, teacher_id in pending:
            chosen = set(self.rng.sample(range(10), self.rng.randint(6, 9)))
            # The showcase pupil does nearly every task; the others fill the teacher's queue.
            (chosen.add if self.rng.random() < .92 else chosen.discard)(int(school.SHOWCASE_PUPIL[1:]))
            for index in sorted(chosen):
                client, sid = self.pupil(index), school.pupil_id(index)
                url = f'/api/journal/homework/{key}'
                current = self.ok(client.get(url, params=dict(studentId=sid)))
                sent = self.ok(client.put(url, params=dict(studentId=sid), json=dict(
                    studentId=sid, version=current['version'], assignment=current['assignment'], action='submit',
                    text=self.rng.choice(content.ANSWERS), fileIds=[])))
                self.homework_waiting.append((key, teacher_id, sid, sent['version'], sent['assignment'], day))
                self.counts['homework'] += 1
        self.at(day, '17:00')
        waiting, self.homework_waiting = self.homework_waiting, []
        for key, teacher_id, sid, version, assignment, sent_on in waiting:
            showcase = sid == school.SHOWCASE_PUPIL
            if sent_on == day or (not showcase and self.rng.random() < .4):
                self.homework_waiting.append((key, teacher_id, sid, version, assignment, sent_on))
                continue
            returned = self.rng.random() < .12 and not showcase
            self.ok(self.teacher(teacher_id).post(f'/api/journal/homework/{key}/review', json=dict(
                studentId=sid, version=version, assignment=assignment, decision='returned' if returned else 'accepted',
                comment=content.RETURN_COMMENT if returned else self.rng.choice(content.REVIEW_COMMENTS))))
            self.counts['reviewed'] += 1

    # ---- a course test of Mathematics 7А: created, taken, released and moved into the journal
    def course_test_create(self, day):
        self.at(day, '08:00')
        teacher = self.teacher('t0')
        bank = []
        for number, (text, options, correct) in enumerate(content.TEST_QUESTIONS):
            ids = [chr(ord('a') + i) for i in range(len(options))]
            bank.append(self.ok(teacher.put(f'/api/journal/quiz-bank/demo-q{number + 1}', json=dict(
                version=0, subject='Математика', tags=['дроби', 'уравнения'],
                question=dict(kind='single', text=text, points=2, options=[dict(id=i, text=o) for i, o in zip(ids, options)],
                              correctIds=[ids[correct]], explanation='Разбор на уроке.')))))
        self.quiz_bank = bank
        definition = dict(
            title='Контрольная работа: дроби и уравнения', instructions='Выберите один ответ в каждом вопросе.',
            variants=[dict(id='v1', name='Вариант 1', questions=[dict(id=q['id'], version=q['version']) for q in bank])],
            opensAt=f'{day}T08:00:00{TZ}', closesAt=f'2026-10-28T09:00:00{TZ}', timeLimitMinutes=20, maxAttempts=1,
            shuffleQuestions=True, shuffleOptions=True)
        self.ok(teacher.put(f'/api/journal/quizzes/{QUIZ_ID}', json=dict(
            version=0, revision=self.api.read('school').revision, groupId='gc0', subjectId='b0', definition=definition,
            studentIds=school.class_pupils(0)[:13], publish=True)))

    def course_test_attempts(self, day):
        self.at(day, '10:00')
        for index in range(11):
            client, sid = self.pupil(index), school.pupil_id(index)
            quiz = self.ok(client.get(f'/api/journal/quizzes/{QUIZ_ID}', params=dict(studentId=sid)))
            begun = self.ok(client.post(f'/api/journal/quizzes/{QUIZ_ID}/attempts', json=dict(version=quiz['version'], studentId=sid, requestId=uuid4().hex)))
            answers = {}
            for number, (text, options, correct) in enumerate(content.TEST_QUESTIONS):
                ids = [chr(ord('a') + i) for i in range(len(options))]
                knows = self.rng.random() < self.ability[sid] + .1
                wrong = [i for i in ids if i != ids[correct]]
                answers[f'demo-q{number + 1}'] = dict(selected=[ids[correct] if knows else self.rng.choice(wrong)])
            self.ok(client.put(f'/api/journal/quizzes/{QUIZ_ID}/attempts/{begun["id"]}', json=dict(version=begun['version'], answers=answers, submit=True)))

    def course_test_release(self, day):
        self.at(day, '12:00')
        teacher = self.teacher('t0')
        quiz = self.ok(teacher.get(f'/api/journal/quizzes/{QUIZ_ID}'))
        closed = self.ok(teacher.post(f'/api/journal/quizzes/{QUIZ_ID}/actions', json=dict(version=quiz['version'], action='close', reason='Работа завершена')))
        self.ok(teacher.post(f'/api/journal/quizzes/{QUIZ_ID}/actions', json=dict(version=closed['version'], action='release', reason='Результаты опубликованы')))

    def course_test_transfer(self, day):
        """Move the marks into the Mathematics lesson of 7А on `day` (after its entry is written)."""
        self.at(day, '18:30')
        teacher = self.teacher('t0')
        rows = [r for r in self.lessons_of(day) if r['lesson']['group'] == '7А' and r['lesson']['subject'] == 'Математика']
        assert rows, f'no Mathematics lesson of 7А on {day}'
        body = dict(lessonKey=rows[0]['lessonKey'], day=day, revision=self.api.read('school').revision, pick='last', publish=True,
                    newWork=dict(name='Контрольная работа: дроби и уравнения'))
        seen = self.ok(teacher.post(f'/api/journal/quizzes/{QUIZ_ID}/transfer/preview', json=body))
        done = self.ok(teacher.post(f'/api/journal/quizzes/{QUIZ_ID}/transfer', json=body | dict(requestId=rid(), token=seen['token'], overwrite=[])))
        self.log(f'course test moved to the journal on {day}')
        return done
