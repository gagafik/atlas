"""Synthetic meeting benchmark; isolated database, no school files or network."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timedelta,timezone
import json,os,statistics,tempfile
from pathlib import Path
from time import perf_counter
from uuid import uuid4


def main():
    with tempfile.TemporaryDirectory(prefix='atlas-meetings-bench-') as directory:
        target=Path(directory)/'benchmark.db'
        os.environ.update(TIMETABLE_DB=str(target),TIMETABLE_REQUIRE_AUTH='1',ATLAS_WEB_PUSH_ENABLED='0',ATLAS_BACKUP_DIR='')
        from backend import api
        from backend.access import password_hash
        from backend.models import Teacher
        from tools.create_admin import create_first_admin
        from fastapi.testclient import TestClient
        if api.DB.resolve()!=target.resolve():raise RuntimeError('Use a fresh Python process with a temporary database.')
        password='Synthetic-meetings-benchmark!';create_first_admin('bench-admin','Synthetic admin',password)
        state=api.read('demo');pupil=state.students[0];workload=state.workloads[0]
        state.students=[pupil.model_copy(update=dict(id=f'p{i}',name=f'Synthetic pupil {i}',groupIds=[])) for i in range(1100)]
        for group in state.groups:group.studentIds=[]
        state.teachers=[Teacher(id=f't{i}',name=f'Synthetic teacher {i}') for i in range(120)]
        state.workloads=[workload.model_copy(update=dict(id=f'w{i}',teacherId=f't{i}')) for i in range(120)]
        api.save('demo',state);before=api.read('demo').model_dump_json()
        hashed=password_hash(password);rows=[]
        for i in range(1100):
            for prefix,role in [('student','student'),('parent-a','parent'),('parent-b','parent')]:
                id=f'{prefix}-{i}';permission=dict(workspaces=[],grants=[dict(workspace='demo',view='student',entityIds=[f'p{i}'])])
                rows.append((id,id,id,role,1,1,hashed,json.dumps(permission)))
        for i in range(120):
            id=f'teacher-{i}';permission=dict(workspaces=[],grants=[dict(workspace='demo',view='teacher',entityIds=[f't{i}'])])
            rows.append((id,id,id,'teacher',1,1,hashed,json.dumps(permission)))
        with api.connect() as db:db.executemany('INSERT INTO accounts VALUES (?,?,?,?,?,?,?,?)',rows)
        def session(login):
            c=TestClient(api.app,headers={'X-Atlas-Request':'1'})
            c.post('/api/auth/login',json=dict(login=login,password=password)).raise_for_status();return c
        def route(part):return '/api/meetings/'+part+'?workspace=demo'
        def body(**kw):return dict(requestId=str(uuid4()),**kw)
        def checked(r):r.raise_for_status();return r.json()
        admin=session('bench-admin');start=(datetime.now(timezone.utc)+timedelta(days=7)).replace(hour=12,minute=0,second=0,microsecond=0);end=start+timedelta(hours=2)
        definition=dict(title='Synthetic meetings',startsAt=start.isoformat(),endsAt=end.isoformat(),hosts=[dict(accountId=f'teacher-{i}',teacherId=f't{i}',location=f'Location {i}') for i in range(120)])
        id=checked(admin.post(route('events'),json=body(definition=definition)))['id']
        action=body(version=1,action='generate',hostIds=[f'teacher-{i}' for i in range(120)],startsAt=start.isoformat(),endsAt=end.isoformat())
        t=perf_counter();preview=checked(admin.post(route(f'events/{id}/availability/preview'),json=action));preview_ms=(perf_counter()-t)*1000
        t=perf_counter();generated=checked(admin.post(route(f'events/{id}/availability'),json=action|dict(previewHash=preview['previewHash'])));generate_ms=(perf_counter()-t)*1000
        t=perf_counter();published=checked(admin.post(route(f'events/{id}/transition'),json=body(version=generated['version'],action='publish')));publish_ms=(perf_counter()-t)*1000
        clients=[session(f'parent-a-{i}') for i in range(32)]
        def flow(pair):
            i,c=pair;results=[]
            for n in range(8):
                t=perf_counter();r=c.get(route(f'events/{id}/slots')+f'&hostId=teacher-{i}');results.append(((perf_counter()-t)*1000,r.status_code));slot=checked(r)['items'][0]
            t=perf_counter();r=c.post(route(f'slots/{slot["id"]}/book'),json=body(version=slot['version'],eventVersion=published['version'],studentId=f'p{i}'));results.append(((perf_counter()-t)*1000,r.status_code));return results
        t=perf_counter();timings=[];codes=[]
        with ThreadPoolExecutor(16) as pool:
            for result in pool.map(flow,enumerate(clients)):
                for elapsed,code in result:timings.append(elapsed);codes.append(code)
        duration=perf_counter()-t;assert set(codes)=={200},codes
        assert api.read('demo').model_dump_json()==before
        with api.connect() as db:
            assert db.execute('SELECT count(*) FROM meeting_slots').fetchone()[0]==1440
            assert db.execute('SELECT count(*) FROM meeting_bookings').fetchone()[0]==32
        for c in [admin,*clients]:c.close()
        print(json.dumps(dict(scope='In-process TestClient; no TCP/TLS/browser; login excluded',pupils=1100,parents=2200,teachers=120,slots=1440,clients=32,parallelFlows=16,requests=len(codes),errors=sum(x!=200 for x in codes),previewMs=round(preview_ms,2),generateMs=round(generate_ms,2),publishMs=round(publish_ms,2),p50Ms=round(statistics.median(timings),2),p95Ms=round(sorted(timings)[int(len(timings)*.95)],2),maxMs=round(max(timings),2),elapsedSeconds=round(duration,2),schoolStateUnchanged=True),indent=2))


if __name__=='__main__':main()
