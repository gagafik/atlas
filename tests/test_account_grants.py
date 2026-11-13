"""Recipient lookup stays synchronized through account changes and restoration."""
from contextlib import closing
import json
import re
import sqlite3

import pytest
from backend import api,account_grants
from test_access import client,enable,create
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
    db.execute('UPDATE accounts SET permissions=? WHERE id=?',(permissions('s3'),'p'));db.commit()
    assert not recipients(db) and recipients(db,'s3')=={'p'}
    with closing(sqlite3.connect(tmp_path/'legacy.db')) as restored:
        assert recipients(restored)=={'p'} and recipients(restored,'s2')=={'p'}
        assert restored.execute('SELECT * FROM accounts').fetchall()==before


def put(client,user,**values):
    body=dict(name=user['name'],login=user['login'],role=user['role'],active=user['active'],workspaces=['demo'],grants=user['grants'],version=user['version'])|values
    answer=client.put(f'/api/access/users/{user["id"]}',json=body)
    assert answer.status_code==200,answer.text


def logins(client,student):
    with api.connect() as db:
        return {u['login'] for u in api.notifications.granted_users(db,'demo','student',[student],['parent','student'])}


def rewrite_permissions(client,user,student):
    with api.connect() as db:db.execute('UPDATE accounts SET permissions=? WHERE id=?',(permissions(student),user['id']))


@pytest.mark.parametrize('change,on_s1,on_s2',[
    (lambda c,p:create(c,'parent',login='newcomer'),{'parent','newcomer'},set()),
    (lambda c,p:put(c,p,grants=[grant(['s2'])]),set(),{'parent'}),
    (lambda c,p:put(c,p,active=False),set(),set()),
    (lambda c,p:put(c,p,role='student'),{'parent'},set()),
    (lambda c,p:put(c,p,role='editor',grants=[]),set(),set()),
    (lambda c,p:put(c,p,login='renamed'),{'renamed'},set()),
    (lambda c,p:rewrite_permissions(c,p,'s2'),set(),{'parent'}),
],ids=['new-account','grants-moved','deactivated','role-to-student','role-to-editor','login-renamed','permissions-rewritten'])
def test_recipients_follow_account_changes(client,change,on_s1,on_s2):
    enable(client);parent=create(client,'parent')
    assert logins(client,'s1')=={'parent'}
    change(client,parent)
    assert logins(client,'s1')==on_s1 and logins(client,'s2')==on_s2


@pytest.mark.parametrize('nested',[False,True])
def test_failed_migration_rolls_back_schema_and_retries_without_committing_caller(db,nested):
    account(db,'p');db.commit()
    if nested:db.execute('BEGIN')
    # Grants that cannot be read make the first backfill fail halfway through the migration.
    account(db,'bad');db.execute("UPDATE accounts SET permissions='{bad' WHERE id='bad'")
    if not nested:db.commit()
    before=db.execute('SELECT * FROM accounts').fetchall()
    with pytest.raises(sqlite3.OperationalError):account_grants.tables(db)
    assert db.in_transaction==nested and db.execute('SELECT * FROM accounts').fetchall()==before
    db.execute("UPDATE accounts SET permissions=? WHERE id='bad'",(permissions('s1'),))
    if not nested:db.commit()
    assert recipients(db)=={'p','bad'}
    assert db.in_transaction==nested
    if nested:
        db.rollback();assert recipients(db)=={'p'}


@pytest.mark.parametrize('kind,name',[('TABLE','account_grant_candidates'),('TRIGGER','account_grant_update')])
def test_missing_projection_object_rebuilds_from_current_accounts(db,kind,name):
    account(db,'p');db.commit();assert recipients(db)=={'p'}
    db.execute(f'DROP {kind} {name}')
    if kind!='TABLE':db.execute('UPDATE accounts SET permissions=? WHERE id=?',(permissions('s2'),'p'))
    before=db.execute('SELECT * FROM accounts').fetchall()
    assert recipients(db,'s1' if kind=='TABLE' else 's2')=={'p'}
    if kind!='TABLE':assert not recipients(db)
    assert db.execute('SELECT * FROM accounts').fetchall()==before


def test_candidate_query_searches_grant_keys_instead_of_scanning_account_permissions(db):
    for i in range(80):account(db,f'p{i}',grants=[grant([f's{i}'])])
    db.commit();assert recipients(db)=={'p1'}
    plan=' '.join(r[3] for r in db.execute('EXPLAIN QUERY PLAN '+account_grants.CANDIDATES_SQL,
        ('demo','student',json.dumps(['s1']),json.dumps(['parent']))))
    assert not re.search(r'\bSCAN (TABLE )?(a|accounts|account_grant_candidates)\b',plan),plan
