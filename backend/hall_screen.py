"""Hall TV screens: today's timetable changes and timed announcements, no personal data."""
from collections import OrderedDict,deque
from contextlib import closing,contextmanager
from datetime import date,datetime,timedelta,timezone
from threading import Lock,RLock
from time import monotonic
from typing import Literal
from zoneinfo import ZoneInfo
import hashlib,json,re,sqlite3

from fastapi import HTTPException
from pydantic import Field,model_validator
from .access import now
from .cycles import pattern_on
from .models import Model
from .notifications import encoded
from .portal_cache import PortalSnapshots
from .resources import room_ids
from .school_forms import enabled

ZONE=ZoneInfo('Asia/Qyzylorda')
MAX_SCREENS=20
MAX_ITEMS=300
MAX_ANNOUNCEMENTS=100
TOKEN=re.compile(r'^[A-Za-z0-9_-]{32,100}$')
NAMES={'full':'Полностью','short':'Фамилия и инициалы','hidden':'Не показывать'}
# The only keys a TV may receive for a timetable change: no pupils, groups, reasons or ids.
ITEM_KEYS={'kind','period','start','end','classes','subject','previousSubject','absent','teachers','rooms','previousRooms','movedTo','movedFrom'}


def iso(value):
    """Fixed-width UTC text, so stored times compare correctly as strings."""
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def short_name(name):
    parts=name.split()
    return parts[0]+' '+' '.join(p[0].upper()+'.' for p in parts[1:3]) if len(parts)>=2 else name


def tables(db):
    db.executescript('''
    CREATE TABLE IF NOT EXISTS hall_screens(id TEXT PRIMARY KEY,workspace TEXT NOT NULL,version INTEGER NOT NULL,status TEXT NOT NULL,created TEXT,updated TEXT,
        document TEXT NOT NULL,token TEXT UNIQUE,token_issued TEXT,token_expires TEXT,last_seen TEXT);
    CREATE INDEX IF NOT EXISTS hall_screens_ws ON hall_screens(workspace,status);
    CREATE TABLE IF NOT EXISTS hall_announcements(seq INTEGER PRIMARY KEY AUTOINCREMENT,id TEXT UNIQUE,workspace TEXT,version INTEGER,status TEXT,starts TEXT,ends TEXT,
        author_id TEXT,author_name TEXT,created TEXT,updated TEXT,document TEXT);
    CREATE INDEX IF NOT EXISTS hall_announcements_window ON hall_announcements(workspace,status,ends);
    CREATE TABLE IF NOT EXISTS hall_history(seq INTEGER PRIMARY KEY AUTOINCREMENT,workspace TEXT,entity TEXT,entity_id TEXT,actor_id TEXT,actor_name TEXT,created TEXT,action TEXT,reason TEXT,document TEXT);
    CREATE INDEX IF NOT EXISTS hall_history_entity ON hall_history(workspace,entity,entity_id,seq);
    CREATE TABLE IF NOT EXISTS hall_requests(seq INTEGER PRIMARY KEY AUTOINCREMENT,user_id TEXT,request_id TEXT,fingerprint TEXT,result TEXT,UNIQUE(user_id,request_id));
    ''')


class ScreenData(Model):
    name:str=Field(min_length=1,max_length=80)
    buildingId:str|None=Field(default=None,max_length=100)
    teacherNames:Literal['full','short','hidden']='short'
    tomorrowFrom:int|None=Field(default=14,ge=0,le=23)
    pageSeconds:int=Field(default=15,ge=8,le=60)


class NewScreen(ScreenData):
    requestId:str=Field(pattern=r'^[a-f0-9-]{36}$')


class ScreenEdit(ScreenData):
    version:int=Field(ge=1)


class LinkIssue(Model):
    version:int=Field(ge=1)
    expiresOn:date|None=None


class Versioned(Model):
    version:int=Field(ge=1)
    reason:str=Field(default='',max_length=500)


class AnnouncementData(Model):
    title:str=Field(min_length=1,max_length=120)
    text:str=Field(default='',max_length=600)
    startsAt:datetime
    endsAt:datetime
    visible:bool=True
    screenIds:list[str]=Field(default_factory=list,max_length=20)
    @model_validator(mode='after')
    def valid_window(self):
        # Times typed without an offset are school time.
        if self.startsAt.tzinfo is None:self.startsAt=self.startsAt.replace(tzinfo=ZONE)
        if self.endsAt.tzinfo is None:self.endsAt=self.endsAt.replace(tzinfo=ZONE)
        try:span=self.endsAt-self.startsAt
        except (ValueError,OverflowError):raise ValueError('Дата выходит за допустимые границы.')
        if span<=timedelta(0):raise ValueError('Окончание показа должно быть позже начала.')
        if span>timedelta(days=92):raise ValueError('Объявление показывается не дольше 92 дней.')
        if len(set(self.screenIds))!=len(self.screenIds):raise ValueError('Экраны не должны повторяться.')
        return self


class NewAnnouncement(AnnouncementData):
    requestId:str=Field(pattern=r'^[a-f0-9-]{36}$')


class AnnouncementEdit(AnnouncementData):
    version:int=Field(ge=1)


def day_items(state,day,rows):
    """Public changes of one date as (items, hidden count). Only whitelisted row fields are read: never pupils, groups, reasons or change names."""
    placements={p.id:p for p in state.placements};loads={w.id:w for w in state.workloads};profiles={p.id:p for p in state.profiles}
    subjects={s.id:s.name for s in state.subjects};rooms={r.id:r for r in state.rooms};changes={c.id:c for c in state.datedLessons}
    order=[(c.id,c.name) for c in state.classes];names=dict(order)
    def label(ids):
        found=[n for id,n in order if id in ids]
        return ', '.join(found[:4])+(f' +{len(found)-4}' if len(found)>4 else '')
    def room_names(ids):return ', '.join(rooms[r].name for r in ids if r in rooms)
    items,closed=[],{}
    def close(ids,row):
        for id in ids:
            start,end=closed.get(id,(row['start'],row['end']));closed[id]=(min(start,row['start']),max(end,row['end']))
    for row in rows:
        if row['activity']!='lesson' or not row['originalClassIds']:continue
        status=row['status'];p=placements.get(row['placementId']);w=loads.get(row['workloadId']);change=changes.get(row['changeId'])
        slot=None
        if p and w and w.profileId in profiles:
            periods=profiles[w.profileId].for_day(p.day)
            slot=periods[p.period] if 0<=p.period<len(periods) else None
        previous=room_ids(w,p.roomId) if p and w else []
        buildings={rooms[r].buildingId for r in [*row['roomIds'],*previous] if r in rooms and rooms[r].buildingId}|({row['buildingId']} if row.get('buildingId') else set())
        item=dict(period=slot.label if slot and row['date']==row['sourceDate'] and row['start']==slot.start else '',start=row['start'],end=row['end'],
                  classes=label(row['originalClassIds']),subject=row['subject'],rooms=row['rooms'],_buildings=buildings)
        if status=='cancelled':
            if change and change.onDate!=day:items.append(item|dict(kind='moved_out',movedTo=change.onDate.isoformat()))
            elif change and change.kind=='cancel':items.append(item|dict(kind='cancelled'))
            else:close(row['originalClassIds'],row)
            continue
        if row['excludedClassIds']:close(row['excludedClassIds'],row)
        if status=='scheduled':continue
        if w and row['subjectId']!=w.subjectId:item['previousSubject']=subjects.get(w.subjectId,'')
        if w and set(row['roomIds'])!=set(previous):item['previousRooms']=room_names(previous)
        if status=='added':items.append(item|dict(kind='added',_teachers=row['teacherIds']))
        elif row['date']!=row['sourceDate']:items.append(item|dict(kind='moved_in',movedFrom=row['sourceDate'],_teachers=row['teacherIds']))
        else:
            absent=[t for t in row['originalTeacherIds'] if t not in row['teacherIds']];substitutes=[t for t in row['teacherIds'] if t not in row['originalTeacherIds']]
            if absent or substitutes:items.append(item|dict(kind='substitution',_absent=absent,_teachers=substitutes))
            elif 'previousSubject' in item:items.append(item|dict(kind='subject'))
            elif 'previousRooms' in item:items.append(item|dict(kind='room'))
            elif slot and (row['start'],row['end'])!=(slot.start,slot.end):items.append(item|dict(kind='time'))
    for id,(start,end) in closed.items():
        if id in names:items.append(dict(kind='closed',period='',start=start,end=end,classes=names[id],subject='',rooms='',_buildings=set()))
    items.sort(key=lambda i:(i['start'],i['classes'],i['kind']))
    return items[:MAX_ITEMS],max(0,len(items)-MAX_ITEMS)


def next_teaching_day(state,day):
    for n in range(1,8):
        found=day+timedelta(days=n)
        if pattern_on(state,found) is not None and found.weekday()<len(state.settings.days):return found
    return None


def present(items,state,data,local,today):
    """Public items of one day for one screen: building filter, finished lessons dropped, teacher names by the screen's mode."""
    building=data.get('buildingId');building=building if building in {b.id for b in state.campus.buildings} else None
    mode=data.get('teacherNames','short');teachers={t.id:t.name for t in state.teachers};clock=local.strftime('%H:%M')
    def people(ids):
        found=[teachers[i] for i in ids if i in teachers]
        return ', '.join(short_name(n) if mode=='short' else n for n in found)
    result=[]
    for raw in items:
        if building and raw['_buildings'] and building not in raw['_buildings']:continue
        if today and raw['end']<=clock:continue
        item=dict(raw)
        if mode!='hidden':
            if '_absent' in raw:item['absent']=people(raw['_absent'])
            if '_teachers' in raw:item['teachers']=people(raw['_teachers'])
        result.append({k:v for k,v in item.items() if k in ITEM_KEYS})
    return result


class Limiter:
    """Process-local limits for the public endpoint: requests per minute and invalid tokens per ten minutes, per client address."""
    def __init__(self,clock=monotonic):
        self.clock,self.lock=clock,Lock()
        self.hits,self.misses=OrderedDict(),OrderedDict()

    def add(self,table,client,moment):
        queue=table.setdefault(client,deque());queue.append(moment);table.move_to_end(client)
        while len(table)>2048:table.popitem(last=False)

    def check(self,client):
        moment=self.clock()
        with self.lock:
            hits,misses=self.hits.get(client,deque()),self.misses.get(client,deque())
            while hits and hits[0]<=moment-60:hits.popleft()
            while misses and misses[0]<=moment-600:misses.popleft()
            if len(hits)>=30 or len(misses)>=10:
                raise HTTPException(429,'Слишком много запросов. Повторите позже.',headers={'Retry-After':'60','Cache-Control':'no-store'})
            self.add(self.hits,client,moment)

    def failed(self,client):
        with self.lock:self.add(self.misses,client,self.clock())

    def reset(self):
        with self.lock:self.hits.clear();self.misses.clear()


class HallScreen:
    def __init__(self,access,notifications,stamp):
        self.access,self.notifications=access,notifications
        self.snapshots=PortalSnapshots(access.read,stamp)
        self.cache,self.cache_lock=OrderedDict(),RLock()
        self.limiter=Limiter()

    def instant(self):return now()

    def prepare(self):
        with closing(self.access.read_connect()) as db:
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='hall_history_entity'").fetchone():return
        with self.access.lock,self.access.connect() as db:tables(db);self.notifications.tables(db)

    @contextmanager
    def context(self,request,workspace,write=False):
        user=request.state.user
        if not user:raise HTTPException(403,'Раздел доступен после входа.')
        snap=self.snapshots.get(workspace)
        if not enabled(user,snap,workspace):raise HTTPException(403,'Нет доступа к этому пространству.')
        self.prepare()
        def check(db):
            row=db.execute('SELECT * FROM accounts WHERE id=? AND active=1',(user['id'],)).fetchone();rev=db.execute('SELECT revision FROM workspaces WHERE id=?',(workspace,)).fetchone()
            if not row or self.access.public(row)!=user or not rev or rev[0]!=snap.state.revision:raise HTTPException(409,'Доступ или данные школы изменились. Обновите страницу.')
            db.row_factory=sqlite3.Row
        if write:
            with self.access.lock,self.access.connect() as db:db.execute('BEGIN IMMEDIATE');check(db);yield db,user,snap
        else:
            with closing(self.access.read_connect()) as db:db.execute('BEGIN');check(db);yield db,user,snap

    def history(self,db,workspace,user,entity,id,action,reason,document):
        # The document never holds a link token or its digest.
        db.execute('INSERT INTO hall_history(workspace,entity,entity_id,actor_id,actor_name,created,action,reason,document) VALUES (?,?,?,?,?,?,?,?,?)',
                   (workspace,entity,id,user['id'],user['name'],iso(self.instant()),action,reason,encoded(document)))

    def replay(self,db,user,body):
        key=hashlib.sha256(encoded(body.model_dump(mode='json')).encode()).hexdigest()
        row=db.execute('SELECT fingerprint,result FROM hall_requests WHERE user_id=? AND request_id=?',(user['id'],body.requestId)).fetchone()
        if row:
            if row['fingerprint']!=key:raise HTTPException(409,'Идентификатор уже использован другим действием.')
            return key,json.loads(row['result'])
        return key,None

    def remember(self,db,user,body,key,result):
        db.execute('INSERT INTO hall_requests(user_id,request_id,fingerprint,result) VALUES (?,?,?,?)',(user['id'],body.requestId,key,encoded(result)))
        db.execute('DELETE FROM hall_requests WHERE user_id=? AND seq NOT IN (SELECT seq FROM hall_requests WHERE user_id=? ORDER BY seq DESC LIMIT 100)',(user['id'],user['id']))
        return result

    def notify_admins(self,db,workspace,version,action,skip):
        rows=[r for r in db.execute("SELECT * FROM accounts WHERE active=1 AND role='admin'") if r['id']!=skip]
        self.notifications.deliver(db,[self.access.public(r) for r in rows],workspace,version,action)

    def screen(self,db,workspace,id):
        row=db.execute('SELECT * FROM hall_screens WHERE workspace=? AND id=?',(workspace,id)).fetchone()
        if not row:raise HTTPException(404,'Экран не найден.')
        return dict(row)|dict(data=json.loads(row['document']))

    def announcement(self,db,workspace,id):
        row=db.execute('SELECT * FROM hall_announcements WHERE workspace=? AND id=?',(workspace,id)).fetchone()
        if not row:raise HTTPException(404,'Объявление не найдено.')
        return dict(row)|dict(data=json.loads(row['document']))

    def summary(self,row,snap,instant):
        data=json.loads(row['document']);buildings={b.id:b.name for b in snap.state.campus.buildings};building=data.get('buildingId')
        expires=row['token_expires']
        status='none' if not row['token'] else 'expired' if expires and expires<=iso(instant) else 'active'
        # The link works through the whole last day (school time), so it ends at the next midnight.
        last=(datetime.fromisoformat(expires).astimezone(ZONE)-timedelta(days=1)).date().isoformat() if expires else None
        return dict(id=row['id'],version=row['version'],status=row['status'],created=row['created'],updated=row['updated'],**data,
                    buildingName=buildings.get(building,'') if building else '',buildingMissing=bool(building) and building not in buildings,
                    link=dict(status=status,issued=row['token_issued'] if row['token'] else None,expires=expires if row['token'] else None,expiresOn=last if row['token'] else None),
                    lastSeen=row['last_seen'])

    def announcement_summary(self,row,instant):
        data=json.loads(row['document']);moment=iso(instant)
        state=('withdrawn' if row['status']=='withdrawn' else 'hidden' if not data['visible'] else 'scheduled' if row['starts']>moment
               else 'ended' if row['ends']<=moment else 'showing')
        return dict(id=row['id'],version=row['version'],state=state,title=data['title'],text=data['text'],startsAt=row['starts'],endsAt=row['ends'],
                    visible=data['visible'],screenIds=data['screenIds'],author=row['author_name'],updated=row['updated'],withdrawReason=data.get('withdrawReason',''))

    def announcements(self,db,workspace,instant):
        """Candidates for a screen right now; payload() still checks visibility and screen choice."""
        moment=iso(instant)
        return db.execute("SELECT * FROM hall_announcements WHERE workspace=? AND status='active' AND starts<=? AND ends>?",(workspace,moment,moment)).fetchall()

    def items(self,snap,day):
        """Changes of a date, computed once per timetable state while cached."""
        key=id(snap.state),day
        with self.cache_lock:
            found=self.cache.get(key)
            if found and found[0] is snap.state:
                self.cache.move_to_end(key);return found[1]
        value=day_items(snap.state,day,snap.lesson_rows(day))
        with self.cache_lock:
            self.cache[key]=snap.state,value;self.cache.move_to_end(key)
            while len(self.cache)>8:self.cache.popitem(last=False)
        return value

    def payload(self,snap,row,announcements,instant):
        """Everything a TV receives. Nothing here may carry ids, versions, authors, pupils, groups or reasons."""
        state=snap.state;data=json.loads(row['document']);local=instant.astimezone(ZONE);today=local.date()
        def day(found,label,current):
            items,more=self.items(snap,found)
            return dict(date=found.isoformat(),label=label,items=present(items,state,data,local,current),more=more)
        days=[day(today,'Сегодня',True)]
        if data.get('tomorrowFrom') is not None and local.hour>=data['tomorrowFrom']:
            found=next_teaching_day(state,today)
            if found:days.append(day(found,'Завтра' if found==today+timedelta(days=1) else state.settings.days[found.weekday()],False))
        moment=iso(instant);shown=[]
        for a in announcements:
            doc=json.loads(a['document'])
            if a['status']=='active' and doc['visible'] and a['starts']<=moment<a['ends'] and (not doc['screenIds'] or row['id'] in doc['screenIds']):shown.append((a['starts'],a['seq'],doc))
        shown.sort(key=lambda s:s[:2],reverse=True)
        return dict(school=state.settings.name,screen=data['name'],serverTime=instant.astimezone(timezone.utc).isoformat(),utcOffsetMinutes=int(local.utcoffset().total_seconds()//60),
                    pageSeconds=data['pageSeconds'],refreshSeconds=60,days=days,announcements=[dict(title=d['title'],text=d['text']) for _,_,d in shown[:10]])

    def register(self,app,Workspace):
        from .hall_screen_routes import register
        register(self,app,Workspace)
