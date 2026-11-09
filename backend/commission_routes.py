"""HTTP routes of commission exams; every request rechecks the current rights inside its transaction."""
from datetime import date,timedelta
import json
import re
import uuid

from fastapi import HTTPException,Query,Request
from .commission_exams import NewExam,EditExam,Cancel,Result,Decide,Letter,KINDS,STATES,PAGE,stamp,today,conflict,has_table
from .natural import natural
from . import school_years
from .notifications import encoded,student_ids

BASE='/api/behaviour/commission-exams'


def register(service,app,Workspace):
    def admin(user):
        if user['role']!='admin':raise HTTPException(403,'Действие доступно администрации школы.')

    def staff(db,user,snap,workspace):
        """Administrator, or a teacher who is on a commission now."""
        if user['role']=='admin':return
        if service.flags(db,user,snap,workspace) is None or user['role']!='teacher':raise HTTPException(403,'Раздел доступен администрации и членам комиссии.')

    def member(user,snap,workspace,row):
        return bool(service.mine(user,snap,workspace)&set(json.loads(row['members'])))

    def check(row,version):
        if row['version']!=version:raise conflict('version','Экзамен изменился. Обновите список.')

    def need(row,*states):
        if row['status'] not in states:raise conflict('status','Статус экзамена не позволяет это действие.')

    def label(day):return date.fromisoformat(day).strftime('%d.%m.%Y')

    def sealed(db,workspace,period_id):
        """An exam of an archived year is read-only: its final grade is frozen, so no new fact may be written about it (409 archivedYear)."""
        if has_table(db,'journal_periods'):school_years.require_current(db,workspace,service.finals.periods.find(db,workspace,period_id)['start'])

    def plan(db,snap,workspace,body):
        """Validate a plan against the school as it is now; returns the stored document."""
        pupil=snap.entities['student'].get(body.studentId)
        if not pupil or not pupil.active:raise HTTPException(422,'Выберите действующего ученика.')
        names=service.names(snap)
        if body.subjectId not in names['subjects'] or body.roomId not in names['rooms']:raise HTTPException(422,'Выберите предмет и кабинет школы.')
        people=[body.chairmanId,body.examinerId,*body.memberIds]
        if any(id not in names['teachers'] for id in people):raise HTTPException(422,'Комиссию составляют учителя школы.')
        if not has_table(db,'journal_periods'):raise HTTPException(404,'Учебный период не найден.')
        period=service.finals.periods.find(db,workspace,body.periodId)
        school_years.require_current(db,workspace,period['start'])
        if body.day>today()+timedelta(days=366) or body.day<today()-timedelta(days=366):raise HTTPException(422,'Дата экзамена — не дальше года от сегодняшнего дня.')
        return dict(kind=body.kind,reason=body.reason.strip(),student=pupil.name,className=names['classes'].get(pupil.classId,''),subject=names['subjects'][body.subjectId],period=period['name'],
                    start=body.start,end=body.end,roomId=body.roomId,room=names['rooms'][body.roomId],chairmanId=body.chairmanId,examinerId=body.examinerId,memberIds=list(body.memberIds),
                    names={id:names['teachers'][id] for id in people})

    def duplicate(db,workspace,body,skip=''):
        if db.execute("SELECT 1 FROM commission_exams WHERE workspace=? AND student_id=? AND subject_id=? AND period_id=? AND status IN ('planned','held') AND id!=?",(workspace,body.studentId,body.subjectId,body.periodId,skip)).fetchone():
            raise conflict('duplicate','Для этого ученика, предмета и периода уже есть экзамен комиссии.')

    def members_of(data):return [data['chairmanId'],data['examinerId'],*data['memberIds']]

    def save(db,row,data,status,version,columns=None):
        columns=columns or {}
        db.execute('UPDATE commission_exams SET version=?,updated=?,status=?,document=?,day=?,members=? WHERE workspace=? AND id=?',
                   (version,stamp(),status,encoded(data),columns.get('day',row['day']),encoded(members_of(data)),row['workspace'],row['id']))

    def staff_card(db,user,snap,workspace,id):
        return service.public(service.exam(db,workspace,id),snap,user,service.sealed(db,workspace))

    @app.get(BASE+'/options')
    def options(request:Request,workspace:Workspace='school'):
        with service.context(request,workspace) as (db,user,snap):
            staff(db,user,snap,workspace)
            scales=[]
            if has_table(db,'grading_scales'):
                for raw, in db.execute('SELECT document FROM grading_scales WHERE workspace=? ORDER BY id',(workspace,)):
                    scale=json.loads(raw)
                    if scale['active']:scales.append(dict(id=scale['id'],version=scale['version'],name=scale['name'],labels=[b['label'] for b in scale['bands']]))
            result=dict(kinds=KINDS,statuses=STATES,today=today().isoformat(),scales=scales)
            if user['role']=='admin':
                classes={c.id:c.name for c in snap.state.classes}
                periods=[service.finals.periods.item(r) for r in db.execute('SELECT * FROM journal_periods WHERE workspace=? ORDER BY start_day DESC',(workspace,))] if has_table(db,'journal_periods') else []
                pupils=sorted((s for s in snap.state.students if s.active),key=lambda s:(natural(classes.get(s.classId,'')),natural(s.name)))
                result|=dict(teachers=[dict(id=t.id,name=t.name) for t in sorted(snap.state.teachers,key=lambda t:natural(t.name))],
                             rooms=[dict(id=r.id,name=r.name) for r in sorted(snap.state.rooms,key=lambda r:natural(r.name))],
                             subjects=[dict(id=s.id,name=s.name) for s in sorted(snap.state.subjects,key=lambda s:natural(s.name))],
                             periods=[dict(id=p['id'],name=p['name'],status=p['status']) for p in periods],
                             classes=[dict(id=id,name=name) for id,name in sorted(classes.items(),key=lambda c:natural(c[1]))],
                             students=[dict(id=s.id,name=s.name,classId=s.classId,className=classes.get(s.classId,'')) for s in pupils])
            return result

    @app.get(BASE)
    def listing(request:Request,workspace:Workspace='school',status:str|None=Query(default=None,pattern='^(planned|held|decided|letter_sent|cancelled)$'),
                periodId:str|None=Query(default=None,max_length=100),classId:str|None=Query(default=None,max_length=100),before:int|None=Query(default=None,ge=1)):
        with service.context(request,workspace) as (db,user,snap):
            staff(db,user,snap,workspace)
            clause,params='workspace=?',[workspace]
            if user['role']!='admin':
                clause+=' AND EXISTS(SELECT 1 FROM json_each(members) m WHERE m.value IN (SELECT value FROM json_each(?)))';params.append(encoded(sorted(service.mine(user,snap,workspace))))
            for column,value in [('status',status),('period_id',periodId),('class_id',classId)]:
                if value:clause+=f' AND {column}=?';params.append(value)
            if before:clause+=' AND seq<?';params.append(before)
            rows=db.execute(f'SELECT * FROM commission_exams WHERE {clause} ORDER BY seq DESC LIMIT {PAGE+1}',params).fetchall();locked=service.sealed(db,workspace)
            return dict(items=[service.public(dict(r)|dict(data=json.loads(r['document'])),snap,user,locked) for r in rows[:PAGE]],nextBefore=rows[PAGE-1]['seq'] if len(rows)>PAGE else None)

    @app.post(BASE)
    def create(body:NewExam,request:Request,workspace:Workspace='school'):
        with service.context(request,workspace,True) as (db,user,snap):
            admin(user)
            key,previous=service.replay(db,user,body)
            if previous:return previous
            data=plan(db,snap,workspace,body);duplicate(db,workspace,body)
            id=uuid.uuid4().hex;created=stamp();pupil=snap.entities['student'][body.studentId]
            db.execute('INSERT INTO commission_exams(id,workspace,student_id,class_id,subject_id,period_id,day,status,version,created,updated,members,document) VALUES (?,?,?,?,?,?,?,\'planned\',1,?,?,?,?)',
                       (id,workspace,body.studentId,pupil.classId,body.subjectId,body.periodId,body.day.isoformat(),created,created,encoded(members_of(data)),encoded(data)))
            service.history(db,workspace,user,id,'created','',data|dict(day=body.day.isoformat(),version=1))
            service.notify_members(db,workspace,id,[dict(id=pupil.id,name=pupil.name)],members_of(data),1,f"Комиссионный экзамен: {data['subject']}, {label(body.day.isoformat())} {body.start}")
            return service.remember(db,user,body,key,staff_card(db,user,snap,workspace,id))

    @app.put(BASE+'/{id}')
    def edit(id:str,body:EditExam,request:Request,workspace:Workspace='school'):
        with service.context(request,workspace,True) as (db,user,snap):
            admin(user);row=service.exam(db,workspace,id);check(row,body.version);need(row,'planned');sealed(db,workspace,row['period_id'])
            data=plan(db,snap,workspace,body);duplicate(db,workspace,body,id)
            pupil=snap.entities['student'][body.studentId];version=row['version']+1
            before=members_of(row['data'])
            db.execute('UPDATE commission_exams SET student_id=?,class_id=?,subject_id=?,period_id=? WHERE workspace=? AND id=?',(body.studentId,pupil.classId,body.subjectId,body.periodId,workspace,id))
            save(db,row,data,'planned',version,dict(day=body.day.isoformat()))
            service.history(db,workspace,user,id,'rescheduled','',dict(before=row['data']|dict(day=row['day']),after=data|dict(day=body.day.isoformat()),version=version))
            service.notify_members(db,workspace,id,[dict(id=pupil.id,name=pupil.name)],sorted(set(before)|set(members_of(data))),version,f"Комиссионный экзамен изменён: {data['subject']}, {label(body.day.isoformat())} {body.start}")
            return staff_card(db,user,snap,workspace,id)

    @app.post(BASE+'/{id}/cancel')
    def cancel(id:str,body:Cancel,request:Request,workspace:Workspace='school'):
        with service.context(request,workspace,True) as (db,user,snap):
            admin(user);row=service.exam(db,workspace,id);check(row,body.version);need(row,'planned','held')
            data=row['data']|dict(cancelReason=body.reason.strip());version=row['version']+1
            save(db,row,data,'cancelled',version)
            service.history(db,workspace,user,id,'cancelled',body.reason.strip(),dict(version=version))
            return staff_card(db,user,snap,workspace,id)

    @app.post(BASE+'/{id}/result')
    def result(id:str,body:Result,request:Request,workspace:Workspace='school'):
        with service.context(request,workspace,True) as (db,user,snap):
            if user['role']=='admin':raise HTTPException(403,'Результат вносит комиссия.')
            row=service.staff_exam(db,user,snap,workspace,id);check(row,body.version);need(row,'planned','held');sealed(db,workspace,row['period_id'])
            if date.fromisoformat(row['day'])>today():raise conflict('not_yet','Результат вносят в день экзамена или позже.')
            scale=None
            if body.outcome=='graded':
                scale=service.scale(db,workspace,body.scaleId,body.scaleVersion)
                if body.label not in [b['label'] for b in scale['bands']]:raise HTTPException(422,'Выберите оценку из шкалы.')
            entered=dict(outcome=body.outcome,label=body.label,passed=body.passed,tasks=body.tasks.strip(),notes=body.notes.strip(),by=user['name'],at=stamp(),
                         scale=dict(id=scale['id'],version=scale['version'],name=scale['name'],bands=scale['bands']) if scale else None)
            first=not row['data'].get('result');data=row['data']|dict(result=entered);version=row['version']+1
            save(db,row,data,'held',version)
            service.history(db,workspace,user,id,'result' if first else 'result_changed','',dict(outcome=body.outcome,label=body.label,passed=body.passed,version=version))
            pupil=snap.entities['student'].get(row['student_id'])
            admins=[r[0] for r in db.execute("SELECT id FROM accounts WHERE active=1 AND role='admin'")]
            service.behaviour.notify_accounts(db,workspace,admins,version,'behaviour.commission_held',user['id'],dict(module='commission',recordId=id,students=[dict(id=row['student_id'],name=pupil.name if pupil else '')],title=f"Результат комиссии: {data['subject']}"))
            return staff_card(db,user,snap,workspace,id)

    @app.post(BASE+'/{id}/decide')
    def decide(id:str,body:Decide,request:Request,workspace:Workspace='school'):
        with service.context(request,workspace,True) as (db,user,snap):
            admin(user)
            key,previous=service.replay(db,user,body,id)
            if previous:return previous
            row=service.exam(db,workspace,id);check(row,body.version);need(row,'held');sealed(db,workspace,row['period_id'])
            if body.writeFinal and row['data']['result']['outcome']!='graded':raise HTTPException(422,'Не явившемуся итог не ставится: отметьте «без записи в итог» и укажите причину.')
            final=service.write_final(db,workspace,user,row,body) if body.writeFinal else dict(status='skipped',reason=body.reason.strip())
            data=row['data']|dict(decision=dict(decidedBy=user['name'],decidedAt=stamp(),final=final,reason=body.reason.strip(),className=service.public(row,snap,user)['className']));version=row['version']+1
            save(db,row,data,'decided',version)
            service.history(db,workspace,user,id,'decided',body.reason.strip(),dict(final=final,version=version))
            return service.remember(db,user,body,key,staff_card(db,user,snap,workspace,id)|dict(final=final))

    @app.post(BASE+'/{id}/letter')
    def letter(id:str,body:Letter,request:Request,workspace:Workspace='school'):
        with service.context(request,workspace,True) as (db,user,snap):
            admin(user);row=service.exam(db,workspace,id);check(row,body.version);need(row,'decided')
            number=service.behaviour.letter_number(db,workspace);sent=stamp();version=row['version']+1
            data=row['data']|dict(letter=dict(number=number,sentAt=sent,sentBy=user['name'],className=row['data']['decision'].get('className',service.public(row,snap,user)['className'])))
            save(db,row,data,'letter_sent',version)
            service.history(db,workspace,user,id,'letter_sent','',dict(number=number,version=version))
            pupil=snap.entities['student'].get(row['student_id']);name=pupil.name if pupil else data['student']
            users=service.notifications.granted_users(db,workspace,'student',{row['student_id']},['student','parent'])
            def target(recipient):
                kids=[s for s in [row['student_id']] if s in student_ids(recipient,workspace)]
                return dict(module='commission',recordId=id,students=[dict(id=s,name=name) for s in kids],title=f"Решение комиссии: {data['subject']}")
            service.notifications.deliver(db,users,workspace,version,'behaviour.commission_letter',topic=f'commission:{workspace}:{id}',supersede=True,target=target)
            return staff_card(db,user,snap,workspace,id)

    @app.get(BASE+'/family')
    def family(studentId:str,request:Request,workspace:Workspace='school'):
        with service.context(request,workspace) as (db,user,snap):
            if user['role'] not in ['parent','student'] or studentId not in service.behaviour.family(user,snap,workspace):raise HTTPException(404,'Ученик недоступен.')
            rows=db.execute("SELECT * FROM commission_exams WHERE workspace=? AND student_id=? AND status IN ('decided','letter_sent') ORDER BY seq DESC",(workspace,studentId)).fetchall()
            return dict(items=[service.family_view(dict(r)|dict(data=json.loads(r['document'])),snap) for r in rows])

    def facts(snap,row):
        card=service.public(row,snap,dict(role='admin'))
        return card

    @app.get(BASE+'/{id}/protocol')
    def protocol(id:str,request:Request,workspace:Workspace='school'):
        with service.context(request,workspace) as (db,user,snap):
            row=service.staff_exam(db,user,snap,workspace,id)
            if row['status']=='cancelled':raise conflict('status','Экзамен отменён.')
            card=service.public(row,snap,user)
            keys=['kindTitle','reason','student','className','subject','period','day','start','end','room','chairman','examiner','members']
            return {k:card[k] for k in keys}|dict(className=(row['data'].get('decision') or {}).get('className',row['data'].get('className',card['className'])),school=snap.state.settings.name,result=card['result'])

    @app.get(BASE+'/{id}/letter')
    def letter_text(id:str,request:Request,workspace:Workspace='school'):
        with service.context(request,workspace) as (db,user,snap):
            row=service.family_exam(db,user,snap,workspace,id) if user['role'] in ['parent','student'] else service.staff_exam(db,user,snap,workspace,id)
            if row['status']!='letter_sent':raise conflict('status','Письмо ещё не отправлено.')
            data=row['data'];card=service.public(row,snap,dict(role='admin'))
            return dict(school=snap.state.settings.name,number=data['letter']['number'],sentAt=data['letter']['sentAt'],student=card['student'],className=data['letter'].get('className',card['className']),subject=card['subject'],
                        period=card['period'],kindTitle=card['kindTitle'],day=row['day'],outcome=data['result']['outcome'],label=data['result']['label'],passed=data['result']['passed'],
                        chairman=card['chairman']['name'],decidedBy=data['decision']['decidedBy'])

    @app.get(BASE+'/{id}/history')
    def history(id:str,request:Request,workspace:Workspace='school'):
        with service.context(request,workspace) as (db,user,snap):
            service.staff_exam(db,user,snap,workspace,id)
            rows=db.execute('SELECT seq,actor_name,created,action,reason,document FROM commission_exam_history WHERE workspace=? AND exam_id=? ORDER BY seq',(workspace,id)).fetchall()
            return dict(items=[dict(seq=r['seq'],actor=r['actor_name'],created=r['created'],action=r['action'],reason=r['reason'],data=json.loads(r['document'])) for r in rows])
