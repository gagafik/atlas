"""The next real lesson of a class by the dated timetable, and one published assignment given to the same subject in other classes."""
from datetime import date, timedelta
import re

from fastapi import HTTPException, Request
from pydantic import Field, ValidationError

from .access import now
from .calendar_ops import plain, resolve_day
from .journal import Content, PupilRecord, key_for, signature, user_ids
from .journal_periods import require_open
from .models import Model
from .resources import teacher_ids
from . import assignment_files
from .homework import course_teachers
from .homework_bulk import replay, remember

SEARCH=60
WINDOW=14
# The homework an assignment is made of; the copy carries exactly these fields.
FIELDS={'homework':'','dueDate':None,'homeworkFiles':[],'homeworkLatePolicy':'allow','homeworkSubmission':'online'}


def course_lessons(snap, group, subject, start, end, resolved=None):
    """Held lessons (not cancelled) of one course, day by day through the dated timetable: moves, extra lessons,
    «день в расписании» and calendar closures all come from resolve_day. snap.lesson_rows is not used: its small cache
    would be flushed by a scan of weeks. resolved shares the resolved days between scans of one request."""
    resolved=resolved if resolved is not None else {}
    courses={}
    day=start
    while day<=min(end,snap.state.settings.yearEnd):
        view,ctx=snap.history.context(day)
        # Keep the view in the entry: its id must not be reused by another view during the scan.
        if id(view) not in courses:
            courses[id(view)]=(view,{p.id for p in view.placements if (w:=ctx.maps['workloads'].get(p.workloadId)) and w.groupId==group and w.subjectId==subject},
                               {d.onDate for d in view.datedLessons})
        _,course,dated=courses[id(view)]
        if course&ctx.template(day) or day in dated:
            if day not in resolved: resolved[day]=resolve_day(view,day,ctx)[0]
            yield from (r for r in resolved[day] if r['activity']=='lesson' and r['status']!='cancelled' and r['groupId']==group and r['subjectId']==subject)
        day+=timedelta(days=1)


def next_lesson(snap, group, subject, after, resolved=None):
    """The first lesson of the course on a later day than `after`, within SEARCH days; None when there is none."""
    return next(course_lessons(snap,group,subject,after+timedelta(days=1),after+timedelta(days=SEARCH),resolved),None)


def grades(names):
    return {m.group(1) for name in names if (m:=re.match(r'\s*(\d{1,2})',name))}


class ParallelTarget(Model):
    lessonKey: str = Field(min_length=64,max_length=64)
    day: date
    version: int = Field(ge=0)
    dueDate: date | None = None


class ParallelPublish(Model):
    requestId: str = Field(pattern=r'^[a-f0-9-]{36}$')
    lessonKey: str = Field(min_length=64,max_length=64)
    day: date
    revision: int = Field(ge=0)
    sourceVersion: int = Field(ge=1)
    targets: list[ParallelTarget] = Field(min_length=1,max_length=20)


class HomeworkParallel:
    def __init__(self, homework):
        self.homework=homework
        self.journal=homework.journal
        self.access=homework.access

    def candidates(self, snap, user, workspace, key, day, db):
        """(payload, prepared): the first lesson within WINDOW days of every other class the user teaches the subject to.
        The list and the publication share this, so what is offered and what is accepted cannot differ."""
        state=snap.state
        source=self.journal.entry(state,workspace,user,key,day,db=db,rows_for_day=snap.lesson_rows)
        lesson=source['document']['lesson']
        held=date.fromisoformat(source['currentDate'] or lesson['date'])
        course=course_teachers(state,lesson)
        base=course if user['role']=='admin' else course&user_ids(user,workspace,'teacher')
        if not base: raise HTTPException(403,'Задать это задание в других классах может учитель курса.')
        groups={w.groupId for w in state.workloads if w.subjectId==lesson['subjectId'] and w.groupId!=lesson['groupId'] and base&set(teacher_ids(w))}
        names={c.id:c.name for c in state.classes};catalog={g.id:g for g in state.groups}
        mine=grades(names.get(c,'') for c in catalog[lesson['groupId']].classIds) if lesson['groupId'] in catalog else set()
        # «Parallel» only orders and labels classes: the grade is the leading number of the class name.
        described=sorted(((bool(mine&grades(names.get(c,'') for c in catalog[id].classIds)),catalog[id]) for id in groups if id in catalog),key=lambda x:(not x[0],x[1].name))
        resolved={};targets=[];prepared={}
        for parallel,group in described:
            item=dict(groupId=group.id,group=group.name,classes=[names[c] for c in group.classIds if c in names],parallel=parallel,lessonKey=None,day=None,start=None,end=None,
                      teachers='',version=0,nextLessonDate=None,available=False,reason='Нет урока в ближайшие 14 дней.')
            targets.append(item)
            row=next(course_lessons(snap,group.id,lesson['subjectId'],held,held+timedelta(days=WINDOW-1),resolved),None)
            if row is None: continue
            target_key=key_for(row)
            item.update(lessonKey=target_key,day=row['date'],start=row['start'],end=row['end'],teachers=row['teachers'])
            nxt=next_lesson(snap,group.id,lesson['subjectId'],date.fromisoformat(row['date']),resolved)
            item['nextLessonDate']=nxt['date'] if nxt else None
            try: record=self.journal.entry(state,workspace,user,target_key,date.fromisoformat(row['date']),db=db,rows_for_day=snap.lesson_rows)
            except HTTPException as exc:
                if exc.status_code not in (403,404): raise
                item['reason']='Этот урок сейчас ведёт другой учитель.' if exc.status_code==403 else 'Урок не найден.'
                continue
            saved=self.journal.find(workspace,target_key,db)
            published=saved['published'] if saved else None
            reason=('Журнал урока закрыт или урок отменён.' if not record['writable'] else
                    'Сначала обновите состав урока в журнале.' if record['needsSync'] else
                    'Урок перенесён: обновите журнал.' if record['currentDate']!=row['date'] else
                    'В уроке уже есть домашнее задание.' if record['document']['content']['homework'] or published and published['content']['homework'] else
                    'Сначала опубликуйте обновлённый состав урока.' if published and signature(published['lesson'])!=signature(record['document']['lesson']) else '')
            item.update(version=record['version'],available=not reason,reason=reason)
            prepared[target_key]=record
        following=next_lesson(snap,lesson['groupId'],lesson['subjectId'],held,resolved)
        return dict(revision=state.revision,source=dict(lessonKey=key,day=held.isoformat(),version=source['version'],publishedVersion=source['publishedVersion'],
                                                          nextLessonDate=following['date'] if following else None),targets=targets),prepared

    def register(self, app, Workspace):
        @app.get('/api/journal/homework-next-lesson')
        def next_lesson_date(request:Request,lessonKey:str,day:date,workspace:Workspace='school'):
            user=self.journal.scope(request,workspace,staff=True)
            snap=self.homework.snapshots.get(workspace)
            def read(db):
                self.journal.recheck_read(db,user,snap,workspace)
                return self.journal.entry(snap.state,workspace,user,lessonKey,day,db=db,rows_for_day=snap.lesson_rows)
            record=self.journal.reading(read)
            lesson=record['document']['lesson']
            held=date.fromisoformat(record['currentDate'] or lesson['date'])
            found=next_lesson(snap,lesson['groupId'],lesson['subjectId'],held)
            return dict(date=found['date'] if found else None,start=found['start'] if found else None,
                        searchedUntil=min(snap.state.settings.yearEnd,held+timedelta(days=SEARCH)).isoformat())

        @app.get('/api/journal/homework-parallel')
        def parallel_candidates(request:Request,lessonKey:str,day:date,workspace:Workspace='school'):
            user=self.journal.scope(request,workspace,staff=True)
            snap=self.homework.snapshots.get(workspace)
            def read(db):
                self.journal.recheck_read(db,user,snap,workspace)
                return self.candidates(snap,user,workspace,lessonKey,day,db)[0]
            return self.journal.reading(read)

        @app.post('/api/journal/homework-parallel')
        def publish_parallel(body:ParallelPublish,request:Request,workspace:Workspace='school'):
            user=self.journal.scope(request,workspace,staff=True)
            keys=[t.lessonKey for t in body.targets]
            if len(set(keys))!=len(keys) or body.lessonKey in keys: raise HTTPException(422,'Урок указан несколько раз.')
            # A retry is recognised before the lessons are checked: after the first success they already have homework.
            with self.access.lock,self.access.connect() as db:
                self.homework.tables(db);self.journal.recheck_account(db,user)
                _,stored=replay(db,user,'homework.parallel',[workspace],body)
            if stored is not None: return stored
            snap=self.homework.snapshots.get(workspace);state=snap.state
            if state.revision!=body.revision: raise HTTPException(409,'Расписание изменилось. Обновите список классов.')
            def read(db):
                self.journal.recheck_read(db,user,snap,workspace)
                return self.candidates(snap,user,workspace,body.lessonKey,body.day,db)
            payload,prepared=self.journal.reading(read)
            offered={t['lessonKey']:t for t in payload['targets'] if t['lessonKey']}
            for t in body.targets:
                candidate=offered.get(t.lessonKey)
                if not candidate: raise HTTPException(403,'Урок не относится к вашим классам этого предмета.')
                if not candidate['available']: raise HTTPException(409,candidate['reason'])
                if candidate['day']!=t.day.isoformat() or candidate['version']!=t.version: raise HTTPException(409,'Журнал урока изменился. Обновите список классов.')
            with self.access.lock,self.access.connect() as db:
                self.homework.tables(db);db.execute('BEGIN IMMEDIATE');self.journal.recheck_account(db,user)
                request_key,stored=replay(db,user,'homework.parallel',[workspace],body)
                if stored is not None: return stored
                revision=db.execute('SELECT revision FROM workspaces WHERE id=?',(workspace,)).fetchone()
                if not revision or revision[0]!=body.revision: raise HTTPException(409,'Расписание изменилось. Обновите список классов.')
                row=db.execute('SELECT * FROM journal_entries WHERE workspace=? AND lesson_key=?',(workspace,body.lessonKey)).fetchone()
                source=self.journal.unpack(row) if row else None
                if not source or source['version']!=body.sourceVersion: raise HTTPException(409,'Задание в этом уроке изменилось. Обновите журнал.')
                shown=source['published']['content'] if source['published'] else None
                if not shown or not shown['homework'].strip() or any(source['document']['content'].get(f,d)!=shown.get(f,d) for f,d in FIELDS.items()):
                    raise HTTPException(409,'Сначала опубликуйте задание в этом уроке.')
                created=now().isoformat();done=[]
                for t in body.targets:
                    row=db.execute('SELECT * FROM journal_entries WHERE workspace=? AND lesson_key=?',(workspace,t.lessonKey)).fetchone()
                    previous=self.journal.unpack(row) if row else None
                    if (previous['version'] if previous else 0)!=t.version: raise HTTPException(409,'Журнал урока изменился. Обновите список классов.')
                    draft_doc=previous['document'] if previous else prepared[t.lessonKey]['document']
                    old_public=previous['published'] if previous else None
                    require_open(db,workspace,draft_doc['lesson'],previous['document']['lesson'] if previous else None,old_public['lesson'] if old_public else None)
                    if draft_doc['content']['homework'] or old_public and old_public['content']['homework']: raise HTTPException(409,'В уроке уже есть домашнее задание.')
                    ids=assignment_files.copy(db,shown.get('homeworkFiles',[]),workspace,body.lessonKey,t.lessonKey,user['id'],t.day)
                    fields=dict(homework=shown['homework'],dueDate=t.dueDate.isoformat() if t.dueDate else None,homeworkFiles=ids,
                                homeworkLatePolicy=shown.get('homeworkLatePolicy','allow'),homeworkSubmission=shown.get('homeworkSubmission','online'))
                    draft=dict(draft_doc,content=dict(draft_doc['content'],**fields))
                    # Marks the teacher has not published stay in the draft only: the publication is the old one, or a blank one, plus the homework.
                    base=old_public or dict(lesson=draft_doc['lesson'],content=Content(records=[PupilRecord(studentId=s['id']) for s in draft_doc['lesson']['students']]).model_dump(mode='json'))
                    published=dict(base,content=dict(base['content'],**fields))
                    try:
                        for document in (draft,published): Content.model_validate(document['content'])
                    except ValidationError as exc: raise HTTPException(422,plain(exc)) from None
                    # Two ordinary revisions, as when a grade is posted from the homework panel: the publication, then the draft.
                    version=previous['version'] if previous else 0
                    updated=self.journal.store_revision(db,workspace,t.lessonKey,user,state.revision,version+1,published,previous,publish=True,created=created)
                    if draft!=published:
                        updated=self.journal.store_revision(db,workspace,t.lessonKey,user,state.revision,version+2,draft,updated,created=created)
                    done.append(dict(lessonKey=t.lessonKey,day=t.day.isoformat(),group=offered[t.lessonKey]['group'],version=updated['version'],publishedVersion=updated['publishedVersion']))
                self.access.record(db,'homework.parallel_published',workspace,state.revision,detail=f'{body.lessonKey} → {len(done)} уроков')
                return remember(db,user,body,request_key,dict(published=done))
