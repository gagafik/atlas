"""Start the backend in-process on a temporary database with the demo clock and school."""
import os
from pathlib import Path

HEADERS = {'X-Atlas-Request': '1'}


def boot(workdir, now):
    """Set the environment, freeze the clock, install the invented school; return the FastAPI module."""
    work = Path(workdir).resolve()
    os.environ.update(TIMETABLE_DB=str(work / 'demo.db'), ATLAS_WEB_PUSH_ENABLED='0', ATLAS_BACKUP_DIR='')
    from . import clock
    clock.assert_safe_database()
    clock.install(now)
    clock.install_school()
    from backend import api
    return api


def client(api):
    from fastapi.testclient import TestClient
    return TestClient(api.app, headers=HEADERS)
