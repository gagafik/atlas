"""Synthetic HTTP test-taking, autosave, review and result-publication workload."""
import argparse
import asyncio
from collections import Counter
from datetime import datetime,timezone,timedelta
import json
import os
from pathlib import Path
import platform
import socket
import subprocess
import sys
import tempfile
import time
from uuid import uuid4
import httpx
from .benchmark_portal import fixture,stop_child
from .benchmark_homework import summary
from .benchmark_http import Workers,RequestTiming,CLIENT_IDLE_SECONDS,SERVER_IDLE_SECONDS


async def probe(url,identities,tasks,assignments,concurrency,client_mode='independent'):
    times={};transport={};endpoint_transport={};statuses=Counter();attempts={}
    async with Workers(url,concurrency,client_mode) as workers:
        async def request(client,label,method,path,cookie,expected=(200,),**kwargs):
            timing=RequestTiming()
            response=await client.request(method,path,headers={'Cookie':cookie,'X-Atlas-Request':'1'},extensions={'trace':timing.trace},**kwargs)
            values=timing.finish(response)
            times.setdefault(label,[]).append(values.pop('total_ms'));statuses[str(response.status_code)]+=1
            phases=endpoint_transport.setdefault(label,{})
            for key,value in values.items():
                transport.setdefault(key,[]).append(value);phases.setdefault(key,[]).append(value)
            if response.status_code not in expected:raise RuntimeError(f'{label}: {response.status_code}: {response.text[:200]}')
            return response
        async def work(task):
            pupil,key,teacher,quiz=task;cookie=identities[pupil][0];parent=identities[1100+pupil][0];sid=f's{pupil}';path='/api/journal/quizzes/'+quiz
            async with workers.slot() as client:
                catalog=(await request(client,'catalog','GET','/api/journal/quizzes',cookie)).json();assert any(i['id']==quiz for i in catalog['items'])
                opened=(await request(client,'assignment','GET',path,cookie,params=dict(studentId=sid))).json()
                started=(await request(client,'start','POST',path+'/attempts',cookie,json=dict(version=opened['version'],studentId=sid,requestId=uuid4().hex))).json()
                aid=started['id'];target=path+'/attempts/'+aid;assert 'correctIds' not in json.dumps(started)
                for i in range(3):
                    answers=dict(choice=dict(selected=['a']),essay=dict(text=f'Synthetic explanation {i} '+('x'*2000)))
                    started=(await request(client,'autosave','PUT',target,cookie,json=dict(version=started['version'],answers=answers))).json()
                hidden=(await request(client,'parent_active','GET',target,parent)).json();assert 'answers' not in hidden
                sent=(await request(client,'submit','PUT',target,cookie,json=dict(version=started['version'],answers=answers,submit=True))).json();assert sent['status']=='submitted' and 'marks' not in sent
                reviewed=(await request(client,'review','POST',target+'/review',teacher,json=dict(version=sent['version'],marks=dict(essay=dict(score=1,comment='Checked'))))).json();assert reviewed['score']==3
                private=(await request(client,'parent_before_release','GET',target,parent)).json();assert 'score' not in private and not private['resultsVisible']
                attempts[pupil]=(target,reviewed['version'])
        async def run(function):
            async with asyncio.TaskGroup() as group:
                for task in tasks:group.create_task(function(task))
        began=time.perf_counter();await run(work)
        for quiz,teacher in assignments:
            async with workers.slot() as client:
                current=(await request(client,'teacher_roster','GET','/api/journal/quizzes/'+quiz,teacher)).json()
                closed=(await request(client,'close','POST','/api/journal/quizzes/'+quiz+'/actions',teacher,json=dict(version=current['version'],action='close',reason='Synthetic completion'))).json()
                await request(client,'release','POST','/api/journal/quizzes/'+quiz+'/actions',teacher,json=dict(version=closed['version'],action='release',reason='Synthetic release'))
        async def result(task):
            pupil,key,teacher,quiz=task;parent=identities[1100+pupil][0];target,version=attempts[pupil]
            async with workers.slot() as client:
                visible=(await request(client,'parent_result','GET',target,parent)).json();assert visible['score']==3 and visible['resultsVisible']
                for _ in range(60):
                    selection=await request(client,'grading_options','GET',target+'/grading',teacher,expected=(200,409))
                    if selection.status_code==409:
                        assert selection.json()['detail'].startswith('Журнал изменился.'),selection.text
                        await asyncio.sleep(.05+(pupil%7)*.02)
                        continue
                    options=selection.json()
                    marked=await request(client,'journal_grade','POST',target+'/grade',teacher,expected=(200,409),json=dict(version=version,grading=dict(journalVersion=options['journalVersion'],revision=options['revision'],assessmentId='test',score=3)))
                    if marked.status_code==200:break
                    assert marked.json()['detail'].startswith(('Журнал изменился.','Журнал или расписание изменились.')),marked.text
                    await asyncio.sleep(.05+(pupil%7)*.02)
                else:raise RuntimeError('Grade retry exhausted')
                history=(await request(client,'history','GET',target+'/history',parent)).json();assert any(i['action']=='quiz.graded' for i in history['items'])
        await run(result);elapsed=time.perf_counter()-began
    all_times=[v for values in times.values() for v in values]
    return dict(completed_workflows=len(tasks),concurrency=concurrency,client_mode=client_mode,client_loop=type(asyncio.get_running_loop()).__name__,duration_s=round(elapsed,2),statuses=dict(statuses),**summary(all_times),endpoints={k:summary(v) for k,v in times.items()},transport={k:summary(v) for k,v in transport.items()},endpoint_transport={label:{k:summary(v) for k,v in phases.items()} for label,phases in endpoint_transport.items()})


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True);p.add_argument('--pupils',type=int,default=100);p.add_argument('--concurrency',type=int,default=50)
    p.add_argument('--client-mode',choices=['independent','shared'],default='independent',help='Independent worker clients, or the previous shared HTTP pool for comparison.')
    p.add_argument('--server-loop',choices=['auto','selector'],default='auto',help='Explicit selector is a diagnostic alternative, not a production setting.');args=p.parse_args()
    if not 1<=args.pupils<=1100 or not 1<=args.concurrency<=100:p.error('pupils 1..1100; concurrency 1..100')
    with tempfile.TemporaryDirectory(prefix='atlas-quizzes-load-') as directory:
        root=Path(directory);os.environ['TIMETABLE_DB']=str(root/'synthetic.db');identities,counts,admin_cookie=fixture(True)
        with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        url=f'http://127.0.0.1:{port}';pidfile=root/'server.pid'
        server_loop='asyncio:SelectorEventLoop' if args.server_loop=='selector' else 'auto'
        launch=f"import os; from pathlib import Path; Path({str(pidfile)!r}).write_text(str(os.getpid())); from backend.api import app; from tools.benchmark_http import ServerTiming; app.add_middleware(ServerTiming); import uvicorn; uvicorn.run(app,host='127.0.0.1',port={port},access_log=False,loop={server_loop!r},timeout_keep_alive={SERVER_IDLE_SECONDS})"
        with (root/'server.log').open('w',encoding='utf-8') as log:
            server=subprocess.Popen([sys.executable,'-c',launch],stdout=log,stderr=log,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            try:
                with httpx.Client(base_url=url,timeout=120,trust_env=False,headers={'Cookie':admin_cookie,'X-Atlas-Request':'1'}) as admin:
                    for _ in range(100):
                        if server.poll() is not None:raise RuntimeError('Synthetic server stopped')
                        try:
                            health=admin.get('/api/health')
                            if health.status_code==200:break
                        except httpx.HTTPError:pass
                        time.sleep(.1)
                    else:raise RuntimeError('Synthetic server did not start')
                    before=admin.get('/api/state').json();day='2026-10-13';instant=datetime.now(timezone.utc)
                    for id,question in [('choice',dict(kind='single',text='2 + 2',points=2,options=[dict(id='a',text='4'),dict(id='b',text='5')],correctIds=['a'])),('essay',dict(kind='essay',text='Explain',points=3))]:
                        admin.put('/api/journal/quiz-bank/'+id,json=dict(version=0,question=question)).raise_for_status()
                    lessons=admin.get('/api/journal/day',params=dict(day=day)).json()['lessons'];prepared={};tasks=[]
                    for pupil in [i*1100//args.pupils for i in range(args.pupils)]:
                        group=pupil//20
                        if group not in prepared:
                            row=next(r for r in lessons if r['lesson']['group']==f'Class {group}' and r['lesson']['subject']=='Subject 0');key=row['lessonKey']
                            entry=admin.get('/api/journal/entry',params=dict(lessonKey=key,day=day)).json();lesson=entry['document']['lesson'];teacher=next(c for c,v,e in identities if v=='teacher' and e==lesson['teacherIds'][0])
                            content=entry['document']['content']|dict(assessments=[dict(id='test',name='Online test',kind='points',maxScore=5,weight=1)])
                            admin.put('/api/journal/entry',json=dict(lessonKey=key,day=day,revision=0,version=0,content=content,publish=True)).raise_for_status()
                            quiz=f'quiz-{group}';definition=dict(title=f'Test {group}',variants=[dict(id='v',name='A',questions=[dict(id='choice',version=1),dict(id='essay',version=1)])],opensAt=(instant-timedelta(hours=1)).isoformat(),closesAt=(instant+timedelta(days=1)).isoformat(),timeLimitMinutes=60)
                            admin.put('/api/journal/quizzes/'+quiz,json=dict(version=0,revision=0,lessonKey=key,day=day,definition=definition,studentIds=[s['id'] for s in lesson['students']],publish=True)).raise_for_status()
                            prepared[group]=(key,teacher,quiz)
                        tasks.append((pupil,*prepared[group]))
                    print(json.dumps(dict(assignments=len(prepared),pupils=len(tasks))),flush=True)
                    result=asyncio.run(probe(url,identities,tasks,[(v[2],v[1]) for v in prepared.values()],args.concurrency,args.client_mode));assert admin.get('/api/state').json()==before
                    report=dict(created=datetime.now(timezone.utc).isoformat(),synthetic=True,state_unchanged=True,data=counts,assignments=len(prepared),platform=platform.platform(),server_loop=health.headers.get('x-atlas-benchmark-loop'),client_keepalive_s=CLIENT_IDLE_SECONDS,server_keepalive_s=SERVER_IDLE_SECONDS,run=result,limitations='Ready sessions; 2 questions and three autosaves per pupil; client and server share one computer. No login storm, year archive, generation, TLS or Internet. Not a school capacity claim.')
                    args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(result),flush=True)
            finally:
                if pidfile.exists():
                    child=int(pidfile.read_text())
                    if child!=server.pid and server.poll() is None:stop_child(child)
                if server.poll() is None:server.terminate()
                try:server.wait(timeout=10)
                except subprocess.TimeoutExpired:server.kill();server.wait(timeout=10)
                # Keep diagnostics when HTTP fails before a JSON report is ready.
                # The temporary database contains only this tool's synthetic data.
                log.flush();args.output.parent.mkdir(parents=True,exist_ok=True)
                args.output.with_suffix('.server.log').write_text((root/'server.log').read_text(encoding='utf-8',errors='replace'),encoding='utf-8')


if __name__=='__main__':main()
