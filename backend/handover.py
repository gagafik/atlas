"""School-controlled adult approvals and accountable physical child handovers."""
from contextlib import closing, contextmanager
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import hashlib
import json
import sqlite3
from uuid import uuid4
from fastapi import HTTPException
from .access import now
from .handover_models import STAFF, PENDING
from .notifications import encoded, permission_key
from .portal_cache import PortalSnapshots
from .school_forms import enabled, grant_ids

ZONE = ZoneInfo('Asia/Qyzylorda')
def today(): return now().astimezone(ZONE).date()
def stamp(): return now().isoformat()
def fingerprint(value): return hashlib.sha256(encoded(value).encode()).hexdigest()
def version(row, expected):
    if row['version'] != expected: raise HTTPException(409, 'Запись изменилась. Обновите страницу.')
def require_reason(reason):
    if not reason.strip(): raise HTTPException(422, 'Укажите основание действия школы.')


def tables(db):
    db.executescript('''
    CREATE TABLE IF NOT EXISTS handover_config(workspace TEXT PRIMARY KEY,version INTEGER,document TEXT);
    CREATE TABLE IF NOT EXISTS handover_adults(seq INTEGER PRIMARY KEY AUTOINCREMENT,id TEXT UNIQUE,workspace TEXT,student_id TEXT,sponsor_id TEXT,sponsor_permissions TEXT,version INTEGER,status TEXT,created TEXT,updated TEXT,document TEXT);
    CREATE INDEX IF NOT EXISTS handover_adult_child ON handover_adults(workspace,student_id,status,seq);
    CREATE TABLE IF NOT EXISTS handover_pickups(seq INTEGER PRIMARY KEY AUTOINCREMENT,id TEXT UNIQUE,workspace TEXT,student_id TEXT,adult_id TEXT,adult_version INTEGER,creator_id TEXT,creator_permissions TEXT,version INTEGER,status TEXT,day TEXT,expected_time TEXT,location_id TEXT,created TEXT,updated TEXT,completed_at TEXT,returned_at TEXT,document TEXT);
    CREATE UNIQUE INDEX IF NOT EXISTS handover_one_departure ON handover_pickups(workspace,student_id,day) WHERE status IN ('requested','arrived','ready','completed');
    CREATE INDEX IF NOT EXISTS handover_pickup_day ON handover_pickups(workspace,day,status,seq);
    CREATE INDEX IF NOT EXISTS handover_pickup_child ON handover_pickups(workspace,student_id,day,seq);
    CREATE INDEX IF NOT EXISTS handover_pickup_adult ON handover_pickups(adult_id,status);
    CREATE TABLE IF NOT EXISTS handover_history(seq INTEGER PRIMARY KEY AUTOINCREMENT,workspace TEXT,student_id TEXT,adult_id TEXT,pickup_id TEXT,created TEXT,actor_id TEXT,actor_name TEXT,action TEXT,reason TEXT,document TEXT);
    CREATE INDEX IF NOT EXISTS handover_history_child ON handover_history(workspace,student_id,seq);
    CREATE INDEX IF NOT EXISTS handover_history_pickup ON handover_history(workspace,pickup_id,seq);
    CREATE TABLE IF NOT EXISTS handover_requests(seq INTEGER PRIMARY KEY AUTOINCREMENT,user_id TEXT,request_id TEXT,fingerprint TEXT,result TEXT,UNIQUE(user_id,request_id));
    CREATE INDEX IF NOT EXISTS handover_request_owner ON handover_requests(user_id,seq);
    ''')


class Handover:
    def __init__(self, access, notifications, state_stamp):
        self.access, self.notifications = access, notifications
        self.snapshots = PortalSnapshots(access.read, state_stamp)

    def prepare(self):
        with closing(self.access.read_connect()) as db:
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='handover_request_owner'").fetchone(): return
        with self.access.lock, self.access.connect() as db: tables(db)

    @contextmanager
    def context(self, request, workspace, write=False):
        user = request.state.user
        if not user: raise HTTPException(403, 'Выдача ребёнка доступна после входа.')
        snap = self.snapshots.get(workspace)
        if not enabled(user, snap, workspace): raise HTTPException(403, 'Нет доступа к этому пространству.')
        self.prepare()
        def check(db):
            row = db.execute('SELECT * FROM accounts WHERE id=? AND active=1', (user['id'],)).fetchone()
            rev = db.execute('SELECT revision FROM workspaces WHERE id=?', (workspace,)).fetchone()
            if not row or self.access.public(row) != user or not rev or rev[0] != snap.state.revision:
                raise HTTPException(409, 'Доступ или состав школы изменились. Обновите страницу.')
            db.row_factory = sqlite3.Row
        if write:
            with self.access.lock, self.access.connect() as db:
                db.execute('BEGIN IMMEDIATE'); check(db); yield db, user, snap
        else:
            with closing(self.access.read_connect()) as db:
                db.execute('BEGIN'); check(db); yield db, user, snap

    def config(self, db, w):
        row = db.execute('SELECT * FROM handover_config WHERE workspace=?', (w,)).fetchone()
        return dict(version=row['version'], **json.loads(row['document'])) if row else dict(version=0, enabled=False, parentRequests=True, advanceDays=30, policy='', locations=[dict(id='main', name='Основной выход')], delegates=[])

    def require_enabled(self, db, w):
        cfg = self.config(db, w)
        if not cfg['enabled']: raise HTTPException(409, 'Школа ещё не включила выдачу через Атлас или временно приостановила её.')
        return cfg

    def permission(self, db, u, w):
        if u['role'] == 'admin': return dict(role='manager', classIds=[])
        if u['role'] not in STAFF: return None
        return next((x for x in self.config(db, w)['delegates'] if x['userId'] == u['id'] and x['permissions'] == permission_key(u)), None)

    def students(self, db, u, snap, w, active=True):
        p = self.permission(db, u, w)
        if p: return {s.id for s in snap.state.students if (s.active or not active) and (not p['classIds'] or s.classId in p['classIds'])}
        if u['role'] == 'parent':
            pupils = snap.entities['student']
            return {id for id in grant_ids(u,w,'student') if id in pupils and pupils[id].active}
        return set()

    def child(self, db, u, snap, w, id, *, staff=False, manager=False, active=True):
        p = self.permission(db, u, w)
        if id not in self.students(db, u, snap, w, active) or (staff and not p) or (manager and (p or {}).get('role') != 'manager'):
            raise HTTPException(404, 'Запись ребёнка недоступна.')
        return snap.entities['student'][id]

    def parent(self, db, snap, w, id, student):
        row = db.execute("SELECT * FROM accounts WHERE id=? AND role='parent' AND active=1", (id,)).fetchone()
        u = self.access.public(row) if row else None
        if not u or student not in self.students(db, u, snap, w): return None
        return u

    def row(self, db, w, id, kind):
        table = 'handover_adults' if kind == 'adult' else 'handover_pickups'
        row = db.execute('SELECT * FROM '+table+' WHERE workspace=? AND id=?', (w, id)).fetchone()
        if not row: raise HTTPException(404, 'Запись не найдена.')
        return {k: row[k] for k in row.keys() if k != 'document'} | dict(detail=json.loads(row['document']))

    def history(self, db, u, w, action, student, reason='', document=None, adult='', pickup=''):
        db.execute('INSERT INTO handover_history(workspace,student_id,adult_id,pickup_id,created,actor_id,actor_name,action,reason,document) VALUES (?,?,?,?,?,?,?,?,?,?)',
                   (w, student, adult, pickup, stamp(), u['id'], u['name'], action, reason, encoded(document or {})))

    def replay(self, db, u, body, operation):
        key = fingerprint([operation, body.model_dump(mode='json')])
        row = db.execute('SELECT fingerprint,result FROM handover_requests WHERE user_id=? AND request_id=?', (u['id'], body.requestId)).fetchone()
        if row:
            if key != row['fingerprint']: raise HTTPException(409, 'Идентификатор уже использован другим действием.')
            return key, json.loads(row['result'])
        return key, None

    def remember(self, db, u, body, key, result):
        db.execute('INSERT INTO handover_requests(user_id,request_id,fingerprint,result) VALUES (?,?,?,?)', (u['id'], body.requestId, key, encoded(result)))
        return result

    def notify(self, db, u, snap, w, student, action, staff=False):
        recipients = {p['id']: p for p in self.notifications.granted_users(db, w, 'student', [student], ['parent']) if student in self.students(db, p, snap, w)}
        if staff:
            for row in db.execute("SELECT * FROM accounts WHERE active=1 AND role IN ('admin','editor','teacher','staff')"):
                p = self.access.public(row)
                if enabled(p,snap,w) and self.permission(db, p, w) and student in self.students(db, p, snap, w): recipients[p['id']] = p
        recipients.pop(u['id'], None)
        self.notifications.deliver(db, list(recipients.values()), w, snap.state.revision, 'handover:' + action)

    def adult_problem(self, db, snap, w, adult, day):
        if adult['status'] != 'approved': return 'Школа не подтвердила этого взрослого.'
        d = adult['detail']
        if not (d['startDate'] <= day <= d['endDate']): return 'Разрешение не действует в выбранный день.'
        sponsor = self.parent(db, snap, w, adult['sponsor_id'], adult['student_id'])
        if not sponsor: return 'Связь родителя с ребёнком больше не действует. Нужна новая проверка школой.'
        return ''

    def public_adult(self, db, u, snap, w, adult):
        self.child(db, u, snap, w, adult['student_id'], active=False)
        result = {k: adult[k] for k in ['id','student_id','sponsor_id','version','status','created','updated']}
        d = dict(adult['detail']); d.pop('proposalReason', None)
        if not self.permission(db, u, w) and u['id'] != adult['sponsor_id']: d.pop('contact', None)
        result['detail'] = d; result['unavailableReason'] = self.adult_problem(db, snap, w, adult, today().isoformat())
        result['canRevoke'] = adult['status'] in {'proposed','approved'} and ((self.permission(db,u,w) or {}).get('role')=='manager' or u['id']==adult['sponsor_id'])
        return result

    def proposal(self, db, u, snap, w, body):
        self.require_enabled(db, w); pupil = self.child(db, u, snap, w, body.studentId)
        if u['role'] != 'parent': self.child(db, u, snap, w, pupil.id, manager=True); require_reason(body.reason)
        sponsor_id = u['id'] if u['role'] == 'parent' else body.sponsorId
        if u['role'] == 'parent' and body.sponsorId and body.sponsorId != u['id']: raise HTTPException(403, 'Предлагайте взрослого от своего имени.')
        sponsor = self.parent(db, snap, w, sponsor_id, pupil.id)
        if not sponsor: raise HTTPException(422, 'Выберите родителя с действующей связью с ребёнком.')
        if body.endDate < today() or body.startDate > today() + timedelta(days=366): raise HTTPException(422, 'Проверьте срок разрешения.')
        if db.execute("SELECT count(*) FROM handover_adults WHERE workspace=? AND student_id=? AND status IN ('proposed','approved')", (w,pupil.id)).fetchone()[0] >= 30:
            raise HTTPException(413, 'Отзовите ненужные разрешения перед добавлением новых.')
        d = dict(kind=body.kind, name=sponsor['name'] if body.kind=='parent' else body.name, relationship='Родитель' if body.kind=='parent' else body.relationship,
                 contact=body.contact, startDate=body.startDate.isoformat(), endDate=body.endDate.isoformat(), studentName=pupil.name, classId=pupil.classId,
                 className=snap.entities['class_'][pupil.classId].name, sponsorName=sponsor['name'], proposalReason=body.reason, consentConfirmed=True)
        id = uuid4().hex
        # sponsor_permissions is stored for compatibility and no longer compared: parent() checks the live link to the child.
        db.execute("INSERT INTO handover_adults(id,workspace,student_id,sponsor_id,sponsor_permissions,version,status,created,updated,document) VALUES (?,?,?,?,?,1,'proposed',?,?,?)", (id,w,pupil.id,sponsor_id,permission_key(sponsor),stamp(),stamp(),encoded(d)))
        self.history(db,u,w,'adult_proposed',pupil.id,body.reason,dict(kind=body.kind),adult=id)
        self.notify(db,u,snap,w,pupil.id,'adult_proposed',True)
        return dict(id=id, version=1)

    def decide_adult(self, db, u, snap, w, adult, body):
        version(adult,body.version); require_reason(body.reason); student=adult['student_id']; action=body.action
        if action != 'revoke' or u['role'] != 'parent': self.child(db,u,snap,w,student,manager=True,active=False)
        elif u['id'] != adult['sponsor_id']: raise HTTPException(403, 'Отозвать чужое предложение может школа.')
        if action in {'approve','reject'} and adult['status']!='proposed': raise HTTPException(409, 'Решение уже принято. Для изменений создайте новое предложение.')
        if action=='approve':
            self.require_enabled(db,w); self.child(db,u,snap,w,student,manager=True)
            if not body.consentChecked or not body.identityChecked: raise HTTPException(422, 'Подтвердите проверку согласия родителя и личности взрослого.')
            sponsor=self.parent(db,snap,w,adult['sponsor_id'],student)
            if not sponsor or adult['detail']['endDate']<today().isoformat():
                raise HTTPException(409,'Срок или связь родителя с ребёнком изменились. Нужно новое предложение.')
        if action=='revoke' and adult['status'] not in {'proposed','approved'}: raise HTTPException(409,'Разрешение уже недействительно.')
        status={'approve':'approved','reject':'rejected','revoke':'revoked'}[action]
        db.execute('UPDATE handover_adults SET status=?,version=version+1,updated=? WHERE id=?',(status,stamp(),adult['id']))
        self.history(db,u,w,'adult_'+action,student,body.reason,dict(before=adult['status'],after=status,consentChecked=body.consentChecked,identityChecked=body.identityChecked),adult=adult['id'])
        if action=='revoke':
            pending=db.execute("SELECT id FROM handover_pickups WHERE adult_id=? AND status IN ('requested','arrived','ready')",(adult['id'],)).fetchall()
            for row in pending:
                db.execute("UPDATE handover_pickups SET status='cancelled',version=version+1,updated=? WHERE id=?",(stamp(),row['id']))
                self.history(db,u,w,'cancel',student,'Разрешение взрослого отозвано: '+body.reason,adult=adult['id'],pickup=row['id'])
        self.notify(db,u,snap,w,student,'adult_updated',True)
        return dict(id=adult['id'],version=adult['version']+1,status=status)

    def pickup_problem(self, db, snap, w, pickup):
        adult=self.row(db,w,pickup['adult_id'],'adult')
        if adult['version'] != pickup['adult_version']: return 'Разрешение взрослого изменилось. Оформите новую заявку.'
        problem=self.adult_problem(db,snap,w,adult,pickup['day'])
        if problem: return problem
        row=db.execute('SELECT * FROM accounts WHERE id=? AND active=1',(pickup['creator_id'],)).fetchone()
        creator=self.access.public(row) if row else None
        if not creator or not enabled(creator,snap,w): return 'Автор заявки больше не имеет доступа.'
        if creator['role']=='parent' and pickup['student_id'] not in self.students(db,creator,snap,w): return 'Связь родителя с ребёнком больше не действует.'
        if creator['role']!='parent' and ((self.permission(db,creator,w) or {}).get('role')!='manager' or pickup['student_id'] not in self.students(db,creator,snap,w)):
            return 'Назначение сотрудника, оформившего заявку, больше не действует.'
        return ''

    def public_pickup(self, db, u, snap, w, row):
        self.child(db,u,snap,w,row['student_id'],active=False)
        result={k:row[k] for k in ['id','student_id','adult_id','adult_version','version','status','day','expected_time','location_id','created','updated','completed_at','returned_at','detail']}
        result['unavailableReason']=self.pickup_problem(db,snap,w,row) if row['status'] in PENDING else ''
        staff=bool(self.permission(db,u,w));own=row['creator_id']==u['id']
        result['canArrive']=staff or own
        result['canCancel']=staff or own or self.row(db,w,row['adult_id'],'adult')['sponsor_id']==u['id']
        if row['status'] in PENDING and row['day']<today().isoformat(): result['displayStatus']='expired'
        else: result['displayStatus']=row['status']
        return result

    def create_pickup(self, db, u, snap, w, body):
        cfg=self.require_enabled(db,w); adult=self.row(db,w,body.adultId,'adult');pupil=self.child(db,u,snap,w,adult['student_id'])
        if u['role']=='parent':
            if not cfg['parentRequests']: raise HTTPException(403,'Заявки оформляет школа.')
        else:self.child(db,u,snap,w,pupil.id,manager=True)
        version(adult,body.adultVersion)
        if body.day<today() or body.day>today()+timedelta(days=cfg['advanceDays']): raise HTTPException(422,'Проверьте дату заявки.')
        problem=self.adult_problem(db,snap,w,adult,body.day.isoformat())
        if problem: raise HTTPException(409,problem)
        point=next((p for p in cfg['locations'] if p['id']==body.locationId),None)
        if not point:raise HTTPException(409,'Место выдачи изменилось. Обновите страницу.')
        if db.execute("SELECT 1 FROM handover_pickups WHERE workspace=? AND student_id=? AND day=? AND status IN ('requested','arrived','ready','completed')",(w,pupil.id,body.day.isoformat())).fetchone():
            raise HTTPException(409,'У ребёнка уже есть заявка или подтверждённая выдача в этот день.')
        id=uuid4().hex;d=dict(studentName=pupil.name,classId=pupil.classId,className=snap.entities['class_'][pupil.classId].name,adultName=adult['detail']['name'],relationship=adult['detail']['relationship'],locationName=point['name'],note=body.note)
        # creator_permissions is stored for compatibility and no longer compared: pickup_problem() checks the creator's live link or delegation.
        db.execute("INSERT INTO handover_pickups(id,workspace,student_id,adult_id,adult_version,creator_id,creator_permissions,version,status,day,expected_time,location_id,created,updated,completed_at,returned_at,document) VALUES (?,?,?,?,?,?,?,1,'requested',?,?,?,?,?,NULL,NULL,?)",
                   (id,w,pupil.id,adult['id'],adult['version'],u['id'],permission_key(u),body.day.isoformat(),body.expectedTime,body.locationId,stamp(),stamp(),encoded(d)))
        self.history(db,u,w,'requested',pupil.id,body.note,adult=adult['id'],pickup=id);self.notify(db,u,snap,w,pupil.id,'requested',True)
        return dict(id=id,version=1)

    def change_pickup(self, db, u, snap, w, row, body):
        version(row,body.version);action=body.action;student=row['student_id'];status=row['status']
        self.child(db,u,snap,w,student,active=False)
        if action in {'ready','note','returned'}: self.child(db,u,snap,w,student,staff=True,manager=action in {'note','returned'},active=action=='ready')
        if action=='cancel':
            require_reason(body.reason)
            if not self.permission(db,u,w) and row['creator_id']!=u['id'] and self.row(db,w,row['adult_id'],'adult')['sponsor_id']!=u['id']:
                raise HTTPException(403,'Отменить эту заявку может её автор, разрешивший взрослого родитель или школа.')
        if action in {'arrive','ready'}:
            self.require_enabled(db,w);self.child(db,u,snap,w,student)
            if row['day']!=today().isoformat():raise HTTPException(409,'Это действие доступно в день выдачи.')
            problem=self.pickup_problem(db,snap,w,row)
            if problem:raise HTTPException(409,problem)
        if action=='arrive':
            if not self.permission(db,u,w) and row['creator_id']!=u['id']:raise HTTPException(403,'Прибытие отмечает автор заявки или школа.')
            if status!='requested':raise HTTPException(409,'Прибытие уже отмечено или заявка завершена.')
            status='arrived'
        elif action=='ready':
            if status not in {'requested','arrived'}:raise HTTPException(409,'Нельзя отметить готовность этой заявки.')
            status='ready'
        elif action=='cancel':
            if status not in PENDING:raise HTTPException(409,'Фактическую передачу нельзя отменить. Школа может зафиксировать возвращение ребёнка.')
            status='cancelled'
        elif action=='returned':
            require_reason(body.reason)
            if status!='completed' or not body.childReturned or row['day']!=today().isoformat():raise HTTPException(422,'Подтвердите фактическое возвращение ребёнка в день выдачи.')
            status='returned'
        elif action=='note':require_reason(body.reason)
        db.execute('UPDATE handover_pickups SET version=version+1,status=?,updated=?,returned_at=? WHERE id=?',(status,stamp(),stamp() if action=='returned' else row['returned_at'],row['id']))
        self.history(db,u,w,action,student,body.reason,dict(before=row['status'],after=status),adult=row['adult_id'],pickup=row['id'])
        if action!='note':self.notify(db,u,snap,w,student,action,True)
        return dict(id=row['id'],version=row['version']+1,status=status)

    def review_transfer(self, db, u, snap, w, row, body, reviewed_at=None):
        self.child(db,u,snap,w,row['student_id'],staff=True);cfg=self.require_enabled(db,w);version(row,body.version)
        if row['status'] not in PENDING or row['day']!=today().isoformat():raise HTTPException(409,'Выдача доступна только для незавершённой заявки на сегодня.')
        if not body.identityChecked or not body.childPresent:raise HTTPException(422,'Подтвердите личную проверку взрослого и присутствие ребёнка.')
        if body.identityMethod=='school-procedure':require_reason(body.reason)
        if row['location_id'] not in {p['id'] for p in cfg['locations']}:raise HTTPException(409,'Место выдачи больше не действует. Оформите новую заявку.')
        problem=self.pickup_problem(db,snap,w,row)
        if problem:raise HTTPException(409,problem)
        checked=(reviewed_at or now()).astimezone(timezone.utc)
        if checked>now()+timedelta(seconds=2) or now()-checked>timedelta(minutes=5):raise HTTPException(409,'Проверка устарела. Повторите предпросмотр перед передачей.')
        adult=self.row(db,w,row['adult_id'],'adult');pupil=snap.entities['student'][row['student_id']]
        fields=body.model_dump(include={'version','reason','identityMethod','identityChecked','childPresent'},mode='json')
        token=fingerprint([row,adult,cfg,snap.state.revision,u['id'],u['version'],permission_key(u),today().isoformat(),fields,checked.isoformat()])
        return dict(previewHash=token,reviewedAt=checked.isoformat(),studentName=pupil.name,className=snap.entities['class_'][pupil.classId].name,adultName=adult['detail']['name'],relationship=adult['detail']['relationship'],locationName=next(p['name'] for p in cfg['locations'] if p['id']==row['location_id']),policy=cfg['policy'])

    def complete(self, db, u, snap, w, row, body):
        preview=self.review_transfer(db,u,snap,w,row,body,body.reviewedAt)
        if not body.handedOver:raise HTTPException(422,'Подтвердите, что ребёнок действительно передан этому взрослому.')
        if preview['previewHash']!=body.previewHash:raise HTTPException(409,'Условия изменились. Повторите проверку перед передачей.')
        db.execute("UPDATE handover_pickups SET version=version+1,status='completed',updated=?,completed_at=? WHERE id=?",(stamp(),stamp(),row['id']))
        self.history(db,u,w,'completed',row['student_id'],body.reason,dict(identityMethod=body.identityMethod,identityChecked=True,childPresent=True,handedOver=True,reviewedAt=body.reviewedAt.isoformat(),policyVersion=self.config(db,w)['version']),adult=row['adult_id'],pickup=row['id'])
        self.notify(db,u,snap,w,row['student_id'],'completed',True)
        return dict(id=row['id'],version=row['version']+1,status='completed')

    def register(self, app, Workspace):
        from .handover_routes import register
        register(self,app,Workspace)
