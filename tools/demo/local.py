"""Look at the fictional school on this computer: the normal API server, the real clock, a temporary database.

    python -m tools.demo.local [--workdir DIR] [--fresh] [--port 8000] [--no-browser]

The first run seeds DIR/demo.db with tools/demo/seed_school.py (about two minutes); later runs reuse it.
The school's own database is never opened: a path inside the repository or with a data/ directory is refused
before anything is created. Unlike the Pages demo the clock is not frozen, so days after 02.11.2026 have a
timetable and no journal records. Stop with Ctrl+C.
"""
import argparse
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ROLE_NAMES = dict(admin='администратор', teacher='учитель', parent='родитель', student='ученик')


def parse(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--workdir', default=str(Path(tempfile.gettempdir()) / 'atlas-local-demo'),
                        help='Каталог временной базы (вне репозитория и без data/ в пути).')
    parser.add_argument('--fresh', action='store_true', help='Создать вымышленную школу заново.')
    parser.add_argument('--port', type=int, default=8000)
    parser.add_argument('--no-browser', action='store_true', help='Не открывать страницу в браузере.')
    return parser.parse_args(argv)


def port_is_free(port):
    with socket.socket() as probe:
        return probe.connect_ex(('127.0.0.1', port)) != 0


def accounts(meta):
    """(role label, login, password) of every account the seed printed, the extra ones last."""
    rows = [(ROLE_NAMES.get(role, role), item['login'], item['password']) for role, item in meta['roles'].items()]
    rows += [(item.get('label', key), item['login'], item['password']) for key, item in meta.get('extra', {}).items()]
    return rows


def describe(url, work, meta):
    lines = [f'Вымышленная школа: {url}', '', f'{"Роль":<34}{"Логин":<22}Пароль']
    lines += [f'{role:<34}{login:<22}{password}' for role, login, password in accounts(meta)]
    lines += ['',
              'Часы настоящие. Записи журнала есть с 05.10 по 02.11.2026: у более поздних дней есть расписание, но нет записей.',
              f'Изменения остаются во временной базе ({work}) до запуска с --fresh. Если интерфейс или API изменились,',
              'запустите с --fresh. Остановка: Ctrl+C.']
    return '\n'.join(lines)


def open_when_ready(url):
    def wait():
        for _ in range(60):
            try:
                urllib.request.urlopen(url, timeout=1).close()
                break
            except OSError:
                time.sleep(.5)
        else:
            return
        webbrowser.open(url)
    threading.Thread(target=wait, daemon=True).start()


def serve(port):
    from tools.demo import clock
    # Replaces only the starting state of the "school" workspace; the real clock stays.
    clock.install_school()
    import uvicorn
    from backend import api
    uvicorn.run(api.app, host='127.0.0.1', port=port, log_level='warning')


def main(argv=None):
    args = parse(argv)
    work = Path(args.workdir).resolve()
    # The environment is replaced before the safety check, so a TIMETABLE_DB of the caller can never be picked up.
    os.environ.update(TIMETABLE_DB=str(work / 'demo.db'), ATLAS_WEB_PUSH_ENABLED='0', ATLAS_BACKUP_DIR='')
    from tools.demo import clock
    clock.assert_safe_database()
    if not (ROOT / 'dist' / 'index.html').exists():
        sys.exit('Сначала соберите интерфейс: npm ci && npm run build')
    if not port_is_free(args.port):
        sys.exit(f'Порт {args.port} занят (возможно, уже запущен ./start.ps1). Укажите другой: --port {args.port + 1}.')
    meta_path = work / 'meta.json'
    if args.fresh or not meta_path.exists() or not (work / 'demo.db').exists():
        print('Создаю вымышленную школу, это займёт около двух минут…', flush=True)
        # meta.json is written last: while it is missing the seed is unfinished, so an interrupted run reseeds next time.
        meta_path.unlink(missing_ok=True)
        # The seed prints Russian progress lines: UTF-8 keeps it working when the output goes to a file or a pipe.
        subprocess.run([sys.executable, '-m', 'tools.demo.seed_school', '--workdir', str(work)], cwd=ROOT, check=True, env={**os.environ, 'PYTHONUTF8': '1'})
    meta = json.loads(meta_path.read_text(encoding='utf-8'))
    url = f'http://127.0.0.1:{args.port}'
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except AttributeError:
        pass
    print(describe(url, work, meta), flush=True)
    if not args.no_browser:
        open_when_ready(url)
    serve(args.port)
    return 0


if __name__ == '__main__':
    sys.exit(main())
