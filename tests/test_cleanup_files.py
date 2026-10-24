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
from backend import admission_files, api, assignment_files, homework_files, message_files
from backend.access import now
from test_access import client, create, enable
from test_admissions import action as open_campaign, campaign as new_campaign, filled, new as new_application, ok as admissions_ok, save as save_application, signup, upload as admission_upload
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


def age_blobs(age=7200):
    # Real uploads are normally older than an hour: only the reference check protects them from the orphan sweep.
    with api.connect() as db:
        roots = [module.root_for(db) for module in (homework_files, message_files, assignment_files)]
    for root in roots:
        for path in root.glob('*.bin'): os.utime(path, (time.time() - age, time.time() - age))


def test_referenced_blobs_older_than_an_hour_are_never_orphans(client):
    s = scenario(client)
    age_blobs()
    report = cleanup_files.run(api.DB)
    # Only the two planted files count (the fresh one is now old too); no referenced blob is reported.
    assert report['modules']['homework']['orphans'] == dict(files=2, bytes=len(b'old orphan') + len(b'fresh orphan'))
    assert report['modules']['messages']['orphans']['files'] == 0 and report['modules']['assignments']['orphans']['files'] == 0
    applied = cleanup_files.run(api.DB, apply=True)
    assert applied['freedBytes'] == len(b'Abandoned attempt') + len(b'old orphan') + len(b'fresh orphan') + len(b'Never sent')
    with api.connect() as db:
        root = homework_files.root_for(db)
        assert not s['orphan'].exists() and not s['fresh'].exists() and s['notes'].exists()
        assert (root / (s['submitted'] + '.bin')).is_file()
        assert (message_files.root_for(db) / (s['protected'] + '.bin')).is_file()
        assert (assignment_files.root_for(db) / (s['first'] + '.bin')).is_file()  # kept alive by the second row only
    assert download(s['pupil'], s['key'], s['submitted']).content == b'Submitted answer'
    assert download_material(s['parent'], s['second'], 's1').content == b'Same worksheet'


def test_apply_rechecks_leftovers_that_became_referenced_since_the_survey(client):
    s = scenario(client)
    age_blobs()
    with api.connect() as db:
        root = homework_files.root_for(db)
    report = dict(freedBytes=0, failed=0)
    with closing(sqlite3.connect(api.DB)) as db:
        db.row_factory = sqlite3.Row
        # The leftover list is stale: this blob is now used by a submitted answer.
        cleanup_files.apply_module(db, 'homework_files', root, homework_files, 30, [(s['submitted'], 16)], report)
    assert (root / (s['submitted'] + '.bin')).is_file() and report == dict(freedBytes=0, failed=0)


def test_apply_skips_rows_whose_file_cannot_be_removed(client, monkeypatch):
    record, pupil, parent, teacher = setup(client)
    key = record['lessonKey']
    ids = [upload(pupil, key, get(pupil, key)['assignment'], body=b'Abandoned %d' % n).json()['id'] for n in range(3)]
    with api.connect() as db:
        for n, id in enumerate(ids):
            db.execute('UPDATE homework_files SET created=? WHERE id=?', ((now() - timedelta(days=5 - n)).isoformat(), id))
    real = homework_files.release_row

    def release_row(db, table, root, id):
        if id in ids[:2]: raise PermissionError('file is in use')
        return real(db, table, root, id)

    monkeypatch.setattr(homework_files, 'release_row', release_row)
    monkeypatch.setattr(cleanup_files, 'BATCH', 2)
    report = cleanup_files.run(api.DB, apply=True)['modules']['homework']
    # The two oldest rows block a whole batch, yet the row behind them is still reached.
    assert report['failed'] == 2 and report['freedBytes'] == len(b'Abandoned 2')
    with api.connect() as db:
        assert {r[0] for r in db.execute('SELECT id FROM homework_files')} == set(ids[:2])


def test_apply_reaches_abandoned_admission_upload_behind_many_draft_files(client, monkeypatch):
    enable(client)
    create(client, 'teacher')
    create(client, 'staff')
    create(client, 'parent')
    admission = open_campaign(client, new_campaign(client))
    family, _, _ = signup(admission)
    draft = new_application(family, admission)
    kept = [admissions_ok(admission_upload(family, draft)) for _ in range(5)]
    draft = save_application(family, draft, filled(draft) | dict(files=[dict(id=f['id'], kind='identity') for f in kept]))
    loose = admissions_ok(admission_upload(family, draft))
    with api.connect() as db:
        db.execute('UPDATE admission_files SET created=?', ((now() - timedelta(days=5)).isoformat(),))
        db.execute('UPDATE admission_files SET created=? WHERE id=?', ((now() - timedelta(days=2)).isoformat(), loose['id']))
    monkeypatch.setattr(cleanup_files, 'BATCH', 2)  # smaller than the number of older draft files
    size = loose['size']
    assert cleanup_files.run(api.DB)['modules']['admissions']['unused'] == dict(files=1, bytes=size)
    report = cleanup_files.run(api.DB, apply=True)['modules']['admissions']
    assert report['freedBytes'] == size and report['failed'] == 0
    with api.connect() as db:
        assert {r[0] for r in db.execute('SELECT id FROM admission_files')} == {f['id'] for f in kept}
        assert not (admission_files.root_for(db) / (loose['id'] + '.bin')).exists()
        assert all((admission_files.root_for(db) / (f['id'] + '.bin')).is_file() for f in kept)
    assert cleanup_files.run(api.DB)['modules']['admissions']['unused']['files'] == 0
