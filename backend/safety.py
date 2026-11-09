"""Safety reports for administrators: «кто в школе» for a fire drill and the register of safety briefings.

Both read the timetable and the school's records and never change them. See SAFETY.md.
"""
from contextlib import closing,contextmanager
from zoneinfo import ZoneInfo
import hashlib,json,sqlite3

from fastapi import HTTPException
from .access import now
from .notifications import encoded
from .portal_cache import PortalSnapshots
from .school_forms import enabled

ZONE=ZoneInfo('Asia/Qyzylorda')
DENIED='Раздел доступен администрации.'


def today():return now().astimezone(ZONE).date()


def tables(db):
    db.executescript('''
    CREATE TABLE IF NOT EXISTS safety_briefings(seq INTEGER PRIMARY KEY AUTOINCREMENT,id TEXT UNIQUE,workspace TEXT,version INTEGER,status TEXT,kind TEXT,held_on TEXT,audience TEXT,class_id TEXT,author_id TEXT,author_name TEXT,created TEXT,updated TEXT,document TEXT);
    CREATE INDEX IF NOT EXISTS safety_briefings_list ON safety_briefings(workspace,status,held_on,seq);
    CREATE TABLE IF NOT EXISTS safety_briefing_people(briefing_id TEXT,workspace TEXT,kind TEXT,person_id TEXT,name TEXT,class_name TEXT,present INTEGER NOT NULL,PRIMARY KEY(briefing_id,kind,person_id));
    CREATE INDEX IF NOT EXISTS safety_briefing_person ON safety_briefing_people(workspace,kind,person_id);
    CREATE TABLE IF NOT EXISTS safety_history(seq INTEGER PRIMARY KEY AUTOINCREMENT,workspace TEXT,briefing_id TEXT,actor_id TEXT,actor_name TEXT,created TEXT,action TEXT,reason TEXT,document TEXT);
    CREATE INDEX IF NOT EXISTS safety_history_briefing ON safety_history(workspace,briefing_id,seq);
    CREATE TABLE IF NOT EXISTS safety_requests(seq INTEGER PRIMARY KEY AUTOINCREMENT,user_id TEXT,request_id TEXT,fingerprint TEXT,result TEXT,UNIQUE(user_id,request_id));
    CREATE INDEX IF NOT EXISTS safety_requests_owner ON safety_requests(user_id,seq);
    ''')


class Safety:
    def __init__(self,access,stamp):
        self.access=access
        self.snapshots=PortalSnapshots(access.read,stamp)
    # Routes reach the clock only through these two, so tests can patch the module functions.
    def now(self):return now()
    def today(self):return today()
    def prepare(self):
        with closing(self.access.read_connect()) as db:
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='safety_requests_owner'").fetchone():return
        with self.access.lock,self.access.connect() as db:tables(db)
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
    def admin(self,user):
        # The guard lets an editor reach /api/safety/, so every route asks again.
        if user['role']!='admin':raise HTTPException(403,DENIED)
    def history(self,db,workspace,briefing,user,action,reason,document):
        db.execute('INSERT INTO safety_history(workspace,briefing_id,actor_id,actor_name,created,action,reason,document) VALUES (?,?,?,?,?,?,?,?)',(workspace,briefing,user['id'],user['name'],self.now().isoformat(),action,reason,encoded(document)))
    def replay(self,db,user,body,operation):
        key=hashlib.sha256(encoded([operation,body.model_dump(mode='json')]).encode()).hexdigest()
        r=db.execute('SELECT fingerprint,result FROM safety_requests WHERE user_id=? AND request_id=?',(user['id'],body.requestId)).fetchone()
        if r:
            if r['fingerprint']!=key:raise HTTPException(409,'Идентификатор уже использован другим действием.')
            return key,json.loads(r['result'])
        return key,None
    def remember(self,db,user,body,key,result):
        db.execute('INSERT INTO safety_requests(user_id,request_id,fingerprint,result) VALUES (?,?,?,?)',(user['id'],body.requestId,key,encoded(result)))
        db.execute('DELETE FROM safety_requests WHERE user_id=? AND seq NOT IN (SELECT seq FROM safety_requests WHERE user_id=? ORDER BY seq DESC LIMIT 100)',(user['id'],user['id']))
        return result
    def register(self,app,Workspace):
        from .safety_drill import register as drill
        from .safety_briefings import register as briefings
        drill(self,app,Workspace)
        briefings(self,app,Workspace)
