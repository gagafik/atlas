"""Bounded synthetic financial workload; never opens the school database."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from time import perf_counter
from uuid import uuid4
import json,os,secrets,statistics,tempfile

def main():
    with tempfile.TemporaryDirectory(prefix='atlas-payments-bench-') as directory:
        target=Path(directory)/'benchmark.db'
        os.environ.update(TIMETABLE_DB=str(target),TIMETABLE_REQUIRE_AUTH='1',ATLAS_WEB_PUSH_ENABLED='0',ATLAS_BACKUP_DIR='')
        from backend import api,account_grants
        from backend.access import now,password_hash,digest,COOKIE
        from backend.notifications import permission_key
        from backend.payments import today
        from tools.create_admin import create_first_admin
        from fastapi.testclient import TestClient
        if api.DB.resolve()!=target.resolve():raise RuntimeError('Run in a fresh process with the isolated database.')
        password='Synthetic-Payments-Benchmark!';create_first_admin('bench-admin','Synthetic administrator',password)
        state=api.read('demo');sample=state.students[0]
        state.students=[sample.model_copy(update=dict(id=f's{i}',name=f'Synthetic pupil {i:04d}')) for i in range(1100)]
        api.save('demo',state);before=api.read('demo').model_dump_json();api.payments.prepare();stamp=now().isoformat();day=today().isoformat();hashed=password_hash(password)
        with api.connect() as db:
            account_grants.tables(db)
            for i in range(1100):
                sid=f's{i}';payers=[]
                for parent in range(2):
                    pid=f'parent-{i}-{parent}';perms=json.dumps(dict(workspaces=['demo'],grants=[dict(workspace='demo',view='student',entityIds=[sid])]))
                    db.execute('INSERT INTO accounts VALUES (?,?,?,?,1,1,?,?)',(pid,pid,f'Synthetic parent {i} {parent}','parent',hashed,perms))
                    user=api.access.public(db.execute('SELECT * FROM accounts WHERE id=?',(pid,)).fetchone());payers.append(dict(id=pid,permissions=permission_key(user)))
                db.execute('INSERT INTO finance_accounts VALUES (?,?,1,?,?,?,NULL)',('demo',sid,f'AT-BENCH-{i}',f'Synthetic pupil {i:04d}',json.dumps(payers)))
                for n in range(12):
                    cid=f'charge-{i}-{n}';db.execute('INSERT INTO finance_charges(id,workspace,student_id,plan_id,installment,title,due,created,book_date,document) VALUES (?,\'demo\',?,NULL,NULL,?,?,?,?,?)',(cid,sid,f'Synthetic fee {n}',day,stamp,day,'{}'))
                    db.execute('INSERT INTO finance_entries(id,workspace,student_id,created,book_date,kind,amount,charge_id,receipt_id,label,reference,actor_id) VALUES (?,\'demo\',?,?,?,\'charge\',1000000,?,NULL,\'Synthetic fee\',\'\',\'benchmark\')',(cid,sid,stamp,day,cid))
                rid=f'receipt-{i}';db.execute('INSERT INTO finance_receipts(id,workspace,created,paid_date,method,reference,reference_key,payer,reason) VALUES (?,\'demo\',?,?,\'bank\',?,?,\'Synthetic parent\',\'Benchmark\')',(rid,stamp,day,rid,rid))
                db.execute('INSERT INTO finance_entries(id,workspace,student_id,created,book_date,kind,amount,charge_id,receipt_id,label,reference,actor_id) VALUES (?,\'demo\',?,?,?,\'receipt\',-10000000,NULL,?,\'Synthetic receipt\',\'\',\'benchmark\')',(rid,sid,stamp,day,rid))
                for n in range(10):db.execute('INSERT INTO finance_allocations(id,workspace,student_id,charge_id,receipt_id,amount,reversal_of,created,book_date,reason,actor_id) VALUES (?,\'demo\',?,?,?,1000000,NULL,?,?,\'Benchmark\',\'benchmark\')',(f'alloc-{i}-{n}',sid,f'charge-{i}-{n}',rid,stamp,day))
        clients=[]
        with api.connect() as db:
            for i in range(64):
                token=secrets.token_urlsafe(32);db.execute('INSERT INTO sessions VALUES (?,?,?,?)',(digest(token),f'parent-{i}-0',stamp,(now()+timedelta(hours=12)).isoformat()))
                c=TestClient(api.app,headers={'X-Atlas-Request':'1'});c.cookies.set(COOKIE,token);clients.append(c)
        admin=TestClient(api.app,headers={'X-Atlas-Request':'1'});admin.post('/api/auth/login',json=dict(login='bench-admin',password=password)).raise_for_status()
        def flow(pair):
            i,c=pair;results=[]
            for _ in range(2):
                for part in ['options','accounts',f'accounts/s{i}']:
                    t=perf_counter();r=c.get('/api/payments/'+part+'?workspace=demo');results.append(((perf_counter()-t)*1000,r.status_code,len(r.content)));r.raise_for_status()
                    if part==f'accounts/s{i}':assert r.json()['summary']['balance']=='2000000'
            return results
        started=perf_counter();samples=[]
        with ThreadPoolExecutor(32) as pool:
            for result in pool.map(flow,enumerate(clients)):samples.extend(result)
        elapsed=perf_counter()-started;staff={}
        for route in ['options','accounts?offset=900','report','report/export']:
            t=perf_counter();r=admin.get('/api/payments/'+route+('&' if '?' in route else '?')+'workspace=demo');r.raise_for_status();staff[route]=round((perf_counter()-t)*1000,2)
        t=perf_counter();r=admin.post('/api/payments/accounts/s0/charges?workspace=demo',json=dict(requestId=str(uuid4()),version=1,title='Synthetic extra fee',amount=100,due=day,reason='Benchmark'));r.raise_for_status();write_ms=(perf_counter()-t)*1000
        assert api.read('demo').model_dump_json()==before
        for c in [admin,*clients]:c.close()
        times=[x[0] for x in samples]
        print(json.dumps(dict(scope='In-process TestClient, signed-in users; no TCP/TLS/browser/login burst or sustained server load. Other checks may run concurrently.',students=1100,parents=2200,charges=13200,receipts=1100,allocations=11000,clients=64,parallelFlows=32,requests=len(samples),errors=sum(x[1]!=200 for x in samples),p50Ms=round(statistics.median(times),2),p95Ms=round(sorted(times)[int(len(times)*.95)],2),maxMs=round(max(times),2),elapsedSeconds=round(elapsed,2),maxResponseBytes=max(x[2] for x in samples),staffMs=staff,chargeWithNotificationsMs=round(write_ms,2),schoolStateUnchanged=True),indent=2))

if __name__=='__main__':main()
