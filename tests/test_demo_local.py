"""The local viewing of the invented school never opens, seeds or serves anything on a school path."""
import json
import subprocess

import pytest

from tools.demo import clock, local


@pytest.fixture
def guarded(monkeypatch, tmp_path):
    # The environment main() rewrites is restored after the test; nothing may seed or serve before the refusal.
    for name in ['TIMETABLE_DB', 'ATLAS_WEB_PUSH_ENABLED', 'ATLAS_BACKUP_DIR']:
        monkeypatch.setenv(name, 'unchanged')
    # A built interface and a free port, so only the safety check can end the run.
    built = tmp_path / 'checkout'
    (built / 'dist').mkdir(parents=True)
    (built / 'dist' / 'index.html').write_text('<!doctype html>', encoding='utf-8')
    monkeypatch.setattr(local, 'ROOT', built)
    monkeypatch.setattr(local, 'port_is_free', lambda port: True)
    def forbidden(*args, **kwargs):
        raise AssertionError('The run must be refused before it seeds or serves anything.')
    monkeypatch.setattr(local.subprocess, 'run', forbidden)
    monkeypatch.setattr(local, 'serve', forbidden)
    return tmp_path


def test_a_path_inside_the_repository_is_refused_and_not_created(guarded):
    inside = clock.ROOT / 'atlas-local-demo-test'
    with pytest.raises(SystemExit):
        local.main(['--workdir', str(inside), '--no-browser'])
    assert not inside.exists()


def test_a_path_with_a_data_directory_is_refused_and_not_created(guarded):
    school = guarded / 'data' / 'x'
    with pytest.raises(SystemExit):
        local.main(['--workdir', str(school), '--no-browser'])
    assert not school.exists() and not school.parent.exists()


def test_an_interrupted_reseed_is_redone_on_the_next_run(guarded, monkeypatch):
    work = guarded / 'viewing'
    work.mkdir()
    (work / 'demo.db').write_bytes(b'old')
    (work / 'meta.json').write_text(json.dumps({'roles': {'admin': {'login': 'a', 'password': 'b'}}}), encoding='utf-8')
    seeded = []
    def seed(command, **kwargs):
        seeded.append(command)
        if len(seeded) == 1:
            raise subprocess.CalledProcessError(1, command)
        (work / 'demo.db').write_bytes(b'new')
        (work / 'meta.json').write_text(json.dumps({'roles': {'admin': {'login': 'a', 'password': 'c'}}}), encoding='utf-8')
    monkeypatch.setattr(local.subprocess, 'run', seed)
    monkeypatch.setattr(local, 'serve', lambda port: None)
    with pytest.raises(subprocess.CalledProcessError):
        local.main(['--workdir', str(work), '--fresh', '--no-browser'])
    # The half-built database is not served with the credentials of the old one.
    assert not (work / 'meta.json').exists()
    local.main(['--workdir', str(work), '--no-browser'])
    assert len(seeded) == 2
    assert json.loads((work / 'meta.json').read_text(encoding='utf-8'))['roles']['admin']['password'] == 'c'
