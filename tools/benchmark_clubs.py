"""Synthetic club catalogue, family reads, seat queue and attendance benchmark."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date,timedelta
from pathlib import Path
from time import perf_counter
from uuid import uuid4
import json,os,statistics,tempfile

def main():
    with tempfile.TemporaryDirectory(prefix='atlas-clubs-') as directory:
        target=Path(directory)/'benchmark.db';os.environ.update(TIMETABLE_DB=str(target),TIMETABLE_REQUIRE_AUTH='1',ATLAS_WEB_PUSH_ENABLED='0',ATLAS_BACKUP_DIR='')
        from backend import api
        from backend.access import password_hash
        from backend.clubs import stamp,today
        from backend.notifications import permission_key
        from tools.create_admin import create_first_admin
        from fastapi.testclient import TestClient
        if api.DB.resolve()!=target.resolve():raise RuntimeError('Use a fresh process and an isolated database.')
        password='Synthetic-clubs-benchmark!';create_first_admin('admin','Synthetic admin',password)
        state=api.read('demo');prototype=state.students[0];ids=[state.students[i].id if i<len(state.students) else f'pupil-{i}' for i in range(1100)]
        state.students=[prototype.model_copy(update=dict(id=ids[i],name=f'Synthetic pupil {i}')) for i in range(1100)];api.save('demo',state);before=api.read('demo').model_dump_json();api.clubs.prepare();hashed=password_hash(password);time=stamp();day=today();end=(date.fromisoformat(day)+timedelta(days=120)).isoformat()
        with api.connect() as db:
            for i in range(2200):
                aid=f'parent-{i}';permissions=dict(workspaces=[],grants=[dict(workspace='demo',view='student',entityIds=[ids[i//2]])]);db.execute('INSERT INTO accounts VALUES (?,?,?,?,?,?,?,?)',(aid,aid,f'Synthetic parent {i}','parent',1,1,hashed,json.dumps(permissions)))
            for i in range(120):
                aid=f'staff-{i}';db.execute('INSERT INTO accounts VALUES (?,?,?,?,?,?,?,?)',(aid,aid,f'Synthetic staff {i}','staff',1,1,hashed,json.dumps(dict(workspaces=['demo'],grants=[]))))
            for i in range(100):
                data=dict(title=f'Synthetic club {i}',description='Extracurricular activity',location='Club room',startsOn=day,endsOn=end,opensOn=day,closesOn=end,capacity=100,queueMode='automatic',classIds=[prototype.classId])
                db.execute("INSERT INTO clubs(id,workspace,version,status,created,updated,search,document) VALUES (?,?,1,'open',?,?,?,?)",(f'club-{i}','demo',time,time,data['title'].casefold(),json.dumps(data)))
                for j in range(50):
                    pupil=j;permissions=dict(id=f'parent-{pupil*2}',role='parent',workspaces=[],grants=[dict(workspace='demo',view='student',entityIds=[ids[pupil]])]);doc=dict(studentName=f'Synthetic pupil {pupil}',className='Example class',decision='',requesterPermissions=permission_key(permissions))
                    db.execute("INSERT INTO club_members(id,club_id,workspace,student_id,user_id,version,status,created,updated,enrolled_on,left_on,document) VALUES (?,?,?,?,?,1,'enrolled',?,?,?,'',?)",(f'member-{i}-{j}',f'club-{i}','demo',ids[pupil],permissions['id'],time,time,day,json.dumps(doc)))
                for j in range(10):
                    doc=dict(date=day,start='16:00',end='17:00',title=f'Historic session {j}',location='Room')
                    # Completed historical sessions exercise paging without creating future conflicts.
                    db.execute("INSERT INTO club_sessions(id,club_id,workspace,version,status,date,start,end,document) VALUES (?,?,?,1,'completed',?,?,?,?)",(f'session-{i}-{j}',f'club-{i}','demo',day,'16:00','17:00',json.dumps(doc)))
                    for k in range(50):db.execute('INSERT INTO club_attendance VALUES (?,?,?,?,?,?)',(f'session-{i}-{j}',ids[k],f'member-{i}-{k}','present','',json.dumps(dict(studentName=f'Synthetic pupil {k}',className='Example class'))))
        def session(login):
            c=TestClient(api.app,headers={'X-Atlas-Request':'1'});c.post('/api/auth/login',json=dict(login=login,password=password)).raise_for_status();return c
        clients=[session(f'parent-{i}') for i in range(32)];admin=session('admin')
        def request(c,method,path,**kw):
            start=perf_counter();r=getattr(c,method)('/api/clubs/'+path,params=dict(workspace='demo'),**kw);ms=(perf_counter()-start)*1000;r.raise_for_status();return ms,r
        def flow(pair):
            i,c=pair;times=[]
            for _ in range(2):
                for path in ['items','items/club-0','items/club-0/members','items/club-0/sessions/session-0-0']:
                    ms,r=request(c,'get',path);times.append(ms)
                    if path.endswith('/members'):assert len(r.json()['items'])==1 and r.json()['items'][0]['studentId']==ids[i//2]
                    if '/sessions/' in path:assert len(r.json()['marks'])==1 and r.json()['marks'][0]['studentId']==ids[i//2]
            return times
        start=perf_counter();times=[]
        with ThreadPoolExecutor(16) as pool:
            for rows in pool.map(flow,enumerate(clients)):times.extend(rows)
        elapsed=perf_counter()-start
        join_ms,r=request(admin,'post','items/club-0/members',json=dict(requestId=str(uuid4()),clubVersion=1,studentId=ids[100],reason='Synthetic registration'))
        member=r.json();_,c=request(admin,'get','items/club-0');withdraw_ms,r=request(admin,'post','items/club-0/members/'+member['id']+'/action',json=dict(requestId=str(uuid4()),version=member['version'],clubVersion=c.json()['version'],action='withdraw',reason='Synthetic cancellation'))
        assert r.json()['status']=='withdrawn' and api.read('demo').model_dump_json()==before
        for c in [*clients,admin]:c.close()
        print(json.dumps(dict(scope='In-process TestClient; excludes TCP/TLS/browser/logins and sustained production traffic',pupils=1100,parents=2200,staff=120,clubs=100,enrolments=5000,sessions=1000,attendanceRows=50000,readClients=32,parallelFlows=16,readRequests=len(times),errors=0,p50Ms=round(statistics.median(times),2),p95Ms=round(sorted(times)[int(len(times)*.95)],2),maxMs=round(max(times),2),readElapsedSeconds=round(elapsed,2),joinMs=round(join_ms,2),withdrawMs=round(withdraw_ms,2),schoolStateUnchanged=True),indent=2))

if __name__=='__main__':main()
