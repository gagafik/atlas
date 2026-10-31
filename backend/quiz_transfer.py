"""Course tests: the results table and the transfer of marks into a lesson of the course, with a preview, an explicit replacement and retry-safe writes.

Marks are written only through Journal.store_revision (course_table.store_marks, the rule of the course table); a mark that anyone but this transfer made is never replaced without the teacher's choice.
The transfer touches neither the attempts nor the quiz version, so a retry stays stable and a draft mark cannot reach a family through the attempt view."""
from copy import deepcopy
from datetime import date, datetime, timedelta
from fractions import Fraction
import hashlib
import json
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import HTTPException, Request
from pydantic import Field, ValidationError, field_validator, model_validator

from .access import now
from .assessment_categories import SHAPE, config, names
from .calendar_ops import plain
from .course_table import FOREIGN, CourseTable, editing_problem, pending, store_marks
from .homework_bulk import remember, replay, tables as bulk_tables
from .homework_grading import HomeworkGrading, existing_grade
from .journal import Assessment, Content, Grade, current_lesson, key_for, signature, staff_read
from .journal_periods import require_open
from .models import Model
from .natural import natural
from .notifications import encoded
from .quizzes import course_lesson, is_course

INTERNAL = {'problem', 'previous', 'current', 'record', 'base', 'new'}


class NewWork(Model):
    name: str = Field(min_length=1,max_length=100)
    category: str = Field(default='',max_length=40)
    weight: float = Field(default=1,ge=0,le=100,allow_inf_nan=False)
    # Empty means the maximum of the test.
    maxScore: float | None = Field(default=None,gt=0,le=10000,allow_inf_nan=False)

    @field_validator('category',mode='before')
    @classmethod
    def category_shape(cls,value):
        if not isinstance(value,str) or not SHAPE.fullmatch(value):raise ValueError('Категория работы указана неверно.')
        return value


class Target(Model):
    lessonKey: str = Field(pattern=r'^[a-f0-9]{64}$')
    day: date
    revision: int = Field(ge=0)
    assessmentId: str | None = Field(default=None,min_length=1,max_length=80)
    newWork: NewWork | None = None
    pick: Literal['last','best'] = 'last'
    publish: bool = False

    @model_validator(mode='after')
    def one_work(self):
        if (self.assessmentId is None)==(self.newWork is None):raise ValueError('Выберите работу журнала или создайте новую.')
        return self


class Transfer(Target):
    requestId: str = Field(pattern=r'^[a-f0-9-]{36}$')
    token: str = Field(pattern=r'^[a-f0-9]{64}$')
    overwrite: list[str] = Field(default_factory=list,max_length=500)

    @model_validator(mode='after')
    def distinct(self):
        if len(set(self.overwrite))!=len(self.overwrite):raise ValueError('Ученик указан несколько раз.')
        return self


def lesson_of(ctx):
    return (ctx['quiz']['published'] or ctx['quiz']['document'])['lesson']


def editor(ctx):
    if not ctx['canTransfer']:raise HTTPException(403,'Переносить результаты в журнал могут учитель курса и администратор.')


def quiz_max(ctx):
    document=ctx['quiz']['published'] or ctx['quiz']['document']
    return round(sum(q['question']['points'] for q in document['definition']['variants'][0]['questions']),6)


def work_id(quiz):
    """One deterministic work per test: a re-run finds it again instead of creating a second."""
    return 'quiz-'+hashlib.sha256(quiz['id'].encode()).hexdigest()[:40]


def scaled(score,maximum,target):
    """The test score in the points of the work: unchanged for equal maxima, else proportional, to hundredths."""
    if maximum==target:return float(score)
    return min(round(float(Fraction(str(score))/Fraction(str(maximum))*Fraction(str(target))),2),float(target))


def pick_attempt(items,pick):
    """(attempt or None, reason): the attempt the pupil's result comes from; 'last' is the newest one, 'best' the highest reviewed one (ties to the newest)."""
    if not items:return None,'Тест не начат.'
    last=items[-1];done=[a for a in items if a['status']=='reviewed']
    if pick=='best' and done:return max(done,key=lambda a:(a['score'],a['number'])),''
    if pick=='last' and last['status']=='reviewed':return last,''
    return None,'В тесте идёт попытка.' if last['status']=='active' else 'Ждёт ручной проверки.'


def seen_mark(grade):
    """A mark is set when it has a score or a label; a comment alone is not one."""
    return dict(score=grade['score'],label=grade['label']) if grade and (grade['score'] is not None or grade['label']) else None


class QuizTransfer:
    def __init__(self, quizzes):
        self.quizzes=quizzes
        self.journal=quizzes.journal
        self.grading=HomeworkGrading(quizzes)

    def warm(self, ctx, day):
        """Resolve the day of the schedule before a read or the writer lock, so neither is held for it."""
        lesson=lesson_of(ctx)
        ctx['snap'].course_rows(lesson['groupId'],lesson['subjectId'])(day)

    def target_lesson(self, db, ctx, key, day):
        """(saved entry or None, the lesson on the schedule or None, the editor's record) of a lesson of this test's course; HTTPException when it is not this account's to read."""
        lesson=lesson_of(ctx);workspace=ctx['workspace']
        rows=ctx['snap'].course_rows(lesson['groupId'],lesson['subjectId'])
        previous=self.journal.find(workspace,key,db)
        if previous:
            stored=previous['document']['lesson']
            if stored['groupId']!=lesson['groupId'] or stored['subjectId']!=lesson['subjectId']:raise HTTPException(403,'Урок не относится к этому курсу.')
            current=current_lesson(ctx['state'],stored,rows)
        else:current=next((r for r in rows(day) if key_for(r)==key),None)
        try:record=self.journal.resolved_entry(ctx['state'],workspace,ctx['user'],key,previous,current,db=db)
        except HTTPException as exc:
            if exc.status_code!=403:raise
            raise HTTPException(403,FOREIGN) from None
        return previous,current,record

    def plan(self, db, ctx, body):
        """What the transfer would do, from one consistent read. `problem` is (status, message) when the lesson cannot take marks; then no pupil's data leaves."""
        quiz,workspace,state=ctx['quiz'],ctx['workspace'],ctx['state']
        document=quiz['published'] or quiz['document']
        previous=current=record=base=old=work=None;new=False;problem=None
        if quiz['status'] in ['draft','withdrawn']:problem=409,'Тест не опубликован или отозван.'
        else:
            try:previous,current,record=self.target_lesson(db,ctx,body.lessonKey,body.day)
            except HTTPException as exc:problem=exc.status_code,exc.detail
        if not problem:
            year=state.settings.yearStart.isoformat(),state.settings.yearEnd.isoformat()
            problem=editing_problem(record,year[0]<=record['document']['lesson']['date']<=year[1],current)
        if not problem and record['currentDate']!=body.day.isoformat():problem=409,'Урок перенесён на другую дату. Загрузите его заново.'
        if not problem:
            base=previous['document'] if previous else record['document'];old=previous['published'] if previous else None
            if body.publish and not quiz['released']:problem=409,'Сначала откройте разбор теста, чтобы результат стал доступен семье.'
            elif body.publish and previous and not old and CourseTable.withdrawn(db,workspace,body.lessonKey):
                problem=409,'Урок скрыт от семьи. Верните его в редакторе урока или перенесите как черновик.'
            elif body.publish and old and signature(old['lesson'])!=signature(base['lesson']):problem=409,'Сначала обновите и опубликуйте состав и время урока в журнале.'
        if not problem:
            works={a['id']:a for a in base['content']['assessments']}
            if body.assessmentId:
                work=works.get(body.assessmentId)
                if not work:problem=409,'Работа удалена или изменена.'
                elif work['kind']=='label':problem=422,'Результат теста переносится только в работу с баллами.'
            elif work_id(quiz) in works:work=works[work_id(quiz)]
            else:
                cfg=config(db,workspace);wanted=body.newWork.category or self.grading.default_category(ctx,cfg);found=names(cfg).get(wanted)
                if not found or not found['active']:problem=422,'Выберите действующую категорию работы.'
                elif len(works)>=10:problem=422,'В уроке уже десять работ.'
                else:
                    try:
                        work=Assessment(id=work_id(quiz),name=body.newWork.name,kind='points',maxScore=body.newWork.maxScore or quiz_max(ctx),weight=body.newWork.weight,category=wanted).model_dump(mode='json')
                    except ValidationError as exc:problem=422,plain(exc)
                    else:work.pop('bookingId',None);new=True
        if problem:return dict(allowed=False,reason=problem[1],problem=problem,work=None,workExists=False,rows=[],counts={},token='',otherPending=False,hasPublished=False,new=False)
        rows=self.rows(db,ctx,body,document,base,old,work)
        counts={k:sum(r['outcome']==k for r in rows) for k in ['write','update','conflict','same','skip']}
        other=bool(previous) and pending(base,old)
        token=hashlib.sha256(encoded([quiz['version'],previous['version'] if previous else 0,previous['publishedVersion'] if previous else None,work,
                                      [[r['studentId'],r['attemptId'],r['value'],r['outcome'],r['draft'],r['published']] for r in rows]]).encode()).hexdigest()
        return dict(allowed=True,reason='',problem=None,work=dict(work,exists=not new),workExists=not new,rows=rows,counts=counts,token=token,otherPending=other,
                    hasPublished=old is not None,previous=previous,current=current,record=record,base=base,new=new)

    def rows(self, db, ctx, body, document, base, old, work):
        workspace,quiz=ctx['workspace'],ctx['quiz']
        attempts={}
        for r in db.execute('SELECT student_id,id,number,status,score,max_score FROM quiz_attempts WHERE workspace=? AND quiz_id=? ORDER BY student_id,number',(workspace,quiz['id'])):
            attempts.setdefault(r[0],[]).append(dict(id=r[1],number=r[2],status=r[3],score=r[4],max=r[5]))
        ledger={r[0]:r[1:] for r in db.execute('SELECT student_id,attempt_id,source_score,value FROM quiz_transfer_marks WHERE workspace=? AND quiz_id=? AND lesson_key=? AND assessment_id=?',
                                              (workspace,quiz['id'],body.lessonKey,work['id']))}
        roster={s['id']:s for s in base['lesson']['students']};records={r['studentId'] for r in base['content']['records']}
        names_={s['id']:s['name'] for s in document['lesson']['students']}
        result=[]
        for id in sorted(document['studentIds'],key=lambda id:(natural(names_.get(id,'')),id)):
            attempt,reason=pick_attempt(attempts.get(id,[]),body.pick)
            row=dict(studentId=id,name=names_.get(id,''),enrolled=id in ctx['groupPupils'],attemptId=attempt['id'] if attempt else None,attemptNumber=attempt['number'] if attempt else None,
                     score=attempt['score'] if attempt else None,maxScore=attempt['max'] if attempt else None,value=None,scaled=False,draft=None,published=None,outcome='skip',reason=reason)
            result.append(row)
            if not attempt:continue
            if not roster.get(id,{}).get('enrolled',id in roster) or id not in records:row['reason']='Ученика нет в составе этого урока.';continue
            value=scaled(attempt['score'],attempt['max'],work['maxScore'])
            draft,published=seen_mark(existing_grade(base,id,work['id'])),seen_mark(existing_grade(old,id,work['id'])) if old else None
            seen=draft or published;own=ledger.get(id)
            if seen is None:outcome='write'
            elif draft and draft['score']==value:outcome='same'
            elif seen['score']==value:outcome='write'
            elif own and seen['score']==own[2]:outcome='update'
            else:outcome='conflict'
            row.update(value=value,scaled=attempt['max']!=work['maxScore'],draft=draft,published=published,outcome=outcome,reason='')
        return result

    def view(self, plan):
        return {k:v for k,v in plan.items() if k not in INTERNAL}

    def summary(self, db, ctx):
        quiz,workspace=ctx['quiz'],ctx['workspace']
        marks={}
        for r in db.execute('''SELECT m.student_id,m.lesson_key,m.day,m.assessment_id,m.work,m.value,m.source_score,m.actor,m.created,a.score FROM quiz_transfer_marks m
                               LEFT JOIN quiz_attempts a ON a.workspace=m.workspace AND a.id=m.attempt_id WHERE m.workspace=? AND m.quiz_id=? ORDER BY m.created DESC,m.rowid DESC''',(workspace,quiz['id'])):
            marks.setdefault(r[0],dict(studentId=r[0],lessonKey=r[1],day=r[2],assessmentId=r[3],work=r[4],value=r[5],sourceScore=r[6],actor=r[7],created=r[8],stale=r[9] is None or abs(r[9]-r[6])>1e-9))
        runs=[]
        for created,actor,body in db.execute("SELECT created,actor,document FROM quiz_history WHERE workspace=? AND quiz_id=? AND action='quiz.transferred' ORDER BY id DESC LIMIT 20",(workspace,quiz['id'])):
            run=json.loads(body)
            runs.append(dict(created=created,actor=actor,**{k:run.get(k) for k in ['lessonKey','day','work','publish','counts']}))
        from .journal import today
        lesson=lesson_of(ctx)
        if runs:day,key=runs[0]['day'],runs[0]['lessonKey']
        elif not is_course(lesson):day,key=lesson['date'],quiz['lessonKey']
        else:
            # A course test has no lesson of its own: offer the latest day of the course up to today (or the close of the test), looking back two weeks.
            closes=datetime.fromisoformat(quiz['closesAt']).astimezone(ZoneInfo('Asia/Qyzylorda')).date()
            rows=ctx['snap'].course_rows(lesson['groupId'],lesson['subjectId']);first=min(today(),closes)
            found=next((d for d in (first-timedelta(days=n) for n in range(14)) if d>=ctx['state'].settings.yearStart and rows(d)),first)
            day,key=found.isoformat(),''
        cfg=config(db,workspace)
        return dict(canTransfer=ctx['canTransfer'],quizMax=quiz_max(ctx),defaultCategory=self.grading.default_category(ctx,cfg),defaultDay=day,defaultLessonKey=key,
                    marks=list(marks.values()),runs=runs)

    def lessons(self, db, ctx, day):
        """The lessons of the course on that day, with what the account may do in each. A lesson held by someone else shows no draft data."""
        state,workspace,user=ctx['state'],ctx['workspace'],ctx['user']
        lesson=lesson_of(ctx);rows=ctx['snap'].course_rows(lesson['groupId'],lesson['subjectId'])
        year=state.settings.yearStart.isoformat(),state.settings.yearEnd.isoformat()
        result=[]
        for row in rows(day):
            key=key_for(row);existing=self.journal.find(workspace,key,db)
            current=current_lesson(state,existing['document']['lesson'],rows) if existing else row
            item=dict(lessonKey=key,day=day.isoformat(),start=row['start'],end=row['end'],version=existing['version'] if existing else 0,editable=False,reason=FOREIGN,
                      rosterFrozen=False,withdrawn=False,hasPublished=bool(existing and existing['published']),hasUnpublished=False,works=[])
            try:record=self.journal.resolved_entry(state,workspace,user,key,existing,current,db=db)
            except HTTPException as exc:
                if exc.status_code!=403:raise
                result.append(item);continue
            document=record['document']
            problem=editing_problem(record,year[0]<=document['lesson']['date']<=year[1],current)
            item.update(editable=problem is None,reason=problem[1] if problem else '',rosterFrozen=record['rosterFrozen'],
                        withdrawn=bool(existing and not existing['published'] and CourseTable.withdrawn(db,workspace,key)),
                        hasUnpublished=bool(existing) and pending(existing['document'],existing['published']))
            if staff_read(user,workspace,current or document['lesson']):
                item['works']=[dict(id=a['id'],name=a['name'],kind=a['kind'],maxScore=a['maxScore'],weight=a['weight'],category=a['category']) for a in document['content']['assessments']]
            result.append(item)
        return dict(day=day.isoformat(),lessons=result)

    def apply(self, db, ctx, body, p):
        """The write of one confirmed preview, inside the caller's transaction. Returns the response dict."""
        quiz,workspace,user=ctx['quiz'],ctx['workspace'],ctx['user']
        previous,base,work,rows=p['previous'],p['base'],p['work'],p['rows']
        conflicts={r['studentId'] for r in rows if r['outcome']=='conflict'}
        if set(body.overwrite)-conflicts:raise HTTPException(422,'Заменить можно только оценку с конфликтом.')
        old=previous['published'] if previous else None
        require_open(db,workspace,p['record']['document']['lesson'],old['lesson'] if old else None,p['current'])
        chosen=[r for r in rows if r['outcome'] in ['write','update'] or r['outcome']=='conflict' and r['studentId'] in body.overwrite]
        draft=deepcopy(base)
        if chosen and p['new']:draft['content']['assessments'].append({k:v for k,v in work.items() if k!='exists'})
        records={r['studentId']:r for r in draft['content']['records']}
        for r in chosen:
            grades=records[r['studentId']]['grades'];found=next((g for g in grades if g['assessmentId']==work['id']),None)
            if found:found.update(score=r['value'],label='')
            else:grades.append(Grade(assessmentId=work['id'],score=r['value']).model_dump(mode='json'))
        try:Content.model_validate(draft['content'])
        except ValidationError as exc:raise HTTPException(422,plain(exc)) from None
        created=now().isoformat()
        if not chosen and (not body.publish or not previous):updated,published=None,False
        else:updated,published=store_marks(self.journal,db,workspace,body.lessonKey,user,ctx['revision'],(previous['version'] if previous else 0)+1,draft,previous,body.publish,created)
        counts=dict(written=sum(r['outcome']=='write' for r in chosen),updated=sum(r['outcome']=='update' for r in chosen),overwritten=sum(r['outcome']=='conflict' for r in chosen),
                    same=p['counts']['same'],kept=len(conflicts)-sum(r['outcome']=='conflict' for r in chosen),skipped=p['counts']['skip'])
        if updated is not None:
            for r in chosen:
                db.execute('INSERT OR REPLACE INTO quiz_transfer_marks VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                           (workspace,quiz['id'],body.lessonKey,work['id'],r['studentId'],r['attemptId'],r['score'],r['value'],user['name'],created,body.day.isoformat(),work['name']))
            self.quizzes.log(db,ctx,'quiz.transferred',dict(lessonKey=body.lessonKey,day=body.day.isoformat(),assessmentId=work['id'],work=work['name'],publish=body.publish,pick=body.pick,counts=counts,
                                                          marks=[dict(studentId=r['studentId'],value=r['value'],previous=r['draft'],outcome=r['outcome']) for r in chosen]))
        done=updated or previous
        return dict(status='saved' if updated is not None else 'unchanged',lessonKey=body.lessonKey,day=body.day.isoformat(),assessmentId=work['id'],
                    version=done['version'] if done else 0,publishedVersion=done['publishedVersion'] if done else None,published=published,counts=counts)


def register(app, quizzes, Workspace):
    transfer=QuizTransfer(quizzes)
    journal=quizzes.journal

    @app.get('/api/journal/quiz-courses')
    def roster(groupId: str,subjectId: str,request: Request,workspace: Workspace='school'):
        user=journal.scope(request,workspace,staff=True);snap=quizzes.snapshots.get(workspace)
        lesson=course_lesson(snap,workspace,user,groupId,subjectId,now())
        journal.reading(lambda db:journal.recheck_read(db,user,snap,workspace))
        return dict(revision=snap.state.revision,groupId=groupId,subjectId=subjectId,group=lesson['group'],subject=lesson['subject'],
                    students=[dict(id=s['id'],name=s['name'],className=s['className']) for s in lesson['students']])

    @app.get('/api/journal/quizzes/{id}/transfer')
    def summary(id: str,request: Request,workspace: Workspace='school'):
        ctx=quizzes.context(request,workspace,id)
        if not ctx['canReview']:raise HTTPException(403,'Результаты в журнал переносит автор, учитель курса или администратор.')
        return quizzes.read_attempts(ctx,lambda db:transfer.summary(db,ctx))

    @app.get('/api/journal/quizzes/{id}/transfer/lessons')
    def lessons(id: str,day: date,request: Request,workspace: Workspace='school'):
        ctx=quizzes.context(request,workspace,id);editor(ctx)
        transfer.warm(ctx,day)
        def read(db):
            quizzes.recheck(db,ctx)
            return transfer.lessons(db,ctx,day)
        return quizzes.read_only(read)

    @app.post('/api/journal/quizzes/{id}/transfer/preview')
    def preview(id: str,body: Target,request: Request,workspace: Workspace='school'):
        ctx=quizzes.context(request,workspace,id);editor(ctx)
        if body.revision!=ctx['revision']:raise HTTPException(409,'Расписание изменилось. Обновите страницу.')
        transfer.warm(ctx,body.day)
        return quizzes.read_attempts(ctx,lambda db:transfer.view(transfer.plan(db,ctx,body)))

    @app.post('/api/journal/quizzes/{id}/transfer')
    def write(id: str,body: Transfer,request: Request,workspace: Workspace='school'):
        ctx=quizzes.context(request,workspace,id);editor(ctx)
        transfer.warm(ctx,body.day)
        with quizzes.access.lock,quizzes.access.connect() as db:
            quizzes.tables(db);bulk_tables(db);db.execute('BEGIN IMMEDIATE');quizzes.recheck(db,ctx)
            # A retry is recognised before any version or token check, so it never reports its own earlier success as stale.
            request_key,stored=replay(db,ctx['user'],'quiz.transfer',[workspace,id],body)
            if stored is not None:return stored
            if body.revision!=ctx['revision']:raise HTTPException(409,'Расписание изменилось. Обновите страницу.')
            quizzes.expire(db,ctx)
            p=transfer.plan(db,ctx,body)
            if p['problem']:raise HTTPException(*p['problem'])
            if p['token']!=body.token:raise HTTPException(409,'Журнал или результаты изменились. Посмотрите изменения ещё раз.')
            return remember(db,ctx['user'],body,request_key,transfer.apply(db,ctx,body,p))
