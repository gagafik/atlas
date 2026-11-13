from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import timedelta
from io import BytesIO
import json, sqlite3
from uuid import uuid4
import pytest
from PIL import Image
from fastapi.testclient import TestClient
from backend import api, school_site, school_site_files, homework_files
from test_access import client, enable, create, signed

def body(**kw):return dict(requestId=str(uuid4()),**kw)
def path(part):return '/api/school-site/'+part+('&' if '?' in part else '?')+'workspace=demo'
def ok(r):
    assert r.status_code==200,r.text
    return r.json()
def get(c,p):return ok(c.get(path(p)))
def data(**kw):return dict(kind='page',slug='about',title='О школе',summary='Открытая информация',blocks=[dict(kind='text',text='Текст для посетителей')])|kw
def save(c,r=None,**kw):return ok(c.request('PUT' if r else 'POST',path('items/'+r['id'] if r else 'items'),json=body(version=r['version'] if r else 0,data=(r['data'] if r else data())|kw)))
def cfg(c,**kw):
    before=get(c,'options')['config'];return ok(c.put(path('config'),json=body(version=before['version'],data=before['data']|kw)))
def action(c,r,name='publish',id=None,**kw):
    id=id or r.get('id','site');token=ok(c.post(path('preview/'+id),json=dict(version=r['version'])))['token'] if name=='publish' else ''
    return c.post(path('action/'+id),json=body(version=r['version'],action=name,token=token,confirmedPublic=True,reason='Проверено администрацией',**kw))
def publish(c,r=None):return ok(action(c,r or get(c,'options')['config']))
def anonymous():return TestClient(api.app)
def photo():
    out=BytesIO();im=Image.new('RGB',(50,30),'green');exif=Image.Exif();exif[270]='Private location';im.save(out,'JPEG',exif=exif);return out.getvalue()
def upload(c,r,payload=None):return c.post(path('items/'+r['id']+'/photos?itemVersion='+str(r['version'])),content=photo() if payload is None else payload,headers={'Content-Type':'image/jpeg'})
def assign(c,*ids):
    r=get(c,'options')['config'];return ok(c.put(path('access'),json=body(version=r['version'],accountIds=list(ids))))
@pytest.fixture
def setup(client):
    enable(client);teacher=create(client,'teacher');parent=create(client,'parent');editor=create(client,'editor')
    cfg(client,title='Демонстрационная школа');return teacher,parent,editor

def test_requires_accounts_and_explicit_staff_assignment(client):
    assert client.get(path('options')).status_code==403
    assert anonymous().get('/school/demo').status_code==404
    enable(client);teacher=create(client,'teacher');t=signed('teacher');create(client,'editor');e=signed('editor')
    assert not get(t,'options')['canEdit'] and not get(e,'options')['canEdit']
    assert t.post(path('items'),json=body(version=0,data=data())).status_code==403
    cfg(client);assign(client,teacher['id']);assert get(t,'options')['canEdit']
    r=save(t);assert t.post(path('action/'+r['id']),json=body(version=r['version'],action='archive')).status_code==403
    assign(client);assert t.get(path('items/'+r['id'])).status_code==403
    assign(client,teacher['id'])
    with api.connect() as db:db.execute("UPDATE accounts SET role='staff',version=version+1 WHERE id=?",(teacher['id'],))
    assert not get(t,'options')['canEdit']

def live(c,**kw):
    r=publish(c,save(c,**kw));publish(c);return r

def test_a_draft_is_not_public_until_published_and_a_withdrawn_page_disappears(client,setup):
    before=api.read('demo');r=save(client);pub=anonymous()
    assert pub.get('/school/demo/p/about').status_code==404
    assert pub.get(path('items/'+r['id'])).status_code==401
    r=publish(client,r);assert pub.get('/school/demo/p/about').status_code==404
    publish(client);assert pub.get('/school/demo/p/about').status_code==200
    r=save(client,r,title='Private draft title')
    assert 'Private draft' not in pub.get('/school/demo/p/about').text
    r=publish(client,r);assert 'Private draft' in pub.get('/school/demo/p/about').text
    ok(action(client,r,'withdraw'));assert pub.get('/school/demo/p/about').status_code==404
    assert api.read('demo')==before

def test_a_title_cannot_inject_markup_and_no_pupil_names_reach_the_page(client,setup):
    before=api.read('demo');live(client,title='<script>alert(1)</script>')
    html=anonymous().get('/school/demo/p/about')
    assert html.status_code==200 and '&lt;script&gt;' in html.text and '<script>' not in html.text
    assert all(p.name not in html.text for p in before.students)

def test_public_pages_are_not_cached_and_run_under_a_strict_policy(client,setup):
    live(client);html=anonymous().get('/school/demo/p/about')
    assert html.headers['cache-control']=='no-store' and "default-src 'none'" in html.headers['content-security-policy']

def test_the_public_list_can_be_filtered_by_kind_and_searched_without_leaking_drafts(client,setup):
    r=live(client);pub=anonymous();save(client,r,title='Private draft title',kind='news')
    assert '/p/about' in pub.get('/school/demo?kind=page').text and '/p/about' not in pub.get('/school/demo?kind=news').text
    assert 'Private draft' not in pub.get('/school/demo?q=Private').text

def refused_publication(client,case,monkeypatch):
    r=save(client);p=ok(client.post(path('preview/'+r['id']),json=dict(version=r['version'])))
    request=body(version=r['version'],action='publish',token=p['token'],confirmedPublic=case!='unconfirmed');sender=client
    if case=='configuration-changed':cfg(client,footer='Changed')
    elif case=='other-administrator':create(client,'admin',login='second');sender=signed('second')
    elif case=='expired':
        real=school_site.now;monkeypatch.setattr(school_site,'now',lambda:real()+timedelta(days=1))
    elif case=='draft-edited':
        r=save(client,r,title='Changed again');request['version']=r['version']
    return sender.post(path('action/'+r['id']),json=request)

@pytest.mark.parametrize('case,status',[('unconfirmed',422),('configuration-changed',409),('other-administrator',409),('expired',409),('draft-edited',409)])
def test_a_stale_or_foreign_preview_cannot_publish(client,setup,monkeypatch,case,status):
    assert refused_publication(client,case,monkeypatch).status_code==status

def test_replay_and_concurrent_edits_do_not_overwrite(client,setup):
    r=save(client);req=body(version=r['version'],data=r['data']|dict(title='One save'));before=len(get(client,'history?id='+r['id'])['items'])
    with ThreadPoolExecutor(2) as pool:responses=list(pool.map(lambda _:client.put(path('items/'+r['id']),json=req),range(2)))
    assert all(x.status_code==200 for x in responses) and responses[0].json()==responses[1].json()
    stale=body(version=r['version'],data=r['data']|dict(title='Stale'))
    assert client.put(path('items/'+r['id']),json=stale).status_code==409
    req['data']['title']='Changed payload';assert client.put(path('items/'+r['id']),json=req).status_code==409
    assert len(get(client,'history?id='+r['id'])['items'])==before+1

def test_publish_retry_and_revoked_editor_cannot_replay(client,setup):
    teacher=setup[0];assign(client,teacher['id']);t=signed('teacher');req=body(version=0,data=data())
    r=ok(t.post(path('items'),json=req));assign(client)
    assert t.post(path('items'),json=req).status_code==403
    p=ok(client.post(path('preview/'+r['id']),json=dict(version=r['version'])))
    req=body(version=r['version'],action='publish',token=p['token'],confirmedPublic=True)
    assert ok(client.post(path('action/'+r['id']),json=req))==ok(client.post(path('action/'+r['id']),json=req))

def test_menu_configuration_snapshot_withdrawal_and_site_offline(client,setup):
    r=save(client);cfg(client,menuIds=[r['id']],contact='Old contact')
    assert action(client,get(client,'options')['config']).status_code==409
    r=publish(client,r);publish(client);pub=anonymous()
    assert '/p/about' in pub.get('/school/demo').text
    cfg(client,contact='Private new contact');assert 'Private new contact' not in pub.get('/school/demo').text
    ok(action(client,r,'archive'));assert '/p/about' not in pub.get('/school/demo').text
    assert pub.get('/school/demo/p/about').status_code==404
    ok(action(client,get(client,'options')['config'],'withdraw'))
    assert pub.get('/school/demo').status_code==404

def test_photos_private_until_published_and_removed_or_withdrawn(client,setup):
    r=save(client,kind='gallery');f=ok(upload(client,r));url='/school/demo/photos/'+f['id'];pub=anonymous()
    assert pub.get(url).status_code==404
    raw=client.get(path('photos/'+f['id']));assert raw.status_code==200
    im=Image.open(BytesIO(raw.content));assert not im.getexif() and 'exif' not in im.info
    r=save(client,r,photos=[dict(id=f['id'],alt='Учебный пример',caption='Только демонстрационное фото')]);r=publish(client,r);publish(client)
    assert pub.get(url).status_code==200 and 'Учебный пример' in pub.get('/school/demo/p/about').text
    r=save(client,r,photos=[]);assert pub.get(url).status_code==200
    r=publish(client,r);assert pub.get(url).status_code==404
    assert client.delete(path('photos/'+f['id'])).status_code==409
    r=save(client,r,photos=[dict(id=f['id'],alt='Возвращённое фото')]);r=publish(client,r);assert pub.get(url).status_code==200
    ok(action(client,r,'withdraw'));assert pub.get(url).status_code==404

def photo_ids(c,r):return [x['id'] for x in get(c,'items/'+r['id']+'/photos')['items']]

def test_a_parent_cannot_read_or_upload_photos_of_the_site(client,setup):
    r=save(client);f=ok(upload(client,r));p=signed('parent')
    assert p.get(path('photos/'+f['id'])).status_code==403
    assert upload(p,r).status_code==403
    assert photo_ids(client,r)==[f['id']]

def test_a_photo_of_another_entry_cannot_be_attached(client,setup):
    r=save(client);other=save(client,slug='other');f=ok(upload(client,r))
    assert client.put(path('items/'+other['id']),json=body(version=other['version'],data=other['data']|dict(photos=[dict(id=f['id'],alt='Bad')]))).status_code==422
    assert get(client,'items/'+other['id'])['data']['photos']==[]

@pytest.mark.parametrize('case,status',[('not-a-photo',422),('quota',413)])
def test_a_rejected_upload_leaves_no_photo(client,setup,monkeypatch,case,status):
    r=save(client)
    if case=='quota':monkeypatch.setattr(homework_files,'total_limit',lambda:1)
    assert upload(client,r,b'<svg onload="alert(1)"></svg>' if case=='not-a-photo' else None).status_code==status
    assert photo_ids(client,r)==[]

def test_an_unused_photo_can_be_deleted(client,setup):
    r=save(client);f=ok(upload(client,r))
    assert client.delete(path('photos/'+f['id'])).status_code==200
    assert photo_ids(client,r)==[]

def test_permission_change_during_upload_discards_reservation(client,setup,monkeypatch):
    t=setup[0];assign(client,t['id']);r=save(client);original=school_site_files.sanitize
    def revoke(path):
        result=original(path);assign(client);return result
    monkeypatch.setattr(school_site_files,'sanitize',revoke)
    assert upload(signed('teacher'),r).status_code==403
    assert get(client,'items/'+r['id']+'/photos')['items']==[]

def test_archive_restore_revision_preserves_published_version_and_photos(client,setup):
    r=save(client);f=ok(upload(client,r));r=save(client,r,photos=[dict(id=f['id'],alt='Original')]);r=publish(client,r);publish(client)
    old=next(h for h in get(client,'history?id='+r['id'])['items'] if h['action']=='publish')
    r=save(client,r,title='New draft',photos=[]);r=publish(client,r)
    r=ok(action(client,r,'restore_revision',historyId=old['id']))
    assert r['data']['title']=='О школе' and r['published']['title']=='New draft'
    r=ok(action(client,r,'archive'));assert anonymous().get('/school/demo/p/about').status_code==404
    assert client.put(path('items/'+r['id']),json=body(version=r['version'],data=r['data'])).status_code==409
    r=ok(action(client,r,'restore'));assert not r['published'];publish(client,r)
    assert anonymous().get('/school/demo/photos/'+f['id']).status_code==200

@pytest.mark.parametrize('patch',[dict(title=' '),dict(slug='../secret'),dict(blocks=[dict(kind='link',text='bad',url='javascript:alert(1)')]),dict(kind='event'),
    dict(kind='event',startsAt='2026-10-15T17:00:00',endsAt='2026-10-15T19:00:00')],ids=['blank-title','path-slug','script-link','event-without-times','event-without-zone'])
def test_invalid_entries_are_refused(client,setup,patch):
    assert client.post(path('items'),json=body(version=0,data=data(**patch))).status_code==422

def test_a_published_event_shows_its_place_and_link(client,setup):
    live(client,kind='event',startsAt='2026-10-15T12:00:00Z',endsAt='2026-10-15T14:00:00Z',place='Актовый зал',blocks=[dict(kind='link',text='Информация',url='https://example.org/')])
    page=anonymous().get('/school/demo/p/about')
    assert page.status_code==200 and 'Актовый зал' in page.text and 'https://example.org/' in page.text

def test_event_times_are_shown_in_the_school_time_zone():
    from backend.school_site_public import event
    shown=event(dict(kind='event',startsAt='2026-10-15T12:00:00+00:00',endsAt='2026-10-15T14:00:00+00:00',place=''))
    assert '17:00' in shown and '19:00' in shown

def test_cross_workspace_identity_and_atomic_history_failure(client,setup,monkeypatch):
    r=save(client);before=get(client,'items/'+r['id'])
    assert client.get('/api/school-site/items/'+r['id']+'?workspace=school').status_code==404
    assert signed('teacher').get('/api/school-site/options?workspace=school').status_code==403
    assert anonymous().get('/school/school/p/about').status_code==404
    def fail(*args):raise RuntimeError('synthetic history failure')
    monkeypatch.setattr(api.school_site,'history',fail)
    with pytest.raises(RuntimeError):client.put(path('items/'+r['id']),json=body(version=r['version'],data=r['data']|dict(title='Should roll back')))
    assert get(client,'items/'+r['id'])==before

def test_backup_restore_covers_draft_public_history_and_photo(client,setup,tmp_path,monkeypatch):
    from tools.backup_school import create_backup
    from tools.restore_school import restore_backup,verify_backup
    r=save(client);f=ok(upload(client,r));r=save(client,r,photos=[dict(id=f['id'],alt='Backup')]);publish(client,r);publish(client)
    backup=tmp_path/'backup';create_backup(api.DB,backup);verify_backup(backup)
    destination=tmp_path/'restored';restore_backup(backup,destination)
    old=anonymous().get('/school/demo/p/about').text
    monkeypatch.setattr(api,'DB',destination/api.DB.name)
    assert anonymous().get('/school/demo/p/about').text==old
    assert anonymous().get('/school/demo/photos/'+f['id']).status_code==200
    verify_backup(backup)

def test_cyrillic_search_pagination_and_private_photo_after_site_offline(client,setup):
    r=save(client,title='Новости науки');f=ok(upload(client,r));r=save(client,r,photos=[dict(id=f['id'],alt='Галерея')]);publish(client,r);publish(client)
    assert get(client,'items?q=НОВОСТИ')['items'][0]['id']==r['id']
    assert 'Новости науки' in anonymous().get('/school/demo?q=НОВОСТИ').text
    assert 'Новости науки' not in anonymous().get('/school/demo?offset=24').text
    ok(action(client,get(client,'options')['config'],'withdraw'))
    assert anonymous().get('/school/demo/photos/'+f['id']).status_code==404
    assert client.get(path('photos/'+f['id'])).status_code==200

def test_failed_history_rolls_back_publication_and_the_preview_stays_valid(client,setup,monkeypatch):
    r=save(client);p=ok(client.post(path('preview/'+r['id']),json=dict(version=r['version'])))
    req=body(version=r['version'],action='publish',token=p['token'],confirmedPublic=True)
    original=api.school_site.history
    def fail(*args):raise RuntimeError('synthetic publication rollback')
    monkeypatch.setattr(api.school_site,'history',fail)
    with pytest.raises(RuntimeError):client.post(path('action/'+r['id']),json=req)
    assert get(client,'items/'+r['id'])['published'] is None
    monkeypatch.setattr(api.school_site,'history',original)
    assert ok(client.post(path('action/'+r['id']),json=req))['published'] is not None
