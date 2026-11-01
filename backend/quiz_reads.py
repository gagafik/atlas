"""WAL snapshots for quiz reads; schema initialization alone reserves the writer."""
import sqlite3

MISSING_TABLES = {
    'quiz_files', 'quiz_file_links',
    'accounts', 'quizzes', 'quiz_questions', 'quiz_members', 'quiz_attempts',
    'quiz_history', 'quiz_catalog_versions', 'quiz_transfer_marks', 'journal_entries', 'journal_periods',
}


def read(quizzes, operation):
    def snapshot():
        with quizzes.access.read_connect() as db:
            # Access supports custom connections in tests/embedders. Enforce the
            # read contract even if their factory does not use SQLite mode=ro.
            db.execute('PRAGMA query_only=ON')
            db.execute('BEGIN')
            return operation(db)
    try:
        return snapshot()
    except sqlite3.OperationalError as exc:
        if str(exc) not in {f'no such table: {name}' for name in MISSING_TABLES}:
            raise
    # A new/older database may not have quiz tables yet. Never cache readiness:
    # rollback, database replacement and separate test databases remain safe.
    with quizzes.access.lock, quizzes.access.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        quizzes.tables(db)
    return snapshot()
