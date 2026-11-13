from fastapi import FastAPI
from fastapi.testclient import TestClient
from backend.api import Frontend


def site(tmp_path):
    (tmp_path / 'assets').mkdir()
    (tmp_path / 'index.html').write_text('<script src="/assets/index-abc.js"></script>')
    (tmp_path / 'sw.js').write_text('// worker')
    (tmp_path / 'assets' / 'index-abc.js').write_text('// build')
    app = FastAPI()
    app.mount('/', Frontend(directory=tmp_path, html=True))
    return TestClient(app)


def test_the_page_and_the_worker_are_rechecked_on_every_load(tmp_path):
    client = site(tmp_path)
    assert client.get('/').headers['cache-control'] == 'no-cache'
    assert client.get('/sw.js').headers['cache-control'] == 'no-cache'


def test_a_hashed_build_file_is_kept_and_a_missing_one_is_not(tmp_path):
    client = site(tmp_path)
    assert 'immutable' in client.get('/assets/index-abc.js').headers['cache-control']
    missing = client.get('/assets/index-old.js')
    assert missing.status_code == 404 and 'immutable' not in missing.headers.get('cache-control', '')
