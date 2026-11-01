"""Staff attendance records; all writes preserve teaching state and its journal."""
from calendar import monthrange
from contextlib import closing,contextmanager
from datetime import date,timedelta
import hashlib,json,sqlite3
from uuid import uuid4
from zoneinfo import ZoneInfo
from fastapi import HTTPException
from .access import now
from .notifications import encoded
from .portal_cache import PortalSnapshots
from .school_forms import enabled,grant_ids
from .staff_time_models import WORK,minutes

STAFF={'admin','editor','teacher','staff'}
ZONE=ZoneInfo('Asia/Qyzylorda')

def today():return now().astimezone(ZONE).date()
def stamp():return now().isoformat()
def version(item,expected):
    if item['version']!=expected:raise HTTPException(409,'Данные изменились. Обновите табель перед действием.')
def bounds(month):
    try:
        start=date.fromisoformat(month+'-01');end=start.replace(day=monthrange(start.year,start.month)[1])
        if len(month)!=7:raise ValueError()
        return start,end
    except ValueError:raise HTTPException(422,'Укажите месяц в формате ГГГГ-ММ.')
def dates(start,end):
    for i in range((end-start).days+1):yield start+timedelta(days=i)
def fingerprint(value):return hashlib.sha256(encoded(value).encode()).hexdigest()
def totals(doc):
    result=dict(workMinutes=0,absenceMinutes=0,fullDay=doc['fullDay'],byCode={})
    for s in doc['segments']:
        duration=minutes(s['end'])-minutes(s['start']);key='workMinutes' if s['code'] in WORK else 'absenceMinutes'
        result[key]+=duration;result['byCode'][s['code']]=result['byCode'].get(s['code'],0)+duration
    return result


def tables(db):
    db.executescript('''
    CREATE TABLE IF NOT EXISTS staff_time_settings(workspace TEXT PRIMARY KEY,version INTEGER,self_report INTEGER);
    CREATE TABLE IF NOT EXISTS staff_time_days(id TEXT PRIMARY KEY,workspace TEXT,employee_id TEXT,on_date TEXT,version INTEGER,status TEXT,created TEXT,updated TEXT,document TEXT,UNIQUE(workspace,employee_id,on_date));
    CREATE INDEX IF NOT EXISTS staff_time_month ON staff_time_days(workspace,on_date,employee_id);
    CREATE INDEX IF NOT EXISTS staff_time_owner ON staff_time_days(workspace,employee_id,on_date);
    CREATE TABLE IF NOT EXISTS staff_time_leave(seq INTEGER PRIMARY KEY AUTOINCREMENT,id TEXT UNIQUE,workspace TEXT,employee_id TEXT,start_date TEXT,end_date TEXT,start_min INTEGER,end_min INTEGER,version INTEGER,status TEXT,created TEXT,updated TEXT,document TEXT);
    CREATE INDEX IF NOT EXISTS staff_leave_owner ON staff_time_leave(workspace,employee_id,start_date,end_date);
    CREATE INDEX IF NOT EXISTS staff_leave_month ON staff_time_leave(workspace,start_date,end_date);
    CREATE TABLE IF NOT EXISTS staff_time_periods(workspace TEXT,month TEXT,version INTEGER,status TEXT,updated TEXT,last_closed INTEGER,PRIMARY KEY(workspace,month));
    CREATE TABLE IF NOT EXISTS staff_time_archives(workspace TEXT,month TEXT,version INTEGER,employee_id TEXT,created TEXT,document TEXT,PRIMARY KEY(workspace,month,version,employee_id));
    CREATE TABLE IF NOT EXISTS staff_time_history(seq INTEGER PRIMARY KEY AUTOINCREMENT,workspace TEXT,entity TEXT,entity_id TEXT,employee_id TEXT,created TEXT,actor_id TEXT,actor_name TEXT,action TEXT,reason TEXT,document TEXT);
    CREATE INDEX IF NOT EXISTS staff_time_history_item ON staff_time_history(workspace,entity,entity_id,seq);
    CREATE TABLE IF NOT EXISTS staff_time_requests(seq INTEGER PRIMARY KEY AUTOINCREMENT,user_id TEXT,request_id TEXT,fingerprint TEXT,result TEXT,UNIQUE(user_id,request_id));
    CREATE INDEX IF NOT EXISTS staff_time_request_owner ON staff_time_requests(user_id,seq);
    ''')


class StaffTime:
    def __init__(self,access,notifications,stamp):
        self.access,self.notifications=access,notifications
        self.snapshots=PortalSnapshots(access.read,stamp)

    def prepare(self):
        with closing(self.access.read_connect()) as db:
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='staff_time_request_owner'").fetchone():return
        with self.access.lock,self.access.connect() as db:tables(db);self.notifications.tables(db)

    @contextmanager
    def context(self,request,workspace,write=False):
        user=request.state.user
        if not user or user['role'] not in STAFF:raise HTTPException(403,'Табель доступен только сотрудникам школы.')
        snap=self.snapshots.get(workspace)
        if not enabled(user,snap,workspace):raise HTTPException(403,'Нет доступа к табелю этого пространства.')
        self.prepare()
        def check(db):
            row=db.execute('SELECT * FROM accounts WHERE id=? AND active=1',(user['id'],)).fetchone()
            rev=db.execute('SELECT revision FROM workspaces WHERE id=?',(workspace,)).fetchone()
            if not row or self.access.public(row)!=user or not rev or rev[0]!=snap.state.revision:raise HTTPException(409,'Аккаунт или школьные связи изменились. Обновите страницу.')
            db.row_factory=sqlite3.Row
        if write:
            with self.access.lock,self.access.connect() as db:
                db.execute('BEGIN IMMEDIATE');check(db);yield db,user,snap
        else:
            with closing(self.access.read_connect()) as db:
                db.execute('BEGIN');check(db);yield db,user,snap

    def admin(self,user):
        if user['role']!='admin':raise HTTPException(403,'Это действие выполняет администрация.')

    def person(self,db,id,snap,workspace):
        row=db.execute('SELECT * FROM accounts WHERE id=?',(id,)).fetchone()
        person=self.access.public(row) if row else None
        return person if person and person['role'] in STAFF and enabled(person,snap,workspace) else None

    def scope(self,user,id):
        if user['role']!='admin' and user['id']!=id:raise HTTPException(404,'Запись сотрудника недоступна.')

    def settings(self,db,workspace):
        row=db.execute('SELECT version,self_report FROM staff_time_settings WHERE workspace=?',(workspace,)).fetchone()
        return dict(version=row[0],selfReport=bool(row[1])) if row else dict(version=0,selfReport=False)

    def period(self,db,workspace,month):
        bounds(month)
        row=db.execute('SELECT * FROM staff_time_periods WHERE workspace=? AND month=?',(workspace,month)).fetchone()
        return dict(row) if row else dict(workspace=workspace,month=month,version=0,status='open',updated=None,last_closed=None)

    def open_dates(self,db,workspace,start,end=None):
        for month in {d.strftime('%Y-%m') for d in dates(start,end or start)}:
            if self.period(db,workspace,month)['status']=='closed':raise HTTPException(409,'Месяц закрыт. Администратор должен открыть его для исправления.')

    def replay(self,db,user,body,operation):
        key=fingerprint([operation,body.model_dump(mode='json')])
        row=db.execute('SELECT fingerprint,result FROM staff_time_requests WHERE user_id=? AND request_id=?',(user['id'],body.requestId)).fetchone()
        if row:
            if row['fingerprint']!=key:raise HTTPException(409,'Этот идентификатор запроса уже использован.')
            return key,json.loads(row['result'])
        return key,None

    def remember(self,db,user,body,key,result):
        db.execute('INSERT INTO staff_time_requests(user_id,request_id,fingerprint,result) VALUES (?,?,?,?)',(user['id'],body.requestId,key,encoded(result)))
        db.execute('DELETE FROM staff_time_requests WHERE user_id=? AND seq NOT IN (SELECT seq FROM staff_time_requests WHERE user_id=? ORDER BY seq DESC LIMIT 100)',(user['id'],user['id']))
        return result

    def history(self,db,workspace,user,entity,id,owner,action,reason,document):
        db.execute('INSERT INTO staff_time_history(workspace,entity,entity_id,employee_id,created,actor_id,actor_name,action,reason,document) VALUES (?,?,?,?,?,?,?,?,?,?)',(workspace,entity,id,owner,stamp(),user['id'],user['name'],action,reason,encoded(document)))

    def notify(self,db,user,snap,workspace,owner,version,action,review=False):
        if review:people=[self.access.public(row) for row in db.execute("SELECT * FROM accounts WHERE role='admin' AND active=1")]
        else:people=[self.person(db,owner,snap,workspace)]
        self.notifications.deliver(db,[p for p in people if p and p['id']!=user['id']],workspace,version,action)

    def notify_schedulers(self,db,user,snap,workspace,owner,version,action):
        """A teacher's leave was approved or cancelled: those who keep the timetable learn that an absence may need entering. No names, no reason."""
        person=self.person(db,owner,snap,workspace)
        if not person or not (grant_ids(person,workspace,'teacher') & snap.entities['teacher'].keys()):return
        people=[self.access.public(row) for row in db.execute("SELECT * FROM accounts WHERE active=1 AND role IN ('admin','editor')")]
        people=[p for p in people if p['id']!=user['id'] and (p['role']=='admin' or workspace in p['workspaces'])]
        self.notifications.deliver(db,people,workspace,version,'timetable.leave_approved' if action=='approve' else 'timetable.leave_cancelled')

    def day(self,db,workspace,id):
        row=db.execute('SELECT * FROM staff_time_days WHERE id=? AND workspace=?',(id,workspace)).fetchone()
        if not row:raise HTTPException(404,'Запись табеля не найдена.')
        return dict(row)

    def leave(self,db,workspace,id):
        row=db.execute('SELECT * FROM staff_time_leave WHERE id=? AND workspace=?',(id,workspace)).fetchone()
        if not row:raise HTTPException(404,'Заявка не найдена.')
        return dict(row)

    def public_day(self,row):
        d=json.loads(row['document'])
        return dict(id=row['id'],employeeId=row['employee_id'],onDate=row['on_date'],version=row['version'],status=row['status'],updated=row['updated'],document=d,totals=totals(d))

    def public_leave(self,row):
        return dict(id=row['id'],employeeId=row['employee_id'],startDate=row['start_date'],endDate=row['end_date'],version=row['version'],status=row['status'],updated=row['updated'],**json.loads(row['document']))

    def work_conflicts(self,db,workspace,employee,onDate,doc):
        requests=db.execute("SELECT * FROM staff_time_leave WHERE workspace=? AND employee_id=? AND status='approved' AND start_date<=? AND end_date>=?",(workspace,employee,onDate,onDate)).fetchall()
        return [r['id'] for r in requests if any(s['code'] in WORK and minutes(s['start'])<r['end_min'] and r['start_min']<minutes(s['end']) for s in doc['segments'])]

    def leave_preview(self,db,workspace,row):
        conflicts=[];facts=[]
        for other in db.execute("SELECT * FROM staff_time_leave WHERE workspace=? AND employee_id=? AND status='approved' AND start_date<=? AND end_date>=? AND start_min<? AND end_min>? AND id!=?",(workspace,row['employee_id'],row['end_date'],row['start_date'],row['end_min'],row['start_min'],row['id'])):
            facts.append(dict(other));conflicts.append(dict(date=other['start_date'],reason='Уже есть согласованное отсутствие.'))
        for entry in db.execute("SELECT * FROM staff_time_days WHERE workspace=? AND employee_id=? AND on_date BETWEEN ? AND ? AND status='approved'",(workspace,row['employee_id'],row['start_date'],row['end_date'])):
            facts.append(dict(entry));d=json.loads(entry['document'])
            if any(s['code'] in WORK and minutes(s['start'])<row['end_min'] and row['start_min']<minutes(s['end']) for s in d['segments']):conflicts.append(dict(date=entry['on_date'],reason='В табеле подтверждены рабочие часы в это время.'))
        return dict(previewHash=fingerprint([row,facts]),conflictCount=len(conflicts),conflicts=conflicts[:40],calendarDays=(date.fromisoformat(row['end_date'])-date.fromisoformat(row['start_date'])).days+1)

    def month_data(self,db,workspace,month,employee=''):
        start,end=bounds(month);suffix=' AND employee_id=?' if employee else '';params=[workspace,start.isoformat(),end.isoformat()]+([employee] if employee else [])
        days=[dict(r) for r in db.execute('SELECT * FROM staff_time_days WHERE workspace=? AND on_date BETWEEN ? AND ?'+suffix+' ORDER BY employee_id,on_date',params)]
        requests=[dict(r) for r in db.execute('SELECT * FROM staff_time_leave WHERE workspace=? AND end_date>=? AND start_date<=?'+suffix+' ORDER BY employee_id,start_date,id',params)]
        return days,requests

    def report(self,days,requests,month):
        start,end=bounds(month);people={}
        def person(id,name):return people.setdefault(id,dict(employeeId=id,name=name,workMinutes=0,absenceMinutes=0,recordedDays=0,fullAbsenceDays=0,pendingDays=0,approvedLeaveDays=set(),pendingRequests=0))
        for entry in days:
            d=json.loads(entry['document']);p=person(entry['employee_id'],d['employeeName'])
            if entry['status']=='approved':
                t=totals(d);p['workMinutes']+=t['workMinutes'];p['absenceMinutes']+=t['absenceMinutes'];p['recordedDays']+=1;p['fullAbsenceDays']+=int(bool(t['fullDay']))
            elif entry['status']!='void':p['pendingDays']+=1
        for request in requests:
            d=json.loads(request['document']);p=person(request['employee_id'],d['employeeName'])
            if request['status']=='submitted':p['pendingRequests']+=1
            if request['status']=='approved':p['approvedLeaveDays'].update(day.isoformat() for day in dates(max(start,date.fromisoformat(request['start_date'])),min(end,date.fromisoformat(request['end_date']))))
        return [p|dict(approvedLeaveDays=len(p['approvedLeaveDays'])) for p in people.values()]

    def close_preview(self,db,workspace,month):
        period=self.period(db,workspace,month);days,requests=self.month_data(db,workspace,month)
        summary=self.report(days,requests,month)
        return dict(version=period['version'],previewHash=fingerprint([period,days,requests]),employees=len(summary),approvedDays=sum(x['recordedDays'] for x in summary),pendingDays=sum(x['pendingDays'] for x in summary),pendingRequests=sum(x['pendingRequests'] for x in summary)),days,requests,summary

    def register(self,app,Workspace):
        from .staff_time_routes import register
        register(self,app,Workspace)
