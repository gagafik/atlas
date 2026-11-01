"""School forms with explicit per-form delegation. Audiences are fixed at publication, except standing forms; surveys may be anonymous."""
from contextlib import closing, contextmanager
from datetime import datetime, timedelta
from types import SimpleNamespace
import csv
import hashlib
import json
import tempfile
from uuid import uuid4

from fastapi import HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask

from .access import WORKSPACES, now
from .class_staff import led
from .messages import Directory
from .natural import natural
from .notifications import ZONE, encoded, has_table, permission_key
from .portal_cache import PortalSnapshots
from .school_forms_models import (ANONYMOUS_MIN, STAFF, Audience, Definition, NewForm, EditForm, AccessChange,
                                  Transition, AnswerWrite, Review, Mutation, Reminder, validate_answers)

HOLDING = ('submitted', 'returned', 'approved')  # answers in these statuses keep their seats
# Announcement audiences are resolved for an administrator's form as for an administrator's message; this account is only the sender.
SYSTEM = dict(id='system', name='Атлас', role='admin', active=True, workspaces=WORKSPACES, grants=[])


def tables(db):
    db.execute('''CREATE TABLE IF NOT EXISTS school_forms (
        seq INTEGER PRIMARY KEY AUTOINCREMENT,id TEXT UNIQUE,workspace TEXT,version INTEGER,
        status TEXT,creator_id TEXT,created TEXT,updated TEXT,document TEXT)''')
    db.execute('CREATE INDEX IF NOT EXISTS school_form_workspace ON school_forms(workspace,seq)')
    db.execute('''CREATE TABLE IF NOT EXISTS school_form_access (
        form_id TEXT,user_id TEXT,permission TEXT,permissions TEXT,PRIMARY KEY(form_id,user_id))''')
    db.execute('CREATE INDEX IF NOT EXISTS school_form_delegate ON school_form_access(user_id,permissions,form_id)')
    db.execute('''CREATE TABLE IF NOT EXISTS school_form_recipients (
        form_id TEXT,user_id TEXT,entity_id TEXT,permissions TEXT,role TEXT,name TEXT,entity_name TEXT,
        PRIMARY KEY(form_id,user_id,entity_id))''')
    db.execute('CREATE INDEX IF NOT EXISTS school_form_recipient ON school_form_recipients(user_id,permissions,form_id)')
    db.execute('''CREATE TABLE IF NOT EXISTS school_form_answers (
        id TEXT PRIMARY KEY,form_id TEXT,user_id TEXT,entity_id TEXT,version INTEGER,status TEXT,
        updated TEXT,document TEXT,UNIQUE(form_id,user_id,entity_id))''')
    db.execute('CREATE INDEX IF NOT EXISTS school_form_answer_status ON school_form_answers(form_id,status,id)')
    db.execute('''CREATE TABLE IF NOT EXISTS school_form_history (
        seq INTEGER PRIMARY KEY AUTOINCREMENT,form_id TEXT,answer_id TEXT,created TEXT,
        actor_id TEXT,action TEXT,document TEXT)''')
    db.execute('CREATE INDEX IF NOT EXISTS school_form_history_item ON school_form_history(form_id,answer_id,seq)')
    db.execute('''CREATE TABLE IF NOT EXISTS school_form_requests (
        seq INTEGER PRIMARY KEY AUTOINCREMENT,user_id TEXT,request_id TEXT,fingerprint TEXT,result TEXT,
        UNIQUE(user_id,request_id))''')
    db.execute('CREATE INDEX IF NOT EXISTS school_form_request_owner ON school_form_requests(user_id,seq)')
    # Ballots and anonymous answers share only the form id and carry no time column or ordering key, so no query pairs them.
    # The database file still keeps the order of insertion in its page layout (and in WAL and backups): SCHOOL_FORMS.md states this limit.
    db.execute('CREATE TABLE IF NOT EXISTS school_form_ballots (form_id TEXT,user_id TEXT,entity_id TEXT,PRIMARY KEY(form_id,user_id,entity_id)) WITHOUT ROWID')
    db.execute('CREATE TABLE IF NOT EXISTS school_form_anonymous (id TEXT,form_id TEXT,answers TEXT,PRIMARY KEY(form_id,id)) WITHOUT ROWID')
    # One row per account, form and local day: the reminder is sent once however many people press the button. Created last: prepare() looks for it.
    db.execute('CREATE TABLE IF NOT EXISTS school_form_reminders (form_id TEXT,user_id TEXT,day TEXT,created TEXT,PRIMARY KEY(form_id,user_id,day))')


def grant_ids(user, workspace, view):
    return {id for g in user['grants'] if g['workspace']==workspace and g['view']==view for id in g['entityIds']}


def enabled(user, snap, workspace):
    if not user or not user['active']: return False
    if user['role']=='admin': return True
    if user['role'] in ['editor','staff']: return workspace in user['workspaces']
    if user['role']=='teacher': return bool(grant_ids(user,workspace,'teacher') & snap.entities['teacher'].keys())
    if user['role'] in ['student','parent']:
        return any(snap.entities['student'][id].active for id in grant_ids(user,workspace,'student') if id in snap.entities['student'])
    return any(g['workspace']==workspace and set(g['entityIds']) & snap.entities.get('class_' if g['view']=='class' else g['view'],{}).keys() for g in user['grants'])


def accepting(form):
    doc=form['definition'];current=now()
    return form['status']=='open' and (not doc['opensAt'] or datetime.fromisoformat(doc['opensAt'])<=current) and (not doc['closesAt'] or current<datetime.fromisoformat(doc['closesAt']))


def local_day():
    """The school's calendar day (Asia/Qyzylorda): reminders are counted per this day, not per UTC date."""
    return now().astimezone(ZONE).date()


def version(form, expected):
    if form['version']!=expected: raise HTTPException(409,'Форма изменилась. Обновите её перед действием.')


def compatible(db, form, expected):
    """An older view stays valid while questions, kind and audience are unchanged."""
    if form['version']==expected: return
    row=db.execute("SELECT document FROM school_form_history WHERE form_id=? AND answer_id='' AND json_extract(document,'$.version')=? ORDER BY seq DESC LIMIT 1",(form['id'],expected)).fetchone()
    old=json.loads(row[0])['definition'] if row else None
    if not old or any(old[k]!=form['definition'][k] for k in ['kind','questions','audience']): raise HTTPException(409,'Вопросы или аудитория формы изменились. Загрузите свежую версию: введённый текст останется на экране.')


def seats(db, form, exclude=''):
    """{question id: {choice: seats left}} for the limited options; `exclude` is an answer that keeps its own seat."""
    left={}
    for q in form['definition']['questions']:
        limits=q.get('limits')
        if not limits: continue
        # The id is quoted in the path: question ids may contain '-'. A single choice and a list both count once per chosen value.
        taken=dict(db.execute('SELECT j.value,count(*) FROM school_form_answers a,json_each(a.document,?) j WHERE a.form_id=? AND a.status IN (SELECT value FROM json_each(?)) AND a.id!=? GROUP BY j.value',
                              (f'$.answers."{q["id"]}"',form['id'],encoded(HOLDING),exclude)))
        left[q['id']]={choice:max(0,limit-taken.get(choice,0)) for choice,limit in limits.items()}
    return left


def reserve(db, form, answer, answers):
    """Refuse a submission that finds an option full. Runs inside the write transaction, so held answers never exceed a limit."""
    left=seats(db,form,answer['id'])
    if not left: return
    # One seat per child, not one per parent: another account's held answer for the same child counts as the child's seat.
    if answer['entityId'] and db.execute('SELECT 1 FROM school_form_answers WHERE form_id=? AND entity_id=? AND id!=? AND status IN (SELECT value FROM json_each(?))',(form['id'],answer['entityId'],answer['id'],encoded(HOLDING))).fetchone():
        raise HTTPException(409,'За этого ребёнка уже подан ответ другим представителем.')
    for question in form['definition']['questions']:
        places=left.get(question['id'])
        if not places: continue
        value=answers.get(question['id'])
        for choice in value if isinstance(value,list) else [] if value in [None,''] else [value]:
            if places.get(choice)==0: raise HTTPException(409,f'Места на вариант «{choice}» закончились. Выберите другой вариант.')


def withheld(count):
    """Results of an anonymous survey stay closed until enough people have answered to hide each one."""
    return count<ANONYMOUS_MIN


def tally(definition, answers):
    """(answers counted, one entry per question) for an iterable of answer dicts."""
    result={q['id']:dict(id=q['id'],title=q['title'],answered=0,choices={choice:0 for choice in (['Да','Нет'] if q['kind']=='consent' else q['choices'])}) for q in definition['questions']}
    total=0
    for given in answers:
        total+=1
        for key,question in result.items():
            value=given.get(key)
            if value is None or value=='' or value==[]:continue
            question['answered']+=1
            values=value if isinstance(value,list) else ['Да' if value is True else 'Нет' if value is False else value]
            for chosen in values:
                if chosen in question['choices']: question['choices'][chosen]+=1
    return total,list(result.values())


class SchoolForms:
    def __init__(self, access, notifications, stamp, messages):
        self.access,self.notifications,self.messages=access,notifications,messages
        self.snapshots=PortalSnapshots(access.read,stamp)

    def prepare(self):
        with closing(self.access.read_connect()) as db:
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='school_form_reminders'").fetchone(): return
        with self.access.lock,self.access.connect() as db:
            tables(db);self.notifications.tables(db)

    @contextmanager
    def context(self, request, workspace, write=False):
        user=request.state.user
        if not user: raise HTTPException(403,'Для школьных форм нужны учётные записи.')
        snap=self.snapshots.get(workspace)
        if not enabled(user,snap,workspace): raise HTTPException(403,'Нет доступа к формам этого пространства.')
        self.prepare()
        def check(db):
            row=db.execute('SELECT * FROM accounts WHERE id=? AND active=1',(user['id'],)).fetchone()
            revision=db.execute('SELECT revision FROM workspaces WHERE id=?',(workspace,)).fetchone()
            if not row or self.access.public(row)!=user or not revision or revision[0]!=snap.state.revision:
                raise HTTPException(409,'Учётная запись или школьные данные изменились. Обновите страницу.')
        if write:
            with self.access.lock,self.access.connect() as db:
                db.execute('BEGIN IMMEDIATE');check(db);yield db,user,snap
        else:
            with closing(self.access.read_connect()) as db:
                db.execute('BEGIN');check(db);yield db,user,snap

    def form(self, db, workspace, id):
        row=db.execute('SELECT id,version,status,created,updated,document FROM school_forms WHERE id=? AND workspace=?',(id,workspace)).fetchone()
        if not row: raise HTTPException(404,'Форма не найдена.')
        return dict(id=row[0],version=row[1],status=row[2],created=row[3],updated=row[4],definition=json.loads(row[5]))

    def permission(self, db, form, user):
        if user['role']=='admin': return 'admin'
        if user['role'] not in STAFF: return None
        row=db.execute('SELECT permission,permissions FROM school_form_access WHERE form_id=? AND user_id=?',(form['id'],user['id'])).fetchone()
        return row[0] if row and row[1]==permission_key(user) else None

    def require(self, db, form, user, manage=False):
        permission=self.permission(db,form,user)
        if permission not in (['admin','manage'] if manage else ['admin','manage','review']):
            raise HTTPException(404,'Форма недоступна.')
        return permission

    def still(self, form, account, entity, role, snap, workspace):
        """The name a recipient row shows while it still gives access, else None.

        Access follows the role and the current links, not the rights the account had at publication:
        ending one child's link closes the form for that child only, and new teacher grants keep a survey."""
        if account['role']!=role: return None
        audience=form['definition']['audience']
        if entity:
            pupil=snap.entities['student'].get(entity)
            if not pupil or not pupil.active or entity not in grant_ids(account,workspace,'student') or audience['classIds'] and pupil.classId not in audience['classIds']: return None
            name=pupil.name
        else: name=account['name']
        if audience.get('preset','roles')=='class_teachers' and not led(account,snap,workspace): return None
        return name

    def audience_contexts(self, user, doc, snap, workspace):
        """[(pupil id, pupil name)] of an account in an audience by roles and classes: its active linked pupils, or one empty pair for staff."""
        if user['role'] not in ['parent','student']: return [('','')]
        classes=doc['audience']['classIds']
        pupils=[snap.entities['student'][id] for id in grant_ids(user,workspace,'student') if id in snap.entities['student']]
        return sorted((s.id,s.name) for s in pupils if s.active and (not classes or s.classId in classes))

    def contexts(self, db, form, user, snap, workspace):
        if form['status']=='draft': return []
        doc=form['definition']
        # A standing form is open to whoever belongs to the audience now; once closed, only people with a recipient row keep it.
        if doc.get('standing') and form['status']=='open' and user['role'] in doc['audience']['roles']:
            return [dict(entityId=entity,name=name or user['name']) for entity,name in self.audience_contexts(user,doc,snap,workspace)]
        result=[]
        for entity,role in db.execute('SELECT entity_id,role FROM school_form_recipients WHERE form_id=? AND user_id=? ORDER BY entity_id',(form['id'],user['id'])).fetchall():
            name=self.still(form,user,entity,role,snap,workspace)
            if name is not None: result.append(dict(entityId=entity,name=name))
        return result

    def require_recipient(self, db, form, user, snap, workspace, entity):
        found=next((c for c in self.contexts(db,form,user,snap,workspace) if c['entityId']==entity),None)
        if not found: raise HTTPException(404,'Ответ на эту форму недоступен.')
        return found

    def save_form(self, db, form, user, action):
        form['version']+=1;form['updated']=now().isoformat()
        db.execute('UPDATE school_forms SET version=?,status=?,updated=?,document=? WHERE id=?',(form['version'],form['status'],form['updated'],encoded(form['definition']),form['id']))
        db.execute('INSERT INTO school_form_history(form_id,answer_id,created,actor_id,action,document) VALUES (?,?,?,?,?,?)',(form['id'],'',form['updated'],user['id'],action,encoded(form)))
        return dict(id=form['id'],version=form['version'],status=form['status'])

    def replay(self, db, user, body, operation):
        fingerprint=hashlib.sha256(encoded([operation,body.model_dump(mode='json')]).encode()).hexdigest()
        row=db.execute('SELECT fingerprint,result FROM school_form_requests WHERE user_id=? AND request_id=?',(user['id'],body.requestId)).fetchone()
        if row:
            if row[0]!=fingerprint: raise HTTPException(409,'Этот идентификатор уже использован для другого действия.')
            return fingerprint,json.loads(row[1])
        return fingerprint,None

    def remember(self, db, user, body, fingerprint, result):
        db.execute('INSERT INTO school_form_requests(user_id,request_id,fingerprint,result) VALUES (?,?,?,?)',(user['id'],body.requestId,fingerprint,encoded(result)))
        db.execute('DELETE FROM school_form_requests WHERE user_id=? AND seq NOT IN (SELECT seq FROM school_form_requests WHERE user_id=? ORDER BY seq DESC LIMIT 100)',(user['id'],user['id']))
        return result

    def accounts_of(self, db, form, snap, workspace, user):
        """([(account, [(pupil id, pupil name)])], saved-group members out of reach) for the audience of the form as it is now."""
        doc=form['definition'];audience=doc['audience'];preset=audience.get('preset','roles');found=[]
        if preset=='roles':
            if set(audience['classIds'])-snap.entities['class_'].keys(): raise HTTPException(422,'В аудитории есть отсутствующий класс.')
            for row in db.execute('SELECT * FROM accounts WHERE active=1 AND role IN (SELECT value FROM json_each(?))',(encoded(audience['roles']),)):
                account=self.access.public(row)
                if enabled(account,snap,workspace): found.append((account,self.audience_contexts(account,doc,snap,workspace)))
            return found,0
        # Announcement audiences come from the messages module: one resolver for both, no copy of its rules here.
        if preset=='saved':
            if user['role']!='admin': raise HTTPException(403,'Сохранённую группу проверяет и публикует администратор, который её составил.')
            if not has_table(db,'message_saved_groups'): raise HTTPException(422,'Сохранённая группа недоступна.')
        body=SimpleNamespace(recipientId=None,recipientIds=[],studentId=None,schoolwide=False,audience=preset,roles=[],groupIds=[],savedGroupId=audience.get('savedGroupId'))
        # The audience is set by an administrator, so the channel matrix of a teacher sender does not apply; a saved group belongs to its owner.
        selected,unavailable=self.messages.audiences.select(db,body,user if preset=='saved' else SYSTEM,Directory(snap.state,workspace))
        pupils=snap.entities['student']
        for account,scopes in selected:
            if enabled(account,snap,workspace): found.append((account,[(id,pupils[id].name) for id in scopes if id in pupils] if account['role'] in ['parent','student'] else [('','')]))
        return found,unavailable

    def recipients(self, db, form, snap, workspace, user):
        """(rows, accounts, fingerprint, unavailable): one row per account and child, with the rights that were checked."""
        result=[];users={};found,unavailable=self.accounts_of(db,form,snap,workspace,user)
        for account,contexts in found:
            for entity,name in contexts:
                result.append((account['id'],entity,permission_key(account),account['role'],account['name'],name));users[account['id']]=account
                if len(result)>10000: raise HTTPException(422,'Аудитория больше 10 000 ответов. Разделите форму.')
        fingerprint=hashlib.sha256(encoded(sorted(result)).encode()).hexdigest()
        return result,list(users.values()),fingerprint,unavailable

    def delegates(self, db, form, snap, workspace):
        result=[]
        for row in db.execute('SELECT a.* FROM accounts a JOIN school_form_access x ON a.id=x.user_id WHERE x.form_id=? AND a.active=1',(form['id'],)):
            user=self.access.public(row)
            if enabled(user,snap,workspace) and self.permission(db,form,user): result.append(user)
        # The creator remains notified only while still an administrator.
        row=db.execute("SELECT a.* FROM accounts a JOIN school_forms f ON f.creator_id=a.id WHERE f.id=? AND a.active=1 AND a.role='admin'",(form['id'],)).fetchone()
        if row:
            user=self.access.public(row)
            if user['id'] not in {u['id'] for u in result}: result.append(user)
        return result

    def answer(self, db, form, user, entity):
        row=db.execute('SELECT document FROM school_form_answers WHERE form_id=? AND user_id=? AND entity_id=?',(form['id'],user['id'],entity)).fetchone()
        return json.loads(row[0]) if row else dict(id=str(uuid4()),version=0,status='draft',answers={},comment='',updated=None,entityId=entity)

    def save_answer(self, db, form, user, answer, owner, action, private=False):
        stamp=now().isoformat();answer['version']+=1
        # A private correction keeps the time and version staff see: 'hidden' counts the saves they never see.
        if private: answer['hidden']=answer.get('hidden',0)+1;answer['draftUpdated']=stamp
        else: answer['updated']=stamp;answer.pop('draftUpdated',None)
        db.execute('''INSERT INTO school_form_answers VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(form_id,user_id,entity_id)
            DO UPDATE SET version=excluded.version,status=excluded.status,updated=excluded.updated,document=excluded.document''',
            (answer['id'],form['id'],owner,answer['entityId'],answer['version'],answer['status'],answer['updated'],encoded(answer)))
        # An unsent correction ('draft') is private to the applicant and never enters the history.
        base={k:v for k,v in answer.items() if k not in ['draft','draftUpdated']}|dict(updated=stamp)
        history=base|dict(answers={}) if action in ['save','withdraw'] else base
        db.execute('INSERT INTO school_form_history(form_id,answer_id,created,actor_id,action,document) VALUES (?,?,?,?,?,?)',(form['id'],answer['id'],stamp,user['id'],action,encoded(history)))
        return dict(id=answer['id'],version=answer['version'],status=answer['status'])

    def ordered(self, db, form_id, snap, statuses, after=''):
        """Answers by class, pupil and sender name; only short columns are sorted, documents load per page."""
        classes,pupils=snap.entities['class_'],snap.entities['student']
        def rank(id, user_id, name, entity_name, entity, role):
            pupil=pupils.get(entity);group=classes.get(pupil.classId) if pupil else None;label=group.name if group else ''
            return (0 if group else 1,natural(label),natural(entity_name or name),natural(name),id),id,label,name,entity_name,role,user_id,entity
        select='SELECT a.id,a.user_id,r.name,r.entity_name,r.entity_id,r.role FROM school_form_answers a JOIN school_form_recipients r ON r.form_id=a.form_id AND r.user_id=a.user_id AND r.entity_id=a.entity_id WHERE a.form_id=? AND '
        rows=sorted((rank(*row) for row in db.execute(select+statuses,(form_id,))),key=lambda r:r[0])
        if after:
            # The cursor resolves through the recipient row even if that answer has since returned to draft.
            row=db.execute(select+'a.id=?',(form_id,after)).fetchone()
            if not row: raise HTTPException(409,'Список ответов изменился. Обновите результаты.')
            mark=rank(*row)[0];rows=[r for r in rows if r[0]>mark]
        return rows

    def anonymous_answer(self, db, form, user, body):
        """Cast a ballot and store the answer apart from it. Nothing written here names the author: no history, stamp, audit or notice."""
        # Answers stay out of the stored request hash: for a short list of options it could be recomputed for every account.
        clean=body.model_copy(update={'answers':{}})
        fingerprint,old=self.replay(db,user,clean,'answer:'+form['id'])
        if old: return old
        compatible(db,form,body.formVersion)
        if not accepting(form): raise HTTPException(409,'Приём ответов сейчас закрыт.')
        if body.action!='submit': raise HTTPException(422,'Анонимный ответ отправляется один раз: черновик и отзыв недоступны.')
        validate_answers(form['definition'],body.answers,True)
        if db.execute('SELECT 1 FROM school_form_ballots WHERE form_id=? AND user_id=? AND entity_id=?',(form['id'],user['id'],body.entityId)).fetchone(): raise HTTPException(409,'Вы уже ответили на этот опрос.')
        db.execute('INSERT INTO school_form_ballots VALUES (?,?,?)',(form['id'],user['id'],body.entityId))
        db.execute('INSERT INTO school_form_anonymous VALUES (?,?,?)',(str(uuid4()),form['id'],encoded(body.answers)))
        return self.remember(db,user,clean,fingerprint,dict(id='',version=1,status='submitted'))

    def roster(self, db, form, snap, workspace):
        """[(account, child id, role, child name)] of recipient rows that still give access to a live account."""
        accounts={};result=[]
        for row in db.execute('SELECT a.*,r.entity_id,r.role FROM school_form_recipients r JOIN accounts a ON a.id=r.user_id WHERE r.form_id=? AND a.active=1',(form['id'],)):
            if row[0] not in accounts:
                account=self.access.public(row[:8]);accounts[row[0]]=account if enabled(account,snap,workspace) else None
            account=accounts[row[0]]
            name=self.still(form,account,row[8],row[9],snap,workspace) if account else None
            if name is not None: result.append((account,row[8],row[9],name if row[8] else ''))
        return result

    def answered(self, db, form):
        """[(account id, child id, role)] of people who have answered. A draft or a withdrawn answer is not an answer."""
        table,clause=('school_form_ballots','') if form['definition'].get('anonymous') else ('school_form_answers'," AND a.status NOT IN ('draft','withdrawn')")
        return db.execute(f'SELECT a.user_id,a.entity_id,r.role FROM {table} a JOIN school_form_recipients r ON r.form_id=a.form_id AND r.user_id=a.user_id AND r.entity_id=a.entity_id WHERE a.form_id=?'+clause,(form['id'],)).fetchall()

    def pending_people(self, db, form, snap, workspace):
        """(recipient rows, those who have not answered). Parents answer separately, but a second parent of an answered child is not chased."""
        rows=self.roster(db,form,snap,workspace);given=self.answered(db,form)
        done={(user,entity) for user,entity,_ in given}
        covered=set() if form['definition'].get('anonymous') else {(entity,role) for _,entity,role in given if entity}
        return rows,[r for r in rows if (r[0]['id'],r[1]) not in done and (r[1],r[2]) not in covered]

    def own_status(self, db, form, user):
        """{child id: state of the caller's own answer}; an anonymous survey only knows that a ballot was cast."""
        if form['definition'].get('anonymous'): return {entity:'submitted' for entity, in db.execute('SELECT entity_id FROM school_form_ballots WHERE form_id=? AND user_id=?',(form['id'],user['id']))}
        return dict(db.execute('SELECT entity_id,status FROM school_form_answers WHERE form_id=? AND user_id=?',(form['id'],user['id'])))

    def documents(self, db, form_id, ids):
        return dict(db.execute('SELECT id,document FROM school_form_answers WHERE form_id=? AND id IN (SELECT value FROM json_each(?))',(form_id,encoded(ids))))

    def register(self, app, Workspace):
        @app.get('/api/forms/options')
        def options(request:Request,workspace:Workspace='school'):
            with self.context(request,workspace) as (db,user,snap):
                if user['role']!='admin': return dict(canCreate=False,classes=[],staff=[],savedGroups=[])
                staff=[]
                for row in db.execute("SELECT * FROM accounts WHERE active=1 AND role IN ('editor','teacher','staff','viewer') ORDER BY name,id"):
                    account=self.access.public(row)
                    if enabled(account,snap,workspace): staff.append(dict(id=account['id'],name=account['name'],role=account['role']))
                # The groups the administrator saved in Messages; a database that never used them has no table, and a read creates none.
                groups=[dict(id=id,name=name) for id,name in db.execute('SELECT id,name FROM message_saved_groups WHERE workspace=? AND owner_id=? ORDER BY name,id',(workspace,user['id']))] if has_table(db,'message_saved_groups') else []
                return dict(canCreate=True,classes=[dict(id=c.id,name=c.name) for c in snap.state.classes],staff=staff,savedGroups=groups)

        @app.get('/api/forms/items')
        def listing(request:Request,workspace:Workspace='school',before:int=Query(default=9223372036854775807,ge=1),limit:int=Query(default=30,ge=1,le=50)):
            with self.context(request,workspace) as (db,user,snap):
                where='';params=[workspace,before]
                if user['role']!='admin':
                    # Delegation follows the rights it was given under; a recipient row only needs the same role (contexts() checks the links).
                    # An open standing form is offered to its roles even without a row: whoever joined later can answer.
                    where=''' AND (EXISTS (SELECT 1 FROM school_form_access a WHERE a.form_id=f.id AND a.user_id=? AND a.permissions=?) OR
                        EXISTS (SELECT 1 FROM school_form_recipients r WHERE r.form_id=f.id AND r.user_id=? AND r.role=?) OR
                        f.status='open' AND json_extract(f.document,'$.standing')=1 AND EXISTS (SELECT 1 FROM json_each(f.document,'$.audience.roles') WHERE value=?))'''
                    params += [user['id'],permission_key(user),user['id'],user['role'],user['role']]
                rows=db.execute('SELECT f.seq,f.id FROM school_forms f WHERE f.workspace=? AND f.seq<?'+where+' ORDER BY f.seq DESC LIMIT ?',(*params,limit+1)).fetchall()
                items=[]
                for seq,id in rows[:limit]:
                    form=self.form(db,workspace,id);permission=self.permission(db,form,user);contexts=self.contexts(db,form,user,snap,workspace)
                    if not permission and not contexts: continue
                    doc=form['definition']
                    own=self.own_status(db,form,user) if contexts else {}
                    items.append(dict(id=id,title=doc['title'],kind=doc['kind'],version=form['version'],status=form['status'],accepting=accepting(form),closesAt=doc['closesAt'],permission=permission,
                                      standing=bool(doc.get('standing')),anonymous=bool(doc.get('anonymous')),contexts=[c|dict(status=own.get(c['entityId'],'none')) for c in contexts]))
                return dict(items=items,nextBefore=rows[limit-1][0] if len(rows)>limit else None)

        @app.post('/api/forms/items')
        def create(body:NewForm,request:Request,workspace:Workspace='school'):
            with self.context(request,workspace,True) as (db,user,snap):
                if user['role']!='admin': raise HTTPException(403,'Новую карточку формы создаёт администратор; затем назначает ответственных.')
                fingerprint,old=self.replay(db,user,body,'create:'+workspace)
                if old: return old
                if db.execute("SELECT count(*) FROM school_forms WHERE workspace=? AND status!='archived'",(workspace,)).fetchone()[0]>=500: raise HTTPException(422,'Архивируйте старые формы: не более 500 действующих карточек.')
                id=str(uuid4());stamp=now().isoformat();definition=Definition(title=body.title,kind=body.kind).model_dump(mode='json')
                db.execute('INSERT INTO school_forms(id,workspace,version,status,creator_id,created,updated,document) VALUES (?,?,?,?,?,?,?,?)',(id,workspace,1,'draft',user['id'],stamp,stamp,encoded(definition)))
                self.access.record(db,'forms.created',workspace,1,principal=user)
                return self.remember(db,user,body,fingerprint,dict(id=id,version=1,status='draft'))

        @app.get('/api/forms/items/{id}')
        def detail(id:str,request:Request,workspace:Workspace='school'):
            with self.context(request,workspace) as (db,user,snap):
                form=self.form(db,workspace,id);permission=self.permission(db,form,user);contexts=self.contexts(db,form,user,snap,workspace)
                if not permission and not contexts: raise HTTPException(404,'Форма недоступна.')
                result=form|dict(permission=permission,contexts=contexts,accepting=accepting(form),seatsLeft=seats(db,form))
                if not permission:
                    result['definition']={k:v for k,v in form['definition'].items() if k!='audience'}
                elif form['definition'].get('anonymous'):
                    # Only the number of ballots: there are no answer rows to count by status.
                    result['counts']={'submitted':db.execute('SELECT count(*) FROM school_form_ballots WHERE form_id=?',(id,)).fetchone()[0]}
                    result['audienceCount']=db.execute('SELECT count(*) FROM school_form_recipients WHERE form_id=?',(id,)).fetchone()[0]
                else:
                    result['counts']=dict(db.execute('SELECT status,count(*) FROM school_form_answers WHERE form_id=? GROUP BY status',(id,)))
                    # A saved draft is private: only aggregate completed/submitted states leave the respondent scope.
                    result['counts'].pop('draft',None)
                    result['audienceCount']=db.execute('SELECT count(*) FROM school_form_recipients WHERE form_id=?',(id,)).fetchone()[0]
                if permission=='admin':
                    result['collaborators']=[]
                    for row in db.execute('SELECT a.*,x.permission,x.permissions FROM school_form_access x JOIN accounts a ON a.id=x.user_id WHERE x.form_id=?',(id,)):
                        account=self.access.public(row[:8])
                        result['collaborators'].append(dict(userId=account['id'],name=account['name'],permission=row[8],current=enabled(account,snap,workspace) and row[9]==permission_key(account)))
                return result

        @app.post('/api/forms/items/{id}/copy')
        def copy_form(id:str,body:Mutation,request:Request,workspace:Workspace='school'):
            with self.context(request,workspace,True) as (db,user,snap):
                if user['role']!='admin': raise HTTPException(403,'Копию карточки создаёт администратор.')
                source=self.form(db,workspace,id);fingerprint,old=self.replay(db,user,body,'copy:'+id)
                if old: return old
                version(source,body.version)
                if db.execute("SELECT count(*) FROM school_forms WHERE workspace=? AND status!='archived'",(workspace,)).fetchone()[0]>=500: raise HTTPException(422,'Архивируйте старые формы.')
                definition=source['definition']|dict(title=source['definition']['title'][:150]+' — копия',opensAt=None,closesAt=None)
                copied=str(uuid4());stamp=now().isoformat()
                db.execute('INSERT INTO school_forms(id,workspace,version,status,creator_id,created,updated,document) VALUES (?,?,?,?,?,?,?,?)',(copied,workspace,1,'draft',user['id'],stamp,stamp,encoded(definition)))
                return self.remember(db,user,body,fingerprint,dict(id=copied,version=1,status='draft'))

        @app.put('/api/forms/items/{id}')
        def edit(id:str,body:EditForm,request:Request,workspace:Workspace='school'):
            with self.context(request,workspace,True) as (db,user,snap):
                form=self.form(db,workspace,id);permission=self.require(db,form,user,True)
                fingerprint,old=self.replay(db,user,body,'edit:'+id)
                if old: return old
                version(form,body.version)
                if form['status']!='draft': raise HTTPException(409,'Опубликованные вопросы неизменны. Создайте новую форму.')
                definition=body.definition.model_dump(mode='json')
                # The stored draft may predate the newer keys: compare it as the model reads it, so an untouched old audience is not a change.
                if permission!='admin' and (Audience.model_validate(form['definition']['audience']).model_dump(mode='json')!=definition['audience'] or any(definition[k]!=form['definition'].get(k,False) for k in ['standing','anonymous'])):
                    raise HTTPException(403,'Аудиторию, постоянный приём и анонимность задаёт администратор.')
                if set(definition['audience']['classIds'])-snap.entities['class_'].keys(): raise HTTPException(422,'Выберите действующие классы.')
                form['definition']=definition;result=self.save_form(db,form,user,'edited')
                return self.remember(db,user,body,fingerprint,result)

        @app.put('/api/forms/items/{id}/access')
        def access_change(id:str,body:AccessChange,request:Request,workspace:Workspace='school'):
            with self.context(request,workspace,True) as (db,user,snap):
                if user['role']!='admin': raise HTTPException(403,'Ответственных назначает администратор.')
                form=self.form(db,workspace,id);fingerprint,old=self.replay(db,user,body,'access:'+id)
                if old: return old
                version(form,body.version);recipients=[]
                for item in body.collaborators:
                    row=db.execute('SELECT * FROM accounts WHERE id=? AND active=1',(item.userId,)).fetchone()
                    candidate=self.access.public(row) if row else None
                    if not candidate or candidate['role'] not in STAFF or not enabled(candidate,snap,workspace): raise HTTPException(422,'Выберите действующих сотрудников этого пространства.')
                    recipients.append(candidate)
                db.execute('DELETE FROM school_form_access WHERE form_id=?',(id,))
                for item,candidate in zip(body.collaborators,recipients): db.execute('INSERT INTO school_form_access VALUES (?,?,?,?)',(id,item.userId,item.permission,permission_key(candidate)))
                result=self.save_form(db,form,user,'access_changed')
                self.notifications.deliver(db,recipients,workspace,form['version'],'forms.assigned')
                return self.remember(db,user,body,fingerprint,result)

        @app.get('/api/forms/items/{id}/preview')
        def preview(id:str,request:Request,workspace:Workspace='school'):
            with self.context(request,workspace) as (db,user,snap):
                form=self.form(db,workspace,id);self.require(db,form,user,True)
                recipients,users,fingerprint,unavailable=self.recipients(db,form,snap,workspace,user)
                roles={}
                for row in recipients: roles[row[3]]=roles.get(row[3],0)+1
                return dict(version=form['version'],audienceHash=fingerprint,accounts=len(users),responses=len(recipients),roles=roles,unavailable=unavailable)

        @app.post('/api/forms/items/{id}/transition')
        def transition(id:str,body:Transition,request:Request,workspace:Workspace='school'):
            with self.context(request,workspace,True) as (db,user,snap):
                form=self.form(db,workspace,id);self.require(db,form,user,True)
                fingerprint,old=self.replay(db,user,body,'transition:'+id)
                if old: return old
                version(form,body.version);doc=form['definition'];users=[]
                if body.action=='publish':
                    audience=doc['audience']
                    if form['status']!='draft' or not doc['questions'] or audience.get('preset','roles')=='roles' and not audience['roles']: raise HTTPException(422,'Для публикации заполните вопросы и аудиторию черновика.')
                    if doc['kind']=='consent' and not any(q['kind']=='consent' and q['required'] for q in doc['questions']): raise HTTPException(422,'Для согласия добавьте обязательный вопрос «Да / Нет».')
                    if doc['closesAt'] and datetime.fromisoformat(doc['closesAt'])<=now(): raise HTTPException(422,'Срок ответа уже прошёл.')
                    recipients,users,current_hash,_=self.recipients(db,form,snap,workspace,user)
                    if not recipients: raise HTTPException(422,'В выбранной аудитории нет действующих аккаунтов.')
                    if doc.get('anonymous') and len(recipients)<ANONYMOUS_MIN: raise HTTPException(422,f'Анонимный опрос нужен минимум для {ANONYMOUS_MIN} адресатов: иначе ответ можно угадать.')
                    if current_hash!=body.audienceHash: raise HTTPException(409,'Аудитория изменилась. Повторите предварительную проверку.')
                    db.executemany('INSERT INTO school_form_recipients VALUES (?,?,?,?,?,?,?)',[(id,*r) for r in recipients])
                    form['status']='open'
                elif body.action=='close':
                    if form['status']!='open': raise HTTPException(409,'Форма уже закрыта или не опубликована.')
                    form['status']='closed'
                elif body.action=='reopen':
                    if form['status'] not in ['open','closed']: raise HTTPException(409,'Эту форму нельзя открыть повторно.')
                    if doc.get('standing') and body.closesAt: raise HTTPException(422,'Постоянная форма принимает ответы без срока. Закройте её вручную.')
                    if body.closesAt and body.closesAt<=now(): raise HTTPException(422,'Выберите будущий срок.')
                    if body.closesAt and doc['opensAt'] and body.closesAt<=datetime.fromisoformat(doc['opensAt']): raise HTTPException(422,'Окончание должно быть позже начала приёма.')
                    doc['closesAt']=body.closesAt.isoformat() if body.closesAt else None
                    Definition.model_validate(doc);form['status']='open'
                else:
                    if form['status']=='archived': raise HTTPException(409,'Форма уже в архиве.')
                    form['status']='archived'
                result=self.save_form(db,form,user,body.action)
                if body.action=='publish': self.notifications.deliver(db,users,workspace,form['version'],'forms.published')
                return self.remember(db,user,body,fingerprint,result)

        @app.get('/api/forms/items/{id}/answer')
        def personal(id:str,request:Request,entityId:str='',workspace:Workspace='school'):
            with self.context(request,workspace) as (db,user,snap):
                form=self.form(db,workspace,id);self.require_recipient(db,form,user,snap,workspace,entityId)
                if form['definition'].get('anonymous'):
                    # The server cannot link a ballot to an answer, so the author sees only that they have answered.
                    voted=db.execute('SELECT 1 FROM school_form_ballots WHERE form_id=? AND user_id=? AND entity_id=?',(id,user['id'],entityId)).fetchone()
                    return dict(answer=dict(id='',version=1 if voted else 0,status='submitted' if voted else 'draft',answers={},heldAnswers={},comment='',updated=None,entityId=entityId),history=[],formVersion=form['version'],accepting=accepting(form))
                answer=self.answer(db,form,user,entityId)
                # Seats belong to the version staff saw, not to the private correction shown below: the applicant's own seat must stay recognisable while they edit.
                answer['heldAnswers']=answer['answers'] if answer['status'] in HOLDING else {}
                # The applicant continues from their private correction; staff never receive it.
                draft=answer.pop('draft',None);saved=answer.pop('draftUpdated',None);answer.pop('hidden',None)
                if draft is not None: answer['answers']=draft;answer['updated']=saved or answer['updated']
                history=[dict(version=d['version'],status=d['status'],updated=d['updated'],comment=d['comment']) for (document,) in db.execute('SELECT document FROM school_form_history WHERE form_id=? AND answer_id=? ORDER BY seq DESC LIMIT 30',(id,answer['id'])) for d in [json.loads(document)]]
                return dict(answer=answer,history=history,formVersion=form['version'],accepting=accepting(form))

        @app.put('/api/forms/items/{id}/answer')
        def answer_write(id:str,body:AnswerWrite,request:Request,workspace:Workspace='school'):
            with self.context(request,workspace,True) as (db,user,snap):
                form=self.form(db,workspace,id);context=self.require_recipient(db,form,user,snap,workspace,body.entityId)
                if form['definition'].get('anonymous'): return self.anonymous_answer(db,form,user,body)
                fingerprint,old=self.replay(db,user,body,'answer:'+id)
                if old: return old
                compatible(db,form,body.formVersion)
                answer=self.answer(db,form,user,body.entityId)
                # A returned application may be corrected after the deadline or a manual close: staff can still decide it.
                correcting=answer['status']=='returned' and body.action in ['save','submit'] and form['status'] in ['open','closed']
                if not accepting(form) and not correcting: raise HTTPException(409,'Приём ответов сейчас закрыт.')
                if answer['version']!=body.version: raise HTTPException(409,'Ответ изменился в другом окне. Загрузите свежую версию.')
                private=False
                if body.action=='withdraw':
                    if answer['status']!='submitted': raise HTTPException(409,'Отозвать можно только ещё не рассмотренный ответ.')
                    answer['status']='withdrawn'
                else:
                    if answer['status']=='submitted': raise HTTPException(409,'Сначала отзовите ответ или дождитесь возврата на исправление.')
                    validate_answers(form['definition'],body.answers,body.action=='submit')
                    if body.action=='submit':
                        reserve(db,form,answer,body.answers)
                        if form['definition'].get('standing'):
                            # The audience is live, so the account joins the recipient rows with its first submission: staff results list answers through them.
                            # A draft leaves no row, or the audience count would show staff that somebody started one.
                            db.execute('''INSERT INTO school_form_recipients(form_id,user_id,entity_id,permissions,role,name,entity_name) VALUES (?,?,?,?,?,?,?)
                                ON CONFLICT(form_id,user_id,entity_id) DO UPDATE SET permissions=excluded.permissions,role=excluded.role,name=excluded.name,entity_name=excluded.entity_name''',
                                (id,user['id'],body.entityId,permission_key(user),user['role'],user['name'],context['name'] if body.entityId else ''))
                    # A decided application is archived in history; a new submission starts the next cycle.
                    if body.action=='submit': answer.pop('draft',None);answer['answers']=body.answers;answer['status']='submitted';answer['comment']=''
                    # An unsent correction stays private; staff keep the version they reviewed.
                    elif answer['status'] in ['returned','approved','rejected']: answer['draft']=body.answers;private=True
                    else: answer['answers']=body.answers;answer['status']='draft';answer['comment']=''
                result=self.save_answer(db,form,user,answer,user['id'],body.action,private)
                if body.action=='submit':
                    reviewers=[u for u in self.delegates(db,form,snap,workspace) if u['id']!=user['id']]
                    self.notifications.deliver(db,reviewers,workspace,answer['version'],'forms.submitted',topic='forms.submitted:'+form['id'])
                return self.remember(db,user,body,fingerprint,result)

        @app.get('/api/forms/items/{id}/responses')
        def responses(id:str,request:Request,workspace:Workspace='school',after:str='',limit:int=Query(default=50,ge=1,le=100)):
            with self.context(request,workspace) as (db,user,snap):
                form=self.form(db,workspace,id);self.require(db,form,user)
                if form['definition'].get('anonymous'):
                    count=db.execute('SELECT count(*) FROM school_form_anonymous WHERE form_id=?',(id,)).fetchone()[0]
                    if withheld(count): return dict(items=[],nextAfter=None,withheld=True,minimum=ANONYMOUS_MIN,count=count)
                    # Random ids give an order that says nothing about when or by whom an answer was sent.
                    rows=db.execute('SELECT id,answers FROM school_form_anonymous WHERE form_id=? AND id>? ORDER BY id LIMIT ?',(id,after,limit+1)).fetchall()
                    return dict(items=[dict(id=row[0],answers=json.loads(row[1])) for row in rows[:limit]],nextAfter=rows[limit-1][0] if len(rows)>limit else None,withheld=False,count=count)
                rows=self.ordered(db,id,snap,"a.status!='draft'",after)[:limit+1];documents=self.documents(db,id,[r[1] for r in rows[:limit]]);mine=grant_ids(user,workspace,'student')
                items=[]
                for key,answer_id,class_name,name,entity_name,role,owner,entity in rows[:limit]:
                    answer=json.loads(documents[answer_id])
                    if answer['status']=='withdrawn': answer['answers']={}
                    # Unsent corrections and the saves behind them stay with the applicant: staff keep the version and time they saw.
                    answer.pop('draft',None);answer.pop('draftUpdated',None);answer['version']-=answer.pop('hidden',0)
                    # 'own' only hides the decision controls; user ids never leave the server.
                    items.append(answer|dict(name=name,entityName=entity_name,role=role,className=class_name,own=owner==user['id'] or bool(entity) and entity in mine))
                return dict(items=items,nextAfter=rows[limit-1][1] if len(rows)>limit else None)

        @app.post('/api/forms/items/{id}/responses/{answer_id}/review')
        def review(id:str,answer_id:str,body:Review,request:Request,workspace:Workspace='school'):
            with self.context(request,workspace,True) as (db,user,snap):
                form=self.form(db,workspace,id);self.require(db,form,user)
                fingerprint,old=self.replay(db,user,body,'review:'+id+':'+answer_id)
                if old: return old
                compatible(db,form,body.formVersion)
                if form['definition']['kind']!='application' or form['status']=='archived': raise HTTPException(409,'Решения доступны для неархивированных заявок.')
                row=db.execute('SELECT user_id,document FROM school_form_answers WHERE form_id=? AND id=?',(id,answer_id)).fetchone()
                if not row: raise HTTPException(404,'Ответ не найден.')
                answer=json.loads(row[1])
                if row[0]==user['id'] or answer['entityId'] and answer['entityId'] in grant_ids(user,workspace,'student'): raise HTTPException(403,'Свою заявку или заявку своего ребёнка рассматривает другой ответственный.')
                if answer['status']!='submitted' or answer['version']-answer.get('hidden',0)!=body.version: raise HTTPException(409,'Ответ уже изменён или отозван. Обновите список.')
                answer['status']=body.action;answer['comment']=body.comment
                result=self.save_answer(db,form,user,answer,row[0],body.action)
                account=db.execute('SELECT * FROM accounts WHERE id=? AND active=1',(row[0],)).fetchone()
                if account:
                    recipient=self.access.public(account)
                    if enabled(recipient,snap,workspace) and any(c['entityId']==answer['entityId'] for c in self.contexts(db,form,recipient,snap,workspace)):
                        # The reviewer's comment stays in the answer: the entry names the application and the decision only.
                        pupil=snap.entities['student'].get(answer['entityId']) if answer['entityId'] else None
                        decision={'approved':'одобрено','rejected':'отклонено','returned':'возвращено на исправление'}[body.action]
                        self.notifications.deliver(db,[recipient],workspace,answer['version'],'forms.reviewed',
                                                   target=dict(module='forms',recordId=answer_id,students=[dict(id=pupil.id,name=pupil.name)] if pupil else [],title=f"«{form['definition']['title']}»: {decision}"))
                return self.remember(db,user,body,fingerprint,result)

        @app.get('/api/forms/items/{id}/responses/{answer_id}/history')
        def review_history(id:str,answer_id:str,request:Request,workspace:Workspace='school'):
            with self.context(request,workspace) as (db,user,snap):
                form=self.form(db,workspace,id);self.require(db,form,user)
                if not db.execute("SELECT 1 FROM school_form_answers WHERE form_id=? AND id=? AND status!='draft'",(id,answer_id)).fetchone(): raise HTTPException(404,'Ответ не найден.')
                # Draft text and draft-save activity never enter the staff history.
                result=[]
                for document,action in db.execute("SELECT document,action FROM school_form_history WHERE form_id=? AND answer_id=? AND action!='save' ORDER BY seq DESC LIMIT 30",(id,answer_id)):
                    item=json.loads(document);result.append({k:item[k] for k in ['status','updated','comment']}|dict(version=item['version']-item.get('hidden',0),action=action))
                return result

        @app.get('/api/forms/items/{id}/summary')
        def summary(id:str,request:Request,workspace:Workspace='school'):
            with self.context(request,workspace) as (db,user,snap):
                form=self.form(db,workspace,id);self.require(db,form,user)
                if form['definition'].get('anonymous'):
                    count=db.execute('SELECT count(*) FROM school_form_anonymous WHERE form_id=?',(id,)).fetchone()[0]
                    if withheld(count): return dict(responses=count,questions=[],withheld=True)
                    total,questions=tally(form['definition'],(json.loads(answers) for answers, in db.execute('SELECT answers FROM school_form_anonymous WHERE form_id=?',(id,))))
                else:
                    total,questions=tally(form['definition'],(json.loads(document)['answers'] for document, in db.execute("SELECT document FROM school_form_answers WHERE form_id=? AND status NOT IN ('draft','withdrawn')",(id,))))
                return dict(responses=total,questions=questions)

        @app.get('/api/forms/items/{id}/seats')
        def seats_left(id:str,request:Request,workspace:Workspace='school'):
            with self.context(request,workspace) as (db,user,snap):
                form=self.form(db,workspace,id)
                if not self.permission(db,form,user) and not self.contexts(db,form,user,snap,workspace): raise HTTPException(404,'Форма недоступна.')
                return seats(db,form)

        @app.get('/api/forms/items/{id}/pending')
        def pending(id:str,request:Request,workspace:Workspace='school',offset:int=Query(default=0,ge=0),limit:int=Query(default=50,ge=1,le=100)):
            with self.context(request,workspace) as (db,user,snap):
                form=self.form(db,workspace,id);permission=self.require(db,form,user);doc=form['definition']
                anonymous=bool(doc.get('anonymous'))
                # A standing form is open to everybody and expects nothing from anybody: there is nobody to chase.
                if doc.get('standing'): return dict(applicable=False,anonymous=False,audience=0,answered=0,pendingCount=0,remindedToday=0,canRemind=False,items=[],nextOffset=None)
                rows,people=self.pending_people(db,form,snap,workspace)
                reminded={owner for owner, in db.execute('SELECT user_id FROM school_form_reminders WHERE form_id=? AND day=?',(id,local_day().isoformat()))}
                classes,pupils=snap.entities['class_'],snap.entities['student']
                def rank(row):
                    account,entity,role,pupil=row;group=classes.get(pupils[entity].classId) if entity in pupils else None;label=group.name if group else ''
                    return (0 if group else 1,natural(label),natural(pupil or account['name']),natural(account['name']),account['id'],entity),label
                ranked=sorted(((rank(r),r) for r in people),key=lambda r:r[0][0])
                # An anonymous survey shows how many have answered and never who is left: the list would shrink next to the answers.
                page=[] if anonymous else ranked[offset:offset+limit]
                return dict(applicable=True,anonymous=anonymous,audience=len(rows),answered=len(rows)-len(people),pendingCount=len(people),
                            remindedToday=len({r[0]['id'] for r in people if r[0]['id'] in reminded}),canRemind=accepting(form) and permission in ['admin','manage'],
                            items=[dict(name=r[0]['name'],role=r[2],entityName=r[3],className=key[1],remindedToday=r[0]['id'] in reminded) for key,r in page],
                            nextOffset=offset+limit if not anonymous and len(ranked)>offset+limit else None)

        @app.post('/api/forms/items/{id}/remind')
        def remind(id:str,body:Reminder,request:Request,workspace:Workspace='school'):
            with self.context(request,workspace,True) as (db,user,snap):
                form=self.form(db,workspace,id);self.require(db,form,user,True)
                fingerprint,old=self.replay(db,user,body,'remind:'+id)
                if old: return old
                if form['definition'].get('standing'): raise HTTPException(409,'Постоянная форма не ждёт ответа от всех.')
                if not accepting(form): raise HTTPException(409,'Приём закрыт: напоминать не о чем.')
                today=local_day();day=today.isoformat()
                _,people=self.pending_people(db,form,snap,workspace)
                reminded={owner for owner, in db.execute('SELECT user_id FROM school_form_reminders WHERE form_id=? AND day=?',(id,day))}
                # One entry per account naming only its unanswered children; whoever was already reminded today is skipped, whoever pressed the button.
                accounts={};children={}
                for account,entity,role,pupil in people:
                    if account['id'] in reminded: continue
                    accounts[account['id']]=account;children.setdefault(account['id'],[])
                    if entity: children[account['id']].append(dict(id=entity,name=pupil))
                skipped=len({r[0]['id'] for r in people})-len(accounts)
                db.executemany('INSERT INTO school_form_reminders(form_id,user_id,day,created) VALUES (?,?,?,?)',[(id,user_id,day,now().isoformat()) for user_id in accounts])
                db.execute('DELETE FROM school_form_reminders WHERE day<?',((today-timedelta(days=30)).isoformat(),))
                if accounts:
                    self.notifications.deliver(db,list(accounts.values()),workspace,form['version'],'forms.reminder',topic='forms.reminder:'+id,
                                               target=lambda u:dict(module='forms',recordId=id,students=children[u['id']],title=f"Напоминание: ответьте на «{form['definition']['title']}»"))
                self.access.record(db,'forms.reminded',workspace,form['version'],detail=str(len(accounts)),principal=user)
                return self.remember(db,user,body,fingerprint,dict(reminded=len(accounts),skipped=skipped))

        @app.get('/api/forms/items/{id}/export')
        def export(id:str,request:Request,workspace:Workspace='school'):
            with self.context(request,workspace) as (db,user,snap):
                form=self.form(db,workspace,id);self.require(db,form,user);anonymous=bool(form['definition'].get('anonymous'))
                if anonymous and withheld(db.execute('SELECT count(*) FROM school_form_anonymous WHERE form_id=?',(id,)).fetchone()[0]):
                    raise HTTPException(409,f'Результаты анонимного опроса откроются, когда ответят не менее {ANONYMOUS_MIN} человек.')
                output=tempfile.SpooledTemporaryFile(max_size=512*1024,mode='w+',encoding='utf-8',newline='')
                writer=csv.writer(output);questions=form['definition']['questions']
                def cell(value):
                    text='; '.join(value) if isinstance(value,list) else 'Да' if value is True else 'Нет' if value is False else '' if value is None else str(value)
                    return "'"+text if text.lstrip().startswith(('=','+','-','@')) else text
                try:
                    output.write('\ufeff')
                    if anonymous:
                        # Answers only, in the order of their random ids: no sender, class, status or time.
                        writer.writerow([cell(q['title']) for q in questions])
                        for answers, in db.execute('SELECT answers FROM school_form_anonymous WHERE form_id=? ORDER BY id',(id,)):
                            given=json.loads(answers);writer.writerow([cell(given.get(q['id'])) for q in questions])
                    else:
                        writer.writerow(['Отправитель','Ученик','Класс','Роль','Статус','Изменено','Комментарий',*[cell(q['title']) for q in questions]])
                        rows=self.ordered(db,id,snap,"a.status NOT IN ('draft','withdrawn')")
                        for start in range(0,len(rows),500):
                            page=rows[start:start+500];documents=self.documents(db,id,[r[1] for r in page])
                            for key,answer_id,class_name,name,entity_name,role,owner,entity in page:
                                answer=json.loads(documents[answer_id])
                                writer.writerow([cell(v) for v in [name,entity_name,class_name,role,answer['status'],answer['updated'],answer['comment'],*[answer['answers'].get(q['id']) for q in questions]]])
                    output.seek(0)
                except BaseException:
                    output.close();raise
                def chunks():
                    try:
                        while chunk:=output.read(65536): yield chunk
                    finally: output.close()
                return StreamingResponse(chunks(),media_type='text/csv; charset=utf-8',headers={'Content-Disposition':'attachment; filename="school-form.csv"','Cache-Control':'no-store'},background=BackgroundTask(output.close))
