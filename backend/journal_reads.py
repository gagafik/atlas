"""WAL snapshots for journal and homework-queue reads; only schema setup reserves the writer."""
import sqlite3

# Journal.tables() writes ('journal_schema',SCHEMA) last. Increase SCHEMA whenever tables() gains
# DDL that reads rely on, so an older database is migrated once under the lock instead of failing.
# Change READY together with a projection's own version in its tables() function.
SCHEMA=1
READY={('attendance',2),('coverage',1),('homework_queue',1),('journal_schema',SCHEMA)}
MISSING_TABLES={'accounts','workspaces','journal_entries','journal_history','journal_members','journal_periods','journal_projection_versions',
    'journal_attendance','homework_queue_tasks','homework_queue_answers','homework_extensions'}


class NotReady(Exception):pass


def read(access,prepare,operation):
    """operation(db) runs in one read-only WAL transaction. Readiness is never cached."""
    def snapshot():
        with access.read_connect() as db:
            # Custom connection factories (tests, embedders) must still obey the read contract.
            db.execute('PRAGMA query_only=ON');db.execute('BEGIN')
            if not READY<=set(db.execute('SELECT name,version FROM journal_projection_versions')):raise NotReady
            return operation(db)
    try:return snapshot()
    except NotReady:pass
    except sqlite3.OperationalError as exc:
        if str(exc) not in {f'no such table: {name}' for name in MISSING_TABLES}:raise
    # A new, older or restored database: set it up once under the writer, then retry.
    # Rollback, replacement and separate test databases stay safe because nothing is cached.
    with access.lock,access.connect() as db:
        db.execute('BEGIN IMMEDIATE');prepare(db)
    try:return snapshot()
    except NotReady:raise RuntimeError('Схема журнала не подготовлена.') from None
