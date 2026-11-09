"""Per-module storage budgets, disk reserve, upload slots with a bounded queue, and Retry-After."""
from collections import namedtuple
import sqlite3
from types import SimpleNamespace
import threading
from uuid import uuid4

from backend import api, homework_files
from backend.access import now
from seeding import insert_row
from test_access import client
from test_assignment_files import upload as upload_material
from test_homework import setup, get, upload
from tools import backup_school


def attempt(client, record, **extra):
    key = record['lessonKey']
    return upload(client, key, get(client, key)['assignment'], **extra)


def test_registry_matches_backup_stores():
    assert {(table, folder) for table, folder, *_ in homework_files.STORES.values()} == set(backup_school.STORES)


def test_tiny_total_limit_still_allows_one_file(client, monkeypatch):
    record, pupil, *_ = setup(client)
    # A limit below one file would block every upload, so a configured 1 MiB must still let the first file in.
    monkeypatch.setenv('ATLAS_UPLOAD_TOTAL_MB', '1')
    monkeypatch.setenv('ATLAS_UPLOAD_HOMEWORK_MB', '1')
    assert attempt(pupil, record).status_code == 200


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
    monkeypatch.setenv('ATLAS_UPLOAD_HOMEWORK_MB', str(homework_files.MAX_FILE // (1024 * 1024)))
    assert attempt(pupil, record).status_code == 200  # 0 + one file fits the budget exactly
    assert attempt(pupil, record).status_code == 413
    assert len(get(pupil, record['lessonKey'])['availableFiles']) == 1
    assert upload_material(client, record).status_code == 200  # another section is unaffected


def test_overall_cap_still_limits_every_store(client, monkeypatch):
    record, pupil, *_ = setup(client)
    assert attempt(pupil, record).status_code == 200
    monkeypatch.setenv('ATLAS_UPLOAD_TOTAL_MB', '10')
    assert attempt(pupil, record).status_code == 413
    assert upload_material(client, record).status_code == 413


def test_disk_reserve_returns_507_and_leaves_nothing(client, monkeypatch):
    record, pupil, *_ = setup(client)
    usage = namedtuple('usage', 'total used free')
    monkeypatch.setattr(homework_files.shutil, 'disk_usage', lambda path: usage(10**12, 0, homework_files.MAX_FILE))
    monkeypatch.setenv('ATLAS_UPLOAD_RESERVE_MB', '1')
    assert attempt(pupil, record).status_code == 507
    assert get(pupil, record['lessonKey'])['availableFiles'] == []
    with api.connect() as db: assert not list(homework_files.root_for(db).glob('*.bin'))
    monkeypatch.setenv('ATLAS_UPLOAD_RESERVE_MB', '0')  # 0 disables the disk check
    assert attempt(pupil, record).status_code == 200


def test_disk_probe_failure_does_not_block_uploads(client, monkeypatch):
    record, pupil, *_ = setup(client)
    def broken(path): raise OSError('probe failed')
    monkeypatch.setattr(homework_files.shutil, 'disk_usage', broken)
    assert attempt(pupil, record).status_code == 200


def occupied_slot(monkeypatch):
    slots = threading.BoundedSemaphore(1)
    assert slots.acquire(blocking=False)
    monkeypatch.setattr(homework_files, 'UPLOAD_SLOTS', slots)
    return slots


def frees_slot_on_first_poll(monkeypatch, slots):
    async def sleep(delay): slots.release()
    monkeypatch.setattr(homework_files, 'asyncio', SimpleNamespace(sleep=sleep))


def test_busy_server_answers_429_with_retry_after(client, monkeypatch):
    record, pupil, *_ = setup(client)
    occupied_slot(monkeypatch)
    monkeypatch.setattr(homework_files, 'UPLOAD_WAIT', 0)
    refused = attempt(pupil, record)
    assert refused.status_code == 429 and refused.headers['retry-after']
    assert get(pupil, record['lessonKey'])['availableFiles'] == []


def test_slot_freed_while_the_request_waits_is_taken_by_it(client, monkeypatch):
    record, pupil, *_ = setup(client)
    slots = occupied_slot(monkeypatch)
    monkeypatch.setattr(homework_files, 'UPLOAD_WAIT', 5)
    frees_slot_on_first_poll(monkeypatch, slots)
    assert attempt(pupil, record).status_code == 200
    assert slots.acquire(blocking=False)  # the finished upload gave its slot back


def test_full_queue_answers_429_at_once(client, monkeypatch):
    record, pupil, *_ = setup(client)
    slots = occupied_slot(monkeypatch)
    monkeypatch.setattr(homework_files, 'UPLOAD_WAIT', 5)
    monkeypatch.setattr(homework_files, 'UPLOAD_WAITERS', threading.Semaphore(0))
    frees_slot_on_first_poll(monkeypatch, slots)  # a request that queued would get this slot, so only an immediate refusal gives 429
    refused = attempt(pupil, record)
    assert refused.status_code == 429 and refused.headers['retry-after']


def test_pending_uploads_answer_retry_after(client):
    record, pupil, *_ = setup(client)
    key = record['lessonKey']
    # Unfinished uploads are capped per pupil: add them one at a time until the next upload is refused.
    refused = None
    for _ in range(10):
        with api.connect() as db:
            homework_files.tables(db)
            insert_row(db, 'homework_files', id=uuid4().hex, workspace='demo', lesson_key=key, student_id='s1', owner_id='someone',
                       filename='a.txt', size=homework_files.MAX_FILE, created=now().isoformat(), status='uploading')
        response = attempt(pupil, record)
        if response.status_code == 429: refused = response;break
        assert response.status_code == 200, response.text
    assert refused is not None and refused.headers['retry-after']
    with api.connect() as db:
        db.execute("DELETE FROM homework_files WHERE status='uploading'")
    assert attempt(pupil, record).status_code == 200
