"""Physical school holdings and private circulation, independent of the journal."""
from contextlib import closing,contextmanager
from datetime import timedelta
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
def fingerprint(value):return hashlib.sha256(encoded(value).encode()).hexdigest()
def version(row,expected):
    if row['version']!=expected:raise HTTPException(409,'Данные библиотеки изменились. Обновите список.')

def tables(db):
    db.executescript('''
    CREATE TABLE IF NOT EXISTS library_config(workspace TEXT PRIMARY KEY,version INTEGER,document TEXT);
    CREATE TABLE IF NOT EXISTS library_titles(seq INTEGER PRIMARY KEY AUTOINCREMENT,id TEXT UNIQUE,workspace TEXT,version INTEGER,archived INTEGER,search TEXT,document TEXT);
    CREATE INDEX IF NOT EXISTS library_title_space ON library_titles(workspace,seq);
    CREATE TABLE IF NOT EXISTS library_copies(seq INTEGER PRIMARY KEY AUTOINCREMENT,id TEXT UNIQUE,workspace TEXT,title_id TEXT,version INTEGER,inventory TEXT,inventory_key TEXT,location TEXT,condition TEXT,status TEXT,UNIQUE(workspace,inventory_key));
    CREATE INDEX IF NOT EXISTS library_copy_title ON library_copies(workspace,title_id,seq);
    CREATE TABLE IF NOT EXISTS library_loans(seq INTEGER PRIMARY KEY AUTOINCREMENT,id TEXT UNIQUE,workspace TEXT,copy_id TEXT,version INTEGER,kind TEXT,borrower_id TEXT,class_id TEXT,borrower_name TEXT,status TEXT,issued TEXT,due TEXT,returned TEXT,renewals INTEGER,reminded TEXT,document TEXT);
    CREATE UNIQUE INDEX IF NOT EXISTS library_copy_open ON library_loans(copy_id) WHERE status IN ('active','lost');
    CREATE INDEX IF NOT EXISTS library_loan_owner ON library_loans(workspace,kind,borrower_id,seq);
    CREATE INDEX IF NOT EXISTS library_loan_state ON library_loans(workspace,status,due,seq);
    CREATE TABLE IF NOT EXISTS library_history(seq INTEGER PRIMARY KEY AUTOINCREMENT,workspace TEXT,entity TEXT,entity_id TEXT,actor_id TEXT,actor_name TEXT,created TEXT,action TEXT,reason TEXT,document TEXT);
    CREATE INDEX IF NOT EXISTS library_history_item ON library_history(workspace,entity,entity_id,seq);
    CREATE TABLE IF NOT EXISTS library_audits(seq INTEGER PRIMARY KEY AUTOINCREMENT,id TEXT UNIQUE,workspace TEXT,version INTEGER,title TEXT,status TEXT,created TEXT,closed TEXT);
    CREATE UNIQUE INDEX IF NOT EXISTS library_audit_open ON library_audits(workspace) WHERE status='open';
    CREATE TABLE IF NOT EXISTS library_audit_items(seq INTEGER PRIMARY KEY AUTOINCREMENT,audit_id TEXT,copy_id TEXT,version INTEGER,copy_version INTEGER,result TEXT,note TEXT,document TEXT,UNIQUE(audit_id,copy_id));
    CREATE INDEX IF NOT EXISTS library_audit_rows ON library_audit_items(audit_id,seq);
    CREATE TABLE IF NOT EXISTS library_requests(seq INTEGER PRIMARY KEY AUTOINCREMENT,user_id TEXT,request_id TEXT,fingerprint TEXT,result TEXT,UNIQUE(user_id,request_id));
    CREATE INDEX IF NOT EXISTS library_request_owner ON library_requests(user_id,seq);
    ''')

class Library:
    def __init__(self,access,notifications,stamp):
        self.access,self.notifications=access,notifications
        self.snapshots=PortalSnapshots(access.read,stamp)
    def prepare(self):
        with closing(self.access.read_connect()) as db:
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='library_request_owner'").fetchone():return
        with self.access.lock,self.access.connect() as db:tables(db);self.notifications.tables(db)
    @contextmanager
    def context(self,request,workspace,write=False):
        user=request.state.user
        if not user:raise HTTPException(403,'Библиотека доступна после входа.')
        snap=self.snapshots.get(workspace)
        if not enabled(user,snap,workspace):raise HTTPException(403,'Нет доступа к библиотеке этого пространства.')
        self.prepare()
        def check(db):
            row=db.execute('SELECT * FROM accounts WHERE id=? AND active=1',(user['id'],)).fetchone();rev=db.execute('SELECT revision FROM workspaces WHERE id=?',(workspace,)).fetchone()
            if not row or self.access.public(row)!=user or not rev or rev[0]!=snap.state.revision:raise HTTPException(409,'Доступ или школьные данные изменились. Обновите страницу.')
            db.row_factory=sqlite3.Row
        if write:
            with self.access.lock,self.access.connect() as db:db.execute('BEGIN IMMEDIATE');check(db);yield db,user,snap
        else:
            with closing(self.access.read_connect()) as db:db.execute('BEGIN');check(db);yield db,user,snap
    def admin(self,user):
        if user['role']!='admin':raise HTTPException(403,'Это действие выполняет администрация.')
    def config(self,db,workspace):
        row=db.execute('SELECT * FROM library_config WHERE workspace=?',(workspace,)).fetchone()
        return dict(version=row['version'],**json.loads(row['document'])) if row else dict(version=0,assignments=[],loanDays=14,maxRenewals=2,maxLoans=30)
    def scope(self,db,user,snap,workspace):
        if user['role']=='admin':return dict(all=True,classes=[])
        if user['role'] in STAFF:
            for a in self.config(db,workspace)['assignments']:
                if a['accountId']==user['id'] and a['permissions']==permission_key(user):return dict(all=a['wholeSchool'],classes=[c for c in a['classIds'] if c in snap.entities['class_']])
        return dict(all=False,classes=[])
    def catalog_manager(self,db,user,snap,workspace):
        if not self.scope(db,user,snap,workspace)['all']:raise HTTPException(403,'Изменять фонд может библиотекарь всей школы или администратор.')
    def account(self,db,id):
        row=db.execute('SELECT * FROM accounts WHERE id=? AND active=1',(id,)).fetchone()
        return self.access.public(row) if row else None
    def borrower(self,db,kind,id,snap,workspace):
        if kind=='student':
            p=snap.entities['student'].get(id)
            if not p or not p.active:raise HTTPException(422,'Ученик отсутствует или неактивен.')
            return dict(kind=kind,id=id,name=p.name,classId=p.classId,className=snap.entities['class_'][p.classId].name)
        p=self.account(db,id)
        if not p or p['role'] not in STAFF or not enabled(p,snap,workspace):raise HTTPException(422,'Сотрудник не имеет доступа к этому пространству.')
        return dict(kind=kind,id=id,name=p['name'],classId='',className='')
    def manage_borrower(self,scope,kind,id,snap):
        if scope['all']:return True
        p=snap.entities['student'].get(id) if kind=='student' else None
        return bool(p and p.active and p.classId in scope['classes'])
    def require_loan(self,db,row,user,snap,workspace,manage=False):
        scope=self.scope(db,user,snap,workspace)
        if self.manage_borrower(scope,row['kind'],row['borrower_id'],snap):return True
        if not manage:
            if row['kind']=='employee' and user['role'] in STAFF and user['id']==row['borrower_id']:return False
            p=snap.entities['student'].get(row['borrower_id']) if row['kind']=='student' else None
            if user['role'] in ['parent','student'] and p and p.active and p.id in grant_ids(user,workspace,'student'):return False
        raise HTTPException(404,'Выдача недоступна.')
    def title(self,db,workspace,id):
        row=db.execute('SELECT * FROM library_titles WHERE workspace=? AND id=?',(workspace,id)).fetchone()
        if not row:raise HTTPException(404,'Название не найдено.')
        return dict(row)|dict(data=json.loads(row['document']))
    def copy(self,db,workspace,id):
        row=db.execute('SELECT c.*,t.document AS title_document,t.version AS title_version,t.archived FROM library_copies c JOIN library_titles t ON t.id=c.title_id WHERE c.workspace=? AND c.id=?',(workspace,id)).fetchone()
        if not row:raise HTTPException(404,'Экземпляр не найден.')
        r=dict(row);r['title']=json.loads(r.pop('title_document'))
        loan=db.execute("SELECT id FROM library_loans WHERE copy_id=? AND status IN ('active','lost')",(id,)).fetchone();r['onLoan']=bool(loan)
        return r
    def loan(self,db,workspace,id):
        row=db.execute('SELECT * FROM library_loans WHERE workspace=? AND id=?',(workspace,id)).fetchone()
        if not row:raise HTTPException(404,'Выдача не найдена.')
        return dict(row)
    def public_loan(self,row,manage=False):
        doc=json.loads(row['document']);r={k:row[k] for k in ['id','version','kind','borrower_id','borrower_name','status','issued','due','returned','renewals']}
        r.update(copyId=row['copy_id'],borrowerId=r.pop('borrower_id'),borrowerName=r.pop('borrower_name'),title=doc['title'],inventory=doc['inventory'],conditionOut=doc['conditionOut'],conditionIn=doc.get('conditionIn'),overdue=row['status']=='active' and row['due']<today().isoformat(),canManage=manage)
        return r
    def history(self,db,workspace,user,entity,id,action,reason,document):
        db.execute('INSERT INTO library_history(workspace,entity,entity_id,actor_id,actor_name,created,action,reason,document) VALUES (?,?,?,?,?,?,?,?,?)',(workspace,entity,id,user['id'],user['name'],stamp(),action,reason,encoded(document)))
    def replay(self,db,user,body,operation):
        key=fingerprint([operation,body.model_dump(mode='json')]);row=db.execute('SELECT fingerprint,result FROM library_requests WHERE user_id=? AND request_id=?',(user['id'],body.requestId)).fetchone()
        if row:
            if row['fingerprint']!=key:raise HTTPException(409,'Идентификатор уже использован другим запросом.')
            return key,json.loads(row['result'])
        return key,None
    def remember(self,db,user,body,key,result):
        db.execute('INSERT INTO library_requests(user_id,request_id,fingerprint,result) VALUES (?,?,?,?)',(user['id'],body.requestId,key,encoded(result)))
        db.execute('DELETE FROM library_requests WHERE user_id=? AND seq NOT IN (SELECT seq FROM library_requests WHERE user_id=? ORDER BY seq DESC LIMIT 100)',(user['id'],user['id']))
        return result
    def recipient_map(self,db,snap,workspace):
        result={}
        for raw in db.execute("SELECT * FROM accounts WHERE active=1 AND role IN ('student','parent','admin','editor','teacher','staff')"):
            u=self.access.public(raw)
            if not enabled(u,snap,workspace):continue
            if u['role'] in STAFF:result.setdefault(('employee',u['id']),[]).append(u)
            elif u['role'] in ['parent','student']:
                for id in grant_ids(u,workspace,'student'):
                    p=snap.entities['student'].get(id)
                    if p and p.active:result.setdefault(('student',id),[]).append(u)
        return result
    def notify(self,db,row,user,snap,workspace,action):
        recipients=self.recipient_map(db,snap,workspace).get((row['kind'],row['borrower_id']),[])
        self.notifications.deliver(db,[u for u in recipients if u['id']!=user['id']],workspace,row['version'],action)
    def issue_preview(self,db,user,snap,workspace,body):
        scope=self.scope(db,user,snap,workspace);config=self.config(db,workspace);day=today()
        if not scope['all'] and not scope['classes']:raise HTTPException(403,'Выдача доступна назначенным библиотекарям.')
        if not day<=body.due<=day+timedelta(days=365):raise HTTPException(422,'Срок возврата должен быть от сегодняшней даты до года вперёд.')
        items=[];counts={}
        for item in body.items:
            c=self.copy(db,workspace,item.copyId);version(c,item.copyVersion)
            if c['onLoan'] or c['status']!='available' or c['condition']=='damaged' or c['archived']:raise HTTPException(409,'Экземпляр недоступен для выдачи.')
            p=self.borrower(db,item.borrower.kind,item.borrower.id,snap,workspace)
            if not self.manage_borrower(scope,p['kind'],p['id'],snap):raise HTTPException(403,'Нет права выдачи этому читателю.')
            key=(p['kind'],p['id']);counts[key]=counts.get(key,0)+1
            items.append(dict(copy=c,borrower=p))
        for (kind,id),count in counts.items():
            current=db.execute("SELECT count(*) FROM library_loans WHERE workspace=? AND kind=? AND borrower_id=? AND status IN ('active','lost')",(workspace,kind,id)).fetchone()[0]
            if current+count>config['maxLoans']:raise HTTPException(409,'Превышено разрешённое число книг на руках. Верните книги или проверьте настройки.')
        result=dict(items=items,due=body.due.isoformat())
        return result|dict(previewHash=fingerprint([result,config,scope,snap.state.revision]))
    def register(self,app,Workspace):
        from .library_routes import register
        from .library_inventory import register as inventory
        register(self,app,Workspace);inventory(self,app,Workspace)
