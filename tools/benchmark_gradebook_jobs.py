"""Background gradebooks under concurrent personal reads and journal writes."""
import argparse,asyncio,json,os,sqlite3,time
from contextlib import closing
from datetime import date,timedelta
from pathlib import Path
from uuid import uuid4
import httpx
from .synthetic_server import synthetic_server
from .benchmark_journal_coverage import seed
from .benchmark_final_grades import mark
from .benchmark_homework import summary


async def during_build(url,cookie,identities,job,period,students,entry,timings):
    stop=asyncio.Event();phase=['queued'];states=set();began=time.perf_counter();writes=[0]
    async with httpx.AsyncClient(base_url=url,trust_env=False,timeout=60,headers={'Cookie':cookie,'X-Atlas-Request':'1'}) as client:
        async def monitor():
            current=job
            try:
                while current['status'] in ['queued','running','cancelling']:
                    phase[0]=current['phase']
                    if phase[0] not in states:print(json.dumps(dict(phase=phase[0],elapsed=round(time.perf_counter()-began,2))),flush=True)
                    states.add(current['phase'])
                    if time.perf_counter()-began>600:raise TimeoutError('Preparation observation exceeded 600 seconds; inspect the same job.')
                    await asyncio.sleep(.2);r=await client.get('/api/final-grades/preparations/'+job['id']);r.raise_for_status();current=r.json()
                assert current['status']=='succeeded',current
                return current
            finally:stop.set()
        async def read(i):
            student=students[i%len(students)];auth=next(c for c,v,id in identities if v=='student' and id==student)
            while not stop.is_set():
                label='read_'+phase[0];start=time.perf_counter()
                r=await client.get('/api/final-grades/report',headers={'Cookie':auth},params=dict(studentId=student,periodId=period));r.raise_for_status()
                assert r.json()['items'] and all(v['student']['id']==student for v in r.json()['items'])
                timings.setdefault(label,[]).append((time.perf_counter()-start)*1000);await asyncio.sleep(.2)
        async def write():
            current=entry
            while not stop.is_set():
                label='write_'+phase[0];content=current['document']['content']|dict(topic='Concurrent synthetic draft '+str(writes[0]))
                start=time.perf_counter();r=await client.put('/api/journal/entry',json=dict(lessonKey=current['lessonKey'],day=current['document']['lesson']['date'],revision=current['revision'],version=current['version'],content=content,publish=False));r.raise_for_status();current=r.json()
                timings.setdefault(label,[]).append((time.perf_counter()-start)*1000);writes[0]+=1;await asyncio.sleep(.2)
        tasks=[asyncio.create_task(read(i)) for i in range(10)]+[asyncio.create_task(write())]
        try:result=await monitor()
        finally:
            stop.set();await asyncio.gather(*tasks)
    return result,dict(seconds=round(time.perf_counter()-began,2),phases=sorted(states),concurrentReaders=10,journalWrites=writes[0])


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True);args=parser.parse_args();timings={}
    with synthetic_server(server_log=args.output.with_suffix('.server.log')) as (url,identities,accounts,cookie):
        with httpx.Client(base_url=url,trust_env=False,timeout=240,headers={'Cookie':cookie,'X-Atlas-Request':'1'}) as c:
            def call(label,method,path,**kw):
                start=time.perf_counter();r=c.request(method,path,**kw);r.raise_for_status();timings.setdefault(label,[]).append((time.perf_counter()-start)*1000);return r.json()
            def closed(name,start,end):
                id=uuid4().hex;base='/api/journal/periods/'+id
                p=call('period','PUT',base,json=dict(version=0,name=name,start=start,end=end,reason='Synthetic preparation'))
                check=call('preview','POST',base+'/preview-close',json=dict(version=p['version']))
                return call('close','POST',base+'/close',json=dict(version=p['version'],previewId=check['previewId'],acknowledgeIssues=True,reason='Synthetic preparation'))
            def enqueue(p):return call('enqueue','POST','/api/final-grades/periods/'+p['id']+'/prepare',json=dict(version=p['version']))
            def wait(job,label):
                start=time.perf_counter()
                while job['status'] in ['queued','running','cancelling']:
                    if time.perf_counter()-start>240:raise TimeoutError('Synthetic preparation observation expired')
                    time.sleep(.15);r=c.get('/api/final-grades/preparations/'+job['id']);r.raise_for_status();job=r.json()
                assert job['status']=='succeeded',job
                timings.setdefault(label,[]).append((time.perf_counter()-start)*1000);return job
            state=c.get('/api/state').json();c.get('/api/final-grades/options').raise_for_status()
            period,seeded=seed(Path(os.environ['TIMETABLE_DB']),state,40);marks=mark(Path(os.environ['TIMETABLE_DB']))
            print(json.dumps(dict(phase='seeded',marks=marks,**seeded)),flush=True)
            first=closed('Synthetic one day',period['start'],period['start']);initial=wait(enqueue(first),'initial_ready')
            scale=call('scale','PUT','/api/final-grades/scales/'+uuid4().hex,json=dict(version=0,name='Synthetic scale',bands=[dict(label='Low',minPercent=0),dict(label='High',minPercent=75)]))
            page=call('sheet','GET','/api/final-grades/books/'+initial['bookId']);proposals=[]
            for row in page['items']:
                r=call('propose','PUT','/api/final-grades/rows/'+row['id'],json=dict(version=row['version'],scaleId=scale['id'],scaleVersion=scale['version'],label='High',submit=True));proposals.append(dict(id=r['id'],version=r['version']))
            approved=call('approve','POST','/api/final-grades/actions',json=dict(action='approve',rows=proposals))['items']
            call('publish','POST','/api/final-grades/actions',json=dict(action='publish',rows=[dict(id=r['id'],version=r['version']) for r in approved]))
            day=date.fromisoformat(period['end'])+timedelta(days=3)
            lessons=call('write_day','GET','/api/journal/day',params=dict(day=day.isoformat()))['lessons']
            entry=call('write_entry','GET','/api/journal/entry',params=dict(day=day.isoformat(),lessonKey=lessons[0]['lessonKey']))
            full=closed('Synthetic term',period['start'],period['end']);job=enqueue(full)
            same=enqueue(full);assert same['id']==job['id']
            ready,load=asyncio.run(during_build(url,cookie,identities,job,first['id'],sorted({r['studentId'] for r in approved}),entry,timings))
            assert load['journalWrites']>0 and 'saving' in load['phases'] and 'journals' in load['phases']
            fullpage=call('sheet','GET','/api/final-grades/books/'+ready['bookId']);assert fullpage['total']==9400
            with closing(sqlite3.connect(os.environ['TIMETABLE_DB'])) as db:
                facts=db.execute('SELECT count(*),sum(json_array_length(works)),sum(length(cast(summary AS BLOB))+length(cast(works AS BLOB))) FROM grading_facts').fetchone();assert facts[1]==marks
            repeated=closed('Synthetic repeated source',period['start'],period['end']);wait(enqueue(repeated),'cached_ready')
            with closing(sqlite3.connect(os.environ['TIMETABLE_DB'])) as db:
                assert db.execute('SELECT count(*) FROM grading_facts').fetchone()[0]==facts[0]
                assert db.execute('SELECT count(*) FROM grading_marks').fetchone()[0]==0
                assert db.execute('SELECT count(*) FROM grading_books').fetchone()[0]==3
            assert c.get('/api/state').json()==state
            result=dict(synthetic=True,stateUnchanged=True,accounts=accounts,seed=seeded,marks=marks,sharedFacts=facts[0],factPayloadMiB=round(facts[2]/1024**2,1),gradeRows=9400,books=3,sharedFactsReused=True,load=load,endpoints={k:summary(v) for k,v in timings.items()},limitations='40 days, two assessments per pupil/lesson, 50 published outcomes; ten readers with pauses and one draft journal writer during preparation. Ready sessions, local HTTP. No annual history, login burst, full school publication, generator contention or sustained production traffic.')
            print(json.dumps(dict(phase='measured',**result)),flush=True)
    args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(result,indent=2),encoding='utf-8')


if __name__=='__main__':main()
