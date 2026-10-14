"""Bounded, authorized catalog without question keys or answer documents."""
import hashlib
import json
import secrets
from time import monotonic
from fastapi import HTTPException, Query, Request
from .access import now
from .journal import user_ids
from .notifications import encoded, permission_key


def register(app,quizzes,Workspace):
    @app.get('/api/journal/quizzes')
    def listing(request:Request,workspace:Workspace='school',history:bool=False,search:str=Query(default='',max_length=120),
                cursor:str|None=Query(default=None,max_length=64),limit:int=Query(default=30,ge=1,le=50)):
        user=quizzes.journal.scope(request,workspace);snap=quizzes.snapshots.get(workspace);staff=user['role'] in ['admin','teacher']
        with quizzes.access.lock,quizzes.access.connect() as db:
            quizzes.tables(db);db.execute('BEGIN');quizzes.journal.recheck_account(db,user)
            if db.execute('SELECT revision FROM workspaces WHERE id=?',(workspace,)).fetchone()!=(snap.state.revision,):raise HTTPException(409,'Расписание изменилось. Обновите тесты.')
            generation=db.execute('SELECT version FROM quiz_catalog_versions WHERE workspace=?',(workspace,)).fetchone()
            token=hashlib.sha256(encoded([workspace,user['id'],permission_key(user),snap.state.revision,generation,history,search]).encode()).hexdigest()
            after=None
            if cursor:
                found=quizzes.cursors.get(cursor)
                if not found or monotonic()-found[0]>900 or found[1]!=token:raise HTTPException(409,'Список тестов изменился. Откройте первую страницу.')
                after=found[2]
            clauses=['q.workspace=?'];args=[workspace]
            student="''" if staff else 'm.student_id';name="''" if staff else 'm.name'
            join='' if staff else 'JOIN quiz_members m ON m.workspace=q.workspace AND m.quiz_id=q.id'
            if not staff:clauses.append('m.student_id IN (SELECT value FROM json_each(?))');args.append(encoded(sorted(user_ids(user,workspace,'student'))))
            if not history:clauses.append("q.status<>'withdrawn'")
            if search.strip():
                db.create_function('atlas_fold',1,str.casefold)
                clauses.append("instr(atlas_fold(json_extract(q.summary,'$.definition.title')||' '||json_extract(q.summary,'$.lesson.subject')||' '||json_extract(q.summary,'$.lesson.group')),?)>0")
                args.append(search.strip().casefold())
            if after:clauses.append(f'(q.closes,q.id,{student})>(?,?,?)');args.extend(after)
            rows=db.execute(f'''SELECT q.id,q.version,q.status,q.closes,q.lesson_key,q.summary,q.published_document IS NOT NULL,q.released,{student},{name}
                FROM quizzes q {join} WHERE {' AND '.join(clauses)} ORDER BY q.closes,q.id,{student} LIMIT 101''',args).fetchall()
            items=[];last=None;processed=0
            for r in rows[:100]:
                last=[r[3],r[0],r[8]];processed+=1;document=json.loads(r[5]);document['studentIds']=[r[8]] if r[8] else []
                quiz=dict(workspace=workspace,id=r[0],version=r[1],status=r[2],closesAt=r[3],lessonKey=r[4],document=document,
                          published=document if r[6] else None,released=bool(r[7]))
                try:ctx=quizzes.resolve(user,snap,quiz,r[8] or None)
                except HTTPException as exc:
                    if exc.status_code not in [403,404]:raise
                    continue
                active=ctx['canReview'] if staff else bool(ctx['current'] and ctx['current']['status']!='cancelled' and r[8] in ctx['current']['studentIds'])
                if not history and not active:continue
                definition=document['definition']
                items.append(dict(id=r[0],version=r[1],status=r[2],released=bool(r[7]),title=definition['title'],closesAt=definition['closesAt'],opensAt=definition['opensAt'],
                                  lessonDate=document['lesson']['date'],subject=document['lesson']['subject'],group=document['lesson']['group'],studentId=r[8] or None,student=r[9] or None,readOnly=not active))
                if len(items)>=limit:break
            next_cursor=None
            if last and processed<len(rows):
                next_cursor=secrets.token_hex(32);quizzes.cursors[next_cursor]=(monotonic(),token,last)
                while len(quizzes.cursors)>4096:quizzes.cursors.popitem(last=False)
            return dict(items=items,nextCursor=next_cursor,staff=staff,serverNow=now().isoformat())
