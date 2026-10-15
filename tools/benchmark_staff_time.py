"""Isolated synthetic staff-time month, reads and approvals; no real school data."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date,timedelta
import json,os,statistics,tempfile
from pathlib import Path
from time import perf_counter
from uuid import uuid4


def main():
    with tempfile.TemporaryDirectory(prefix='atlas-staff-time-') as directory:
        target=Path(directory)/'benchmark.db'
        os.environ.update(TIMETABLE_DB=str(target),TIMETABLE_REQUIRE_AUTH='1',ATLAS_WEB_PUSH_ENABLED='0',ATLAS_BACKUP_DIR='')
        from backend import api
        from backend.access import password_hash
        from backend.staff_time import stamp,today,bounds
        from tools.create_admin import create_first_admin
        from fastapi.testclient import TestClient
        if api.DB.resolve()!=target.resolve():raise RuntimeError('Run in a fresh process with an isolated database.')
        password='Synthetic-staff-time-benchmark!';create_first_admin('admin','Synthetic admin',password)
        before=api.read('demo').model_dump_json();api.staff_time.prepare();hashed=password_hash(password)
        month=(today().replace(day=1)-timedelta(days=1)).strftime('%Y-%m');start,end=bounds(month);time=stamp();days=(end-start).days+1
        with api.connect() as db:
            for i in range(120):
                id=f'staff-{i}';name=f'Synthetic employee {i}'
                db.execute('INSERT INTO accounts VALUES (?,?,?,?,?,?,?,?)',(id,id,name,'staff',1,1,hashed,json.dumps(dict(workspaces=['demo'],grants=[]))))
                doc=json.dumps(dict(fullDay=None,segments=[dict(start='09:00',end='12:00',code='work'),dict(start='13:00',end='17:00',code='work')],note='',employeeName=name,source='admin'))
                db.executemany('INSERT INTO staff_time_days VALUES (?,?,?,?,?,?,?,?,?)',[(f'{id}-{d}','demo',id,(start+timedelta(days=d)).isoformat(),1,'draft' if d==0 else 'approved',time,time,doc) for d in range(days)])
        def session(login):
            c=TestClient(api.app,headers={'X-Atlas-Request':'1'});c.post('/api/auth/login',json=dict(login=login,password=password)).raise_for_status();return c
        clients=[session(f'staff-{i}') for i in range(32)];admin=session('admin')
        timings=[];codes=[]
        def request(c,method,route,**kw):
            t=perf_counter();r=getattr(c,method)('/api/staff-time/'+route,params=dict(workspace='demo',month=month),**kw);elapsed=(perf_counter()-t)*1000;r.raise_for_status();return elapsed,r.status_code
        def flow(pair):
            i,c=pair;result=[]
            for n in range(6):result.append(request(c,'get','report'))
            result.append(request(admin,'post',f'days/staff-{i}-0/action',json=dict(requestId=str(uuid4()),version=1,action='approve')))
            result.append(request(c,'get','report'));return result
        started=perf_counter()
        with ThreadPoolExecutor(16) as pool:
            for result in pool.map(flow,enumerate(clients)):
                for elapsed,code in result:timings.append(elapsed);codes.append(code)
        total=perf_counter()-started
        admin_ms,_=request(admin,'get','report');csv_ms,_=request(admin,'get','export')
        assert api.read('demo').model_dump_json()==before
        for c in [*clients,admin]:c.close()
        print(json.dumps(dict(scope='In-process TestClient; login excluded, no TCP/TLS/browser',employees=120,days=120*days,parallelFlows=16,clients=32,requests=len(codes),errors=sum(c!=200 for c in codes),p50Ms=round(statistics.median(timings),2),p95Ms=round(sorted(timings)[int(len(timings)*.95)],2),maxMs=round(max(timings),2),adminReportMs=round(admin_ms,2),adminCsvMs=round(csv_ms,2),elapsedSeconds=round(total,2),schoolStateUnchanged=True),indent=2))


if __name__=='__main__':main()
