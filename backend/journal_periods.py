"""Reporting periods, transactional journal locks and immutable closure archives."""
from datetime import date,timedelta
import json
from typing import Literal
from uuid import uuid4
from zoneinfo import ZoneInfo
from fastapi import HTTPException,Request,Query
from pydantic import Field,model_validator
from .models import Model
from .access import now
from .notifications import encoded
from .journal import user_ids
from .journal_coverage import CoverageQuery


def tables(db):
    db.execute('''CREATE TABLE IF NOT EXISTS journal_periods (workspace TEXT,id TEXT,name TEXT,start_day TEXT,end_day TEXT,kind TEXT,
        version INTEGER,status TEXT,ever_closed INTEGER,updated TEXT,PRIMARY KEY(workspace,id))''')
    db.execute('''CREATE TABLE IF NOT EXISTS journal_period_history (id INTEGER PRIMARY KEY,workspace TEXT,period_id TEXT,version INTEGER,
        action TEXT,created TEXT,actor TEXT,reason TEXT,document TEXT)''')
    db.execute('CREATE INDEX IF NOT EXISTS period_history ON journal_period_history(workspace,period_id,id)')
    db.execute('''CREATE TABLE IF NOT EXISTS journal_period_previews (id TEXT PRIMARY KEY,workspace TEXT,period_id TEXT,period_version INTEGER,
        actor_id TEXT,expires TEXT,token TEXT,counts TEXT,draft_changes INTEGER,published_count INTEGER)''')
    db.execute('''CREATE TABLE IF NOT EXISTS journal_period_closures (workspace TEXT,period_id TEXT,version INTEGER,created TEXT,actor TEXT,
        reason TEXT,school_revision INTEGER,counts TEXT,published_count INTEGER,draft_changes INTEGER,state_document TEXT,
        PRIMARY KEY(workspace,period_id,version))''')
    db.execute('''CREATE TABLE IF NOT EXISTS journal_period_snapshots (workspace TEXT,period_id TEXT,closure_version INTEGER,lesson_key TEXT,
        published_version INTEGER,published_at TEXT,day TEXT,start TEXT,
        PRIMARY KEY(workspace,period_id,closure_version,lesson_key))''')
    db.execute('''CREATE TABLE IF NOT EXISTS journal_period_documents (workspace TEXT,lesson_key TEXT,published_version INTEGER,
        teacher_ids TEXT,document TEXT,PRIMARY KEY(workspace,lesson_key,published_version))''')
    db.execute('CREATE INDEX IF NOT EXISTS period_snapshot_day ON journal_period_snapshots(workspace,period_id,closure_version,day,start,lesson_key)')


def locks(db,workspace,*lessons):
    days=set()
    for lesson in lessons:
        if not lesson:continue
        days.add(lesson['date'])
        source=lesson.get('sourceKey',lesson.get('id',''))
        if source and not source.startswith('extra:'):
            raw=source.split(':',1)[0]
            try:days.add(date.fromisoformat(raw).isoformat())
            except ValueError:pass
    if not days:return []
    return [dict(id=r[0],name=r[1],start=r[2],end=r[3],version=r[4]) for r in db.execute('''SELECT id,name,start_day,end_day,version
        FROM journal_periods WHERE workspace=? AND status='closed' AND EXISTS
        (SELECT 1 FROM json_each(?) d WHERE d.value BETWEEN start_day AND end_day) ORDER BY start_day,id''',(workspace,encoded(sorted(days))))]


def require_open(db,workspace,*lessons):
    found=locks(db,workspace,*lessons)
    if found:raise HTTPException(403,'Журнал закрыт в периоде: '+', '.join(p['name'] for p in found)+'. Для исправления администратор должен открыть период с указанием причины.')


def range_locks(db,workspace,start,end):
    """Closed periods overlapping the inclusive day range."""
    # A fresh database may have no journal tables yet.
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='journal_periods'").fetchone():return []
    return [dict(id=r[0],name=r[1],start=r[2],end=r[3],version=r[4]) for r in db.execute("SELECT id,name,start_day,end_day,version FROM journal_periods WHERE workspace=? AND status='closed' AND start_day<=? AND end_day>=? ORDER BY start_day,id",(workspace,end,start))]


class PeriodEdit(Model):
    version:int=Field(ge=0)
    name:str=Field(min_length=1,max_length=120)
    start:date
    end:date
    kind:Literal['quarter','semester','year','custom']='custom'
    reason:str=Field(min_length=3,max_length=1000)
    @model_validator(mode='after')
    def bounds(self):
        if not 0<=(self.end-self.start).days<=365:raise ValueError('Период должен содержать от 1 до 366 дней.')
        return self


class PeriodVersion(Model):
    version:int=Field(ge=1)


class PeriodAction(PeriodVersion):
    reason:str=Field(min_length=3,max_length=1000)


class ClosePeriod(PeriodAction):
    previewId:str=Field(pattern=r'^[a-f0-9]{32}$')
    acknowledgeIssues:bool=False


class JournalPeriods:
    def __init__(self,coverage):
        self.coverage,self.journal,self.access=coverage,coverage.journal,coverage.access

    def context(self,request,workspace,admin=False):
        user,snap=self.coverage.reports.context(request,workspace)
        if admin and user['role']!='admin':raise HTTPException(403,'Учебными периодами управляет администратор.')
        return user,snap

    def recheck(self,db,user,snap,workspace):
        self.coverage.absences.recheck(db,user,snap,workspace)

    def item(self,row):
        return dict(id=row[1],name=row[2],start=row[3],end=row[4],kind=row[5],version=row[6],status=row[7],everClosed=bool(row[8]),updated=row[9])

    def find(self,db,workspace,id,version=None):
        row=db.execute('SELECT * FROM journal_periods WHERE workspace=? AND id=?',(workspace,id)).fetchone()
        if not row:raise HTTPException(404,'Учебный период не найден.')
        value=self.item(row)
        if version is not None and version!=value['version']:raise HTTPException(409,'Учебный период изменился. Обновите список.')
        return value

    def record(self,db,workspace,value,action,user,reason):
        stamp=now().isoformat()
        db.execute('INSERT INTO journal_period_history(workspace,period_id,version,action,created,actor,reason,document) VALUES (?,?,?,?,?,?,?,?)',
            (workspace,value['id'],value['version'],action,stamp,user['name'],reason,encoded(value)))
        self.access.record(db,'journal.period_'+action,workspace,detail=encoded(dict(periodId=value['id'],version=value['version'],reason=reason)))

    def archive_scope(self,user,workspace):
        if user['role']=='admin':return '',[]
        return ' AND EXISTS(SELECT 1 FROM json_each(d.teacher_ids) t WHERE t.value IN (SELECT value FROM json_each(?)))',[encoded(sorted(user_ids(user,workspace,'teacher')))]

    def finish_archive(self,user,snap,workspace,id,version,key):
        with self.access.lock,self.access.connect() as db:
            db.execute('BEGIN IMMEDIATE');self.recheck(db,user,snap,workspace)
            self.access.record(db,'journal.period_archive_read',workspace,detail=encoded(dict(periodId=id,version=version,lessonKey=key)))

    def register(self,app,Workspace):
        @app.get('/api/journal/periods')
        def listing(request:Request,workspace:Workspace='school'):
            user,snap=self.context(request,workspace)
            with self.access.read_connect() as db:
                db.execute('BEGIN');self.recheck(db,user,snap,workspace)
                values=[self.item(r) for r in db.execute('SELECT * FROM journal_periods WHERE workspace=? ORDER BY start_day DESC,end_day,id',(workspace,))]
            return dict(admin=user['role']=='admin',items=values,yearStart=snap.state.settings.yearStart,yearEnd=snap.state.settings.yearEnd)

        @app.put('/api/journal/periods/{id}')
        def edit(id:str,body:PeriodEdit,request:Request,workspace:Workspace='school'):
            if len(id)!=32 or any(c not in '0123456789abcdef' for c in id):raise HTTPException(422,'Недопустимый идентификатор периода.')
            user,snap=self.context(request,workspace,True)
            if not snap.state.settings.yearStart<=body.start<=body.end<=snap.state.settings.yearEnd:raise HTTPException(422,'Период должен входить в текущий учебный год.')
            with self.access.lock,self.access.connect() as db:
                db.execute('BEGIN IMMEDIATE');self.recheck(db,user,snap,workspace)
                existing=db.execute('SELECT * FROM journal_periods WHERE workspace=? AND id=?',(workspace,id)).fetchone()
                if existing:
                    value=self.find(db,workspace,id,body.version)
                    if value['everClosed']:raise HTTPException(409,'После первого закрытия название и границы периода сохранены в архиве и не меняются.')
                elif body.version:raise HTTPException(409,'Период отсутствует. Обновите список.')
                elif db.execute('SELECT count(*) FROM journal_periods WHERE workspace=?',(workspace,)).fetchone()[0]>=200:raise HTTPException(422,'До 200 учебных периодов в пространстве.')
                stamp=now().isoformat()
                db.execute('INSERT INTO journal_periods VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT(workspace,id) DO UPDATE SET name=excluded.name,start_day=excluded.start_day,end_day=excluded.end_day,kind=excluded.kind,version=excluded.version,updated=excluded.updated',
                    (workspace,id,body.name,body.start.isoformat(),body.end.isoformat(),body.kind,body.version+1,'open',0,stamp))
                value=self.find(db,workspace,id);self.record(db,workspace,value,'updated' if existing else 'created',user,body.reason)
            return value

        @app.post('/api/journal/periods/{id}/preview-close')
        def preview(id:str,body:PeriodVersion,request:Request,workspace:Workspace='school'):
            user,snap=self.context(request,workspace,True)
            with self.access.read_connect() as db:
                db.execute('BEGIN');token=self.coverage.token(db,user,snap,workspace)
                value=self.find(db,workspace,id,body.version)
                if value['status']!='open':raise HTTPException(409,'Период уже закрыт.')
                query=CoverageQuery(start=value['start'],end=value['end'],status='all',asOf=now())
                counts=self.coverage.result(db,user,snap,workspace,query,token)['counts']
                published,drafts=db.execute('''SELECT count(*),coalesce(sum(e.version!=e.published_version),0)
                    FROM journal_attendance_lessons l CROSS JOIN journal_entries e ON e.workspace=l.workspace AND e.lesson_key=l.lesson_key
                    WHERE l.workspace=? AND l.day BETWEEN ? AND ?''',(workspace,value['start'],value['end'])).fetchone()
            preview_id=uuid4().hex;expires=(now()+timedelta(minutes=15)).isoformat()
            with self.access.lock,self.access.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                if self.coverage.token(db,user,snap,workspace)!=token:raise HTTPException(409,'Журналы или расписание изменились. Повторите проверку.')
                self.find(db,workspace,id,body.version)
                db.execute('DELETE FROM journal_period_previews WHERE expires<? OR (workspace=? AND period_id=? AND actor_id=?)',(now().isoformat(),workspace,id,user['id']))
                db.execute('INSERT INTO journal_period_previews VALUES (?,?,?,?,?,?,?,?,?,?)',(preview_id,workspace,id,body.version,user['id'],expires,token,encoded(counts),drafts,published))
            pending=counts['upcoming']>0 or value['end']>now().astimezone(ZoneInfo('Asia/Qyzylorda')).date().isoformat()
            issues=any(counts[s] for s in ['missing','draft','incomplete','stale','orphaned']) or bool(drafts) or not counts['expected']
            return dict(previewId=preview_id,expires=expires,counts=counts,published=published,draftChanges=drafts,requiresAcknowledgement=issues,canClose=not pending)

        @app.post('/api/journal/periods/{id}/close')
        def close(id:str,body:ClosePeriod,request:Request,workspace:Workspace='school'):
            user,snap=self.context(request,workspace,True)
            with self.access.lock,self.access.connect() as db:
                db.execute('BEGIN IMMEDIATE');self.recheck(db,user,snap,workspace)
                value=self.find(db,workspace,id,body.version)
                preview=db.execute('SELECT * FROM journal_period_previews WHERE id=? AND workspace=? AND period_id=? AND actor_id=?',(body.previewId,workspace,id,user['id'])).fetchone()
                if value['status']!='open' or not preview or preview[3]!=body.version or preview[5]<now().isoformat() or preview[6]!=self.coverage.token(db,user,snap,workspace):raise HTTPException(409,'Проверка устарела. Проверьте период перед закрытием ещё раз.')
                counts=json.loads(preview[7]);drafts,published=preview[8:10]
                if counts['upcoming'] or value['end']>now().astimezone(ZoneInfo('Asia/Qyzylorda')).date().isoformat():raise HTTPException(422,'Период ещё не закончился или содержит незавершённые уроки.')
                issues=any(counts[s] for s in ['missing','draft','incomplete','stale','orphaned']) or drafts or not counts['expected']
                if issues and not body.acknowledgeIssues:raise HTTPException(422,'Подтвердите закрытие с указанными пробелами. Неопубликованные изменения не попадут в архив.')
                version=body.version+1;stamp=now().isoformat()
                state_document=db.execute('SELECT document FROM workspaces WHERE id=?',(workspace,)).fetchone()[0]
                db.execute('INSERT INTO journal_period_closures VALUES (?,?,?,?,?,?,?,?,?,?,?)',(workspace,id,version,stamp,user['name'],body.reason,snap.state.revision,preview[7],published,drafts,state_document))
                # Store each immutable publication once, even in overlapping or
                # repeatedly closed periods. SQLite streams the documents.
                db.execute('''INSERT INTO journal_period_documents
                    SELECT e.workspace,e.lesson_key,e.published_version,json_extract(e.published_document,'$.lesson.teacherIds'),e.published_document
                    FROM journal_attendance_lessons l CROSS JOIN journal_entries e ON e.workspace=l.workspace AND e.lesson_key=l.lesson_key
                    LEFT JOIN journal_period_documents d ON d.workspace=e.workspace AND d.lesson_key=e.lesson_key AND d.published_version=e.published_version
                    WHERE l.workspace=? AND l.day BETWEEN ? AND ? AND d.lesson_key IS NULL''',(workspace,value['start'],value['end']))
                db.execute('''INSERT INTO journal_period_snapshots
                    SELECT ?,?,?,l.lesson_key,l.version,l.published_at,l.day,l.start
                    FROM journal_attendance_lessons l
                    WHERE l.workspace=? AND l.day BETWEEN ? AND ?''',(workspace,id,version,workspace,value['start'],value['end']))
                db.execute("UPDATE journal_periods SET status='closed',ever_closed=1,version=?,updated=? WHERE workspace=? AND id=?",(version,stamp,workspace,id))
                value=self.find(db,workspace,id);self.record(db,workspace,value,'closed',user,body.reason)
                db.execute('DELETE FROM journal_period_previews WHERE workspace=? AND period_id=?',(workspace,id))
            return value

        @app.post('/api/journal/periods/{id}/reopen')
        def reopen(id:str,body:PeriodAction,request:Request,workspace:Workspace='school'):
            user,snap=self.context(request,workspace,True)
            with self.access.lock,self.access.connect() as db:
                db.execute('BEGIN IMMEDIATE');self.recheck(db,user,snap,workspace)
                value=self.find(db,workspace,id,body.version)
                if value['status']!='closed':raise HTTPException(409,'Период уже открыт.')
                db.execute("UPDATE journal_periods SET status='open',version=version+1,updated=? WHERE workspace=? AND id=?",(now().isoformat(),workspace,id))
                value=self.find(db,workspace,id);self.record(db,workspace,value,'reopened',user,body.reason)
            return value

        @app.get('/api/journal/periods/{id}/history')
        def history(id:str,request:Request,workspace:Workspace='school',before:int|None=Query(default=None,ge=1)):
            user,snap=self.context(request,workspace,True)
            with self.access.read_connect() as db:
                db.execute('BEGIN');self.recheck(db,user,snap,workspace);self.find(db,workspace,id)
                rows=db.execute('SELECT id,version,action,created,actor,reason,document FROM journal_period_history WHERE workspace=? AND period_id=? AND (? IS NULL OR id<?) ORDER BY id DESC LIMIT 51',(workspace,id,before,before)).fetchall()
                return dict(items=[dict(id=r[0],version=r[1],action=r[2],created=r[3],actor=r[4],reason=r[5],period=json.loads(r[6])) for r in rows[:50]],nextBefore=rows[49][0] if len(rows)>50 else None)

        @app.get('/api/journal/periods/{id}/closures')
        def closures(id:str,request:Request,workspace:Workspace='school',before:int|None=Query(default=None,ge=1)):
            user,snap=self.context(request,workspace)
            with self.access.read_connect() as db:
                db.execute('BEGIN');self.recheck(db,user,snap,workspace);self.find(db,workspace,id)
                rows=db.execute('SELECT version,created,actor,reason,school_revision,counts,published_count,draft_changes FROM journal_period_closures WHERE workspace=? AND period_id=? AND (? IS NULL OR version<?) ORDER BY version DESC LIMIT 51',(workspace,id,before,before)).fetchall()
                return dict(items=[dict(version=r[0],created=r[1],**(dict(actor=r[2],reason=r[3],schoolRevision=r[4],counts=json.loads(r[5]),published=r[6],draftChanges=r[7]) if user['role']=='admin' else {})) for r in rows[:50]],nextBefore=rows[49][0] if len(rows)>50 else None)

        @app.get('/api/journal/periods/{id}/closures/{version}/lessons')
        def archive(id:str,version:int,request:Request,workspace:Workspace='school',offset:int=Query(default=0,ge=0,le=10000000),lessonKey:str=''):
            user,snap=self.context(request,workspace);scope,args=self.archive_scope(user,workspace)
            with self.access.read_connect() as db:
                db.execute('BEGIN');self.recheck(db,user,snap,workspace);self.find(db,workspace,id)
                if not db.execute('SELECT 1 FROM journal_period_closures WHERE workspace=? AND period_id=? AND version=?',(workspace,id,version)).fetchone():raise HTTPException(404,'Архив закрытия не найден.')
                source='journal_period_snapshots s JOIN journal_period_documents d ON d.workspace=s.workspace AND d.lesson_key=s.lesson_key AND d.published_version=s.published_version'
                where='s.workspace=? AND s.period_id=? AND s.closure_version=?'+scope;params=[workspace,id,version,*args]
                if lessonKey:
                    row=db.execute('SELECT s.lesson_key,s.published_version,s.published_at,d.document FROM '+source+' WHERE '+where+' AND s.lesson_key=?',params+[lessonKey]).fetchone()
                    if not row:raise HTTPException(404,'Урок не найден или недоступен в этом архиве.')
                    value=dict(lessonKey=row[0],publishedVersion=row[1],publishedAt=row[2],document=json.loads(row[3]))
                else:
                    rows=db.execute('''SELECT s.lesson_key,s.published_version,s.published_at,s.day,s.start,json_extract(d.document,'$.lesson.subject'),
                        json_extract(d.document,'$.lesson.group'),json_extract(d.document,'$.lesson.teachers') FROM '''+source+' WHERE '+where+' ORDER BY s.day,s.start,s.lesson_key LIMIT 51 OFFSET ?',params+[offset]).fetchall()
                    value=dict(items=[dict(lessonKey=r[0],publishedVersion=r[1],publishedAt=r[2],date=r[3],start=r[4],subject=r[5],group=r[6],teachers=r[7]) for r in rows[:50]],nextOffset=offset+50 if len(rows)>50 else None)
            self.finish_archive(user,snap,workspace,id,version,lessonKey)
            return value
