"""Private question bank. Assignments retain snapshots of previously selected versions."""
import json
from fastapi import HTTPException, Query, Request
from .quiz_models import BankWrite
from .access import now
from .notifications import encoded


def register(app,quizzes,Workspace):
    @app.get('/api/journal/quiz-bank')
    def listing(request:Request,workspace:Workspace='school',search:str=Query(default='',max_length=120),
                archived:bool=False,after:str=Query(default='',max_length=80),limit:int=Query(default=30,ge=1,le=50)):
        user=quizzes.journal.scope(request,workspace,staff=True)
        with quizzes.access.lock,quizzes.access.connect() as db:
            quizzes.tables(db);db.execute('BEGIN');quizzes.journal.recheck_account(db,user)
            db.create_function('atlas_fold',1,str.casefold)
            clauses=['workspace=?','archived=?','id>?'];args=[workspace,int(archived),after]
            if user['role']!='admin':clauses.append('owner_id=?');args.append(user['id'])
            if search.strip():clauses.append('instr(atlas_fold(subject||document),?)>0');args.append(search.strip().casefold())
            rows=db.execute(f"SELECT id,version,owner_id,updated,subject,archived,document FROM quiz_questions WHERE {' AND '.join(clauses)} ORDER BY id LIMIT ?",(*args,limit+1)).fetchall()
            return dict(items=[dict(id=r[0],version=r[1],ownerId=r[2],updated=r[3],subject=r[4],archived=bool(r[5]),**json.loads(r[6])) for r in rows[:limit]],
                        nextAfter=rows[limit-1][0] if len(rows)>limit else None)

    @app.put('/api/journal/quiz-bank/{id}')
    def save(id:str,body:BankWrite,request:Request,workspace:Workspace='school'):
        if not id or len(id)>80:raise HTTPException(422,'Неверный идентификатор вопроса.')
        user=quizzes.journal.scope(request,workspace,staff=True)
        with quizzes.access.lock,quizzes.access.connect() as db:
            quizzes.tables(db);db.execute('BEGIN IMMEDIATE');quizzes.journal.recheck_account(db,user)
            old=db.execute('SELECT version,owner_id FROM quiz_questions WHERE workspace=? AND id=?',(workspace,id)).fetchone()
            if old and user['role']!='admin' and old[1]!=user['id']:raise HTTPException(403,'Это вопрос другого автора.')
            if body.version!=(old[0] if old else 0):raise HTTPException(409,'Вопрос изменился. Обновите банк.')
            document=dict(tags=body.tags,question=body.question.model_dump(mode='json'));updated=now().isoformat();version=body.version+1
            owner=old[1] if old else user['id']
            db.execute('INSERT INTO quiz_questions VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(workspace,id) DO UPDATE SET version=excluded.version,updated=excluded.updated,subject=excluded.subject,archived=excluded.archived,document=excluded.document',
                       (workspace,id,version,owner,updated,body.subject,int(body.archived),encoded(document)))
            quizzes.access.record(db,'quiz.question_saved',workspace,detail=f'{id} · редакция {version}')
            return dict(id=id,version=version,ownerId=owner,updated=updated,subject=body.subject,archived=body.archived,**document)
