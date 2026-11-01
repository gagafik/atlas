"""Teacher workload by date, week and period: lessons planned, taught, replaced and missed. Administration sees every teacher, a teacher only their own.

Nothing is stored: the figures come from the dated timetable (workload_facts) and the published journals, read in one snapshot."""
from collections import defaultdict
from datetime import date, timedelta
import json
from typing import Literal
from fastapi import HTTPException, Request
from .access import LOCAL
from .attendance_reports import csv_response
from .journal import user_ids
from .notifications import encoded
from .workload_facts import FIELDS, KINDS, add, blank, moment, period_facts

SOURCE = 'dated_schedule_and_published_journals'
ROWS = 100000
UNKNOWN = 'Учитель вне текущего состава'
PUBLISHED = '''SELECT DISTINCT d.lesson_key,json_extract(d.lesson,'$.date'),json_extract(d.lesson,'$.start'),json_extract(d.lesson,'$.end'),json_extract(d.lesson,'$.group'),
    json_extract(d.lesson,'$.groupId'),json_extract(d.lesson,'$.subject'),json_extract(d.lesson,'$.teacherIds')
    FROM journal_coverage_teachers t CROSS JOIN journal_coverage_documents d ON d.workspace=t.workspace AND d.lesson_key=t.lesson_key
    WHERE t.workspace=? AND t.day BETWEEN ? AND ?'''
OUTCOMES = dict(taught='Проведён', merged='Присоединён к другому уроку', upcoming='Предстоит', moved='Перенесён', cancelled='Отменён', replaced='Заменён', uncovered='Без замены')
SPLIT = tuple(k for k in KINDS if k != 'merge')  # what replacements are made of; a merged group is no replacement and has its own figure
LABELS = dict(own='Свой урок', extra='Дополнительный урок', journal='По журналу вне расписания', substitution='Замена', merge='Объединение', self_study='Самостоятельная работа', move='Перенос')
TEACHER_COLUMNS = ['Учитель', 'Начало', 'Конец', 'По расписанию', 'По расписанию на дату', 'Проведено уроков', 'Часы', 'Минуты', 'Свои', 'Замены',
                   *['Из них: ' + LABELS[k].lower() for k in SPLIT], 'Дополнительные', 'По журналу вне расписания', 'Присоединено при объединении', 'Пропущено',
                   'При отсутствии', 'Без замены', 'Отменено', 'Перенесено', 'Предстоит', 'Подтверждено журналом', 'Расхождения', 'Разница с расписанием']


def monday(day): return day - timedelta(days=day.weekday())
def hours(minutes): return f'{minutes / 60:.2f}'.replace('.', ',')


def figures(total):
    return {**{k: total[k] for k in FIELDS}, 'byType': dict(total['byType']), 'delta': total['taught'] - total['plannedToDate']}


def combine(items):
    total = blank()
    for item in items:
        for k in FIELDS: total[k] += item[k]
        for k, v in item['byType'].items(): total['byType'][k] = total['byType'].get(k, 0) + v
    return figures(total)


def numbers(item, first, last):
    """The figure columns of the teachers and weeks views."""
    return [first, last, item['planned'], item['plannedToDate'], item['taught'], hours(item['minutes']), item['minutes'], item['own'], item['substitutions'],
            *[item['byType'].get(k, 0) for k in SPLIT], item['extra'], item['journalOnly'], item['merged'], item['missed'], item['missedAbsent'], item['uncovered'],
            item['cancelled'], item['moved'], item['upcoming'], item['confirmed'], item['differs'], item['delta']]


class TeacherWorkload:
    def __init__(self, journal, access):
        self.journal, self.access = journal, access

    def scope(self, request, workspace, teacher_id):
        """(account, schedule snapshot, selected teacher ids); None selects the whole school. The server alone decides what an account may see."""
        user = request.state.user or LOCAL
        if user['role'] not in ['admin', 'teacher']: raise HTTPException(403, 'Отчёт о нагрузке доступен администрации и самому учителю.')
        snap = self.journal.snapshots.get(workspace)
        # An administrator may also open a teacher who has left the roster, if the period still holds his lessons (checked once they are counted).
        if user['role'] == 'admin': return user, snap, {teacher_id} if teacher_id else None
        own = user_ids(user, workspace, 'teacher') & set(snap.entities['teacher'])
        if not own: raise HTTPException(403, 'Нет назначения преподавателем.')
        if teacher_id and teacher_id not in own: raise HTTPException(404, 'Учитель не найден или недоступен.')
        return user, snap, {teacher_id} if teacher_id else own

    def period(self, snap, start, end):
        settings = snap.state.settings
        if end < start or (end - start).days > 365: raise HTTPException(422, 'Период: начало не позже конца, не более 366 дней.')
        first, last = max(start, settings.yearStart), min(end, settings.yearEnd)
        if first > last: raise HTTPException(422, 'Период вне учебного года.')
        return first, last

    def read(self, user, snap, workspace, operation):
        """operation(db) in one read-only snapshot that still answers for this account and schedule revision."""
        def run(db):
            self.journal.recheck_read(db, user, snap, workspace)
            return operation(db)
        return self.journal.reading(run)

    def published(self, user, snap, workspace, first, last):
        """Lesson key -> the publication of the lesson with its date, time and teachers; every teacher, so own figures never differ from the administration's."""
        rows = self.read(user, snap, workspace, lambda db: db.execute(PUBLISHED, (workspace, first.isoformat(), last.isoformat())).fetchall())
        return {r[0]: dict(date=r[1], start=r[2], end=r[3], group=r[4], groupId=r[5], subject=r[6], teacherIds=json.loads(r[7])) for r in rows}

    def calculate(self, user, snap, workspace, selected, first, last, weeks=False, lessons=False):
        """Rows per teacher: figures, and on demand weeks (every week of the period) and lessons."""
        published = self.published(user, snap, workspace, first, last)
        names = {t.id: t.name for t in snap.state.teachers}
        totals, by_week, listed, count = defaultdict(blank), defaultdict(lambda: defaultdict(blank)), defaultdict(list), 0
        for f in period_facts(snap.history, first, last, moment(), published, names):
            t = f['teacherId']
            if selected is not None and t not in selected: continue
            add(totals[t], f)
            if weeks: add(by_week[t][monday(date.fromisoformat(f['date']))], f)
            if lessons:
                count += 1
                if count > ROWS: raise HTTPException(422, 'Более 100 000 уроков: сократите период выгрузки.')
                listed[t].append({k: f[k] for k in ['date', 'start', 'end', 'group', 'subject', 'minutes', 'outcome', 'kind', 'confirmed', 'differs', 'absent']})
        if selected is not None and any(t not in totals and t not in snap.entities['teacher'] for t in selected): raise HTTPException(404, 'Учитель не найден или недоступен.')
        chosen = sorted(selected) if selected is not None else [t for t in totals if any(v for k, v in totals[t].items() if k != 'byType')]
        starts = [monday(first) + timedelta(days=7 * i) for i in range((monday(last) - monday(first)).days // 7 + 1)]
        result = []
        for t in sorted(chosen, key=lambda t: (names.get(t, UNKNOWN), t)):
            item = dict(teacherId=t, name=names.get(t, UNKNOWN), **figures(totals[t]))
            if weeks: item['weeks'] = [dict(start=max(w, first).isoformat(), end=min(w + timedelta(days=6), last).isoformat(), **figures(by_week[t][w])) for w in starts]
            if lessons: item['lessons'] = sorted(listed[t], key=lambda x: (x['date'], x['start'], x['group'], x['subject']))
            result.append(item)
        return result

    def register(self, app, Workspace):
        @app.get('/api/staff-time/workload/options')
        def options(request: Request, workspace: Workspace = 'school'):
            user, snap, selected = self.scope(request, workspace, '')
            periods = self.read(user, snap, workspace, lambda db: db.execute('SELECT id,name,start_day,end_day FROM journal_periods WHERE workspace=? ORDER BY start_day,id', (workspace,)).fetchall())
            settings = snap.state.settings
            return dict(canSeeAll=user['role'] == 'admin', teachers=[dict(id=t.id, name=t.name) for t in snap.state.teachers if selected is None or t.id in selected],
                        yearStart=settings.yearStart, yearEnd=settings.yearEnd, today=moment()[0].isoformat(),
                        periods=[dict(id=r[0], name=r[1], start=r[2], end=r[3]) for r in periods])

        @app.get('/api/staff-time/workload')
        def report(start: date, end: date, request: Request, teacherId: str = '', workspace: Workspace = 'school'):
            user, snap, selected = self.scope(request, workspace, teacherId)
            first, last = self.period(snap, start, end)
            teachers = self.calculate(user, snap, workspace, selected, first, last, weeks=selected is not None, lessons=selected is not None)
            clock = moment()
            return dict(revision=snap.state.revision, start=first.isoformat(), end=last.isoformat(), asOf=f'{clock[0].isoformat()} {clock[1]}', source=SOURCE,
                        teachers=teachers, totals=combine(teachers))

        @app.get('/api/staff-time/workload/export')
        def export(start: date, end: date, request: Request, teacherId: str = '', view: Literal['teachers', 'weeks', 'lessons'] = 'teachers', workspace: Workspace = 'school'):
            user, snap, selected = self.scope(request, workspace, teacherId)
            first, last = self.period(snap, start, end)
            teachers = self.calculate(user, snap, workspace, selected, first, last, weeks=view == 'weeks', lessons=view == 'lessons')
            a, b = first.isoformat(), last.isoformat()
            if view == 'teachers':
                rows = [TEACHER_COLUMNS] + [[t['name'], *numbers(t, a, b)] for t in teachers]
                if selected is None: rows.append(['Итого', *numbers(combine(teachers), a, b)])
            elif view == 'weeks':
                rows = [['Учитель', 'Неделя с', 'Неделя по', *TEACHER_COLUMNS[3:]]] + [[t['name'], *numbers(w, w['start'], w['end'])] for t in teachers for w in t['weeks']]
            else:
                rows = [['Учитель', 'Дата', 'Начало', 'Конец', 'Группа', 'Предмет', 'Минуты', 'Итог', 'Вид', 'Подтверждено журналом', 'Расхождение']] + [
                    [t['name'], x['date'], x['start'], x['end'], x['group'], x['subject'], x['minutes'], OUTCOMES[x['outcome']], LABELS.get(x['kind'], ''),
                     'Да' if x['confirmed'] else 'Нет', 'Да' if x['differs'] else 'Нет'] for t in teachers for x in t['lessons']]
            if len(rows) > ROWS: raise HTTPException(422, 'Более 100 000 строк: сократите период выгрузки.')
            response = csv_response(rows)
            response.headers['Content-Disposition'] = f'attachment; filename="teacher-workload-{a}-{b}-{view}.csv"'
            with self.access.lock, self.access.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                self.journal.recheck_account(db, user)
                self.access.record(db, 'staff-time.workload_exported', workspace, detail=encoded(dict(start=a, end=b, teacherId=teacherId, view=view)), principal=user)
            return response
