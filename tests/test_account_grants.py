"""Recipient lookup stays synchronized through account changes and restoration."""
from contextlib import closing
import json
import sqlite3

import pytest
from backend import api,account_grants
from test_notification_recipients import account,grant


@pytest.fixture
def db(tmp_path):
    with closing(sqlite3.connect(tmp_path/'accounts.db')) as connection:
        api.access.tables(connection)
        yield connection


def recipients(db,student='s1'):
    return {u['id'] for u in api.notifications.granted_users(db,'demo','student',[student],['parent','student'])}


def permissions(student):
    return json.dumps(dict(workspaces=[],grants=[grant([student,student])],familyRules=[]))


def test_legacy_backfill_preserves_accounts_and_restored_database(db,tmp_path):
    account(db,'p',grants=[grant(['s1','s1']),grant(['s2']),grant(['s1'],'school')]);db.commit()
    before=db.execute('SELECT * FROM accounts').fetchall()
    with closing(sqlite3.connect(tmp_path/'legacy.db')) as legacy:db.backup(legacy)
    assert recipients(db)=={'p'} and not db.in_transaction
    assert db.execute('SELECT * FROM accounts').fetchall()==before
    assert db.execute('SELECT count(*) FROM account_grant_candidates').fetchone()==(3,)
    db.execute('UPDATE accounts SET permissions=? WHERE id=?',(permissions('s3'),'p'));db.commit()
    assert not recipients(db) and recipients(db,'s3')=={'p'}
    with closing(sqlite3.connect(tmp_path/'legacy.db')) as restored:
        assert recipients(restored)=={'p'} and recipients(restored,'s2')=={'p'}
        assert restored.execute('SELECT * FROM accounts').fetchall()==before


def test_triggers_cover_insert_replace_update_and_delete_in_same_transaction(db):
    account(db,'p');db.commit();assert recipients(db)=={'p'}
    account(db,'new');assert recipients(db)=={'p','new'}
    db.execute('UPDATE accounts SET permissions=? WHERE id=?',(permissions('s2'),'p'))
    assert recipients(db)=={'new'} and recipients(db,'s2')=={'p'}
    row=list(db.execute("SELECT * FROM accounts WHERE id='p'").fetchone());row[7]=permissions('s3')
    db.execute('INSERT OR REPLACE INTO accounts VALUES (?,?,?,?,?,?,?,?)',row)
    assert not recipients(db,'s2') and recipients(db,'s3')=={'p'}
    # REPLACE on the login constraint may remove a different account id without
    # running SQLite DELETE triggers (recursive_triggers is off by default).
    row[0]='replacement';row[7]=permissions('s1')
    db.execute('INSERT OR REPLACE INTO accounts VALUES (?,?,?,?,?,?,?,?)',row)
    assert recipients(db)=={'new','replacement'} and not recipients(db,'s3')
    assert db.execute("SELECT count(*) FROM account_grant_candidates WHERE account_id='p'").fetchone()==(0,)
    ignored=list(row);ignored[0]='ignored';ignored[7]=permissions('s4')
    db.execute('INSERT OR IGNORE INTO accounts VALUES (?,?,?,?,?,?,?,?)',ignored)
    assert recipients(db)=={'new','replacement'} and not recipients(db,'s4')
    db.execute("UPDATE accounts SET id='renamed',login='renamed' WHERE id='replacement'")
    assert recipients(db)=={'new','renamed'}
    db.execute("UPDATE OR REPLACE accounts SET login='new' WHERE id='renamed'")
    assert recipients(db)=={'renamed'}
    assert db.execute("SELECT count(*) FROM account_grant_candidates WHERE account_id IN ('new','replacement')").fetchone()==(0,)
    db.execute("UPDATE accounts SET active=0 WHERE id='renamed'");assert not recipients(db)
    db.execute("UPDATE accounts SET active=1,role='teacher' WHERE id='renamed'");assert not recipients(db)
    db.execute("UPDATE accounts SET role='parent' WHERE id='renamed'");assert recipients(db)=={'renamed'}
    db.execute("UPDATE accounts SET permissions=? WHERE id='renamed'",(json.dumps(dict(workspaces=[],grants=[])),));assert not recipients(db)
    db.execute("DELETE FROM accounts WHERE id='renamed'")
    assert not db.execute('SELECT * FROM account_grant_candidates').fetchall()
    db.rollback()
    assert recipients(db)=={'p'} and recipients(db,'s2')==set()


@pytest.mark.parametrize('nested',[False,True])
def test_failed_migration_rolls_back_schema_and_retries_without_committing_caller(db,nested):
    account(db,'p');db.commit()
    if nested:db.execute('BEGIN');account(db,'pending')
    before=db.execute('SELECT * FROM accounts').fetchall()
    class Failure:
        def execute(self,sql,*args):
            if 'FROM accounts a,json_each' in sql:raise sqlite3.OperationalError('injected backfill failure')
            return db.execute(sql,*args)
    with pytest.raises(sqlite3.OperationalError,match='injected'):account_grants.tables(Failure())
    assert db.in_transaction==nested and db.execute('SELECT * FROM accounts').fetchall()==before
    assert not db.execute("SELECT name FROM sqlite_master WHERE name LIKE 'account_grant_%'").fetchall()
    assert recipients(db)==({'p','pending'} if nested else {'p'})
    assert db.in_transaction==nested
    if nested:
        db.rollback();assert recipients(db)=={'p'}


@pytest.mark.parametrize('kind,name',[
    ('TABLE','account_grant_candidates'),('INDEX','account_grant_owner'),('TRIGGER','account_grant_update'),
])
def test_missing_projection_object_rebuilds_from_current_accounts(db,kind,name):
    account(db,'p');db.commit();assert recipients(db)=={'p'}
    db.execute(f'DROP {kind} {name}')
    if kind!='TABLE':db.execute('UPDATE accounts SET permissions=? WHERE id=?',(permissions('s2'),'p'))
    before=db.execute('SELECT * FROM accounts').fetchall()
    assert recipients(db,'s1' if kind=='TABLE' else 's2')=={'p'}
    if kind!='TABLE':assert not recipients(db)
    assert db.execute('SELECT * FROM accounts').fetchall()==before


def test_warm_lookup_uses_read_snapshot_and_current_committed_roles(db,tmp_path):
    account(db,'p');db.commit();assert recipients(db)=={'p'}
    # Enable WAL before opening a separate reader, as the production app does.
    db.execute('PRAGMA journal_mode=WAL')
    db.execute('BEGIN IMMEDIATE');db.execute("UPDATE accounts SET role='teacher' WHERE id='p'")
    with closing(sqlite3.connect((tmp_path/'accounts.db').as_uri()+'?mode=ro',uri=True)) as reader:
        assert recipients(reader)=={'p'}
        db.commit()
        assert not recipients(reader)


def test_candidate_query_searches_grant_keys_instead_of_scanning_account_permissions(db):
    for i in range(80):account(db,f'p{i}',grants=[grant([f's{i}'])])
    db.commit();assert recipients(db)=={'p1'}
    plan=' '.join(r[3] for r in db.execute('EXPLAIN QUERY PLAN '+account_grants.CANDIDATES_SQL,
        ('demo','student',json.dumps(['s1']),json.dumps(['parent']))))
    assert 'SEARCH account_grant_candidates USING PRIMARY KEY' in plan and 'entity_id=?' in plan
    assert 'SEARCH a USING INDEX' in plan
