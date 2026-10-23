"""tools.cleanup_files: dry-run report, guarded --apply, and refusal of unsafe databases."""
from contextlib import closing
from datetime import timedelta
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import time
from uuid import uuid4

import pytest
from backend import api, assignment_files, homework_files, message_files
from backend.access import now
from test_access import client
from test_assignment_files import upload as upload_material, download as download_material, save_materials
from test_homework import setup, get, answer, upload, download
from tools import cleanup_files

ROOT = Path(__file__).resolve().parents[1]


def plant(root, name, data=b'leftover', age=0):
    root.mkdir(parents=True, exist_ok=True)
    path = root / name
    path.write_bytes(data)
    if age: os.utime(path, (time.time() - age, time.time() - age))
    return path


def message_upload(parent, draft, body):
    response = parent.post('/api/messages/files', params=dict(workspace='demo', draftId=draft, filename='note.txt'), content=body)
    assert response.status_code == 200, response.text
    return response.json()['id']


def scenario(client):
    record, pupil, parent, teacher = setup(client)
    key = record['lessonKey']
    abandoned = upload(pupil, key, get(pupil, key)['assignment'], body=b'Abandoned attempt').json()['id']
    submitted = upload(pupil, key, get(pupil, key)['assignment'], body=b'Submitted answer').json()['id']
    assert answer(pupil, key, get(pupil, key), files=[submitted]).status_code == 200
    # One message attachment is kept by a saved draft, another was never sent.
    draft = str(uuid4())
    protected = message_upload(parent, draft, b'Draft attachment')
    loose = message_upload(parent, str(uuid4()), b'Never sent')
    with api.connect() as db:
        from backend.message_drafts import tables as draft_tables
        draft_tables(db)
        owner = db.execute('SELECT owner_id FROM message_files WHERE id=?', (protected,)).fetchone()[0]
        db.execute('INSERT INTO message_drafts VALUES (?,?,?,?,?,?,?,?)',
                   ('demo', owner, draft, 1, 'active', None, json.dumps(dict(fileIds=[protected])), now().isoformat()))
    # The same worksheet uploaded twice: one row stays linked, so the shared bytes stay.
    first = upload_material(teacher, record, b'Same worksheet').json()
    second = upload_material(teacher, record, b'Same worksheet').json()
    save_materials(teacher, record, [second['id']])
    old = (now() - timedelta(days=2)).isoformat()
    with api.connect() as db:
        for table in ['homework_files', 'message_files', 'assignment_files']:
            db.execute(f'UPDATE {table} SET created=?', (old,))
        root = homework_files.root_for(db)
    orphan = plant(root, uuid4().hex + '.bin', b'old orphan', age=7200)
    fresh = plant(root, uuid4().hex + '.bin', b'fresh orphan')
    notes = plant(root, 'notes.txt', b'not an upload', age=7200)
    return dict(pupil=pupil, parent=parent, key=key, abandoned=abandoned, submitted=submitted, protected=protected, loose=loose,
                first=first['id'], second=second['id'], orphan=orphan, fresh=fresh, notes=notes)


def counts():
    with api.connect() as db:
        return {t: db.execute(f'SELECT count(*) FROM {t}').fetchone()[0] for t in ['homework_files', 'message_files', 'assignment_files']}


def test_dry_run_reports_and_changes_nothing_then_apply_deletes_only_unused(client):
    s = scenario(client)
    before = counts()
    files_before = sorted(p.name for p in s['orphan'].parent.iterdir())
    report = cleanup_files.run(api.DB)
    assert report['applied'] is False and report['freedBytes'] == 0
    json.dumps(report, ensure_ascii=False)
    homework_report = report['modules']['homework']
    assert homework_report['unused'] == dict(files=1, bytes=len(b'Abandoned attempt'))
    assert homework_report['orphans'] == dict(files=1, bytes=len(b'old orphan')) and homework_report['unknownFiles'] == 1
    assert homework_report['missingFiles'] == 0 and homework_report['files'] == 2
    assert report['modules']['messages']['unused']['files'] == 1  # only the file no draft keeps
    assert report['modules']['messages']['staleDraftFiles'] == dict(files=0, bytes=0)
    assert report['modules']['assignments']['unused'] == dict(files=1, bytes=0)  # its bytes are still used by the linked copy
    assert report['modules']['quizzes']['initialized'] is False and report['totalBytes'] > 0
    assert counts() == before and sorted(p.name for p in s['orphan'].parent.iterdir()) == files_before
    applied = cleanup_files.run(api.DB, apply=True)
    assert applied['applied'] is True
    assert applied['freedBytes'] == len(b'Abandoned attempt') + len(b'old orphan') + len(b'Never sent')
    with api.connect() as db:
        assert {r[0] for r in db.execute('SELECT id FROM homework_files')} == {s['submitted']}
        assert {r[0] for r in db.execute('SELECT id FROM message_files')} == {s['protected']}
        assert {r[0] for r in db.execute('SELECT id FROM assignment_files')} == {s['second']}
        root = homework_files.root_for(db)
        assert not (root / (s['abandoned'] + '.bin')).exists() and not s['orphan'].exists()
        assert s['fresh'].exists() and s['notes'].exists() and (root / (s['submitted'] + '.bin')).is_file()
        assert (message_files.root_for(db) / (s['protected'] + '.bin')).is_file()
        assert (assignment_files.root_for(db) / (s['first'] + '.bin')).is_file()  # the shared bytes outlive the first row
    assert download(s['pupil'], s['key'], s['submitted']).content == b'Submitted answer'
    assert download_material(s['parent'], s['second'], 's1').content == b'Same worksheet'
    again = cleanup_files.run(api.DB)
    for module in again['modules'].values():
        assert module['unused']['files'] == 0 and module['orphans']['files'] == 0
    assert again['modules']['homework']['unknownFiles'] == 1  # reported, never deleted


def test_missing_files_are_reported_never_deleted(client):
    s = scenario(client)
    with api.connect() as db:
        (homework_files.root_for(db) / (s['submitted'] + '.bin')).unlink()
    for apply in [False, True]:
        assert cleanup_files.run(api.DB, apply=apply)['modules']['homework']['missingFiles'] == 1
    assert counts()['homework_files'] == 1  # the submitted row stays (the abandoned one is gone)


def test_days_option_keeps_recent_uploads(client):
    scenario(client)
    with api.connect() as db:
        db.execute('UPDATE homework_files SET created=?', ((now() - timedelta(days=3)).isoformat(),))
    assert cleanup_files.run(api.DB, days=5)['modules']['homework']['unused']['files'] == 0
    assert cleanup_files.run(api.DB, days=2)['modules']['homework']['unused']['files'] == 1


@pytest.mark.parametrize('marker', ['directory', 'file'])
def test_refuses_database_inside_git_checkout(tmp_path, marker):
    checkout = tmp_path / 'checkout'
    (checkout / 'data').mkdir(parents=True)
    if marker == 'directory': (checkout / '.git').mkdir()
    else: (checkout / '.git').write_text('gitdir: ../elsewhere\n')
    database = checkout / 'data' / 'school.db'
    with closing(sqlite3.connect(database)) as db:
        db.execute('CREATE TABLE t (x)')
        db.commit()
    for call in [lambda: cleanup_files.run(database), lambda: cleanup_files.run(database, apply=True),
                 lambda: cleanup_files.main(['--database', str(database)])]:
        with pytest.raises(SystemExit, match='Git'): call()
    assert not database.with_suffix('.files').exists()


def test_rejects_non_sqlite_missing_and_unconfigured(tmp_path, monkeypatch):
    plain = tmp_path / 'notes.db'
    plain.write_bytes(b'not a database at all')
    with pytest.raises(SystemExit, match='SQLite'): cleanup_files.run(plain)
    with pytest.raises(SystemExit): cleanup_files.run(tmp_path / 'missing.db')
    monkeypatch.delenv('TIMETABLE_DB', raising=False)
    with pytest.raises(SystemExit, match='TIMETABLE_DB'): cleanup_files.main([])
    with pytest.raises(SystemExit): cleanup_files.main(['--database', str(plain), '--days', '0'])
    assert plain.read_bytes() == b'not a database at all'


def test_cli_prints_counts_only(client):
    s = scenario(client)
    result = subprocess.run([sys.executable, '-m', 'tools.cleanup_files', '--database', str(api.DB)], cwd=ROOT,
                            capture_output=True, text=True, encoding='utf-8', timeout=120)
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report['applied'] is False and report['modules']['homework']['unused']['files'] == 1
    assert 'answer.txt' not in result.stdout and 'note.txt' not in result.stdout and not re.search(r'[a-f0-9]{32}', result.stdout)
    assert s['abandoned'] not in result.stdout and counts()['homework_files'] == 2
