import base64,csv,hashlib,json,sqlite3,tempfile
from collections import defaultdict
from datetime import datetime
from uuid import uuid4
from fastapi import HTTPException,Query,Request
from fastapi.responses import StreamingResponse
from pydantic import ValidationError
from starlette.background import BackgroundTask
from .notifications import encoded
from .registers import cell,version
from .registers_models import InventoryImport,InventoryPreview,ItemAction,ItemData,ItemMove,ItemWrite
from .school_forms import enabled
from .tabular import parse_file

CONDITIONS=dict(new='Новое',good='Хорошее',fair='Удовлетворительное',poor='Плохое',broken='Неисправно')
STATES=dict(active='На учёте',written_off='Списан')
COLUMNS=dict(number='Инвентарный номер',title='Наименование',category='Категория',room='Кабинет',location='Место',responsible='Ответственный',condition='Состояние',acquiredOn='Дата поступления',serial='Серийный номер',note='Примечание')
HEADERS={v.casefold():k for k,v in COLUMNS.items()}
COLUMN_OF=dict(roomId='room',responsibleId='responsible',acquiredOn='acquiredOn')
REQUIRED=['number','title','category']
LIMIT=2000
TAKEN='Инвентарный номер уже используется.'
NO_ROOM='Кабинет не найден в расписании.'
KEEP=['writtenOffOn','writtenOffReason','writtenOffBy']

def register(s,app,Workspace):
    def stored(db,workspace,id):
        r=db.execute('SELECT * FROM registers_inventory WHERE workspace=? AND id=?',(workspace,id)).fetchone()
        if not r:raise HTTPException(404,'Предмет не найден.')
        return r

    def public(db,snap,workspace,row):
        doc=json.loads(row['document']);who=s.staff_person(db,snap,workspace,doc['responsibleId'])
        # data is exactly what a client may send back; names frozen with the record stay outside it.
        return dict(id=row['id'],version=row['version'],status=row['status'],number=row['number'],created=row['created'],updated=row['updated'],roomName=doc['roomName'],responsibleName=doc['responsibleName'],roomCurrent=bool(doc['roomId'] and doc['roomId'] in snap.entities['room']),responsibleCurrent=bool(who),writtenOff=dict(on=doc.get('writtenOffOn',''),reason=doc.get('writtenOffReason',''),by=doc.get('writtenOffBy','')),data={k:doc[k] for k in ItemData.model_fields})

    def short(row):
        doc=json.loads(row['document'])
        return dict(id=row['id'],version=row['version'],status=row['status'],number=row['number'],title=doc['title'],category=row['category'],roomId=row['room_id'],roomName=doc['roomName'],location=doc['location'],responsibleName=doc['responsibleName'],condition=row['condition'])

    def place(db,snap,workspace,data,previous):
        """Rooms come from the current timetable; an unchanged link may outlive its source."""
        prev=previous or {};room=who=''
        if data.roomId:
            found=snap.entities['room'].get(data.roomId)
            if found:room=found.name
            elif data.roomId==prev.get('roomId'):room=prev.get('roomName','')
            else:raise HTTPException(422,NO_ROOM)
        if data.responsibleId:
            p=s.staff_person(db,snap,workspace,data.responsibleId)
            if p:who=p['name']
            elif data.responsibleId==prev.get('responsibleId'):who=prev.get('responsibleName','')
            else:raise HTTPException(422,'Выберите действующего сотрудника школы.')
        if data.acquiredOn and data.acquiredOn>s.today():raise HTTPException(422,'Дата поступления не может быть в будущем.')
        return data.model_dump(mode='json')|dict(roomName=room,responsibleName=who)

    def search(doc):return ' '.join(str(doc[k]) for k in ['number','title','category','serial']).casefold()

    def insert(db,workspace,id,doc):
        stamped=s.now().isoformat()
        try:db.execute("INSERT INTO registers_inventory(id,workspace,number,number_key,version,status,room_id,responsible_id,category,condition,created,updated,search,document) VALUES (?,?,?,?,1,'active',?,?,?,?,?,?,?,?)",(id,workspace,doc['number'],doc['number'].casefold(),doc['roomId'],doc['responsibleId'],doc['category'],doc['condition'],stamped,stamped,search(doc),encoded(doc)))
        except sqlite3.IntegrityError:raise HTTPException(409,TAKEN) from None

    def assigned(db,snap,workspace,u,person):
        if person and person!=u['id']:
            target=s.account(db,person)
            if target and enabled(target,snap,workspace):s.notifications.deliver(db,[target],workspace,1,'registers.inventory')

    def rows(db,workspace,status,q,roomId,category,responsibleId,before,limit,order='seq DESC'):
        return db.execute(f"SELECT * FROM registers_inventory WHERE workspace=? AND status=? AND seq<? AND instr(search,?)>0 AND (?='' OR room_id=?) AND (?='' OR category=?) AND (?='' OR responsible_id=?) ORDER BY {order} LIMIT ?",(workspace,status,before,q.strip().casefold(),roomId,roomId,category,category,responsibleId,responsibleId,limit)).fetchall()

    def preview(db,snap,workspace,body):
        """Parse and check a whole file; nothing is stored, and the hash binds the result to this state."""
        try:raw=base64.b64decode(body.content,validate=True)
        except ValueError:raise HTTPException(422,'Не удалось прочитать содержимое файла.') from None
        if not raw or len(raw)>2_000_000:raise HTTPException(422,'Файл пустой или больше 2 МБ.')
        try:parsed=parse_file(body.filename,raw)
        except ValueError as e:raise HTTPException(422,str(e)) from None
        except Exception:raise HTTPException(422,'Не удалось прочитать файл. Сохраните его как CSV или XLSX.') from None
        table=[(n,r) for n,r in enumerate(parsed['sheets'][0]['rows'],1) if any(c.strip() for c in r)]
        if len(table)<2:raise HTTPException(422,'В файле нет предметов: нужна строка заголовков и хотя бы одна строка данных.')
        warnings=list(parsed['warnings']);index={};head=table[0][1]
        for i,name in enumerate(head):
            key=HEADERS.get(name.strip().casefold())
            if key in index:raise HTTPException(422,f'Колонка «{COLUMNS[key]}» повторяется.')
            if key:index[key]=i
            elif name.strip():warnings.append(f'Колонка «{name.strip()}» не используется и пропущена.')
        if (missing:=[COLUMNS[k] for k in REQUIRED if k not in index]):raise HTTPException(422,'В файле не хватает колонок: '+', '.join(missing)+'.')
        if len(table)-1>LIMIT:raise HTTPException(413,f'Импортируйте не более {LIMIT} предметов за один раз.')
        rooms=defaultdict(list)
        for r in snap.state.rooms:rooms[r.name.strip().casefold()].append(r)
        staff=defaultdict(list)
        for p in s.staff_list(db,snap,workspace):staff[p['name'].strip().casefold()].append(p)
        labels={v.casefold():k for k,v in CONDITIONS.items()}|{k:k for k in CONDITIONS}
        # tabular lists formula cells as (row, column) counted from 0; a line number counts rows from 1.
        formulas={(row+1,col) for row,col in parsed['sheets'][0].get('formulas',[])}
        items=[];errors=[];seen={}
        for line,r in table[1:]:
            get=lambda k:r[index[k]].strip() if k in index and index[k]<len(r) else ''
            try:
                if (used:=next((COLUMNS[k] for k,col in index.items() if (line,col) in formulas),None)):raise ValueError(f'В ячейке колонки «{used}» формула. Сохраните её как значение.')
                number=get('number');values=dict(number=number,title=get('title'),category=get('category'),location=get('location'),serial=get('serial'),note=get('note'),roomId='',responsibleId='')
                if get('room'):
                    found=rooms.get(get('room').casefold(),[])
                    if len(found)!=1:raise ValueError('Кабинет не найден в расписании.' if not found else 'Несколько кабинетов с таким названием.')
                    values['roomId']=found[0].id
                if get('responsible'):
                    found=staff.get(get('responsible').casefold(),[])
                    if len(found)!=1:raise ValueError('Сотрудник не найден среди сотрудников школы.' if not found else 'Несколько сотрудников с таким именем — назначьте вручную.')
                    values['responsibleId']=found[0]['id']
                if get('condition'):
                    if get('condition').casefold() not in labels:raise ValueError('Состояние: '+', '.join(CONDITIONS.values())+'.')
                    values['condition']=labels[get('condition').casefold()]
                if get('acquiredOn'):
                    for fmt in ('%Y-%m-%d','%d.%m.%Y'):
                        try:values['acquiredOn']=datetime.strptime(get('acquiredOn'),fmt).date();break
                        except ValueError:pass
                    else:raise ValueError('Дата поступления: ГГГГ-ММ-ДД или ДД.ММ.ГГГГ.')
                try:data=ItemData(**values)
                except ValidationError as e:
                    err=e.errors()[0]
                    raise ValueError(err['msg'].removeprefix('Value error, ') if err['type']=='value_error' else f"Проверьте колонку «{COLUMNS.get(COLUMN_OF.get(str(err['loc'][0]),str(err['loc'][0])),'')}».") from None
                key=number.casefold()
                if key in seen:raise ValueError(f'Номер повторяется в файле (строка {seen[key]}).')
                seen[key]=line
                if db.execute('SELECT 1 FROM registers_inventory WHERE workspace=? AND number_key=?',(workspace,key)).fetchone():raise ValueError(TAKEN)
                doc=place(db,snap,workspace,data,None)
                items.append(dict(line=line)|doc)
            except HTTPException as e:errors.append(dict(line=line,message=e.detail))
            except ValueError as e:errors.append(dict(line=line,message=str(e)))
        return dict(items=items,errors=errors,warnings=warnings,previewHash=hashlib.sha256(encoded([items,snap.state.revision]).encode()).hexdigest())

    @app.get('/api/registers/inventory/export')
    def export(request:Request,workspace:Workspace='school',status:str=Query(default='active',pattern='^(active|written_off)$'),q:str=Query(default='',max_length=200),roomId:str=Query(default='',max_length=100),category:str=Query(default='',max_length=60),responsibleId:str=Query(default='',max_length=100)):
        file=tempfile.SpooledTemporaryFile(mode='w+',encoding='utf-8',newline='',max_size=512*1024)
        try:
            with s.context(request,workspace) as (db,u,snap):
                s.require(db,u,snap,workspace,'inventory')
                found=rows(db,workspace,status,q,roomId,category,responsibleId,9223372036854775807,20001,'number_key')
                if len(found)>20000:raise HTTPException(422,'Более 20 000 предметов. Уточните фильтры для выгрузки.')
                file.write('﻿');writer=csv.writer(file);writer.writerow([*COLUMNS.values(),'Статус','Дата списания','Причина списания'])
                for r in found:
                    d=json.loads(r['document'])
                    writer.writerow([cell(v) for v in [d['number'],d['title'],d['category'],d['roomName'],d['location'],d['responsibleName'],CONDITIONS[d['condition']],d['acquiredOn'] or '',d['serial'],d['note'],STATES[r['status']],d.get('writtenOffOn',''),d.get('writtenOffReason','')]])
            file.seek(0)
            return StreamingResponse(iter(lambda:file.read(65536),''),media_type='text/csv; charset=utf-8',headers={'Content-Disposition':'attachment; filename="inventory.csv"','Cache-Control':'no-store'},background=BackgroundTask(file.close))
        except BaseException:file.close();raise

    @app.post('/api/registers/inventory/import/preview')
    def import_preview(body:InventoryPreview,request:Request,workspace:Workspace='school'):
        with s.context(request,workspace) as (db,u,snap):
            s.require(db,u,snap,workspace,'inventory');return preview(db,snap,workspace,body)

    @app.post('/api/registers/inventory/import')
    def import_apply(body:InventoryImport,request:Request,workspace:Workspace='school'):
        with s.context(request,workspace,True) as (db,u,snap):
            s.require(db,u,snap,workspace,'inventory');key,old=s.replay(db,u,body,'inventory-import:'+workspace)
            if old:return old
            p=preview(db,snap,workspace,body)
            if p['errors'] or not p['items']:raise HTTPException(422,'Исправьте все строки перед импортом. Ничего не сохранено.')
            if body.previewHash!=p['previewHash']:raise HTTPException(409,'Реестр или файл изменились. Выполните предпросмотр заново.')
            ids=[]
            for item in p['items']:
                doc={k:v for k,v in item.items() if k!='line'};id=uuid4().hex;insert(db,workspace,id,doc);ids.append(id)
                s.history(db,workspace,'inventory',id,u,'imported',body.reason,dict(before=None,after=doc))
            s.access.record(db,'registers.inventory_imported',workspace,detail=f'{len(ids)} · {body.reason}',principal=u)
            return s.remember(db,u,body,key,dict(count=len(ids),ids=ids))

    @app.get('/api/registers/inventory')
    def inventory(request:Request,workspace:Workspace='school',status:str=Query(default='active',pattern='^(active|written_off)$'),q:str=Query(default='',max_length=200),roomId:str=Query(default='',max_length=100),category:str=Query(default='',max_length=60),responsibleId:str=Query(default='',max_length=100),before:int=Query(default=9223372036854775807,ge=1),limit:int=Query(default=50,ge=1,le=200)):
        with s.context(request,workspace) as (db,u,snap):
            s.require(db,u,snap,workspace,'inventory')
            items,nxt=s.page(rows(db,workspace,status,q,roomId,category,responsibleId,before,limit+1),limit)
            return dict(items=[short(r) for r in items],nextBefore=nxt)

    @app.get('/api/registers/inventory/{id}')
    def item(id:str,request:Request,workspace:Workspace='school'):
        with s.context(request,workspace) as (db,u,snap):
            s.require(db,u,snap,workspace,'inventory');return public(db,snap,workspace,stored(db,workspace,id))

    @app.get('/api/registers/inventory/{id}/moves')
    def moves(id:str,request:Request,workspace:Workspace='school',before:int=Query(default=9223372036854775807,ge=1),limit:int=Query(default=20,ge=1,le=50)):
        with s.context(request,workspace) as (db,u,snap):
            s.require(db,u,snap,workspace,'inventory');stored(db,workspace,id)
            found=db.execute('SELECT * FROM registers_inventory_moves WHERE workspace=? AND item_id=? AND seq<? ORDER BY seq DESC LIMIT ?',(workspace,id,before,limit+1)).fetchall()
            items,nxt=s.page(found,limit)
            return dict(items=[dict(seq=r['seq'],movedOn=r['moved_on'],created=r['created'],actor=r['actor_name'],reason=r['reason'],source=json.loads(r['source']),target=json.loads(r['target'])) for r in items],nextBefore=nxt)

    @app.post('/api/registers/inventory')
    def create(body:ItemWrite,request:Request,workspace:Workspace='school'):
        with s.context(request,workspace,True) as (db,u,snap):
            s.require(db,u,snap,workspace,'inventory');key,old=s.replay(db,u,body,'inventory:'+workspace+':new')
            if old:return old
            if body.version!=0:raise HTTPException(422,'Новый предмет создаётся с нулевой версией.')
            doc=place(db,snap,workspace,body.data,None);id=uuid4().hex;insert(db,workspace,id,doc)
            s.history(db,workspace,'inventory',id,u,'created',body.reason,dict(before=None,after=doc));assigned(db,snap,workspace,u,doc['responsibleId'])
            return s.remember(db,u,body,key,public(db,snap,workspace,stored(db,workspace,id)))

    @app.put('/api/registers/inventory/{id}')
    def edit(id:str,body:ItemWrite,request:Request,workspace:Workspace='school'):
        with s.context(request,workspace,True) as (db,u,snap):
            s.require(db,u,snap,workspace,'inventory');key,old=s.replay(db,u,body,'inventory:'+workspace+':'+id)
            if old:return old
            row=stored(db,workspace,id);version(row,body.version)
            if row['status']!='active':raise HTTPException(409,'Списанный предмет не редактируется. Сначала восстановите его.')
            if len(body.reason)<3:raise HTTPException(422,'Укажите причину изменения.')
            before=json.loads(row['document']);data=body.data
            if data.roomId!=before['roomId'] or data.location!=before['location']:raise HTTPException(422,'Перемещение оформляется отдельным действием.')
            doc=place(db,snap,workspace,data,before)|{k:before[k] for k in KEEP if k in before}
            try:db.execute('UPDATE registers_inventory SET version=version+1,number=?,number_key=?,responsible_id=?,category=?,condition=?,updated=?,search=?,document=? WHERE id=?',(doc['number'],doc['number'].casefold(),doc['responsibleId'],doc['category'],doc['condition'],s.now().isoformat(),search(doc),encoded(doc),id))
            except sqlite3.IntegrityError:raise HTTPException(409,TAKEN) from None
            s.history(db,workspace,'inventory',id,u,'edited',body.reason,dict(before=before,after=doc))
            if doc['responsibleId']!=before['responsibleId']:assigned(db,snap,workspace,u,doc['responsibleId'])
            return s.remember(db,u,body,key,public(db,snap,workspace,stored(db,workspace,id)))

    @app.post('/api/registers/inventory/{id}/move')
    def move(id:str,body:ItemMove,request:Request,workspace:Workspace='school'):
        with s.context(request,workspace,True) as (db,u,snap):
            s.require(db,u,snap,workspace,'inventory');key,old=s.replay(db,u,body,'inventory-move:'+workspace+':'+id)
            if old:return old
            row=stored(db,workspace,id);version(row,body.version)
            if row['status']!='active':raise HTTPException(409,'Списанный предмет нельзя перемещать.')
            doc=json.loads(row['document']);day=body.movedOn or s.today()
            if (body.roomId,body.location)==(doc['roomId'],doc['location']):raise HTTPException(422,'Предмет уже находится здесь.')
            room=snap.entities['room'].get(body.roomId) if body.roomId else None
            if body.roomId and not room:raise HTTPException(422,NO_ROOM)
            if day>s.today() or (doc['acquiredOn'] and day.isoformat()<doc['acquiredOn']):raise HTTPException(422,'Дата перемещения не может быть в будущем или раньше поступления.')
            source=dict(roomId=doc['roomId'],roomName=doc['roomName'],location=doc['location']);target=dict(roomId=body.roomId,roomName=room.name if room else '',location=body.location)
            after=doc|dict(roomId=target['roomId'],roomName=target['roomName'],location=target['location']);stamped=s.now().isoformat()
            db.execute('INSERT INTO registers_inventory_moves(workspace,item_id,moved_on,created,actor_id,actor_name,reason,source,target) VALUES (?,?,?,?,?,?,?,?,?)',(workspace,id,day.isoformat(),stamped,u['id'],u['name'],body.reason,encoded(source),encoded(target)))
            db.execute('UPDATE registers_inventory SET version=version+1,room_id=?,updated=?,search=?,document=? WHERE id=?',(body.roomId,stamped,search(after),encoded(after),id))
            s.history(db,workspace,'inventory',id,u,'moved',body.reason,dict(before=source,after=target,movedOn=day.isoformat()))
            return s.remember(db,u,body,key,public(db,snap,workspace,stored(db,workspace,id)))

    @app.post('/api/registers/inventory/{id}/action')
    def action(id:str,body:ItemAction,request:Request,workspace:Workspace='school'):
        with s.context(request,workspace,True) as (db,u,snap):
            s.require(db,u,snap,workspace,'inventory');key,old=s.replay(db,u,body,'inventory-action:'+workspace+':'+id)
            if old:return old
            row=stored(db,workspace,id);version(row,body.version);doc=json.loads(row['document'])
            if body.action=='write_off':
                if row['status']!='active':raise HTTPException(409,'Предмет уже списан.')
                day=body.on or s.today()
                if day>s.today() or (doc['acquiredOn'] and day.isoformat()<doc['acquiredOn']):raise HTTPException(422,'Дата списания не может быть в будущем или раньше поступления.')
                status='written_off';off=dict(on=day.isoformat(),reason=body.reason,by=u['name']);doc|=dict(writtenOffOn=off['on'],writtenOffReason=off['reason'],writtenOffBy=off['by'])
            else:
                if u['role']!='admin':raise HTTPException(403,'Восстановить списанный предмет может администрация.')
                if row['status']!='written_off':raise HTTPException(409,'Предмет не списан.')
                # The card forgets a cancelled write-off; the history keeps its date, reason and author.
                status='active';off=dict(on=doc.get('writtenOffOn',''),reason=doc.get('writtenOffReason',''),by=doc.get('writtenOffBy',''));doc|=dict(writtenOffOn='',writtenOffReason='',writtenOffBy='')
            db.execute('UPDATE registers_inventory SET version=version+1,status=?,updated=?,document=? WHERE id=?',(status,s.now().isoformat(),encoded(doc),id))
            s.history(db,workspace,'inventory',id,u,dict(write_off='written_off',restore='restored')[body.action],body.reason,dict(before=row['status'],after=status,writtenOff=off))
            return s.remember(db,u,body,key,public(db,snap,workspace,stored(db,workspace,id)))
