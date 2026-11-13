from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3

import pytest
from tools import backup_school, restore_school


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


def two_stores(tmp_path):
    source, attachment, content = fixture_database(tmp_path)
    message_content=b'A separate conversation attachment'
    message_path=source.with_suffix('.files')/'messages'/attachment.name
    message_path.parent.mkdir();message_path.write_bytes(message_content)
    with closing(sqlite3.connect(source)) as db:
        db.execute('CREATE TABLE message_files(id TEXT,size INTEGER,sha256 TEXT,status TEXT)')
        db.execute('INSERT INTO message_files VALUES (?,?,?,?)',('a'*32,len(message_content),hashlib.sha256(message_content).hexdigest(),'ready'))
        db.execute('INSERT INTO message_files VALUES (?,?,?,?)',('c'*32,10*1024*1024,None,'uploading'))
        db.commit()
    return source, attachment, content, message_content


def test_backup_of_two_stores_restores_database_and_files_without_unfinished_uploads(tmp_path):
    source, attachment, content, message_content = two_stores(tmp_path)
    target = tmp_path / 'snapshot'
    manifest = backup_school.create_backup(source, target)
    assert json.loads((target/'manifest.json').read_text()) == manifest
    assert manifest['incompleteUploadsExcluded'] == 2
    assert {(target/f['path']).read_bytes() for f in manifest['files']} == {content,message_content}
    restored = tmp_path/'restored'
    restore_school.restore_backup(target, restored)
    with closing(sqlite3.connect(restored/'school.db')) as db:
        assert db.execute('SELECT revision FROM workspaces').fetchone()[0] == 11
        assert [row[0] for row in db.execute('SELECT status FROM homework_files')] == ['ready']
        assert [row[0] for row in db.execute('SELECT status FROM message_files')] == ['ready']
    assert (restored/'school.files/homework'/attachment.name).read_bytes() == content
    assert (restored/'school.files/messages'/attachment.name).read_bytes() == message_content


def test_backup_leaves_the_source_with_its_unfinished_uploads_and_writable(tmp_path):
    source, *_ = two_stores(tmp_path)
    backup_school.create_backup(source, tmp_path / 'snapshot')
    with closing(sqlite3.connect(source, timeout=0)) as db:
        assert db.execute("SELECT count(*) FROM homework_files WHERE status='uploading'").fetchone()[0] == 1
        assert db.execute("SELECT count(*) FROM message_files WHERE status='uploading'").fetchone()[0] == 1
        db.execute('UPDATE workspaces SET revision=12')
        db.commit()


def test_source_cannot_be_written_while_the_backup_is_made(tmp_path, monkeypatch):
    source, *_ = fixture_database(tmp_path)
    original = backup_school.digest
    attempts = []
    def observe(path):
        # The first checksum is taken while the backup still holds its writer reservation; the last one is not.
        with closing(sqlite3.connect(source, timeout=0)) as writer:
            try:
                writer.execute('UPDATE workspaces SET revision=12')
                attempts.append('written')
            except sqlite3.OperationalError as error:
                attempts.append('locked' if 'locked' in str(error) else str(error))
            writer.rollback()
        return original(path)
    monkeypatch.setattr(backup_school, 'digest', observe)
    backup_school.create_backup(source, tmp_path / 'snapshot')
    assert attempts and attempts[0] == 'locked' and attempts[-1] == 'written'


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


def shared_blob_database(tmp_path, second_hash=None):
    # Two lesson rows share one deduplicated file whose own first row no longer exists.
    source, _, _ = fixture_database(tmp_path, attachment=False)
    blob, data = 'd' * 32, b'Shared lesson worksheet'
    path = source.with_suffix('.files') / 'assignments' / (blob + '.bin')
    path.parent.mkdir(parents=True)
    path.write_bytes(data)
    sha = hashlib.sha256(data).hexdigest()
    with closing(sqlite3.connect(source)) as db:
        db.execute('CREATE TABLE assignment_files(id TEXT,size INTEGER,sha256 TEXT,status TEXT,blob_id TEXT)')
        db.execute('INSERT INTO assignment_files VALUES (?,?,?,?,?)', ('e' * 32, len(data), sha, 'ready', blob))
        db.execute('INSERT INTO assignment_files VALUES (?,?,?,?,?)', ('f' * 32, len(data), second_hash or sha, 'ready', blob))
        db.commit()
    return source, path, data


def test_inconsistent_shared_blob_never_marks_backup_complete(tmp_path):
    source, _, _ = shared_blob_database(tmp_path, second_hash='0' * 64)
    with pytest.raises(RuntimeError, match='Invalid attachment metadata'):
        backup_school.create_backup(source, tmp_path / 'snapshot')
    assert not (tmp_path / 'snapshot' / 'manifest.json').exists()


def test_backup_restores_every_table_with_its_rows(tmp_path):
    source, _, _ = fixture_database(tmp_path, attachment=False)
    with closing(sqlite3.connect(source)) as db:
        db.execute('CREATE TABLE absence_batches(id TEXT, status TEXT)')
        db.executemany('INSERT INTO absence_batches VALUES (?,?)', [('a', 'preview'), ('b', 'applied'), ('c', 'applied')])
        db.execute('CREATE TABLE empty_log(id TEXT)')
        db.commit()
    def snapshot(path):
        with closing(sqlite3.connect(path)) as db:
            names = [row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
            return {name: db.execute(f'SELECT * FROM "{name}" ORDER BY 1').fetchall() for name in names}
    target = tmp_path / 'snapshot'
    backup_school.create_backup(source, target)
    assert snapshot(target / 'school.db') == snapshot(source)
