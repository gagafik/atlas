"""Import an aSc Timetables 2012 XML export into a separate temporary Atlas database.

    python -I tools/import_asc_xml.py SOURCE.xml --db <TEMP>/asc.db
        [--json <TEMP>/state.json] [--report <TEMP>/import.json] [--rosters synthetic|none]
        [--period-policy observed|free] [--rooms allowed|used] [--only-used] [--force]

The result holds real names and, by default, synthetic pupils, so paths inside any
Git checkout (this one, the main one behind a worktree, or another) are refused.
--db must be a new file or a database this tool created: existing Atlas databases
are never written, even with --force. --json and --report never replace an SQLite
file, the source or each other; other existing files only with --force.
backend.api is never imported: it binds data/timetable.db. The import always fills
the `school` row, which keeps a server started with TIMETABLE_DB=<TEMP>/asc.db from
loading the real school seed. Only the counts report is printed.
"""
from contextlib import closing
from pathlib import Path
import argparse
import json
import os
import sqlite3
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from backend.asc_import import MAX_BYTES, import_asc

APPLICATION_ID = 0x41534331  # 'ASC1': marks databases created by this tool.
NAME = 'aSc import (temp)'


def outside(value, flag):
    if str(value).startswith(('\\\\', '//')):
        raise SystemExit(f'{flag}: сетевые пути и пути вида \\\\?\\ запрещены. Укажите обычный временный каталог.')
    path = Path(value).resolve()
    for folder in [path, *path.parents]:
        if not folder.exists(): continue
        if (folder / '.git').exists() or os.path.samefile(folder, ROOT):
            raise SystemExit(f'{flag}: путь внутри репозитория запрещён. Укажите временный каталог вне проекта.')
    return path


def output(value, flag, force, taken):
    path = outside(value, flag)
    if any(path == other or path.exists() and other.exists() and os.path.samefile(path, other) for other in taken):
        raise SystemExit(f'{flag}: файл совпадает с исходным XML, базой или другим выходным файлом.')
    if path.exists():
        if not path.is_file(): raise SystemExit(f'{flag}: это не файл.')
        with open(path, 'rb') as existing:
            if existing.read(16) == b'SQLite format 3\x00': raise SystemExit(f'{flag}: файл является базой SQLite и не заменяется.')
        if not force: raise SystemExit(f'{flag}: файл уже существует. Для замены укажите --force.')
    return path


def write_db(path, workspace, state, force):
    fresh = not path.exists()
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as db:
        try:
            if not fresh and db.execute('PRAGMA application_id').fetchone()[0] != APPLICATION_ID:
                raise SystemExit('--db: база создана не этим импортом. Существующие базы Атласа не изменяются; укажите новый файл.')
            if fresh: db.execute(f'PRAGMA application_id={APPLICATION_ID}')
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('CREATE TABLE IF NOT EXISTS workspaces (id TEXT PRIMARY KEY, revision INTEGER, document TEXT)')
        except sqlite3.DatabaseError: raise SystemExit('--db: файл не является базой SQLite. Укажите новый файл.') from None
        with db:
            row = db.execute('SELECT revision, document FROM workspaces WHERE id=?', (workspace,)).fetchone()
            if row is None:
                db.execute('INSERT INTO workspaces (id, revision, document) VALUES (?, ?, ?)', (workspace, state.revision, state.model_dump_json()))
                return
            if not force: raise SystemExit(f'В базе уже есть рабочая область «{workspace}». Для замены укажите --force.')
            try: previous = json.loads(row[1])['settings']['name']
            except (TypeError, ValueError, KeyError): previous = None
            if previous != NAME: raise SystemExit(f'Рабочая область «{workspace}» не похожа на временный импорт aSc и не заменяется.')
            # The import writes the document without capturing timetable versions; old versions must not overlay a new import.
            try: dated = db.execute('SELECT 1 FROM timetable_current WHERE workspace=?', (workspace,)).fetchone()
            except sqlite3.OperationalError: dated = None
            if dated: raise SystemExit(f'Рабочая область «{workspace}» уже ведёт историю расписания и не заменяется; укажите новый файл.')
            state = state.model_copy(update={'revision': row[0] + 1})
            db.execute('UPDATE workspaces SET revision=?, document=? WHERE id=? AND revision=?', (state.revision, state.model_dump_json(), workspace, row[0]))


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'): stream.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description='Импорт XML aSc Timetables 2012 во временную базу Атласа.')
    parser.add_argument('source', type=Path)
    parser.add_argument('--db', required=True)
    parser.add_argument('--json')
    parser.add_argument('--report')
    parser.add_argument('--rosters', choices=['synthetic', 'none'], default='synthetic')
    parser.add_argument('--period-policy', choices=['observed', 'free'], default='observed')
    parser.add_argument('--rooms', choices=['allowed', 'used'], default='allowed')
    parser.add_argument('--only-used', action='store_true')
    parser.add_argument('--force', action='store_true')
    args = parser.parse_args(argv)
    if not args.source.is_file() or args.source.stat().st_size > MAX_BYTES: raise SystemExit('Исходный файл не найден или превышает 20 МБ.')
    db = outside(args.db, '--db')
    taken = [args.source.resolve(), db]
    state_path = args.json and output(args.json, '--json', args.force, taken)
    report_path = args.report and output(args.report, '--report', args.force, taken + [state_path] if state_path else taken)
    try:
        state, report = import_asc(args.source.read_bytes(), args.source.name, rosters=args.rosters, period_policy=args.period_policy,
                                   only_used=args.only_used, rooms=args.rooms)
    except ValueError as exc: raise SystemExit(str(exc)) from None
    write_db(db, 'school', state, args.force)
    for path, text in [(state_path, state.model_dump_json(indent=2)), (report_path, json.dumps(report, ensure_ascii=False, indent=2))]:
        if path:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
