"""One pupil's published marks and attendance for a period in a single answer, for the pupil and parent screens."""
from datetime import date
import json

from fastapi import HTTPException, Request
from . import absence_codes
from .assessment_categories import category_of, config as category_config

# A school year of one pupil is a few thousand records; more means a wrong range, not a bigger answer.
MAX_ROWS = 5000


def scale_of(db, workspace, student):
    """The grade scale whose bands the chart paints: the one of the pupil's latest published final, else the school's only active threshold scale."""
    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name IN ('final_results','grading_scales')")}
    scale = None
    if 'final_results' in tables:
        row = db.execute("SELECT document FROM final_results WHERE workspace=? AND student_id=? ORDER BY json_extract(document,'$.publishedAt') DESC LIMIT 1", (workspace, student)).fetchone()
        scale = json.loads(row[0]).get('scale') if row else None
    if not scale and 'grading_scales' in tables:
        found = [s for s in (json.loads(r[0]) for r in db.execute('SELECT document FROM grading_scales WHERE workspace=?', (workspace,))) if s['active'] and s['kind'] == 'thresholds']
        scale = found[0] if len(found) == 1 else None
    return dict(name=scale['name'], kind=scale['kind'], bands=[dict(label=b['label'], minPercent=b['minPercent']) for b in scale['bands']]) if scale else None


def unsigned_marks(db, workspace, student, user):
    """(lesson key, work id) of the grades this account has not signed yet, and when it last signed."""
    row = db.execute('SELECT through_seq,signed_at FROM grade_signatures WHERE workspace=? AND student_id=? AND user_id=?', (workspace, student, user['id'])).fetchone()
    through, signed_at = row if row else (0, None)
    waiting = db.execute('SELECT DISTINCT lesson_key,assessment_id FROM grade_events WHERE workspace=? AND student_id=? AND hidden=0 AND seq>?', (workspace, student, through)).fetchall()
    return {(r[0], r[1]) for r in waiting}, signed_at


def register(journal, app, Workspace):
    @app.get('/api/journal/pupil-period')
    def pupil_period(request: Request, studentId: str, start: date, end: date, workspace: Workspace = 'school'):
        """Marks and attendance of the published lessons of one pupil; the summary is the one of the first page of /api/journal/report."""
        from .journal import summary
        user = journal.pupil_reader(request, workspace, studentId, start, end)
        family = user['role'] in ['student', 'parent']
        def read(db):
            journal.recheck_account(db, user)
            count = db.execute('SELECT count(*) FROM journal_attendance WHERE workspace=? AND student_id=? AND day BETWEEN ? AND ?', (workspace, studentId, start.isoformat(), end.isoformat())).fetchone()[0]
            if count > MAX_ROWS: raise HTTPException(422, 'Слишком много записей: выберите период короче.')
            cfg, codes = category_config(db, workspace), absence_codes.names(db, workspace)
            rows, _, classes, _, _ = journal.published_rows(db, user, workspace, studentId, start, end)
            waiting, signed_at = unsigned_marks(db, workspace, studentId, user) if family else (set(), None)
            marks, attendance = [], []
            for row in rows:
                lesson, record, key = row['lesson'], row['record'], row['lessonKey']
                where = dict(lessonKey=key, date=lesson['date'], start=lesson['start'], end=lesson['end'], subjectId=lesson['subjectId'], subject=lesson['subject'], teachers=lesson['teachers'])
                attendance.append(where | dict(group=lesson['group'], rooms=lesson['rooms'], attendance=record['attendance'], lateMinutes=record['lateMinutes'], leftAt=record['leftAt'], absenceCode=record['absenceCode'], byNote=bool(row.get('absenceApproval'))))
                works = {a['id']: a for a in row['content']['assessments']}
                for grade in record['grades']:
                    if grade['score'] is None and not grade['label']: continue
                    work = works[grade['assessmentId']]
                    marks.append(where | dict(assessmentId=work['id'], name=work['name'], kind=work['kind'], category=category_of(work), maxScore=work['maxScore'], weight=work['weight'],
                                              score=grade['score'], label=grade['label'], comment=grade['comment'], unsigned=(key, work['id']) in waiting))
            return dict(studentId=studentId, start=start.isoformat(), end=end.isoformat(), source='published', summary=summary(rows, cfg, classes), marks=marks, attendance=attendance,
                        absenceCodes=codes, scale=scale_of(db, workspace, studentId), signedAt=signed_at)
        return journal.reading(read)
