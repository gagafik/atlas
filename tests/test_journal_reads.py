"""Journal and homework-queue reads use WAL snapshots: no schema work, no writer lock, writes are never blocked."""
from concurrent.futures import ThreadPoolExecutor
import sqlite3
import threading

import pytest
from backend import absences, api, journal, journal_reads
from test_access import client, signed
from test_homework import setup
from test_journal import DAY, lesson, entry, write, graded

REPORT='/api/journal/report?workspace=demo&studentId=s1&start=2026-08-01&end=2026-09-01'
QUEUE='/api/journal/homework-queue?workspace=demo'


def counted(monkeypatch):
    calls=[];original=api.journal.tables
    def tables(db):calls.append(1);return original(db)
    monkeypatch.setattr(api.journal,'tables',tables)
    return calls


def test_reads_bootstrap_once_and_never_write(client,monkeypatch):
    key=lesson(client)['lessonKey']
    saved=write(client,graded(entry(client,key)),publish=True);assert saved.status_code==200,saved.text
    calls=counted(monkeypatch)
    for path in ['/api/journal/options?workspace=demo',f'/api/journal/day?workspace=demo&day={DAY}',
                 f'/api/journal/entry?workspace=demo&lessonKey={key}&day={DAY}',f'/api/journal/history?workspace=demo&lessonKey={key}',
                 REPORT,QUEUE+'&filter=all']:
        response=client.get(path);assert response.status_code==200,(path,response.text)
    assert calls==[]
    with pytest.raises(sqlite3.OperationalError,match='readonly'):
        journal_reads.read(api.access,api.journal.prepare,lambda db:db.execute('DELETE FROM journal_entries'))
    assert calls==[]


def test_fresh_database_is_prepared_once_by_the_first_read(client,monkeypatch):
    calls=counted(monkeypatch)
    assert client.get('/api/journal/options?workspace=demo').status_code==200
    assert len(calls)==1
    for path in ['/api/journal/options?workspace=demo',f'/api/journal/day?workspace=demo&day={DAY}',REPORT,QUEUE]:
        assert client.get(path).status_code==200
    assert len(calls)==1
    with api.connect(read_only=True) as db:
        assert ('journal_schema',journal_reads.SCHEMA) in set(db.execute('SELECT name,version FROM journal_projection_versions'))


@pytest.mark.parametrize('marker',['journal_schema','coverage','attendance','homework_queue'])
def test_missing_marker_rebuilds_once_and_returns_the_same_data(client,monkeypatch,marker):
    record,pupil,parent,teacher=setup(client)
    expected=[parent.get(REPORT).json(),teacher.get(QUEUE+'&filter=all').json()]
    calls=counted(monkeypatch)
    with api.connect() as db:db.execute('DELETE FROM journal_projection_versions WHERE name=?',(marker,))
    assert [parent.get(REPORT).json(),teacher.get(QUEUE+'&filter=all').json()]==expected
    assert len(calls)==1
    assert parent.get(REPORT).json()==expected[0] and len(calls)==1


def test_dropped_member_table_is_recreated_by_the_next_read(client):
    record,pupil,parent,teacher=setup(client)
    with api.connect() as db:db.execute('DROP TABLE journal_members')
    assert client.get('/api/journal/options?workspace=demo').status_code==200
    assert len(teacher.get(QUEUE+'&filter=all').json()['items'])>0


def test_failed_preparation_rolls_back_and_unrelated_errors_are_not_masked(client,monkeypatch):
    def fail(db):
        db.execute('CREATE TABLE journal_partial_probe(id TEXT)')
        raise RuntimeError('Synthetic migration failure')
    with pytest.raises(RuntimeError,match='migration failure'):
        journal_reads.read(api.access,fail,lambda db:db.execute('SELECT 1').fetchone())
    with api.connect(read_only=True) as db:
        assert not db.execute("SELECT name FROM sqlite_master WHERE name='journal_partial_probe'").fetchone()
    assert journal_reads.read(api.access,api.journal.prepare,lambda db:db.execute('SELECT count(*) FROM journal_entries').fetchone())==(0,)
    def unexpected(db):raise AssertionError('Unrelated errors must not prepare the schema')
    with pytest.raises(sqlite3.OperationalError,match='unrelated_table'):
        journal_reads.read(api.access,unexpected,lambda db:db.execute('SELECT * FROM unrelated_table').fetchall())


def test_journal_and_homework_share_one_schedule_cache(client):
    assert api.homework.snapshots is api.journal.snapshots
    assert api.absences.snapshots is api.journal.snapshots and api.quizzes.snapshots is api.journal.snapshots


def test_journal_reads_work_while_writer_lock_and_sqlite_writer_are_held(client,monkeypatch):
    record,pupil,parent,teacher=setup(client);key=record['lessonKey']
    requests=[(teacher,'/api/journal/options?workspace=demo'),(teacher,f'/api/journal/day?workspace=demo&day={DAY}'),
              (teacher,f'/api/journal/entry?workspace=demo&lessonKey={key}&day={DAY}'),(teacher,f'/api/journal/history?workspace=demo&lessonKey={key}'),
              (teacher,REPORT),(pupil,REPORT),(parent,REPORT),(client,REPORT),
              (pupil,QUEUE),(parent,QUEUE),(teacher,QUEUE+'&filter=all'),(client,QUEUE+'&filter=all'),(pupil,'/api/journal/options?workspace=demo')]
    for reader,path in requests:assert reader.get(path).status_code==200,path
    def forbidden():raise AssertionError('Ordinary journal reads must not open a writable connection')
    monkeypatch.setattr(api.access,'connect',forbidden)
    with ThreadPoolExecutor(8) as pool:
        with api.access.lock,api.connect() as writer:
            writer.execute('BEGIN IMMEDIATE');writer.execute("UPDATE accounts SET name=name WHERE login='teacher'")
            pending=[pool.submit(reader.get,path) for reader,path in requests]
            responses=[future.result(timeout=3) for future in pending]
    assert all(response.status_code==200 for response in responses)
    assert len(responses[4].json()['rows'])==1 and len(responses[8].json()['items'])==1


def test_entry_and_history_recheck_account_in_snapshot(client,monkeypatch):
    record,pupil,parent,teacher=setup(client);key=record['lessonKey']
    original=api.journal.snapshots.get
    def revoke(workspace):
        result=original(workspace)
        with api.connect() as db:db.execute("UPDATE accounts SET version=version+1 WHERE login='teacher'")
        return result
    monkeypatch.setattr(api.journal.snapshots,'get',revoke)
    for path in [f'/api/journal/entry?workspace=demo&lessonKey={key}&day={DAY}',f'/api/journal/day?workspace=demo&day={DAY}',
                 f'/api/journal/history?workspace=demo&lessonKey={key}','/api/journal/options?workspace=demo']:
        assert teacher.get(path).status_code==409,path
    monkeypatch.setattr(api.journal.snapshots,'get',original)
    assert signed('teacher').get(f'/api/journal/entry?workspace=demo&lessonKey={key}&day={DAY}').status_code==200


@pytest.mark.parametrize('login,reader',[('teacher','teacher'),('parent','parent'),('student','pupil')])
def test_report_rechecks_account_in_snapshot(client,monkeypatch,login,reader):
    record,pupil,parent,teacher=setup(client)
    actor=dict(teacher=teacher,parent=parent,pupil=pupil)[reader];original=api.journal.reading
    def revoke(operation):
        # The account changes after the request was identified but before the read transaction starts.
        with api.connect() as db:db.execute('UPDATE accounts SET version=version+1 WHERE login=?',(login,))
        return original(operation)
    monkeypatch.setattr(api.journal,'reading',revoke)
    response=actor.get(REPORT);assert response.status_code==409,response.text
    monkeypatch.setattr(api.journal,'reading',original)
    assert signed(login).get(REPORT).status_code==200


def test_schema_marker_write_leaves_no_open_transaction(client):
    assert client.get('/api/journal/options?workspace=demo').status_code==200
    def marker(db):return db.execute("SELECT version FROM journal_projection_versions WHERE name='journal_schema'").fetchone()
    with api.connect() as db:
        db.execute("DELETE FROM journal_projection_versions WHERE name='journal_schema'");db.commit()
        api.journal.tables(db)
        # Quiz and homework writers call tables(db) and then start their own BEGIN IMMEDIATE.
        assert not db.in_transaction and marker(db)==(journal_reads.SCHEMA,)
        db.execute('BEGIN IMMEDIATE');db.execute('ROLLBACK')
    # Inside a caller's transaction the marker belongs to that transaction.
    with api.connect() as db:
        db.execute("DELETE FROM journal_projection_versions WHERE name='journal_schema'");db.commit()
        db.execute('BEGIN IMMEDIATE');api.journal.tables(db)
        assert db.in_transaction and marker(db)==(journal_reads.SCHEMA,)
        db.execute('ROLLBACK');assert marker(db) is None


def test_entry_and_day_recheck_schedule_revision_in_snapshot(client,monkeypatch):
    record,pupil,parent,teacher=setup(client);key=record['lessonKey']
    original=api.journal.snapshots.get
    def revise(workspace):
        result=original(workspace);api.save(workspace,api.read(workspace));return result
    monkeypatch.setattr(api.journal.snapshots,'get',revise)
    for path in [f'/api/journal/entry?workspace=demo&lessonKey={key}&day={DAY}',f'/api/journal/day?workspace=demo&day={DAY}']:
        response=teacher.get(path);assert response.status_code==409 and 'Расписание изменилось' in response.text


def blocked(monkeypatch,owner,name):
    """Wrap owner.name so only its first call pauses until release is set; return the events."""
    started,release=threading.Event(),threading.Event()
    original=getattr(owner,name)
    def wrapper(*args,**kwargs):
        if not started.is_set():
            started.set()
            assert release.wait(5)
        return original(*args,**kwargs)
    monkeypatch.setattr(owner,name,wrapper)
    return started,release


@pytest.mark.parametrize('owner,name',[(journal,'summary'),(absences,'approvals')])
def test_long_report_read_does_not_block_journal_save(client,monkeypatch,owner,name):
    record,pupil,parent,teacher=setup(client);key=record['lessonKey']
    current=entry(client,key)
    started,release=blocked(monkeypatch,owner,name)
    with ThreadPoolExecutor(2) as pool:
        reading=pool.submit(parent.get,REPORT)
        assert started.wait(3)
        saving=pool.submit(write,client,current)
        try:assert saving.result(timeout=3).status_code==200
        finally:release.set()
        assert reading.result(timeout=3).status_code==200


def test_long_queue_read_does_not_block_journal_save(client,monkeypatch):
    record,pupil,parent,teacher=setup(client);key=record['lessonKey']
    current=entry(client,key)
    started,release=blocked(monkeypatch,api.homework,'resolved_context')
    with ThreadPoolExecutor(2) as pool:
        reading=pool.submit(pupil.get,QUEUE)
        assert started.wait(3)
        saving=pool.submit(write,client,current)
        try:assert saving.result(timeout=3).status_code==200
        finally:release.set()
        response=reading.result(timeout=3)
        # The save changed the draft only, so the reader still gets its consistent page.
        assert response.status_code==200 and len(response.json()['items'])==1
