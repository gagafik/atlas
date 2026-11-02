"""Seed stages for messages, forms, meetings, behaviour notes and the assessment calendar."""
from datetime import date, timedelta
from uuid import uuid4

from . import school


def rid():
    return str(uuid4())


class Social:
    def account(self, login):
        return self.account_ids[login]

    def messages(self):
        logins = school.logins()
        teacher = self.account(logins['teachers'][0])
        class_teacher_two = self.account(logins['teachers'][1])
        # A parent writes to the class teacher and gets an answer.
        parent = self.parent(0)
        thread = self.ok(parent.post('/api/messages/threads', json=dict(
            requestId=rid(), title='Вопрос по домашнему заданию', text='Здравствуйте! Подскажите, пожалуйста, какие задачи нужно решить к четвергу?',
            recipientId=teacher, studentId=school.SHOWCASE_PUPIL)))
        self.ok(self.teacher('t0').post(f'/api/messages/threads/{thread["id"]}/reply', json=dict(
            requestId=rid(), text='Добрый день! Нужно решить задачи 6–9 из параграфа 4, остальное по желанию.')))
        self.ok(parent.post(f'/api/messages/threads/{thread["id"]}/reply', json=dict(requestId=rid(), text='Спасибо, передам.')))
        second = self.ok(self.parent(3).post('/api/messages/threads', json=dict(
            requestId=rid(), title='Отсутствие 3 ноября', text='Сын пропустит занятия 3 ноября: визит к врачу.',
            recipientId=teacher, studentId=school.pupil_id(3))))
        self.ok(self.teacher('t0').post(f'/api/messages/threads/{second["id"]}/reply', json=dict(requestId=rid(), text='Принято, спасибо, что предупредили.')))
        self.ok(self.parent(6).post('/api/messages/threads', json=dict(
            requestId=rid(), title='Тема сочинения', text='Можно ли выбрать тему самостоятельно?',
            recipientId=class_teacher_two, studentId=school.pupil_id(6))))
        # Announcements: one from the teacher to the class, one from the administrator to all families (with a confirmation).
        self.ok(self.teacher('t0').post('/api/messages/threads', json=dict(
            requestId=rid(), kind='announcement', title='Экскурсия в музей', text='В пятницу класс едет в краеведческий музей. Сбор у школы в 9:00.',
            groupIds=['gc0'], roles=['parent', 'student'])))
        self.ok(self.admin.post('/api/messages/threads', json=dict(
            requestId=rid(), kind='announcement', title='Родительские собрания в ноябре', requireAck=True,
            text='Собрания классов пройдут 5 и 6 ноября в актовом зале. Просим подтвердить, что вы прочитали это сообщение.',
            schoolwide=True, roles=['parent'])))
        self.ok(self.admin.post('/api/messages/threads', json=dict(
            requestId=rid(), kind='announcement', title='Педагогический совет', text='Во вторник в 15:30 в кабинете 201 состоится педагогический совет.',
            audience='teachers')))
        self.log('messages and announcements written')

    def parents_form(self):
        form = self.ok(self.admin.post('/api/forms/items', json=dict(requestId=rid(), title='Согласие на экскурсию', kind='survey')))
        current = self.ok(self.admin.get('/api/forms/items/' + form['id']))
        definition = current['definition'] | dict(
            description='Ответьте, пожалуйста, до понедельника.',
            questions=[dict(id='q', title='Вы согласны на поездку в музей?', kind='single', required=True, choices=['Да', 'Нет']),
                       dict(id='n', title='Есть ли особые пожелания?', kind='text', required=False)],
            audience=dict(roles=['parent'], classIds=['c0']))
        self.ok(self.admin.put('/api/forms/items/' + form['id'], json=dict(requestId=rid(), version=current['version'], definition=definition)))
        preview = self.ok(self.admin.get(f'/api/forms/items/{form["id"]}/preview'))
        self.ok(self.admin.post(f'/api/forms/items/{form["id"]}/transition', json=dict(
            requestId=rid(), version=preview['version'], action='publish', audienceHash=preview['audienceHash'])))
        published = self.ok(self.admin.get('/api/forms/items/' + form['id']))
        for index in self.rng.sample(range(school.PUPILS_PER_CLASS), 9):
            self.ok(self.parent(index).put(f'/api/forms/items/{form["id"]}/answer', json=dict(
                requestId=rid(), version=0, formVersion=published['version'], entityId=school.pupil_id(index), action='submit',
                answers={'q': 'Да' if index % 5 else 'Нет', 'n': 'Нет' if index % 2 else 'Без особых пожеланий'})))
        self.log('form published, 9 of 15 families answered')

    def consultations(self):
        teacher = self.teacher('t0')
        series = dict(requestId=rid(), teacherId='t0', title='Консультации по математике', startDate='2026-11-03', endDate='2026-11-24',
                      weekdays=[1], startTime='16:00', endTime='17:00', slotMinutes=15, roomId='r0')
        preview = self.ok(teacher.post('/api/meetings/consultations/preview', json=series))
        made = self.ok(teacher.post('/api/meetings/consultations', json=series | dict(previewHash=preview['previewHash'])))
        event_id = made.get('id') or made['event']['id']
        slots = self.ok(teacher.get(f'/api/meetings/events/{event_id}/slots', params=dict(limit=100)))['items']
        detail = self.ok(self.parent(0).get(f'/api/meetings/events/{event_id}'))
        for slot_number, index in [(0, 0), (1, 4), (3, 8)]:
            client = self.parent(index)
            event = self.ok(client.get(f'/api/meetings/events/{event_id}'))
            self.ok(client.post(f'/api/meetings/slots/{slots[slot_number]["id"]}/book', json=dict(
                requestId=rid(), version=slots[slot_number]['version'], eventVersion=event['version'], studentId=school.pupil_id(index))))
        del detail
        self.log(f'consultation series with {len(slots)} slots, 3 booked')

    def class_meeting(self):
        self.ok(self.teacher('t0').post('/api/meetings/class-meetings', json=dict(
            requestId=rid(), title='Родительское собрание 7А', classIds=['c0'], startsAt=f'2026-11-05T17:00:00{self.tz}',
            endsAt=f'2026-11-05T18:00:00{self.tz}', location='Актовый зал')))

    def verify_families(self):
        """The school records who each guardian is, so the family screens show a verified relationship."""
        items = self.ok(self.admin.get('/api/access/families'))['items']
        done = 0
        for item in items:
            if item['verified']:
                continue
            relation = 'mother' if self.guardian_is_mother(item['parentName']) else 'father'
            self.ok(self.admin.put(f'/api/access/families/{item["parentId"]}/{item["studentId"]}', json=dict(
                version=item['version'], accountVersion=item['accountVersion'], relationship=relation, verified=True,
                evidence='Данные из заявления о приёме (вымышленный пример)', status='active', reason='Сверено школой')))
            done += 1
        self.log(f'{done} family links verified')

    @staticmethod
    def guardian_is_mother(name):
        return name.split()[0].endswith('а')

    def absence_notes(self):
        """Parents explain some absences of the second period; the class teacher accepts most of them."""
        sent = []
        for index in range(school.PUPILS_PER_CLASS):
            sid = school.pupil_id(index)
            days = [d for d in self.absent_days.get(sid, []) if d >= '2026-10-19']
            if not days or len(sent) >= 6:
                continue
            note = self.ok(self.parent(index).post('/api/journal/absences', json=dict(
                requestId=rid(), studentId=sid, startDate=days[0], endDate=days[0],
                reason='Был на приёме у врача, справка у классного руководителя.', fileIds=[])))
            sent.append(note)
        for number, note in enumerate(sent):
            if number == len(sent) - 1:
                break  # the last one waits for a decision
            action = 'reject' if number == len(sent) - 2 else 'approve'
            self.ok(self.teacher('t0').post(f'/api/journal/absences/{note["id"]}/decision', json=dict(
                requestId=rid(), version=note['version'], action=action,
                comment='Принято.' if action == 'approve' else 'Нужна справка, пожалуйста, принесите её в школу.')))
        self.log(f'{len(sent)} absence notes sent')

    def mark_read(self):
        """Everybody has read the old news; only the latest few entries stay unread."""
        logins = school.logins()
        people = [('demo-admin', self.admin)] + [(login, self.as_(login)) for key in ('teachers', 'parents', 'pupils') for login in logins[key]]
        for login, client in people:
            items = self.ok(client.get('/api/notifications/inbox'))['items']
            if len(items) > 3:
                self.ok(client.post('/api/notifications/read', json=dict(throughId=items[3]['id'], read=True)))

    def behaviour_notes(self, day):
        notes = self.behaviour_plan.get(day, [])
        if not notes:
            return
        self.at(day, '18:45')
        for category, pupils, text in notes:
            self.ok(self.teacher('t0').post('/api/behaviour/notes', json=dict(
                requestId=rid(), studentIds=[school.pupil_id(i) for i in pupils], categoryId=category, day=day, text=text)))

    def assessment_bookings(self):
        """Future-dated bookings: the next lessons of three courses."""
        state = self.api.read('school')
        today = date.fromisoformat(self.now[0])
        plans = [('w0-0', 'sor', 'Дроби', 'c0'), ('w1-1', 'sor', 'Имя прилагательное', 'c1'), ('w0-4', 'sor', 'Давление', 'c0')]
        for workload_id, type_id, topic, _ in plans:
            workload = next(w for w in state.workloads if w.id == workload_id)
            placement = next(p for p in state.placements if p.workloadId == workload_id)
            day = today + timedelta(days=3)
            while day.weekday() != placement.day:
                day += timedelta(days=1)
            teacher = self.teacher(workload.teacherId)
            self.ok(teacher.post('/api/assessment-calendar/bookings', json=dict(
                requestId=rid(), groupId=workload.groupId, subjectId=workload.subjectId, day=day.isoformat(),
                lessonId=f'{day.isoformat()}:{placement.id}', typeId=type_id, topic=topic)))
        self.log('assessment bookings made')
