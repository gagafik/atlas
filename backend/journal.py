"""Dated academic journal, with independent revisions and published projections."""
from datetime import date
import hashlib
import json
from typing import Literal
from zoneinfo import ZoneInfo
from fastapi import HTTPException, Query, Request
from pydantic import ConfigDict, Field, field_validator, model_validator
from .models import Model
from .access import LOCAL, now
from .journal_reads import SCHEMA, read as snapshot_read
from .attendance_projection import late_share
from . import absence_codes
from .calendar_ops import resolve_day, DayContext
from .notifications import encoded
from .assessment_categories import SHAPE, DEFAULTS, tally, merge, policy_for, composite, breakdown, config as category_config


class Assessment(Model):
    id: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=100)
    kind: Literal['points', 'label'] = 'points'
    maxScore: float = Field(default=10, gt=0, le=10000, allow_inf_nan=False)
    weight: float = Field(default=1, ge=0, le=100, allow_inf_nan=False)
    # Empty means "the school default": save_entry fills it from the catalog (assessment_categories.resolve_content).
    category: str = Field(default='', max_length=40)
    bookingId: str | None = Field(default=None, pattern=r'^[a-f0-9]{32}$')
    @field_validator('category', mode='before')
    @classmethod
    def category_shape(cls, value):
        if not isinstance(value,str) or not SHAPE.fullmatch(value): raise ValueError('Категория работы указана неверно.')
        return value
    @field_validator('maxScore', 'weight', mode='before')
    @classmethod
    def number(cls, value, info):
        # Say what is wrong in Russian; the Field limits stay as a backstop.
        points = info.field_name=='maxScore'
        if isinstance(value,bool) or not isinstance(value,(int,float)) or not (0<value<=10000 if points else 0<=value<=100):
            raise ValueError('Максимум баллов — число больше 0 и не больше 10000.' if points else 'Вес работы — число от 0 до 100.')
        return value


class Grade(Model):
    assessmentId: str
    score: float | None = Field(default=None, ge=0, le=10000, allow_inf_nan=False)
    label: str = Field(default='', max_length=30)
    comment: str = Field(default='', max_length=1000)


class PupilRecord(Model):
    studentId: str
    attendance: Literal['unmarked','present','absent','late','excused','left'] = 'unmarked'
    lateMinutes: int = Field(default=0, ge=0, le=600)
    # Early leave carries the time the pupil left; a teacher's absence type is a label on 'absent' and never excuses (only an accepted note does).
    leftAt: str | None = Field(default=None, pattern=r'^(?:[01][0-9]|2[0-3]):[0-5][0-9]$')
    absenceCode: str = Field(default='', max_length=40)
    comment: str = Field(default='', max_length=1000)
    grades: list[Grade] = Field(default_factory=list, max_length=10)


class MaterialReference(Model):
    # Responses include server snapshots. Accept them on round-trip, but resolve
    # every reference again inside store_revision; never trust submitted text/files.
    model_config = ConfigDict(extra='ignore')
    id: str = Field(pattern=r'^[a-f0-9]{32}$')
    version: int = Field(ge=1)


class TopicReference(Model):
    model_config = ConfigDict(extra='ignore')
    planId: str = Field(pattern=r'^[a-f0-9]{32}$')
    version: int = Field(ge=1)
    topicId: str = Field(pattern=r'^[a-zA-Z0-9_-]{1,80}$')
    units: int = Field(default=1, ge=1, le=12)
    @field_validator('units', mode='before')
    @classmethod
    def whole_hours(cls, value):
        if isinstance(value,bool) or not isinstance(value,int) or not 1<=value<=12:raise ValueError('Часов по теме в уроке — целое число от 1 до 12.')
        return value


class Content(Model):
    topic: str = Field(default='', max_length=2000)
    homework: str = Field(default='', max_length=4000)
    dueDate: date | None = None
    homeworkFiles: list[str] = Field(default_factory=list, max_length=5)
    materials: list[MaterialReference] = Field(default_factory=list, max_length=10)
    curriculum: TopicReference | None = None
    homeworkLatePolicy: Literal['allow','block'] = 'allow'
    homeworkSubmission: Literal['online','offline'] = 'online'
    assessments: list[Assessment] = Field(default_factory=list, max_length=10)
    records: list[PupilRecord] = Field(default_factory=list, max_length=1000)

    @model_validator(mode='after')
    def valid_grades(self):
        assessments = {a.id:a for a in self.assessments}
        if len(assessments) != len(self.assessments): raise ValueError('Проверочные работы не должны повторяться.')
        linked = [a.bookingId for a in self.assessments if a.bookingId]
        if len(set(linked)) != len(linked): raise ValueError('Одна работа календаря связана с несколькими работами журнала.')
        if len({r.studentId for r in self.records}) != len(self.records): raise ValueError('Ученик указан несколько раз.')
        for record in self.records:
            if record.attendance != 'late' and record.lateMinutes: raise ValueError('Минуты опоздания указываются только при опоздании.')
            if (record.attendance == 'left') != bool(record.leftAt): raise ValueError('Для отметки «Ушёл раньше» укажите время ухода; при других отметках время не указывается.')
            if record.absenceCode and record.attendance != 'absent': raise ValueError('Тип отсутствия указывается только при отметке «Отсутствует».')
            if len({g.assessmentId for g in record.grades}) != len(record.grades): raise ValueError('Повтор оценки по одной работе.')
            for grade in record.grades:
                assessment = assessments.get(grade.assessmentId)
                if not assessment: raise ValueError('Оценка ссылается на отсутствующую работу.')
                if assessment.kind == 'points' and (grade.label or grade.score is not None and grade.score > assessment.maxScore):
                    raise ValueError('Баллы должны быть от нуля до максимума работы, без текстовой оценки.')
                if assessment.kind == 'label' and grade.score is not None: raise ValueError('Для текстовой оценки не задаются баллы.')
        if self.dueDate and not self.homework: raise ValueError('Срок относится к домашнему заданию.')
        if self.homeworkFiles and not self.homework.strip(): raise ValueError('Добавьте описание задания к материалам.')
        if self.homeworkLatePolicy=='block' and (not self.dueDate or not self.homework.strip()):
            raise ValueError('Для закрытия приёма укажите задание и срок.')
        if self.homeworkSubmission=='offline' and not self.homework.strip(): raise ValueError('Способ сдачи относится к домашнему заданию.')
        if self.homeworkSubmission=='offline' and self.homeworkLatePolicy=='block': raise ValueError('Закрыть приём после срока можно только при онлайн-сдаче.')
        return self


class SaveEntry(Model):
    lessonKey: str = Field(min_length=64, max_length=64)
    day: date
    revision: int = Field(ge=0)
    version: int = Field(ge=0)
    content: Content
    publish: bool = False
    syncRoster: bool = False


class Withdraw(Model):
    lessonKey: str = Field(min_length=64, max_length=64)
    version: int = Field(ge=1)


def today():return now().astimezone(ZoneInfo('Asia/Qyzylorda')).date()
def minute():return now().astimezone(ZoneInfo('Asia/Qyzylorda')).strftime('%H:%M')


def begun(lesson):
    day = today().isoformat()
    return lesson['date']<day or lesson['date']==day and lesson['start']<=minute()


def has_marks(content):
    """Attendance or grades exist only after the lesson took place."""
    return any(r['attendance']!='unmarked' or r['lateMinutes'] or any(g['score'] is not None or g['label'] for g in r['grades']) for r in content['records'])


def for_editor(record):
    """The entry as the lesson editor opens it. Once a lesson has begun and nobody is marked, everybody shows as present
    (absent where a school note covers the lesson). This is only shown: nothing is stored until the teacher saves, so coverage
    and reports stay as they are. Only the two editor routes call it; stored documents never carry the default."""
    lesson,content = record['document']['lesson'],record['document']['content']
    started = begun(lesson)
    result = record|dict(lessonBegun=started,attendanceDefaulted=False)
    if not (started and record['writable'] and not record['needsSync'] and content['records'] and all(r['attendance']=='unmarked' for r in content['records'])): return result
    enrolled = {s['id'] for s in lesson['students'] if s.get('enrolled',True)}
    records = [r|dict(attendance='absent' if r['studentId'] in record['attendanceApprovals'] else 'present') if r['studentId'] in enrolled else r for r in content['records']]
    return result|dict(attendanceDefaulted=True,document=dict(record['document'],content=dict(content,records=records)))


def check_attendance(db, workspace, role, document, previous):
    """Marks a save may newly introduce. What the pupil's earlier saved records already hold still round-trips unchanged:
    an old raw 'excused', or a code the school has since switched off."""
    lesson = document['lesson']
    names = {s['id']:s['name'] for s in lesson['students']}
    before = {}
    for saved in [previous['published'],previous['document']] if previous else []:
        for r in saved['content']['records'] if saved else []: before.setdefault(r['studentId'],[]).append(r)
    for record in document['content']['records']:
        old = before.get(record['studentId'],[])
        if record['attendance']=='excused' and not any(o['attendance']=='excused' for o in old):
            raise HTTPException(422,'Уважительную причину даёт принятая объяснительная. Отметьте «Отсутствует» и укажите тип; записку вносит классный руководитель или администратор.')
        if record['attendance']=='left' and not lesson['start']<record['leftAt']<lesson['end']:
            raise HTTPException(422,f"Время ухода ученика {names[record['studentId']]} должно быть внутри урока.")
        code = record['absenceCode']
        if code and not any(o.get('absenceCode')==code for o in old): absence_codes.check(db,workspace,code,role)


def frozen(published, current, day):
    """A publication of an earlier day keeps its roster, time and teachers; a dated move to another date still asks for review."""
    return bool(published and published['date']<day.isoformat() and (current is None or current['date']==published['date']))


def key_for(row):
    # Changing the subject/group creates another journal, never reassigns old marks.
    return hashlib.sha256(encoded([row['id'],row['subjectId'],row['groupId']]).encode()).hexdigest()


def snapshot(state, row):
    classes = {c.id:c.name for c in state.classes}
    # Rows of an earlier day carry the class each pupil had then.
    dated = row.get('studentClasses',{})
    return {key:row[key] for key in ['date','start','end','subjectId','subject','groupId','group','teacherIds','teachers','rooms','status','rosterComplete']} | dict(
        sourceKey=row['id'],students=[dict(id=s.id,name=s.name,classId=dated[s.id][0] if s.id in dated else s.classId,className=dated[s.id][1] if s.id in dated else classes[s.classId],enrolled=True)
                                    for s in state.students if s.id in row['studentIds']])


def user_ids(user, workspace, view):
    return {id for g in user['grants'] if g['workspace']==workspace and g['view']==view for id in g['entityIds']}


def staff_read(user, workspace, lesson):
    return user['role']=='admin' or user['role']=='teacher' and bool(user_ids(user,workspace,'teacher') & set(lesson['teacherIds']))


def current_lesson(state, lesson, rows_for_day):
    source = lesson['sourceKey']
    if source.startswith('extra:'):
        item = next((x for x in state.datedLessons if x.id==source[6:]),None)
        if not item: return None
        day = item.onDate
    else:
        raw, placement_id = source.split(':',1)
        day = date.fromisoformat(raw)
        change = next((x for x in state.datedLessons if x.placementId==placement_id and x.sourceDate==day),None)
        if change: day = change.onDate
    rows = rows_for_day(day)
    return next((r for r in rows if r['activity']=='lesson' and r['id']==source and
                 r['subjectId']==lesson['subjectId'] and r['groupId']==lesson['groupId']),None)


def merged_snapshot(current, old):
    result = dict(current)
    ids = {s['id'] for s in current['students']}
    result['students'] = current['students'] + [dict(s,enrolled=False) for s in old['students'] if s['id'] not in ids]
    return result


def signature(lesson):
    return encoded({k:lesson[k] for k in ['date','start','end','subjectId','groupId','teacherIds','rooms','students','status']})


def pupil_projection(document, student_id):
    record = next((r for r in document['content']['records'] if r['studentId']==student_id),None)
    if record is None: return None
    lesson = document['lesson']
    student = next(s for s in lesson['students'] if s['id']==student_id)
    return dict(student=dict(id=student_id,name=student['name']),
                lesson={k:lesson[k] for k in ['date','start','end','subjectId','subject','group','teachers','rooms']},
                content={k:document['content'][k] for k in ['topic','homework','dueDate']} | dict(
                    # Calendar ids are staff data; the category stays visible.
                    assessments=[{k:v for k,v in a.items() if k!='bookingId'} for a in document['content']['assessments']],
                    homeworkFiles=document['content'].get('homeworkFiles',[]),
                    materials=document['content'].get('materials',[]),
                    curriculum=document['content'].get('curriculum'),
                    homeworkLatePolicy=document['content'].get('homeworkLatePolicy','allow'),
                    homeworkSubmission=document['content'].get('homeworkSubmission','online')),
                # Documents saved before early leave and teacher types lack these keys; without the defaults a republish would look like news.
                record=dict(leftAt=None,absenceCode='')|record)


def summary(rows, cfg=None, classes=None):
    """Attendance and per-subject marks of published rows; classes maps lessonKey to the pupil's class for the share rules."""
    cfg = cfg or DEFAULTS
    classes = classes or {}
    subjects = {}
    attendance = {s:0 for s in ['unmarked','present','absent','late','excused','left']}
    late_minutes = 0
    late_share_sum = 0.0
    for row in rows:
        record,content,lesson = row['record'],row['content'],row['lesson']
        attendance[record['attendance']] += 1
        late_minutes += record['lateMinutes']
        late_share_sum += late_share(record['lateMinutes'],lesson['start'],lesson['end'])
        current = subjects.setdefault(lesson['subjectId'],dict(subjectId=lesson['subjectId'],subject=lesson['subject'],pointsCount=0,labelCount=0,weightedSum=0,weight=0,sums={},classId=None))
        assessments = {a['id']:a for a in content['assessments']}
        for grade in record['grades']:
            a = assessments[grade['assessmentId']]
            if grade['score'] is not None:
                current['pointsCount'] += 1
                current['weightedSum'] += grade['score']/a['maxScore']*100*a['weight']
                current['weight'] += a['weight']
            elif grade['label']: current['labelCount'] += 1
        merge(current['sums'],tally(content['assessments'],record['grades']))
        current['classId'] = classes.get(row.get('lessonKey')) or current['classId']
    result = []
    for s in subjects.values():
        policy = policy_for(cfg,s['subjectId'],s['classId'])
        share = composite(s['sums'],policy)['exact']
        result.append(dict(subjectId=s['subjectId'],subject=s['subject'],pointsCount=s['pointsCount'],labelCount=s['labelCount'],percentage=round(s['weightedSum']/s['weight'],2) if s['weight'] else None,
                           categories=breakdown(s['sums'],cfg,policy['method']),sharePercentage=round(float(share),2) if share is not None else None,rule=policy['ruleName']))
    return dict(attendance=attendance,lateMinutes=late_minutes,lateShare=round(late_share_sum,2),subjects=result)


class Journal:
    def __init__(self, access, notifications, stamp=None):
        self.access,self.notifications = access,notifications
        # One schedule snapshot cache for the journal and every module built on homework.snapshots.
        from .portal_cache import PortalSnapshots
        self.snapshots = PortalSnapshots(access.read,stamp) if stamp else None

    def prepare(self, db):
        self.tables(db);self.access.tables(db)

    def reading(self, operation):
        """Run operation(db) in a read-only WAL snapshot; only schema setup takes the writer."""
        return snapshot_read(self.access,self.prepare,operation)

    def tables(self, db):
        db.execute('CREATE TABLE IF NOT EXISTS journal_entries (workspace TEXT,lesson_key TEXT,version INTEGER,updated TEXT,actor_name TEXT,document TEXT,published_document TEXT,published_version INTEGER,published_at TEXT,PRIMARY KEY(workspace,lesson_key))')
        db.execute('CREATE TABLE IF NOT EXISTS journal_history (id INTEGER PRIMARY KEY,workspace TEXT,lesson_key TEXT,version INTEGER,updated TEXT,actor_name TEXT,action TEXT,document TEXT)')
        db.execute('CREATE INDEX IF NOT EXISTS journal_history_entry ON journal_history(workspace,lesson_key,version)')
        db.execute('CREATE INDEX IF NOT EXISTS journal_versions ON journal_entries(workspace,version)')
        from .grade_feed import tables as grade_tables
        grade_tables(db)
        db.execute("CREATE INDEX IF NOT EXISTS journal_entry_day ON journal_entries(workspace,json_extract(document,'$.lesson.date'))")
        exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='journal_members'").fetchone()
        db.execute('CREATE TABLE IF NOT EXISTS journal_members (workspace TEXT,lesson_key TEXT,student_id TEXT,name TEXT,kind TEXT,PRIMARY KEY(workspace,lesson_key,student_id,kind))')
        db.execute('CREATE INDEX IF NOT EXISTS journal_member_lookup ON journal_members(workspace,student_id,kind)')
        if not exists:
            for field,kind in [('document','draft'),('published_document','published')]:
                db.execute(f"INSERT OR IGNORE INTO journal_members SELECT e.workspace,e.lesson_key,json_extract(s.value,'$.id'),json_extract(s.value,'$.name'),? FROM journal_entries e,json_each(e.{field},'$.lesson.students') s",(kind,))
        from .attendance_projection import tables
        tables(db)
        from .coverage_projection import tables as coverage_tables
        coverage_tables(db)
        from .journal_periods import tables as period_tables
        period_tables(db)
        from .assignment_files import tables as assignment_tables
        assignment_tables(db)
        from .homework_deadlines import tables as deadline_tables
        deadline_tables(db)
        from .homework_queue_projection import tables as homework_queue_tables
        homework_queue_tables(db)
        from .assessment_categories import tables as category_tables
        category_tables(db)
        # Written last: ordinary reads treat a database without it as not yet migrated. A savepoint keeps the
        # caller free to start its own BEGIN IMMEDIATE (a plain INSERT would leave an implicit transaction open).
        if db.execute("SELECT version FROM journal_projection_versions WHERE name='journal_schema'").fetchone()!=(SCHEMA,):
            db.execute('SAVEPOINT journal_schema_marker')
            try:
                db.execute("INSERT OR REPLACE INTO journal_projection_versions VALUES ('journal_schema',?)",(SCHEMA,))
                db.execute('RELEASE journal_schema_marker')
            except Exception:
                db.execute('ROLLBACK TO journal_schema_marker');db.execute('RELEASE journal_schema_marker');raise

    def rows(self, db, workspace, day, keys):
        """Day cards inside a snapshot: only the lesson part of the draft is parsed."""
        # The compact coverage projection carries the published lesson without parsing grade documents.
        query = "SELECT e.lesson_key,e.version,e.published_version,e.published_document IS NOT NULL,json_extract(e.document,'$.lesson'),c.lesson FROM journal_entries e LEFT JOIN journal_coverage_documents c ON c.workspace=e.workspace AND c.lesson_key=e.lesson_key WHERE e.workspace=? AND (json_extract(e.document,'$.lesson.date')=?"
        params = [workspace,day.isoformat()]
        if keys:
            query += ' OR e.lesson_key IN (SELECT value FROM json_each(?))'; params.append(encoded(keys))
        return [dict(lessonKey=r[0],version=r[1],publishedVersion=r[2],hasPublished=bool(r[3]),lesson=json.loads(r[4]),published=json.loads(r[5]) if r[5] else None) for r in db.execute(query+')',params)]

    def members(self, db, workspace, key, document, kind):
        from .coverage_projection import replace as coverage_replace
        coverage_replace(db,workspace,key)
        from .homework_queue_projection import replace as homework_queue_replace
        homework_queue_replace(db,workspace,key)
        from .homework_deadlines import invalidate as invalidate_deadlines
        invalidate_deadlines(db,workspace,key)
        db.execute('DELETE FROM journal_members WHERE workspace=? AND lesson_key=? AND kind=?',(workspace,key,kind))
        if document:
            db.executemany('INSERT INTO journal_members VALUES (?,?,?,?,?)',[(workspace,key,s['id'],s['name'],kind) for s in document['lesson']['students']])
        if kind=='published':
            from .attendance_projection import replace
            replace(db,workspace,key)

    def historical_students(self, db, workspace, user):
        query = 'SELECT DISTINCT m.student_id,m.name FROM journal_members m JOIN journal_entries e ON e.workspace=m.workspace AND e.lesson_key=m.lesson_key WHERE m.workspace=?'
        params = [workspace]
        if user['role']=='teacher':
            query += " AND m.kind='draft' AND EXISTS(SELECT 1 FROM json_each(e.document,'$.lesson.teacherIds') t WHERE t.value IN (SELECT value FROM json_each(?)))"
            params.append(encoded(sorted(user_ids(user,workspace,'teacher'))))
        elif user['role'] in ['student','parent']:
            query += " AND m.kind='published' AND m.student_id IN (SELECT value FROM json_each(?))"
            params.append(encoded(sorted(user_ids(user,workspace,'student'))))
        return [dict(id=r[0],name=r[1]) for r in db.execute(query,params)]

    def panels(self, db, workspace, user):
        """Which management panels have anything for this account. Flags only decide what the page mounts: every endpoint still enforces rights."""
        role = user['role']
        result = dict(finalGrades=role=='admin',absences=role in ['admin','parent'],attendance=role=='admin',attendanceGrid=role in ['admin','teacher'],coverage=role in ['admin','teacher'],periods=role in ['admin','teacher'])
        if role in ['teacher','student','parent']:
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name IN ('grading_rows','final_results')")}
            if role=='teacher' and 'grading_rows' in tables:
                result['finalGrades'] = db.execute('SELECT 1 FROM grading_rows WHERE workspace=? AND assigned=? LIMIT 1',(workspace,user['id'])).fetchone() is not None
            elif role!='teacher' and 'final_results' in tables:
                result['finalGrades'] = db.execute('SELECT 1 FROM final_results WHERE workspace=? AND student_id IN (SELECT value FROM json_each(?)) LIMIT 1',(workspace,encoded(sorted(user_ids(user,workspace,'student'))))).fetchone() is not None
        if role=='student':
            # A pupil reads own notes (summary only); the panel appears once the school or a parent has filed one.
            result['absences'] = bool(db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='absence_notices'").fetchone()
                and db.execute('SELECT 1 FROM absence_notices WHERE workspace=? AND student_id IN (SELECT value FROM json_each(?)) LIMIT 1',(workspace,encoded(sorted(user_ids(user,workspace,'student'))))).fetchone())
        return result

    def unpack(self,row):
        return dict(workspace=row[0],lessonKey=row[1],version=row[2],updated=row[3],actor=row[4],document=json.loads(row[5]),
                    published=json.loads(row[6]) if row[6] else None,publishedVersion=row[7],publishedAt=row[8])

    def scope(self, request, workspace, staff=False):
        user = request.state.user or LOCAL
        allowed = user['role']=='admin' or user['role']=='teacher' and user_ids(user,workspace,'teacher')
        if not staff: allowed = allowed or user['role'] in ['student','parent'] and user_ids(user,workspace,'student')
        if not allowed: raise HTTPException(403,'Нет доступа к учебному журналу этого пространства.')
        return user

    def find(self, workspace, key, db=None):
        if db is None: return self.reading(lambda db:self.find(workspace,key,db))
        row = db.execute('SELECT * FROM journal_entries WHERE workspace=? AND lesson_key=?',(workspace,key)).fetchone()
        return self.unpack(row) if row else None

    def lesson_rows(self, state, workspace):
        """Schedule rows of this revision for any day, resolved against the timetable and rosters valid then."""
        if self.snapshots is None: return lambda day: resolve_day(state,day)[0]
        snap = self.snapshots.get(workspace)
        if snap.state.revision!=state.revision: raise HTTPException(409,'Расписание изменилось. Обновите журнал.')
        return snap.lesson_rows

    def entry(self, state, workspace, user, key, day, sync=False, db=None, rows_for_day=None):
        """Saved journal, lesson and restrictions from one snapshot; rows_for_day is an optional cached schedule."""
        rows_for_day = rows_for_day or self.lesson_rows(state,workspace)
        def read(db):
            existing = self.find(workspace,key,db)
            if existing:
                current = current_lesson(state,existing['document']['lesson'],rows_for_day)
            else:
                rows = rows_for_day(day)
                current = next((r for r in rows if r['activity']=='lesson' and key_for(r)==key),None)
            return self.resolved_entry(state,workspace,user,key,existing,current,sync=sync,db=db)
        return self.reading(read) if db is None else read(db)

    def resolved_entry(self, state, workspace, user, key, existing, current, sync=False, *, db=None):
        """Describe a journal against an already resolved, immutable school snapshot.

        This does not authorize a later write: callers must still recheck account,
        school/journal versions and period locks inside their write transaction.
        An optional initialized connection keeps restriction reads in the caller's
        snapshot; ownership and closing remain with that caller.
        """
        if not current and not existing: raise HTTPException(404,'Урок не найден.')
        if not (current and staff_read(user,workspace,current) or existing and staff_read(user,workspace,existing['document']['lesson'])):
            raise HTTPException(403,'Этот урок не назначен вашей учётной записи.')
        lesson = snapshot(state,current) if current else None
        writable = bool(current and current['status']!='cancelled' and staff_read(user,workspace,current))
        fixed = frozen(existing['published']['lesson'] if existing and existing['published'] else None,current,today())
        if existing:
            document = existing['document']
            needs_sync = False
            if not fixed:
                merged = merged_snapshot(lesson,document['lesson']) if lesson else document['lesson']
                needs_sync = not lesson or signature(document['lesson']) != signature(merged)
                if sync and writable:
                    document = dict(document,lesson=merged)
                    records = {r['studentId']:r for r in document['content']['records']}
                    document['content'] = dict(document['content'],records=[records.get(s['id'],PupilRecord(studentId=s['id']).model_dump(mode='json')) for s in merged['students']])
            result = {k:v for k,v in existing.items() if k!='published'} | dict(document=document,hasPublished=existing['published'] is not None)
        else:
            needs_sync = False
            result = dict(lessonKey=key,version=0,updated=None,actor=None,publishedVersion=None,publishedAt=None,hasPublished=False,
                          document=dict(lesson=lesson,content=Content(records=[PupilRecord(studentId=s['id']) for s in lesson['students']]).model_dump(mode='json')))
        from .absences import approvals
        from .journal_periods import locks
        from .assessment_categories import lesson_bookings
        def restrictions(db):
            excuses=approvals(db,workspace,[result['document']['lesson']]).get(0,{})
            closed=locks(db,workspace,lesson,result['document']['lesson'],existing['published']['lesson'] if existing and existing['published'] else None)
            return excuses,closed,lesson_bookings(db,workspace,key,category_config(db,workspace)),[dict(id=c['id'],name=c['name'],active=c['active']) for c in absence_codes.current(db,workspace)['items']]
        excuses,closed,bookings,codes=self.reading(restrictions) if db is None else restrictions(db)
        return result | dict(periodLocks=closed,attendanceApprovals=excuses,bookings=bookings,absenceCodes=codes,revision=state.revision,writable=writable and not closed,needsSync=needs_sync and not sync,currentDate=lesson['date'] if lesson else None,
                             rosterFrozen=fixed,futureLesson=bool(lesson and lesson['date']>today().isoformat()),
                             canWithdraw=bool(not closed and existing and existing['published'] and staff_read(user,workspace,current or existing['document']['lesson'])))

    def recheck_account(self, db, user):
        if user['id']=='local':
            if db.execute('SELECT 1 FROM accounts LIMIT 1').fetchone(): raise HTTPException(409,'Доступ изменился. Войдите в учётную запись.')
        else:
            row = db.execute('SELECT * FROM accounts WHERE id=? AND active=1',(user['id'],)).fetchone()
            if not row or self.access.public(row)!=user: raise HTTPException(409,'Учётная запись изменилась. Войдите снова.')

    def recheck_read(self, db, user, snap, workspace):
        """A snapshot read answers only for the account and schedule revision it was authorized with."""
        self.recheck_account(db,user)
        if db.execute('SELECT revision FROM workspaces WHERE id=?',(workspace,)).fetchone()!=(snap.state.revision,):
            raise HTTPException(409,'Расписание изменилось. Обновите журнал.')

    def notify(self, db, workspace, version, before, after, key=None):
        if key:
            from .grade_feed import record
            from .journal_notices import deliver_journal
            record(db,workspace,key,before,after)
            return deliver_journal(self.notifications,db,workspace,version,key,before,after)
        ids = {r['studentId'] for d in [before,after] if d for r in d['content']['records']}
        changed = {id for id in ids if (pupil_projection(before,id) if before else None) != (pupil_projection(after,id) if after else None)}
        targets = self.notifications.granted_users(db,workspace,'student',changed,['student','parent'])
        self.notifications.deliver(db,targets,workspace,version,'journal.updated' if after else 'journal.withdrawn')

    def store_revision(self, db, workspace, key, user, revision, version, document, previous, *, publish=False, created=None):
        """Store one authorized, validated revision inside the caller's write transaction."""
        if not db.in_transaction: raise RuntimeError('Journal revisions require a transaction.')
        if document['content'].get('materials'):
            from .teaching_materials import bind
            document=dict(document,content=dict(document['content'],materials=bind(db,document['content']['materials'],workspace,key,user)))
        if document['content'].get('curriculum'):
            from .curriculum import bind as bind_topic
            document=dict(document,content=dict(document['content'],curriculum=bind_topic(db,document['content']['curriculum'],workspace,key,document['lesson'])))
        created=created or now().isoformat()
        old_public=previous['published'] if previous else None
        published=document if publish else old_public
        published_version=version if publish else previous['publishedVersion'] if previous else None
        published_at=created if publish else previous['publishedAt'] if previous else None
        row=(workspace,key,version,created,user['name'],encoded(document),encoded(published) if published else None,published_version,published_at)
        db.execute('INSERT INTO journal_entries VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(workspace,lesson_key) DO UPDATE SET version=excluded.version,updated=excluded.updated,actor_name=excluded.actor_name,document=excluded.document,published_document=excluded.published_document,published_version=excluded.published_version,published_at=excluded.published_at',row)
        action='journal.published' if publish else 'journal.draft_saved'
        self.members(db,workspace,key,document,'draft')
        if publish:self.members(db,workspace,key,published,'published')
        if publish:
            from .curriculum import project
            project(db,workspace,key,published)
        db.execute('INSERT INTO journal_history(workspace,lesson_key,version,updated,actor_name,action,document) VALUES (?,?,?,?,?,?,?)',
                   (workspace,key,version,created,user['name'],action,encoded(document)))
        self.access.record(db,action,workspace,revision,detail=f'{key}: редакция {version}')
        if publish:self.notify(db,workspace,version,old_public,published,key)
        return self.unpack(row)

    def register(self, app, Workspace):
        from .grade_feed import register as grade_feed
        grade_feed(self,app,Workspace)
        from .assessment_categories import register as categories
        categories(self,app,Workspace)
        @app.get('/api/journal/options')
        def options(request: Request,workspace: Workspace='school'):
            user = self.scope(request,workspace)
            snap = self.snapshots.get(workspace);state = snap.state
            allowed_students = user_ids(user,workspace,'student')
            if user['role']=='admin': allowed_students = {s.id for s in state.students}
            elif user['role']=='teacher':
                from .resources import teacher_ids
                teachers = user_ids(user,workspace,'teacher')
                groups = {w.groupId for w in state.workloads if teachers & set(teacher_ids(w))}
                allowed_students = {s.id for s in state.students if any(g.id in groups and (s.classId in g.classIds if g.kind=='whole' else s.id in g.studentIds) for g in state.groups)}
            students = {s.id:dict(id=s.id,name=s.name) for s in state.students if s.id in allowed_students}
            def read(db):
                self.recheck_read(db,user,snap,workspace)
                return self.historical_students(db,workspace,user),self.panels(db,workspace,user)
            historical,panels = self.reading(read)
            for student in historical: students.setdefault(student['id'],student)
            return dict(staff=user['role'] in ['admin','teacher'],absenceNotices=panels['absences'],panels=panels,yearStart=state.settings.yearStart,yearEnd=state.settings.yearEnd,students=sorted(students.values(),key=lambda s:s['name']))

        @app.get('/api/journal/day')
        def day_view(request: Request,day: date,workspace: Workspace='school'):
            user = self.scope(request,workspace,staff=True)
            snap = self.snapshots.get(workspace);state = snap.state
            rows = snap.lesson_rows(day)
            keys = [key_for(r) for r in rows if r['activity']=='lesson']
            def read(db):
                self.recheck_read(db,user,snap,workspace)
                return {r['lessonKey']:r for r in self.rows(db,workspace,day,keys)}
            saved = self.reading(read)
            result = []
            for row in rows:
                if row['activity']!='lesson': continue
                key = key_for(row)
                record = saved.get(key)
                if not (staff_read(user,workspace,row) or record and staff_read(user,workspace,record['lesson'])): continue
                lesson,count,fixed = {k:row[k] for k in ['date','start','end','subject','group','teachers','rooms','status']},len(row['studentIds']),False
                if record and frozen(record['published'],row,today()) and record['published']['date']==day.isoformat():
                    # An earlier day keeps the time, teachers and roster it was published with.
                    published = record['published']
                    lesson,count,fixed = {k:published[k] for k in ['date','start','end','subject','group','teachers','rooms']}|dict(status=row['status']),sum(bool(s.get('enrolled',True)) for s in published['students']),True
                result.append(dict(lessonKey=key,lesson=lesson,studentCount=count,version=record['version'] if record else 0,publishedVersion=record['publishedVersion'] if record else None,hasPublished=bool(record and record['hasPublished']),archived=False,frozen=fixed))
            present = {r['lessonKey'] for r in result}
            for key,record in saved.items():
                lesson = record['lesson']
                if key not in present and lesson['date']==day.isoformat() and staff_read(user,workspace,lesson):
                    result.append(dict(lessonKey=key,lesson={k:lesson[k] for k in ['date','start','end','subject','group','teachers','rooms','status']},studentCount=len(lesson['students']),version=record['version'],publishedVersion=record['publishedVersion'],hasPublished=record['hasPublished'],archived=True,frozen=False))
            result.sort(key=lambda r:(r['lesson']['start'],r['lesson']['group'],r['lesson']['subject']))
            return dict(revision=state.revision,lessons=result)

        @app.get('/api/journal/entry')
        def get_entry(request: Request,lessonKey: str,day: date,syncRoster: bool=False,workspace: Workspace='school'):
            user = self.scope(request,workspace,staff=True)
            snap = self.snapshots.get(workspace)
            def read(db):
                self.recheck_read(db,user,snap,workspace)
                return self.entry(snap.state,workspace,user,lessonKey,day,syncRoster,db=db,rows_for_day=snap.lesson_rows)
            return for_editor(self.reading(read))

        @app.put('/api/journal/entry')
        def save_entry(body: SaveEntry,request: Request,workspace: Workspace='school'):
            user = self.scope(request,workspace,staff=True)
            snap = self.snapshots.get(workspace);state = snap.state
            if state.revision!=body.revision: raise HTTPException(409,'Расписание изменилось. Загрузите урок заново.')
            if not state.settings.yearStart <= body.day <= state.settings.yearEnd: raise HTTPException(422,'Дата вне учебного года.')
            record = self.entry(state,workspace,user,body.lessonKey,body.day,body.syncRoster,rows_for_day=snap.lesson_rows)
            if record['version']!=body.version: raise HTTPException(409,'Журнал уже изменён в другом окне. Загрузите запись заново.')
            if not record['writable']: raise HTTPException(403,'Период закрыт или урок доступен только для просмотра. Обновите журнал для подробностей.')
            if record['currentDate']!=body.day.isoformat(): raise HTTPException(409,'Урок перенесён на другую дату. Загрузите его заново.')
            if record['needsSync']: raise HTTPException(409,'Изменилось время или состав урока. Сначала просмотрите обновление состава.')
            expected = {s['id'] for s in record['document']['lesson']['students']}
            if {r.studentId for r in body.content.records}!=expected: raise HTTPException(422,'Список записей учеников должен совпадать с составом журнала.')
            document = dict(lesson=record['document']['lesson'],content=body.content.model_dump(mode='json'))
            if body.publish and document['lesson']['date']>today().isoformat() and has_marks(document['content']):
                raise HTTPException(422,'Урок ещё не прошёл. Посещаемость и оценки публикуются в день урока или позже; тему, домашнее задание и материалы можно опубликовать заранее без отметок.')
            created,version = now().isoformat(),body.version+1
            with self.access.lock,self.access.connect() as db:
                self.tables(db);self.access.tables(db)
                if not db.in_transaction: db.execute('BEGIN IMMEDIATE')
                self.recheck_account(db,user)
                revision = db.execute('SELECT revision FROM workspaces WHERE id=?',(workspace,)).fetchone()
                existing = db.execute('SELECT * FROM journal_entries WHERE workspace=? AND lesson_key=?',(workspace,body.lessonKey)).fetchone()
                previous = self.unpack(existing) if existing else None
                if not revision or revision[0]!=body.revision or (previous['version'] if previous else 0)!=body.version: raise HTTPException(409,'Данные изменились во время сохранения. Обновите журнал.')
                old_public = previous['published'] if previous else None
                from .journal_periods import require_open
                require_open(db,workspace,document['lesson'],previous['document']['lesson'] if previous else None,old_public['lesson'] if old_public else None)
                check_attendance(db,workspace,user['role'],document,previous)
                from .assessment_categories import resolve_content
                document['content']=resolve_content(db,workspace,body.lessonKey,document['content'],previous,user)
                from .assignment_files import bind
                bind(db,body.content.homeworkFiles,workspace,body.lessonKey,user['id'])
                self.store_revision(db,workspace,body.lessonKey,user,state.revision,version,document,previous,publish=body.publish,created=created)
            return for_editor(self.entry(state,workspace,user,body.lessonKey,body.day,rows_for_day=snap.lesson_rows))

        @app.post('/api/journal/withdraw')
        def withdraw(body: Withdraw,request: Request,workspace: Workspace='school'):
            user = self.scope(request,workspace,staff=True)
            snap = self.snapshots.get(workspace);state = snap.state
            with self.access.lock,self.access.connect() as db:
                self.tables(db);self.access.tables(db)
                if not db.in_transaction: db.execute('BEGIN IMMEDIATE')
                self.recheck_account(db,user)
                row = db.execute('SELECT * FROM journal_entries WHERE workspace=? AND lesson_key=?',(workspace,body.lessonKey)).fetchone()
                if not row: raise HTTPException(404,'Запись не найдена.')
                record = self.unpack(row)
                # The timetable of that day decides who teaches the lesson; the saved draft only when the lesson is gone.
                current = snap.current_lesson(record['document']['lesson'])
                if not staff_read(user,workspace,current or record['document']['lesson']): raise HTTPException(403,'Нет доступа к этой записи.')
                if record['version']!=body.version: raise HTTPException(409,'Запись изменилась. Обновите журнал.')
                if not record['published']: raise HTTPException(409,'Запись уже скрыта.')
                revision=db.execute('SELECT revision FROM workspaces WHERE id=?',(workspace,)).fetchone()
                if not revision or revision[0]!=state.revision:raise HTTPException(409,'Расписание изменилось. Обновите журнал.')
                from .journal_periods import require_open
                require_open(db,workspace,record['document']['lesson'],record['published']['lesson'],current)
                updated,version = now().isoformat(),body.version+1
                db.execute('UPDATE journal_entries SET version=?,updated=?,actor_name=?,published_document=NULL,published_version=NULL,published_at=NULL WHERE workspace=? AND lesson_key=?',(version,updated,user['name'],workspace,body.lessonKey))
                db.execute('INSERT INTO journal_history(workspace,lesson_key,version,updated,actor_name,action,document) VALUES (?,?,?,?,?,?,?)',(workspace,body.lessonKey,version,updated,user['name'],'journal.withdrawn',encoded(record['document'])))
                self.access.record(db,'journal.withdrawn',workspace,detail=f'{body.lessonKey}: редакция {version}')
                self.members(db,workspace,body.lessonKey,None,'published')
                from .curriculum import project
                project(db,workspace,body.lessonKey,None)
                self.notify(db,workspace,version,record['published'],None,body.lessonKey)
            return dict(version=version)

        @app.get('/api/journal/history')
        def history(request: Request,lessonKey: str,workspace: Workspace='school',version: int | None=None,beforeVersion: int | None=None):
            user = self.scope(request,workspace,staff=True)
            snap = self.snapshots.get(workspace)
            def read(db):
                self.recheck_read(db,user,snap,workspace)
                record = self.find(workspace,lessonKey,db)
                if not record: raise HTTPException(404,'Запись не найдена.')
                current = current_lesson(snap.state,record['document']['lesson'],snap.lesson_rows)
                if not (staff_read(user,workspace,record['document']['lesson']) or current and staff_read(user,workspace,current)):
                    raise HTTPException(403,'Нет доступа к этой записи.')
                query = 'SELECT version,updated,actor_name,action,document FROM journal_history WHERE workspace=? AND lesson_key=?'
                params = [workspace,lessonKey]
                if version is not None: query += ' AND version=?'; params.append(version)
                if beforeVersion is not None: query += ' AND version<?'; params.append(beforeVersion)
                return db.execute(query+' ORDER BY version DESC LIMIT 50',params).fetchall()
            rows = self.reading(read)
            return [dict(version=r[0],updated=r[1],actor=r[2],action=r[3],**(dict(document=json.loads(r[4])) if version is not None else {})) for r in rows]

        @app.get('/api/journal/report')
        def report(request: Request,studentId: str,start: date,end: date,workspace: Workspace='school',
                   after: str | None=Query(default=None,pattern=r'^\d{4}-\d{2}-\d{2}\|\d{2}:\d{2}\|[a-f0-9]{64}$'),limit: int=Query(default=100,ge=1,le=200)):
            """Published rows by (date, start, lessonKey). The first page also carries the summary of the whole period."""
            user = self.scope(request,workspace)
            if end<start or (end-start).days>366: raise HTTPException(422,'Выберите период до 367 дней.')
            if user['role'] in ['student','parent'] and studentId not in user_ids(user,workspace,'student'):
                raise HTTPException(403,'Этот ученик не назначен вашей учётной записи.')
            from .absences import approvals
            def read(db):
                self.recheck_account(db,user)
                cfg = category_config(db,workspace);classes = {}
                codes = absence_codes.names(db,workspace)
                # The attendance projection holds one row per published record, so it drives the range scan.
                query = 'SELECT e.lesson_key,e.published_document,e.published_version,e.published_at,a.day,a.start FROM journal_attendance a JOIN journal_entries e ON e.workspace=a.workspace AND e.lesson_key=a.lesson_key WHERE a.workspace=? AND a.student_id=? AND a.day BETWEEN ? AND ? AND e.published_document IS NOT NULL'
                params = [workspace,studentId,start.isoformat(),end.isoformat()]
                if user['role']=='teacher':
                    query += " AND EXISTS(SELECT 1 FROM json_each(e.published_document,'$.lesson.teacherIds') t WHERE t.value IN (SELECT value FROM json_each(?)))"
                    params.append(encoded(sorted(user_ids(user,workspace,'teacher'))))
                if after:
                    query += ' AND (a.day,a.start,a.lesson_key)>(?,?,?)'; params += after.split('|')
                query += ' ORDER BY a.day,a.start,a.lesson_key'
                if after: query += ' LIMIT ?'; params.append(limit+1)
                fetched = db.execute(query,params).fetchall()
                more = bool(after) and len(fetched)>limit
                if more: fetched = fetched[:limit]
                rows,marks = [],[]
                for key,raw,published_version,published_at,day,begin in fetched:
                    document = json.loads(raw)
                    if not start.isoformat()<=document['lesson']['date']<=end.isoformat(): continue
                    if user['role'] in ['admin','teacher'] and not staff_read(user,workspace,document['lesson']): continue
                    projected = pupil_projection(document,studentId)
                    if projected:
                        rows.append(projected | dict(lessonKey=key,version=published_version,publishedAt=published_at));marks.append(f'{day}|{begin}|{key}')
                        classes[key] = next((x.get('classId') for x in document['lesson']['students'] if x['id']==studentId),None)
                def approve(chosen):
                    accepted = approvals(db,workspace,[r['lesson']|dict(students=[dict(id=studentId)]) for r in chosen])
                    for i,row in enumerate(chosen):
                        excuse = accepted.get(i,{}).get(studentId)
                        if excuse and row['record']['attendance']=='absent':
                            row['record'] = row['record']|dict(attendance='excused')
                            row['absenceApproval'] = excuse
                approve(rows)
                # A later page continues after the last row it scanned, even if some were filtered out.
                if after: return dict(rows=rows,summary=None,nextAfter=f'{fetched[-1][4]}|{fetched[-1][5]}|{fetched[-1][0]}' if more else None,absenceCodes=codes,source='published')
                return dict(rows=rows[:limit],summary=summary(rows,cfg,classes),nextAfter=marks[limit-1] if len(rows)>limit else None,absenceCodes=codes,source='published')
            return self.reading(read)
