"""Synthetic HTTP moderation and recovery workflows; never use the school's DB."""
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


async def probe(url, identities, admin_cookie, count, concurrency):
    timings={};statuses=Counter();semaphore=asyncio.Semaphore(concurrency)
    async with httpx.AsyncClient(base_url=url,trust_env=False,timeout=120,limits=httpx.Limits(max_connections=concurrency,max_keepalive_connections=concurrency)) as client:
        async def call(label,method,path,cookie,expected=200,**kwargs):
            start=time.perf_counter()
            r=await client.request(method,'/api/'+path,headers={'Cookie':cookie,'X-Atlas-Request':'1'},**kwargs)
            statuses[str(r.status_code)]+=1
            if r.status_code!=expected:raise AssertionError(f'{label}: {r.status_code} {r.text[:200]}')
            timings.setdefault(label,[]).append((time.perf_counter()-start)*1000)
            return r.json()
        async def workflow(i):
            async with semaphore:
                pupil=identities[i*20][0]
                teacher=next(cookie for cookie,view,id in identities if view=='teacher' and id==f't{i}')
                thread=(await call('create','POST','messages/threads',teacher,json=dict(requestId=str(uuid4()),kind='group',title='Synthetic review',text='Evidence',groupIds=[f'g{i}'],roles=['student'])))['id']
                path='messages/threads/'+thread
                view=await call('history','GET',path,pupil);post=view['posts'][0]['id']
                await call('add_successor','POST',path+'/participants/add',teacher,json=dict(requestId=str(uuid4()),version=1,userIds=['load-admin'],shareHistory=True))
                report=(await call('report','POST',f'messages/posts/{post}/report',pupil,json=dict(requestId=str(uuid4()),reason='Synthetic review',shareEvidence=True)))['id']
                report_path='messages/reports/'+report
                await call('evidence','GET',report_path,admin_cookie)
                await call('hide','POST',report_path+'/decision',admin_cookie,json=dict(requestId=str(uuid4()),version=1,postVersion=0,action='hide',note='Synthetic hide'))
                hidden=await call('hidden_history','GET',path,pupil);assert hidden['posts'][0]['hidden']
                await call('restore','POST',report_path+'/decision',admin_cookie,json=dict(requestId=str(uuid4()),version=2,postVersion=1,action='restore',note='Synthetic restore'))
                await call('disable_manager','PUT',f'access/users/teacher{i}',admin_cookie,json=dict(login=f'teacher{i}',name=f'teacher{i}',role='teacher',active=False,version=1,workspaces=[],grants=[dict(workspace='school',view='teacher',entityIds=[f't{i}'])]))
                await call('unavailable','GET',path,pupil,expected=404)
                await call('recover','POST','messages/recovery/'+thread,admin_cookie,json=dict(requestId=str(uuid4()),version=4,userId='load-admin',reason='Synthetic departure'))
                final=await call('recovered_history','GET',path,pupil)
                assert final['version']==5 and not final['posts'][0]['hidden'] and final['managerId']=='load-admin'
        start=time.perf_counter();await asyncio.gather(*(workflow(i) for i in range(count)))
        queue=await call('reports_list','GET','messages/reports',admin_cookie);assert len(queue['items'])==count
        missing=await call('recovery_list','GET','messages/recovery',admin_cookie);assert missing['items']==[]
    return dict(concurrency=concurrency,completed_workflows=count,duration_s=round(time.perf_counter()-start,2),statuses=dict(statuses),endpoints={key:summary(values) for key,values in timings.items()})


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--groups',type=int,default=40);parser.add_argument('--concurrency',type=int,default=20);args=parser.parse_args()
    if not 1<=args.groups<=50 or not 1<=args.concurrency<=100:parser.error('Use 1..50 groups and 1..100 concurrency.')
    with synthetic_server() as (url,identities,counts,cookie):
        with httpx.Client(base_url=url,trust_env=False,headers={'Cookie':cookie},timeout=30) as client:
            before=client.get('/api/state').json()
            result=asyncio.run(probe(url,identities,cookie,args.groups,args.concurrency))
            assert before==client.get('/api/state').json()
    report=dict(synthetic=True,state_unchanged=True,data=counts,run=result,limitations='Existing sessions on one laptop; short burst, no uploads/login storm/generation/TLS/network/sustained traffic. Not school capacity evidence.')
    args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(report,indent=2),encoding='utf-8');print(json.dumps(result))


if __name__=='__main__':main()
