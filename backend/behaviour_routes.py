"""HTTP routes for praise and remarks; every request rechecks the current scope."""
from datetime import date,timedelta
import json

from fastapi import HTTPException,Query,Request
from .behaviour import Config,NewNotes,Edit,Withdraw,Acknowledge,KINDS,PAGE,stamp,today,new_id,export_rows,window
from .notifications import encoded


def register(service,app,Workspace):
    access=service.access

    def staff(db,user,snap,workspace):
        allowed=service.scope(db,user,snap,workspace)
        if user['role']!='admin' and not allowed:raise HTTPException(403,'Раздел доступен учителям учеников и назначенным классным руководителям.')
        return allowed

    def require_change(user,row,version):
        if not service.may_change(user,row):raise HTTPException(403,'Изменить запись может её автор или администратор.')
        if row['version']!=version:raise HTTPException(409,'Запись изменилась. Обновите список.')
        if row['status']!='published':raise HTTPException(409,'Запись уже отозвана.')

    @app.get('/api/behaviour/options')
    def options(request:Request,workspace:Workspace='school'):
        with service.context(request,workspace) as (db,user,snap):
            cfg=service.config(db,workspace);allowed=service.scope(db,user,snap,workspace);own=service.family(user,snap,workspace)
            classes={c.id:c.name for c in snap.state.classes}
            pupils=sorted((snap.entities['student'][id] for id in allowed|own),key=lambda s:(classes.get(s.classId,''),s.name))
            return dict(admin=user['role']=='admin',canWrite=bool(allowed),family=bool(own) and not allowed,canAcknowledge=user['role']=='parent',
                        today=today().isoformat(),kinds=KINDS,remarkAck=cfg['remarkAck'],categories=cfg['categories'],
                        configVersion=cfg['version'] if user['role']=='admin' else None,
                        classes=[dict(id=id,name=name) for id,name in classes.items() if any(s.classId==id for s in pupils)],
                        students=[dict(id=s.id,name=s.name,classId=s.classId,className=classes.get(s.classId,'')) for s in pupils])

    @app.put('/api/behaviour/config')
    def configure(body:Config,request:Request,workspace:Workspace='school'):
        with service.context(request,workspace,True) as (db,user,snap):
            if user['role']!='admin':raise HTTPException(403,'Категории настраивает администратор.')
            current=service.config(db,workspace)
            if current['version']!=body.version:raise HTTPException(409,'Настройки изменились. Обновите страницу.')
            value=dict(categories=[c.model_dump() for c in body.categories],remarkAck=body.remarkAck);version=current['version']+1
            db.execute('INSERT INTO behaviour_config VALUES (?,?,?) ON CONFLICT(workspace) DO UPDATE SET version=excluded.version,document=excluded.document',(workspace,version,encoded(value)))
            service.history(db,workspace,user,'','configured',body.reason,dict(before={k:current[k] for k in ['categories','remarkAck']},after=value))
            access.record(db,'behaviour.configured',workspace,detail=f'Редакция {version} · {body.reason}')
            return dict(version=version,**value)

    @app.post('/api/behaviour/notes')
    def create(body:NewNotes,request:Request,workspace:Workspace='school'):
        with service.context(request,workspace,True) as (db,user,snap):
            key,previous=service.replay(db,user,body)
            if previous:return previous
            allowed=staff(db,user,snap,workspace)
            if not set(body.studentIds)<=allowed:raise HTTPException(403,'Можно отметить только учеников, которых вы сейчас учите или курируете.')
            if body.day>today() or body.day<today()-timedelta(days=366):raise HTTPException(422,'Дата записи — не позже сегодняшнего дня и не раньше года назад.')
            category=service.category(db,workspace,body.categoryId);cfg=service.config(db,workspace)
            require=category['kind']=='remark' and cfg['remarkAck'];batch=new_id();created=stamp();ids=[]
            for student in body.studentIds:
                id=new_id();ids.append(id);pupil=snap.entities['student'][student]
                data=dict(category=category['title'],text=body.text.strip(),requireAck=require)
                db.execute('''INSERT INTO behaviour_notes(id,batch,workspace,student_id,class_id,version,status,kind,category_id,day,author_id,author_name,created,updated,document)
                              VALUES (?,?,?,?,?,1,'published',?,?,?,?,?,?,?,?)''',(id,batch,workspace,student,pupil.classId,category['kind'],category['id'],body.day.isoformat(),user['id'],user['name'],created,created,encoded(data)))
                service.history(db,workspace,user,id,'created','',data)
            service.notify_family(db,workspace,body.studentIds,1,'behaviour.published')
            return service.remember(db,user,body,key,dict(batch=batch,ids=ids))

    @app.get('/api/behaviour/notes')
    def notes(request:Request,workspace:Workspace='school',classId:str|None=None,studentId:str|None=None,kind:str|None=Query(default=None,pattern='^(praise|remark)$'),
              status:str|None=Query(default=None,pattern='^(published|withdrawn)$'),start:date|None=None,end:date|None=None,before:int|None=Query(default=None,ge=1)):
        window(start,end)
        with service.context(request,workspace) as (db,user,snap):
            staff(db,user,snap,workspace)
            clause,params=service.filtered(db,user,snap,workspace,classId,studentId,kind,start,end,status)
            if before:clause+=' AND seq<?';params.append(before)
            rows=db.execute(f'SELECT * FROM behaviour_notes WHERE {clause} ORDER BY day DESC,seq DESC LIMIT {PAGE+1}',params).fetchall()
            items=[service.public(dict(r)|dict(data=json.loads(r['document'])),snap,True,user) for r in rows[:PAGE]]
            return dict(items=items,nextBefore=rows[PAGE-1]['seq'] if len(rows)>PAGE else None)

    @app.get('/api/behaviour/summary')
    def summary(request:Request,workspace:Workspace='school',classId:str|None=None,start:date|None=None,end:date|None=None):
        window(start,end)
        with service.context(request,workspace) as (db,user,snap):
            staff(db,user,snap,workspace)
            clause,params=service.filtered(db,user,snap,workspace,classId,None,None,start,end,'published')
            counts={}
            for student,kind,count in db.execute(f'SELECT student_id,kind,count(*) FROM behaviour_notes WHERE {clause} GROUP BY student_id,kind',params):
                counts.setdefault(student,dict(praise=0,remark=0))[kind]=count
            classes={c.id:c.name for c in snap.state.classes}
            rows=[dict(studentId=id,student=snap.entities['student'][id].name,className=classes.get(snap.entities['student'][id].classId,''),**value)
                  for id,value in counts.items() if id in snap.entities['student']]
            return dict(items=sorted(rows,key=lambda r:(r['className'],r['student'])))

    @app.get('/api/behaviour/export')
    def export(request:Request,workspace:Workspace='school',classId:str|None=None,studentId:str|None=None,kind:str|None=Query(default=None,pattern='^(praise|remark)$'),
               start:date|None=None,end:date|None=None):
        window(start,end)
        with service.context(request,workspace) as (db,user,snap):
            staff(db,user,snap,workspace)
            clause,params=service.filtered(db,user,snap,workspace,classId,studentId,kind,start,end,None)
            rows=db.execute(f'SELECT * FROM behaviour_notes WHERE {clause} ORDER BY day,seq LIMIT 20001',params).fetchall()
            if len(rows)>20000:raise HTTPException(413,'Слишком много записей. Сократите период или выберите класс.')
            return export_rows(rows,snap)

    @app.get('/api/behaviour/family')
    def family(request:Request,studentId:str,workspace:Workspace='school',before:int|None=Query(default=None,ge=1)):
        with service.context(request,workspace) as (db,user,snap):
            if studentId not in service.family(user,snap,workspace):raise HTTPException(404,'Ученик недоступен.')
            clause,params="workspace=? AND student_id=? AND status='published'",[workspace,studentId]
            totals={k:v for k,v in db.execute(f'SELECT kind,count(*) FROM behaviour_notes WHERE {clause} GROUP BY kind',params)}
            if before:clause+=' AND seq<?';params.append(before)
            rows=db.execute(f'SELECT * FROM behaviour_notes WHERE {clause} ORDER BY day DESC,seq DESC LIMIT {PAGE+1}',params).fetchall()
            return dict(items=[service.public(dict(r)|dict(data=json.loads(r['document'])),snap) for r in rows[:PAGE]],
                        nextBefore=rows[PAGE-1]['seq'] if len(rows)>PAGE else None,praise=totals.get('praise',0),remark=totals.get('remark',0))

    @app.post('/api/behaviour/notes/{id}/edit')
    def edit(id:str,body:Edit,request:Request,workspace:Workspace='school'):
        with service.context(request,workspace,True) as (db,user,snap):
            row=service.staff_note(db,user,snap,workspace,id);require_change(user,row,body.version)
            if row['ack_at']:raise HTTPException(409,'Родитель уже ознакомился с записью. Отзовите её и создайте новую.')
            category=service.category(db,workspace,body.categoryId)
            data=row['data']|dict(category=category['title'],text=body.text.strip(),requireAck=category['kind']=='remark' and service.config(db,workspace)['remarkAck'])
            db.execute('UPDATE behaviour_notes SET version=version+1,updated=?,kind=?,category_id=?,document=? WHERE id=?',(stamp(),category['kind'],category['id'],encoded(data),id))
            service.history(db,workspace,user,id,'edited',body.reason,dict(before=row['data'],after=data))
            service.notify_family(db,workspace,[row['student_id']],row['version']+1,'behaviour.changed')
            return service.public(service.note(db,workspace,id),snap,True,user)

    @app.post('/api/behaviour/notes/{id}/withdraw')
    def withdraw(id:str,body:Withdraw,request:Request,workspace:Workspace='school'):
        with service.context(request,workspace,True) as (db,user,snap):
            row=service.staff_note(db,user,snap,workspace,id);require_change(user,row,body.version)
            data=row['data']|dict(withdrawReason=body.reason)
            db.execute("UPDATE behaviour_notes SET version=version+1,updated=?,status='withdrawn',document=? WHERE id=?",(stamp(),encoded(data),id))
            service.history(db,workspace,user,id,'withdrawn',body.reason,data)
            service.notify_family(db,workspace,[row['student_id']],row['version']+1,'behaviour.withdrawn')
            return service.public(service.note(db,workspace,id),snap,True,user)

    @app.post('/api/behaviour/notes/{id}/acknowledge')
    def acknowledge(id:str,body:Acknowledge,request:Request,workspace:Workspace='school'):
        with service.context(request,workspace,True) as (db,user,snap):
            row=service.note(db,workspace,id)
            if user['role']!='parent' or row['student_id'] not in service.family(user,snap,workspace) or row['status']!='published':raise HTTPException(404,'Запись не найдена.')
            if row['version']!=body.version:raise HTTPException(409,'Запись изменилась. Обновите список.')
            if not row['data']['requireAck'] or row['ack_at']:raise HTTPException(409,'Подтверждение не требуется.')
            db.execute('UPDATE behaviour_notes SET version=version+1,updated=?,ack_at=?,ack_by=? WHERE id=?',(stamp(),stamp(),user['name'],id))
            service.history(db,workspace,user,id,'acknowledged','',dict(by=user['name']))
            author=db.execute('SELECT * FROM accounts WHERE id=? AND active=1',(row['author_id'],)).fetchone()
            if author:service.notifications.deliver(db,[access.public(author)],workspace,row['version']+1,'behaviour.acknowledged')
            return service.public(service.note(db,workspace,id),snap)

    @app.get('/api/behaviour/notes/{id}/history')
    def history(id:str,request:Request,workspace:Workspace='school'):
        with service.context(request,workspace) as (db,user,snap):
            staff(db,user,snap,workspace);service.staff_note(db,user,snap,workspace,id)
            rows=db.execute('SELECT seq,actor_name,created,action,reason,document FROM behaviour_history WHERE workspace=? AND note_id=? ORDER BY seq',(workspace,id)).fetchall()
            return dict(items=[dict(seq=r['seq'],actor=r['actor_name'],created=r['created'],action=r['action'],reason=r['reason'],data=json.loads(r['document'])) for r in rows])
