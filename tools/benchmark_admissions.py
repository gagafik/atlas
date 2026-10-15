"""Synthetic admissions load in a temporary database; no real school data."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from time import perf_counter
from uuid import uuid4
import json, os, secrets, statistics, tempfile

def main():
    with tempfile.TemporaryDirectory(prefix='atlas-admissions-bench-') as directory:
        target=Path(directory)/'benchmark.db'
        os.environ.update(TIMETABLE_DB=str(target),TIMETABLE_REQUIRE_AUTH='1',ATLAS_WEB_PUSH_ENABLED='0',ATLAS_BACKUP_DIR='')
        from backend import api
        from backend.access import now, password_hash, digest
        from backend.admission_models import ApplicationData
        from backend.admissions import COOKIE
        from tools.create_admin import create_first_admin
        from fastapi.testclient import TestClient
        if api.DB.resolve()!=target.resolve():raise RuntimeError('Run in a fresh process with an isolated database.')
        password='Synthetic-Admissions-Benchmark!';create_first_admin('bench-admin','Benchmark admin',password)
        before=api.read('demo').model_dump_json();api.admissions.prepare();stamp=now().isoformat();hashed=password_hash(password)
        config=dict(title='Synthetic admissions',year='2027/28',description='Benchmark',contact='Synthetic school',privacyNotice='Synthetic data only',grades=[1],documents=[],questions=[],opensAt=(now()-timedelta(days=1)).isoformat(),closesAt=(now()+timedelta(days=30)).isoformat())
        with api.connect() as db:
            db.execute("INSERT INTO admission_campaigns(id,workspace,version,status,published,created,document) VALUES ('campaign','demo',1,'open',1,?,?)",(stamp,json.dumps(config)))
            for i in range(5000):
                family=f'family-{i}';id=f'case-{i}';d=ApplicationData(childName=f'Synthetic child {i}',birthDate='2020-01-01',grade=1,guardianName=f'Family {i}',email=f'family-{i}@example.invalid',phone='555-0100',consent=True).model_dump_json()
                db.execute('INSERT INTO admission_families VALUES (?,?,?,?,?,1,?)',(family,family,family,hashed,digest(secrets.token_hex(24)),stamp))
                db.execute("INSERT INTO admission_applications(id,campaign_id,family_id,version,status,created,updated,document,submitted,submitted_at,search) VALUES (?,?,?,1,'submitted',?,?,?,?,?,?)",(id,'campaign',family,stamp,stamp,d,d,stamp,d.casefold()))
                db.execute("INSERT INTO admission_history(campaign_id,application_id,actor_id,actor_name,created,action,audience,message,document) VALUES ('campaign',?,?,?,?,'submitted','both','',?)",(id,family,family,stamp,d))
        clients=[]
        # Session creation is deliberately excluded; this measures already signed-in browsing.
        with api.connect() as db:
            for i in range(32):
                token=secrets.token_urlsafe(32);db.execute('INSERT INTO admission_sessions VALUES (?,?,?,?)',(digest(token),f'family-{i}',stamp,(now()+timedelta(hours=12)).isoformat()))
                c=TestClient(api.app,headers={'X-Atlas-Request':'1'});c.cookies.set(COOKIE,token,path='/admissions-api');clients.append(c)
        admin=TestClient(api.app,headers={'X-Atlas-Request':'1'});admin.post('/api/auth/login',json=dict(login='bench-admin',password=password)).raise_for_status()
        def flow(pair):
            i,c=pair;results=[]
            for _ in range(3):
                for path in ['applications',f'applications/case-{i}',f'applications/case-{i}/history']:
                    t=perf_counter();r=c.get('/admissions-api/'+path);results.append(((perf_counter()-t)*1000,r.status_code,len(r.content)));r.raise_for_status()
                    if path.endswith(f'case-{i}'):assert r.json()['data']['childName']==f'Synthetic child {i}'
            return results
        started=perf_counter();samples=[]
        with ThreadPoolExecutor(16) as pool:
            for result in pool.map(flow,enumerate(clients)):samples.extend(result)
        elapsed=perf_counter()-started;staff=[]
        for offset in [0,30,300,3000]:
            t=perf_counter();r=admin.get('/api/admissions/campaigns/campaign/applications',params=dict(workspace='demo',offset=offset));r.raise_for_status();staff.append((perf_counter()-t)*1000)
            assert len(r.json()['items'])==30
        t=perf_counter();r=admin.post('/api/admissions/applications/case-0/review?workspace=demo',json=dict(requestId=str(uuid4()),version=1,action='accepted',message='Synthetic decision'));r.raise_for_status();review=(perf_counter()-t)*1000
        assert api.read('demo').model_dump_json()==before
        for c in [admin,*clients]:c.close()
        times=[r[0] for r in samples]
        print(json.dumps(dict(scope='In-process TestClient; no TCP/TLS/browser/uploads/hash work; full tests may run concurrently',families=5000,applications=5000,clients=32,parallelFlows=16,requests=len(samples),errors=sum(r[1]!=200 for r in samples),p50Ms=round(statistics.median(times),2),p95Ms=round(sorted(times)[int(len(times)*.95)],2),maxMs=round(max(times),2),elapsedSeconds=round(elapsed,2),maxResponseBytes=max(r[2] for r in samples),staffPageMaxMs=round(max(staff),2),reviewMs=round(review,2),schoolStateUnchanged=True),indent=2))

if __name__=='__main__':main()
