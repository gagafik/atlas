"""Synthetic canteen load. Run as a fresh process; never opens real school data."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from time import perf_counter
import json, os, secrets, statistics, tempfile


def main():
    with tempfile.TemporaryDirectory(prefix='atlas-canteen-bench-') as directory:
        target=Path(directory)/'benchmark.db'
        os.environ.update(TIMETABLE_DB=str(target),TIMETABLE_REQUIRE_AUTH='1',ATLAS_WEB_PUSH_ENABLED='0',ATLAS_BACKUP_DIR='')
        from backend import api, canteen
        from backend.access import now, password_hash, digest, COOKIE
        from tools.create_admin import create_first_admin
        from fastapi.testclient import TestClient
        if api.DB.resolve()!=target.resolve():raise RuntimeError('Use a fresh process with the isolated database.')
        password='Synthetic-Canteen-Benchmark!';create_first_admin('bench-admin','Synthetic administrator',password)
        state=api.read('demo');sample=state.students[0]
        state.students=[sample.model_copy(update=dict(id=f's{i}',name=f'Synthetic pupil {i:04d}')) for i in range(1100)]
        api.save('demo',state);before=api.read('demo').model_dump_json();api.canteen.prepare();stamp=now().isoformat();day=canteen.today();hashed=password_hash(password)
        with api.connect() as db:
            for i in range(1100):
                for parent in range(2):
                    pid=f'parent-{i}-{parent}';perms=json.dumps(dict(workspaces=['demo'],grants=[dict(workspace='demo',view='student',entityIds=[f's{i}'])]))
                    db.execute('INSERT INTO accounts VALUES (?,?,?,?,1,1,?,?)',(pid,pid,f'Synthetic parent {i} {parent}','parent',hashed,perms))
            for i in range(120):
                db.execute('INSERT INTO accounts VALUES (?,?,?,?,1,1,?,?)',(f'teacher-{i}',f'teacher-{i}',f'Synthetic teacher {i}','teacher',hashed,json.dumps(dict(workspaces=['demo'],grants=[]))))
            variant=dict(id='standard',title='Synthetic lunch',dishes='Soup, rice',allergens=[],notes='',classIds=[],price=0,capacity=2000)
            for n in range(30):
                mealday=(day+timedelta(days=n)).isoformat();mid=f'menu-{n}'
                definition=dict(day=mealday,meal='lunch',title='Synthetic lunch',starts='12:00',ends='14:00',opensAt=stamp,orderClosesAt=stamp,cancelClosesAt=stamp,classIds=[],notice='',variants=[variant])
                db.execute('INSERT INTO canteen_menus(id,workspace,version,status,day,meal,currency,decimals,created,document) VALUES (?,\'demo\',2,\'published\',?,\'lunch\',\'KZT\',2,?,?)',(mid,mealday,stamp,json.dumps(definition)))
                for i in range(1100):
                    doc=dict(studentName=f'Synthetic pupil {i:04d}',className='Synthetic class',classId=sample.classId,variant=variant,menuTitle='Synthetic lunch',currency='KZT',decimals=2,starts='12:00',ends='14:00',cancelClosesAt=stamp)
                    db.execute('INSERT INTO canteen_orders(id,workspace,menu_id,student_id,version,status,day,meal,variant_id,created,updated,served_at,charge_id,document) VALUES (?,\'demo\',?,?,1,\'ordered\',?,\'lunch\',\'standard\',?,?,NULL,NULL,?)',(f'order-{n}-{i}',mid,f's{i}',mealday,stamp,stamp,json.dumps(doc)))
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
                for part in ['options','menus','orders']:
                    t=perf_counter();r=c.get('/api/canteen/'+part+'?'+period);results.append(((perf_counter()-t)*1000,r.status_code,len(r.content)));r.raise_for_status()
                    if part=='orders':assert r.json()['totalCount']==30 and all(x['student_id']==f's{i}' for x in r.json()['items'])
            return results
        samples=[];started=perf_counter()
        with ThreadPoolExecutor(32) as pool:
            for result in pool.map(flow,enumerate(clients)):samples.extend(result)
        elapsed=perf_counter()-started;staff={}
        for route in ['options','orders','report','report/export']:
            t=perf_counter();r=admin.get('/api/canteen/'+route+'?'+period);r.raise_for_status();staff[route]=round((perf_counter()-t)*1000,2)
        assert api.read('demo').model_dump_json()==before
        for c in [admin,*clients]:c.close()
        times=[x[0] for x in samples]
        print(json.dumps(dict(scope='In-process TestClient signed-in reads; no TCP/TLS/browser/login burst or sustained server capacity claim. Other checks may run concurrently.',students=1100,parents=2200,teachers=120,menus=30,orders=33000,clients=64,parallelFlows=32,requests=len(samples),errors=sum(x[1]!=200 for x in samples),p50Ms=round(statistics.median(times),2),p95Ms=round(sorted(times)[int(len(times)*.95)],2),maxMs=round(max(times),2),elapsedSeconds=round(elapsed,2),maxResponseBytes=max(x[2] for x in samples),staffMs=staff,schoolStateUnchanged=True),indent=2))


if __name__=='__main__':main()
