"""Isolated parent-registry lifecycle and read-path load probe."""
import argparse
import asyncio
from collections import Counter
import json
from pathlib import Path
import time
import httpx
from .synthetic_server import synthetic_server
from .benchmark_homework import summary


async def probe(url,identities,admin,count,concurrency):
    timings={};statuses=Counter();semaphore=asyncio.Semaphore(concurrency)
    async with httpx.AsyncClient(base_url=url,trust_env=False,timeout=120,limits=httpx.Limits(max_connections=concurrency,max_keepalive_connections=concurrency)) as client:
        async def call(label,method,path,cookie,expected=200,**kwargs):
            started=time.perf_counter();r=await client.request(method,'/api/'+path,headers={'Cookie':cookie,'X-Atlas-Request':'1'},**kwargs)
            statuses[str(r.status_code)]+=1
            if r.status_code!=expected:raise AssertionError(f'{label}: {r.status_code} {r.text[:200]}')
            timings.setdefault(label,[]).append((time.perf_counter()-started)*1000);return r.json()
        async def workflow(i):
            async with semaphore:
                child=i*20;parent=identities[1100+child][0];other=identities[2200+child][0]
                schedule=f'portal/schedule?workspace=school&view=student&entityId=s{child}'
                path=f'access/families/parent{child}/s{child}?workspace=school'
                value=await call('prepare','GET',path,admin)
                body=dict(version=value['version'],accountVersion=value['accountVersion'],relationship='guardian',verified=True,evidence='Synthetic staff verification',startDate=None,endDate=None,reason='Synthetic lifecycle',status='revoked')
                await call('before','GET',schedule,parent)
                await call('revoke','PUT',path,admin,json=body)
                await call('revoked_schedule','GET',schedule,parent,expected=403)
                mine=await call('own_registry','GET','portal/family',parent);assert mine['items'][0]['activity']=='revoked'
                await call('other_guardian','GET',schedule,other)
                current=await call('reopen','GET',path,admin)
                await call('restore','PUT',path,admin,json=body|dict(version=current['version'],accountVersion=current['accountVersion'],status='active'))
                await call('restored_schedule','GET',schedule,parent)
        start=time.perf_counter();await asyncio.gather(*(workflow(i) for i in range(count)))
        page=await call('list','GET','access/families?workspace=school',admin);assert len(page['items'])==50 and page['nextOffset']==50
        page2=await call('list_next','GET','access/families?workspace=school&offset=50',admin)
        assert not {(r['parentId'],r['studentId']) for r in page['items']} & {(r['parentId'],r['studentId']) for r in page2['items']}
    return dict(concurrency=concurrency,completed_workflows=count,duration_s=round(time.perf_counter()-start,2),statuses=dict(statuses),endpoints={key:summary(value) for key,value in timings.items()})


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--families',type=int,default=40);parser.add_argument('--concurrency',type=int,default=20);args=parser.parse_args()
    if not 1<=args.families<=55 or not 1<=args.concurrency<=100:parser.error('Use 1..55 families and 1..100 concurrency.')
    with synthetic_server() as (url,identities,counts,cookie):
        with httpx.Client(base_url=url,trust_env=False,headers={'Cookie':cookie},timeout=30) as client:
            before=client.get('/api/state').json();result=asyncio.run(probe(url,identities,cookie,args.families,args.concurrency));assert before==client.get('/api/state').json()
    report=dict(synthetic=True,state_unchanged=True,data=counts,run=result,limitations='Existing sessions, short burst on one laptop; no login storm, uploads, generation, TLS/network or sustained traffic. Not production school capacity evidence.')
    args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(report,indent=2),encoding='utf-8');print(json.dumps(result))


if __name__=='__main__':main()
