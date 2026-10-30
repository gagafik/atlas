"""Expected lessons compared with publications, scoped before exposing any pupil."""
from collections import OrderedDict
from datetime import date,datetime,timedelta
import hashlib,json,time
from threading import RLock
from typing import Literal
from zoneinfo import ZoneInfo
from fastapi import HTTPException,Request
from pydantic import Field,model_validator
from .models import Model
from .access import now
from .calendar_ops import dates,resolve_day
from .journal import frozen,key_for,staff_read,user_ids
from .notifications import encoded,permission_key
from .resources import teacher_ids
from .attendance_reports import csv_response

LABELS=dict(missing='Журнал не создан',draft='Нет публикации',incomplete='Заполнен не полностью',stale='Расписание или состав изменились',complete='Заполнен',upcoming='Урок ещё не закончился',orphaned='Публикация вне текущего расписания')
PAGE=50
CACHE_ENTRIES=4
CACHE_ROWS=200000
CACHE_SECONDS=600
KEYS=('lessonKey','date','start','end','subject','group','teachers','classNames','status','due','pupils','missingPupils','unmarkedPupils','rosterUnknown','topicFilled','needsSync','version','publishedVersion','hasDraftChanges','canOpen')
STATUS=KEYS.index('status')
# Server-only tail of every cached row: never part of the public projection, tokens or paging.
INTERNAL=('classIds','teacherIds','sourceDate')
CLASS_IDS,TEACHER_IDS,SOURCE=range(len(KEYS),len(KEYS)+3)
STALE='Журнал или доступ изменились. Повторите проверку.'


def actionable(r):
    """«Не заполнено»: a due lesson without a journal or publication, with an outdated one, or lacking the topic or marks. An unknown class size alone is an administrator's data issue, never a reminder for the teacher."""
    return bool(r['due'] and (r['status'] in ('missing','draft','stale') or r['status']=='incomplete' and (not r['topicFilled'] or r['missingPupils'] or r['unmarkedPupils'])))


class CoverageQuery(Model):
    start:date
    end:date
    mode:Literal['classes','teaching']='classes'
    classId:str=Field(default='',max_length=100)
    teacherId:str=Field(default='',max_length=100)
    subjectId:str=Field(default='',max_length=100)
    status:Literal['issues','all','missing','draft','incomplete','stale','complete','upcoming','orphaned']='issues'
    offset:int=Field(default=0,ge=0,le=10000000)
    token:str=Field(default='',max_length=64)
    asOf:datetime|None=None
    @model_validator(mode='after')
    def period(self):
        if not 0<=(self.end-self.start).days<=365:raise ValueError('Выберите период до 366 дней.')
        if self.asOf and (self.asOf.tzinfo is None or self.asOf>now()):raise ValueError('Момент проверки должен иметь часовой пояс и не быть в будущем.')
        return self


class CoverageDetail(CoverageQuery):
    lessonKey:str=Field(min_length=64,max_length=64)
    day:date
    pupilOffset:int=Field(default=0,ge=0,le=10000000)


class JournalCoverage:
    def __init__(self,reports):
        self.reports,self.access,self.journal,self.absences=reports,reports.access,reports.journal,reports.absences
        self.cache=OrderedDict();self.lock=RLock()

    def scope_key(self,db,user,snap,workspace,body):
        """Access half of the token: account, permissions and the unfiltered class/pupil scope."""
        self.journal.recheck_account(db,user)
        base=body.model_copy(update=dict(classId='',teacherId=''))
        try:classes,own=self.scope(db,user,snap,workspace,base)
        except HTTPException as error:return None,error
        # An administrator's scope is the whole school by definition: roster changes are covered by the data half.
        scope=[] if user['role']=='admin' else [sorted(classes),sorted(own),sorted((s.id,s.classId) for s in snap.state.students if s.classId in classes)]
        return hashlib.sha256(encoded([workspace,user['id'],permission_key(user),body.mode,*scope]).encode()).hexdigest()[:32],None

    def data_key(self,db,workspace,body,pupils,revision):
        """Data half: only journals and publications of the requested days (and pupils)."""
        drafts="SELECT count(*),coalesce(sum(e.version),0),coalesce(max(e.updated),'') FROM journal_entries e WHERE e.workspace=? AND json_extract(e.document,'$.lesson.date') BETWEEN ? AND ?"
        published="SELECT count(*),coalesce(sum(l.version),0),coalesce(max(l.published_at),'') FROM journal_attendance_lessons l WHERE l.workspace=? AND l.day BETWEEN ? AND ?"
        args=[workspace,body.start.isoformat(),body.end.isoformat()]
        if pupils is not None:
            member=' AND EXISTS(SELECT 1 FROM journal_members m WHERE m.workspace={0}.workspace AND m.lesson_key={0}.lesson_key{1} AND m.student_id IN (SELECT value FROM json_each(?)))'
            drafts+=member.format('e','');published+=member.format('l'," AND m.kind='published'");args.append(encoded(pupils))
        found=[tuple(db.execute(query,args).fetchone()) for query in [drafts,published]]
        return hashlib.sha256(encoded([revision,*found]).encode()).hexdigest()[:32]

    def token(self,db,user,snap,workspace,body):
        """64 characters: access half + data half. A sent token must keep the same access half."""
        key,error=self.scope_key(db,user,snap,workspace,body)
        if body.token and (key is None or body.token[:32]!=key):raise HTTPException(409,STALE)
        if error:raise error
        classes,_=self.scope(db,user,snap,workspace,body)
        # Pupils who were in these classes on any day count too: a transferred pupil's earlier lessons still belong to the class.
        pupils=None if user['role']=='admin' and not body.classId else sorted({s.id for s in snap.state.students if s.classId in classes}|{sid for sid,kind,target,*_ in snap.history.stints if kind=='class' and target in classes})
        current=db.execute('SELECT revision FROM workspaces WHERE id=?',(workspace,)).fetchone()
        return key+self.data_key(db,workspace,body,pupils,current[0] if current else 0)

    def scope(self,db,user,snap,workspace,body):
        own=user_ids(user,workspace,'teacher');state=snap.state
        if user['role']=='admin':classes={c.id for c in state.classes}
        elif body.mode=='classes':
            classes=self.absences.classes(db,user,snap,workspace,'journal_coverage')&set(snap.entities['class_'])
            if not classes:raise HTTPException(403,'Нужно назначение ответственным за класс.')
        else:
            if not own:raise HTTPException(403,'Нет назначения преподавателем.')
            # Every timetable version valid in the period contributes the classes its teacher taught; the index is kept per History, never rebuilt per request.
            classes=snap.history.taught(own,body.start,body.end)&{c.id for c in state.classes}
        if body.classId and body.classId not in classes:raise HTTPException(404,'Класс не найден или недоступен.')
        if body.teacherId and (body.teacherId not in snap.entities['teacher'] or user['role']!='admin' and body.mode=='teaching' and body.teacherId not in own):raise HTTPException(404,'Учитель не найден или недоступен.')
        return classes & {body.classId} if body.classId else classes,own

    def selected(self,row,user,body,classes,own):
        if body.subjectId and row['subjectId']!=body.subjectId:return False
        if body.teacherId and body.teacherId not in row['teacherIds']:return False
        if user['role']!='admin' and body.mode=='teaching' and not own&set(row['teacherIds']):return False
        return bool(classes&set(row['classIds']))

    def publications(self,db,user,workspace,day,keys,body,classes,own,people):
        # Start with compact date/key indexes, never scan journal grade documents.
        if user['role']=='admin' and not body.classId:
            found={r[0] for r in db.execute('SELECT lesson_key FROM journal_attendance_lessons WHERE workspace=? AND day=?',(workspace,day))}
        elif user['role']!='admin' and body.mode=='teaching':
            found={r[0] for r in db.execute('''SELECT DISTINCT lesson_key FROM journal_coverage_teachers
                WHERE workspace=? AND teacher_id IN (SELECT value FROM json_each(?)) AND day=?''',(workspace,encoded(sorted(own)),day))}
        else:
            ids=[id for id,s in people.items() if s.classId in classes]
            found={r[0] for r in db.execute('''SELECT DISTINCT lesson_key FROM journal_attendance INDEXED BY attendance_student
                WHERE workspace=? AND student_id IN (SELECT value FROM json_each(?)) AND day=?''',(workspace,encoded(ids),day))}
        found|=set(keys)
        if not found:return {},{}
        args=(encoded(sorted(found)),workspace)
        rows=db.execute('''SELECT e.lesson_key,e.version,e.published_version,e.lesson,e.topic_filled,e.draft_teachers,e.record_ids,e.unmarked_ids
            FROM json_each(?) x CROSS JOIN journal_coverage_documents e ON e.workspace=? AND e.lesson_key=x.value''',args)
        entries={};marks={}
        for r in rows:
            entries[r[0]]=dict(version=r[1],publishedVersion=r[2],lesson=json.loads(r[3]) if r[3] else None,topic=bool(r[4]),draftTeachers=json.loads(r[5]))
            unmarked=set(json.loads(r[7]));marks[r[0]]={id:'unmarked' if id in unmarked else 'marked' for id in json.loads(r[6])}
        return entries,marks

    def calculate(self,db,user,snap,workspace,body,*,detail=None):
        classes,own=self.scope(db,user,snap,workspace,body);state=snap.state
        if body.start<state.settings.yearStart or body.end>state.settings.yearEnd:raise HTTPException(422,'Контроль использует расписание текущего учебного года. Выберите даты внутри него.')
        cutoff=body.asOf.astimezone(ZoneInfo('Asia/Qyzylorda'))
        people={s.id:s for s in state.students};class_names={c.id:c.name for c in state.classes}
        teacher_filter=({body.teacherId} if body.teacherId else own) if user['role']!='admin' and body.mode=='teaching' or body.teacherId else None
        history,segments=snap.history,{}
        def segment(day):
            # One filtered calendar per history segment: versions and enrollment periods change only at their boundaries.
            index=history.segment(day)
            if index not in segments:
                view,ctx=history.context(day);pupils={s.id:s for s in view.students}
                # Resolve only potentially visible lessons. Full entity maps and all
                # relevant overrides remain available, including substitute teachers.
                groups={g.id for g in view.groups if classes&set(g.classIds) or any(pupils[id].classId in classes for id in g.studentIds)}
                replacement_ids={d.placementId for d in view.datedLessons if teacher_filter and teacher_filter&set(d.teacherIds or [])}
                placements=[p for p in view.placements if ctx.maps['workloads'][p.workloadId].groupId in groups and (not teacher_filter or teacher_filter&set(teacher_ids(ctx.maps['workloads'][p.workloadId])) or p.id in replacement_ids)]
                placement_ids={p.id for p in placements}
                changes=[d for d in view.datedLessons if d.placementId in placement_ids or not d.placementId and d.groupId in groups and (not teacher_filter or teacher_filter&set(d.teacherIds or []))]
                segments[index]=(view.model_copy(update=dict(placements=placements,datedLessons=changes,commitments=[])),ctx,pupils)
            return segments[index]
        rows=[];orphans={};expected=set();cancelled=0;detail_value=None
        period=[detail.day] if detail else dates(body.start,body.end)
        for day in period:
            calendar,ctx,pupils=segment(day)
            resolved,_=resolve_day(calendar,day,ctx)
            chosen=[]
            for row in resolved:
                if row['activity']!='lesson' or ctx.maps['groups'][row['groupId']].kind=='staff':continue
                if row['status']=='cancelled':
                    if self.selected(row|dict(classIds=row['originalClassIds']),user,body,classes,own):cancelled+=1
                    continue
                if self.selected(row,user,body,classes,own):chosen.append(row)
            entries,marks=self.publications(db,user,workspace,day.isoformat(),[key_for(r) for r in chosen],body,classes,own,pupils)
            for row in chosen:
                key=key_for(row);expected.add(key);saved=entries.get(key);published=saved['lesson'] if saved else None
                # A publication of an earlier day keeps the roster, time and teachers it was published with.
                fixed=frozen(published,row,cutoff.date())
                roster_unknown=not row['rosterComplete']
                group=ctx.maps['groups'][row['groupId']]
                if group.kind=='whole':
                    roster_unknown |= any(ctx.maps['classes'][c].studentCount is None or sum(pupils[id].classId==c for id in row['studentIds'])<(ctx.maps['classes'][c].studentCount or 0) for c in classes&set(row['classIds']))
                if row['audienceSize'] is not None and row['audienceSize']>len(row['studentIds']):roster_unknown=True
                actual=marks.get(key,{}) if published else {}
                if fixed:
                    held={s['id']:s['classId'] for s in published['students'] if s.get('enrolled',True)}
                    ids={id for id,class_id in held.items() if class_id in classes};stale=False;identity=published
                else:
                    held={id:pupils[id].classId for id in row['studentIds']}
                    ids={id for id in row['studentIds'] if pupils[id].classId in classes}
                    old_ids={s['id'] for s in published['students'] if s.get('enrolled',True) and (pupils[s['id']].classId if s['id'] in pupils else s['classId']) in classes} if published else set()
                    stale=bool(published and (any(published[k]!=row[k] for k in ['date','start','end','subjectId','groupId','teachers','rooms']) or set(published['teacherIds'])!=set(row['teacherIds']) or old_ids!=ids));identity=row
                missing=ids-set(actual);unmarked={id for id in ids if actual.get(id)=='unmarked'}
                due=day<cutoff.date() or day==cutoff.date() and row['end']<=cutoff.strftime('%H:%M')
                topic=bool(saved and saved['topic'])
                status='upcoming' if not due else 'missing' if not saved else 'draft' if not published else 'stale' if stale else 'incomplete' if missing or unmarked or not topic or roster_unknown else 'complete'
                value=dict(lessonKey=key,date=identity['date'],start=identity['start'],end=identity['end'],subject=identity['subject'],group=identity['group'],teachers=identity['teachers'],classNames=[class_names[c] for c in sorted(classes&set(row['classIds']))],status=status,due=due,
                    pupils=len(ids),missingPupils=len(missing),unmarkedPupils=len(unmarked),rosterUnknown=roster_unknown,topicFilled=topic,needsSync=stale,
                    version=saved['version'] if saved else 0,publishedVersion=saved['publishedVersion'] if saved else None,hasDraftChanges=bool(published and saved['version']!=saved['publishedVersion']),canOpen=staff_read(user,workspace,row),
                    # Current resolved teachers (a substitute wins even over a frozen publication) and the scope-limited classes.
                    classIds=sorted(classes&set(row['classIds'])),teacherIds=list(row['teacherIds']),sourceDate=row['sourceDate'] or row['date'])
                rows.append(value)
                if detail and key==detail.lessonKey:
                    names=sorted([dict(id=id,name=people[id].name if id in people else 'Ученик вне текущего состава',className=class_names.get(held[id],'Вне текущего состава'),issue='missing' if id in missing else 'unmarked') for id in missing|unmarked],key=lambda s:(s['name'].casefold(),s['id']))
                    detail_value=dict(lesson={k:value[k] for k in KEYS},pupils=names[detail.pupilOffset:detail.pupilOffset+PAGE],nextOffset=detail.pupilOffset+PAGE if len(names)>detail.pupilOffset+PAGE else None,total=len(names))
            if detail:continue
            for key,saved in entries.items():
                lesson=saved['lesson']
                if not lesson or lesson['date']!=day.isoformat():continue
                actual_classes={people[s['id']].classId for s in lesson['students'] if s['id'] in people}
                # Archived publications are visible only within current teaching
                # or class scope. No pupil names, comments or marks are exposed.
                archived=user['role']=='admin' and not body.classId and not actual_classes&classes
                archived_match=archived and (not body.subjectId or body.subjectId==lesson['subjectId']) and (not body.teacherId or body.teacherId in lesson['teacherIds'])
                if not archived_match and not self.selected(lesson|dict(classIds=actual_classes),user,body,classes,own):continue
                orphans[key]=dict(lessonKey=key,date=lesson['date'],start=lesson['start'],end=lesson['end'],subject=lesson['subject'],group=lesson['group'],teachers=lesson['teachers'],classNames=['Вне текущего состава'] if archived else [class_names[c] for c in sorted(classes&actual_classes)],status='orphaned',due=False,pupils=0,missingPupils=0,unmarkedPupils=0,rosterUnknown=False,topicFilled=saved['topic'],needsSync=True,version=saved['version'],publishedVersion=saved['publishedVersion'],hasDraftChanges=saved['version']!=saved['publishedVersion'],canOpen=user['role']=='admin' or bool(own&set(saved['draftTeachers'])),
                    classIds=[] if archived else sorted(classes&actual_classes),teacherIds=list(lesson['teacherIds']),sourceDate=lesson['date'])
        if detail:
            if not detail_value:raise HTTPException(404,'Урок не найден в доступном расписании выбранной даты.')
            return detail_value
        rows += [r for key,r in orphans.items() if key not in expected]
        rows.sort(key=lambda r:(r['date'],r['start'],r['group'],r['lessonKey']))
        counts={status:sum(r['status']==status for r in rows) for status in LABELS}
        counts.update(expected=len(expected),cancelled=cancelled,missingPupils=sum(r['missingPupils'] for r in rows if r['due']),unmarkedPupils=sum(r['unmarkedPupils'] for r in rows if r['due']))
        return dict(counts=counts,rows=rows)

    def finish(self,db_user,snap,workspace,token,body,action):
        # Only access is rechecked: a save elsewhere during a long calculation must not fail the page.
        with self.access.lock,self.access.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            key,error=self.scope_key(db,db_user,snap,workspace,body)
            if error or key!=token[:32]:raise HTTPException(409,STALE)
            self.scope(db,db_user,snap,workspace,body)
            self.access.record(db,action,workspace,detail=encoded(body.model_dump(mode='json',exclude={'token','offset'})))

    def result(self,db,user,snap,workspace,body,token):
        """Counts and compact rows (tuples in KEYS order) of a consistent snapshot, paged without recalculation."""
        path=db.execute('PRAGMA database_list').fetchone()[2]
        filters=encoded(body.model_dump(mode='json',exclude={'offset','status','token'}))
        with self.lock:
            stamp=time.monotonic()
            for k in [k for k,v in self.cache.items() if stamp-v[0]>CACHE_SECONDS]:self.cache.pop(k)
            for candidate in dict.fromkeys([body.token,token]):
                if candidate and (path,candidate,filters) in self.cache:
                    self.cache.move_to_end((path,candidate,filters));return self.cache[(path,candidate,filters)][1]|dict(token=candidate)
        if body.token and body.token!=token:raise HTTPException(409,STALE)
        value=self.calculate(db,user,snap,workspace,body)
        rows=tuple(tuple(tuple(r[k]) if isinstance(r[k],list) else r[k] for k in KEYS+INTERNAL) for r in value['rows'])
        value=dict(counts=value['counts'],rows=rows)
        if len(rows)<=CACHE_ROWS:
            with self.lock:
                self.cache[(path,token,filters)]=(time.monotonic(),value)
                while len(self.cache)>CACHE_ENTRIES or sum(len(v[1]['rows']) for v in self.cache.values())>CACHE_ROWS:self.cache.popitem(last=False)
        return value|dict(token=token)

    def summary(self,db,user,snap,workspace,classes,cached):
        """One row per class from the cached snapshot; a mixed group counts in every class it covers. No pupil is named or counted."""
        rows=[dict(zip(KEYS,r))|dict(classIds=r[CLASS_IDS],teacherIds=r[TEACHER_IDS]) for r in cached['rows']]
        pending=[r for r in rows if actionable(r)]
        # Read live, never cached: binding an account to a teacher clears the warning at once.
        bound=set(self.reminders.holders(db,workspace,{t for r in pending for t in r['teacherIds']}))
        teachers=snap.entities['teacher'];result=[];by_class={}
        for r in rows:
            for id in r['classIds']:by_class.setdefault(id,[]).append(r)
        for c in sorted((c for c in snap.state.classes if c.id in classes),key=lambda c:(c.name.casefold(),c.id)):
            mine=by_class.get(c.id,[]);due=[r for r in mine if r['due']];open_=[r for r in mine if actionable(r)]
            result.append(dict(classId=c.id,className=c.name,classTeacher=teachers[c.classTeacherId].name if c.classTeacherId in teachers else None,expected=len(due),unfilled=len(open_),
                **{status:sum(r['status']==status for r in due) for status in ['missing','draft','incomplete','stale','complete']},
                upcoming=sum(r['status']=='upcoming' for r in mine),rosterUnknown=sum(r['rosterUnknown'] for r in due),
                lastRecord=max((r['date'] for r in mine if r['publishedVersion'] is not None and r['status']!='orphaned'),default=None),
                unfilledSince=min((r['date'] for r in open_),default=None),unbound=sum(not bound&set(r['teacherIds']) for r in open_)))
        value=dict(classes=result,counts=cached['counts'])
        if user['role']=='admin':
            # Whether a colleague has an account is account administration data: administrators only.
            groups={}
            for r in pending:
                if bound&set(r['teacherIds']):continue
                for t in set(r['teacherIds']) or {None}:
                    g=groups.setdefault(t,dict(teacherId=t,teacher=teachers[t].name if t in teachers else 'Учитель не назначен',lessons=0,since=r['date'],classNames=set()))
                    g['lessons']+=1;g['since']=min(g['since'],r['date']);g['classNames'].update(r['classNames'])
            value['unbound']=sorted([g|dict(classNames=sorted(g['classNames'])) for g in groups.values()],key=lambda g:(-g['lessons'],g['teacher']))
        return value

    def register(self,app,Workspace):
        # Imported here: coverage_reminders imports this module.
        from .coverage_reminders import CoverageReminders,settings
        self.reminders=CoverageReminders(self);self.reminders.register(app,Workspace)

        @app.get('/api/journal/coverage/options')
        def options(request:Request,workspace:Workspace='school'):
            user,snap=self.reports.context(request,workspace)
            self.reminders.prepare()
            with self.access.read_connect() as db:
                db.execute('BEGIN');self.absences.recheck(db,user,snap,workspace)
                modes=['classes'] if user['role']=='admin' else (['teaching'] if user['role']=='teacher' else [])+(['classes'] if self.absences.classes(db,user,snap,workspace,'journal_coverage') else [])
                year=CoverageQuery(start=snap.state.settings.yearStart,end=min(snap.state.settings.yearEnd,snap.state.settings.yearStart+timedelta(days=365)))
                scopes={mode:self.scope(db,user,snap,workspace,year.model_copy(update=dict(mode=mode)))[0] for mode in modes}
                own=user_ids(user,workspace,'teacher')
                return dict(modes=modes,classes={mode:[dict(id=c.id,name=c.name) for c in snap.state.classes if c.id in ids] for mode,ids in scopes.items()},teachers={mode:[dict(id=t.id,name=t.name) for t in snap.state.teachers if user['role']=='admin' or mode=='classes' or t.id in own] for mode in modes},yearStart=snap.state.settings.yearStart,yearEnd=snap.state.settings.yearEnd,today=now().astimezone(ZoneInfo('Asia/Qyzylorda')).date(),subjects=[dict(id=s.id,name=s.name) for s in snap.state.subjects],
                    canConfigure=user['role']=='admin',reminders={k:v for k,v in settings(db,workspace).items() if k in ['enabled','afterDays','weeklyDigest']})

        def read(body,request,workspace,detail=False,export=False):
            user,snap=self.reports.context(request,workspace)
            if not body.asOf:body=body.model_copy(update={'asOf':now()})
            if detail and not body.start<=body.day<=body.end:raise HTTPException(422,'Дата урока должна входить в проверяемый период.')
            with self.access.read_connect() as db:
                db.execute('BEGIN');self.absences.recheck(db,user,snap,workspace)
                token=self.token(db,user,snap,workspace,body)
                if detail:
                    # Drill-downs are live: only the access half of a sent token was checked.
                    value=self.calculate(db,user,snap,workspace,body,detail=body);used=body.token or token
                else:
                    cached=self.result(db,user,snap,workspace,body,token);used=cached['token']
                    rows=[r for r in cached['rows'] if body.status=='all' or body.status=='issues' and r[STATUS] not in ['complete','upcoming'] or r[STATUS]==body.status]
                    if export:
                        if len(rows)>100000:raise HTTPException(422,'Более 100 000 уроков: сократите период выгрузки.')
                        rows=[dict(zip(KEYS,r)) for r in rows]
                        value=csv_response([['Дата','Начало','Конец','Предмет','Группа','Классы','Учителя','Состояние','Ученики в области проверки','Нет записи','Не отмечено','Состав требует проверки','Тема заполнена','Редакция публикации','Проверено на момент']]+[[r['date'],r['start'],r['end'],r['subject'],r['group'],', '.join(r['classNames']),r['teachers'],LABELS[r['status']],r['pupils'],r['missingPupils'],r['unmarkedPupils'],'Да' if r['rosterUnknown'] else 'Нет','Да' if r['topicFilled'] else 'Нет',r['publishedVersion'],body.asOf.isoformat()] for r in rows])
                    else:value=dict(counts=cached['counts'],total=len(rows),items=[dict(zip(KEYS,r)) for r in rows[body.offset:body.offset+PAGE]],nextOffset=body.offset+PAGE if len(rows)>body.offset+PAGE else None)
            self.finish(user,snap,workspace,used,body,'journal.coverage_exported' if export else 'journal.coverage_read')
            return value if export else value|dict(token=used,asOf=body.asOf.isoformat(),membership='dated',source='dated_schedule_and_published_journals')

        @app.post('/api/journal/coverage/report')
        def report(body:CoverageQuery,request:Request,workspace:Workspace='school'):return read(body,request,workspace)
        @app.post('/api/journal/coverage/classes')
        def classes_summary(body:CoverageQuery,request:Request,workspace:Workspace='school'):
            user,snap=self.reports.context(request,workspace)
            if body.mode!='classes':raise HTTPException(422,'Сводка по классам строится по классам.')
            # The filters equal the plain report's, so both share one snapshot and token. A drill-down is a /report call with
            # classId and no token: the data half of a token covers the pupils of the requested classes, so the summary's token may be stale there (409).
            body=body.model_copy(update=dict(classId='',teacherId='',status='all',offset=0,asOf=body.asOf or now()))
            self.reminders.prepare()
            with self.access.read_connect() as db:
                db.execute('BEGIN');self.absences.recheck(db,user,snap,workspace)
                token=self.token(db,user,snap,workspace,body)
                cached=self.result(db,user,snap,workspace,body,token)
                classes,_=self.scope(db,user,snap,workspace,body)
                value=self.summary(db,user,snap,workspace,classes,cached)
            self.finish(user,snap,workspace,cached['token'],body,'journal.coverage_classes_read')
            return value|dict(token=cached['token'],asOf=body.asOf.isoformat())
        @app.post('/api/journal/coverage/details')
        def details(body:CoverageDetail,request:Request,workspace:Workspace='school'):return read(body,request,workspace,detail=True)
        @app.post('/api/journal/coverage/export')
        def export(body:CoverageQuery,request:Request,workspace:Workspace='school'):return read(body,request,workspace,export=True)
