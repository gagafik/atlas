"""Read concurrency and expiry write-path invariants on isolated quiz databases."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import sqlite3

import pytest
from backend import api,quizzes,quiz_attempts
from test_access import client
from test_quizzes import clock,ready,start,save,attempt,action,NOW


def test_empty_database_bootstraps_once_and_read_contract_rejects_writes(client,monkeypatch):
    calls=[];original=api.quizzes.tables
    def tables(db):calls.append(1);return original(db)
    monkeypatch.setattr(api.quizzes,'tables',tables)
    first=client.get('/api/journal/quizzes?workspace=demo');assert first.status_code==200,first.text
    assert first.json()['items']==[] and len(calls)==1
    for path in ['/api/journal/quizzes?workspace=demo','/api/journal/quiz-bank?workspace=demo']:
        assert client.get(path).status_code==200
    assert len(calls)==1
    with pytest.raises(sqlite3.OperationalError,match='readonly'):
        api.quizzes.read_only(lambda db:db.execute("DELETE FROM quiz_questions"))
    assert len(calls)==1


def test_bootstrap_rolls_back_failure_and_does_not_mask_unrelated_sql_errors(client,monkeypatch):
    def fail(db):
        db.execute('CREATE TABLE quiz_partial_probe(id TEXT)')
        raise RuntimeError('Synthetic migration failure')
    with monkeypatch.context() as patch:
        patch.setattr(api.quizzes,'tables',fail)
        with pytest.raises(RuntimeError,match='migration failure'):
            api.quizzes.read_only(lambda db:db.execute('SELECT * FROM quizzes').fetchall())
    with api.connect(read_only=True) as db:
        assert not db.execute("SELECT name FROM sqlite_master WHERE name='quiz_partial_probe'").fetchone()
    assert api.quizzes.read_only(lambda db:db.execute('SELECT * FROM quizzes').fetchall())==[]
    def unexpected(db):raise AssertionError('Unrelated errors must not initialize the schema')
    monkeypatch.setattr(api.quizzes,'tables',unexpected)
    with pytest.raises(sqlite3.OperationalError,match='unrelated_table'):
        api.quizzes.read_only(lambda db:db.execute('SELECT * FROM unrelated_table').fetchall())


def test_warm_views_work_while_writer_lock_and_sqlite_writer_are_held(client,monkeypatch):
    quiz,pupil,parent,teacher,_=ready(client);begun=start(pupil,quiz).json()
    base=f'/api/journal/quizzes/test-one/attempts/{begun["id"]}?workspace=demo'
    requests=[(pupil,'/api/journal/quizzes?workspace=demo'),(teacher,'/api/journal/quiz-bank?workspace=demo'),
              (pupil,'/api/journal/quizzes/test-one?workspace=demo&studentId=s1'),(teacher,'/api/journal/quizzes/test-one?workspace=demo'),
              (pupil,base),(parent,base),(teacher,base),(pupil,base.replace('?','/history?'))]
    for reader,path in requests:assert reader.get(path).status_code==200
    def forbidden():raise AssertionError('Ordinary quiz reads must not open a writable connection')
    monkeypatch.setattr(api.access,'connect',forbidden)
    with ThreadPoolExecutor(8) as pool:
        with api.access.lock,api.connect() as writer:
            writer.execute('BEGIN IMMEDIATE');writer.execute("UPDATE accounts SET name=name WHERE login='teacher'")
            pending=[pool.submit(reader.get,path) for reader,path in requests]
            responses=[future.result(timeout=3) for future in pending]
    assert all(response.status_code==200 for response in responses)
    assert responses[4].json()['canAnswer']
    assert all('answers' not in r.json() for r in responses[5:7])


def test_parallel_expiry_views_finalize_and_notify_exactly_once(client,monkeypatch):
    quiz,pupil,parent,teacher,_=ready(client);begun=start(pupil,quiz).json()
    saved=save(pupil,begun,{'choice':dict(selected=['a'])}).json()
    for module in [quizzes,quiz_attempts]:monkeypatch.setattr(module,'now',lambda:NOW+timedelta(hours=2))
    path=f'/api/journal/quizzes/test-one/attempts/{begun["id"]}?workspace=demo'
    with ThreadPoolExecutor(6) as pool:results=list(pool.map(lambda reader:reader.get(path),[pupil,parent,teacher]*2))
    assert all(r.status_code==200 for r in results)
    assert all(r.json()['status']=='reviewed' and r.json()['version']==saved['version']+1 for r in results)
    with api.connect(read_only=True) as db:
        assert db.execute("SELECT count(*) FROM quiz_history WHERE attempt_id=? AND action='quiz.submitted'",(begun['id'],)).fetchone()==(1,)
    notices=teacher.get('/api/notifications/inbox').json()['items']
    assert sum(n['action']=='quiz.submitted' for n in notices)==1
    assert attempt(teacher,begun)['score']==2


def test_grade_options_read_while_writer_is_held_and_respect_closed_period(client,monkeypatch):
    from test_journal_periods import close,period
    quiz,pupil,parent,teacher,_=ready(client)
    sent=save(pupil,start(pupil,quiz).json(),submit=True).json()
    action(teacher,action(teacher,quiz,'close').json(),'release')
    path=f'/api/journal/quizzes/test-one/attempts/{sent["id"]}/grading?workspace=demo'
    expected=teacher.get(path).json();assert expected['allowed']
    def forbidden():raise AssertionError('Grading options must not open a writable connection')
    with monkeypatch.context() as patch,ThreadPoolExecutor(1) as pool:
        patch.setattr(api.access,'connect',forbidden)
        with api.access.lock,api.connect() as writer:
            writer.execute('BEGIN IMMEDIATE');writer.execute("UPDATE accounts SET name=name WHERE login='teacher'")
            response=pool.submit(teacher.get,path).result(timeout=3)
        assert response.status_code==200 and response.json()==expected
    close(client,period(client))
    blocked=teacher.get(path);assert blocked.status_code==200
    assert not blocked.json()['allowed'] and 'Журнал закрыт' in blocked.json()['reason']


@pytest.mark.parametrize('change',['account','school','quiz'])
def test_expiry_rechecks_changes_between_read_snapshot_and_write(client,monkeypatch,change):
    quiz,pupil,parent,teacher,_=ready(client);begun=start(pupil,quiz).json()
    for module in [quizzes,quiz_attempts]:monkeypatch.setattr(module,'now',lambda:NOW+timedelta(hours=2))
    original=api.quizzes.read_only
    def changed(operation):
        result=original(operation)
        if type(result) is object:
            if change=='school':api.save('demo',api.read('demo'))
            else:
                with api.connect() as db:
                    if change=='account':db.execute("UPDATE accounts SET version=version+1 WHERE login='student'")
                    else:db.execute("UPDATE quizzes SET version=version+1 WHERE id='test-one'")
        return result
    monkeypatch.setattr(api.quizzes,'read_only',changed)
    response=pupil.get(f'/api/journal/quizzes/test-one/attempts/{begun["id"]}?workspace=demo')
    assert response.status_code==409,response.text
    with api.connect(read_only=True) as db:
        assert db.execute('SELECT status,version FROM quiz_attempts WHERE id=?',(begun['id'],)).fetchone()==('active',1)
        assert db.execute("SELECT count(*) FROM quiz_history WHERE attempt_id=? AND action='quiz.submitted'",(begun['id'],)).fetchone()==(0,)


@pytest.mark.parametrize('path',['/api/journal/quizzes','/api/journal/quiz-bank','/api/journal/quizzes/test-one/attempts/{attempt}/history'])
def test_read_snapshot_rechecks_account_after_request_identification(client,monkeypatch,path):
    quiz,pupil,parent,teacher,_=ready(client);begun=start(pupil,quiz).json();path=path.format(attempt=begun['id'])
    original=api.journal.scope
    def changed(*args,**kw):
        user=original(*args,**kw)
        with api.connect() as db:db.execute('UPDATE accounts SET version=version+1 WHERE id=?',(user['id'],))
        return user
    monkeypatch.setattr(api.journal,'scope',changed)
    response=teacher.get(path+'?workspace=demo');assert response.status_code==409,response.text
    assert 'correctIds' not in response.text and 'answers' not in response.text
