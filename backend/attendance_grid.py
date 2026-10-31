"""Class attendance grid (days × lessons per pupil) with a daily confirmation by the class teacher.

Reads are dated: a pupil appears in the class on the days the class held them, drafts are never shown. Every mark is stored
through Journal.store_revision, so history, notices, projections and the teacher's private draft keep working as before."""
from collections import OrderedDict
from datetime import date as Day,timedelta
import hashlib,json
from typing import Literal
from fastapi import HTTPException,Query,Request
from pydantic import Field,ValidationError,model_validator
from .models import Model
from .access import now
from . import absence_codes,attendance_days as store
from .attendance_reports import Period
from .calendar_ops import dates,plain,resolve_day
from .coverage_reminders import SYSTEM,ZONE,settings as reminder_settings
from .journal import Content,PupilRecord,current_lesson,frozen,key_for,user_ids
from .journal_periods import range_locks
from .notifications import encoded

MAX_DAYS=62
MAX_CELLS=1500
MAX_LESSONS=100
MAX_CONFIRM_DAYS=31
MEASURED_CLASSES=5
KEEP_REQUESTS=30
ATTENDANCE=PupilRecord.model_fields['attendance'].annotation
# Everything a mark consists of; grades, the comment and the pupil stay as the teacher left them.
MARK_FIELDS=[k for k in PupilRecord.model_fields if k not in ('studentId','comment','grades')]
EDIT_ONLY='Правка сетки доступна классному руководителю и администратору.'
NO_CLASS='Класс не найден или недоступен.'
CHANGED='Доступ изменился. Обновите страницу.'


def moment():return now().astimezone(ZONE)
def blank(id):
    # An explicit 'unmarked': the lesson track may change the default, and an attendance-only publication must never mark anybody present.
    return PupilRecord(studentId=id,attendance='unmarked').model_dump(mode='json')
def ru(day):return day.strftime('%d.%m.%Y') if isinstance(day,Day) else Day.fromisoformat(day).strftime('%d.%m.%Y')


class GridCell(Model):
    lessonKey: str = Field(min_length=64,max_length=64)
    day: Day
    studentId: str = Field(min_length=1,max_length=100)
    expected: ATTENDANCE | None = None
    expectedLate: int = Field(default=0,ge=0,le=600)


class GridMarks(Model):
    requestId: str = Field(pattern=r'^[a-f0-9-]{36}$')
    classId: str = Field(min_length=1,max_length=100)
    cells: list[GridCell] = Field(min_length=1,max_length=MAX_CELLS)
    attendance: ATTENDANCE
    lateMinutes: int = Field(default=0,ge=0,le=600)
    reason: str = Field(default='',max_length=500)
    @model_validator(mode='after')
    def valid(self):
        if len({(c.lessonKey,c.studentId) for c in self.cells})!=len(self.cells):raise ValueError('Ячейки не должны повторяться.')
        days={}
        for c in self.cells:
            if days.setdefault(c.lessonKey,c.day)!=c.day:raise ValueError('Урок не может относиться к двум датам.')
        if len(days)>MAX_LESSONS:raise ValueError('Выберите не больше 100 уроков за одно действие.')
        if self.attendance!='late' and self.lateMinutes:raise ValueError('Минуты опоздания указываются только при опоздании.')
        return self


class DayChoice(Model):
    date: Day
    version: int = Field(ge=0)


class DayDecision(Model):
    requestId: str = Field(pattern=r'^[a-f0-9-]{36}$')
    classId: str = Field(min_length=1,max_length=100)
    days: list[DayChoice] = Field(min_length=1,max_length=MAX_CONFIRM_DAYS)
    action: Literal['confirm','revoke']
    comment: str = Field(default='',max_length=500)
    acknowledge: bool = False
    @model_validator(mode='after')
    def valid(self):
        if len({d.date for d in self.days})!=len(self.days):raise ValueError('Дни не должны повторяться.')
        if self.action=='revoke' and not self.comment.strip():raise ValueError('Объясните, почему подтверждение снято.')
        return self


class AttendanceGrid:
    def __init__(self,coverage):
        self.coverage,self.reports,self.absences,self.journal,self.access=coverage,coverage.reports,coverage.absences,coverage.journal,coverage.access
        # Live day status and uncovered lessons beside the cached summary of the attendance report.
        self.reports.days=self.report_block

    def clock(self):
        # Called late: tests and the journal itself fix the school date through journal.today.
        from .journal import today
        return today(),moment().strftime('%H:%M')

    def period(self,snap,start,end):
        if not 0<=(end-start).days<MAX_DAYS:raise HTTPException(422,'Выберите период до 62 дней.')
        year=snap.state.settings
        if start<year.yearStart or end>year.yearEnd:raise HTTPException(422,'Сетка использует расписание текущего учебного года. Выберите даты внутри него.')

    # Access: 'edit' (administrator, class teacher or deputy with attendance_grid), 'read' (attendance_read), 'own' (a subject teacher: own lessons only).
    def scopes(self,db,user,snap,workspace,start,end):
        classes={c.id for c in snap.state.classes}
        if user['role']=='admin':return {id:'edit' for id in classes}
        own=user_ids(user,workspace,'teacher') if user['role']=='teacher' else set()
        result={id:'own' for id in snap.history.taught(own,start,end)&classes} if own else {}
        for capability,level in (('attendance_read','read'),('attendance_grid','edit')):
            for id in self.absences.classes(db,user,snap,workspace,capability)&classes:result[id]=level
        return result

    def level(self,db,user,snap,workspace,class_id,start,end,edit=False):
        level=self.scopes(db,user,snap,workspace,start,end).get(class_id)
        # Nothing is said about classes the account cannot open.
        if level is None:raise HTTPException(404,NO_CLASS)
        if edit and level!='edit':raise HTTPException(403,EDIT_ONLY)
        return level

    def typed(self,db,user,snap,workspace):
        """Classes whose absence types this account may see: the report (attendance_read) and the notes (absence_review) show them there already.
        Editing the grid (attendance_grid) and teaching in the class show the result of a note, never its type."""
        if user['role']=='admin':return {c.id for c in snap.state.classes}
        return self.absences.classes(db,user,snap,workspace,'attendance_read')|self.absences.classes(db,user,snap,workspace,'absence_review')

    def class_lessons(self,snap,class_ids,days):
        """{day: (lesson rows of the classes, {pupil id: pupil of that day})}. Only the groups of these classes are resolved, never the whole school."""
        days=tuple(days);key=(frozenset(class_ids),days)
        with snap.lock:
            cache=snap.__dict__.setdefault('attendance_grid_lessons',OrderedDict())
            if key in cache:
                cache.move_to_end(key);return cache[key]
        history,segments,result=snap.history,{},{}
        for day in days:
            index=history.segment(day)
            if index not in segments:
                view,ctx=history.context(day);pupils={s.id:s for s in view.students}
                groups={g.id for g in view.groups if class_ids&set(g.classIds) or any(pupils[id].classId in class_ids for id in g.studentIds)}
                placements=[p for p in view.placements if ctx.maps['workloads'][p.workloadId].groupId in groups]
                ids={p.id for p in placements}
                changes=[d for d in view.datedLessons if d.placementId in ids or not d.placementId and d.groupId in groups]
                segments[index]=(view.model_copy(update=dict(placements=placements,datedLessons=changes,commitments=[])),ctx,pupils)
            calendar,ctx,pupils=segments[index]
            resolved,_=resolve_day(calendar,day,ctx)
            result[day]=([r for r in resolved if r['activity']=='lesson' and ctx.maps['groups'][r['groupId']].kind!='staff' and class_ids&set(r['classIds'] or r['originalClassIds'])],pupils)
        with snap.lock:
            cache[key]=result
            while len(cache)>6:cache.popitem(last=False)
        return result

    def stored(self,db,workspace,keys):
        """Compact publication of each journal: version, published lesson (time, teachers, pupils with their class) and the draft's teachers."""
        keys=sorted(set(keys))
        if not keys:return {}
        rows=db.execute('''SELECT e.lesson_key,e.version,e.published_version,e.lesson,e.draft_teachers
            FROM json_each(?) x CROSS JOIN journal_coverage_documents e ON e.workspace=? AND e.lesson_key=x.value''',(encoded(keys),workspace))
        return {r[0]:dict(version=r[1],publishedVersion=r[2],lesson=json.loads(r[3]) if r[3] else None,draftTeachers=json.loads(r[4]) if r[4] else []) for r in rows}

    def build(self,db,user,snap,workspace,class_id,start,end,level,lessons,typed=False):
        """The grid of one class in one read snapshot: lessons, cells, counts and day states. Returns (response part, private data).
        `typed`: the account may see the absence type of an excused cell."""
        state=snap.state;today,at=self.clock();iso_today=today.isoformat()
        closed=range_locks(db,workspace,start.isoformat(),end.isoformat())
        locked=lambda day:[p['name'] for p in closed if p['start']<=day<=p['end']]
        stored=self.stored(db,workspace,[key_for(r) for rows,_ in lessons.values() for r in rows])
        own=user_ids(user,workspace,'teacher') if level=='own' else set()
        ended=lambda day,finish:day<iso_today or day==iso_today and finish<=at
        items=[];day_members={};active={};complete={}
        for day,(rows,pupils) in lessons.items():
            iso=day.isoformat();day_members[iso]={id for id,p in pupils.items() if p.active and p.classId==class_id}
            for row in rows:
                key=key_for(row);saved=stored.get(key);published=saved['lesson'] if saved else None
                fixed=frozen(published,row,today);cancelled=row['status']=='cancelled'
                # A publication of an earlier day keeps the pupils it was made for; a cancelled lesson shows only what was published.
                if fixed or cancelled and published:roster={s['id'] for s in published['students'] if s.get('enrolled',True) and s['classId']==class_id}
                elif cancelled:roster=set()
                else:roster={id for id in row['studentIds'] if pupils[id].classId==class_id}
                identity=published if fixed else row;over=ended(identity['date'],identity['end'])
                if not cancelled:
                    active[iso]=active.get(iso,0)+1;complete[iso]=complete.get(iso,True) and over
                teachers=set(row['teacherIds'])|set(published['teacherIds'] if published else [])|set(saved['draftTeachers'] if saved else [])
                if level=='own' and not own&teachers:continue
                source=row['sourceDate'] or row['date'];stops=sorted({*locked(row['date']),*locked(source)})
                items.append(dict(lessonKey=key,start=identity['start'],end=identity['end'],subject=identity['subject'],subjectId=identity['subjectId'],group=identity['group'],teachers=identity['teachers'],
                    status=row['status'],state='published' if published else 'draft' if saved else 'none',hasDraftChanges=bool(published and saved['version']!=saved['publishedVersion']),
                    frozen=fixed,archived=False,canEdit=level=='edit' and not cancelled and row['date']<=iso_today and not stops,
                    version=saved['version'] if saved else 0,publishedVersion=saved['publishedVersion'] if saved else None,
                    _date=row['date'],_roster=roster,_ended=over,_cancelled=cancelled))
        keys={i['lessonKey'] for i in items}
        held=set().union(*day_members.values(),*(i['_roster'] for i in items))
        facts={}
        if held:
            period=Period(start=start,end=end,classId=class_id)
            query,args=self.reports.query(user,snap,workspace,period,dict.fromkeys(sorted(held)),effective=True)
            found=db.execute(query+'SELECT lesson_key,student_id,day,raw,effective,late_minutes,notice_code FROM visible',args).fetchall()
            # Published lessons that the timetable no longer shows stay visible, read-only, for the pupils the publication held in this class.
            old=self.stored(db,workspace,{r[0] for r in found if r[0] not in keys});archived={}
            for key,student,day,raw,effective,late,code in found:
                if key in keys:facts[key,student]=(raw,effective,late,code)
                elif key in old and old[key]['lesson']:
                    lesson=old[key]['lesson']
                    if any(s['id']==student and s.get('enrolled',True) and s['classId']==class_id for s in lesson['students']):
                        facts[key,student]=(raw,effective,late,code);archived.setdefault(key,set()).add(student)
            for key,pupils in archived.items():
                lesson=old[key]['lesson']
                if level=='own' and not own&(set(lesson['teacherIds'])|set(old[key]['draftTeachers'])):continue
                items.append(dict(lessonKey=key,start=lesson['start'],end=lesson['end'],subject=lesson['subject'],subjectId=lesson['subjectId'],group=lesson['group'],teachers=lesson['teachers'],
                    status='archived',state='published',hasDraftChanges=False,frozen=False,archived=True,canEdit=False,version=old[key]['version'],publishedVersion=old[key]['publishedVersion'],
                    _date=lesson['date'],_roster=pupils,_ended=ended(lesson['date'],lesson['end']),_cancelled=False))
        hidden=not typed
        cells={};by_day={};by_pupil={};total=store.counts()
        for item in items:
            row=cells[item['lessonKey']]={}
            for id in sorted(item['_roster']):
                fact=facts.get((item['lessonKey'],id))
                row[id]=[fact[0],fact[1],fact[2],None if hidden else fact[3]] if fact else [None,None,0,None]
                # A mark counts whenever it exists; a missing or empty one is a gap only once the lesson is over.
                if fact:status=fact[1] if fact[1]!='unmarked' or item['_ended'] else None
                else:status='noRecord' if item['_ended'] and not item['_cancelled'] else None
                by_pupil.setdefault(id,store.counts())
                if status:
                    for target in (total,by_day.setdefault(item['_date'],store.counts()),by_pupil[id]):target[status]+=1
        people={s.id:s for s in state.students};names={}
        missing=[id for id in by_pupil if id not in people]
        if missing:names=dict(db.execute("SELECT student_id,name FROM journal_members WHERE workspace=? AND kind='published' AND student_id IN (SELECT value FROM json_each(?))",(workspace,encoded(sorted(missing)))).fetchall())
        name=lambda id:people[id].name if id in people else names.get(id,'Ученик вне текущего состава')
        pupils=sorted((dict(id=id,name=name(id),active=bool(people[id].active) if id in people else False,counts=c,rates=store.rates(c)) for id,c in by_pupil.items()),key=lambda p:(p['name'].casefold(),p['id']))
        marked=store.day_states(db,workspace,{class_id},start,end).get(class_id,{})
        day_rows=[];day_pupils={}
        for iso in sorted({i['_date'] for i in items}):
            mine=sorted((i for i in items if i['_date']==iso),key=lambda i:(i['start'],i['group'],i['subject'],i['lessonKey']))
            counts=by_day.get(iso) or store.counts();stops=locked(iso);current=marked.get(iso)
            ready=bool(active.get(iso) and complete.get(iso))
            day_pupils[iso]=day_members.get(iso,set()).union(*(i['_roster'] for i in mine))
            day_rows.append(dict(date=iso,lessons=[{k:v for k,v in i.items() if not k.startswith('_')} for i in mine],locked=stops,counts=counts,rates=store.rates(counts),
                openLessons=sum(i['_ended'] and not i['_cancelled'] and i['state']!='published' for i in mine),
                confirmation=dict(status=current['status'] if current else 'none',version=current['version'] if current else 0,updated=current['updated'] if current else None,actor=current['actor'] if current else None,
                    expected=ready,canConfirm=ready and level=='edit' and not stops)))
        value=dict(days=day_rows,pupils=pupils,cells=cells,totals=dict(counts=total,rates=store.rates(total)))
        return value,dict(rosters={i['lessonKey']:i['_roster'] for i in items},day_pupils=day_pupils,active=active)

    def history_rows(self,db,workspace,key,student):
        rows=db.execute('''SELECT version,updated,actor_name,
            (SELECT json_extract(r.value,'$.attendance') FROM json_each(document,'$.content.records') r WHERE json_extract(r.value,'$.studentId')=?),
            (SELECT json_extract(r.value,'$.lateMinutes') FROM json_each(document,'$.content.records') r WHERE json_extract(r.value,'$.studentId')=?)
            FROM journal_history WHERE workspace=? AND lesson_key=? AND action='journal.published' ORDER BY version DESC LIMIT 50''',(student,student,workspace,key)).fetchall()
        items=[];later=None
        # Newest first: a run of equal marks is one entry, dated by the revision that set it.
        for version,updated,actor,attendance,late in reversed([r for r in rows if r[3] is not None]):
            if (attendance,late)!=later:items.append(dict(version=version,updated=updated,actor=actor,attendance=attendance,lateMinutes=late))
            later=(attendance,late)
        return items[::-1]

    def receipt(self,db,workspace,user,body,other):
        """(fingerprint, stored result or None); the same request id with other content is refused."""
        fingerprint=hashlib.sha256(encoded(body.model_dump(mode='json')).encode()).hexdigest()
        row=db.execute('SELECT request_hash,result FROM attendance_grid_requests WHERE workspace=? AND actor_id=? AND request_id=?',(workspace,user['id'],body.requestId)).fetchone()
        if row and row[0]!=fingerprint:raise HTTPException(409,other)
        return fingerprint,json.loads(row[1]) if row else None

    def remember(self,db,workspace,user,body,fingerprint,result):
        db.execute('INSERT INTO attendance_grid_requests VALUES (?,?,?,?,?,?)',(workspace,user['id'],body.requestId,fingerprint,now().isoformat(),encoded(result)))
        db.execute('DELETE FROM attendance_grid_requests WHERE workspace=? AND created<?',(workspace,(now()-timedelta(days=KEEP_REQUESTS)).isoformat()))
        return result

    def begin(self,db):
        # Journal.tables may have opened a transaction already (a migration step); otherwise take the writer now.
        if not db.in_transaction:db.execute('BEGIN IMMEDIATE')

    def write_lesson(self,db,workspace,state,user,key,existing,resolved,edits,created):
        """Only a mark changes: the publication is the previous one (or a blank one) plus the edited marks, the teacher's draft keeps everything plus them.

        Two ordinary revisions, exactly as when a grade is posted from the homework panel. This is the one place a dedicated attendance save of the lesson track would replace."""
        def apply(content,lesson,edits):
            records={r['studentId']:r for r in content['records']};ids=[s['id'] for s in lesson['students']]
            rows=[records.get(id,blank(id)) for id in ids]+[r for id,r in records.items() if id not in ids]
            return dict(content,records=[r|{k:blank(r['studentId'])[k] for k in MARK_FIELDS}|dict(attendance=edits[r['studentId']][0],lateMinutes=edits[r['studentId']][1]) if r['studentId'] in edits else r for r in rows])
        draft_doc=resolved['document'];old_public=existing['published'] if existing else None
        base_lesson=old_public['lesson'] if resolved['rosterFrozen'] else draft_doc['lesson']
        base_content=old_public['content'] if old_public else Content(records=[PupilRecord(studentId=s['id'],attendance='unmarked') for s in base_lesson['students']]).model_dump(mode='json')
        published=dict(lesson=base_lesson,content=apply(base_content,base_lesson,edits))
        draft=dict(draft_doc,content=apply(draft_doc['content'],draft_doc['lesson'],edits))
        try:
            for document in (published,draft):Content.model_validate(document['content'])
        except ValidationError as exc:raise HTTPException(422,plain(exc)) from None
        version=existing['version'] if existing else 0
        updated=self.journal.store_revision(db,workspace,key,user,state.revision,version+1,published,existing,publish=True,created=created)
        if draft!=published:updated=self.journal.store_revision(db,workspace,key,user,state.revision,version+2,draft,updated,created=created)
        return updated

    def roster_of(self,snap,class_id,day,current,existing,resolved):
        if resolved['rosterFrozen']:return {s['id'] for s in existing['published']['lesson']['students'] if s.get('enrolled',True) and s['classId']==class_id}
        classes={s.id:s.classId for s in snap.history.context(day)[0].students}
        return {id for id in current['studentIds'] if classes.get(id)==class_id}

    def register(self,app,Workspace):
        from .absences import tables as absence_tables
        from .class_staff import led

        def rows_of(snap,memo):
            def rows(day):
                if day not in memo:memo[day]=snap.lesson_rows(day)
                return memo[day]
            return rows

        @app.get('/api/journal/attendance/grid/options')
        def options(request:Request,workspace:Workspace='school'):
            user,snap=self.reports.context(request,workspace);year=snap.state.settings
            with self.access.read_connect() as db:
                db.execute('BEGIN');self.absences.recheck(db,user,snap,workspace)
                levels=self.scopes(db,user,snap,workspace,year.yearStart,year.yearEnd);leading=led(user,snap,workspace)
                classes=sorted((c for c in snap.state.classes if c.id in levels),key=lambda c:(c.name.casefold(),c.id))
                first=next((c.id for c in classes if c.id in leading),classes[0].id if classes else '')
                # Absence types are for those who already see them in the report or the notes, not for a grid editor or a subject teacher.
                see=self.typed(db,user,snap,workspace)
                codes=absence_codes.current(db,workspace)['items'] if any(c.id in see for c in classes) else []
                return dict(available=bool(classes),admin=user['role']=='admin',today=self.clock()[0].isoformat(),yearStart=year.yearStart,yearEnd=year.yearEnd,
                    classes=[dict(id=c.id,name=c.name,level=levels[c.id],classTeacher=c.id in leading) for c in classes],defaultClassId=first,codes=codes)

        @app.get('/api/journal/attendance/grid')
        def grid(request:Request,classId:str=Query(min_length=1,max_length=100),start:Day=Query(),end:Day=Query(),workspace:Workspace='school'):
            user,snap=self.reports.context(request,workspace);self.period(snap,start,end)
            with self.access.read_connect() as db:
                db.execute('BEGIN');self.absences.recheck(db,user,snap,workspace)
                level=self.level(db,user,snap,workspace,classId,start,end)
                lessons=self.class_lessons(snap,{classId},dates(start,end))
                typed=classId in self.typed(db,user,snap,workspace)
                value,_=self.build(db,user,snap,workspace,classId,start,end,level,lessons,typed)
                codes=absence_codes.current(db,workspace)['items'] if typed else []
                className=snap.entities['class_'][classId].name
            # Only access is rechecked before the answer is released, as the attendance report does.
            with self.access.lock,self.access.connect() as db:
                db.execute('BEGIN IMMEDIATE');self.journal.recheck_account(db,user)
                if self.scopes(db,user,snap,workspace,start,end).get(classId)!=level:raise HTTPException(409,CHANGED)
                self.access.record(db,'attendance.grid_read',workspace,detail=encoded(dict(classId=classId,start=start,end=end)))
            return dict(classId=classId,className=className,level=level,start=start.isoformat(),end=end.isoformat(),today=self.clock()[0].isoformat(),canEdit=level=='edit',codes=codes)|value

        @app.get('/api/journal/attendance/grid/history')
        def history(request:Request,classId:str=Query(min_length=1,max_length=100),lessonKey:str=Query(min_length=64,max_length=64),studentId:str=Query(min_length=1,max_length=100),day:Day=Query(),workspace:Workspace='school'):
            user,snap=self.reports.context(request,workspace);self.period(snap,day,day)
            with self.access.read_connect() as db:
                db.execute('BEGIN');self.absences.recheck(db,user,snap,workspace)
                level=self.level(db,user,snap,workspace,classId,day,day)
                _,private=self.build(db,user,snap,workspace,classId,day,day,level,self.class_lessons(snap,{classId},[day]))
                if studentId not in private['rosters'].get(lessonKey,()):raise HTTPException(404,'Отметка не найдена или недоступна.')
                items=self.history_rows(db,workspace,lessonKey,studentId)
            return dict(items=items)

        @app.post('/api/journal/attendance/grid/marks')
        def marks(body:GridMarks,request:Request,workspace:Workspace='school'):
            user,snap=self.reports.context(request,workspace);state=snap.state;today,_=self.clock()
            days=sorted({c.day for c in body.cells})
            if days[0]<state.settings.yearStart or days[-1]>state.settings.yearEnd:raise HTTPException(422,'Дата вне учебного года.')
            with self.access.read_connect() as db:
                db.execute('BEGIN');self.absences.recheck(db,user,snap,workspace);self.level(db,user,snap,workspace,body.classId,days[0],days[-1],edit=True)
            # Resolved outside the writer: the snapshot keeps only a few days at a time.
            rows_for_day=rows_of(snap,{day:snap.lesson_rows(day) for day in days})
            with self.access.lock,self.access.connect() as db:
                self.journal.tables(db);absence_tables(db);self.begin(db)
                self.absences.recheck(db,user,snap,workspace)
                self.level(db,user,snap,workspace,body.classId,days[0],days[-1],edit=True)
                fingerprint,stored=self.receipt(db,workspace,user,body,'Запрос уже использован для других ячеек.')
                if stored is not None:return stored
                groups={}
                for cell in body.cells:groups.setdefault(cell.lessonKey,[]).append(cell)
                target=(body.attendance,body.lateMinutes);plan=[];stale=[]
                # Every lesson is checked before the first one is written: one refusal leaves the whole request unapplied.
                for key,group in groups.items():
                    day=group[0].day;existing=self.journal.find(workspace,key,db)
                    if existing:current=current_lesson(state,existing['document']['lesson'],rows_for_day)
                    else:current=next((r for r in rows_for_day(day) if r['activity']=='lesson' and key_for(r)==key),None)
                    resolved=self.journal.resolved_entry(state,workspace,SYSTEM,key,existing,current,sync=True,db=db)
                    if day>today:raise HTTPException(422,'Урок ещё не прошёл.')
                    if resolved['periodLocks']:raise HTTPException(403,'Журнал закрыт в периоде: '+', '.join(p['name'] for p in resolved['periodLocks'])+'. Для исправления администратор должен открыть период с указанием причины.')
                    if not resolved['writable'] or resolved['currentDate']!=day.isoformat():raise HTTPException(409,'Урок перенесён или отменён. Обновите сетку.')
                    roster=self.roster_of(snap,body.classId,day,current,existing,resolved)
                    published={r['studentId']:(r['attendance'],r['lateMinutes']) for r in (existing['published']['content']['records'] if existing and existing['published'] else [])}
                    edits={};told=0
                    for cell in group:
                        if cell.studentId not in roster:raise HTTPException(404,'Ученик не относится к классу на эту дату.')
                        # A missing record is an unmarked one on both sides: a publication that leaves the pupil unmarked changes nothing the grid showed.
                        was=published.get(cell.studentId,store.UNMARKED)
                        if was!=((cell.expected,cell.expectedLate) if cell.expected is not None else store.UNMARKED):stale.append(f"{current['subject']} {day:%d.%m}");continue
                        # Nothing to do when the family already sees this mark.
                        if was==target:continue
                        edits[cell.studentId]=target;told+=was[0]!='unmarked'
                    plan.append((key,current,existing,resolved,edits,told))
                if stale:raise HTTPException(409,'Отметки изменились: '+', '.join(list(dict.fromkeys(stale))[:5])+'. Обновите сетку.')
                created=now().isoformat();done=[];changed=0;recipients={}
                for key,current,existing,resolved,edits,told in plan:
                    changed+=len(edits)
                    if not edits:continue
                    updated=self.write_lesson(db,workspace,state,user,key,existing,resolved,edits,created)
                    done.append(dict(lessonKey=key,version=updated['version'],publishedVersion=updated['publishedVersion']))
                    if told:
                        # The lesson's teachers learn that a mark they published was replaced (no pupil names); the actor needs no notice.
                        ids=set(current['teacherIds'])|set(existing['published']['lesson']['teacherIds'] if existing and existing['published'] else [])
                        owners=[u for users in self.coverage.reminders.holders(db,workspace,ids).values() for u in users if u['id']!=user['id']]
                        for owner in {u['id']:u for u in owners}.values():
                            self.journal.notifications.deliver(db,[owner],workspace,updated['version'],'journal.attendance_corrected',topic=f'attendance-corrected:{workspace}:{key}',
                                target=dict(title=f"{current['subject']}, {Day.fromisoformat(current['date']):%d.%m}: {user['name']} исправил(а) отметки посещаемости: {told}"))
                skipped=len(body.cells)-changed
                self.access.record(db,'attendance.grid_marked',workspace,state.revision,detail=encoded(dict(classId=body.classId,requestId=body.requestId,lessons=len(done),cells=changed,attendance=body.attendance,reason=body.reason)))
                return self.remember(db,workspace,user,body,fingerprint,dict(status='applied',changed=changed,skipped=skipped,lessons=done))

        @app.post('/api/journal/attendance/grid/days')
        def decide(body:DayDecision,request:Request,workspace:Workspace='school'):
            user,snap=self.reports.context(request,workspace);state=snap.state;today,_=self.clock()
            chosen=sorted(body.days,key=lambda d:d.date);first,last=chosen[0].date,chosen[-1].date
            if first<state.settings.yearStart or last>state.settings.yearEnd:raise HTTPException(422,'Дата вне учебного года.')
            with self.access.read_connect() as db:
                db.execute('BEGIN');self.absences.recheck(db,user,snap,workspace);self.level(db,user,snap,workspace,body.classId,first,last,edit=True)
            lessons=self.class_lessons(snap,{body.classId},[d.date for d in chosen if d.date<=today])
            with self.access.lock,self.access.connect() as db:
                self.journal.tables(db);absence_tables(db);self.begin(db)
                self.absences.recheck(db,user,snap,workspace)
                self.level(db,user,snap,workspace,body.classId,first,last,edit=True)
                fingerprint,stored=self.receipt(db,workspace,user,body,'Запрос уже использован для других дней.')
                if stored is not None:return stored
                built,private=self.build(db,user,snap,workspace,body.classId,first,last,'edit',lessons) if lessons else (dict(days=[]),dict(active={},day_pupils={}))
                shown={d['date']:d for d in built['days']}
                states=store.day_states(db,workspace,{body.classId},first,last).get(body.classId,{})
                confirm=body.action=='confirm';plan=[];stamp=now().isoformat()
                # All days are checked before the first is written; the error names the day.
                for choice in chosen:
                    iso=choice.date.isoformat();refuse=lambda code,text:HTTPException(code,f'{ru(choice.date)}: {text}')
                    if choice.date>today:raise refuse(422,'день ещё не наступил.')
                    if (stops:=range_locks(db,workspace,iso,iso)):raise refuse(403,'период закрыт ('+', '.join(p['name'] for p in stops)+'). Администратор должен сначала открыть период с указанием причины.')
                    current=states.get(iso)
                    if (current['version'] if current else 0)!=choice.version:raise refuse(409,'состояние дня изменилось. Обновите сетку.')
                    day=shown.get(iso);open_=unmarked=0
                    if confirm:
                        if current and current['status']=='confirmed':continue
                        if not private['active'].get(iso):raise refuse(422,'в этот день у класса нет уроков.')
                        if not day['confirmation']['expected']:raise refuse(409,'уроки этого дня ещё не закончились.')
                        open_,unmarked=day['openLessons'],day['counts']['unmarked']+day['counts']['noRecord']
                        if (open_ or unmarked) and not body.acknowledge:raise refuse(409,f'не заполнено уроков: {open_}, без отметки: {unmarked}. Подтвердите ещё раз, если проверили.')
                    elif not current or current['status'] not in ('confirmed','changed'):raise refuse(409,'день не подтверждён, снимать нечего.')
                    plan.append((iso,(current['version'] if current else 0)+1,open_,unmarked))
                comment=body.comment.strip()
                for iso,version,open_,unmarked in plan:
                    status='confirmed' if confirm else 'revoked'
                    pupils=encoded(sorted(private['day_pupils'].get(iso,())))
                    db.execute('''INSERT INTO attendance_day_confirmations VALUES (?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(workspace,class_id,day) DO UPDATE SET
                        version=excluded.version,status=excluded.status,updated=excluded.updated,actor_id=excluded.actor_id,actor_name=excluded.actor_name,pupils=excluded.pupils,open_lessons=excluded.open_lessons,unmarked=excluded.unmarked''',
                        (workspace,body.classId,iso,version,status,stamp,user['id'],user['name'],pupils,open_,unmarked))
                    note=comment+(' ' if comment else '')+f'Подтверждено при незаполненном: уроков {open_}, без отметки {unmarked}.' if confirm and (open_ or unmarked) else comment
                    db.execute('INSERT INTO attendance_day_history(workspace,class_id,day,version,created,actor_id,actor_name,action,comment,request_id) VALUES (?,?,?,?,?,?,?,?,?,?)',
                        (workspace,body.classId,iso,version,stamp,user['id'],user['name'],status,note,body.requestId))
                self.access.record(db,'attendance.days_confirmed' if confirm else 'attendance.days_revoked',workspace,state.revision,detail=encoded(dict(classId=body.classId,days=[p[0] for p in plan],comment=comment)))
                after=store.day_states(db,workspace,{body.classId},first,last).get(body.classId,{})
                return self.remember(db,workspace,user,body,fingerprint,dict(status='applied',days=[dict(date=c.date.isoformat(),**{k:after[c.date.isoformat()][k] for k in ('status','version','updated','actor')}) for c in chosen if c.date.isoformat() in after]))

        @app.get('/api/journal/attendance/grid/days/history')
        def days_history(request:Request,classId:str=Query(min_length=1,max_length=100),day:Day=Query(),workspace:Workspace='school'):
            user,snap=self.reports.context(request,workspace);self.period(snap,day,day)
            with self.access.read_connect() as db:
                db.execute('BEGIN');self.absences.recheck(db,user,snap,workspace);self.level(db,user,snap,workspace,classId,day,day)
                rows=db.execute('SELECT version,created,actor_name,action,comment FROM attendance_day_history WHERE workspace=? AND class_id=? AND day=? ORDER BY id DESC LIMIT 50',(workspace,classId,day.isoformat())).fetchall()
            return dict(items=[dict(version=r[0],created=r[1],actor=r[2],action=r[3],comment=r[4]) for r in rows])

    def report_block(self,db,user,snap,workspace,body):
        """Uncovered lessons and the daily confirmations for the classes of an attendance report; school-wide and per-pupil reports stay unmeasured."""
        none=dict(missingJournals='not_measured',dayConfirmation=None)
        classes=[body.classId] if body.classId else [] if user['role']=='admin' else sorted(self.reports.scope(db,user,snap,workspace,body)[0])
        if body.historicalOnly or body.studentId or not classes or len(classes)>MEASURED_CLASSES:return none
        year=snap.state.settings;today,at=self.clock()
        first,last=max(body.start,year.yearStart),min(body.end,year.yearEnd,today)
        rows=[];ended={}
        if first<=last:
            wanted=set(classes)
            for day,(found,_) in self.class_lessons(snap,wanted,dates(first,last)).items():
                for row in found:
                    if row['status']=='cancelled':continue
                    due=day<today or day==today and row['end']<=at
                    rows.append(dict(date=day.isoformat(),due=due,classIds=sorted(wanted&set(row['classIds'])),status=row['status']))
                    if due and (not body.subjectId or row['subjectId']==body.subjectId):ended[key_for(row)]=True
        published={r[0] for r in db.execute('SELECT l.lesson_key FROM json_each(?) x CROSS JOIN journal_attendance_lessons l ON l.workspace=? AND l.lesson_key=x.value',(encoded(sorted(ended)),workspace))} if ended else set()
        expected=store.expected_days(rows);states=store.day_states(db,workspace,set(classes),first,last) if first<=last else {}
        begin=store.starts(db,workspace,reminder_settings(db,workspace)['confirmSince']);names={c.id:c.name for c in snap.state.classes}
        return dict(missingJournals=len(set(ended)-published),dayConfirmation=dict(classes=[dict(classId=id,className=names[id],**store.summarize(expected.get(id,set()),states.get(id,{}),begin.get(id,begin['default']))) for id in classes]))
