"""Commission exams: a planned exam with a commission, its result, the decision with the final grade and the letter to the family."""
from contextlib import closing,contextmanager
from datetime import date
from typing import Literal
from zoneinfo import ZoneInfo
import hashlib,json,sqlite3

from fastapi import HTTPException
from pydantic import Field,model_validator
from .access import now
from .journal import user_ids
from .models import Model
from .notifications import encoded,late_opener,student_ids

KINDS={'reexam':'Пересдача после неудовлетворительной итоговой оценки','family':'Семейное или домашнее обучение','transfer':'После перевода','appeal':'Апелляция оценки'}
STATES={'planned':'Запланирован','held':'Проведён','decided':'Решение принято','letter_sent':'Письмо отправлено','cancelled':'Отменён'}
PAGE=50
TIME=r'^([01]\d|2[0-3]):[0-5]\d$'
def stamp():return now().isoformat()
def today():return now().astimezone(ZoneInfo('Asia/Qyzylorda')).date()
def conflict(code,message,**extra):return HTTPException(409,dict(code=code,message=message,**extra))


def tables(db):
    db.executescript('''
    CREATE TABLE IF NOT EXISTS commission_exams(seq INTEGER PRIMARY KEY AUTOINCREMENT,id TEXT UNIQUE,workspace TEXT,student_id TEXT,class_id TEXT,subject_id TEXT,period_id TEXT,
        day TEXT,status TEXT,version INTEGER,created TEXT,updated TEXT,members TEXT,document TEXT);
    CREATE INDEX IF NOT EXISTS commission_exam_list ON commission_exams(workspace,status,seq);
    CREATE INDEX IF NOT EXISTS commission_exam_student ON commission_exams(workspace,student_id,seq);
    CREATE TABLE IF NOT EXISTS commission_exam_history(seq INTEGER PRIMARY KEY AUTOINCREMENT,workspace TEXT,exam_id TEXT,actor_id TEXT,actor_name TEXT,created TEXT,action TEXT,reason TEXT,document TEXT);
    CREATE INDEX IF NOT EXISTS commission_exam_history_exam ON commission_exam_history(workspace,exam_id,seq);
    CREATE TABLE IF NOT EXISTS commission_requests(seq INTEGER PRIMARY KEY AUTOINCREMENT,user_id TEXT,request_id TEXT,fingerprint TEXT,result TEXT,UNIQUE(user_id,request_id));
    ''')


def has_table(db,name):return bool(db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(name,)).fetchone())


class Plan(Model):
    studentId:str=Field(min_length=1,max_length=100)
    subjectId:str=Field(min_length=1,max_length=100)
    periodId:str=Field(min_length=1,max_length=100)
    kind:Literal['reexam','family','transfer','appeal']
    reason:str=Field(min_length=3,max_length=1000)
    day:date
    start:str=Field(pattern=TIME)
    end:str=Field(pattern=TIME)
    roomId:str=Field(min_length=1,max_length=100)
    chairmanId:str=Field(min_length=1,max_length=100)
    examinerId:str=Field(min_length=1,max_length=100)
    memberIds:list[str]=Field(min_length=1,max_length=8)
    @model_validator(mode='after')
    def valid(self):
        if self.start>=self.end:raise ValueError('Экзамен заканчивается позже, чем начинается.')
        if self.chairmanId==self.examinerId:raise ValueError('Председатель и экзаменатор — разные люди.')
        if len(set(self.memberIds))!=len(self.memberIds) or {self.chairmanId,self.examinerId}&set(self.memberIds):raise ValueError('Члены комиссии не должны повторяться.')
        return self


class NewExam(Plan):
    requestId:str=Field(pattern=r'^[a-f0-9-]{36}$')


class EditExam(Plan):
    version:int=Field(ge=1)


class Cancel(Model):
    version:int=Field(ge=1)
    reason:str=Field(min_length=3,max_length=500)


class Result(Model):
    version:int=Field(ge=1)
    outcome:Literal['graded','absent']
    scaleId:str=Field(default='',max_length=100)
    scaleVersion:int=Field(default=0,ge=0)
    label:str=Field(default='',max_length=30)
    passed:bool=False
    tasks:str=Field(default='',max_length=4000)
    notes:str=Field(default='',max_length=4000)
    @model_validator(mode='after')
    def valid(self):
        if self.outcome=='graded' and (not self.scaleId or not self.label):raise ValueError('Выберите шкалу и оценку.')
        if self.outcome=='absent' and (self.label or self.passed):raise ValueError('У не явившегося нет оценки.')
        return self


class Decide(Model):
    requestId:str=Field(pattern=r'^[a-f0-9-]{36}$')
    version:int=Field(ge=1)
    writeFinal:bool=True
    replace:bool=False
    reason:str=Field(default='',max_length=500)
    @model_validator(mode='after')
    def valid(self):
        if (self.replace or not self.writeFinal) and len(self.reason.strip())<3:raise ValueError('Укажите причину, минимум 3 символа.')
        return self


class Letter(Model):
    version:int=Field(ge=1)


class CommissionExams:
    def __init__(self,behaviour,finals):
        self.behaviour,self.finals=behaviour,finals
        self.access,self.notifications,self.snapshots=behaviour.access,behaviour.notifications,behaviour.snapshots
        behaviour.commission=self

    def prepare(self):
        with closing(self.access.read_connect()) as db:
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='commission_exam_student'").fetchone():return
        with self.access.lock,self.access.connect() as db:tables(db)

    @contextmanager
    def context(self,request,workspace,write=False):
        """The behaviour context: the module switch, the account and revision recheck, BEGIN IMMEDIATE for writes."""
        self.prepare()
        with self.behaviour.context(request,workspace,write) as found:yield found

    def mine(self,user,snap,workspace):
        """Teacher entities the account is linked to now; checked on every request."""
        return user_ids(user,workspace,'teacher') & {t.id for t in snap.state.teachers} if user['role']=='teacher' else set()

    def exam(self,db,workspace,id):
        row=db.execute('SELECT * FROM commission_exams WHERE workspace=? AND id=?',(workspace,id)).fetchone()
        if not row:raise HTTPException(404,'Экзамен не найден.')
        return dict(row)|dict(data=json.loads(row['document']))

    def staff_exam(self,db,user,snap,workspace,id):
        row=self.exam(db,workspace,id)
        if user['role']!='admin' and not self.mine(user,snap,workspace)&set(json.loads(row['members'])):raise HTTPException(404,'Экзамен не найден.')
        return row

    def family_exam(self,db,user,snap,workspace,id):
        row=self.exam(db,workspace,id)
        if row['student_id'] not in self.behaviour.family(user,snap,workspace) or row['status'] not in ['decided','letter_sent']:raise HTTPException(404,'Экзамен не найден.')
        return row

    def names(self,snap):
        return dict(teachers={t.id:t.name for t in snap.state.teachers},subjects={s.id:s.name for s in snap.state.subjects},rooms={r.id:r.name for r in snap.state.rooms},classes={c.id:c.name for c in snap.state.classes})

    def person(self,id,data,names):return dict(id=id,name=names['teachers'].get(id) or data.get('names',{}).get(id,''))

    def public(self,row,snap,user):
        data=row['data'];names=self.names(snap);pupil=snap.entities['student'].get(row['student_id']);members=json.loads(row['members'])
        mine=self.mine(user,snap,row['workspace']) & set(members);admin=user['role']=='admin';status=row['status']
        result=data.get('result');decision=data.get('decision')
        return dict(id=row['id'],version=row['version'],status=status,statusTitle=STATES[status],kind=data['kind'],kindTitle=KINDS[data['kind']],reason=data['reason'],
            studentId=row['student_id'],student=pupil.name if pupil else data['student'],classId=row['class_id'],className=names['classes'].get(row['class_id'],data['className']),
            subjectId=row['subject_id'],subject=names['subjects'].get(row['subject_id'],data['subject']),periodId=row['period_id'],period=data['period'],
            day=row['day'],start=data['start'],end=data['end'],roomId=data['roomId'],room=names['rooms'].get(data['roomId'],data['room']),
            chairman=self.person(data['chairmanId'],data,names),examiner=self.person(data['examinerId'],data,names),members=[self.person(id,data,names) for id in data['memberIds']],
            result=None if not result else {k:result[k] for k in ['outcome','label','passed','tasks','notes','by','at']}|dict(scaleName=result['scale']['name'] if result.get('scale') else ''),
            decision=None if not decision else {k:decision[k] for k in ['decidedBy','decidedAt','final','reason']},
            letter=data.get('letter'),cancelReason=data.get('cancelReason',''),
            canEdit=admin and status=='planned',canCancel=admin and status in ['planned','held'],canResult=bool(mine) and status in ['planned','held'],
            canDecide=admin and status=='held',canSendLetter=admin and status=='decided')

    def family_view(self,row,snap):
        data=row['data'];result=data['result'];decision=data['decision'];letter=data.get('letter')
        subjects={s.id:s.name for s in snap.state.subjects}
        return dict(id=row['id'],studentId=row['student_id'],subject=subjects.get(row['subject_id'],data['subject']),period=data['period'],kind=data['kind'],kindTitle=KINDS[data['kind']],day=row['day'],
            decision=dict(outcome=result['outcome'],label=result['label'],passed=result['passed'],decidedAt=decision['decidedAt']),
            letter=dict(number=letter['number'],sentAt=letter['sentAt']) if letter else None)

    def history(self,db,workspace,user,exam,action,reason,document):
        db.execute('INSERT INTO commission_exam_history(workspace,exam_id,actor_id,actor_name,created,action,reason,document) VALUES (?,?,?,?,?,?,?,?)',
                   (workspace,exam,user['id'],user['name'],stamp(),action,reason,encoded(document)))
        self.access.record(db,'behaviour.commission_'+action,workspace,detail=encoded(dict(examId=exam,version=document.get('version'),reason=reason)))

    def replay(self,db,user,body):
        key=hashlib.sha256(encoded(body.model_dump(mode='json')).encode()).hexdigest()
        row=db.execute('SELECT fingerprint,result FROM commission_requests WHERE user_id=? AND request_id=?',(user['id'],body.requestId)).fetchone()
        if row:
            if row['fingerprint']!=key:raise HTTPException(409,'Идентификатор уже использован другим действием.')
            return key,json.loads(row['result'])
        return key,None

    def remember(self,db,user,body,key,result):
        db.execute('INSERT INTO commission_requests(user_id,request_id,fingerprint,result) VALUES (?,?,?,?)',(user['id'],body.requestId,key,encoded(result)))
        db.execute('DELETE FROM commission_requests WHERE user_id=? AND seq NOT IN (SELECT seq FROM commission_requests WHERE user_id=? ORDER BY seq DESC LIMIT 100)',(user['id'],user['id']))
        return result

    def flags(self,db,user,snap,workspace):
        """What the account may open in the commission screen, or None. Read-only: the tables may not exist yet."""
        if user['role']=='admin':return dict(manage=True,member=False,family=False)
        if not has_table(db,'commission_exams'):return None
        if user['role']=='teacher':
            mine=self.mine(user,snap,workspace)
            found=mine and db.execute("SELECT 1 FROM commission_exams WHERE workspace=? AND status!='cancelled' AND EXISTS(SELECT 1 FROM json_each(members) m WHERE m.value IN (SELECT value FROM json_each(?))) LIMIT 1",(workspace,encoded(sorted(mine)))).fetchone()
            return dict(manage=False,member=True,family=False) if found else None
        kids=self.behaviour.family(user,snap,workspace)
        if kids and db.execute("SELECT 1 FROM commission_exams WHERE workspace=? AND status IN ('decided','letter_sent') AND student_id IN (SELECT value FROM json_each(?)) LIMIT 1",(workspace,encoded(sorted(kids)))).fetchone():
            return dict(manage=False,member=False,family=True)
        return None

    def notify_members(self,db,workspace,row_id,students,member_ids,version,title):
        targets=self.notifications.granted_users(db,workspace,'teacher',member_ids,['teacher'])
        self.notifications.deliver(db,targets,workspace,version,'behaviour.commission_planned',target=dict(module='commission',recordId=row_id,students=students,title=title))

    def opener(self,db,user,workspace,data,kids):
        db.row_factory=sqlite3.Row
        row=db.execute('SELECT student_id,status,members FROM commission_exams WHERE workspace=? AND id=?',(workspace,data.get('recordId'))).fetchone() if has_table(db,'commission_exams') else None
        if not row:return None
        if user['role'] in ['parent','student']:return dict(section='commission') if row['status']=='letter_sent' and row['student_id'] in kids else None
        if user['role']=='admin':return dict(section='commission')
        if user['role']=='teacher' and self.mine(user,self.snapshots.get(workspace),workspace)&set(json.loads(row['members'])):return dict(section='commission')
        return None

    def scale(self,db,workspace,id,version):
        """The scale a result was entered on; edited or switched off since, the result must be entered again."""
        if not has_table(db,'grading_scales'):raise conflict('scale_changed','Шкала недоступна или изменилась. Внесите результат заново.')
        try:return self.finals.scale_for(db,workspace,id,version)
        except HTTPException as e:
            if e.status_code==409:raise conflict('scale_changed','Шкала недоступна или изменилась. Внесите результат заново.') from None
            raise

    def write_final(self,db,workspace,user,row,body):
        """Put the graded result into the period's final grade through the final-grades steps; any error rolls back the exam with it."""
        if user['role']=='admin' and workspace not in user['workspaces']:raise HTTPException(403,'Действие доступно администратору.')
        no_book=conflict('no_book','Нет ведомости текущего закрытия периода. Закройте период и подготовьте ведомость.')
        result=row['data']['result'];label=result['label'];row_factory=db.row_factory
        # The final-grades code reads plain tuples.
        db.row_factory=None
        try:
            if not has_table(db,'grading_books') or not has_table(db,'journal_periods'):raise no_book
            period=self.finals.periods.find(db,workspace,row['period_id'])
            book=db.execute('SELECT id FROM grading_books WHERE workspace=? AND period_id=? AND closure_version=?',(workspace,row['period_id'],period['version'])).fetchone() if period['status']=='closed' else None
            if not book:raise no_book
            found=db.execute('SELECT id FROM grading_rows WHERE workspace=? AND book_id=? AND student_id=? AND subject_id=?',(workspace,book[0],row['student_id'],row['subject_id'])).fetchone()
            if not found:raise conflict('no_row','Ученика нет в ведомости этого закрытия периода.')
            fr=self.finals.row(db,workspace,found[0],user);book=self.finals.book(db,workspace,fr['bookId'],True)
            scale=self.scale(db,workspace,result['scale']['id'],result['scale']['version'])
            raw=db.execute('SELECT document FROM final_results WHERE workspace=? AND period_id=? AND student_id=? AND subject_id=?',(workspace,row['period_id'],row['student_id'],row['subject_id'])).fetchone()
            published=json.loads(raw[0]) if raw else None;proposal=fr['proposal']
            if published and published['outcome']=='graded' and published['label']==label and fr['status']=='published':
                return dict(status='unchanged',rowId=fr['id'],label=label,previous=label)
            differs=bool(published and (published['outcome']!='graded' or published['label']!=label)) or bool(proposal and fr['status']!='published' and (proposal['outcome']!='graded' or proposal['label']!=label))
            if differs and not body.replace:
                current=published or proposal
                raise conflict('grade_exists','Для этого ученика и предмета в периоде уже есть другой итог.',current=dict(label=current['label'],outcome=current['outcome'],status=fr['status'],published=bool(published)))
            reason=body.reason.strip()
            if fr['status']=='published':self.finals.step(db,workspace,user,fr,'reopen',reason=reason)
            elif fr['status'] in ['submitted','approved'] and body.replace:self.finals.step(db,workspace,user,fr,'return',reason=reason)
            explanation=f"Комиссионный экзамен: {KINDS[row['data']['kind']]}. {row['data']['reason']}".strip()
            self.finals.apply(db,workspace,fr,book,scale,user,'graded',label,proposal['comment'] if proposal else '',explanation,True)
            self.finals.step(db,workspace,user,fr,'approve')
            key=self.finals.step(db,workspace,user,fr,'publish')
            self.finals.notify(db,workspace,False,fr['version'],*key[:4],[key[4]])
            before=published['label'] if published and published['outcome']=='graded' else published['outcome'] if published else None
            return dict(status='written',rowId=fr['id'],bookId=fr['bookId'],label=label,previous=before)
        finally:db.row_factory=row_factory

    def register(self,app,Workspace):
        late_opener('commission',self.opener)
        from .commission_routes import register
        register(self,app,Workspace)
