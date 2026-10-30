"""Per-module storage budgets, disk reserve, upload slots with a bounded queue, and Retry-After."""
from collections import namedtuple
import sqlite3
import threading
import time
from uuid import uuid4

import pytest
from fastapi import HTTPException
from backend import api, homework_files
from backend.access import now
from test_access import client
from test_homework import setup, get, upload
from tools import backup_school


def attempt(client, record, **extra):
    key = record['lessonKey']
    return upload(client, key, get(client, key)['assignment'], **extra)


def test_registry_matches_backup_stores():
    assert {(table, folder) for table, folder, *_ in homework_files.STORES.values()} == set(backup_school.STORES)


def test_total_limit_backward_compatible(monkeypatch):
    mib = 1024 * 1024
    monkeypatch.delenv('ATLAS_UPLOAD_TOTAL_MB', raising=False)
    assert homework_files.total_limit() == 65536 * mib
    for value, expected in [('5', 10), ('2048', 2048), ('99999999', 1048576)]:
        monkeypatch.setenv('ATLAS_UPLOAD_TOTAL_MB', value)
        assert homework_files.total_limit() == expected * mib, value
    homework = homework_files.module_limit('homework')
    monkeypatch.setenv('ATLAS_UPLOAD_MESSAGES_MB', '100')
    assert homework_files.module_limit('messages') == 100 * mib
    assert homework_files.module_limit('homework') == homework
    monkeypatch.setenv('ATLAS_UPLOAD_RESERVE_MB', '0')
    assert homework_files.reserve_bytes() == 0


def test_slot_settings_clamp(monkeypatch):
    monkeypatch.setenv('ATLAS_UPLOAD_SLOTS', '0')
    assert homework_files.setting('ATLAS_UPLOAD_SLOTS', 16, 1, 64) == 1
    monkeypatch.setenv('ATLAS_UPLOAD_SLOTS', '1000')
    assert homework_files.setting('ATLAS_UPLOAD_SLOTS', 16, 1, 64) == 64
    monkeypatch.setenv('ATLAS_UPLOAD_SLOTS', 'x')
    assert homework_files.setting('ATLAS_UPLOAD_SLOTS', 16, 1, 64) == 16
    monkeypatch.setenv('ATLAS_UPLOAD_SLOTS', '')
    assert homework_files.setting('ATLAS_UPLOAD_SLOTS', 16, 1, 64) == 16


def test_stored_counts_distinct_blobs_and_never_creates_tables():
    db = sqlite3.connect(':memory:')
    assert homework_files.stored(db, 'quiz_files') == 0
    assert not db.execute("SELECT 1 FROM sqlite_master WHERE name='quiz_files'").fetchone()
    db.execute('CREATE TABLE assignment_files (id TEXT PRIMARY KEY,size INTEGER,blob_id TEXT)')
    db.executemany('INSERT INTO assignment_files VALUES (?,?,?)', [('a', 10, 'a'), ('b', 10, 'a'), ('c', 10, None), ('d', 5, 'd')])
    assert homework_files.stored(db, 'assignment_files') == 25
    assert homework_files.total_stored(db) == 25
    db.execute('CREATE TABLE quiz_files (id TEXT PRIMARY KEY,size INTEGER)')
    db.executemany('INSERT INTO quiz_files VALUES (?,?)', [('x', 3), ('y', 4)])
    assert homework_files.stored(db, 'quiz_files') == 7 and homework_files.total_stored(db) == 32


def test_module_budget_blocks_only_its_own_store(client, monkeypatch):
    record, pupil, *_ = setup(client)
    monkeypatch.setenv('ATLAS_UPLOAD_HOMEWORK_MB', '10')
    assert attempt(pupil, record).status_code == 200  # 0 + 10 MiB fits the 10 MiB budget exactly
    refused = attempt(pupil, record)
    assert refused.status_code == 413 and '«Домашние работы»' in refused.json()['detail'], refused.text
    with api.connect() as db:
        homework_files.require_capacity(db, 'messages')  # another section is unaffected
        homework_files.require_capacity(db, 'assignments')
        assert db.execute("SELECT count(*) FROM homework_files WHERE status='uploading'").fetchone()[0] == 0
        assert db.execute('SELECT count(*) FROM homework_files').fetchone()[0] == 1


def test_overall_cap_still_limits_every_store(client, monkeypatch):
    record, pupil, *_ = setup(client)
    assert attempt(pupil, record).status_code == 200
    monkeypatch.setenv('ATLAS_UPLOAD_TOTAL_MB', '10')
    refused = attempt(pupil, record)
    assert refused.status_code == 413 and 'Лимит хранилища' in refused.json()['detail']
    with api.connect() as db:
        for module in homework_files.STORES:
            with pytest.raises(HTTPException) as exc:
                homework_files.require_capacity(db, module)
            assert exc.value.status_code == 413


def test_disk_reserve_returns_507_and_leaves_nothing(client, monkeypatch):
    record, pupil, *_ = setup(client)
    usage = namedtuple('usage', 'total used free')
    monkeypatch.setattr(homework_files.shutil, 'disk_usage', lambda path: usage(10**12, 0, homework_files.MAX_FILE))
    monkeypatch.setenv('ATLAS_UPLOAD_RESERVE_MB', '1')
    refused = attempt(pupil, record)
    assert refused.status_code == 507 and 'заканчивается место' in refused.json()['detail'], refused.text
    with api.connect() as db:
        assert db.execute('SELECT count(*) FROM homework_files').fetchone()[0] == 0
        assert not list(homework_files.root_for(db).glob('*.bin'))
    monkeypatch.setenv('ATLAS_UPLOAD_RESERVE_MB', '0')  # 0 disables the disk check
    assert attempt(pupil, record).status_code == 200


def test_disk_probe_failure_does_not_block_uploads(client, monkeypatch):
    record, pupil, *_ = setup(client)
    def broken(path): raise OSError('probe failed')
    monkeypatch.setattr(homework_files.shutil, 'disk_usage', broken)
    assert attempt(pupil, record).status_code == 200


def test_busy_server_queues_then_answers_429_with_retry_after(client, monkeypatch):
    record, pupil, *_ = setup(client)
    slots = threading.BoundedSemaphore(1)
    assert slots.acquire(blocking=False)
    monkeypatch.setattr(homework_files, 'UPLOAD_SLOTS', slots)
    monkeypatch.setattr(homework_files, 'UPLOAD_WAIT', 0.3)
    started = time.perf_counter()
    refused = attempt(pupil, record)
    assert refused.status_code == 429 and refused.headers['retry-after'] == '5'
    assert time.perf_counter() - started >= 0.25  # it queued before giving up
    with api.connect() as db:
        assert db.execute('SELECT count(*) FROM homework_files').fetchone()[0] == 0
    # A slot freed while the request waits is taken by it.
    monkeypatch.setattr(homework_files, 'UPLOAD_WAIT', 5)
    threading.Timer(0.2, slots.release).start()
    assert attempt(pupil, record).status_code == 200
    # A full queue answers at once.
    assert slots.acquire(blocking=False)
    monkeypatch.setattr(homework_files, 'UPLOAD_WAITERS', threading.Semaphore(0))
    started = time.perf_counter()
    refused = attempt(pupil, record)
    assert refused.status_code == 429 and refused.headers['retry-after'] == '5' and time.perf_counter() - started < 2
    slots.release()
    assert slots.acquire(blocking=False)  # nothing leaked
    slots.release()


def test_pending_uploads_answer_retry_after(client):
    record, pupil, *_ = setup(client)
    key = record['lessonKey']
    ids = [uuid4().hex for _ in range(2)]
    with api.connect() as db:
        homework_files.tables(db)
        db.executemany('INSERT INTO homework_files VALUES (?,?,?,?,?,?,?,?,?,NULL)',
                       [(id, 'demo', key, 's1', 'someone', 'a.txt', homework_files.MAX_FILE, now().isoformat(), 'uploading') for id in ids])
    refused = attempt(pupil, record)
    assert refused.status_code == 429 and refused.headers['retry-after'] == '5', refused.text
    with api.connect() as db:
        db.execute("DELETE FROM homework_files WHERE status='uploading'")
    assert attempt(pupil, record).status_code == 200
