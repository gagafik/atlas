"""Targeted recipient selection preserves effective family and inbox permissions."""
import json
from datetime import timedelta

import pytest
from backend import api,families
from backend.notifications import permission_key
from backend.access import now
from seeding import insert_row
from test_access import client,enable
from test_homework_grading import ready,options,payload,grade
from test_homework import review,get


def grant(ids,workspace='demo',view='student'):
    return dict(workspace=workspace,view=view,entityIds=ids)


def account(db,id,role='parent',active=1,grants=None,rules=None):
    permissions=dict(workspaces=[],grants=grants or [grant(['s1'])],familyRules=rules or [])
    insert_row(db,'accounts',id=id,login=id,name=id,role=role,active=active,version=1,password=api.access.dummy,permissions=json.dumps(permissions))


@pytest.mark.parametrize('view,ids,roles,recipients',[
    ('student',['s1'],['student','parent'],['pupil','parent','duplicates','inclusive']),
    ('student',['s1','s2'],['student','parent'],['pupil','parent','other-pupil','duplicates','inclusive','expired','future']),
    ('teacher',['t0'],['teacher'],['teacher']),('student',[],['student','parent'],[])])
def test_notifications_reach_exactly_the_accounts_with_effective_grants_across_dates_roles_and_workspaces(client,monkeypatch,view,ids,roles,recipients):
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
        api.notifications.deliver(db,api.notifications.granted_users(db,'demo',view,ids,roles),'demo',1,'journal.updated')
        assert sorted(r[0] for r in db.execute("SELECT user_id FROM notifications WHERE action='journal.updated'"))==sorted(recipients)


def test_grading_skips_unrelated_accounts_but_respects_mute_and_family_expiry(client,monkeypatch):
    record,sent,pupil,parent,teacher=ready(client);key=record['lessonKey'];mark=payload(options(teacher,key))
    monkeypatch.setattr(families,'today',lambda:'2026-10-14')
    with api.connect() as db:
        db.execute('DELETE FROM notifications')
        account(db,'expired-parent',rules=[dict(workspace='demo',studentId='s1',startDate=None,endDate='2026-10-13')])
        account(db,'current-parent',rules=[dict(workspace='demo',studentId='s1',startDate='2026-10-14',endDate='2026-10-14')])
        account(db,'other-family',grants=[grant(['s3'])])
    parent.put('/api/notifications/preferences',json={'enabled':False}).raise_for_status()
    grade(teacher,key,sent,mark).raise_for_status()
    assert parent.get('/api/notifications/inbox').json()['unread']==0
    assert {i['action'] for i in pupil.get('/api/notifications/inbox').json()['items']}=={'journal.grade','homework.reviewed'}
    with api.connect() as db:
        assert not db.execute("SELECT 1 FROM notifications WHERE user_id IN ('expired-parent','other-family')").fetchone()
        assert db.execute("SELECT count(*) FROM notifications WHERE user_id='current-parent'").fetchone()[0]==2
    # Effective grants are recalculated at the next school date, without restart.
    monkeypatch.setattr(families,'today',lambda:'2026-10-15')
    review(teacher,key,get(teacher,key),'returned','Уточните ответ').raise_for_status()
    with api.connect() as db:
        assert db.execute("SELECT count(*) FROM notifications WHERE user_id='current-parent'").fetchone()[0]==2
        assert not db.execute("SELECT 1 FROM notifications WHERE user_id='other-family'").fetchone()


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
