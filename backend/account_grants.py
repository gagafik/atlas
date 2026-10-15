"""Derived lookup for notification candidates; effective grants remain authoritative."""
import json

OBJECTS={
    ('table','account_grant_candidates'),
    ('index','account_grant_owner'),('index','account_grant_login'),
    ('trigger','account_grant_insert'),('trigger','account_grant_update'),('trigger','account_grant_delete'),
}

CANDIDATES_SQL='''SELECT a.* FROM accounts a JOIN (
    SELECT DISTINCT account_id FROM account_grant_candidates
    WHERE workspace=? AND view=? AND entity_id IN (SELECT value FROM json_each(?))
) c ON c.account_id=a.id
WHERE a.active=1 AND a.role IN (SELECT value FROM json_each(?))'''


def projection(source):
    return f'''SELECT json_extract(g.value,'$.workspace'),json_extract(g.value,'$.view'),
        entity.value,{source}.id,{source}.login
        FROM json_each({source}.permissions,'$.grants') g,
             json_each(g.value,'$.entityIds') entity'''


def tables(db):
    names=tuple(name for _,name in OBJECTS)
    found=set(db.execute('SELECT type,name FROM sqlite_master WHERE name IN ('+','.join('?' for _ in names)+')',names))
    if found==OBJECTS:return
    # Missing tables/indexes/triggers require a complete rebuild: an absent
    # trigger may have missed permission changes. No process/path cache is used.
    db.execute('SAVEPOINT account_grant_migration')
    try:
        db.execute('''CREATE TABLE IF NOT EXISTS account_grant_candidates (
            workspace TEXT NOT NULL,view TEXT NOT NULL,entity_id TEXT NOT NULL,
            account_id TEXT NOT NULL,account_login TEXT,
            PRIMARY KEY(workspace,view,entity_id,account_id)) WITHOUT ROWID''')
        db.execute('CREATE INDEX IF NOT EXISTS account_grant_owner ON account_grant_candidates(account_id)')
        db.execute('CREATE INDEX IF NOT EXISTS account_grant_login ON account_grant_candidates(account_login)')
        # REPLACE does not reliably fire DELETE triggers. The inserted row must
        # clear both the previous id and any displaced unique-login owner.
        for name,event,old in [
            ('account_grant_insert','INSERT',''),
            ('account_grant_update','UPDATE OF id,login,permissions','account_id=OLD.id OR '),
        ]:
            db.execute(f'''CREATE TRIGGER IF NOT EXISTS {name} AFTER {event} ON accounts BEGIN
                DELETE FROM account_grant_candidates WHERE {old}account_id=NEW.id OR account_login=NEW.login;
                INSERT OR IGNORE INTO account_grant_candidates {projection('NEW')};
            END''')
        db.execute('''CREATE TRIGGER IF NOT EXISTS account_grant_delete AFTER DELETE ON accounts BEGIN
            DELETE FROM account_grant_candidates WHERE account_id=OLD.id;
        END''')
        db.execute('DELETE FROM account_grant_candidates')
        db.execute('''INSERT OR IGNORE INTO account_grant_candidates
            SELECT json_extract(g.value,'$.workspace'),json_extract(g.value,'$.view'),entity.value,a.id,a.login
            FROM accounts a,json_each(a.permissions,'$.grants') g,json_each(g.value,'$.entityIds') entity''')
        db.execute('RELEASE account_grant_migration')
    except Exception:
        db.execute('ROLLBACK TO account_grant_migration');db.execute('RELEASE account_grant_migration')
        raise


def candidates(db,workspace,view,ids,roles):
    tables(db)
    return db.execute(CANDIDATES_SQL,(workspace,view,json.dumps(sorted(ids)),json.dumps(roles)))
