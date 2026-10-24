from concurrent.futures import ThreadPoolExecutor
from datetime import date,datetime,timedelta,timezone
from uuid import uuid4
import base64,csv,io,json
import pytest
from backend import api,registers
from backend.models import Room
from test_access import client,enable,create,signed

def body(**kw):return dict(requestId=str(uuid4()),**kw)
def path(part):return '/api/registers/'+part+('&' if '?' in part else '?')+'workspace=demo'
def ok(r):
    assert r.status_code==200,r.text
    return r.json()
def get(c,part):return ok(c.get(path(part)))
def post(c,part,**kw):return c.post(path(part),json=body(**kw))
def put(c,part,**kw):return c.put(path(part),json=body(**kw))
def ago(**kw):return (datetime.now(timezone.utc)-timedelta(**kw)).isoformat()
def ahead(**kw):return (datetime.now(timezone.utc)+timedelta(**kw)).isoformat()
def school_day(c,days=0):return (date.fromisoformat(get(c,'options')['today'])+timedelta(days=days)).isoformat()
def assign(c,*pairs,family=None):
    cfg=get(c,'options')['config']
    return ok(put(c,'config',version=cfg['version'],reason='Назначение прав',familySummaries=cfg['familySummaries'] if family is None else family,assignments=[dict(accountId=u['id'],capabilities=caps) for u,caps in pairs]))
def add_room(id='rt',name='Временный'):
    state=api.read('demo');state.rooms.append(Room(id=id,name=name));api.save('demo',state)
def drop_room(id='rt'):
    state=api.read('demo');state.rooms=[x for x in state.rooms if x.id!=id];api.save('demo',state)
def rows(r):
    assert r.status_code==200,r.text
    return list(csv.reader(io.StringIO(r.text.lstrip('﻿'))))

@pytest.fixture
def setup(client):
    enable(client)
    guard=create(client,'staff',login='guard',name='Охранник Тестов');teacher=create(client,'teacher',name='Учитель Тестов')
    parent=create(client,'parent',name='Родитель Первый');student=create(client,'student',name='Ученик Тестов')
    other=create(client,'parent',login='other',name='Родитель Второй',grants=[dict(workspace='demo',view='student',entityIds=['s3'])])
    return guard,teacher,parent,student,other

def test_capabilities_are_per_register_and_revocable(client,setup):
    guard,teacher,parent,*_=setup;g=signed('guard')
    assert get(g,'options')['capabilities']==[]
    for part in ['visitors','incidents','inventory']:assert g.get(path(part)).status_code==403
    assign(client,(guard,['visitors']))
    assert g.get(path('visitors')).status_code==200 and g.get(path('incidents')).status_code==403 and g.get(path('inventory')).status_code==403
    options=get(g,'options');assert options['capabilities']==['visitors'] and 'students' not in options and 'config' not in options and 'categories' not in options
    assert {r['name'] for r in options['rooms']}=={'301','302','303'} and options['staff']
    assert g.put(path('config'),json=body(version=1,reason='Попытка',assignments=[])).status_code==403
    # The guard refuses an unassigned teacher before any record is touched.
    t=signed('teacher');assert get(t,'options')['capabilities']==[]
    res=t.get(path('visitors'));assert res.status_code==403 and 'Журнал посетителей' in res.json()['detail']
    cfg=get(client,'options')['config']
    assert put(client,'config',version=cfg['version'],reason='Родитель',assignments=[dict(accountId=parent['id'],capabilities=['visitors'])]).status_code==422
    assert put(client,'config',version=cfg['version'],reason='Администратор',assignments=[dict(accountId='nobody',capabilities=['visitors'])]).status_code==422
    admin=client.get('/api/auth/status').json()['user']
    assert put(client,'config',version=cfg['version'],reason='Администратор',assignments=[dict(accountId=admin['id'],capabilities=['visitors'])]).status_code==422
    assert put(client,'config',version=cfg['version'],reason='Дубль',assignments=[dict(accountId=guard['id'],capabilities=['visitors','visitors'])]).status_code==422
    assert put(client,'config',version=cfg['version'],reason='Дубль',assignments=[dict(accountId=guard['id'],capabilities=['visitors']),dict(accountId=guard['id'],capabilities=['incidents'])]).status_code==422
    assert put(client,'config',version=cfg['version']-1,reason='Старая версия',assignments=[]).status_code==409
    with api.connect() as db:db.execute("UPDATE accounts SET role='editor',version=version+1 WHERE id=?",(guard['id'],))
    assert g.get(path('visitors')).status_code==403
    assert [a['current'] for a in get(client,'options')['config']['assignments']]==[False]
    assign(client,(guard,['visitors','inventory']));assert get(g,'options')['capabilities']==['inventory','visitors'] and g.get(path('visitors')).status_code==200
    assert g.get(path('history/config/config')).status_code==403
    history=get(client,'history/config/config')['items'];assert history[0]['action']=='configured' and history[0]['data']['after']['assignments'][0]['capabilities']==['inventory','visitors'] and history[0]['data']['before']['assignments']
    with api.connect() as db:assert db.execute("SELECT count(*) FROM audit WHERE action='registers.configured'").fetchone()[0]==2

def test_assignments_do_not_cross_workspaces(client,setup):
    guard=setup[0];assign(client,(guard,['visitors']))
    g=signed('guard');assert g.get('/api/registers/visitors?workspace=school').status_code==403
    with api.connect() as db:db.execute("UPDATE accounts SET permissions=? WHERE id=?",(json.dumps(dict(workspaces=['demo','school'],grants=[])),guard['id']))
    res=g.get('/api/registers/visitors?workspace=school');assert res.status_code==403 and 'Журнал посетителей' in res.json()['detail']

def test_parent_and_student_options_expose_no_staff_data(client,setup):
    for login in ['parent','student']:
        options=get(signed(login),'options')
        assert options['capabilities']==[] and not options['admin'] and not options.get('staff') and 'rooms' not in options and 'students' not in options and 'config' not in options
        for part in ['visitors','incidents','inventory']:assert signed(login).get(path(part)).status_code==403
    assert get(signed('parent'),'options')['family'] and not get(signed('student'),'options')['family']

def test_registers_never_change_timetable(client,setup):
    guard,teacher,*_=setup;state=api.read('demo');assign(client,(guard,['visitors','incidents','inventory']))
    ok(post(client,'visitors',version=0,data=visitor()));ok(post(client,'incidents',version=0,data=incident(studentIds=['s1'])));ok(post(client,'inventory',version=0,data=item()))
    assert api.read('demo')==state

def visitor(**kw):return dict(name='Иван Тестов',purpose='Встреча с завучем',hostText='Завуч',badge='',documentType='',note='')|kw
def arrive(c,**kw):return post(c,'visitors',version=0,data=visitor(**kw))
def depart(c,r,**kw):return post(c,'visitors/'+r['id']+'/action',version=r['version'],action='depart',**kw)

def test_visitor_arrival_departure_and_inside_view(client,setup):
    guard,teacher,*_=setup;assign(client,(guard,['visitors']));g=signed('guard')
    first=ok(arrive(g,name='Первый Гость',badge='A1',hostId=teacher['id'],documentType='passport',arrivedAt=ago(minutes=30)))
    second=ok(arrive(client,name='Второй Гость',badge='a2',hostId=teacher['id'],arrivedAt=ago(minutes=20)))
    assert first['status']=='inside' and first['badge']=='A1' and first['hostName']=='Учитель Тестов' and first['author']=='Охранник Тестов' and second['badge']=='A2'
    assert arrive(g,name='Третий Гость',badge='a1').status_code==409
    inside=get(g,'visitors?inside=true');assert inside['count']==2 and inside['inside']==2 and [x['badge'] for x in inside['items']]==['A1','A2'] and not any(x['overdue'] for x in inside['items'])
    left=ok(depart(g,first));assert left['status']=='left' and left['departed'] and left['version']==2
    assert get(g,'visitors?inside=true')['count']==1
    again=depart(g,left);assert again.status_code==409 and 'Уход уже' in again.json()['detail']
    third=ok(arrive(g,name='Третий Гость',badge='A1',hostText='Бухгалтерия',arrivedAt=ago(minutes=5)))
    day=get(g,'options')['today'];listing=get(g,'visitors?day='+day)
    assert [x['data']['name'] for x in listing['items']]==['Первый Гость','Второй Гость','Третий Гость'] and listing['inside']==2 and listing['count']==3
    assert get(g,'visitors?day='+day+'&q=ВТОРОЙ')['count']==1
    assert arrive(g,name='Будущий Гость',arrivedAt=ahead(hours=1)).status_code==422
    assert arrive(g,name='Давний Гость',arrivedAt=ago(days=8)).status_code==422
    assert depart(g,second,at=ago(hours=2)).status_code==422
    assert depart(g,second,at=ahead(hours=1)).status_code==422
    assert post(g,'visitors/'+second['id']+'/action',version=second['version'],action='void').status_code==422
    voided=ok(post(g,'visitors/'+second['id']+'/action',version=second['version'],action='void',reason='Запись внесена по ошибке'))
    assert voided['status']=='voided' and get(g,'visitors?inside=true')['count']==1
    assert [x['status'] for x in get(g,'visitors?day='+day)['items']]==['left','voided','inside']
    assert post(g,'visitors/'+second['id']+'/action',version=voided['version'],action='void',reason='Повторно').status_code==409
    assert arrive(g,name='Четвёртый Гость',badge='a2',hostText='Секретарь').status_code==200
    actions=[x['action'] for x in get(g,'history/visitors/'+first['id'])['items']];assert actions==['departed','arrived']
    assert signed('teacher').get(path('history/visitors/'+first['id'])).status_code==403

def test_visitor_edit_corrections_and_hosts(client,setup):
    guard,teacher,*_=setup;assign(client,(guard,['visitors']));g=signed('guard')
    r=ok(arrive(g,name='Гость Ошибкин',badge='B1',hostId=teacher['id'],arrivedAt=ago(hours=2)))
    data=lambda **kw:r['data']|dict(badge=r['badge'],arrivedAt=r['arrived'])|kw
    assert put(g,'visitors/'+r['id'],version=r['version'],data=data(name='Гость Исправленный')).status_code==422
    edited=ok(put(g,'visitors/'+r['id'],version=r['version'],data=data(name='Гость Исправленный',badge='b2'),reason='Опечатка в имени'))
    assert edited['version']==2 and edited['data']['name']=='Гость Исправленный' and edited['badge']=='B2' and edited['arrived']==r['arrived']
    # An unchanged host stays even after the account is gone; a new inactive host is refused.
    with api.connect() as db:db.execute('UPDATE accounts SET active=0,version=version+1 WHERE id=?',(teacher['id'],))
    kept=ok(put(g,'visitors/'+r['id'],version=2,data=data(name='Гость Исправленный',badge='B2',note='Заметка'),reason='Добавлена заметка'));assert kept['hostName']=='Учитель Тестов' and kept['data']['hostId']==teacher['id']
    assert put(g,'visitors/'+r['id'],version=3,data=data(hostId='missing'),reason='Другой сотрудник').status_code==422
    other=ok(arrive(g,name='Другой Гость',badge='B3',hostText='Директор'))
    assert put(g,'visitors/'+other['id'],version=1,data=other['data']|dict(badge='b2',arrivedAt=other['arrived']),reason='Пропуск как у другого').status_code==409
    moved=ok(put(g,'visitors/'+other['id'],version=1,data=other['data']|dict(badge='B3',arrivedAt=ago(days=2)),reason='Пришёл позавчера'));assert moved['day']!=other['day'] and moved['overdue']
    done=ok(depart(g,moved));assert put(g,'visitors/'+other['id'],version=done['version'],data=other['data']|dict(arrivedAt=ahead(minutes=1)),reason='Позже ухода').status_code==422
    voided=ok(post(g,'visitors/'+r['id']+'/action',version=3,action='void',reason='Дубликат записи'))
    assert put(g,'visitors/'+r['id'],version=voided['version'],data=data(),reason='Правка аннулированной').status_code==409
    history=get(g,'history/visitors/'+r['id'])['items'];assert [x['action'] for x in history]==['voided','edited','edited','arrived'] and history[1]['data']['before']['note']=='' and history[1]['data']['after']['note']=='Заметка'

def test_visitor_host_notification_and_no_document_numbers(client,setup):
    guard,teacher,*_=setup;assign(client,(guard,['visitors']));g=signed('guard')
    assert arrive(g,name='Иванов 123456789').status_code==422
    assert arrive(g,purpose='Звонил +7 (701) 123-45-67').status_code==422
    assert arrive(g,note='Паспорт N 12 34 56 78 90').status_code==422
    assert arrive(g,hostText='Кабинет 12').status_code==200
    assert arrive(g,hostText='').status_code==422 and arrive(g,badge='плохой пропуск!').status_code==422 and arrive(g,documentType='123456789').status_code==422
    def notified():
        with api.connect() as db:return [r[0] for r in db.execute("SELECT user_id FROM notifications WHERE action='registers.visitor'")]
    assert notified()==[]
    ok(arrive(g,hostId=teacher['id']));assert notified()==[teacher['id']]
    ok(arrive(g,hostId=guard['id']));assert notified()==[teacher['id']]
    ok(arrive(g,hostText='Секретарь'));assert notified()==[teacher['id']]

def test_visitor_overdue_previous_day(client,setup,monkeypatch):
    guard=setup[0];assign(client,(guard,['visitors']));g=signed('guard')
    old=ok(arrive(g,name='Вчерашний Гость',arrivedAt=ago(days=1,hours=2)));assert old['overdue'] and old['data']['hostText']=='Завуч'
    fresh=ok(arrive(g,name='Сегодняшний Гость',arrivedAt=ago(minutes=1)))
    inside=get(g,'visitors?inside=true')['items'];assert [x['data']['name'] for x in inside]==['Вчерашний Гость','Сегодняшний Гость'] and [x['overdue'] for x in inside]==[True,False]
    monkeypatch.setattr(registers,'today',lambda:date.today()+timedelta(days=2))
    assert all(x['overdue'] for x in get(g,'visitors?inside=true')['items'])
    left=ok(depart(g,old,at=ago(hours=20)));assert left['status']=='left' and not left['overdue']

def test_visitor_export_csv(client,setup):
    guard,teacher,*_=setup;assign(client,(guard,['visitors']));g=signed('guard')
    ok(arrive(g,name='Иван Тестов',purpose='=HYPERLINK(1)',badge='A1',hostId=teacher['id'],documentType='passport',arrivedAt=ago(minutes=10),note='Из комиссии'))
    gone=ok(arrive(g,name='Ушедший Гость',hostText='Директор',arrivedAt=ago(minutes=40)));ok(depart(g,gone))
    ok(arrive(g,name='Вчерашний Гость',arrivedAt=ago(days=2)))
    voided=ok(arrive(g,name='Ошибочный Гость',hostText='Завуч',arrivedAt=ago(minutes=50)));ok(post(g,'visitors/'+voided['id']+'/action',version=1,action='void',reason='Запись внесена ошибочно'))
    day=get(g,'options')['today'];res=g.get(path('visitors/export?day='+day))
    assert res.content.startswith(b'\xef\xbb\xbf') and day in res.headers['content-disposition'] and 'visitors-' in res.headers['content-disposition']
    table=rows(res)
    assert table[0]==['Дата','Пришёл','Ушёл','Посетитель','Цель визита','К кому','Пропуск','Документ','Примечание','Состояние','Записал']
    assert [r[3] for r in table[1:]]==['Ошибочный Гость','Ушедший Гость','Иван Тестов'] and "'=HYPERLINK(1)" in res.text
    row=table[3];assert row[0]==day and row[5]=='Учитель Тестов' and row[6]=='A1' and row[7]=='Паспорт' and row[9]=='В здании' and row[10]=='Охранник Тестов' and len(row[1])==5 and row[2]==''
    assert table[1][9]=='Аннулирована' and table[2][2]
    assert 'Вчерашний' not in res.text
    assert signed('teacher').get(path('visitors/export?day='+day)).status_code==403 and g.get(path('visitors/export')).status_code==422

def test_visitor_replay_and_versions(client,setup):
    guard=setup[0];assign(client,(guard,['visitors']));g=signed('guard')
    payload=body(version=0,data=visitor(name='Повторный Гость',badge='R1'))
    first=ok(g.post(path('visitors'),json=payload));assert ok(g.post(path('visitors'),json=payload))==first
    changed=g.post(path('visitors'),json=payload|dict(data=visitor(name='Другой Гость')));assert changed.status_code==409 and 'Идентификатор' in changed.json()['detail']
    assert get(g,'visitors?inside=true')['count']==1
    assert put(g,'visitors/'+first['id'],version=7,data=first['data']|dict(badge='R1'),reason='Устаревшая правка').status_code==409
    assert g.post(path('visitors'),json=body(version=3,data=visitor())).status_code==422
    assert depart(g,first|dict(version=4)).status_code==409 and g.get(path('visitors/missing-id')).status_code in (404,405)
    ok(depart(g,first))

def incident(**kw):
    return dict(kind='accident',severity='minor',occurredAt=ago(minutes=30),place='Спортивный зал',roomId='',studentIds=[],staffIds=[],otherPersons='',description='Ученик упал на разминке и ушиб колено.',firstAid='Приложен холод.',parentNotices=[],followUp='',familySummary='')|kw
def notice(student='s1',**kw):return dict(studentId=student,by='Классный руководитель',at=ago(minutes=10),method='phone',note='Родитель извещён')|kw
def started(c,**kw):return ok(post(c,'incidents',version=0,data=incident(**kw)))
def edited(c,r,reason='Уточнение записи',**kw):return put(c,'incidents/'+r['id'],version=r['version'],data=r['data']|kw,reason=reason)
def step(c,r,action,reason='Решение администрации'):return post(c,'incidents/'+r['id']+'/action',version=r['version'],action=action,reason=reason)

def test_incident_numbering_under_concurrency_has_no_gaps(client,setup,monkeypatch):
    monkeypatch.setattr(registers,'today',lambda:date(2026,10,15))
    with ThreadPoolExecutor(max_workers=2) as pool:made=list(pool.map(lambda c:started(c,place='Коридор'),[client,signed('admin')]))
    assert {r['number'] for r in made}=={'2026-0001','2026-0002'}
    with monkeypatch.context() as patch:
        patch.setattr(api.registers.notifications,'deliver',lambda *a,**k:(_ for _ in ()).throw(RuntimeError('rollback')))
        with pytest.raises(RuntimeError):started(client)
    third=started(client);assert third['number']=='2026-0003'
    with api.connect() as db:
        assert db.execute('SELECT count(*) FROM registers_incidents').fetchone()[0]==3 and db.execute("SELECT count(*) FROM registers_history WHERE register='incidents'").fetchone()[0]==3
    monkeypatch.setattr(registers,'today',lambda:date(2027,1,2));assert started(client)['number']=='2027-0001'
    assert get(client,'incidents')['items'][0]['number']=='2027-0001'
    assert len(get(client,'incidents?year='+str(date.today().year))['items'])==4 and get(client,'incidents?year=1999')['items']==[]

def test_incident_history_close_reopen(client,setup):
    guard,teacher,*_=setup;assign(client,(guard,['incidents']));g=signed('guard')
    r=started(g,studentIds=['s1'],staffIds=[teacher['id']],roomId='r0')
    assert r['version']==1 and r['status']=='open' and r['roomName']=='301' and r['roomCurrent'] and r['staff']==[dict(id=teacher['id'],name='Учитель Тестов',current=True)] and r['students'][0]['className']=='10A' and r['author']=='Охранник Тестов'
    assert put(g,'incidents/'+r['id'],version=1,data=r['data']).status_code==422
    r=ok(edited(g,r,place='Спортивный зал, раздевалка'));assert r['version']==2
    assert step(g,r,'close').status_code==422 and step(g,r,'close').json()['detail'].startswith('Укажите, кто и когда')
    r=ok(edited(g,r,parentNotices=[notice()]));assert r['version']==3
    closed=ok(step(g,r,'close'));assert closed['status']=='closed' and closed['closedBy']=='Охранник Тестов' and closed['closedAt'] and closed['version']==4
    assert edited(g,closed).status_code==409
    assert step(g,closed,'close').status_code==409 and step(g,closed,'reopen').status_code==403
    reopened=ok(step(client,closed,'reopen'));assert reopened['status']=='open' and not reopened['closedAt'] and reopened['version']==5
    assert step(client,reopened,'reopen').status_code==409
    history=get(g,'history/incidents/'+r['id'])['items'];assert [x['action'] for x in history]==['reopen','close','edited','edited','created']
    assert history[2]['data']['before']['parentNotices']==[] and history[2]['data']['after']['parentNotices'][0]['by']=='Классный руководитель' and history[1]['reason']=='Решение администрации'
    assert get(g,'history/incidents/'+r['id']+'?limit=2')['nextBefore'] and len(get(g,'history/incidents/'+r['id']+'?limit=2')['items'])==2
    assert g.get(path('history/incidents/missing')).status_code==404
    plain=started(g,kind='incident',severity='serious',description='Повреждено школьное имущество в коридоре.');assert ok(step(g,plain,'close'))['status']=='closed'

def test_incident_validation_and_people(client,setup):
    guard,teacher,parent,*_=setup;assign(client,(guard,['incidents']));g=signed('guard')
    bad=lambda **kw:post(g,'incidents',version=0,data=incident(**kw))
    assert bad(occurredAt=ahead(hours=1)).status_code==422 and bad(occurredAt=ago(days=400)).status_code==422
    assert bad(studentIds=['s1','s1']).status_code==422 and bad(studentIds=['nobody']).status_code==422 and bad(staffIds=[parent['id']]).status_code==422 and bad(staffIds=['nobody']).status_code==422
    assert bad(roomId='r99').status_code==422 and bad(description='коротко').status_code==422 and bad(kind='other').status_code==422
    assert bad(studentIds=['s1'],parentNotices=[notice('s3')]).status_code==422
    assert bad(studentIds=['s1'],parentNotices=[notice(at=ago(hours=5))]).status_code==422 and bad(studentIds=['s1'],parentNotices=[notice(at=ahead(hours=1))]).status_code==422
    assert bad(studentIds=['s1'],occurredAt=ago(days=300),parentNotices=[notice(at=ago(days=300))]).status_code==200
    found=get(g,'incidents?studentId=s1');assert found['items'][0]['studentCount']==1 and get(g,'incidents?studentId=s2')['items']==[]
    assert get(g,'incidents?q=РАЗМИНКЕ')['items'] and get(g,'incidents?status=closed')['items']==[] and g.get(path('incidents?status=bogus')).status_code==422
    assert 'students' in get(g,'options') and get(g,'options')['students'][0]['className']=='10A'

def test_incident_access_is_explicit(client,setup):
    guard,teacher,parent,*_=setup;t=signed('teacher');p=signed('parent');r=started(client)
    for c in [t,p,signed('student'),signed('guard')]:
        assert c.get(path('incidents')).status_code==403 and c.get(path('incidents/'+r['id'])).status_code==403 and c.get(path('history/incidents/'+r['id'])).status_code==403
        assert c.post(path('incidents'),json=body(version=0,data=incident())).status_code==403 and step(c,r,'close').status_code==403
    assign(client,(guard,['visitors']));g=signed('guard')
    assert g.get(path('incidents')).status_code==403 and g.get(path('incidents/'+r['id'])).status_code==403 and g.get(path('history/incidents/'+r['id'])).status_code==403
    assign(client,(guard,['incidents']));assert g.get(path('incidents')).status_code==200 and g.get(path('incidents/'+r['id'])).status_code==200 and g.get(path('visitors')).status_code==403
    assert p.get(path('family/incidents')).status_code==200 and t.get(path('family/incidents')).status_code==403 and g.get(path('family/incidents')).status_code==403 and signed('student').get(path('family/incidents')).status_code==403
    assert g.get('/api/registers/incidents/'+r['id']+'?workspace=school').status_code==403

def test_incident_family_summary_is_opt_in_and_private(client,setup):
    guard,teacher,parent,student,other=setup;create(client,'parent',login='third',name='Родитель Третий',grants=[dict(workspace='demo',view='student',entityIds=['s2'])])
    assign(client,(guard,['incidents']));g=signed('guard');p,o,third=signed('parent'),signed('other'),signed('third')
    r=started(g,studentIds=['s1','s3'],place='Секретное место',description='Секретное описание происшествия.',familySummary='Ребёнок получил лёгкую травму, помощь оказана.')
    assert step(client,r,'publish_summary').status_code==409 and step(g,r,'publish_summary').status_code==403
    assert get(p,'family/incidents')=={'items':[],'enabled':False}
    assign(client,(guard,['incidents']),family=True);assert get(client,'options')['familySummaries'] and get(g,'options')['familySummaries']
    assert step(client,started(g,studentIds=['s1']),'publish_summary').status_code==422 and step(client,started(g,familySummary='Без учеников в записи.'),'publish_summary').status_code==422
    r=ok(step(client,r,'publish_summary'));assert r['summaryPublished'] and r['publishedAt']
    assert step(client,r,'publish_summary').status_code==409 and edited(g,r,familySummary='Другой текст').status_code==409
    r=ok(edited(g,r,followUp='Беседа проведена'))
    mine=get(p,'family/incidents');assert mine['enabled'] and len(mine['items'])==1
    text=json.dumps(mine,ensure_ascii=False)
    for secret in ['Ученик 3','Секретн','Спортивн','Классный','Охранник','Администратор','Беседа','description','place','firstAid','parentNotices','staff']:assert secret not in text
    assert 'лёгкую травму' in text and set(mine['items'][0])=={'number','occurredOn','kind','summary','publishedAt','children'} and mine['items'][0]['children']==['Ученик 1 (пример)']
    assert get(o,'family/incidents')['items'][0]['children']==['Ученик 3 (пример)'] and get(third,'family/incidents')['items']==[]
    with api.connect() as db:assert {x[0] for x in db.execute("SELECT user_id FROM notifications WHERE action='registers.summary'")}=={parent['id'],other['id']}
    withdrawn=ok(step(client,r,'withdraw_summary'));assert not withdrawn['summaryPublished'] and get(p,'family/incidents')['items']==[] and step(client,withdrawn,'withdraw_summary').status_code==409
    ok(step(client,withdrawn,'publish_summary'));assert len(get(p,'family/incidents')['items'])==1
    # Turning the school setting off hides every published summary at once.
    assign(client,(guard,['incidents']),family=False);assert get(p,'family/incidents')=={'items':[],'enabled':False}
    assert [x['action'] for x in get(g,'history/incidents/'+r['id'])['items']][:4]==['publish_summary','withdraw_summary','edited','publish_summary']
    # A pupil who no longer attends is not shown to the family.
    assign(client,(guard,['incidents']),family=True);state=api.read('demo');state.students[0].active=False;api.save('demo',state)
    assert p.get(path('family/incidents')).status_code==403 and get(o,'family/incidents')['items'][0]['children']==['Ученик 3 (пример)']

def test_incident_keeps_frozen_pupil_after_leaving(client,setup):
    guard,teacher,*_=setup;assign(client,(guard,['incidents']));g=signed('guard');add_room()
    r=started(g,studentIds=['s1','s2'],staffIds=[teacher['id']],roomId='rt')
    state=api.read('demo');state.students[0].active=False;api.save('demo',state);drop_room()
    with api.connect() as db:db.execute('UPDATE accounts SET active=0,version=version+1 WHERE id=?',(teacher['id'],))
    d=get(g,'incidents/'+r['id']);assert [(x['name'],x['current']) for x in d['students']]==[('Ученик 1 (пример)',False),('Ученик 2 (пример)',True)] and d['roomCurrent'] is False and d['roomName']=='Временный' and d['staff']==[dict(id=teacher['id'],name='Учитель Тестов',current=False)]
    r=ok(edited(g,r,description='Уточнённое описание после выздоровления.'));assert r['students'][0]['name']=='Ученик 1 (пример)' and not r['students'][0]['current'] and r['roomName']=='Временный' and r['staff'][0]['name']=='Учитель Тестов'
    r=ok(edited(g,r,studentIds=['s1','s2','s3'],reason='Добавлен ученик'));assert [x['id'] for x in r['students']]==['s1','s2','s3']
    state=api.read('demo');state.students[3].active=False;api.save('demo',state)
    assert edited(g,r,studentIds=['s1','s4'],reason='Неактивный ученик').status_code==422 and edited(g,r,staffIds=['nobody'],reason='Чужой сотрудник').status_code==422
    assert get(g,'incidents?studentId=s1')['items'][0]['id']==r['id']


def item(**kw):return dict(number='INV-1',title='Проектор',category='Техника',roomId='r0',location='Шкаф у окна',responsibleId='',condition='good',acquiredOn='2025-09-01',serial='SN-100',note='')|kw
def inv(c,**kw):return ok(post(c,'inventory',version=0,data=item(**kw)))
def move(c,r,reason='Нужен в другом кабинете',**kw):return post(c,'inventory/'+r['id']+'/move',version=r['version'],reason=reason,**kw)
def write_off(c,r,**kw):return post(c,'inventory/'+r['id']+'/action',version=r['version'],action='write_off',reason='Неисправен, списан по акту',**kw)

def test_inventory_moves_writeoff_and_history(client,setup):
    guard,teacher,*_=setup;assign(client,(guard,['inventory']));g=signed('guard');day=school_day(client)
    r=inv(g,responsibleId=teacher['id']);assert r['roomName']=='301' and r['responsibleName']=='Учитель Тестов' and r['roomCurrent'] and r['responsibleCurrent'] and r['data']['number']=='INV-1'
    assert post(g,'inventory',version=0,data=item(number='inv-1',title='Другой')).status_code==409
    assert post(g,'inventory',version=0,data=item(number='INV-2',roomId='r99')).status_code==422 and post(g,'inventory',version=0,data=item(number='INV-2',responsibleId='nobody')).status_code==422
    assert post(g,'inventory',version=0,data=item(number='INV-2',roomId='',location='')).status_code==422 and post(g,'inventory',version=0,data=item(number='bad number!')).status_code==422
    assert post(g,'inventory',version=0,data=item(number='INV-2',acquiredOn=school_day(client,3))).status_code==422 and post(g,'inventory',version=3,data=item(number='INV-2')).status_code==422
    with api.connect() as db:assert db.execute("SELECT count(*) FROM notifications WHERE action='registers.inventory'").fetchone()[0]==1
    moved=ok(move(g,r,roomId='r1',location='Стеллаж 2'));assert moved['roomName']=='302' and moved['version']==2 and moved['data']['roomId']=='r1'
    moves=get(g,'inventory/'+r['id']+'/moves')['items'];assert len(moves)==1 and moves[0]['source']['roomId']=='r0' and moves[0]['target']['roomId']=='r1' and moves[0]['target']['location']=='Стеллаж 2' and moves[0]['actor']=='Охранник Тестов' and moves[0]['movedOn']==day and moves[0]['reason']
    assert move(g,moved,roomId='r1',location='Стеллаж 2').status_code==422 and move(g,r,roomId='r2').status_code==409 and move(g,moved,roomId='r99').status_code==422
    assert move(g,moved,roomId='r2',movedOn=school_day(client,1)).status_code==422 and move(g,moved,roomId='r2',movedOn='2020-01-01').status_code==422 and move(g,moved,reason='ab',roomId='r2').status_code==422
    assert put(g,'inventory/'+r['id'],version=2,data=moved['data']|dict(roomId='r2'),reason='Смена кабинета').status_code==422
    assert put(g,'inventory/'+r['id'],version=2,data=moved['data']|dict(location='Другое'),reason='Смена места').status_code==422
    assert put(g,'inventory/'+r['id'],version=2,data=moved['data']|dict(title='Проектор Epson'),reason='').status_code==422
    fixed=ok(put(g,'inventory/'+r['id'],version=2,data=moved['data']|dict(title='Проектор Epson',number='inv-7',condition='fair'),reason='Уточнено название'));assert fixed['version']==3 and fixed['number']=='inv-7'
    other=inv(g,number='INV-8');assert put(g,'inventory/'+other['id'],version=1,data=other['data']|dict(number='INV-7'),reason='Номер как у другого').status_code==409
    out=ok(write_off(g,fixed));assert out['status']=='written_off' and out['writtenOff']==dict(on=day,reason='Неисправен, списан по акту',by='Охранник Тестов')
    assert move(g,out,roomId='r2').status_code==409 and put(g,'inventory/'+r['id'],version=out['version'],data=out['data'],reason='Правка списанного').status_code==409 and write_off(g,out).status_code==409
    assert post(g,'inventory/'+r['id']+'/action',version=out['version'],action='restore',reason='Ошибочное списание').status_code==403
    back=ok(post(client,'inventory/'+r['id']+'/action',version=out['version'],action='restore',reason='Ошибочное списание'));assert back['status']=='active' and back['writtenOff']['on']==''
    assert [x['action'] for x in get(g,'history/inventory/'+r['id'])['items']]==['restored','written_off','edited','moved','created']
    assert [x['id'] for x in get(g,'inventory?roomId=r1')['items']]==[r['id']] and [x['number'] for x in get(g,'inventory?roomId=r0')['items']]==['INV-8']
    assert get(g,'inventory?status=written_off')['items']==[] and get(g,'inventory?q=EPSON')['items'][0]['title']=='Проектор Epson' and get(g,'inventory?category=Мебель')['items']==[]
    ok(write_off(g,back,on='2025-10-01'));assert [x['number'] for x in get(g,'inventory?status=written_off')['items']]==['inv-7'] and [x['number'] for x in get(g,'inventory')['items']]==['INV-8']
    assert write_off(g,other,on='2025-08-01').status_code==422 and write_off(g,other,on=school_day(client,1)).status_code==422
    assert get(g,'options')['categories']==['Техника'] and get(g,'inventory?limit=1')['nextBefore'] is None

def test_inventory_paging_filters_and_responsible(client,setup):
    guard,teacher,*_=setup;assign(client,(guard,['inventory']));g=signed('guard')
    for i in range(1,4):inv(g,number=f'P-{i}',title=f'Стул {i}',category='Мебель',responsibleId=teacher['id'] if i==2 else '')
    first=get(g,'inventory?limit=2');assert [x['number'] for x in first['items']]==['P-3','P-2'] and first['nextBefore']
    last=get(g,'inventory?limit=2&before='+str(first['nextBefore']));assert [x['number'] for x in last['items']]==['P-1'] and last['nextBefore'] is None
    assert [x['number'] for x in get(g,'inventory?responsibleId='+teacher['id'])['items']]==['P-2'] and get(g,'inventory?q=стул 3')['items'][0]['number']=='P-3'

def test_inventory_responsible_room_and_access(client,setup):
    guard,teacher,*_=setup;assign(client,(guard,['inventory']));g=signed('guard');t=signed('teacher')
    add_room();r=inv(g,roomId='rt',responsibleId=teacher['id']);assert t.get(path('inventory')).status_code==403 and t.get(path('inventory/'+r['id'])).status_code==403 and t.get(path('inventory/'+r['id']+'/moves')).status_code==403
    drop_room()
    with api.connect() as db:db.execute('UPDATE accounts SET active=0,version=version+1 WHERE id=?',(teacher['id'],))
    d=get(g,'inventory/'+r['id']);assert not d['roomCurrent'] and not d['responsibleCurrent'] and d['roomName']=='Временный' and d['responsibleName']=='Учитель Тестов'
    d=ok(put(g,'inventory/'+r['id'],version=1,data=d['data']|dict(note='Без кабинета'),reason='Заметка'));assert d['roomName']=='Временный' and d['data']['note']=='Без кабинета'
    assert move(g,d,roomId='rt').status_code==422
    d=ok(move(g,d,roomId='r1'));assert d['roomCurrent'] and d['roomName']=='302'
    assert ok(move(g,d,roomId='',location='Склад'))['roomName']=='' and not get(g,'inventory/'+r['id'])['roomCurrent']
    assert g.get(path('inventory/missing')).status_code==404 and g.get(path('inventory/missing/moves')).status_code==404 and g.get(path('visitors')).status_code==403

def test_inventory_export_csv(client,setup):
    guard=setup[0];assign(client,(guard,['inventory']));g=signed('guard')
    inv(g,number='A-1',title='+SUM(1)',condition='broken',note='=cmd');gone=inv(g,number='A-2',title='Старый стол',category='Мебель',acquiredOn=None);ok(write_off(g,gone))
    res=g.get(path('inventory/export'));table=rows(res)
    assert res.content.startswith(b'\xef\xbb\xbf') and res.headers['content-disposition'].endswith('inventory.csv"')
    assert table[0]==['Инвентарный номер','Наименование','Категория','Кабинет','Место','Ответственный','Состояние','Дата поступления','Серийный номер','Примечание','Статус','Дата списания','Причина списания']
    assert len(table)==2 and table[1][1]=="'+SUM(1)" and table[1][6]=='Неисправно' and table[1][9]=="'=cmd" and table[1][10]=='На учёте' and table[1][3]=='301'
    old=rows(g.get(path('inventory/export?status=written_off')));assert old[1][0]=='A-2' and old[1][10]=='Списан' and old[1][12]=='Неисправен, списан по акту' and old[1][7]==''
    assert len(rows(g.get(path('inventory/export?category=Мебель'))))==1 and len(rows(g.get(path('inventory/export?q=sum'))))==2
    assert signed('teacher').get(path('inventory/export')).status_code==403

COLUMNS=['Инвентарный номер','Наименование','Категория','Кабинет','Место','Ответственный','Состояние','Дата поступления','Серийный номер','Примечание']
def sheet(table,delimiter=';',encoding='cp1251'):
    return base64.b64encode(('\r\n'.join(delimiter.join(r) for r in table)+'\r\n').encode(encoding)).decode()
def preview(c,table,**kw):return c.post(path('inventory/import/preview'),json=dict(filename='inventory.csv',content=sheet(table,**kw)))
def apply(c,table,hash='',**kw):return c.post(path('inventory/import'),json=body(filename='inventory.csv',content=sheet(table),previewHash=hash,reason='Начальное заполнение')|kw)

def test_inventory_import_preview_and_apply_all_or_nothing(client,setup):
    guard,teacher,*_=setup;assign(client,(guard,['inventory']));g=signed('guard');inv(g)
    bad=[COLUMNS,['OK-1','Парта','Мебель','301','','','хорошее','01.09.2025','','Новая'],['BAD-ROOM','Стул','Мебель','999','','','','',''],['DUP','Стол','Мебель','302','Склад','Учитель Тестов','Новое','2025-09-02','',''],['dup','Стол 2','Мебель','302','','','','',''],['inv-1','Проектор','Техника','301','','','','',''],['BAD-COND','Шкаф','Мебель','301','','','отличное','',''],['NO-PLACE','Лампа','Свет','','','','','',''],['BAD-DATE','Лампа','Свет','301','','','','31.02.2025','']]
    p=ok(preview(g,bad));assert [i['number'] for i in p['items']]==['OK-1','DUP'] and [e['line'] for e in p['errors']]==[3,5,6,7,8,9] and p['warnings']==[] and len(p['previewHash'])==64
    assert p['items'][0]['roomId']=='r0' and p['items'][0]['condition']=='good' and p['items'][0]['acquiredOn']=='2025-09-01' and p['items'][1]['responsibleName']=='Учитель Тестов' and p['items'][1]['roomName']=='302'
    assert 'Кабинет не найден' in p['errors'][0]['message'] and 'повторяется' in p['errors'][1]['message'] and 'уже используется' in p['errors'][2]['message'] and 'Состояние' in p['errors'][3]['message'] and 'место' in p['errors'][4]['message'] and 'Дата' in p['errors'][5]['message']
    failed=apply(g,bad,p['previewHash']);assert failed.status_code==422 and 'Ничего не сохранено' in failed.json()['detail']
    count=lambda:get(g,'inventory?limit=200')['items'].__len__();assert count()==1
    good=[bad[0],*bad[1:2],bad[3]];p=ok(preview(g,good));assert len(p['items'])==2 and not p['errors']
    payload=body(filename='inventory.csv',content=sheet(good),previewHash=p['previewHash'],reason='Начальное заполнение')
    done=ok(g.post(path('inventory/import'),json=payload));assert done['count']==2 and count()==3 and ok(g.post(path('inventory/import'),json=payload))==done and count()==3
    imported=get(g,'inventory/'+done['ids'][0]);assert imported['number']=='OK-1' and imported['roomName']=='301'
    assert [x['action'] for x in get(g,'history/inventory/'+done['ids'][0])['items']]==['imported']
    with api.connect() as db:assert db.execute("SELECT count(*) FROM audit WHERE action='registers.inventory_imported'").fetchone()[0]==1
    again=[COLUMNS,['NEW-1','Лампа','Свет','301','','','','','','']]
    assert apply(g,again,'').status_code==409 and apply(g,again,p['previewHash']).status_code==409 and count()==3
    hash=ok(preview(g,again))['previewHash'];assert apply(g,again,hash).status_code==200 and count()==4
    assert apply(g,again,hash).status_code==422
    many=[COLUMNS,*[[f'M-{i}','Предмет','Разное','301','','','','','',''] for i in range(2001)]]
    assert preview(g,many).status_code==413 and apply(g,many,'x').status_code==413
    assert preview(g,[COLUMNS]).status_code==422 and preview(g,[['Номер','Название'],['1','2']]).status_code==422 and preview(g,[[*COLUMNS,'Наименование'],['1']*11]).status_code==422
    assert g.post(path('inventory/import/preview'),json=dict(filename='inventory.csv',content='not base64!')).status_code==422 and g.post(path('inventory/import/preview'),json=dict(filename='evil.exe',content=sheet(again))).status_code==422
    assert g.post(path('inventory/import/preview'),json=dict(filename='broken.xlsx',content=base64.b64encode(b'not a workbook').decode())).status_code==422
    for c in [signed('teacher'),signed('parent')]:assert preview(c,again).status_code==403 and apply(c,again,hash).status_code==403

def test_inventory_import_reads_utf8_comma_and_xlsx(client,setup):
    guard=setup[0];assign(client,(guard,['inventory']));g=signed('guard')
    p=ok(preview(g,[COLUMNS,['U-1','Принтер','Техника','301','','','','','','']],delimiter=',',encoding='utf-8'));assert p['items'][0]['number']=='U-1' and not p['errors']
    from openpyxl import Workbook
    book=Workbook();sheet_=book.active;sheet_.append(COLUMNS);sheet_.append(['X-1','Сканер','Техника',301,'','','Новое',date(2025,9,1),'','']);sheet_.append(['X-2','Формула','Техника','301','','','','','','=1+1'])
    out=io.BytesIO();book.save(out)
    p=ok(g.post(path('inventory/import/preview'),json=dict(filename='inventory.xlsx',content=base64.b64encode(out.getvalue()).decode())))
    assert [i['number'] for i in p['items']]==['X-1','X-2'] and p['items'][0]['roomName']=='301' and p['items'][0]['acquiredOn']=='2025-09-01' and any('формул' in w for w in p['warnings'])

def test_inventory_export_reimports(client,setup):
    guard,teacher,*_=setup;assign(client,(guard,['inventory']));g=signed('guard')
    inv(g,number='E-1',title='Доска',responsibleId=teacher['id'],note='С подставкой');inv(g,number='E-2',title='Шкаф, большой',category='Мебель',roomId='r1',location='')
    text=g.get(path('inventory/export')).text;assert text.count('\n')>=3
    changed=text.replace('E-1','F-1').replace('E-2','F-2')
    p=ok(g.post(path('inventory/import/preview'),json=dict(filename='inventory.csv',content=base64.b64encode(changed.encode('utf-8')).decode())))
    assert sorted(i['number'] for i in p['items'])==['F-1','F-2'] and not p['errors'] and len(p['warnings'])==3 and any('Статус' in w for w in p['warnings'])
    assert next(i for i in p['items'] if i['number']=='F-1')['responsibleName']=='Учитель Тестов' and next(i for i in p['items'] if i['number']=='F-2')['title']=='Шкаф, большой'
    again=ok(g.post(path('inventory/import/preview'),json=dict(filename='inventory.csv',content=base64.b64encode(text.encode('utf-8')).decode())));assert len(again['errors'])==2 and not again['items']

def test_concurrent_writes_keep_badges_numbers_and_config_unique(client,setup):
    guard=setup[0];assign(client,(guard,['visitors','inventory']));g1,g2=signed('guard'),signed('guard')
    with ThreadPoolExecutor(max_workers=2) as pool:made=list(pool.map(lambda c:arrive(c,badge='Z9'),[g1,g2]))
    assert sorted(r.status_code for r in made)==[200,409] and get(g1,'visitors?inside=true')['count']==1
    with ThreadPoolExecutor(max_workers=2) as pool:made=list(pool.map(lambda c:post(c,'inventory',version=0,data=item(number='C-1')),[g1,g2]))
    assert sorted(r.status_code for r in made)==[200,409] and len(get(g1,'inventory')['items'])==1
    version=get(client,'options')['config']['version'];a,b=signed('admin'),signed('admin')
    with ThreadPoolExecutor(max_workers=2) as pool:saved=list(pool.map(lambda c:put(c,'config',version=version,reason='Одновременное сохранение',assignments=[]),[a,b]))
    assert sorted(r.status_code for r in saved)==[200,409] and get(client,'options')['config']['version']==version+1
