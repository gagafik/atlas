from uuid import uuid4
import json
from fastapi import HTTPException,Query,Request
from .clubs import today,version,unpack
from .clubs_models import SessionWrite,SessionAction,AttendanceWrite
from .notifications import encoded

def register(s,app,Workspace):
    def public(db,u,snap,c,r,include_marks=True):
        manager=s.manager(db,u,c);own=encoded(sorted(s.pupils(u,snap,c['workspace'])))
        marks=[dict(studentId=a['student_id'],status=a['status'],note=a['note'],**json.loads(a['document'])) for a in db.execute('SELECT * FROM club_attendance WHERE session_id=? AND (?=1 OR student_id IN (SELECT value FROM json_each(?))) ORDER BY student_id',(r['id'],int(manager),own))] if include_marks else []
        return {k:r[k] for k in ['id','version','status','data']}|dict(marks=marks)

    @app.get('/api/clubs/items/{id}/sessions')
    def sessions(id:str,request:Request,workspace:Workspace='school',after:int=Query(default=0,ge=0),limit:int=Query(default=30,ge=1,le=100)):
        with s.context(request,workspace) as (db,u,snap):
            c=s.club(db,workspace,id);s.visible(db,u,snap,c)
            rows=db.execute('SELECT * FROM club_sessions WHERE club_id=? AND seq>? ORDER BY seq LIMIT ?',(id,after,limit+1)).fetchall()
            return dict(items=[public(db,u,snap,c,unpack(r),False) for r in rows[:limit]],nextAfter=rows[limit-1]['seq'] if len(rows)>limit else None)

    @app.get('/api/clubs/items/{id}/sessions/{sid}')
    def session(id:str,sid:str,request:Request,workspace:Workspace='school'):
        with s.context(request,workspace) as (db,u,snap):
            c=s.club(db,workspace,id);s.visible(db,u,snap,c);return public(db,u,snap,c,s.session(db,c,sid))

    def save(body,request,workspace,id,sid):
        with s.context(request,workspace,True) as (db,u,snap):
            c=s.club(db,workspace,id);s.require_manager(db,u,c);key,old=s.replay(db,u,body,'session:'+workspace+':'+id+':'+(sid or 'new'))
            if old:return old
            version(c,body.clubVersion)
            if c['status']=='archived':raise HTTPException(409,'Кружок в архиве.')
            previous=s.session(db,c,sid) if sid else None
            if previous:
                version(previous,body.version)
                if previous['status']!='planned':raise HTTPException(409,'Можно менять только запланированное занятие.')
                if len(body.reason)<3:raise HTTPException(422,'Укажите причину изменения.')
            elif body.version!=0:raise HTTPException(409,'Новая запись должна иметь версию 0.')
            if not previous and db.execute('SELECT count(*) FROM club_sessions WHERE club_id=?',(id,)).fetchone()[0]>=200:raise HTTPException(422,'В одном кружке до 200 занятий.')
            d=body.data.model_dump(mode='json')
            if not c['data']['startsOn']<=d['date']<=c['data']['endsOn']:raise HTTPException(422,'Дата вне периода кружка.')
            overlap=db.execute("SELECT * FROM club_sessions WHERE workspace=? AND id<>? AND date=? AND start<? AND end>? AND status IN ('planned','active','completed')",(workspace,sid or '',d['date'],d['end'],d['start'])).fetchall()
            students={r[0] for r in db.execute("SELECT student_id FROM club_members WHERE club_id=? AND status='enrolled'",(id,))}
            staff={a['id'] for a in s.active_staff(db,snap,c)}
            for other in overlap:
                if other['club_id']==id:raise HTTPException(409,'Время пересекается с другим занятием кружка.')
                other_students={r[0] for r in db.execute("SELECT student_id FROM club_members WHERE club_id=? AND status='enrolled'",(other['club_id'],))}
                other_staff={a['id'] for a in s.active_staff(db,snap,s.club(db,workspace,other['club_id']))}
                if students & other_students or staff & other_staff:raise HTTPException(409,'Участник или ответственный уже занят в другом кружке в это время.')
            sid=sid or uuid4().hex
            if previous:db.execute('UPDATE club_sessions SET version=version+1,date=?,start=?,end=?,document=? WHERE id=?',(d['date'],d['start'],d['end'],encoded(d),sid))
            else:db.execute("INSERT INTO club_sessions(id,club_id,workspace,version,status,date,start,end,document) VALUES (?,?,?,1,'planned',?,?,?,?)",(sid,id,workspace,d['date'],d['start'],d['end'],encoded(d)))
            s.bump(db,c);s.history(db,u,c,sid,'session-saved',body.reason,dict(before=previous['data'] if previous else None,after=d))
            s.notify(db,u,snap,c,students,'clubs.session')
            return s.remember(db,u,body,key,public(db,u,snap,c,s.session(db,c,sid)))
    @app.post('/api/clubs/items/{id}/sessions')
    def create(id:str,body:SessionWrite,request:Request,workspace:Workspace='school'):return save(body,request,workspace,id,None)
    @app.put('/api/clubs/items/{id}/sessions/{sid}')
    def edit(id:str,sid:str,body:SessionWrite,request:Request,workspace:Workspace='school'):return save(body,request,workspace,id,sid)

    @app.post('/api/clubs/items/{id}/sessions/{sid}/action')
    def action(id:str,sid:str,body:SessionAction,request:Request,workspace:Workspace='school'):
        with s.context(request,workspace,True) as (db,u,snap):
            c=s.club(db,workspace,id);s.require_manager(db,u,c);r=s.session(db,c,sid);key,old=s.replay(db,u,body,'session-action:'+workspace+':'+sid)
            if old:return old
            version(r,body.version)
            if c['status']=='archived':raise HTTPException(409,'Кружок в архиве.')
            allowed={'start':(['planned'],'active'),'complete':(['active'],'completed'),'reopen':(['completed'],'active'),'cancel':(['planned','active'],'cancelled')}
            valid,target=allowed[body.action]
            if r['status'] not in valid:raise HTTPException(409,'Действие недоступно для занятия.')
            if body.action in ['start','complete','reopen'] and r['data']['date']>today():raise HTTPException(409,'Отметки будущего занятия пока недоступны.')
            if body.action=='start':
                rows=db.execute("SELECT * FROM club_members WHERE club_id=? AND enrolled_on<>'' AND enrolled_on<=? AND (left_on='' OR left_on>?) ORDER BY seq",(id,r['date'],r['date'])).fetchall()
                for raw in rows:
                    m=unpack(raw)
                    if not s.eligible(snap,c,m['student_id']):continue
                    db.execute("INSERT INTO club_attendance VALUES (?,?,?,'unknown','',?)",(sid,m['student_id'],m['id'],encoded({k:m['data'][k] for k in ['studentName','className']})))
                if not db.execute('SELECT 1 FROM club_attendance WHERE session_id=?',(sid,)).fetchone():raise HTTPException(409,'Нет участников на эту дату. При необходимости отмените занятие.')
            if body.action=='complete' and db.execute("SELECT 1 FROM club_attendance WHERE session_id=? AND status='unknown'",(sid,)).fetchone():raise HTTPException(409,'Сначала отметьте каждого участника.')
            db.execute('UPDATE club_sessions SET version=version+1,status=? WHERE id=?',(target,sid));s.bump(db,c)
            marks=[dict(a) for a in db.execute('SELECT student_id,status,note,document FROM club_attendance WHERE session_id=?',(sid,))]
            s.history(db,u,c,sid,'session-'+target,body.reason,dict(date=r['date'],title=r['data']['title'],marks=[dict(studentName=json.loads(a['document'])['studentName'],status=a['status'],note=a['note']) for a in marks]))
            recipients={a['student_id'] for a in marks}|{a[0] for a in db.execute("SELECT student_id FROM club_members WHERE club_id=? AND status='enrolled'",(id,))}
            s.notify(db,u,snap,c,recipients,'clubs.session')
            return s.remember(db,u,body,key,public(db,u,snap,c,s.session(db,c,sid)))

    @app.put('/api/clubs/items/{id}/sessions/{sid}/attendance')
    def attendance(id:str,sid:str,body:AttendanceWrite,request:Request,workspace:Workspace='school'):
        with s.context(request,workspace,True) as (db,u,snap):
            c=s.club(db,workspace,id);s.require_manager(db,u,c);r=s.session(db,c,sid);key,old=s.replay(db,u,body,'attendance:'+workspace+':'+sid)
            if old:return old
            version(r,body.version)
            if c['status']=='archived' or r['status']!='active':raise HTTPException(409,'Для изменения нужно открыть отметки занятия.')
            roster={a['student_id']:dict(a) for a in db.execute('SELECT * FROM club_attendance WHERE session_id=?',(sid,))}
            if any(m.studentId not in roster for m in body.marks):raise HTTPException(422,'Ученик не входил в сохранённый состав занятия.')
            previous=[];after=[]
            for m in body.marks:
                oldmark=roster[m.studentId];name=json.loads(oldmark['document'])['studentName'];previous.append(dict(studentName=name,status=oldmark['status'],note=oldmark['note']));after.append(dict(studentName=name,status=m.status,note=m.note))
                db.execute('UPDATE club_attendance SET status=?,note=? WHERE session_id=? AND student_id=?',(m.status,m.note,sid,m.studentId))
            db.execute('UPDATE club_sessions SET version=version+1 WHERE id=?',(sid,));s.bump(db,c)
            s.history(db,u,c,sid,'attendance',body.reason,dict(date=r['date'],before=previous,after=after));s.notify(db,u,snap,c,[m.studentId for m in body.marks],'clubs.session')
            return s.remember(db,u,body,key,public(db,u,snap,c,s.session(db,c,sid)))
