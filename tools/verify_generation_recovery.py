"""Crash and restart an isolated API with real queued solvers; never touch school data.

Run: python -m tools.verify_generation_recovery --output data/feature-qa/queue-recovery.json
"""
import argparse
from contextlib import closing
import json
import os
from pathlib import Path
import signal
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time

import httpx
from .benchmark_portal import fixture, stop_child


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',required=True)
    args=parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='atlas-generation-recovery-') as directory:
        root=Path(directory)
        database=root/'synthetic.sqlite'
        os.environ['TIMETABLE_DB']=str(database)
        _,counts,cookie=fixture(True)
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        url=f'http://127.0.0.1:{port}'
        def state():
            with closing(sqlite3.connect(database)) as db:
                return db.execute("SELECT document FROM workspaces WHERE id='school'").fetchone()[0]
        def stored(id):
            with closing(sqlite3.connect(database)) as db:
                return json.loads(db.execute('SELECT document FROM generation_jobs WHERE id=?',(id,)).fetchone()[0])
        before=state()
        server=None;pidfile=None;log=None
        def launch(number):
            nonlocal server,pidfile,log
            pidfile=root/f'server-{number}.pid'
            logpath=root/f'server-{number}.log'
            log=logpath.open('w',encoding='utf-8')
            command=f"import os; from pathlib import Path; Path({str(pidfile)!r}).write_text(str(os.getpid())); import uvicorn; uvicorn.run('backend.api:app',host='127.0.0.1',port={port},access_log=False)"
            server=subprocess.Popen([sys.executable,'-c',command],stdout=log,stderr=log,
                                    creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            with httpx.Client(trust_env=False,timeout=2) as client:
                for _ in range(150):
                    if server.poll() is not None: raise RuntimeError(logpath.read_text(encoding='utf-8'))
                    try:
                        if client.get(url+'/api/health').status_code==200: return
                    except httpx.HTTPError: pass
                    time.sleep(.1)
            raise RuntimeError('Isolated API did not start')
        def stop(crash=False):
            nonlocal server,log
            if server is not None:
                if pidfile.exists():
                    pid=int(pidfile.read_text())
                    if crash and sys.platform!='win32':
                        try: os.kill(pid,signal.SIGKILL)
                        except ProcessLookupError: pass
                    else: stop_child(pid)
                if server.poll() is None:
                    try: server.wait(timeout=10)
                    except subprocess.TimeoutExpired: server.terminate();server.wait(timeout=10)
                server=None
            if log: log.close();log=None
        try:
            launch(1)
            with httpx.Client(base_url=url,trust_env=False,timeout=30,
                              headers={'Cookie':cookie,'X-Atlas-Request':'1'}) as client:
                requests=[]
                for _ in range(2):
                    response=client.post('/api/generation-jobs',json=dict(revision=0,seconds=10))
                    response.raise_for_status();requests.append(response.json()['id'])
            deadline=time.monotonic()+30
            while time.monotonic()<deadline:
                running=stored(requests[0])
                if running['status']=='running' and running['progress'].get('execution')=='process': break
                if running['status'] not in ['running','queued']:
                    raise RuntimeError('First search finished before crash could be exercised')
                time.sleep(.02)
            else: raise RuntimeError('No real child solver observed')
            assert stored(requests[1])['status']=='queued'
            phase_at_crash=running['progress'].get('phase')
            stop(crash=True)
            assert stored(requests[0])['status']=='running'
            assert state()==before
            began=time.monotonic()
            launch(2)
            # Observe the DB, not the jobs endpoint: recovery must start from lifespan.
            deadline=time.monotonic()+65
            while time.monotonic()<deadline:
                results=[stored(id) for id in requests]
                if all(r['status'] not in ['queued','running','cancelling'] for r in results): break
                time.sleep(.1)
            else: raise RuntimeError('Queue did not recover after the API restart')
            assert [r['status'] for r in results]==['ready','ready'],results
            assert [r['attempts'] for r in results]==[2,1]
            assert [r['restarts'] for r in results]==[1,0]
            assert all(r['result']['preview']['canApply'] for r in results)
            with closing(sqlite3.connect(database)) as db:
                previews=db.execute('SELECT count(*) FROM previews').fetchone()[0]
                lease=db.execute('SELECT token FROM generation_owner WHERE slot=1').fetchone()[0]
            assert previews==2 and lease is None
            assert state()==before
            report=dict(synthetic=counts,crashedWhile=phase_at_crash,recoverySeconds=round(time.monotonic()-began,2),
                        statuses=[r['status'] for r in results],attempts=[r['attempts'] for r in results],
                        restarts=[r['restarts'] for r in results],previews=previews,sourceUnchanged=True,
                        recoveredWithoutJobsPolling=True,leaseReleased=True)
        finally: stop()
    report['temporaryDatabaseRemoved']=True
    Path(args.output).parent.mkdir(parents=True,exist_ok=True)
    Path(args.output).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report),flush=True)


if __name__=='__main__': main()
