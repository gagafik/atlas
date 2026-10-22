"""Personal course preferences, reviewed before entering the planning state."""
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import hashlib
import json
from uuid import uuid4
from fastapi import HTTPException, Request
from pydantic import Field, model_validator
from .models import Model, State, CourseRequest
from .editing import inspect_edit
from .access import LOCAL, now


class IntakeConfig(Model):
    version: int = Field(default=0,ge=0)
    open: bool = False
    courseIds: list[str] = Field(default_factory=list,max_length=200)
    maxRequests: int = Field(default=2,ge=1,le=10)
    maxAlternatives: int = Field(default=5,ge=1,le=20)
    closesOn: date | None = None
    message: str = Field(default='',max_length=2000)
    @model_validator(mode='after')
    def valid(self):
        if len(self.courseIds)!=len(set(self.courseIds)) or self.open and not self.courseIds: raise ValueError('Для приёма выберите разные курсы.')
        return self


class ConfigWrite(IntakeConfig):
    revision: int = Field(ge=0)


class Choice(Model):
    courseIds: list[str] = Field(min_length=1,max_length=20)
    @model_validator(mode='after')
    def unique(self):
        if len(set(self.courseIds))!=len(self.courseIds): raise ValueError('Альтернативы не должны повторяться.')
        return self


class Submission(Model):
    revision: int = Field(ge=0)
    configVersion: int = Field(ge=0)
    version: int = Field(ge=0)
    studentId: str
    choices: list[Choice] = Field(max_length=10)
    note: str = Field(default='',max_length=1000)


class Review(Model):
    revision: int = Field(ge=0)
    studentId: str
    version: int = Field(ge=1)
    comment: str = Field(default='',max_length=1000)


class Apply(Model):
    token: str = Field(max_length=100)
    revision: int = Field(ge=0)


def request_id(student_id,slot):
    return 'intake-'+hashlib.sha256(f'{student_id}:{slot}'.encode()).hexdigest()[:32]


def register_intake(app,Workspace,access,read,require_revision,save,connect,db_lock):
    def tables(db):
        db.execute('CREATE TABLE IF NOT EXISTS intake_settings (workspace TEXT PRIMARY KEY,document TEXT NOT NULL)')
        db.execute('CREATE TABLE IF NOT EXISTS intake_submissions (workspace TEXT,student_id TEXT,version INTEGER,status TEXT,updated TEXT,document TEXT,PRIMARY KEY(workspace,student_id))')
        db.execute('CREATE TABLE IF NOT EXISTS intake_history (id INTEGER PRIMARY KEY,workspace TEXT,student_id TEXT,version INTEGER,updated TEXT,document TEXT)')
        db.execute('CREATE TABLE IF NOT EXISTS intake_previews (token TEXT PRIMARY KEY,workspace TEXT,created TEXT,document TEXT)')
    def config(db,workspace):
        row=db.execute('SELECT document FROM intake_settings WHERE workspace=?',(workspace,)).fetchone()
        return IntakeConfig.model_validate_json(row[0]) if row else IntakeConfig()
    def get(db,workspace,student):
        row=db.execute('SELECT document FROM intake_submissions WHERE workspace=? AND student_id=?',(workspace,student)).fetchone()
        return json.loads(row[0]) if row else dict(studentId=student,version=0,status='draft',choices=[],note='',comment='',accepted=[])
    def write(db,workspace,record):
        updated=now().isoformat();record['updated']=updated
        doc=json.dumps(record,ensure_ascii=False)
        db.execute('INSERT OR REPLACE INTO intake_submissions VALUES (?,?,?,?,?,?)',(workspace,record['studentId'],record['version'],record['status'],updated,doc))
        db.execute('INSERT INTO intake_history (workspace,student_id,version,updated,document) VALUES (?,?,?,?,?)',(workspace,record['studentId'],record['version'],updated,doc))
    def user(request,workspace,staff=False):
        enabled,value=access.identify(request);value=value if enabled else LOCAL
        if not value: raise HTTPException(401,'Войдите в учётную запись.')
        is_staff=value['role']=='admin' or value['role']=='editor' and workspace in value['workspaces']
        if staff and not is_staff or not staff and value['role'] not in ['student','parent']:
            raise HTTPException(403,'Нет доступа к этому разделу заявок.')
        return value
    def recheck(db,value):
        if value['id']=='local':
            if db.execute('SELECT 1 FROM accounts LIMIT 1').fetchone(): raise HTTPException(409,'Доступ изменился. Войдите снова.')
        else:
            row=db.execute('SELECT * FROM accounts WHERE id=? AND active=1',(value['id'],)).fetchone()
            if not row or access.public(row)!=value: raise HTTPException(409,'Учётная запись изменилась. Войдите снова.')
    def current(db,workspace,revision):
        row=db.execute('SELECT document FROM workspaces WHERE id=?',(workspace,)).fetchone()
        if not row: raise HTTPException(404,'Пространство не найдено.')
        state=State.model_validate_json(row[0])
        if state.revision!=revision: raise HTTPException(409,'План изменился. Обновите данные перед отправкой.')
        return state
    def ids(value,workspace):
        return {id for g in value['grants'] if g['workspace']==workspace and g['view']=='student' for id in g['entityIds']}
    def student(state,id,value=None,workspace=None):
        found=next((s for s in state.students if s.id==id),None)
        if not found or value is not None and id not in ids(value,workspace): raise HTTPException(403,'Нет доступа к этому ученику.')
        return found
    def offered(state,cfg,pupil):
        groups={g.id:g for g in state.groups}
        return [c for c in state.courses if c.id in cfg.courseIds and any(pupil.classId in groups[s.groupId].classIds for s in c.sections)]
    def accepting(cfg):
        # The closing day ends at midnight school time, whatever the server time zone is.
        return cfg.open and (cfg.closesOn is None or now().astimezone(ZoneInfo('Asia/Qyzylorda')).date()<=cfg.closesOn)
    def in_plan(state,record):
        existing={r.id:r.model_dump(mode='json') for r in state.courseRequests}
        return bool(record.get('accepted')) and all(existing.get(r['id'])==r for r in record['accepted'])
    def check_choices(state,cfg,pupil,choices):
        if not pupil.active: raise HTTPException(422,'Архивный ученик не участвует в приёме.')
        available={c.id for c in offered(state,cfg,pupil)}
        if len(choices)>cfg.maxRequests or any(len(c.courseIds)>cfg.maxAlternatives or set(c.courseIds)-available for c in choices):
            raise HTTPException(422,'Проверьте число заявок, альтернатив и доступность курсов для класса.')
        flat=[id for c in choices for id in c.courseIds]
        if len(flat)!=len(set(flat)): raise HTTPException(422,'Не повторяйте курс в разных заявках одного ученика.')

    @app.get('/api/intake/config')
    def settings(request:Request,workspace:Workspace='school'):
        user(request,workspace,True)
        with db_lock,connect() as db: tables(db);return config(db,workspace)

    @app.put('/api/intake/config')
    def configure(body:ConfigWrite,request:Request,workspace:Workspace='school'):
        value=user(request,workspace,True)
        with db_lock,connect() as db:
            tables(db);recheck(db,value);state=current(db,workspace,body.revision);previous=config(db,workspace)
            if body.version!=previous.version: raise HTTPException(409,'Настройки приёма изменились. Обновите страницу.')
            if set(body.courseIds)-{c.id for c in state.courses}: raise HTTPException(422,'В списке есть отсутствующий курс.')
            cfg=IntakeConfig.model_validate(body.model_dump(exclude={'revision'})|dict(version=body.version+1))
            db.execute('INSERT OR REPLACE INTO intake_settings VALUES (?,?)',(workspace,cfg.model_dump_json()))
            access.record(db,'courses.intake_configured',workspace,state.revision)
        return cfg

    @app.get('/api/intake/options')
    def options(request:Request,workspace:Workspace='school'):
        value=user(request,workspace);state=read(workspace);allowed=ids(value,workspace)
        return [dict(id=s.id,name=s.name) for s in state.students if s.id in allowed]

    @app.get('/api/intake/student')
    def personal(request:Request,studentId:str,workspace:Workspace='school'):
        value=user(request,workspace);state=read(workspace);pupil=student(state,studentId,value,workspace)
        with db_lock,connect() as db:
            tables(db);cfg=config(db,workspace);record=get(db,workspace,studentId)
        return dict(revision=state.revision,config=cfg,accepting=accepting(cfg) and pupil.active,
                    courses=[dict(id=c.id,name=c.name) for c in offered(state,cfg,pupil)],submission=record,inPlan=in_plan(state,record))

    @app.put('/api/intake/student')
    def submit(body:Submission,request:Request,workspace:Workspace='school'):
        value=user(request,workspace)
        with db_lock,connect() as db:
            tables(db);recheck(db,value);state=current(db,workspace,body.revision);pupil=student(state,body.studentId,value,workspace)
            cfg=config(db,workspace);old=get(db,workspace,pupil.id)
            if cfg.version!=body.configVersion or old['version']!=body.version: raise HTTPException(409,'Приём или заявка изменились. Загрузите свежую версию.')
            if not accepting(cfg): raise HTTPException(422,'Приём заявок закрыт.')
            check_choices(state,cfg,pupil,body.choices)
            record=old|dict(version=old['version']+1,status='pending',choices=[c.model_dump() for c in body.choices],note=body.note,comment='',author=value['name'])
            write(db,workspace,record);access.record(db,'courses.preference_submitted',workspace,state.revision)
        return personal(request,body.studentId,workspace)

    @app.get('/api/intake/incoming')
    def incoming(request:Request,workspace:Workspace='school',offset:int=0):
        user(request,workspace,True);state=read(workspace)
        if not 0<=offset<=100000: raise HTTPException(422,'Некорректная страница.')
        names={s.id:s.name for s in state.students}
        with db_lock,connect() as db:
            tables(db);total=db.execute('SELECT COUNT(*) FROM intake_submissions WHERE workspace=?',(workspace,)).fetchone()[0]
            rows=[json.loads(r[0]) for r in db.execute("SELECT document FROM intake_submissions WHERE workspace=? ORDER BY CASE status WHEN 'pending' THEN 0 ELSE 1 END,updated DESC,student_id LIMIT 100 OFFSET ?",(workspace,offset))]
        return dict(total=total,offset=offset,rows=[r|dict(studentName=names.get(r['studentId'],'Удалённый ученик'),inPlan=in_plan(state,r)) for r in rows])

    @app.post('/api/intake/reject')
    def reject(body:Review,request:Request,workspace:Workspace='school'):
        value=user(request,workspace,True)
        if not body.comment: raise HTTPException(422,'Укажите причину возврата заявки.')
        with db_lock,connect() as db:
            tables(db);recheck(db,value);state=current(db,workspace,body.revision);record=get(db,workspace,body.studentId)
            if record['version']!=body.version or record['status']!='pending': raise HTTPException(409,'Заявка уже изменилась или рассмотрена.')
            record.update(status='rejected',comment=body.comment,reviewer=value['name']);write(db,workspace,record)
            access.record(db,'courses.preference_returned',workspace,state.revision)
        return dict(reviewed=True)

    @app.post('/api/intake/preview')
    def preview(body:Review,request:Request,workspace:Workspace='school'):
        value=user(request,workspace,True);state=require_revision(workspace,body.revision)
        with db_lock,connect() as db:
            tables(db);cfg=config(db,workspace);record=get(db,workspace,body.studentId)
        if record['version']!=body.version or record['status'] not in ['pending','accepted']: raise HTTPException(409,'Заявка изменилась или возвращена ученику.')
        pupil=student(state,body.studentId);choices=[Choice.model_validate(c) for c in record['choices']];check_choices(state,cfg,pupil,choices)
        old_ids={r['id'] for r in record.get('accepted',[])}
        planned=[];existing={r.id:r for r in state.courseRequests}
        for slot,choice in enumerate(choices):
            id=request_id(pupil.id,slot);old=existing.get(id)
            if old and (old.studentId!=pupil.id or id not in old_ids): raise HTTPException(409,'Идентификатор заявки занят другой записью. Рассмотрите её в редакторе заявок.')
            planned.append(CourseRequest(id=id,studentId=pupil.id,courseIds=choice.courseIds,required=old.required if old else True,lockedGroupId=old.lockedGroupId if old else None))
            old_ids.add(id)
        if any(r.id in old_ids and r.lockedGroupId for r in state.courseRequests): raise HTTPException(422,'В плане есть закреплённая секция этой заявки. Сначала рассмотрите закрепление в редакторе заявок.')
        candidate=State.model_validate(state.model_dump()|dict(courseRequests=[r for r in state.courseRequests if r.id not in old_ids]+planned))
        result=inspect_edit(state,candidate);token=str(uuid4())
        document=dict(revision=state.revision,studentId=pupil.id,version=record['version'],configVersion=cfg.version,comment=body.comment,
                      candidate=candidate.model_dump(mode='json'),accepted=[r.model_dump(mode='json') for r in planned],canApply=result['canApply'])
        with db_lock,connect() as db:
            tables(db);db.execute('DELETE FROM intake_previews WHERE created<?',((now()-timedelta(minutes=30)).isoformat(),))
            db.execute('INSERT INTO intake_previews VALUES (?,?,?,?)',(token,workspace,now().isoformat(),json.dumps(document,ensure_ascii=False)))
            db.execute('DELETE FROM intake_previews WHERE workspace=? AND token NOT IN (SELECT token FROM intake_previews WHERE workspace=? ORDER BY created DESC LIMIT 100)',(workspace,workspace))
        return dict(token=token,revision=state.revision,**result)

    @app.post('/api/intake/apply')
    def apply(body:Apply,request:Request,workspace:Workspace='school'):
        value=user(request,workspace,True)
        with db_lock,connect() as db:
            tables(db);row=db.execute('SELECT document,created FROM intake_previews WHERE token=? AND workspace=?',(body.token,workspace)).fetchone()
        if not row or row[1]<(now()-timedelta(minutes=30)).isoformat(): raise HTTPException(404,'Предпросмотр устарел. Проверьте заявку снова.')
        data=json.loads(row[0])
        if data['revision']!=body.revision: raise HTTPException(409,'Другая версия плана.')
        if not data['canApply']: raise HTTPException(422,'Предложение не прошло проверку.')
        candidate=State.model_validate(data['candidate'])
        def accept(db,saved):
            recheck(db,value);cfg=config(db,workspace);record=get(db,workspace,data['studentId'])
            if cfg.version!=data['configVersion'] or record['version']!=data['version'] or record['status'] not in ['pending','accepted']:
                raise HTTPException(409,'Заявка или условия приёма изменились. Проверьте новую версию.')
            record.update(status='accepted',comment=data['comment'],reviewer=value['name'],accepted=data['accepted'])
            write(db,workspace,record);db.execute('DELETE FROM intake_previews WHERE token=?',(body.token,))
            access.record(db,'courses.preference_accepted',workspace,saved.revision)
        return save(workspace,candidate,after_save=accept)
