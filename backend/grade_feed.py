"""Per-pupil feed of published grade changes and family signatures, as in EduPage."""
import json

from fastapi import HTTPException, Query, Request
from pydantic import Field
from .access import now
from .models import Model
from .notifications import encoded

PAGE = 50
MAX_SEQ = 2**63 - 1
ROLES = {'parent': 0, 'student': 1}


def tables(db):
    # Plain execute, never executescript: this runs inside journal write transactions,
    # and executescript would commit them halfway.
    db.execute('''CREATE TABLE IF NOT EXISTS grade_events(seq INTEGER PRIMARY KEY AUTOINCREMENT,workspace TEXT,student_id TEXT,lesson_key TEXT,
        assessment_id TEXT,created TEXT,change TEXT,document TEXT,hidden INTEGER NOT NULL DEFAULT 0)''')
    db.execute('CREATE INDEX IF NOT EXISTS grade_events_student ON grade_events(workspace,student_id,hidden,seq)')
    db.execute('CREATE INDEX IF NOT EXISTS grade_events_lesson ON grade_events(workspace,lesson_key,student_id,assessment_id)')
    db.execute('''CREATE TABLE IF NOT EXISTS grade_signatures(workspace TEXT,student_id TEXT,user_id TEXT,user_name TEXT,role TEXT,through_seq INTEGER,signed_at TEXT,
        PRIMARY KEY(workspace,student_id,user_id))''')


def graded(projection):
    if not projection: return {}, {}
    assessments = {a['id']: a for a in projection['content']['assessments']}
    return {g['assessmentId']: g for g in projection['record']['grades'] if g['score'] is not None or g['label']}, assessments


def describe(grade, assessment):
    return (grade['score'], grade['label'], grade['comment'], assessment['maxScore'], assessment['weight'], assessment['name'])


def record(db, workspace, key, before, after):
    """Store what each pupil's family can now see changed in published grades.

    Only the newest event of a grade stays visible, so a corrected comment or a
    withdrawn publication never remains readable in the feed.
    """
    from .journal import pupil_projection
    tables(db)
    if after is None:
        # Withdrawn from pupils and parents: hide everything this lesson told the family.
        db.execute('UPDATE grade_events SET hidden=1 WHERE workspace=? AND lesson_key=? AND hidden=0', (workspace, key))
        return
    created = now().isoformat()
    lesson = after['lesson']
    ids = {r['studentId'] for d in [before, after] if d for r in d['content']['records']}
    for student in sorted(ids):
        old, old_assessments = graded(pupil_projection(before, student) if before else None)
        new, new_assessments = graded(pupil_projection(after, student))
        for id in sorted(set(old) | set(new)):
            a, b = old.get(id), new.get(id)
            if b and not a: change = 'new'
            elif a and not b: change = 'removed'
            elif describe(a, old_assessments[id]) != describe(b, new_assessments[id]): change = 'changed'
            else: continue
            # A removed grade is described with the assessment it belonged to.
            assessment = old_assessments[id] if change == 'removed' else new_assessments[id]
            prior = old_assessments.get(id)
            data = dict(subject=lesson['subject'], date=lesson['date'], start=lesson['start'], teachers=lesson.get('teachers', []),
                        assessment=assessment['name'], kind=assessment['kind'], maxScore=assessment['maxScore'], weight=assessment['weight'],
                        score=b['score'] if b else None, label=b['label'] if b else '', comment=b['comment'] if b else '',
                        previous=dict(score=a['score'], label=a['label'], maxScore=prior['maxScore'], weight=prior['weight'], assessment=prior['name'],
                                      commentChanged=bool(b) and a['comment'] != b['comment']) if a else None)
            db.execute('UPDATE grade_events SET hidden=1 WHERE workspace=? AND lesson_key=? AND student_id=? AND assessment_id=? AND hidden=0',
                       (workspace, key, student, id))
            db.execute('INSERT INTO grade_events(workspace,student_id,lesson_key,assessment_id,created,change,document) VALUES (?,?,?,?,?,?,?)',
                       (workspace, student, key, id, created, change, encoded(data)))


def unsigned(db, workspace, student, through):
    return db.execute('SELECT count(*) FROM grade_events WHERE workspace=? AND student_id=? AND hidden=0 AND seq>?', (workspace, student, through)).fetchone()[0]


class Sign(Model):
    studentId: str = Field(min_length=1, max_length=100)
    throughSeq: int = Field(ge=1, le=MAX_SEQ)


def register(journal, app, Workspace):
    access = journal.access

    def family(request, workspace, student):
        from .journal import user_ids
        user = journal.scope(request, workspace)
        if user['role'] not in ROLES or student not in user_ids(user, workspace, 'student'):
            raise HTTPException(403, 'Этот ученик не назначен вашей учётной записи.')
        return user

    def staff(request, workspace, student):
        user = journal.scope(request, workspace, staff=True)
        if user['role'] == 'admin': return user
        state = access.read(workspace)
        from .messages import Directory
        pupil = next((s for s in state.students if s.id == student and s.active), None)
        teaches = user['role'] == 'teacher' and student in Directory(state, workspace).pupils(user)
        if not pupil or not teaches: raise HTTPException(403, 'Ученик не относится к вашим урокам.')
        return user

    @app.get('/api/journal/grades/feed')
    def feed(request: Request, studentId: str, workspace: Workspace = 'school', before: int | None = Query(default=None, ge=1, le=MAX_SEQ)):
        user = family(request, workspace, studentId)
        with access.lock, access.connect() as db:
            tables(db)
            row = db.execute('SELECT through_seq,signed_at FROM grade_signatures WHERE workspace=? AND student_id=? AND user_id=?', (workspace, studentId, user['id'])).fetchone()
            through, signed_at = (row[0], row[1]) if row else (0, None)
            clause, params = 'workspace=? AND student_id=? AND hidden=0', [workspace, studentId]
            if before: clause += ' AND seq<?'; params.append(before)
            rows = db.execute(f'SELECT seq,created,change,document FROM grade_events WHERE {clause} ORDER BY seq DESC LIMIT {PAGE+1}', params).fetchall()
            waiting = unsigned(db, workspace, studentId, through)
        items = [dict(seq=r[0], created=r[1], change=r[2], signed=r[0] <= through, **json.loads(r[3])) for r in rows[:PAGE]]
        return dict(items=items, nextBefore=rows[PAGE-1][0] if len(rows) > PAGE else None, unsigned=waiting, signedAt=signed_at,
                    latest=rows[0][0] if rows and not before else None, canSign=True, role=user['role'])

    @app.post('/api/journal/grades/sign')
    def sign(body: Sign, request: Request, workspace: Workspace = 'school'):
        user = family(request, workspace, body.studentId)
        with access.lock, access.connect() as db:
            tables(db)
            if not db.in_transaction: db.execute('BEGIN IMMEDIATE')
            journal.recheck_account(db, user)
            row = db.execute('SELECT through_seq,signed_at FROM grade_signatures WHERE workspace=? AND student_id=? AND user_id=?', (workspace, body.studentId, user['id'])).fetchone()
            latest = db.execute('SELECT max(seq) FROM grade_events WHERE workspace=? AND student_id=? AND hidden=0', (workspace, body.studentId)).fetchone()[0] or 0
            if row and row[0] >= body.throughSeq:
                # A stale or repeated signature acknowledges nothing new: keep its time,
                # even if the grades it covered were withdrawn since.
                through, signed_at = row
            elif body.throughSeq > latest:
                # Only grades the family has already been shown can be signed.
                raise HTTPException(409, 'Список оценок изменился. Обновите его и подпишите снова.')
            else:
                through, signed_at = body.throughSeq, now().isoformat()
                db.execute('''INSERT INTO grade_signatures VALUES (?,?,?,?,?,?,?) ON CONFLICT(workspace,student_id,user_id)
                              DO UPDATE SET through_seq=excluded.through_seq,signed_at=excluded.signed_at,user_name=excluded.user_name,role=excluded.role''',
                           (workspace, body.studentId, user['id'], user['name'], user['role'], through, signed_at))
            waiting = unsigned(db, workspace, body.studentId, through)
        return dict(throughSeq=through, signedAt=signed_at, unsigned=waiting)

    @app.get('/api/journal/grades/signatures')
    def signatures(request: Request, studentId: str, workspace: Workspace = 'school'):
        staff(request, workspace, studentId)
        with access.lock, access.connect() as db:
            tables(db)
            events = bool(db.execute('SELECT 1 FROM grade_events WHERE workspace=? AND student_id=? AND hidden=0 LIMIT 1', (workspace, studentId)).fetchone())
            # The current family only: active accounts whose effective grants include the pupil now.
            members = journal.notifications.granted_users(db, workspace, 'student', {studentId}, list(ROLES))
            signed = {r[0]: (r[1], r[2]) for r in db.execute('SELECT user_id,through_seq,signed_at FROM grade_signatures WHERE workspace=? AND student_id=?', (workspace, studentId))}
            items = []
            for member in sorted(members, key=lambda m: (ROLES[m['role']], m['name'], m['id'])):
                through, signed_at = signed.get(member['id'], (0, None))
                items.append(dict(name=member['name'], role=member['role'], signedAt=signed_at, unsigned=unsigned(db, workspace, studentId, through)))
        return dict(items=items, events=events)
