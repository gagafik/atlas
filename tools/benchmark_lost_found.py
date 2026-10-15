"""Synthetic catalogue/claims benchmark. No real school database or network."""
from concurrent.futures import ThreadPoolExecutor
import json,os,statistics,tempfile
from pathlib import Path
from time import perf_counter
from uuid import uuid4

def main():
    with tempfile.TemporaryDirectory(prefix='atlas-lost-found-') as directory:
        target=Path(directory)/'benchmark.db';os.environ.update(TIMETABLE_DB=str(target),TIMETABLE_REQUIRE_AUTH='1',ATLAS_WEB_PUSH_ENABLED='0',ATLAS_BACKUP_DIR='')
        from backend import api
        from backend.access import password_hash
        from backend.lost_found import stamp,today
        from backend.notifications import permission_key
        from tools.create_admin import create_first_admin
        from fastapi.testclient import TestClient
        if api.DB.resolve()!=target.resolve():raise RuntimeError('Use a fresh process and isolated database.')
        password='Synthetic-lost-found-benchmark!';create_first_admin('admin','Synthetic administrator',password)
        state=api.read('demo');prototype=state.students[0];ids=[state.students[i].id if i<len(state.students) else f'pupil-{i}' for i in range(1100)]
        state.students=[prototype.model_copy(update=dict(id=ids[i],name=f'Synthetic pupil {i}')) for i in range(1100)];api.save('demo',state);before=api.read('demo').model_dump_json();api.lost_found.prepare();hashed=password_hash(password);time=stamp();day=today().isoformat();users=[]
        with api.connect() as db:
            for i in range(2200):
                id=f'parent-{i}';permissions=dict(workspaces=[],grants=[dict(workspace='demo',view='student',entityIds=[ids[i//2]])]);db.execute('INSERT INTO accounts VALUES (?,?,?,?,?,?,?,?)',(id,id,f'Synthetic parent {i}','parent',1,1,hashed,json.dumps(permissions)));users.append(dict(id=id,role='parent',**permissions))
            for i in range(120):
                id=f'staff-{i}';db.execute('INSERT INTO accounts VALUES (?,?,?,?,?,?,?,?)',(id,id,f'Synthetic staff {i}','staff',1,1,hashed,json.dumps(dict(workspaces=['demo'],grants=[]))))
            for i in range(10000):
                data=dict(title=f'Synthetic finding {i}',description='School object',category='other',foundOn=day,foundPlace='Lobby',privateNote='Private identifying mark',pickupInstructions='School office',photoIds=[])
                db.execute('INSERT INTO lost_found_items(id,workspace,version,status,created,updated,search,document) VALUES (?,?,1,?,?,?,?,?)',(f'item-{i}','demo','open',time,time,data['title'].casefold(),json.dumps(data)))
                if i<5000:
                    owner=i%2200;data=dict(proof='Private synthetic proof',claimantName=f'Parent {owner}',ownerName=f'Pupil {owner//2}',decision='',receivedBy='',reviewerName='')
                    db.execute('INSERT INTO lost_found_claims(id,workspace,item_id,user_id,permissions,student_id,version,status,created,updated,document) VALUES (?,?,?,?,?,?,1,?,?,?,?)',(f'claim-{i}','demo',f'item-{i}',users[owner]['id'],permission_key(users[owner]),ids[owner//2],'pending',time,time,json.dumps(data)))
        def session(login):
            c=TestClient(api.app,headers={'X-Atlas-Request':'1'});c.post('/api/auth/login',json=dict(login=login,password=password)).raise_for_status();return c
        clients=[session(f'parent-{i}') for i in range(32)];admin=session('admin')
        def request(c,method,path,**kw):
            start=perf_counter();r=getattr(c,method)('/api/lost-found/'+path,params=dict(workspace='demo'),**kw);elapsed=(perf_counter()-start)*1000;r.raise_for_status();return elapsed,r
        def flow(pair):
            i,c=pair;times=[]
            for _ in range(2):
                ms,r=request(c,'get','items');times.append(ms);assert len(r.json()['items'])==20 and 'privateNote' not in r.text
                ms,r=request(c,'get','claims');times.append(ms);assert r.json()['items'] and all(x['data']['claimantName']==f'Parent {i}' for x in r.json()['items'])
                ms,r=request(c,'get','claims/claim-'+str(i));times.append(ms);assert 'Private synthetic proof' in r.text
                ms,r=request(c,'get','export');times.append(ms);assert 'Private synthetic proof' not in r.text
            return times
        start=perf_counter();times=[]
        with ThreadPoolExecutor(16) as pool:
            for rows in pool.map(flow,enumerate(clients)):times.extend(rows)
        elapsed=perf_counter()-start
        approve_ms,r=request(admin,'post','claims/claim-0/action',json=dict(requestId=str(uuid4()),version=1,itemVersion=1,action='approve',reason='Checked identifying marks'));claim=r.json()
        collect_ms,r=request(admin,'post','claims/claim-0/action',json=dict(requestId=str(uuid4()),version=claim['version'],itemVersion=claim['item']['version'],action='collect',reason='Handed over in person',receivedBy='Synthetic parent'));assert r.json()['status']=='collected'
        assert api.read('demo').model_dump_json()==before
        for c in [*clients,admin]:c.close()
        print(json.dumps(dict(scope='In-process TestClient; no TCP/TLS/browser/login/photo processing or sustained server load',pupils=1100,parents=2200,staff=120,findings=10000,claims=5000,readClients=32,parallelFlows=16,readRequests=len(times),errors=0,p50Ms=round(statistics.median(times),2),p95Ms=round(sorted(times)[int(len(times)*.95)],2),maxMs=round(max(times),2),readElapsedSeconds=round(elapsed,2),approveMs=round(approve_ms,2),collectMs=round(collect_ms,2),schoolStateUnchanged=True),indent=2))

if __name__=='__main__':main()
