"""Synthetic term closure, shared immutable archives and concurrent teacher reads."""
import argparse,asyncio,json,os,sqlite3,time
from contextlib import closing
from pathlib import Path
from uuid import uuid4
import httpx
from .synthetic_server import synthetic_server
from .benchmark_journal_coverage import seed
from .benchmark_homework import summary


async def read_archives(url,identities,period_id,version,timings):
    limit=asyncio.Semaphore(10)
    async with httpx.AsyncClient(base_url=url,trust_env=False,timeout=120) as c:
        async def teacher(i):
            async with limit:
                headers={'Cookie':identities[3300+i][0],'X-Atlas-Request':'1'}
                path=f'/api/journal/periods/{period_id}/closures/{version}/lessons'
                started=time.perf_counter();r=await c.get(path,headers=headers);r.raise_for_status()
                timings.setdefault('teacher_archive_page',[]).append((time.perf_counter()-started)*1000)
                rows=r.json()['items'];assert len(rows)==50
                started=time.perf_counter();r=await c.get(path,headers=headers,params={'lessonKey':rows[0]['lessonKey']});r.raise_for_status()
                timings.setdefault('teacher_archive_lesson',[]).append((time.perf_counter()-started)*1000)
                assert f't{i}' in r.json()['document']['lesson']['teacherIds']
        await asyncio.gather(*(teacher(i) for i in range(20)))


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    timings={};rejected=0
    with synthetic_server() as (url,identities,accounts,cookie):
        with httpx.Client(base_url=url,trust_env=False,timeout=180,headers={'Cookie':cookie,'X-Atlas-Request':'1'}) as c:
            state=c.get('/api/state').json();c.get('/api/journal/periods').raise_for_status()
            period,seeded=seed(Path(os.environ['TIMETABLE_DB']),state,40)
            print(json.dumps(dict(phase='seeded',**seeded)),flush=True)
            def call(label,method,path,**kw):
                start=time.perf_counter();r=c.request(method,path,**kw);r.raise_for_status()
                timings.setdefault(label,[]).append((time.perf_counter()-start)*1000);return r.json()
            started=time.perf_counter();id=uuid4().hex;base='/api/journal/periods/'+id
            p=call('create','PUT',base,json=dict(version=0,name='Synthetic term',start=period['start'],end=period['end'],kind='quarter',reason='Synthetic closure measurement'))
            check=call('preview','POST',base+'/preview-close',json=dict(version=p['version']))
            assert check['canClose'] and check['counts']['complete']==seeded['savedLessons']
            p=call('close','POST',base+'/close',json=dict(version=p['version'],previewId=check['previewId'],acknowledgeIssues=True,reason='Synthetic closure measurement'))
            asyncio.run(read_archives(url,identities,id,p['version'],timings))
            rows=call('admin_archive_page','GET',f'{base}/closures/2/lessons')['items'];key=rows[0]['lessonKey']
            entry=call('locked_entry','GET','/api/journal/entry',params=dict(lessonKey=key,day=rows[0]['date']))
            assert not entry['writable'] and not entry['canWithdraw']
            payload=dict(lessonKey=key,day=entry['document']['lesson']['date'],revision=entry['revision'],version=entry['version'],content=entry['document']['content'],publish=True)
            assert c.put('/api/journal/entry',json=payload).status_code==403;rejected+=1
            p=call('reopen','POST',base+'/reopen',json=dict(version=p['version'],reason='Synthetic correction'))
            payload['content']['topic']='Corrected synthetic topic'
            call('correct_publication','PUT','/api/journal/entry',json=payload)
            old=call('old_archive','GET',f'{base}/closures/2/lessons',params=dict(lessonKey=key))
            assert old['document']['content']['topic']=='Synthetic topic'
            check=call('preview','POST',base+'/preview-close',json=dict(version=p['version']))
            p=call('close','POST',base+'/close',json=dict(version=p['version'],previewId=check['previewId'],acknowledgeIssues=True,reason='Synthetic reclosure'))
            newer=call('new_archive','GET',f'{base}/closures/{p["version"]}/lessons',params=dict(lessonKey=key))
            assert newer['document']['content']['topic']=='Corrected synthetic topic'
            assert c.get('/api/state').json()==state
            with closing(sqlite3.connect(os.environ['TIMETABLE_DB'])) as db:
                documents,bytes=db.execute('SELECT count(*),sum(length(cast(document AS BLOB))) FROM journal_period_documents').fetchone()
                links=db.execute('SELECT count(*) FROM journal_period_snapshots').fetchone()[0]
                assert documents==seeded['savedLessons']+1 and links==seeded['savedLessons']*2
            run=dict(seconds=round(time.perf_counter()-started,2),concurrentReaders=10,successfulRequests=sum(map(len,timings.values())),expected403=rejected,endpoints={k:summary(v) for k,v in timings.items()},immutableDocuments=documents,archiveLinks=links,documentMiB=round(bytes/1024**2,1))
            print(json.dumps(dict(phase='measured',**run)),flush=True)
    result=dict(synthetic=True,stateUnchanged=True,accounts=accounts,seed=seeded,run=run,limitations='40-day synthetic term with saved documents, directly seeded projections and ready sessions. No grade/history volume, login storm, concurrent writers/generation, external network/TLS or sustained load. Closure still holds one SQLite write transaction; this is not full-school capacity.')
    args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(result,indent=2),encoding='utf-8');print(json.dumps(result))


if __name__=='__main__':main()
