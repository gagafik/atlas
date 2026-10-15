"""Synthetic signed-in handover reads; isolated database, no school data."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from time import perf_counter
import json, os, secrets, statistics, tempfile


def main():
    with tempfile.TemporaryDirectory(prefix='atlas-handover-bench-') as directory:
        target=Path(directory)/'benchmark.db'
        os.environ.update(TIMETABLE_DB=str(target),TIMETABLE_REQUIRE_AUTH='1',ATLAS_WEB_PUSH_ENABLED='0',ATLAS_BACKUP_DIR='')
        from backend import api, handover
        from backend.access import now, password_hash, digest, COOKIE
        from backend.notifications import permission_key
        from tools.create_admin import create_first_admin
        from fastapi.testclient import TestClient
        if api.DB.resolve()!=target.resolve():raise RuntimeError('Use a fresh process with the isolated database.')
        password='Synthetic-Handover-Benchmark!';create_first_admin('bench-admin','Synthetic administrator',password)
        state=api.read('demo');sample=state.students[0]
        state.students=[sample.model_copy(update=dict(id=f's{i}',name=f'Synthetic pupil {i:04d}')) for i in range(1100)]
        api.save('demo',state);before=api.read('demo').model_dump_json();api.handover.prepare();stamp=now().isoformat();day=handover.today();hashed=password_hash(password)
        with api.connect() as db:
            for i in range(1100):
                for parent in range(2):
                    pid=f'parent-{i}-{parent}';perms=json.dumps(dict(workspaces=['demo'],grants=[dict(workspace='demo',view='student',entityIds=[f's{i}'])]))
                    db.execute('INSERT INTO accounts VALUES (?,?,?,?,1,1,?,?)',(pid,pid,f'Synthetic parent {i} {parent}','parent',hashed,perms))
            for i in range(120):
                db.execute('INSERT INTO accounts VALUES (?,?,?,?,1,1,?,?)',(f'teacher-{i}',f'teacher-{i}',f'Synthetic teacher {i}','teacher',hashed,json.dumps(dict(workspaces=['demo'],grants=[]))))
            cfg=dict(enabled=True,parentRequests=True,advanceDays=30,policy='Synthetic school policy for checked physical handover.',locations=[dict(id='main',name='Synthetic exit')],delegates=[])
            db.execute("INSERT INTO handover_config VALUES ('demo',1,?)",(json.dumps(cfg),))
            for i in range(1100):
                pid=f'parent-{i}-0';u=api.access.public(db.execute('SELECT * FROM accounts WHERE id=?',(pid,)).fetchone());permissions=permission_key(u)
                adult=dict(kind='parent',name=u['name'],relationship='Parent',contact='',startDate=day.isoformat(),endDate=(day+timedelta(days=365)).isoformat(),studentName=f'Synthetic pupil {i:04d}',classId=sample.classId,className='Synthetic class',sponsorName=u['name'],consentConfirmed=True)
                db.execute("INSERT INTO handover_adults(id,workspace,student_id,sponsor_id,sponsor_permissions,version,status,created,updated,document) VALUES (?,'demo',?,?,?,2,'approved',?,?,?)",(f'adult-{i}',f's{i}',pid,permissions,stamp,stamp,json.dumps(adult)))
                for n in range(30):
                    date=(day+timedelta(days=n)).isoformat();id=f'pickup-{n}-{i}';doc=dict(studentName=f'Synthetic pupil {i:04d}',className='Synthetic class',classId=sample.classId,adultName=u['name'],relationship='Parent',locationName='Synthetic exit',note='')
                    db.execute("INSERT INTO handover_pickups(id,workspace,student_id,adult_id,adult_version,creator_id,creator_permissions,version,status,day,expected_time,location_id,created,updated,document) VALUES (?,'demo',?,?,2,?,?,1,'requested',?,'17:00','main',?,?,?)",(id,f's{i}',f'adult-{i}',pid,permissions,date,stamp,stamp,json.dumps(doc)))
                    db.execute("INSERT INTO handover_history(workspace,student_id,adult_id,pickup_id,created,actor_id,actor_name,action,reason,document) VALUES ('demo',?,?,?,?,?,?,'requested','','{}')",(f's{i}',f'adult-{i}',id,stamp,pid,u['name']))
        clients=[]
        with api.connect() as db:
            for i in range(64):
                token=secrets.token_urlsafe(32);db.execute('INSERT INTO sessions VALUES (?,?,?,?)',(digest(token),f'parent-{i}-0',stamp,(now()+timedelta(hours=12)).isoformat()))
                c=TestClient(api.app,headers={'X-Atlas-Request':'1'});c.cookies.set(COOKIE,token);clients.append(c)
        admin=TestClient(api.app,headers={'X-Atlas-Request':'1'});admin.post('/api/auth/login',json=dict(login='bench-admin',password=password)).raise_for_status()
        period=f'workspace=demo&start={day.isoformat()}&end={(day+timedelta(days=29)).isoformat()}'
        def flow(pair):
            i,c=pair;results=[]
            for _ in range(2):
                for part in ['options','adults','pickups','history']:
                    t=perf_counter();r=c.get('/api/handover/'+part+'?'+period);results.append(((perf_counter()-t)*1000,r.status_code,len(r.content)));r.raise_for_status()
                    if part=='pickups':assert r.json()['totalCount']==30 and all(x['student_id']==f's{i}' for x in r.json()['items'])
                    if part=='adults':assert r.json()['totalCount']==1
                    if part=='history':assert len(r.json()['items'])==30 and all(x['student_id']==f's{i}' for x in r.json()['items'])
            return results
        samples=[];started=perf_counter()
        with ThreadPoolExecutor(32) as pool:
            for result in pool.map(flow,enumerate(clients)):samples.extend(result)
        elapsed=perf_counter()-started;staff={};sizes={}
        for route in ['options','adults','pickups','summary','pickups/export','history']:
            t=perf_counter();r=admin.get('/api/handover/'+route+'?'+period);r.raise_for_status();staff[route]=round((perf_counter()-t)*1000,2);sizes[route]=len(r.content)
        assert api.read('demo').model_dump_json()==before
        for c in [admin,*clients]:c.close()
        times=[x[0] for x in samples]
        print(json.dumps(dict(scope='In-process TestClient signed-in reads; no TCP/TLS/browser/login burst or sustained server capacity claim. Other checks may run concurrently.',students=1100,parents=2200,teachers=120,adults=1100,pickups=33000,history=33000,clients=64,parallelFlows=32,requests=len(samples),errors=sum(x[1]!=200 for x in samples),p50Ms=round(statistics.median(times),2),p95Ms=round(sorted(times)[int(len(times)*.95)],2),maxMs=round(max(times),2),elapsedSeconds=round(elapsed,2),maxResponseBytes=max(x[2] for x in samples),staffMs=staff,staffBytes=sizes,schoolStateUnchanged=True),indent=2))


if __name__=='__main__':main()
