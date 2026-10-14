"""Synthetic maximum-size batches with repeat requests and attendance checks."""
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


async def probe(url,identities,admin):
    timings={};statuses=Counter();limit=asyncio.Semaphore(20)
    async with httpx.AsyncClient(base_url=url,trust_env=False,timeout=120) as client:
        async def call(label,method,path,cookie,**kwargs):
            async with limit:
                start=time.perf_counter()
                response=await client.request(method,'/api/'+path,headers={'Cookie':cookie,'X-Atlas-Request':'1'},**kwargs)
                timings.setdefault(label,[]).append((time.perf_counter()-start)*1000);statuses[str(response.status_code)]+=1
                response.raise_for_status()
                return response.json()
        async def read(i,attendance):
            result=await call('report_'+attendance,'GET',f'journal/report?studentId=s{i}&start=2026-10-13&end=2026-10-13',identities[1100+i][0])
            assert result['summary']['attendance'][attendance]==1
        async def batch(items,action):
            preview=await call('bulk_preview_'+action,'POST','journal/absences/bulk/preview',admin,json=dict(requestId=str(uuid4()),action=action,comment='Synthetic reviewed batch',items=[dict(id=n['id'],version=n['version']) for n in items]))
            assert len(preview['items'])==50
            result=await call('bulk_'+action,'POST',f"journal/absences/bulk/{preview['id']}/apply",admin)
            assert result['count']==50
            retry=await call('bulk_retry','POST',f"journal/absences/bulk/{preview['id']}/apply",admin)
            assert retry==result
            return result['items']
        start=time.perf_counter()
        notices=await asyncio.gather(*(call('submit','POST','journal/absences',identities[1100+i][0],json=dict(requestId=str(uuid4()),studentId=f's{i}',startDate='2026-10-13',endDate='2026-10-13',reason='Synthetic absence')) for i in range(100)))
        approved=await asyncio.gather(*(batch(notices[i:i+50],'approve') for i in [0,50]))
        await asyncio.gather(*(read(i,'excused') for i in range(100)))
        await asyncio.gather(*(batch(items,'revoke') for items in approved))
        await asyncio.gather(*(read(i,'absent') for i in range(100)))
        return dict(duration_s=round(time.perf_counter()-start,2),students=100,batchSize=50,concurrentBatches=2,statuses=dict(statuses),endpoints={k:summary(v) for k,v in timings.items()})


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    with synthetic_server() as (url,identities,counts,cookie):
        with httpx.Client(base_url=url,trust_env=False,timeout=120,headers={'Cookie':cookie,'X-Atlas-Request':'1'}) as client:
            before=client.get('/api/state').json();day='2026-10-13'
            lessons=client.get('/api/journal/day',params=dict(day=day)).json()['lessons']
            for i in range(5):
                key=next(r['lessonKey'] for r in lessons if r['lesson']['group']==f'Class {i}' and r['lesson']['subject']=='Subject 0')
                entry=client.get('/api/journal/entry',params=dict(day=day,lessonKey=key)).json()
                content=entry['document']['content']
                for record in content['records']:record['attendance']='absent'
                client.put('/api/journal/entry',json=dict(lessonKey=key,day=day,revision=0,version=0,content=content,publish=True)).raise_for_status()
            result=asyncio.run(probe(url,identities,cookie));assert client.get('/api/state').json()==before
    report=dict(synthetic=True,stateUnchanged=True,data=counts,run=result,limitations='Short laptop burst with existing sessions, two simultaneous batches of 50 and 20 simultaneous parent requests; no login storm, generation, TLS/network, or sustained load. Not production capacity evidence.')
    args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(result))


if __name__=='__main__':main()
