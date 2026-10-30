"""HTTP routes for praise, remarks and measures; every request rechecks the current scope."""
from datetime import date,timedelta
import json

from fastapi import HTTPException,Query,Request
from .behaviour import Config,NewNotes,Edit,Withdraw,Decision,Acknowledge,KINDS,LEVELS,PAGE,stamp,today,new_id,export_rows,window
from .notifications import encoded

STATUS='^(pending|published|rejected|withdrawn)$'


def register(service,app,Workspace):
    access=service.access

    def staff(db,user,snap,workspace):
        allowed=service.scope(db,user,snap,workspace)
        if user['role']!='admin' and not allowed:raise HTTPException(403,'Раздел доступен учителям учеников и назначенным классным руководителям.')
        return allowed

    def admin(user):
        if user['role']!='admin':raise HTTPException(403,'Решение принимает администрация школы.')

    def require_change(user,row,version):
        if row['version']!=version:raise HTTPException(409,'Запись изменилась. Обновите список.')
        if row['status'] not in ['pending','published']:raise HTTPException(409,'Запись уже отозвана или отклонена.')
        if not service.may_change(user,row):raise HTTPException(403,'Изменить запись может её автор до утверждения меры или администратор.')

    def items(rows,snap,user=None,staff_view=True):
        return [service.public(dict(r)|dict(data=json.loads(r['document'])),snap,staff_view,user) for r in rows]

    def publish(db,workspace,row,user,version,data,snap):
        """Bring a note into force: number its letter and tell the family if it is visible."""
        if data.get('letter') and not data.get('letterNumber'):data['letterNumber']=service.letter_number(db,workspace)
        if row['visible']:service.notify_family(db,workspace,[row['student_id']],version,'behaviour.published',note(row,data['category'],snap))

    def note(row,category,snap,kind=None):
        pupil=snap.entities['student'].get(row['student_id'])
        return dict(id=row['id'],kind=kind or row['kind'],category=category,name=pupil.name if pupil else '')

    @app.get('/api/behaviour/options')
    def options(request:Request,workspace:Workspace='school'):
        with service.context(request,workspace) as (db,user,snap):
            cfg=service.config(db,workspace);allowed=service.scope(db,user,snap,workspace);own=service.family(user,snap,workspace)
            classes={c.id:c.name for c in snap.state.classes}
            pupils=sorted((snap.entities['student'][id] for id in allowed|own),key=lambda s:(classes.get(s.classId,''),s.name))
            pending=db.execute("SELECT count(*) FROM behaviour_notes WHERE workspace=? AND status='pending'",(workspace,)).fetchone()[0] if user['role']=='admin' else 0
            return dict(admin=user['role']=='admin',canWrite=bool(allowed),family=bool(own) and not allowed,canAcknowledge=user['role']=='parent',
                        today=today().isoformat(),kinds=KINDS,levels=LEVELS,myLevels=sorted(service.levels(db,user,snap,workspace)),
                        remarkAck=cfg['remarkAck'],categories=cfg['categories'],pending=pending,
                        configVersion=cfg['version'] if user['role']=='admin' else None,
                        classes=[dict(id=id,name=name) for id,name in classes.items() if any(s.classId==id for s in pupils)],
                        students=[dict(id=s.id,name=s.name,classId=s.classId,className=classes.get(s.classId,'')) for s in pupils])

    @app.put('/api/behaviour/config')
    def configure(body:Config,request:Request,workspace:Workspace='school'):
        with service.context(request,workspace,True) as (db,user,snap):
            admin(user)
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
            if body.day>today() or body.day<today()-timedelta(days=366):raise HTTPException(422,'Дата записи — не позже сегодняшнего дня и не раньше года назад.')
            category=service.category(db,workspace,body.categoryId);cfg=service.config(db,workspace);lesson=None
            if body.lessonKey:
                # Whoever teaches the lesson today may mark its pupils at teacher level, substitutes included.
                lesson=service.lesson(db,user,snap,workspace,body.day,body.lessonKey)
                if lesson['locked']:raise HTTPException(403,'Период журнала закрыт: отметки к этому уроку больше не добавляются.')
                if not set(body.studentIds)<=set(lesson['studentIds']):raise HTTPException(403,'Отметить можно только учеников этого урока.')
                if category['level']!='teacher' and not service.allowed(db,user,snap,workspace,category['level'],body.studentIds):
                    raise HTTPException(403,f'Меру уровня «{LEVELS[category["level"]]}» вы не можете назначить выбранным ученикам.')
            else:
                allowed=staff(db,user,snap,workspace)
                if not set(body.studentIds)<=allowed:raise HTTPException(403,'Можно отметить только учеников, которых вы сейчас учите или курируете.')
                if not service.allowed(db,user,snap,workspace,category['level'],body.studentIds):
                    raise HTTPException(403,f'Меру уровня «{LEVELS[category["level"]]}» вы не можете назначить выбранным ученикам.')
            status='pending' if category['approval'] and user['role']!='admin' else 'published'
            batch=new_id();created=stamp();ids=[]
            for student in body.studentIds:
                id=new_id();ids.append(id);pupil=snap.entities['student'][student]
                data=dict(category=category['title'],text=body.text.strip(),level=category['level'],approval=category['approval'],letter=category['letter'],
                          printed=category['report'] if body.printed is None else body.printed,requireAck=category['letter'] or category['kind']=='remark' and cfg['remarkAck'],
                          approvedBy=user['name'] if category['approval'] and status=='published' else '')
                if lesson:data['lesson']=dict(key=body.lessonKey,label=lesson['label'])
                row=dict(id=id,student_id=student,kind=category['kind'],visible=int(body.visible))
                if status=='published':publish(db,workspace,row,user,1,data,snap)
                db.execute('''INSERT INTO behaviour_notes(id,batch,workspace,student_id,class_id,version,status,kind,category_id,day,author_id,author_name,created,updated,document,visible)
                              VALUES (?,?,?,?,?,1,?,?,?,?,?,?,?,?,?,?)''',(id,batch,workspace,student,pupil.classId,status,category['kind'],category['id'],body.day.isoformat(),user['id'],user['name'],created,created,encoded(data),int(body.visible)))
                service.history(db,workspace,user,id,'created','',data|dict(visible=body.visible,status=status))
            if status=='pending':service.notify_admins(db,workspace,1,user['id'])
            return service.remember(db,user,body,key,dict(batch=batch,ids=ids,status=status))

    @app.get('/api/behaviour/lesson')
    def lesson_marks(request:Request,lessonKey:str=Query(pattern=r'^[a-f0-9]{64}$'),day:date=Query(),workspace:Workspace='school'):
        with service.context(request,workspace) as (db,user,snap):
            lesson=service.lesson(db,user,snap,workspace,day,lessonKey)
            levels=service.levels(db,user,snap,workspace)|{'teacher'}
            rows=db.execute("""SELECT * FROM behaviour_notes WHERE workspace=? AND day=? AND status IN ('pending','published')
                               AND json_extract(document,'$.lesson.key')=? ORDER BY seq""",(workspace,day.isoformat(),lessonKey)).fetchall()
            pupils=set(lesson['studentIds'])
            return dict(label=lesson['label'],canMark=not lesson['locked'] and day<=today(),
                        categories=[c for c in service.config(db,workspace)['categories'] if c['active'] and c['level'] in levels],
                        notes=[dict(id=r['id'],studentId=r['student_id'],kind=r['kind'],category=json.loads(r['document'])['category'],status=r['status'],author=r['author_name'])
                               for r in rows if r['student_id'] in pupils])

    @app.get('/api/behaviour/notes')
    def notes(request:Request,workspace:Workspace='school',classId:str|None=None,studentId:str|None=None,kind:str|None=Query(default=None,pattern='^(praise|remark)$'),
              status:str|None=Query(default=None,pattern=STATUS),start:date|None=None,end:date|None=None,before:int|None=Query(default=None,ge=1)):
        window(start,end)
        with service.context(request,workspace) as (db,user,snap):
            staff(db,user,snap,workspace)
            clause,params=service.filtered(db,user,snap,workspace,classId,studentId,kind,start,end,status)
            if before:clause+=' AND seq<?';params.append(before)
            rows=db.execute(f'SELECT * FROM behaviour_notes WHERE {clause} ORDER BY day DESC,seq DESC LIMIT {PAGE+1}',params).fetchall()
            return dict(items=items(rows[:PAGE],snap,user),nextBefore=rows[PAGE-1]['seq'] if len(rows)>PAGE else None)

    @app.get('/api/behaviour/queue')
    def queue(request:Request,workspace:Workspace='school',before:int|None=Query(default=None,ge=1)):
        with service.context(request,workspace) as (db,user,snap):
            admin(user)
            clause,params="workspace=? AND status='pending'",[workspace]
            if before:clause+=' AND seq<?';params.append(before)
            rows=db.execute(f'SELECT * FROM behaviour_notes WHERE {clause} ORDER BY seq DESC LIMIT {PAGE+1}',params).fetchall()
            return dict(items=items(rows[:PAGE],snap,user),nextBefore=rows[PAGE-1]['seq'] if len(rows)>PAGE else None)

    @app.post('/api/behaviour/notes/{id}/approve')
    def approve(id:str,body:Decision,request:Request,workspace:Workspace='school'):
        with service.context(request,workspace,True) as (db,user,snap):
            admin(user);row=service.staff_note(db,user,snap,workspace,id)
            if row['version']!=body.version:raise HTTPException(409,'Запись изменилась. Обновите список.')
            if row['status']!='pending':raise HTTPException(409,'Запись не ждёт согласования.')
            data=row['data']|dict(approvedBy=user['name'],decision=body.reason.strip());version=row['version']+1
            publish(db,workspace,row,user,version,data,snap)
            db.execute("UPDATE behaviour_notes SET version=?,updated=?,status='published',document=? WHERE id=?",(version,stamp(),encoded(data),id))
            service.history(db,workspace,user,id,'approved',body.reason.strip(),data)
            service.notify_accounts(db,workspace,{row['author_id']},version,'behaviour.decided',user['id'])
            return service.public(service.note(db,workspace,id),snap,True,user)

    @app.post('/api/behaviour/notes/{id}/reject')
    def reject(id:str,body:Decision,request:Request,workspace:Workspace='school'):
        with service.context(request,workspace,True) as (db,user,snap):
            admin(user);row=service.staff_note(db,user,snap,workspace,id)
            if not body.reason.strip():raise HTTPException(422,'Укажите причину отказа.')
            if row['version']!=body.version:raise HTTPException(409,'Запись изменилась. Обновите список.')
            if row['status']!='pending':raise HTTPException(409,'Запись не ждёт согласования.')
            data=row['data']|dict(decision=body.reason.strip());version=row['version']+1
            db.execute("UPDATE behaviour_notes SET version=?,updated=?,status='rejected',document=? WHERE id=?",(version,stamp(),encoded(data),id))
            service.history(db,workspace,user,id,'rejected',body.reason.strip(),data)
            service.notify_accounts(db,workspace,{row['author_id']},version,'behaviour.decided',user['id'])
            return service.public(service.note(db,workspace,id),snap,True,user)

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

    @app.get('/api/behaviour/board')
    def board(request:Request,workspace:Workspace='school',classId:str|None=None,kind:str=Query(default='praise',pattern='^(praise|remark)$'),
              start:date|None=None,end:date|None=None):
        window(start,end)
        with service.context(request,workspace) as (db,user,snap):
            staff(db,user,snap,workspace)
            clause,params=service.filtered(db,user,snap,workspace,classId,None,kind,start,end,'published')
            counts=db.execute(f'SELECT student_id,count(*) AS n FROM behaviour_notes WHERE {clause} GROUP BY student_id ORDER BY n DESC',params).fetchall()
            classes={c.id:c.name for c in snap.state.classes};pupils=snap.entities['student']
            ranked=sorted((r for r in counts if r[0] in pupils),key=lambda r:(-r[1],pupils[r[0]].name))[:50]
            places=sorted({r[1] for r in ranked},reverse=True)
            # Equal counts share a place, so the podium may hold more than three pupils.
            return dict(kind=kind,items=[dict(place=places.index(n)+1,studentId=id,student=pupils[id].name,className=classes.get(pupils[id].classId,''),count=n) for id,n in ranked])

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
            clause,params="workspace=? AND student_id=? AND status='published' AND visible=1",[workspace,studentId]
            totals={k:v for k,v in db.execute(f'SELECT kind,count(*) FROM behaviour_notes WHERE {clause} GROUP BY kind',params)}
            if before:clause+=' AND seq<?';params.append(before)
            rows=db.execute(f'SELECT * FROM behaviour_notes WHERE {clause} ORDER BY day DESC,seq DESC LIMIT {PAGE+1}',params).fetchall()
            return dict(items=items(rows[:PAGE],snap,staff_view=False),nextBefore=rows[PAGE-1]['seq'] if len(rows)>PAGE else None,
                        praise=totals.get('praise',0),remark=totals.get('remark',0))

    @app.get('/api/behaviour/notes/{id}/letter')
    def letter(id:str,request:Request,workspace:Workspace='school'):
        with service.context(request,workspace) as (db,user,snap):
            row=service.family_note(db,user,snap,workspace,id) if user['role'] in ['parent','student'] else service.staff_note(db,user,snap,workspace,id)
            data=row['data']
            if row['status']!='published' or not data.get('letterNumber'):raise HTTPException(409,'Письмо-решение готовится только для действующей меры с письмом.')
            classes={c.id:c.name for c in snap.state.classes};pupil=snap.entities['student'].get(row['student_id'])
            return dict(school=snap.state.settings.name,number=data['letterNumber'],day=row['day'],student=pupil.name if pupil else '',
                        className=classes.get(row['class_id'],''),kind=KINDS[row['kind']],title=data['category'],text=data['text'],
                        level=LEVELS[data.get('level','teacher')],author=row['author_name'],approvedBy=data.get('approvedBy',''),
                        acknowledgedAt=row['ack_at'],acknowledgedBy=row['ack_by'])

    @app.post('/api/behaviour/notes/{id}/edit')
    def edit(id:str,body:Edit,request:Request,workspace:Workspace='school'):
        with service.context(request,workspace,True) as (db,user,snap):
            row=service.staff_note(db,user,snap,workspace,id);require_change(user,row,body.version)
            if row['ack_at']:raise HTTPException(409,'Родитель уже ознакомился с записью. Отзовите её и создайте новую.')
            category=service.category(db,workspace,body.categoryId)
            if not service.allowed(db,user,snap,workspace,category['level'],[row['student_id']]):raise HTTPException(403,'Эта категория вам недоступна для этого ученика.')
            if row['status']=='published' and category['approval'] and user['role']!='admin':
                raise HTTPException(409,'Мера с согласованием назначается новой записью. Отзовите эту запись и создайте новую.')
            data=row['data']|dict(category=category['title'],text=body.text.strip(),level=category['level'],approval=category['approval'],letter=category['letter'],
                                  requireAck=category['letter'] or category['kind']=='remark' and service.config(db,workspace)['remarkAck'])
            if body.printed is not None:data['printed']=body.printed
            version=row['version']+1
            if row['status']=='published' and data['letter'] and not data.get('letterNumber'):data['letterNumber']=service.letter_number(db,workspace)
            db.execute('UPDATE behaviour_notes SET version=?,updated=?,kind=?,category_id=?,document=? WHERE id=?',(version,stamp(),category['kind'],category['id'],encoded(data),id))
            service.history(db,workspace,user,id,'edited',body.reason,dict(before=row['data'],after=data))
            if row['status']=='published' and row['visible']:service.notify_family(db,workspace,[row['student_id']],version,'behaviour.changed',note(row,category['title'],snap,category['kind']))
            return service.public(service.note(db,workspace,id),snap,True,user)

    @app.post('/api/behaviour/notes/{id}/withdraw')
    def withdraw(id:str,body:Withdraw,request:Request,workspace:Workspace='school'):
        with service.context(request,workspace,True) as (db,user,snap):
            row=service.staff_note(db,user,snap,workspace,id);require_change(user,row,body.version)
            data=row['data']|dict(withdrawReason=body.reason);version=row['version']+1
            db.execute("UPDATE behaviour_notes SET version=?,updated=?,status='withdrawn',document=? WHERE id=?",(version,stamp(),encoded(data),id))
            service.history(db,workspace,user,id,'withdrawn',body.reason,data)
            if row['status']=='published' and row['visible']:service.notify_family(db,workspace,[row['student_id']],version,'behaviour.withdrawn',note(row,row['data']['category'],snap))
            return service.public(service.note(db,workspace,id),snap,True,user)

    @app.post('/api/behaviour/notes/{id}/acknowledge')
    def acknowledge(id:str,body:Acknowledge,request:Request,workspace:Workspace='school'):
        with service.context(request,workspace,True) as (db,user,snap):
            if user['role']!='parent':raise HTTPException(404,'Запись не найдена.')
            row=service.family_note(db,user,snap,workspace,id)
            if row['version']!=body.version:raise HTTPException(409,'Запись изменилась. Обновите список.')
            if not row['data']['requireAck'] or row['ack_at']:raise HTTPException(409,'Подтверждение не требуется.')
            db.execute('UPDATE behaviour_notes SET version=version+1,updated=?,ack_at=?,ack_by=? WHERE id=?',(stamp(),stamp(),user['name'],id))
            service.history(db,workspace,user,id,'acknowledged','',dict(by=user['name']))
            service.notify_accounts(db,workspace,{row['author_id']},row['version']+1,'behaviour.acknowledged')
            return service.public(service.note(db,workspace,id),snap)

    @app.get('/api/behaviour/notes/{id}/history')
    def history(id:str,request:Request,workspace:Workspace='school'):
        with service.context(request,workspace) as (db,user,snap):
            staff(db,user,snap,workspace);service.staff_note(db,user,snap,workspace,id)
            rows=db.execute('SELECT seq,actor_name,created,action,reason,document FROM behaviour_history WHERE workspace=? AND note_id=? ORDER BY seq',(workspace,id)).fetchall()
            return dict(items=[dict(seq=r['seq'],actor=r['actor_name'],created=r['created'],action=r['action'],reason=r['reason'],data=json.loads(r['document'])) for r in rows])
