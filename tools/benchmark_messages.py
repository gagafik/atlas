"""Measure synthetic conversations and a school announcement on an isolated API."""
import argparse
import asyncio
from collections import Counter
from datetime import datetime,timezone
import json
from pathlib import Path
import time
from uuid import uuid4
import httpx
from .synthetic_server import synthetic_server
from .benchmark_homework import summary


async def probe(url,identities,cookie,concurrency,count):
    timings={};statuses=Counter();semaphore=asyncio.Semaphore(concurrency)
    async with httpx.AsyncClient(base_url=url,trust_env=False,timeout=120,limits=httpx.Limits(max_connections=concurrency,max_keepalive_connections=concurrency)) as client:
        async def request(label,method,path,session,**kwargs):
            started=time.perf_counter()
            r=await client.request(method,'/api/messages/'+path,headers={'Cookie':session,'X-Atlas-Request':'1'},**kwargs)
            timings.setdefault(label,[]).append((time.perf_counter()-started)*1000);statuses[str(r.status_code)]+=1
            r.raise_for_status();return r.json()
        announcement=await request('announcement','POST','threads',cookie,json=dict(requestId=str(uuid4()),kind='announcement',title='Synthetic school announcement',text='Read and acknowledge.',roles=['parent'],schoolwide=True,requireAck=True))
        async def workflow(pupil):
            async with semaphore:
                parent=identities[1100+pupil][0]
                teacher=next(c for c,view,id in identities if view=='teacher' and id==f't{pupil//20}')
                value=await request('create','POST','threads',parent,json=dict(requestId=str(uuid4()),title='Synthetic question',text='Question for the teacher.',recipientId=f'teacher{pupil//20}',studentId=f's{pupil}'))
                path='threads/'+value['id']
                thread=await request('teacher_read','GET',path,teacher)
                await request('reply','POST',path+'/reply',teacher,json=dict(requestId=str(uuid4()),version=thread['version'],text='Teacher answer.'))
                visible=await request('parent_read','GET',path,parent)
                assert len(visible['posts'])==2
                await request('read_marker','PUT',path+'/read',parent,json=dict(throughId=visible['latestId']))
                await request('inbox','GET','threads',parent)
                ann=await request('announcement_read','GET','threads/'+announcement['id'],parent)
                assert ann['participants']==[] and not ann['canReply']
                await request('ack','POST','threads/'+announcement['id']+'/change',parent,json=dict(version=ann['version'],action='acknowledge'))
        began=time.perf_counter()
        await asyncio.gather(*(workflow(i*1100//count) for i in range(count)))
        elapsed=time.perf_counter()-began
        ann=await request('receipts','GET','threads/'+announcement['id'],cookie)
        assert len(ann['participants'])==2200 and sum(p['acknowledged'] for p in ann['participants'])==count
    return dict(concurrency=concurrency,completed_workflows=count,duration_s=round(elapsed,2),statuses=dict(statuses),endpoints={name:summary(values) for name,values in timings.items()})


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True);parser.add_argument('--concurrency',type=int,default=20);parser.add_argument('--families',type=int,default=60)
    args=parser.parse_args()
    if not 1<=args.concurrency<=100 or not 1<=args.families<=100:parser.error('Use 1..100 concurrency and families.')
    with synthetic_server() as (url,identities,counts,cookie):
        with httpx.Client(base_url=url,trust_env=False,timeout=30,headers={'Cookie':cookie}) as admin:
            before=admin.get('/api/state').json()
            result=asyncio.run(probe(url,identities,cookie,args.concurrency,args.families))
            assert before==admin.get('/api/state').json()
        report=dict(created=datetime.now(timezone.utc).isoformat(),synthetic=True,state_unchanged=True,data=counts,run=result,
                    limitations='Existing sessions, no think time. Text messages only, one school announcement. Client/server share a laptop. No login storm, generation, Internet/TLS or long-lived traffic; not production capacity evidence.')
        args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(result),flush=True)


if __name__=='__main__':main()
