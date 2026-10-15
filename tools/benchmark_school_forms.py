"""Synthetic, in-process forms benchmark. Never opens the school's database."""
import json
import os
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
import statistics
import tempfile
from time import perf_counter
from uuid import uuid4


def main():
    with tempfile.TemporaryDirectory(prefix='atlas-forms-bench-') as directory:
        os.environ['TIMETABLE_DB']=str(Path(directory)/'benchmark.db')
        os.environ['TIMETABLE_REQUIRE_AUTH']='1'
        os.environ['ATLAS_WEB_PUSH_ENABLED']='0'
        os.environ['ATLAS_BACKUP_DIR']=''
        from backend import api
        from backend.access import password_hash
        from tools.create_admin import create_first_admin
        from fastapi.testclient import TestClient
        if api.DB.resolve() != (Path(directory)/'benchmark.db').resolve():
            raise RuntimeError('Run this benchmark in a fresh Python process; the configured database must be temporary.')
        password='Synthetic-forms-benchmark!'
        create_first_admin('benchmark-admin','Synthetic administrator',password)
        state=api.read('demo');template=state.students[0]
        state.students=[template.model_copy(update=dict(id=f'p{i}',name=f'Synthetic pupil {i}',groupIds=[])) for i in range(1100)]
        # Keep a valid demo timetable structure; the benchmark never edits its placements.
        for group in state.groups:group.studentIds=[]
        api.save('demo',state)
        before=api.read('demo').model_dump_json()
        hashed=password_hash(password);rows=[]
        for i in range(1100):
            for prefix,role in [('student','student'),('parent-a','parent'),('parent-b','parent')]:
                id=f'{prefix}-{i}';permissions=dict(workspaces=[],grants=[dict(workspace='demo',view='student',entityIds=[f'p{i}'])])
                rows.append((id,id,id,role,1,1,hashed,json.dumps(permissions)))
        for i in range(120):
            id=f'staff-{i}';rows.append((id,id,id,'staff',1,1,hashed,json.dumps(dict(workspaces=['demo'],grants=[]))))
        with api.connect() as db:db.executemany('INSERT INTO accounts VALUES (?,?,?,?,?,?,?,?)',rows)
        def session(login):
            client=TestClient(api.app,headers={'X-Atlas-Request':'1'})
            response=client.post('/api/auth/login',json=dict(login=login,password=password));response.raise_for_status()
            return client
        admin=session('benchmark-admin')
        def route(id='',suffix=''):return '/api/forms/items'+('/'+id if id else '')+suffix+'?workspace=demo'
        def payload(**values):return dict(requestId=str(uuid4()),**values)
        created=admin.post(route(),json=payload(title='Synthetic school survey',kind='survey'));created.raise_for_status();id=created.json()['id']
        definition=admin.get(route(id)).json()['definition']|dict(audience=dict(roles=['parent','student','staff'],classIds=[]),questions=[dict(id='q',kind='single',title='Choose',required=True,choices=['Yes','No'])])
        response=admin.put(route(id),json=payload(version=1,definition=definition));response.raise_for_status()
        start=perf_counter();preview=admin.get(route(id,'/preview'));preview.raise_for_status();preview_ms=(perf_counter()-start)*1000
        start=perf_counter();published=admin.post(route(id,'/transition'),json=payload(version=2,action='publish',audienceHash=preview.json()['audienceHash']));published.raise_for_status();publish_ms=(perf_counter()-start)*1000
        clients=[session(f'parent-a-{i}') for i in range(32)]
        timings=[];codes=[]
        def flow(pair):
            i,client=pair;result=[]
            for j in range(8):
                url=route(id,'/answer')+f'&entityId=p{i}' if j%2 else route()
                start=perf_counter();response=client.get(url);result.append(((perf_counter()-start)*1000,response.status_code))
            start=perf_counter();response=client.put(route(id,'/answer'),json=payload(version=0,formVersion=published.json()['version'],entityId=f'p{i}',action='submit',answers={'q':'Yes'}))
            result.append(((perf_counter()-start)*1000,response.status_code));return result
        start=perf_counter()
        with ThreadPoolExecutor(max_workers=16) as pool:
            for results in pool.map(flow,enumerate(clients)):
                for elapsed,code in results:timings.append(elapsed);codes.append(code)
        duration=perf_counter()-start
        assert set(codes)=={200},codes
        summary=admin.get(route(id,'/summary'));summary.raise_for_status();assert summary.json()['responses']==32
        assert api.read('demo').model_dump_json()==before
        with closing(api.access.read_connect()) as db:
            assert db.execute('SELECT count(*) FROM school_form_recipients').fetchone()[0]==3420
            assert db.execute('SELECT count(*) FROM school_form_answers').fetchone()[0]==32
        for client in [admin,*clients]:client.close()
        print(json.dumps(dict(scope='In-process TestClient, demo data, no TCP/TLS/browser; login outside request timings',pupils=1100,parents=2200,staff=120,recipients=preview.json()['responses'],parallelFlows=16,clients=32,requests=len(codes),errors=sum(c!=200 for c in codes),previewMs=round(preview_ms,2),publishMs=round(publish_ms,2),elapsedSeconds=round(duration,3),p50Ms=round(statistics.median(timings),2),p95Ms=round(sorted(timings)[int(len(timings)*.95)],2),maxMs=round(max(timings),2),schoolStateUnchanged=True),indent=2))


if __name__=='__main__':main()
