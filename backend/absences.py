"""Private parent notices, class-scoped review and reversible attendance excuses."""
from datetime import date, timedelta
import hashlib
import json
from typing import Literal
from uuid import uuid4

from fastapi import HTTPException, Query, Request
from pydantic import Field, model_validator
from .models import Model
from .access import LOCAL, now
from .journal import user_ids
from .notifications import encoded


def tables(db):
    from .class_staff import tables as staff_tables
    staff_tables(db)
    db.execute('''CREATE TABLE IF NOT EXISTS absence_notices (
        id TEXT PRIMARY KEY,workspace TEXT,author_id TEXT,student_id TEXT,class_id TEXT,
        student_name TEXT,created TEXT,updated TEXT,version INTEGER,status TEXT,
        start_at TEXT,end_at TEXT,reason TEXT,request_id TEXT,request_hash TEXT,
        UNIQUE(workspace,author_id,request_id))''')
    db.execute('CREATE INDEX IF NOT EXISTS absence_student ON absence_notices(workspace,student_id,status,start_at,end_at)')
    db.execute('CREATE INDEX IF NOT EXISTS absence_author ON absence_notices(workspace,author_id,created)')
    db.execute('CREATE TABLE IF NOT EXISTS absence_reviewers (workspace TEXT PRIMARY KEY,version INTEGER,document TEXT)')
    db.execute('''CREATE TABLE IF NOT EXISTS absence_history (
        id INTEGER PRIMARY KEY,notice_id TEXT,version INTEGER,created TEXT,actor_id TEXT,actor_name TEXT,
        action TEXT,comment TEXT,request_id TEXT,request_hash TEXT,UNIQUE(notice_id,actor_id,request_id))''')
    db.execute('CREATE INDEX IF NOT EXISTS absence_history_notice ON absence_history(notice_id,id)')


def approvals(db, workspace, lessons):
    """Return only decision IDs, never private reasons or guardian identities."""
    if not lessons or not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='absence_notices'").fetchone():return {}
    students={s['id'] for lesson in lessons for s in lesson['students']}
    if not students:return {}
    start=min(l['date'] for l in lessons);end=max(l['date'] for l in lessons)+'T23:59'
    rows=db.execute("SELECT id,student_id,start_at,end_at,version FROM absence_notices WHERE workspace=? AND status='approved' AND student_id IN (SELECT value FROM json_each(?)) AND start_at<=? AND end_at>=?",(workspace,encoded(sorted(students)),end,start)).fetchall()
    result={}
    for i,lesson in enumerate(lessons):
        if lesson.get('status')=='cancelled':continue
        ids={s['id'] for s in lesson['students']}
        bounds=(lesson['date']+'T'+lesson['start'],lesson['date']+'T'+lesson['end'])
        result[i]={r[1]:dict(id=r[0],version=r[4]) for r in rows if r[1] in ids and r[2]<=bounds[0] and bounds[1]<=r[3]}
    return result


class Notice(Model):
    requestId: str = Field(pattern=r'^[a-f0-9-]{36}$')
    studentId: str = Field(min_length=1,max_length=100)
    startDate: date
    endDate: date
    startTime: str = Field(default='00:00',pattern=r'^(?:[01][0-9]|2[0-3]):[0-5][0-9]$')
    endTime: str = Field(default='23:59',pattern=r'^(?:[01][0-9]|2[0-3]):[0-5][0-9]$')
    reason: str = Field(min_length=1,max_length=4000)
    fileIds: list[str] = Field(default_factory=list,max_length=5)
    @model_validator(mode='after')
    def valid(self):
        if not self.reason.strip():raise ValueError('Укажите причину отсутствия.')
        if not 0<=(self.endDate-self.startDate).days<=30 or self.startDate==self.endDate and self.endTime<=self.startTime:raise ValueError('Выберите последовательный период до 31 дня.')
        if len(set(self.fileIds))!=len(self.fileIds):raise ValueError('Файлы не должны повторяться.')
        return self
    def bounds(self):return self.startDate.isoformat()+'T'+self.startTime,self.endDate.isoformat()+'T'+self.endTime


class Decision(Model):
    requestId: str = Field(pattern=r'^[a-f0-9-]{36}$')
    version: int = Field(ge=1)
    action: Literal['approve','reject','revoke','withdraw']
    comment: str = Field(default='',max_length=2000)


class Reviewers(Model):
    version: int = Field(ge=0)
    assignments: dict[str,list[str]]
    @model_validator(mode='after')
    def bounded(self):
        if len(self.assignments)>500 or any(len(v)>20 or len(set(v))!=len(v) for v in self.assignments.values()):raise ValueError('До 500 классов и 20 разных ответственных на класс.')
        return self


class Absences:
    def __init__(self,journal,snapshots):
        self.journal,self.access,self.notifications,self.snapshots=journal,journal.access,journal.notifications,snapshots
        from .absence_files import AbsenceFiles
        self.files=AbsenceFiles(self)

    def context(self,request,workspace):
        user=request.state.user or LOCAL
        if user['role'] not in ['admin','teacher','staff','parent']:raise HTTPException(403,'Раздел доступен родителям и ответственным сотрудникам.')
        if user['role']=='staff' and workspace not in user['workspaces']:raise HTTPException(403,'Нет доступа к этому пространству.')
        return user,self.snapshots.get(workspace)

    def recheck(self,db,user,snapshot,workspace):
        self.journal.recheck_account(db,user)
        current=db.execute('SELECT revision FROM workspaces WHERE id=?',(workspace,)).fetchone()
        if not current or current[0]!=snapshot.state.revision:raise HTTPException(409,'Школьные данные изменились. Обновите раздел.')

    def config(self,db,workspace):
        from .class_staff import config
        value=config(db,workspace)
        return dict(version=value['version'],assignments={id:[r['accountId'] for r in rows if 'absence_review' in r['capabilities']] for id,rows in value['assignments'].items()})

    def classes(self,db,user,snapshot,workspace,capability='absence_review'):
        from .class_staff import classes
        return classes(db,user,snapshot,workspace,capability)

    def parent(self,user,snapshot,workspace,student):
        pupil=snapshot.entities['student'].get(student)
        if user['role']!='parent' or student not in user_ids(user,workspace,'student') or not pupil or not pupil.active:raise HTTPException(403,'Нет действующего доступа к этому ребёнку.')
        return pupil

    def can_review(self,db,user,snapshot,workspace,student):
        pupil=snapshot.entities['student'].get(student)
        return user['role']=='admin' or bool(pupil and pupil.classId in self.classes(db,user,snapshot,workspace))

    def find(self,db,id,user,snapshot,workspace):
        row=db.execute('SELECT * FROM absence_notices WHERE id=? AND workspace=?',(id,workspace)).fetchone()
        if not row:raise HTTPException(404,'Заявление не найдено.')
        own=user['role']=='parent' and row[2]==user['id'] and row[3] in user_ids(user,workspace,'student')
        if not (own or self.can_review(db,user,snapshot,workspace,row[3])):raise HTTPException(404,'Заявление не найдено.')
        return row

    def item(self,row,private=False):
        result=dict(id=row[0],studentId=row[3],studentName=row[5],created=row[6],updated=row[7],version=row[8],status=row[9],start=row[10],end=row[11])
        if private:result['reason']=row[12]
        return result

    def closed_periods(self,db,workspace,row):
        """Closed journal periods whose days the notice overlaps; approving or revoking would change their figures."""
        from .journal_periods import range_locks
        return range_locks(db,workspace,row[10][:10],row[11][:10])

    def detail(self,db,row,user,snapshot,workspace):
        result=self.item(row,True);review=self.can_review(db,user,snapshot,workspace,row[3])
        result.update(canReview=review,canWithdraw=user['role']=='parent' and row[2]==user['id'] and row[9]=='pending',files=self.files.linked(db,row[0]),
                      periodLocks=[p['name'] for p in self.closed_periods(db,row[1],row)] if review else [])
        return result

    def notify(self,db,row,snapshot,action):
        workspace,student=row[1],row[3];pupil=snapshot.entities['student'].get(student)
        review_ids=self.config(db,workspace)['assignments'].get(pupil.classId,[]) if pupil else []
        query="SELECT * FROM accounts WHERE active=1 AND (role='admin' OR id=? OR id IN (SELECT value FROM json_each(?)))"
        users=[self.access.public(r) for r in db.execute(query,(row[2],encoded(review_ids)))]
        targets=[u for u in users if self.can_review(db,u,snapshot,workspace,student) or u['id']==row[2] and student in user_ids(u,workspace,'student')]
        self.notifications.deliver(db,targets,workspace,row[8],action)

    def decision_status(self,db,row,user,snap,workspace,version,action):
        if action=='withdraw':
            if user['role']!='parent' or row[2]!=user['id']:raise HTTPException(403,'Отозвать может автор заявления.')
            allowed=row[9]=='pending';status='withdrawn'
        else:
            if not self.can_review(db,user,snap,workspace,row[3]):raise HTTPException(403,'Нет права рассматривать это заявление.')
            allowed=row[9]==('approved' if action=='revoke' else 'pending')
            status=dict(approve='approved',reject='rejected',revoke='revoked')[action]
        if not allowed or row[8]!=version:raise HTTPException(409,'Состояние заявления изменилось. Откройте его заново.')
        # Only approved notices change attendance figures; reject and withdraw move a pending notice and stay allowed.
        if action in ['approve','revoke'] and (closed:=self.closed_periods(db,workspace,row)):
            raise HTTPException(403,'Решение изменит посещаемость закрытого учебного периода: '+', '.join(p['name'] for p in closed)+'. Администратор должен сначала открыть период с указанием причины.')
        return status

    def apply_decision(self,db,row,user,snap,workspace,status,comment,request_id,fingerprint,*,notify=True):
        """Caller validates every decision first and owns the write transaction."""
        id=row[0];stamp=now().isoformat();version=row[8]+1
        db.execute('UPDATE absence_notices SET version=?,status=?,updated=? WHERE id=?',(version,status,stamp,id))
        db.execute('INSERT INTO absence_history(notice_id,version,created,actor_id,actor_name,action,comment,request_id,request_hash) VALUES (?,?,?,?,?,?,?,?,?)',(id,version,stamp,user['id'],user['name'],status,comment,request_id,fingerprint))
        changed=self.find(db,id,user,snap,workspace)
        if notify:self.notify(db,changed,snap,'absence.updated')
        if notify and status in ['approved','revoked']:
            candidates=db.execute("""SELECT * FROM accounts a WHERE active=1 AND role IN ('student','parent') AND EXISTS(
                SELECT 1 FROM json_each(a.permissions,'$.grants') g,json_each(g.value,'$.entityIds') s
                WHERE json_extract(g.value,'$.workspace')=? AND json_extract(g.value,'$.view')='student' AND s.value=?)""",(workspace,row[3])).fetchall()
            targets=[self.access.public(r) for r in candidates]
            self.notifications.deliver(db,[u for u in targets if row[3] in user_ids(u,workspace,'student')],workspace,version,'journal.updated')
        self.access.record(db,'absence.'+status,workspace,detail=id)
        return changed

    def register(self,app,Workspace):
        from .class_staff import register as register_staff
        register_staff(self,app,Workspace)
        from .absence_bulk import register
        register(self,app,Workspace)
        @app.get('/api/journal/absences/options')
        def options(request:Request,workspace:Workspace='school'):
            from .journal import today
            user,snap=self.context(request,workspace)
            settings=snap.state.settings;default=min(max(today(),settings.yearStart),settings.yearEnd)
            with self.access.lock,self.access.connect() as db:
                tables(db);self.recheck(db,user,snap,workspace)
                config=self.config(db,workspace);classes=self.classes(db,user,snap,workspace)
                pupils=[dict(id=s.id,name=s.name) for s in snap.state.students if s.active and s.id in user_ids(user,workspace,'student')] if user['role']=='parent' else []
                result=dict(parent=user['role']=='parent',admin=user['role']=='admin',canReview=user['role']=='admin' or bool(classes),students=pupils,yearStart=settings.yearStart,yearEnd=settings.yearEnd,defaultDate=default.isoformat())
                if user['role']=='admin':
                    candidates=[self.access.public(r) for r in db.execute("SELECT * FROM accounts WHERE active=1 AND role='teacher'")]
                    result.update(config=config,classes=[dict(id=c.id,name=c.name) for c in snap.state.classes],teachers=[dict(id=u['id'],name=u['name'],login=u['login']) for u in candidates if user_ids(u,workspace,'teacher')])
                return result

        @app.put('/api/journal/absences/reviewers')
        def reviewers(body:Reviewers,request:Request,workspace:Workspace='school'):
            user,snap=self.context(request,workspace)
            if user['role']!='admin':raise HTTPException(403,'Назначение ответственных доступно администратору.')
            with self.access.lock,self.access.connect() as db:
                tables(db);db.execute('BEGIN IMMEDIATE');self.recheck(db,user,snap,workspace)
                if db.execute('SELECT 1 FROM class_staff_permissions WHERE workspace=?',(workspace,)).fetchone():
                    raise HTTPException(409,'Полномочия уже разделены. Используйте «Права работы с классами» в разделе пользователей.')
                if self.config(db,workspace)['version']!=body.version:raise HTTPException(409,'Назначения изменились. Обновите раздел.')
                classes={c.id for c in snap.state.classes}
                teachers={r[0]:self.access.public(r) for r in db.execute("SELECT * FROM accounts WHERE active=1 AND role='teacher'")}
                if set(body.assignments)-classes or any(a not in teachers or not user_ids(teachers[a],workspace,'teacher') for ids in body.assignments.values() for a in ids):raise HTTPException(422,'Выберите существующий класс и действующего учителя этого пространства.')
                db.execute('INSERT INTO absence_reviewers VALUES (?,?,?) ON CONFLICT(workspace) DO UPDATE SET version=excluded.version,document=excluded.document',(workspace,body.version+1,encoded(body.assignments)))
                self.access.record(db,'absence.reviewers_updated',workspace,detail=encoded(body.assignments))
            return dict(version=body.version+1)

        @app.get('/api/journal/absences')
        def listing(request:Request,workspace:Workspace='school',status:Literal['all','pending','approved','rejected','withdrawn','revoked']='all',before:int|None=Query(default=None,ge=1)):
            user,snap=self.context(request,workspace)
            with self.access.lock,self.access.connect() as db:
                tables(db);self.recheck(db,user,snap,workspace)
                query='SELECT n.rowid,n.* FROM absence_notices n WHERE workspace=?';params=[workspace]
                if user['role']=='parent':
                    query+=' AND author_id=? AND student_id IN (SELECT value FROM json_each(?))';params += [user['id'],encoded(sorted(user_ids(user,workspace,'student')))]
                elif user['role']!='admin':
                    classes=self.classes(db,user,snap,workspace);ids=[s.id for s in snap.state.students if s.classId in classes]
                    query+=' AND student_id IN (SELECT value FROM json_each(?))';params.append(encoded(ids))
                if status!='all':query+=' AND status=?';params.append(status)
                if before is not None:query+=' AND n.rowid<?';params.append(before)
                rows=db.execute(query+' ORDER BY n.rowid DESC LIMIT 51',params).fetchall()
                return dict(items=[self.item(r[1:]) for r in rows[:50]],nextBefore=rows[49][0] if len(rows)>50 else None)

        @app.post('/api/journal/absences/preview')
        def preview(body:Notice,request:Request,workspace:Workspace='school'):
            user,snap=self.context(request,workspace);self.parent(user,snap,workspace,body.studentId)
            if not snap.state.settings.yearStart<=body.startDate<=body.endDate<=snap.state.settings.yearEnd:raise HTTPException(422,'Период вне учебного года.')
            start,end=body.bounds();lessons=[]
            for offset in range((body.endDate-body.startDate).days+1):
                for r in snap.student_lessons(body.startDate+timedelta(days=offset),body.studentId):
                    if r['activity']=='lesson' and r['status']!='cancelled' and start<=r['date']+'T'+r['start'] and r['date']+'T'+r['end']<=end:lessons.append(r)
            with self.access.lock,self.access.connect() as db:self.recheck(db,user,snap,workspace)
            return dict(lessons=lessons,revision=snap.state.revision)

        @app.post('/api/journal/absences')
        def submit(body:Notice,request:Request,workspace:Workspace='school'):
            user,snap=self.context(request,workspace);pupil=self.parent(user,snap,workspace,body.studentId)
            if not snap.state.settings.yearStart<=body.startDate<=body.endDate<=snap.state.settings.yearEnd:raise HTTPException(422,'Период вне учебного года.')
            fingerprint=hashlib.sha256(encoded(body.model_dump(mode='json')).encode()).hexdigest();start,end=body.bounds()
            with self.access.lock,self.access.connect() as db:
                tables(db);self.files.tables(db);db.execute('BEGIN IMMEDIATE');self.recheck(db,user,snap,workspace)
                previous=db.execute('SELECT * FROM absence_notices WHERE workspace=? AND author_id=? AND request_id=?',(workspace,user['id'],body.requestId)).fetchone()
                if previous:
                    if previous[14]!=fingerprint:raise HTTPException(409,'Этот запрос уже использован для другого заявления.')
                    return self.detail(db,previous,user,snap,workspace)
                if db.execute("SELECT 1 FROM absence_notices WHERE workspace=? AND student_id=? AND status IN ('pending','approved') AND start_at<? AND end_at>?",(workspace,body.studentId,end,start)).fetchone():raise HTTPException(409,'За этот период уже есть заявление. Уточните его состояние у школы или выберите другой период.')
                count=db.execute('SELECT count(*) FROM absence_notices WHERE author_id=? AND created>?',(user['id'],(now()-timedelta(hours=1)).isoformat())).fetchone()[0]
                if count>=10:raise HTTPException(429,'До 10 заявлений в час. Повторите позже.')
                id=uuid4().hex;created=now().isoformat()
                db.execute('INSERT INTO absence_notices VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(id,workspace,user['id'],pupil.id,pupil.classId,pupil.name,created,created,1,'pending',start,end,body.reason,body.requestId,fingerprint))
                self.files.bind(db,body.fileIds,user,workspace,pupil.id,body.requestId,id)
                db.execute('INSERT INTO absence_history(notice_id,version,created,actor_id,actor_name,action,comment,request_id,request_hash) VALUES (?,?,?,?,?,?,?,?,?)',(id,1,created,user['id'],user['name'],'submitted','',body.requestId,fingerprint))
                row=self.find(db,id,user,snap,workspace);self.notify(db,row,snap,'absence.submitted')
                self.access.record(db,'absence.submitted',workspace,detail=id)
                return self.detail(db,row,user,snap,workspace)

        @app.get('/api/journal/absences/{id}')
        def detail(id:str,request:Request,workspace:Workspace='school'):
            user,snap=self.context(request,workspace)
            with self.access.lock,self.access.connect() as db:
                tables(db);self.files.tables(db);self.recheck(db,user,snap,workspace);row=self.find(db,id,user,snap,workspace)
                self.access.record(db,'absence.read',workspace,detail=id)
                return self.detail(db,row,user,snap,workspace)

        @app.get('/api/journal/absences/{id}/history')
        def history(id:str,request:Request,workspace:Workspace='school',before:int|None=Query(default=None,ge=1)):
            user,snap=self.context(request,workspace)
            with self.access.lock,self.access.connect() as db:
                tables(db);self.recheck(db,user,snap,workspace);self.find(db,id,user,snap,workspace)
                rows=db.execute('SELECT id,version,created,actor_name,action,comment,request_id FROM absence_history WHERE notice_id=? AND (? IS NULL OR id<?) ORDER BY id DESC LIMIT 51',(id,before,before)).fetchall()
                self.access.record(db,'absence.history_read',workspace,detail=id)
                return dict(items=[dict(id=r[0],version=r[1],created=r[2],actor=r[3],action=r[4],comment=r[5],batchId=r[6][6:] if r[6].startswith('batch:') else None) for r in rows[:50]],nextBefore=rows[49][0] if len(rows)>50 else None)

        @app.post('/api/journal/absences/{id}/decision')
        def decision(id:str,body:Decision,request:Request,workspace:Workspace='school'):
            user,snap=self.context(request,workspace);fingerprint=hashlib.sha256(encoded(body.model_dump()).encode()).hexdigest()
            if body.action!='withdraw' and not body.comment.strip():raise HTTPException(422,'Объясните решение.')
            with self.access.lock,self.access.connect() as db:
                tables(db);self.files.tables(db);db.execute('BEGIN IMMEDIATE');self.recheck(db,user,snap,workspace);row=self.find(db,id,user,snap,workspace)
                previous=db.execute('SELECT request_hash FROM absence_history WHERE notice_id=? AND actor_id=? AND request_id=?',(id,user['id'],body.requestId)).fetchone()
                if previous:
                    if previous[0]!=fingerprint:raise HTTPException(409,'Запрос уже использован для другого решения.')
                    return self.detail(db,row,user,snap,workspace)
                status=self.decision_status(db,row,user,snap,workspace,body.version,body.action)
                changed=self.apply_decision(db,row,user,snap,workspace,status,body.comment,body.requestId,fingerprint)
                return self.detail(db,changed,user,snap,workspace)
