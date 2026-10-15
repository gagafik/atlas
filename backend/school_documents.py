"""Private issued school documents with immutable text and transactional numbering."""
from contextlib import closing,contextmanager
from html import escape
import hashlib,json,sqlite3
from zoneinfo import ZoneInfo
from fastapi import HTTPException
from .access import now
from .portal_cache import PortalSnapshots
from .school_forms import enabled,grant_ids
from .notifications import encoded,permission_key
from .document_models import TOKEN,values_for

STAFF={'admin','editor','teacher','staff'}
def stamp():return now().isoformat()
def today():return now().astimezone(ZoneInfo('Asia/Qyzylorda')).date()
def fingerprint(value):return hashlib.sha256(encoded(value).encode()).hexdigest()
def version(row,expected):
    if row['version']!=expected:raise HTTPException(409,'Документ или шаблон изменился. Обновите данные.')

def tables(db):
    db.executescript('''
    CREATE TABLE IF NOT EXISTS document_templates(seq INTEGER PRIMARY KEY AUTOINCREMENT,id TEXT UNIQUE,workspace TEXT,version INTEGER,active INTEGER,created TEXT,updated TEXT,document TEXT);
    CREATE INDEX IF NOT EXISTS document_template_workspace ON document_templates(workspace,seq);
    CREATE TABLE IF NOT EXISTS school_documents(seq INTEGER PRIMARY KEY AUTOINCREMENT,id TEXT UNIQUE,workspace TEXT,template_id TEXT,version INTEGER,status TEXT,archived INTEGER,kind TEXT,subject_id TEXT,subject_name TEXT,title TEXT,number TEXT,created TEXT,updated TEXT,issued TEXT,assignee_id TEXT,assignee_permissions TEXT,document TEXT,UNIQUE(workspace,number));
    CREATE INDEX IF NOT EXISTS document_workspace ON school_documents(workspace,seq);
    CREATE INDEX IF NOT EXISTS document_assignee ON school_documents(assignee_id,assignee_permissions,workspace,seq);
    CREATE INDEX IF NOT EXISTS document_subject ON school_documents(workspace,kind,subject_id,seq);
    CREATE TABLE IF NOT EXISTS document_recipients(document_id TEXT,user_id TEXT,permissions TEXT,PRIMARY KEY(document_id,user_id));
    CREATE INDEX IF NOT EXISTS document_recipient_owner ON document_recipients(user_id,permissions,document_id);
    CREATE TABLE IF NOT EXISTS document_counters(workspace TEXT,series TEXT,year INTEGER,next_number INTEGER,PRIMARY KEY(workspace,series,year));
    CREATE TABLE IF NOT EXISTS document_history(seq INTEGER PRIMARY KEY AUTOINCREMENT,workspace TEXT,entity TEXT,entity_id TEXT,actor_id TEXT,actor_name TEXT,created TEXT,action TEXT,reason TEXT,document TEXT);
    CREATE INDEX IF NOT EXISTS document_history_item ON document_history(workspace,entity,entity_id,seq);
    CREATE TABLE IF NOT EXISTS document_requests(seq INTEGER PRIMARY KEY AUTOINCREMENT,user_id TEXT,request_id TEXT,fingerprint TEXT,result TEXT,UNIQUE(user_id,request_id));
    CREATE INDEX IF NOT EXISTS document_request_owner ON document_requests(user_id,seq);
    ''')

class SchoolDocuments:
    def __init__(self,access,notifications,stamp):
        self.access,self.notifications=access,notifications
        self.snapshots=PortalSnapshots(access.read,stamp)

    def prepare(self):
        with closing(self.access.read_connect()) as db:
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='document_request_owner'").fetchone():return
        with self.access.lock,self.access.connect() as db:tables(db);self.notifications.tables(db)

    @contextmanager
    def context(self,request,workspace,write=False):
        user=request.state.user
        if not user:raise HTTPException(403,'Документы доступны после входа в аккаунт.')
        snap=self.snapshots.get(workspace)
        if not enabled(user,snap,workspace):raise HTTPException(403,'Нет доступа к документам этого пространства.')
        self.prepare()
        def check(db):
            row=db.execute('SELECT * FROM accounts WHERE id=? AND active=1',(user['id'],)).fetchone()
            rev=db.execute('SELECT revision FROM workspaces WHERE id=?',(workspace,)).fetchone()
            if not row or self.access.public(row)!=user or not rev or rev[0]!=snap.state.revision:raise HTTPException(409,'Доступ или школьные данные изменились. Обновите страницу.')
            db.row_factory=sqlite3.Row
        if write:
            with self.access.lock,self.access.connect() as db:
                db.execute('BEGIN IMMEDIATE');check(db);yield db,user,snap
        else:
            with closing(self.access.read_connect()) as db:
                db.execute('BEGIN');check(db);yield db,user,snap

    def admin(self,user):
        if user['role']!='admin':raise HTTPException(403,'Это действие выполняет администрация.')

    def account(self,db,id):
        row=db.execute('SELECT * FROM accounts WHERE id=? AND active=1',(id,)).fetchone()
        return self.access.public(row) if row else None

    def assignee(self,db,id,snap,workspace):
        if not id:return ''
        user=self.account(db,id)
        if not user or user['role'] not in STAFF or not enabled(user,snap,workspace):raise HTTPException(422,'Ответственный сотрудник больше не имеет доступа к пространству.')
        return permission_key(user)

    def template(self,db,workspace,id):
        row=db.execute('SELECT * FROM document_templates WHERE workspace=? AND id=?',(workspace,id)).fetchone()
        if not row:raise HTTPException(404,'Шаблон не найден.')
        return dict(row)|dict(definition=json.loads(row['document']))

    def document(self,db,workspace,id):
        row=db.execute('SELECT * FROM school_documents WHERE workspace=? AND id=?',(workspace,id)).fetchone()
        if not row:raise HTTPException(404,'Документ не найден.')
        return dict(row)|dict(content=json.loads(row['document']))

    def manager(self,row,user):
        return user['role']=='admin' or user['role'] in STAFF and row['assignee_id']==user['id'] and row['assignee_permissions']==permission_key(user)

    def subject(self,db,row,snap,workspace):
        if row['kind']=='internal':return dict(name='Внутренний документ',className='')
        if row['kind']=='student':
            pupil=snap.entities['student'].get(row['subject_id'])
            if not pupil or not pupil.active:raise HTTPException(422,'Ученик отсутствует или неактивен.')
            klass=snap.entities['class_'].get(pupil.classId)
            return dict(name=pupil.name,className=klass.name if klass else '')
        user=self.account(db,row['subject_id'])
        if not user or user['role'] not in STAFF or not enabled(user,snap,workspace):raise HTTPException(422,'Сотрудник больше не имеет доступа к пространству.')
        return dict(name=user['name'],className='')

    def own_subject(self,row,user,snap,workspace):
        if row['kind']=='employee':return user['role'] in STAFF and row['subject_id']==user['id']
        if row['kind']=='student' and user['role'] in ['student','parent']:
            pupil=snap.entities['student'].get(row['subject_id'])
            return bool(pupil and pupil.active and pupil.id in grant_ids(user,workspace,'student'))
        return False

    def require(self,db,row,user,snap,workspace,manage=False):
        if self.manager(row,user):return True
        if not manage and row['status'] in ['issued','revoked'] and self.own_subject(row,user,snap,workspace) and db.execute('SELECT 1 FROM document_recipients WHERE document_id=? AND user_id=? AND permissions=?',(row['id'],user['id'],permission_key(user))).fetchone():return False
        raise HTTPException(404,'Документ недоступен.')

    def summary(self,row):
        return dict(id=row['id'],version=row['version'],status=row['status'],archived=bool(row['archived']),kind=row['kind'],subjectName=row['subject_name'],title=row['title'],number=row['number'],created=row['created'],updated=row['updated'],issued=row['issued'])

    def public(self,row,manage=False):
        result=self.summary(row);doc=row['content']
        result.update(snapshot=doc.get('snapshot'),revocationReason=doc.get('revocationReason',''),canPrepare=manage)
        if manage:result.update(templateId=row['template_id'],subjectId=row['subject_id'],assigneeId=row['assignee_id'],definition=doc['definition'],values=doc['values'],templateVersion=doc['templateVersion'],recipients=doc.get('recipients',[]))
        return result

    def history(self,db,workspace,user,entity,id,action,reason,document):
        db.execute('INSERT INTO document_history(workspace,entity,entity_id,actor_id,actor_name,created,action,reason,document) VALUES (?,?,?,?,?,?,?,?,?)',(workspace,entity,id,user['id'],user['name'],stamp(),action,reason,encoded(document)))

    def save(self,db,row,user,action,reason=''):
        row['version']+=1;row['updated']=stamp()
        db.execute('UPDATE school_documents SET version=?,status=?,archived=?,subject_name=?,number=?,updated=?,issued=?,assignee_id=?,assignee_permissions=?,document=? WHERE id=?',(row['version'],row['status'],row['archived'],row['subject_name'],row['number'],row['updated'],row['issued'],row['assignee_id'],row['assignee_permissions'],encoded(row['content']),row['id']))
        result=self.public(row,True);self.history(db,row['workspace'],user,'document',row['id'],action,reason,result)
        return self.summary(row)

    def replay(self,db,user,body,operation):
        key=fingerprint([operation,body.model_dump(mode='json')]);row=db.execute('SELECT fingerprint,result FROM document_requests WHERE user_id=? AND request_id=?',(user['id'],body.requestId)).fetchone()
        if row:
            if row['fingerprint']!=key:raise HTTPException(409,'Идентификатор уже использован другим запросом.')
            return key,json.loads(row['result'])
        return key,None

    def remember(self,db,user,body,key,result):
        db.execute('INSERT INTO document_requests(user_id,request_id,fingerprint,result) VALUES (?,?,?,?)',(user['id'],body.requestId,key,encoded(result)))
        db.execute('DELETE FROM document_requests WHERE user_id=? AND seq NOT IN (SELECT seq FROM document_requests WHERE user_id=? ORDER BY seq DESC LIMIT 100)',(user['id'],user['id']))
        return result

    def recipients(self,db,row,snap,workspace):
        roles=['student','parent'] if row['kind']=='student' else list(STAFF) if row['kind']=='employee' else []
        result=[]
        for raw in db.execute('SELECT * FROM accounts WHERE active=1 AND role IN (SELECT value FROM json_each(?))',(encoded(roles),)):
            user=self.access.public(raw)
            if enabled(user,snap,workspace) and self.own_subject(row,user,snap,workspace):result.append(user)
        return sorted(result,key=lambda u:u['id'])

    def access_preview(self,db,row,snap,workspace):
        people=self.recipients(db,row,snap,workspace)
        existing=[dict(r) for r in db.execute('SELECT user_id,permissions FROM document_recipients WHERE document_id=? ORDER BY user_id',(row['id'],))]
        target=[dict(id=p['id'],name=p['name'],role=p['role']) for p in people]
        return dict(recipients=target,previousRecipients=row['content'].get('recipients',[]),previewHash=fingerprint([row['id'],row['version'],existing,[(p['id'],permission_key(p)) for p in people]])),people

    def issue_preview(self,db,row,snap,workspace):
        template=self.template(db,workspace,row['template_id'])
        if not template['active']:raise HTTPException(409,'Шаблон отключён. Администратор должен разрешить выдачу.')
        if row['status'] not in ['draft','submitted'] or row['archived']:raise HTTPException(409,'Документ нельзя выдать в этом состоянии.')
        doc=row['content'];values=values_for(doc['definition'],doc['values'],True);subject=self.subject(db,row,snap,workspace);day=today();series=doc['definition']['series']
        counter=db.execute('SELECT next_number FROM document_counters WHERE workspace=? AND series=? AND year=?',(workspace,series,day.year)).fetchone();number=counter[0] if counter else 1
        label=f'{series}/{day.year}/{number:06d}'
        fields=values|dict(schoolName=snap.state.settings.name,personName=subject['name'],className=subject['className'],schoolYear=f'{snap.state.settings.yearStart.year}/{snap.state.settings.yearEnd.year}',date=day.strftime('%d.%m.%Y'),number=label)
        snapshot=dict(title=doc['definition']['title'],text=TOKEN.sub(lambda m:fields.get(m[1],''),doc['definition']['text']),schoolName=fields['schoolName'],subjectName=subject['name'],number=label,date=day.isoformat())
        access,people=self.access_preview(db,row,snap,workspace)
        result=dict(snapshot=snapshot,recipients=access['recipients'],previewHash=fingerprint([row['id'],row['version'],snapshot,access['previewHash'],template['version']]))
        return result,people,(series,day.year,number)

    def install_recipients(self,db,row,people):
        row['content']['recipients']=[dict(id=p['id'],name=p['name'],role=p['role']) for p in people]
        db.execute('DELETE FROM document_recipients WHERE document_id=?',(row['id'],))
        db.executemany('INSERT INTO document_recipients VALUES (?,?,?)',[(row['id'],p['id'],permission_key(p)) for p in people])

    def notify(self,db,row,user,people,action):
        self.notifications.deliver(db,[p for p in people if p and p['id']!=user['id']],row['workspace'],row['version'],action)

    def listing(self,db,user,snap,workspace,before,limit,status='',archived=False,year=0):
        sql='SELECT * FROM school_documents d WHERE workspace=? AND seq<?';params=[workspace,before]
        if not archived:sql+=' AND archived=0'
        if status:sql+=' AND status=?';params.append(status)
        if year:sql+=' AND created>=? AND created<?';params.extend([f'{year:04d}-01-01',f'{year+1:04d}-01-01'])
        if user['role']!='admin':
            sql+=' AND ((assignee_id=? AND assignee_permissions=?) OR id IN (SELECT document_id FROM document_recipients WHERE user_id=? AND permissions=?))';params.extend([user['id'],permission_key(user),user['id'],permission_key(user)])
        rows=db.execute(sql+' ORDER BY seq DESC LIMIT ?',params+[limit+1]).fetchall();items=[]
        for raw in rows[:limit]:
            row=dict(raw)
            try:self.require(db,row,user,snap,workspace)
            except HTTPException:continue
            items.append(self.summary(row))
        return dict(items=items,nextBefore=rows[limit-1]['seq'] if len(rows)>limit else None)

    def html(self,row):
        snapshot=row['content']['snapshot'];revoked=row['status']=='revoked'
        warning='<aside><strong>ДОКУМЕНТ ОТОЗВАН</strong><p>'+escape(row['content'].get('revocationReason',''))+'</p></aside>' if revoked else ''
        return '<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>'+escape(snapshot['title'])+'</title><style>body{font:16px/1.5 serif;margin:24px auto;padding:20px;max-width:800px;color:#111}pre{white-space:pre-wrap;overflow-wrap:anywhere;font:inherit}aside{border:3px solid #a00;padding:16px;color:#a00}header{border-bottom:1px solid #777}footer{font-size:12px;color:#555;margin-top:32px}@media print{body{margin:0;padding:0}aside{color:#000;border-color:#000}}</style><body>'+warning+'<header><p>'+escape(snapshot['schoolName'])+'</p><h1>'+escape(snapshot['title'])+'</h1><p>№ '+escape(snapshot['number'])+' · '+escape(snapshot['date'])+'</p></header><pre>'+escape(snapshot['text'])+'</pre><footer>Сохранённая копия из Атласа. Статус выдачи следует проверять в школьном архиве. Электронная подпись не добавлена.</footer></body></html>'

    def register(self,app,Workspace):
        from .document_routes import register
        register(self,app,Workspace)
