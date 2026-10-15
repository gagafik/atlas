"""Shared topic resources, live permission checks, and immutable lesson delivery."""
from copy import deepcopy
from datetime import date, timedelta

import pytest
from backend import api, teaching_material_files
from backend.access import now
from backend.models import DatedLesson
from test_access import client
from test_curriculum import content, new, request, save, staff
from test_journal import entry, lesson, report, write
from test_teaching_materials import save as material, get, upload, download


def ref(m):
    return dict(id=m['id'],version=m['version'])


def topics(*materials):
    result=content()['topics'];result[0]['materials']=[ref(m) for m in materials]
    return result


def resolve(c,p,**extra):
    return request(c,'/topic-materials',day='2026-08-17',lessonKey=lesson(c)['lessonKey'],
                   planId=p['id'],topicId='intro',version=p['version'],**extra)


def use(c,p,record=None):
    record=record or entry(c,lesson(c)['lessonKey'])
    r=resolve(c,p);assert r.status_code==200,r.text
    record['document']['content']['materials']=r.json()['materials']
    record['document']['content']['curriculum']=dict(planId=p['id'],version=p['version'],topicId='intro',units=1)
    return record


def test_plan_requires_shared_specific_version_and_filters_picker(client):
    teacher,_,_,_=staff(client)
    private=material(teacher,title='Private heading').json()
    assert not private['canUseInPlan']
    assert save(teacher,topics=topics(private)).status_code==409
    shared=material(teacher,private,visibility='staff',title='Shared heading').json()
    assert shared['canUseInPlan'] and not get(teacher,private['id'],version=1).json()['canUseInPlan']
    assert save(teacher,topics=topics(private)).status_code==409
    assert save(client,topics=topics(private)).status_code==409  # even the admin cannot leak private history
    private2=material(teacher).json()
    listing=teacher.get('/api/journal/library?workspace=demo&sharedOnly=true').json()['items']
    assert [m['id'] for m in listing]==[shared['id']]
    p=new(teacher,topics=topics(shared))
    assert p['content']['topics'][0]['materials']==[ref(shared)]
    assert p['materialStatus'][f"{shared['id']}:2"]==ref(shared)|dict(available=True,title='Shared heading')
    assert 'Private heading' not in str(p) and private2['id'] not in str(p)


def test_revocation_redacts_history_and_keeps_existing_links_without_readding(client):
    teacher,_,_,_=staff(client)
    m=material(teacher,visibility='staff',title='Sensitive heading',text='Secret text').json()
    p=new(teacher,topics=topics(m))
    material(teacher,m,visibility='private').raise_for_status()
    hidden=request(teacher,'/'+p['id'],version=1).json()
    assert hidden['materialStatus'][f"{m['id']}:1"]==ref(m)|dict(available=False)
    assert 'Sensitive heading' not in str(hidden) and 'Secret text' not in str(hidden)
    assert resolve(teacher,p).json()==dict(materials=[],unavailable=[ref(m)])
    p2=save(teacher,p,title='Edited without reopening material').json()
    changed=deepcopy(p2['content']['topics']);changed[1]['materials']=[ref(m)]
    assert save(teacher,p2,topics=changed).status_code==409  # cannot move a closed reference to a new topic
    p3=save(teacher,p2,topics=content()['topics']).json()
    assert save(teacher,p3,topics=topics(m)).status_code==409
    assert request(teacher,'/'+p['id']).json()['version']==3


def test_copy_needs_explicit_omission_and_preserves_original_and_available_versions(client):
    teacher,_,_,_=staff(client)
    shared=material(teacher,visibility='staff').json()
    closed=material(teacher,visibility='staff',title='Later closed').json()
    p=new(teacher,topics=topics(shared,closed))
    material(teacher,closed,visibility='private').raise_for_status()
    body=dict(version=1,groupId=p['content']['groupId'],start='2027-08-17',end='2028-06-11')
    assert request(teacher,f"/{p['id']}/copy",'POST',body).status_code==409
    r=request(teacher,f"/{p['id']}/copy",'POST',body|dict(omitUnavailableMaterials=True));assert r.status_code==200,r.text
    copied=r.json();assert copied['omittedMaterials']==1
    assert copied['content']['topics'][0]['materials']==[ref(shared)]
    assert request(teacher,'/'+p['id']).json()['content']==p['content']


def test_topic_transfer_publication_keeps_version_files_after_source_is_closed(client):
    teacher,_,student,parent=staff(client)
    file=upload(teacher).json()['id']
    m=material(teacher,visibility='staff',fileIds=[file],text='First version').json()
    p=new(teacher,topics=topics(m));record=use(teacher,p);key=record['lessonKey']
    assert report(parent).json()['rows']==[]
    assert download(parent,file,m,key).status_code==404
    saved=write(teacher,record,publish=True);assert saved.status_code==200,saved.text
    m2=material(teacher,m,visibility='private',fileIds=[],text='Private second version').json()
    p2=save(teacher,p,title='Plan changed').json()
    assert resolve(teacher,p2).json()['materials']==[]
    for family in [student,parent]:
        row=report(family).json()['rows'][0]['content']
        assert row['materials'][0]['text']=='First version' and row['curriculum']['version']==1
        assert download(family,file,m,key).content==b'Lesson material'
        assert download(family,file,m2,key).status_code==404
        assert request(family,'/topic-materials',day='2026-08-17',lessonKey=key,planId=p['id'],topicId='intro',version=1).status_code==403


def test_substitute_can_resolve_but_revocation_before_save_is_atomic(client):
    teacher,other,_,_=staff(client)
    m=material(teacher,visibility='staff').json();p=new(teacher,topics=topics(m))
    s=api.read('demo');s.datedLessons=[DatedLesson(id='sub',name='Replacement',kind='change',placementId='eng',
        sourceDate=date(2026,8,17),onDate=date(2026,8,17),teacherIds=['t1'])];api.save('demo',s)
    assert request(other,'/'+p['id']).status_code==403
    record=use(other,p);key=record['lessonKey']
    material(teacher,m,visibility='private').raise_for_status()
    assert write(other,record,publish=True).status_code==403
    assert entry(other,key)['version']==0
    with api.connect() as db:
        for table in ['teaching_material_lessons','curriculum_bindings','curriculum_publications']:
            exists=db.execute('SELECT 1 FROM sqlite_master WHERE name=?',(table,)).fetchone()
            assert not exists or db.execute(f'SELECT count(*) FROM {table}').fetchone()[0]==0


def test_resolve_rejects_stale_plan_wrong_lesson_archived_and_missing_topic(client):
    p=new(client,topics=topics(material(client,visibility='staff').json()))
    p2=save(client,p,title='Current').json()
    assert resolve(client,p).status_code==409
    key=lesson(client)['lessonKey']
    base=dict(day='2026-08-17',lessonKey=key,planId=p['id'],topicId='intro',version=2)
    assert request(client,'/topic-materials',**(base|dict(lessonKey=lesson(client,subject='Математика')['lessonKey']))).status_code==403
    assert request(client,'/topic-materials',**(base|dict(topicId='missing'))).status_code==404
    request(client,f"/{p['id']}/archive",'POST',dict(version=2,archived=True)).raise_for_status()
    assert resolve(client,p2).status_code==409


@pytest.mark.parametrize('change',['account','school'])
def test_resolve_rechecks_account_and_school_after_capturing_lesson(client,monkeypatch,change):
    teacher,_,_,_=staff(client);p=new(teacher,topics=topics(material(teacher,visibility='staff').json()))
    original=api.curriculum.recheck
    def altered(db,user,workspace,school):
        if change=='account': db.execute("UPDATE accounts SET version=version+1 WHERE login='teacher'")
        else: db.execute("UPDATE workspaces SET revision=revision+1 WHERE id='demo'")
        original(db,user,workspace,school)
    monkeypatch.setattr(api.curriculum,'recheck',altered)
    assert resolve(teacher,p).status_code==409


def test_limits_and_deduplicated_status_for_repeated_topic_resources(client):
    m=material(client,visibility='staff',text='Long resource text').json()
    repeated=[dict(id=f't{i}',title=f'Topic {i}',hours=1,materials=[ref(m)]) for i in range(500)]
    p=new(client,topics=repeated)
    assert len(p['materialStatus'])==1 and 'Long resource text' not in str(p)
    duplicate=deepcopy(repeated[:1]);duplicate[0]['materials']=[ref(m),ref(m)|dict(version=2)]
    assert save(client,p,topics=duplicate).status_code==422
    excess=[dict(id=f't{i}',title='Topic',hours=1,materials=[dict(id=f'm{i}',version=1)]) for i in range(101)]
    assert save(client,p,topics=excess).status_code==422
    repeated[0]['materials']=[dict(id=f'm{i}',version=1) for i in range(11)]
    assert save(client,p,topics=repeated).status_code==422


def test_plan_only_file_is_retained_and_restored_with_backup(client,tmp_path,monkeypatch):
    from tools.backup_school import create_backup
    file=upload(client).json()['id'];m=material(client,visibility='staff',fileIds=[file]).json()
    p=new(client,topics=topics(m))
    material(client,m,fileIds=[],text='New version without file').raise_for_status()
    with api.connect() as db:
        db.execute('UPDATE teaching_material_files SET created=?',((now()-timedelta(days=2)).isoformat(),))
        teaching_material_files.collect_unused(db)
    source=api.DB;backup=tmp_path/'copy';manifest=create_backup(source,backup)
    assert len(manifest['files'])==1
    monkeypatch.setattr(api,'DB',backup/source.name)
    assert request(client,'/'+p['id']).json()['content']==p['content']
    assert resolve(client,p).json()['materials'][0]['files'][0]['id']==file
    assert download(client,file,m).content==b'Lesson material'
