"""Quiz bank packages: format hardening, export and import with attachments."""
from datetime import timedelta
import hashlib
import io
import json
import os
import pathlib
import sqlite3
import stat
import struct
import threading
import time
from uuid import uuid4
import warnings
from zipfile import ZipFile, ZipInfo, ZIP_BZIP2, ZIP_DEFLATED, ZIP_STORED

from fastapi import HTTPException
import pytest
from starlette.requests import ClientDisconnect
from backend import api, homework_files, quiz_bank_exchange as exchange, quiz_bank_package as pkg, quiz_files
from backend.quiz_models import Question
from test_access import client, signed
from test_homework import setup
from test_quiz_files import ok, upload, file_get
from seeding import insert_row

FILES_PER_QUESTION=next(m.max_length for m in Question.model_fields['fileIds'].metadata if hasattr(m,'max_length'))


def digest(data):
    return hashlib.sha256(data).hexdigest()


def file_id(n=1):
    return f'{n:032x}'


def essay(**extra):
    return dict(subject='Физика',tags=['опыты'],archived=False,question=dict(kind='essay',text='Опишите опыт')|extra)


def manifest(files=(),questions=None,**extra):
    """files: (id, filename, bytes); the first question links them all."""
    items=[dict(id=id,filename=name,size=len(data),sha256=digest(data)) for id,name,data in files]
    if questions is None:questions=[essay(fileIds=[id for id,_,_ in files][:6])]
    return dict(format='atlas.quiz-bank',version=1,questions=questions,files=items)|extra


def build(path,doc,files=(),compression=ZIP_STORED,extra=()):
    """Writes manifest.json, files/<id> entries and any extra (name|ZipInfo, bytes) members."""
    with warnings.catch_warnings():
        warnings.simplefilter('ignore')
        with ZipFile(path,'w',compression=compression) as z:
            z.writestr('manifest.json',json.dumps(doc,ensure_ascii=False))
            for id,_,data in files:z.writestr('files/'+id,data)
            for name,data in extra:z.writestr(name,data)
    return path


def set_encrypted(path,name):
    """zipfile's writer clears the flag, so set bit 0 in the local and central headers."""
    data=bytearray(path.read_bytes())
    with ZipFile(path) as z:offset=z.getinfo(name).header_offset
    data[offset+6]|=1
    position=data.find(b'PK\x01\x02')
    while position>=0:
        length=struct.unpack_from('<H',data,position+28)[0]
        if bytes(data[position+46:position+46+length])==name.encode():data[position+8]|=1
        position=data.find(b'PK\x01\x02',position+1)
    path.write_bytes(bytes(data))


def link(name,target):
    info=ZipInfo(name);info.external_attr=(stat.S_IFLNK|0o777)<<16
    return info,target


def end_record(entries,size,offset=0):
    return struct.pack('<4s4H2LH',b'PK\x05\x06',0,0,entries,entries,size,offset,0)


def zip64_end(entries,size,offset=0):
    # The locator points at the record, which follows the central directory.
    record=struct.pack('<4sQ2H2L4Q',b'PK\x06\x06',44,45,45,0,0,entries,entries,size,offset)
    return record+struct.pack('<4sLQL',b'PK\x06\x07',0,offset+size,1)+struct.pack('<4s4H2LH',b'PK\x05\x06',0xffff,0xffff,0xffff,0xffff,0xffffffff,0xffffffff,0)


def central_entries(count):
    header=struct.pack('<4s4B4HL2L5H2L',b'PK\x01\x02',20,0,20,0,0,0,0,0,0,0,0,1,0,0,0,0,0,0)
    return (header+b'a')*count


def rejected(path,filename='package.zip'):
    with pytest.raises(HTTPException) as caught:pkg.inspect_package(path,filename)
    return caught.value


def test_valid_zip_inspects_and_extracts(tmp_path):
    data=b'Question material';files=[(file_id(),'task.txt',data)]
    package=pkg.inspect_package(build(tmp_path/'ok.zip',manifest(files),files,compression=ZIP_DEFLATED),'ok.zip')
    assert len(package.questions)==1 and package.files[0].size==len(data)
    target=tmp_path/'out'/'a.bin'
    pkg.extract_files(tmp_path/'ok.zip',package,{file_id():target})
    assert target.read_bytes()==data


def test_two_questions_may_reference_the_same_file(tmp_path):
    data=b'Shared';files=[(file_id(),'task.txt',data)]
    doc=manifest(files,[essay(fileIds=[file_id()]),essay(solutionFileIds=[file_id()])])
    assert len(pkg.inspect_package(build(tmp_path/'p.zip',doc,files),'p.zip').files)==1


@pytest.mark.parametrize('case,status',[
    ('extra-file',422),
    ('missing-file',422),
    ('directory',422),
    ('symlink',422),
    ('encrypted',422),
    ('size-mismatch',422),
    ('duplicate',422),
    ('manifest-too-big',413),
    ('no-manifest',422),
    ('path-in-name',422),
    ('bad-extension',422),
    ('backslash',422),
    ('bzip2',422),
    ('too-many-entries',422),
    ('not-a-zip',422),
])
def test_inspect_rejects_bad_archives(tmp_path,case,status):
    data=b'Question material';files=[(file_id(),'task.txt',data)];path=tmp_path/'bad.zip'
    if case=='extra-file':build(path,manifest(files),files,extra=[('files/'+file_id(2),b'x')])
    elif case=='missing-file':build(path,manifest(files),[])
    elif case=='directory':build(path,manifest(files),files,extra=[(ZipInfo('files/'),b'')])
    elif case=='symlink':build(path,manifest(files),files,extra=[link('files/link','manifest.json')])
    elif case=='encrypted':
        build(path,manifest(files),files);set_encrypted(path,'files/'+file_id())
    elif case=='size-mismatch':build(path,manifest(files),[(file_id(),'task.txt',data+b'!')])
    elif case=='duplicate':build(path,manifest(files),files,extra=[('files/'+file_id(),b'Other material!')])
    elif case=='manifest-too-big':build(path,manifest(files)|dict(padding=' '*pkg.MAX_MANIFEST),files,compression=ZIP_DEFLATED)
    elif case=='no-manifest':
        with ZipFile(path,'w') as z:z.writestr('files/'+file_id(),data)
    elif case=='path-in-name':build(path,manifest([(file_id(),'a/task.txt',data)]),files)
    elif case=='bad-extension':build(path,manifest([(file_id(),'task.exe',data)]),files)
    elif case=='backslash':
        build(path,manifest(files),files)
        path.write_bytes(path.read_bytes().replace(b'files/'+file_id().encode(),b'files\\'+file_id().encode()))
    elif case=='bzip2':build(path,manifest(files),files,compression=ZIP_BZIP2)
    elif case=='too-many-entries':build(path,manifest(files),files,extra=[(f'files/{n:032x}',b'x') for n in range(2,pkg.MAX_FILES+3)])
    else:path.write_bytes(b'not a zip archive')
    assert rejected(path).status_code==status


def test_json_with_files_wrong_extension_and_size(tmp_path):
    files=[(file_id(),'task.txt',b'abc')];path=tmp_path/'p.json'
    path.write_text(json.dumps(manifest(files)),encoding='utf-8')
    assert rejected(path,'p.json').status_code==422
    path.write_bytes(b'x');assert rejected(path,'x.rar').status_code==422
    path.write_bytes(b'x'*(pkg.MAX_MANIFEST+1));assert rejected(path,'big.json').status_code==413


def test_bom_json_and_manifest_are_accepted(tmp_path):
    valid=json.dumps(manifest(),ensure_ascii=False).encode()
    assert len(pkg.parse_manifest(b'\xef\xbb\xbf'+valid).questions)==1
    path=tmp_path/'bom.json';path.write_bytes(b'\xef\xbb\xbf'+valid)
    assert len(pkg.inspect_package(path,'bom.json').questions)==1
    zipped=tmp_path/'bom.zip'
    with ZipFile(zipped,'w') as z:z.writestr('manifest.json',b'\xef\xbb\xbf'+valid)
    assert len(pkg.inspect_package(zipped,'bom.zip').questions)==1


@pytest.mark.parametrize('document,status',[
    (lambda:manifest(questions=[essay(),dict(subject='x',question=dict(kind='essay'))]),422),
    (lambda:manifest(questions=[essay()|dict(id='forged')]),422),
    (lambda:manifest(questions=[dict(subject='',question=dict(kind='single',text='Q'))]),422),
    (lambda:b'not json',422),
    (lambda:b'x'*(pkg.MAX_MANIFEST+1),413),
],ids=['question-without-text','forged-id','choice-without-options','not-json','too-big'])
def test_invalid_manifests_are_refused(document,status):
    data=document()
    with pytest.raises(HTTPException) as caught:pkg.parse_manifest(data if isinstance(data,bytes) else json.dumps(data).encode())
    assert caught.value.status_code==status


def test_entry_rejects_stored_projections_but_allows_empty_lists():
    assert pkg.Entry.model_validate(essay(attachments=[],solutionAttachments=[]))
    with pytest.raises(ValueError):
        pkg.Entry.model_validate(essay(attachments=[dict(id=file_id(),filename='a.txt',size=1,created='x')]))


def test_manifest_bytes_strips_projections_and_parses_back_to_the_same_package():
    package=pkg.parse_manifest(pkg.template_bytes())
    compact=pkg.manifest_bytes(package);pretty=pkg.manifest_bytes(package,pretty=True)
    assert b'attachments' not in compact and b'solutionAttachments' not in pretty
    assert pkg.parse_manifest(compact)==package==pkg.parse_manifest(pretty)


@pytest.fixture
def unopened(monkeypatch):
    """ZipFile parses the whole central directory, so every bound must fire before the archive is opened."""
    def forbidden(path):raise AssertionError('the archive was opened before the directory bound')
    monkeypatch.setattr(pkg,'archive',forbidden)


def test_huge_central_directory_with_small_declared_count_is_rejected(tmp_path,unopened):
    # Only the size bound can catch this: the declared count (1) is within MAX_ENTRIES.
    path=tmp_path/'big.zip';directory=central_entries(20000)
    assert len(directory)>pkg.MAX_DIRECTORY
    path.write_bytes(directory+end_record(1,len(directory)))
    assert rejected(path).status_code==422


@pytest.mark.parametrize('case',['plain','zip64'])
def test_oversized_declared_entry_count_is_rejected(tmp_path,unopened,case):
    # Only the count bound can catch this: the directory itself is tiny.
    path=tmp_path/'count.zip';directory=central_entries(1)
    path.write_bytes(directory+(end_record(pkg.MAX_ENTRIES+1,len(directory)) if case=='plain' else zip64_end(400000,len(directory))))
    assert rejected(path).status_code==422


def test_the_most_files_allowed_pass_the_directory_bound(tmp_path):
    files=[(file_id(n),f'f{n}.txt',b'x%d'%n) for n in range(1,pkg.MAX_FILES+1)]
    doc=manifest(files,[essay(fileIds=[id for id,_,_ in files[i:i+FILES_PER_QUESTION]]) for i in range(0,len(files),FILES_PER_QUESTION)])
    assert len(pkg.inspect_package(build(tmp_path/'max.zip',doc,files),'max.zip').files)==pkg.MAX_FILES


def single_zip(tmp_path,data=b'Question material',compression=ZIP_STORED):
    files=[(file_id(),'task.txt',data)];path=build(tmp_path/'p.zip',manifest(files),files,compression=compression)
    return path,pkg.inspect_package(path,'p.zip')


def test_extract_failure_on_second_file_removes_the_first(tmp_path):
    files=[(file_id(1),'a.txt',b'First material'),(file_id(2),'b.txt',b'Second material')]
    path=build(tmp_path/'p.zip',manifest(files,[essay(fileIds=[file_id(1),file_id(2)])]),files)
    package=pkg.inspect_package(path,'p.zip');package.files[1].sha256=digest(b'other')
    targets={file_id(1):tmp_path/'dest'/'1.bin',file_id(2):tmp_path/'dest'/'2.bin'}
    with pytest.raises(HTTPException) as caught:pkg.extract_files(path,package,targets)
    assert caught.value.status_code==422
    assert list((tmp_path/'dest').iterdir())==[]


def test_extract_media_failure_removes_files(tmp_path):
    files=[(file_id(),'picture.png',b'<html/> not a png')]
    path=build(tmp_path/'p.zip',manifest(files),files);package=pkg.inspect_package(path,'p.zip')
    target=tmp_path/'dest'/'a.bin'
    with pytest.raises(HTTPException) as caught:pkg.extract_files(path,package,{file_id():target})
    assert caught.value.status_code==422 and not target.exists()


def test_extract_corrupt_deflate_is_a_422_not_a_500(tmp_path):
    path,package=single_zip(tmp_path,b'Question material '*500,ZIP_DEFLATED)
    raw=bytearray(path.read_bytes())
    with ZipFile(path) as z:offset=z.getinfo('files/'+file_id()).header_offset
    name,extra=struct.unpack_from('<2H',raw,offset+26);start=offset+30+name+extra
    raw[start:start+8]=b'\xff'*8;path.write_bytes(bytes(raw))
    target=tmp_path/'dest'/'a.bin'
    with pytest.raises(HTTPException) as caught:pkg.extract_files(path,package,{file_id():target})
    assert caught.value.status_code==422
    assert list((tmp_path/'dest').iterdir())==[]


def test_extract_stops_at_the_declared_size(tmp_path):
    path,package=single_zip(tmp_path)
    package.files[0].size=3;target=tmp_path/'dest'/'a.bin'
    with pytest.raises(HTTPException) as caught:pkg.extract_files(path,package,{file_id():target})
    assert caught.value.status_code==422 and not target.exists()


def written(tmp_path,data=b'Question material'):
    files=[(file_id(),'task.txt',data)];source=tmp_path/'storage.bin';source.write_bytes(data)
    return pkg.parse_manifest(json.dumps(manifest(files)).encode()),{file_id():source}


def test_write_package_round_trip(tmp_path):
    package,sources=written(tmp_path)
    pkg.write_package(tmp_path/'out.zip',package,sources)
    assert pkg.inspect_package(tmp_path/'out.zip','out.zip')==package


def test_write_package_disk_failure_is_not_a_missing_attachment(tmp_path,monkeypatch):
    package,sources=written(tmp_path);original=ZipFile.open
    def broken(self,name,mode='r',*args,**kwargs):
        if mode=='w' and isinstance(name,str):raise OSError(28,'No space left on device')
        return original(self,name,mode,*args,**kwargs)
    monkeypatch.setattr(ZipFile,'open',broken)
    with pytest.raises(OSError):pkg.write_package(tmp_path/'out.zip',package,sources)


# ---- HTTP: template, export, preview and atomic import ----

PNG=b'\x89PNG\r\n\x1a\n'+bytes(40)
EXPORT='/api/journal/quiz-bank/export?workspace=demo'


def temp_children():
    base=api.DB.resolve().with_suffix('.files')/'quiz-bank-tmp'
    return list(base.iterdir()) if base.exists() else []


def counts():
    with api.connect() as db:
        tables=('quiz_questions','quiz_files','quiz_bank_imports','quiz_file_links')
        return tuple(db.execute(f'SELECT count(*) FROM {t}').fetchone()[0] for t in tables)+(
            db.execute("SELECT count(*) FROM audit WHERE action='quiz.bank_imported'").fetchone()[0],)


def stored_files():
    with api.connect() as db:root=quiz_files.root_for(db)
    return sorted(p.name for p in root.glob('*.bin')) if root.exists() else []


def account(login):
    with api.connect() as db:return db.execute('SELECT id FROM accounts WHERE login=?',(login,)).fetchone()[0]


def seed_file(db,login,size,status,created=None,name='x.txt'):
    """A quiz_files row of that account, as a crash or a full quota leaves it."""
    api.quizzes.tables(db);id=uuid4().hex
    insert_row(db,'quiz_files',id=id,workspace='demo',owner_id=account(login),filename=name,size=size,created=created or quiz_files.now().isoformat(),status=status)
    return id


TEMPLATE_QUESTIONS=len(pkg.parse_manifest(pkg.template_bytes()).questions)


def put_question(reader,id,version=0,subject='Физика',archived=False,tags=('опыты',),**question):
    content=dict(kind='essay',text='Опишите опыт')|question
    r=reader.put(f'/api/journal/quiz-bank/{id}?workspace=demo',json=dict(version=version,subject=subject,tags=list(tags),archived=archived,question=content))
    return ok(r)


def export(reader,*questions):
    return reader.post(EXPORT,json=dict(items=[dict(id=q['id'],version=q['version']) for q in questions]))


def bank(reader,archived=False):
    return ok(reader.get('/api/journal/quiz-bank',params=dict(workspace='demo',archived=str(archived).lower())))['items']


def preview(reader,data,name='p.zip'):
    return reader.post('/api/journal/quiz-bank/import/preview',params=dict(workspace='demo',filename=name),content=data)


def apply(reader,data,name='p.zip',request=None,hash=None):
    params=dict(workspace='demo',filename=name,requestId=request or uuid4().hex,packageHash=hash or digest(data))
    return reader.post('/api/journal/quiz-bank/import',params=params,content=data)


def zipped(tmp_path,files,questions=None,name='pack.zip'):
    return build(tmp_path/name,manifest(files,questions),files).read_bytes()


def exported(client):
    """Teacher with an archived question that owns a text file and a PNG solution."""
    record,pupil,parent,teacher=setup(client)
    text=ok(upload(teacher,b'Question material','task.txt'));picture=ok(upload(teacher,PNG,'picture.png'))
    source=put_question(teacher,'source',archived=True,fileIds=[text['id']],solutionFileIds=[picture['id']],explanation='Подсказка')
    return pupil,teacher,source,text,picture


def test_export_zip_hides_internal_identifiers(client):
    pupil,teacher,source,text,picture=exported(client)
    response=export(teacher,source);assert response.status_code==200,response.text
    assert response.headers['content-type']=='application/zip' and 'no-store' in response.headers['cache-control']
    assert response.headers['content-disposition'].startswith('attachment')
    with ZipFile(io.BytesIO(response.content)) as z:document=json.loads(z.read('manifest.json'))
    for secret in (text['id'],picture['id'],source['id']):assert secret.encode() not in response.content
    entry=document['questions'][0]
    assert not {'id','ownerId','version'}&set(entry) and not {'attachments','solutionAttachments'}&set(entry['question'])
    assert entry['archived'] is True and entry['subject']=='Физика' and {f['filename'] for f in document['files']}=={'task.txt','picture.png'}
    assert temp_children()==[]


def test_export_without_files_is_json_and_shared_files_are_listed_once(client):
    pupil,teacher,source,text,picture=exported(client)
    plain=put_question(teacher,'plain');response=export(teacher,plain)
    assert response.headers['content-type'].startswith('application/json') and response.headers['content-disposition'].endswith('.json"')
    assert pkg.parse_manifest(response.content).files==[]
    twin=put_question(teacher,'twin',fileIds=[text['id']])
    response=export(teacher,source,twin);assert response.status_code==200
    with ZipFile(io.BytesIO(response.content)) as z:
        document=json.loads(z.read('manifest.json'));assert len(z.namelist())==3
    assert len(document['files'])==2 and document['questions'][1]['question']['fileIds']==document['questions'][0]['question']['fileIds']
    assert temp_children()==[]


def test_legacy_document_without_file_keys_exports(client):
    record,pupil,parent,teacher=setup(client);put_question(teacher,'legacy')
    with api.connect() as db:
        db.execute('UPDATE quiz_questions SET document=? WHERE id=?',(json.dumps(dict(tags=[],question=dict(kind='essay',text='Старый вопрос'))),'legacy'))
    assert pkg.parse_manifest(export(teacher,dict(id='legacy',version=1)).content).files==[]


SELECTION_LIMIT=next(m.max_length for m in pkg.Selection.model_fields['items'].metadata if hasattr(m,'max_length'))


@pytest.mark.parametrize('case,status',[('unrelated-teacher',403),('missing-question',403),('wrong-version',409),('too-many-items',422),('duplicate-item',422),
                                        ('empty-selection',422),('other-workspace',403)])
def test_export_access_and_selection_rules(client,case,status):
    pupil,teacher,source,*_=exported(client)
    items=[dict(id=f'q{n}',version=1) for n in range(SELECTION_LIMIT+1)]
    url,reader,selection={
        'unrelated-teacher':(EXPORT,signed('unrelated'),[dict(id=source['id'],version=source['version'])]),
        'missing-question':(EXPORT,teacher,[dict(id='missing',version=1)]),
        'wrong-version':(EXPORT,teacher,[dict(id=source['id'],version=source['version']+1)]),
        'too-many-items':(EXPORT,teacher,items),
        'duplicate-item':(EXPORT,teacher,[items[0],items[0]]),
        'empty-selection':(EXPORT,teacher,[]),
        'other-workspace':('/api/journal/quiz-bank/export?workspace=school',teacher,items[:1])}[case]
    assert reader.post(url,json=dict(items=selection)).status_code==status
    assert temp_children()==[]


def test_admin_exports_any_question(client):
    pupil,teacher,source,*_=exported(client)
    assert export(client,source).status_code==200


@pytest.mark.parametrize('limit,value',[('MAX_TOTAL',1),('MAX_FILES',0),('MAX_MANIFEST',10)])
def test_export_limits_are_413(client,monkeypatch,limit,value):
    pupil,teacher,source,*_=exported(client);plain=put_question(teacher,'plain')
    monkeypatch.setattr(pkg,limit,value)
    assert export(teacher,source).status_code==413
    if limit=='MAX_MANIFEST':assert export(teacher,plain).status_code==413
    assert temp_children()==[]


@pytest.mark.parametrize('damage',['changed','missing','no-checksum'])
def test_export_detects_changed_or_missing_storage(client,damage):
    pupil,teacher,source,text,picture=exported(client)
    with api.connect() as db:root=quiz_files.root_for(db)
    path=homework_files.file_path(root,text['id'])
    if damage=='changed':path.write_bytes(b'x'*len(path.read_bytes()))
    elif damage=='missing':path.unlink()
    else:
        with api.connect() as db:db.execute('UPDATE quiz_files SET sha256=NULL WHERE id=?',(picture['id'],))
    assert export(teacher,source).status_code==409 and temp_children()==[]


def test_template_download_and_import(client):
    record,pupil,parent,teacher=setup(client)
    response=teacher.get('/api/journal/quiz-bank/template',params=dict(workspace='demo'))
    assert response.status_code==200 and response.headers['content-type'].startswith('application/json')
    assert 'nosniff' in response.headers['x-content-type-options']
    n=len(pkg.parse_manifest(response.content).questions)
    assert pupil.get('/api/journal/quiz-bank/template',params=dict(workspace='demo')).status_code==403
    other=signed('unrelated');shown=ok(preview(other,response.content,'template.json'))
    assert shown['questions']==n and shown['files']==0 and shown['format']=='json'
    result=ok(apply(other,response.content,'template.json',hash=shown['packageHash']))
    assert result['imported']==n and result['files']==0
    items=bank(other);assert len(items)==n and {i['ownerId'] for i in items}=={account('unrelated')}
    assert bank(teacher)==[] and temp_children()==[]


def test_round_trip_creates_owned_copies_with_new_files(client):
    pupil,teacher,source,text,picture=exported(client)
    package=export(teacher,source).content;other=signed('unrelated')
    shown=ok(preview(other,package))
    assert (shown['questions'],shown['files'],shown['archived'],shown['totalSize'])==(1,2,1,len(b'Question material')+len(PNG))
    assert shown['subjects']==['Физика'] and shown['size']==len(package) and shown['packageHash']==digest(package)
    item=shown['items'][0];assert (item['subject'],item['kind'],item['files'],item['archived'])==('Физика','essay',2,True)
    result=ok(apply(other,package,hash=shown['packageHash']))
    assert result['imported']==1 and result['archived']==1 and result['files']==2 and len(result['questionIds'])==1
    [copy]=bank(other,archived=True)
    assert copy['id']==result['questionIds'][0] and copy['id']!=source['id'] and copy['ownerId']==account('unrelated') and copy['version']==1
    assert (copy['subject'],copy['tags'],copy['archived'])==(source['subject'],source['tags'],True)
    skip={'fileIds','solutionFileIds','attachments','solutionAttachments'}
    assert {k:v for k,v in copy['question'].items() if k not in skip}=={k:v for k,v in source['question'].items() if k not in skip}
    for part in ('attachments','solutionAttachments'):
        new,old=copy['question'][part],source['question'][part]
        assert [(a['filename'],a['size']) for a in new]==[(a['filename'],a['size']) for a in old]
        assert not {a['id'] for a in new}&{a['id'] for a in old}
    assert file_get(other,copy['question']['attachments'][0]).content==b'Question material'
    assert file_get(other,copy['question']['solutionAttachments'][0]).content==PNG
    assert file_get(teacher,copy['question']['attachments'][0]).status_code==404
    assert [i['id'] for i in bank(teacher,archived=True)]==['source'] and bank(teacher)==[]
    # The copy is a complete package again: its files keep their checksums, so the importer can export it and the result imports.
    again=export(other,copy);assert again.status_code==200,again.text
    assert ok(apply(other,again.content))['files']==2
    assert temp_children()==[]


def test_admin_import_is_owned_by_the_admin(client):
    pupil,teacher,source,*_=exported(client)
    result=ok(apply(client,export(teacher,source).content))
    assert [i['ownerId'] for i in bank(client,archived=True) if i['id'] in result['questionIds']]==[account('admin')]


def test_preview_writes_nothing(client):
    pupil,teacher,source,*_=exported(client)
    package=export(teacher,source).content;before=(counts(),stored_files())
    assert ok(preview(signed('unrelated'),package))['files']==2
    assert (counts(),stored_files())==before and temp_children()==[]


def test_wrong_package_hash_is_409_and_writes_nothing(client):
    pupil,teacher,source,*_=exported(client);package=export(teacher,source).content;before=(counts(),stored_files())
    assert apply(signed('unrelated'),package,hash=digest(b'other')).status_code==409
    assert (counts(),stored_files())==before and temp_children()==[]


def test_apply_is_idempotent_and_fingerprinted(client,monkeypatch):
    pupil,teacher,source,*_=exported(client);package=export(teacher,source).content;other=signed('unrelated')
    first=ok(apply(other,package,request='a'*32));after=(counts(),stored_files())
    again=ok(apply(other,package,request='a'*32))
    assert again==first and (counts(),stored_files())==after
    different=apply(other,export(teacher,put_question(teacher,'second')).content,'q.json',request='a'*32)
    assert different.status_code==409
    monkeypatch.setattr(homework_files,'total_limit',lambda:0)
    assert ok(apply(other,package,request='a'*32))==first
    assert apply(other,package,request='b'*32).status_code==413
    assert temp_children()==[]


def test_replay_is_per_owner_and_a_new_request_id_makes_a_new_copy(client):
    pupil,teacher,source,*_=exported(client);package=export(teacher,source).content;other=signed('unrelated')
    one=ok(apply(other,package,request='a'*32));two=ok(apply(other,package))
    mine=ok(apply(teacher,package,request='a'*32))
    assert set(one['questionIds']).isdisjoint(two['questionIds']) and set(one['questionIds']).isdisjoint(mine['questionIds'])
    assert len(bank(other,archived=True))==2


def two_file_package(tmp_path):
    files=[(file_id(1),'a.txt',b'First material'),(file_id(2),'b.txt',b'Second material')]
    return zipped(tmp_path,files,[essay(fileIds=[file_id(1)]),essay(fileIds=[file_id(2)])])


def test_a_package_with_a_bad_second_file_imports_nothing(client,tmp_path):
    pupil,teacher,source,*_=exported(client);other=signed('unrelated');before=(counts(),stored_files())
    files=[(file_id(1),'a.txt',b'First material'),(file_id(2),'b.png',b'<html/> not a png')]
    broken=zipped(tmp_path,files,[essay(fileIds=[file_id(1)]),essay(fileIds=[file_id(2)])])
    assert apply(other,broken).status_code==422
    assert (counts(),stored_files())==before and temp_children()==[]
    assert ok(apply(other,two_file_package(tmp_path)))['files']==2


def test_a_failure_while_linking_the_second_question_imports_nothing(client,monkeypatch,tmp_path):
    pupil,teacher,source,*_=exported(client);package=two_file_package(tmp_path);other=signed('unrelated')
    before=(counts(),stored_files());calls=[];original=quiz_files.bind_question
    def second_fails(*args,**kwargs):
        calls.append(1)
        if len(calls)==2:raise HTTPException(422,'Injected failure')
        return original(*args,**kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(quiz_files,'bind_question',second_fails)
        assert apply(other,package).status_code==422
    assert (counts(),stored_files())==before and temp_children()==[]
    assert ok(apply(other,package))['files']==2


def test_concurrent_duplicate_returns_the_stored_result_and_releases_its_files(client,monkeypatch,tmp_path):
    record,pupil,parent,teacher=setup(client);other=signed('unrelated')
    package=zipped(tmp_path,[(file_id(),'a.txt',b'Single material')]);original=pkg.extract_files;inner=[]
    def racing(*args,**kwargs):
        original(*args,**kwargs)
        if not inner:
            inner.append(None);inner[0]=apply(other,package,request='c'*32)
    monkeypatch.setattr(pkg,'extract_files',racing)
    outer=apply(other,package,request='c'*32)
    assert outer.status_code==200 and inner[0].status_code==200 and outer.json()==inner[0].json()
    assert counts()==(1,1,1,1,1) and len(stored_files())==1 and temp_children()==[]


def test_quota_blocks_preview_and_apply_but_not_json(client,monkeypatch):
    pupil,teacher,source,*_=exported(client);package=export(teacher,source).content;other=signed('unrelated')
    template=pkg.template_bytes();before=(counts(),stored_files())
    with monkeypatch.context() as patch:
        patch.setattr(homework_files,'total_limit',lambda:0)
        for call in (preview,apply):assert call(other,package).status_code==413
        assert (counts(),stored_files())==before and ok(apply(other,template,'t.json'))['imported']==TEMPLATE_QUESTIONS
    # The account's own files fill its quota (far above it, yet inside the section budget).
    with api.connect() as db:seed_file(db,'unrelated',homework_files.module_limit('quizzes')//2,'ready',name='big.txt')
    before=(counts(),stored_files())
    for call in (preview,apply):assert call(other,package).status_code==413
    assert (counts(),stored_files())==before and ok(preview(other,template,'t.json'))['questions']==TEMPLATE_QUESTIONS
    assert ok(preview(signed('teacher'),package))['files']==2 and temp_children()==[]


def test_package_files_obey_the_quiz_section_budget(client,monkeypatch):
    pupil,teacher,source,*_=exported(client);package=export(teacher,source).content;other=signed('unrelated');template=pkg.template_bytes()
    monkeypatch.setenv('ATLAS_UPLOAD_QUIZZES_MB','10')  # the section budget; one file of that size fills it
    with api.connect() as db:big=seed_file(db,'teacher',homework_files.module_limit('quizzes'),'ready',name='big.txt')
    before=(counts(),stored_files())
    for call in (preview,apply):assert call(other,package).status_code==413
    assert (counts(),stored_files())==before and uploading()==0 and temp_children()==[]
    assert ok(preview(other,template,'t.json'))['questions']==TEMPLATE_QUESTIONS and ok(apply(other,template,'t.json'))['imported']==TEMPLATE_QUESTIONS
    with api.connect() as db:db.execute('DELETE FROM quiz_files WHERE id=?',(big,))
    assert ok(preview(other,package))['files']==2 and ok(apply(other,package))['files']==2


def test_package_files_obey_the_disk_reserve(client,monkeypatch):
    from collections import namedtuple
    pupil,teacher,source,*_=exported(client);package=export(teacher,source).content;other=signed('unrelated');template=pkg.template_bytes()
    usage=namedtuple('usage','total used free')
    monkeypatch.setattr(homework_files.shutil,'disk_usage',lambda path:usage(10**12,0,1000))
    monkeypatch.setenv('ATLAS_UPLOAD_RESERVE_MB','1');before=(counts(),stored_files())
    for call in (preview,apply):assert call(other,package).status_code==507
    assert (counts(),stored_files())==before and uploading()==0 and temp_children()==[]
    assert ok(apply(other,template,'t.json'))['imported']==TEMPLATE_QUESTIONS  # no attachments, nothing to store
    monkeypatch.setenv('ATLAS_UPLOAD_RESERVE_MB','0')  # 0 disables the disk check
    assert ok(preview(other,package))['files']==2 and ok(apply(other,package))['files']==2


def test_pending_uploads_block_a_second_package(client,tmp_path):
    record,pupil,parent,teacher=setup(client);other=signed('unrelated');package=two_file_package(tmp_path)
    with api.connect() as db:
        for _ in range(2):seed_file(db,'unrelated',10,'uploading')
    before=counts();response=apply(other,package)
    assert response.status_code==429 and int(response.headers['retry-after'])>0 and counts()==before and temp_children()==[]


@pytest.mark.parametrize('key',['id','ownerId','version'])
@pytest.mark.parametrize('call',[preview,apply],ids=['preview','apply'])
def test_package_cannot_forge_ids_or_owners(client,key,call):
    record,pupil,parent,teacher=setup(client);put_question(teacher,'seed');before=(counts(),stored_files())
    forged=json.dumps(manifest(questions=[essay()|{key:'forged'}])).encode()
    assert call(teacher,forged,'f.json').status_code==422
    assert (counts(),stored_files())==before and temp_children()==[]


def test_archive_limit_stops_streaming_with_413(client,monkeypatch,tmp_path):
    record,pupil,parent,teacher=setup(client);package=two_file_package(tmp_path)
    monkeypatch.setattr(pkg,'MAX_ARCHIVE',100)
    chunks=lambda:iter([package[:60],package[60:120],package[120:]])
    for response in (preview(teacher,package),apply(teacher,package),preview(teacher,chunks()),apply(teacher,chunks(),hash='0'*64)):
        assert response.status_code==413
    monkeypatch.setattr(pkg,'MAX_MANIFEST',100)
    assert preview(teacher,pkg.template_bytes(),'big.json').status_code==413 and temp_children()==[]


def test_bad_uploads_are_refused(client):
    record,pupil,parent,teacher=setup(client)
    assert preview(teacher,b'x','pack.rar').status_code==422 and apply(teacher,b'x','pack.exe').status_code==422
    assert preview(teacher,b'','pack.zip').status_code==422
    assert preview(teacher,b'not a zip','pack.zip').status_code==422
    assert preview(teacher,b'{broken','pack.json').status_code==422
    bom=b'\xef\xbb\xbf'+pkg.template_bytes();assert ok(preview(teacher,bom,'bom.json'))['questions']==TEMPLATE_QUESTIONS
    assert temp_children()==[]


def test_students_and_parents_cannot_exchange_packages(client):
    record,pupil,parent,teacher=setup(client);package=pkg.template_bytes()
    for reader in (pupil,parent):
        assert preview(reader,package,'t.json').status_code==403 and apply(reader,package,'t.json').status_code==403
        assert reader.post(EXPORT,json=dict(items=[dict(id='x',version=1)])).status_code==403
    assert temp_children()==[]


# ---- recovery, revoked accounts and transfer limits ----

def deactivate(login):
    with api.connect() as db:db.execute('UPDATE accounts SET active=0 WHERE login=?',(login,))


def uploading():
    with api.connect() as db:return db.execute("SELECT count(*) FROM quiz_files WHERE status='uploading'").fetchone()[0]


def strand(login,count,minutes=45):
    """What a crash leaves behind: reserved 'uploading' rows with their bytes."""
    created=(quiz_files.now()-timedelta(minutes=minutes)).isoformat();ids=[]
    with api.connect() as db:
        api.quizzes.tables(db);root=quiz_files.root_for(db);root.mkdir(parents=True,exist_ok=True)
        for _ in range(count):
            ids.append(seed_file(db,login,10,'uploading',created));homework_files.file_path(root,ids[-1]).write_bytes(b'stranded')
    return ids


def revoking(monkeypatch,owner,name,login):
    """Wraps owner.name so the account is deactivated right after the original returns."""
    original=getattr(owner,name)
    def wrapper(*args,**kwargs):
        result=original(*args,**kwargs);deactivate(login);return result
    monkeypatch.setattr(owner,name,wrapper)


def test_account_revoked_during_extraction_rolls_back_and_cleans_up(client,monkeypatch,tmp_path):
    pupil,teacher,source,*_=exported(client);package=two_file_package(tmp_path);before=(counts(),stored_files())
    revoking(monkeypatch,pkg,'extract_files','unrelated')
    assert apply(signed('unrelated'),package).status_code==409
    assert (counts(),stored_files())==before and uploading()==0 and temp_children()==[]


def test_account_revoked_before_the_reservation_writes_nothing(client,monkeypatch,tmp_path):
    pupil,teacher,source,*_=exported(client);package=two_file_package(tmp_path);before=(counts(),stored_files())
    revoking(monkeypatch,pkg,'inspect_package','unrelated');extracted=[]
    monkeypatch.setattr(pkg,'extract_files',lambda *args,**kwargs:extracted.append(1))
    assert apply(signed('unrelated'),package).status_code==409
    # The reservation step itself must refuse: nothing is reserved or extracted, not merely rolled back later.
    assert extracted==[] and (counts(),stored_files())==before and uploading()==0 and temp_children()==[]


def test_account_revoked_during_preview_is_409(client,monkeypatch,tmp_path):
    pupil,teacher,source,*_=exported(client);package=two_file_package(tmp_path)
    revoking(monkeypatch,pkg,'inspect_package','unrelated')
    assert preview(signed('unrelated'),package).status_code==409 and temp_children()==[]


def test_account_revoked_before_export_reads_nothing(client,monkeypatch):
    pupil,teacher,source,*_=exported(client)
    revoking(monkeypatch,api.quizzes.journal,'scope','teacher')
    assert export(teacher,source).status_code==409 and temp_children()==[]


def test_stranded_reservations_do_not_lock_their_owner_out(client,tmp_path):
    record,pupil,parent,teacher=setup(client);other=signed('unrelated');package=two_file_package(tmp_path)
    # collect_unused frees only 20 school-wide per call, and a 429 used to roll even that back.
    ids=strand('unrelated',25);assert uploading()==25
    result=ok(apply(other,package));assert result['files']==2 and uploading()==0
    with api.connect() as db:root=quiz_files.root_for(db)
    assert not any(homework_files.file_path(root,id).exists() for id in ids) and len(stored_files())==2
    ids=strand('unrelated',25);ok(upload(other,b'Question material','task.txt'))
    assert uploading()==0 and not any(homework_files.file_path(root,id).exists() for id in ids)


def test_fresh_reservations_are_not_collected_and_other_owners_are_untouched(client,tmp_path):
    record,pupil,parent,teacher=setup(client);other=signed('unrelated')
    strand('unrelated',2,minutes=5);before=counts()
    refused=apply(other,two_file_package(tmp_path))
    assert refused.status_code==429 and int(refused.headers['retry-after'])>0 and counts()==before and uploading()==2
    # A neighbour's abandoned rows go through the school-wide cleanup of every import; the fresh ones of the first owner stay and still block it.
    strand('teacher',3);ok(apply(client,two_file_package(tmp_path)))
    assert uploading()==2 and apply(other,two_file_package(tmp_path)).status_code==429


def test_release_removes_orphaned_bytes_but_never_a_committed_file(client):
    record,pupil,parent,teacher=setup(client);gone,pending=strand('unrelated',2);ready=strand('unrelated',1)[0]
    with api.connect() as db:
        db.execute("UPDATE quiz_files SET status='ready' WHERE id=?",(ready,))
        db.execute('DELETE FROM quiz_files WHERE id=?',(gone,));root=quiz_files.root_for(db)
        quiz_files.release_reserved(db,[gone,pending,ready,uuid4().hex])
        remaining={r[0] for r in db.execute('SELECT id FROM quiz_files')}
    assert remaining=={ready} and homework_files.file_path(root,ready).exists()
    assert not homework_files.file_path(root,gone).exists() and not homework_files.file_path(root,pending).exists()


def test_one_stuck_file_does_not_strand_the_other_reservations(client,monkeypatch):
    record,pupil,parent,teacher=setup(client);stuck,free=strand('unrelated',2,minutes=5);original=pathlib.Path.unlink
    def locked(self,*args,**kwargs):
        if self.name==stuck+'.bin':raise PermissionError('file is in use')
        return original(self,*args,**kwargs)
    monkeypatch.setattr(pathlib.Path,'unlink',locked)
    with api.connect() as db:
        root=quiz_files.root_for(db);quiz_files.release_reserved(db,[stuck,free])
        left={r[0] for r in db.execute('SELECT id FROM quiz_files')}
    assert left=={stuck} and homework_files.file_path(root,stuck).exists() and not homework_files.file_path(root,free).exists()


def test_reservations_deleted_during_extraction_abort_the_import_cleanly(client,monkeypatch,tmp_path):
    pupil,teacher,source,*_=exported(client);package=two_file_package(tmp_path);before=(counts(),stored_files());original=pkg.extract_files
    def vanishing(*args,**kwargs):
        original(*args,**kwargs)
        with api.connect() as db:db.execute("DELETE FROM quiz_files WHERE status='uploading'")
    monkeypatch.setattr(pkg,'extract_files',vanishing)
    assert apply(signed('unrelated'),package).status_code==409
    assert (counts(),stored_files())==before and temp_children()==[]


def test_release_waits_out_a_busy_database(client,monkeypatch,tmp_path):
    pupil,teacher,source,*_=exported(client);package=two_file_package(tmp_path);before=(counts(),stored_files())
    def broken(*args,**kwargs):raise HTTPException(422,'Сбой извлечения.')
    original=quiz_files.release_reserved;busy=[]
    def locked(*args):
        if len(busy)<2:
            busy.append(1);raise sqlite3.OperationalError('database is locked')
        return original(*args)
    monkeypatch.setattr(pkg,'extract_files',broken);monkeypatch.setattr(quiz_files,'release_reserved',locked)
    monkeypatch.setattr(exchange,'RELEASE_PAUSE',0)
    assert apply(signed('unrelated'),package).status_code==422
    assert (counts(),stored_files())==before and uploading()==0 and temp_children()==[]


def test_export_of_a_damaged_bank_document_is_409(client):
    record,pupil,parent,teacher=setup(client);put_question(teacher,'broken')
    with api.connect() as db:
        db.execute('UPDATE quiz_questions SET document=? WHERE id=?',(json.dumps(dict(tags=[],question=dict(kind='single',text='Без вариантов'))),'broken'))
    assert export(teacher,dict(id='broken',version=1)).status_code==409 and temp_children()==[]


@pytest.fixture
def busy_server(monkeypatch):
    """Call it once the test's own uploads are done: the only upload slot is then taken by somebody else, and a waiting request gives up at once."""
    def occupy():
        slots=threading.BoundedSemaphore(1);slots.acquire()
        monkeypatch.setattr(homework_files,'UPLOAD_SLOTS',slots);monkeypatch.setattr(homework_files,'UPLOAD_WAIT',0)
        return slots
    return occupy


def test_busy_server_answers_429_with_retry_after(client,busy_server):
    pupil,teacher,source,*_=exported(client);package=export(teacher,source).content
    other=signed('unrelated');before=(counts(),stored_files());busy_server()
    for response in (export(teacher,source),preview(other,package),apply(other,package)):
        assert response.status_code==429 and int(response.headers['retry-after'])>0
    assert (counts(),stored_files())==before and temp_children()==[]


def test_json_export_needs_no_upload_slot(client,busy_server):
    pupil,teacher,source,*_=exported(client);plain=put_question(teacher,'plain');busy_server()
    assert export(teacher,plain).status_code==200


def test_refused_requests_leave_the_busy_slot_taken_and_free_it_for_later_ones(client,busy_server):
    pupil,teacher,source,*_=exported(client);package=export(teacher,source).content;other=signed('unrelated');slots=busy_server()
    assert preview(other,package).status_code==429 and apply(other,package).status_code==429
    assert export(teacher,source).status_code==429  # the refusals did not release the slot they never took
    slots.release()
    assert export(teacher,source).status_code==200 and preview(other,package).status_code==200
    assert apply(other,package).status_code==200  # and the slot is back after every request


def test_a_pupil_does_not_queue_for_a_slot(client,monkeypatch):
    record,pupil,parent,teacher=setup(client)
    slots=threading.BoundedSemaphore(1);slots.acquire();monkeypatch.setattr(homework_files,'UPLOAD_SLOTS',slots)
    monkeypatch.setattr(homework_files,'UPLOAD_WAIT',0.05)  # queueing would end in 429, not 403
    assert preview(pupil,pkg.template_bytes(),'t.json').status_code==403 and apply(pupil,pkg.template_bytes(),'t.json').status_code==403


@pytest.mark.parametrize('error,status',[(TimeoutError,408),(ClientDisconnect,400)])
def test_interrupted_uploads_are_refused_and_leave_nothing(client,monkeypatch,error,status):
    record,pupil,parent,teacher=setup(client);put_question(teacher,'seed');before=(counts(),stored_files())
    monkeypatch.setattr(homework_files,'UPLOAD_SLOTS',threading.BoundedSemaphore(1));monkeypatch.setattr(homework_files,'UPLOAD_WAIT',0)
    async def interrupted(*args,**kwargs):raise error()
    with monkeypatch.context() as patch:
        patch.setattr(exchange,'receive',interrupted)
        for call in (preview,apply):
            assert call(teacher,pkg.template_bytes(),'t.json').status_code==status
    assert (counts(),stored_files())==before and temp_children()==[]
    ok(preview(teacher,pkg.template_bytes(),'t.json'))  # the only slot came back, otherwise 429


def test_old_temp_folders_are_swept_and_fresh_ones_kept(client):
    record,pupil,parent,teacher=setup(client)
    with api.connect() as db:base=quiz_files.root_for(db).parent/'quiz-bank-tmp'
    base.mkdir(parents=True);stale,fresh=base/'stale',base/'fresh';stale.mkdir();fresh.mkdir();(stale/'package').write_bytes(b'abandoned')
    old=time.time()-7200;os.utime(stale,(old,old))
    ok(preview(teacher,pkg.template_bytes(),'t.json'))
    assert [p.name for p in temp_children()]==['fresh']
