"""Reminders about unfilled journals: admin settings, school-day arithmetic and a daily worker of the single API process.

One row per lesson and teacher in journal_coverage_reminders makes a reminder happen once, also across restarts.
Notices, reminder rows and the run mark are written in one transaction, so a crash never sends twice."""
from contextlib import closing,asynccontextmanager
from datetime import date,timedelta
import json,logging,sqlite3,threading
from zoneinfo import ZoneInfo
from fastapi import HTTPException,Request
from pydantic import Field,model_validator
from starlette.concurrency import run_in_threadpool
from .models import Model
from .access import WORKSPACES,now
from .calendar_ops import dates,schedule_day
from .duty_schedule import closure_on
from .journal_coverage import CoverageQuery,actionable
from .journal_periods import range_locks
from .notifications import encoded
from . import account_grants

ZONE=ZoneInfo('Asia/Qyzylorda')
HHMM=r'^([01]\d|2[0-3]):[0-5]\d$'
# Off by default: teachers still work elsewhere, so switching it on at deploy would flood them. The administrator enables it.
DEFAULT_SETTINGS=dict(enabled=False,afterDays=2,remindAt='16:00',lessonReminders=True,weeklyDigest=False,digestWeekday=4,since=None)
SCAN_DAYS=60
GRACE_DAYS=7
DIGEST_DAYS=28
KEEP_DAYS=180
SYSTEM=dict(id='system',name='Атлас',role='admin',workspaces=WORKSPACES,grants=[])
TABLES=[
    'CREATE TABLE IF NOT EXISTS journal_coverage_settings(workspace TEXT PRIMARY KEY,version INTEGER,document TEXT)',
    'CREATE TABLE IF NOT EXISTS journal_coverage_settings_history(id INTEGER PRIMARY KEY,workspace TEXT,version INTEGER,created TEXT,actor_id TEXT,actor_name TEXT,reason TEXT,before_document TEXT,after_document TEXT)',
    'CREATE TABLE IF NOT EXISTS journal_coverage_reminders(workspace TEXT,lesson_key TEXT,teacher_id TEXT,day TEXT,status TEXT,created TEXT,PRIMARY KEY(workspace,lesson_key,teacher_id))',
    'CREATE INDEX IF NOT EXISTS journal_coverage_reminders_day ON journal_coverage_reminders(workspace,day)',
    'CREATE TABLE IF NOT EXISTS journal_coverage_runs(workspace TEXT,on_date TEXT,kind TEXT,created TEXT,detail TEXT,PRIMARY KEY(workspace,on_date,kind))',
]


def tables(db):
    # Plain statements, never executescript or DROP: the tables are added to an existing school database.
    for sql in TABLES:db.execute(sql)


def settings(db,workspace):
    # A database that never used reminders has no table yet.
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='journal_coverage_settings'").fetchone():return DEFAULT_SETTINGS|dict(version=0)
    row=db.execute('SELECT version,document FROM journal_coverage_settings WHERE workspace=?',(workspace,)).fetchone()
    return DEFAULT_SETTINGS|(json.loads(row[1]) if row else {})|dict(version=row[0] if row else 0)


def school_day(state,day,everyone=None):
    """A day that counts for «N school days after the lesson»: inside the year, a teaching weekday or a chosen «день в расписании», not closed for the whole school."""
    s=state.settings
    return s.yearStart<=day<=s.yearEnd and (day.weekday()<len(s.days) or schedule_day(state,day) is not None) and closure_on(state,day,['holiday','winter'],everyone) is None


def reminder_day(state,day,n):
    """The n-th school day after the lesson day (the lesson day itself never counts); None when the year ends first."""
    everyone={c.id for c in state.classes};found=0;current=day
    for _ in range(400):
        current+=timedelta(days=1)
        if current>state.settings.yearEnd:return None
        found+=school_day(state,current,everyone)
        if found>=n:return current
    return None


def windows(state,today,config):
    """(lesson_days, digest_days): dates of lessons whose reminder falls into the catch-up window / is already due."""
    if not config['since']:return set(),set()
    first=max(state.settings.yearStart,date.fromisoformat(config['since']),today-timedelta(days=SCAN_DAYS))
    due={d:reminder_day(state,d,config['afterDays']) for d in dates(first,today-timedelta(days=1))}
    # The grace window lets a server that was down for a few days catch up without scanning the whole year.
    lessons={d for d,r in due.items() if r and today-timedelta(days=GRACE_DAYS)<=r<=today}
    digest={d for d,r in due.items() if r and r<=today and d>=today-timedelta(days=DIGEST_DAYS)}
    return lessons,digest


class ReminderSettings(Model):
    version:int=Field(ge=0)
    enabled:bool=False
    afterDays:int=Field(default=2,ge=1,le=10)
    remindAt:str=Field(default='16:00',pattern=HHMM)
    lessonReminders:bool=True
    weeklyDigest:bool=False
    digestWeekday:int=Field(default=4,ge=0,le=6)
    reason:str=Field(default='',max_length=500)
    @model_validator(mode='after')
    def something(self):
        if self.enabled and not (self.lessonReminders or self.weeklyDigest):raise ValueError('Выберите напоминания по урокам или недельную сводку.')
        return self


class CoverageReminders:
    def __init__(self,coverage):
        self.coverage,self.access=coverage,coverage.access
        self.notifications=coverage.journal.notifications
        self.stop=threading.Event();self.thread=None

    def prepare(self):
        try:
            with closing(self.access.read_connect()) as db:
                # Grant tables must exist before granted_users runs on a read-only connection: candidates() would try to write.
                if len(db.execute("SELECT name FROM sqlite_master WHERE name IN ('journal_coverage_runs','account_grant_insert')").fetchall())==2:return
        except sqlite3.OperationalError:pass
        with self.access.lock,self.access.connect() as db:
            tables(db);self.access.tables(db);self.coverage.journal.tables(db);self.notifications.tables(db);account_grants.tables(db)

    def holders(self,db,workspace,ids):
        """Active teacher accounts by the teacher ids they hold (a person may hold several)."""
        ids=set(ids);found={}
        for user in self.notifications.granted_users(db,workspace,'teacher',ids,['teacher']):
            for g in user['grants']:
                if g['workspace']==workspace and g['view']=='teacher':
                    for id in ids&set(g['entityIds']):found.setdefault(id,{})[user['id']]=user
        return {id:list(users.values()) for id,users in found.items()}

    def overdue(self,snap,workspace,start,end):
        """Unfilled lessons of the days that a teacher could still fill: not cancelled, not in a closed period, with a teacher."""
        # The whole school as the system administrator; frozen publications are judged against their own roster by calculate.
        with closing(self.access.read_connect()) as db:
            db.execute('BEGIN')
            value=self.coverage.calculate(db,SYSTEM,snap,workspace,CoverageQuery(start=start,end=end,status='all',asOf=now()))
            rows=[r for r in value['rows'] if actionable(r) and r['teacherIds']]
            if not rows:return []
            days=[d for r in rows for d in (r['date'],r['sourceDate'])]
            # A moved lesson is locked by either of its days, as journal_periods.locks does.
            closed=[(p['start'],p['end']) for p in range_locks(db,workspace,min(days),max(days))]
        return [r for r in rows if not any(a<=d<=b for a,b in closed for d in (r['date'],r['sourceDate']))]

    def process_one(self):
        """One pass over the workspaces; True when notices or skip marks were stored."""
        self.prepare();progress=False
        for workspace in WORKSPACES:
            current=now().astimezone(ZONE);today=current.date()
            with closing(self.access.read_connect()) as db:
                # Never creates a workspace: a missing one has no lessons.
                if not db.execute('SELECT 1 FROM workspaces WHERE id=?',(workspace,)).fetchone():continue
                config=settings(db,workspace)
                done={r[0] for r in db.execute('SELECT kind FROM journal_coverage_runs WHERE workspace=? AND on_date=?',(workspace,today.isoformat()))}
            if not config['enabled'] or current.strftime('%H:%M')<config['remindAt']:continue
            kinds=[]
            if config['lessonReminders'] and 'lessons' not in done:kinds.append('lessons')
            if config['weeklyDigest'] and today.weekday()==config['digestWeekday'] and 'digest' not in done:kinds.append('digest')
            if not kinds:continue
            snap=self.coverage.absences.snapshots.get(workspace)
            # Lessons wait for the next school day (they stay inside the grace window); the digest goes out on the chosen weekday.
            idle='lessons' in kinds and not school_day(snap.state,today)
            if idle:kinds.remove('lessons')
            lessons,digest=windows(snap.state,today,config)
            days=(lessons if 'lessons' in kinds else set())|(digest if 'digest' in kinds else set())
            rows=self.overdue(snap,workspace,min(days),max(days)) if days else []
            progress=self.store(workspace,snap,config,today,kinds,idle,{d.isoformat() for d in lessons},{d.isoformat() for d in digest},rows) or progress
        return progress

    def pairs(self,db,workspace,rows):
        """Rows grouped per account: {account id: (account, {lesson key: row})}, and the teacher ids without an account."""
        held=self.holders(db,workspace,{t for r in rows for t in r['teacherIds']});users={}
        for r in rows:
            for t in r['teacherIds']:
                for user in held.get(t,[]):users.setdefault(user['id'],(user,{}))[1][r['lessonKey']]=r
        return users,held

    def store(self,workspace,snap,config,today,kinds,idle,lesson_days,digest_days,rows):
        """Notices, reminder rows and run marks of one workspace in one transaction; a changed schedule, settings or lesson sends nothing."""
        stamp=now().isoformat();day=today.isoformat();progress=False
        with self.access.lock,self.access.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            revision=db.execute('SELECT revision FROM workspaces WHERE id=?',(workspace,)).fetchone()
            # No run mark is written: the next pass (60 s) retries.
            if not revision or revision[0]!=snap.state.revision or settings(db,workspace)['version']!=config['version']:return False
            done={r[0] for r in db.execute('SELECT kind FROM journal_coverage_runs WHERE workspace=? AND on_date=?',(workspace,day))}
            keys=encoded(sorted({r['lessonKey'] for r in rows}))
            # A lesson saved or published between the calculation and now is judged again tomorrow, while it is still inside the window.
            versions=dict(db.execute('SELECT lesson_key,version FROM journal_entries WHERE workspace=? AND lesson_key IN (SELECT value FROM json_each(?))',(workspace,keys)))
            current=[r for r in rows if versions.get(r['lessonKey'],0)==r['version']]
            if idle and 'lessons' not in done:
                db.execute('INSERT INTO journal_coverage_runs VALUES (?,?,?,?,?)',(workspace,day,'lessons',stamp,encoded(dict(note='not a school day'))))
            if 'lessons' in kinds and 'lessons' not in done:
                found=[r for r in current if r['date'] in lesson_days]
                known={r[0] for r in db.execute('SELECT DISTINCT lesson_key FROM journal_coverage_reminders WHERE workspace=? AND lesson_key IN (SELECT value FROM json_each(?))',(workspace,encoded(sorted({r['lessonKey'] for r in found}))))}
                found=[r for r in found if r['lessonKey'] not in known]
                users,held=self.pairs(db,workspace,found);skipped=0
                for r in found:
                    for t in r['teacherIds']:
                        skipped+=not held.get(t)
                        db.execute('INSERT OR IGNORE INTO journal_coverage_reminders VALUES (?,?,?,?,?,?)',(workspace,r['lessonKey'],t,day,'sent' if held.get(t) else 'skipped',stamp))
                for user,items in sorted(users.values(),key=lambda x:x[0]['id']):
                    first=min(items.values(),key=lambda r:(r['date'],r['start'],r['lessonKey']))
                    text=f"Не заполнено уроков: {len(items)}. Самый ранний: {date.fromisoformat(first['date']):%d.%m.%Y}, {', '.join(first['classNames']) or first['group']}, {first['subject']}. Откройте «Контроль заполнения журналов»."
                    self.notifications.deliver(db,[user],workspace,snap.state.revision,'journal.coverage_reminder',topic=f'journal-coverage:{workspace}',target=dict(title=text))
                detail=encoded(dict(sent=len(users),skipped=skipped,lessons=len(found)))
                db.execute('INSERT INTO journal_coverage_runs VALUES (?,?,?,?,?)',(workspace,day,'lessons',stamp,detail))
                if found:self.access.record(db,'journal.coverage_reminders_sent',workspace,snap.state.revision,detail=detail,principal=SYSTEM)
                progress=progress or bool(found)
            if 'digest' in kinds and 'digest' not in done:
                # Every unfilled lesson of the last weeks, also those reminded before: the digest repeats until the lesson is filled.
                found=[r for r in current if r['date'] in digest_days]
                users,held=self.pairs(db,workspace,found)
                for user,items in sorted(users.values(),key=lambda x:x[0]['id']):
                    first=min(items.values(),key=lambda r:(r['date'],r['start'],r['lessonKey']))
                    text=f"За {DIGEST_DAYS//7} недели не заполнено уроков: {len(items)}, самый ранний — {date.fromisoformat(first['date']):%d.%m.%Y}. Откройте «Контроль заполнения журналов»."
                    self.notifications.deliver(db,[user],workspace,snap.state.revision,'journal.coverage_digest',topic=f'journal-coverage-digest:{workspace}',target=dict(title=text))
                detail=encoded(dict(sent=len(users),withoutAccount=len({t for r in found for t in r['teacherIds'] if not held.get(t)}),lessons=len(found)))
                db.execute('INSERT INTO journal_coverage_runs VALUES (?,?,?,?,?)',(workspace,day,'digest',stamp,detail))
                if users:self.access.record(db,'journal.coverage_digest_sent',workspace,snap.state.revision,detail=detail,principal=SYSTEM)
                progress=progress or bool(users)
            cutoff=(today-timedelta(days=KEEP_DAYS)).isoformat()
            db.execute('DELETE FROM journal_coverage_reminders WHERE workspace=? AND day<?',(workspace,cutoff))
            db.execute('DELETE FROM journal_coverage_runs WHERE workspace=? AND on_date<?',(workspace,cutoff))
        return progress

    def start(self):
        if self.thread and self.thread.is_alive():return
        self.prepare();self.stop.clear()
        def work():
            while not self.stop.is_set():
                try:progress=self.process_one()
                except Exception as exc:
                    logging.getLogger(__name__).warning('Coverage reminder worker error: %s',type(exc).__name__)
                    progress=False
                self.stop.wait(0.05 if progress else 60)
        self.thread=threading.Thread(target=work,name='atlas-coverage-reminders',daemon=True)
        self.thread.start()

    def shutdown(self):
        self.stop.set()
        if self.thread:self.thread.join(timeout=15)

    def admin(self,user):
        if user['role']!='admin':raise HTTPException(403,'Напоминания о журнале настраивает администратор.')

    def register(self,app,Workspace):
        previous=app.router.lifespan_context
        @asynccontextmanager
        async def lifespan(application):
            async with previous(application):
                await run_in_threadpool(self.start)
                try:yield
                finally:await run_in_threadpool(self.shutdown)
        app.router.lifespan_context=lifespan

        @app.get('/api/journal/coverage/reminders')
        def get_settings(request:Request,workspace:Workspace='school'):
            user,snap=self.coverage.reports.context(request,workspace);self.admin(user);self.prepare()
            with closing(self.access.read_connect()) as db:
                db.execute('BEGIN');self.coverage.journal.recheck_account(db,user)
                runs=[dict(kind=r[0],onDate=r[1],detail=json.loads(r[2])) for r in db.execute('SELECT kind,on_date,detail FROM journal_coverage_runs WHERE workspace=? ORDER BY on_date DESC,kind LIMIT 10',(workspace,))]
                return settings(db,workspace)|dict(lastRuns=runs,today=now().astimezone(ZONE).date())

        @app.put('/api/journal/coverage/reminders')
        def put_settings(body:ReminderSettings,request:Request,workspace:Workspace='school'):
            user,snap=self.coverage.reports.context(request,workspace);self.admin(user);self.prepare()
            with self.access.lock,self.access.connect() as db:
                db.execute('BEGIN IMMEDIATE');self.coverage.journal.recheck_account(db,user)
                current=settings(db,workspace)
                if current['version']!=body.version:raise HTTPException(409,'Настройки напоминаний изменились. Обновите страницу.')
                value=body.model_dump(exclude={'version','reason'})
                # Only lessons from the day of switching on are reminded: a disabled stretch is never made up.
                value['since']=now().astimezone(ZONE).date().isoformat() if body.enabled and not current['enabled'] else current['since']
                db.execute('INSERT INTO journal_coverage_settings VALUES (?,?,?) ON CONFLICT(workspace) DO UPDATE SET version=excluded.version,document=excluded.document',(workspace,body.version+1,encoded(value)))
                db.execute('INSERT INTO journal_coverage_settings_history (workspace,version,created,actor_id,actor_name,reason,before_document,after_document) VALUES (?,?,?,?,?,?,?,?)',
                    (workspace,body.version+1,now().isoformat(),user['id'],user['name'],body.reason,encoded({k:current[k] for k in value}),encoded(value)))
                self.access.record(db,'journal.coverage_reminders_saved',workspace,snap.state.revision,detail=encoded(value))
                return value|dict(version=body.version+1)
