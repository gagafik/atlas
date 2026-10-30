"""Report cards on top of final grades: band points and the average, the year grade from period finals,
the subject layout, the class report, the class x subject matrix and the whole-sheet CSV.

Everything here is read-only for families: no calculation, percent, explanation or author leaves this module."""
import csv
from fractions import Fraction
import json
import tempfile
from typing import Annotated,Literal
from fastapi import HTTPException,Query,Request
from fastapi.responses import StreamingResponse
from pydantic import Field,model_validator
from .models import Model
from .natural import natural
from .notifications import encoded
from .class_staff import led,classes,eligible

OUTCOMES={'not_assessed':'Не аттестован','exempt':'Освобождён'}
STATES={'draft':'Черновик','submitted':'На утверждении','approved':'Утверждено','published':'Опубликовано'}
SubjectId=Annotated[str,Field(min_length=1,max_length=100)]


class SchoolReport(Model):
    version: int = Field(ge=0)
    phrases: list[Annotated[str,Field(min_length=1,max_length=200)]] = Field(default_factory=list,max_length=50)
    yearRule: Literal['periods','works'] = 'periods'


class Layout(Model):
    version: int = Field(ge=0)
    order: list[SubjectId] = Field(default_factory=list,max_length=100)
    hidden: list[SubjectId] = Field(default_factory=list,max_length=100)

    @model_validator(mode='after')
    def valid(self):
        if len(set(self.order))!=len(self.order) or len(set(self.hidden))!=len(self.hidden):raise ValueError('Предметы не должны повторяться.')
        return self


def points(band):
    value=band.get('points')
    return Fraction(str(value)) if value is not None else None


def band_points(result):
    """Points of the band a published or proposed result chose; none for a missing weight or a result that is not a grade."""
    if result.get('outcome')!='graded':return None
    return next((points(b) for b in result['scale']['bands'] if b['label']==result['label']),None)


def half_up(value):
    # Half-up on an exact ratio, the same arithmetic as recommendation(); points are never negative.
    units=(2*value.numerator*100+value.denominator)//(2*value.denominator)
    return f'{units//100}.{units%100:02d}'


def shown(value):
    text=half_up(value)
    return text.rstrip('0').rstrip('.')


def gpa_of(items):
    """Exact mean of the band points; a grade whose band has no points is skipped, an attestation outcome is not a grade at all."""
    counted=skipped=0;total=Fraction(0)
    for item in items:
        if item.get('outcome')!='graded':continue
        value=band_points(item)
        if value is None:skipped+=1
        else:counted+=1;total+=value
    return dict(value=half_up(total/counted),counted=counted,skipped=skipped) if counted else None


def nearest_band(scale,value):
    """The band closest to the mean; a tie goes to the higher points. Works for 5-point and letter scales alike."""
    options=[(points(b),b['label']) for b in scale['bands'] if points(b) is not None]
    return min(options,key=lambda o:(abs(o[0]-value),-o[0]))[1] if options else None


def school(db,workspace):
    raw=db.execute("SELECT version,document FROM grading_report_settings WHERE workspace=? AND period_id='' AND class_id=''",(workspace,)).fetchone()
    return json.loads(raw[1])|dict(version=raw[0]) if raw else dict(version=0,phrases=[],yearRule='periods')


def layout_of(db,workspace,period_id,class_id):
    raw=db.execute('SELECT version,document FROM grading_report_settings WHERE workspace=? AND period_id=? AND class_id=?',(workspace,period_id,class_id)).fetchone()
    return json.loads(raw[1])|dict(version=raw[0]) if raw else dict(version=0,order=[],hidden=[])


def arrange(items,layout):
    """Hidden subjects drop out, chosen ones come first in the chosen order, the rest follow by name."""
    rank={s:i for i,s in enumerate(layout['order'])};hidden=set(layout['hidden'])
    key=lambda i:(0,rank[i['subjectId']],(),i['subjectId']) if i['subjectId'] in rank else (1,0,natural(i.get('subject')),i['subjectId'])
    return sorted([i for i in items if i['subjectId'] not in hidden],key=key)


def arranged(db,workspace,period_id,items,class_id=''):
    groups={}
    for item in items:groups.setdefault(class_id or item['student']['classId'],[]).append(item)
    return [i for c,group in groups.items() for i in arrange(group,layout_of(db,workspace,period_id,c))]


def results(db,workspace,period,students):
    """{pupil: published results}; internal recommendation details stay with staff, including records published before this rule."""
    found={s:[] for s in students}
    for student,raw in db.execute('SELECT student_id,document FROM final_results WHERE workspace=? AND period_id=? AND student_id IN (SELECT value FROM json_each(?))',(workspace,period['id'],encoded(sorted(students)))):
        item=json.loads(raw);item.pop('calculation',None);item['current']=period['status']=='closed' and period['version']==item['closureVersion']
        found[student].append(item)
    return found


def year_advice(db,workspace,period,row,scale,cache):
    """A year grade comes from the published quarter (or semester) finals of the pupil when the school keeps that rule and the scale has points."""
    if period['kind']!='year' or all(points(b) is None for b in scale['bands']):return None
    if period['id'] not in cache:
        rule=school(db,workspace)['yearRule']
        cache[period['id']]=[dict(id=r[0],name=r[1],kind=r[2],version=r[3],status=r[4]) for r in db.execute("SELECT id,name,kind,version,status FROM journal_periods WHERE workspace=? AND kind IN ('quarter','semester') AND start_day>=? AND end_day<=? ORDER BY start_day",(workspace,period['start'],period['end']))] if rule=='periods' else []
    subs=cache[period['id']]
    if not subs:return None
    found={r[0]:json.loads(r[1]) for r in db.execute('SELECT period_id,document FROM final_results WHERE workspace=? AND student_id=? AND subject_id=? AND period_id IN (SELECT value FROM json_each(?))',(workspace,row['studentId'],row['subjectId'],encoded([s['id'] for s in subs])))}
    # Quarters win over semesters, so a semester summary is never counted twice.
    kind=next((k for k in ['quarter','semester'] if any(s['kind']==k and s['id'] in found for s in subs)),None)
    if kind is None:return None
    chosen=[s for s in subs if s['kind']==kind];entries=[];values=[]
    for s in chosen:
        if s['id'] not in found:continue
        doc=found[s['id']];value=band_points(doc)
        if value is not None:values.append(value)
        entries.append(dict(id=s['id'],name=s['name'],label=doc['label'] if doc['outcome']=='graded' else OUTCOMES.get(doc['outcome'],''),points=shown(value) if value is not None else None,current=s['status']=='closed' and s['version']==doc['closureVersion']))
    if not values:return None
    mean=sum(values)/len(values)
    return dict(rawPercent=None,roundedPercent=None,recommended=nearest_band(scale,mean),basis='periods',points=half_up(mean),periods=entries,missing=[s['name'] for s in chosen if s['id'] not in found])


def duty_classes(db,user,snap,workspace,capability):
    """class_staff.classes for a read connection: the registry tables appear with the first write, and without them only the duties derived from the class record exist."""
    names={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name IN ('class_staff_permissions','absence_reviewers')")}
    if len(names)==2:return classes(db,user,snap,workspace,capability)
    return led(user,snap,workspace) if eligible(user,snap,workspace) else set()


def measures_of(db,workspace,students,period):
    from .behaviour import report_measures
    return report_measures(db,workspace,students,period['start'],period['end'])


def register(service,app,Workspace):
    from .final_grades import public_result
    from .behaviour import csv_cell

    @app.put('/api/final-grades/report-settings')
    def settings(body:SchoolReport,request:Request,workspace:Workspace='school'):
        user,snap=service.context(request,workspace,True,True)
        with service.access.lock,service.access.connect() as db:
            db.execute('BEGIN IMMEDIATE');service.recheck(db,user,snap,workspace)
            if school(db,workspace)['version']!=body.version:raise HTTPException(409,'Настройки табеля изменились. Обновите страницу.')
            version=body.version+1;value=body.model_dump(mode='json',exclude={'version'})
            db.execute("INSERT INTO grading_report_settings VALUES (?,'','',?,?) ON CONFLICT(workspace,period_id,class_id) DO UPDATE SET version=excluded.version,document=excluded.document",(workspace,version,encoded(value)))
            service.access.record(db,'journal.final_report_settings',workspace,detail=encoded(dict(version=version)))
            return value|dict(version=version)

    def known(db,snap,workspace,period_id,class_id):
        period=service.periods.find(db,workspace,period_id)
        if class_id not in snap.entities['class_']:raise HTTPException(404,'Класс не найден.')
        return period

    @app.get('/api/final-grades/report-layouts/{periodId}/{classId}')
    def layout_read(periodId:str,classId:str,request:Request,workspace:Workspace='school'):
        user,snap=service.context(request,workspace,True,True)
        with service.access.read_connect() as db:
            db.execute('BEGIN');service.recheck(db,user,snap,workspace);known(db,snap,workspace,periodId,classId)
            book=db.execute('SELECT id FROM grading_books WHERE workspace=? AND period_id=? ORDER BY closure_version DESC LIMIT 1',(workspace,periodId)).fetchone()
            subjects=db.execute("SELECT r.subject_id,min(json_extract(r.source,'$.subject')) FROM grading_rows r WHERE r.workspace=? AND r.book_id=? AND r.class_id=? GROUP BY r.subject_id",(workspace,book[0],classId)).fetchall() if book else []
            return layout_of(db,workspace,periodId,classId)|dict(subjects=[dict(id=k,name=v or '') for k,v in sorted(subjects,key=lambda s:(natural(s[1]),s[0]))])

    @app.put('/api/final-grades/report-layouts/{periodId}/{classId}')
    def layout_write(periodId:str,classId:str,body:Layout,request:Request,workspace:Workspace='school'):
        user,snap=service.context(request,workspace,True,True)
        with service.access.lock,service.access.connect() as db:
            db.execute('BEGIN IMMEDIATE');service.recheck(db,user,snap,workspace);known(db,snap,workspace,periodId,classId)
            if layout_of(db,workspace,periodId,classId)['version']!=body.version:raise HTTPException(409,'Порядок предметов изменился. Обновите страницу.')
            version=body.version+1;value=body.model_dump(mode='json',exclude={'version'})
            db.execute('INSERT INTO grading_report_settings VALUES (?,?,?,?,?) ON CONFLICT(workspace,period_id,class_id) DO UPDATE SET version=excluded.version,document=excluded.document',(workspace,periodId,classId,version,encoded(value)))
            service.access.record(db,'journal.final_layout',workspace,detail=encoded(dict(periodId=periodId,classId=classId,version=version)))
            return value|dict(version=version)

    @app.get('/api/final-grades/class-report')
    def class_report(request:Request,periodId:str,classId:str,workspace:Workspace='school',preview:bool=False):
        user,snap=service.context(request,workspace);admin=user['role']=='admin'
        # Rights come first: nothing about the period or the class is revealed to anyone else.
        if preview and not admin:raise HTTPException(403,'Предварительный табель доступен администратору.')
        if not admin and classId not in led(user,snap,workspace):raise HTTPException(403,'Нет доступа к табелям этого класса.')
        found=snap.entities['class_'].get(classId)
        if not found:raise HTTPException(404,'Класс не найден.')
        pupils=sorted([s for s in snap.state.students if s.active and s.classId==classId],key=lambda s:(natural(s.name),s.id));ids=[s.id for s in pupils]
        with service.access.read_connect() as db:
            db.execute('BEGIN');service.recheck(db,user,snap,workspace)
            period=service.periods.find(db,workspace,periodId);by=results(db,workspace,period,ids)
            if preview:
                book=db.execute('SELECT id FROM grading_books WHERE workspace=? AND period_id=? AND closure_version=?',(workspace,periodId,period['version'])).fetchone()
                if period['status']!='closed' or not book:raise HTTPException(409,'Подготовьте ведомость нового закрытия.')
                book=service.book(db,workspace,book[0])
                # Approved but unpublished grades stand in for the published ones; drafts and submitted rows never appear.
                for (rid,) in db.execute("SELECT id FROM grading_rows WHERE workspace=? AND book_id=? AND status='approved' AND student_id IN (SELECT value FROM json_each(?))",(workspace,book['id'],encoded(sorted(ids)))).fetchall():
                    row=service.row(db,workspace,rid,user);item=public_result(row,book)|dict(preview=True,current=True)
                    by[row['studentId']]=[i for i in by[row['studentId']] if i['subjectId']!=row['subjectId']]+[item]
            layout=layout_of(db,workspace,periodId,classId)
            measures=measures_of(db,workspace,ids,period) if admin or classId in duty_classes(db,user,snap,workspace,'behaviour_notes') else {}
            cards=[];titles={}
            for s in pupils:
                items=arrange(by[s.id],layout);titles.update({i['subjectId']:i['subject'] for i in items})
                cards.append(dict(id=s.id,name=s.name,items=items,gpa=gpa_of(items),measures=measures.get(s.id,[])))
            subjects=[dict(id=i['subjectId'],name=i['subject']) for i in arrange([dict(subjectId=k,subject=v,student=dict(classId=classId)) for k,v in titles.items()],layout)]
            return dict(school=snap.state.settings.name,period=period,classId=classId,className=found.name,subjects=subjects,pupils=cards,preview=preview)

    def base(db,user,id,snap,workspace):
        scope,args=service.scope(user);where='r.workspace=? AND r.book_id=?'+scope;params=[workspace,id,*args]
        if user['role']!='admin' and not db.execute('SELECT 1 FROM grading_rows r WHERE '+where,params).fetchone():raise HTTPException(404,'Ведомость не назначена вам.')
        return where,params,service.book(db,workspace,id)

    @app.get('/api/final-grades/books/{id}/matrix')
    def matrix(id:str,request:Request,workspace:Workspace='school'):
        user,snap=service.context(request,workspace,True)
        with service.access.read_connect() as db:
            db.execute('BEGIN');service.recheck(db,user,snap,workspace);where,params,book=base(db,user,id,snap,workspace)
            # One pass over the book: counters per pair, names from the frozen source.
            rows=db.execute("SELECT r.class_id,r.subject_id,r.status,r.assigned='',r.proposal IS NOT NULL,count(*),min(json_extract(r.source,'$.student.className')),min(json_extract(r.source,'$.subject')) FROM grading_rows r WHERE "+where+' GROUP BY 1,2,3,4,5',params).fetchall()
            cells={};names={'class':{},'subject':{}}
            for c,s,status,free,proposed,n,class_name,subject_name in rows:
                cell=cells.setdefault((c,s),dict(classId=c,subjectId=s,total=0,draft=0,submitted=0,approved=0,published=0,proposed=0,unassigned=0))
                cell['total']+=n;cell[status]+=n;cell['proposed']+=n*proposed;cell['unassigned']+=n*free
                names['class'][c]=class_name or '';names['subject'][s]=subject_name or ''
            ordered=lambda kind:[dict(id=k,name=v) for k,v in sorted(names[kind].items(),key=lambda x:(natural(x[1]),x[0]))]
            return dict(book=book,classes=ordered('class'),subjects=ordered('subject'),cells=sorted(cells.values(),key=lambda c:(c['classId'],c['subjectId'])))

    @app.get('/api/final-grades/books/{id}/handles')
    def handles(id:str,request:Request,workspace:Workspace='school',classId:str=Query(min_length=1,max_length=100),subjectId:str='',status:Literal['all','draft','submitted','approved','published']='all'):
        user,snap=service.context(request,workspace,True)
        with service.access.read_connect() as db:
            db.execute('BEGIN');service.recheck(db,user,snap,workspace);where,params,_=base(db,user,id,snap,workspace)
            for column,value in [('class_id',classId),('subject_id',subjectId),('status','' if status=='all' else status)]:
                if value:where+=f' AND r.{column}=?';params.append(value)
            rows=db.execute('SELECT r.id,r.version,r.status,r.assigned,r.subject_id,r.class_id FROM grading_rows r WHERE '+where+' ORDER BY r.id LIMIT 5000',params).fetchall()
            return dict(items=[dict(id=r[0],version=r[1],status=r[2],assigned=r[3],subjectId=r[4],classId=r[5]) for r in rows])

    @app.get('/api/final-grades/books/{id}/export')
    def export(id:str,request:Request,workspace:Workspace='school',classId:str='',subjectId:str='',status:Literal['all','draft','submitted','approved','published']='all'):
        user,snap=service.context(request,workspace,True)
        with service.access.read_connect() as db:
            db.execute('BEGIN');service.recheck(db,user,snap,workspace);where,params,_=base(db,user,id,snap,workspace)
            for column,value in [('class_id',classId),('subject_id',subjectId),('status','' if status=='all' else status)]:
                if value:where+=f' AND r.{column}=?';params.append(value)
            # A teacher only has own rows, so the responsible name is the own account; for the administrator the names come from the account list.
            people={t['id']:t['name'] for t in service.teachers(db,workspace,snap)} if user['role']=='admin' else {user['id']:user['name']}
            rows=db.execute('SELECT r.status,r.assigned,r.source,r.proposal,f.document FROM grading_rows r LEFT JOIN final_results f ON f.workspace=r.workspace AND f.row_id=r.id WHERE '+where,params).fetchall()
        parsed=[(json.loads(r[2]),json.loads(r[3]) if r[3] else None,r[0],r[1],r[4]) for r in rows]
        parsed.sort(key=lambda x:(natural(x[0]['student']['className']),natural(x[0]['student']['name']),natural(x[0]['subject'])))
        output=tempfile.SpooledTemporaryFile(max_size=512*1024,mode='w+',encoding='utf-8',newline='')
        try:
            writer=csv.writer(output,delimiter=';');output.write('﻿')
            writer.writerow(['Ученик','Класс','Предмет','Статус','Итог','Процент','После округления','Рекомендация','Шкала','Редакция шкалы','Работ с баллами','Словесная оценка','Обоснование','Ответственный','Автор','Утвердил','В табеле'])
            for source,proposal,state,assigned,published in parsed:
                p=proposal or {};calc=p.get('calculation') or {};scale=p.get('scale') or {}
                writer.writerow([csv_cell(v) for v in [source['student']['name'],source['student']['className'],source['subject'],STATES[state],
                    (p['label'] if p['outcome']=='graded' else OUTCOMES[p['outcome']]) if p else '',calc.get('rawPercent') or '',calc.get('roundedPercent') or '',calc.get('recommended') or '',scale.get('name',''),scale.get('version',''),
                    source['numeric'],p.get('comment',''),p.get('explanation',''),people.get(assigned,''),p.get('author',''),(p.get('approval') or {}).get('actor',''),'Да' if published else '']])
            output.seek(0)
        except BaseException:
            output.close();raise
        def stream():
            with output:
                while chunk:=output.read(65536):yield chunk.encode('utf-8')
        return StreamingResponse(stream(),media_type='text/csv; charset=utf-8',headers={'Content-Disposition':'attachment; filename="final-grades.csv"'})
