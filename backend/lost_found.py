"""Private school lost-property workflow. School records are read only."""
from contextlib import closing,contextmanager
from zoneinfo import ZoneInfo
import hashlib,json,sqlite3
from fastapi import HTTPException
from .access import now
from .notifications import encoded,permission_key
from .portal_cache import PortalSnapshots
from .school_forms import enabled,grant_ids

STAFF={'admin','editor','teacher','staff'}
def stamp():return now().isoformat()
def today():return now().astimezone(ZoneInfo('Asia/Qyzylorda')).date()
def version(row,value):
    if row['version']!=value:raise HTTPException(409,'Запись изменилась. Обновите данные перед действием.')

def tables(db):
    db.executescript('''
    CREATE TABLE IF NOT EXISTS lost_found_config(workspace TEXT PRIMARY KEY,version INTEGER,document TEXT);
    CREATE TABLE IF NOT EXISTS lost_found_items(seq INTEGER PRIMARY KEY AUTOINCREMENT,id TEXT UNIQUE,workspace TEXT,version INTEGER,status TEXT,created TEXT,updated TEXT,search TEXT,document TEXT);
    CREATE INDEX IF NOT EXISTS lost_found_space ON lost_found_items(workspace,status,seq);
    CREATE TABLE IF NOT EXISTS lost_found_claims(seq INTEGER PRIMARY KEY AUTOINCREMENT,id TEXT UNIQUE,workspace TEXT,item_id TEXT,user_id TEXT,permissions TEXT,student_id TEXT,version INTEGER,status TEXT,created TEXT,updated TEXT,document TEXT);
    CREATE UNIQUE INDEX IF NOT EXISTS lost_found_approved ON lost_found_claims(item_id) WHERE status='approved';
    CREATE UNIQUE INDEX IF NOT EXISTS lost_found_active_owner ON lost_found_claims(item_id,user_id) WHERE status IN ('pending','approved');
    CREATE INDEX IF NOT EXISTS lost_found_owner ON lost_found_claims(workspace,user_id,permissions,seq);
    CREATE INDEX IF NOT EXISTS lost_found_claim_item ON lost_found_claims(item_id,seq);
    CREATE TABLE IF NOT EXISTS lost_found_history(seq INTEGER PRIMARY KEY AUTOINCREMENT,workspace TEXT,item_id TEXT,claim_id TEXT,actor_id TEXT,actor_name TEXT,created TEXT,action TEXT,reason TEXT,document TEXT);
    CREATE INDEX IF NOT EXISTS lost_found_history_item ON lost_found_history(workspace,item_id,seq);
    CREATE TABLE IF NOT EXISTS lost_found_requests(seq INTEGER PRIMARY KEY AUTOINCREMENT,user_id TEXT,request_id TEXT,fingerprint TEXT,result TEXT,UNIQUE(user_id,request_id));
    CREATE INDEX IF NOT EXISTS lost_found_requests_owner ON lost_found_requests(user_id,seq);
    CREATE TABLE IF NOT EXISTS lost_found_files(id TEXT PRIMARY KEY,workspace TEXT,item_id TEXT,owner_id TEXT,filename TEXT,size INTEGER,created TEXT,status TEXT,sha256 TEXT,linked INTEGER DEFAULT 0);
    CREATE INDEX IF NOT EXISTS lost_found_file_item ON lost_found_files(workspace,item_id,status);
    CREATE INDEX IF NOT EXISTS lost_found_file_owner ON lost_found_files(owner_id,status,created);
    ''')

class LostFound:
    def __init__(self,access,notifications,stamp):
        self.access,self.notifications=access,notifications
        self.snapshots=PortalSnapshots(access.read,stamp)
    def prepare(self):
        with closing(self.access.read_connect()) as db:
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='lost_found_file_owner'").fetchone():return
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
        row=db.execute('SELECT * FROM lost_found_config WHERE workspace=?',(workspace,)).fetchone()
        return dict(version=row['version'],assignments=json.loads(row['document'])) if row else dict(version=0,assignments=[])
    def manager(self,db,user,workspace):
        return user['role']=='admin' or (user['role'] in STAFF and any(a['accountId']==user['id'] and a['permissions']==permission_key(user) for a in self.config(db,workspace)['assignments']))
    def require_manager(self,db,user,workspace):
        if not self.manager(db,user,workspace):raise HTTPException(403,'Действие доступно ответственному за находки.')
    def item(self,db,workspace,id):
        r=db.execute('SELECT * FROM lost_found_items WHERE workspace=? AND id=?',(workspace,id)).fetchone()
        if not r:raise HTTPException(404,'Находка не найдена.')
        return dict(r)|dict(data=json.loads(r['document']))
    def claim(self,db,workspace,id):
        r=db.execute('SELECT * FROM lost_found_claims WHERE workspace=? AND id=?',(workspace,id)).fetchone()
        if not r:raise HTTPException(404,'Заявка недоступна.')
        return dict(r)|dict(data=json.loads(r['document']))
    def owns(self,row,user,snap,workspace):
        if row['user_id']!=user['id'] or row['permissions']!=permission_key(user):return False
        if row['student_id']:
            p=snap.entities['student'].get(row['student_id'])
            return bool(user['role'] in ['parent','student'] and p and p.active and p.id in grant_ids(user,workspace,'student'))
        return user['role'] in STAFF
    def require_claim(self,db,row,user,snap,workspace):
        if not self.manager(db,user,workspace) and not self.owns(row,user,snap,workspace):raise HTTPException(404,'Заявка недоступна.')
    def current_owner(self,db,row,snap,workspace):
        owner=self.account(db,row['user_id'])
        return owner if owner and enabled(owner,snap,workspace) and self.owns(row,owner,snap,workspace) else None
    def public_item(self,row,manager=False):
        d={k:v for k,v in row['data'].items() if manager or k not in ['privateNote','pickupInstructions']}
        return {k:row[k] for k in ['id','version','status','created','updated']}|dict(data=d)
    def visible_item(self,db,row,user,snap,workspace):
        if self.manager(db,user,workspace) or row['status'] in ['open','reserved']:return
        rows=db.execute('SELECT * FROM lost_found_claims WHERE item_id=? AND user_id=?',(row['id'],user['id']))
        if not any(self.owns(r,user,snap,workspace) for r in rows):raise HTTPException(404,'Находка недоступна.')
    def public_claim(self,db,row,user,snap,workspace):
        manager=self.manager(db,user,workspace);item=self.item(db,workspace,row['item_id'])
        result={k:row[k] for k in ['id','version','status','created','updated']}|dict(data=row['data'],item=self.public_item(item,manager),ownerCurrent=bool(self.current_owner(db,row,snap,workspace)),canWithdraw=self.owns(row,user,snap,workspace) and row['status'] in ['pending','approved'])
        if manager:result['userId']=row['user_id'];result['studentId']=row['student_id']
        if row['status']=='approved' and (manager or self.owns(row,user,snap,workspace)):result['pickupInstructions']=item['data']['pickupInstructions']
        return result
    def history(self,db,workspace,user,item,claim,action,reason,document):
        db.execute('INSERT INTO lost_found_history(workspace,item_id,claim_id,actor_id,actor_name,created,action,reason,document) VALUES (?,?,?,?,?,?,?,?,?)',(workspace,item,claim,user['id'],user['name'],stamp(),action,reason,encoded(document)))
    def replay(self,db,user,body,operation):
        key=hashlib.sha256(encoded([operation,body.model_dump(mode='json')]).encode()).hexdigest()
        r=db.execute('SELECT fingerprint,result FROM lost_found_requests WHERE user_id=? AND request_id=?',(user['id'],body.requestId)).fetchone()
        if r:
            if r['fingerprint']!=key:raise HTTPException(409,'Идентификатор уже использован другим действием.')
            return key,json.loads(r['result'])
        return key,None
    def remember(self,db,user,body,key,result):
        db.execute('INSERT INTO lost_found_requests(user_id,request_id,fingerprint,result) VALUES (?,?,?,?)',(user['id'],body.requestId,key,encoded(result)))
        db.execute('DELETE FROM lost_found_requests WHERE user_id=? AND seq NOT IN (SELECT seq FROM lost_found_requests WHERE user_id=? ORDER BY seq DESC LIMIT 100)',(user['id'],user['id']))
        return result
    def notify(self,db,row,user,snap,workspace):
        target=self.current_owner(db,row,snap,workspace)
        if target and target['id']!=user['id']:self.notifications.deliver(db,[target],workspace,row['version'],'lost-found.changed')
    def notify_managers(self,db,user,snap,workspace,revision):
        recipients=[]
        for raw in db.execute("SELECT * FROM accounts WHERE active=1 AND role IN ('admin','editor','teacher','staff')"):
            u=self.access.public(raw)
            if u['id']!=user['id'] and enabled(u,snap,workspace) and self.manager(db,u,workspace):recipients.append(u)
        self.notifications.deliver(db,recipients,workspace,revision,'lost-found.claimed')
    def change_item(self,db,row,status):
        db.execute('UPDATE lost_found_items SET status=?,version=version+1,updated=? WHERE id=?',(status,stamp(),row['id']))
    def change_claim(self,db,row,status,user,reason,received=''):
        data=row['data']|dict(decision=reason,receivedBy=received,reviewerName=user['name'])
        db.execute('UPDATE lost_found_claims SET status=?,version=version+1,updated=?,document=? WHERE id=?',(status,stamp(),encoded(data),row['id']))
        row.update(status=status,version=row['version']+1,data=data)
        self.history(db,row['workspace'],user,row['item_id'],row['id'],status,reason,data)
    def register(self,app,Workspace):
        from .lost_found_routes import register
        from .lost_found_files import register as files
        register(self,app,Workspace);files(self,app,Workspace)
