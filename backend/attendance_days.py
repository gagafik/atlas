"""Daily confirmation of the class attendance grid: storage, write-through invalidation and pure helpers.

No route code and no import of the journal: attendance_projection calls marks_changed lazily inside the
journal write transaction. A confirmation is a statement of the class teacher, never a lock."""
from .access import actor,now
from .notifications import encoded

STATUSES=('confirmed','changed','revoked')
MARKED=('present','absent','late','excused')
UNMARKED=('unmarked',0)


def tables(db):
    # Plain statements, never executescript or DROP: the tables are added to an existing school database.
    db.execute('''CREATE TABLE IF NOT EXISTS attendance_day_confirmations (workspace TEXT,class_id TEXT,day TEXT,version INTEGER,status TEXT,
        updated TEXT,actor_id TEXT,actor_name TEXT,pupils TEXT,open_lessons INTEGER,unmarked INTEGER,PRIMARY KEY(workspace,class_id,day))''')
    db.execute('CREATE INDEX IF NOT EXISTS attendance_day_confirmation_day ON attendance_day_confirmations(workspace,day)')
    db.execute('''CREATE TABLE IF NOT EXISTS attendance_day_history (id INTEGER PRIMARY KEY,workspace TEXT,class_id TEXT,day TEXT,version INTEGER,
        created TEXT,actor_id TEXT,actor_name TEXT,action TEXT,comment TEXT,request_id TEXT)''')
    db.execute('CREATE INDEX IF NOT EXISTS attendance_day_history_day ON attendance_day_history(workspace,class_id,day,id)')
    db.execute('''CREATE TABLE IF NOT EXISTS attendance_grid_requests (workspace TEXT,actor_id TEXT,request_id TEXT,request_hash TEXT,created TEXT,result TEXT,
        PRIMARY KEY(workspace,actor_id,request_id))''')
    db.execute('''CREATE TABLE IF NOT EXISTS attendance_day_reminders (workspace TEXT,class_id TEXT,day TEXT,teacher_id TEXT,status TEXT,created TEXT,
        PRIMARY KEY(workspace,class_id,day,teacher_id))''')


def exists(db):
    # Homework publications reach the journal without necessarily preparing every table on an old database.
    return bool(db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='attendance_day_confirmations'").fetchone())


def marks_changed(db,workspace,before,after):
    """Confirmed days of the pupils whose marks really changed become 'changed'.

    before and after map pupil -> (attendance,late minutes,day) of one lesson; a missing record equals an unmarked one.
    Runs inside the caller's write transaction: it never commits or begins one."""
    touched={}
    for id in before.keys()|after.keys():
        old,new=before.get(id),after.get(id)
        was,became=old[:2] if old else UNMARKED,new[:2] if new else UNMARKED
        first=old[2] if old else new[2];last=new[2] if new else first
        # A moved lesson with marks touches both days; an unmarked one changes nothing.
        if was==became and (first==last or was==UNMARKED):continue
        for day in {first,last}:touched.setdefault(day,[]).append(id)
    if not touched or not exists(db):return
    stamp=now().isoformat();user=actor.get()
    for day,ids in touched.items():
        rows=db.execute('''SELECT class_id,version FROM attendance_day_confirmations WHERE workspace=? AND day=? AND status='confirmed'
            AND EXISTS(SELECT 1 FROM json_each(pupils) p WHERE p.value IN (SELECT value FROM json_each(?)))''',(workspace,day,encoded(sorted(ids)))).fetchall()
        for class_id,version in rows:
            db.execute("UPDATE attendance_day_confirmations SET status='changed',version=?,updated=? WHERE workspace=? AND class_id=? AND day=?",(version+1,stamp,workspace,class_id,day))
            db.execute("INSERT INTO attendance_day_history(workspace,class_id,day,version,created,actor_id,actor_name,action,comment,request_id) VALUES (?,?,?,?,?,?,?,'changed','',NULL)",
                (workspace,class_id,day,version+1,stamp,user['id'],user['name']))


def day_states(db,workspace,class_ids,start,end):
    """{class id: {day: dict(status,version,updated,actor)}}; class_ids None means every class."""
    if not exists(db):return {}
    start,end=(x if isinstance(x,str) else x.isoformat() for x in (start,end))
    query='SELECT class_id,day,status,version,updated,actor_name FROM attendance_day_confirmations WHERE workspace=? AND day BETWEEN ? AND ?'
    args=[workspace,start,end]
    if class_ids is not None:query+=' AND class_id IN (SELECT value FROM json_each(?))';args.append(encoded(sorted(class_ids)))
    result={}
    for class_id,day,status,version,updated,name in db.execute(query,args):result.setdefault(class_id,{})[day]=dict(status=status,version=version,updated=updated,actor=name)
    return result


def starts(db,workspace,since):
    """{class id: first day owed}: the earliest confirmation row of the class or the day reminders were switched on, whichever is earlier.
    The 'default' key holds `since` for a class without a row; None means the class never started and reminders are off."""
    found=dict(db.execute('SELECT class_id,min(day) FROM attendance_day_confirmations WHERE workspace=? GROUP BY class_id',(workspace,)).fetchall()) if exists(db) else {}
    return {id:min(x for x in (day,since) if x) for id,day in found.items()}|dict(default=since)


def expected_days(rows):
    """{class id: iso days on which every lesson of the class has ended}; coverage rows with date, due, classIds and status."""
    state={}
    for r in rows:
        if r['status']=='orphaned':continue
        for id in r['classIds']:state[id,r['date']]=state.get((id,r['date']),True) and bool(r['due'])
    result={}
    for (id,day),ended in state.items():
        if ended:result.setdefault(id,set()).add(day)
    return result


def summarize(days,states,since):
    """Counts of one class: its expected days, their confirmation states and the days still owed since `since` (None: not measured)."""
    status=lambda day:states.get(day,{}).get('status')
    owed=sorted(d for d in days if since and d>=since and status(d)!='confirmed')
    return dict(expectedDays=len(days),confirmedDays=sum(status(d)=='confirmed' for d in days),changedDays=sum(status(d)=='changed' for d in days),
        pendingDays=len(owed) if since else None,pendingSince=owed[0] if owed else None)


def counts():
    return dict(present=0,absent=0,late=0,excused=0,unmarked=0,noRecord=0)


def rates(counts):
    """Shares of the marked lessons; unmarked and missing records are never in the denominator."""
    marked=sum(counts.get(s,0) for s in MARKED)
    share=lambda n:round(100*n/marked,1) if marked else None
    return dict(marked=marked,attendedPercent=share(counts.get('present',0)+counts.get('late',0)),absentPercent=share(counts.get('absent',0)),excusedPercent=share(counts.get('excused',0)))
