"""Per-pupil deadline extensions, separate from shared assignment conditions."""
from datetime import date
import json
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import HTTPException, Query, Request
from pydantic import Field

from .access import now
from .models import Model
from .notifications import encoded


class ChangeDeadline(Model):
    studentId:str=Field(min_length=1,max_length=100)
    assignment:str=Field(pattern=r'^[a-f0-9]{64}$')
    version:int=Field(ge=0)
    action:Literal['extend','revoke']
    dueDate:date|None=None
    reason:str=Field(min_length=1,max_length=1000)


def tables(db):
    db.execute('''CREATE TABLE IF NOT EXISTS homework_extensions (
        workspace TEXT,lesson_key TEXT,student_id TEXT,version INTEGER,assignment TEXT,due_date TEXT,reason TEXT,created TEXT,actor_name TEXT,
        PRIMARY KEY(workspace,lesson_key,student_id))''')
    db.execute('CREATE INDEX IF NOT EXISTS homework_extension_due ON homework_extensions(workspace,due_date,lesson_key,student_id) WHERE due_date IS NOT NULL')
    db.execute('''CREATE TABLE IF NOT EXISTS homework_extension_history (
        id INTEGER PRIMARY KEY,workspace TEXT,lesson_key TEXT,student_id TEXT,version INTEGER,created TEXT,actor_name TEXT,action TEXT,document TEXT)''')
    db.execute('CREATE INDEX IF NOT EXISTS homework_extension_target ON homework_extension_history(workspace,lesson_key,student_id,id)')


def load(db,ctx,student=None):
    return db.execute('SELECT version,assignment,due_date,reason,created,actor_name FROM homework_extensions WHERE workspace=? AND lesson_key=? AND student_id=?',
                      (ctx['workspace'],ctx['key'],student or ctx['student'])).fetchone()


def effective(db,ctx,student=None):
    content=ctx['published']['content'];row=load(db,ctx,student)
    active=bool(row and row[2])
    return dict(content,dueDate=row[2] if active else content['dueDate']),row,active


def permitted(ctx):
    current=ctx['current']
    return bool(ctx['canReview'] and current and current['status']!='cancelled' and ctx['student'] in current['studentIds'] and ctx['roster'][ctx['student']].get('enrolled',True))


def view(db,ctx):
    from .homework import deadline_closed
    content,row,active=effective(db,ctx)
    base=ctx['published']['content']['dueDate']
    today=now().astimezone(ZoneInfo('Asia/Qyzylorda')).date()
    current=date.fromisoformat(content['dueDate']) if base else None
    minimum=max(today,date.fromordinal(current.toordinal()+1)) if current and current<date.max else None
    maximum=date.fromordinal(min(date.max.toordinal(),date.fromisoformat(base).toordinal()+366)) if base else None
    return dict(version=row[0] if row else 0,baseDueDate=base,effectiveDueDate=content['dueDate'],deadlineClosed=deadline_closed(content),
                canEdit=bool(base and permitted(ctx) and ctx['online']),minDate=minimum.isoformat() if minimum else None,maxDate=maximum.isoformat() if maximum else None,
                extension=dict(dueDate=row[2],reason=row[3],updated=row[4],actor=row[5]) if active else None)


def history_write(db,workspace,key,student,version,created,actor,action,document):
    db.execute('INSERT INTO homework_extension_history(workspace,lesson_key,student_id,version,created,actor_name,action,document) VALUES (?,?,?,?,?,?,?,?)',
               (workspace,key,student,version,created,actor,action,encoded(document)))


def invalidate(db,workspace,key):
    """An individual date ends only when the publication is withdrawn or loses its homework, the general date is removed, or it reaches the individual date."""
    rows=db.execute('SELECT student_id,version,assignment,due_date FROM homework_extensions WHERE workspace=? AND lesson_key=? AND due_date IS NOT NULL',(workspace,key)).fetchall()
    if not rows:return
    raw=db.execute('SELECT published_document FROM journal_entries WHERE workspace=? AND lesson_key=?',(workspace,key)).fetchone()
    document=json.loads(raw[0]) if raw and raw[0] else None
    content=document['content'] if document and document['content']['homework'] else None
    base=content['dueDate'] if content else None
    for student,version,assignment,due in rows:
        if content and base and base<due:continue
        created=now().isoformat()
        reason='Публикация задания отозвана.' if not content else 'Общий срок снят.' if not base else 'Общий срок перенесён на дату индивидуального или позже.'
        db.execute('UPDATE homework_extensions SET version=version+1,due_date=NULL,reason=?,created=?,actor_name=? WHERE workspace=? AND lesson_key=? AND student_id=?',
                   (reason,created,'Система',workspace,key,student))
        history_write(db,workspace,key,student,version+1,created,'Система','invalidated',dict(previousDueDate=due,dueDate=None,baseDueDate=base,reason=reason,assignment=assignment))


class HomeworkDeadlines:
    def __init__(self,homework):self.homework=homework;self.access=homework.access

    def register(self,app,Workspace):
        @app.put('/api/journal/homework/{key}/deadline')
        def change(key:str,body:ChangeDeadline,request:Request,workspace:Workspace='school'):
            ctx=self.homework.context(request,workspace,key,body.studentId)
            if not permitted(ctx):raise HTTPException(403,'Срок меняет учитель курса или администратор для ученика действующего урока.')
            if not ctx['online']:raise HTTPException(422,'Индивидуальный срок задаётся только для онлайн-сдачи.')
            if body.assignment!=ctx['assignment']:raise HTTPException(409,'Условия задания изменились. Обновите работу.')
            with self.access.lock,self.access.connect() as db:
                self.homework.tables(db);db.execute('BEGIN IMMEDIATE');self.homework.recheck(db,ctx)
                before=view(db,ctx)
                if body.version!=before['version']:raise HTTPException(409,'Индивидуальный срок уже изменён. Обновите работу.')
                if not before['baseDueDate']:raise HTTPException(422,'У задания нет общего срока. Сначала укажите и опубликуйте его в журнале.')
                if body.action=='extend':
                    if not body.dueDate or not before['minDate'] or not before['minDate']<=body.dueDate.isoformat()<=before['maxDate']:
                        raise HTTPException(422,'Новый срок должен быть позже действующего, не раньше сегодняшнего дня и не далее 366 дней от общего срока.')
                    due=body.dueDate.isoformat()
                else:
                    if body.dueDate is not None:raise HTTPException(422,'При отмене не указывайте новый срок.')
                    if not before['extension']:raise HTTPException(409,'Действующего продления нет.')
                    due=None
                version=before['version']+1;created=now().isoformat()
                db.execute('INSERT INTO homework_extensions VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(workspace,lesson_key,student_id) DO UPDATE SET version=excluded.version,assignment=excluded.assignment,due_date=excluded.due_date,reason=excluded.reason,created=excluded.created,actor_name=excluded.actor_name',
                           (workspace,key,body.studentId,version,ctx['assignment'],due,body.reason,created,ctx['user']['name']))
                history_write(db,workspace,key,body.studentId,version,created,ctx['user']['name'],body.action,
                              dict(previousDueDate=before['effectiveDueDate'],dueDate=due,baseDueDate=before['baseDueDate'],reason=body.reason,assignment=ctx['assignment']))
                from .homework_queue_projection import bump
                bump(db,workspace)
                self.access.record(db,'homework.deadline_changed',workspace,ctx['revision'],detail=f'{key} · {body.studentId} · срок {due or before["baseDueDate"]} · {body.reason}')
                self.homework.notify(db,ctx,body.studentId,version,'homework.deadline_changed')
                return view(db,ctx)

        @app.get('/api/journal/homework/{key}/deadline-history')
        def history(key:str,studentId:str,request:Request,workspace:Workspace='school',before:int|None=Query(default=None,ge=1)):
            ctx=self.homework.context(request,workspace,key,studentId)
            with self.access.lock,self.access.connect() as db:
                self.homework.tables(db);db.execute('BEGIN');self.homework.recheck(db,ctx)
                rows=db.execute('SELECT id,version,created,actor_name,action,document FROM homework_extension_history WHERE workspace=? AND lesson_key=? AND student_id=? AND (? IS NULL OR id<?) ORDER BY id DESC LIMIT 21',
                                (workspace,key,studentId,before,before)).fetchall()
                return dict(items=[dict(id=r[0],version=r[1],created=r[2],actor=r[3],action=r[4],**json.loads(r[5])) for r in rows[:20]],nextBefore=rows[19][0] if len(rows)>20 else None)
