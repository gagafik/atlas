"""Register of safety briefings with attendance, and the list of people who have not had a primary or repeated one."""
from datetime import date
from typing import Literal
from uuid import uuid4
import json

from fastapi import HTTPException,Query,Request
from pydantic import Field,model_validator
from .models import Model
from .notifications import encoded
from .safety import ZONE
from . import school_years

KINDS=[('introductory','Вводный'),('primary','Первичный'),('repeated','Повторный'),('unscheduled','Внеплановый'),('targeted','Целевой')]
# Only these two kinds meet the requirement that every person is briefed again in the period.
COUNTED=('primary','repeated')
Kind=Literal['introductory','primary','repeated','unscheduled','targeted']
MAX_SPAN_DAYS=366


class Mutation(Model):
    requestId:str=Field(min_length=8,max_length=80)


class BriefingData(Model):
    kind:Kind
    heldOn:date
    topic:str=Field(min_length=1,max_length=200)
    conductorTeacherId:str|None=Field(default=None,max_length=100)
    conductorName:str=Field(default='',max_length=160)
    note:str=Field(default='',max_length=2000)
    absentIds:list[str]=Field(default_factory=list,max_length=400)
    @model_validator(mode='after')
    def valid(self):
        if not self.conductorTeacherId and not self.conductorName:raise ValueError('Укажите, кто провёл инструктаж.')
        if len(set(self.absentIds))!=len(self.absentIds):raise ValueError('Отсутствующие не должны повторяться.')
        return self


class NewBriefing(Mutation,BriefingData):
    audience:Literal['class','staff']
    classId:str|None=Field(default=None,max_length=100)
    staffLabel:str=Field(default='',max_length=120)
    teacherIds:list[str]=Field(default_factory=list,max_length=300)
    @model_validator(mode='after')
    def audience_valid(self):
        if self.audience=='class' and (not self.classId or self.teacherIds):raise ValueError('Выберите класс.')
        if self.audience=='staff' and (not 1<=len(self.teacherIds) or not self.staffLabel or len(set(self.teacherIds))!=len(self.teacherIds)):raise ValueError('Выберите сотрудников и назовите группу.')
        return self


class BriefingEdit(Mutation,BriefingData):
    version:int=Field(ge=1)


class Withdraw(Mutation):
    version:int=Field(ge=1)
    reason:str=Field(min_length=3,max_length=500)


def span(start,end):
    if start and end and (end<start or (end-start).days>MAX_SPAN_DAYS):raise HTTPException(422,'Выберите период не длиннее года.')


def register(s,app,Workspace):
    def roster(snap,classId,day):
        view,_=snap.history.context(day)
        return sorted(((p.id,p.name) for p in view.students if p.classId==classId and p.active),key=lambda x:x[1])
    def stored(db,workspace,id):
        r=db.execute('SELECT * FROM safety_briefings WHERE workspace=? AND id=?',(workspace,id)).fetchone()
        if not r:raise HTTPException(404,'Инструктаж не найден.')
        return r
    def counts(db,ids):
        found={i:[0,0] for i in ids}
        for i,present,total in db.execute('SELECT briefing_id,sum(present),count(*) FROM safety_briefing_people WHERE briefing_id IN (SELECT value FROM json_each(?)) GROUP BY briefing_id',(encoded(list(ids)),)):found[i]=[present,total-present]
        return found
    def short(row,count):
        doc=json.loads(row['document'])
        return dict(id=row['id'],version=row['version'],kind=row['kind'],heldOn=row['held_on'],topic=doc['topic'],audience=row['audience'],classId=row['class_id'] or None,
                    audienceLabel=doc['audienceLabel'],conductor=doc['conductor']['name'],present=count[0],absent=count[1])
    def detail(db,workspace,row):
        doc=json.loads(row['document'])
        people=[dict(id=p['person_id'],kind=p['kind'],name=p['name'],className=p['class_name'],present=bool(p['present'])) for p in db.execute('SELECT * FROM safety_briefing_people WHERE briefing_id=?',(row['id'],))]
        people.sort(key=lambda p:(p['className'],p['name']))
        history=[dict(action=h['action'],actor=h['actor_name'],created=h['created'],reason=h['reason']) for h in db.execute('SELECT * FROM safety_history WHERE workspace=? AND briefing_id=? ORDER BY seq DESC LIMIT 20',(workspace,row['id']))]
        count=[sum(p['present'] for p in people),sum(not p['present'] for p in people)]
        return short(row,count)|dict(status=row['status'],note=doc['note'],conductorTeacherId=doc['conductor']['teacherId'],people=people,history=history)
    def checked(db,workspace,snap,data):
        # Not in the future and after the archived years; before the first rollover also not before the year start. Summer briefings between two years are recordable.
        today=s.today();state=snap.state
        if data.heldOn>today or (not school_years.archived(db,workspace) and data.heldOn<state.settings.yearStart):raise HTTPException(422,'Дата инструктажа — не в будущем и не раньше начала учебного года.')
        school_years.require_current(db,workspace,data.heldOn.isoformat())
        if data.conductorTeacherId:
            found=snap.entities['teacher'].get(data.conductorTeacherId)
            if not found:raise HTTPException(422,'Преподаватель не найден в расписании.')
            return dict(teacherId=found.id,name=found.name)
        return dict(teacherId=None,name=data.conductorName)

    @app.get('/api/safety/options')
    def options(request:Request,workspace:Workspace='school'):
        with s.context(request,workspace) as (db,u,snap):
            s.admin(u);state=snap.state;local=s.now().astimezone(ZONE)
            return dict(today=s.today().isoformat(),now=local.strftime('%Y-%m-%dT%H:%M'),yearStart=state.settings.yearStart.isoformat(),yearEnd=state.settings.yearEnd.isoformat(),school=state.settings.name,
                        kinds=[dict(id=k,label=l) for k,l in KINDS],classes=[dict(id=c.id,name=c.name) for c in state.classes],
                        teachers=sorted((dict(id=t.id,name=t.name) for t in state.teachers),key=lambda t:t['name']))

    @app.get('/api/safety/roster')
    def pupils(request:Request,classId:str,day:date,workspace:Workspace='school'):
        with s.context(request,workspace) as (db,u,snap):
            s.admin(u)
            if classId not in snap.entities['class_']:raise HTTPException(404,'Класс не найден.')
            return [dict(id=i,name=n) for i,n in roster(snap,classId,day)]

    @app.post('/api/safety/briefings')
    def create(body:NewBriefing,request:Request,workspace:Workspace='school'):
        with s.context(request,workspace,True) as (db,u,snap):
            s.admin(u);key,old=s.replay(db,u,body,'create:'+workspace)
            if old:return old
            conductor=checked(db,workspace,snap,body);people=[]
            if body.audience=='class':
                found=snap.entities['class_'].get(body.classId)
                if not found:raise HTTPException(422,'Класс не найден.')
                people=[('student',i,n,found.name) for i,n in roster(snap,body.classId,body.heldOn)]
                if not people:raise HTTPException(422,'В классе на эту дату нет учеников.')
                label=found.name
            else:
                teachers=snap.entities['teacher']
                if any(i not in teachers for i in body.teacherIds):raise HTTPException(422,'Выберите преподавателей из расписания.')
                people=[('teacher',i,teachers[i].name,'') for i in body.teacherIds];label=body.staffLabel
            if set(body.absentIds)-{p[1] for p in people}:raise HTTPException(422,'Отсутствующие должны быть среди участников.')
            id=str(uuid4());stamp=s.now().isoformat()
            doc=dict(topic=body.topic,conductor=conductor,note=body.note,audienceLabel=label)
            db.execute('INSERT INTO safety_briefings(id,workspace,version,status,kind,held_on,audience,class_id,author_id,author_name,created,updated,document) VALUES (?,?,1,?,?,?,?,?,?,?,?,?,?)',
                       (id,workspace,'active',body.kind,body.heldOn.isoformat(),body.audience,body.classId or '',u['id'],u['name'],stamp,stamp,encoded(doc)))
            db.executemany('INSERT INTO safety_briefing_people(briefing_id,workspace,kind,person_id,name,class_name,present) VALUES (?,?,?,?,?,?,?)',[(id,workspace,k,i,n,c,int(i not in body.absentIds)) for k,i,n,c in people])
            s.history(db,workspace,id,u,'created','',dict(kind=body.kind,heldOn=body.heldOn.isoformat(),topic=body.topic,absentIds=body.absentIds))
            s.access.record(db,'safety.briefing_created',workspace,detail=id,principal=u)
            return s.remember(db,u,body,key,detail(db,workspace,stored(db,workspace,id)))

    @app.get('/api/safety/briefings')
    def listing(request:Request,workspace:Workspace='school',start:date|None=None,end:date|None=None,kind:Kind|None=None,classId:str|None=None,before:int|None=None,limit:int=Query(default=20,ge=1,le=50)):
        span(start,end)
        with s.context(request,workspace) as (db,u,snap):
            s.admin(u)
            where=["workspace=?","status='active'"];params=[workspace]
            for clause,value in [('held_on>=?',start),('held_on<=?',end),('kind=?',kind),('class_id=?',classId)]:
                if value:where.append(clause);params.append(value.isoformat() if isinstance(value,date) else value)
            if before is not None:
                mark=db.execute('SELECT held_on FROM safety_briefings WHERE workspace=? AND seq=?',(workspace,before)).fetchone()
                if mark:where.append('(held_on<? OR held_on=? AND seq<?)');params+=[mark['held_on'],mark['held_on'],before]
            rows=db.execute(f"SELECT * FROM safety_briefings WHERE {' AND '.join(where)} ORDER BY held_on DESC,seq DESC LIMIT ?",(*params,limit+1)).fetchall()
            page=rows[:limit];count=counts(db,[r['id'] for r in page])
            return dict(items=[short(r,count[r['id']]) for r in page],nextBefore=page[-1]['seq'] if len(rows)>limit else None)

    @app.get('/api/safety/briefings/{id}')
    def one(id:str,request:Request,workspace:Workspace='school'):
        with s.context(request,workspace) as (db,u,snap):
            s.admin(u)
            return detail(db,workspace,stored(db,workspace,id))

    @app.put('/api/safety/briefings/{id}')
    def edit(id:str,body:BriefingEdit,request:Request,workspace:Workspace='school'):
        with s.context(request,workspace,True) as (db,u,snap):
            s.admin(u);key,old=s.replay(db,u,body,'edit:'+workspace+':'+id)
            if old:return old
            row=stored(db,workspace,id)
            if row['version']!=body.version or row['status']!='active':raise HTTPException(409,'Запись изменилась. Обновите данные перед действием.')
            school_years.require_current(db,workspace,row['held_on'])
            conductor=checked(db,workspace,snap,body)
            members={r['person_id']:r['present'] for r in db.execute('SELECT person_id,present FROM safety_briefing_people WHERE briefing_id=?',(id,))}
            if set(body.absentIds)-members.keys():raise HTTPException(422,'Отсутствующие должны быть среди участников.')
            doc=json.loads(row['document']);before=dict(kind=row['kind'],heldOn=row['held_on'],topic=doc['topic'],absentIds=sorted(i for i,p in members.items() if not p))
            doc|=dict(topic=body.topic,conductor=conductor,note=body.note)
            db.execute('UPDATE safety_briefings SET version=version+1,kind=?,held_on=?,updated=?,document=? WHERE workspace=? AND id=?',(body.kind,body.heldOn.isoformat(),s.now().isoformat(),encoded(doc),workspace,id))
            db.execute('UPDATE safety_briefing_people SET present=1-(person_id IN (SELECT value FROM json_each(?))) WHERE briefing_id=?',(encoded(body.absentIds),id))
            s.history(db,workspace,id,u,'updated','',dict(before=before,after=dict(kind=body.kind,heldOn=body.heldOn.isoformat(),topic=body.topic,absentIds=sorted(body.absentIds))))
            s.access.record(db,'safety.briefing_updated',workspace,detail=id,principal=u)
            return s.remember(db,u,body,key,detail(db,workspace,stored(db,workspace,id)))

    @app.post('/api/safety/briefings/{id}/withdraw')
    def withdraw(id:str,body:Withdraw,request:Request,workspace:Workspace='school'):
        with s.context(request,workspace,True) as (db,u,snap):
            s.admin(u);key,old=s.replay(db,u,body,'withdraw:'+workspace+':'+id)
            if old:return old
            row=stored(db,workspace,id)
            if row['version']!=body.version or row['status']!='active':raise HTTPException(409,'Запись изменилась. Обновите данные перед действием.')
            school_years.require_current(db,workspace,row['held_on'])
            db.execute("UPDATE safety_briefings SET version=version+1,status='withdrawn',updated=? WHERE workspace=? AND id=?",(s.now().isoformat(),workspace,id))
            s.history(db,workspace,id,u,'withdrawn',body.reason,{})
            s.access.record(db,'safety.briefing_withdrawn',workspace,detail=id,principal=u)
            return s.remember(db,u,body,key,detail(db,workspace,stored(db,workspace,id)))

    @app.get('/api/safety/overdue')
    def overdue(request:Request,audience:Literal['pupils','staff'],start:date,end:date,workspace:Workspace='school',classId:str|None=None):
        span(start,end)
        with s.context(request,workspace) as (db,u,snap):
            s.admin(u)
            if audience=='pupils':
                view,_=snap.history.context(end);classes={c.id:c.name for c in view.classes}
                people=[(p.id,p.name,p.classId or '',classes.get(p.classId,'')) for p in view.students if p.active and (not classId or p.classId==classId)]
            else:people=[(t.id,t.name,'','') for t in snap.state.teachers]
            last=dict(db.execute(f"SELECT p.person_id,max(b.held_on) FROM safety_briefing_people p JOIN safety_briefings b ON b.id=p.briefing_id WHERE p.workspace=? AND p.kind=? AND p.present=1 AND b.status='active' AND b.kind IN ({','.join('?'*len(COUNTED))}) AND b.held_on<=? GROUP BY p.person_id",
                                  (workspace,'student' if audience=='pupils' else 'teacher',*COUNTED,end.isoformat())).fetchall())
            found=[dict(id=i,name=n,classId=c,className=cn,last=last.get(i)) for i,n,c,cn in people if last.get(i) is None or last[i]<start.isoformat()]
            found.sort(key=lambda p:(p['className'],p['name']))
            return dict(start=start.isoformat(),end=end.isoformat(),people=found)
