"""Read-load probe of the journal report, day list and homework queue next to concurrent journal saves.

Everything runs against a temporary synthetic database: the school database is never opened.
The tool avoids backend.journal_reads so the same file can measure older code for a before/after table.
"""
import argparse,json,os,sqlite3,tempfile,threading,time
from contextlib import closing
from datetime import date,timedelta
from pathlib import Path
import httpx
from .synthetic_server import synthetic_server
from .benchmark_homework import summary

ROOT=Path(__file__).resolve().parents[1]
LONG=900


def seed(path,state,days):
    from backend.models import State
    from backend.calendar_ops import DayContext,resolve_day
    from backend.journal import snapshot,key_for
    from backend.attendance_projection import populate
    from backend.coverage_projection import populate as coverage_populate
    path=path.resolve()
    if not path.is_relative_to(Path(tempfile.gettempdir()).resolve()) or not path.parent.name.startswith('atlas-message-load-'):raise ValueError('Synthetic temporary DB required.')
    state=State.model_validate(state);assert not state.events and not state.datedLessons
    ctx=DayContext(state);templates={};current=state.settings.yearStart
    for n in range(5):
        day=current+timedelta(days=n)
        templates[n]=[(r,snapshot(state,r)) for r in resolve_day(state,day,ctx)[0] if r['activity']=='lesson']
    days_list=[]
    while len(days_list)<days:
        if current.weekday()<5:days_list.append(current)
        current+=timedelta(days=1)
    started=time.perf_counter();count=0;sample=None
    def entries():
        nonlocal count,sample
        for day in days_list:
            for row,template in templates[day.weekday()]:
                current=row|dict(id=f'{day}:{row["placementId"]}',date=day.isoformat())
                lesson=template|dict(date=day.isoformat(),sourceKey=current['id'])
                key=key_for(current)
                if sample is None:sample=dict(day=day.isoformat(),lessonKey=key)
                records=[dict(studentId=s['id'],attendance='present',lateMinutes=0,comment='',grades=[dict(assessmentId='work',score=5+len(s['id'])%5,label='',comment='')]) for s in lesson['students']]
                doc=json.dumps(dict(lesson=lesson,content=dict(topic='Synthetic topic',homework='Synthetic homework',dueDate=(day+timedelta(days=2)).isoformat(),
                    assessments=[dict(id='work',name='Synthetic work',kind='points',maxScore=10,weight=1)],records=records)),ensure_ascii=False)
                count+=1
                yield ('school',key,1,day.isoformat(),'Synthetic teacher',doc,doc,1,day.isoformat())
    with closing(sqlite3.connect(path)) as db,db:
        db.executemany('INSERT INTO journal_entries VALUES (?,?,?,?,?,?,?,?,?)',entries())
        populate(db)
        coverage_populate(db)
        db.execute("INSERT INTO journal_attendance_epoch VALUES ('school',1)")
        for field,kind in [('document','draft'),('published_document','published')]:
            db.execute(f"INSERT OR IGNORE INTO journal_members SELECT e.workspace,e.lesson_key,json_extract(s.value,'$.id'),json_extract(s.value,'$.name'),? FROM journal_entries e,json_each(e.{field},'$.lesson.students') s",(kind,))
        # The first queue request rebuilds the queue projection on old and new code alike.
        db.execute("DELETE FROM journal_projection_versions WHERE name='homework_queue'")
    return sample,dict(teachingDays=days,savedLessons=count,pupilRecords=count*20,seedSeconds=round(time.perf_counter()-started,2),databaseMiB=round(path.stat().st_size/1024**2,1))


def timed(client,label,timings,path,cookie,**params):
    began=time.perf_counter()
    response=client.get(path,params=params,headers={'Cookie':cookie,'X-Atlas-Request':'1'})
    timings.setdefault(label,[]).append((time.perf_counter()-began)*1000)
    response.raise_for_status();return response.json()


def write_back(client,cookie,sample,count):
    """Save the same lesson again, unchanged, count times; return the latency of each save."""
    headers={'Cookie':cookie,'X-Atlas-Request':'1'};latencies=[]
    record=client.get('/api/journal/entry',params=dict(lessonKey=sample['lessonKey'],day=sample['day']),headers=headers).raise_for_status().json()
    assert record['writable'] and not record['needsSync']
    for _ in range(count):
        body=dict(lessonKey=record['lessonKey'],day=sample['day'],revision=record['revision'],version=record['version'],content=record['document']['content'],publish=False)
        began=time.perf_counter();response=client.put('/api/journal/entry',json=body,headers=headers);latencies.append((time.perf_counter()-began)*1000)
        response.raise_for_status();record=response.json()
    return latencies


def probe(url,identities,admin,year,sample,readers):
    timings={};parent=identities[1100][0];pupil=identities[0][0]
    with httpx.Client(base_url=url,trust_env=False,timeout=LONG) as client:
        state=client.get('/api/state',headers={'Cookie':admin}).json()
        begin=time.perf_counter()
        # The first queue request rebuilds the deleted queue projection; it is not a timed sample.
        timed(client,'projection_rebuild',timings,'/api/journal/homework-queue',admin,filter='all',limit=1,**year)
        rebuild=timings.pop('projection_rebuild')[0]/1000
        def measure(label,path,cookie,repeats=5,**params):
            first=timed(client,label+'_first',timings,path,cookie,**params)
            for _ in range(repeats):last=timed(client,label,timings,path,cookie,**params)
            return first,last
        report,_=measure('admin_report_year','/api/journal/report',admin,studentId='s0',**year)
        measure('parent_report_year','/api/journal/report',parent,studentId='s0',**year)
        queue,_=measure('admin_queue_year','/api/journal/homework-queue',admin,filter='all',limit=50,**year)
        if queue['nextCursor']:
            # Same shape on old and new code: the cursor of the first page, 5 times (cursors stay valid for 15 minutes).
            for _ in range(5):timed(client,'admin_queue_year_page2',timings,'/api/journal/homework-queue',admin,filter='all',limit=50,cursor=queue['nextCursor'],**year)
        pupil_queue,_=measure('pupil_queue_default','/api/journal/homework-queue',pupil)
        day,_=measure('admin_day','/api/journal/day',admin,day=sample['day'])
        alone=write_back(client,admin,sample,10)
        stop=threading.Event();reads=[];failures=[]
        def reader():
            with httpx.Client(base_url=url,trust_env=False,timeout=LONG) as own:
                while not stop.is_set():
                    began=time.perf_counter()
                    try:
                        own.get('/api/journal/report',params=dict(studentId='s0',**year),headers={'Cookie':admin,'X-Atlas-Request':'1'}).raise_for_status()
                        reads.append((time.perf_counter()-began)*1000)
                    except httpx.HTTPError as exc:failures.append(repr(exc))
        threads=[threading.Thread(target=reader,daemon=True) for _ in range(readers)]
        for thread in threads:thread.start()
        try:
            time.sleep(1)
            during=write_back(client,admin,sample,10)
        finally:
            stop.set()
            for thread in threads:thread.join(timeout=LONG)
        assert not failures,failures[:3]
        assert client.get('/api/state',headers={'Cookie':admin}).json()==state
    endpoints={k:summary(v) for k,v in timings.items() if not k.endswith('_first')}
    firsts={k[:-6]:round(v[0],1) for k,v in timings.items() if k.endswith('_first')}
    write_alone,write_reads=summary(alone),summary(during)
    blocked=write_reads['max_ms']>2*write_alone['p95_ms']+0.5*endpoints['admin_report_year']['p50_ms']
    return dict(seconds=round(time.perf_counter()-begin,2),projectionRebuildSeconds=round(rebuild,2),firstRequestMs=firsts,endpoints=endpoints,
                reportFirstPage=dict(rows=len(report['rows']),nextAfter=report.get('nextAfter') is not None,pagedSummary=report.get('summary') is not None),
                queueFirstPageItems=len(queue['items']),pupilQueueItems=len(pupil_queue['items']),pupilQueueWindow=pupil_queue.get('window','range'),dayLessons=len(day['lessons']),
                write_alone=write_alone,write_during_reads=write_reads,readers=readers,readsDuringWrites=summary(reads) if reads else None,writeBlockedByRead=blocked)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True);parser.add_argument('--days',type=int,default=170);parser.add_argument('--readers',type=int,default=5)
    args=parser.parse_args()
    if not 5<=args.days<=200:parser.error('Use 5..200 teaching days.')
    if not 1<=args.readers<=20:parser.error('Use 1..20 concurrent readers.')
    if args.output.resolve().is_relative_to((ROOT/'data').resolve()):parser.error('Результаты замера сохраняйте вне каталога data/.')
    with synthetic_server(server_log=args.output.with_suffix('.server.log')) as (url,identities,accounts,cookie):
        with httpx.Client(base_url=url,trust_env=False,timeout=LONG,headers={'Cookie':cookie,'X-Atlas-Request':'1'}) as c:
            state=c.get('/api/state').json();c.get('/api/journal/options').raise_for_status()
            sample,seeded=seed(Path(os.environ['TIMETABLE_DB']),state,args.days)
            print(json.dumps(dict(phase='seeded',**seeded)),flush=True)
        year=dict(start=state['settings']['yearStart'],end=state['settings']['yearEnd'])
        run=probe(url,identities,cookie,year,sample,args.readers)
    result=dict(synthetic=True,stateUnchanged=True,accounts=accounts,seed=seeded,run=run,
                limitations='One machine, synthetic seeded documents (20 pupils per lesson, one points work with a grade per pupil) inserted directly with their projections. Not the real school; no HTTPS, browser, network, login storm or sustained load. Writes remain serialized by the global lock and each save rewrites its own journal projections; write-vs-write contention is not measured. Homework detail, absences and attendance reports are not part of this probe.')
    args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(result,indent=2,ensure_ascii=False),encoding='utf-8');print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()
