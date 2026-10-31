"""Transactional attendance-only projection of published journal documents."""

# absence_code follows attendance so that the summary's GROUP BY student, subject, status, type reads the index in order.
SUMMARY_INDEX='''CREATE INDEX IF NOT EXISTS attendance_summary ON journal_attendance
    (workspace,student_id,subject_id,attendance,absence_code,day,start,end,late_minutes)'''
# Lateness of this many minutes or more also counts as a share of the lesson; shorter lateness counts in minutes only.
LATE_SHARE_FROM=5
LATE_SHARE_SQL=f'''CASE WHEN late_minutes>={LATE_SHARE_FROM} THEN min(1.0,late_minutes*1.0/nullif((substr(end,1,2)*60+substr(end,4,2))-(substr(start,1,2)*60+substr(start,4,2)),0)) ELSE 0 END'''


def late_share(minutes,start,end):
    """The same rule as LATE_SHARE_SQL for one record: the part of the lesson missed, at most 1."""
    length=int(end[:2])*60+int(end[3:5])-int(start[:2])*60-int(start[3:5])
    return min(1.0,minutes/length) if minutes>=LATE_SHARE_FROM and length>0 else 0.0


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
            json_extract(e.published_document,'$.lesson.end'),json_extract(e.published_document,'$.lesson.subjectId'),
            json_extract(r.value,'$.leftAt'),coalesce(json_extract(r.value,'$.absenceCode'),'')
        FROM journal_entries e,json_each(e.published_document,'$.content.records') r
        WHERE e.published_document IS NOT NULL'''+clause,args)


def tables(db):
    db.execute('CREATE TABLE IF NOT EXISTS journal_projection_versions (name TEXT PRIMARY KEY,version INTEGER)')
    # Daily confirmations are not derived from publications, so they are never rebuilt with the projection below.
    from .attendance_days import tables as day_tables
    day_tables(db)
    if db.execute("SELECT version FROM journal_projection_versions WHERE name='attendance'").fetchone()==(3,):
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
            day TEXT,start TEXT,end TEXT,subject_id TEXT,left_at TEXT,absence_code TEXT,
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
        db.execute("INSERT OR REPLACE INTO journal_projection_versions VALUES ('attendance',3)")
        db.execute('RELEASE attendance_migration')
    except Exception:
        db.execute('ROLLBACK TO attendance_migration');db.execute('RELEASE attendance_migration')
        raise


def marks(db,workspace,key):
    return {r[0]:r[1:] for r in db.execute('SELECT student_id,attendance,late_minutes,day FROM journal_attendance WHERE workspace=? AND lesson_key=?',(workspace,key))}


def replace(db,workspace,key):
    before=marks(db,workspace,key)
    db.execute('DELETE FROM journal_attendance WHERE workspace=? AND lesson_key=?',(workspace,key))
    db.execute('DELETE FROM journal_attendance_lessons WHERE workspace=? AND lesson_key=?',(workspace,key))
    populate(db,workspace,key)
    # A confirmed day of the class teacher stays confirmed unless a mark of one of its pupils really changed.
    from .attendance_days import marks_changed
    marks_changed(db,workspace,before,marks(db,workspace,key))
    db.execute('''INSERT INTO journal_attendance_epoch VALUES (?,1)
        ON CONFLICT(workspace) DO UPDATE SET version=journal_attendance_epoch.version+1''',(workspace,))
