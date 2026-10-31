"""Approved staff leave that is not yet a teacher absence in the timetable, and lessons still left without a substitute.

The feed only reads. The absence itself goes through the ordinary preview and apply of the school document, so it has history, undo and a revision check."""
from datetime import date, timedelta
from . import staff_time
from .calendar_ops import DayContext, dates, resolve_day
from .journal import user_ids
from .notifications import has_table

NAME = 'Отсутствие по заявке'
LEAVES = "SELECT id,employee_id,start_date,end_date,start_min,end_min,status FROM staff_time_leave WHERE workspace=? AND status IN ('approved','cancelled') AND end_date>=? ORDER BY start_date,seq"
HORIZON = 31


def today(): return staff_time.today()
def clock(minutes): return '23:59' if minutes >= 1440 else f'{minutes // 60:02d}:{minutes % 60:02d}'


class Lessons:
    """Lessons that are still held, by date; one resolved day is kept for every date asked."""
    def __init__(self, state):
        # Duties and meetings are not lessons: their rows are not worth resolving.
        self.state, self.ctx, self.days = state.model_copy(update=dict(commitments=[])), None, {}

    def rows(self, day):
        if day not in self.days:
            if self.ctx is None: self.ctx = DayContext(self.state)
            self.days[day] = [r for r in resolve_day(self.state, day, self.ctx)[0] if r['activity'] == 'lesson' and r['status'] != 'cancelled' and self.ctx.maps['groups'][r['groupId']].kind != 'staff']
        return self.days[day]

    def count(self, teacher_id, day, start, end):
        return sum(teacher_id in r['teacherIds'] and r['start'] < end and start < r['end'] for r in self.rows(day))


def teachers_of(db, access, owner, workspace, known):
    """The teacher entities an account is linked to in this workspace; none for staff without a teaching link."""
    row = db.execute('SELECT * FROM accounts WHERE id=?', (owner,)).fetchone()
    person = access.public(row) if row else None
    return sorted(user_ids(person, workspace, 'teacher') & known) if person and person['active'] else []


def made_by(stored, leave_id):
    """The stored absences a leave made: its own id, or with an index when the account then had several teachers."""
    base = 'leave-' + leave_id
    return [a for id, a in stored.items() if id == base or id.startswith(base + '-') and id[len(base) + 1:].isdigit()]


def feed(db, access, state, workspace, day):
    """pending: approved leave to add as an absence; withdrawn: absences of a cancelled leave; uncovered: lessons of absent teachers not yet replaced."""
    names, settings, lessons = {t.id: t.name for t in state.teachers}, state.settings, Lessons(state)
    stored, linked, pending, withdrawn = {a.id: a for a in state.absences}, {}, [], []
    # A leave never exposes its code or note: the neutral name is all the timetable gets.
    for id, owner, start_date, end_date, start_min, end_min, status in (db.execute(LEAVES, (workspace, (day - timedelta(days=HORIZON)).isoformat())).fetchall() if has_table(db, 'staff_time_leave') else []):
        if status == 'cancelled':
            # Found by id, not through the account's present teacher link: a deactivated or relinked account must not leave its absence behind.
            withdrawn += [dict(leaveId=id, absenceId=a.id, teacherId=a.teacherId, teacher=names.get(a.teacherId, ''), start=a.start.isoformat(), end=a.end.isoformat()) for a in made_by(stored, id)]
            continue
        if owner not in linked: linked[owner] = teachers_of(db, access, owner, workspace, names.keys())
        for i, teacher_id in enumerate(linked[owner]):
            absence_id = 'leave-' + id + ('' if len(linked[owner]) == 1 else f'-{i}')
            start, end = max(date.fromisoformat(start_date), settings.yearStart), min(date.fromisoformat(end_date), settings.yearEnd)
            start_time, end_time = clock(start_min), clock(end_min)
            if start > end or start_time >= end_time or absence_id in stored: continue
            if any(a.teacherId == teacher_id and a.start <= start and a.end >= end and a.startTime <= start_time and a.endTime >= end_time for a in state.absences): continue
            pending.append(dict(leaveId=id, teacherId=teacher_id, teacher=names[teacher_id], start=start.isoformat(), end=end.isoformat(), startTime=start_time, endTime=end_time,
                                lessons=sum(lessons.count(teacher_id, d, start_time, end_time) for d in dates(start, end)),
                                absence=dict(id=absence_id, name=NAME, teacherId=teacher_id, start=start.isoformat(), end=end.isoformat(), startTime=start_time, endTime=end_time)))
    uncovered, horizon = [], min(day + timedelta(days=HORIZON), settings.yearEnd)
    for a in state.absences:
        if a.end < day: continue
        days = [dict(date=d.isoformat(), lessons=n) for d in dates(max(a.start, day, settings.yearStart), min(a.end, horizon)) if (n := lessons.count(a.teacherId, d, a.startTime, a.endTime))]
        if days: uncovered.append(dict(absenceId=a.id, teacherId=a.teacherId, teacher=names.get(a.teacherId, ''), days=days))
    uncovered.sort(key=lambda x: (x['days'][0]['date'], x['absenceId']))
    return dict(revision=state.revision, today=day.isoformat(), pending=pending, withdrawn=withdrawn, uncovered=uncovered)


def register(app, Workspace, read, access):
    @app.get('/api/calendar/absence-feed')
    def absence_feed(workspace: Workspace = 'school'):
        state = read(workspace)
        with access.read_connect() as db:
            db.execute('BEGIN')
            return feed(db, access, state, workspace, today())
