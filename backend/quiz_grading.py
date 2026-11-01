"""Explicit publication of a reviewed quiz result through the existing journal contract."""
from copy import deepcopy
from fastapi import HTTPException, Request
from .access import now
from .quiz_models import Version
from .quiz_attempts import context, load
from .quizzes import is_course
from .homework_grading import HomeworkGrading, ReviewGrade


class PublishGrade(Version):
    grading: ReviewGrade


def prepared(quizzes,request,workspace,id,attempt_id,project):
    ctx=context(quizzes,request,workspace,id,attempt_id)
    if not ctx['canReview']:raise HTTPException(403,'Оценку публикует действующий учитель ученика или администратор.')
    if is_course((ctx['quiz']['published'] or ctx['quiz']['document'])['lesson']):raise HTTPException(409,'Результаты теста курса переносятся в журнал из таблицы результатов.')
    if not ctx['quiz']['released']:raise HTTPException(409,'Сначала откройте разбор теста, чтобы результат стал доступен семье.')
    def read(db):
        quizzes.recheck(db,ctx)
        row=db.execute('SELECT * FROM journal_entries WHERE workspace=? AND lesson_key=?',(workspace,ctx['key'])).fetchone()
        record=quizzes.journal.unpack(row) if row else None
        if not record or not record['published']:raise HTTPException(409,'Сначала опубликуйте урок в журнале с актуальным составом.')
        ctx.update(record=record,published=record['published'])
        return project(db,ctx)
    return quizzes.read_only(read)


def register(app,quizzes,Workspace):
    grading=HomeworkGrading(quizzes)

    @app.get('/api/journal/quizzes/{id}/attempts/{attempt_id}/grading')
    def options(id:str,attempt_id:str,request:Request,workspace:Workspace='school'):
        return prepared(quizzes,request,workspace,id,attempt_id,lambda db,ctx:grading.options(ctx,db=db))

    @app.post('/api/journal/quizzes/{id}/attempts/{attempt_id}/grade')
    def publish(id:str,attempt_id:str,body:PublishGrade,request:Request,workspace:Workspace='school'):
        ctx,record=prepared(quizzes,request,workspace,id,attempt_id,lambda db,ctx:(ctx,grading.prepare(ctx,db=db)))
        with quizzes.access.lock,quizzes.access.connect() as db:
            quizzes.tables(db);db.execute('BEGIN IMMEDIATE');quizzes.recheck(db,ctx)
            attempt=load(db,workspace,id,attempt_id)
            if attempt['version']!=body.version:raise HTTPException(409,'Результат теста изменился. Обновите его.')
            if attempt['status']!='reviewed':raise HTTPException(409,'Сначала завершите проверку всех вопросов.')
            evidence=grading.apply(db,ctx,record,body.grading,now().isoformat())
            evidence['sourceScore']=round(sum(m['score'] for m in attempt['document']['marks'].values()),6)
            attempt['document']['grading']=evidence;attempt['version']+=1
            quizzes.persist_attempt(db,ctx,attempt);quizzes.log(db,ctx,'quiz.graded',deepcopy(attempt),attempt)
            return quizzes.attempt_view(ctx,attempt)
