"""«Кто в школе»: who should be in the building at a chosen moment, for a fire drill.

Built from the timetable of the day (substitutions, cancellations, moves, swapped days and dated rosters
come from Snapshot.lesson_rows), minus pupils the school knows are away. Names, class, room and the
responsible teacher only: no subjects, reasons, groups or absence codes.
"""
from collections import defaultdict
from datetime import date,datetime,time,timezone
from fastapi import HTTPException,Query,Request

from .absences import approvals
from .cycles import pattern_on
from .notifications import has_table
from .safety import ZONE

LEFT_STATES={'absent','excused'}


def parse(value):
    dt=datetime.fromisoformat(value)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def away_ids(db,workspace,day,clock,moment,reference):
    """Pupils known to be away at the moment: accepted note, latest published teacher mark, completed pickup."""
    away=set()
    lessons=[dict(date=day.isoformat(),start=r['start'],end=r['end'],subjectId=r['subjectId'],status='scheduled',students=[dict(id=s) for s in ids]) for r,ids in reference]
    for i,found in approvals(db,workspace,lessons).items():away|=set(found)
    if has_table(db,'journal_attendance') and has_table(db,'journal_attendance_lessons'):
        marks={}
        for sid,attendance,_,left in db.execute("SELECT a.student_id,a.attendance,a.start,a.left_at FROM journal_attendance_lessons l JOIN journal_attendance a ON a.workspace=l.workspace AND a.lesson_key=l.lesson_key WHERE l.workspace=? AND l.day=? AND a.attendance!='unmarked' AND a.start<=? ORDER BY a.start",(workspace,day.isoformat(),clock)):marks[sid]=(attendance,left)
        for sid,(attendance,left) in marks.items():
            if attendance in LEFT_STATES or attendance=='left' and left and left<=clock:away.add(sid)
            else:away.discard(sid)
    if has_table(db,'handover_pickups'):
        for sid,done,back in db.execute("SELECT student_id,completed_at,returned_at FROM handover_pickups WHERE workspace=? AND day=? AND status IN ('completed','returned')",(workspace,day.isoformat())):
            if done and parse(done)<=moment and (not back or parse(back)>moment):away.add(sid)
    return away


def who_is_in(db,snap,workspace,day,clock):
    state=snap.state;view,_=snap.history.context(day)
    pupils={s.id:s for s in view.students};classes={c.id:c for c in view.classes};teachers=snap.entities['teacher']
    rows=snap.lesson_rows(day)
    lessons=[r for r in rows if r['activity']=='lesson' and r['status']!='cancelled']
    mine=defaultdict(list)
    for r in lessons:
        for sid in r['studentIds']:
            if sid in pupils:mine[sid].append(r)
    place,reference={},defaultdict(list)
    for sid,found in mine.items():
        found.sort(key=lambda r:(r['start'],r['group']))
        if not found[0]['start']<=clock<max(r['end'] for r in found):continue
        current=next((r for r in found if r['start']<=clock<r['end']),None)
        ahead=current or next(r for r in found if r['start']>clock)
        place[sid]=current;reference[ahead['id']].append(sid)
    by_id={r['id']:r for r in lessons}
    moment=datetime.combine(day,time.fromisoformat(clock),ZONE)
    away=away_ids(db,workspace,day,clock,moment,[(by_id[i],ids) for i,ids in reference.items()])
    order={c.id:n for n,c in enumerate(state.classes)}
    grouped=defaultdict(lambda:dict(places={},away=[],expected=0))
    for sid,current in place.items():
        pupil=pupils[sid];entry=grouped[pupil.classId or ''];entry['expected']+=1
        if sid in away:entry['away'].append(dict(id=sid,name=pupil.name));continue
        owner=classes.get(pupil.classId);boss=owner.classTeacherId if owner else None
        if current:
            ids=list(current['teacherIds']) or ([boss] if boss else []);key=('lesson',current['id'])
            made=dict(kind='lesson',roomIds=list(current['roomIds']),rooms=current['rooms'],teacherIds=ids,responsible=current['teachers'] if current['teacherIds'] else (teachers[boss].name if boss in teachers else ''))
        else:
            key=('between',boss or '')
            made=dict(kind='between',roomIds=[],rooms='',teacherIds=[boss] if boss else [],responsible=teachers[boss].name if boss in teachers else '')
        entry['places'].setdefault(key,made|dict(pupils=[]))['pupils'].append(dict(id=sid,name=pupil.name))
    result=[]
    for classId,entry in sorted(grouped.items(),key=lambda x:(order.get(x[0],len(order)),x[0])):
        owner=classes.get(classId);boss=owner.classTeacherId if owner else None
        places=sorted(entry['places'].values(),key=lambda p:(p['kind']!='lesson',p['rooms'],p['responsible']))
        for p in places:p['pupils'].sort(key=lambda x:x['name'])
        result.append(dict(classId=classId,className=owner.name if owner else 'Без класса',classTeacher=teachers[boss].name if boss in teachers else '',
                           expected=entry['expected'],places=places,away=sorted(entry['away'],key=lambda x:x['name'])))
    staff={}
    for r in rows:
        if r['activity'] not in ('lesson','duty') or r['status']=='cancelled' or not r['start']<=clock<r['end']:continue
        for t in r['teacherIds']:
            if t not in teachers:continue
            lesson=r['activity']=='lesson'
            staff[t,r['id']]=dict(teacherId=t,name=teachers[t].name,kind=r['activity'],place=r['rooms'] if lesson else r['group'],
                                  classes=', '.join(classes[c].name for c in r['classIds'] if c in classes) if lesson else '')
    people=sorted(staff.values(),key=lambda p:(p['name'],p['kind']))
    teaching=pattern_on(state,day) is not None and day.weekday()<len(state.settings.days)
    return dict(date=day.isoformat(),time=clock,teachingDay=teaching,classes=result,staff=people,
                totals=dict(expected=sum(c['expected'] for c in result),away=sum(len(c['away']) for c in result),staff=len({p['teacherId'] for p in people})))


def register(s,app,Workspace):
    @app.get('/api/safety/drill')
    def drill(request:Request,workspace:Workspace='school',at:str|None=Query(default=None,pattern=r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$')):
        with s.context(request,workspace) as (db,u,snap):
            s.admin(u)
            if at is None:at=s.now().astimezone(ZONE).strftime('%Y-%m-%dT%H:%M')
            try:day=date.fromisoformat(at[:10]);time.fromisoformat(at[11:])
            except ValueError:raise HTTPException(422,'Некорректная дата или время.') from None
            if not snap.state.settings.yearStart<=day<=snap.state.settings.yearEnd:raise HTTPException(422,'Дата вне учебного года.')
            return who_is_in(db,snap,workspace,day,at[11:])
