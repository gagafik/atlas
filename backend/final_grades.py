"""School-defined final grades, pinned to immutable journal closures."""
from decimal import Decimal
from fractions import Fraction
import hashlib
import json
from typing import Literal
from fastapi import HTTPException, Request, Query
from pydantic import Field, model_validator
from .models import Model
from .access import now
from .journal import user_ids
from .class_staff import led
from .natural import natural
from .notifications import encoded
from .assessment_categories import composite,load
from .gradebook_jobs import teacher_accounts,pick_account
from .final_reports import year_advice,gpa_of,arranged,results,measures_of,duty_classes,school,OUTCOMES


def tables(db):
    db.execute('''CREATE TABLE IF NOT EXISTS grading_scales(workspace TEXT,id TEXT,version INTEGER,document TEXT,PRIMARY KEY(workspace,id))''')
    db.execute('''CREATE TABLE IF NOT EXISTS grading_scale_versions(workspace TEXT,id TEXT,version INTEGER,document TEXT,actor TEXT,created TEXT,PRIMARY KEY(workspace,id,version))''')
    db.execute('''CREATE TABLE IF NOT EXISTS grading_books(workspace TEXT,id TEXT,period_id TEXT,closure_version INTEGER,created TEXT,PRIMARY KEY(workspace,id),UNIQUE(workspace,period_id,closure_version))''')
    db.execute('''CREATE TABLE IF NOT EXISTS grading_rows(workspace TEXT,id TEXT,book_id TEXT,student_id TEXT,subject_id TEXT,class_id TEXT,assigned TEXT,version INTEGER,status TEXT,source TEXT,proposal TEXT,PRIMARY KEY(workspace,id),UNIQUE(workspace,book_id,student_id,subject_id))''')
    db.execute('CREATE INDEX IF NOT EXISTS grading_assignment ON grading_rows(workspace,assigned,book_id,id)')
    db.execute('CREATE INDEX IF NOT EXISTS grading_sheet ON grading_rows(workspace,book_id,class_id,subject_id,status,id)')
    db.execute('''CREATE TABLE IF NOT EXISTS grading_marks(id INTEGER PRIMARY KEY,workspace TEXT,row_id TEXT,document TEXT)''')
    db.execute('CREATE INDEX IF NOT EXISTS grading_evidence ON grading_marks(workspace,row_id,id)')
    db.execute('''CREATE TABLE IF NOT EXISTS grading_history(id INTEGER PRIMARY KEY,workspace TEXT,row_id TEXT,version INTEGER,action TEXT,actor TEXT,created TEXT,reason TEXT,document TEXT)''')
    db.execute('CREATE INDEX IF NOT EXISTS grading_record_history ON grading_history(workspace,row_id,id)')
    db.execute('''CREATE TABLE IF NOT EXISTS final_results(workspace TEXT,period_id TEXT,student_id TEXT,subject_id TEXT,row_id TEXT,version INTEGER,document TEXT,PRIMARY KEY(workspace,period_id,student_id,subject_id))''')
    db.execute('CREATE INDEX IF NOT EXISTS final_pupil ON final_results(workspace,student_id,period_id)')
    db.execute('CREATE INDEX IF NOT EXISTS final_row ON final_results(workspace,row_id)')
    db.execute('''CREATE TABLE IF NOT EXISTS grading_requests(seq INTEGER PRIMARY KEY AUTOINCREMENT,user_id TEXT,request_id TEXT,fingerprint TEXT,result TEXT,UNIQUE(user_id,request_id))''')
    # School-wide settings use period_id='' and class_id=''; a report layout is kept per period and class, so it survives a re-closure.
    db.execute('''CREATE TABLE IF NOT EXISTS grading_report_settings(workspace TEXT,period_id TEXT,class_id TEXT,version INTEGER,document TEXT,PRIMARY KEY(workspace,period_id,class_id))''')
    from .gradebook_jobs import tables as job_tables
    job_tables(db)


class Band(Model):
    label: str = Field(min_length=1,max_length=30)
    minPercent: Decimal = Field(default=0,ge=0,le=100,max_digits=7,decimal_places=4)
    # Weight of the grade in the pupil's average; older scale documents simply lack the key.
    points: Decimal | None = Field(default=None,ge=0,le=100,max_digits=6,decimal_places=2)


class Scale(Model):
    version: int = Field(ge=0)
    name: str = Field(min_length=1,max_length=120)
    kind: Literal['thresholds','labels'] = 'thresholds'
    bands: list[Band] = Field(min_length=2,max_length=30)
    rounding: Literal['none','0','1','2'] = 'none'
    minCount: int = Field(default=1,ge=1,le=10000)
    active: bool = True

    @model_validator(mode='after')
    def valid(self):
        if len({b.label for b in self.bands})!=len(self.bands):raise ValueError('Названия оценок должны отличаться.')
        if any(b.points is not None for b in self.bands) and any(b.points is None for b in self.bands):raise ValueError('Укажите вес для среднего балла у всех оценок шкалы.')
        if self.kind=='thresholds' and (self.bands[0].minPercent!=0 or any(a.minPercent>=b.minPercent for a,b in zip(self.bands,self.bands[1:]))):
            raise ValueError('Пороги начинаются с 0 и строго возрастают.')
        return self


class Prepare(Model):
    version: int = Field(ge=1)


class Version(Model):
    id: str = Field(min_length=1,max_length=100)
    version: int = Field(ge=1)


class Batch(Model):
    rows: list[Version] = Field(min_length=1,max_length=100)
    action: Literal['assign','approve','return','publish','reopen','withdraw']
    teacherId: str = Field(default='',max_length=100)
    reason: str = Field(default='',max_length=1000)

    @model_validator(mode='after')
    def valid(self):
        if len({r.id for r in self.rows})!=len(self.rows):raise ValueError('Повтор строки в запросе.')
        if self.action in ['return','reopen','withdraw'] and len(self.reason)<3:raise ValueError('Нужна причина, минимум 3 символа.')
        return self


class AutoAssign(Model):
    after: str = Field(default='',max_length=100)


class TableRow(Model):
    id: str = Field(min_length=1,max_length=100)
    version: int = Field(ge=1)
    outcome: Literal['graded','not_assessed','exempt'] = 'graded'
    label: str = Field(default='',max_length=30)
    comment: str = Field(default='',max_length=1000)
    explanation: str = Field(default='',max_length=1000)


class TableSave(Model):
    requestId: str = Field(pattern=r'^[a-f0-9-]{36}$')
    scaleId: str = Field(min_length=1,max_length=100)
    scaleVersion: int = Field(ge=1)
    submit: bool = False
    rows: list[TableRow] = Field(min_length=1,max_length=100)

    @model_validator(mode='after')
    def valid(self):
        if len({r.id for r in self.rows})!=len(self.rows):raise ValueError('Повтор строки в запросе.')
        return self


class Accept(Model):
    requestId: str = Field(pattern=r'^[a-f0-9-]{36}$')
    scaleId: str = Field(min_length=1,max_length=100)
    scaleVersion: int = Field(ge=1)
    rows: list[Version] = Field(min_length=1,max_length=100)
    submit: bool = False
    overwrite: bool = False

    @model_validator(mode='after')
    def valid(self):
        if len({r.id for r in self.rows})!=len(self.rows):raise ValueError('Повтор строки в запросе.')
        return self


class Proposal(Model):
    version: int = Field(ge=1)
    scaleId: str = Field(min_length=1,max_length=100)
    scaleVersion: int = Field(ge=1)
    outcome: Literal['graded','not_assessed','exempt'] = 'graded'
    label: str = Field(default='',max_length=30)
    comment: str = Field(default='',max_length=1000)
    explanation: str = Field(default='',max_length=1000)
    submit: bool = False


def recommendation(source,scale):
    # Books prepared after the category rules pin the matching share policy and per-category sums in their source;
    # older books keep the plain weighted average they were prepared with.
    shares=composite(load(source['categories']),source['policy']) if source.get('policy') and 'categories' in source else None
    if shares:exact,count=shares['exact'],shares['counted']
    else:exact,count=Fraction(source['weightedSum'])/Fraction(source['weight']) if Fraction(source['weight']) else None,source['numeric']
    # Compare exact ratios at thresholds; finite display precision must not
    # move a pupil across a boundary. Half-up rounding is exact for positive scores.
    raw=Decimal(exact.numerator)/Decimal(exact.denominator) if exact is not None else None
    rounded=raw;comparison=exact
    if exact is not None and scale['rounding']!='none':
        places=int(scale['rounding']);factor=10**places
        units=(2*exact.numerator*factor+exact.denominator)//(2*exact.denominator)
        rounded=Decimal(units).scaleb(-places);comparison=Fraction(units,factor)
    label=None
    if rounded is not None and count>=scale['minCount'] and scale['kind']=='thresholds':
        label=next(b['label'] for b in reversed(scale['bands']) if comparison>=Fraction(str(b['minPercent'])))
    result=dict(rawPercent=str(raw) if raw is not None else None,roundedPercent=str(rounded) if rounded is not None else None,recommended=label)
    if shares:result|=dict(method=source['policy']['method'],rule=source['policy']['ruleName'],categories=shares['categories'],missing=shares['missing'],excluded=shares['excluded'],blocked=shares['blocked'])
    return result


def public_result(row,book):
    """What a family may read of a proposal: the grade and the comment, never the calculation, the explanation or the author."""
    p=row['proposal']
    return dict(student=row['source']['student'],subjectId=row['subjectId'],subject=row['source']['subject'],periodId=book['period']['id'],period=book['period']['name'],closureVersion=book['closureVersion'],outcome=p['outcome'],label=p['label'],comment=p['comment'],scale=p['scale'],publishedAt=now().isoformat())


class FinalGrades:
    def __init__(self,periods):
        self.periods,self.journal,self.access=periods,periods.journal,periods.access
        self.snapshots=periods.coverage.absences.snapshots

    def context(self,request,workspace,staff=False,admin=False):
        user=self.journal.scope(request,workspace,staff)
        if user['role']=='admin' and workspace not in user['workspaces'] or admin and user['role']!='admin':raise HTTPException(403,'Действие доступно администратору.')
        snap=self.snapshots.get(workspace)
        if user['role']=='teacher' and not user_ids(user,workspace,'teacher') & {t.id for t in snap.state.teachers}:raise HTTPException(403,'Нет действующего назначения учителя.')
        # Ordinary readers use WAL snapshots and must not wait behind preparation.
        with self.access.read_connect() as db:
            ready=db.execute("SELECT 1 FROM sqlite_master WHERE type='index' AND name='grading_job_recent'").fetchone() and db.execute("SELECT 1 FROM pragma_table_info('grading_projected') WHERE name='format'").fetchone() and db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='grading_report_settings'").fetchone()
        if not ready:
            with self.access.lock,self.access.connect() as db:self.journal.tables(db);tables(db)
        return user,snap

    def recheck(self,db,user,snap,workspace):self.periods.recheck(db,user,snap,workspace)

    def replay(self,db,user,body):
        """A lost response is answered again from the stored compact result; the same id with another body is a conflict."""
        key=hashlib.sha256(encoded(body.model_dump(mode='json')).encode()).hexdigest()
        row=db.execute('SELECT fingerprint,result FROM grading_requests WHERE user_id=? AND request_id=?',(user['id'],body.requestId)).fetchone()
        if row:
            if row[0]!=key:raise HTTPException(409,'Идентификатор уже использован другим действием.')
            return key,json.loads(row[1])
        return key,None

    def remember(self,db,user,body,key,result):
        db.execute('INSERT INTO grading_requests(user_id,request_id,fingerprint,result) VALUES (?,?,?,?)',(user['id'],body.requestId,key,encoded(result)))
        db.execute('DELETE FROM grading_requests WHERE user_id=? AND seq NOT IN (SELECT seq FROM grading_requests WHERE user_id=? ORDER BY seq DESC LIMIT 100)',(user['id'],user['id']))

    def scope(self,user):return ('',[]) if user['role']=='admin' else (' AND r.assigned=?',[user['id']])

    def row(self,db,workspace,id,user,version=None):
        raw=db.execute('SELECT * FROM grading_rows WHERE workspace=? AND id=?',(workspace,id)).fetchone()
        if not raw or user['role']!='admin' and raw[6]!=user['id']:raise HTTPException(404,'Строка не найдена или не назначена вам.')
        if not db.execute('SELECT 1 FROM grading_books WHERE workspace=? AND id=?',(workspace,raw[2])).fetchone():raise HTTPException(404,'Ведомость ещё не готова.')
        value=dict(id=raw[1],bookId=raw[2],studentId=raw[3],subjectId=raw[4],classId=raw[5],assigned=raw[6],version=raw[7],status=raw[8],source=json.loads(raw[9]),proposal=json.loads(raw[10]) if raw[10] else None)
        if version is not None and value['version']!=version:raise HTTPException(409,'Строка изменилась. Обновите ведомость.')
        public=db.execute('SELECT document FROM final_results WHERE workspace=? AND row_id=?',(workspace,id)).fetchone()
        value['published']=json.loads(public[0]) if public else None
        return value

    def book(self,db,workspace,id,current=False):
        raw=db.execute('SELECT * FROM grading_books WHERE workspace=? AND id=?',(workspace,id)).fetchone()
        if not raw:raise HTTPException(404,'Ведомость не найдена.')
        period=self.periods.find(db,workspace,raw[2]);fresh=period['status']=='closed' and period['version']==raw[3]
        if current and not fresh:raise HTTPException(409,'Период открыт или закрыт повторно. Подготовьте ведомость нового закрытия.')
        return dict(id=id,period=period,closureVersion=raw[3],created=raw[4],current=fresh)

    def teachers(self,db,workspace,snap):
        ids={t.id for t in snap.state.teachers}
        users=[self.access.public(r) for r in db.execute("SELECT * FROM accounts WHERE active=1 AND role='teacher'")]
        return [dict(id=u['id'],name=u['name']+' · '+u['login']) for u in users if user_ids(u,workspace,'teacher') & ids]

    def save(self,db,workspace,row,user,action,reason=''):
        row['version']+=1
        public=db.execute('SELECT document FROM final_results WHERE workspace=? AND row_id=?',(workspace,row['id'])).fetchone()
        row['published']=json.loads(public[0]) if public else None
        db.execute('UPDATE grading_rows SET assigned=?,version=?,status=?,proposal=? WHERE workspace=? AND id=?',(row['assigned'],row['version'],row['status'],encoded(row['proposal']) if row['proposal'] else None,workspace,row['id']))
        db.execute('INSERT INTO grading_history(workspace,row_id,version,action,actor,created,reason,document) VALUES (?,?,?,?,?,?,?,?)',(workspace,row['id'],row['version'],action,user['name'],now().isoformat(),reason,encoded(row)))
        self.access.record(db,'journal.final_'+action,workspace,detail=encoded(dict(rowId=row['id'],version=row['version'],reason=reason)))

    def scale_for(self,db,workspace,id,version):
        raw=db.execute('SELECT document FROM grading_scales WHERE workspace=? AND id=?',(workspace,id)).fetchone()
        scale=json.loads(raw[0]) if raw else None
        if not scale or not scale['active'] or scale['version']!=version:raise HTTPException(409,'Шкала недоступна или изменилась. Обновите список.')
        return scale

    def draft(self,row):
        if row['status']!='draft':raise HTTPException(409,'Верните строку в черновик перед исправлением.')

    def advice(self,db,workspace,book,row,scale,cache=None):
        # A year book with period finals recommends from them; everything else keeps the works-based calculation.
        return year_advice(db,workspace,book['period'],row,scale,{} if cache is None else cache) or recommendation(row['source'],scale)

    def apply(self,db,workspace,row,book,scale,user,outcome,label,comment,explanation,submit):
        """The one place a proposal is validated and stored; the row and the table share it."""
        calc=self.advice(db,workspace,book,row,scale)
        if outcome=='graded' and label not in [b['label'] for b in scale['bands']]:raise HTTPException(422,'Выберите итог из школьной шкалы.')
        if outcome!='graded' and label:raise HTTPException(422,'У неаттестации или освобождения нет оценки.')
        # A labels scale never computes a recommendation: choosing the grade by hand is its normal use.
        manual=scale['kind']=='labels' and outcome=='graded'
        if not manual and (outcome!='graded' or not calc['recommended'] or label!=calc['recommended']) and len(explanation)<3:raise HTTPException(422,'Объясните итог без рекомендации или отклонение от неё.')
        row['proposal']=dict(outcome=outcome,label=label,comment=comment,explanation=explanation,scale=scale,calculation=calc,author=user['name'],preparedAt=now().isoformat())
        row['status']='submitted' if submit else 'draft';self.save(db,workspace,row,user,'submitted' if submit else 'saved')
        return row

    def notify(self,db,workspace,withdrawn,revision,period_id,period_name,student,name,subjects):
        """One entry per child and period. The title names the subjects and, for a publication, the grades; a withdrawal names neither a grade nor a reason and leads nowhere."""
        def listing(head,parts):return head+(': '+', '.join(parts[:3])+(f' и ещё {len(parts)-3}' if len(parts)>3 else '') if parts else '')
        users=self.journal.notifications.granted_users(db,workspace,'student',{student},['student','parent'])
        if withdrawn:title=listing(f'Отозван итог · {period_name}',sorted(subjects,key=natural));module={}
        else:
            period=self.periods.find(db,workspace,period_id)
            items=arranged(db,workspace,period_id,results(db,workspace,period,[student])[student])
            title=listing(f'Итоги · {period_name}',[f"{i['subject']} — {i['label'] if i['outcome']=='graded' else OUTCOMES[i['outcome']]}" for i in items]);module=dict(module='finals')
        self.journal.notifications.deliver(db,users,workspace,revision,'journal.finals_withdrawn' if withdrawn else 'journal.finals_published',topic=f'finals:{workspace}:{period_id}:{student}',supersede=True,
            target=dict(recordId=period_id,students=[dict(id=student,name=name)],title=title,**module))

    def register(self,app,Workspace):
        @app.get('/api/final-grades/options')
        def options(request:Request,workspace:Workspace='school'):
            user,snap=self.context(request,workspace);staff=user['role'] in ['admin','teacher']
            # A class teacher reads the published reports of the current pupils of the own classes.
            leading=led(user,snap,workspace);pupils={s.id for s in snap.state.students if s.active and s.classId in leading}
            with self.access.read_connect() as db:
                db.execute('BEGIN');self.recheck(db,user,snap,workspace)
                scales=[json.loads(r[0]) for r in db.execute('SELECT document FROM grading_scales WHERE workspace=? ORDER BY id',(workspace,))] if staff else []
                if staff:
                    scope,args=self.scope(user)
                    books=[self.book(db,workspace,r[0]) for r in db.execute('SELECT b.id FROM grading_books b WHERE b.workspace=?'+('' if user['role']=='admin' else ' AND EXISTS(SELECT 1 FROM grading_rows r WHERE r.workspace=b.workspace AND r.book_id=b.id'+scope+')')+' ORDER BY b.created DESC',[workspace,*args])]
                    periods=[self.periods.item(r) for r in db.execute("SELECT * FROM journal_periods WHERE workspace=? AND ever_closed=1 ORDER BY start_day DESC",(workspace,))] if user['role']=='admin' else []
                    if not periods and pupils:periods=[self.periods.item(r) for r in db.execute('''SELECT p.* FROM journal_periods p WHERE p.workspace=? AND EXISTS(SELECT 1 FROM final_results f WHERE f.workspace=p.workspace AND f.period_id=p.id AND f.student_id IN (SELECT value FROM json_each(?))) ORDER BY p.start_day DESC''',(workspace,encoded(sorted(pupils))))]
                else:
                    books=[];ids=user_ids(user,workspace,'student')
                    periods=[self.periods.item(r) for r in db.execute('''SELECT p.* FROM journal_periods p WHERE p.workspace=? AND EXISTS(SELECT 1 FROM final_results f WHERE f.workspace=p.workspace AND f.period_id=p.id AND f.student_id IN (SELECT value FROM json_each(?))) ORDER BY p.start_day DESC''',(workspace,encoded(sorted(ids))))]
                students=[dict(id=s.id,name=s.name) for s in snap.state.students if user['role']=='admin' or user['role'] in ['parent','student'] and s.id in user_ids(user,workspace,'student') or s.id in pupils]
                # Classes for the report-card steps: all of them for the administrator, the led ones for a class teacher.
                shown=[c for c in snap.state.classes if user['role']=='admin' or c.id in leading]
                settings=school(db,workspace) if staff else {}
                return dict(admin=user['role']=='admin',staff=staff,classTeacher=bool(leading),scales=scales,books=books,periods=periods,students=students,teachers=self.teachers(db,workspace,snap) if user['role']=='admin' else [],
                    classes=[dict(id=c.id,name=c.name) for c in sorted(shown,key=lambda c:(natural(c.name),c.id))],phrases=settings.get('phrases',[]),yearRule=settings.get('yearRule','periods'),reportVersion=settings.get('version',0))

        @app.put('/api/final-grades/scales/{id}')
        def scale_edit(id:str,body:Scale,request:Request,workspace:Workspace='school'):
            if len(id)!=32 or any(c not in '0123456789abcdef' for c in id):raise HTTPException(422,'Недопустимый идентификатор шкалы.')
            user,snap=self.context(request,workspace,True,True)
            with self.access.lock,self.access.connect() as db:
                db.execute('BEGIN IMMEDIATE');self.recheck(db,user,snap,workspace)
                row=db.execute('SELECT version FROM grading_scales WHERE workspace=? AND id=?',(workspace,id)).fetchone()
                if (row[0] if row else 0)!=body.version:raise HTTPException(409,'Шкала изменилась. Обновите список.')
                if not row and db.execute('SELECT count(*) FROM grading_scales WHERE workspace=?',(workspace,)).fetchone()[0]>=100:raise HTTPException(422,'Допускается до 100 шкал.')
                value=body.model_dump(mode='json')|dict(id=id,version=body.version+1)
                db.execute('INSERT INTO grading_scales VALUES (?,?,?,?) ON CONFLICT(workspace,id) DO UPDATE SET version=excluded.version,document=excluded.document',(workspace,id,value['version'],encoded(value)))
                db.execute('INSERT INTO grading_scale_versions VALUES (?,?,?,?,?,?)',(workspace,id,value['version'],encoded(value),user['name'],now().isoformat()))
                self.access.record(db,'journal.final_scale',workspace,detail=encoded(dict(id=id,version=value['version'])))
            return value

        @app.get('/api/final-grades/books/{id}')
        def sheet(id:str,request:Request,workspace:Workspace='school',offset:int=Query(0,ge=0,le=10000000),classId:str='',subjectId:str='',status:Literal['all','draft','submitted','approved','published']='all',scaleId:str=''):
            user,snap=self.context(request,workspace,True);scope,args=self.scope(user)
            with self.access.read_connect() as db:
                db.execute('BEGIN');self.recheck(db,user,snap,workspace)
                base='r.workspace=? AND r.book_id=?'+scope;params=[workspace,id,*args]
                if user['role']!='admin' and not db.execute('SELECT 1 FROM grading_rows r WHERE '+base,params).fetchone():raise HTTPException(404,'Ведомость не назначена вам.')
                book=self.book(db,workspace,id)
                choices=db.execute("SELECT DISTINCT r.class_id,json_extract(r.source,'$.student.className'),r.subject_id,json_extract(r.source,'$.subject') FROM grading_rows r WHERE "+base,params).fetchall()
                # Only the filters that are set enter the statement, so SQLite can use the grading_sheet index.
                where=base
                for column,value in [('class_id',classId),('subject_id',subjectId),('status','' if status=='all' else status)]:
                    if value:where+=f' AND r.{column}=?';params.append(value)
                # Names come from the frozen book source, so the order matches the cards; only short columns are sorted per request.
                rows=db.execute("SELECT r.id,json_extract(r.source,'$.student.className'),json_extract(r.source,'$.student.name'),json_extract(r.source,'$.subject') FROM grading_rows r WHERE "+where,params).fetchall()
                rows.sort(key=lambda x:(natural(x[1]),natural(x[2]),natural(x[3]),x[0]))
                total=len(rows);ids=rows[offset:offset+50]
                classes=sorted({(r[0],r[1] or '') for r in choices},key=lambda c:(natural(c[1]),c[0]));subjects=sorted({(r[2],r[3] or '') for r in choices},key=lambda c:(natural(c[1]),c[0]))
                items=[self.row(db,workspace,r[0],user) for r in ids]
                if scaleId:
                    raw=db.execute('SELECT document FROM grading_scales WHERE workspace=? AND id=?',(workspace,scaleId)).fetchone()
                    if not raw:raise HTTPException(404,'Шкала не найдена.')
                    scale=json.loads(raw[0])
                    cache={}
                    for item in items:item['advice']=self.advice(db,workspace,book,item,scale,cache)
                return dict(book=book,items=items,total=total,nextOffset=offset+50 if total>offset+50 else None,classes=[dict(id=k,name=v) for k,v in classes],subjects=[dict(id=k,name=v) for k,v in subjects])

        @app.get('/api/final-grades/rows/{id}/evidence')
        def evidence(id:str,request:Request,workspace:Workspace='school',after:int=Query(0,ge=0)):
            user,snap=self.context(request,workspace,True)
            with self.access.read_connect() as db:
                db.execute('BEGIN');self.recheck(db,user,snap,workspace);row=self.row(db,workspace,id,user)
                kind=db.execute('SELECT source_kind FROM grading_books WHERE workspace=? AND id=?',(workspace,row['bookId'])).fetchone()[0]
                if kind=='shared':
                    from .gradebook_jobs import shared_evidence
                    page=shared_evidence(db,workspace,row,after)
                else:
                    marks=db.execute('SELECT id,document FROM grading_marks WHERE workspace=? AND row_id=? AND id>? ORDER BY id LIMIT 51',(workspace,id,after)).fetchall()
                    page=dict(items=[dict(id=r[0],**json.loads(r[1])) for r in marks[:50]],nextAfter=marks[49][0] if len(marks)>50 else None)
                scales=[json.loads(r[0]) for r in db.execute('SELECT document FROM grading_scales WHERE workspace=?',(workspace,))]
                book=self.book(db,workspace,row['bookId']);cache={}
                return dict(row=row,**page,recommendations={s['id']+':'+str(s['version']):self.advice(db,workspace,book,row,s,cache) for s in scales if s['active']})

        @app.get('/api/final-grades/rows/{id}/history')
        def history(id:str,request:Request,workspace:Workspace='school',before:int|None=Query(None,ge=1)):
            user,snap=self.context(request,workspace,True)
            with self.access.read_connect() as db:
                db.execute('BEGIN');self.recheck(db,user,snap,workspace);self.row(db,workspace,id,user)
                rows=db.execute('SELECT id,version,action,actor,created,reason,document FROM grading_history WHERE workspace=? AND row_id=? AND (? IS NULL OR id<?) ORDER BY id DESC LIMIT 51',(workspace,id,before,before)).fetchall()
                return dict(items=[dict(id=r[0],version=r[1],action=r[2],actor=r[3],created=r[4],reason=r[5],row=json.loads(r[6])) for r in rows[:50]],nextBefore=rows[49][0] if len(rows)>50 else None)

        @app.put('/api/final-grades/rows/{id}')
        def proposal(id:str,body:Proposal,request:Request,workspace:Workspace='school'):
            user,snap=self.context(request,workspace,True)
            with self.access.lock,self.access.connect() as db:
                db.execute('BEGIN IMMEDIATE');self.recheck(db,user,snap,workspace);row=self.row(db,workspace,id,user,body.version);book=self.book(db,workspace,row['bookId'],True)
                self.draft(row);scale=self.scale_for(db,workspace,body.scaleId,body.scaleVersion)
                return self.apply(db,workspace,row,book,scale,user,body.outcome,body.label,body.comment,body.explanation,body.submit)

        @app.post('/api/final-grades/books/{id}/proposals')
        def table_save(id:str,body:TableSave,request:Request,workspace:Workspace='school'):
            user,snap=self.context(request,workspace,True)
            with self.access.lock,self.access.connect() as db:
                db.execute('BEGIN IMMEDIATE');self.recheck(db,user,snap,workspace)
                key,done=self.replay(db,user,body)
                if done is not None:return done
                book=self.book(db,workspace,id,True);scale=self.scale_for(db,workspace,body.scaleId,body.scaleVersion);items=[]
                for item in body.rows:
                    row=self.row(db,workspace,item.id,user,item.version)
                    if row['bookId']!=id:raise HTTPException(404,'Строка не найдена или не назначена вам.')
                    try:
                        self.draft(row);self.apply(db,workspace,row,book,scale,user,item.outcome,item.label,item.comment,item.explanation,body.submit)
                    except HTTPException as e:raise HTTPException(e.status_code,f"{row['source']['student']['name']}: {e.detail}")
                    items.append(dict(id=row['id'],version=row['version'],status=row['status']))
                result=dict(items=items);self.remember(db,user,body,key,result)
                return result

        @app.post('/api/final-grades/books/{id}/accept')
        def accept(id:str,body:Accept,request:Request,workspace:Workspace='school'):
            """Saves the server's own recommendation as the proposal; the client never sends a grade."""
            user,snap=self.context(request,workspace,True)
            with self.access.lock,self.access.connect() as db:
                db.execute('BEGIN IMMEDIATE');self.recheck(db,user,snap,workspace)
                key,done=self.replay(db,user,body)
                if done is not None:return done
                book=self.book(db,workspace,id,True);scale=self.scale_for(db,workspace,body.scaleId,body.scaleVersion);items=[];skipped=[]
                for item in body.rows:
                    row=self.row(db,workspace,item.id,user,item.version)
                    if row['bookId']!=id:raise HTTPException(404,'Строка не найдена или не назначена вам.')
                    if row['status']!='draft':skipped.append(dict(id=row['id'],reason='status'));continue
                    calc=self.advice(db,workspace,book,row,scale);old=row['proposal']
                    if not calc['recommended']:skipped.append(dict(id=row['id'],reason='none'));continue
                    if old and old['label']!=calc['recommended'] and not body.overwrite:skipped.append(dict(id=row['id'],reason='manual'));continue
                    try:self.apply(db,workspace,row,book,scale,user,'graded',calc['recommended'],old['comment'] if old else '','',body.submit)
                    except HTTPException as e:raise HTTPException(e.status_code,f"{row['source']['student']['name']}: {e.detail}")
                    items.append(dict(id=row['id'],version=row['version'],status=row['status']))
                result=dict(items=items,skipped=skipped);self.remember(db,user,body,key,result)
                return result

        @app.post('/api/final-grades/books/{id}/auto-assign')
        def auto_assign(id:str,body:AutoAssign,request:Request,workspace:Workspace='school'):
            user,snap=self.context(request,workspace,True,True)
            with self.access.lock,self.access.connect() as db:
                db.execute('BEGIN IMMEDIATE');self.recheck(db,user,snap,workspace);self.book(db,workspace,id,True)
                # The closure can be older than the staff list: only accounts of teachers who are still on the school's staff.
                current={t.id for t in snap.state.teachers};accounts={t:a for t,a in teacher_accounts(db,workspace).items() if t in current}
                # A short transaction per call keeps the writer lock free; the client repeats from nextAfter.
                ids=[r[0] for r in db.execute("SELECT id FROM grading_rows WHERE workspace=? AND book_id=? AND assigned='' AND status='draft' AND id>? ORDER BY id LIMIT 251",(workspace,id,body.after))]
                more=len(ids)>250;ids=ids[:250];assigned=skipped=0;missing=set()
                for rid in ids:
                    row=self.row(db,workspace,rid,user);teachers=row['source'].get('teachers',[]);account=pick_account([t['id'] for t in teachers],accounts)
                    if account:row['assigned']=account;self.save(db,workspace,row,user,'assign');assigned+=1
                    else:skipped+=1;missing.update(t['name'] or t['id'] for t in teachers if len(accounts.get(t['id'],[]))!=1)
                return dict(assigned=assigned,skipped=skipped,noAccount=sorted(missing,key=natural),nextAfter=ids[-1] if more else None)

        @app.post('/api/final-grades/actions')
        def action(body:Batch,request:Request,workspace:Workspace='school'):
            user,snap=self.context(request,workspace,True,True)
            with self.access.lock,self.access.connect() as db:
                db.execute('BEGIN IMMEDIATE');self.recheck(db,user,snap,workspace)
                if body.action=='assign' and body.teacherId and body.teacherId not in {t['id'] for t in self.teachers(db,workspace,snap)}:raise HTTPException(422,'Выберите действующую учётную запись учителя.')
                changed={};results=[]
                for item in body.rows:
                    row=self.row(db,workspace,item.id,user,item.version);book=self.book(db,workspace,row['bookId'],body.action!='withdraw')
                    if body.action=='assign':
                        if row['status']!='draft':raise HTTPException(409,'Назначение меняется только в черновике.')
                        row['assigned']=body.teacherId
                    elif body.action=='approve':
                        if row['status']!='submitted':raise HTTPException(409,'Утверждать можно только отправленные итоги.')
                        row['status']='approved';row['proposal']['approval']=dict(actor=user['name'],created=now().isoformat())
                    elif body.action in ['return','reopen']:
                        allowed=['submitted','approved'] if body.action=='return' else ['published']
                        if row['status'] not in allowed:raise HTTPException(409,'Статус не позволяет это действие.')
                        row['status']='draft'
                        if row['proposal']:row['proposal'].pop('approval',None)
                    elif body.action=='publish':
                        if row['status']!='approved':raise HTTPException(409,'Публиковать можно только утверждённые итоги.')
                        public=public_result(row,book)
                        db.execute('INSERT INTO final_results VALUES (?,?,?,?,?,?,?) ON CONFLICT(workspace,period_id,student_id,subject_id) DO UPDATE SET row_id=excluded.row_id,version=excluded.version,document=excluded.document',(workspace,book['period']['id'],row['studentId'],row['subjectId'],row['id'],row['version']+1,encoded(public)))
                        row['status']='published';changed.setdefault((book['period']['id'],book['period']['name'],row['studentId'],row['source']['student']['name']),[]).append(row['source']['subject'])
                    elif body.action=='withdraw':
                        current=db.execute('SELECT row_id FROM final_results WHERE workspace=? AND period_id=? AND student_id=? AND subject_id=?',(workspace,book['period']['id'],row['studentId'],row['subjectId'])).fetchone()
                        if not current or current[0]!=row['id']:raise HTTPException(409,'У этой строки нет действующей публикации.')
                        db.execute('DELETE FROM final_results WHERE workspace=? AND period_id=? AND student_id=? AND subject_id=?',(workspace,book['period']['id'],row['studentId'],row['subjectId']))
                        row['status']='draft'
                        if row['proposal']:row['proposal'].pop('approval',None)
                        changed.setdefault((book['period']['id'],book['period']['name'],row['studentId'],row['source']['student']['name']),[]).append(row['source']['subject'])
                    # Only these actions carry a reason; approve, publish and assign never store a stale one.
                    self.save(db,workspace,row,user,body.action,body.reason if body.action in ['return','reopen','withdraw'] else '');results.append(row)
                for (period_id,period_name,student,name),subjects in changed.items():
                    self.notify(db,workspace,body.action=='withdraw',max(r['version'] for r in results),period_id,period_name,student,name,subjects)
                return dict(items=results)

        @app.get('/api/final-grades/report')
        def report(request:Request,studentId:str,periodId:str,workspace:Workspace='school'):
            user,snap=self.context(request,workspace)
            pupil=snap.entities['student'].get(studentId)
            family=user['role'] in ['student','parent'] and studentId in user_ids(user,workspace,'student')
            leader=pupil is not None and pupil.active and pupil.classId in led(user,snap,workspace)
            if user['role']!='admin' and not family and not leader:raise HTTPException(403,'Нет доступа к табелю этого ученика.')
            with self.access.read_connect() as db:
                db.execute('BEGIN');self.recheck(db,user,snap,workspace)
                # Check ownership before revealing period existence or any result.
                period=self.periods.find(db,workspace,periodId)
                items=arranged(db,workspace,periodId,results(db,workspace,period,[studentId])[studentId])
                # Measures marked for the card follow the family and the administrator; a class teacher reads them only with the behaviour duty of the class.
                measures=measures_of(db,workspace,[studentId],period).get(studentId,[]) if user['role']=='admin' or family or leader and pupil.classId in duty_classes(db,user,snap,workspace,'behaviour_notes') else []
                return dict(period=period,items=items,gpa=gpa_of(items),measures=measures)

        from .final_reports import register as reports
        reports(self,app,Workspace)
