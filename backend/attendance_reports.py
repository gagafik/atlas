"""Class-scoped attendance summaries; never expose grades or private reasons."""
from datetime import date
from collections import OrderedDict
from copy import deepcopy
import csv
import hashlib
import io
from threading import RLock
import time
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import HTTPException, Request, Response
from pydantic import Field, model_validator
from .access import now
from . import absence_codes
from .models import Model
from .notifications import encoded, permission_key

STATES=['unmarked','present','absent','late','excused']
LABELS=dict(unmarked='Не отмечено',present='Присутствует',absent='Отсутствует',late='Опоздание',excused='Уважительная причина')
PAGE=50
CACHE_SECONDS=600
CACHE_ENTRIES=16
STALE='Данные отчёта изменились. Обновите сводку.'


class Period(Model):
    start: date
    end: date
    classId: str = Field(default='',max_length=100)
    studentId: str = Field(default='',max_length=100)
    subjectId: str = Field(default='',max_length=100)
    historicalOnly: bool = False
    offset: int = Field(default=0,ge=0,le=10000000)
    token: str = Field(default='',max_length=64)

    @model_validator(mode='after')
    def period(self):
        if not 0<=(self.end-self.start).days<=365:raise ValueError('Выберите последовательный период до 366 дней.')
        if self.historicalOnly and self.classId:raise ValueError('Выберите класс или учеников вне текущего состава.')
        return self


class Details(Period):
    studentId: str = Field(min_length=1,max_length=100)
    attendance: Literal['all','unmarked','present','absent','late','excused'] = 'all'


class Export(Period):
    view: Literal['students','classes','subjects','lessons'] = 'students'
    attendance: Literal['all','unmarked','present','absent','late','excused'] = 'all'


def empty():return dict(records=0,attendance={s:0 for s in STATES},lateMinutes=0,excusedCodes={})


def add(target,source):
    target['records']+=source['records'];target['lateMinutes']+=source['lateMinutes']
    for status in STATES:target['attendance'][status]+=source['attendance'][status]
    for code,count in source['excusedCodes'].items():target['excusedCodes'][code]=target['excusedCodes'].get(code,0)+count


def csv_response(rows):
    def safe(value):
        text=str(value if value is not None else '')
        return "'"+text if text[:1] in ['\t','\r','\n'] or text.lstrip().startswith(('=','+','-','@')) else text
    stream=io.StringIO(newline='');writer=csv.writer(stream,delimiter=';')
    writer.writerows([[safe(value) for value in row] for row in rows])
    return Response(('\ufeff'+stream.getvalue()).encode('utf-8'),media_type='text/csv; charset=utf-8',headers={'Content-Disposition':'attachment; filename="attendance.csv"','Cache-Control':'no-store','X-Content-Type-Options':'nosniff'})


class AttendanceReports:
    def __init__(self,absences):
        self.absences,self.access,self.journal=absences,absences.access,absences.journal
        self.cache=OrderedDict();self.cache_lock=RLock()

    def context(self,request,workspace):
        user,snap=self.absences.context(request,workspace)
        if user['role'] not in ['admin','teacher','staff']:raise HTTPException(403,'Сводная посещаемость доступна ответственным сотрудникам.')
        from .journal import user_ids
        if user['role'] in ['admin','staff'] and workspace not in user['workspaces'] or user['role']=='teacher' and not user_ids(user,workspace,'teacher'):
            raise HTTPException(403,'Нет доступа к журналам этого пространства.')
        from .absences import tables
        with self.access.lock,self.access.connect() as db:self.journal.tables(db);tables(db)
        return user,snap

    def access_key(self,db,user,snap,workspace):
        """Account, permissions and (except for administrators) the class/pupil scope; independent of the filters of one report."""
        self.journal.recheck_account(db,user)
        scope=[]
        if user['role']!='admin':
            # An administrator's scope is the whole school by definition: roster changes are covered by the data half.
            allowed=self.absences.classes(db,user,snap,workspace,'attendance_read');classes={c.id for c in snap.state.classes if c.id in allowed}
            scope=[sorted(classes),sorted((s.id,s.classId) for s in snap.state.students if s.classId in classes)]
        return hashlib.sha256(encoded([workspace,user['id'],permission_key(user),*scope]).encode()).hexdigest()[:32]

    def data_key(self,db,user,snap,workspace,body,classes,pupils):
        """Publications and approved notices of the selected pupils and days, plus the names shown."""
        ids=[body.studentId] if body.studentId else sorted(pupils) if (user['role']!='admin' or body.classId) and not body.historicalOnly else None
        start,end=body.start.isoformat(),body.end.isoformat()
        if ids is None:
            lessons=db.execute("SELECT count(*),coalesce(sum(version),0),coalesce(max(published_at),'') FROM journal_attendance_lessons WHERE workspace=? AND day BETWEEN ? AND ?",(workspace,start,end)).fetchone()
            notices=db.execute("SELECT count(*),coalesce(sum(version),0),coalesce(max(updated),'') FROM absence_notices WHERE workspace=? AND status='approved' AND substr(start_at,1,10)<=? AND substr(end_at,1,10)>=?",(workspace,end,start)).fetchone()
        else:
            lessons=db.execute("""SELECT count(*),coalesce(sum(l.version),0),coalesce(max(l.published_at),'') FROM journal_attendance a INDEXED BY attendance_student
                JOIN journal_attendance_lessons l ON l.workspace=a.workspace AND l.lesson_key=a.lesson_key
                WHERE a.workspace=? AND a.student_id IN (SELECT value FROM json_each(?)) AND a.day BETWEEN ? AND ?""",(workspace,encoded(ids),start,end)).fetchone()
            notices=db.execute("SELECT count(*),coalesce(sum(version),0),coalesce(max(updated),'') FROM absence_notices WHERE workspace=? AND student_id IN (SELECT value FROM json_each(?)) AND status='approved' AND substr(start_at,1,10)<=? AND substr(end_at,1,10)>=?",(workspace,encoded(ids),end,start)).fetchone()
        names=[sorted((id,s.name,s.classId,s.active) for id,s in pupils.items()),sorted(classes.items()),sorted((s.id,s.name) for s in snap.state.subjects)]
        return hashlib.sha256(encoded([tuple(lessons),tuple(notices),names]).encode()).hexdigest()[:32]

    def token(self,db,user,snap,workspace,body):
        """64 characters: access half + data half. A sent token must keep the same access half."""
        key=self.access_key(db,user,snap,workspace)
        if body.token and body.token[:32]!=key:raise HTTPException(409,STALE)
        classes,pupils=self.scope(db,user,snap,workspace,body)
        return key+self.data_key(db,user,snap,workspace,body,classes,pupils)

    def scope(self,db,user,snap,workspace,body):
        allowed=self.absences.classes(db,user,snap,workspace,'attendance_read')
        if user['role']!='admin' and not allowed:raise HTTPException(403,'Для этого отчёта нужно назначение ответственным за класс.')
        classes={c.id:c.name for c in snap.state.classes if user['role']=='admin' or c.id in allowed}
        if body.classId and body.classId not in classes:raise HTTPException(404,'Класс не найден или недоступен.')
        if body.historicalOnly and user['role']!='admin':raise HTTPException(403,'Архив вне текущего состава доступен администратору.')
        pupils={s.id:s for s in snap.state.students if s.classId in classes and (not body.classId or s.classId==body.classId) and not body.historicalOnly}
        if body.studentId:
            current=snap.entities['student'].get(body.studentId)
            if current and body.studentId not in pupils or not current and (user['role']!='admin' or body.classId):raise HTTPException(404,'Ученик не найден или недоступен.')
            pupils={id:s for id,s in pupils.items() if id==body.studentId}
        return classes,pupils

    def query(self,user,snap,workspace,body,pupils,*,effective=False,notice=False):
        where=['a.workspace=?','a.day BETWEEN ? AND ?'];args=[workspace,body.start.isoformat(),body.end.isoformat()]
        if body.historicalOnly:
            where.append('a.student_id NOT IN (SELECT value FROM json_each(?))');args.append(encoded(sorted(snap.entities['student'])))
        elif user['role']!='admin' or body.classId:
            where.append('a.student_id IN (SELECT value FROM json_each(?))');args.append(encoded(sorted(pupils)))
        if body.studentId:where.append('a.student_id=?');args.append(body.studentId)
        if body.subjectId:where.append('a.subject_id=?');args.append(body.subjectId)
        # Annual totals scan the clustered facts sequentially; a class/pupil
        # starts at its own index. No per-pupil join to full lesson descriptions.
        index='INDEXED BY attendance_student' if notice or effective else 'INDEXED BY attendance_day' if user['role']=='admin' and not body.classId and not body.studentId and (body.end-body.start).days<45 else 'INDEXED BY attendance_summary'
        # A note limited to one subject excuses only the lessons of that subject; code '' is a note without a type.
        covers='''FROM absence_notices n WHERE n.workspace=a.workspace AND n.student_id=a.student_id
                    AND n.status='approved' AND n.start_at<=a.day||'T'||a.start AND n.end_at>=a.day||'T'||a.end
                    AND (n.subject_id IS NULL OR n.subject_id=a.subject_id)'''
        extra=f''',CASE WHEN a.attendance='absent' AND EXISTS(SELECT 1 {covers}) THEN 'excused' ELSE a.attendance END AS effective,
                CASE WHEN a.attendance='absent' THEN (SELECT coalesce(n.code_id,'') {covers} ORDER BY 1 LIMIT 1) END AS notice_code''' if effective else ''
        return 'WITH visible AS (SELECT a.*,a.attendance AS raw'+extra+' FROM journal_attendance a '+index+' WHERE '+' AND '.join(where)+') ',args

    def summary(self,db,user,snap,workspace,body):
        # Cache only a fully scoped result. A sent token is served from its snapshot while it
        # exists (token() verified the live access half); otherwise the live token must be equal.
        live=self.token(db,user,snap,workspace,body)
        path=next(r[2] for r in db.execute('PRAGMA database_list') if r[1]=='main')
        filters=encoded(body.model_dump(mode='json',include={'start','end','classId','studentId','subjectId','historicalOnly'}))
        stamp=time.monotonic()
        with self.cache_lock:
            for expired in [k for k,(created,_) in self.cache.items() if stamp-created>=CACHE_SECONDS]:self.cache.pop(expired)
            for candidate in dict.fromkeys([body.token,live]):
                if candidate and (path,candidate,filters) in self.cache:
                    self.cache.move_to_end((path,candidate,filters))
                    return deepcopy(self.cache[(path,candidate,filters)][1])|dict(token=candidate)
        if body.token and body.token!=live:raise HTTPException(409,STALE)
        value=self.calculate(db,user,snap,workspace,body)|dict(generatedAt=now().isoformat())
        if value['studentCount']<=5000:
            with self.cache_lock:
                self.cache[(path,live,filters)]=(time.monotonic(),deepcopy(value));self.cache.move_to_end((path,live,filters))
                while len(self.cache)>CACHE_ENTRIES:self.cache.popitem(last=False)
        return value|dict(token=live)

    def calculate(self,db,user,snap,workspace,body):
        classes,pupils=self.scope(db,user,snap,workspace,body)
        query,args=self.query(user,snap,workspace,body,pupils)
        rows=db.execute(query+'''SELECT student_id,subject_id,raw,count(*),sum(late_minutes),max(day||start||lesson_key)
            FROM visible GROUP BY student_id,subject_id,raw''',args).fetchall()
        students={id:dict(id=id,name=s.name,classId=s.classId,className=classes[s.classId],active=s.active,**empty()) for id,s in pupils.items()}
        subjects={};latest_students={};latest_subjects={}
        for student,subject,status,count,minutes,last in rows:
            value=students.setdefault(student,dict(id=student,name='Ученик вне текущего состава',classId=None,className='Вне текущего состава',active=None,**empty()))
            subject_value=subjects.setdefault(subject,dict(id=subject,name='',**empty()))
            for item in [value,subject_value]:
                item['records']+=count;item['attendance'][status]+=count;item['lateMinutes']+=minutes
            latest_students[student]=max(latest_students.get(student,''),last)
            latest_subjects[subject]=max(latest_subjects.get(subject,''),last)
        adjusted,adjust_args=self.query(user,snap,workspace,body,pupils,notice=True)
        # Start with approved notices (usually a much smaller set), not an
        # EXISTS lookup for every status counter of every annual pupil record.
        # One row per excused lesson first, so overlapping notes never count a lesson twice; then the lessons per type.
        corrections=db.execute(adjusted+'''SELECT student_id,subject_id,code,count(*) FROM (
            SELECT v.student_id,v.subject_id,v.lesson_key,min(coalesce(n.code_id,'')) AS code
            FROM absence_notices n CROSS JOIN visible v ON v.workspace=n.workspace AND v.student_id=n.student_id
            AND v.day BETWEEN substr(n.start_at,1,10) AND substr(n.end_at,1,10)
            WHERE n.workspace=? AND n.status='approved' AND v.raw='absent'
            AND n.start_at<=v.day||'T'||v.start AND n.end_at>=v.day||'T'||v.end
            AND (n.subject_id IS NULL OR n.subject_id=v.subject_id)
            GROUP BY v.student_id,v.subject_id,v.lesson_key) GROUP BY 1,2,3''',adjust_args+[workspace]).fetchall()
        for student,subject,code,count in corrections:
            for item in [students[student],subjects[subject]]:
                item['attendance']['absent']-=count;item['attendance']['excused']+=count
                item['excusedCodes'][code]=item['excusedCodes'].get(code,0)+count
        # Unknown IDs are retained in an explicit archive bucket, never silently
        # attributed to a current class. Names come from the last saved lesson.
        missing=[dict(id=id,key=last[15:]) for id,last in latest_students.items() if id not in pupils]
        # Keep the small list of selected keys outermost. Without this order,
        # SQLite may scan every published lesson to resolve eight old names.
        old_names={r[0]:r[1] for r in db.execute('''SELECT m.student_id,m.name FROM json_each(?) x CROSS JOIN journal_members m
            ON m.workspace=? AND m.kind='published' AND m.student_id=json_extract(x.value,'$.id')
            AND m.lesson_key=json_extract(x.value,'$.key')''',(encoded(missing),workspace))} if missing else {}
        for id,name in old_names.items():students[id]['name']=name
        totals=empty();class_rows={id:dict(id=id,name=name,students=0,withoutRecords=0,**empty()) for id,name in classes.items()
            if not body.historicalOnly and not body.studentId and (not body.classId or body.classId==id)}
        for student in students.values():
            value=class_rows.setdefault(student['classId'],dict(id=student['classId'],name=student['className'],students=0,withoutRecords=0,**empty()))
            value['students']+=1;value['withoutRecords']+=student['records']==0
            add(value,student);add(totals,student)
        subject_names={s.id:s.name for s in snap.state.subjects}
        historical_subjects=[dict(id=id,key=last[15:]) for id,last in latest_subjects.items() if id not in subject_names]
        for id,name in db.execute('''SELECT l.subject_id,l.subject FROM json_each(?) x CROSS JOIN journal_attendance_lessons l
            ON l.workspace=? AND l.lesson_key=json_extract(x.value,'$.key')''',(encoded(historical_subjects),workspace)):subject_names[id]=name
        for id,value in subjects.items():value['name']=subject_names.get(id,'Предмет прежнего журнала')
        ordered=sorted(students.values(),key=lambda s:(-s['attendance']['absent'],-s['attendance']['late'],s['name'].casefold(),s['id']))
        return dict(totals=totals,classes=sorted(class_rows.values(),key=lambda c:c['name'].casefold()),subjects=sorted(subjects.values(),key=lambda s:s['name'].casefold()),students=ordered,studentCount=len(ordered),withoutRecords=sum(s['records']==0 for s in ordered))

    def details(self,db,user,snap,workspace,body,*,export=False):
        if not body.studentId:raise HTTPException(422,'Выберите ученика для списка уроков.')
        _,pupils=self.scope(db,user,snap,workspace,body)
        query,args=self.query(user,snap,workspace,body,pupils,effective=True)
        where=" WHERE effective=?" if body.attendance!='all' else ''
        base_args=list(args)
        if where:args.append(body.attendance)
        limit=10001 if export else PAGE+1;offset=0 if export else body.offset
        rows=db.execute(query+'''SELECT v.lesson_key,v.day,v.start,v.end,v.subject_id,l.subject,l.group_name,l.teachers,
            v.raw,v.effective,v.late_minutes,l.version,l.published_at,v.notice_code FROM visible v JOIN journal_attendance_lessons l
            ON l.workspace=v.workspace AND l.lesson_key=v.lesson_key'''+where+' ORDER BY v.day,v.start,v.lesson_key LIMIT ? OFFSET ?',args+[limit,offset]).fetchall()
        if export and len(rows)>10000:raise HTTPException(422,'Более 10 000 записей: сократите период выгрузки уроков.')
        keys=['lessonKey','date','start','end','subjectId','subject','group','teachers','rawAttendance','attendance','lateMinutes','version','publishedAt','absenceCode']
        items=[dict(zip(keys,r)) for r in rows[:limit if export else PAGE]]
        for item in items:
            item['excusedByNotice']=item['rawAttendance']=='absent' and item['attendance']=='excused'
            if not item['excusedByNotice']:item['absenceCode']=None
        pupil=snap.entities['student'].get(body.studentId)
        name=pupil.name if pupil else db.execute(query+'''SELECT m.name FROM visible v JOIN journal_members m
            ON m.workspace=v.workspace AND m.lesson_key=v.lesson_key AND m.student_id=v.student_id AND m.kind='published'
            ORDER BY v.day DESC,v.start DESC,v.lesson_key DESC LIMIT 1''',base_args).fetchone()
        if not pupil and not name:raise HTTPException(404,'Ученик не найден в опубликованных журналах этого периода.')
        return dict(student=dict(id=body.studentId,name=name if isinstance(name,str) else name[0] if name else 'Ученик вне текущего состава'),items=items,nextOffset=offset+PAGE if not export and len(rows)>PAGE else None)

    def finish(self,user,snap,workspace,token,body,action):
        # A report reads one WAL snapshot. Only access is revalidated before releasing the response:
        # a concurrent grant or class change requires a refresh, a publication elsewhere does not.
        with self.access.lock,self.access.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if self.access_key(db,user,snap,workspace)!=token[:32]:raise HTTPException(409,STALE)
            self.scope(db,user,snap,workspace,body)
            self.access.record(db,action,workspace,detail=encoded(body.model_dump(mode='json',exclude={'token','offset'})))

    def register(self,app,Workspace):
        @app.get('/api/journal/attendance/options')
        def options(request:Request,workspace:Workspace='school'):
            user,snap=self.context(request,workspace)
            with self.access.read_connect() as db:
                db.execute('BEGIN');self.absences.recheck(db,user,snap,workspace)
                allowed=self.absences.classes(db,user,snap,workspace,'attendance_read')
                classes=[dict(id=c.id,name=c.name) for c in snap.state.classes if user['role']=='admin' or c.id in allowed]
                ids={c['id'] for c in classes}
                today=now().astimezone(ZoneInfo('Asia/Qyzylorda')).date().isoformat()
                period=db.execute("SELECT name,start_day,end_day FROM journal_periods WHERE workspace=? AND start_day<=? AND end_day>=? ORDER BY julianday(end_day)-julianday(start_day),start_day DESC,id LIMIT 1",(workspace,today,today)).fetchone()
                return dict(available=user['role']=='admin' or bool(allowed),admin=user['role']=='admin',classes=classes,
                    students=[dict(id=s.id,name=s.name,classId=s.classId,active=s.active) for s in snap.state.students if s.classId in ids],
                    subjects=[dict(id=s.id,name=s.name) for s in snap.state.subjects],yearStart=snap.state.settings.yearStart,yearEnd=snap.state.settings.yearEnd,
                    today=today,period=dict(name=period[0],start=period[1],end=period[2]) if period else None)

        def read(request,workspace,body,detail=False):
            user,snap=self.context(request,workspace)
            with self.access.read_connect() as db:
                db.execute('BEGIN');self.absences.recheck(db,user,snap,workspace)
                if detail:
                    # Drill-downs are live and scope-checked; only the access half of a sent token matters.
                    token=self.token(db,user,snap,workspace,body)
                    value=self.details(db,user,snap,workspace,body)|dict(token=body.token or token,generatedAt=now().isoformat())
                else:value=self.summary(db,user,snap,workspace,body)
                # Type names are live and outside the cached summary and its token: a rename shows at once.
                value['codes']=absence_codes.current(db,workspace)['items']
            self.finish(user,snap,workspace,value['token'],body,'attendance.details_read' if detail else 'attendance.summary_read')
            if not detail:
                all_students=value['students'];value['students']=all_students[body.offset:body.offset+PAGE]
                value['nextOffset']=body.offset+PAGE if len(all_students)>body.offset+PAGE else None
            return value|dict(source='published',membership='current',missingJournals='not_measured')

        @app.post('/api/journal/attendance/report')
        def report(body:Period,request:Request,workspace:Workspace='school'):return read(request,workspace,body)

        @app.post('/api/journal/attendance/details')
        def details(body:Details,request:Request,workspace:Workspace='school'):return read(request,workspace,body,True)

        @app.post('/api/journal/attendance/export')
        def export(body:Export,request:Request,workspace:Workspace='school'):
            user,snap=self.context(request,workspace)
            with self.access.read_connect() as db:
                db.execute('BEGIN');self.absences.recheck(db,user,snap,workspace)
                header=['Источник','Начало периода','Конец периода','Состав классов']
                prefix=['Опубликованные журналы',body.start.isoformat(),body.end.isoformat(),'Текущий состав; пропущенные журналы не подсчитаны']
                if body.view=='lessons':
                    token=self.token(db,user,snap,workspace,body);used=body.token or token
                    value=self.details(db,user,snap,workspace,body,export=True)
                    rows=[header+['Ученик','ID ученика','Дата','Начало','Конец','Предмет','Группа','Учителя','Посещаемость','Исходная отметка','Минут опоздания','Принятая объяснительная','Редакция','Опубликовано']]
                    codes=absence_codes.names(db,workspace)
                    rows[0].append('Тип отсутствия')
                    rows += [prefix+[value['student']['name'],body.studentId,r['date'],r['start'],r['end'],r['subject'],r['group'],r['teachers'],LABELS[r['attendance']],LABELS[r['rawAttendance']],r['lateMinutes'],'Да' if r['excusedByNotice'] else '',r['version'],r['publishedAt'],
                        '' if not r['excusedByNotice'] else codes.get(r['absenceCode'],r['absenceCode']) if r['absenceCode'] else 'Без типа'] for r in value['items']]
                else:
                    value=self.summary(db,user,snap,workspace,body);used=value['token'];codes=absence_codes.current(db,workspace)['items']
                    # Appended after the existing columns: one per type of the live list, then notes without a type.
                    rows=[header+(['Класс','Ученики','Без опубликованных записей'] if body.view=='classes' else ['Предмет','ID предмета'] if body.view=='subjects' else ['Ученик','ID ученика','Класс','Статус ученика'])+['Записей']+[LABELS[s] for s in STATES]+['Минут опоздания']+['Уважительная причина: '+c['name'] for c in codes]+['Уважительная причина: без типа']]
                    for r in value[body.view]:
                        identity=[r['name'],r['students'],r['withoutRecords']] if body.view=='classes' else [r['name'],r['id']] if body.view=='subjects' else [r['name'],r['id'],r['className'],'Активен' if r['active'] else 'Неактивен' if r['active'] is False else 'Вне текущего состава']
                        rows.append(prefix+identity+[r['records']]+[r['attendance'][s] for s in STATES]+[r['lateMinutes']]+[r['excusedCodes'].get(c['id'],0) for c in codes]+[r['excusedCodes'].get('',0)])
            response=csv_response(rows);self.finish(user,snap,workspace,used,body,'attendance.exported')
            return response
