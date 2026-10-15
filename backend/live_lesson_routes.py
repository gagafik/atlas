import hashlib
import json
from fastapi import HTTPException, Query, Request, Response
from .live_lessons import stamp
from .live_lesson_models import Save, Control, AnswerWrite, Review
from .notifications import encoded
from .school_forms import grant_ids
from .quiz_models import public_question
from .payment_routes import csv_response


def register(s,app,Workspace):
    def scope(db,u,snap,w,row,student):
        manage=s.manage(u,snap,w,row)
        ids=s.audience(db,snap,row)
        if manage:return manage,ids,''
        ids &= grant_ids(u,w,'student')
        chosen=student or next(iter(sorted(ids)),'')
        if chosen not in ids:raise HTTPException(404,'Нет доступа к этому ученику.')
        return False,ids,chosen

    def head(row):
        return {k:row[k] for k in ['id','version','status','title','group_id','subject_id','created','updated']}

    def answer_view(a,q,reveal):
        if not a:return None
        result=dict(version=a['version'],submitted=bool(a['submitted']),updated=a['updated'],answer=json.loads(a['document']))
        if reveal:result['mark']=s.mark(q,a)
        return result

    @app.get('/api/live-lessons/options')
    def options(request:Request,workspace:Workspace='school'):
        with s.context(request,workspace) as (db,u,snap):
            staff=u['role'] in {'admin','teacher'}
            pairs=s.pairs(u,snap,workspace) if staff else set()
            return dict(staff=staff,canAnswer=u['role']=='student',
                pairs=[dict(groupId=g,subjectId=q,group=snap.entities['group'][g].name,subject=next(x.name for x in snap.state.subjects if x.id==q),studentCount=len(s.members(snap,g))) for g,q in sorted(pairs)],
                students=[dict(id=p.id,name=p.name) for p in snap.state.students if p.active and p.id in grant_ids(u,workspace,'student')])

    @app.get('/api/live-lessons')
    def listing(request:Request,workspace:Workspace='school',before:int=Query(default=0,ge=0),status:str='all'):
        if status not in {'all','draft','live','ended','cancelled'}:raise HTTPException(422,'Неизвестный статус занятия.')
        with s.context(request,workspace) as (db,u,snap):
            clauses=['workspace=?'];args=[workspace]
            if before:clauses.append('seq<?');args.append(before)
            if status!='all':clauses.append('status=?');args.append(status)
            if u['role']=='teacher':
                pairs=s.pairs(u,snap,workspace)
                clauses+=['owner_id=?',"EXISTS (SELECT 1 FROM json_each(?) p WHERE json_extract(p.value,'$[0]')=group_id AND json_extract(p.value,'$[1]')=subject_id)"]
                args.extend([u['id'],encoded([list(p) for p in sorted(pairs)])])
            elif u['role']!='admin':
                ids=grant_ids(u,workspace,'student') & {p.id for p in snap.state.students if p.active}
                groups={g.id for g in snap.state.groups if s.members(snap,g.id)&ids}
                clauses += ["status<>'draft'",'group_id IN (SELECT value FROM json_each(?))',
                            'EXISTS (SELECT 1 FROM live_lesson_members m WHERE m.lesson_id=live_lessons.id AND m.student_id IN (SELECT value FROM json_each(?)))']
                args.extend([encoded(sorted(groups)),encoded(sorted(ids))])
                # The same child must satisfy both the captured and current membership.
                clauses.append("EXISTS (SELECT 1 FROM live_lesson_members m JOIN json_each(?) c ON json_extract(c.value,'$[0]')=m.student_id AND json_extract(c.value,'$[1]')=live_lessons.group_id WHERE m.lesson_id=live_lessons.id)")
                args.append(encoded([[id,g.id] for g in snap.state.groups for id in sorted(s.members(snap,g.id)&ids)]))
            rows=db.execute('SELECT seq,id,version,status,title,group_id,subject_id,created,updated FROM live_lessons WHERE '+' AND '.join(clauses)+' ORDER BY seq DESC LIMIT 51',args).fetchall()
            return dict(items=[head(r) for r in rows[:50]],nextBefore=rows[49]['seq'] if len(rows)>50 else None)

    @app.post('/api/live-lessons')
    def create(body:Save,request:Request,workspace:Workspace='school'):
        with s.context(request,workspace,True) as (db,u,snap):
            d=body.definition;s.require_pair(u,snap,workspace,d.groupId,d.subjectId)
            key,old=s.replay(db,u,body,'create:'+workspace)
            return old or s.remember(db,u,body,key,s.save(db,u,snap,workspace,body))

    @app.put('/api/live-lessons/{id}')
    def edit(id:str,body:Save,request:Request,workspace:Workspace='school'):
        with s.context(request,workspace,True) as (db,u,snap):
            row=s.access_row(db,u,snap,workspace,id,staff=True);key,old=s.replay(db,u,body,'edit:'+id)
            return old or s.remember(db,u,body,key,s.save(db,u,snap,workspace,body,row))

    @app.post('/api/live-lessons/{id}/control')
    def control(id:str,body:Control,request:Request,workspace:Workspace='school'):
        with s.context(request,workspace,True) as (db,u,snap):
            row=s.access_row(db,u,snap,workspace,id,staff=True);key,old=s.replay(db,u,body,'control:'+id)
            return old or s.remember(db,u,body,key,s.control(db,u,snap,workspace,row,body))

    @app.put('/api/live-lessons/{id}/answer')
    def answer(id:str,body:AnswerWrite,request:Request,workspace:Workspace='school'):
        with s.context(request,workspace,True) as (db,u,snap):
            row=s.access_row(db,u,snap,workspace,id,body.studentId)
            if u['role']!='student':raise HTTPException(403,'Отвечать может только ученик.')
            key,old=s.replay(db,u,body,'answer:'+id)
            return old or s.remember(db,u,body,key,s.answer(db,u,snap,row,body))

    @app.put('/api/live-lessons/{id}/review')
    def review(id:str,body:Review,request:Request,workspace:Workspace='school'):
        with s.context(request,workspace,True) as (db,u,snap):
            row=s.access_row(db,u,snap,workspace,id,staff=True)
            if body.studentId not in s.audience(db,snap,row):raise HTTPException(404,'Ученик недоступен.')
            key,old=s.replay(db,u,body,'review:'+id)
            return old or s.remember(db,u,body,key,s.review(db,u,snap,row,body))

    @app.get('/api/live-lessons/{id}/status')
    def status(id:str,request:Request,workspace:Workspace='school',studentId:str=''):
        with s.context(request,workspace) as (db,u,snap):
            row=s.access_row(db,u,snap,workspace,id,studentId,metadata=True);manage,ids,student=scope(db,u,snap,workspace,row,studentId)
            rounds=s.rounds(db,row); r=rounds[-1] if rounds else None
            args=(id,encoded(sorted(ids)) if manage else encoded([student]))
            revision=db.execute('SELECT coalesce(sum(version),0) FROM live_lesson_answers WHERE lesson_id=? AND student_id IN (SELECT value FROM json_each(?))',args).fetchone()[0]
            result=dict(version=row['version'],status=row['status'],questionIndex=r['question_index'] if r else None,
                        phase=r['phase'] if r else 'waiting',deadline=r['deadline'] if r else None,answerRevision=revision)
            if manage:
                counts=db.execute('SELECT count(*),coalesce(sum(submitted),0) FROM live_lesson_answers WHERE lesson_id=? AND student_id IN (SELECT value FROM json_each(?)) AND question_index=?',(*args,r['question_index'] if r else -1)).fetchone()
                result.update(saved=counts[0],submitted=counts[1],participants=len(ids))
            etag='"'+hashlib.sha256(encoded([result,snap.state.revision,u,student]).encode()).hexdigest()+'"'
            headers={'ETag':etag,'Cache-Control':'no-store','Vary':'Cookie','X-Atlas-Server-Time':stamp()}
            if request.headers.get('if-none-match')==etag:return Response(status_code=304,headers=headers)
            return Response(encoded(result),media_type='application/json',headers=headers)

    @app.get('/api/live-lessons/{id}')
    def detail(id:str,request:Request,workspace:Workspace='school',studentId:str=''):
        with s.context(request,workspace) as (db,u,snap):
            row=s.access_row(db,u,snap,workspace,id,studentId);manage,ids,student=scope(db,u,snap,workspace,row,studentId)
            doc=json.loads(row['document']);rounds=s.rounds(db,row)
            result=head(row)|dict(manage=manage,serverNow=stamp(),instructions=doc['instructions'],questionCount=len(doc['questions']),
                group=snap.entities['group'].get(row['group_id']).name if row['group_id'] in snap.entities['group'] else 'Удалённая группа',
                subject=next((x.name for x in snap.state.subjects if x.id==row['subject_id']),'Удалённый предмет'))
            if manage:result['definition']=doc
            else:result.update(studentId=student,students=[dict(id=x,name=snap.entities['student'][x].name) for x in sorted(ids)])
            own={r['question_index']:r for r in db.execute('SELECT * FROM live_lesson_answers WHERE lesson_id=? AND student_id=?',(id,student))} if student else {}
            result['rounds']=[]
            for r in rounds:
                i=r['question_index'];q=doc['questions'][i];reveal=r['phase']=='revealed'
                item=dict(index=i,phase=r['phase'],deadline=r['deadline'],question=public_question(q))
                if manage or reveal:item['solution']=q['question']
                if student and (u['role']=='student' or reveal):item['response']=answer_view(own.get(i),q['question'],reveal)
                item['canAnswer']=bool(student and u['role']=='student' and row['status']=='live' and r['phase']=='open' and not (own.get(i) and own[i]['submitted']))
                result['rounds'].append(item)
            return result

    @app.get('/api/live-lessons/{id}/responses')
    def responses(id:str,request:Request,workspace:Workspace='school',questionIndex:int=Query(ge=0,le=49),offset:int=Query(default=0,ge=0,le=100000)):
        with s.context(request,workspace) as (db,u,snap):
            row=s.access_row(db,u,snap,workspace,id,staff=True);ids=sorted(s.audience(db,snap,row),key=lambda x:(snap.entities['student'][x].name,x))
            r=next((r for r in s.rounds(db,row) if r['question_index']==questionIndex),None)
            if not r:raise HTTPException(404,'Вопрос ещё не открывали.')
            q=json.loads(row['document'])['questions'][questionIndex]['question']
            rows={r['student_id']:r for r in db.execute('SELECT * FROM live_lesson_answers WHERE lesson_id=? AND question_index=? AND student_id IN (SELECT value FROM json_each(?))',(id,questionIndex,encoded(ids[offset:offset+50])))}
            return dict(items=[dict(studentId=p,name=snap.entities['student'][p].name,response=answer_view(rows.get(p),q,r['phase']!='open')) for p in ids[offset:offset+50]],
                phase=r['phase'],nextOffset=offset+50 if offset+50<len(ids) else None,totalCount=len(ids))

    @app.get('/api/live-lessons/{id}/history')
    def history(id:str,request:Request,workspace:Workspace='school',before:int=Query(default=0,ge=0)):
        with s.context(request,workspace) as (db,u,snap):
            row=s.access_row(db,u,snap,workspace,id,staff=True);ids=s.audience(db,snap,row)
            rows=db.execute("SELECT * FROM live_lesson_history WHERE lesson_id=? AND (?=0 OR seq<?) AND (json_extract(document,'$.studentId') IS NULL OR json_extract(document,'$.studentId') IN (SELECT value FROM json_each(?))) ORDER BY seq DESC LIMIT 51",(id,before,before,encoded(sorted(ids)))).fetchall()
            return dict(items=[{k:r[k] for k in ['seq','created','actor_name','action']}|dict(detail=json.loads(r['document'])) for r in rows[:50]],nextBefore=rows[49]['seq'] if len(rows)>50 else None)

    @app.get('/api/live-lessons/{id}/export.csv')
    def export(id:str,request:Request,workspace:Workspace='school'):
        with s.context(request,workspace) as (db,u,snap):
            row=s.access_row(db,u,snap,workspace,id,staff=True);ids=s.audience(db,snap,row)
            doc=json.loads(row['document']);rounds={r['question_index']:r for r in s.rounds(db,row)}
            def rows():
                yield ['Ученик','Вопрос','Ответ','Состояние вопроса','Балл','Максимум','Комментарий']
                for a in db.execute('SELECT * FROM live_lesson_answers WHERE lesson_id=? AND student_id IN (SELECT value FROM json_each(?)) ORDER BY student_id,question_index',(id,encoded(sorted(ids)))):
                    q=doc['questions'][a['question_index']]['question'];r=rounds[a['question_index']];answer=json.loads(a['document']);mark=s.mark(q,a) if r['phase']!='open' else None
                    text=answer['text'] or '; '.join(o['text'] for o in q['options'] if o['id'] in answer['selected'])
                    yield [snap.entities['student'][a['student_id']].name,q['text'],text,r['phase'],mark['score'] if mark else '',q['points'],mark['comment'] if mark else '']
            return csv_response(rows(),'interactive-lesson.csv')
