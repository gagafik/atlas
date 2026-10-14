"""Synthetic HTTP homework workflow probe, isolated from the real school database."""
import argparse
import asyncio
from collections import Counter
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import platform
import socket
import subprocess
import sys
import tempfile
import time

import httpx
from .benchmark_portal import fixture, stop_child


def summary(values):
    ordered=sorted(values)
    return dict(count=len(values),p50_ms=round(ordered[math.ceil(len(values)*.50)-1],1),
                p95_ms=round(ordered[math.ceil(len(values)*.95)-1],1),max_ms=round(ordered[-1],1))


async def probe(url, identities, tasks, concurrency, size):
    latencies=[];by_endpoint={};statuses=Counter();finished=0
    semaphore=asyncio.Semaphore(concurrency)
    # Pupils have independent answers but may share a journal revision.
    async with httpx.AsyncClient(base_url=url,timeout=120,trust_env=False,
                                 limits=httpx.Limits(max_connections=concurrency,max_keepalive_connections=concurrency)) as client:
        async def request(label, method, path, cookie, expected=(200,), **kwargs):
            began=time.perf_counter()
            response=await client.request(method,path,headers={'Cookie':cookie,'X-Atlas-Request':'1'},**kwargs)
            elapsed=(time.perf_counter()-began)*1000
            latencies.append(elapsed);by_endpoint.setdefault(label,[]).append(elapsed);statuses[str(response.status_code)]+=1
            if response.status_code not in expected:
                raise RuntimeError(f'{label}: HTTP {response.status_code}: {response.text[:200]}')
            return response
        async def work(task):
            nonlocal finished
            async with semaphore:
                pupil,key,teacher=task
                cookie=identities[pupil][0];student=f's{pupil}'
                path=f'/api/journal/homework/{key}'
                queued=(await request('queue_student','GET','/api/journal/homework-queue',cookie)).json()
                assert any(r['lessonKey']==key and r['studentId']==student and r['canSubmit'] for r in queued['items'])
                current=(await request('open','GET',path,cookie,params=dict(studentId=student))).json()
                assert len(current['assignmentFiles'])==1
                material=current['assignmentFiles'][0]
                content=await request('material_student','GET','/api/journal/assignment-files/'+material['id'],cookie,params=dict(studentId=student))
                assert content.content==b'M'*size
                data=dict(studentId=student,version=current['version'],assignment=current['assignment'],action='draft',text='Synthetic answer',fileIds=[])
                draft=(await request('draft','PUT',path,cookie,json=data)).json()
                # Upload concurrency limits intentionally return 429 under a burst;
                # retry briefly as a real client must, and count those responses.
                for attempt in range(60):
                    began=time.perf_counter()
                    response=await client.post(path+'/files',params=dict(studentId=student,filename='answer.txt',assignment=current['assignment']),
                                               headers={'Cookie':cookie,'X-Atlas-Request':'1'},content=b'x'*size)
                    elapsed=(time.perf_counter()-began)*1000
                    latencies.append(elapsed);by_endpoint.setdefault('upload',[]).append(elapsed);statuses[str(response.status_code)]+=1
                    if response.status_code==200: break
                    if response.status_code!=429: response.raise_for_status()
                    await asyncio.sleep(.2+(pupil%7)*.03)
                else: raise RuntimeError('Upload remained busy after retries.')
                file_id=response.json()['id']
                sent=(await request('submit','PUT',path,cookie,json=data|dict(version=draft['version'],action='submit',fileIds=[file_id]))).json()
                queued=(await request('queue_teacher','GET','/api/journal/homework-queue',teacher)).json()
                assert any(r['lessonKey']==key and r['studentId']==student and r['canReview'] for r in queued['items'])
                for attempt in range(60):
                    selection=await request('grading_options','GET',path+'/grading',teacher,expected=(200,409),params=dict(studentId=student))
                    if selection.status_code==409:
                        assert selection.json()['detail'].startswith('Журнал изменился.'),selection.text
                        await asyncio.sleep(.05+(pupil%7)*.02)
                        continue
                    options=selection.json()
                    choice=next(a for a in options['assessments'] if a['id']=='homework')
                    assert options['allowed'] and choice['selectable'] and choice['publishedGrade'] is None and choice['draftGrade'] is None
                    marked=await request('review_grade','POST',path+'/review',teacher,expected=(200,409),json=dict(
                        studentId=student,version=sent['version'],assignment=current['assignment'],decision='accepted',comment='Checked',
                        grading=dict(journalVersion=options['journalVersion'],revision=options['revision'],assessmentId='homework',score=9)))
                    if marked.status_code==200:break
                    # Retry only an optimistic journal collision; never overwrite a grade.
                    assert marked.json()['detail'].startswith(('Журнал изменился.','Журнал или расписание изменились.')),marked.text
                    await asyncio.sleep(.05+(pupil%7)*.02)
                else:raise RuntimeError('Journal remained busy after grading retries.')
                parent_cookie=identities[1100+pupil][0]
                queued=(await request('queue_parent','GET','/api/journal/homework-queue',parent_cookie,params=dict(filter='accepted'))).json()
                assert any(r['lessonKey']==key and r['studentId']==student and r['status']=='accepted' for r in queued['items'])
                result=(await request('parent','GET',path,parent_cookie,params=dict(studentId=student))).json()
                assert result['status']=='accepted' and result['draft'] is None
                assert result['submitted']['assignmentFiles']==[material]
                assert result['submitted']['grading']['grade']['score']==9
                journal=(await request('parent_grade','GET','/api/journal/report',parent_cookie,params=dict(studentId=student,start='2026-10-13',end='2026-10-13'))).json()
                row=next(r for r in journal['rows'] if r['lessonKey']==key)
                assert next(g for g in row['record']['grades'] if g['assessmentId']=='homework')['score']==9
                content=await request('material_parent','GET','/api/journal/assignment-files/'+material['id'],parent_cookie,params=dict(studentId=student))
                assert content.content==b'M'*size
                content=await request('download','GET',path+'/files/'+file_id,parent_cookie,params=dict(studentId=student))
                assert content.content==b'x'*size
                finished+=1
        began=time.perf_counter()
        await asyncio.gather(*(work(task) for task in tasks))
        elapsed=time.perf_counter()-began
    return dict(concurrency=concurrency,completed_workflows=finished,attachment_bytes=size,duration_s=round(elapsed,2),
                requests_per_second=round(len(latencies)/elapsed,2),statuses=dict(statuses),**summary(latencies),
                endpoints={name:summary(values) for name,values in by_endpoint.items()})


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--concurrency',type=int,default=20)
    parser.add_argument('--pupils',type=int,default=60)
    parser.add_argument('--file-kib',type=int,default=256)
    args=parser.parse_args()
    if not 1<=args.concurrency<=100 or not 1<=args.pupils<=1100 or not 1<=args.file_kib<=1024:
        parser.error('Use concurrency 1..100, pupils 1..1100, file-kib 1..1024.')
    with tempfile.TemporaryDirectory(prefix='atlas-homework-load-') as directory:
        root=Path(directory);os.environ['TIMETABLE_DB']=str(root/'synthetic.db')
        identities,counts,admin_cookie=fixture(True)
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        url=f'http://127.0.0.1:{port}';pidfile=root/'server.pid'
        launch=f"import os; from pathlib import Path; Path({str(pidfile)!r}).write_text(str(os.getpid())); import uvicorn; uvicorn.run('backend.api:app',host='127.0.0.1',port={port},access_log=False)"
        with (root/'server.log').open('w',encoding='utf-8') as log:
            server=subprocess.Popen([sys.executable,'-c',launch],stdout=log,stderr=log,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            try:
                with httpx.Client(base_url=url,trust_env=False,timeout=120,headers={'Cookie':admin_cookie,'X-Atlas-Request':'1'}) as admin:
                    for _ in range(100):
                        if server.poll() is not None: raise RuntimeError('Synthetic server stopped.')
                        try:
                            if admin.get('/api/health').status_code==200: break
                        except httpx.HTTPError: pass
                        time.sleep(.1)
                    else: raise RuntimeError('Synthetic server did not start.')
                    before=admin.get('/api/state').json();day='2026-10-13'
                    lessons=admin.get('/api/journal/day',params=dict(day=day)).json()['lessons']
                    choices=[i*1100//args.pupils for i in range(args.pupils)];prepared={};tasks=[]
                    for pupil in choices:
                        group=f'g{pupil//20}'
                        if group not in prepared:
                            row=next(r for r in lessons if r['lesson']['group']==f'Class {pupil//20}' and r['lesson']['subject']=='Subject 0')
                            key=row['lessonKey']
                            entry=admin.get('/api/journal/entry',params=dict(lessonKey=key,day=day)).json()
                            material=admin.post('/api/journal/assignment-files',params=dict(lessonKey=key,day=day,revision=0,version=0,filename='material.txt'),content=b'M'*(args.file_kib*1024))
                            material.raise_for_status()
                            content=entry['document']['content']|dict(homework='Synthetic homework',dueDate='2026-10-14',homeworkFiles=[material.json()['id']],
                                assessments=[dict(id='homework',name='Homework',kind='points',maxScore=10,weight=1)])
                            response=admin.put('/api/journal/entry',json=dict(lessonKey=key,day=day,revision=0,version=0,content=content,publish=True))
                            response.raise_for_status()
                            teacher=next(cookie for cookie,view,entity in identities if view=='teacher' and entity==entry['document']['lesson']['teacherIds'][0])
                            prepared[group]=(key,teacher)
                        tasks.append((pupil,*prepared[group]))
                    print(json.dumps(dict(prepared_assignments=len(prepared),pupils=len(tasks))),flush=True)
                    result=asyncio.run(probe(url,identities,tasks,args.concurrency,args.file_kib*1024))
                    assert admin.get('/api/state').json()==before
                    report=dict(created=datetime.now(timezone.utc).isoformat(),synthetic=True,state_unchanged=True,data=counts,shared_materials=len(prepared),
                                platform=platform.platform(),run=result,
                                limitations='Existing sessions; synthetic published assignments; no login storm, solver, TLS, Internet latency, large files or multi-server load. Upload 429 and optimistic journal 409 retries included. Client and server share a laptop; not a school capacity claim.')
                    args.output.parent.mkdir(parents=True,exist_ok=True)
                    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
                    print(json.dumps(result),flush=True)
            finally:
                if pidfile.exists():
                    child=int(pidfile.read_text())
                    if child!=server.pid and server.poll() is None: stop_child(child)
                if server.poll() is None: server.terminate()
                try: server.wait(timeout=10)
                except subprocess.TimeoutExpired: server.kill();server.wait(timeout=10)


if __name__=='__main__':main()
