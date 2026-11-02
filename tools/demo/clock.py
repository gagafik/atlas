"""A frozen clock and a fictional school for the demo backend.

Import this module before backend.api only through install(), which first
refuses any database that could hold real school data.
"""
import importlib
import os
import pkgutil
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_state = {'now': None}


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


def frozen_now():
    return _state['now'].astimezone(timezone.utc)


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
