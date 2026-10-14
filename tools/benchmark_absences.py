"""Synthetic school: private evidence, class review and reversible attendance."""
import argparse
import asyncio
from collections import Counter
import json
from pathlib import Path
import time
from uuid import uuid4
import httpx
from .synthetic_server import synthetic_server
from .benchmark_homework import summary


async def probe(url,identities,admin,count,concurrency):
    timings={};statuses=Counter();semaphore=asyncio.Semaphore(concurrency);content=b'x'*(256*1024)
    async with httpx.AsyncClient(base_url=url,trust_env=False,timeout=120,limits=httpx.Limits(max_connections=concurrency,max_keepalive_connections=concurrency)) as client:
        async def call(label,method,path,cookie,expected=200,**kwargs):
            start=time.perf_counter();r=await client.request(method,'/api/'+path,headers={'Cookie':cookie,'X-Atlas-Request':'1'},**kwargs)
            statuses[str(r.status_code)]+=1;timings.setdefault(label,[]).append((time.perf_counter()-start)*1000)
            if r.status_code!=expected:raise AssertionError(f'{label}: {r.status_code} {r.text[:150]}')
            return r
        async def workflow(i):
            async with semaphore:
                child=i*20;parent=identities[1100+child][0];other=identities[2200+child][0];teacher=identities[3300+i][0];request=str(uuid4())
                data=dict(requestId=request,studentId=f's{child}',startDate='2026-10-13',endDate='2026-10-13',reason='Synthetic private absence')
                preview=(await call('preview','POST','journal/absences/preview',parent,json=data)).json();assert preview['lessons']
                for attempt in range(60):
                    start=time.perf_counter();r=await client.post('/api/journal/absence-files',params=dict(studentId=f's{child}',draftId=request,filename='evidence.txt'),headers={'Cookie':parent,'X-Atlas-Request':'1'},content=content)
                    statuses[str(r.status_code)]+=1;timings.setdefault('upload',[]).append((time.perf_counter()-start)*1000)
                    if r.status_code==200:break
                    if r.status_code!=429:raise AssertionError(r.text[:150])
                    await asyncio.sleep(.2+(i%7)*.03)
                else:raise AssertionError('Upload remained busy.')
                file=r.json()['id'];data['fileIds']=[file]
                sent=(await call('submit','POST','journal/absences',parent,json=data)).json();id=sent['id']
                await call('private_read','GET','journal/absences/'+id,teacher)
                downloaded=await call('evidence','GET','journal/absence-files/'+file,teacher);assert downloaded.content==content
                await call('other_guardian_denied','GET','journal/absences/'+id,other,expected=404)
                decision=dict(requestId=str(uuid4()),version=1,action='approve',comment='Checked by synthetic class coordinator')
                accepted=(await call('approve','POST',f'journal/absences/{id}/decision',teacher,json=decision)).json()
                query=f'journal/report?studentId=s{child}&start=2026-10-13&end=2026-10-13'
                result=(await call('journal_excused','GET',query,parent)).json();assert result['summary']['attendance']['excused']==1
                await call('revoke','POST',f'journal/absences/{id}/decision',teacher,json=decision|dict(requestId=str(uuid4()),version=accepted['version'],action='revoke'))
                result=(await call('journal_restored','GET',query,parent)).json();assert result['summary']['attendance']['absent']==1
        start=time.perf_counter();await asyncio.gather(*(workflow(i) for i in range(count)))
        await call('review_queue','GET','journal/absences',admin)
        return dict(concurrency=concurrency,completed_workflows=count,attachmentBytes=len(content),duration_s=round(time.perf_counter()-start,2),statuses=dict(statuses),endpoints={k:summary(v) for k,v in timings.items()})


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True);parser.add_argument('--families',type=int,default=40);parser.add_argument('--concurrency',type=int,default=20);args=parser.parse_args()
    if not 1<=args.families<=55 or not 1<=args.concurrency<=100:parser.error('Use 1..55 families and 1..100 concurrency.')
    with synthetic_server() as (url,identities,counts,cookie):
        with httpx.Client(base_url=url,trust_env=False,timeout=120,headers={'Cookie':cookie,'X-Atlas-Request':'1'}) as client:
            before=client.get('/api/state').json();day='2026-10-13'
            assigned=client.put('/api/journal/absences/reviewers',json=dict(version=0,assignments={f'c{i}':[f'teacher{i}'] for i in range(args.families)}));assigned.raise_for_status()
            rows=client.get('/api/journal/day',params=dict(day=day)).json()['lessons']
            for i in range(args.families):
                key=next(r['lessonKey'] for r in rows if r['lesson']['group']==f'Class {i}' and r['lesson']['subject']=='Subject 0')
                entry=client.get('/api/journal/entry',params=dict(lessonKey=key,day=day)).json();document=entry['document']['content']
                for pupil in document['records']:pupil['attendance']='absent'
                written=client.put('/api/journal/entry',json=dict(lessonKey=key,day=day,revision=0,version=0,content=document,publish=True));written.raise_for_status()
            result=asyncio.run(probe(url,identities,cookie,args.families,args.concurrency));assert client.get('/api/state').json()==before
    report=dict(synthetic=True,state_unchanged=True,data=counts,run=result,limitations='Existing sessions and short laptop burst; upload 429 retries included. No login storm, simultaneous generator, external TLS/network or sustained workload; not whole-school capacity evidence.')
    args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(report,indent=2),encoding='utf-8');print(json.dumps(result))


if __name__=='__main__':main()
