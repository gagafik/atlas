"""Reviewed, atomic invitation batches. Receipts retain metadata, never raw codes."""
from datetime import timedelta
import json
import secrets
from typing import Literal

from fastapi import HTTPException, Query, Request
from pydantic import Field, model_validator
from .access import digest, now
from .account_grants import candidates
from .invitations import Invitation, public, validate_targets
from .models import Model


class Row(Model):
    key: str = Field(pattern=r'^[a-zA-Z0-9_-]{1,80}$')
    recipient: str = Field(min_length=1, max_length=160)
    role: Literal['parent','student','teacher']
    entityIds: list[str] = Field(min_length=1, max_length=200)


class Draft(Model):
    workspace: Literal['school','demo']
    days: int = Field(default=7, ge=1, le=30)
    rows: list[Row] = Field(min_length=1, max_length=50)

    @model_validator(mode='after')
    def valid(self):
        if len({row.key for row in self.rows})!=len(self.rows): raise ValueError('Строки пакета не должны повторяться.')
        if sum(len(row.entityIds) for row in self.rows)>500: raise ValueError('В пакете допускается до 500 назначений школьных записей.')
        identities=set();targets=set()
        for row in self.rows:
            Invitation(recipient=row.recipient,role=row.role,days=self.days,grants=[grant(self.workspace,row)])
            identity=(row.role,row.recipient.casefold(),tuple(sorted(row.entityIds)))
            if identity in identities: raise ValueError('В пакете повторяется получатель с тем же доступом.')
            identities.add(identity)
            if row.role!='parent':
                for id in row.entityIds:
                    if (row.role,id) in targets: raise ValueError('Один ученик или преподаватель повторяется в нескольких строках пакета.')
                    targets.add((row.role,id))
        return self


class Issue(Draft):
    requestId: str = Field(pattern=r'^[a-zA-Z0-9_-]{16,80}$')
    previewToken: str = Field(pattern=r'^[a-f0-9]{64}$')
    acknowledgedRows: list[str] = Field(default_factory=list, max_length=50)


def grant(workspace,row):
    return dict(workspace=workspace,view='teacher' if row.role=='teacher' else 'student',entityIds=row.entityIds)


def encoded(value): return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'))


def tables(db):
    db.execute('''CREATE TABLE IF NOT EXISTS invitation_batches (
        id INTEGER PRIMARY KEY,request_id TEXT UNIQUE NOT NULL,creator_id TEXT NOT NULL,
        workspace TEXT NOT NULL,request_hash TEXT NOT NULL,created TEXT NOT NULL)''')
    db.execute('CREATE INDEX IF NOT EXISTS invitation_batch_workspace ON invitation_batches(workspace,id)')
    db.execute('''CREATE TABLE IF NOT EXISTS invitation_batch_items (
        batch_id INTEGER NOT NULL,row_key TEXT NOT NULL,invitation_id INTEGER NOT NULL UNIQUE,
        targets TEXT NOT NULL,PRIMARY KEY(batch_id,row_key))''')


def preview(db,access,body):
    current=db.execute('SELECT revision,document FROM workspaces WHERE id=?',(body.workspace,)).fetchone()
    if not current: raise HTTPException(422,'Рабочее пространство не найдено. Обновите страницу.')
    document=json.loads(current[1]);classes={c['id']:c['name'] for c in document['classes']}
    students={s['id']:s for s in document['students']};teachers={t['id']:t for t in document['teachers']}
    all_ids={id for row in body.rows for id in row.entityIds}
    accounts=[]
    for view,roles in [('student',['student','parent']),('teacher',['teacher'])]:
        ids={id for row in body.rows if ('teacher' if row.role=='teacher' else 'student')==view for id in row.entityIds}
        if not ids: continue
        for account in candidates(db,body.workspace,view,ids,roles):
            effective=access.public(account)
            assigned={id for g in effective['grants'] if g['workspace']==body.workspace and g['view']==view for id in g['entityIds']}
            if assigned & ids: accounts.append((effective,assigned))
    pending=[]
    for row in db.execute('''SELECT i.* FROM access_invitations i WHERE revoked IS NULL AND accepted IS NULL AND expires>?
        AND EXISTS(SELECT 1 FROM json_each(i.permissions,'$.grants') g,json_each(g.value,'$.entityIds') e
            WHERE json_extract(g.value,'$.workspace')=? AND e.value IN (SELECT value FROM json_each(?)))''',
        (now().isoformat(),body.workspace,encoded(sorted(all_ids)))):
        item=public(row)
        assigned={id for g in item['grants'] if g['workspace']==body.workspace for id in g['entityIds']}
        pending.append((item,assigned))
    result=[];sources=[]
    for ordinal,row in enumerate(body.rows,1):
        permissions=[grant(body.workspace,row)]
        try: validate_targets(db,permissions,documents={body.workspace:document})
        except HTTPException as exc: raise HTTPException(exc.status_code,f'Строка {ordinal}: {exc.detail}') from None
        records=teachers if row.role=='teacher' else students
        targets=[dict(id=id,name=records[id]['name'],className=classes.get(records[id].get('classId'),'')) for id in row.entityIds]
        ids=set(row.entityIds)
        matches=[u for u,assigned in accounts if u['role']==row.role and assigned & ids]
        waiting=[i for i,assigned in pending if i['role']==row.role and assigned & ids]
        warnings=[]
        if matches: warnings.append(dict(kind='accounts',count=len(matches),names=[u['name'] for u in matches[:3]]))
        if waiting: warnings.append(dict(kind='invitations',count=len(waiting),names=[i['recipient'] for i in waiting[:3]]))
        result.append(dict(key=row.key,recipient=row.recipient,role=row.role,targets=targets,warnings=warnings))
        sources.append(dict(key=row.key,accounts=sorted((u['id'],u['version']) for u in matches),invitations=sorted(i['id'] for i in waiting)))
    token=digest(encoded(dict(draft=Draft.model_validate(body.model_dump(include={'workspace','days','rows'})).model_dump(),
                              revision=current[0],items=result,sources=sources)))
    return dict(previewToken=token,items=result)


def receipt(db,id):
    batch=db.execute('SELECT id,workspace,created FROM invitation_batches WHERE id=?',(id,)).fetchone()
    if not batch: raise HTTPException(404,'Пакет приглашений не найден.')
    items=[]
    for key,invitation_id,targets in db.execute('SELECT row_key,invitation_id,targets FROM invitation_batch_items WHERE batch_id=? ORDER BY invitation_id',(id,)):
        row=db.execute('SELECT * FROM access_invitations WHERE id=?',(invitation_id,)).fetchone()
        items.append(public(row)|dict(key=key,targets=json.loads(targets)))
    return dict(id=batch[0],workspace=batch[1],created=batch[2],items=items)


def register(app,access,transaction,admin):
    @app.post('/api/access/invitations/batches/preview')
    def review(body:Draft,request:Request):
        with transaction() as db:
            admin(db,request)
            return preview(db,access,body)

    @app.post('/api/access/invitations/batches')
    def issue(body:Issue,request:Request):
        with transaction() as db:
            admin(db,request);tables(db)
            request_hash=digest(encoded(body.model_dump(exclude={'requestId'})))
            previous=db.execute('SELECT id,creator_id,request_hash FROM invitation_batches WHERE request_id=?',(body.requestId,)).fetchone()
            if previous:
                if previous[1]!=request.state.user['id'] or previous[2]!=request_hash:
                    raise HTTPException(409,'Номер запроса уже использован для другого пакета.')
                return receipt(db,previous[0])|dict(replayed=True)
            checked=preview(db,access,body)
            if checked['previewToken']!=body.previewToken:
                raise HTTPException(409,'Данные школы, аккаунты или приглашения изменились. Повторите проверку списка.')
            warnings={item['key'] for item in checked['items'] if item['warnings']}
            acknowledged=set(body.acknowledgedRows)
            if len(acknowledged)!=len(body.acknowledgedRows) or acknowledged!=warnings:
                raise HTTPException(422,'Подтвердите каждую строку с существующим аккаунтом или приглашением.')
            created=now();expires=(created+timedelta(days=body.days)).isoformat()
            batch_id=db.execute('INSERT INTO invitation_batches (request_id,creator_id,workspace,request_hash,created) VALUES (?,?,?,?,?)',
                (body.requestId,request.state.user['id'],body.workspace,request_hash,created.isoformat())).lastrowid
            codes={}
            for row,item in zip(body.rows,checked['items']):
                raw=secrets.token_urlsafe(32)
                permissions=dict(workspaces=[body.workspace],grants=[grant(body.workspace,row)])
                invitation_id=db.execute('''INSERT INTO access_invitations
                    (token_hash,recipient,role,permissions,created,expires,creator_id) VALUES (?,?,?,?,?,?,?)''',
                    (digest(raw),row.recipient,row.role,encoded(permissions),created.isoformat(),expires,request.state.user['id'])).lastrowid
                db.execute('INSERT INTO invitation_batch_items VALUES (?,?,?,?)',(batch_id,row.key,invitation_id,encoded(item['targets'])))
                codes[invitation_id]=raw
            access.record(db,'invitations.batch.created',detail=f'Пакет {batch_id} · {len(body.rows)} приглашений')
            result=receipt(db,batch_id)
            for item in result['items']: item['code']=codes[item['id']]
            return result|dict(replayed=False)

    @app.get('/api/access/invitations/batches')
    def listing(request:Request,workspace:Literal['school','demo']='school',before:int|None=Query(default=None,ge=1)):
        with transaction() as db:
            admin(db,request);tables(db)
            rows=db.execute('''SELECT b.id,b.created,count(i.invitation_id) FROM invitation_batches b
                JOIN invitation_batch_items i ON i.batch_id=b.id WHERE b.workspace=? AND (? IS NULL OR b.id<?)
                GROUP BY b.id ORDER BY b.id DESC LIMIT 21''',(workspace,before,before)).fetchall()
            return dict(items=[dict(id=id,created=created,count=count) for id,created,count in rows[:20]],nextBefore=rows[19][0] if len(rows)>20 else None)

    @app.get('/api/access/invitations/batches/{id}')
    def get(id:int,request:Request):
        with transaction() as db:
            admin(db,request);tables(db)
            return receipt(db,id)

    @app.post('/api/access/invitations/batches/{id}/revoke')
    def revoke(id:int,request:Request):
        with transaction() as db:
            admin(db,request);tables(db);receipt(db,id)
            changed=db.execute('''UPDATE access_invitations SET revoked=? WHERE id IN
                (SELECT invitation_id FROM invitation_batch_items WHERE batch_id=?) AND accepted IS NULL AND revoked IS NULL AND expires>?''',
                (now().isoformat(),id,now().isoformat())).rowcount
            if changed: access.record(db,'invitations.batch.revoked',detail=f'Пакет {id} · отозвано {changed}')
            return receipt(db,id)|dict(revokedCount=changed)
