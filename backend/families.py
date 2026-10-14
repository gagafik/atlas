"""School-maintained guardian relationships and time-bounded parent grants.

The register keeps private evidence/history. Compact date rules in account
permissions let every consumer enforce expiry without extra database reads.
"""
from datetime import date
import json
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import HTTPException, Query, Request
from pydantic import Field, model_validator

from .access import actor, now
from .models import Model
from .notifications import encoded


def today(): return now().astimezone(ZoneInfo('Asia/Qyzylorda')).date().isoformat()


def effective_grants(permissions):
    rules={(r['workspace'],r['studentId']):r for r in permissions.get('familyRules',[])}
    day=today();result=[]
    for grant in permissions['grants']:
        ids=[]
        for id in grant['entityIds']:
            rule=rules.get((grant['workspace'],id)) if grant['view']=='student' else None
            if not rule or (not rule['startDate'] or rule['startDate']<=day) and (not rule['endDate'] or day<=rule['endDate']): ids.append(id)
        if ids: result.append(grant|dict(entityIds=ids))
    return result


def tables(db):
    db.execute('''CREATE TABLE IF NOT EXISTS family_links (
        workspace TEXT, parent_id TEXT, student_id TEXT, student_name TEXT,
        version INTEGER, document TEXT, PRIMARY KEY(workspace,parent_id,student_id))''')
    db.execute('CREATE INDEX IF NOT EXISTS family_owner ON family_links(parent_id,workspace)')
    db.execute('''CREATE TABLE IF NOT EXISTS family_history (
        id INTEGER PRIMARY KEY,workspace TEXT,parent_id TEXT,student_id TEXT,version INTEGER,
        created TEXT,actor_id TEXT,actor_name TEXT,source TEXT,document TEXT)''')
    db.execute('CREATE INDEX IF NOT EXISTS family_history_link ON family_history(workspace,parent_id,student_id,id)')


def assigned(permissions):
    return {(g['workspace'],s) for g in permissions['grants'] if g['view']=='student' for s in g['entityIds']}


def base():
    return dict(relationship='unspecified',verified=False,evidence='',startDate=None,endDate=None,status='active',reason='Назначено школой; сведения о полномочиях ещё не уточнены.')


def write_link(db,parent,workspace,student,name,version,document,source,principal):
    document=dict(document)
    db.execute('INSERT INTO family_links VALUES (?,?,?,?,?,?) ON CONFLICT(workspace,parent_id,student_id) DO UPDATE SET student_name=excluded.student_name,version=excluded.version,document=excluded.document',(workspace,parent,student,name,version,encoded(document)))
    db.execute('INSERT INTO family_history (workspace,parent_id,student_id,version,created,actor_id,actor_name,source,document) VALUES (?,?,?,?,?,?,?,?,?)',(workspace,parent,student,version,now().isoformat(),principal['id'],principal['name'],source,encoded(document)))


def student_names(db,spaces):
    names={}
    for space in spaces:
        row=db.execute('SELECT document FROM workspaces WHERE id=?',(space,)).fetchone()
        if row:
            names.update({(space,s['id']):s['name'] for s in json.loads(row[0]).get('students',[])})
    return names


def sync_account(db,parent,role,permissions,*,source,principal=None,previous=None):
    """Called in the same transaction as account/invitation writes; never regrant a revoked link implicitly."""
    tables(db);principal=principal or actor.get()
    incoming=assigned(permissions) if role=='parent' else set()
    if previous and previous[3]=='parent':
        old=assigned(json.loads(previous[7]));old_names=student_names(db,{space for space,_ in old})
        for key in old:
            if not db.execute('SELECT 1 FROM family_links WHERE workspace=? AND parent_id=? AND student_id=?',(key[0],parent,key[1])).fetchone():
                write_link(db,parent,*key,old_names.get(key,key[1]),0,base(),'family.legacy_import',principal)
    rows=db.execute('SELECT workspace,student_id,student_name,version,document FROM family_links WHERE parent_id=?',(parent,)).fetchall()
    existing={(r[0],r[1]):r for r in rows}
    names=student_names(db,{space for space,_ in incoming})
    for key in incoming:
        row=existing.get(key)
        if row and json.loads(row[4])['status']=='revoked':
            raise HTTPException(409,'Связь с ребёнком отозвана. Восстановите её явно в реестре семей; приглашение или редактирование аккаунта не снимает ограничение.')
        if not row:
            if key not in names:raise HTTPException(422,'Запись ученика изменилась. Обновите назначения родителя.')
            doc=base()
            write_link(db,parent,*key,names.get(key,key[1]),1,doc,source,principal)
    for key,row in existing.items():
        doc=json.loads(row[4])
        if key not in incoming and doc['status']!='revoked':
            write_link(db,parent,*key,row[2],row[3]+1,doc|dict(status='revoked',reason='Доступ снят в настройках аккаунта.'),'account.revoked',principal)
    rules=[]
    if role=='parent':
        for space,student,document in db.execute('SELECT workspace,student_id,document FROM family_links WHERE parent_id=?',(parent,)):
            doc=json.loads(document)
            if (space,student) in incoming and doc['status']=='active': rules.append(dict(workspace=space,studentId=student,startDate=doc['startDate'],endDate=doc['endDate']))
    return {k:v for k,v in permissions.items() if k!='familyRules'} | (dict(familyRules=rules) if role=='parent' else {})


class Link(Model):
    version: int = Field(ge=0)
    accountVersion: int = Field(ge=0)
    relationship: Literal['unspecified','mother','father','guardian','representative']='unspecified'
    verified: bool=False
    evidence: str=Field(default='',max_length=2000)
    startDate: date | None=None
    endDate: date | None=None
    status: Literal['active','revoked']='active'
    reason: str=Field(min_length=1,max_length=1000)

    @model_validator(mode='after')
    def check(self):
        if not self.reason.strip(): raise ValueError('Укажите причину изменения.')
        if self.startDate and self.endDate and self.endDate<self.startDate: raise ValueError('Окончание доступа не может быть раньше начала.')
        if self.verified and (not self.evidence.strip() or self.relationship=='unspecified'): raise ValueError('Для подтверждения укажите отношение и основание проверки школой.')
        return self


class Families:
    def __init__(self,access,notifications):self.access,self.notifications=access,notifications

    def admin(self,db,request):
        user=request.state.user
        row=db.execute('SELECT role,active,version FROM accounts WHERE id=?',(user['id'],)).fetchone() if user else None
        if not row or row[0]!='admin' or not row[1] or row[2]!=user['version']: raise HTTPException(403,'Реестр семей доступен действующему администратору.')
        return user

    @staticmethod
    def activity(doc):
        day=today()
        if doc['status']=='revoked':return 'revoked'
        if doc['startDate'] and day<doc['startDate']:return 'scheduled'
        if doc['endDate'] and day>doc['endDate']:return 'expired'
        return 'active'

    def rows(self,db,workspace,parent=None,*,q='',students=(),offset=0,limit=51):
        # Legacy account grants are virtual, unverified links until a school edit.
        # Reading the register never rewrites the school's accounts or grants.
        db.create_function('casefold',1,lambda value:(value or '').casefold(),deterministic=True)
        return db.execute('''WITH links AS (
            SELECT f.parent_id,f.student_id,f.student_name,f.version,f.document FROM family_links f WHERE f.workspace=? AND (? IS NULL OR f.parent_id=?)
            UNION ALL
            SELECT a.id,s.value,'',0,NULL FROM accounts a,json_each(a.permissions,'$.grants') g,json_each(g.value,'$.entityIds') s
            WHERE a.role='parent' AND (? IS NULL OR a.id=?) AND json_extract(g.value,'$.workspace')=? AND json_extract(g.value,'$.view')='student'
            AND NOT EXISTS(SELECT 1 FROM family_links f WHERE f.workspace=? AND f.parent_id=a.id AND f.student_id=s.value))
            SELECT l.*,a.name,a.login,a.active,a.role,a.version FROM links l JOIN accounts a ON a.id=l.parent_id
            WHERE (instr(casefold(a.name||' '||a.login||' '||l.student_name),?)>0 OR l.student_id IN (SELECT value FROM json_each(?)))
            ORDER BY a.name,l.parent_id,l.student_id LIMIT ? OFFSET ?''',(workspace,parent,parent,parent,parent,workspace,workspace,q.casefold(),encoded(list(students)),limit,offset)).fetchall()

    def item(self,row,names,workspace,*,private=False):
        parent,student,saved_name,version,raw,name,login,active,role,account_version=row
        doc=json.loads(raw) if raw else base()
        result=dict(parentId=parent,studentId=student,studentName=names.get((workspace,student),saved_name or student),parentName=name,
                    version=version,accountVersion=account_version,accountActive=bool(active and role=='parent'),relationship=doc['relationship'],verified=doc['verified'],
                    startDate=doc['startDate'],endDate=doc['endDate'],status=doc['status'],activity=self.activity(doc),legacy=raw is None)
        if private:result.update(evidence=doc['evidence'],reason=doc['reason'],login=login)
        return result

    def register(self,app,Workspace):
        @app.get('/api/access/families')
        def listing(request:Request,workspace:Workspace='school',q:str=Query(default='',max_length=100),offset:int=Query(default=0,ge=0,le=100000)):
            with self.access.lock,self.access.connect() as db:
                self.admin(db,request);tables(db)
                names=student_names(db,[workspace])
                students=[id for (space,id),name in names.items() if q.casefold() in name.casefold()]
                rows=self.rows(db,workspace,q=q,students=students,offset=offset)
                return dict(items=[self.item(r,names,workspace) for r in rows[:50]],nextOffset=offset+50 if len(rows)>50 else None)

        @app.get('/api/access/families/{parent}/{student}')
        def detail(parent:str,student:str,request:Request,workspace:Workspace='school'):
            with self.access.lock,self.access.connect() as db:
                self.admin(db,request);tables(db)
                row=db.execute('''SELECT f.parent_id,f.student_id,f.student_name,f.version,f.document,a.name,a.login,a.active,a.role,a.version
                    FROM family_links f JOIN accounts a ON a.id=f.parent_id WHERE f.workspace=? AND f.parent_id=? AND f.student_id=?''',(workspace,parent,student)).fetchone()
                if not row:
                    account=db.execute('SELECT name,login,active,role,version,permissions FROM accounts WHERE id=?',(parent,)).fetchone()
                    names=student_names(db,[workspace])
                    if not account or account[3]!='parent' or (workspace,student) not in names and (workspace,student) not in assigned(json.loads(account[5])):raise HTTPException(404,'Родитель или ученик не найден.')
                    row=(parent,student,names.get((workspace,student),student),0,None,*account[:5])
                self.access.record(db,'family.evidence_read',workspace,detail=parent+' · '+student)
                return self.item(row,student_names(db,[workspace]),workspace,private=True)

        @app.get('/api/access/families/{parent}/{student}/history')
        def history(parent:str,student:str,request:Request,workspace:Workspace='school',before:int|None=Query(default=None,ge=1)):
            with self.access.lock,self.access.connect() as db:
                self.admin(db,request);tables(db)
                rows=db.execute('SELECT id,version,created,actor_name,source,document FROM family_history WHERE workspace=? AND parent_id=? AND student_id=? AND (? IS NULL OR id<?) ORDER BY id DESC LIMIT 51',(workspace,parent,student,before,before)).fetchall()
                self.access.record(db,'family.history_read',workspace,detail=parent+' · '+student)
                return dict(items=[dict(id=r[0],version=r[1],created=r[2],actor=r[3],source=r[4],document=json.loads(r[5])) for r in rows[:50]],nextBefore=rows[49][0] if len(rows)>50 else None)

        @app.put('/api/access/families/{parent}/{student}')
        def save(parent:str,student:str,body:Link,request:Request,workspace:Workspace='school'):
            with self.access.lock,self.access.connect() as db:
                tables(db);db.execute('BEGIN IMMEDIATE');principal=self.admin(db,request)
                row=db.execute('SELECT * FROM accounts WHERE id=?',(parent,)).fetchone()
                if not row or row[3]!='parent':raise HTTPException(422,'Выберите аккаунт родителя.')
                if row[5]!=body.accountVersion:raise HTTPException(409,'Аккаунт изменился. Обновите реестр и список пользователей.')
                current=db.execute('SELECT version,document FROM family_links WHERE workspace=? AND parent_id=? AND student_id=?',(workspace,parent,student)).fetchone()
                if (current[0] if current else 0)!=body.version:raise HTTPException(409,'Связь изменена другим администратором. Обновите реестр.')
                school=db.execute('SELECT document FROM workspaces WHERE id=?',(workspace,)).fetchone()
                pupil=next((s for s in json.loads(school[0]).get('students',[]) if s['id']==student),None) if school else None
                permissions=json.loads(row[7])
                if body.status=='active' and (not pupil or not pupil['active']):raise HTTPException(422,'Для доступа выберите действующего ученика.')
                if not pupil and not current and (workspace,student) not in assigned(permissions):raise HTTPException(404,'Ученик не найден.')
                doc=body.model_dump(mode='json',exclude={'version','accountVersion'})
                grants={g['workspace']:set(g['entityIds']) for g in permissions['grants'] if g['view']=='student'}
                if not current and (workspace,student) in assigned(permissions):
                    write_link(db,parent,workspace,student,pupil['name'] if pupil else student,0,base(),'family.legacy_import',principal)
                if body.status=='active':grants.setdefault(workspace,set()).add(student)
                else:grants.setdefault(workspace,set()).discard(student)
                if any(len(ids)>200 for ids in grants.values()):raise HTTPException(422,'Слишком много учеников в одном аккаунте.')
                permissions['grants']=[dict(workspace=space,view='student',entityIds=sorted(ids)) for space,ids in sorted(grants.items()) if ids]
                write_link(db,parent,workspace,student,pupil['name'] if pupil else student,body.version+1,doc,'family.updated',principal)
                permissions=sync_account(db,parent,'parent',permissions,source='family.legacy_import',principal=principal)
                db.execute('UPDATE accounts SET permissions=?,version=version+1 WHERE id=?',(encoded(permissions),parent))
                # Other sessions keep their identity, but every request reads fresh grants.
                if row[4]:
                    updated=self.access.public(db.execute('SELECT * FROM accounts WHERE id=?',(parent,)).fetchone())
                    self.notifications.deliver(db,[updated],workspace,body.version+1,'family.updated')
                self.access.record(db,'family.updated',workspace,detail=parent+' · '+student+' · '+body.status)
            return dict(ok=True,version=body.version+1,accountVersion=body.accountVersion+1)

        @app.get('/api/portal/family')
        def mine(request:Request,offset:int=Query(default=0,ge=0,le=100000)):
            user=request.state.user
            if not user or user['role']!='parent':raise HTTPException(403,'Этот раздел доступен родителю.')
            with self.access.lock,self.access.connect() as db:
                tables(db);items=[];has_more=False
                for space in ['school','demo']:
                    names=student_names(db,[space])
                    rows=self.rows(db,space,user['id'],offset=offset)
                    has_more=has_more or len(rows)>50
                    items.extend(self.item(r,names,space)|dict(workspace=space) for r in rows[:50])
                return dict(items=items,nextOffset=offset+50 if has_more else None,timezone='Asia/Qyzylorda')
