"""Load probe of the course table: one course of 30 pupils over 60 lessons, read and saved over HTTP, with concurrent readers.

Everything runs against a temporary synthetic database: the school database is never opened.
The course is g0/b0 of the synthetic school: class c0 gets ten pupils of c1, so it has 30, and the journal is seeded with every lesson
of the school for the given teaching days (the same seeding as benchmark_journal_reads).
"""
import argparse,json,os,sqlite3,threading,time
from contextlib import closing
from pathlib import Path
from uuid import uuid4
import httpx
from .synthetic_server import synthetic_server
from .benchmark_homework import summary
from .benchmark_journal_reads import seed

ROOT=Path(__file__).resolve().parents[1]
LONG=900
GROUP,SUBJECT,TEACHER='g0','b0','t0'


def prepare(path,state):
    """Move s20..s29 to c0 in the stored school (a new revision, so no cached snapshot survives) and return the same state."""
    from backend.models import State
    state=State.model_validate(state)
    state.revision+=1
    for student in state.students:
        if student.id in {f's{i}' for i in range(20,30)}:student.classId='c0'
    for school_class in state.classes:
        if school_class.id=='c0':school_class.studentCount=30
        if school_class.id=='c1':school_class.studentCount=10
    with closing(sqlite3.connect(path)) as db,db:
        db.execute("UPDATE workspaces SET revision=?,document=? WHERE id='school'",(state.revision,state.model_dump_json()))
    return json.loads(state.model_dump_json())


def column(table,count,offset,score):
    """`count` editable lessons of the table from `offset`, with one cell per pupil on the roster set to the score."""
    editable=[(i,l) for i,l in enumerate(table['lessons']) if l['editable']][offset:offset+count]
    work={w['lesson']:w['id'] for w in table['works']}
    return [dict(lessonKey=l['lessonKey'],day=l['day'],version=l['version'],
                 cells=[dict(studentId=p['id'],assessmentId=work[i],score=score,label='') for p in table['pupils'] if i in p['lessons']]) for i,l in editable]


def probe(url,identities,admin,state,readers):
    teacher=next(cookie for cookie,view,entity in identities if view=='teacher' and entity==TEACHER)
    year=dict(start=state['settings']['yearStart'],end=state['settings']['yearEnd'])
    query=dict(groupId=GROUP,subjectId=SUBJECT,**year)
    def headers(cookie):return {'Cookie':cookie,'X-Atlas-Request':'1'}
    began=time.perf_counter()
    with httpx.Client(base_url=url,trust_env=False,timeout=LONG) as client:
        def read(cookie=teacher,sizes=None):
            started=time.perf_counter()
            response=client.get('/api/journal/course-table',params=query,headers=headers(cookie));elapsed=(time.perf_counter()-started)*1000
            response.raise_for_status()
            if sizes is not None:sizes.append(len(response.content))
            return response.json(),elapsed
        def save(table,lessons,publish):
            body=dict(requestId=str(uuid4()),groupId=GROUP,subjectId=SUBJECT,revision=table['revision'],publish=publish,lessons=lessons)
            started=time.perf_counter();response=client.post('/api/journal/course-table/save',json=body,headers=headers(teacher));elapsed=(time.perf_counter()-started)*1000
            response.raise_for_status();result=response.json()
            assert not result['conflicts'] and len(result['saved'])==len(lessons),result['conflicts'][:2]
            return elapsed
        courses=client.get('/api/journal/course-table/courses',headers=headers(teacher)).raise_for_status().json()['courses']
        mine=next(c for c in courses if (c['groupId'],c['subjectId'])==(GROUP,SUBJECT))
        assert mine['kind']=='course' and mine['canEdit']
        sizes=[];first_table,first=read(sizes=sizes)
        warm=[read(sizes=sizes)[1] for _ in range(10)]
        admin_first=read(admin,sizes)[1]
        admin_warm=[read(admin,sizes)[1] for _ in range(10)]
        shape=dict(pupils=len(first_table['pupils']),lessons=len(first_table['lessons']),editableLessons=sum(l['editable'] for l in first_table['lessons']),works=len(first_table['works']))
        assert shape['pupils']==30 and shape['editableLessons']>=20,shape
        counter=iter(range(1000))
        def next_score():return 1+next(counter)%9
        draft,publish,chunk=[],[],[]
        for _ in range(5):
            table=read()[0];draft.append(save(table,column(table,1,0,next_score()),False))
            table=read()[0];publish.append(save(table,column(table,1,0,next_score()),True))
        for _ in range(3):
            table=read()[0];chunk.append(save(table,column(table,10,10,next_score()),True))
        stop=threading.Event();reads=[];failures=[]
        def reader():
            with httpx.Client(base_url=url,trust_env=False,timeout=LONG) as own:
                while not stop.is_set():
                    started=time.perf_counter()
                    try:
                        own.get('/api/journal/course-table',params=query,headers=headers(teacher)).raise_for_status()
                        reads.append((time.perf_counter()-started)*1000)
                    except httpx.HTTPError as exc:failures.append(repr(exc))
        threads=[threading.Thread(target=reader,daemon=True) for _ in range(readers)]
        for thread in threads:thread.start()
        busy=[]
        try:
            time.sleep(1)
            for _ in range(3):
                table=read()[0];busy.append(save(table,column(table,10,20,next_score()),True))
        finally:
            stop.set()
            for thread in threads:thread.join(timeout=LONG)
        assert not failures,failures[:3]
        assert client.get('/api/state',headers=headers(admin)).json()==state
    return dict(seconds=round(time.perf_counter()-began,2),course=shape,payloadBytes=dict(teacher=sizes[0],admin=sizes[11]),
                teacherRead=dict(firstMs=round(first,1),warm=summary(warm)),adminRead=dict(firstMs=round(admin_first,1),warm=summary(admin_warm)),
                saveOneColumnDraft=summary(draft),saveOneColumnPublish=summary(publish),saveTenLessonsPublish=summary(chunk),
                readers=readers,saveTenLessonsPublishDuringReads=summary(busy),readsDuringSave=summary(reads) if reads else None)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True);parser.add_argument('--days',type=int,default=60);parser.add_argument('--readers',type=int,default=5)
    args=parser.parse_args()
    if not 60<=args.days<=200:parser.error('Use 60..200 teaching days.')
    if not 1<=args.readers<=20:parser.error('Use 1..20 concurrent readers.')
    if args.output.resolve().is_relative_to((ROOT/'data').resolve()):parser.error('Результаты замера сохраняйте вне каталога data/.')
    with synthetic_server(server_log=args.output.with_suffix('.server.log')) as (url,identities,accounts,cookie):
        with httpx.Client(base_url=url,trust_env=False,timeout=LONG,headers={'Cookie':cookie,'X-Atlas-Request':'1'}) as c:
            c.get('/api/journal/options').raise_for_status()
            state=prepare(Path(os.environ['TIMETABLE_DB']),c.get('/api/state').json())
            sample,seeded=seed(Path(os.environ['TIMETABLE_DB']),state,args.days)
            print(json.dumps(dict(phase='seeded',**seeded)),flush=True)
            state=c.get('/api/state').json()
        run=probe(url,identities,cookie,state,args.readers)
    result=dict(synthetic=True,stateUnchanged=True,accounts=accounts,seed=seeded,run=run,
                limitations='One machine, synthetic seeded documents (one points work per lesson, published and identical to the draft) inserted directly with their projections; the dated rosters are the plain school state, so rosters come from the current state. Not the real school; no HTTPS, browser, network or sustained load. Every save publishes to 30 pupils with three accounts each, which is the main cost of a publication. Writes stay serialized by the global lock.')
    args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(result,indent=2,ensure_ascii=False),encoding='utf-8');print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()
