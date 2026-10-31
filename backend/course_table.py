"""Course table: the pupils of one group and subject against the works of all its lessons, with fast mark entry.

Marks are written only through Journal.store_revision, one lesson at a time, in the two-revision pattern of
homework_grading.apply: the publication gets the works and marks, the draft keeps everything else the teacher has not published."""
from collections import defaultdict
from copy import deepcopy
from datetime import date

from fastapi import HTTPException, Request
from pydantic import Field, ValidationError, model_validator

from . import class_staff
from .access import now
from .assessment_categories import GREY, breakdown, category_of, composite, config as category_config, merge, policy_for, tally
from .calendar_ops import plain
from .homework import course_teachers
from .homework_bulk import remember, replay
from .journal import Content, Grade, PupilRecord, current_lesson, signature, staff_read, user_ids
from .journal_periods import require_open
from .models import Model
from .natural import natural
from .resources import teacher_ids

GONE='Урока больше нет в расписании или он отменён.'
FOREIGN='Урок вёл другой учитель: оценку ставит он или администратор.'
DENIED='Таблица курса доступна учителю курса, классному руководителю и администратору.'
STALE='Журнал урока уже изменён. Таблица обновлена: проверьте и сохраните снова.'
ENTRIES=("SELECT * FROM journal_entries WHERE workspace=? AND json_extract(document,'$.lesson.groupId')=? AND json_extract(document,'$.lesson.subjectId')=? "
         "AND (json_extract(document,'$.lesson.date') BETWEEN ? AND ? OR json_extract(published_document,'$.lesson.date') BETWEEN ? AND ?)")


class Cell(Model):
    studentId: str = Field(min_length=1,max_length=100)
    assessmentId: str = Field(min_length=1,max_length=80)
    score: float | None = Field(default=None,ge=0,le=10000,allow_inf_nan=False)
    label: str = Field(default='',max_length=30)


class LessonEdit(Model):
    lessonKey: str = Field(min_length=64,max_length=64)
    day: date
    version: int = Field(ge=0)
    cells: list[Cell] = Field(default_factory=list,max_length=2000)

    @model_validator(mode='after')
    def distinct(self):
        if len({(c.studentId,c.assessmentId) for c in self.cells})!=len(self.cells): raise ValueError('Ячейка указана несколько раз.')
        return self


class CourseSave(Model):
    requestId: str = Field(pattern=r'^[a-f0-9-]{36}$')
    groupId: str = Field(min_length=1,max_length=100)
    subjectId: str = Field(min_length=1,max_length=100)
    revision: int = Field(ge=0)
    publish: bool = False
    # A long publication holds the writer lock; the client sends longer saves in chunks.
    lessons: list[LessonEdit] = Field(min_length=1,max_length=10)

    @model_validator(mode='after')
    def distinct(self):
        if len({l.lessonKey for l in self.lessons})!=len(self.lessons): raise ValueError('Урок указан несколько раз.')
        return self


def touches(group, students, led):
    """Whether the group has pupils of a led class now: a whole group by its classes, any other kind by its pupils."""
    if group.kind=='whole': return bool(set(group.classIds)&led)
    return any(students[id].classId in led for id in group.studentIds if id in students)


def course_scope(user, snap, workspace, group, subject):
    """What this account may do in the course: teachers of the workload edit; the administrator and the class teacher only read."""
    state=snap.state
    if not any(w.groupId==group and w.subjectId==subject for w in state.workloads): raise HTTPException(404,'Курс не найден.')
    own=user_ids(user,workspace,'teacher') if user['role']=='teacher' else set()
    if own&course_teachers(state,dict(groupId=group,subjectId=subject)): return dict(kind='course',canEdit=True,classes=None)
    if user['role']=='admin': return dict(kind='admin',canEdit=False,classes=None)
    led=class_staff.led(user,snap,workspace)
    if led and touches(snap.entities['group'][group],snap.entities['student'],led): return dict(kind='class',canEdit=False,classes=led)
    raise HTTPException(403,DENIED)


def spans(snap, group, start, end):
    """Pupil id -> (from, to) ISO dates of the dated rosters that overlap start..end; None is the start of the year, or still enrolled."""
    g=snap.entities['group'][group]
    first=snap.history.first
    stints=defaultdict(list)
    for student,kind,target,a,b in snap.history.stints:
        if (kind=='class' and g.kind=='whole' and target in g.classIds or kind=='group' and target==g.id) and (a<=end or a<=first) and (b is None or start<=b):
            stints[student].append((a,b))
    if stints:
        return {id:(None if min(a for a,_ in found)<=first else min(a for a,_ in found).isoformat(),
                    None if any(b is None for _,b in found) else max(b for _,b in found).isoformat()) for id,found in stints.items()}
    # A database from before the dated rosters: whoever belongs to the group when the period ends, or today.
    from .journal import today
    return {id:(None,None) for id in snap.history.context(min(end,today()))[1].group_students.get(group,[])}


def editing_problem(record, day_ok, current=None):
    """(status, message) why the marks of this lesson cannot be written now, else None. The table offers and the save accepts by this one rule."""
    if record['periodLocks']: return 403,'Журнал закрыт в периоде: '+', '.join(p['name'] for p in record['periodLocks'])+'.'
    if not record['writable']: return 403,GONE if record['currentDate'] is None or current and current['status']=='cancelled' else FOREIGN
    if record['needsSync']: return 409,'Изменилось время или состав урока. Откройте урок в журнале дня и обновите состав.'
    if record['currentDate']!=record['document']['lesson']['date']: return 409,'Урок перенесён на другую дату. Загрузите его заново.'
    from .journal import today
    if record['futureLesson'] or record['document']['lesson']['date']>today().isoformat(): return 422,'Урок ещё не прошёл: оценки ставятся в день урока или позже.'
    if not day_ok: return 422,'Дата урока вне учебного года.'
    return None


def publication(draft, old):
    """The publication that carries the draft's works and marks and nothing else of the draft: its topic, homework and attendance stay in the draft."""
    base=old or dict(lesson=draft['lesson'],content=Content(records=[PupilRecord(studentId=s['id']) for s in draft['lesson']['students']]).model_dump(mode='json'))
    grades={r['studentId']:r['grades'] for r in draft['content']['records']}
    if any(r['studentId'] not in grades for r in base['content']['records']): raise HTTPException(409,'Состав журнала изменился.')
    return dict(base,content=dict(base['content'],assessments=draft['content']['assessments'],records=[dict(r,grades=grades[r['studentId']]) for r in base['content']['records']]))


def news(draft, old):
    """Whether the draft holds works or marks the publication does not; a lesson never published needs a mark to be worth publishing.
    A pupil who is in the draft roster only counts too: the publication cannot carry the mark yet, and the lesson editor has to publish the roster first."""
    if not old: return any(r['grades'] for r in draft['content']['records'])
    if draft['content']['assessments']!=old['content']['assessments']: return True
    shown={r['studentId']:r['grades'] for r in old['content']['records']}
    return any(r['grades']!=shown.get(r['studentId'],[]) for r in draft['content']['records'])


def pending(draft, old):
    try: publication(draft,old)
    except HTTPException: return True
    return news(draft,old)


def build_table(snap, cfg, scope, group, subject, start, end, shown, revision):
    """The compact payload: lessons and works as columns, pupils as rows, marks by work index, totals by the family report's own maths."""
    led=scope['classes']
    lessons,works,index,used=[],[],{},set()
    people,present={},defaultdict(list)
    for i,item in enumerate(shown):
        lesson,content=item['document']['lesson'],item['document']['content']
        lessons.append(dict(lessonKey=item['key'],day=lesson['date'],start=lesson['start'],end=lesson['end'],version=item['version'],publishedVersion=item['publishedVersion'],
                            editable=item['editable'],reason=item['reason'],rosterFrozen=item['frozen'],hasUnpublished=item['pending'],withdrawn=item['withdrawn'],openable=item['openable']))
        for a in content['assessments']:
            index[i,a['id']]=len(works);used.add(category_of(a))
            works.append(dict(lesson=i,id=a['id'],name=a['name'],kind=a['kind'],maxScore=a['maxScore'],weight=a['weight'],category=category_of(a)))
        for s in lesson['students']:
            # The class teacher sees the pupils of the own class, by the class each had on the day of the lesson, and nobody else.
            if led is not None and s.get('classId') not in led: continue
            people[s['id']]=dict(id=s['id'],name=s['name'],classId=s.get('classId'),className=s.get('className',''))
            if s.get('enrolled',True): present[s['id']].append(i)
    catalog={c['id'] for c in cfg['categories']}
    categories=[c for c in cfg['categories'] if c['id'] in used]+[dict(id=id,name=id,title='',color=GREY,active=False) for id in sorted(used-catalog)]
    students,classes=snap.entities['student'],snap.entities['class_']
    dated=spans(snap,group,start,end)
    for id in dated:
        current=students.get(id)
        if id in people or not current or led is not None and current.classId not in led: continue
        people[id]=dict(id=id,name=current.name,classId=current.classId,className=classes[current.classId].name if current.classId in classes else '')
    rows=[]
    for id in sorted(people,key=lambda id:(natural(people[id]['name']),id)):
        person,sums,marks=people[id],{},{}
        for i,item in enumerate(shown):
            record=item['records'].get(id)
            if record is None or led is not None and item['roster'].get(id,{}).get('classId') not in led: continue
            merge(sums,tally(item['document']['content']['assessments'],record['grades']))
            for g in record['grades']:
                k=index.get((i,g['assessmentId']))
                if k is None or not (g['score'] is not None or g['label'] or g['comment']): continue
                marks[k]=dict(score=g['score'],label=g['label'],comment=g['comment'])
                if item['draft']:
                    was=item['grades'].get((id,g['assessmentId']))
                    if (g['score'],g['label'])!=((was['score'],was['label']) if was else (None,'')): marks[k]['unpublished']=True
        policy=policy_for(cfg,subject,person['classId'] or (students[id].classId if id in students else None))
        total=composite(sums,policy)
        lo,hi=dated.get(id,(None,None))
        rows.append(dict(person,**{'from':lo,'to':hi},lessons=present.get(id,[]),marks=marks,categories=breakdown(sums,cfg,policy['method']),
                         sharePercentage=round(float(total['exact']),2) if total['exact'] is not None else None,
                         blocked=total['blocked'],missing=total['missing'],excluded=total['excluded'],rule=policy['ruleName']))
    return dict(revision=revision,groupId=group,group=snap.entities['group'][group].name,subjectId=subject,subject=next(s.name for s in snap.state.subjects if s.id==subject),
                scope=scope['kind'],canEdit=scope['canEdit'],source='draft' if scope['kind']=='course' else 'published',start=start.isoformat(),end=end.isoformat(),
                categories=categories,lessons=lessons,works=works,pupils=rows)


class CourseTable:
    def __init__(self, journal, homework):
        self.journal,self.homework=journal,homework
        self.access=journal.access
        self.snapshots=homework.snapshots

    @staticmethod
    def withdrawn(db, workspace, key):
        """Whether the teacher hid the lesson from the families: no publication is left and the history holds the withdrawal."""
        return db.execute("SELECT 1 FROM journal_history WHERE workspace=? AND lesson_key=? AND action='journal.withdrawn' LIMIT 1",(workspace,key)).fetchone() is not None

    def shown(self, db, user, snap, scope, workspace, group, subject, start, end):
        """The document each lesson contributes, with what the table needs to know about it. Only a course teacher's read resolves schedule days."""
        state=snap.state
        course=scope['kind']=='course'
        rows=snap.course_rows(group,subject) if course else None
        year=state.settings.yearStart.isoformat(),state.settings.yearEnd.isoformat()
        first,last=start.isoformat(),end.isoformat()
        result=[]
        for raw in db.execute(ENTRIES,(workspace,group,subject,first,last,first,last)):
            existing=self.journal.unpack(raw)
            doc,old=existing['document'],existing['published']
            item=None
            if course:
                lesson=doc['lesson'];current=current_lesson(state,lesson,rows)
                try: record=self.journal.resolved_entry(state,workspace,user,existing['lessonKey'],existing,current,db=db)
                except HTTPException as exc:
                    # A lesson held by a substitute alone is not the course teacher's to read as a draft.
                    if exc.status_code!=403: raise
                    record=None
                if record and staff_read(user,workspace,current or lesson):
                    problem=editing_problem(record,year[0]<=lesson['date']<=year[1],current)
                    item=dict(draft=True,document=doc,editable=problem is None,reason=problem[1] if problem else '',frozen=record['rosterFrozen'],pending=pending(doc,old),
                              withdrawn=not old and self.withdrawn(db,workspace,existing['lessonKey']),openable=True)
                # The lesson editor opens what resolved_entry lets this account read; a lesson of a substitute alone is refused there.
                elif old: item=dict(draft=False,document=old,editable=False,reason=FOREIGN,frozen=False,pending=False,withdrawn=False,openable=record is not None)
            # Only a teacher of the lesson, or the administrator, opens it in the journal: the class teacher reads marks here and nowhere else.
            elif old: item=dict(draft=False,document=old,editable=False,reason='',frozen=False,pending=False,withdrawn=False,openable=scope['kind']=='admin')
            if item is None or not first<=item['document']['lesson']['date']<=last or not item['document']['content']['assessments']: continue
            # The published copy of a marked draft tells which marks the families do not see yet.
            grades={(r['studentId'],g['assessmentId']):g for r in old['content']['records'] for g in r['grades']} if item['draft'] and old else {}
            result.append(item|dict(key=existing['lessonKey'],version=existing['version'],publishedVersion=existing['publishedVersion'],grades=grades,
                                    records={r['studentId']:r for r in item['document']['content']['records']},roster={s['id']:s for s in item['document']['lesson']['students']}))
        result.sort(key=lambda r:(r['document']['lesson']['date'],r['document']['lesson']['start'],r['key']))
        return result

    def apply_lesson(self, db, user, snap, workspace, group, subject, item, publish, created):
        """One lesson of a save inside its own savepoint: ('saved'|'unchanged'|'conflict', payload). An HTTPException is that lesson's conflict."""
        state=snap.state
        previous=self.journal.find(workspace,item.lessonKey,db)
        if previous is None: raise HTTPException(409,'Урок изменился: в нём ещё нет сохранённых работ.')
        stored=previous['document']['lesson']
        if stored['groupId']!=group or stored['subjectId']!=subject: raise HTTPException(403,'Урок не относится к этому курсу.')
        if previous['version']!=item.version: raise HTTPException(409,STALE)
        # Publishing everything pending never brings back a lesson the teacher hid: it needs a mark typed in the table.
        if publish and not previous['published'] and not item.cells and self.withdrawn(db,workspace,item.lessonKey): return 'unchanged',item.lessonKey
        current=current_lesson(state,stored,snap.course_rows(group,subject))
        record=self.journal.resolved_entry(state,workspace,user,item.lessonKey,previous,current,db=db)
        old=previous['published']
        require_open(db,workspace,stored,old['lesson'] if old else None,current)
        problem=editing_problem(record,state.settings.yearStart<=date.fromisoformat(stored['date'])<=state.settings.yearEnd,current)
        if problem: raise HTTPException(*problem)
        if record['currentDate']!=item.day.isoformat(): raise HTTPException(409,'Урок перенесён на другую дату. Загрузите его заново.')
        draft=deepcopy(previous['document'])
        works={a['id']:a for a in draft['content']['assessments']}
        records={r['studentId']:r for r in draft['content']['records']}
        roster={s['id']:s for s in draft['lesson']['students']}
        problems=[]
        for cell in item.cells:
            work=works.get(cell.assessmentId)
            if not work: message='Работа удалена или изменена.'
            elif not roster.get(cell.studentId,{}).get('enrolled',cell.studentId in roster) or cell.studentId not in records: message='Ученика нет в составе этого урока.'
            elif work['kind']=='points' and (cell.label or cell.score is not None and cell.score>work['maxScore']): message=f"Баллы — от 0 до {work['maxScore']:g}."
            elif work['kind']=='label' and cell.score is not None: message='Для этой работы нужна текстовая оценка.'
            else: continue
            problems.append(dict(studentId=cell.studentId,assessmentId=cell.assessmentId,message=message))
        if problems: return 'conflict',dict(status=422,message='Проверьте отмеченные ячейки.',cells=problems)
        for cell in item.cells:
            grades=records[cell.studentId]['grades']
            found=next((g for g in grades if g['assessmentId']==cell.assessmentId),None)
            if cell.score is None and not cell.label:
                # The comment belongs to the lesson editor: clearing a mark keeps it, and a mark without one disappears.
                if found and found['comment']: found.update(score=None,label='')
                elif found: grades.remove(found)
            elif found: found.update(score=cell.score,label=cell.label)
            else: grades.append(Grade(assessmentId=cell.assessmentId,score=cell.score,label=cell.label).model_dump(mode='json'))
        try: Content.model_validate(draft['content'])
        except ValidationError as exc: raise HTTPException(422,plain(exc)) from None
        version=item.version+1
        if publish:
            published=publication(draft,old)
            try: Content.model_validate(published['content'])
            except ValidationError as exc: raise HTTPException(422,plain(exc)) from None
            if news(draft,old):
                if old and signature(old['lesson'])!=signature(draft['lesson']): raise HTTPException(409,'Сначала обновите и опубликуйте состав и время урока в журнале.')
                # Two ordinary revisions, as when a grade is posted from the homework panel: the publication, then the draft.
                updated=self.journal.store_revision(db,workspace,item.lessonKey,user,state.revision,version,published,previous,publish=True,created=created)
                if draft!=published: updated=self.journal.store_revision(db,workspace,item.lessonKey,user,state.revision,version+1,draft,updated,created=created)
                return 'saved',dict(lessonKey=item.lessonKey,version=updated['version'],publishedVersion=updated['publishedVersion'],cells=len(item.cells),published=True)
        if draft==previous['document']: return 'unchanged',item.lessonKey
        updated=self.journal.store_revision(db,workspace,item.lessonKey,user,state.revision,version,draft,previous,created=created)
        return 'saved',dict(lessonKey=item.lessonKey,version=updated['version'],publishedVersion=updated['publishedVersion'],cells=len(item.cells),published=False)

    def register(self, app, Workspace):
        journal,snapshots=self.journal,self.snapshots

        @app.get('/api/journal/course-table/courses')
        def courses(request: Request,workspace: Workspace='school'):
            user=journal.scope(request,workspace,staff=True)
            snap=snapshots.get(workspace);state=snap.state
            def read(db):
                journal.recheck_read(db,user,snap,workspace)
                return [dict(id=r[0],name=r[1],start=r[2],end=r[3],kind=r[4],status=r[5]) for r in db.execute('SELECT id,name,start_day,end_day,kind,status FROM journal_periods WHERE workspace=? ORDER BY start_day,id',(workspace,))]
            periods=journal.reading(read)
            own=user_ids(user,workspace,'teacher') if user['role']=='teacher' else set()
            led=class_staff.led(user,snap,workspace)
            groups,students,teachers,classes=snap.entities['group'],snap.entities['student'],snap.entities['teacher'],snap.entities['class_']
            subjects={s.id:s for s in state.subjects}
            found={}
            for w in state.workloads: found.setdefault((w.groupId,w.subjectId),[]).append(w)
            items=[]
            for (group,subject),loads in found.items():
                if user['role']=='admin': kind,edit='admin',False
                elif own&course_teachers(state,dict(groupId=group,subjectId=subject)): kind,edit='course',True
                elif led and touches(groups[group],students,led): kind,edit='class',False
                else: continue
                ids=list(dict.fromkeys(id for w in loads for id in teacher_ids(w)))
                items.append(dict(groupId=group,group=groups[group].name,subjectId=subject,subject=subjects[subject].name,classes=[classes[c].name for c in groups[group].classIds if c in classes],
                                  teachers=[teachers[id].name for id in ids if id in teachers],kind=kind,canEdit=edit))
            items.sort(key=lambda c:(natural(c['group']),c['subject'],c['groupId'],c['subjectId']))
            from .journal import today
            return dict(revision=state.revision,today=today().isoformat(),yearStart=state.settings.yearStart.isoformat(),yearEnd=state.settings.yearEnd.isoformat(),periods=periods,courses=items)

        @app.get('/api/journal/course-table')
        def table(request: Request,groupId: str,subjectId: str,start: date,end: date,workspace: Workspace='school'):
            user=journal.scope(request,workspace,staff=True)
            if end<start or (end-start).days>366: raise HTTPException(422,'Выберите период до 367 дней.')
            snap=snapshots.get(workspace)
            scope=course_scope(user,snap,workspace,groupId,subjectId)
            def read(db):
                journal.recheck_read(db,user,snap,workspace)
                cfg=category_config(db,workspace)
                return build_table(snap,cfg,scope,groupId,subjectId,start,end,self.shown(db,user,snap,scope,workspace,groupId,subjectId,start,end),snap.state.revision)
            return journal.reading(read)

        @app.post('/api/journal/course-table/save')
        def save(body: CourseSave,request: Request,workspace: Workspace='school'):
            user=journal.scope(request,workspace,staff=True)
            snap=snapshots.get(workspace);state=snap.state
            if not course_scope(user,snap,workspace,body.groupId,body.subjectId)['canEdit']: raise HTTPException(403,'Оценки в таблице курса ставит учитель курса.')
            # Resolve the days before the writer lock, so the lock is held for writing only.
            rows=snap.course_rows(body.groupId,body.subjectId)
            for item in body.lessons: rows(item.day)
            with self.access.lock,self.access.connect() as db:
                self.homework.tables(db)
                if not db.in_transaction: db.execute('BEGIN IMMEDIATE')
                journal.recheck_account(db,user)
                request_key,stored=replay(db,user,'journal.course_table',[workspace,body.groupId,body.subjectId],body)
                if stored is not None: return stored
                revision=db.execute('SELECT revision FROM workspaces WHERE id=?',(workspace,)).fetchone()
                if not revision or revision[0]!=body.revision or revision[0]!=state.revision: raise HTTPException(409,'Расписание изменилось. Обновите таблицу.')
                created=now().isoformat();saved,unchanged,conflicts=[],[],[]
                for item in body.lessons:
                    db.execute('SAVEPOINT course_lesson')
                    try: status,outcome=self.apply_lesson(db,user,snap,workspace,body.groupId,body.subjectId,item,body.publish,created)
                    except HTTPException as exc:
                        db.execute('ROLLBACK TO course_lesson');db.execute('RELEASE course_lesson')
                        conflicts.append(dict(lessonKey=item.lessonKey,day=item.day.isoformat(),status=exc.status_code,message=exc.detail,cells=[]))
                        continue
                    db.execute('RELEASE course_lesson')
                    if status=='saved': saved.append(outcome)
                    elif status=='unchanged': unchanged.append(outcome)
                    else: conflicts.append(dict(lessonKey=item.lessonKey,day=item.day.isoformat(),**outcome))
                if saved: self.access.record(db,'journal.course_saved',workspace,state.revision,detail=f'{body.groupId}/{body.subjectId} · уроков {len(saved)}')
                return remember(db,user,body,request_key,dict(saved=saved,unchanged=unchanged,conflicts=conflicts))
