"""Read-load probe of expected lessons and synthetic saved journal documents."""
import argparse,asyncio,csv,io,json,os,sqlite3,tempfile,time
from contextlib import closing
from datetime import date,timedelta
from pathlib import Path
import httpx
from .synthetic_server import synthetic_server
from .benchmark_homework import summary


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
    started=time.perf_counter();count=0
    def entries():
        nonlocal count
        for day in days_list:
            for row,template in templates[day.weekday()]:
                current=row|dict(id=f'{day}:{row["placementId"]}',date=day.isoformat())
                lesson=template|dict(date=day.isoformat(),sourceKey=current['id'])
                doc=json.dumps(dict(lesson=lesson,content=dict(topic='Synthetic topic',homework='',dueDate=None,assessments=[],records=[dict(studentId=s['id'],attendance='present',lateMinutes=0,comment='',grades=[]) for s in lesson['students']])),ensure_ascii=False)
                count+=1
                yield ('school',key_for(current),1,day.isoformat(),'Synthetic teacher',doc,doc,1,day.isoformat())
    with closing(sqlite3.connect(path)) as db,db:
        db.executemany('INSERT INTO journal_entries VALUES (?,?,?,?,?,?,?,?,?)',entries())
        populate(db)
        coverage_populate(db)
        db.execute("INSERT INTO journal_attendance_epoch VALUES ('school',1)")
    return dict(start=days_list[0].isoformat(),end=days_list[-1].isoformat(),status='all'),dict(teachingDays=days,savedLessons=count,pupilRecords=count*20,seedSeconds=round(time.perf_counter()-started,2),databaseMiB=round(path.stat().st_size/1024**2,1))


async def probe(url,identities,admin,period,counts):
    timings={};limit=asyncio.Semaphore(10)
    async with httpx.AsyncClient(base_url=url,trust_env=False,timeout=300) as client:
        async def call(label,cookie,path='report',**extra):
            async with limit:
                begin=time.perf_counter();response=await client.post('/api/journal/coverage/'+path,headers={'Cookie':cookie,'X-Atlas-Request':'1'},json=period|extra)
                timings.setdefault(label,[]).append((time.perf_counter()-begin)*1000);response.raise_for_status();return response
        begin=time.perf_counter()
        school=(await call('school_period',admin)).json()
        assert school['counts']['expected']==counts['savedLessons'] and school['total']==counts['savedLessons']
        assert sum(school['counts'][s] for s in ['missing','draft','incomplete','stale','orphaned'])==0
        async def assigned(i):
            value=(await call('class_period',identities[3300+i][0])).json()
            assert value['counts']['expected']==8*counts['teachingDays']
        await asyncio.gather(*(assigned(i) for i in range(20)))
        # Check full CSV, including all pages.
        response=await call('class_csv',identities[3300][0],path='export')
        assert len(list(csv.reader(io.StringIO(response.content.decode('utf-8-sig')),delimiter=';')))==8*counts['teachingDays']+1
        week_end=(date.fromisoformat(period['start'])+timedelta(days=4)).isoformat()
        week=(await call('school_week',admin,end=week_end)).json();assert week['counts']['expected']==2350
        month_end=min(period['end'],(date.fromisoformat(period['start'])+timedelta(days=27)).isoformat())
        month=(await call('school_month',admin,end=month_end)).json();assert month['counts']['expected']==470*min(counts['teachingDays'],20)
        async def teacher_week(i):
            value=(await call('teacher_week',identities[3300+i][0],mode='teaching',end=week_end)).json()
            assert value['counts']['expected']>0 and value['counts']['missing']==0
        await asyncio.gather(*(teacher_week(i) for i in range(20)))
        return dict(seconds=round(time.perf_counter()-begin,2),concurrentReports=10,successfulRequests=sum(map(len,timings.values())),endpoints={k:summary(v) for k,v in timings.items()})


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True);parser.add_argument('--days',type=int,default=200);args=parser.parse_args()
    if not 5<=args.days<=200:parser.error('Use 5..200 teaching days.')
    with synthetic_server() as (url,identities,accounts,cookie):
        with httpx.Client(base_url=url,trust_env=False,timeout=30,headers={'Cookie':cookie,'X-Atlas-Request':'1'}) as c:
            state=c.get('/api/state').json();c.get('/api/journal/coverage/options').raise_for_status()
            current=c.get('/api/access/class-staff').json();c.put('/api/access/class-staff',json=dict(version=current['version'],revision=current['revision'],reason='Синтетическая нагрузка',assignments={f'c{i}':[dict(accountId=f'teacher{i}',capabilities=['absence_review','attendance_read','journal_coverage'])] for i in range(55)})).raise_for_status()
            period,seeded=seed(Path(os.environ['TIMETABLE_DB']),state,args.days)
            print(json.dumps(dict(phase='seeded',**seeded)),flush=True)
            run=asyncio.run(probe(url,identities,cookie,period,seeded))
            assert c.get('/api/state').json()==state
    result=dict(synthetic=True,stateUnchanged=True,accounts=accounts,seed=seeded,run=run,limitations='Saved journal documents and attendance projection are seeded directly. Publication/write throughput, grade/history/member-table volume, login storms, concurrent writes/generation, external network/TLS and sustained load are not measured. Not full-school production capacity.')
    args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(result,indent=2),encoding='utf-8');print(json.dumps(result))


if __name__=='__main__':main()
