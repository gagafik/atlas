from contextlib import closing
import json
from pathlib import Path
import sqlite3
import shutil
import subprocess
import sys

import pytest

from tools import backup_school, restore_school
from test_backup_school import fixture_database


def snapshot(tmp_path):
    source,attachment,content=fixture_database(tmp_path)
    directory=tmp_path/'backup'
    backup_school.create_backup(source,directory)
    return source,directory,content


def rewrite(directory,change):
    path=directory/'manifest.json';manifest=json.loads(path.read_text(encoding='utf-8'))
    change(manifest)
    path.write_text(json.dumps(manifest),encoding='utf-8')


STORAGES=[('homework_files','homework'),('message_files','messages'),('absence_files','absences'),('assignment_files','assignments'),
          ('teaching_material_files','materials'),('lost_found_files','lost-found'),('school_site_files','school-site'),('admission_files','admissions'),('quiz_files','quizzes')]


def all_storages_backup(tmp_path):
    source,attachment,content=fixture_database(tmp_path)
    with closing(sqlite3.connect(source)) as db:
        for index,(table,folder) in enumerate(STORAGES[1:],1):
            id=f'{index:032x}';data=f'Private synthetic {folder}'.encode()
            original=source.with_suffix('.files')/folder/(id+'.bin');original.parent.mkdir(parents=True);original.write_bytes(data)
            db.execute(f'CREATE TABLE {table}(id TEXT,size INTEGER,sha256 TEXT,status TEXT)')
            db.execute(f'INSERT INTO {table} VALUES (?,?,?,?)',(id,len(data),backup_school.digest(original),'ready'))
        db.commit()
    directory=tmp_path/'backup';backup_school.create_backup(source,directory)
    return source,directory


def test_every_storage_is_verified_and_restored_and_the_original_is_preserved(tmp_path):
    source,directory=all_storages_backup(tmp_path)
    result=restore_school.verify_backup(directory)
    assert result['files']==len(STORAGES) and result['workspaces']==1
    assert {entry['path'].split('/')[1] for entry in result['manifest']['files']}=={folder for _,folder in STORAGES}
    destination=tmp_path/'restored'
    assert restore_school.restore_backup(directory,destination)==result
    with closing(sqlite3.connect(destination/'school.db')) as db:
        assert db.execute('SELECT revision FROM workspaces').fetchone()[0]==11
    for _,folder in STORAGES:assert list((destination/'school.files'/folder).glob('*.bin'))
    with closing(sqlite3.connect(source)) as db:
        assert db.execute('SELECT count(*) FROM homework_files').fetchone()[0]==2
    assert restore_school.verify_backup(destination)==result


@pytest.mark.parametrize('inside',['source','backup','restored'])
def test_restore_never_replaces_or_nests_into_existing_data(tmp_path,inside):
    source,directory=all_storages_backup(tmp_path)
    destination=tmp_path/'restored';restore_school.restore_backup(directory,destination)
    with pytest.raises((ValueError,FileExistsError)):
        restore_school.restore_backup(directory,dict(source=source.parent,backup=directory,restored=destination)[inside])


@pytest.mark.parametrize('path',['../outside.db','/outside.db','C:/outside.db','nested/db.db','..\\outside.db','.','manifest.json'])
def test_untrusted_manifest_paths_never_escape_backup(tmp_path,path):
    _,directory,_=snapshot(tmp_path)
    rewrite(directory,lambda manifest:manifest['database'].update(path=path))
    with pytest.raises(ValueError):restore_school.restore_backup(directory,tmp_path/'restored')
    assert not (tmp_path/'restored').exists()


@pytest.mark.parametrize('change',['corrupt_file','corrupt_database','extra_file','missing_manifest','missing_file','unlisted_reference','new_reference','duplicate','incomplete_upload'])
def test_integrity_and_reference_failures_are_not_reported_as_valid(tmp_path,change):
    _,directory,_=snapshot(tmp_path)
    data=json.loads((directory/'manifest.json').read_text())
    file=directory/data['files'][0]['path']
    if change=='corrupt_file':file.write_bytes(b'bad')
    if change=='corrupt_database':(directory/'school.db').write_bytes(b'bad')
    if change=='extra_file':(directory/'secret.txt').write_text('unlisted')
    if change=='missing_manifest':(directory/'manifest.json').unlink()
    if change=='missing_file':file.unlink()
    if change=='unlisted_reference':
        file.unlink();rewrite(directory,lambda m:m.update(files=[]))
    if change in ['new_reference','incomplete_upload']:
        with closing(sqlite3.connect(directory/'school.db')) as db:
            db.execute('INSERT INTO homework_files VALUES (?,?,?,?)',('c'*32,1,'b'*64,'ready' if change=='new_reference' else 'uploading'));db.commit()
        rewrite(directory,lambda m:m['database'].update(size=(directory/'school.db').stat().st_size,sha256=backup_school.digest(directory/'school.db')))
    if change=='duplicate':rewrite(directory,lambda m:m['files'].append(m['files'][0]))
    with pytest.raises((ValueError,sqlite3.Error)):restore_school.verify_backup(directory)


def test_duplicate_json_keys_are_rejected(tmp_path):
    _,directory,_=snapshot(tmp_path)
    marker=directory/'manifest.json';text=marker.read_text()
    marker.write_text(text.replace('"files":','"files": [], "files":',1))
    with pytest.raises(ValueError,match='Duplicate manifest'):restore_school.verify_backup(directory)


@pytest.mark.parametrize('kind',['backup_root','storage_root','storage_folder','file'])
def test_links_and_junctions_cannot_hide_outside_data(tmp_path,kind):
    source,directory,_=snapshot(tmp_path)
    if kind=='backup_root':
        link=tmp_path/'linked';target=directory
    elif kind=='storage_root':
        link=source.with_suffix('.files');target=tmp_path/'outside-files';link.rename(target)
    elif kind=='storage_folder':
        link=source.with_suffix('.files')/'homework';target=tmp_path/'outside-folder';link.rename(target)
    else:
        link=source.with_suffix('.files')/'homework'/('a'*32+'.bin');target=tmp_path/'outside.bin';link.rename(target)
    try:link.symlink_to(target,target_is_directory=kind!='file')
    except OSError:pytest.skip('Creating symlinks is unavailable for this Windows account; Linux CI covers it.')
    with pytest.raises(ValueError,match='links'):
        if kind=='backup_root':restore_school.verify_backup(link)
        else:backup_school.create_backup(source,tmp_path/'linked-snapshot')
    if kind!='backup_root':assert not (tmp_path/'linked-snapshot'/'manifest.json').exists()


class FailingCopies:
    """Stand-in for restore_school.shutil: whichever way an attachment is copied, the disk fails."""
    def __getattr__(self,name):
        real=getattr(shutil,name)
        if not name.startswith('copy') or name=='copyfileobj':return real
        def copy(source,target,*args,**kwargs):
            if str(source).endswith('.bin'):raise OSError('Simulated disk failure')
            return real(source,target,*args,**kwargs)
        return copy


def test_failed_restore_keeps_source_and_does_not_mark_destination_complete(tmp_path,monkeypatch):
    _,directory,_=snapshot(tmp_path)
    original=backup_school.digest(directory/'school.db')
    monkeypatch.setattr(restore_school,'shutil',FailingCopies())
    with pytest.raises(OSError):restore_school.restore_backup(directory,tmp_path/'partial')
    assert not (tmp_path/'partial'/'manifest.json').exists()
    assert backup_school.digest(directory/'school.db')==original
    assert restore_school.verify_backup(directory)['files']==1


def test_restore_cli_checks_new_copy_without_printing_private_content(tmp_path):
    _,directory,content=snapshot(tmp_path)
    result=subprocess.run([sys.executable,'-m','tools.restore_school',str(directory),'--output',str(tmp_path/'restored')],capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stderr
    assert json.loads(result.stdout)['restored'] is True
    assert content.decode() not in result.stdout and 'private' not in result.stdout


def test_json_expression_index_survives_protected_verification_and_restore(tmp_path):
    source,_,_=fixture_database(tmp_path)
    with closing(sqlite3.connect(source)) as db:
        db.execute("CREATE INDEX school_json_day ON workspaces(json_extract(document,'$.private'))")
        db.commit()
    backup_school.create_backup(source,tmp_path/'json-snapshot')
    assert restore_school.verify_backup(tmp_path/'json-snapshot')['workspaces']==1
    assert restore_school.restore_backup(tmp_path/'json-snapshot',tmp_path/'json-restore')['files']==1


def test_old_sqlite_reports_runtime_requirement_without_touching_backup(tmp_path,monkeypatch):
    _,directory,_=snapshot(tmp_path)
    before=(directory/'manifest.json').read_bytes()
    monkeypatch.setattr(sqlite3,'sqlite_version_info',(3,40,1))
    with pytest.raises(ValueError):restore_school.verify_backup(directory)
    assert (directory/'manifest.json').read_bytes()==before


def test_shared_blob_rows_are_backed_up_once(tmp_path):
    from test_backup_school import shared_blob_database
    source,path,data=shared_blob_database(tmp_path)
    directory=tmp_path/'backup';manifest=backup_school.create_backup(source,directory)
    assert [f['path'] for f in manifest['files']]==['school.files/assignments/'+path.name]
    assert restore_school.verify_backup(directory)['files']==1
    destination=tmp_path/'restored'
    assert restore_school.restore_backup(directory,destination)['files']==1
    assert (destination/'school.files/assignments'/path.name).read_bytes()==data
    with closing(sqlite3.connect(destination/'school.db')) as db:
        assert db.execute('SELECT count(*) FROM assignment_files').fetchone()[0]==2


def test_rows_sharing_a_file_must_agree_about_its_bytes(tmp_path):
    from test_backup_school import shared_blob_database
    source,_,_=shared_blob_database(tmp_path)
    directory=tmp_path/'backup';backup_school.create_backup(source,directory)
    with closing(sqlite3.connect(directory/'school.db')) as db:
        db.execute("UPDATE assignment_files SET sha256=? WHERE id=?",('0'*64,'f'*32));db.commit()
    rewrite(directory,lambda m:m['database'].update(size=(directory/'school.db').stat().st_size,sha256=backup_school.digest(directory/'school.db')))
    with pytest.raises(ValueError):restore_school.verify_backup(directory)
