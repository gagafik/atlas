"""Synthetic marked journals, final-grade preparation and concurrent personal reads."""
import argparse,asyncio,json,os,sqlite3,time,tempfile
from contextlib import closing
from pathlib import Path
from uuid import uuid4
import httpx
from .synthetic_server import synthetic_server
from .benchmark_journal_coverage import seed
from .benchmark_homework import summary


def mark(path):
    path=path.resolve()
    if not path.is_relative_to(Path(tempfile.gettempdir()).resolve()) or not path.parent.name.startswith('atlas-message-load-'):raise ValueError('Synthetic database required.')
    count=0
    with closing(sqlite3.connect(path)) as db,db:
        for key,raw in db.execute('SELECT lesson_key,document FROM journal_entries'):
            doc=json.loads(raw);doc['content']['assessments']=[dict(id='points',name='Synthetic points',kind='points',maxScore=10,weight=1),dict(id='label',name='Synthetic label',kind='label',maxScore=10,weight=1)]
            for r in doc['content']['records']:
                r['grades']=[dict(assessmentId='points',score=8,label='',comment=''),dict(assessmentId='label',score=None,label='Pass',comment='')];count+=2
            raw=json.dumps(doc,ensure_ascii=False)
            db.execute('UPDATE journal_entries SET document=?,published_document=? WHERE workspace=? AND lesson_key=?',(raw,raw,'school',key))
    return count


async def read_reports(url,identities,period,students,timings):
    limit=asyncio.Semaphore(20)
    async with httpx.AsyncClient(base_url=url,trust_env=False,timeout=120) as c:
        async def one(i):
            async with limit:
                student=students[i%len(students)]
                cookie=next(cookie for cookie,view,id in identities if view=='student' and id==student)
                headers={'Cookie':cookie,'X-Atlas-Request':'1'}
                start=time.perf_counter();r=await c.get('/api/final-grades/report',headers=headers,params=dict(studentId=student,periodId=period));r.raise_for_status()
                assert r.json()['items'] and all(v['student']['id']==student for v in r.json()['items'])
                timings.setdefault('personal_report',[]).append((time.perf_counter()-start)*1000)
        await asyncio.gather(*(one(i) for i in range(100)))


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True);args=parser.parse_args();timings={}
    with synthetic_server() as (url,identities,accounts,cookie):
        with httpx.Client(base_url=url,trust_env=False,timeout=240,headers={'Cookie':cookie,'X-Atlas-Request':'1'}) as c:
            def call(label,method,path,**kw):
                start=time.perf_counter();r=c.request(method,path,**kw);r.raise_for_status();timings.setdefault(label,[]).append((time.perf_counter()-start)*1000);return r.json()
            state=c.get('/api/state').json();c.get('/api/final-grades/options').raise_for_status()
            period,seeded=seed(Path(os.environ['TIMETABLE_DB']),state,40);marks=mark(Path(os.environ['TIMETABLE_DB']))
            print(json.dumps(dict(phase='seeded',marks=marks,**seeded)),flush=True)
            id=uuid4().hex;base='/api/journal/periods/'+id
            p=call('period','PUT',base,json=dict(version=0,name='Synthetic term',start=period['start'],end=period['end'],reason='Synthetic grade measurement'))
            preview=call('preview','POST',base+'/preview-close',json=dict(version=p['version']))
            p=call('close','POST',base+'/close',json=dict(version=p['version'],previewId=preview['previewId'],acknowledgeIssues=True,reason='Synthetic grade measurement'))
            began=time.perf_counter();job=call('enqueue','POST',f'/api/final-grades/periods/{id}/prepare',json=dict(version=p['version']))
            while job['status'] in ['queued','running','cancelling']:
                if time.perf_counter()-began>240:raise TimeoutError('Synthetic preparation timed out; inspect the same job.')
                time.sleep(.25);response=c.get('/api/final-grades/preparations/'+job['id']);response.raise_for_status();job=response.json()
            assert job['status']=='succeeded',job
            timings.setdefault('prepare',[]).append((time.perf_counter()-began)*1000);b=dict(id=job['bookId'])
            s=call('scale','PUT','/api/final-grades/scales/'+uuid4().hex,json=dict(version=0,name='Synthetic scale',bands=[dict(label='Low',minPercent=0),dict(label='High',minPercent=75)]))
            page=call('sheet','GET','/api/final-grades/books/'+b['id']);assert page['total']>=1100
            submitted=[]
            for row in page['items']:
                r=call('propose','PUT','/api/final-grades/rows/'+row['id'],json=dict(version=row['version'],scaleId=s['id'],scaleVersion=s['version'],label='High',submit=True));submitted.append(dict(id=r['id'],version=r['version']))
            reviewed=call('approve','POST','/api/final-grades/actions',json=dict(rows=submitted,action='approve'))['items']
            call('publish','POST','/api/final-grades/actions',json=dict(rows=[dict(id=r['id'],version=r['version']) for r in reviewed],action='publish'))
            first=page['items'][0];evidence=call('evidence','GET',f'/api/final-grades/rows/{first["id"]}/evidence');assert evidence['items'] and evidence['recommendations']
            asyncio.run(read_reports(url,identities,id,sorted({r['studentId'] for r in reviewed}),timings));assert c.get('/api/state').json()==state
            with closing(sqlite3.connect(os.environ['TIMETABLE_DB'])) as db:
                stored=db.execute('SELECT sum(json_array_length(works)) FROM grading_facts').fetchone()[0];assert stored==marks
                size=db.execute('SELECT sum(length(cast(summary AS BLOB))+length(cast(works AS BLOB))) FROM grading_facts').fetchone()[0]
            result=dict(synthetic=True,stateUnchanged=True,accounts=accounts,seed=seeded,marks=marks,gradeRows=page['total'],sourceMiB=round(size/1024**2,1),concurrentReaders=20,endpoints={k:summary(v) for k,v in timings.items()},limitations='40 teaching days, two marks per pupil/lesson, 50 published results, 100 personal requests with ready sessions. Not annual history, full school publication, concurrent writers/generator, internet/TLS, login storm or sustained load. Background preparation copies shared facts in bounded transactions.')
            print(json.dumps(dict(phase='measured',**result)),flush=True)
    args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(result,indent=2),encoding='utf-8')


if __name__=='__main__':main()
