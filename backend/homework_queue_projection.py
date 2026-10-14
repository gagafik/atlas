"""Small published-task and answer indexes; updated in the originating transaction."""
import json

from .notifications import encoded


def bump(db, workspace):
    db.execute('INSERT INTO homework_queue_versions VALUES (?,1) ON CONFLICT(workspace) DO UPDATE SET version=version+1',(workspace,))


def task(db, workspace, key, draft, published):
    if not published or not published['content']['homework']:return
    from .homework import fingerprint
    lesson,content=published['lesson'],published['content']
    source=draft['lesson']['sourceKey']
    kind,source_id=('extra',source[6:]) if source.startswith('extra:') else ('placement',source.split(':',1)[1])
    db.execute('INSERT INTO homework_queue_tasks VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',(
        workspace,key,content['dueDate'] or lesson['date'],lesson['date'],content['dueDate'],
        lesson['subject'],lesson['group'],content['homework'][:240],fingerprint(published),
        kind,source_id,encoded(draft['lesson']['teacherIds']),content.get('homeworkLatePolicy','allow')))


def answer(db, workspace, key, student, document):
    from .homework import status
    sent=document.get('submitted')
    values=(status(document),sent['assignment'] if sent else None,sent['submittedAt'] if sent else None,int(bool(sent and sent['late'])))
    old=db.execute('SELECT status,assignment,submitted_at,late FROM homework_queue_answers WHERE workspace=? AND lesson_key=? AND student_id=?',
                   (workspace,key,student)).fetchone()
    # Private drafts never change the queue or its pagination token.
    if values==old or old is None and sent is None:return
    db.execute('INSERT INTO homework_queue_answers VALUES (?,?,?,?,?,?,?) ON CONFLICT(workspace,lesson_key,student_id) DO UPDATE SET status=excluded.status,assignment=excluded.assignment,submitted_at=excluded.submitted_at,late=excluded.late',
               (workspace,key,student,*values))
    bump(db,workspace)


def tables(db):
    if db.execute("SELECT version FROM journal_projection_versions WHERE name='homework_queue'").fetchone()==(1,):return
    db.execute('SAVEPOINT homework_queue_migration')
    try:
        db.execute('''CREATE TABLE IF NOT EXISTS homework_queue_tasks (
            workspace TEXT,lesson_key TEXT,sort_date TEXT,lesson_date TEXT,due_date TEXT,
            subject TEXT,group_name TEXT,preview TEXT,assignment TEXT,source_kind TEXT,source_id TEXT,draft_teachers TEXT,late_policy TEXT,
            PRIMARY KEY(workspace,lesson_key))''')
        db.execute('CREATE INDEX IF NOT EXISTS homework_queue_date ON homework_queue_tasks(workspace,sort_date,lesson_key)')
        db.execute('''CREATE TABLE IF NOT EXISTS homework_queue_answers (
            workspace TEXT,lesson_key TEXT,student_id TEXT,status TEXT,assignment TEXT,submitted_at TEXT,late INTEGER,
            PRIMARY KEY(workspace,lesson_key,student_id))''')
        db.execute('CREATE TABLE IF NOT EXISTS homework_queue_versions (workspace TEXT PRIMARY KEY,version INTEGER)')
        db.execute('DELETE FROM homework_queue_tasks');db.execute('DELETE FROM homework_queue_answers')
        rows=db.execute('SELECT workspace,lesson_key,document,published_document FROM journal_entries WHERE published_document IS NOT NULL')
        while batch:=rows.fetchmany(100):
            for workspace,key,draft,published in batch:task(db,workspace,key,json.loads(draft),json.loads(published))
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='homework_submissions'").fetchone():
            rows=db.execute('SELECT workspace,lesson_key,student_id,document FROM homework_submissions')
            while batch:=rows.fetchmany(100):
                for workspace,key,student,document in batch:answer(db,workspace,key,student,json.loads(document))
        db.execute("INSERT INTO journal_projection_versions VALUES ('homework_queue',1)")
        db.execute('RELEASE homework_queue_migration')
    except Exception:
        db.execute('ROLLBACK TO homework_queue_migration');db.execute('RELEASE homework_queue_migration');raise


def replace(db, workspace, key):
    row=db.execute('SELECT document,published_document FROM journal_entries WHERE workspace=? AND lesson_key=?',(workspace,key)).fetchone()
    db.execute('DELETE FROM homework_queue_tasks WHERE workspace=? AND lesson_key=?',(workspace,key))
    if row:task(db,workspace,key,json.loads(row[0]),json.loads(row[1]) if row[1] else None)
    bump(db,workspace)
