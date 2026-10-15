"""Synthetic signed-in polling and answer bursts; never opens a school database."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from time import perf_counter
import json, os, secrets, statistics, tempfile


def main():
    with tempfile.TemporaryDirectory(prefix='atlas-live-lessons-') as directory:
        target=Path(directory)/'benchmark.db'
        os.environ.update(TIMETABLE_DB=str(target),TIMETABLE_REQUIRE_AUTH='1',ATLAS_WEB_PUSH_ENABLED='0',ATLAS_BACKUP_DIR='')
        from backend import api
        from backend.access import now,password_hash,digest,COOKIE
        from backend.models import Group
        from backend.quiz_models import Question
        from tools.create_admin import create_first_admin
        from fastapi.testclient import TestClient
        if api.DB.resolve()!=target.resolve():raise RuntimeError('Run in a fresh process with the isolated database.')
        password='Synthetic-Interactive-Benchmark!';create_first_admin('bench-admin','Synthetic administrator',password)
        state=api.read('demo');sample=state.students[0];workload=state.workloads[0]
        state.students=[sample.model_copy(update=dict(id=f's{i}',name=f'Synthetic pupil {i:04d}')) for i in range(1100)]
        for n in range(37):
            state.groups.append(Group(id=f'bench-group-{n}',name=f'Synthetic group {n}',kind='elective',classIds=[sample.classId],studentIds=[f's{i}' for i in range(n*30,min(1100,(n+1)*30))]))
            state.workloads.append(workload.model_copy(update=dict(id=f'bench-workload-{n}',groupId=f'bench-group-{n}')))
        api.save('demo',state);before=api.read('demo').model_dump_json();api.live_lessons.prepare();ts=now().isoformat();hashed=password_hash(password)
        q=Question(kind='single',text='Synthetic question',options=[dict(id='a',text='4'),dict(id='b',text='5')],correctIds=['a']).model_dump(mode='json')
        with api.connect() as db:
            for i in range(1100):
                grants=json.dumps(dict(workspaces=['demo'],grants=[dict(workspace='demo',view='student',entityIds=[f's{i}'])]))
                for account,role in [(f'pupil-{i}','student'),(f'parent-{i}-0','parent'),(f'parent-{i}-1','parent')]:
                    db.execute('INSERT INTO accounts VALUES (?,?,?,?,1,1,?,?)',(account,account,'Synthetic '+account,role,hashed,grants))
            for i in range(120):
                db.execute('INSERT INTO accounts VALUES (?,?,?,?,1,1,?,?)',(f'teacher-{i}',f'teacher-{i}',f'Synthetic teacher {i}','teacher',hashed,json.dumps(dict(workspaces=['demo'],grants=[dict(workspace='demo',view='teacher',entityIds=[workload.teacherId])]))))
            for n in range(37):
                doc=dict(title='Synthetic session',instructions='',groupId=f'bench-group-{n}',subjectId=workload.subjectId,questions=[dict(id=f'q{k}',version=1,question=q) for k in range(20)])
                db.execute("INSERT INTO live_lessons(id,workspace,owner_id,version,status,group_id,subject_id,title,created,updated,document) VALUES (?,'demo',?,3,'live',?,?,?, ?,?,?)",(f'lesson-{n}',f'teacher-{n}',doc['groupId'],workload.subjectId,doc['title'],ts,ts,json.dumps(doc)))
                db.execute("INSERT INTO live_lesson_rounds VALUES (?,0,'open',?)",(f'lesson-{n}',(now()+timedelta(hours=1)).isoformat()))
                db.executemany('INSERT INTO live_lesson_members VALUES (?,?)',[(f'lesson-{n}',f's{i}') for i in range(n*30,min(1100,(n+1)*30))])
            clients=[]
            for i in range(128):
                token=secrets.token_urlsafe(32);db.execute('INSERT INTO sessions VALUES (?,?,?,?)',(digest(token),f'pupil-{i}',ts,(now()+timedelta(hours=12)).isoformat()))
                c=TestClient(api.app,headers={'X-Atlas-Request':'1'});c.cookies.set(COOKIE,token);clients.append(c)
        def flow(pair):
            i,c=pair;out=[];etag='';base=f'/api/live-lessons/lesson-{i//30}'
            for step in range(6):
                started=perf_counter()
                if step==2:
                    r=c.put(base+'/answer?workspace=demo',json=dict(requestId=secrets.token_hex(16),version=0,studentId=f's{i}',questionIndex=0,answer=dict(selected=['a'],text=''),submit=True));kind='answer'
                else:
                    r=c.get(base+'/status?workspace=demo',headers={'If-None-Match':etag} if etag else {});kind='poll';etag=r.headers.get('etag',etag)
                out.append((kind,(perf_counter()-started)*1000,r.status_code,len(r.content)))
                assert r.status_code in {200,304},r.text
            return out
        started=perf_counter();samples=[]
        with ThreadPoolExecutor(32) as pool:
            for r in pool.map(flow,enumerate(clients)):samples.extend(r)
        elapsed=perf_counter()-started
        def stats(kind):
            rows=[x for x in samples if x[0]==kind];times=sorted(x[1] for x in rows)
            return dict(requests=len(rows),p50Ms=round(statistics.median(times),2),p95Ms=round(times[int(len(times)*.95)],2),maxMs=round(max(times),2),maxBytes=max(x[3] for x in rows),notModified=sum(x[2]==304 for x in rows))
        assert api.read('demo').model_dump_json()==before
        for c in clients:c.close()
        print(json.dumps(dict(scope='In-process TestClient only; no TCP/TLS, browser, login burst or sustained capacity claim. Concurrent checks may run.',students=1100,parents=2200,teachers=120,sessions=37,questionsPerSession=20,clients=128,parallelFlows=32,elapsedSeconds=round(elapsed,2),polling=stats('poll'),answers=stats('answer'),errors=0,schoolStateUnchanged=True),indent=2))


if __name__=='__main__':main()
