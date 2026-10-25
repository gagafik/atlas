"""Extracurricular enrolment and attendance, independent of timetable and journal."""
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
def today():return now().astimezone(ZoneInfo('Asia/Qyzylorda')).date().isoformat()
def version(row,n):
    if row['version']!=n:raise HTTPException(409,'Данные изменились. Обновите раздел перед действием.')
def unpack(row):return dict(row)|dict(data=json.loads(row['document']))

def tables(db):
    db.executescript('''
    CREATE TABLE IF NOT EXISTS clubs(seq INTEGER PRIMARY KEY AUTOINCREMENT,id TEXT UNIQUE,workspace TEXT,version INTEGER,status TEXT,created TEXT,updated TEXT,search TEXT,document TEXT);
    CREATE INDEX IF NOT EXISTS clubs_space ON clubs(workspace,status,seq);
    CREATE TABLE IF NOT EXISTS club_access(club_id TEXT,user_id TEXT,permissions TEXT,name TEXT,PRIMARY KEY(club_id,user_id));
    CREATE INDEX IF NOT EXISTS club_access_owner ON club_access(user_id,permissions,club_id);
    CREATE TABLE IF NOT EXISTS club_members(seq INTEGER PRIMARY KEY AUTOINCREMENT,id TEXT UNIQUE,club_id TEXT,workspace TEXT,student_id TEXT,user_id TEXT,version INTEGER,status TEXT,created TEXT,updated TEXT,enrolled_on TEXT,left_on TEXT,document TEXT);
    CREATE UNIQUE INDEX IF NOT EXISTS club_active_student ON club_members(club_id,student_id) WHERE status IN ('enrolled','waiting');
    CREATE INDEX IF NOT EXISTS club_members_club ON club_members(club_id,status,seq);
    CREATE INDEX IF NOT EXISTS club_members_student ON club_members(workspace,student_id,status,seq);
    CREATE TABLE IF NOT EXISTS club_sessions(seq INTEGER PRIMARY KEY AUTOINCREMENT,id TEXT UNIQUE,club_id TEXT,workspace TEXT,version INTEGER,status TEXT,date TEXT,start TEXT,end TEXT,document TEXT);
    CREATE INDEX IF NOT EXISTS club_sessions_dates ON club_sessions(workspace,date,status,club_id);
    CREATE TABLE IF NOT EXISTS club_attendance(session_id TEXT,student_id TEXT,member_id TEXT,status TEXT,note TEXT,document TEXT,PRIMARY KEY(session_id,student_id));
    CREATE INDEX IF NOT EXISTS club_attendance_student ON club_attendance(student_id,session_id);
    CREATE TABLE IF NOT EXISTS club_history(seq INTEGER PRIMARY KEY AUTOINCREMENT,club_id TEXT,entity_id TEXT,actor_id TEXT,actor_name TEXT,created TEXT,action TEXT,reason TEXT,document TEXT);
    CREATE INDEX IF NOT EXISTS club_history_club ON club_history(club_id,seq);
    CREATE TABLE IF NOT EXISTS club_requests(seq INTEGER PRIMARY KEY AUTOINCREMENT,user_id TEXT,request_id TEXT,fingerprint TEXT,result TEXT,UNIQUE(user_id,request_id));
    CREATE INDEX IF NOT EXISTS club_request_owner ON club_requests(user_id,seq);
    ''')

class Clubs:
    def __init__(self,access,notifications,stamp):
        self.access,self.notifications=access,notifications
        self.snapshots=PortalSnapshots(access.read,stamp)
    def prepare(self):
        with closing(self.access.read_connect()) as db:
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='index' AND name='club_request_owner'").fetchone():return
        with self.access.lock,self.access.connect() as db:tables(db);self.notifications.tables(db)
    @contextmanager
    def context(self,request,workspace,write=False):
        u=request.state.user
        if not u:raise HTTPException(403,'Кружки доступны после входа.')
        snap=self.snapshots.get(workspace)
        if not enabled(u,snap,workspace):raise HTTPException(403,'Нет доступа к пространству.')
        self.prepare()
        def check(db):
            row=db.execute('SELECT * FROM accounts WHERE id=? AND active=1',(u['id'],)).fetchone();rev=db.execute('SELECT revision FROM workspaces WHERE id=?',(workspace,)).fetchone()
            if not row or self.access.public(row)!=u or not rev or rev[0]!=snap.state.revision:raise HTTPException(409,'Доступ или данные школы изменились. Обновите страницу.')
            db.row_factory=sqlite3.Row
        if write:
            with self.access.lock,self.access.connect() as db:
                db.execute('BEGIN IMMEDIATE');check(db);yield db,u,snap
        else:
            with closing(self.access.read_connect()) as db:
                db.execute('BEGIN');check(db);yield db,u,snap
    def club(self,db,workspace,id):
        r=db.execute('SELECT * FROM clubs WHERE workspace=? AND id=?',(workspace,id)).fetchone()
        if not r:raise HTTPException(404,'Кружок недоступен.')
        return unpack(r)
    def member(self,db,club,id):
        r=db.execute('SELECT * FROM club_members WHERE club_id=? AND id=?',(club['id'],id)).fetchone()
        if not r:raise HTTPException(404,'Запись недоступна.')
        return unpack(r)
    def session(self,db,club,id):
        r=db.execute('SELECT * FROM club_sessions WHERE club_id=? AND id=?',(club['id'],id)).fetchone()
        if not r:raise HTTPException(404,'Занятие недоступно.')
        return unpack(r)
    def manager(self,db,u,club):
        return u['role']=='admin' or (u['role'] in STAFF and bool(db.execute('SELECT 1 FROM club_access WHERE club_id=? AND user_id=? AND permissions=?',(club['id'],u['id'],permission_key(u))).fetchone()))
    def require_manager(self,db,u,club):
        if not self.manager(db,u,club):raise HTTPException(403,'Действие доступно ответственному за этот кружок.')
    def account(self,db,id):
        r=db.execute('SELECT * FROM accounts WHERE id=? AND active=1',(id,)).fetchone()
        return self.access.public(r) if r else None
    def active_staff(self,db,snap,club):
        result=[]
        for r in db.execute('SELECT * FROM club_access WHERE club_id=?',(club['id'],)):
            a=self.account(db,r['user_id'])
            if a and a['role'] in STAFF and enabled(a,snap,club['workspace']) and permission_key(a)==r['permissions']:result.append(a)
        return result
    def pupils(self,u,snap,workspace):
        ids=grant_ids(u,workspace,'student') if u['role'] in ['parent','student'] else set()
        return {id for id in ids if id in snap.entities['student'] and snap.entities['student'][id].active}
    def eligible(self,snap,club,id):
        p=snap.entities['student'].get(id)
        return bool(p and p.active and p.classId in club['data']['classIds'])
    def visible(self,db,u,snap,club):
        own=self.pupils(u,snap,club['workspace'])
        if self.manager(db,u,club):return
        if club['status']=='open' and (u['role'] in STAFF or any(self.eligible(snap,club,id) for id in own)):return
        if own and db.execute('SELECT 1 FROM club_members WHERE club_id=? AND student_id IN (SELECT value FROM json_each(?))',(club['id'],encoded(sorted(own)))).fetchone():return
        raise HTTPException(404,'Кружок недоступен.')
    def history(self,db,u,club,entity,action,reason,data):
        db.execute('INSERT INTO club_history(club_id,entity_id,actor_id,actor_name,created,action,reason,document) VALUES (?,?,?,?,?,?,?,?)',(club['id'],entity,u['id'],u['name'],stamp(),action,reason,encoded(data)))
    def replay(self,db,u,body,op):
        key=hashlib.sha256(encoded([op,body.model_dump(mode='json')]).encode()).hexdigest()
        r=db.execute('SELECT fingerprint,result FROM club_requests WHERE user_id=? AND request_id=?',(u['id'],body.requestId)).fetchone()
        if r:
            if r['fingerprint']!=key:raise HTTPException(409,'Идентификатор уже использован другим действием.')
            return key,json.loads(r['result'])
        return key,None
    def remember(self,db,u,body,key,result):
        db.execute('INSERT INTO club_requests(user_id,request_id,fingerprint,result) VALUES (?,?,?,?)',(u['id'],body.requestId,key,encoded(result)))
        db.execute('DELETE FROM club_requests WHERE user_id=? AND seq NOT IN (SELECT seq FROM club_requests WHERE user_id=? ORDER BY seq DESC LIMIT 100)',(u['id'],u['id']))
        return result
    def bump(self,db,club):
        db.execute('UPDATE clubs SET version=version+1,updated=? WHERE id=?',(stamp(),club['id']))
        club['version']+=1
    def notify(self,db,u,snap,club,students,action='clubs.changed'):
        ids=[id for id in set(students) if id in snap.entities['student'] and snap.entities['student'][id].active]
        targets=self.notifications.granted_users(db,club['workspace'],'student',ids,['parent','student']) if ids else []
        targets=[t for t in targets if t['id']!=u['id']]
        self.notifications.deliver(db,targets,club['workspace'],club['version'],action)
    def public(self,db,u,snap,club):
        manager=self.manager(db,u,club)
        counts={r['status']:r['n'] for r in db.execute("SELECT status,count(*) n FROM club_members WHERE club_id=? AND status IN ('enrolled','waiting') GROUP BY status",(club['id'],))}
        result={k:club[k] for k in ['id','version','status','created','updated','data']}
        result.update(canManage=manager,enrolled=counts.get('enrolled',0),waiting=counts.get('waiting',0),accepting=club['status']=='open' and club['data']['opensOn']<=today()<=club['data']['closesOn'],staff=[dict(id=a['id'],name=a['name']) for a in self.active_staff(db,snap,club)])
        return result
    def public_member(self,db,u,snap,club,row):
        if not self.manager(db,u,club) and row['student_id'] not in self.pupils(u,snap,club['workspace']):raise HTTPException(404,'Запись недоступна.')
        position=db.execute("SELECT count(*) FROM club_members WHERE club_id=? AND status='waiting' AND seq<=?",(club['id'],row['seq'])).fetchone()[0] if row['status']=='waiting' else None
        return {k:row[k] for k in ['id','version','status','created','updated']}|dict(studentId=row['student_id'],data={k:v for k,v in row['data'].items() if k!='requesterPermissions'},position=position,eligible=self.eligible(snap,club,row['student_id']))
    def change_member(self,db,u,snap,club,row,status,reason):
        d=row['data']|dict(decision=reason)
        joined=today() if status=='enrolled' else row['enrolled_on']
        left=today() if status in ['withdrawn','rejected','completed','ineligible'] else ''
        db.execute('UPDATE club_members SET status=?,version=version+1,updated=?,enrolled_on=?,left_on=?,document=? WHERE id=?',(status,stamp(),joined,left,encoded(d),row['id']))
        row.update(status=status,version=row['version']+1,data=d,enrolled_on=joined,left_on=left)
        self.history(db,u,club,row['id'],status,reason,dict(studentName=d['studentName'],studentId=row['student_id'],status=status))
        self.notify(db,u,snap,club,[row['student_id']])
    def conflict(self,db,club,student):
        return bool(db.execute("""SELECT 1 FROM club_sessions a JOIN club_sessions b ON a.workspace=b.workspace AND a.date=b.date AND a.start<b.end AND b.start<a.end
            JOIN club_members m ON m.club_id=b.club_id AND m.student_id=? AND m.status='enrolled'
            WHERE a.club_id=? AND b.club_id<>a.club_id AND a.status IN ('planned','active') AND b.status IN ('planned','active') AND a.date>=? LIMIT 1""",(student,club['id'],today())).fetchone())
    def advance(self,db,u,snap,club,manual=False):
        if club['status']!='open' or today()>club['data']['endsOn'] or (not manual and club['data']['queueMode']!='automatic'):return []
        free=club['data']['capacity']-db.execute("SELECT count(*) FROM club_members WHERE club_id=? AND status='enrolled'",(club['id'],)).fetchone()[0]
        promoted=[]
        for raw in db.execute("SELECT * FROM club_members WHERE club_id=? AND status='waiting' ORDER BY seq",(club['id'],)).fetchall():
            if free<=0:break
            r=unpack(raw)
            owner=self.account(db,r['user_id'])
            valid=owner and enabled(owner,snap,club['workspace']) and (self.manager(db,owner,club) or r['student_id'] in self.pupils(owner,snap,club['workspace']))  # the requester's link to the pupil, not a hash of their rights
            if not self.eligible(snap,club,r['student_id']) or not valid:self.change_member(db,u,snap,club,r,'ineligible','Ученик или полномочия заявителя изменились. Нужна новая запись.');continue
            if self.conflict(db,club,r['student_id']):break  # Do not silently bypass a conflicting FIFO head.
            self.change_member(db,u,snap,club,r,'enrolled','Зачисление по порядку очереди.');promoted.append(r['id']);free-=1
            if manual:break
        return promoted
    def register(self,app,Workspace):
        from .clubs_routes import register
        from .clubs_sessions import register as sessions
        register(self,app,Workspace);sessions(self,app,Workspace)
