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
from .absences import notice_title, notice_period, notice_target, attendance_target
from . import absence_codes


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
    # Previews of school notes: the payload is a SchoolNotes document, the result the receipt of the apply.
    db.execute('''CREATE TABLE IF NOT EXISTS absence_note_batches (
        id TEXT PRIMARY KEY,workspace TEXT,actor_id TEXT,request_id TEXT,request_hash TEXT,
        created TEXT,expires TEXT,payload TEXT,status TEXT,result TEXT,UNIQUE(workspace,actor_id,request_id))''')
    db.execute('CREATE INDEX IF NOT EXISTS absence_note_batches_actor ON absence_note_batches(actor_id,created)')


def notify_batch(service,db,rows,snap):
    """Resolve recipients once; each account receives at most one event per kind."""
    workspace=rows[0][1];students={r[3] for r in rows};authors={r[2] for r in rows}
    classes={p.classId for id in students if (p:=snap.entities['student'].get(id))}
    reviewers=service.reviewer_ids(db,workspace,snap,classes)
    journal_changed=rows[0][9] in ['approved','revoked']
    candidates=db.execute("""SELECT * FROM accounts a WHERE active=1 AND (role='admin'
        OR id IN (SELECT value FROM json_each(?)) OR ((role='parent' OR ? AND role='student') AND EXISTS(
            SELECT 1 FROM json_each(a.permissions,'$.grants') g,json_each(g.value,'$.entityIds') s
            WHERE json_extract(g.value,'$.workspace')=? AND json_extract(g.value,'$.view')='student'
            AND s.value IN (SELECT value FROM json_each(?)))))""",
        (encoded(sorted(authors|reviewers)),journal_changed,workspace,encoded(sorted(students)))).fetchall()
    private_targets=[];journal_targets=[];own={}
    for account in candidates:
        user=service.access.public(account);children=user_ids(user,workspace,'student')
        mine=(list(rows) if user['role']=='admin' else [r for r in rows if service.can_review(db,user,snap,workspace,r[3])] if user['role'] in ['teacher','staff'] and user['id'] in reviewers
              else [r for r in rows if service.level(db,user,snap,workspace,r)=='full'] if user['role']=='parent' else [])
        if mine:private_targets.append(user);own[user['id']]=mine
        if journal_changed and user['role'] in ['student','parent'] and students & children:journal_targets.append(user)
    version=max(r[8] for r in rows)
    title=lambda mine:notice_title('absence.updated',mine[0]) if len(mine)==1 else f'Решения по заявлениям об отсутствии: {len(mine)}'
    service.notifications.deliver(db,private_targets,workspace,version,'absence.updated',target=lambda u:notice_target(title(own[u['id']]),own[u['id']]))
    if journal_changed:service.notifications.deliver(db,journal_targets,workspace,version,'journal.updated',target=attendance_target(workspace,rows))


def notify_notes(service,db,rows,snap,actor):
    """Notes entered by the school: reviewers with the full level, then the family. One event of each kind per account; never the actor."""
    workspace=rows[0][1];students=sorted({r[3] for r in rows})
    classes={p.classId for id in students if (p:=snap.entities['student'].get(id))}
    reviewers=service.reviewer_ids(db,workspace,snap,classes)
    staff=db.execute("SELECT * FROM accounts WHERE active=1 AND (role='admin' OR id IN (SELECT value FROM json_each(?)))",(encoded(sorted(reviewers)),)).fetchall()
    family=service.notifications.granted_users(db,workspace,'student',students,['parent','student'])
    recorded={};journal={};own={}
    for user in [service.access.public(r) for r in staff]+family:
        if user['id']==actor['id']:continue
        seen={r[0]:service.level(db,user,snap,workspace,r) for r in rows};levels=set(seen.values())
        own[user['id']]=[r for r in rows if seen[r[0]]]
        if 'full' in levels:recorded[user['id']]=user
        # A pupil and every guardian with current access learn at least the summary and see the changed attendance.
        if user['role'] in ['parent','student'] and levels-{None}:recorded[user['id']]=user;journal[user['id']]=user
    title=lambda mine:f'Школа внесла записку об отсутствии: {notice_period(mine[0])}' if len(mine)==1 else f'Школа внесла записки об отсутствии: {len(mine)}'
    service.notifications.deliver(db,list(recorded.values()),workspace,1,'absence.recorded',target=lambda u:notice_target(title(own[u['id']]),own[u['id']]))
    service.notifications.deliver(db,list(journal.values()),workspace,1,'journal.updated',target=attendance_target(workspace,rows))


def register(service,app,Workspace):
    from .absences import tables as notice_tables, SchoolNotes

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

    # Notes entered by the school. The preview reports a result for every pupil; applying is all-or-nothing on the previewed set,
    # so a team is never left partly excused and one receipt, one notification per recipient cover the whole operation.
    states=dict(pending='на рассмотрении',approved='принята')

    def locked(db,workspace,body):
        periods=service.closed_range(db,workspace,body.startDate.isoformat(),body.endDate.isoformat())
        if periods:raise HTTPException(403,'Записка изменит посещаемость закрытого учебного периода: '+', '.join(p['name'] for p in periods)+'. Администратор должен сначала открыть период с указанием причины.')

    def problem(db,workspace,pupil,start,end,subject):
        if not pupil.active:return 'Ученик выбыл; записку внести нельзя.'
        status=service.overlap(db,workspace,pupil.id,start,end,subject)
        return f'Уже есть записка за этот период: {states[status]}. Рассмотрите её вместо новой.' if status else None

    def outcome(db,workspace,snap,body,pupils,subject):
        start,end=body.bounds();lessons=service.lessons(snap,[p.id for p in pupils],body,subject)
        return [dict(studentId=p.id,studentName=p.name,classId=p.classId,className=c.name if (c:=snap.entities['class_'].get(p.classId)) else '',lessonCount=len(lessons[p.id]),
                     lessons=[{k:r[k] for k in ['date','start','end','subject']} for r in sorted(lessons[p.id],key=lambda r:(r['date'],r['start']))[:20]],
                     problem=problem(db,workspace,p,start,end,subject)) for p in pupils]

    def shown(body,subject,items,**values):
        start,end=body.bounds()
        return dict(status='preview',start=start,end=end,codeId=body.codeId,subjectId=subject or '',reason=body.reason,items=items)|values

    @app.post('/api/journal/absences/school/preview')
    def school_preview(body:SchoolNotes,request:Request,workspace:Workspace='school'):
        user,snap=context(request,workspace);service.in_year(snap,body);subject=service.subject_of(snap,body)
        payload=encoded(body.model_dump(mode='json'));fingerprint=hashlib.sha256(payload.encode()).hexdigest()
        with service.access.lock,service.access.connect() as db:
            notice_tables(db);tables(db)
            db.execute('BEGIN IMMEDIATE');service.recheck(db,user,snap,workspace)
            previous=db.execute('SELECT * FROM absence_note_batches WHERE workspace=? AND actor_id=? AND request_id=?',(workspace,user['id'],body.requestId)).fetchone()
            if previous and previous[4]!=fingerprint:raise HTTPException(409,'Этот запрос уже использован для другой записки.')
            # Every pupil is resolved against the actor's classes before any detail about them is returned.
            pupils=[service.reviewable(db,user,snap,workspace,id) for id in body.studentIds]
            if previous and previous[8]=='applied':return json.loads(previous[9])
            absence_codes.check(db,workspace,body.codeId,user['role']);locked(db,workspace,body)
            items=outcome(db,workspace,snap,body,pupils,subject)
            # A pupil with a problem blocks the whole set: nothing is stored and the rate limit is not used.
            if any(item['problem'] for item in items):return shown(body,subject,items,status='blocked')
            stamp=now()
            if previous and previous[6]>stamp.isoformat():return shown(body,subject,items,id=previous[0],expires=previous[6])
            # A new preview, or an expired one prepared again in place under its request id, uses the hourly allowance (the expired row is replaced, not counted).
            count=db.execute('SELECT count(*) FROM absence_note_batches WHERE actor_id=? AND created>? AND id!=?',(user['id'],(stamp-timedelta(hours=1)).isoformat(),previous[0] if previous else '')).fetchone()[0]
            if count>=30:raise HTTPException(429,'До 30 новых предпросмотров записок в час. Повторите позже.')
            expires=(stamp+timedelta(minutes=15)).isoformat()
            if previous:id=previous[0];db.execute('UPDATE absence_note_batches SET created=?,expires=? WHERE id=?',(stamp.isoformat(),expires,id))
            else:
                id=uuid4().hex
                db.execute('INSERT INTO absence_note_batches VALUES (?,?,?,?,?,?,?,?,?,?)',(id,workspace,user['id'],body.requestId,fingerprint,stamp.isoformat(),expires,payload,'preview',None))
            service.access.record(db,'absence.notes_preview',workspace,detail=id)
            return shown(body,subject,items,id=id,expires=expires)

    @app.post('/api/journal/absences/school/{id}/apply')
    def school_apply(id:str,request:Request,workspace:Workspace='school'):
        user,snap=context(request,workspace)
        with service.access.lock,service.access.connect() as db:
            notice_tables(db);tables(db)
            db.execute('BEGIN IMMEDIATE');service.recheck(db,user,snap,workspace)
            batch=db.execute('SELECT * FROM absence_note_batches WHERE id=? AND workspace=? AND actor_id=?',(id,workspace,user['id'])).fetchone()
            if not batch:raise HTTPException(404,'Предпросмотр не найден.')
            body=SchoolNotes.model_validate_json(batch[7])
            pupils=[service.reviewable(db,user,snap,workspace,pupil) for pupil in body.studentIds]
            if batch[8]=='applied':return json.loads(batch[9])
            if batch[6]<=now().isoformat():raise HTTPException(409,'Предпросмотр истёк. Подготовьте записки заново.')
            service.in_year(snap,body);subject=service.subject_of(snap,body)
            absence_codes.check(db,workspace,body.codeId,user['role'],stale=True);locked(db,workspace,body)
            start,end=body.bounds();blocked=[(p.name,text) for p in pupils if (text:=problem(db,workspace,p,start,end,subject))]
            if blocked:raise HTTPException(409,'Записки не внесены: '+'; '.join(f'{name}: {text}' for name,text in blocked)+'. Подготовьте предпросмотр заново.')
            # All notes, history, notifications and the retry receipt commit together.
            rows=[]
            for pupil in pupils:
                notice=service.insert_notice(db,user=user,pupil=pupil,workspace=workspace,start=start,end=end,reason=body.reason,request_id=f'batch:{id}:{pupil.id}',fingerprint=batch[4],status='approved',origin='school',code_id=body.codeId,subject_id=subject,action='recorded')
                rows.append(db.execute('SELECT * FROM absence_notices WHERE id=?',(notice,)).fetchone())
                service.access.record(db,'absence.recorded',workspace,detail=notice)
            notify_notes(service,db,rows,snap,user)
            result=dict(id=id,status='applied',count=len(rows),items=[dict(id=r[0],studentId=r[3],studentName=r[5],status='approved') for r in rows])
            db.execute("UPDATE absence_note_batches SET status='applied',result=? WHERE id=?",(encoded(result),id))
            service.access.record(db,'absence.notes_applied',workspace,detail=id)
            return result
