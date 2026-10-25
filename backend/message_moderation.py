"""Reported evidence is an explicit, auditable exception to conversation privacy."""
from datetime import timedelta
import hashlib
import json
from typing import Literal
from urllib.parse import quote
from uuid import uuid4

from fastapi import HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import Field

from .access import now
from .models import Model
from .notifications import encoded
from .message_files import root_for, tables as file_tables
from .homework_files import file_path


class Report(Model):
    requestId: str = Field(pattern=r'^[a-f0-9-]{36}$')
    reason: str = Field(min_length=1, max_length=2000)
    shareEvidence: bool = False


class Decision(Model):
    requestId: str = Field(pattern=r'^[a-f0-9-]{36}$')
    version: int = Field(ge=1)
    postVersion: int = Field(ge=0)
    action: Literal['dismiss', 'hide', 'restore']
    note: str = Field(min_length=1, max_length=2000)


class MessageModeration:
    def __init__(self, messages):
        self.messages, self.access = messages, messages.access

    def tables(self, db):
        db.execute('''CREATE TABLE IF NOT EXISTS message_reports (
            id TEXT PRIMARY KEY, workspace TEXT, post_id INTEGER, reporter_id TEXT, reporter_name TEXT,
            created TEXT, reason TEXT, evidence TEXT, status TEXT, version INTEGER,
            note TEXT, reviewer_id TEXT, reviewed TEXT, request_id TEXT, signature TEXT,
            UNIQUE(reporter_id,request_id), UNIQUE(reporter_id,post_id))''')
        db.execute('CREATE INDEX IF NOT EXISTS message_report_queue ON message_reports(workspace,status,created,id)')
        db.execute('CREATE INDEX IF NOT EXISTS message_report_owner ON message_reports(reporter_id,workspace,created,id)')
        db.execute('''CREATE TABLE IF NOT EXISTS message_moderation (
            post_id INTEGER PRIMARY KEY, hidden INTEGER, version INTEGER)''')
        db.execute('''CREATE TABLE IF NOT EXISTS message_report_decisions (
            user_id TEXT, request_id TEXT, signature TEXT, report_id TEXT, created TEXT,
            action TEXT, note TEXT, PRIMARY KEY(user_id,request_id))''')
        db.execute('CREATE INDEX IF NOT EXISTS message_decision_report ON message_report_decisions(report_id,created)')

    @staticmethod
    def admin(user):
        if user['role'] != 'admin': raise HTTPException(403, 'Рассмотрение жалоб доступно администратору.')

    @staticmethod
    def state(db, post):
        row = db.execute('SELECT hidden,version FROM message_moderation WHERE post_id=?', (post,)).fetchone()
        return dict(hidden=bool(row[0]), postVersion=row[1]) if row else dict(hidden=False, postVersion=0)

    @staticmethod
    def summary(row):
        return dict(zip(['id','workspace','postId','reporterId','reporter','created','reason','evidence','status','version','note','reviewerId','reviewed'], row[:13]))

    def find(self, db, id, workspace, user):
        row = db.execute('SELECT * FROM message_reports WHERE id=? AND workspace=?', (id,workspace)).fetchone()
        if not row or user['role'] != 'admin' and row[3] != user['id']: raise HTTPException(404, 'Жалоба недоступна.')
        result = self.summary(row)
        result['evidence'] = json.loads(result['evidence'])
        return result | self.state(db, row[2])

    def register(self, app, Workspace):
        @app.post('/api/messages/posts/{post_id}/report')
        def report(post_id: int, body: Report, request: Request, workspace: Workspace='school'):
            user, directory = self.messages.context(request, workspace)
            if not body.shareEvidence or not body.reason.strip(): raise HTTPException(422, 'Укажите причину и подтвердите передачу сообщения и вложений администратору.')
            signature = hashlib.sha256(encoded(dict(workspace=workspace,post=post_id,body=body.model_dump())).encode()).hexdigest()
            with self.access.lock, self.access.connect() as db:
                self.messages.tables(db); db.execute('BEGIN IMMEDIATE'); self.messages.recheck(db,user,directory)
                prior = db.execute('SELECT id,signature FROM message_reports WHERE reporter_id=? AND request_id=?', (user['id'],body.requestId)).fetchone()
                if prior:
                    if prior[1] != signature: raise HTTPException(409, 'Повторная жалоба отличается от отправленной.')
                    return dict(id=prior[0])
                post = db.execute('SELECT thread_id,author_id,author_name,created,body FROM message_posts WHERE id=?', (post_id,)).fetchone()
                if not post: raise HTTPException(404, 'Сообщение недоступно.')
                thread, _, through = self.messages.readable(db,post[0],user,directory,self.messages.thread_accounts(db,post[0],user))
                # A former participant may report only what they could read.
                if through is not None and post_id > through: raise HTTPException(404, 'Сообщение недоступно.')
                if post[1] == user['id']: raise HTTPException(422, 'Нельзя пожаловаться на своё сообщение.')
                if self.state(db,post_id)['hidden']: raise HTTPException(409, 'Сообщение уже скрыто администратором.')
                if db.execute('SELECT 1 FROM message_reports WHERE reporter_id=? AND post_id=?',(user['id'],post_id)).fetchone(): raise HTTPException(409, 'Вы уже отправили жалобу на это сообщение. Проверьте её состояние в разделе жалоб.')
                count = db.execute('SELECT count(*) FROM message_reports WHERE reporter_id=? AND created>?',(user['id'],(now()-timedelta(hours=1)).isoformat())).fetchone()[0]
                if count >= 5: raise HTTPException(429, 'Можно отправить до пяти жалоб за час.')
                evidence = dict(threadId=thread['id'],title=thread['title'],kind=thread['kind'],authorId=post[1],author=post[2],created=post[3],text=post[4],files=self.messages.files.for_posts(db,[post_id])[post_id])
                id = str(uuid4())
                db.execute('INSERT INTO message_reports VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)', (id,workspace,post_id,user['id'],user['name'],now().isoformat(),body.reason,encoded(evidence),'open',1,'',None,None,body.requestId,signature))
                admins = [self.access.public(r) for r in db.execute("SELECT * FROM accounts WHERE active=1 AND role='admin'")]
                self.messages.notifications.deliver(db,admins,workspace,1,'message.reported')
                self.access.record(db,'message.reported',workspace,detail=id)
            return dict(id=id)

        @app.get('/api/messages/reports')
        def reports(request: Request, workspace: Workspace='school', status: Literal['all','open','dismissed','hidden','restored']='all', offset: int=Query(default=0,ge=0,le=100000)):
            user, _ = self.messages.context(request,workspace)
            with self.access.lock, self.access.connect() as db:
                self.messages.tables(db)
                rows = db.execute('''SELECT id,status,version,created,reporter_name,post_id FROM message_reports
                    WHERE workspace=? AND (?='admin' OR reporter_id=?) AND (?='all' OR status=?)
                    ORDER BY created DESC,id DESC LIMIT 51 OFFSET ?''',(workspace,user['role'],user['id'],status,status,offset)).fetchall()
                return dict(items=[dict(zip(['id','status','version','created','reporter','postId'],r)) for r in rows[:50]],nextOffset=offset+50 if len(rows)>50 else None)

        @app.get('/api/messages/reports/{id}')
        def detail(id: str, request: Request, workspace: Workspace='school'):
            user, directory = self.messages.context(request,workspace)
            with self.access.lock, self.access.connect() as db:
                self.messages.tables(db); db.execute('BEGIN IMMEDIATE'); self.messages.recheck(db,user,directory)
                result = self.find(db,id,workspace,user)
                # Reporter retains their submission and outcome even if membership is later revoked.
                # Only administrators receive the immutable evidence snapshot on this route.
                if user['role'] != 'admin': result.pop('evidence')
                else: self.access.record(db,'message.evidence_read',workspace,detail=id)
                history = db.execute('SELECT created,action,note FROM message_report_decisions WHERE report_id=? ORDER BY created,request_id',(id,)).fetchall()
                return result | dict(history=[dict(zip(['created','action','note'],r)) for r in history],canReview=user['role']=='admin' and result.get('evidence',{}).get('authorId')!=user['id'])

        @app.get('/api/messages/reports/{id}/files/{file_id}')
        def evidence_file(id: str, file_id: str, request: Request, workspace: Workspace='school'):
            user, directory = self.messages.context(request,workspace); self.admin(user)
            with self.access.lock, self.access.connect() as db:
                self.messages.tables(db); file_tables(db); db.execute('BEGIN IMMEDIATE'); self.messages.recheck(db,user,directory)
                report = self.find(db,id,workspace,user)
                if file_id not in {f['id'] for f in report['evidence']['files']}: raise HTTPException(404,'Файл не относится к жалобе.')
                row = db.execute("SELECT filename,size FROM message_files WHERE id=? AND workspace=? AND post_id=? AND status='ready'",(file_id,workspace,report['postId'])).fetchone()
                if not row: raise HTTPException(404,'Файл недоступен.')
                path = file_path(root_for(db),file_id)
                try: stat = path.stat()
                except OSError: raise HTTPException(404,'Файл недоступен в хранилище.') from None
                self.access.record(db,'message.evidence_file_read',workspace,detail=id+' · '+file_id)
            return FileResponse(path,stat_result=stat,media_type='application/octet-stream',headers={'Content-Disposition':"attachment; filename*=UTF-8''"+quote(row[0],safe=''),'Content-Length':str(row[1]),'Cache-Control':'no-store','X-Content-Type-Options':'nosniff','Content-Security-Policy':"sandbox; default-src 'none'"})

        @app.post('/api/messages/reports/{id}/decision')
        def decide(id: str, body: Decision, request: Request, workspace: Workspace='school'):
            user, directory = self.messages.context(request,workspace); self.admin(user)
            if not body.note.strip(): raise HTTPException(422,'Объясните решение для заявителя.')
            signature = hashlib.sha256(encoded(dict(workspace=workspace,id=id,body=body.model_dump())).encode()).hexdigest()
            with self.access.lock, self.access.connect() as db:
                self.messages.tables(db); db.execute('BEGIN IMMEDIATE'); self.messages.recheck(db,user,directory)
                report = self.find(db,id,workspace,user)
                prior = db.execute('SELECT signature FROM message_report_decisions WHERE user_id=? AND request_id=?',(user['id'],body.requestId)).fetchone()
                if prior:
                    if prior[0] != signature: raise HTTPException(409,'Повторное решение отличается от отправленного.')
                    return dict(ok=True)
                if report['evidence']['authorId'] == user['id']: raise HTTPException(403,'Жалобу на ваше сообщение должен рассмотреть другой администратор.')
                if report['version'] != body.version or report['postVersion'] != body.postVersion: raise HTTPException(409,'Жалоба или состояние сообщения изменились. Обновите материалы.')
                if body.action=='restore' and not report['hidden'] or body.action=='hide' and report['hidden']: raise HTTPException(409,'Состояние сообщения уже изменилось.')
                stamp = now().isoformat()
                if body.action != 'dismiss':
                    db.execute('INSERT INTO message_moderation VALUES (?,?,?) ON CONFLICT(post_id) DO UPDATE SET hidden=excluded.hidden,version=excluded.version',(report['postId'],int(body.action=='hide'),report['postVersion']+1))
                    db.execute('UPDATE message_threads SET version=version+1 WHERE id=?',(report['evidence']['threadId'],))
                status = dict(hide='hidden',restore='restored',dismiss='dismissed')[body.action]
                db.execute('UPDATE message_reports SET status=?,version=version+1,note=?,reviewer_id=?,reviewed=? WHERE id=?',(status,body.note,user['id'],stamp,id))
                db.execute('INSERT INTO message_report_decisions VALUES (?,?,?,?,?,?,?)',(user['id'],body.requestId,signature,id,stamp,body.action,body.note))
                targets = self.messages.accounts(db,user_ids=[report['reporterId'],report['evidence']['authorId']])
                self.messages.notifications.deliver(db,[u for u in targets.values() if directory.enabled(u)],workspace,report['version']+1,'message.moderated')
                self.access.record(db,'message.moderation_'+body.action,workspace,detail=id)
            return dict(ok=True)
