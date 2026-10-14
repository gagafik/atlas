"""Compact journal completeness index, maintained in the journal transaction."""


def populate(db,workspace=None,key=None):
    db.create_function('atlas_topic_filled',1,lambda value:bool(value and value.strip()))
    clause=' WHERE e.workspace=? AND e.lesson_key=?' if workspace is not None else ''
    args=(workspace,key) if workspace is not None else ()
    db.execute('''INSERT INTO journal_coverage_documents
        SELECT e.workspace,e.lesson_key,e.version,e.published_version,
        CASE WHEN e.published_document IS NULL THEN NULL ELSE json_object(
            'date',json_extract(e.published_document,'$.lesson.date'),
            'start',json_extract(e.published_document,'$.lesson.start'),
            'end',json_extract(e.published_document,'$.lesson.end'),
            'subjectId',json_extract(e.published_document,'$.lesson.subjectId'),
            'subject',json_extract(e.published_document,'$.lesson.subject'),
            'groupId',json_extract(e.published_document,'$.lesson.groupId'),
            'group',json_extract(e.published_document,'$.lesson.group'),
            'teacherIds',json_extract(e.published_document,'$.lesson.teacherIds'),
            'teachers',json_extract(e.published_document,'$.lesson.teachers'),
            'rooms',json_extract(e.published_document,'$.lesson.rooms'),
            'students',(SELECT json_group_array(json_object('id',json_extract(s.value,'$.id'),
                'classId',json_extract(s.value,'$.classId'),'enrolled',coalesce(json_extract(s.value,'$.enrolled'),1)))
                FROM json_each(e.published_document,'$.lesson.students') s)) END,
        atlas_topic_filled(json_extract(e.published_document,'$.content.topic')),
        json_extract(e.document,'$.lesson.teacherIds'),
        (SELECT json_group_array(json_extract(r.value,'$.studentId')) FROM json_each(e.published_document,'$.content.records') r),
        (SELECT json_group_array(json_extract(r.value,'$.studentId')) FROM json_each(e.published_document,'$.content.records') r WHERE json_extract(r.value,'$.attendance')='unmarked')
        FROM journal_entries e'''+clause,args)
    db.execute('''INSERT INTO journal_coverage_teachers
        SELECT e.workspace,t.value,e.lesson_key,json_extract(e.published_document,'$.lesson.date')
        FROM journal_entries e,json_each(e.published_document,'$.lesson.teacherIds') t'''+clause,args)


def tables(db):
    if db.execute("SELECT version FROM journal_projection_versions WHERE name='coverage'").fetchone()==(1,):return
    db.execute('SAVEPOINT coverage_migration')
    try:
        db.execute('''CREATE TABLE IF NOT EXISTS journal_coverage_documents (workspace TEXT,lesson_key TEXT,version INTEGER,published_version INTEGER,
            lesson TEXT,topic_filled INTEGER,draft_teachers TEXT,record_ids TEXT,unmarked_ids TEXT,PRIMARY KEY(workspace,lesson_key))''')
        db.execute('''CREATE TABLE IF NOT EXISTS journal_coverage_teachers (workspace TEXT,teacher_id TEXT,lesson_key TEXT,day TEXT,
            PRIMARY KEY(workspace,lesson_key,teacher_id)) WITHOUT ROWID''')
        db.execute('CREATE INDEX IF NOT EXISTS coverage_teacher_day ON journal_coverage_teachers(workspace,teacher_id,day)')
        db.execute('DELETE FROM journal_coverage_documents');db.execute('DELETE FROM journal_coverage_teachers')
        populate(db)
        db.execute("INSERT INTO journal_projection_versions VALUES ('coverage',1)")
        db.execute('RELEASE coverage_migration')
    except Exception:
        db.execute('ROLLBACK TO coverage_migration');db.execute('RELEASE coverage_migration');raise


def replace(db,workspace,key):
    db.execute('DELETE FROM journal_coverage_documents WHERE workspace=? AND lesson_key=?',(workspace,key))
    db.execute('DELETE FROM journal_coverage_teachers WHERE workspace=? AND lesson_key=?',(workspace,key))
    populate(db,workspace,key)
