"""Independent admissions cases with immutable submissions and delegated review."""
from contextlib import closing, contextmanager
from datetime import date, datetime
import hashlib, json, sqlite3
from fastapi import HTTPException
from .access import now, digest
from .notifications import encoded, permission_key
from .portal_cache import PortalSnapshots
from .school_forms import enabled
from .school_forms_models import validate_answers

STAFF = {'admin', 'editor', 'teacher', 'staff'}
COOKIE = 'atlas_applicant'

def stamp(): return now().isoformat()
def version(row, expected):
    if row['version'] != expected: raise HTTPException(409, 'Заявка или настройки изменились. Обновите раздел.')

def tables(db):
    db.executescript('''
    CREATE TABLE IF NOT EXISTS admission_campaigns(seq INTEGER PRIMARY KEY AUTOINCREMENT,id TEXT UNIQUE,workspace TEXT,version INTEGER,status TEXT,published INTEGER,created TEXT,document TEXT);
    CREATE INDEX IF NOT EXISTS admission_campaign_workspace ON admission_campaigns(workspace,status,seq);
    CREATE TABLE IF NOT EXISTS admission_reviewers(campaign_id TEXT,user_id TEXT,permissions TEXT,PRIMARY KEY(campaign_id,user_id));
    CREATE TABLE IF NOT EXISTS admission_families(id TEXT PRIMARY KEY,login TEXT UNIQUE,name TEXT,password TEXT,recovery TEXT,version INTEGER,created TEXT);
    CREATE TABLE IF NOT EXISTS admission_sessions(token TEXT PRIMARY KEY,user_id TEXT,created TEXT,expires TEXT);
    CREATE INDEX IF NOT EXISTS admission_session_owner ON admission_sessions(user_id,created);
    CREATE TABLE IF NOT EXISTS admission_attempts(seq INTEGER PRIMARY KEY AUTOINCREMENT,client TEXT,login TEXT,kind TEXT,created TEXT);
    CREATE INDEX IF NOT EXISTS admission_attempt_client ON admission_attempts(client,created);
    CREATE INDEX IF NOT EXISTS admission_attempt_login ON admission_attempts(login,created);
    CREATE TABLE IF NOT EXISTS admission_applications(seq INTEGER PRIMARY KEY AUTOINCREMENT,id TEXT UNIQUE,campaign_id TEXT,family_id TEXT,version INTEGER,status TEXT,created TEXT,updated TEXT,document TEXT,submitted TEXT,submitted_at TEXT,reply_until TEXT,duplicate_key TEXT,search TEXT);
    CREATE INDEX IF NOT EXISTS admission_application_family ON admission_applications(family_id,seq);
    CREATE INDEX IF NOT EXISTS admission_application_campaign ON admission_applications(campaign_id,status,seq);
    CREATE UNIQUE INDEX IF NOT EXISTS admission_child_once ON admission_applications(campaign_id,family_id,duplicate_key) WHERE duplicate_key IS NOT NULL AND status!='withdrawn';
    CREATE TABLE IF NOT EXISTS admission_history(seq INTEGER PRIMARY KEY AUTOINCREMENT,campaign_id TEXT,application_id TEXT,actor_id TEXT,actor_name TEXT,created TEXT,action TEXT,audience TEXT,message TEXT,document TEXT);
    CREATE INDEX IF NOT EXISTS admission_history_case ON admission_history(application_id,seq);
    CREATE TABLE IF NOT EXISTS admission_requests(seq INTEGER PRIMARY KEY AUTOINCREMENT,user_id TEXT,request_id TEXT,fingerprint TEXT,result TEXT,UNIQUE(user_id,request_id));
    CREATE INDEX IF NOT EXISTS admission_request_owner ON admission_requests(user_id,seq);
    CREATE TABLE IF NOT EXISTS admission_files(id TEXT PRIMARY KEY,application_id TEXT,owner_id TEXT,filename TEXT,size INTEGER,created TEXT,status TEXT,sha256 TEXT,linked INTEGER);
    CREATE INDEX IF NOT EXISTS admission_files_case ON admission_files(application_id,status);
    CREATE INDEX IF NOT EXISTS admission_files_owner ON admission_files(owner_id,status);
    ''')

def campaign_result(row):
    data = json.loads(row['document'])
    return dict(id=row['id'],version=row['version'],status=row['status'],published=bool(row['published']),data=data,
                accepting=row['status']=='open' and datetime.fromisoformat(data['opensAt']) <= now() < datetime.fromisoformat(data['closesAt']))

def case_result(row, family=False):
    return dict(id=row['id'],campaignId=row['campaign_id'],version=row['version'],status=row['status'],created=row['created'],updated=row['updated'],
                data=json.loads(row['document'] if family else row['submitted']),submittedAt=row['submitted_at'],replyUntil=row['reply_until'])

class Admissions:
    def __init__(self, access, stamp):
        self.access=access;self.snapshots=PortalSnapshots(access.read,stamp)

    def prepare(self):
        with closing(self.access.read_connect()) as db:
            if self.exists(db): return
        with self.access.lock,self.access.connect() as db: tables(db)

    def exists(self,db):return bool(db.execute("SELECT 1 FROM sqlite_master WHERE name='admission_files_owner'").fetchone())

    @contextmanager
    def database(self,write=False):
        if write:
            with self.access.lock,self.access.connect() as db:
                db.execute('BEGIN IMMEDIATE');db.row_factory=sqlite3.Row
                if not self.exists(db):raise HTTPException(404,'Приём заявок ещё не настроен.')
                yield db
        else:
            with closing(self.access.read_connect()) as db:
                db.execute('BEGIN');db.row_factory=sqlite3.Row
                if not self.exists(db):raise HTTPException(404,'Приём заявок ещё не настроен.')
                yield db

    @contextmanager
    def staff(self,request,workspace,write=False):
        u=request.state.user
        if not u or u['role'] not in STAFF:raise HTTPException(403,'Нужен доступ сотрудника приёмной комиссии.')
        snap=self.snapshots.get(workspace)
        if not enabled(u,snap,workspace):raise HTTPException(403,'Нет доступа к пространству.')
        self.prepare()
        with self.database(write) as db:
            row=db.execute('SELECT * FROM accounts WHERE id=? AND active=1',(u['id'],)).fetchone()
            revision=db.execute('SELECT revision FROM workspaces WHERE id=?',(workspace,)).fetchone()
            if not row or self.access.public(row)!=u or not revision or revision[0]!=snap.state.revision:raise HTTPException(409,'Доступ изменился. Обновите страницу.')
            yield db,u,snap

    @contextmanager
    def family(self,request,write=False):
        with self.database(write) as db:
            u=db.execute('SELECT f.* FROM admission_families f JOIN admission_sessions s ON s.user_id=f.id WHERE s.token=? AND s.expires>?',(digest(request.cookies.get(COOKIE,'')),stamp())).fetchone()
            if not u:raise HTTPException(401,'Войдите в кабинет поступающей семьи.')
            yield db,dict(u)

    def admin(self,u):
        if u['role']!='admin':raise HTTPException(403,'Это действие доступно администрации.')

    def campaign(self,db,id,workspace=None,public=False):
        row=db.execute('SELECT * FROM admission_campaigns WHERE id=?',(id,)).fetchone()
        if not row or workspace is not None and row['workspace']!=workspace or public and (not row['published'] or row['status']=='archived'):raise HTTPException(404,'Приёмная кампания недоступна.')
        return row

    def require_reviewer(self,db,u,campaign):
        if u['role']!='admin' and not db.execute('SELECT 1 FROM admission_reviewers WHERE campaign_id=? AND user_id=? AND permissions=?',(campaign['id'],u['id'],permission_key(u))).fetchone():raise HTTPException(403,'Администратор должен назначить вас в эту приёмную комиссию.')

    def application(self,db,id,family=None):
        row=db.execute('SELECT * FROM admission_applications WHERE id=?',(id,)).fetchone()
        if not row or family and row['family_id']!=family['id']:raise HTTPException(404,'Заявка недоступна.')
        return row

    def staff_case(self,db,u,workspace,id):
        row=self.application(db,id);campaign=self.campaign(db,row['campaign_id'],workspace)
        self.require_reviewer(db,u,campaign)
        if row['submitted'] is None:raise HTTPException(404,'Заявка ещё не подана.')
        return row,campaign

    def accepting(self,campaign):
        if not campaign_result(campaign)['accepting']:raise HTTPException(409,'Приём новых заявок сейчас закрыт.')

    def editable(self,row,campaign,submitting=False):
        if row['status'] not in ['draft','returned']:raise HTTPException(409,'Анкету можно менять в черновике или после запроса уточнений.')
        if campaign['status']=='archived':raise HTTPException(409,'Приёмная кампания в архиве.')
        if submitting:
            if row['status']=='returned':
                if not row['reply_until'] or datetime.fromisoformat(row['reply_until'])<=now():raise HTTPException(409,'Срок уточнений истёк. Обратитесь в школу.')
            else:self.accepting(campaign)

    def check_data(self,db,row,campaign,data,submitting):
        definition=json.loads(campaign['document']);validate_answers(definition,data['answers'],submitting)
        if data['grade'] is not None and data['grade'] not in definition['grades']:raise HTTPException(422,'Выберите доступную параллель.')
        if data['birthDate'] and date.fromisoformat(data['birthDate'])>=now().date():raise HTTPException(422,'Дата рождения должна быть в прошлом.')
        if data['email'] and ('@' not in data['email'] or any(c.isspace() for c in data['email'])):raise HTTPException(422,'Проверьте адрес электронной почты.')
        kinds={d['id'] for d in definition['documents']}
        for f in data['files']:
            if f['kind'] not in kinds:raise HTTPException(422,'Выберите вид документа из анкеты.')
            if not db.execute("SELECT 1 FROM admission_files WHERE id=? AND application_id=? AND status='ready'",(f['id'],row['id'])).fetchone():raise HTTPException(422,'Файл недоступен или принадлежит другой заявке.')
        if submitting:
            if not all([data['childName'],data['birthDate'],data['grade'] is not None,data['guardianName'],data['phone'],data['email'],data['consent']]):raise HTTPException(422,'Заполните сведения о ребёнке, представителе, контакты и подтвердите согласие.')
            if any(d['required'] and d['id'] not in {f['kind'] for f in data['files']} for d in definition['documents']):raise HTTPException(422,'Приложите все обязательные документы.')
            for q in definition['questions']:
                if q['kind']=='consent' and q['required'] and data['answers'].get(q['id']) is not True:raise HTTPException(422,'Подтвердите обязательные согласия.')

    def replay(self,db,u,body,operation):
        fingerprint=hashlib.sha256(encoded([operation,body.model_dump(mode='json')]).encode()).hexdigest()
        row=db.execute('SELECT fingerprint,result FROM admission_requests WHERE user_id=? AND request_id=?',(u['id'],str(body.requestId))).fetchone()
        if row and row['fingerprint']!=fingerprint:raise HTTPException(409,'Этот номер запроса уже использован для другого действия.')
        return fingerprint,json.loads(row['result']) if row else None

    def remember(self,db,u,body,key,result):
        db.execute('INSERT INTO admission_requests(user_id,request_id,fingerprint,result) VALUES (?,?,?,?)',(u['id'],str(body.requestId),key,encoded(result)))
        db.execute('DELETE FROM admission_requests WHERE user_id=? AND seq NOT IN (SELECT seq FROM admission_requests WHERE user_id=? ORDER BY seq DESC LIMIT 100)',(u['id'],u['id']))
        return result

    def history(self,db,u,campaign,case,action,audience,message='',document=None):
        db.execute('INSERT INTO admission_history(campaign_id,application_id,actor_id,actor_name,created,action,audience,message,document) VALUES (?,?,?,?,?,?,?,?,?)',(campaign,case,u['id'],u['name'],stamp(),action,audience,message,encoded(document) if document is not None else None))

    def register(self,app,Workspace):
        from .admission_routes import register
        from .admission_auth import register as auth
        from .admission_files import register as files
        register(self,app,Workspace);auth(self,app);files(self,app,Workspace)
