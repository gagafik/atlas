"""School registers: visitor log, incident register and school inventory.

Records live beside the timetable and never change it. Rights are school-wide and
per register: an administrator names staff accounts and the registers each may keep.
An assignment is bound to the account's current role and grants (permission_key), so a
changed role or grant silently revokes it until an administrator saves it again.
"""
from contextlib import closing,contextmanager
from datetime import timezone
from zoneinfo import ZoneInfo
import hashlib,json,sqlite3
from fastapi import HTTPException
from .access import now
from .notifications import encoded,permission_key
from .portal_cache import PortalSnapshots
from .school_forms import enabled

STAFF={'admin','editor','teacher','staff'}
ELIGIBLE={'editor','teacher','staff'}
CAPABILITIES=['visitors','incidents','inventory']
ZONE=ZoneInfo('Asia/Qyzylorda')
DENIED=dict(visitors='Журнал посетителей доступен назначенным сотрудникам и администрации.',incidents='Журнал происшествий доступен администрации и сотрудникам с отдельным правом.',inventory='Инвентарь школы доступен назначенным ответственным и администрации.')
def stamp():return now().isoformat()
def today():return now().astimezone(ZONE).date()
def iso(value):return value.astimezone(timezone.utc).replace(microsecond=0).isoformat()
def local_day(value):return value.astimezone(ZONE).date().isoformat()
def version(row,value):
    if row['version']!=value:raise HTTPException(409,'Запись изменилась. Обновите данные перед действием.')
def cell(v):
    v=str(v)
    # Spreadsheet programs run text that starts with these characters as formulas.
    return "'"+v if v.lstrip().startswith(('=','+','-','@','\t','\r')) else v

def tables(db):
    db.executescript('''
    CREATE TABLE IF NOT EXISTS registers_config(workspace TEXT PRIMARY KEY,version INTEGER NOT NULL,document TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS registers_counters(workspace TEXT,kind TEXT,year INTEGER,last INTEGER NOT NULL,PRIMARY KEY(workspace,kind,year));
    CREATE TABLE IF NOT EXISTS registers_visitors(seq INTEGER PRIMARY KEY AUTOINCREMENT,id TEXT UNIQUE,workspace TEXT,version INTEGER,status TEXT,day TEXT,arrived TEXT,departed TEXT,badge TEXT,host_id TEXT,author_id TEXT,author_name TEXT,created TEXT,updated TEXT,search TEXT,document TEXT);
    CREATE UNIQUE INDEX IF NOT EXISTS registers_visitor_badge ON registers_visitors(workspace,badge) WHERE status='inside' AND badge!='';
    CREATE INDEX IF NOT EXISTS registers_visitor_day ON registers_visitors(workspace,day,seq);
    CREATE INDEX IF NOT EXISTS registers_visitor_status ON registers_visitors(workspace,status,seq);
    CREATE TABLE IF NOT EXISTS registers_incidents(seq INTEGER PRIMARY KEY AUTOINCREMENT,id TEXT UNIQUE,workspace TEXT,number TEXT,version INTEGER,status TEXT,kind TEXT,occurred_on TEXT,summary_published INTEGER NOT NULL DEFAULT 0,author_id TEXT,author_name TEXT,created TEXT,updated TEXT,search TEXT,document TEXT,UNIQUE(workspace,number));
    CREATE INDEX IF NOT EXISTS registers_incident_list ON registers_incidents(workspace,status,seq);
    CREATE TABLE IF NOT EXISTS registers_incident_people(incident_id TEXT,workspace TEXT,kind TEXT,person_id TEXT,PRIMARY KEY(incident_id,kind,person_id));
    CREATE INDEX IF NOT EXISTS registers_incident_person ON registers_incident_people(workspace,kind,person_id);
    CREATE TABLE IF NOT EXISTS registers_inventory(seq INTEGER PRIMARY KEY AUTOINCREMENT,id TEXT UNIQUE,workspace TEXT,number TEXT,number_key TEXT,version INTEGER,status TEXT,room_id TEXT,responsible_id TEXT,category TEXT,condition TEXT,created TEXT,updated TEXT,search TEXT,document TEXT,UNIQUE(workspace,number_key));
    CREATE INDEX IF NOT EXISTS registers_inventory_room ON registers_inventory(workspace,room_id,seq);
    CREATE INDEX IF NOT EXISTS registers_inventory_status ON registers_inventory(workspace,status,seq);
    CREATE TABLE IF NOT EXISTS registers_inventory_moves(seq INTEGER PRIMARY KEY AUTOINCREMENT,workspace TEXT,item_id TEXT,moved_on TEXT,created TEXT,actor_id TEXT,actor_name TEXT,reason TEXT,source TEXT,target TEXT);
    CREATE INDEX IF NOT EXISTS registers_inventory_moves_item ON registers_inventory_moves(workspace,item_id,seq);
    CREATE TABLE IF NOT EXISTS registers_history(seq INTEGER PRIMARY KEY AUTOINCREMENT,workspace TEXT,register TEXT,entity_id TEXT,actor_id TEXT,actor_name TEXT,created TEXT,action TEXT,reason TEXT,document TEXT);
    CREATE INDEX IF NOT EXISTS registers_history_entity ON registers_history(workspace,register,entity_id,seq);
    CREATE TABLE IF NOT EXISTS registers_requests(seq INTEGER PRIMARY KEY AUTOINCREMENT,user_id TEXT,request_id TEXT,fingerprint TEXT,result TEXT,UNIQUE(user_id,request_id));
    CREATE INDEX IF NOT EXISTS registers_requests_owner ON registers_requests(user_id,seq);
    ''')

class Registers:
    def __init__(self,access,notifications,stamp):
        self.access,self.notifications=access,notifications
        self.snapshots=PortalSnapshots(access.read,stamp)
    # Routes reach the clock only through these two, so tests can patch the module functions.
    def now(self):return now()
    def today(self):return today()
    def prepare(self):
        with closing(self.access.read_connect()) as db:
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='registers_requests_owner'").fetchone():return
        with self.access.lock,self.access.connect() as db:tables(db);self.notifications.tables(db)
    @contextmanager
    def context(self,request,workspace,write=False):
        user=request.state.user
        if not user:raise HTTPException(403,'Раздел доступен после входа.')
        snap=self.snapshots.get(workspace)
        if not enabled(user,snap,workspace):raise HTTPException(403,'Нет доступа к этому пространству.')
        self.prepare()
        def check(db):
            row=db.execute('SELECT * FROM accounts WHERE id=? AND active=1',(user['id'],)).fetchone();rev=db.execute('SELECT revision FROM workspaces WHERE id=?',(workspace,)).fetchone()
            if not row or self.access.public(row)!=user or not rev or rev[0]!=snap.state.revision:raise HTTPException(409,'Доступ или данные школы изменились. Обновите страницу.')
            db.row_factory=sqlite3.Row
        if write:
            with self.access.lock,self.access.connect() as db:db.execute('BEGIN IMMEDIATE');check(db);yield db,user,snap
        else:
            with closing(self.access.read_connect()) as db:db.execute('BEGIN');check(db);yield db,user,snap
    def account(self,db,id):
        row=db.execute('SELECT * FROM accounts WHERE id=? AND active=1',(id,)).fetchone()
        return self.access.public(row) if row else None
    def config(self,db,workspace):
        row=db.execute('SELECT * FROM registers_config WHERE workspace=?',(workspace,)).fetchone()
        if not row:return dict(version=0,assignments=[],familySummaries=False)
        doc=json.loads(row['document']);return dict(version=row['version'],assignments=doc['assignments'],familySummaries=doc['familySummaries'])
    def capabilities(self,db,user,snap,workspace,cfg=None):
        if user['role']=='admin':return set(CAPABILITIES)
        if user['role'] not in ELIGIBLE or not enabled(user,snap,workspace):return set()
        key=permission_key(user);cfg=cfg or self.config(db,workspace)
        return {c for a in cfg['assignments'] if a['accountId']==user['id'] and a['permissions']==key for c in a['capabilities']}
    def require(self,db,user,snap,workspace,cap):
        if cap not in self.capabilities(db,user,snap,workspace):raise HTTPException(403,DENIED[cap])
    def staff_person(self,db,snap,workspace,id):
        a=self.account(db,id) if id else None
        return dict(id=a['id'],name=a['name']) if a and a['role'] in STAFF and enabled(a,snap,workspace) else None
    def staff_list(self,db,snap,workspace):
        result=[]
        for raw in db.execute("SELECT * FROM accounts WHERE active=1 AND role IN ('admin','editor','teacher','staff') ORDER BY name,id"):
            a=self.access.public(raw)
            if enabled(a,snap,workspace):result.append(dict(id=a['id'],name=a['name']))
        return result
    def history(self,db,workspace,register,entity,user,action,reason,document):
        db.execute('INSERT INTO registers_history(workspace,register,entity_id,actor_id,actor_name,created,action,reason,document) VALUES (?,?,?,?,?,?,?,?,?)',(workspace,register,entity,user['id'],user['name'],stamp(),action,reason,encoded(document)))
    def replay(self,db,user,body,operation):
        key=hashlib.sha256(encoded([operation,body.model_dump(mode='json')]).encode()).hexdigest()
        r=db.execute('SELECT fingerprint,result FROM registers_requests WHERE user_id=? AND request_id=?',(user['id'],body.requestId)).fetchone()
        if r:
            if r['fingerprint']!=key:raise HTTPException(409,'Идентификатор уже использован другим действием.')
            return key,json.loads(r['result'])
        return key,None
    def remember(self,db,user,body,key,result):
        db.execute('INSERT INTO registers_requests(user_id,request_id,fingerprint,result) VALUES (?,?,?,?)',(user['id'],body.requestId,key,encoded(result)))
        db.execute('DELETE FROM registers_requests WHERE user_id=? AND seq NOT IN (SELECT seq FROM registers_requests WHERE user_id=? ORDER BY seq DESC LIMIT 100)',(user['id'],user['id']))
        return result
    def counter(self,db,workspace,kind):
        # The counter row changes inside the caller's transaction: a rollback leaves no gap.
        year=self.today().year
        db.execute('INSERT INTO registers_counters(workspace,kind,year,last) VALUES (?,?,?,1) ON CONFLICT(workspace,kind,year) DO UPDATE SET last=last+1',(workspace,kind,year))
        return f"{year}-{db.execute('SELECT last FROM registers_counters WHERE workspace=? AND kind=? AND year=?',(workspace,kind,year)).fetchone()[0]:04d}"
    def notify_capable(self,db,user,snap,workspace,cap,revision,action):
        cfg=self.config(db,workspace);recipients=[]
        for raw in db.execute("SELECT * FROM accounts WHERE active=1 AND role IN ('admin','editor','teacher','staff')"):
            u=self.access.public(raw)
            if u['id']!=user['id'] and enabled(u,snap,workspace) and cap in self.capabilities(db,u,snap,workspace,cfg):recipients.append(u)
        self.notifications.deliver(db,recipients,workspace,revision,action)
    def page(self,rows,limit,key='seq'):
        return rows[:limit],rows[limit-1][key] if len(rows)>limit else None
    def register(self,app,Workspace):
        from .registers_routes import register as routes
        from .registers_visitors import register as visitors
        from .registers_incidents import register as incidents
        from .registers_inventory import register as inventory
        routes(self,app,Workspace);visitors(self,app,Workspace);incidents(self,app,Workspace);inventory(self,app,Workspace)
