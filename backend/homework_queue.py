"""Bounded queue reads in WAL snapshots. Index candidates never replace current lesson/account checks."""
from collections import OrderedDict
from datetime import date, timedelta
import hashlib
import secrets
import threading
from time import monotonic
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import HTTPException, Query, Request

from .access import now
from . import journal_reads
from .journal import user_ids
from .notifications import encoded, permission_key
from .resources import teacher_ids

SCAN=100
AHEAD=14
Filter=Literal['all','todo','submitted','returned','accepted','overdue']


class HomeworkQueue:
    def __init__(self,homework):
        self.homework=homework;self.journal=homework.journal;self.access=homework.access
        # Reads no longer run under the global database lock, so the cursor store needs its own.
        self.cursors=OrderedDict();self.cursor_lock=threading.Lock()

    def cursor_read(self,value):
        if len(value)!=64 or any(c not in '0123456789abcdef' for c in value):
            raise HTTPException(422,'Неверная страница очереди.')
        with self.cursor_lock:
            entry=self.cursors.get(value)
            if not entry or monotonic()-entry[0]>900:
                self.cursors.pop(value,None)
                raise HTTPException(409,'Страница устарела. Обновите очередь.')
            return entry[1]

    def cursor_save(self,position):
        # Candidates can fail authorization. Never expose their lesson/student IDs
        # in a client-decodable cursor, even for an otherwise empty result page.
        key=secrets.token_hex(32)
        with self.cursor_lock:
            self.cursors[key]=(monotonic(),position)
            while len(self.cursors)>4096:self.cursors.popitem(last=False)
        return key

    def read(self,request,workspace,start,end,selected,history,search,cursor,limit):
        user=self.journal.scope(request,workspace)
        snap=self.homework.snapshots.get(workspace);state=snap.state
        staff=user['role'] in ['admin','teacher']
        selected=selected or ('submitted' if staff else 'todo')
        day=now().astimezone(ZoneInfo('Asia/Qyzylorda')).date();today=day.isoformat()
        year_start,year_end=state.settings.yearStart,state.settings.yearEnd
        if not staff and start is None and end is None:
            # Pupils and parents open on earlier undone work (newest first), then the next two weeks.
            window='upcoming'
            phases=[('past',year_start,min(year_end,day-timedelta(days=1)),True),('next',max(year_start,day),min(year_end,day+timedelta(days=AHEAD)),False)]
            start,end=year_start,max(year_start,min(year_end,day+timedelta(days=AHEAD)))
        else:
            window='range'
            start=start or year_start;end=end or year_end
            if end<start or (end-start).days>366:raise HTTPException(422,'Выберите период не длиннее 367 дней, с началом до окончания.')
            phases=[('range',start,end,False)]
        # Only conditions, rights, the schedule revision and the school day bind a cursor: the keyset
        # (sort, lesson, pupil) stays valid when unrelated journal entries or answers change.
        token=hashlib.sha256(encoded([workspace,user['id'],permission_key(user),state.revision,today,start.isoformat(),end.isoformat(),window,selected,history,search]).encode()).hexdigest()
        after=self.cursor_read(cursor) if cursor else None
        if after and after[0]!=token:raise HTTPException(409,'Очередь изменилась. Обновите список перед загрузкой следующей страницы.')
        clauses=[];args=[]
        if user['role']=='teacher':
            own=user_ids(user,workspace,'teacher')
            loads={w.id for w in state.workloads if own&set(teacher_ids(w))}
            placements={p.id for p in state.placements if p.workloadId in loads}
            placements|={d.placementId for d in state.datedLessons if own&set(d.teacherIds or []) and d.placementId}
            extras={d.id for d in state.datedLessons if not d.placementId and own&set(d.teacherIds or [])}
            # The course (workload group + subject) reviews, so its lessons are candidates whoever teaches them today.
            pairs=sorted({(w.groupId,w.subjectId) for w in state.workloads if own&set(teacher_ids(w))})
            clauses.append('''(EXISTS (SELECT 1 FROM json_each(q.draft_teachers) t WHERE t.value IN (SELECT value FROM json_each(?)))
                OR q.source_kind='placement' AND q.source_id IN (SELECT value FROM json_each(?))
                OR q.source_kind='extra' AND q.source_id IN (SELECT value FROM json_each(?))
                OR EXISTS (SELECT 1 FROM json_each(?) p WHERE json_extract(p.value,'$[0]')=q.group_id AND json_extract(p.value,'$[1]')=q.subject_id))''')
            args.extend([encoded(sorted(own)),encoded(sorted(placements)),encoded(sorted(extras)),encoded([list(p) for p in pairs])])
        elif not staff:
            clauses.append('m.student_id IN (SELECT value FROM json_each(?))');args.append(encoded(sorted(user_ids(user,workspace,'student'))))
        changed='(a.assignment IS NOT NULL AND a.assignment<>q.assignment)'
        status="coalesce(a.status,'not_submitted')"
        # Homework without online submission is never a to-do once its day has passed: there is nothing to send.
        todo=f"(({status} IN ('not_submitted','returned') OR {changed}) AND (q.submission='online' OR {{sort}}>=?))"
        if selected in ['todo','overdue']:clauses.append(todo);args.append(today)
        elif selected!='all':
            clauses.extend([f'{status}=?',f'NOT {changed}']);args.append(selected)
        if selected=='overdue':clauses.append("{due}<? AND q.submission='online'");args.append(today)
        if search:
            clauses.append("instr(atlas_fold(q.subject||' '||q.group_name||' '||m.name),?)>0");args.append(search.casefold())

        def fetch(db,low,high,descending,position,count):
            # Two disjoint, bounded index ranges: an extension may be inside the
            # requested window even when the shared deadline is far outside it.
            rows=[];order='DESC' if descending else 'ASC'
            for extended in [False,True]:
                sort='x.due_date' if extended else 'q.sort_date'
                due='x.due_date' if extended else 'q.due_date'
                source='''homework_extensions x INDEXED BY homework_extension_due
                    JOIN homework_queue_tasks q ON q.workspace=x.workspace AND q.lesson_key=x.lesson_key''' if extended else 'homework_queue_tasks q'
                extra="x.due_date IS NOT NULL AND x.student_id=m.student_id" if extended else '''NOT EXISTS (
                    SELECT 1 FROM homework_extensions x WHERE x.workspace=q.workspace AND x.lesson_key=q.lesson_key
                    AND x.student_id=m.student_id AND x.due_date IS NOT NULL)'''
                where=['q.workspace=?','{sort} BETWEEN ? AND ?',*clauses];values=[workspace,low.isoformat(),high.isoformat(),*args]
                if position:
                    where.append('({sort},q.lesson_key,m.student_id)'+('<' if descending else '>')+'(?,?,?)');values.extend(position)
                where=' AND '.join(where).format(sort=sort,due=due)
                rows.extend(db.execute(f'''SELECT q.workspace,q.lesson_key,{sort},q.lesson_date,{due},q.subject,q.group_name,q.preview,
                    q.assignment,q.source_kind,q.source_id,q.draft_teachers,q.late_policy,
                    m.student_id,m.name,{status},a.submitted_at,coalesce(a.late,0),{changed},q.due_date,{int(extended)},q.submission
                    FROM {source} JOIN journal_members m ON m.workspace=q.workspace AND m.lesson_key=q.lesson_key AND m.kind='published'
                    LEFT JOIN homework_queue_answers a ON a.workspace=q.workspace AND a.lesson_key=q.lesson_key AND a.student_id=m.student_id
                    WHERE {where} AND {extra} ORDER BY {sort} {order},q.lesson_key {order},m.student_id {order} LIMIT ?''',(*values,count)).fetchall())
            return sorted(rows,key=lambda r:(r[2],r[1],r[13]),reverse=descending)[:count]

        def operation(db):
            self.journal.recheck_account(db,user)
            if db.execute('SELECT revision FROM workspaces WHERE id=?',(workspace,)).fetchone()!=(state.revision,):
                raise HTTPException(409,'Расписание изменилось. Обновите очередь.')
            db.create_function('atlas_fold',1,str.casefold)
            contexts={};items=[];budget=SCAN;next_cursor=None
            # One scan budget serves the whole request, however many phases it crosses.
            for index in range(after[1] if after else 0,len(phases)):
                name,low,high,descending=phases[index]
                if low>high:continue
                position=after[2:] if after and index==after[1] and after[2] is not None else None
                rows=fetch(db,low,high,descending,position,budget+1)
                processed=0;last=None
                for r in rows[:budget]:
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
                    online=r[21]!='offline'
                    is_changed=bool(r[18]);needs_answer=r[15] in ['not_submitted','returned'] or is_changed
                    closed=bool(online and r[12]=='block' and r[4] and r[4]<today)
                    items.append(dict(lessonKey=key,studentId=student,student=r[14],subject=r[5],group=r[6],preview=r[7],
                        lessonDate=r[3],dueDate=r[4],baseDueDate=r[19],extended=bool(r[20]),status=r[15],submittedAt=r[16],late=bool(r[17]),assignmentChanged=is_changed,
                        overdue=bool(online and needs_answer and r[4] and r[4]<today),deadlineClosed=closed,readOnly=not active,submission=r[21],
                        canReview=bool(ctx['canReview'] and r[15]=='submitted' and not is_changed),
                        canSubmit=bool(user['role']=='student' and online and enrolled and needs_answer and not closed)))
                    if len(items)>=limit:break
                budget-=processed
                if processed<len(rows):next_cursor=self.cursor_save([token,index,*last]);break
                if (budget<=0 or len(items)>=limit) and index+1<len(phases):next_cursor=self.cursor_save([token,index+1,None,None,None]);break
            return dict(items=items,nextCursor=next_cursor,staff=staff,filter=selected,window=window,start=start.isoformat(),end=end.isoformat(),
                        yearStart=year_start.isoformat(),yearEnd=year_end.isoformat(),schoolToday=today)
        return journal_reads.read(self.access,self.homework.tables,operation)

    def register(self,app,Workspace):
        @app.get('/api/journal/homework-queue')
        def queue(request:Request,workspace:Workspace='school',start:date|None=None,end:date|None=None,
                  filter:Filter|None=None,history:bool=False,search:str=Query(default='',max_length=100),
                  cursor:str|None=Query(default=None,max_length=1024),limit:int=Query(default=30,ge=1,le=50)):
            return self.read(request,workspace,start,end,filter,history,search.strip(),cursor,limit)
