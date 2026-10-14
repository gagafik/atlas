"""Isolated HTTP test server with synthetic accounts. Never opens the school DB."""
from contextlib import contextmanager
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import httpx
from .benchmark_portal import fixture,stop_child


@contextmanager
def synthetic_server():
    with tempfile.TemporaryDirectory(prefix='atlas-message-load-') as directory:
        root=Path(directory);previous=os.environ.get('TIMETABLE_DB')
        os.environ['TIMETABLE_DB']=str(root/'synthetic.db')
        identities,counts,cookie=fixture(True)
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        url=f'http://127.0.0.1:{port}';pidfile=root/'server.pid'
        launch=f"import os; from pathlib import Path; Path({str(pidfile)!r}).write_text(str(os.getpid())); import uvicorn; uvicorn.run('backend.api:app',host='127.0.0.1',port={port},access_log=False)"
        with (root/'server.log').open('w',encoding='utf-8') as log:
            process=subprocess.Popen([sys.executable,'-c',launch],stdout=log,stderr=log,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            try:
                with httpx.Client(base_url=url,trust_env=False,timeout=2) as client:
                    for _ in range(100):
                        if process.poll() is not None: raise RuntimeError('Synthetic server stopped.')
                        try:
                            if client.get('/api/health').status_code==200:break
                        except httpx.HTTPError:pass
                        time.sleep(.1)
                    else:raise RuntimeError('Synthetic server did not start.')
                yield url,identities,counts,cookie
            finally:
                if pidfile.exists():
                    child=int(pidfile.read_text())
                    if child!=process.pid and process.poll() is None:stop_child(child)
                if process.poll() is None:process.terminate()
                try:process.wait(timeout=10)
                except subprocess.TimeoutExpired:process.kill();process.wait(timeout=10)
                if previous is None:os.environ.pop('TIMETABLE_DB',None)
                else:os.environ['TIMETABLE_DB']=previous
