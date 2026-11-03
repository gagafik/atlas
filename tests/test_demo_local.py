"""The local viewing of the invented school never opens, seeds or serves anything on a school path."""
import pytest

from tools.demo import local


@pytest.fixture
def guarded(monkeypatch):
    # The environment main() rewrites is restored after the test; nothing may seed or serve before the refusal.
    for name in ['TIMETABLE_DB', 'ATLAS_WEB_PUSH_ENABLED', 'ATLAS_BACKUP_DIR']:
        monkeypatch.setenv(name, 'unchanged')
    def forbidden(*args, **kwargs):
        raise AssertionError('The run must be refused before it seeds or serves anything.')
    monkeypatch.setattr(local.subprocess, 'run', forbidden)
    monkeypatch.setattr(local, 'serve', forbidden)


def test_a_path_inside_the_repository_is_refused_and_not_created(guarded):
    inside = local.ROOT / 'atlas-local-demo-test'
    with pytest.raises(SystemExit):
        local.main(['--workdir', str(inside), '--no-browser'])
    assert not inside.exists()


def test_a_path_with_a_data_directory_is_refused_and_not_created(guarded, tmp_path):
    school = tmp_path / 'data' / 'x'
    with pytest.raises(SystemExit):
        local.main(['--workdir', str(school), '--no-browser'])
    assert not school.exists() and not school.parent.exists()
