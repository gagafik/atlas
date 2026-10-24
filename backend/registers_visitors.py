import csv,json,sqlite3,tempfile
from datetime import date,datetime,timedelta
from uuid import uuid4
from fastapi import HTTPException,Query,Request
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask
from .notifications import encoded
from .registers import ZONE,cell,iso,local_day,version
from .registers_models import VisitorAction,VisitorWrite
from .school_forms import enabled

DOCUMENTS=dict(passport='Паспорт',id_card='Удостоверение личности',driver_license='Водительское удостоверение',service_id='Служебное удостоверение',other='Другое')
STATES=dict(inside='В здании',left='Ушёл',voided='Аннулирована')
FIELDS=['name','purpose','hostId','hostText','documentType','note']
BADGE='Пропуск с этим номером уже выдан посетителю, который ещё в здании.'

def register(s,app,Workspace):
    def public(row):
        doc=json.loads(row['document'])
        return dict(id=row['id'],version=row['version'],status=row['status'],day=row['day'],arrived=row['arrived'],departed=row['departed'] or None,badge=row['badge'],author=row['author_name'],created=row['created'],updated=row['updated'],overdue=row['status']=='inside' and row['day']<s.today().isoformat(),hostName=doc.get('hostName',''),data={k:doc.get(k,'') for k in FIELDS})

    def stored(db,workspace,id):
        r=db.execute('SELECT * FROM registers_visitors WHERE workspace=? AND id=?',(workspace,id)).fetchone()
        if not r:raise HTTPException(404,'Запись о посетителе не найдена.')
        return r

    def inside_count(db,workspace):return db.execute("SELECT count(*) FROM registers_visitors WHERE workspace=? AND status='inside'",(workspace,)).fetchone()[0]

    def host(db,snap,workspace,data,previous):
        if not data.hostId:return ''
        if previous and previous.get('hostId')==data.hostId:return previous.get('hostName','')
        h=s.staff_person(db,snap,workspace,data.hostId)
        if not h:raise HTTPException(422,'Выберите действующего сотрудника школы.')
        return h['name']

    def document(data,name):
        return data.model_dump(mode='json',exclude={'arrivedAt'})|dict(hostName=name)

    def search(doc):return ' '.join(str(doc[k]) for k in ['name','purpose','badge','hostName','hostText']).casefold()

    def arrival(value,limit=True):
        # Late entries are allowed for a week: a guard may write the log after the visit.
        current=s.now()
        if value>current+timedelta(minutes=2):raise HTTPException(422,'Время прихода не может быть в будущем.')
        if limit and value<current-timedelta(days=7):raise HTTPException(422,'Поздние записи — не старше 7 дней.')
        return value

    @app.get('/api/registers/visitors/export')
    def export(request:Request,day:date,workspace:Workspace='school'):
        file=tempfile.SpooledTemporaryFile(mode='w+',encoding='utf-8',newline='',max_size=512*1024)
        try:
            with s.context(request,workspace) as (db,u,snap):
                s.require(db,u,snap,workspace,'visitors')
                rows=db.execute('SELECT * FROM registers_visitors WHERE workspace=? AND day=? ORDER BY arrived,seq LIMIT 5001',(workspace,day.isoformat())).fetchall()
                if len(rows)>5000:raise HTTPException(422,'Более 5000 записей за день. Обратитесь к администратору.')
                file.write('﻿');writer=csv.writer(file);writer.writerow(['Дата','Пришёл','Ушёл','Посетитель','Цель визита','К кому','Пропуск','Документ','Примечание','Состояние','Записал'])
                clock=lambda v:datetime.fromisoformat(v).astimezone(ZONE).strftime('%H:%M') if v else ''
                for r in rows:
                    d=json.loads(r['document'])
                    writer.writerow([cell(v) for v in [r['day'],clock(r['arrived']),clock(r['departed']),d['name'],d['purpose'],d.get('hostName') or d.get('hostText',''),r['badge'],DOCUMENTS.get(d.get('documentType',''),''),d.get('note',''),STATES[r['status']],r['author_name']]])
            file.seek(0)
            return StreamingResponse(iter(lambda:file.read(65536),''),media_type='text/csv; charset=utf-8',headers={'Content-Disposition':f'attachment; filename="visitors-{day.isoformat()}.csv"','Cache-Control':'no-store'},background=BackgroundTask(file.close))
        except BaseException:file.close();raise

    @app.get('/api/registers/visitors')
    def visitors(request:Request,workspace:Workspace='school',day:date|None=None,inside:bool=False,q:str=Query(default='',max_length=200),before:int=Query(default=9223372036854775807,ge=1),limit:int=Query(default=100,ge=1,le=500)):
        with s.context(request,workspace) as (db,u,snap):
            s.require(db,u,snap,workspace,'visitors');term=q.strip().casefold()
            if inside:
                rows=db.execute("SELECT * FROM registers_visitors WHERE workspace=? AND status='inside' AND instr(search,?)>0 ORDER BY arrived,seq LIMIT ?",(workspace,term,limit)).fetchall()
                return dict(items=[public(r) for r in rows],nextBefore=None,count=len(rows),inside=inside_count(db,workspace))
            # Pages walk back through registration order; the page itself is shown by arrival time.
            rows=db.execute('SELECT * FROM registers_visitors WHERE workspace=? AND day=? AND seq<? AND instr(search,?)>0 ORDER BY seq DESC LIMIT ?',(workspace,(day or s.today()).isoformat(),before,term,limit+1)).fetchall()
            items,nxt=s.page(rows,limit)
            return dict(items=[public(r) for r in sorted(items,key=lambda r:(r['arrived'],r['seq']))],nextBefore=nxt,count=len(items),inside=inside_count(db,workspace))

    @app.post('/api/registers/visitors')
    def create(body:VisitorWrite,request:Request,workspace:Workspace='school'):
        with s.context(request,workspace,True) as (db,u,snap):
            s.require(db,u,snap,workspace,'visitors');key,old=s.replay(db,u,body,'visitor:'+workspace+':new')
            if old:return old
            if body.version!=0:raise HTTPException(422,'Новая запись создаётся с нулевой версией.')
            data=body.data;arrived=arrival(data.arrivedAt or s.now(),bool(data.arrivedAt));name=host(db,snap,workspace,data,None)
            doc=document(data,name)|dict(badge=data.badge.upper());id=uuid4().hex;stamped=s.now().isoformat()
            try:db.execute("INSERT INTO registers_visitors(id,workspace,version,status,day,arrived,departed,badge,host_id,author_id,author_name,created,updated,search,document) VALUES (?,?,1,'inside',?,?,'',?,?,?,?,?,?,?,?)",(id,workspace,local_day(arrived),iso(arrived),doc['badge'],data.hostId,u['id'],u['name'],stamped,stamped,search(doc),encoded(doc)))
            except sqlite3.IntegrityError:raise HTTPException(409,BADGE) from None
            s.history(db,workspace,'visitors',id,u,'arrived','',dict(before=None,after=doc))
            if data.hostId and data.hostId!=u['id']:
                target=s.account(db,data.hostId)
                if target and enabled(target,snap,workspace):s.notifications.deliver(db,[target],workspace,1,'registers.visitor')
            return s.remember(db,u,body,key,public(stored(db,workspace,id)))

    @app.put('/api/registers/visitors/{id}')
    def edit(id:str,body:VisitorWrite,request:Request,workspace:Workspace='school'):
        with s.context(request,workspace,True) as (db,u,snap):
            s.require(db,u,snap,workspace,'visitors');key,old=s.replay(db,u,body,'visitor:'+workspace+':'+id)
            if old:return old
            row=stored(db,workspace,id);version(row,body.version)
            if row['status']=='voided':raise HTTPException(409,'Аннулированную запись не изменить.')
            if len(body.reason)<3:raise HTTPException(422,'Укажите причину исправления.')
            before=json.loads(row['document']);data=body.data
            arrived=row['arrived']
            if data.arrivedAt and iso(data.arrivedAt)!=row['arrived']:
                arrived=iso(arrival(data.arrivedAt))
                if row['departed'] and arrived>row['departed']:raise HTTPException(422,'Время прихода позже времени ухода.')
            doc=document(data,host(db,snap,workspace,data,before))|dict(badge=data.badge.upper())
            try:db.execute('UPDATE registers_visitors SET version=version+1,day=?,arrived=?,badge=?,host_id=?,updated=?,search=?,document=? WHERE id=?',(local_day(datetime.fromisoformat(arrived)),arrived,doc['badge'],data.hostId,s.now().isoformat(),search(doc),encoded(doc),id))
            except sqlite3.IntegrityError:raise HTTPException(409,BADGE) from None
            s.history(db,workspace,'visitors',id,u,'edited',body.reason,dict(before=before,after=doc))
            return s.remember(db,u,body,key,public(stored(db,workspace,id)))

    @app.post('/api/registers/visitors/{id}/action')
    def action(id:str,body:VisitorAction,request:Request,workspace:Workspace='school'):
        with s.context(request,workspace,True) as (db,u,snap):
            s.require(db,u,snap,workspace,'visitors');key,old=s.replay(db,u,body,'visitor-action:'+workspace+':'+id)
            if old:return old
            row=stored(db,workspace,id);version(row,body.version);stamped=s.now().isoformat()
            if body.action=='depart':
                if row['status']!='inside':raise HTTPException(409,'Уход уже отмечен или запись аннулирована.')
                left=body.at or s.now()
                if iso(left)<row['arrived'] or left>s.now()+timedelta(minutes=2):raise HTTPException(422,'Время ухода раньше прихода или в будущем.')
                db.execute("UPDATE registers_visitors SET version=version+1,status='left',departed=?,updated=? WHERE id=?",(iso(left),stamped,id))
                s.history(db,workspace,'visitors',id,u,'departed',body.reason,dict(departed=iso(left)))
            else:
                if len(body.reason)<3:raise HTTPException(422,'Укажите причину аннулирования.')
                if row['status']=='voided':raise HTTPException(409,'Запись уже аннулирована.')
                db.execute("UPDATE registers_visitors SET version=version+1,status='voided',updated=? WHERE id=?",(stamped,id))
                s.history(db,workspace,'visitors',id,u,'voided',body.reason,dict(before=row['status']))
            return s.remember(db,u,body,key,public(stored(db,workspace,id)))
