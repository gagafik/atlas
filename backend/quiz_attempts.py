"""Server-timed attempts, versioned autosaves and private grading until release."""
from copy import deepcopy
from datetime import datetime, timedelta
import secrets
from uuid import uuid4

from fastapi import HTTPException, Query, Request
from .access import now
from .notifications import encoded
from . import quiz_models as models, quiz_store as store


def load(db,workspace,quiz,attempt):
    row=db.execute('SELECT * FROM quiz_attempts WHERE workspace=? AND quiz_id=? AND id=?',(workspace,quiz,attempt)).fetchone()
    if not row:raise HTTPException(404,'Попытка не найдена.')
    return store.attempt(row)


def context(quizzes,request,workspace,quiz,id):
    return quizzes.context(request,workspace,quiz,attempt_id=id)


def validated_answers(quizzes,ctx,attempt,answers):
    variant=next(v for v in ctx['quiz']['published']['definition']['variants'] if v['id']==attempt['document']['variant']['id'])
    questions={q['id']:q['question'] for q in variant['questions']}
    if set(answers)-set(questions):raise HTTPException(422,'Ответ содержит вопрос из другого варианта.')
    result={}
    for id,answer in answers.items():
        q=questions[id]
        if q['kind'] in ['single','multiple']:
            if answer.text or len(set(answer.selected))!=len(answer.selected) or set(answer.selected)-{o['id'] for o in q['options']}:
                raise HTTPException(422,'Выберите допустимые варианты ответа.')
            if q['kind']=='single' and len(answer.selected)>1:raise HTTPException(422,'Можно выбрать только один ответ.')
        elif answer.selected:raise HTTPException(422,'В этом вопросе нужен текстовый ответ.')
        result[id]=answer.model_dump(mode='json')
    return result


def register(app,quizzes,Workspace):
    @app.post('/api/journal/quizzes/{id}/attempts')
    def start(id:str,body:models.Start,request:Request,workspace:Workspace='school'):
        ctx=quizzes.context(request,workspace,id,body.studentId);quiz=ctx['quiz']
        if not ctx['canAnswer']:raise HTTPException(403,'Начать тест может только назначенный ученик действующего урока.')
        with quizzes.access.lock,quizzes.access.connect() as db:
            quizzes.tables(db);db.execute('BEGIN IMMEDIATE');quizzes.recheck(db,ctx)
            if body.version!=quiz['version']:raise HTTPException(409,'Тест изменился. Обновите назначение.')
            quizzes.expire(db,ctx,body.studentId)
            previous=db.execute('SELECT * FROM quiz_attempts WHERE workspace=? AND quiz_id=? AND student_id=? AND request_id=?',
                                (workspace,id,body.studentId,body.requestId)).fetchone()
            if previous:return quizzes.attempt_view(ctx,store.attempt(previous))
            definition=quiz['published']['definition'];instant=now()
            if quiz['status']!='published' or not datetime.fromisoformat(definition['opensAt'])<=instant<datetime.fromisoformat(definition['closesAt']):
                raise HTTPException(409,'Приём ответов ещё не открыт или уже завершён.')
            rows=db.execute('SELECT * FROM quiz_attempts WHERE workspace=? AND quiz_id=? AND student_id=? ORDER BY number',
                            (workspace,id,body.studentId)).fetchall()
            active=next((r for r in rows if r[6]=='active'),None)
            if active:return quizzes.attempt_view(ctx,store.attempt(active))
            if len(rows)>=definition['maxAttempts']:raise HTTPException(409,'Все разрешённые попытки использованы.')
            variant=next(v for v in definition['variants'] if v['id']==quiz['published']['variantsByStudent'][body.studentId])
            order=[q['id'] for q in variant['questions']];options={q['id']:[o['id'] for o in q['question']['options']] for q in variant['questions']}
            if definition['shuffleQuestions']:secrets.SystemRandom().shuffle(order)
            if definition['shuffleOptions']:
                for values in options.values():secrets.SystemRandom().shuffle(values)
            document=dict(variant=dict(id=variant['id'],name=variant['name']),order=order,optionOrder=options,answers={},publicationVersion=quiz['version'])
            deadline=min(datetime.fromisoformat(definition['closesAt']),instant+timedelta(minutes=definition['timeLimitMinutes']))
            maximum=round(sum(q['question']['points'] for q in variant['questions']),6)
            values=(workspace,uuid4().hex,id,body.studentId,len(rows)+1,1,'active',instant.isoformat(),deadline.isoformat(),None,body.requestId,encoded(document),None,maximum)
            db.execute('INSERT INTO quiz_attempts VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)',values);attempt=store.attempt(values)
            quizzes.log(db,ctx,'quiz.started',dict(number=attempt['number'],deadline=attempt['deadline'],variant=document['variant']),attempt)
            return quizzes.attempt_view(ctx,attempt)

    @app.get('/api/journal/quizzes/{id}/attempts/{attempt_id}')
    def detail(id:str,attempt_id:str,request:Request,workspace:Workspace='school'):
        ctx=context(quizzes,request,workspace,id,attempt_id)
        def read(db):
            attempt=load(db,workspace,id,attempt_id)
            return quizzes.attempt_view(ctx,attempt)|dict(serverNow=now().isoformat(),canAnswer=ctx['canAnswer'] and ctx['quiz']['status']=='published' and attempt['status']=='active')
        return quizzes.read_attempts(ctx,read)

    @app.put('/api/journal/quizzes/{id}/attempts/{attempt_id}')
    def answer(id:str,attempt_id:str,body:models.SaveAnswers,request:Request,workspace:Workspace='school'):
        ctx=context(quizzes,request,workspace,id,attempt_id)
        if not ctx['canAnswer']:raise HTTPException(403,'Ответ сохраняет только назначенный ученик действующего урока.')
        with quizzes.access.lock,quizzes.access.connect() as db:
            quizzes.tables(db);db.execute('BEGIN IMMEDIATE');quizzes.recheck(db,ctx)
            attempt=load(db,workspace,id,attempt_id)
            if attempt['status']!='active' or ctx['quiz']['status']!='published':raise HTTPException(409,'Попытка уже завершена. Обновите её.')
            instant=now()
            if instant>=datetime.fromisoformat(attempt['deadline']):
                # Persist only the last server-saved answers. Return the closed
                # attempt successfully so finalization is not rolled back by an error.
                quizzes.finish(db,ctx,attempt,instant,'timed_out')
                return quizzes.attempt_view(ctx,attempt)|dict(expired=True)
            if body.version!=attempt['version']:raise HTTPException(409,'Ответы изменились в другой вкладке. Обновите попытку.')
            attempt['document']['answers']=validated_answers(quizzes,ctx,attempt,body.answers)
            if body.submit:quizzes.finish(db,ctx,attempt,instant)
            else:attempt['version']+=1;quizzes.persist_attempt(db,ctx,attempt)
            return quizzes.attempt_view(ctx,attempt)

    @app.post('/api/journal/quizzes/{id}/attempts/{attempt_id}/review')
    def review(id:str,attempt_id:str,body:models.ReviewAttempt,request:Request,workspace:Workspace='school'):
        ctx=context(quizzes,request,workspace,id,attempt_id)
        if not ctx['canReview']:raise HTTPException(403,'Проверку выполняет действующий учитель этого ученика или администратор.')
        with quizzes.access.lock,quizzes.access.connect() as db:
            quizzes.tables(db);db.execute('BEGIN IMMEDIATE');quizzes.recheck(db,ctx)
            quizzes.expire(db,ctx,ctx['student']);attempt=load(db,workspace,id,attempt_id)
            if attempt['version']!=body.version:raise HTTPException(409,'Попытка изменилась. Обновите проверку.')
            if attempt['status']=='active':raise HTTPException(409,'Дождитесь отправки или окончания времени.')
            if ctx['quiz']['released'] and not body.reason:raise HTTPException(422,'Для исправления опубликованного результата укажите причину.')
            questions={q['id']:q['question'] for q in quizzes.attempt_questions(ctx,attempt)}
            if set(body.marks)-set(questions):raise HTTPException(422,'Оценка относится к другому вопросу.')
            marks=deepcopy(attempt['document']['marks'])
            for key,mark in body.marks.items():
                if mark.score>questions[key]['points']:raise HTTPException(422,'Баллы превышают максимум вопроса.')
                if questions[key]['kind']!='essay' and not mark.comment:raise HTTPException(422,'Для изменения автоматической проверки укажите объяснение.')
                marks[key]=dict(score=mark.score,comment=mark.comment,automatic=False)
            if set(marks)!=set(questions):raise HTTPException(422,'Оцените все вопросы с ручной проверкой.')
            attempt['document'].update(marks=marks,comment=body.comment,reviewedAt=now().isoformat(),reviewer=ctx['user']['name'],correctionReason=body.reason)
            attempt['version']+=1;attempt['status']='reviewed'
            quizzes.persist_attempt(db,ctx,attempt);quizzes.log(db,ctx,'quiz.reviewed',deepcopy(attempt),attempt)
            if ctx['quiz']['released']:quizzes.notify(db,ctx,'quiz.results_updated',[ctx['student']])
            return quizzes.attempt_view(ctx,attempt)

    @app.get('/api/journal/quizzes/{id}/attempts/{attempt_id}/history')
    def history(id:str,attempt_id:str,request:Request,workspace:Workspace='school',before:int|None=Query(default=None,ge=1)):
        ctx=context(quizzes,request,workspace,id,attempt_id)
        def read(db):
            quizzes.recheck(db,ctx)
            rows=db.execute('SELECT id,created,actor,action,document FROM quiz_history WHERE workspace=? AND quiz_id=? AND attempt_id=? AND (? IS NULL OR id<?) ORDER BY id DESC LIMIT 21',
                            (workspace,id,attempt_id,before,before)).fetchall()
            import json
            items=[]
            for r in rows[:20]:
                document=json.loads(r[4]);item=dict(id=r[0],created=r[1],actor=r[2],action=r[3])
                if 'document' in document:item['attempt']=quizzes.attempt_view(ctx,document)
                items.append(item)
            return dict(items=items,nextBefore=rows[19][0] if len(rows)>20 else None)
        return quizzes.read_only(read)
