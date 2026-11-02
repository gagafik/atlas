"""Run the real backend on a seeded demo database with the clock frozen.

    python -m tools.demo.frozen_server --workdir DIR [--port 8765]

It serves ROOT/dist as well, so `npm run build` has to run first. The database
lives in DIR/demo.db and is never the school's. The capture tool visits this
server; nothing here is deployed.
"""
import argparse
import json
import os
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workdir', required=True)
    parser.add_argument('--port', type=int, default=8765)
    args = parser.parse_args()
    work = Path(args.workdir).resolve()
    os.environ.update(TIMETABLE_DB=str(work / 'demo.db'), ATLAS_WEB_PUSH_ENABLED='0', ATLAS_BACKUP_DIR='')
    from tools.demo import clock
    clock.assert_safe_database()  # before anything is read and before the backend is imported
    clock.forget_sessions()
    meta = json.loads((work / 'meta.json').read_text(encoding='utf-8'))
    clock.install(meta['now'])
    clock.install_school()
    import uvicorn
    from backend import api
    uvicorn.run(api.app, host='127.0.0.1', port=args.port, log_level='warning')


if __name__ == '__main__':
    sys.exit(main())
