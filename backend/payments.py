"""Append-only school fee subledger. It records money movements, never executes them."""
from contextlib import closing, contextmanager
from datetime import timedelta
from zoneinfo import ZoneInfo
from uuid import uuid4
import hashlib, json, sqlite3
from fastapi import HTTPException
from .access import now
from .notifications import encoded, permission_key
from .portal_cache import PortalSnapshots
from .school_forms import enabled, grant_ids

STAFF = {'admin', 'editor', 'teacher', 'staff'}
def stamp(): return now().isoformat()
def today(): return now().astimezone(ZoneInfo('Asia/Qyzylorda')).date()
def fingerprint(value): return hashlib.sha256(encoded(value).encode()).hexdigest()
def version(row, expected):
    if row['version'] != expected: raise HTTPException(409, 'Финансовые данные изменились. Обновите страницу.')
def tables(db):
    db.executescript('''
    CREATE TABLE IF NOT EXISTS finance_config(workspace TEXT PRIMARY KEY,version INTEGER,document TEXT);
    CREATE TABLE IF NOT EXISTS finance_accounts(workspace TEXT,student_id TEXT,version INTEGER,code TEXT UNIQUE,name TEXT,payers TEXT,reminded TEXT,PRIMARY KEY(workspace,student_id));
    CREATE TABLE IF NOT EXISTS finance_plans(seq INTEGER PRIMARY KEY AUTOINCREMENT,id TEXT UNIQUE,workspace TEXT,version INTEGER,archived INTEGER,document TEXT);
    CREATE INDEX IF NOT EXISTS finance_plan_space ON finance_plans(workspace,seq);
    CREATE TABLE IF NOT EXISTS finance_charges(seq INTEGER PRIMARY KEY AUTOINCREMENT,id TEXT UNIQUE,workspace TEXT,student_id TEXT,plan_id TEXT,installment INTEGER,title TEXT,due TEXT,created TEXT,book_date TEXT,document TEXT,UNIQUE(plan_id,student_id,installment));
    CREATE INDEX IF NOT EXISTS finance_charge_owner ON finance_charges(workspace,student_id,seq);
    CREATE TABLE IF NOT EXISTS finance_receipts(seq INTEGER PRIMARY KEY AUTOINCREMENT,id TEXT UNIQUE,workspace TEXT,created TEXT,paid_date TEXT,method TEXT,reference TEXT,reference_key TEXT,payer TEXT,reason TEXT,UNIQUE(workspace,reference_key));
    CREATE INDEX IF NOT EXISTS finance_receipt_space ON finance_receipts(workspace,seq);
    CREATE TABLE IF NOT EXISTS finance_entries(seq INTEGER PRIMARY KEY AUTOINCREMENT,id TEXT UNIQUE,workspace TEXT,student_id TEXT,created TEXT,book_date TEXT,kind TEXT,amount INTEGER,charge_id TEXT,receipt_id TEXT,label TEXT,reference TEXT,actor_id TEXT);
    CREATE INDEX IF NOT EXISTS finance_entry_owner ON finance_entries(workspace,student_id,seq);
    CREATE INDEX IF NOT EXISTS finance_entry_charge ON finance_entries(charge_id,seq);
    CREATE INDEX IF NOT EXISTS finance_entry_receipt ON finance_entries(receipt_id,student_id,seq);
    CREATE INDEX IF NOT EXISTS finance_entry_day ON finance_entries(workspace,book_date,seq);
    CREATE TABLE IF NOT EXISTS finance_allocations(seq INTEGER PRIMARY KEY AUTOINCREMENT,id TEXT UNIQUE,workspace TEXT,student_id TEXT,charge_id TEXT,receipt_id TEXT,amount INTEGER,reversal_of TEXT UNIQUE,created TEXT,book_date TEXT,reason TEXT,actor_id TEXT);
    CREATE INDEX IF NOT EXISTS finance_allocation_charge ON finance_allocations(charge_id,seq);
    CREATE INDEX IF NOT EXISTS finance_allocation_receipt ON finance_allocations(receipt_id,student_id,seq);
    CREATE INDEX IF NOT EXISTS finance_allocation_owner ON finance_allocations(workspace,student_id,seq);
    CREATE TABLE IF NOT EXISTS finance_periods(seq INTEGER PRIMARY KEY AUTOINCREMENT,id TEXT UNIQUE,workspace TEXT,through TEXT,created TEXT,actor_id TEXT,actor_name TEXT,reason TEXT,document TEXT);
    CREATE INDEX IF NOT EXISTS finance_period_space ON finance_periods(workspace,seq);
    CREATE TABLE IF NOT EXISTS finance_history(seq INTEGER PRIMARY KEY AUTOINCREMENT,workspace TEXT,student_id TEXT,created TEXT,actor_id TEXT,actor_name TEXT,action TEXT,reason TEXT,document TEXT);
    CREATE INDEX IF NOT EXISTS finance_history_owner ON finance_history(workspace,student_id,seq);
    CREATE TABLE IF NOT EXISTS finance_requests(seq INTEGER PRIMARY KEY AUTOINCREMENT,user_id TEXT,request_id TEXT,fingerprint TEXT,result TEXT,UNIQUE(user_id,request_id));
    CREATE INDEX IF NOT EXISTS finance_request_owner ON finance_requests(user_id,seq);
    ''')

class Payments:
    def __init__(self, access, notifications, state_stamp):
        self.access, self.notifications = access, notifications
        self.snapshots = PortalSnapshots(access.read, state_stamp)
    def prepare(self):
        with closing(self.access.read_connect()) as db:
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='finance_request_owner'").fetchone(): return
        with self.access.lock, self.access.connect() as db: tables(db); self.notifications.tables(db)
    @contextmanager
    def context(self, request, workspace, write=False):
        user = request.state.user
        if not user: raise HTTPException(403, 'Платежи доступны после входа.')
        snap = self.snapshots.get(workspace)
        if not enabled(user, snap, workspace): raise HTTPException(403, 'Нет доступа к этому пространству.')
        self.prepare()
        def check(db):
            row=db.execute('SELECT * FROM accounts WHERE id=? AND active=1',(user['id'],)).fetchone()
            rev=db.execute('SELECT revision FROM workspaces WHERE id=?',(workspace,)).fetchone()
            if not row or self.access.public(row)!=user or not rev or rev[0]!=snap.state.revision: raise HTTPException(409,'Доступ или состав школы изменились. Обновите страницу.')
            db.row_factory=sqlite3.Row
        if write:
            with self.access.lock,self.access.connect() as db: db.execute('BEGIN IMMEDIATE');check(db);yield db,user,snap
        else:
            with closing(self.access.read_connect()) as db: db.execute('BEGIN');check(db);yield db,user,snap
    def config(self,db,w):
        row=db.execute('SELECT * FROM finance_config WHERE workspace=?',(w,)).fetchone()
        return dict(version=row['version'],**json.loads(row['document'])) if row else dict(version=0,currency='KZT',decimals=2,familyPolicy='assigned',managers=[],closedThrough=None)
    def admin(self,u):
        if u['role']!='admin':raise HTTPException(403,'Это действие выполняет администрация.')
    def manager(self,db,u,snap,w):
        return u['role']=='admin' or u['role'] in STAFF and any(a['id']==u['id'] and a['permissions']==permission_key(u) for a in self.config(db,w)['managers'])
    def require_manager(self,db,u,snap,w):
        if not self.manager(db,u,snap,w): raise HTTPException(403,'Нужны отдельные полномочия по платежам.')
    def open_day(self,db,w):
        day=today().isoformat();closed=self.config(db,w)['closedThrough']
        if closed and day<=closed:raise HTTPException(409,'Учётный день закрыт. Новые операции доступны со следующего дня.')
        return day
    def account(self,db,w,id,snap,create=False):
        p=snap.entities['student'].get(id)
        row=db.execute('SELECT * FROM finance_accounts WHERE workspace=? AND student_id=?',(w,id)).fetchone()
        if not p and not row:raise HTTPException(404,'Лицевой счёт не найден.')
        if create and not row:
            if not p or not p.active:raise HTTPException(422,'Для нового счёта нужен действующий ученик.')
            db.execute('INSERT INTO finance_accounts VALUES (?,?,0,?,?,?,NULL)',(w,id,'AT-'+uuid4().hex[:16].upper(),p.name,'[]'))
            row=db.execute('SELECT * FROM finance_accounts WHERE workspace=? AND student_id=?',(w,id)).fetchone()
        return dict(row) if row else dict(workspace=w,student_id=id,version=0,code='',name=p.name,payers='[]',reminded=None)
    def family_access(self,db,u,snap,w,account):
        id=account['student_id'];p=snap.entities['student'].get(id)
        if u['role']!='parent' or not p or not p.active or id not in grant_ids(u,w,'student'):return False
        if self.config(db,w)['familyPolicy']=='assigned':
            return any(a['id']==u['id'] and a['permissions']==permission_key(u) for a in json.loads(account['payers']))
        row=db.execute('SELECT document FROM family_links WHERE workspace=? AND parent_id=? AND student_id=?',(w,u['id'],id)).fetchone()
        if not row:return False
        doc=json.loads(row[0]);return doc.get('status')=='active' and bool(doc.get('verified'))
    def require_account(self,db,u,snap,w,id,write=False):
        a=self.account(db,w,id,snap);manage=self.manager(db,u,snap,w)
        if not manage and (write or not self.family_access(db,u,snap,w,a)):raise HTTPException(404,'Лицевой счёт недоступен.')
        return a,manage
    def touch(self,db,w,id):db.execute('UPDATE finance_accounts SET version=version+1 WHERE workspace=? AND student_id=?',(w,id))
    def history(self,db,w,u,id,action,reason,doc):
        db.execute('INSERT INTO finance_history(workspace,student_id,created,actor_id,actor_name,action,reason,document) VALUES (?,?,?,?,?,?,?,?)',(w,id,stamp(),u['id'],u['name'],action,reason,encoded(doc)))
    def replay(self,db,u,body,operation):
        key=fingerprint([operation,body.model_dump(mode='json')]);r=db.execute('SELECT fingerprint,result FROM finance_requests WHERE user_id=? AND request_id=?',(u['id'],body.requestId)).fetchone()
        if r:
            if key!=r['fingerprint']:raise HTTPException(409,'Этот идентификатор уже использован другим действием.')
            return key,json.loads(r['result'])
        return key,None
    def remember(self,db,u,body,key,result):
        # Financial replay keys are retained; pruning could duplicate an old receipt/batch.
        db.execute('INSERT INTO finance_requests(user_id,request_id,fingerprint,result) VALUES (?,?,?,?)',(u['id'],body.requestId,key,encoded(result)))
        return result
    def entry(self,db,w,u,id,kind,amount,label,charge=None,receipt=None,reference=''):
        self.open_day(db,w);self.check_capacity(db,w,id);key=uuid4().hex
        db.execute('INSERT INTO finance_entries(id,workspace,student_id,created,book_date,kind,amount,charge_id,receipt_id,label,reference,actor_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',(key,w,id,stamp(),today().isoformat(),kind,amount,charge,receipt,label,reference,u['id']))
        return key
    def charge_rows(self,db,w,id,through=None):
        day=through or today().isoformat()
        rows=db.execute('''SELECT c.*,COALESCE((SELECT SUM(e.amount) FROM finance_entries e WHERE e.charge_id=c.id AND e.book_date<=?),0) AS total,
        COALESCE((SELECT SUM(a.amount) FROM finance_allocations a WHERE a.charge_id=c.id AND a.book_date<=?),0) AS allocated
        FROM finance_charges c WHERE c.workspace=? AND c.student_id=? AND c.book_date<=? ORDER BY c.due,c.seq''',(day,day,w,id,day)).fetchall()
        return [dict(r)|dict(outstanding=r['total']-r['allocated']) for r in rows]
    def credits(self,db,w,id,through=None):
        day=through or today().isoformat()
        rows=db.execute('''SELECT r.*, -SUM(e.amount) AS total,
        COALESCE((SELECT SUM(a.amount) FROM finance_allocations a WHERE a.receipt_id=r.id AND a.student_id=? AND a.book_date<=?),0) AS allocated
        FROM finance_receipts r JOIN finance_entries e ON e.receipt_id=r.id
        WHERE e.workspace=? AND e.student_id=? AND e.book_date<=? GROUP BY r.id ORDER BY r.seq''',(id,day,w,id,day)).fetchall()
        return [dict(r)|dict(available=r['total']-r['allocated']) for r in rows]
    def summary(self,db,w,id,through=None,items=None):
        day=through or today().isoformat()
        charges,credits=items if items is not None else (self.charge_rows(db,w,id,day),self.credits(db,w,id,day))
        outstanding=sum(x['outstanding'] for x in charges);credit=sum(x['available'] for x in credits)
        return dict(charged=sum(x['total'] for x in charges),received=sum(x['total'] for x in credits),outstanding=outstanding,unallocated=credit,balance=outstanding-credit,overdue=sum(x['outstanding'] for x in charges if x['due']<day))
    def allocate(self,db,w,u,id,charge,receipt,amount,reason):
        key=uuid4().hex
        db.execute('INSERT INTO finance_allocations(id,workspace,student_id,charge_id,receipt_id,amount,reversal_of,created,book_date,reason,actor_id) VALUES (?,?,?,?,?,?,NULL,?,?,?,?)',(key,w,id,charge,receipt,amount,stamp(),today().isoformat(),reason,u['id']))
        return key
    def check_capacity(self,db,w,id,entries=1):
        count=db.execute('SELECT count(*) FROM finance_entries WHERE workspace=? AND student_id=?',(w,id)).fetchone()[0]
        allocations=db.execute('SELECT count(*) FROM finance_allocations WHERE workspace=? AND student_id=?',(w,id)).fetchone()[0]
        if count+entries>20000 or allocations>40000:raise HTTPException(413,'Достигнут предел операций счёта. Требуется расширение архива.')
    def create_receipt(self,db,u,snap,w,body,reference_key=None):
        self.open_day(db,w)
        if body.paidDate>today():raise HTTPException(422,'Дата поступления не может быть в будущем.')
        if not body.source.strip():raise HTTPException(422,'Укажите источник оплаты.')
        reference=body.reference.strip();refkey=reference_key or (('bank:'+body.source.strip().casefold() if body.method=='bank' else 'manual:'+body.method)+':'+reference.casefold())
        if not reference:raise HTTPException(422,'Укажите номер документа поступления.')
        if db.execute('SELECT 1 FROM finance_receipts WHERE workspace=? AND reference_key=?',(w,refkey)).fetchone():raise HTTPException(409,'Документ поступления уже учтён, в том числе если впоследствии исправлен.')
        for part in body.parts:
            a=self.account(db,w,part.studentId,snap);version(a,part.version);self.check_capacity(db,w,part.studentId)
        rid=uuid4().hex
        db.execute('INSERT INTO finance_receipts(id,workspace,created,paid_date,method,reference,reference_key,payer,reason) VALUES (?,?,?,?,?,?,?,?,?)',(rid,w,stamp(),body.paidDate.isoformat(),body.method,reference,refkey,body.payer,body.reason))
        for part in body.parts:
            self.account(db,w,part.studentId,snap,True)
            self.entry(db,w,u,part.studentId,'receipt',-part.amount,'Поступление оплаты' if body.method!='opening' else 'Перенос переплаты',receipt=rid,reference=reference)
            self.touch(db,w,part.studentId);self.history(db,w,u,part.studentId,'receipt',body.reason,dict(receiptId=rid,amount=part.amount,paidDate=body.paidDate.isoformat(),reference=reference))
        self.notify(db,w,u,snap,[p.studentId for p in body.parts],'payments:received')
        return rid
    def notify(self,db,w,u,snap,ids,action):
        recipients=[];seen=set();affected=set(ids)
        for candidate in self.notifications.granted_users(db,w,'student',ids,['parent']):
            if candidate['id'] in seen or candidate['id']==u['id']:continue
            own_ids=affected.intersection(grant_ids(candidate,w,'student'))
            if any(self.family_access(db,candidate,snap,w,self.account(db,w,id,snap)) for id in own_ids):recipients.append(candidate);seen.add(candidate['id'])
        self.notifications.deliver(db,recipients,w,snap.state.revision,action)
    def register(self,app,Workspace):
        from .payment_routes import register
        from .payment_batches import register as batches
        register(self,app,Workspace);batches(self,app,Workspace)
