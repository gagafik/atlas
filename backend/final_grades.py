"""School-defined final grades, pinned to immutable journal closures."""
from decimal import Decimal
from fractions import Fraction
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
    from .gradebook_jobs import tables as job_tables
    job_tables(db)


class Band(Model):
    label: str = Field(min_length=1,max_length=30)
    minPercent: Decimal = Field(default=0,ge=0,le=100,max_digits=7,decimal_places=4)


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
    exact=Fraction(source['weightedSum'])/Fraction(source['weight']) if Fraction(source['weight']) else None
    # Compare exact ratios at thresholds; finite display precision must not
    # move a pupil across a boundary. Half-up rounding is exact for positive scores.
    raw=Decimal(exact.numerator)/Decimal(exact.denominator) if exact is not None else None
    rounded=raw;comparison=exact
    if exact is not None and scale['rounding']!='none':
        places=int(scale['rounding']);factor=10**places
        units=(2*exact.numerator*factor+exact.denominator)//(2*exact.denominator)
        rounded=Decimal(units).scaleb(-places);comparison=Fraction(units,factor)
    label=None
    if rounded is not None and source['numeric']>=scale['minCount'] and scale['kind']=='thresholds':
        label=next(b['label'] for b in reversed(scale['bands']) if comparison>=Fraction(str(b['minPercent'])))
    return dict(rawPercent=str(raw) if raw is not None else None,roundedPercent=str(rounded) if rounded is not None else None,recommended=label)


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
            ready=db.execute("SELECT 1 FROM sqlite_master WHERE type='index' AND name='grading_job_recent'").fetchone()
        if not ready:
            with self.access.lock,self.access.connect() as db:self.journal.tables(db);tables(db)
        return user,snap

    def recheck(self,db,user,snap,workspace):self.periods.recheck(db,user,snap,workspace)

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
                return dict(admin=user['role']=='admin',staff=staff,classTeacher=bool(leading),scales=scales,books=books,periods=periods,students=students,teachers=self.teachers(db,workspace,snap) if user['role']=='admin' else [])

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
        def sheet(id:str,request:Request,workspace:Workspace='school',offset:int=Query(0,ge=0,le=10000000),classId:str='',subjectId:str='',status:Literal['all','draft','submitted','approved','published']='all'):
            user,snap=self.context(request,workspace,True);scope,args=self.scope(user)
            with self.access.read_connect() as db:
                db.execute('BEGIN');self.recheck(db,user,snap,workspace)
                base='r.workspace=? AND r.book_id=?'+scope;params=[workspace,id,*args]
                if user['role']!='admin' and not db.execute('SELECT 1 FROM grading_rows r WHERE '+base,params).fetchone():raise HTTPException(404,'Ведомость не назначена вам.')
                book=self.book(db,workspace,id)
                choices=db.execute("SELECT DISTINCT r.class_id,json_extract(r.source,'$.student.className'),r.subject_id,json_extract(r.source,'$.subject') FROM grading_rows r WHERE "+base,params).fetchall()
                where=base+' AND (?=\'\' OR r.class_id=?) AND (?=\'\' OR r.subject_id=?) AND (?=\'all\' OR r.status=?)';params+=[classId,classId,subjectId,subjectId,status,status]
                # Names come from the frozen book source, so the order matches the cards; only short columns are sorted per request.
                rows=db.execute("SELECT r.id,json_extract(r.source,'$.student.className'),json_extract(r.source,'$.student.name'),json_extract(r.source,'$.subject') FROM grading_rows r WHERE "+where,params).fetchall()
                rows.sort(key=lambda x:(natural(x[1]),natural(x[2]),natural(x[3]),x[0]))
                total=len(rows);ids=rows[offset:offset+50]
                classes=sorted({(r[0],r[1] or '') for r in choices},key=lambda c:(natural(c[1]),c[0]));subjects=sorted({(r[2],r[3] or '') for r in choices},key=lambda c:(natural(c[1]),c[0]))
                return dict(book=book,items=[self.row(db,workspace,r[0],user) for r in ids],total=total,nextOffset=offset+50 if total>offset+50 else None,classes=[dict(id=k,name=v) for k,v in classes],subjects=[dict(id=k,name=v) for k,v in subjects])

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
                return dict(row=row,**page,recommendations={s['id']+':'+str(s['version']):recommendation(row['source'],s) for s in scales if s['active']})

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
                db.execute('BEGIN IMMEDIATE');self.recheck(db,user,snap,workspace);row=self.row(db,workspace,id,user,body.version);self.book(db,workspace,row['bookId'],True)
                if row['status']!='draft':raise HTTPException(409,'Верните строку в черновик перед исправлением.')
                raw=db.execute('SELECT document FROM grading_scales WHERE workspace=? AND id=?',(workspace,body.scaleId)).fetchone()
                scale=json.loads(raw[0]) if raw else None
                if not scale or not scale['active'] or scale['version']!=body.scaleVersion:raise HTTPException(409,'Шкала недоступна или изменилась. Обновите список.')
                calc=recommendation(row['source'],scale)
                if body.outcome=='graded' and body.label not in [b['label'] for b in scale['bands']]:raise HTTPException(422,'Выберите итог из школьной шкалы.')
                if body.outcome!='graded' and body.label:raise HTTPException(422,'У неаттестации или освобождения нет оценки.')
                # A labels scale never computes a recommendation: choosing the grade by hand is its normal use.
                manual=scale['kind']=='labels' and body.outcome=='graded'
                if not manual and (body.outcome!='graded' or not calc['recommended'] or body.label!=calc['recommended']) and len(body.explanation)<3:raise HTTPException(422,'Объясните итог без рекомендации или отклонение от неё.')
                row['proposal']=dict(outcome=body.outcome,label=body.label,comment=body.comment,explanation=body.explanation,scale=scale,calculation=calc,author=user['name'],preparedAt=now().isoformat())
                row['status']='submitted' if body.submit else 'draft';self.save(db,workspace,row,user,'submitted' if body.submit else 'saved')
                return row

        @app.post('/api/final-grades/actions')
        def action(body:Batch,request:Request,workspace:Workspace='school'):
            user,snap=self.context(request,workspace,True,True)
            with self.access.lock,self.access.connect() as db:
                db.execute('BEGIN IMMEDIATE');self.recheck(db,user,snap,workspace)
                if body.action=='assign' and body.teacherId and body.teacherId not in {t['id'] for t in self.teachers(db,workspace,snap)}:raise HTTPException(422,'Выберите действующую учётную запись учителя.')
                changed=set();results=[]
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
                        p=row['proposal'];public=dict(student=row['source']['student'],subjectId=row['subjectId'],subject=row['source']['subject'],periodId=book['period']['id'],period=book['period']['name'],closureVersion=book['closureVersion'],outcome=p['outcome'],label=p['label'],comment=p['comment'],scale=p['scale'],publishedAt=now().isoformat())
                        db.execute('INSERT INTO final_results VALUES (?,?,?,?,?,?,?) ON CONFLICT(workspace,period_id,student_id,subject_id) DO UPDATE SET row_id=excluded.row_id,version=excluded.version,document=excluded.document',(workspace,book['period']['id'],row['studentId'],row['subjectId'],row['id'],row['version']+1,encoded(public)))
                        row['status']='published';changed.add(row['studentId'])
                    elif body.action=='withdraw':
                        current=db.execute('SELECT row_id FROM final_results WHERE workspace=? AND period_id=? AND student_id=? AND subject_id=?',(workspace,book['period']['id'],row['studentId'],row['subjectId'])).fetchone()
                        if not current or current[0]!=row['id']:raise HTTPException(409,'У этой строки нет действующей публикации.')
                        db.execute('DELETE FROM final_results WHERE workspace=? AND period_id=? AND student_id=? AND subject_id=?',(workspace,book['period']['id'],row['studentId'],row['subjectId']))
                        row['status']='draft'
                        if row['proposal']:row['proposal'].pop('approval',None)
                        changed.add(row['studentId'])
                    # Only these actions carry a reason; approve, publish and assign never store a stale one.
                    self.save(db,workspace,row,user,body.action,body.reason if body.action in ['return','reopen','withdraw'] else '');results.append(row)
                if changed:
                    users=[self.access.public(r) for r in db.execute("SELECT * FROM accounts WHERE active=1 AND role IN ('student','parent')")]
                    recipients=[u for u in users if user_ids(u,workspace,'student') & changed]
                    self.journal.notifications.deliver(db,recipients,workspace,max(r['version'] for r in results),'journal.finals_withdrawn' if body.action=='withdraw' else 'journal.finals_published')
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
                items=[json.loads(r[0]) for r in db.execute('SELECT document FROM final_results WHERE workspace=? AND period_id=? AND student_id=?',(workspace,periodId,studentId))]
                items.sort(key=lambda item:(natural(item.get('subject')),item['subjectId']))
                for item in items:
                    # Internal recommendation details stay with staff, including records published before this rule.
                    item.pop('calculation',None);item['current']=period['status']=='closed' and period['version']==item['closureVersion']
                return dict(period=period,items=items)
