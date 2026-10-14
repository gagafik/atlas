"""Isolated HTTP read-load probe; all people/data are synthetic, never school DB.

Run from the project root: python -m tools.benchmark_portal --output data/portal-load.json
Creates a temporary database and a child API server bound only to loopback.
Session hashing/login storms, writes, uploads and generation are NOT measured.
"""
import argparse
import asyncio
from collections import Counter
from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
import math
import os
from pathlib import Path
import platform
import secrets
import signal
import socket
import subprocess
import sys
import tempfile
import time

import httpx


def stop_child(pid):
    if sys.platform=='win32':
        import ctypes
        from ctypes import wintypes
        kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
        kernel.OpenProcess.restype=wintypes.HANDLE
        kernel.TerminateProcess.argtypes=[wintypes.HANDLE,wintypes.UINT]
        kernel.WaitForSingleObject.argtypes=[wintypes.HANDLE,wintypes.DWORD]
        kernel.CloseHandle.argtypes=[wintypes.HANDLE]
        handle=kernel.OpenProcess(0x100001,False,pid)  # SYNCHRONIZE | PROCESS_TERMINATE
        if handle:
            try:
                kernel.TerminateProcess(handle,0)
                if kernel.WaitForSingleObject(handle,10000)!=0: raise RuntimeError('Test server did not stop.')
            finally: kernel.CloseHandle(handle)
    else:
        try: os.kill(pid,signal.SIGTERM)
        except ProcessLookupError: pass


def fixture(with_generation=False):
    # Import only after TIMETABLE_DB points at the isolated temporary database.
    from backend import api
    from backend.models import State
    from backend.access import digest, COOKIE
    from backend.notifications import permission_key
    profiles = [dict(id='primary', name='Test bells', periods=[
        dict(label=str(i+1), start=f'{8+i:02d}:00', end=f'{8+i:02d}:45') for i in range(10)])]
    classes = [dict(id=f'c{i}', name=f'Class {i}', maxDaily=10, studentCount=20) for i in range(55)]
    groups = [dict(id=f'g{i}', name=f'Class {i}', classIds=[f'c{i}']) for i in range(55)]
    teachers = [dict(id=f't{i}', name=f'Teacher {i}') for i in range(120)]
    students = [dict(id=f's{i}', name=f'Pupil {i}', classId=f'c{i//20}') for i in range(1100)]
    rooms = [dict(id=f'r{i}', name=f'Room {i}', capacity=25) for i in range(55)]
    subjects = [dict(id=f'b{i}', name=f'Subject {i}') for i in range(10)]
    loads, placements = [], []
    for c in range(55):
        for slot in range(10 if c>=40 else 8):
            wid = f'w{c}-{slot}'
            loads.append(dict(id=wid, groupId=f'g{c}', subjectId=f'b{slot}', teacherId=f't{(c+slot*11)%120}', hours=5, roomIds=[f'r{c}']))
            placements.extend(dict(id=f'{wid}-{day}',workloadId=wid,day=day,period=slot,roomId=f'r{c}') for day in range(5))
    state = State.model_validate(dict(classes=classes,groups=groups,students=students,teachers=teachers,
                                     rooms=rooms,subjects=subjects,profiles=profiles,workloads=loads,placements=placements))
    accounts, sessions, notices, identities = [], [], [], []
    created = datetime.now(timezone.utc)
    for role, count in [('student',1100),('parent',2200),('teacher',120),('viewer',30)]:
        for i in range(count):
            uid=f'{role}{i}'; raw=secrets.token_urlsafe(32)
            view='teacher' if role=='teacher' else 'class' if role=='viewer' else 'student'
            entity=f't{i}' if role=='teacher' else f'c{i}' if role=='viewer' else f's{i%1100}'
            permissions=dict(workspaces=[],grants=[dict(workspace='school',view=view,entityIds=[entity])])
            user=dict(id=uid,name=uid,role=role,**permissions)
            # No test passwords/login endpoint: benchmarking existing sessions only.
            accounts.append((uid,uid,uid,role,1,1,api.access.dummy,json.dumps(permissions)))
            sessions.append((digest(raw),uid,created.isoformat(),(created+timedelta(hours=1)).isoformat()))
            notices.extend((uid,permission_key(user),'school',0,created.isoformat(),'saved',None) for _ in range(10))
            identities.append((f'{COOKIE}={raw}',view,entity))
    admin_cookie = None
    if with_generation:
        raw=secrets.token_urlsafe(32)
        permissions=json.dumps(dict(workspaces=['school'],grants=[]))
        accounts.append(('load-admin','load-admin','Load admin','admin',1,1,api.access.dummy,permissions))
        sessions.append((digest(raw),'load-admin',created.isoformat(),(created+timedelta(hours=1)).isoformat()))
        admin_cookie=f'{COOKIE}={raw}'
    with closing(api.connect()) as db, db:
        api.access.tables(db); api.notifications.tables(db)
        db.execute('INSERT INTO workspaces VALUES (?,?,?)',('school',state.revision,state.model_dump_json()))
        db.executemany('INSERT INTO accounts VALUES (?,?,?,?,?,?,?,?)',accounts)
        db.executemany('INSERT INTO sessions VALUES (?,?,?,?)',sessions)
        db.executemany('INSERT INTO notifications (user_id,permissions,workspace,revision,created,action,read_at) VALUES (?,?,?,?,?,?,?)',notices)
    return identities, dict(accounts=len(accounts),students=1100,parents=2200,teachers=120,staff=30,
                            classes=55,placements=len(placements),notifications=len(notices),testAdministrators=int(with_generation)), admin_cookie


async def probe(url, identities, concurrency, seconds):
    latencies, statuses, by_endpoint = [], Counter(), {}
    limits=httpx.Limits(max_connections=concurrency,max_keepalive_connections=concurrency)
    async with httpx.AsyncClient(base_url=url,timeout=60,limits=limits,trust_env=False) as client:
        start=time.perf_counter(); deadline=start+seconds
        async def worker(index):
            n=0
            while time.perf_counter()<deadline:
                cookie,view,entity=identities[(index+n*concurrency)%len(identities)]
                choice=(index+n)%10
                if choice<6:
                    endpoint='schedule'
                    path=f'/api/portal/schedule?view={view}&entityId={entity}&day=2026-10-13'
                else:
                    endpoint=['options','inbox','inbox','auth'][choice-6]
                    path={'options':'/api/portal/options','inbox':'/api/notifications/inbox','auth':'/api/auth/status'}[endpoint]
                began=time.perf_counter()
                try:
                    response=await client.get(path,headers={'Cookie':cookie})
                    statuses[str(response.status_code)]+=1
                    if response.status_code==200 and endpoint=='schedule':
                        # Own name only; no pupil roster/source data in responses.
                        data=response.json()
                        expected=('Teacher ' if view=='teacher' else 'Class ' if view=='class' else 'Pupil ')+entity[1:]
                        if data.get('name')!=expected or any(key in response.text for key in ['studentIds','sourceCells']):
                            statuses['projection_error']+=1
                except httpx.HTTPError:
                    statuses['transport_error']+=1
                elapsed=(time.perf_counter()-began)*1000
                latencies.append(elapsed); by_endpoint.setdefault(endpoint,[]).append(elapsed)
                n+=1
        await asyncio.gather(*(worker(i) for i in range(concurrency)))
        elapsed=time.perf_counter()-start
    def summary(values):
        ordered=sorted(values)
        return dict(count=len(values),p50_ms=round(ordered[math.ceil(len(values)*.50)-1],1),
                    p95_ms=round(ordered[math.ceil(len(values)*.95)-1],1),max_ms=round(ordered[-1],1))
    return dict(concurrency=concurrency,duration_s=round(elapsed,2),requests_per_second=round(len(latencies)/elapsed,2),
                statuses=dict(statuses),**summary(latencies),endpoints={k:summary(v) for k,v in by_endpoint.items()})


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',required=True)
    parser.add_argument('--concurrency',default='20,100')
    parser.add_argument('--seconds',type=int,default=12)
    parser.add_argument('--generation',action='store_true',help='Also run a 30-second search on the synthetic school; never apply the result.')
    args=parser.parse_args()
    levels=[int(v) for v in args.concurrency.split(',')]
    if any(v<1 or v>500 for v in levels) or not 1<=args.seconds<=300: parser.error('Use 1..500 concurrent requests and 1..300 seconds.')
    with tempfile.TemporaryDirectory(prefix='atlas-portal-load-') as directory:
        os.environ['TIMETABLE_DB']=str(Path(directory)/'synthetic.sqlite')
        identities, counts, admin_cookie=fixture(args.generation)
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0)); port=sock.getsockname()[1]
        url=f'http://127.0.0.1:{port}'
        log=open(Path(directory)/'server.log','w',encoding='utf-8')
        # Windows venv python.exe can launch a second python process. Record the
        # actual server PID to stop precisely that child before cleaning its DB.
        pidfile=Path(directory)/'server.pid'
        launch=f"import os; from pathlib import Path; Path({str(pidfile)!r}).write_text(str(os.getpid())); import uvicorn; uvicorn.run('backend.api:app',host='127.0.0.1',port={port},access_log=False)"
        server=subprocess.Popen([sys.executable,'-c',launch],
                                stdout=log,stderr=log,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        try:
            with httpx.Client(trust_env=False,timeout=2) as client:
                for _ in range(100):
                    if server.poll() is not None: raise RuntimeError('Test server exited: '+Path(directory,'server.log').read_text())
                    try:
                        if client.get(url+'/api/health').status_code==200: break
                    except httpx.HTTPError: pass
                    time.sleep(.1)
                else: raise RuntimeError('Test server did not start.')
            generation=None
            if args.generation:
                with httpx.Client(base_url=url,trust_env=False,timeout=30,headers={'Cookie':admin_cookie,'X-Atlas-Request':'1'}) as client:
                    before_generation=client.get('/api/state').json()
                    response=client.post('/api/generation-jobs',json=dict(revision=0,seconds=30))
                    response.raise_for_status(); job_id=response.json()['id']
                    generation=dict(id=job_id,search_seconds=30,status_at_start=response.json()['status'])
            results=[]
            for level in levels:
                result=asyncio.run(probe(url,identities,level,args.seconds)); results.append(result)
                print(json.dumps(result),flush=True)
            if generation is not None:
                with httpx.Client(base_url=url,trust_env=False,timeout=30,headers={'Cookie':admin_cookie,'X-Atlas-Request':'1'}) as client:
                    status=client.get('/api/generation-jobs/'+job_id).json()
                    generation.update(status_after_load=status['status'],progress=status.get('progress'),elapsed=status.get('elapsed'))
                    if status['status'] in ['queued','running','cancelling']:
                        client.post('/api/generation-jobs/'+job_id+'/cancel').raise_for_status()
                        for _ in range(100):
                            status=client.get('/api/generation-jobs/'+job_id).json()
                            if status['status'] not in ['queued','running','cancelling']: break
                            time.sleep(.1)
                    generation['final_status']=status['status']
                    # A search/preview is never allowed to overwrite the input.
                    state=client.get('/api/state').json()
                    assert state==before_generation
                    generation['state_unchanged']=True
                    print(json.dumps(dict(generation=generation)),flush=True)
            report=dict(created=datetime.now(timezone.utc).isoformat(),synthetic=True,workload='60% dated schedule, 10% options, 20% inbox, 10% auth; closed-loop, no think time',
                        limitations='Read-only, no login hashing, generation, journal writes, attachments, TLS or network latency; client and server share a laptop. Not a production capacity claim.',
                        platform=platform.platform(),logical_cpus=os.cpu_count(),data=counts,runs=results,generation=generation)
            output=Path(args.output); output.parent.mkdir(parents=True,exist_ok=True)
            output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        finally:
            if pidfile.exists():
                child_pid=int(pidfile.read_text())
                if child_pid!=server.pid and server.poll() is None:
                    stop_child(child_pid)
            if server.poll() is None: server.terminate()
            try: server.wait(timeout=10)
            except subprocess.TimeoutExpired: server.kill(); server.wait(timeout=10)
            log.close()


if __name__=='__main__': main()
