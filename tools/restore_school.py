"""Verify a backup or restore into a NEW directory. Never replaces a running school."""
import argparse
from contextlib import closing
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import sqlite3

from .backup_school import STORES, blob_expr, digest, reject_link

MAX_MANIFEST_BYTES=64*1024*1024
MAX_FILES=200000


def check_runtime():
    # The former Debian Bookworm image (SQLite 3.40.1) rejects our JSON
    # expression index with trusted_schema=OFF. Keep the protected check and
    # require the newer runtime used by the server image instead of relaxing it.
    if sqlite3.sqlite_version_info<(3,42,0):
        raise ValueError('Verified restores require SQLite 3.42 or newer.')


def unique_object(pairs):
    result={}
    for key,value in pairs:
        if key in result:raise ValueError('Duplicate manifest field.')
        result[key]=value
    return result


def path_inside(root, relative):
    if not isinstance(relative,str) or not relative or '\\' in relative or ':' in relative:
        raise ValueError('Invalid backup path.')
    parts=PurePosixPath(relative).parts
    if PurePosixPath(relative).is_absolute() or any(part in ['.','..'] for part in parts) or '/'.join(parts)!=relative:
        raise ValueError('Invalid backup path.')
    candidate=root
    for part in parts:
        candidate=candidate/part
        reject_link(candidate)
    if not candidate.resolve().is_relative_to(root):raise ValueError('Backup path escapes its directory.')
    return candidate


def entry_check(root, entry):
    if not isinstance(entry,dict) or set(entry)!={'path','size','sha256'}:
        raise ValueError('Invalid manifest entry.')
    size,sha=entry['size'],entry['sha256']
    if type(size) is not int or size<0 or not isinstance(sha,str) or not re.fullmatch('[a-f0-9]{64}',sha):
        raise ValueError('Invalid size or checksum.')
    path=path_inside(root,entry['path'])
    if not path.is_file() or path.stat().st_size!=size or digest(path)!=sha:
        raise ValueError('Backup file is missing or corrupted.')
    return path


def verify_backup(directory:Path):
    check_runtime()
    reject_link(directory)
    root=directory.resolve(strict=True)
    if not root.is_dir():raise ValueError('A backup directory is required.')
    marker=path_inside(root,'manifest.json')
    if not marker.is_file() or marker.stat().st_size>MAX_MANIFEST_BYTES:
        raise ValueError('Backup completion manifest is missing or too large.')
    manifest=json.loads(marker.read_text(encoding='utf-8'),object_pairs_hook=unique_object)
    if not isinstance(manifest,dict) or manifest.get('format')!='atlas-local-backup-v1':
        raise ValueError('Unsupported backup format.')
    entries=manifest.get('files')
    if not isinstance(entries,list) or len(entries)>MAX_FILES:raise ValueError('Invalid attachment list.')
    database=manifest.get('database')
    if not isinstance(database,dict) or not isinstance(database.get('path'),str) or '/' in database['path'] or database['path']=='manifest.json':
        raise ValueError('Invalid database entry.')
    db_path=entry_check(root,database)
    if not db_path.name.lower().endswith(('.db','.sqlite','.sqlite3')):
        raise ValueError('Unsupported database filename.')
    prefix=db_path.with_suffix('.files').name
    folders={folder for _,folder in STORES}
    listed={}
    for entry in entries:
        if not isinstance(entry,dict) or not isinstance(entry.get('path'),str):raise ValueError('Invalid attachment entry.')
        parts=PurePosixPath(entry['path']).parts
        if len(parts)!=3 or parts[0]!=prefix or parts[1] not in folders or not re.fullmatch('[a-f0-9]{32}\\.bin',parts[2]):
            raise ValueError('Unexpected attachment path.')
        if entry['path'] in listed:raise ValueError('Duplicate attachment path.')
        entry_check(root,entry)
        listed[entry['path']]=(entry['size'],entry['sha256'])
    actual_files=set()
    for item in root.rglob('*'):
        reject_link(item)
        if item.is_file():actual_files.add(item.relative_to(root).as_posix())
    if actual_files!={database['path'],'manifest.json',*listed}:
        raise ValueError('Backup contains unlisted or missing files.')
    with closing(sqlite3.connect(db_path.as_uri()+'?mode=ro&immutable=1',uri=True)) as db:
        db.execute('PRAGMA trusted_schema=OFF')
        if db.execute('PRAGMA quick_check').fetchall()!=[('ok',)]:raise ValueError('Database integrity check failed.')
        if db.execute('PRAGMA foreign_key_check').fetchone():raise ValueError('Database foreign-key check failed.')
        tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if 'workspaces' not in tables:raise ValueError('School database has no workspace table.')
        expected={}
        for table,folder in STORES:
            if table not in tables:continue
            if db.execute(f"SELECT 1 FROM {table} WHERE status='uploading' LIMIT 1").fetchone():
                raise ValueError('Backup contains an incomplete upload.')
            for id,size,sha in db.execute(f"SELECT {blob_expr(db,table)},size,sha256 FROM {table} WHERE status='ready'"):
                if not isinstance(id,str) or not re.fullmatch('[a-f0-9]{32}',id):raise ValueError('Invalid attachment metadata.')
                path=f'{prefix}/{folder}/{id}.bin'
                # Rows sharing one deduplicated file must agree about its bytes.
                if expected.setdefault(path,(size,sha))!=(size,sha):raise ValueError('Invalid attachment metadata.')
        if expected!=listed:raise ValueError('Manifest and database attachment references do not match.')
        # Parse stored workspace documents without migrating or changing their schema.
        count=0
        for document, in db.execute('SELECT document FROM workspaces'):
            if not isinstance(json.loads(document),dict):raise ValueError('Invalid workspace document.')
            count+=1
    return dict(manifest=manifest,files=len(entries),bytes=database['size']+sum(x['size'] for x in entries),workspaces=count)


def restore_backup(source:Path,destination:Path):
    verified=verify_backup(source)
    root=source.resolve(strict=True)
    reject_link(destination)
    target=destination.resolve()
    if target.is_relative_to(root) or root.is_relative_to(target):
        raise ValueError('Restore and backup directories must be separate.')
    if target.exists():raise FileExistsError('Restore requires a new directory; existing data is never replaced.')
    target.parent.mkdir(parents=True,exist_ok=True)
    if shutil.disk_usage(target.parent).free<verified['bytes']+64*1024*1024:
        raise ValueError('Not enough free space for the restored copy.')
    target.mkdir(mode=0o700)
    # No completion marker until the restored bytes have been checked.
    for entry in [verified['manifest']['database'],*verified['manifest']['files']]:
        original=path_inside(root,entry['path'])
        saved=path_inside(target,entry['path']);saved.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(original,saved)
        if saved.stat().st_size!=entry['size'] or digest(saved)!=entry['sha256']:
            raise ValueError('Backup changed during restore; destination is incomplete.')
    pending=target/'manifest.pending.json'
    pending.write_text(json.dumps(verified['manifest'],ensure_ascii=False,indent=2),encoding='utf-8')
    pending.replace(target/'manifest.json')
    try:result=verify_backup(target)
    except Exception:
        (target/'manifest.json').replace(target/'manifest.failed.json')
        raise
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('backup',type=Path)
    parser.add_argument('--output',type=Path,help='Restore only into a new directory; omit to verify in place.')
    args=parser.parse_args()
    try:
        result=restore_backup(args.backup,args.output) if args.output else verify_backup(args.backup)
    except (ValueError,OSError,sqlite3.Error) as exc:
        if sqlite3.sqlite_version_info<(3,42,0):
            parser.exit(2,'Verification requires SQLite 3.42 or newer. Update the Python runtime. No existing school data was replaced.\n')
        parser.exit(2,f'Backup verification/restore failed ({type(exc).__name__}). No existing school data was replaced.\n')
    print(json.dumps(dict(verified=True,restored=bool(args.output),files=result['files'],bytes=result['bytes'],workspaces=result['workspaces'])))


if __name__=='__main__':main()
