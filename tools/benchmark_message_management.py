"""Isolated HTTP probe of adding participants, management handoff and revocation."""
import argparse
import asyncio
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import time
from uuid import uuid4

import httpx

from .synthetic_server import synthetic_server
from .benchmark_homework import summary


async def probe(url, identities, admin_cookie, concurrency, count):
    timings = {}; statuses = Counter(); semaphore = asyncio.Semaphore(concurrency)
    async with httpx.AsyncClient(base_url=url, trust_env=False, timeout=120,
                                 limits=httpx.Limits(max_connections=concurrency,max_keepalive_connections=concurrency)) as client:
        async def request(label, method, path, cookie, *, expected=200, **kwargs):
            start = time.perf_counter()
            r = await client.request(method, '/api/messages/'+path, headers={'Cookie':cookie,'X-Atlas-Request':'1'}, **kwargs)
            statuses[str(r.status_code)] += 1
            if r.status_code != expected: raise AssertionError(f'{label}: {r.status_code} {r.text[:200]}')
            timings.setdefault(label, []).append((time.perf_counter()-start)*1000)
            return r

        async def workflow(index):
            async with semaphore:
                pupil = index*20; parent = identities[1100+pupil][0]
                teacher = next(cookie for cookie,view,id in identities if view=='teacher' and id==f't{index}')
                created = await request('create', 'POST', 'threads', teacher,
                                        json=dict(requestId=str(uuid4()),kind='group',title='Synthetic group',text='Initial history',groupIds=[f'g{index}'],roles=['student']))
                path = 'threads/'+created.json()['id']
                await request('before_join', 'GET', path, parent, expected=404)
                choices = (await request('candidates', 'GET', path+'/candidates', teacher)).json()
                assert f'parent{pupil}' in {u['id'] for u in choices['items']}
                membership = (await request('roster', 'GET', path+'/management', teacher)).json()
                assert len(membership['members'])==21
                await request('add', 'POST', path+'/participants/add', teacher,
                              json=dict(requestId=str(uuid4()),version=1,userIds=[f'parent{pupil}','load-admin'],shareHistory=True))
                joined = (await request('joined_history', 'GET', path, parent)).json()
                assert len(joined['posts'])==2 and joined['version']==2
                await request('transfer', 'POST', path+'/manager', teacher,
                              json=dict(requestId=str(uuid4()),version=2,userId='load-admin'))
                await request('former_manager', 'GET', path+'/management', teacher, expected=403)
                await request('leave', 'POST', path+'/participants', teacher,
                              json=dict(version=3,userId=f'teacher{index}',action='leave'))
                await request('revoked_read', 'GET', path, teacher, expected=404)
                final = (await request('new_manager', 'GET', path+'/management', admin_cookie)).json()
                assert final['managerId']=='load-admin' and final['version']==4 and len(final['members'])==22
        start = time.perf_counter()
        await asyncio.gather(*(workflow(index) for index in range(count)))
    return dict(concurrency=concurrency,completed_workflows=count,duration_s=round(time.perf_counter()-start,2),
                statuses=dict(statuses),endpoints={label:summary(values) for label,values in timings.items()})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--concurrency',type=int,default=20)
    parser.add_argument('--groups',type=int,default=40)
    args = parser.parse_args()
    if not 1<=args.concurrency<=100 or not 1<=args.groups<=55: parser.error('Use concurrency 1..100 and groups 1..55.')
    with synthetic_server() as (url,identities,counts,cookie):
        with httpx.Client(base_url=url,trust_env=False,timeout=30,headers={'Cookie':cookie}) as admin:
            before = admin.get('/api/state').json()
            result = asyncio.run(probe(url,identities,cookie,args.concurrency,args.groups))
            assert before==admin.get('/api/state').json()
    report = dict(created=datetime.now(timezone.utc).isoformat(),synthetic=True,state_unchanged=True,data=counts,run=result,
                  limitations='Existing sessions, no think time. 40 short groups by default, 21 initial members each. Expected 403/404 exercise access boundaries. One laptop, no files, login storm, generation, TLS/network or sustained traffic; not production capacity evidence.')
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result),flush=True)


if __name__=='__main__': main()
