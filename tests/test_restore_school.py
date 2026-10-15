from contextlib import closing
import json
from pathlib import Path
import sqlite3
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


def test_verify_and_restore_all_storages_preserves_original_and_refuses_replacement(tmp_path):
    source,attachment,content=fixture_database(tmp_path)
    with closing(sqlite3.connect(source)) as db:
        for index,(table,folder) in enumerate(backup_school.STORES[1:],1):
            id=f'{index:032x}';data=f'Private synthetic {folder}'.encode()
            original=source.with_suffix('.files')/folder/(id+'.bin');original.parent.mkdir(parents=True);original.write_bytes(data)
            db.execute(f'CREATE TABLE {table}(id TEXT,size INTEGER,sha256 TEXT,status TEXT)')
            db.execute(f'INSERT INTO {table} VALUES (?,?,?,?)',(id,len(data),backup_school.digest(original),'ready'))
        db.commit()
    directory=tmp_path/'backup';backup_school.create_backup(source,directory)
    result=restore_school.verify_backup(directory)
    assert result['files']==5 and result['workspaces']==1
    destination=tmp_path/'restored'
    assert restore_school.restore_backup(directory,destination)==result
    with closing(sqlite3.connect(destination/'school.db')) as db:
        assert db.execute('SELECT revision FROM workspaces').fetchone()[0]==11
        assert db.execute('SELECT count(*) FROM homework_files').fetchone()[0]==1
    with closing(sqlite3.connect(source)) as db:
        assert db.execute('SELECT count(*) FROM homework_files').fetchone()[0]==2
    for target in [source.parent,directory,destination]:
        with pytest.raises((ValueError,FileExistsError)):
            restore_school.restore_backup(directory,target)
    assert restore_school.verify_backup(destination)==result


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


def test_failed_restore_keeps_source_and_does_not_mark_destination_complete(tmp_path,monkeypatch):
    _,directory,_=snapshot(tmp_path)
    original=backup_school.digest(directory/'school.db')
    copy=restore_school.shutil.copyfile
    def fail(source,target):
        if str(source).endswith('.bin'):raise OSError('Simulated disk failure')
        return copy(source,target)
    monkeypatch.setattr(restore_school.shutil,'copyfile',fail)
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
    with pytest.raises(ValueError,match='SQLite 3.42'):restore_school.verify_backup(directory)
    assert (directory/'manifest.json').read_bytes()==before
