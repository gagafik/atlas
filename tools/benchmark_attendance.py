"""Synthetic annual attendance index; this does not benchmark full journal storage."""
import argparse
import asyncio
from datetime import date,timedelta
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import time
import csv
import io
import httpx
from .synthetic_server import synthetic_server
from .benchmark_homework import summary


def seed(path,state,days):
    root=Path(tempfile.gettempdir()).resolve();path=path.resolve()
    if not path.is_relative_to(root) or not path.parent.name.startswith('atlas-message-load-'):
        raise ValueError('Only the isolated synthetic database may be seeded.')
    dates=[];day=date(2026,8,17)
    while len(dates)<days:
        if day.weekday()<5:dates.append(day.isoformat())
        day+=timedelta(days=1)
    classes={c['id']:c['name'] for c in state['classes']}
    assert len(classes)==55 and len(state['students'])==1100
    started=time.perf_counter()
    with sqlite3.connect(path) as db:
        db.execute('DROP INDEX attendance_summary')
        db.execute('CREATE TEMP TABLE attendance_load_roster(id TEXT,class_name TEXT,n INTEGER)')
        db.executemany('INSERT INTO attendance_load_roster VALUES (?,?,?)',[(s['id'],classes[s['classId']],i) for i,s in enumerate(state['students'])])
        def lessons():
            for day in dates:
                for class_id,name in classes.items():
                    for period in range(8):
                        key=hashlib.sha256(f'{day}:{class_id}:{period}'.encode()).hexdigest()
                        yield ('school',key,day,f'{8+period:02}:00',f'{8+period:02}:40',f'sub{period}',f'Subject {period}',name,'Synthetic teacher',1,day+'T00:00:00+00:00')
        db.executemany('INSERT INTO journal_attendance_lessons VALUES (?,?,?,?,?,?,?,?,?,?,?)',lessons())
        db.execute('''INSERT INTO journal_attendance
            SELECT l.workspace,l.lesson_key,s.id,CASE (s.n+CAST(substr(l.start,1,2) AS INTEGER)+CAST(substr(l.day,9,2) AS INTEGER))%20
                WHEN 0 THEN 'absent' WHEN 1 THEN 'late' WHEN 2 THEN 'excused' WHEN 3 THEN 'unmarked' ELSE 'present' END,
                CASE WHEN (s.n+CAST(substr(l.start,1,2) AS INTEGER)+CAST(substr(l.day,9,2) AS INTEGER))%20=1 THEN 7 ELSE 0 END,
                l.day,l.start,l.end,l.subject_id
            FROM journal_attendance_lessons l JOIN attendance_load_roster s ON s.class_name=l.group_name
            WHERE l.workspace='school' ''')
        db.execute("INSERT INTO journal_attendance_epoch VALUES ('school',1) ON CONFLICT(workspace) DO UPDATE SET version=version+1")
        # One accepted notice per pupil covers the first five teaching days.
        # These rows, like the attendance facts, are synthetic query fixtures.
        db.executemany('''INSERT INTO absence_notices
            (id,workspace,author_id,student_id,class_id,student_name,created,updated,version,status,start_at,end_at,reason)
            VALUES (?,'school','synthetic-parent',?,?,?,?,?,2,'approved',?,?,'Synthetic notice')''',
            [(f'notice-{s["id"]}',s['id'],s['classId'],s['name'],dates[0],dates[0],dates[0]+'T00:00',dates[4]+'T23:59') for s in state['students']])
        total=db.execute('SELECT count(*) FROM journal_attendance').fetchone()[0]
        from backend.attendance_projection import SUMMARY_INDEX
        db.execute(SUMMARY_INDEX)
    assert total==1100*8*days
    return dict(start=dates[0],end=dates[-1]),dict(days=days,lessons=len(classes)*8*days,pupilRecords=total,approvedNotices=1100,seedSeconds=round(time.perf_counter()-started,2),databaseMiB=round(path.stat().st_size/1024**2,1))


async def probe(url,identities,admin,period,days):
    timings={};limit=asyncio.Semaphore(10)
    async with httpx.AsyncClient(base_url=url,trust_env=False,timeout=180) as client:
        async def call(label,cookie,path='report',**extra):
            async with limit:
                started=time.perf_counter();r=await client.post('/api/journal/attendance/'+path,headers={'Cookie':cookie,'X-Atlas-Request':'1'},json=period|extra)
                timings.setdefault(label,[]).append((time.perf_counter()-started)*1000);r.raise_for_status()
                return r
        started=time.perf_counter()
        whole=(await call('school_year',admin)).json()
        assert whole['totals']['records']==1100*8*days and whole['studentCount']==1100 and len(whole['students'])==50
        assert whole['totals']['attendance']==dict(present=55*8*days*16,absent=55*8*(days-5),late=55*8*days,excused=55*8*(days+5),unmarked=55*8*days)
        assert whole['totals']['lateMinutes']==55*8*days*7
        async def coordinator(i):
            value=(await call('class_year',identities[3300+i][0])).json()
            assert value['totals']['records']==20*8*days and value['studentCount']==20
        await asyncio.gather(*(coordinator(i) for i in range(20)))
        first=(await call('pupil_lessons',identities[3300][0],path='details',studentId='s0')).json()
        assert len(first['items'])==50 and first['nextOffset']==50
        second=(await call('pupil_lessons',identities[3300][0],path='details',studentId='s0',offset=50,token=first['token'])).json()
        assert not {r['lessonKey'] for r in first['items']} & {r['lessonKey'] for r in second['items']}
        exported=await call('school_csv',admin,path='export',view='students',token=whole['token'])
        assert len(list(csv.reader(io.StringIO(exported.content.decode('utf-8-sig')),delimiter=';')))==1101
        day_report=(await call('school_day',admin,end=period['start'])).json()
        assert day_report['totals']['records']==1100*8 and day_report['totals']['attendance']['absent']==0
        month_end=(date.fromisoformat(period['start'])+timedelta(days=27)).isoformat()
        month_report=(await call('school_month',admin,end=month_end)).json()
        assert month_report['totals']['records']==1100*8*min(days,20)
        return dict(seconds=round(time.perf_counter()-started,2),concurrentClassReports=10,successfulRequests=sum(len(v) for v in timings.values()),endpoints={k:summary(v) for k,v in timings.items()})


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True);parser.add_argument('--days',type=int,default=200);args=parser.parse_args()
    if not 10<=args.days<=200:parser.error('Use 10..200 school days.')
    with synthetic_server() as (url,identities,accounts,cookie):
        with httpx.Client(base_url=url,trust_env=False,timeout=60,headers={'Cookie':cookie,'X-Atlas-Request':'1'}) as client:
            state=client.get('/api/state').json()
            client.get('/api/journal/attendance/options').raise_for_status()
            client.put('/api/journal/absences/reviewers',json=dict(version=0,assignments={f'c{i}':[f'teacher{i}'] for i in range(55)})).raise_for_status()
            period,index=seed(Path(os.environ['TIMETABLE_DB']),state,args.days)
            print(json.dumps(dict(phase='index_ready',**index)),flush=True)
            result=asyncio.run(probe(url,identities,cookie,period,args.days))
            assert client.get('/api/state').json()==state
    output=dict(synthetic=True,schoolStateUnchanged=True,accounts=accounts,index=index,run=result,limitations='Attendance projection and approved notices seeded directly for query measurement. Full journal documents, grades, migration costs, concurrent writes/generation, mass login, external TLS/network and sustained workload are not measured. Not production capacity evidence.')
    args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(output,indent=2),encoding='utf-8');print(json.dumps(output))


if __name__=='__main__':main()
