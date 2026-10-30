"""Accepting several submitted works of one lesson at once, and the retry-safe request log shared with the parallel-class publication."""
import hashlib
import json

from fastapi import HTTPException, Request
from pydantic import Field

from .access import now
from .models import Model
from .notifications import encoded
from .homework import status


def tables(db):
    db.execute('''CREATE TABLE IF NOT EXISTS homework_requests (
        seq INTEGER PRIMARY KEY AUTOINCREMENT,user_id TEXT,request_id TEXT,fingerprint TEXT,result TEXT,UNIQUE(user_id,request_id))''')


def replay(db, user, action, scope, body):
    """(key, stored result or None). The key binds a requestId to one action on one target, so it cannot be reused elsewhere."""
    key=hashlib.sha256(encoded(dict(action=action,scope=scope,body=body.model_dump(mode='json'))).encode()).hexdigest()
    row=db.execute('SELECT fingerprint,result FROM homework_requests WHERE user_id=? AND request_id=?',(user['id'],body.requestId)).fetchone()
    if row:
        if row[0]!=key: raise HTTPException(409,'Идентификатор уже использован другим действием.')
        return key,json.loads(row[1])
    return key,None


def remember(db, user, body, key, result):
    db.execute('INSERT INTO homework_requests(user_id,request_id,fingerprint,result) VALUES (?,?,?,?)',(user['id'],body.requestId,key,encoded(result)))
    db.execute('DELETE FROM homework_requests WHERE user_id=? AND seq NOT IN (SELECT seq FROM homework_requests WHERE user_id=? ORDER BY seq DESC LIMIT 100)',(user['id'],user['id']))
    return result


class BulkItem(Model):
    studentId: str = Field(min_length=1,max_length=100)
    version: int = Field(ge=1)


class BulkReview(Model):
    requestId: str = Field(pattern=r'^[a-f0-9-]{36}$')
    assignment: str = Field(pattern=r'^[a-f0-9]{64}$')
    comment: str = Field(default='',max_length=4000)
    items: list[BulkItem] = Field(min_length=1,max_length=300)


class HomeworkBulk:
    def __init__(self, homework):
        self.homework=homework
        self.access=homework.access

    def skip_reason(self, ctx, item, version, document):
        sent=document['submitted']
        if version!=item.version: return 'Работа изменилась после загрузки списка.'
        if not sent: return 'Работа не отправлена или отозвана.'
        if status(document)=='returned': return 'Работа на доработке.'
        if status(document)=='accepted': return 'Работа уже принята.'
        if sent['assignment']!=ctx['assignment']: return 'Условия задания изменились: верните работу для новой отправки.'
        return None

    def register(self, app, Workspace):
        @app.post('/api/journal/homework/{key}/review-bulk')
        def review_bulk(key:str,body:BulkReview,request:Request,workspace:Workspace='school'):
            ctx=self.homework.context(request,workspace,key)
            if not ctx['canReview']: raise HTTPException(403,'Проверку выполняет учитель курса или администратор.')
            if body.assignment!=ctx['assignment']: raise HTTPException(409,'Задание изменилось. Обновите его перед проверкой.')
            ids=[item.studentId for item in body.items]
            if len(set(ids))!=len(ids): raise HTTPException(422,'Ученик указан несколько раз.')
            if any(id not in ctx['roster'] for id in ids): raise HTTPException(404,'Ученик не включён в опубликованное задание.')
            with self.access.lock,self.access.connect() as db:
                self.homework.tables(db);db.execute('BEGIN IMMEDIATE');self.homework.recheck(db,ctx)
                # A retry is recognised before any version check, so it never reports its own earlier success as stale.
                request_key,previous=replay(db,ctx['user'],'homework.review_bulk',[workspace,key],body)
                if previous is None:
                    created=now().isoformat();accepted=[];skipped=[]
                    for item in body.items:
                        version,document=self.homework.load(db,ctx,item.studentId)
                        reason=self.skip_reason(ctx,item,version,document)
                        if reason:
                            skipped.append(dict(studentId=item.studentId,name=ctx['roster'][item.studentId]['name'],reason=reason))
                            continue
                        # Exactly what a single acceptance without a grade writes: submission, queue projection, audit, history and the family notice.
                        document['submitted']['review']=dict(decision='accepted',comment=body.comment,reviewedAt=created,reviewer=ctx['user']['name'])
                        self.homework.persist(db,dict(ctx,student=item.studentId),version+1,document,'homework.reviewed')
                        accepted.append(item.studentId)
                    if accepted: self.access.record(db,'homework.bulk_reviewed',workspace,ctx['revision'],detail=f'{key} · принято {len(accepted)}')
                    previous=remember(db,ctx['user'],body,request_key,dict(accepted=accepted,skipped=skipped))
            return previous|dict(overview=self.homework.view(ctx))
