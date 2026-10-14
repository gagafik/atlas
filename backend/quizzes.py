"""Lesson-scoped online tests with immutable published questions and attempt clocks."""
from copy import deepcopy
from collections import OrderedDict
from datetime import datetime, timedelta
import json
import secrets
from uuid import uuid4

from fastapi import HTTPException, Query, Request

from .access import now
from .journal import staff_read, user_ids, snapshot
from .journal_periods import require_open
from .notifications import encoded
from . import quiz_models as models, quiz_store as store


class Quizzes:
    def __init__(self, journal, snapshots):
        self.journal=journal;self.access=journal.access;self.snapshots=snapshots
        self.cursors=OrderedDict()

    def tables(self,db):
        self.journal.tables(db);self.access.tables(db);store.tables(db)

    def recheck(self,db,ctx):
        self.journal.recheck_account(db,ctx['user'])
        if db.execute('SELECT revision FROM workspaces WHERE id=?',(ctx['workspace'],)).fetchone()!=(ctx['revision'],):
            raise HTTPException(409,'Расписание или учебные связи изменились. Обновите тест.')
        row=db.execute('SELECT version FROM quizzes WHERE workspace=? AND id=?',(ctx['workspace'],ctx['quiz']['id'])).fetchone()
        if row!=(ctx['quiz']['version'],):raise HTTPException(409,'Тест изменился. Обновите его перед продолжением.')

    def resolve(self,user,snap,quiz,student=None):
        workspace=quiz['workspace'];staff=user['role'] in ['admin','teacher']
        document=quiz['published'] or quiz['document'];lesson=document['lesson']
        current=snap.current_lesson(lesson)
        if staff:
            if not (staff_read(user,workspace,lesson) or current and staff_read(user,workspace,current)):
                raise HTTPException(403,'Тест относится к другому уроку.')
        elif not quiz['published'] or student not in user_ids(user,workspace,'student') or student not in document['studentIds']:
            raise HTTPException(403,'Нет доступа к этому назначению теста.')
        if student is not None and student not in document['studentIds']:raise HTTPException(404,'Ученику не назначен этот тест.')
        active=bool(current and current['status']!='cancelled')
        enrolled=bool(active and student in current['studentIds'])
        return dict(user=user,staff=staff,workspace=workspace,quiz=quiz,student=student,current=current,state=snap.state,revision=snap.state.revision,
                    canReview=bool(staff and active and staff_read(user,workspace,current) and (student is None or enrolled)),
                    canAnswer=bool(user['role']=='student' and enrolled),key=quiz['lessonKey'])

    def context(self,request,workspace,id,student=None,attempt_id=None):
        user=self.journal.scope(request,workspace);snap=self.snapshots.get(workspace)
        with self.access.lock,self.access.connect() as db:
            self.tables(db)
            if attempt_id is not None:
                member=db.execute('SELECT student_id FROM quiz_attempts WHERE workspace=? AND quiz_id=? AND id=?',(workspace,id,attempt_id)).fetchone()
                if not member:raise HTTPException(404,'Попытка не найдена.')
                student=member[0]
            row=db.execute('SELECT * FROM quizzes WHERE workspace=? AND id=?',(workspace,id)).fetchone()
        if not row:raise HTTPException(404,'Тест не найден.')
        return self.resolve(user,snap,store.unpack(row),student)

    def notify(self,db,ctx,action,students=None):
        if action=='quiz.submitted':
            ids=ctx['current']['teacherIds'] if ctx['current'] else []
            targets=self.journal.notifications.granted_users(db,ctx['workspace'],'teacher',ids,['teacher'])
        else:
            targets=self.journal.notifications.granted_users(db,ctx['workspace'],'student',students or [],['student','parent'])
        self.journal.notifications.deliver(db,targets,ctx['workspace'],ctx['quiz']['version'],action)

    def log(self,db,ctx,action,document,attempt=None):
        created=now().isoformat();id=ctx['quiz']['id']
        store.history(db,ctx['workspace'],id,attempt['id'] if attempt else None,attempt['studentId'] if attempt else None,
                      created,ctx['user']['name'],action,document)
        self.access.record(db,action,ctx['workspace'],ctx['revision'],detail=f'{id}'+(f' · попытка {attempt["id"]}' if attempt else ''))

    def persist_attempt(self,db,ctx,attempt):
        score=round(sum(m['score'] for m in attempt['document'].get('marks',{}).values()),6) if attempt['status']=='reviewed' else None
        db.execute('UPDATE quiz_attempts SET version=?,status=?,submitted=?,document=?,score=? WHERE workspace=? AND id=?',
                   (attempt['version'],attempt['status'],attempt['submittedAt'],encoded(attempt['document']),score,ctx['workspace'],attempt['id']))

    def attempt_questions(self,ctx,attempt):
        document=attempt['document']
        variant=next(v for v in ctx['quiz']['published']['definition']['variants'] if v['id']==document['variant']['id'])
        questions={q['id']:q for q in variant['questions']};result=[]
        for id in document['order']:
            item=deepcopy(questions[id]);options={o['id']:o for o in item['question']['options']}
            item['question']['options']=[options[o] for o in document['optionOrder'][id]];result.append(item)
        return result

    def finish(self,db,ctx,attempt,instant,reason='submitted'):
        if attempt['status']!='active':return
        document=attempt['document'];marks={}
        questions=self.attempt_questions(ctx,attempt)
        for item in questions:
            mark=models.auto_mark(item['question'],document['answers'].get(item['id'],{}))
            if mark is not None:marks[item['id']]=mark
        document.update(marks=marks,comment='',finishReason=reason)
        attempt.update(status='reviewed' if len(marks)==len(questions) else 'submitted',version=attempt['version']+1,
                       submittedAt=min(instant,datetime.fromisoformat(attempt['deadline'])).isoformat())
        self.persist_attempt(db,ctx,attempt);self.log(db,ctx,'quiz.submitted',deepcopy(attempt),attempt)
        self.notify(db,ctx,'quiz.submitted')

    def expire(self,db,ctx,student=None,force=False):
        instant=now();query="SELECT * FROM quiz_attempts WHERE workspace=? AND quiz_id=? AND status='active'"
        args=[ctx['workspace'],ctx['quiz']['id']]
        if student:query+=' AND student_id=?';args.append(student)
        if not force:query+=' AND deadline<=?';args.append(instant.isoformat())
        for row in db.execute(query,args).fetchall():self.finish(db,ctx,store.attempt(row),instant,'closed' if force else 'timed_out')

    def attempt_view(self,ctx,attempt):
        value={k:v for k,v in attempt.items() if k not in ['document','requestId']}
        value.update(canAnswer=bool(ctx['canAnswer'] and ctx['quiz']['status']=='published' and attempt['status']=='active'),canReview=ctx['canReview'])
        active=attempt['status']=='active';own=ctx['user']['role']=='student' and attempt['studentId']==ctx['student']
        if active and not own:return value
        document=attempt['document'];show=ctx['staff'] or ctx['quiz']['released']
        questions=self.attempt_questions(ctx,attempt)
        value.update(questions=[models.public_question(q) for q in questions],answers=document['answers'],
                     variant=document['variant'],maxScore=sum(q['question']['points'] for q in questions))
        if not active:
            value.update(finishReason=document['finishReason'],resultsVisible=show)
            if show:
                value.update(marks=document['marks'],comment=document['comment'],solutions=questions,grading=document.get('grading'))
                value['score']=round(sum(m['score'] for m in document['marks'].values()),6) if attempt['status']=='reviewed' else None
                value['correctionReason']=document.get('correctionReason','')
                if value['grading']:value['grading']=dict(value['grading'],stale=value['grading']['sourceScore']!=value['score'])
        return value

    def view(self,db,ctx):
        quiz=ctx['quiz'];document=quiz['published'] or quiz['document'];definition=document['definition']
        result=dict(id=quiz['id'],version=quiz['version'],status=quiz['status'],released=quiz['released'],staff=ctx['staff'],canManage=ctx['canReview'],
                    lessonKey=quiz['lessonKey'],lessonDate=document['lesson']['date'],subject=document['lesson']['subject'],group=document['lesson']['group'],
                    title=definition['title'],instructions=definition['instructions'],opensAt=definition['opensAt'],closesAt=definition['closesAt'],
                    timeLimitMinutes=definition['timeLimitMinutes'],maxAttempts=definition['maxAttempts'],serverNow=now().isoformat())
        if ctx['staff']:result.update(document=quiz['document'],revision=ctx['revision'])
        if ctx['student']:
            self.expire(db,ctx,ctx['student'])
            rows=db.execute('SELECT id,number,status,started,deadline,submitted,score,max_score FROM quiz_attempts WHERE workspace=? AND quiz_id=? AND student_id=? ORDER BY number DESC',
                        (ctx['workspace'],quiz['id'],ctx['student'])).fetchall()
            attempts=[]
            for r in rows:
                item=dict(id=r[0],number=r[1],status=r[2],startedAt=r[3],deadline=r[4],submittedAt=r[5],resultsVisible=bool(quiz['released'] and r[2]!='active'))
                if item['resultsVisible']:item.update(score=r[6],maxScore=r[7])
                attempts.append(item)
            result.update(studentId=ctx['student'],attempts=attempts)
            current=now();window=datetime.fromisoformat(definition['opensAt'])<=current<datetime.fromisoformat(definition['closesAt'])
            result['canStart']=bool(ctx['canAnswer'] and quiz['status']=='published' and window and (any(a['status']=='active' for a in attempts) or len(attempts)<definition['maxAttempts']))
        elif ctx['staff']:
            self.expire(db,ctx)
            rows=db.execute('SELECT student_id,id,number,status,started,deadline,submitted,score,max_score FROM quiz_attempts WHERE workspace=? AND quiz_id=? ORDER BY student_id,number',
                            (ctx['workspace'],quiz['id'])).fetchall()
            grouped={id:[] for id in document['studentIds']}
            for r in rows:grouped[r[0]].append(dict(id=r[1],number=r[2],status=r[3],startedAt=r[4],deadline=r[5],submittedAt=r[6],score=r[7],maxScore=r[8]))
            names={s['id']:s['name'] for s in document['lesson']['students']}
            result['students']=[dict(id=id,name=names[id],variant=document['variantsByStudent'][id],attempts=grouped[id],
                                     enrolled=bool(ctx['current'] and ctx['current']['status']!='cancelled' and id in ctx['current']['studentIds'])) for id in document['studentIds']]
        return result

    def questions(self,db,workspace,user,definition,old=None):
        previous={(q['id'],q['version']):q for v in (old or {}).get('definition',{}).get('variants',[]) for q in v['questions']}
        resolved=definition.model_dump(mode='json');totals=[]
        for variant in resolved['variants']:
            questions=[]
            for ref in variant['questions']:
                if (ref['id'],ref['version']) in previous:question=previous[ref['id'],ref['version']]
                else:
                    row=db.execute('SELECT version,owner_id,archived,document FROM quiz_questions WHERE workspace=? AND id=?',(workspace,ref['id'])).fetchone()
                    if not row or row[2] or user['role']!='admin' and row[1]!=user['id']:raise HTTPException(403,'Вопрос отсутствует в доступном банке.')
                    if row[0]!=ref['version']:raise HTTPException(409,'Вопрос в банке изменился. Выберите его заново.')
                    question=dict(**ref,question=json.loads(row[3])['question'])
                questions.append(deepcopy(question))
            variant['questions']=questions;totals.append(round(sum(q['question']['points'] for q in questions),6))
        if len(set(totals))!=1 or totals[0]>10000:raise HTTPException(422,'Варианты должны иметь одинаковый максимум баллов, не более 10000.')
        if len(encoded(resolved).encode('utf-8'))>2*1024*1024:raise HTTPException(422,'Текст и эталоны всех вариантов должны занимать не более 2 МиБ.')
        return resolved

    def register(self,app,Workspace):
        from .quiz_bank import register as register_bank
        from .quiz_attempts import register as register_attempts
        from .quiz_catalog import register as register_catalog
        from .quiz_grading import register as register_grading
        register_bank(app,self,Workspace);register_catalog(app,self,Workspace);register_attempts(app,self,Workspace);register_grading(app,self,Workspace)

        @app.put('/api/journal/quizzes/{id}')
        def save(id:str,body:models.QuizWrite,request:Request,workspace:Workspace='school'):
            if len(id)>80 or not id:raise HTTPException(422,'Неверный идентификатор теста.')
            user=self.journal.scope(request,workspace,staff=True);snap=self.snapshots.get(workspace)
            record=self.journal.entry(snap.state,workspace,user,body.lessonKey,body.day)
            if not record['writable'] or record['needsSync']:raise HTTPException(409,'Сначала обновите состав урока и откройте его период в журнале.')
            if body.revision!=snap.state.revision or record['currentDate']!=body.day.isoformat():raise HTTPException(409,'Урок изменился. Обновите список.')
            if not snap.state.settings.yearStart<=body.day<=snap.state.settings.yearEnd:raise HTTPException(422,'Урок вне учебного года.')
            lesson=record['document']['lesson'];pupils={s['id'] for s in lesson['students'] if s.get('enrolled',True)}
            if set(body.studentIds)-pupils:raise HTTPException(422,'Тест можно назначить только действующим ученикам урока.')
            with self.access.lock,self.access.connect() as db:
                self.tables(db);db.execute('BEGIN IMMEDIATE');self.journal.recheck_account(db,user)
                if db.execute('SELECT revision FROM workspaces WHERE id=?',(workspace,)).fetchone()!=(body.revision,):raise HTTPException(409,'Расписание изменилось.')
                require_open(db,workspace,lesson)
                row=db.execute('SELECT * FROM quizzes WHERE workspace=? AND id=?',(workspace,id)).fetchone();old=store.unpack(row) if row else None
                if body.version!=(old['version'] if old else 0):raise HTTPException(409,'Тест уже изменён. Обновите его.')
                if old and (old['lessonKey']!=body.lessonKey or old['status']!='draft'):
                    raise HTTPException(409,'Опубликованный тест сохраняет свои условия. Для изменений создайте копию.')
                if body.publish and body.definition.closesAt<=now():raise HTTPException(422,'Нельзя опубликовать тест с истёкшим сроком.')
                previous=old['document'] if old else None
                if not old and body.copyFrom:
                    source=db.execute('SELECT * FROM quizzes WHERE workspace=? AND id=?',(workspace,body.copyFrom)).fetchone()
                    if not source:raise HTTPException(404,'Исходный тест не найден.')
                    copy=self.resolve(user,snap,store.unpack(source));previous=copy['quiz']['document']
                definition=self.questions(db,workspace,user,body.definition,previous)
                assignments=dict(body.variantsByStudent);variant_ids=[v['id'] for v in definition['variants']]
                unassigned=[s for s in body.studentIds if s not in assignments];secrets.SystemRandom().shuffle(unassigned)
                counts={v:sum(x==v for x in assignments.values()) for v in variant_ids}
                for student in unassigned:
                    variant=min(variant_ids,key=lambda v:counts[v]);assignments[student]=variant;counts[variant]+=1
                document=dict(lesson=lesson,definition=definition,studentIds=body.studentIds,variantsByStudent=assignments)
                version=body.version+1;created=now().isoformat();status='published' if body.publish else 'draft'
                summary=dict(lesson={k:v for k,v in lesson.items() if k!='students'},definition={k:v for k,v in definition.items() if k!='variants'},studentIds=[])
                values=(workspace,id,version,old['ownerId'] if old else user['id'],created,status,body.definition.closesAt.isoformat(),body.lessonKey,encoded(document),encoded(document) if body.publish else None,0,encoded(summary))
                db.execute('INSERT INTO quizzes VALUES (?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(workspace,id) DO UPDATE SET version=excluded.version,updated=excluded.updated,status=excluded.status,closes=excluded.closes,document=excluded.document,published_document=excluded.published_document,summary=excluded.summary',values)
                db.execute('DELETE FROM quiz_members WHERE workspace=? AND quiz_id=?',(workspace,id))
                if body.publish:
                    names={s['id']:s['name'] for s in lesson['students']}
                    db.executemany('INSERT INTO quiz_members VALUES (?,?,?,?,?)',[(workspace,id,s,names[s],assignments[s]) for s in body.studentIds])
                store.bump(db,workspace)
                ctx=self.resolve(user,snap,store.unpack(values));self.log(db,ctx,'quiz.published' if body.publish else 'quiz.draft_saved',dict(version=version,document=document))
                if body.publish:self.notify(db,ctx,'quiz.published',body.studentIds)
                return self.view(db,ctx)

        @app.get('/api/journal/quizzes/{id}')
        def detail(id:str,request:Request,workspace:Workspace='school',studentId:str|None=None):
            ctx=self.context(request,workspace,id,studentId)
            with self.access.lock,self.access.connect() as db:
                self.tables(db);db.execute('BEGIN IMMEDIATE');self.recheck(db,ctx);return self.view(db,ctx)

        @app.post('/api/journal/quizzes/{id}/actions')
        def action(id:str,body:models.QuizAction,request:Request,workspace:Workspace='school'):
            ctx=self.context(request,workspace,id)
            if not ctx['canReview']:raise HTTPException(403,'Управлять тестом может действующий учитель урока или администратор.')
            quiz=ctx['quiz']
            with self.access.lock,self.access.connect() as db:
                self.tables(db);db.execute('BEGIN IMMEDIATE');self.recheck(db,ctx)
                if body.version!=quiz['version']:raise HTTPException(409,'Тест изменился. Обновите его.')
                if body.action=='release':
                    if quiz['status'] not in ['published','closed'] or quiz['released']:raise HTTPException(409,'Разбор уже открыт или тест не опубликован.')
                    if quiz['status']=='published' and now()<datetime.fromisoformat(quiz['closesAt']):raise HTTPException(409,'Сначала завершите приём ответов для всех учеников.')
                    self.expire(db,ctx)
                    if db.execute("SELECT 1 FROM quiz_attempts WHERE workspace=? AND quiz_id=? AND status<>'reviewed' LIMIT 1",(workspace,id)).fetchone():
                        raise HTTPException(409,'Сначала завершите ручную проверку всех отправленных попыток.')
                    quiz['released']=True;quiz['status']='closed'
                elif body.action=='close':
                    if quiz['status']!='published':raise HTTPException(409,'Приём этого теста уже закрыт.')
                    self.expire(db,ctx,force=True);quiz['status']='closed'
                else:
                    if quiz['status']=='withdrawn':raise HTTPException(409,'Тест уже отозван.')
                    self.expire(db,ctx,force=True);quiz['status']='withdrawn'
                quiz['version']+=1
                db.execute('UPDATE quizzes SET version=?,status=?,released=?,updated=? WHERE workspace=? AND id=?',
                           (quiz['version'],quiz['status'],int(quiz['released']),now().isoformat(),workspace,id))
                store.bump(db,workspace);self.log(db,ctx,'quiz.'+body.action,dict(version=quiz['version'],reason=body.reason))
                self.notify(db,ctx,'quiz.'+body.action,(quiz['published'] or quiz['document'])['studentIds'])
                return self.view(db,ctx)
