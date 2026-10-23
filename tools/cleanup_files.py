"""Report and remove unused uploaded files. Dry-run by default; nothing is deleted without --apply.

    python -m tools.cleanup_files --database PATH [--apply] [--days N]

Make a full copy first: python -m tools.backup_school (check manifest.json afterwards).
Deleted: never-used uploads past the grace period (the same rule the API applies online,
without its per-upload limit) and unreferenced <32 hex>.bin leftovers older than one hour.
Never deleted: referenced files (history, drafts, notices, published items, shared lesson
materials), files whose names do not look like uploads, and rows whose file is missing
(only reported). A database inside a Git checkout is refused. The printed JSON holds
counts and bytes only: no names, ids or content.
"""
from contextlib import closing
from datetime import timedelta
from pathlib import Path
import argparse
import importlib
import json
import os
import re
import sqlite3
import sys
import time

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from backend import homework_files as storage
from tools.backup_school import reject_link

BLOB = re.compile(r'[a-f0-9]{32}\.bin')
ORPHAN_AGE = 3600  # An upload in flight always has its row first; this adds a margin.
STALE_DRAFT_DAYS = 180
BATCH = 200


def checked_database(value):
    if str(value).startswith(('\\\\', '//')):
        raise SystemExit('--database: сетевые пути и пути вида \\\\?\\ запрещены. Укажите обычный локальный путь.')
    path = Path(value).resolve()
    for folder in [path, *path.parents]:
        if (folder / '.git').exists():
            raise SystemExit('--database: база внутри Git-репозитория. Запустите инструмент на сервере или для копии вне проекта.')
    if not path.is_file():
        raise SystemExit('--database: файл не найден.')
    with open(path, 'rb') as stream:
        if stream.read(16) != b'SQLite format 3\x00': raise SystemExit('--database: это не база SQLite.')
    return path


def holders(db, table, blob):
    if 'blob_id' in storage.columns(db, table):
        return {r[0] for r in db.execute(f'SELECT id FROM {table} WHERE blob_id=? OR (blob_id IS NULL AND id=?)', (blob, blob))}
    return {r[0] for r in db.execute(f'SELECT id FROM {table} WHERE id=?', (blob,))}


def reclaimable(db, table, rows):
    # Bytes freed when all `rows` go: a shared file counts once, and only if no other row keeps it.
    ids = {r[0] for r in rows}
    seen = set()
    total = 0
    for id, size in rows:
        blob = storage.blob_of(db, table, id)
        if blob in seen: continue
        seen.add(blob)
        if holders(db, table, blob) <= ids: total += size
    return total


def release(db, table, root, id, size):
    blob = storage.blob_of(db, table, id)
    last = not storage.referenced(db, table, blob, exclude=id)
    storage.release_row(db, table, root, id)
    return size if last else 0


def orphans(db, table, root):
    # Unreferenced upload-shaped files old enough that no request can still be writing them.
    found, unknown = [], 0
    if root.is_dir():
        for entry in os.scandir(root):
            if not entry.is_file(follow_symlinks=False) or not BLOB.fullmatch(entry.name):
                unknown += 1
                continue
            info = entry.stat(follow_symlinks=False)
            if time.time() - info.st_mtime > ORPHAN_AGE and not storage.referenced(db, table, entry.name[:-4]):
                found.append((entry.name[:-4], info.st_size))
    return found, unknown


def missing(db, table, root):
    expr = 'coalesce(blob_id,id)' if 'blob_id' in storage.columns(db, table) else 'id'
    return sum(1 for (blob,) in db.execute(f"SELECT {expr} FROM {table} WHERE status='ready'")
               if not (root / (blob + '.bin')).is_file())


def stale_drafts(db, days):
    cutoff = (storage.now() - timedelta(days=days)).isoformat()
    rows = db.execute('''SELECT f.id,f.size FROM message_files f JOIN message_drafts d
        ON d.workspace=f.workspace AND d.owner_id=f.owner_id AND d.id=f.draft_id
        WHERE f.post_id IS NULL AND f.status='ready' AND d.status='active' AND d.updated<?
        AND EXISTS(SELECT 1 FROM json_each(d.content,'$.fileIds') WHERE value=f.id)''', (cutoff,)).fetchall()
    return dict(files=len(rows), bytes=sum(r[1] for r in rows))


def survey(db, key, table, root, module, days):
    # Read-only: what exists, what the online rules would delete and what is left over on disk.
    exists = bool(db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone())
    title, limit = storage.STORES[key][4], storage.module_limit(key)
    if not exists:
        return dict(title=title, initialized=False, files=0, bytes=0, budgetBytes=limit, unused=dict(files=0, bytes=0),
                    orphans=dict(files=0, bytes=0), unknownFiles=0, missingFiles=0, freedBytes=0, failed=0), [], []
    rows = module.unused_rows(db, -1, days)
    left, unknown = orphans(db, table, root)
    result = dict(title=title, initialized=True, files=db.execute(f"SELECT count(*) FROM {table} WHERE status='ready'").fetchone()[0],
                  bytes=storage.module_stored(db, key), budgetBytes=limit,
                  unused=dict(files=len(rows), bytes=reclaimable(db, table, rows)),
                  orphans=dict(files=len(left), bytes=sum(size for _, size in left)),
                  unknownFiles=unknown, missingFiles=missing(db, table, root), freedBytes=0, failed=0)
    if key == 'messages': result['staleDraftFiles'] = stale_drafts(db, STALE_DRAFT_DAYS)
    return result, rows, left


def apply_module(db, table, root, module, days, leftovers, report):
    # Each batch re-checks its predicate inside BEGIN IMMEDIATE, which shares the API's writer lock.
    while True:
        db.execute('BEGIN IMMEDIATE')
        deleted = 0
        for id, size in module.unused_rows(db, BATCH, days):
            try: report['freedBytes'] += release(db, table, root, id, size)
            except OSError: report['failed'] += 1
            else: deleted += 1
        db.commit()
        if not deleted: break
    for start in range(0, len(leftovers), BATCH):
        db.execute('BEGIN IMMEDIATE')
        for blob, size in leftovers[start:start + BATCH]:
            if storage.referenced(db, table, blob): continue
            try: (root / (blob + '.bin')).unlink(missing_ok=True)
            except OSError: report['failed'] += 1
            else: report['freedBytes'] += size
        db.commit()


def run(path, apply=False, days=1):
    path = checked_database(path)
    with closing(sqlite3.connect(path, timeout=10)) as db:
        db.row_factory = sqlite3.Row
        plans, modules = {}, {}
        db.execute('BEGIN')  # Planning never writes: any helper DDL is rolled back.
        try:
            for key, (table, folder, _env, _default, _title, target) in storage.STORES.items():
                module = importlib.import_module(target)
                root = module.root_for(db)
                for folder_path in (root.parent, root):
                    try: reject_link(folder_path)
                    except ValueError: raise SystemExit('Каталог файлов не должен быть символической ссылкой или junction.') from None
                modules[key], rows, left = survey(db, key, table, root, module, days)
                plans[key] = (table, root, module, left)
            totals = dict(totalBytes=storage.total_stored(db), overallLimitBytes=storage.total_limit(),
                          freeBytes=storage.free_bytes(db), reserveBytes=storage.reserve_bytes())
        finally: db.rollback()
        if apply:
            for key, (table, root, module, left) in plans.items():
                if modules[key]['initialized']: apply_module(db, table, root, module, days, left, modules[key])
        return dict(applied=apply, **totals, freedBytes=sum(m['freedBytes'] for m in modules.values()), modules=modules)


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'): stream.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--database', help='Путь к базе; по умолчанию переменная TIMETABLE_DB.')
    parser.add_argument('--apply', action='store_true', help='Удалить найденное. Без флага только отчёт.')
    parser.add_argument('--days', type=int, default=1, help='Сколько суток хранить неиспользованные готовые загрузки (от 1).')
    args = parser.parse_args(argv)
    if args.days < 1: parser.error('--days: укажите целое число не меньше 1.')
    database = args.database or os.environ.get('TIMETABLE_DB')
    if not database: raise SystemExit('Укажите --database или переменную окружения TIMETABLE_DB.')
    print(json.dumps(run(database, args.apply, args.days), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
