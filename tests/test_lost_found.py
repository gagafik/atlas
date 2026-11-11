from concurrent.futures import ThreadPoolExecutor
from datetime import date,datetime,timedelta,timezone
from io import BytesIO
from pathlib import Path
from uuid import uuid4
import json
import pytest
from PIL import Image
from backend import api,lost_found,lost_found_files,homework_files
from test_access import client,enable,create,signed

def body(**kw):return dict(requestId=str(uuid4()),**kw)
def path(part):return '/api/lost-found/'+part+('&' if '?' in part else '?')+'workspace=demo'
def ok(r):
    assert r.status_code==200,r.text
    return r.json()
def get(c,part):return ok(c.get(path(part)))
def post(c,part,**kw):return c.post(path(part),json=body(**kw))
def data(**kw):return dict(title='Синяя куртка',description='Найдена после занятий',foundOn='2026-10-01',foundPlace='Вестибюль',privateNote='Потайной карман слева',pickupInstructions='Кабинет 101, после 15:00')|kw
def item(c,**kw):return ok(post(c,'items',version=0,data=data(**kw)))
def action(c,r,action,**kw):return post(c,'items/'+r['id']+'/action',version=r['version'],action=action,reason='Учёт находки',**kw)
def published(c,**kw):return ok(action(c,item(c,**kw),'publish'))
def claim(c,r,**kw):return ok(post(c,'items/'+r['id']+'/claims',itemVersion=r['version'],studentId='s1',proof='Внутри нашиты инициалы',**kw))
def review(c,r,action,**kw):
    current=get(c,'claims/'+r['id']);return post(c,'claims/'+r['id']+'/action',version=current['version'],itemVersion=current['item']['version'],action=action,reason='Сверены особые приметы',**kw)
def assign(c,*users):
    cfg=get(c,'options')['config'];return ok(c.put(path('config'),json=body(version=cfg['version'],accountIds=[u['id'] for u in users],reason='Школьное дежурство')))
def photo(width=60,height=40):
    b=BytesIO();i=Image.new('RGB',(width,height),'blue');exif=Image.Exif();exif[270]='Private EXIF test';i.save(b,'JPEG',exif=exif);i.close();return b.getvalue()
def upload(c,r,content=None):return c.post(path('items/'+r['id']+'/photos?itemVersion='+str(r['version'])),content=photo() if content is None else content,headers={'Content-Type':'image/jpeg'})
def edit(c,r,**kw):return ok(c.put(path('items/'+r['id']),json=body(version=r['version'],data=r['data']|kw,reason='Уточнение карточки')))
@pytest.fixture
def setup(client,monkeypatch):
    monkeypatch.setattr(lost_found,'today',lambda:date(2026,10,15));enable(client)
    teacher=create(client,'teacher');parent=create(client,'parent');student=create(client,'student');other=create(client,'parent',login='other',grants=[dict(workspace='demo',view='student',entityIds=['s3'])])
    return teacher,parent,student,other

def test_drafts_and_private_fields_never_leak_or_match_search(client,setup):
    parent=signed('parent');r=item(client)
    assert get(parent,'items')['items']==[] and parent.get(path('items/'+r['id'])).status_code==404
    assert parent.get(path('items?status=draft')).status_code==403
    r=ok(action(client,r,'publish'));public=get(parent,'items/'+r['id'])
    assert 'privateNote' not in public['data'] and 'pickupInstructions' not in public['data']
    assert not get(parent,'items?q=Потайной')['items']
    assert get(parent,'items?q=СИНЯЯ')['items'][0]['id']==r['id']
    assert post(parent,'items',version=0,data=data()).status_code==403
    assert get(parent,'options')['staff']==[]

def test_complete_claim_review_collection_and_scoped_export(client,setup):
    state=api.read('demo');p=signed('parent');other=signed('other');r=published(client,title='=Formula title');c=claim(p,r)
    assert other.get(path('claims/'+c['id'])).status_code==404 and get(other,'claims')['items']==[]
    assert p.get(path('claims?all=true')).status_code==403
    assert 'pickupInstructions' not in c and 'privateNote' not in json.dumps(c)
    assert p.get(path('history/'+r['id'])).status_code==403
    assert review(p,c,'approve').status_code==403
    c=ok(review(client,c,'approve'));assert c['status']=='approved' and c['item']['status']=='reserved'
    assert get(p,'claims/'+c['id'])['pickupInstructions'].startswith('Кабинет')
    assert review(client,c,'collect').status_code==422
    c=ok(review(client,c,'collect',receivedBy='Родитель, имя проверено'));assert c['status']=='collected' and c['item']['status']=='returned'
    assert get(p,'items')['items']==[];assert get(p,'claims')['items'][0]['data']['receivedBy']=='Родитель, имя проверено'
    assert other.get(path('items/'+r['id'])).status_code==404
    csv=p.get(path('export'));assert csv.content.startswith(b'\xef\xbb\xbf') and "'=Formula title" in csv.text and 'нашиты' not in csv.text
    assert 'Formula' not in other.get(path('export')).text
    assert api.read('demo')==state

def test_appointment_revocation_and_permission_key(client,setup):
    teacher=setup[0];t=signed('teacher');r=item(client)
    assert action(t,r,'publish').status_code==403
    assign(client,teacher);assert get(t,'options')['canManage'];r=ok(action(t,r,'publish'))
    request=body(version=r['version'],action='withdraw',reason='Уточнение фото');ok(t.post(path('items/'+r['id']+'/action'),json=request))
    assign(client);assert t.post(path('items/'+r['id']+'/action'),json=request).status_code==403
    assign(client,teacher)
    with api.connect() as db:db.execute("UPDATE accounts SET role='staff',version=version+1 WHERE id=?",(teacher['id'],))
    assert not get(t,'options')['canManage']
    assert t.get(path('history/'+r['id'])).status_code==403

def test_active_family_and_frozen_author_are_required(client,setup):
    r=published(client);p=signed('parent');c=claim(p,r);pupil=signed('student')
    assert pupil.get(path('claims/'+c['id'])).status_code==404
    replacement=create(client,'parent',login='newparent');assert signed('newparent').get(path('claims/'+c['id'])).status_code==404
    with api.connect() as db:db.execute('UPDATE accounts SET permissions=?,version=version+1 WHERE id=?',(json.dumps(dict(workspaces=['demo'],grants=[dict(workspace='demo',view='student',entityIds=['s3'])])),setup[1]['id']))
    assert get(p,'claims')['items']==[] and p.get(path('claims/'+c['id'])).status_code==404
    assert review(client,c,'approve').status_code==409
    assert not get(client,'claims/'+c['id'])['ownerCurrent']

def test_approval_is_atomic_and_release_reopens_item(client,setup):
    r=published(client);c1=claim(signed('parent'),r);c2=claim(signed('student'),r)
    payloads=[body(version=c['version'],itemVersion=r['version'],action='approve',reason='Проверены приметы') for c in [c1,c2]]
    admin2=signed('admin')
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses=list(pool.map(lambda args:args[0].post(path('claims/'+args[1]['id']+'/action'),json=args[2]),[(client,c1,payloads[0]),(admin2,c2,payloads[1])]))
    assert sorted(x.status_code for x in responses)==[200,409]
    winner=next(ok(x) for x in responses if x.status_code==200)
    assert get(client,'items/'+r['id'])['status']=='reserved'
    c=ok(review(client,winner,'reopen'));assert c['status']=='pending' and c['item']['status']=='open'

def test_collection_rejects_other_claims_without_revealing_identity(client,setup):
    r=published(client);p=signed('parent');c1=claim(p,r);c2=claim(signed('student'),r)
    ok(review(client,c1,'approve'));ok(review(client,c1,'collect',receivedBy='Проверенный родитель'))
    closed=get(signed('student'),'claims/'+c2['id']);assert closed['status']=='rejected' and 'Проверенный родитель' not in json.dumps(closed)
    assert review(client,c2,'approve').status_code==409

def test_archive_closes_pending_and_withdraw_preserves_claims(client,setup):
    r=published(client);p=signed('parent');c=claim(p,r);r=ok(action(client,r,'withdraw'))
    assert get(p,'items')['items']==[] and get(p,'claims/'+c['id'])['status']=='pending'
    assert review(client,c,'approve').status_code==409
    r=ok(action(client,r,'archive'));assert get(p,'claims/'+c['id'])['status']=='rejected'
    assert action(client,r,'publish').status_code==409

def test_owner_withdraw_and_staff_cannot_approve_self(client,setup):
    t=signed('teacher');assign(client,setup[0]);r=published(client)
    c=ok(post(t,'items/'+r['id']+'/claims',itemVersion=r['version'],proof='Свой ключ с меткой'))
    assert review(t,c,'approve').status_code==403
    c=ok(review(client,c,'approve'));c=ok(review(t,c,'withdraw'));assert c['status']=='withdrawn' and c['item']['status']=='open'

def claim_payload(r):return body(itemVersion=r['version'],studentId='s1',proof='Скрытая метка на подкладке')
def claim_url(r):return path('items/'+r['id']+'/claims')

def test_repeating_the_same_claim_request_returns_the_same_claim(client,setup):
    r=published(client);p=signed('parent');payload=claim_payload(r)
    first=ok(p.post(claim_url(r),json=payload));assert ok(p.post(claim_url(r),json=payload))==first

def test_a_changed_replay_or_a_second_claim_conflicts(client,setup):
    r=published(client);p=signed('parent');payload=claim_payload(r);ok(p.post(claim_url(r),json=payload))
    assert p.post(claim_url(r),json=payload|{'proof':'Другой признак'}).status_code==409
    assert post(p,'items/'+r['id']+'/claims',itemVersion=r['version'],studentId='s1',proof='Повторная заявка').status_code==409

def test_an_action_on_a_stale_item_version_conflicts(client,setup):
    r=published(client)
    assert action(client,r|{'version':r['version']-1},'withdraw').status_code==409
    assert get(client,'items/'+r['id'])['status']=='open'

def test_failed_notification_rolls_back_the_approval(client,setup,monkeypatch):
    r=published(client);p=signed('parent');first=claim(p,r);history=get(client,'history/'+r['id'])
    with monkeypatch.context() as patch:
        patch.setattr(api.notifications,'deliver',lambda *a,**k:(_ for _ in ()).throw(RuntimeError('rollback')))
        with pytest.raises(RuntimeError):review(client,first,'approve')
    assert get(client,'items/'+r['id'])['status']=='open' and get(p,'claims/'+first['id'])['status']=='pending'
    assert get(client,'history/'+r['id'])==history

def test_photo_pixels_reencoded_exif_removed_visibility_and_history(client,setup):
    p=signed('parent');r=item(client);raw=photo(2000,1000);f=ok(upload(client,r,raw));assert f['size']<len(raw)
    assert p.get(path('photos/'+f['id'])).status_code==404
    r=edit(client,r,photoIds=[f['id']]);r=ok(action(client,r,'publish'));res=p.get(path('photos/'+f['id']));assert res.status_code==200 and res.headers['cache-control']=='no-store'
    with Image.open(BytesIO(res.content)) as image:assert image.size==(1600,800) and not image.getexif() and not image.info.get('icc_profile')
    assert client.delete(path('photos/'+f['id'])).status_code==409
    r=edit(client,r,photoIds=[]);assert p.get(path('photos/'+f['id'])).status_code==404
    assert client.get(path('photos/'+f['id'])).status_code==200
    assert upload(p,r).status_code==403

def photo_ids(c,r):return [x['id'] for x in get(c,'items/'+r['id']+'/photos')['items']]

@pytest.mark.parametrize('case,status',[('corrupt',422),('too-large',413),('stale-version',409),('quota',413)])
def test_rejected_upload_leaves_the_card_without_photos(client,setup,monkeypatch,case,status):
    r=item(client);content=dict(corrupt=b'\x89PNG\r\n\x1a\nnot an image').get(case)
    if case=='too-large':content=b'x'*(homework_files.MAX_FILE+1)
    if case=='quota':monkeypatch.setattr(homework_files,'total_limit',lambda:homework_files.MAX_FILE-1)
    assert upload(client,r|{'version':r['version']+21} if case=='stale-version' else r,content).status_code==status
    assert photo_ids(client,r)==[]

def test_a_photo_of_another_card_cannot_be_attached(client,setup):
    r=item(client);f=ok(upload(client,r));other=item(client)
    assert client.put(path('items/'+other['id']),json=body(version=other['version'],data=other['data']|dict(photoIds=[f['id']]),reason='Wrong photo')).status_code==422
    assert get(client,'items/'+other['id'])['data']['photoIds']==[]

def test_a_deleted_photo_is_gone(client,setup):
    r=item(client);f=ok(upload(client,r))
    ok(client.delete(path('photos/'+f['id'])))
    assert client.get(path('photos/'+f['id'])).status_code==404 and photo_ids(client,r)==[]

def test_photo_permission_change_during_stream_discards_file(client,setup):
    teacher=setup[0];assign(client,teacher);t=signed('teacher');r=item(t)
    def chunks():
        yield photo()
        assign(client)
    assert t.post(path('items/'+r['id']+'/photos?itemVersion=1'),content=chunks()).status_code==403
    with api.connect() as db:assert db.execute('SELECT count(*) FROM lost_found_files').fetchone()[0]==0
    assert not list(api.DB.with_suffix('.files').glob('lost-found/*.bin'))

def test_photo_and_claim_survive_backup_and_restore(client,setup,tmp_path,monkeypatch):
    from tools.backup_school import create_backup
    from tools.restore_school import restore_backup
    r=item(client);f=ok(upload(client,r));r=edit(client,r,photoIds=[f['id']]);r=ok(action(client,r,'publish'));c=claim(signed('parent'),r)
    backup=tmp_path/'saved';create_backup(api.DB,backup);restore=tmp_path/'restored';restore_backup(backup,restore)
    monkeypatch.setattr(api,'DB',restore/api.DB.name)
    parent=signed('parent')
    assert parent.get(path('photos/'+f['id'])).status_code==200
    assert get(parent,'claims/'+c['id'])['item']['id']==r['id']

def test_pagination_history_workspace_and_replay_after_changed_permissions(client,setup):
    a=published(client);b=published(client,title='Вторая находка');p=signed('parent');page=get(p,'items?limit=1')
    assert page['items'][0]['id']==b['id'];assert get(p,'items?before='+str(page['nextBefore']))['items'][0]['id']==a['id']
    assert p.get('/api/lost-found/items/'+a['id']+'?workspace=school').status_code==403
    payload=body(itemVersion=a['version'],studentId='s1',proof='Приватное доказательство');c=ok(p.post(path('items/'+a['id']+'/claims'),json=payload))
    with api.connect() as db:
        permissions=dict(workspaces=['demo'],grants=[dict(workspace='demo',view='student',entityIds=['s1','s2'])]);db.execute('UPDATE accounts SET permissions=?,version=version+1 WHERE id=?',(json.dumps(permissions),setup[1]['id']))
    assert p.post(path('items/'+a['id']+'/claims'),json=payload).status_code==404
    assert get(p,'claims')['items']==[]
    history=get(client,'history/'+a['id']+'?limit=1');assert history['nextBefore'] and history['items'][0]['action']=='claimed'
    assert get(client,'history/'+a['id']+'?before='+str(history['nextBefore']))['items']

def test_deactivated_pupil_and_account_cannot_retain_access(client,setup):
    r=published(client);p=signed('parent');c=claim(p,r);state=api.read('demo');state.students[0].active=False;api.save('demo',state)
    assert p.get(path('claims')).status_code==403
    assert review(client,c,'approve').status_code==409
    with api.connect() as db:db.execute('UPDATE accounts SET active=0,version=version+1 WHERE id=?',(setup[1]['id'],))
    assert p.get(path('items')).status_code==401

def test_one_claim_per_account_and_five_unresolved_limit(client,setup):
    with api.connect() as db:db.execute('UPDATE accounts SET permissions=?,version=version+1 WHERE id=?',(json.dumps(dict(workspaces=['demo'],grants=[dict(workspace='demo',view='student',entityIds=['s1','s2'])])),setup[1]['id']))
    p=signed('parent');r=published(client);c=claim(p,r)
    assert post(p,'items/'+r['id']+'/claims',itemVersion=r['version'],studentId='s2',proof='Другой ребёнок').status_code==409
    ok(review(p,c,'withdraw'));r=get(client,'items/'+r['id']);ok(post(p,'items/'+r['id']+'/claims',itemVersion=r['version'],studentId='s2',proof='Уточнён владелец'))
    for i in range(4):claim(p,published(client,title=f'Находка {i}'))
    r=published(client);assert post(p,'items/'+r['id']+'/claims',itemVersion=r['version'],studentId='s1',proof='Шестая заявка').status_code==409

def test_unused_old_photo_is_collected_and_the_used_one_stays(client,setup,monkeypatch):
    r=item(client);f=ok(upload(client,r));r=edit(client,r,photoIds=[f['id']]);unused=ok(upload(client,r))
    with api.connect() as db:root=lost_found_files.root_for(db)
    assert homework_files.file_path(root,unused['id']).exists()
    real=lost_found_files.now
    monkeypatch.setattr(homework_files,'now',lambda:real()+timedelta(days=2))
    ok(upload(client,r))  # the next upload sweeps the stale ones
    assert unused['id'] not in photo_ids(client,r) and f['id'] in photo_ids(client,r)
    assert not homework_files.file_path(root,unused['id']).exists() and homework_files.file_path(root,f['id']).exists()

def test_card_changed_during_the_upload_aborts_it_and_keeps_no_file(client,setup,monkeypatch):
    r=item(client);f=ok(upload(client,r));r=edit(client,r,photoIds=[f['id']])
    original=lost_found_files.sanitize
    def changed(path):
        result=original(path);edit(client,r,description='Карточка изменилась во время загрузки');return result
    monkeypatch.setattr(lost_found_files,'sanitize',changed)
    assert upload(client,r).status_code==409
    with api.connect() as db:root=lost_found_files.root_for(db)
    assert photo_ids(client,r)==[f['id']] and [x.name for x in root.glob('*.bin')]==[f['id']+'.bin']
