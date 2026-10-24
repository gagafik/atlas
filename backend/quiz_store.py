"""Quiz storage. Question and attempt snapshots are never rewritten by bank edits."""
import json
from .notifications import encoded


def tables(db):
    from .quiz_files import tables as file_tables
    file_tables(db)
    db.execute('''CREATE TABLE IF NOT EXISTS quiz_questions (
        workspace TEXT,id TEXT,version INTEGER,owner_id TEXT,updated TEXT,subject TEXT,archived INTEGER,document TEXT,
        PRIMARY KEY(workspace,id))''')
    db.execute('CREATE INDEX IF NOT EXISTS quiz_question_owner ON quiz_questions(workspace,owner_id,archived,id)')
    # Replays of bank package imports; only used inside write transactions after tables().
    db.execute('''CREATE TABLE IF NOT EXISTS quiz_bank_imports (
        workspace TEXT,owner_id TEXT,request_id TEXT,package_hash TEXT,created TEXT,result TEXT,
        PRIMARY KEY(workspace,owner_id,request_id))''')
    db.execute('''CREATE TABLE IF NOT EXISTS quizzes (
        workspace TEXT,id TEXT,version INTEGER,owner_id TEXT,updated TEXT,status TEXT,closes TEXT,lesson_key TEXT,
        document TEXT,published_document TEXT,released INTEGER,summary TEXT,PRIMARY KEY(workspace,id))''')
    db.execute('CREATE INDEX IF NOT EXISTS quiz_due ON quizzes(workspace,closes,id)')
    db.execute('''CREATE TABLE IF NOT EXISTS quiz_members (
        workspace TEXT,quiz_id TEXT,student_id TEXT,name TEXT,variant_id TEXT,PRIMARY KEY(workspace,quiz_id,student_id))''')
    db.execute('CREATE INDEX IF NOT EXISTS quiz_personal ON quiz_members(workspace,student_id,quiz_id)')
    db.execute('''CREATE TABLE IF NOT EXISTS quiz_attempts (
        workspace TEXT,id TEXT,quiz_id TEXT,student_id TEXT,number INTEGER,version INTEGER,status TEXT,
        started TEXT,deadline TEXT,submitted TEXT,request_id TEXT,document TEXT,score REAL,max_score REAL,
        PRIMARY KEY(workspace,id),UNIQUE(workspace,quiz_id,student_id,number),UNIQUE(workspace,quiz_id,student_id,request_id))''')
    db.execute('CREATE UNIQUE INDEX IF NOT EXISTS quiz_one_active ON quiz_attempts(workspace,quiz_id,student_id) WHERE status=\'active\'')
    db.execute('CREATE INDEX IF NOT EXISTS quiz_expiration ON quiz_attempts(workspace,quiz_id,status,deadline)')
    db.execute('''CREATE TABLE IF NOT EXISTS quiz_history (
        id INTEGER PRIMARY KEY,workspace TEXT,quiz_id TEXT,attempt_id TEXT,student_id TEXT,created TEXT,actor TEXT,
        action TEXT,document TEXT)''')
    db.execute('CREATE INDEX IF NOT EXISTS quiz_history_target ON quiz_history(workspace,quiz_id,attempt_id,id)')
    db.execute('CREATE TABLE IF NOT EXISTS quiz_catalog_versions(workspace TEXT PRIMARY KEY,version INTEGER)')


def unpack(row):
    document=json.loads(row[8])
    published=(document if row[9]==row[8] else json.loads(row[9])) if row[9] else None
    return dict(workspace=row[0],id=row[1],version=row[2],ownerId=row[3],updated=row[4],status=row[5],closesAt=row[6],
                lessonKey=row[7],document=document,published=published,released=bool(row[10]))


def attempt(row):
    return dict(id=row[1],quizId=row[2],studentId=row[3],number=row[4],version=row[5],status=row[6],
                startedAt=row[7],deadline=row[8],submittedAt=row[9],requestId=row[10],document=json.loads(row[11]))


def bump(db,workspace):
    db.execute('INSERT INTO quiz_catalog_versions VALUES (?,1) ON CONFLICT(workspace) DO UPDATE SET version=version+1',(workspace,))


def history(db,workspace,quiz,attempt_id,student,created,actor,action,document):
    db.execute('INSERT INTO quiz_history(workspace,quiz_id,attempt_id,student_id,created,actor,action,document) VALUES (?,?,?,?,?,?,?,?)',
               (workspace,quiz,attempt_id,student,created,actor,action,encoded(document)))
