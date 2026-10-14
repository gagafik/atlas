"""Consistent local SQLite + private attachments backup. Never overwrites a backup."""
import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil
import sqlite3
from uuid import uuid4


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def create_backup(source: Path, destination: Path):
    source = source.resolve(strict=True)
    destination = destination.resolve()
    if not source.is_file():
        raise ValueError('Source must be an existing SQLite file.')
    if destination == source.parent or source.is_relative_to(destination):
        raise ValueError('The backup directory must not contain the source database.')
    destination.mkdir(parents=True, exist_ok=False)
    target = destination / source.name
    entries = []
    discarded = 0
    # A RESERVED writer lock leaves readers available but prevents metadata/GC
    # changing while immutable ready files are copied. Use a separate reader for
    # SQLite backup: backing up the connection with a write transaction can hang.
    uri = source.as_uri() + '?mode=rw'
    with closing(sqlite3.connect(uri, uri=True, timeout=10)) as reservation:
        reservation.execute('BEGIN IMMEDIATE')
        try:
            with closing(sqlite3.connect(uri, uri=True, timeout=10)) as reader, closing(sqlite3.connect(target)) as copy:
                reader.backup(copy)
                if copy.execute('PRAGMA quick_check').fetchall() != [('ok',)]:
                    raise RuntimeError('Database integrity check failed; backup is incomplete.')
                for table,folder in [('homework_files','homework'),('message_files','messages'),('absence_files','absences')]:
                    if not copy.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(table,)).fetchone():continue
                    rows = copy.execute(f"SELECT id,size,sha256 FROM {table} WHERE status='ready' ORDER BY id").fetchall()
                    file_root = source.with_suffix('.files') / folder
                    copy_root = target.with_suffix('.files') / folder
                    if rows:
                        copy_root.mkdir(parents=True)
                    for file_id, size, expected_hash in rows:
                        if not re.fullmatch('[a-f0-9]{32}', file_id) or not re.fullmatch('[a-f0-9]{64}', expected_hash or ''):
                            raise RuntimeError('Invalid attachment metadata; backup is incomplete.')
                        original = (file_root / (file_id + '.bin')).resolve(strict=True)
                        if not original.is_relative_to(file_root.resolve()):
                            raise RuntimeError('Attachment outside storage; backup is incomplete.')
                        saved = copy_root / original.name
                        shutil.copyfile(original, saved)
                        actual_hash = digest(saved)
                        if saved.stat().st_size != size or actual_hash != expected_hash:
                            raise RuntimeError('Attachment verification failed; backup is incomplete.')
                        entries.append(dict(path=saved.relative_to(destination).as_posix(), size=size, sha256=actual_hash))
                    # Incomplete uploads cannot be resumed after restoring a backup.
                    # They have no draft or submitted-history references.
                    discarded += copy.execute(f"DELETE FROM {table} WHERE status='uploading'").rowcount
                    copy.commit()
        finally:
            reservation.rollback()
    manifest = dict(format='atlas-local-backup-v1', createdAt=datetime.now(timezone.utc).isoformat(),
                    database=dict(path=target.name, size=target.stat().st_size, sha256=digest(target)),
                    files=entries, incompleteUploadsExcluded=discarded)
    # The completion marker is created only after every file and the DB verified.
    pending = destination / 'manifest.pending.json'
    pending.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    pending.replace(destination / 'manifest.json')
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', type=Path, default=Path(__file__).resolve().parents[1] / 'data' / 'timetable.db')
    parser.add_argument('--output', type=Path, help='New directory; existing directories are rejected.')
    args = parser.parse_args()
    destination = args.output or args.database.parent / 'backups' / (datetime.now().strftime('%Y%m%d-%H%M%S-') + uuid4().hex[:8])
    manifest = create_backup(args.database, destination)
    print(json.dumps(dict(directory=str(destination.resolve()), files=len(manifest['files']), verified=True), ensure_ascii=False))


if __name__ == '__main__':
    main()
