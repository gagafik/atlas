"""Bounded queue reads. Index candidates never replace current lesson/account checks."""
import base64
from datetime import date
import hashlib
import json
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import HTTPException, Query, Request

from .access import now
from .journal import user_ids
from .notifications import encoded, permission_key
from .resources import teacher_ids

SCAN=100
Filter=Literal['all','todo','submitted','returned','accepted','overdue']


def cursor_read(value):
    try:
        result=json.loads(base64.urlsafe_b64decode(value.encode()).decode())
        if not isinstance(result,list) or len(result)!=4 or not all(isinstance(x,str) for x in result):raise ValueError()
        date.fromisoformat(result[1])
        if len(result[0])!=64 or len(result[2])!=64 or not 0<len(result[3])<=100:raise ValueError()
        return result
    except (ValueError,UnicodeError):
        raise HTTPException(422,'Неверная страница очереди.')


class HomeworkQueue:
    def __init__(self,homework):
        self.homework=homework;self.journal=homework.journal;self.access=homework.access

    def read(self,request,workspace,start,end,selected,history,search,cursor,limit):
        user=self.journal.scope(request,workspace)
        snap=self.homework.snapshots.get(workspace);state=snap.state
        staff=user['role'] in ['admin','teacher']
        start=start or state.settings.yearStart;end=end or state.settings.yearEnd
        if end<start or (end-start).days>366:raise HTTPException(422,'Выберите период не длиннее 367 дней, с началом до окончания.')
        selected=selected or ('submitted' if staff else 'todo')
        today=now().astimezone(ZoneInfo('Asia/Qyzylorda')).date().isoformat()
        clauses=['q.workspace=?','q.sort_date BETWEEN ? AND ?'];args=[workspace,start.isoformat(),end.isoformat()]
        if user['role']=='teacher':
            own=user_ids(user,workspace,'teacher')
            loads={w.id for w in state.workloads if own&set(teacher_ids(w))}
            placements={p.id for p in state.placements if p.workloadId in loads}
            placements|={d.placementId for d in state.datedLessons if own&set(d.teacherIds or []) and d.placementId}
            extras={d.id for d in state.datedLessons if not d.placementId and own&set(d.teacherIds or [])}
            clauses.append('''(EXISTS (SELECT 1 FROM json_each(q.draft_teachers) t WHERE t.value IN (SELECT value FROM json_each(?)))
                OR q.source_kind='placement' AND q.source_id IN (SELECT value FROM json_each(?))
                OR q.source_kind='extra' AND q.source_id IN (SELECT value FROM json_each(?)))''')
            args.extend([encoded(sorted(own)),encoded(sorted(placements)),encoded(sorted(extras))])
        elif not staff:
            clauses.append('m.student_id IN (SELECT value FROM json_each(?))');args.append(encoded(sorted(user_ids(user,workspace,'student'))))
        changed='(a.assignment IS NOT NULL AND a.assignment<>q.assignment)'
        status="coalesce(a.status,'not_submitted')"
        todo=f"({status} IN ('not_submitted','returned') OR {changed})"
        if selected in ['todo','overdue']:clauses.append(todo)
        elif selected!='all':
            clauses.extend([f'{status}=?',f'NOT {changed}']);args.append(selected)
        if selected=='overdue':clauses.append('q.due_date<?');args.append(today)
        if search:
            clauses.append("instr(atlas_fold(q.subject||' '||q.group_name||' '||m.name),?)>0");args.append(search.casefold())
        after=cursor_read(cursor) if cursor else None
        with self.access.lock,self.access.connect() as db:
            self.homework.tables(db);db.execute('BEGIN')
            self.journal.recheck_account(db,user)
            if db.execute('SELECT revision FROM workspaces WHERE id=?',(workspace,)).fetchone()!=(state.revision,):
                raise HTTPException(409,'Расписание изменилось. Обновите очередь.')
            version=db.execute('SELECT version FROM homework_queue_versions WHERE workspace=?',(workspace,)).fetchone()
            token=hashlib.sha256(encoded([workspace,permission_key(user),state.revision,version,today,start.isoformat(),end.isoformat(),selected,history,search]).encode()).hexdigest()
            if after:
                if after[0]!=token:raise HTTPException(409,'Очередь изменилась. Обновите список перед загрузкой следующей страницы.')
                clauses.append('(q.sort_date,q.lesson_key,m.student_id)>(?,?,?)');args.extend(after[1:])
            db.create_function('atlas_fold',1,str.casefold)
            rows=db.execute(f'''SELECT q.*,m.student_id,m.name,{status},a.submitted_at,coalesce(a.late,0),{changed}
                FROM homework_queue_tasks q JOIN journal_members m ON m.workspace=q.workspace AND m.lesson_key=q.lesson_key AND m.kind='published'
                LEFT JOIN homework_queue_answers a ON a.workspace=q.workspace AND a.lesson_key=q.lesson_key AND a.student_id=m.student_id
                WHERE {' AND '.join(clauses)} ORDER BY q.sort_date,q.lesson_key,m.student_id LIMIT ?''',(*args,SCAN+1)).fetchall()
            contexts={};items=[];last=None;processed=0
            for r in rows[:SCAN]:
                key,student=r[1],r[13];last=[r[2],key,student];processed+=1
                if key not in contexts:
                    record=db.execute('SELECT * FROM journal_entries WHERE workspace=? AND lesson_key=?',(workspace,key)).fetchone()
                    try:contexts[key]=self.homework.resolved_context(user,snap,self.journal.unpack(record) if record else None,workspace,key,student)
                    except HTTPException as exc:
                        if exc.status_code not in [403,404]:raise
                        contexts[key]=None
                ctx=contexts[key]
                if not ctx or student not in ctx['roster']:continue
                current=ctx['current']
                enrolled=bool(current and current['status']!='cancelled' and student in current['studentIds'] and ctx['roster'][student].get('enrolled',True))
                active=ctx['canReview'] if staff else enrolled
                if not active and not history:continue
                is_changed=bool(r[18]);needs_answer=r[15] in ['not_submitted','returned'] or is_changed
                closed=bool(r[12]=='block' and r[4] and r[4]<today)
                items.append(dict(lessonKey=key,studentId=student,student=r[14],subject=r[5],group=r[6],preview=r[7],
                    lessonDate=r[3],dueDate=r[4],status=r[15],submittedAt=r[16],late=bool(r[17]),assignmentChanged=is_changed,
                    overdue=bool(needs_answer and r[4] and r[4]<today),deadlineClosed=closed,readOnly=not active,
                    canReview=bool(ctx['canReview'] and r[15]=='submitted' and not is_changed),
                    canSubmit=bool(user['role']=='student' and enrolled and needs_answer and not closed)))
                if len(items)>=limit:break
            next_cursor=base64.urlsafe_b64encode(encoded([token,*last]).encode()).decode() if last and processed<len(rows) else None
            return dict(items=items,nextCursor=next_cursor,staff=staff,filter=selected,start=start.isoformat(),end=end.isoformat(),schoolToday=today)

    def register(self,app,Workspace):
        @app.get('/api/journal/homework-queue')
        def queue(request:Request,workspace:Workspace='school',start:date|None=None,end:date|None=None,
                  filter:Filter|None=None,history:bool=False,search:str=Query(default='',max_length=100),
                  cursor:str|None=Query(default=None,max_length=1024),limit:int=Query(default=30,ge=1,le=50)):
            return self.read(request,workspace,start,end,filter,history,search.strip(),cursor,limit)
