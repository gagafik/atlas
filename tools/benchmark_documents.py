"""Synthetic school document reads and issuance; isolated database, no school data."""
from concurrent.futures import ThreadPoolExecutor
import json,os,statistics,tempfile
from pathlib import Path
from time import perf_counter
from uuid import uuid4


def main():
    with tempfile.TemporaryDirectory(prefix='atlas-documents-') as directory:
        target=Path(directory)/'benchmark.db'
        os.environ.update(TIMETABLE_DB=str(target),TIMETABLE_REQUIRE_AUTH='1',ATLAS_WEB_PUSH_ENABLED='0',ATLAS_BACKUP_DIR='')
        from backend import api
        from backend.access import password_hash
        from backend.notifications import permission_key
        from backend.school_documents import stamp,today
        from tools.create_admin import create_first_admin
        from fastapi.testclient import TestClient
        if api.DB.resolve()!=target.resolve():raise RuntimeError('Run in a fresh process with an isolated database.')
        password='Synthetic-documents-benchmark!';create_first_admin('admin','Synthetic admin',password)
        state=api.read('demo');prototype=state.students[0]
        pupil_ids=[state.students[i].id if i<len(state.students) else f'pupil-{i}' for i in range(1100)]
        state.students=[prototype.model_copy(update=dict(id=pupil_ids[i],name=f'Synthetic pupil {i}')) for i in range(1100)]
        api.save('demo',state);before=api.read('demo').model_dump_json();api.school_documents.prepare();hashed=password_hash(password)
        people={}
        with api.connect() as db:
            for i in range(2200):
                id=f'parent-{i}';grants=[dict(workspace='demo',view='student',entityIds=[pupil_ids[i//2]])]
                db.execute('INSERT INTO accounts VALUES (?,?,?,?,?,?,?,?)',(id,id,f'Synthetic parent {i}','parent',1,1,hashed,json.dumps(dict(workspaces=[],grants=grants))))
                people[id]=api.access.public(db.execute('SELECT * FROM accounts WHERE id=?',(id,)).fetchone())
            for i in range(120):
                id=f'staff-{i}';db.execute('INSERT INTO accounts VALUES (?,?,?,?,?,?,?,?)',(id,id,f'Synthetic employee {i}','staff',1,1,hashed,json.dumps(dict(workspaces=['demo'],grants=[]))))
        def session(login):
            c=TestClient(api.app,headers={'X-Atlas-Request':'1'});c.post('/api/auth/login',json=dict(login=login,password=password)).raise_for_status();return c
        admin=session('admin');route='/api/documents/';params=dict(workspace='demo')
        def request(c,method,path,**kw):
            started=perf_counter();r=getattr(c,method)(route+path,params=params,**kw);elapsed=(perf_counter()-started)*1000;r.raise_for_status();return elapsed,r
        def body(**kw):return dict(requestId=str(uuid4()),**kw)
        definition=dict(title='Synthetic certificate',kind='student',series='BENCH',text='{{personName}} {{number}} {{date}}',fields=[])
        _,r=request(admin,'post','templates',json=body(version=0,definition=definition,active=True));template=r.json()
        _,r=request(admin,'post','items',json=body(templateId=template['id'],templateVersion=1,subjectId=pupil_ids[0]));first=r.json()
        _,r=request(admin,'get',f'items/{first["id"]}/preview');preview=r.json()
        request(admin,'post',f'items/{first["id"]}/action',json=body(version=1,action='issue',previewHash=preview['previewHash']))
        # Seed a large fictional historical archive, then exercise normal authenticated APIs.
        with api.connect() as db:
            raw=db.execute('SELECT document FROM school_documents WHERE id=?',(first['id'],)).fetchone()[0];time=stamp()
            for n in range(1,10000):
                pupil=n%1100;id=f'archive-{n}';name=f'Synthetic pupil {pupil}';number=f'BENCH/{today().year}/{n+1:06d}';doc=json.loads(raw)
                doc['snapshot'].update(subjectName=name,number=number,text=f'{name} {number}');doc['recipients']=[]
                db.execute('INSERT INTO school_documents(id,workspace,template_id,version,status,archived,kind,subject_id,subject_name,title,number,created,updated,issued,assignee_id,assignee_permissions,document) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(id,'demo',template['id'],2,'issued',0,'student',pupil_ids[pupil],name,definition['title'],number,time,time,time,'','',json.dumps(doc)))
                db.executemany('INSERT INTO document_recipients VALUES (?,?,?)',[(id,f'parent-{i}',permission_key(people[f'parent-{i}'])) for i in [pupil*2,pupil*2+1]])
            db.execute('UPDATE document_counters SET next_number=10001')
        clients=[session(f'parent-{i*2}') for i in range(32)];reads=[];writes=[]
        def flow(pair):
            i,c=pair;times=[]
            for _ in range(3):
                ms,r=request(c,'get','items');times.append(ms);items=r.json()['items'];assert items and all(x['subjectName']==f'Synthetic pupil {i}' for x in items)
                id=items[0]['id'];ms,r=request(c,'get',f'items/{id}');times.append(ms);assert 'recipients' not in r.json()
                ms,r=request(c,'get',f'items/{id}/print');times.append(ms);assert 'no-store' in r.headers['cache-control']
            return times
        started=perf_counter()
        with ThreadPoolExecutor(16) as pool:
            for times in pool.map(flow,enumerate(clients)):reads.extend(times)
        elapsed=perf_counter()-started
        for i in range(8):
            ms,r=request(admin,'post','items',json=body(templateId=template['id'],templateVersion=1,subjectId=pupil_ids[i]));writes.append(ms);item=r.json()
            ms,r=request(admin,'get',f'items/{item["id"]}/preview');writes.append(ms)
            ms,r=request(admin,'post',f'items/{item["id"]}/action',json=body(version=1,action='issue',previewHash=r.json()['previewHash']));writes.append(ms)
        listing_ms,_=request(admin,'get','items');assert api.read('demo').model_dump_json()==before
        for c in [*clients,admin]:c.close()
        def metrics(values):return dict(requests=len(values),p50Ms=round(statistics.median(values),2),p95Ms=round(sorted(values)[int(len(values)*.95)],2),maxMs=round(max(values),2))
        print(json.dumps(dict(scope='In-process TestClient; login excluded, no TCP/TLS/browser',pupils=1100,parents=2200,staff=120,archiveSize=10000,parallelFlows=16,readClients=32,errors=0,reads=metrics(reads),issuance=metrics(writes),adminListingMs=round(listing_ms,2),readElapsedSeconds=round(elapsed,2),schoolStateUnchanged=True),indent=2))


if __name__=='__main__':main()
