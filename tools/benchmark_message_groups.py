"""Isolated group discussions, uploads and revoked downloads under HTTP load."""
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


async def probe(url,identities,concurrency,count):
    timings={};statuses=Counter();semaphore=asyncio.Semaphore(concurrency)
    content=(b'Synthetic project material.\n'*11000)[:256*1024]
    async with httpx.AsyncClient(base_url=url,trust_env=False,timeout=120,limits=httpx.Limits(max_connections=concurrency,max_keepalive_connections=concurrency)) as client:
        async def request(label,method,path,session,*,expected=200,**kwargs):
            began=time.perf_counter()
            for attempt in range(100):
                r=await client.request(method,'/api/messages/'+path,headers={'Cookie':session,'X-Atlas-Request':'1'},**kwargs)
                statuses[str(r.status_code)]+=1
                if r.status_code==429 and label=='upload':
                    await asyncio.sleep(.1+attempt*.01);continue
                if r.status_code!=expected:raise AssertionError(f'{label}: {r.status_code} {r.text[:200]}')
                timings.setdefault(label,[]).append((time.perf_counter()-began)*1000)
                return r
            raise AssertionError('Upload retry limit exceeded')
        async def workflow(index):
            async with semaphore:
                pupil=index*20
                parent=identities[1100+pupil][0]
                teacher=next(c for c,view,id in identities if view=='teacher' and id==f't{index}')
                created=await request('create','POST','threads',teacher,json=dict(requestId=str(uuid4()),kind='group',title='Synthetic class discussion',text='Project planning.',groupIds=[f'g{index}'],roles=['student','parent']))
                id=created.json()['id'];path='threads/'+id
                first=(await request('parent_read','GET',path,parent)).json()
                assert len(first['participants'])==60
                draft=str(uuid4())
                file=(await request('upload','POST','files',parent,params=dict(draftId=draft,threadId=id,filename='project.txt'),content=content)).json()['id']
                await request('reply','POST',path+'/reply',parent,json=dict(requestId=str(uuid4()),version=first['version'],text='Attached material.',draftId=draft,fileIds=[file]))
                thread=(await request('teacher_read','GET',path,teacher)).json()
                assert len(thread['posts'])==2 and thread['posts'][-1]['files'][0]['id']==file
                data=await request('download','GET','files/'+file,teacher)
                assert data.content==content
                await request('inbox','GET','threads',parent)
                await request('remove','POST',path+'/participants',teacher,json=dict(version=thread['version'],userId=f'parent{pupil}',action='remove'))
                await request('revoked_file','GET','files/'+file,parent,expected=404)
                await request('revoked_thread','GET',path,parent,expected=404)
        began=time.perf_counter()
        await asyncio.gather(*(workflow(i) for i in range(count)))
    return dict(concurrency=concurrency,completed_workflows=count,participants_per_group=61,file_bytes=len(content),duration_s=round(time.perf_counter()-began,2),statuses=dict(statuses),endpoints={label:summary(values) for label,values in timings.items()})


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--concurrency',type=int,default=20)
    parser.add_argument('--groups',type=int,default=40)
    args=parser.parse_args()
    if not 1<=args.concurrency<=100 or not 1<=args.groups<=55:parser.error('Use concurrency 1..100 and groups 1..55.')
    with synthetic_server() as (url,identities,counts,cookie):
        with httpx.Client(base_url=url,trust_env=False,timeout=30,headers={'Cookie':cookie}) as admin:
            before=admin.get('/api/state').json()
            result=asyncio.run(probe(url,identities,args.concurrency,args.groups))
            assert before==admin.get('/api/state').json()
        report=dict(created=datetime.now(timezone.utc).isoformat(),synthetic=True,state_unchanged=True,data=counts,run=result,
            limitations='Existing sessions, no think time. 256 KiB text files; upload 429 retried by this probe (UI requires manual repeat); upload latency includes retries. Expected 404 verifies revoked access. One laptop, no login storm, generation, TLS/network or sustained traffic; not production capacity evidence.')
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(result),flush=True)


if __name__=='__main__':main()
