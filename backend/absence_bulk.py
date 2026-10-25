"""Actor-bound, expiring previews and atomic, replay-safe absence decisions."""
from datetime import timedelta
import hashlib
import json
from typing import Literal
from uuid import uuid4

from fastapi import HTTPException, Request
from pydantic import Field, model_validator
from .access import now
from .models import Model
from .notifications import encoded
from .journal import user_ids


class Choice(Model):
    id: str = Field(pattern=r'^[a-f0-9]{32}$')
    version: int = Field(ge=1)


class Batch(Model):
    requestId: str = Field(pattern=r'^[a-f0-9-]{36}$')
    items: list[Choice] = Field(min_length=1,max_length=50)
    action: Literal['approve','reject','revoke']
    comment: str = Field(min_length=1,max_length=2000)

    @model_validator(mode='after')
    def valid(self):
        if not self.comment.strip():raise ValueError('Объясните общее решение.')
        if len({item.id for item in self.items})!=len(self.items):raise ValueError('Заявления не должны повторяться.')
        return self


def tables(db):
    db.execute('''CREATE TABLE IF NOT EXISTS absence_batches (
        id TEXT PRIMARY KEY,workspace TEXT,actor_id TEXT,request_id TEXT,request_hash TEXT,
        created TEXT,expires TEXT,payload TEXT,revision INTEGER,reviewer_version INTEGER,
        status TEXT,result TEXT,UNIQUE(workspace,actor_id,request_id))''')
    db.execute('CREATE INDEX IF NOT EXISTS absence_batches_actor ON absence_batches(actor_id,created)')


def notify_batch(service,db,rows,snap):
    """Resolve recipients once; each account receives at most one event per kind."""
    workspace=rows[0][1];students={r[3] for r in rows};authors={r[2] for r in rows}
    classes={p.classId for id in students if (p:=snap.entities['student'].get(id))}
    reviewers=service.reviewer_ids(db,workspace,snap,classes)
    journal_changed=rows[0][9] in ['approved','revoked']
    candidates=db.execute("""SELECT * FROM accounts a WHERE active=1 AND (role='admin'
        OR id IN (SELECT value FROM json_each(?)) OR (? AND role IN ('student','parent') AND EXISTS(
            SELECT 1 FROM json_each(a.permissions,'$.grants') g,json_each(g.value,'$.entityIds') s
            WHERE json_extract(g.value,'$.workspace')=? AND json_extract(g.value,'$.view')='student'
            AND s.value IN (SELECT value FROM json_each(?)))))""",
        (encoded(sorted(authors|reviewers)),journal_changed,workspace,encoded(sorted(students)))).fetchall()
    private_targets=[];journal_targets=[]
    for account in candidates:
        user=service.access.public(account);children=user_ids(user,workspace,'student')
        if (user['role']=='admin' or user['role'] in ['teacher','staff'] and user['id'] in reviewers and any(service.can_review(db,user,snap,workspace,r[3]) for r in rows)
            or user['role']=='parent' and any(r[2]==user['id'] and r[3] in children for r in rows)):
            private_targets.append(user)
        if journal_changed and user['role'] in ['student','parent'] and students & children:journal_targets.append(user)
    version=max(r[8] for r in rows)
    service.notifications.deliver(db,private_targets,workspace,version,'absence.updated')
    if journal_changed:service.notifications.deliver(db,journal_targets,workspace,version,'journal.updated')


def register(service,app,Workspace):
    from .absences import tables as notice_tables

    def context(request,workspace):
        user,snap=service.context(request,workspace)
        if user['role'] not in ['admin','teacher','staff']:raise HTTPException(403,'Общее решение доступно только ответственным сотрудникам.')
        return user,snap

    def rows_for(db,body,user,snap,workspace):
        # Resolve every permission before returning any private content or writing.
        return [service.find(db,item.id,user,snap,workspace) for item in body.items]

    def validate(db,body,rows,user,snap,workspace):
        if body.action in ['approve','revoke']:
            # The batch is all-or-nothing, so name every notice that touches a closed period at once.
            refused=[(row,closed) for row in rows if (closed:=service.closed_periods(db,workspace,row))]
            if refused:
                raise HTTPException(403,'Общее решение не применено: заявления затрагивают закрытые учебные периоды — '
                    +'; '.join(f"{row[5]}, {row[10].replace('T',' ')} — {row[11].replace('T',' ')}: {', '.join(p['name'] for p in closed)}" for row,closed in refused)
                    +'. Снимите их с выбора или попросите администратора открыть период с указанием причины.')
        return [service.decision_status(db,row,user,snap,workspace,item.version,body.action)
                for item,row in zip(body.items,rows)]

    def fresh(db,batch,snap,workspace):
        if batch[6]<=now().isoformat():raise HTTPException(409,'Предпросмотр истёк. Обновите заявления и подготовьте решение заново.')
        # Timetable saves do not matter: every row is re-resolved against the current pupils and assignments.
        if batch[9]!=service.config(db,workspace)['version']:
            raise HTTPException(409,'Назначения ответственных изменились. Подготовьте новый предпросмотр.')

    def prepared(db,batch,body,rows,user,snap,workspace):
        if batch[10]=='applied':return json.loads(batch[11])
        fresh(db,batch,snap,workspace)
        statuses=validate(db,body,rows,user,snap,workspace)
        return dict(id=batch[0],status='preview',expires=batch[6],action=body.action,comment=body.comment,
                    items=[service.detail(db,row,user,snap,workspace)|dict(nextStatus=status)
                           for row,status in zip(rows,statuses)])

    @app.post('/api/journal/absences/bulk/preview')
    def preview(body:Batch,request:Request,workspace:Workspace='school'):
        user,snap=context(request,workspace)
        payload=encoded(body.model_dump());fingerprint=hashlib.sha256(payload.encode()).hexdigest()
        with service.access.lock,service.access.connect() as db:
            notice_tables(db);service.files.tables(db);tables(db)
            db.execute('BEGIN IMMEDIATE');service.recheck(db,user,snap,workspace)
            previous=db.execute('SELECT * FROM absence_batches WHERE workspace=? AND actor_id=? AND request_id=?',(workspace,user['id'],body.requestId)).fetchone()
            if previous and previous[4]!=fingerprint:raise HTTPException(409,'Этот запрос уже использован для другого общего решения.')
            rows=rows_for(db,body,user,snap,workspace)
            if previous:return prepared(db,previous,body,rows,user,snap,workspace)
            validate(db,body,rows,user,snap,workspace)
            count=db.execute('SELECT count(*) FROM absence_batches WHERE actor_id=? AND created>?',(user['id'],(now()-timedelta(hours=1)).isoformat())).fetchone()[0]
            if count>=30:raise HTTPException(429,'До 30 новых общих решений в час. Повторите позже.')
            id=uuid4().hex;stamp=now()
            db.execute('INSERT INTO absence_batches VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                       (id,workspace,user['id'],body.requestId,fingerprint,stamp.isoformat(),(stamp+timedelta(minutes=15)).isoformat(),payload,snap.state.revision,service.config(db,workspace)['version'],'preview',None))
            batch=db.execute('SELECT * FROM absence_batches WHERE id=?',(id,)).fetchone()
            service.access.record(db,'absence.bulk_preview',workspace,detail=id)
            return prepared(db,batch,body,rows,user,snap,workspace)

    @app.post('/api/journal/absences/bulk/{id}/apply')
    def apply(id:str,request:Request,workspace:Workspace='school'):
        user,snap=context(request,workspace)
        with service.access.lock,service.access.connect() as db:
            notice_tables(db);tables(db)
            db.execute('BEGIN IMMEDIATE');service.recheck(db,user,snap,workspace)
            batch=db.execute('SELECT * FROM absence_batches WHERE id=? AND workspace=? AND actor_id=?',(id,workspace,user['id'])).fetchone()
            if not batch:raise HTTPException(404,'Предпросмотр не найден.')
            body=Batch.model_validate_json(batch[7]);rows=rows_for(db,body,user,snap,workspace)
            if batch[10]=='applied':return json.loads(batch[11])
            fresh(db,batch,snap,workspace)
            statuses=validate(db,body,rows,user,snap,workspace)
            # All writes, history, notifications and the retry receipt commit together.
            changed=[service.apply_decision(db,row,user,snap,workspace,status,body.comment,'batch:'+id,batch[4],notify=False)
                     for row,status in zip(rows,statuses)]
            notify_batch(service,db,changed,snap)
            result=dict(id=id,status='applied',count=len(changed),items=[service.item(row) for row in changed])
            db.execute("UPDATE absence_batches SET status='applied',result=? WHERE id=?",(encoded(result),id))
            service.access.record(db,'absence.bulk_applied',workspace,detail=id)
            return result
