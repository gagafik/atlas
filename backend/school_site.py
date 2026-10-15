"""Private editorial drafts and independently revocable public snapshots."""
from contextlib import closing, contextmanager
from datetime import timedelta
import hashlib, json, secrets, sqlite3
from fastapi import HTTPException
from .access import now
from .notifications import encoded, permission_key
from .portal_cache import PortalSnapshots
from .school_forms import enabled
from .school_site_models import Content, SiteData

STAFF = {'admin', 'editor', 'teacher', 'staff'}

def stamp(): return now().isoformat()
def version(row, expected):
    if row['version'] != expected: raise HTTPException(409, 'Редакция изменилась. Обновите раздел.')

def tables(db):
    db.executescript('''
    CREATE TABLE IF NOT EXISTS school_site_config(workspace TEXT PRIMARY KEY,version INTEGER,document TEXT,published TEXT,enabled INTEGER);
    CREATE TABLE IF NOT EXISTS school_site_access(workspace TEXT,user_id TEXT,permissions TEXT,PRIMARY KEY(workspace,user_id));
    CREATE TABLE IF NOT EXISTS school_site_entries(seq INTEGER PRIMARY KEY AUTOINCREMENT,id TEXT UNIQUE,workspace TEXT,version INTEGER,slug TEXT,kind TEXT,archived INTEGER,created TEXT,updated TEXT,document TEXT,published TEXT,published_at TEXT,published_search TEXT,draft_search TEXT,UNIQUE(workspace,slug));
    CREATE INDEX IF NOT EXISTS school_site_public ON school_site_entries(workspace,kind,published_at,seq) WHERE published IS NOT NULL;
    CREATE TABLE IF NOT EXISTS school_site_history(seq INTEGER PRIMARY KEY AUTOINCREMENT,workspace TEXT,entry_id TEXT,actor_id TEXT,actor_name TEXT,created TEXT,action TEXT,reason TEXT,document TEXT);
    CREATE INDEX IF NOT EXISTS school_site_history_entry ON school_site_history(workspace,entry_id,seq);
    CREATE TABLE IF NOT EXISTS school_site_previews(token TEXT PRIMARY KEY,workspace TEXT,entry_id TEXT,user_id TEXT,version INTEGER,config_version INTEGER,created TEXT);
    CREATE TABLE IF NOT EXISTS school_site_requests(seq INTEGER PRIMARY KEY AUTOINCREMENT,user_id TEXT,request_id TEXT,fingerprint TEXT,result TEXT,UNIQUE(user_id,request_id));
    CREATE TABLE IF NOT EXISTS school_site_files(id TEXT PRIMARY KEY,workspace TEXT,item_id TEXT,owner_id TEXT,filename TEXT,size INTEGER,created TEXT,status TEXT,sha256 TEXT,linked INTEGER);
    CREATE INDEX IF NOT EXISTS school_site_files_item ON school_site_files(workspace,item_id,status);
    CREATE INDEX IF NOT EXISTS school_site_request_owner ON school_site_requests(user_id,seq);
    ''')

def config_result(row):
    return dict(version=row['version'], data=json.loads(row['document']), published=json.loads(row['published']) if row['published'] else None, enabled=bool(row['enabled']))

def entry_result(row):
    return dict(id=row['id'], version=row['version'], data=json.loads(row['document']), published=json.loads(row['published']) if row['published'] else None,
                archived=bool(row['archived']), created=row['created'], updated=row['updated'], publishedAt=row['published_at'])

class SchoolSite:
    def __init__(self, access, stamp):
        self.access = access
        self.snapshots = PortalSnapshots(access.read, stamp)

    def prepare(self):
        with closing(self.access.read_connect()) as db:
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='school_site_request_owner'").fetchone(): return
        with self.access.lock, self.access.connect() as db: tables(db)

    @contextmanager
    def context(self, request, workspace, write=False):
        u = request.state.user
        if not u or u['role'] not in STAFF: raise HTTPException(403, 'Редактор сайта доступен назначенным сотрудникам после входа.')
        snap = self.snapshots.get(workspace)
        if not enabled(u, snap, workspace): raise HTTPException(403, 'Нет доступа к пространству.')
        self.prepare()
        def check(db):
            row = db.execute('SELECT * FROM accounts WHERE id=? AND active=1', (u['id'],)).fetchone()
            rev = db.execute('SELECT revision FROM workspaces WHERE id=?', (workspace,)).fetchone()
            if not row or self.access.public(row) != u or not rev or rev[0] != snap.state.revision:
                raise HTTPException(409, 'Права или данные изменились. Обновите страницу.')
            db.row_factory = sqlite3.Row
        if write:
            with self.access.lock, self.access.connect() as db:
                db.execute('BEGIN IMMEDIATE'); check(db); yield db, u, snap
        else:
            with closing(self.access.read_connect()) as db:
                db.execute('BEGIN'); check(db); yield db, u, snap

    @contextmanager
    def public_context(self, workspace):
        with closing(self.access.read_connect()) as db:
            db.row_factory = sqlite3.Row; db.execute('BEGIN')
            if not db.execute("SELECT 1 FROM sqlite_master WHERE name='school_site_request_owner'").fetchone(): raise HTTPException(404, 'Сайт недоступен.')
            cfg = db.execute('SELECT * FROM school_site_config WHERE workspace=?', (workspace,)).fetchone()
            if not cfg or not cfg['enabled'] or not cfg['published']: raise HTTPException(404, 'Сайт недоступен.')
            yield db, json.loads(cfg['published'])

    def config(self, db, workspace):
        row = db.execute('SELECT * FROM school_site_config WHERE workspace=?', (workspace,)).fetchone()
        return row or dict(version=0, document=encoded(SiteData(title='Сайт школы').model_dump()), published=None, enabled=0)

    def editor(self, db, u, workspace):
        return u['role'] == 'admin' or bool(db.execute('SELECT 1 FROM school_site_access WHERE workspace=? AND user_id=? AND permissions=?', (workspace, u['id'], permission_key(u))).fetchone())

    def require_editor(self, db, u, workspace):
        if not self.editor(db, u, workspace): raise HTTPException(403, 'Администратор должен назначить вас редактором этого сайта.')

    def admin(self, u):
        if u['role'] != 'admin': raise HTTPException(403, 'Публикацией и доступом управляет администрация.')

    def entry(self, db, workspace, id):
        row = db.execute('SELECT * FROM school_site_entries WHERE workspace=? AND id=?', (workspace, id)).fetchone()
        if not row: raise HTTPException(404, 'Материал недоступен.')
        return row

    def replay(self, db, u, body, op):
        key = hashlib.sha256(encoded([op, body.model_dump(mode='json')]).encode()).hexdigest()
        row = db.execute('SELECT fingerprint,result FROM school_site_requests WHERE user_id=? AND request_id=?', (u['id'], str(body.requestId))).fetchone()
        if row and row['fingerprint'] != key: raise HTTPException(409, 'Повтор запроса содержит другое действие.')
        return key, json.loads(row['result']) if row else None

    def remember(self, db, u, body, key, result):
        db.execute('INSERT INTO school_site_requests(user_id,request_id,fingerprint,result) VALUES (?,?,?,?)', (u['id'], str(body.requestId), key, encoded(result)))
        db.execute('DELETE FROM school_site_requests WHERE user_id=? AND seq NOT IN (SELECT seq FROM school_site_requests WHERE user_id=? ORDER BY seq DESC LIMIT 100)', (u['id'], u['id']))
        return result

    def history(self, db, u, workspace, id, action, reason, data):
        db.execute('INSERT INTO school_site_history(workspace,entry_id,actor_id,actor_name,created,action,reason,document) VALUES (?,?,?,?,?,?,?,?)', (workspace, id, u['id'], u['name'], stamp(), action, reason, encoded(data)))

    def photos(self, db, workspace, id, data, link=False):
        for photo in data['photos']:
            r = db.execute("SELECT * FROM school_site_files WHERE id=? AND workspace=? AND item_id=? AND status='ready'", (photo['id'], workspace, id)).fetchone()
            if not r: raise HTTPException(422, 'Фото недоступно или относится к другому материалу.')
            if link: db.execute('UPDATE school_site_files SET linked=1 WHERE id=?', (photo['id'],))

    def preview(self, db, u, workspace, id, ver):
        token = secrets.token_urlsafe(32)
        db.execute('DELETE FROM school_site_previews WHERE created<?', ((now()-timedelta(minutes=15)).isoformat(),))
        db.execute('DELETE FROM school_site_previews WHERE user_id=? AND token NOT IN (SELECT token FROM school_site_previews WHERE user_id=? ORDER BY created DESC LIMIT 19)', (u['id'], u['id']))
        db.execute('INSERT INTO school_site_previews VALUES (?,?,?,?,?,?,?)', (hashlib.sha256(token.encode()).hexdigest(), workspace, id, u['id'], ver, self.config(db, workspace)['version'], stamp()))
        return token

    def consume(self, db, u, workspace, id, body):
        if not body.confirmedPublic: raise HTTPException(422, 'Подтвердите, что материал можно открыть без входа.')
        digest = hashlib.sha256(body.token.encode()).hexdigest()
        r = db.execute('SELECT * FROM school_site_previews WHERE token=?', (digest,)).fetchone()
        if not r or r['workspace'] != workspace or r['entry_id'] != id or r['user_id'] != u['id'] or r['created'] <= (now()-timedelta(minutes=15)).isoformat():
            raise HTTPException(409, 'Откройте свежий предпросмотр перед публикацией.')
        if r['version'] != body.version or r['config_version'] != self.config(db, workspace)['version']:
            raise HTTPException(409, 'Предпросмотр устарел. Проверьте материал заново.')
        db.execute('DELETE FROM school_site_previews WHERE token=?', (digest,))

    def register(self, app, Workspace):
        from .school_site_routes import register
        from .school_site_files import register as files
        from .school_site_public import register as public
        register(self, app, Workspace); files(self, app, Workspace); public(self, app, Workspace)
