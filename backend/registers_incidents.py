import json
from datetime import timedelta
from uuid import uuid4
from fastapi import HTTPException,Query,Request
from .notifications import encoded
from .registers import iso,local_day,version
from .registers_models import IncidentAction,IncidentData,IncidentWrite
from .school_forms import enabled,grant_ids

DATA=list(IncidentData.model_fields)

def register(s,app,Workspace):
    def stored(db,workspace,id):
        r=db.execute('SELECT * FROM registers_incidents WHERE workspace=? AND id=?',(workspace,id)).fetchone()
        if not r:raise HTTPException(404,'Запись о происшествии не найдена.')
        return r

    def short(row):
        doc=json.loads(row['document'])
        return dict(id=row['id'],number=row['number'],status=row['status'],kind=row['kind'],severity=doc['severity'],occurredAt=doc['occurredAt'],place=doc['place'],studentCount=len(doc['students']),summaryPublished=bool(row['summary_published']))

    def detail(db,snap,workspace,row,admin,cfg):
        doc=json.loads(row['document']);pupils=snap.entities['student'];classes=snap.entities['class_'];rooms=snap.entities['room'];students=[]
        for p in doc['students']:
            now=pupils.get(p['id']);live=bool(now and now.active);cls=classes.get(now.classId if now else p['classId'])
            students.append(dict(id=p['id'],name=now.name if live else p['name'],className=cls.name if cls else '',current=live))
        staff=[]
        for p in doc['staff']:
            now=s.staff_person(db,snap,workspace,p['id']);staff.append(dict(id=p['id'],name=now['name'] if now else p['name'],current=bool(now)))
        return dict(id=row['id'],number=row['number'],version=row['version'],status=row['status'],created=row['created'],updated=row['updated'],author=row['author_name'],closedAt=doc['closedAt'],closedBy=doc['closedBy'],summaryPublished=bool(row['summary_published']),publishedAt=doc['publishedAt'],canReopen=admin and row['status']=='closed',canPublish=admin and cfg['familySummaries'],data={k:doc[k] for k in DATA},students=students,staff=staff,roomName=doc['roomName'],roomCurrent=bool(doc['roomId'] and doc['roomId'] in rooms))

    def link(db,snap,workspace,data,previous):
        """Validate the people, room and moments of a record and freeze their names with it."""
        prev=previous or {};current=s.now();at=data.occurredAt;pupils=snap.entities['student']
        if at>current+timedelta(minutes=2) or (at<current-timedelta(days=366) and iso(at)!=prev.get('occurredAt')):raise HTTPException(422,'Дата происшествия — не в будущем и не раньше года назад.')
        if any(n.at<at-timedelta(hours=1) or n.at>current+timedelta(minutes=2) for n in data.parentNotices):raise HTTPException(422,'Время сообщения родителям не может быть в будущем или намного раньше происшествия.')
        kept={p['id']:p for p in prev.get('students',[])};students=[]
        for id in data.studentIds:
            p=pupils.get(id)
            if p and p.active:students.append(dict(id=p.id,name=p.name,classId=p.classId))
            elif id in kept:students.append(kept[id])
            else:raise HTTPException(422,'Выберите действующих учеников школы.')
        kept={p['id']:p for p in prev.get('staff',[])};staff=[]
        for id in data.staffIds:
            p=s.staff_person(db,snap,workspace,id)
            if p:staff.append(p)
            elif id in kept:staff.append(kept[id])
            else:raise HTTPException(422,'Выберите действующих сотрудников школы.')
        room=''
        if data.roomId:
            found=snap.entities['room'].get(data.roomId)
            if found:room=found.name
            elif data.roomId==prev.get('roomId'):room=prev.get('roomName','')
            else:raise HTTPException(422,'Кабинет не найден в расписании.')
        doc=data.model_dump(mode='json');doc['occurredAt']=iso(at)
        for raw,notice in zip(doc['parentNotices'],data.parentNotices):raw['at']=iso(notice.at)
        keep={k:prev.get(k,'') for k in ['closedAt','closedBy','publishedAt','publishedBy']}
        return doc|dict(students=students,staff=staff,roomName=room)|keep

    def people(db,id,workspace,doc):
        db.execute('DELETE FROM registers_incident_people WHERE incident_id=?',(id,))
        for kind,rows in [('student',doc['students']),('staff',doc['staff'])]:
            db.executemany('INSERT INTO registers_incident_people(incident_id,workspace,kind,person_id) VALUES (?,?,?,?)',[(id,workspace,kind,p['id']) for p in rows])

    def search(number,doc):return ' '.join([number,doc['place'],doc['description']]).casefold()

    @app.get('/api/registers/incidents')
    def incidents(request:Request,workspace:Workspace='school',status:str=Query(default='',pattern='^(open|closed)?$'),year:str=Query(default='',pattern=r'^(\d{4})?$'),q:str=Query(default='',max_length=200),studentId:str=Query(default='',max_length=100),before:int=Query(default=9223372036854775807,ge=1),limit:int=Query(default=30,ge=1,le=100)):
        with s.context(request,workspace) as (db,u,snap):
            s.require(db,u,snap,workspace,'incidents')
            rows=db.execute("SELECT * FROM registers_incidents WHERE workspace=? AND (?='' OR status=?) AND (?='' OR occurred_on LIKE ?||'-%') AND seq<? AND instr(search,?)>0 AND (?='' OR id IN (SELECT incident_id FROM registers_incident_people WHERE workspace=? AND kind='student' AND person_id=?)) ORDER BY seq DESC LIMIT ?",(workspace,status,status,year,year,before,q.strip().casefold(),studentId,workspace,studentId,limit+1)).fetchall()
            items,nxt=s.page(rows,limit)
            return dict(items=[short(r) for r in items],nextBefore=nxt)

    @app.get('/api/registers/incidents/{id}')
    def incident(id:str,request:Request,workspace:Workspace='school'):
        with s.context(request,workspace) as (db,u,snap):
            s.require(db,u,snap,workspace,'incidents')
            return detail(db,snap,workspace,stored(db,workspace,id),u['role']=='admin',s.config(db,workspace))

    def save(body,request,workspace,id):
        with s.context(request,workspace,True) as (db,u,snap):
            s.require(db,u,snap,workspace,'incidents');key,old=s.replay(db,u,body,'incident:'+workspace+':'+(id or 'new'))
            if old:return old
            previous=stored(db,workspace,id) if id else None
            if previous:
                version(previous,body.version)
                if previous['status']=='closed':raise HTTPException(409,'Закрытую запись сначала откройте повторно.')
                if len(body.reason)<3:raise HTTPException(422,'Укажите причину изменения.')
                before=json.loads(previous['document'])
                if previous['summary_published'] and body.data.familySummary!=before['familySummary']:raise HTTPException(409,'Сначала снимите сводку для семьи с публикации.')
            elif body.version!=0:raise HTTPException(422,'Новая запись создаётся с нулевой версией.')
            else:before=None
            doc=link(db,snap,workspace,body.data,before);stamped=s.now().isoformat()
            if previous:
                id=previous['id'];db.execute('UPDATE registers_incidents SET version=version+1,kind=?,occurred_on=?,updated=?,search=?,document=? WHERE id=?',(doc['kind'],local_day(body.data.occurredAt),stamped,search(previous['number'],doc),encoded(doc),id))
                action='edited'
            else:
                id=uuid4().hex;number=s.counter(db,workspace,'incident')
                db.execute("INSERT INTO registers_incidents(id,workspace,number,version,status,kind,occurred_on,author_id,author_name,created,updated,search,document) VALUES (?,?,?,1,'open',?,?,?,?,?,?,?,?)",(id,workspace,number,doc['kind'],local_day(body.data.occurredAt),u['id'],u['name'],stamped,stamped,search(number,doc),encoded(doc)))
                action='created'
            people(db,id,workspace,doc);s.history(db,workspace,'incidents',id,u,action,body.reason,dict(before=before,after=doc))
            if action=='created':s.notify_capable(db,u,snap,workspace,'incidents',1,'registers.incident')
            return s.remember(db,u,body,key,detail(db,snap,workspace,stored(db,workspace,id),u['role']=='admin',s.config(db,workspace)))

    @app.post('/api/registers/incidents')
    def create(body:IncidentWrite,request:Request,workspace:Workspace='school'):return save(body,request,workspace,None)
    @app.put('/api/registers/incidents/{id}')
    def edit(id:str,body:IncidentWrite,request:Request,workspace:Workspace='school'):return save(body,request,workspace,id)

    @app.post('/api/registers/incidents/{id}/action')
    def action(id:str,body:IncidentAction,request:Request,workspace:Workspace='school'):
        with s.context(request,workspace,True) as (db,u,snap):
            s.require(db,u,snap,workspace,'incidents');key,old=s.replay(db,u,body,'incident-action:'+workspace+':'+id)
            if old:return old
            row=stored(db,workspace,id);version(row,body.version);doc=json.loads(row['document']);cfg=s.config(db,workspace);stamped=s.now().isoformat();status=row['status'];published=row['summary_published'];admin=u['role']=='admin'
            if body.action=='close':
                if status!='open':raise HTTPException(409,'Закрыть можно только открытую запись.')
                if any(not any(n['studentId']==p for n in doc['parentNotices']) for p in doc['studentIds']):raise HTTPException(422,'Укажите, кто и когда сообщил родителям каждого ученика.')
                status='closed';doc|=dict(closedAt=stamped,closedBy=u['name'])
            elif body.action=='reopen':
                if not admin:raise HTTPException(403,'Открыть запись повторно может администрация.')
                if status!='closed':raise HTTPException(409,'Запись не закрыта.')
                status='open';doc|=dict(closedAt='',closedBy='')
            elif not admin:raise HTTPException(403,'Сводки для семей публикует администрация.')
            elif body.action=='publish_summary':
                if not cfg['familySummaries']:raise HTTPException(409,'Школа не публикует сведения о происшествиях семьям. Включите это в настройках журналов.')
                if published:raise HTTPException(409,'Сводка уже опубликована.')
                if not doc['familySummary'].strip() or not doc['studentIds']:raise HTTPException(422,'Для публикации нужны текст сводки и хотя бы один ученик.')
                published=1;doc|=dict(publishedAt=stamped,publishedBy=u['name'])
            else:
                if not published:raise HTTPException(409,'Сводка не опубликована.')
                published=0
            db.execute('UPDATE registers_incidents SET version=version+1,status=?,summary_published=?,updated=?,document=? WHERE id=?',(status,published,stamped,encoded(doc),id))
            s.history(db,workspace,'incidents',id,u,body.action,body.reason,dict(before=row['status'],after=status,summaryPublished=bool(published)))
            if body.action=='publish_summary':
                pupils=snap.entities['student'];ids=[i for i in doc['studentIds'] if i in pupils and pupils[i].active]
                s.notifications.deliver(db,[t for t in s.notifications.granted_users(db,workspace,'student',ids,['parent']) if enabled(t,snap,workspace)],workspace,row['version']+1,'registers.summary')
            return s.remember(db,u,body,key,detail(db,snap,workspace,stored(db,workspace,id),admin,cfg))

    @app.get('/api/registers/family/incidents')
    def family(request:Request,workspace:Workspace='school'):
        with s.context(request,workspace) as (db,u,snap):
            if u['role']!='parent':raise HTTPException(403,'Сводки о происшествиях доступны родителям.')
            cfg=s.config(db,workspace)
            if not cfg['familySummaries']:return dict(items=[],enabled=False)
            pupils=snap.entities['student'];ids=sorted(i for i in grant_ids(u,workspace,'student') if i in pupils and pupils[i].active)
            rows=db.execute("SELECT i.id,i.number,i.occurred_on,i.kind,i.document,p.person_id FROM registers_incidents i JOIN registers_incident_people p ON p.incident_id=i.id AND p.kind='student' WHERE i.workspace=? AND i.summary_published=1 AND p.person_id IN (SELECT value FROM json_each(?)) ORDER BY i.seq DESC,p.person_id LIMIT 500",(workspace,encoded(ids))).fetchall()
            found={}
            for r in rows:
                doc=json.loads(r['document']);item=found.setdefault(r['id'],dict(number=r['number'],occurredOn=r['occurred_on'],kind=r['kind'],summary=doc['familySummary'],publishedAt=doc['publishedAt'],children=[]))
                item['children'].append(pupils[r['person_id']].name)
            return dict(items=list(found.values()),enabled=True)
