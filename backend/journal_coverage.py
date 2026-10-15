"""Expected lessons compared with publications, scoped before exposing any pupil."""
from collections import OrderedDict
from copy import deepcopy
from datetime import date,datetime,timedelta
import hashlib,json,time
from threading import RLock
from typing import Literal
from zoneinfo import ZoneInfo
from fastapi import HTTPException,Request
from pydantic import Field,model_validator
from .models import Model
from .access import now
from .calendar_ops import DayContext,dates,resolve_day
from .journal import key_for,staff_read,user_ids
from .notifications import encoded
from .resources import teacher_ids
from .attendance_reports import csv_response

LABELS=dict(missing='Журнал не создан',draft='Нет публикации',incomplete='Заполнен не полностью',stale='Расписание или состав изменились',complete='Заполнен',upcoming='Урок ещё не закончился',orphaned='Публикация вне текущего расписания')
PAGE=50


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

    def token(self,db,user,snap,workspace):
        base=self.reports.token(db,user,snap,workspace)
        versions=db.execute('SELECT count(*),coalesce(sum(version),0) FROM journal_entries WHERE workspace=?',(workspace,)).fetchone()
        return hashlib.sha256(encoded([base,versions]).encode()).hexdigest()

    def scope(self,db,user,snap,workspace,body):
        own=user_ids(user,workspace,'teacher');state=snap.state
        if user['role']=='admin':classes={c.id for c in state.classes}
        elif body.mode=='classes':
            classes=self.absences.classes(db,user,snap,workspace,'journal_coverage')&set(snap.entities['class_'])
            if not classes:raise HTTPException(403,'Нужно назначение ответственным за класс.')
        else:
            if not own:raise HTTPException(403,'Нет назначения преподавателем.')
            groups={w.groupId for w in state.workloads if own&set(teacher_ids(w))}
            groups|={d.groupId for d in state.datedLessons if d.groupId and own&set(d.teacherIds or [])}
            placements={p.id:p for p in state.placements};loads={w.id:w for w in state.workloads}
            groups|={loads[placements[d.placementId].workloadId].groupId for d in state.datedLessons if d.placementId in placements and own&set(d.teacherIds or [])}
            classes={c for g in state.groups if g.id in groups for c in g.classIds}
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
        cutoff=body.asOf.astimezone(ZoneInfo('Asia/Qyzylorda'));ctx=DayContext(state)
        people={s.id:s for s in state.students};class_names={c.id:c.name for c in state.classes}
        # Resolve only potentially visible lessons. Full entity maps and all
        # relevant overrides remain available, including substitute teachers.
        groups={g.id for g in state.groups if classes&set(g.classIds) or any(people[id].classId in classes for id in g.studentIds)}
        teacher_filter=({body.teacherId} if body.teacherId else own) if user['role']!='admin' and body.mode=='teaching' or body.teacherId else None
        replacement_ids={d.placementId for d in state.datedLessons if teacher_filter and teacher_filter&set(d.teacherIds or [])}
        placements=[p for p in state.placements if ctx.maps['workloads'][p.workloadId].groupId in groups and (not teacher_filter or teacher_filter&set(teacher_ids(ctx.maps['workloads'][p.workloadId])) or p.id in replacement_ids)]
        placement_ids={p.id for p in placements}
        changes=[d for d in state.datedLessons if d.placementId in placement_ids or not d.placementId and d.groupId in groups and (not teacher_filter or teacher_filter&set(d.teacherIds or []))]
        calendar=state.model_copy(update=dict(placements=placements,datedLessons=changes,commitments=[]))
        rows=[];orphans={};expected=set();cancelled=0;detail_value=None
        period=[detail.day] if detail else dates(body.start,body.end)
        for day in period:
            resolved,_=resolve_day(calendar,day,ctx)
            chosen=[]
            for row in resolved:
                if row['activity']!='lesson' or ctx.maps['groups'][row['groupId']].kind=='staff':continue
                if row['status']=='cancelled':
                    if self.selected(row|dict(classIds=row['originalClassIds']),user,body,classes,own):cancelled+=1
                    continue
                if self.selected(row,user,body,classes,own):chosen.append(row)
            entries,marks=self.publications(db,user,workspace,day.isoformat(),[key_for(r) for r in chosen],body,classes,own,people)
            for row in chosen:
                key=key_for(row);expected.add(key);saved=entries.get(key);published=saved['lesson'] if saved else None
                ids={id for id in row['studentIds'] if people[id].classId in classes}
                roster_unknown=not row['rosterComplete']
                group=ctx.maps['groups'][row['groupId']]
                if group.kind=='whole':
                    roster_unknown |= any(ctx.maps['classes'][c].studentCount is None or sum(people[id].classId==c for id in row['studentIds'])<(ctx.maps['classes'][c].studentCount or 0) for c in classes&set(row['classIds']))
                if row['audienceSize'] is not None and row['audienceSize']>len(row['studentIds']):roster_unknown=True
                actual=marks.get(key,{}) if published else {}
                missing=ids-set(actual);unmarked={id for id in ids if actual.get(id)=='unmarked'}
                old_ids={s['id'] for s in published['students'] if s.get('enrolled',True) and (people[s['id']].classId if s['id'] in people else s['classId']) in classes} if published else set()
                stale=bool(published and (any(published[k]!=row[k] for k in ['date','start','end','subjectId','groupId','teachers','rooms']) or set(published['teacherIds'])!=set(row['teacherIds']) or old_ids!=ids))
                due=day<cutoff.date() or day==cutoff.date() and row['end']<=cutoff.strftime('%H:%M')
                topic=bool(saved and saved['topic'])
                status='upcoming' if not due else 'missing' if not saved else 'draft' if not published else 'stale' if stale else 'incomplete' if missing or unmarked or not topic or roster_unknown else 'complete'
                value=dict(lessonKey=key,date=row['date'],start=row['start'],end=row['end'],subject=row['subject'],group=row['group'],teachers=row['teachers'],classNames=[class_names[c] for c in sorted(classes&set(row['classIds']))],status=status,due=due,
                    pupils=len(ids),missingPupils=len(missing),unmarkedPupils=len(unmarked),rosterUnknown=roster_unknown,topicFilled=topic,needsSync=stale,
                    version=saved['version'] if saved else 0,publishedVersion=saved['publishedVersion'] if saved else None,hasDraftChanges=bool(published and saved['version']!=saved['publishedVersion']),canOpen=staff_read(user,workspace,row))
                rows.append(value)
                if detail and key==detail.lessonKey:
                    pupils=sorted([dict(id=id,name=people[id].name,className=class_names[people[id].classId],issue='missing' if id in missing else 'unmarked') for id in missing|unmarked],key=lambda s:(s['name'].casefold(),s['id']))
                    detail_value=dict(lesson=value,pupils=pupils[detail.pupilOffset:detail.pupilOffset+PAGE],nextOffset=detail.pupilOffset+PAGE if len(pupils)>detail.pupilOffset+PAGE else None,total=len(pupils))
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
                orphans[key]=dict(lessonKey=key,date=lesson['date'],start=lesson['start'],end=lesson['end'],subject=lesson['subject'],group=lesson['group'],teachers=lesson['teachers'],classNames=['Вне текущего состава'] if archived else [class_names[c] for c in sorted(classes&actual_classes)],status='orphaned',due=False,pupils=0,missingPupils=0,unmarkedPupils=0,rosterUnknown=False,topicFilled=saved['topic'],needsSync=True,version=saved['version'],publishedVersion=saved['publishedVersion'],hasDraftChanges=saved['version']!=saved['publishedVersion'],canOpen=user['role']=='admin' or bool(own&set(saved['draftTeachers'])))
        if detail:
            if not detail_value:raise HTTPException(404,'Урок не найден в доступном расписании выбранной даты.')
            return detail_value
        rows += [r for key,r in orphans.items() if key not in expected]
        rows.sort(key=lambda r:(r['date'],r['start'],r['group'],r['lessonKey']))
        counts={status:sum(r['status']==status for r in rows) for status in LABELS}
        counts.update(expected=len(expected),cancelled=cancelled,missingPupils=sum(r['missingPupils'] for r in rows if r['due']),unmarkedPupils=sum(r['unmarkedPupils'] for r in rows if r['due']))
        return dict(counts=counts,rows=rows)

    def finish(self,db_user,snap,workspace,token,body,action):
        with self.access.lock,self.access.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if self.token(db,db_user,snap,workspace)!=token:raise HTTPException(409,'Журнал или доступ изменились. Повторите проверку.')
            self.scope(db,db_user,snap,workspace,body)
            self.access.record(db,action,workspace,detail=encoded(body.model_dump(mode='json',exclude={'token','offset'})))

    def result(self,db,user,snap,workspace,body,token):
        key=(db.execute('PRAGMA database_list').fetchone()[2],token,encoded(body.model_dump(mode='json',exclude={'offset','status','token'})))
        with self.lock:
            stamp=time.monotonic()
            for k in [k for k,v in self.cache.items() if stamp-v[0]>60]:self.cache.pop(k)
            if key in self.cache:self.cache.move_to_end(key);return deepcopy(self.cache[key][1])
        value=self.calculate(db,user,snap,workspace,body)
        if len(value['rows'])<=20000:
            with self.lock:
                self.cache[key]=(time.monotonic(),deepcopy(value))
                while len(self.cache)>4:self.cache.popitem(last=False)
        return value

    def register(self,app,Workspace):
        @app.get('/api/journal/coverage/options')
        def options(request:Request,workspace:Workspace='school'):
            user,snap=self.reports.context(request,workspace)
            with self.access.read_connect() as db:
                db.execute('BEGIN');self.absences.recheck(db,user,snap,workspace)
                modes=['classes'] if user['role']=='admin' else (['teaching'] if user['role']=='teacher' else [])+(['classes'] if self.absences.classes(db,user,snap,workspace,'journal_coverage') else [])
                scopes={mode:self.scope(db,user,snap,workspace,CoverageQuery(start=snap.state.settings.yearStart,end=snap.state.settings.yearStart,mode=mode))[0] for mode in modes}
                own=user_ids(user,workspace,'teacher')
                return dict(modes=modes,classes={mode:[dict(id=c.id,name=c.name) for c in snap.state.classes if c.id in ids] for mode,ids in scopes.items()},teachers={mode:[dict(id=t.id,name=t.name) for t in snap.state.teachers if user['role']=='admin' or mode=='classes' or t.id in own] for mode in modes},yearStart=snap.state.settings.yearStart,yearEnd=snap.state.settings.yearEnd,today=now().astimezone(ZoneInfo('Asia/Qyzylorda')).date(),subjects=[dict(id=s.id,name=s.name) for s in snap.state.subjects])

        def read(body,request,workspace,detail=False,export=False):
            user,snap=self.reports.context(request,workspace)
            if not body.asOf:body=body.model_copy(update={'asOf':now()})
            if detail and not body.start<=body.day<=body.end:raise HTTPException(422,'Дата урока должна входить в проверяемый период.')
            with self.access.read_connect() as db:
                db.execute('BEGIN');token=self.token(db,user,snap,workspace)
                if body.token and body.token!=token:raise HTTPException(409,'Журнал или доступ изменились. Повторите проверку.')
                if detail:value=self.calculate(db,user,snap,workspace,body,detail=body)
                else:
                    value=self.result(db,user,snap,workspace,body,token)
                    rows=[r for r in value['rows'] if body.status=='all' or body.status=='issues' and r['status'] not in ['complete','upcoming'] or r['status']==body.status]
                    if export:
                        if len(rows)>100000:raise HTTPException(422,'Более 100 000 уроков: сократите период выгрузки.')
                        value=csv_response([['Дата','Начало','Конец','Предмет','Группа','Классы','Учителя','Состояние','Ученики в области проверки','Нет записи','Не отмечено','Состав требует проверки','Тема заполнена','Редакция публикации','Проверено на момент']]+[[r['date'],r['start'],r['end'],r['subject'],r['group'],', '.join(r['classNames']),r['teachers'],LABELS[r['status']],r['pupils'],r['missingPupils'],r['unmarkedPupils'],'Да' if r['rosterUnknown'] else 'Нет','Да' if r['topicFilled'] else 'Нет',r['publishedVersion'],body.asOf.isoformat()] for r in rows])
                    else:value=dict(counts=value['counts'],total=len(rows),items=rows[body.offset:body.offset+PAGE],nextOffset=body.offset+PAGE if len(rows)>body.offset+PAGE else None)
            self.finish(user,snap,workspace,token,body,'journal.coverage_exported' if export else 'journal.coverage_read')
            return value if export else value|dict(token=token,asOf=body.asOf.isoformat(),membership='current',source='current_schedule_and_published_journals')

        @app.post('/api/journal/coverage/report')
        def report(body:CoverageQuery,request:Request,workspace:Workspace='school'):return read(body,request,workspace)
        @app.post('/api/journal/coverage/details')
        def details(body:CoverageDetail,request:Request,workspace:Workspace='school'):return read(body,request,workspace,detail=True)
        @app.post('/api/journal/coverage/export')
        def export(body:CoverageQuery,request:Request,workspace:Workspace='school'):return read(body,request,workspace,export=True)
