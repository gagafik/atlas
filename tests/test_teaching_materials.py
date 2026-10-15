"""Reusable versions, sharing, lesson publication and private file retention."""
from datetime import timedelta
import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from backend import api, teaching_materials as library, teaching_material_files as files, homework_files
from backend.access import now
from test_access import client, enable, create, signed
from test_journal import entry, lesson, write, report

BASE='/api/journal/library'
UPLOAD='/api/journal/material-files'


def setup(client):
    enable(client)
    create(client,'teacher')
    create(client,'teacher',login='colleague',grants=[dict(workspace='demo',view='teacher',entityIds=['t1'])])
    create(client,'student');create(client,'parent')
    return signed('teacher'),signed('colleague'),signed('student'),signed('parent')


def save(client, material=None, **content):
    current=material['content'] if material else dict(title='Дроби',subject='Математика',topics=['Числа'],text='Объяснение темы',fileIds=[])
    body=dict(version=material['latestVersion'] if material else 0,visibility=content.pop('visibility',material['visibility'] if material else 'private'),content=current|content)
    return client.request('PUT' if material else 'POST',BASE+('/'+material['id'] if material else ''),params=dict(workspace='demo'),json=body)


def get(client, id, **params):
    return client.get(BASE+'/'+id,params=dict(workspace='demo')|params)


def upload(client, body=b'Lesson material', name='material.txt'):
    return client.post(UPLOAD,params=dict(workspace='demo',filename=name),content=body,headers={'Content-Type':'application/octet-stream'})


def download(client, file, material=None, key=None, **params):
    if material: params|=dict(materialId=material['id'],version=material['version'])
    if key:params['lessonKey']=key
    return client.get(UPLOAD+'/'+file,params=dict(workspace='demo')|params)


def attach(client, record, material, publish=False):
    record['document']['content']['materials']=[dict(id=material['id'],version=material['version'],title='Forged client text',files=[])]
    return write(client,record,publish=publish)


def test_private_shared_library_search_copy_and_version_history(client):
    teacher,other,pupil,parent=setup(client)
    before=api.read('demo')
    material=save(teacher).json();id=material['id']
    assert material['version']==1 and material['canEdit'] and material['visibility']=='private'
    assert get(other,id).status_code==404
    for account in [pupil,parent]:
        assert get(account,id).status_code==403
        assert account.get(BASE+'?workspace=demo').status_code==403
        assert save(account).status_code==403
    material=save(teacher,material,visibility='staff',text='Новая версия').json()
    assert get(other,id).json()['content']['text']=='Новая версия'
    assert not get(other,id).json()['canEdit']
    assert save(other,material,text='Forbidden').status_code==403
    assert teacher.get(BASE+'?workspace=demo&q=ДРОБИ%20числа').json()['items'][0]['id']==id
    assert teacher.get(BASE+'?workspace=demo&q=missing').json()['items']==[]
    assert other.get(BASE+'?workspace=demo&mine=true').json()['items']==[]
    old=get(teacher,id,version=1).json();assert old['content']['text']=='Объяснение темы'
    history=teacher.get(BASE+f'/{id}/history?workspace=demo').json()
    assert [r['version'] for r in history['items']]==[2,1]
    assert get(other,id,version=1).status_code==404  # sharing a new version does not disclose private history
    assert other.post(BASE+f'/{id}/copy?workspace=demo',json=dict(version=1)).status_code==404
    assert [h['version'] for h in other.get(BASE+f'/{id}/history?workspace=demo').json()['items']]==[2]
    copied=other.post(BASE+f'/{id}/copy?workspace=demo',json=dict(version=2)).json()
    assert copied['visibility']=='private' and copied['version']==1 and copied['content']['text']==material['content']['text']
    assert get(teacher,copied['id']).status_code==404
    assert api.read('demo')==before


def test_publication_pins_canonical_snapshot_without_homework(client):
    teacher,other,pupil,parent=setup(client)
    file=upload(teacher).json()['id']
    material=save(teacher,fileIds=[file]).json()
    record=entry(teacher,lesson(teacher)['lessonKey']);key=record['lessonKey']
    assert not record['document']['content']['homework']
    record=attach(teacher,record,material).json()
    assert record['document']['content']['materials'][0]['title']=='Дроби'
    assert report(parent).json()['rows']==[]
    assert download(parent,file,material,key).status_code==404
    record=write(teacher,record,publish=True).json()
    visible=report(parent).json()['rows'][0]['content']['materials'][0]
    assert visible['text']=='Объяснение темы' and visible['files'][0]['id']==file
    for family in [pupil,parent]:
        response=download(family,file,material,key)
        assert response.content==b'Lesson material' and response.headers['cache-control']=='no-store'
        assert download(family,file,material).status_code==403
    assert download(other,file,material,key).status_code==403
    current=save(teacher,material,text='Изменено после урока').json()
    assert report(parent).json()['rows'][0]['content']['materials'][0]['version']==1
    record=attach(teacher,record,current).json()
    assert report(parent).json()['rows'][0]['content']['materials'][0]['text']=='Объяснение темы'
    assert download(parent,file,current,key).status_code==404
    record=write(teacher,record,publish=True).json()
    assert report(parent).json()['rows'][0]['content']['materials'][0]['text']=='Изменено после урока'
    assert download(parent,file,material,key).status_code==404
    assert download(parent,file,current,key).status_code==200
    assert download(teacher,file,material,key).status_code==200
    assert teacher.post('/api/journal/withdraw?workspace=demo',json=dict(lessonKey=key,version=record['version'])).status_code==200
    assert download(parent,file,current,key).status_code==404


def test_shared_attachment_survives_privacy_archive_and_copy_has_independent_files(client):
    teacher,other,_,parent=setup(client)
    file=upload(other).json()['id']
    source=save(other,visibility='staff',fileIds=[file]).json()
    copy=teacher.post(BASE+f"/{source['id']}/copy?workspace=demo",json=dict(version=1)).json()
    record=attach(teacher,entry(teacher,lesson(teacher)['lessonKey']),source,publish=True).json();key=record['lessonKey']
    private=save(other,source,visibility='private').json()
    assert get(teacher,source['id']).status_code==404
    assert download(teacher,file,source).status_code==404
    assert download(teacher,file,copy).status_code==200
    assert download(parent,file,source,key).status_code==200
    assert write(teacher,record,publish=True).status_code==200  # retains pinned private version
    another=entry(teacher,lesson(teacher,day='2026-08-24')['lessonKey'],day='2026-08-24')
    assert attach(teacher,another,source).status_code==403
    archived=other.post(BASE+f"/{source['id']}/archive?workspace=demo",json=dict(version=private['version'],archived=True))
    assert archived.status_code==200
    assert save(other,archived.json(),text='forbidden').status_code==403
    assert download(parent,file,source,key).status_code==200
    restored=other.post(BASE+f"/{source['id']}/archive?workspace=demo",json=dict(version=archived.json()['version'],archived=False)).json()
    assert not restored['archived'] and restored['version']==4
    assert other.get(BASE+'?workspace=demo&archived=true').json()['items']==[]


def test_rejects_stale_duplicate_foreign_workspace_and_unowned_files_atomically(client):
    teacher,other,_,_=setup(client)
    foreign=upload(other).json()['id']
    assert save(teacher,fileIds=[foreign]).status_code==422
    own=save(teacher).json()
    assert save(teacher,own,text='Version2').status_code==200
    assert save(teacher,own,text='Stale').status_code==409
    assert get(teacher,own['id'],workspace='school').status_code==403
    assert client.get(BASE+f"/{own['id']}?workspace=school").status_code==404
    record=entry(teacher,lesson(teacher)['lessonKey'])
    record['document']['content']['materials']=[dict(id=own['id'],version=1)]*2
    assert write(teacher,record).status_code==422
    with api.connect() as db:
        assert db.execute('SELECT count(*) FROM teaching_material_lessons').fetchone()[0]==0
    record['document']['content']['materials']=[dict(id=own['id'],version=99)]
    assert write(teacher,record).status_code==404
    assert entry(teacher,record['lessonKey'])['version']==0
    with api.connect() as db:
        assert db.execute('SELECT count(*) FROM teaching_material_file_links').fetchone()[0]==0


def test_parallel_updates_keep_one_version_and_pagination_has_no_private_metadata(client):
    teacher,other,_,_=setup(client)
    material=save(teacher,visibility='staff').json()
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses=list(pool.map(lambda text:save(teacher,material,text=text),['one','two']))
    assert sorted(r.status_code for r in responses)==[200,409]
    save(other,title='Секретная заготовка')
    for index in range(3):save(teacher,title=f'Открытый {index}',visibility='staff')
    page1=other.get(BASE+'?workspace=demo&limit=2&q=Открытый').json()
    page2=other.get(BASE+'?workspace=demo&limit=2&q=Открытый&offset=2').json()
    assert page1['hasMore'] and not page2['hasMore']
    assert len({r['id'] for p in [page1,page2] for r in p['items']})==3
    assert 'Секретная' not in teacher.get(BASE+'?workspace=demo').text


@pytest.mark.parametrize('change',['account','teacher'])
def test_upload_rechecks_current_role_and_school_assignment(client,monkeypatch,change):
    teacher,_,_,_=setup(client)
    original=homework_files.validate_file
    def changed(path,name):
        if change=='account':
            with api.connect() as db:db.execute("UPDATE accounts SET version=version+1 WHERE login='teacher'")
        else:
            state=api.read('demo');state.teachers=[t for t in state.teachers if t.id!='t2']
            for workload in state.workloads:
                if workload.teacherId=='t2':workload.teacherId='t1'
                workload.additionalTeacherIds=[t for t in workload.additionalTeacherIds if t!='t2']
            api.save('demo',state)
        return original(path,name)
    monkeypatch.setattr(homework_files,'validate_file',changed)
    assert upload(teacher).status_code in [403,409]
    with api.connect() as db:
        assert db.execute('SELECT count(*) FROM teaching_material_files').fetchone()[0]==0
        assert not list(files.root_for(db).glob('*.bin'))


def test_file_limits_cleanup_backup_and_restore_all_versions(client,tmp_path,monkeypatch):
    from tools.backup_school import create_backup
    teacher,other,_,_=setup(client)
    for body,name in [(b'','empty.txt'),(b'bad','bad.pdf'),(b'bad','x.exe')]:
        assert upload(teacher,body,name).status_code==422
    assert upload(teacher,b'x'*(homework_files.MAX_FILE+1)).status_code==413
    file=upload(teacher).json()['id']
    material=save(teacher,visibility='staff',fileIds=[file]).json()
    copy=other.post(BASE+f"/{material['id']}/copy?workspace=demo",json=dict(version=1)).json()
    newer=save(teacher,material,fileIds=[]).json()
    unused=upload(teacher).json()['id']
    with api.connect() as db:
        db.execute('UPDATE teaching_material_files SET created=?',((now()-timedelta(days=2)).isoformat(),))
        files.collect_unused(db)
        assert not (files.root_for(db)/(unused+'.bin')).exists()
        assert homework_files.total_stored(db)==len(b'Lesson material')
    backup=tmp_path/'restore';source=api.DB
    manifest=create_backup(source,backup);assert len(manifest['files'])==1
    monkeypatch.setattr(api,'DB',backup/source.name)
    assert download(teacher,file,material).content==b'Lesson material'
    assert download(teacher,file,newer).status_code==404
    assert download(other,file,copy).content==b'Lesson material'
    monkeypatch.setenv('ATLAS_UPLOAD_TOTAL_MB','10')
    assert upload(teacher).status_code==413


def test_family_revocation_and_read_recheck(client,monkeypatch):
    teacher,_,_,parent=setup(client)
    file=upload(teacher).json()['id'];material=save(teacher,fileIds=[file]).json()
    record=attach(teacher,entry(teacher,lesson(teacher)['lessonKey']),material,publish=True).json()
    original=api.teaching_materials.lesson_file
    def revoke(db,user,*args):
        db.execute("UPDATE accounts SET version=version+1 WHERE login='parent'")
        return original(db,user,*args)
    monkeypatch.setattr(api.teaching_materials,'lesson_file',revoke)
    assert download(parent,file,material,record['lessonKey']).status_code==409


def test_homework_grading_preserves_separate_published_and_draft_material_versions(client):
    from test_homework_grading import ready, grade, options, payload
    record,sent,_,parent,teacher=ready(client);key=record['lessonKey']
    first=save(teacher).json()
    record=attach(teacher,record,first,publish=True).json()
    second=save(teacher,first,text='Ещё не опубликовано в уроке').json()
    record=attach(teacher,record,second).json()
    response=grade(teacher,key,sent,payload(options(teacher,key)))
    assert response.status_code==200,response.text
    assert report(parent).json()['rows'][0]['content']['materials'][0]['version']==1
    saved=entry(teacher,key)
    assert saved['document']['content']['materials'][0]['version']==2
    assert saved['document']['content']['materials'][0]['text']==second['content']['text']


def test_closed_period_prevents_binding_material_and_preserves_archive(client,monkeypatch):
    from test_journal_periods import period, close, archive, period_clock
    teacher,_,_,parent=setup(client)
    material=save(teacher).json()
    record=attach(teacher,entry(teacher,lesson(teacher)['lessonKey']),material,publish=True).json()
    closed=close(client,period(client))
    newer=save(teacher,material,text='После закрытия периода').json()
    assert attach(teacher,record,newer).status_code==403
    assert report(parent).json()['rows'][0]['content']['materials'][0]['version']==1
    with api.connect() as db:
        assert db.execute('SELECT version FROM teaching_material_lessons').fetchall()==[(material['version'],)]


def test_validation_and_global_reservation_during_upload(client,monkeypatch):
    teacher,_,_,_=setup(client)
    for changes in [dict(title=' '),dict(text=''),dict(topics=['Тема','тема']),dict(topics=['x'*101]),dict(topics=['']*21)]:
        assert save(teacher,**changes).status_code==422
    original=homework_files.validate_file
    def reserved(path,name):
        with api.connect() as db:
            assert homework_files.total_stored(db)==homework_files.MAX_FILE
        return original(path,name)
    monkeypatch.setattr(homework_files,'validate_file',reserved)
    file=upload(teacher).json()['id']
    assert save(teacher,text='',fileIds=[file]).status_code==200
    assert save(teacher,fileIds=[file]*2).status_code==422
