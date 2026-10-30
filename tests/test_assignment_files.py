"""Assignment publication, historical materials and end-of-day submission policy."""
from datetime import datetime, timedelta, timezone
import json

import pytest
from backend import api, assignment_files, homework, homework_files
from test_access import client, enable, create, signed
from test_homework import setup, get, answer, review
from test_journal import entry, lesson, graded, write, DAY
from test_journal_periods import period, close, period_clock

BASE='/api/journal/assignment-files'


def upload(client, record, body=b'Teacher materials', name='lesson.txt', **extra):
    return client.post(BASE,params=dict(workspace='demo',lessonKey=record['lessonKey'],day=record['document']['lesson']['date'],
        revision=record['revision'],version=record['version'],filename=name)|extra,content=body,headers={'Content-Type':'application/octet-stream'})


def download(client, id, student=None, workspace='demo'):
    return client.get(BASE+'/'+id,params=dict(workspace=workspace)|({'studentId':student} if student else {}))


def save_materials(client, record, ids, publish=True, **fields):
    record['document']['content'].update(homeworkFiles=ids,**fields)
    response=write(client,record,publish=publish)
    assert response.status_code==200,response.text
    return response.json()


def listing(client, record):
    return client.get(BASE,params=dict(workspace='demo',lessonKey=record['lessonKey'],day=DAY))


def test_upload_before_first_save_and_publish_materials_to_family(client):
    enable(client)
    for role in ['teacher','student','parent']:create(client,role)
    teacher,pupil,parent=signed('teacher'),signed('student'),signed('parent')
    record=graded(entry(teacher,lesson(teacher)['lessonKey']))
    before=api.read('demo')
    response=upload(teacher,record,name='../папка/материал.txt');assert response.status_code==200,response.text
    material=response.json();id=material['id'];assert material['filename']=='материал.txt'
    assert entry(teacher,record['lessonKey'])['version']==0
    assert download(teacher,id).content==b'Teacher materials'
    assert download(client,id).status_code==404  # another author's unbound draft
    assert download(parent,id,'s1').status_code==404
    record=save_materials(teacher,record,[id],publish=False)
    assert download(client,id).status_code==200
    assert download(parent,id,'s1').status_code==404
    save_materials(teacher,record,[id])
    for user in [pupil,parent]:
        visible=get(user,record['lessonKey']);assert visible['assignmentFiles']==[material]
        response=download(user,id,'s1');assert response.content==b'Teacher materials'
        assert response.headers['cache-control']=='no-store'
        assert response.headers['x-content-type-options']=='nosniff'
        assert response.headers['content-disposition'].startswith('attachment;')
    assert api.read('demo')==before


def test_material_change_keeps_original_submitted_conditions_but_hides_unseen_history(client):
    record,pupil,parent,teacher=setup(client);key=record['lessonKey']
    old=upload(teacher,record,body=b'Old task').json()
    record=save_materials(teacher,record,[old['id']])
    sent=answer(pupil,key,get(pupil,key)).json()
    new=upload(teacher,record,body=b'New task').json()
    record=save_materials(teacher,record,[new['id']])
    current=get(parent,key)
    assert current['assignmentChanged'] and current['assignmentFiles']==[new]
    assert current['submitted']['assignmentFiles']==[old]
    assert download(parent,old['id'],'s1').content==b'Old task'
    other=signed('other-pupil')
    assert download(other,new['id'],'s2').status_code==200
    assert download(other,old['id'],'s2').status_code==404
    assert review(teacher,key,get(teacher,key)).status_code==409
    assert answer(pupil,key,get(pupil,key),'withdraw').status_code==200
    assert download(parent,old['id'],'s1').status_code==200
    history=parent.get(f'/api/journal/homework/{key}/history?workspace=demo&studentId=s1').json()['items']
    assert history[-1]['submitted']['assignmentFiles']==[old]
    assert client.post('/api/journal/withdraw?workspace=demo',json=dict(lessonKey=key,version=record['version'])).status_code==200
    assert download(parent,old['id'],'s1').status_code==404


def test_scope_rejects_foreign_teacher_lesson_workspace_and_family(client):
    record,pupil,parent,teacher=setup(client)
    material=upload(teacher,record).json()['id']
    assert upload(parent,record).status_code==403
    assert listing(pupil,record).status_code==403
    assert listing(signed('unrelated'),record).status_code==403
    assert download(signed('unrelated'),material).status_code==403
    assert download(parent,material,'s2').status_code==403
    assert download(parent,material,'s1',workspace='school').status_code==403
    record['document']['content']['homeworkFiles']=[material]
    assert write(client,record).status_code==422  # unbound upload belongs to teacher
    assert write(teacher,record,publish=True).status_code==200
    other=graded(entry(client,lesson(client,'Математика')['lessonKey']))
    other['document']['content']['homeworkFiles']=[material]
    assert write(client,other).status_code==422
    assert download(parent,material,'s1').status_code==200
    with api.connect() as db:db.execute("UPDATE accounts SET active=0 WHERE login='parent'")
    assert download(parent,material,'s1').status_code==401


@pytest.mark.parametrize('change',['journal','schedule','account','closure'])
def test_changes_during_upload_reject_and_discard_partial_material(client,monkeypatch,change):
    record,_,_,teacher=setup(client)
    original=homework_files.validate_file
    def concurrent(path,name):
        if change=='journal':write(teacher,record).raise_for_status()
        elif change=='schedule':api.save('demo',api.read('demo'))
        elif change=='account':
            with api.connect() as db:db.execute("UPDATE accounts SET version=version+1 WHERE login='teacher'")
        else:close(client,period(client))
        return original(path,name)
    monkeypatch.setattr(homework_files,'validate_file',concurrent)
    response=upload(teacher,record);assert response.status_code in [403,409],response.text
    with api.connect() as db:
        assert db.execute('SELECT count(*) FROM assignment_files').fetchone()[0]==0
        assert not list(assignment_files.root_for(db).glob('*.bin'))


def test_gc_retains_every_saved_version_and_backup_restores_materials(client,tmp_path,monkeypatch):
    from tools.backup_school import create_backup
    record,pupil,parent,teacher=setup(client);key=record['lessonKey']
    linked=upload(teacher,record,body=b'Saved').json()['id']
    record=save_materials(teacher,record,[linked])
    answer(pupil,key,get(pupil,key)).raise_for_status()
    record=save_materials(teacher,record,[])
    draft=upload(teacher,record,body=b'Private').json()['id']
    record=save_materials(teacher,record,[draft],publish=False)
    abandoned=upload(teacher,record).json()['id']
    with api.connect() as db:
        db.execute('UPDATE assignment_files SET created=?',((homework.now()-timedelta(days=2)).isoformat(),))
        assignment_files.collect_unused(db)
        assert {r[0] for r in db.execute('SELECT id FROM assignment_files')}=={linked,draft}
        assert not (assignment_files.root_for(db)/(abandoned+'.bin')).exists()
    source=api.DB;destination=tmp_path/'restore'
    manifest=create_backup(source,destination);assert len(manifest['files'])==2
    monkeypatch.setattr(api,'DB',destination/source.name)
    assert download(parent,linked,'s1').content==b'Saved'
    assert download(parent,draft,'s1').status_code==404
    assert download(teacher,draft).content==b'Private'


def test_limits_types_and_global_quota(client,monkeypatch):
    record,_,_,teacher=setup(client)
    for body,name in [(b'', 'empty.txt'),(b'wrong','bad.pdf'),(b'code','x.exe')]:
        assert upload(teacher,record,body,name).status_code==422
    assert upload(teacher,record,b'x'*(homework_files.MAX_FILE+1)).status_code==413
    id=upload(teacher,record).json()['id']
    with api.connect() as db:
        assert homework_files.total_stored(db)==len(b'Teacher materials')
    record['document']['content']['homeworkFiles']=[id,id]
    assert write(teacher,record).status_code==422
    record['document']['content']['homeworkFiles']=[id]*6
    assert write(teacher,record).status_code==422
    monkeypatch.setenv('ATLAS_UPLOAD_TOTAL_MB','10')
    assert upload(teacher,record).status_code==413
    with api.connect() as db:assert db.execute('SELECT count(*) FROM assignment_files').fetchone()[0]==1


def test_deadline_boundary_atomic_rejection_and_draft_preservation(client,monkeypatch):
    record,pupil,parent,teacher=setup(client);key=record['lessonKey']
    record=save_materials(teacher,record,[],homeworkLatePolicy='block')
    before=datetime(2026,8,19,18,59,59,tzinfo=timezone.utc)
    monkeypatch.setattr(homework,'now',lambda:before)
    current=get(pupil,key);assert current['canSubmit'] and not current['deadlineClosed']
    sent=answer(pupil,key,current).json();assert sent['submitted']['latePolicy']=='block' and not sent['submitted']['late']
    review(teacher,key,sent,'returned','Доработайте').raise_for_status()
    after=before+timedelta(seconds=1);monkeypatch.setattr(homework,'now',lambda:after)
    current=get(pupil,key);assert not current['canSubmit'] and current['canSaveDraft'] and current['deadlineClosed']
    draft=answer(pupil,key,current,'draft','Сохранить после срока');assert draft.status_code==200
    refused=answer(pupil,key,draft.json(),text='Нельзя отправить');assert refused.status_code==409
    unchanged=get(pupil,key);assert unchanged['draft']['text']=='Сохранить после срока' and unchanged['version']==draft.json()['version']
    assert get(parent,key)['submitted']['text']=='Решение ученика'
    record=save_materials(teacher,record,[],homeworkLatePolicy='allow')
    current=get(pupil,key);assert current['canSubmit'] and not current['assignmentChanged']  # a late-policy change is not substantive
    final=answer(pupil,key,current).json();assert final['submitted']['late'] and final['submitted']['latePolicy']=='allow'
    assert final['submitted']['attempt']==2


def test_block_requires_date_and_homework_and_commit_checks_actual_clock(client,monkeypatch):
    record,pupil,_,teacher=setup(client);key=record['lessonKey']
    record['document']['content'].update(homeworkLatePolicy='block',dueDate=None)
    assert write(teacher,record).status_code==422
    record['document']['content']['dueDate']='2026-08-19'
    record=save_materials(teacher,record,[])
    before=datetime(2026,8,19,18,59,59,tzinfo=timezone.utc)
    monkeypatch.setattr(homework,'now',lambda:before)
    current=get(pupil,key)
    original=api.homework.recheck
    def crosses(db,ctx):
        original(db,ctx)
        monkeypatch.setattr(homework,'now',lambda:before+timedelta(seconds=1))
    monkeypatch.setattr(api.homework,'recheck',crosses)
    assert answer(pupil,key,current).status_code==409
    with api.connect() as db:assert db.execute('SELECT count(*) FROM homework_submissions').fetchone()[0]==0


def test_material_read_rechecks_current_account_after_context(client,monkeypatch):
    record,_,parent,teacher=setup(client)
    id=upload(teacher,record).json()['id'];save_materials(teacher,record,[id])
    original=api.homework.context
    def revoked(*args,**kwargs):
        ctx=original(*args,**kwargs)
        with api.connect() as db:db.execute('UPDATE accounts SET version=version+1 WHERE id=?',(ctx['user']['id'],))
        return ctx
    monkeypatch.setattr(api.homework,'context',revoked)
    assert download(parent,id,'s1').status_code==409


def test_failed_upload_cleanup_does_not_exhaust_shared_slots(client,monkeypatch):
    from contextlib import contextmanager
    import threading
    from fastapi import HTTPException
    record,_,_,teacher=setup(client)
    slots=threading.BoundedSemaphore(1)
    monkeypatch.setattr(homework_files,'UPLOAD_SLOTS',slots)
    @contextmanager
    def unavailable():
        raise RuntimeError('Synthetic database failure during cleanup')
        yield  # contextmanager protocol
    def rejected(path,name):
        monkeypatch.setattr(api.homework.access,'connect',unavailable)
        raise HTTPException(422,'Synthetic invalid file')
    monkeypatch.setattr(homework_files,'validate_file',rejected)
    with pytest.raises(RuntimeError,match='Synthetic database failure'):
        upload(teacher,record)
    assert slots.acquire(blocking=False)
    slots.release()


def test_identical_materials_are_stored_once_and_released_by_reference(client,tmp_path,monkeypatch):
    from tools.backup_school import create_backup
    from tools.restore_school import verify_backup
    record,pupil,parent,teacher=setup(client);body=b'Same worksheet'
    other=entry(client,lesson(client,'Математика')['lessonKey'])
    a=upload(teacher,record,body).json();b=upload(teacher,record,body).json()
    c=upload(client,other,body).json()  # another author and lesson
    assert len({a['id'],b['id'],c['id']})==3 and download(teacher,b['id']).content==body
    with api.connect() as db:
        assert {r[0] for r in db.execute('SELECT coalesce(blob_id,id) FROM assignment_files')}=={a['id']}
        assert [p.name for p in assignment_files.root_for(db).glob('*.bin')]==[a['id']+'.bin']
        assert homework_files.module_stored(db,'assignments')==len(body)==homework_files.total_stored(db)
    monkeypatch.setattr(assignment_files,'MAX_OWNER',len(body)+homework_files.MAX_FILE)
    d=upload(teacher,record,body);assert d.status_code==200,d.text  # the owner's quota counts the content once
    save_materials(teacher,record,[b['id']])
    assert download(parent,c['id'],'s1').status_code in [403,404]  # another lesson's row keeps its own access rules
    with api.connect() as db:
        db.execute('UPDATE assignment_files SET created=?',((homework.now()-timedelta(days=2)).isoformat(),))
        assignment_files.collect_unused(db)
        assert {r[0] for r in db.execute('SELECT id FROM assignment_files')}=={b['id']}
        assert (assignment_files.root_for(db)/(a['id']+'.bin')).is_file()  # still referenced by b
    assert download(parent,b['id'],'s1').content==body
    destination=tmp_path/'restore';source=api.DB
    manifest=create_backup(source,destination);assert len(manifest['files'])==1
    assert verify_backup(destination)['files']==1
    monkeypatch.setattr(api,'DB',destination/source.name)
    assert download(parent,b['id'],'s1').content==body
    monkeypatch.setattr(api,'DB',source)
    save_materials(teacher,entry(teacher,record['lessonKey']),[])
    with api.connect() as db:
        assert (assignment_files.root_for(db)/(a['id']+'.bin')).is_file()  # saved history keeps the row and the bytes


def test_blob_column_migration_is_idempotent_and_keeps_legacy_rows(client,tmp_path):
    from contextlib import closing
    import sqlite3
    with closing(sqlite3.connect(tmp_path/'legacy.db')) as db:
        db.execute('''CREATE TABLE assignment_files (id TEXT PRIMARY KEY,workspace TEXT,lesson_key TEXT,owner_id TEXT,
            filename TEXT,size INTEGER,created TEXT,status TEXT,sha256 TEXT,day TEXT)''')
        db.execute("INSERT INTO assignment_files VALUES (?,?,?,?,?,?,?,?,?,?)",('a'*32,'demo','k','o','x.txt',3,'2026-01-01','ready','b'*64,'2026-08-17'))
        assignment_files.tables(db);assignment_files.tables(db)
        columns=[r[1] for r in db.execute('PRAGMA table_info(assignment_files)')]
        assert columns.count('blob_id')==1 and columns[-1]=='blob_id'
        assert db.execute('SELECT blob_id FROM assignment_files').fetchone()[0] is None
        assert homework_files.stored(db,'assignment_files')==3
    record,_,parent,teacher=setup(client)
    first=upload(teacher,record,b'Legacy bytes').json()['id']
    with api.connect() as db:db.execute('UPDATE assignment_files SET blob_id=NULL')  # as if uploaded before the column existed
    assert download(teacher,first).content==b'Legacy bytes'
    second=upload(teacher,record,b'Legacy bytes').json()['id']
    with api.connect() as db:
        assert db.execute('SELECT blob_id FROM assignment_files WHERE id=?',(second,)).fetchone()[0]==first
        assert [p.name for p in assignment_files.root_for(db).glob('*.bin')]==[first+'.bin']
        assert not homework_files.referenced(db,'assignment_files',second)
        assert homework_files.referenced(db,'assignment_files',first,exclude=first)


def test_dedupe_skips_a_target_whose_file_is_missing(client):
    record,_,_,teacher=setup(client)
    first=upload(teacher,record,b'Same bytes').json()['id']
    with api.connect() as db:(assignment_files.root_for(db)/(first+'.bin')).unlink()
    second=upload(teacher,record,b'Same bytes').json()['id']
    with api.connect() as db:
        assert db.execute('SELECT blob_id FROM assignment_files WHERE id=?',(second,)).fetchone()[0]==second
        assert (assignment_files.root_for(db)/(second+'.bin')).is_file()
    assert download(teacher,second).content==b'Same bytes'
