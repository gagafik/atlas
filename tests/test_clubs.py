from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from uuid import uuid4
import json,sqlite3
import pytest
from backend import api,clubs,clubs_routes,clubs_sessions
from test_access import client,enable,create,signed

def body(**kw):return dict(requestId=str(uuid4()),**kw)
def path(part):return '/api/clubs/'+part+('&' if '?' in part else '?')+'workspace=demo'
def ok(r):
    assert r.status_code==200,r.text
    return r.json()
def get(c,p):return ok(c.get(path(p)))
def post(c,p,**kw):return c.post(path(p),json=body(**kw))
def data(**kw):return dict(title='Шахматный клуб',description='Занятия после уроков',location='Кабинет 101',startsOn='2026-10-01',endsOn='2027-05-31',opensOn='2026-10-01',closesOn='2027-04-01',capacity=1,queueMode='automatic',classIds=[c.id for c in api.read('demo').classes])|kw
def club(c,**kw):return ok(post(c,'items',version=0,data=data(**kw)))
def fresh(c,r):return get(c,'items/'+r['id'])
def action(c,r,a):return post(c,'items/'+r['id']+'/action',version=fresh(c,r)['version'],action=a,reason='Школьное решение')
def published(c,**kw):return ok(action(c,club(c,**kw),'publish'))
def join(c,r,student='s1'):
    return post(c,'items/'+r['id']+'/members',clubVersion=fresh(c,r)['version'],studentId=student,reason='Заявление на кружок')
def members(c,r):return get(c,'items/'+r['id']+'/members')['items']
def member_action(c,r,m,a):return post(c,'items/'+r['id']+'/members/'+m['id']+'/action',version=m['version'],clubVersion=fresh(c,r)['version'],action=a,reason='Уточнение участия')
def assign(c,r,*users):return ok(c.put(path('items/'+r['id']+'/access'),json=body(version=fresh(c,r)['version'],accountIds=[u['id'] for u in users],reason='Ответственный ведущий')))
def session(c,r,**kw):return ok(post(c,'items/'+r['id']+'/sessions',version=0,clubVersion=fresh(c,r)['version'],data=dict(date='2026-10-15',start='17:00',end='18:00',title='Тренировка',location='101')|kw))
def session_action(c,r,x,a):return post(c,'items/'+r['id']+'/sessions/'+x['id']+'/action',version=x['version'],action=a,reason='Учёт занятия')
@pytest.fixture
def setup(client,monkeypatch):
    for module in [clubs,clubs_routes,clubs_sessions]:monkeypatch.setattr(module,'today',lambda:'2026-10-15')
    enable(client);t=create(client,'teacher');p=create(client,'parent');other=create(client,'parent',login='other',grants=[dict(workspace='demo',view='student',entityIds=['s3'])]);same=create(client,'parent',login='same');student=create(client,'student')
    return t,p,other,same,student

def test_private_drafts_and_separate_delegation(client,setup):
    t,p=signed('teacher'),signed('parent');r=club(client)
    assert get(p,'items')['items']==[] and p.get(path('items/'+r['id'])).status_code==404
    assert post(t,'items',version=0,data=data()).status_code==403
    assert post(t,'items/'+r['id']+'/action',version=r['version'],action='publish',reason='Публикация').status_code==403
    assign(client,r,setup[0]);assert get(t,'items?mode=manage')['items'][0]['id']==r['id'];r=ok(action(t,r,'publish'))
    assert get(p,'items')['items'][0]['id']==r['id'];assert get(p,'options')['staff']==[]
    another=club(client);assert t.get(path('items/'+another['id'])).status_code==404
    assign(client,r);assert t.post(path('items/'+r['id']+'/action'),json=body(version=fresh(client,r)['version'],action='close',reason='Закрытие')).status_code==403

def test_fifo_withdrawal_promotes_once_and_family_privacy(client,setup):
    before=api.read('demo');p,o=signed('parent'),signed('other');r=published(client);a=ok(join(p,r));b=ok(join(o,r,'s3'))
    assert a['status']=='enrolled' and b['status']=='waiting' and b['position']==1
    assert len(members(p,r))==1 and len(members(o,r))==1
    assert member_action(o,r,a,'withdraw').status_code==403
    assert p.get(path('items/'+r['id']+'/history')).status_code==403
    assert join(signed('same'),r).status_code==409
    ok(member_action(signed('same'),r,a,'withdraw'));rows=members(client,r)
    assert rows[0]['status']=='withdrawn' and rows[1]['status']=='enrolled'
    assert 'requesterPermissions' not in json.dumps(rows)
    assert api.read('demo')==before

def test_manual_queue_never_bypassed_by_new_joiner(client,setup):
    p,o=signed('parent'),signed('other');r=published(client,queueMode='manual');a=ok(join(p,r));b=ok(join(o,r,'s3'));ok(member_action(p,r,a,'withdraw'))
    d=ok(join(p,r));assert d['status']=='waiting' and d['position']==2
    assert member_action(client,r,d,'promote').status_code==409
    ok(member_action(client,r,b,'promote'));assert [x['status'] for x in members(client,r)]==['withdrawn','enrolled','waiting']

def test_racing_last_seat_and_retry_are_atomic(client,setup):
    r=published(client);p,o=signed('parent'),signed('other');payload=[body(clubVersion=r['version'],studentId=id,reason='Запись') for id in ['s1','s3']]
    with ThreadPoolExecutor(2) as pool:responses=list(pool.map(lambda pair:pair[0].post(path('items/'+r['id']+'/members'),json=pair[1]),zip([p,o],payload)))
    assert sorted(x.status_code for x in responses)==[200,409]
    winner=responses[0].status_code==200;i=0 if winner else 1;c=p if winner else o
    assert c.post(path('items/'+r['id']+'/members'),json=payload[i]).json()==responses[i].json()
    loser=o if winner else p;assert ok(join(loser,r,'s3' if winner else 's1'))['status']=='waiting'
    assert fresh(client,r)['enrolled']==1 and fresh(client,r)['waiting']==1

def test_sessions_attendance_correction_and_family_export(client,setup):
    r=published(client,capacity=3);assign(client,r,setup[0]);p,o,t=signed('parent'),signed('other'),signed('teacher');ok(join(p,r));ok(join(o,r,'s3'));x=session(t,r)
    assert session_action(p,r,x,'start').status_code==403
    x=ok(session_action(t,r,x,'start'));assert len(x['marks'])==2
    assert session_action(t,r,x,'complete').status_code==409
    marks=[dict(studentId=m['studentId'],status='present' if m['studentId']=='s1' else 'absent',note='Учтено') for m in x['marks']]
    x=ok(t.put(path('items/'+r['id']+'/sessions/'+x['id']+'/attendance'),json=body(version=x['version'],marks=marks,reason='Посещение занятия')))
    x=ok(session_action(t,r,x,'complete'));personal=get(p,'items/'+r['id']+'/sessions/'+x['id']);assert len(personal['marks'])==1 and personal['marks'][0]['studentId']=='s1'
    x=ok(session_action(t,r,x,'reopen'));x=ok(t.put(path('items/'+r['id']+'/sessions/'+x['id']+'/attendance'),json=body(version=x['version'],marks=[dict(studentId='s1',status='late',note='Пришёл позже')],reason='Исправление отметки')))
    ok(session_action(t,r,x,'complete'));history=get(t,'items/'+r['id']+'/history')['items'];assert sum(h['action']=='session-completed' for h in history)==2
    csv=p.get(path('items/'+r['id']+'/export?kind=attendance'));assert csv.content.startswith(b'\xef\xbb\xbf') and 'Пришёл позже' in csv.text and api.read('demo').students[2].name not in csv.text

def test_snapshot_survives_withdrawal_and_blocks_roster_injection(client,setup):
    r=published(client);p=signed('parent');m=ok(join(p,r));x=ok(session_action(client,r,session(client,r),'start'));ok(member_action(p,r,m,'withdraw'))
    assert len(get(p,'items/'+r['id']+'/sessions/'+x['id'])['marks'])==1
    bad=client.put(path('items/'+r['id']+'/sessions/'+x['id']+'/attendance'),json=body(version=x['version'],marks=[dict(studentId='s3',status='present')],reason='Подмена состава'));assert bad.status_code==422
    assert action(client,r,'archive').status_code==409
    x=ok(session_action(client,r,x,'cancel'));ok(action(client,r,'archive'));assert fresh(p,r)['status']=='archived'

def test_conflicting_club_blocks_registration_and_schedule_edit(client,setup):
    a=published(client);b=published(client,title='Робототехника');p=signed('parent');session(client,a);session(client,b);ok(join(p,a));assert join(p,b).status_code==409
    x=session(client,b,date='2026-10-16');assign(client,a,setup[0])
    assert client.put(path('items/'+b['id']+'/access'),json=body(version=fresh(client,b)['version'],accountIds=[setup[0]['id']],reason='Назначение')).status_code==409
    response=post(client,'items/'+b['id']+'/sessions',version=0,clubVersion=fresh(client,b)['version'],data=dict(date='2026-10-15',start='17:30',end='18:30',title='Занятие'));assert response.status_code==409

def test_changed_parent_rights_invalidate_queue_promotion_and_access(client,setup):
    p,o=signed('parent'),signed('other');r=published(client);a=ok(join(p,r));ok(join(o,r,'s3'))
    with api.connect() as db:
        raw=db.execute('SELECT permissions FROM accounts WHERE id=?',(setup[2]['id'],)).fetchone();d=json.loads(raw[0]);d['grants']=[dict(workspace='demo',view='student',entityIds=['s4'])];db.execute('UPDATE accounts SET permissions=?,version=version+1 WHERE id=?',(json.dumps(d),setup[2]['id']))
    ok(member_action(p,r,a,'withdraw'));assert members(client,r)[1]['status']=='ineligible';assert members(o,r)==[]

def test_deadlines_versions_capacity_and_archival(client,setup,monkeypatch):
    r=published(client,capacity=2);p=signed('parent');ok(join(p,r));ok(join(client,r,'s3'))
    current=fresh(client,r);d=current['data']|dict(capacity=1)
    assert client.put(path('items/'+r['id']),json=body(version=current['version'],data=d,reason='Уменьшение')).status_code==409
    x=session(client,r,date='2026-10-16');assert session_action(client,r,x,'start').status_code==409
    ok(action(client,r,'archive'));assert all(m['status']=='completed' for m in members(p,r));assert get(p,'items/'+r['id']+'/sessions')['items'][0]['status']=='cancelled'
    assert join(p,r).status_code==409
    another=published(client,closesOn='2026-10-14');assert join(p,another).status_code==409

def test_notification_failure_rolls_back_enrolment_and_history(client,setup,monkeypatch):
    r=published(client);p=signed('parent');before=fresh(client,r)
    def fail(*a,**kw):raise RuntimeError('synthetic notification failure')
    monkeypatch.setattr(api.notifications,'deliver',fail)
    with pytest.raises(RuntimeError):join(p,r)
    assert members(client,r)==[] and fresh(client,r)['version']==before['version']
    assert not any(h['action']=='joined' for h in get(client,'items/'+r['id']+'/history')['items'])

def test_pagination_workspace_scope_and_replay_revocation(client,setup):
    p=signed('parent');a=published(client,title='=Шахматы');published(client,title='Дебаты')
    page=get(p,'items?limit=1');nextpage=get(p,'items?limit=1&before='+str(page['nextBefore']));assert page['items'][0]['id']!=nextpage['items'][0]['id']
    assert p.get('/api/clubs/items/'+a['id']+'?workspace=school').status_code==403
    r=ok(join(p,a));assert len(members(p,a))==1
    with api.connect() as db:db.execute('UPDATE accounts SET active=0 WHERE id=?',(setup[1]['id'],))
    assert p.get(path('items/'+a['id'])).status_code==401

def test_backup_restore_keeps_module_and_school_state(client,setup,tmp_path):
    from tools.backup_school import create_backup
    from tools.restore_school import restore_backup,verify_backup
    r=published(client);ok(join(signed('parent'),r));session(client,r)
    backup=tmp_path/'copy';create_backup(api.DB,backup);target=tmp_path/'restored';restore_backup(backup,target);verify_backup(backup)
    with closing(sqlite3.connect(api.DB)) as before,closing(sqlite3.connect((target/api.DB.name).as_uri()+'?mode=ro&immutable=1',uri=True)) as after:
        tables=[x[0] for x in before.execute("SELECT name FROM sqlite_master WHERE type='table' AND (name='clubs' OR name LIKE 'club_%' OR name='workspaces')")]
        for table in tables:assert sorted(before.execute('SELECT * FROM '+table).fetchall(),key=repr)==sorted(after.execute('SELECT * FROM '+table).fetchall(),key=repr)
    verify_backup(target)

def test_stale_staff_assignment_does_not_block_other_clubs(client,setup):
    a=published(client);b=published(client);assign(client,a,setup[0]);assign(client,b,setup[0]);session(client,a)
    with api.connect() as db:db.execute("UPDATE accounts SET role='staff',permissions=?,version=version+1 WHERE id=?",(json.dumps(dict(workspaces=['demo'],grants=[])),setup[0]['id']))
    # Old assignment no longer confers management or teacher-time constraints.
    assert fresh(client,a)['staff']==[];session(client,b)
    assert post(signed('teacher'),'items/'+a['id']+'/action',version=fresh(client,a)['version'],action='close',reason='Закрытие').status_code==403

def test_expired_family_request_is_not_automatically_enrolled(client,setup):
    r=published(client);p=signed('parent');a=ok(join(p,r));ok(join(signed('other'),r,'s3'))
    with api.connect() as db:
        permissions=json.loads(db.execute('SELECT permissions FROM accounts WHERE id=?',(setup[2]['id'],)).fetchone()[0]);permissions['familyRules']=[dict(workspace='demo',studentId='s3',startDate='2020-01-01',endDate='2020-12-31')];db.execute('UPDATE accounts SET permissions=?,version=version+1 WHERE id=?',(json.dumps(permissions),setup[2]['id']))
    ok(member_action(p,r,a,'withdraw'));assert members(client,r)[1]['status']=='ineligible'

def test_closing_queue_preserves_enrolled_members_and_completed_history(client,setup):
    r=published(client);p=signed('parent');ok(join(p,r));ok(join(signed('other'),r,'s3'));x=session(client,r)
    ok(action(client,r,'close'));assert [m['status'] for m in members(client,r)]==['enrolled','withdrawn']
    x=ok(session_action(client,r,x,'start'))
    payload=body(version=x['version'],marks=[dict(studentId='s1',status='present',note='На занятии')],reason='Посещение')
    url=path('items/'+r['id']+'/sessions/'+x['id']+'/attendance');saved=ok(client.put(url,json=payload));assert ok(client.put(url,json=payload))==saved
    x=ok(session_action(client,r,saved,'complete'));ok(action(client,r,'archive'))
    assert get(p,'items/'+r['id']+'/sessions/'+x['id'])['marks'][0]['status']=='present'
    assert session_action(client,r,x,'reopen').status_code==409
