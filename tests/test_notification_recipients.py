"""Targeted recipient selection preserves effective family and inbox permissions."""
import json
from datetime import timedelta

import pytest
from backend import api,families
from backend.notifications import permission_key
from backend.access import now
from test_access import client,enable
from test_homework_grading import ready,options,payload,grade
from test_homework import review,get


def grant(ids,workspace='demo',view='student'):
    return dict(workspace=workspace,view=view,entityIds=ids)


def account(db,id,role='parent',active=1,grants=None,rules=None):
    permissions=dict(workspaces=[],grants=grants or [grant(['s1'])],familyRules=rules or [])
    db.execute('INSERT INTO accounts VALUES (?,?,?,?,?,?,?,?)',(id,id,id,role,active,1,api.access.dummy,json.dumps(permissions)))


@pytest.mark.parametrize('view,ids,roles',[
    ('student',['s1'],['student','parent']),('student',['s1','s2'],['student','parent']),
    ('teacher',['t0'],['teacher']),('student',[],['student','parent'])])
def test_sql_candidates_match_effective_grants_across_dates_roles_and_workspaces(client,monkeypatch,view,ids,roles):
    enable(client);monkeypatch.setattr(families,'today',lambda:'2026-10-14')
    with api.connect() as db:
        account(db,'pupil',role='student')
        account(db,'parent')
        account(db,'inactive',active=0)
        account(db,'other-pupil',grants=[grant(['s2'])])
        account(db,'other-workspace',grants=[grant(['s1'],'school')])
        account(db,'wrong-view',grants=[grant(['s1'],view='class')])
        account(db,'duplicates',grants=[grant(['s1']),grant(['s2','s1']),grant(['s1'],'school')])
        account(db,'teacher',role='teacher',grants=[grant(['t0'],view='teacher')])
        account(db,'other-teacher',role='teacher',grants=[grant(['t1'],view='teacher')])
        account(db,'wrong-role',role='viewer')
        for id,start,end in [('inclusive','2026-10-14','2026-10-14'),('expired',None,'2026-10-13'),('future','2026-10-15',None)]:
            account(db,id,grants=[grant(['s1','s2'])],rules=[dict(workspace='demo',studentId='s1',startDate=start,endDate=end)])
        users=[api.access.public(row) for row in db.execute('SELECT * FROM accounts WHERE active=1')]
        expected=[u for u in users if u['role'] in roles and any(g['workspace']=='demo' and g['view']==view and set(ids)&set(g['entityIds']) for g in u['grants'])]
        selected=api.notifications.granted_users(db,'demo',view,ids,roles)
        assert sorted(selected,key=lambda u:u['id'])==sorted(expected,key=lambda u:u['id'])
        assert len({u['id'] for u in selected})==len(selected)
        api.notifications.deliver(db,selected,'demo',1,'journal.updated')
        emitted=dict(db.execute("SELECT user_id,permissions FROM notifications WHERE action='journal.updated'"))
        assert emitted=={u['id']:permission_key(u) for u in expected}


def test_grading_skips_unrelated_accounts_but_respects_mute_and_family_expiry(client,monkeypatch):
    record,sent,pupil,parent,teacher=ready(client);key=record['lessonKey'];mark=payload(options(teacher,key))
    monkeypatch.setattr(families,'today',lambda:'2026-10-14')
    with api.connect() as db:
        db.execute('DELETE FROM notifications')
        account(db,'expired-parent',rules=[dict(workspace='demo',studentId='s1',startDate=None,endDate='2026-10-13')])
        account(db,'current-parent',rules=[dict(workspace='demo',studentId='s1',startDate='2026-10-14',endDate='2026-10-14')])
        for i in range(25):account(db,'unrelated-'+str(i),grants=[grant(['s3'])])
    parent.put('/api/notifications/preferences',json={'enabled':False}).raise_for_status()
    original=api.access.public
    def checked(row,**kwargs):
        assert not row[0].startswith('unrelated-'),'Unrelated accounts were decoded during targeted notification.'
        return original(row,**kwargs)
    monkeypatch.setattr(api.access,'public',checked)
    grade(teacher,key,sent,mark).raise_for_status()
    assert parent.get('/api/notifications/inbox').json()['unread']==0
    assert {i['action'] for i in pupil.get('/api/notifications/inbox').json()['items']}=={'journal.grade','homework.reviewed'}
    with api.connect() as db:
        assert not db.execute("SELECT 1 FROM notifications WHERE user_id='expired-parent'").fetchone()
        assert db.execute("SELECT count(*) FROM notifications WHERE user_id='current-parent'").fetchone()[0]==2
    # Effective grants are recalculated at the next school date, without restart.
    monkeypatch.setattr(families,'today',lambda:'2026-10-15')
    review(teacher,key,get(teacher,key),'returned','Уточните ответ').raise_for_status()
    with api.connect() as db:assert db.execute("SELECT count(*) FROM notifications WHERE user_id='current-parent'").fetchone()[0]==2


def test_index_upgrade_preserves_latest_500_and_expiry_across_permissions(client):
    enable(client)
    with api.connect() as db:
        db.execute('DELETE FROM notifications')
        db.execute('DROP INDEX IF EXISTS notification_age')
        account(db,'recipient',role='student');user=api.access.public(db.execute("SELECT * FROM accounts WHERE id='recipient'").fetchone())
        recent=now().isoformat();expired=(now()-timedelta(days=91)).isoformat()
        db.executemany('INSERT INTO notifications(user_id,permissions,workspace,revision,created,action) VALUES (?,?,?,?,?,?)',
            [('recipient',f'old-grants-{i%2}','demo',i,recent,'journal.updated') for i in range(505)]+[
                ('other','key','demo',1,expired,'journal.updated'),('other','key','demo',2,recent,'journal.updated')])
        newest=[r[0] for r in db.execute("SELECT id FROM notifications WHERE user_id='recipient' ORDER BY id DESC LIMIT 499")]
        api.notifications.deliver(db,[user],'demo',506,'homework.reviewed')
        rows=db.execute("SELECT id,permissions,action FROM notifications WHERE user_id='recipient' ORDER BY id DESC").fetchall()
        assert len(rows)==500 and [r[0] for r in rows[1:]]==newest
        assert rows[0][1:]==(permission_key(user),'homework.reviewed')
        assert db.execute("SELECT revision FROM notifications WHERE user_id='other'").fetchall()==[(2,)]
        # The old database gains the retention index without rewriting inbox records.
        assert db.execute("SELECT 1 FROM sqlite_master WHERE type='index' AND name='notification_age'").fetchone()
