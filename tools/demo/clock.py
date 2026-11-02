"""A frozen clock and a fictional school for the demo backend.

Import this module before backend.api only through install(), which first
refuses any database that could hold real school data.
"""
import importlib
import os
import pkgutil
import sqlite3
import sys
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_state = {'now': None, 'tick': 0}


def assert_safe_database():
    """Exit before the backend is imported unless TIMETABLE_DB is a temporary demo database."""
    raw = os.environ.get('TIMETABLE_DB')
    if not raw:
        sys.exit('TIMETABLE_DB is not set: the demo runs only on a temporary database.')
    path = Path(raw).resolve()
    if path == ROOT or ROOT in path.parents:
        sys.exit('TIMETABLE_DB is inside the repository: use a temporary directory.')
    if any(part.lower() == 'data' for part in path.parts):
        sys.exit('TIMETABLE_DB has a "data" directory in its path: the demo never touches school data.')
    return path


def set_time(iso):
    _state['now'] = datetime.fromisoformat(iso)
    _state['tick'] = 0


def frozen_now():
    """The frozen instant, advanced by one microsecond per call.

    Sessions are trimmed by `created`; a frozen clock that never moves would tie
    them, and a new session could be trimmed away at once.
    """
    _state['tick'] += 1
    return (_state['now'] + timedelta(microseconds=_state['tick'])).astimezone(timezone.utc)


def forget_sessions():
    """Drop the sessions of earlier runs of this temporary database.

    A restarted clock begins again at the frozen instant, so a new session would look older than
    the old ones and be trimmed away at once (only the ten newest sessions of an account stay).
    """
    path = assert_safe_database()
    if not path.exists():
        return
    with closing(sqlite3.connect(path)) as db:
        try:
            db.execute('DELETE FROM sessions')
            db.commit()
        except sqlite3.OperationalError:
            pass  # a database without accounts yet


def install(iso):
    """Freeze backend time at `iso` and return the number of patched modules."""
    assert_safe_database()
    set_time(iso)
    from backend import access
    import backend
    for module in pkgutil.iter_modules(backend.__path__):
        try:
            importlib.import_module(f'backend.{module.name}')
        except ImportError:
            continue  # an optional import tool without its dependency
    original = access.now
    patched = 0
    for name, module in list(sys.modules.items()):
        if name.startswith('backend') and getattr(module, 'now', None) is original:
            setattr(module, 'now', frozen_now)
            patched += 1
    access.now = frozen_now
    return patched


def install_school():
    """Serve the invented school; backend.seed.initial_state('school') is never called."""
    from backend import api
    from backend.models import State
    from . import school
    original = api.initial_state
    fictional = State.model_validate(school.base_data())

    def initial_state(workspace='school'):
        if workspace == 'school':
            return fictional.model_copy(deep=True)
        if workspace == 'demo':
            return original('demo')  # the repository's own invented sample, no file access
        raise ValueError(workspace)

    api.initial_state = initial_state
