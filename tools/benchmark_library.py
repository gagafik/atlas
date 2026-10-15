"""Synthetic physical holdings, private reads, batch checkout and inventory."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import json,os,statistics,tempfile
from pathlib import Path
from time import perf_counter
from uuid import uuid4


def main():
    with tempfile.TemporaryDirectory(prefix='atlas-library-') as directory:
        target=Path(directory)/'benchmark.db';os.environ.update(TIMETABLE_DB=str(target),TIMETABLE_REQUIRE_AUTH='1',ATLAS_WEB_PUSH_ENABLED='0',ATLAS_BACKUP_DIR='')
        from backend import api
        from backend.access import password_hash
        from backend.library import stamp,today
        from tools.create_admin import create_first_admin
        from fastapi.testclient import TestClient
        if api.DB.resolve()!=target.resolve():raise RuntimeError('Run in a fresh process using an isolated database.')
        password='Synthetic-library-benchmark!';create_first_admin('admin','Synthetic admin',password)
        state=api.read('demo');prototype=state.students[0];ids=[state.students[i].id if i<len(state.students) else f'pupil-{i}' for i in range(1100)]
        state.students=[prototype.model_copy(update=dict(id=ids[i],name=f'Synthetic pupil {i}')) for i in range(1100)];api.save('demo',state);before=api.read('demo').model_dump_json();api.library.prepare();hashed=password_hash(password);time=stamp();due=(today()+timedelta(days=14)).isoformat()
        with api.connect() as db:
            for i in range(2200):
                id=f'parent-{i}';permissions=dict(workspaces=[],grants=[dict(workspace='demo',view='student',entityIds=[ids[i//2]])]);db.execute('INSERT INTO accounts VALUES (?,?,?,?,?,?,?,?)',(id,id,f'Synthetic parent {i}','parent',1,1,hashed,json.dumps(permissions)))
            for i in range(120):
                id=f'staff-{i}';db.execute('INSERT INTO accounts VALUES (?,?,?,?,?,?,?,?)',(id,id,f'Synthetic staff {i}','staff',1,1,hashed,json.dumps(dict(workspaces=['demo'],grants=[]))))
            titles=[dict(title=f'Synthetic textbook {i}',author='Synthetic author',isbn='',publisher='',year=2026,kind='textbook',grade='10',subject='Science',description='') for i in range(20)]
            for i,t in enumerate(titles):db.execute('INSERT INTO library_titles(id,workspace,version,archived,search,document) VALUES (?,?,?,?,?,?)',(f'title-{i}','demo',1,0,t['title'].casefold(),json.dumps(t)))
            for i in range(20000):
                title=i%20;inventory=f'BOOK-{i:06d}';db.execute('INSERT INTO library_copies(id,workspace,title_id,version,inventory,inventory_key,location,condition,status) VALUES (?,?,?,?,?,?,?,?,?)',(f'copy-{i}','demo',f'title-{title}',2 if i<8000 else 1,inventory,inventory.casefold(),'Shelf','good','available'))
                if i<8000:
                    pupil=i%1100;doc=dict(title=titles[title],inventory=inventory,conditionOut='good');db.execute('INSERT INTO library_loans(id,workspace,copy_id,version,kind,borrower_id,class_id,borrower_name,status,issued,due,returned,renewals,reminded,document) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(f'loan-{i}','demo',f'copy-{i}',1,'student',ids[pupil],prototype.classId,f'Synthetic pupil {pupil}','active',time,due,None,0,None,json.dumps(doc)))
        def session(login):
            c=TestClient(api.app,headers={'X-Atlas-Request':'1'});c.post('/api/auth/login',json=dict(login=login,password=password)).raise_for_status();return c
        clients=[session(f'parent-{i*2}') for i in range(32)];admin=session('admin')
        def request(c,method,path,**kw):
            start=perf_counter();r=getattr(c,method)('/api/library/'+path,params=dict(workspace='demo'),**kw);elapsed=(perf_counter()-start)*1000;r.raise_for_status();return elapsed,r
        def flow(pair):
            i,c=pair;times=[]
            for _ in range(2):
                ms,r=request(c,'get','loans');times.append(ms);loans=r.json()['items'];assert loans and all(l['borrowerId']==ids[i] for l in loans)
                ms,r=request(c,'get','loans/'+loans[0]['id']);times.append(ms);assert not r.json()['canManage']
                ms,r=request(c,'get','titles');times.append(ms);assert len(r.json()['items'])==20
                ms,r=request(c,'get','export');times.append(ms);assert f'Synthetic pupil {i};' in r.text
            return times
        started=perf_counter();times=[]
        with ThreadPoolExecutor(16) as pool:
            for rows in pool.map(flow,enumerate(clients)):times.extend(rows)
        read_elapsed=perf_counter()-started
        cart=dict(items=[dict(copyId=f'copy-{8000+i}',copyVersion=1,borrower=dict(kind='student',id=ids[i])) for i in range(50)],due=due)
        preview_ms,r=request(admin,'post','loans/preview',json=dict(requestId=str(uuid4()),**cart));preview=r.json()
        issue_ms,r=request(admin,'post','loans',json=dict(requestId=str(uuid4()),**cart,previewHash=preview['previewHash']));assert len(r.json()['items'])==50
        inventory_ms,r=request(admin,'post','audits',json=dict(requestId=str(uuid4()),title='Synthetic inventory'));audit=r.json();assert sum(audit['counts'].values())==20000
        audit_read_ms,_=request(admin,'get','audits/'+audit['id']);assert api.read('demo').model_dump_json()==before
        for c in [*clients,admin]:c.close()
        print(json.dumps(dict(scope='In-process TestClient; no TCP/TLS/browser/login or sustained server load',pupils=1100,parents=2200,staff=120,titles=20,copies=20000,seededOpenLoans=8000,readClients=32,parallelFlows=16,readRequests=len(times),errors=0,p50Ms=round(statistics.median(times),2),p95Ms=round(sorted(times)[int(len(times)*.95)],2),maxMs=round(max(times),2),readElapsedSeconds=round(read_elapsed,2),batchCopies=50,batchPreviewMs=round(preview_ms,2),batchIssueMs=round(issue_ms,2),inventoryStartMs=round(inventory_ms,2),inventoryPageMs=round(audit_read_ms,2),schoolStateUnchanged=True),indent=2))


if __name__=='__main__':main()
