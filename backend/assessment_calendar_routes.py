"""HTTP routes of the assessment calendar; every request rechecks the current pairs, scope and lessons."""
from datetime import date,timedelta
import csv,io,json,sqlite3

from fastapi import HTTPException,Query,Request
from fastapi.responses import Response
from .assessment_calendar import Config,NewBooking,Move,Edit,Publish,Cancel,STATES,CATEGORIES,COORDINATOR_ROLES,PairLessons,audience,lesson_days,class_load,check_days,check_window,csv_cell,new_id,stamp,today,label
from .journal import key_for
from .journal_periods import range_locks
from .notifications import encoded,permission_key
from .school_forms import enabled,grant_ids

STATUS='^(planned|published|cancelled)$'
LIMIT=2000


def register(service,app,Workspace):
    access=service.access
    P='/api/assessment-calendar'

    def need_pair(pairs,group,subject):
        if (group,subject) not in pairs:raise HTTPException(403,'Назначать работы можно только по предмету и группе вашей текущей нагрузки.')

    def visible(row,scope,pairs):
        """Staff see works of the classes they teach (or of their own pairs); managers see all."""
        return scope=='all' or bool(set(row['class_ids'])&scope) or (row['group_id'],row['subject_id']) in pairs

    def item(row,user,snap,pairs,manager,pl):
        lesson=pl.find(row['group_id'],row['subject_id'],date.fromisoformat(row['day']),row['lesson_id']) if row['status']!='cancelled' else None
        return service.public(row,snap,user,pairs,manager,today(),lesson)

    def limits(db,user,snap,workspace,cfg,typ,day,aud,text,exclude=None):
        """None while the class has room; otherwise an administrator's recorded override, or a refusal naming the conflicts."""
        messages,ids=service.limit_issues(db,workspace,snap,cfg,typ,day,aud,exclude)
        if not messages:return None
        text=text.strip()
        if not text:
            raise HTTPException(409,'Превышена нагрузка класса. '+' '.join(messages)+(' Администратор может назначить работу с указанием причины.' if user['role']!='admin' else ' Укажите причину превышения.'))
        if user['role']!='admin':raise HTTPException(403,'Разрешить превышение может только администратор.')
        access.record(db,'assessment.override',workspace,detail=encoded(dict(day=day.isoformat(),reason=text)))
        return dict(reason=text,by=user['name'],at=stamp(),issues=messages,bookingIds=ids)

    def open_period(db,workspace,day):
        """A closed journal period freezes its dates for everybody, administrators included."""
        closed=range_locks(db,workspace,day.isoformat(),day.isoformat())
        if closed:raise HTTPException(409,'Период журнала закрыт: '+', '.join(p['name'] for p in closed)+'. Работу на эту дату назначить нельзя.')

    def validate_slot(db,user,snap,workspace,cfg,pairs,pl,group,subject,day,lesson_id,typ,text,exclude=None):
        """The date must carry a non-cancelled lesson of the pair, in an open period, free of other works and within the limits."""
        need_pair(pairs,group,subject)
        if day<today():raise HTTPException(422,'Дата работы — не раньше сегодняшнего дня.')
        settings=snap.state.settings
        if not settings.yearStart<=day<=settings.yearEnd:raise HTTPException(422,'Дата вне учебного года.')
        row=pl.find(group,subject,day,lesson_id)
        if not row or row['status']=='cancelled':raise HTTPException(422,'В этот день у группы нет такого урока по предмету. Выберите дату и урок из расписания.')
        open_period(db,workspace,day)
        busy=db.execute("SELECT document FROM assessment_bookings WHERE workspace=? AND day=? AND lesson_id=? AND group_id=? AND subject_id=? AND status!='cancelled' AND id!=?",
                        (workspace,day.isoformat(),lesson_id,group,subject,exclude or '')).fetchone()
        if busy:
            data=json.loads(busy[0])
            raise HTTPException(409,f"На этот урок уже назначена работа: {data.get('short','')} «{data.get('topic','')}».")
        aud=audience(row,snap.entities['student'])
        return row,aud,limits(db,user,snap,workspace,cfg,typ,day,aud,text,exclude)

    def mutable(db,user,snap,workspace,cfg,id,version):
        """The booking the user may change right now: visible, theirs by the current workload, not stale and not in the past."""
        manager=service.manager(db,user,workspace,cfg);scope,pairs=service.staff_scope(db,user,snap,workspace,cfg)
        row=service.booking(db,workspace,id)
        if not visible(row,scope,pairs):raise HTTPException(404,'Работа не найдена.')
        if not (manager or (row['group_id'],row['subject_id']) in pairs):raise HTTPException(403,'Изменять работу может учитель предмета и группы, ответственный за календарь или администратор.')
        if row['version']!=version:raise HTTPException(409,'Работа изменилась. Обновите календарь.')
        if row['status']=='cancelled':raise HTTPException(409,'Работа отменена.')
        if user['role']!='admin' and row['day']<today().isoformat():raise HTTPException(409,'Дата работы уже прошла; изменить её может администратор.')
        return row,manager,pairs

    def lesson_fields(found,aud):
        return dict(start=found['start'],end_time=found['end'],lesson_id=found['id'],lesson_key=key_for(found),class_ids=encoded(aud['class_ids']),
                    student_ids=encoded(aud['student_ids']),whole_ids=encoded(aud['whole_ids']))

    def reread(db,workspace,id,user,snap,pairs,manager,pl):
        return item(service.booking(db,workspace,id),user,snap,pairs,manager,pl)

    @app.get(P+'/options')
    def options(request:Request,workspace:Workspace='school'):
        with service.context(request,workspace) as (db,user,snap):
            cfg=service.config(db,workspace);manager=service.manager(db,user,workspace,cfg);pairs=service.pairs(user,snap,workspace,manager)
            admin=user['role']=='admin';own=service.family(user,snap,workspace);staff=manager or bool(pairs)
            names=service.names(snap);periods=service.periods(db,workspace) if staff else []
            mine=None if manager else service.classes_of(snap,pairs)
            classes=[c for c in snap.state.classes if mine is None or c.id in mine] if staff else []
            pair_rows=sorted((dict(groupId=g,group=names['group'].get(g,''),subjectId=s,subject=names['subject'].get(s,''),classIds=list(snap.entities['group'][g].classIds),
                                   classes=', '.join(names['klass'].get(c,c) for c in snap.entities['group'][g].classIds)) for g,s in pairs if g in snap.entities['group']),
                             key=lambda p:(p['classes'],p['subject'],p['group']))
            period_names={p['id']:p['name'] for p in periods}
            settings=snap.state.settings
            candidates,keys=[],{}
            if admin:
                for row in db.execute('SELECT * FROM accounts WHERE active=1 ORDER BY name,id'):
                    p=access.public(row)
                    if p['role'] in COORDINATOR_ROLES and enabled(p,snap,workspace):candidates.append(dict(id=p['id'],name=p['name'],role=p['role']));keys[p['id']]=permission_key(p)
            return dict(admin=admin,coordinator=service.coordinator(db,user,workspace,cfg),canBook=bool(pairs),family=bool(own) and not staff,today=today().isoformat(),
                        yearStart=settings.yearStart.isoformat(),yearEnd=settings.yearEnd.isoformat(),
                        types=cfg['types'] if admin else [t for t in cfg['types'] if t['active']],categories=CATEGORIES,states=STATES,
                        maxPerDay=cfg['maxPerDay'],maxPerWeek=cfg['maxPerWeek'],
                        windows=[w|dict(period=period_names.get(w['periodId'],'')) for w in cfg['windows']] if staff else [],periods=periods,
                        pairs=pair_rows,classes=[dict(id=c.id,name=c.name) for c in classes],
                        teachers=[dict(id=t.id,name=t.name) for t in sorted(snap.state.teachers,key=lambda t:t.name)] if manager else [],
                        students=[dict(id=s.id,name=s.name,className=names['klass'].get(s.classId,'')) for s in sorted((snap.entities['student'][id] for id in own),key=lambda s:s.name)],
                        configVersion=cfg['version'] if admin else None,candidates=candidates,
                        # Only coordinators who still hold the right: a leaver or a changed role/access is not shown as ticked.
                        coordinatorIds=[c['id'] for c in cfg['coordinators'] if keys.get(c['id'])==c['permissions']],
                        # Event names of other classes (a quarantine, an excursion) are for staff only.
                        schoolEvents=[dict(name=e.name,start=e.start.isoformat(),end=e.end.isoformat(),effect=e.effect) for e in snap.state.events if staff and e.end>=settings.yearStart and e.start<=settings.yearEnd])

    @app.get(P+'/slots')
    def slots(request:Request,groupId:str=Query(min_length=1,max_length=100),subjectId:str=Query(min_length=1,max_length=100),day:date=Query(),
              typeId:str|None=Query(default=None,max_length=40),exclude:str|None=Query(default=None,max_length=64),workspace:Workspace='school'):
        check_days(day)
        with service.context(request,workspace) as (db,user,snap):
            cfg=service.config(db,workspace);manager=service.manager(db,user,workspace,cfg);pairs=service.pairs(user,snap,workspace,manager)
            need_pair(pairs,groupId,subjectId)
            pl=PairLessons(snap.state);settings=snap.state.settings;students=snap.entities['student']
            booked={}
            for r in db.execute("SELECT id,lesson_id,document FROM assessment_bookings WHERE workspace=? AND day=? AND group_id=? AND subject_id=? AND status!='cancelled' AND id!=?",
                                (workspace,day.isoformat(),groupId,subjectId,exclude or '')):
                data=json.loads(r['document']);booked[r['lesson_id']]=dict(id=r['id'],short=data.get('short',''),topic=data.get('topic',''))
            rows=pl.slots(groupId,subjectId,day);classes=service.names(snap)['klass']
            issues=[];typ=next((t for t in cfg['types'] if t['id']==typeId),None)
            free=next((r for r in rows if r['id'] not in booked),None)
            if typ and free:issues=service.limit_issues(db,workspace,snap,cfg,typ,day,audience(free,students),exclude)[0]
            return dict(day=day.isoformat(),past=day<today(),closed=[p['name'] for p in range_locks(db,workspace,day.isoformat(),day.isoformat())],
                        outsideYear=not settings.yearStart<=day<=settings.yearEnd,
                        slots=[dict(lessonId=r['id'],start=r['start'],end=r['end'],status=r['status'],teachers=r['teachers'],rooms=r['rooms'],
                                    substitute=set(r['teacherIds'])!=set(r['originalTeacherIds']),classes=', '.join(classes.get(c,c) for c in r['classIds']),booked=booked.get(r['id'])) for r in rows],
                        issues=issues,canOverride=user['role']=='admin')

    @app.get(P+'/lesson-days')
    def days_with_lessons(request:Request,groupId:str=Query(min_length=1,max_length=100),subjectId:str=Query(min_length=1,max_length=100),
                          start:date=Query(),end:date=Query(),workspace:Workspace='school'):
        check_window(start,end,62)
        with service.context(request,workspace) as (db,user,snap):
            manager=service.manager(db,user,workspace);pairs=service.pairs(user,snap,workspace,manager)
            need_pair(pairs,groupId,subjectId)
            return dict(days=lesson_days(PairLessons(snap.state),snap.state,groupId,subjectId,start,end))

    @app.get(P+'/bookings')
    def bookings(request:Request,start:date=Query(),end:date=Query(),classId:str|None=Query(default=None,max_length=100),teacherId:str|None=Query(default=None,max_length=100),
                 status:str|None=Query(default=None,pattern=STATUS),workspace:Workspace='school'):
        check_window(start,end,62)
        with service.context(request,workspace) as (db,user,snap):
            cfg=service.config(db,workspace);scope,pairs=service.staff_scope(db,user,snap,workspace,cfg);manager=scope=='all'
            taught=None
            if teacherId:
                if not manager and teacherId not in grant_ids(user,workspace,'teacher'):raise HTTPException(403,'Смотреть можно только свои предметы.')
                taught=service.teacher_pairs(snap,{teacherId})
            clause,params='workspace=? AND day BETWEEN ? AND ?',[workspace,start.isoformat(),end.isoformat()]
            if status:clause+=' AND status=?';params.append(status)
            if classId:clause+=' AND EXISTS(SELECT 1 FROM json_each(class_ids) WHERE value=?)';params.append(classId)
            rows=db.execute(f'SELECT * FROM assessment_bookings WHERE {clause} ORDER BY day,start,seq LIMIT {LIMIT+1}',params).fetchall()
            if len(rows)>LIMIT:raise HTTPException(413,'Слишком много записей. Сократите период или выберите класс.')
            rows=[r for r in map(service.decoded,rows) if visible(r,scope,pairs) and (taught is None or (r['group_id'],r['subject_id']) in taught)]
            pl=PairLessons(snap.state)
            return dict(items=[item(r,user,snap,pairs,manager,pl) for r in rows],
                        events=[dict(name=e.name,start=e.start.isoformat(),end=e.end.isoformat(),effect=e.effect,classIds=e.classIds) for e in snap.state.events if e.start<=end and e.end>=start],
                        limits=dict(maxPerDay=cfg['maxPerDay'],maxPerWeek=cfg['maxPerWeek']))

    @app.get(P+'/load')
    def load(request:Request,start:date=Query(),end:date=Query(),workspace:Workspace='school'):
        check_window(start,end,42)
        with service.context(request,workspace) as (db,user,snap):
            cfg=service.config(db,workspace)
            if not service.manager(db,user,workspace,cfg):raise HTTPException(403,'Сводная нагрузка доступна администрации и ответственным за календарь.')
            first=start-timedelta(days=start.weekday());last=end+timedelta(days=6-end.weekday());counted={t['id']:t['counted'] for t in cfg['types']}
            rows=db.execute("SELECT * FROM assessment_bookings WHERE workspace=? AND status IN ('planned','published') AND day BETWEEN ? AND ? ORDER BY day,start,seq",
                            (workspace,first.isoformat(),last.isoformat())).fetchall()
            found=[b for b in map(service.decoded,rows) if counted.get(b['type_id'],True)];names=service.names(snap)
            days=[(start+timedelta(days=i)).isoformat() for i in range((end-start).days+1)]
            mondays=[(first+timedelta(days=7*i)).isoformat() for i in range(((last-first).days+1)//7)]
            members={c.id:set() for c in snap.state.classes}
            for s in snap.state.students:
                if s.active and s.classId in members:members[s.classId].add(s.id)
            result=[]
            for c in snap.state.classes:
                mine=[b for b in found if c.id in b['class_ids']];cells={}
                for d in days:
                    today_items=[b for b in mine if b['day']==d]
                    cells[d]=dict(load=class_load(today_items,c.id,members[c.id]),items=[dict(id=b['id'],short=b['data'].get('short',''),subject=names['subject'].get(b['subject_id'],''),status=b['status']) for b in today_items])
                weeks={m:class_load([b for b in mine if m<=b['day']<(date.fromisoformat(m)+timedelta(days=7)).isoformat()],c.id,members[c.id]) for m in mondays}
                result.append(dict(classId=c.id,name=c.name,cells=cells,weeks=weeks))
            return dict(days=days,weeks=mondays,rows=result,maxPerDay=cfg['maxPerDay'],maxPerWeek=cfg['maxPerWeek'])

    @app.get(P+'/family')
    def family_view(request:Request,studentId:str=Query(min_length=1,max_length=100),start:date=Query(),end:date=Query(),workspace:Workspace='school'):
        check_window(start,end,62)
        with service.context(request,workspace) as (db,user,snap):
            if studentId not in service.family(user,snap,workspace):raise HTTPException(404,'Ученик недоступен.')
            rows=db.execute("""SELECT * FROM assessment_bookings WHERE workspace=? AND status='published' AND day BETWEEN ? AND ?
                               AND group_id IN (SELECT value FROM json_each(?)) ORDER BY day,start,seq""",
                            (workspace,start.isoformat(),end.isoformat(),encoded(sorted(service.groups_of(snap,studentId))))).fetchall()
            pl=PairLessons(snap.state)
            return dict(items=[service.family_item(r,snap,pl.find(r['group_id'],r['subject_id'],date.fromisoformat(r['day']),r['lesson_id'])) for r in map(service.decoded,rows)])

    @app.get(P+'/export')
    def export(request:Request,classId:str=Query(min_length=1,max_length=100),month:str=Query(pattern=r'^\d{4}-\d{2}$'),workspace:Workspace='school'):
        try:start=date(int(month[:4]),int(month[5:]),1)
        except ValueError:raise HTTPException(422,'Проверьте месяц.')
        check_days(start)
        end=(start.replace(day=28)+timedelta(days=4)).replace(day=1)-timedelta(days=1)
        with service.context(request,workspace) as (db,user,snap):
            cfg=service.config(db,workspace);scope,pairs=service.staff_scope(db,user,snap,workspace,cfg);manager=scope=='all'
            if classId not in snap.entities['class_']:raise HTTPException(404,'Класс не найден.')
            if not manager and classId not in scope:raise HTTPException(403,'Класс недоступен: экспорт доступен по классам вашей нагрузки.')
            rows=db.execute("""SELECT * FROM assessment_bookings WHERE workspace=? AND status IN ('planned','published') AND day BETWEEN ? AND ?
                               AND EXISTS(SELECT 1 FROM json_each(class_ids) WHERE value=?) ORDER BY day,start,seq""",(workspace,start.isoformat(),end.isoformat(),classId)).fetchall()
            output=io.StringIO(newline='');output.write('﻿');writer=csv.writer(output)
            writer.writerow(['Дата','Начало','Окончание','Класс','Группа','Предмет','Тип','Тема','Учителя','Статус'])
            for r in map(service.decoded,rows):
                b=service.public(r,snap,user,pairs,manager,today())
                writer.writerow([csv_cell(v) for v in [label(b['day']),b['start'],b['end'],snap.entities['class_'][classId].name,b['group'],b['subject'],b['short'],b['topic'],b['teachers'],b['statusTitle']]])
            return Response(output.getvalue().encode('utf-8'),media_type='text/csv; charset=utf-8',headers={'Content-Disposition':f'attachment; filename="assessments-{month}.csv"'})

    @app.get(P+'/bookings/{id}/history')
    def history(id:str,request:Request,workspace:Workspace='school'):
        with service.context(request,workspace) as (db,user,snap):
            scope,pairs=service.staff_scope(db,user,snap,workspace)
            if not visible(service.booking(db,workspace,id),scope,pairs):raise HTTPException(404,'Работа не найдена.')
            rows=db.execute('SELECT seq,actor_name,created,action,reason,document FROM assessment_history WHERE workspace=? AND booking_id=? ORDER BY seq',(workspace,id)).fetchall()
            return dict(items=[dict(seq=r['seq'],actor=r['actor_name'],created=r['created'],action=r['action'],reason=r['reason'],data=json.loads(r['document'])) for r in rows])

    @app.post(P+'/bookings')
    def create(body:NewBooking,request:Request,workspace:Workspace='school'):
        with service.context(request,workspace,True) as (db,user,snap):
            key,previous=service.replay(db,user,body,'create')
            if previous:return previous
            cfg=service.config(db,workspace);manager=service.manager(db,user,workspace,cfg);pairs=service.pairs(user,snap,workspace,manager)
            need_pair(pairs,body.groupId,body.subjectId)
            typ=service.type_of(cfg,body.typeId);pl=PairLessons(snap.state)
            found,aud,override=validate_slot(db,user,snap,workspace,cfg,pairs,pl,body.groupId,body.subjectId,body.day,body.lessonId,typ,body.overrideReason)
            id,created=new_id(),stamp();status='published' if body.publish else 'planned'
            data=dict(type=typ['title'],short=typ['short'],topic=body.topic.strip(),note=body.note.strip(),group=found['group'],subject=found['subject'],teachers=found['teachers'],
                      override=override or {},journal=dict(lessonKey=key_for(found),assessmentId=None))
            fields=lesson_fields(found,aud)
            try:
                db.execute('''INSERT INTO assessment_bookings(id,workspace,version,status,type_id,category,day,start,end_time,lesson_id,lesson_key,group_id,subject_id,class_ids,student_ids,whole_ids,
                              author_id,author_name,created,updated,published_at,document) VALUES (?,?,1,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                           (id,workspace,status,typ['id'],typ['category'],body.day.isoformat(),fields['start'],fields['end_time'],fields['lesson_id'],fields['lesson_key'],body.groupId,body.subjectId,
                            fields['class_ids'],fields['student_ids'],fields['whole_ids'],user['id'],user['name'],created,created,created if status=='published' else None,encoded(data)))
            except sqlite3.IntegrityError:raise HTTPException(409,'На этот урок уже назначена работа.')
            service.history(db,workspace,user,id,'created',override['reason'] if override else '',dict(data=data,status=status,day=body.day.isoformat(),lessonId=fields['lesson_id']))
            note=dict(id=id,group_id=body.groupId,subject_id=body.subjectId,version=1)
            if status=='published':service.notify_family(db,workspace,snap,note,'assessment.published')
            service.notify_teachers(db,workspace,snap,note,'assessment.staff_changed',user)
            return service.remember(db,user,body,key,reread(db,workspace,id,user,snap,pairs,manager,pl))

    @app.post(P+'/bookings/{id}/move')
    def move(id:str,body:Move,request:Request,workspace:Workspace='school'):
        with service.context(request,workspace,True) as (db,user,snap):
            key,previous=service.replay(db,user,body,'move:'+id)
            if previous:return previous
            cfg=service.config(db,workspace);row,manager,pairs=mutable(db,user,snap,workspace,cfg,id,body.version)
            pl=PairLessons(snap.state)
            found,aud,override=validate_slot(db,user,snap,workspace,cfg,pairs,pl,row['group_id'],row['subject_id'],body.day,body.lessonId,service.type_for(cfg,row),body.overrideReason,id)
            fields=lesson_fields(found,aud);version=row['version']+1
            # The journal work belongs to the old lesson: a move to another lesson drops the link (the work keeps its category).
            linked=(row['data'].get('journal') or {}).get('assessmentId');dropped=bool(linked) and fields['lesson_key']!=row['lesson_key']
            data=row['data']|dict(override=override or {},teachers=found['teachers'],journal=dict(lessonKey=fields['lesson_key'],assessmentId=None if dropped else linked))
            try:
                db.execute('''UPDATE assessment_bookings SET version=?,updated=?,day=?,start=?,end_time=?,lesson_id=?,lesson_key=?,class_ids=?,student_ids=?,whole_ids=?,document=? WHERE workspace=? AND id=?''',
                           (version,stamp(),body.day.isoformat(),fields['start'],fields['end_time'],fields['lesson_id'],fields['lesson_key'],fields['class_ids'],fields['student_ids'],fields['whole_ids'],encoded(data),workspace,id))
            except sqlite3.IntegrityError:raise HTTPException(409,'На этот урок уже назначена работа.')
            service.history(db,workspace,user,id,'moved',body.reason,dict(before=dict(day=row['day'],start=row['start'],lessonId=row['lesson_id']),
                                                                             after=dict(day=body.day.isoformat(),start=fields['start'],lessonId=fields['lesson_id']),override=override or {},**(dict(journalUnlinked=True) if dropped else {})))
            note=dict(id=id,group_id=row['group_id'],subject_id=row['subject_id'],version=version)
            if row['status']=='published':service.notify_family(db,workspace,snap,note,'assessment.moved')
            service.notify_teachers(db,workspace,snap,note,'assessment.staff_changed',user)
            return service.remember(db,user,body,key,reread(db,workspace,id,user,snap,pairs,manager,pl))

    @app.post(P+'/bookings/{id}/edit')
    def edit(id:str,body:Edit,request:Request,workspace:Workspace='school'):
        with service.context(request,workspace,True) as (db,user,snap):
            cfg=service.config(db,workspace);row,manager,pairs=mutable(db,user,snap,workspace,cfg,id,body.version)
            changed=body.typeId!=row['type_id'];override=row['data'].get('override',{})
            typ=service.type_of(cfg,body.typeId) if changed else service.type_for(cfg,row)
            if changed:
                # A new type may weigh on the class differently; the slot itself stays as booked.
                aud=dict(class_ids=row['class_ids'],student_ids=row['student_ids'],whole_ids=row['whole_ids'])
                override=limits(db,user,snap,workspace,cfg,typ,date.fromisoformat(row['day']),aud,body.overrideReason,id) or {}
            data=row['data']|dict(type=typ['title'],short=typ['short'],topic=body.topic.strip(),note=body.note.strip(),override=override);version=row['version']+1
            db.execute('UPDATE assessment_bookings SET version=?,updated=?,type_id=?,category=?,document=? WHERE workspace=? AND id=?',(version,stamp(),typ['id'],typ['category'],encoded(data),workspace,id))
            service.history(db,workspace,user,id,'edited',override.get('reason','') if changed else '',
                            dict(before=dict(typeId=row['type_id'],type=row['data'].get('type',''),topic=row['data'].get('topic',''),note=row['data'].get('note','')),
                                 after=dict(typeId=typ['id'],type=typ['title'],topic=data['topic'],note=data['note']),override=override))
            if row['status']=='published' and changed:service.notify_family(db,workspace,snap,dict(id=id,group_id=row['group_id'],version=version),'assessment.changed')
            return reread(db,workspace,id,user,snap,pairs,manager,PairLessons(snap.state))

    @app.post(P+'/bookings/{id}/publish')
    def publish(id:str,body:Publish,request:Request,workspace:Workspace='school'):
        with service.context(request,workspace,True) as (db,user,snap):
            cfg=service.config(db,workspace);row,manager,pairs=mutable(db,user,snap,workspace,cfg,id,body.version)
            if row['status']!='planned':raise HTTPException(409,'Работа уже опубликована или отменена.')
            # The family is told about the work only while its lesson is still in the timetable and its period is open.
            pl=PairLessons(snap.state);found=pl.find(row['group_id'],row['subject_id'],date.fromisoformat(row['day']),row['lesson_id'])
            if not found or found['status']=='cancelled':raise HTTPException(409,'Урока в расписании больше нет или он отменён. Перенесите работу на другой урок.')
            open_period(db,workspace,date.fromisoformat(row['day']))
            version=row['version']+1;at=stamp()
            db.execute("UPDATE assessment_bookings SET version=?,updated=?,status='published',published_at=? WHERE workspace=? AND id=?",(version,at,at,workspace,id))
            service.history(db,workspace,user,id,'published','',dict(day=row['day']))
            service.notify_family(db,workspace,snap,dict(id=id,group_id=row['group_id'],version=version),'assessment.published')
            return reread(db,workspace,id,user,snap,pairs,manager,pl)

    @app.post(P+'/bookings/{id}/cancel')
    def cancel(id:str,body:Cancel,request:Request,workspace:Workspace='school'):
        with service.context(request,workspace,True) as (db,user,snap):
            cfg=service.config(db,workspace);row,manager,pairs=mutable(db,user,snap,workspace,cfg,id,body.version)
            data=row['data']|dict(cancelReason=body.reason.strip());version=row['version']+1
            db.execute("UPDATE assessment_bookings SET version=?,updated=?,status='cancelled',document=? WHERE workspace=? AND id=?",(version,stamp(),encoded(data),workspace,id))
            service.history(db,workspace,user,id,'cancelled',body.reason.strip(),dict(day=row['day'],was=row['status']))
            note=dict(id=id,group_id=row['group_id'],subject_id=row['subject_id'],version=version)
            if row['status']=='published':service.notify_family(db,workspace,snap,note,'assessment.cancelled')
            service.notify_teachers(db,workspace,snap,note,'assessment.staff_changed',user)
            return reread(db,workspace,id,user,snap,pairs,manager,PairLessons(snap.state))

    @app.put(P+'/config')
    def configure(body:Config,request:Request,workspace:Workspace='school'):
        with service.context(request,workspace,True) as (db,user,snap):
            if user['role']!='admin':raise HTTPException(403,'Настройки задаёт администрация школы.')
            current=service.config(db,workspace)
            if current['version']!=body.version:raise HTTPException(409,'Настройки изменились. Обновите страницу.')
            used={r[0] for r in db.execute('SELECT DISTINCT type_id FROM assessment_bookings WHERE workspace=?',(workspace,))}
            titles={t['id']:t['title'] for t in current['types']}
            for id in sorted(used-{t.id for t in body.types}):
                raise HTTPException(422,f"Тип «{titles.get(id,id)}» уже используется. Отключите его вместо удаления.")
            periods={p['id']:p for p in service.periods(db,workspace)};windows=[]
            for w in body.windows:
                period=periods.get(w.periodId)
                if not period:raise HTTPException(422,'Учебный период для окна СОЧ не найден.')
                if w.start.isoformat()<period['start'] or w.end.isoformat()>period['end']:raise HTTPException(422,f"Окно СОЧ должно лежать внутри периода «{period['name']}».")
                windows.append(w.model_dump(mode='json'))
            coordinators,stored=[],{c['id'] for c in current['coordinators']}
            for id in body.coordinatorIds:
                row=db.execute('SELECT * FROM accounts WHERE id=? AND active=1',(id,)).fetchone();p=access.public(row) if row else None
                if not p or p['role'] not in COORDINATOR_ROLES or not enabled(p,snap,workspace):
                    # A coordinator who has left or changed role drops out; only a newly named person must be valid.
                    if id in stored:continue
                    raise HTTPException(422,'Проверьте ответственных сотрудников.')
                coordinators.append(dict(id=id,name=p['name'],permissions=permission_key(p)))
            value=dict(types=[t.model_dump() for t in body.types],maxPerDay=body.maxPerDay,maxPerWeek=body.maxPerWeek,windows=windows,coordinators=coordinators);version=current['version']+1
            db.execute('INSERT INTO assessment_config VALUES (?,?,?) ON CONFLICT(workspace) DO UPDATE SET version=excluded.version,document=excluded.document',(workspace,version,encoded(value)))
            service.history(db,workspace,user,'','configured',body.reason,dict(before={k:current[k] for k in value},after=value))
            access.record(db,'assessment.configured',workspace,detail=f'Редакция {version} · {body.reason}')
            return dict(version=version,types=value['types'],maxPerDay=body.maxPerDay,maxPerWeek=body.maxPerWeek,windows=windows,coordinatorIds=[c['id'] for c in coordinators])
