"""Transactional attendance-only projection of published journal documents."""

SUMMARY_INDEX='''CREATE INDEX IF NOT EXISTS attendance_summary ON journal_attendance
    (workspace,student_id,subject_id,attendance,day,start,end,late_minutes)'''


def populate(db,workspace=None,key=None):
    clause=' AND e.workspace=? AND e.lesson_key=?' if workspace is not None else ''
    args=(workspace,key) if workspace is not None else ()
    db.execute('''INSERT INTO journal_attendance_lessons
        SELECT e.workspace,e.lesson_key,json_extract(e.published_document,'$.lesson.date'),
            json_extract(e.published_document,'$.lesson.start'),json_extract(e.published_document,'$.lesson.end'),
            json_extract(e.published_document,'$.lesson.subjectId'),json_extract(e.published_document,'$.lesson.subject'),
            json_extract(e.published_document,'$.lesson.group'),json_extract(e.published_document,'$.lesson.teachers'),
            e.published_version,e.published_at
        FROM journal_entries e WHERE e.published_document IS NOT NULL'''+clause,args)
    db.execute('''INSERT INTO journal_attendance
        SELECT e.workspace,e.lesson_key,json_extract(r.value,'$.studentId'),
            json_extract(r.value,'$.attendance'),json_extract(r.value,'$.lateMinutes'),
            json_extract(e.published_document,'$.lesson.date'),json_extract(e.published_document,'$.lesson.start'),
            json_extract(e.published_document,'$.lesson.end'),json_extract(e.published_document,'$.lesson.subjectId')
        FROM journal_entries e,json_each(e.published_document,'$.content.records') r
        WHERE e.published_document IS NOT NULL'''+clause,args)


def tables(db):
    db.execute('CREATE TABLE IF NOT EXISTS journal_projection_versions (name TEXT PRIMARY KEY,version INTEGER)')
    if db.execute("SELECT version FROM journal_projection_versions WHERE name='attendance'").fetchone()==(2,):
        db.execute(SUMMARY_INDEX)
        return
    # A failed/rolled-back migration never leaves a completed marker. This also
    # works inside the caller's existing transaction without committing it.
    db.execute('SAVEPOINT attendance_migration')
    try:
        db.execute('''CREATE TABLE IF NOT EXISTS journal_attendance_lessons (
            workspace TEXT,lesson_key TEXT,day TEXT,start TEXT,end TEXT,subject_id TEXT,
            subject TEXT,group_name TEXT,teachers TEXT,version INTEGER,published_at TEXT,
            PRIMARY KEY(workspace,lesson_key))''')
        db.execute('CREATE INDEX IF NOT EXISTS attendance_lesson_day ON journal_attendance_lessons(workspace,day,lesson_key)')
        db.execute('CREATE INDEX IF NOT EXISTS attendance_lesson_subject ON journal_attendance_lessons(workspace,subject_id,day)')
        # This is a derived index, fully rebuilt from published documents.
        db.execute('DROP TABLE IF EXISTS journal_attendance')
        db.execute('''CREATE TABLE journal_attendance (
            workspace TEXT,lesson_key TEXT,student_id TEXT,attendance TEXT,late_minutes INTEGER,
            day TEXT,start TEXT,end TEXT,subject_id TEXT,
            PRIMARY KEY(workspace,lesson_key,student_id)) WITHOUT ROWID''')
        db.execute('CREATE INDEX attendance_student ON journal_attendance(workspace,student_id,day)')
        db.execute('CREATE INDEX attendance_day ON journal_attendance(workspace,day)')
        db.execute('CREATE TABLE IF NOT EXISTS journal_attendance_epoch (workspace TEXT PRIMARY KEY,version INTEGER)')
        db.execute('DELETE FROM journal_attendance');db.execute('DELETE FROM journal_attendance_lessons')
        populate(db)
        db.execute(SUMMARY_INDEX)
        db.execute('''INSERT INTO journal_attendance_epoch SELECT workspace,coalesce(sum(published_version),0)
            FROM journal_entries GROUP BY workspace
            ON CONFLICT(workspace) DO UPDATE SET version=journal_attendance_epoch.version+1''')
        db.execute("INSERT OR REPLACE INTO journal_projection_versions VALUES ('attendance',2)")
        db.execute('RELEASE attendance_migration')
    except Exception:
        db.execute('ROLLBACK TO attendance_migration');db.execute('RELEASE attendance_migration')
        raise


def replace(db,workspace,key):
    db.execute('DELETE FROM journal_attendance WHERE workspace=? AND lesson_key=?',(workspace,key))
    db.execute('DELETE FROM journal_attendance_lessons WHERE workspace=? AND lesson_key=?',(workspace,key))
    populate(db,workspace,key)
    db.execute('''INSERT INTO journal_attendance_epoch VALUES (?,1)
        ON CONFLICT(workspace) DO UPDATE SET version=journal_attendance_epoch.version+1''',(workspace,))
