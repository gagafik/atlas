from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3

import pytest
from tools import backup_school


def fixture_database(tmp_path, *, attachment=True):
    source = tmp_path / 'live' / 'school.db'
    source.parent.mkdir()
    content = b'A submitted answer with a private attachment'
    file_id = 'a' * 32
    with closing(sqlite3.connect(source)) as db:
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('CREATE TABLE workspaces(id TEXT, revision INTEGER, document TEXT)')
        db.execute('INSERT INTO workspaces VALUES (?,?,?)', ('school', 11, '{"private":"data"}'))
        if attachment:
            db.execute('CREATE TABLE homework_files(id TEXT,size INTEGER,sha256 TEXT,status TEXT)')
            db.execute('INSERT INTO homework_files VALUES (?,?,?,?)', (file_id,len(content),hashlib.sha256(content).hexdigest(),'ready'))
            db.execute('INSERT INTO homework_files VALUES (?,?,?,?)', ('b'*32,10*1024*1024,None,'uploading'))
        db.commit()
    path = source.with_suffix('.files') / 'homework' / (file_id + '.bin')
    if attachment:
        path.parent.mkdir(parents=True)
        path.write_bytes(content)
    return source, path, content


def test_backup_restores_database_and_files_and_blocks_writes_during_copy(tmp_path, monkeypatch):
    source, attachment, content = fixture_database(tmp_path)
    message_content=b'A separate conversation attachment'
    message_path=source.with_suffix('.files')/'messages'/attachment.name
    message_path.parent.mkdir();message_path.write_bytes(message_content)
    with closing(sqlite3.connect(source)) as db:
        db.execute('CREATE TABLE message_files(id TEXT,size INTEGER,sha256 TEXT,status TEXT)')
        db.execute('INSERT INTO message_files VALUES (?,?,?,?)',('a'*32,len(message_content),hashlib.sha256(message_content).hexdigest(),'ready'))
        db.execute('INSERT INTO message_files VALUES (?,?,?,?)',('c'*32,10*1024*1024,None,'uploading'))
        db.commit()
    original_copy = backup_school.shutil.copyfile
    def checked_copy(src, dst):
        with closing(sqlite3.connect(source, timeout=0)) as writer:
            with pytest.raises(sqlite3.OperationalError, match='locked'):
                writer.execute('UPDATE workspaces SET revision=12')
        return original_copy(src, dst)
    monkeypatch.setattr(backup_school.shutil, 'copyfile', checked_copy)
    target = tmp_path / 'snapshot'
    manifest = backup_school.create_backup(source, target)
    assert json.loads((target/'manifest.json').read_text()) == manifest
    assert manifest['incompleteUploadsExcluded'] == 2
    assert len(manifest['files']) == 2
    assert {(target/f['path']).read_bytes() for f in manifest['files']} == {content,message_content}
    restored = tmp_path/'restored'
    backup_school.shutil.copytree(target, restored)
    with closing(sqlite3.connect(restored/'school.db')) as db:
        assert db.execute('SELECT revision FROM workspaces').fetchone()[0] == 11
        assert db.execute('SELECT count(*) FROM homework_files').fetchone()[0] == 1
        assert db.execute('SELECT count(*) FROM message_files').fetchone()[0] == 1
    assert (restored/'school.files/homework'/attachment.name).read_bytes() == content
    assert (restored/'school.files/messages'/attachment.name).read_bytes() == message_content
    with closing(sqlite3.connect(source, timeout=0)) as db:
        # The source retains an in-flight upload and accepts writes again.
        assert db.execute('SELECT count(*) FROM homework_files').fetchone()[0] == 2
        assert db.execute('SELECT count(*) FROM message_files').fetchone()[0] == 2
        db.execute('UPDATE workspaces SET revision=12')
        db.commit()


def test_missing_or_corrupt_attachment_never_marks_backup_complete(tmp_path):
    source, attachment, _ = fixture_database(tmp_path)
    attachment.write_bytes(b'corrupted')
    with pytest.raises(RuntimeError, match='verification failed'):
        backup_school.create_backup(source, tmp_path/'incomplete')
    assert not (tmp_path/'incomplete/manifest.json').exists()
    attachment.unlink()
    with pytest.raises(FileNotFoundError):
        backup_school.create_backup(source, tmp_path/'missing')
    assert not (tmp_path/'missing/manifest.json').exists()
    with closing(sqlite3.connect(source)) as db:
        assert db.execute('SELECT count(*) FROM homework_files').fetchone()[0] == 2


def test_backup_older_database_and_refuses_overwrite_or_nonexistent_source(tmp_path):
    source, _, _ = fixture_database(tmp_path, attachment=False)
    target = tmp_path/'snapshot'
    manifest = backup_school.create_backup(source, target)
    assert manifest['files'] == []
    before = (target/'manifest.json').read_bytes()
    with pytest.raises(FileExistsError):
        backup_school.create_backup(source, target)
    assert (target/'manifest.json').read_bytes() == before
    with pytest.raises(FileNotFoundError):
        backup_school.create_backup(tmp_path/'missing.db', tmp_path/'other')
    assert not (tmp_path/'missing.db').exists()
