"""Private parent notices, class-scoped review and reversible attendance excuses."""
from datetime import date, timedelta
import hashlib
import json
from typing import Literal
from uuid import uuid4

from fastapi import HTTPException, Query, Request
from pydantic import Field, model_validator
from .models import Model
from .access import LOCAL, now
from .journal import user_ids
from .notifications import encoded, late_opener
from . import absence_codes

LEGAL = ['mother','father','guardian']  # family-registry relationships that make a verified link a legal guardian
DISABLED = 'Электронные записки для этого класса выключены. Свяжитесь с классным руководителем.'


DECISIONS={'approved':'одобрено','rejected':'отклонено','withdrawn':'отозвано','revoked':'решение отменено'}


def notice_period(row):
    """dd.mm, or dd.mm–dd.mm for a longer span: the dates the note already shows everybody who may open it."""
    start,end=row[10][:10],row[11][:10];first=f'{start[8:10]}.{start[5:7]}'
    return first if start==end else f'{first}–{end[8:10]}.{end[5:7]}'


def notice_title(action,row):
    """Dates and the decision word; never the reason, the code, the files or the reviewer's comment."""
    word=DECISIONS.get(row[9])
    return f'Заявление об отсутствии, {notice_period(row)}: {word}' if action!='absence.submitted' and word else f'Заявление об отсутствии: {notice_period(row)}'


def notice_target(title,own):
    """Where an entry about these notes leads: the first one, the pupils they concern and a title."""
    return dict(module='absences',recordId=own[0][0],students=[dict(id=i,name=n) for i,n in {r[3]:r[5] for r in own}.items()],title=title)


def attendance_target(workspace,rows):
    """Title only, no link: a guardian who may not see the note must not learn that one exists, only that attendance changed."""
    def target(user):
        mine={r[3]:r[5] for r in rows if r[3] in user_ids(user,workspace,'student')}
        return dict(title='Изменились отметки посещаемости',students=[dict(id=i,name=n) for i,n in mine.items()])
    return target


def tables(db):
    from .class_staff import tables as staff_tables
    from .families import tables as family_tables
    staff_tables(db);family_tables(db)
    db.execute('''CREATE TABLE IF NOT EXISTS absence_notices (
        id TEXT PRIMARY KEY,workspace TEXT,author_id TEXT,student_id TEXT,class_id TEXT,
        student_name TEXT,created TEXT,updated TEXT,version INTEGER,status TEXT,
        start_at TEXT,end_at TEXT,reason TEXT,request_id TEXT,request_hash TEXT,
        UNIQUE(workspace,author_id,request_id))''')
    db.execute('CREATE INDEX IF NOT EXISTS absence_student ON absence_notices(workspace,student_id,status,start_at,end_at)')
    db.execute('CREATE INDEX IF NOT EXISTS absence_author ON absence_notices(workspace,author_id,created)')
    # Legacy reviewer document, read only by class_staff.config() until the registry is saved.
    db.execute('CREATE TABLE IF NOT EXISTS absence_reviewers (workspace TEXT PRIMARY KEY,version INTEGER,document TEXT)')
    db.execute('''CREATE TABLE IF NOT EXISTS absence_history (
        id INTEGER PRIMARY KEY,notice_id TEXT,version INTEGER,created TEXT,actor_id TEXT,actor_name TEXT,
        action TEXT,comment TEXT,request_id TEXT,request_hash TEXT,UNIQUE(notice_id,actor_id,request_id))''')
    db.execute('CREATE INDEX IF NOT EXISTS absence_history_notice ON absence_history(notice_id,id)')
    # Columns are always appended by ALTER, never in CREATE TABLE, so fresh and migrated databases share one positional layout:
    # row[15] origin ('parent' or 'school'), row[16] author_name, row[17] code_id, row[18] subject_id (NULL = every subject).
    columns={r[1] for r in db.execute('PRAGMA table_info(absence_notices)')}
    if 'origin' not in columns:db.execute("ALTER TABLE absence_notices ADD COLUMN origin TEXT NOT NULL DEFAULT 'parent'")
    if 'author_name' not in columns:
        db.execute('ALTER TABLE absence_notices ADD COLUMN author_name TEXT')
        # The UPDATE opens an implicit transaction; callers start BEGIN IMMEDIATE right after tables(), so close the one this migration opened.
        open_before=db.in_transaction
        db.execute('UPDATE absence_notices SET author_name=(SELECT h.actor_name FROM absence_history h WHERE h.notice_id=absence_notices.id ORDER BY h.id LIMIT 1)')
        if not open_before:db.commit()
    if 'code_id' not in columns:db.execute('ALTER TABLE absence_notices ADD COLUMN code_id TEXT')
    if 'subject_id' not in columns:db.execute('ALTER TABLE absence_notices ADD COLUMN subject_id TEXT')
    # A class without a row has electronic notes enabled; rows appear only when someone switches them.
    db.execute('''CREATE TABLE IF NOT EXISTS absence_class_settings (
        workspace TEXT NOT NULL,class_id TEXT NOT NULL,version INTEGER NOT NULL,enabled INTEGER NOT NULL,
        updated TEXT NOT NULL,actor_id TEXT NOT NULL,actor_name TEXT NOT NULL,PRIMARY KEY(workspace,class_id))''')
    db.execute('''CREATE TABLE IF NOT EXISTS absence_class_history (
        id INTEGER PRIMARY KEY,workspace TEXT NOT NULL,class_id TEXT NOT NULL,version INTEGER NOT NULL,created TEXT NOT NULL,
        actor_id TEXT NOT NULL,actor_name TEXT NOT NULL,enabled INTEGER NOT NULL,reason TEXT NOT NULL,request_id TEXT NOT NULL,
        UNIQUE(workspace,class_id,actor_id,request_id))''')
    absence_codes.tables(db)


def approvals(db, workspace, lessons):
    """Return only decision IDs, never private reasons or guardian identities."""
    if not lessons or not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='absence_notices'").fetchone():return {}
    students={s['id'] for lesson in lessons for s in lesson['students']}
    if not students:return {}
    start=min(l['date'] for l in lessons);end=max(l['date'] for l in lessons)+'T23:59'
    # Journal reads run on read-only snapshots and can precede the first migration after a deploy: no subject column means every subject.
    subject='subject_id' if 'subject_id' in {r[1] for r in db.execute('PRAGMA table_info(absence_notices)')} else 'NULL'
    rows=db.execute(f"SELECT id,student_id,start_at,end_at,version,{subject} FROM absence_notices WHERE workspace=? AND status='approved' AND student_id IN (SELECT value FROM json_each(?)) AND start_at<=? AND end_at>=?",(workspace,encoded(sorted(students)),end,start)).fetchall()
    result={}
    for i,lesson in enumerate(lessons):
        if lesson.get('status')=='cancelled':continue
        ids={s['id'] for s in lesson['students']}
        bounds=(lesson['date']+'T'+lesson['start'],lesson['date']+'T'+lesson['end'])
        result[i]={r[1]:dict(id=r[0],version=r[4]) for r in rows if r[1] in ids and r[2]<=bounds[0] and bounds[1]<=r[3] and (r[5] is None or r[5]==lesson.get('subjectId'))}
    return result


class Span(Model):
    startDate: date
    endDate: date
    startTime: str = Field(default='00:00',pattern=r'^(?:[01][0-9]|2[0-3]):[0-5][0-9]$')
    endTime: str = Field(default='23:59',pattern=r'^(?:[01][0-9]|2[0-3]):[0-5][0-9]$')
    reason: str = Field(min_length=1,max_length=4000)
    codeId: str = Field(default='',max_length=40)
    subjectId: str = Field(default='',max_length=100)
    @model_validator(mode='after')
    def valid(self):
        if not self.reason.strip():raise ValueError('Укажите причину отсутствия.')
        if not 0<=(self.endDate-self.startDate).days<=30 or self.startDate==self.endDate and self.endTime<=self.startTime:raise ValueError('Выберите последовательный период до 31 дня.')
        return self
    def bounds(self):return self.startDate.isoformat()+'T'+self.startTime,self.endDate.isoformat()+'T'+self.endTime


class Notice(Span):
    requestId: str = Field(pattern=r'^[a-f0-9-]{36}$')
    studentId: str = Field(min_length=1,max_length=100)
    fileIds: list[str] = Field(default_factory=list,max_length=5)
    @model_validator(mode='after')
    def files(self):
        if len(set(self.fileIds))!=len(self.fileIds):raise ValueError('Файлы не должны повторяться.')
        return self


class SchoolNotes(Span):
    """One note per pupil, entered by the school; accepted at once. A single pupil is a batch of one."""
    requestId: str = Field(pattern=r'^[a-f0-9-]{36}$')
    studentIds: list[str] = Field(min_length=1,max_length=50)
    codeId: str = Field(min_length=1,max_length=40)
    @model_validator(mode='after')
    def pupils(self):
        if any(not 1<=len(id)<=100 for id in self.studentIds):raise ValueError('Выберите учеников.')
        if len(set(self.studentIds))!=len(self.studentIds):raise ValueError('Ученики не должны повторяться.')
        return self


class Decision(Model):
    requestId: str = Field(pattern=r'^[a-f0-9-]{36}$')
    version: int = Field(ge=1)
    action: Literal['approve','reject','revoke','withdraw']
    comment: str = Field(default='',max_length=2000)


class Electronic(Model):
    requestId: str = Field(pattern=r'^[a-f0-9-]{36}$')
    version: int = Field(ge=0)
    enabled: bool
    reason: str = Field(default='',max_length=500)


class Absences:
    def __init__(self,journal,snapshots):
        self.journal,self.access,self.notifications,self.snapshots=journal,journal.access,journal.notifications,snapshots
        from .absence_files import AbsenceFiles
        self.files=AbsenceFiles(self)

    def context(self,request,workspace):
        user=request.state.user or LOCAL
        if user['role'] not in ['admin','teacher','staff','parent','student']:raise HTTPException(403,'Раздел доступен семьям и ответственным сотрудникам.')
        if user['role']=='staff' and workspace not in user['workspaces']:raise HTTPException(403,'Нет доступа к этому пространству.')
        return user,self.snapshots.get(workspace)

    def recheck(self,db,user,snapshot,workspace):
        self.journal.recheck_account(db,user)
        current=db.execute('SELECT revision FROM workspaces WHERE id=?',(workspace,)).fetchone()
        if not current or current[0]!=snapshot.state.revision:raise HTTPException(409,'Школьные данные изменились. Обновите раздел.')

    def config(self,db,workspace):
        from .class_staff import config
        value=config(db,workspace)
        return dict(version=value['version'],assignments={id:[r['accountId'] for r in rows if 'absence_review' in r['capabilities']] for id,rows in value['assignments'].items()})

    def classes(self,db,user,snapshot,workspace,capability='absence_review'):
        from .class_staff import classes
        return classes(db,user,snapshot,workspace,capability)

    def reviewer_ids(self,db,workspace,snapshot,class_ids):
        """Candidate recipients for notices of these classes: registry entries and the accounts of their class teachers; can_review still decides."""
        assignments=self.config(db,workspace)['assignments']
        leaders={c.classTeacherId for c in snapshot.state.classes if c.id in class_ids and c.classTeacherId}
        accounts=self.notifications.granted_users(db,workspace,'teacher',leaders,['teacher'])
        return {account for id in class_ids for account in assignments.get(id,[])}|{u['id'] for u in accounts}

    def electronic(self,db,workspace,class_id):
        """Switch of electronic notes for one class; no row means enabled."""
        row=db.execute('SELECT enabled,version,updated,actor_name FROM absence_class_settings WHERE workspace=? AND class_id=?',(workspace,class_id)).fetchone() if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='absence_class_settings'").fetchone() else None
        return dict(enabled=bool(row[0]),version=row[1],updated=row[2],actor=row[3]) if row else dict(enabled=True,version=0,updated=None,actor=None)

    def require_electronic(self,db,workspace,pupil):
        if not self.electronic(db,workspace,pupil.classId)['enabled']:raise HTTPException(403,DISABLED)

    def can_switch(self,db,user,snapshot,workspace,class_id):
        """Administrators and the class teacher whose own review duty is not revoked; a registry deputy cannot."""
        from .class_staff import led
        return user['role']=='admin' or class_id in led(user,snapshot,workspace) and class_id in self.classes(db,user,snapshot,workspace)

    def leader(self,snapshot,class_id):
        school=snapshot.entities['class_'].get(class_id);teacher=snapshot.entities['teacher'].get(school.classTeacherId) if school and school.classTeacherId else None
        return teacher.name if teacher else None

    def parent(self,user,snapshot,workspace,student):
        pupil=snapshot.entities['student'].get(student)
        if user['role']!='parent' or student not in user_ids(user,workspace,'student') or not pupil or not pupil.active:raise HTTPException(403,'Нет действующего доступа к этому ребёнку.')
        return pupil

    def can_review(self,db,user,snapshot,workspace,student):
        pupil=snapshot.entities['student'].get(student)
        return user['role']=='admin' or bool(pupil and pupil.classId in self.classes(db,user,snapshot,workspace))

    def legal_children(self,db,user,workspace):
        """Pupils whose current link to this parent is a verified legal-guardian record of the family registry; dates are already enforced by the grants."""
        if user['role']!='parent':return set()
        own=user_ids(user,workspace,'student');result=set()
        for student,raw in db.execute('SELECT student_id,document FROM family_links WHERE workspace=? AND parent_id=?',(workspace,user['id'])):
            document=json.loads(raw)
            if student in own and document['status']=='active' and document['verified'] and document['relationship'] in LEGAL:result.add(student)
        return result

    def level(self,db,user,snapshot,workspace,row):
        """'full' (reason, files, history), 'summary' (status, period, type; nothing private) or None."""
        if self.can_review(db,user,snapshot,workspace,row[3]):return 'full'
        children=user_ids(user,workspace,'student')
        if user['role']=='parent' and row[3] in children:
            if row[2]==user['id'] or row[3] in self.legal_children(db,user,workspace):return 'full'
            # Notes entered by the school are not another guardian's private story: every guardian with current access sees the summary.
            return 'summary' if row[15]=='school' else None
        return 'summary' if user['role']=='student' and row[3] in children else None

    def find(self,db,id,user,snapshot,workspace,summary=False):
        row=db.execute('SELECT * FROM absence_notices WHERE id=? AND workspace=?',(id,workspace)).fetchone()
        level=self.level(db,user,snapshot,workspace,row) if row else None
        if level!='full' and not (summary and level=='summary'):raise HTTPException(404,'Заявление не найдено.')
        return row

    def item(self,row,private=False):
        result=dict(id=row[0],studentId=row[3],studentName=row[5],created=row[6],updated=row[7],version=row[8],status=row[9],start=row[10],end=row[11],origin=row[15],codeId=row[17],subjectId=row[18])
        if private:result['reason']=row[12]
        return result

    def closed_range(self,db,workspace,start,end):
        """Closed journal periods overlapping the day range; accepting or revoking a note would change their figures."""
        from .journal_periods import range_locks
        return range_locks(db,workspace,start,end)

    def closed_periods(self,db,workspace,row):
        return self.closed_range(db,workspace,row[10][:10],row[11][:10])

    def detail(self,db,row,user,snapshot,workspace):
        if self.level(db,user,snapshot,workspace,row)=='summary':return self.item(row)|dict(summary=True,canReview=False,canWithdraw=False,files=[],periodLocks=[])
        result=self.item(row,True);review=self.can_review(db,user,snapshot,workspace,row[3])
        result.update(summary=False,authorName=row[16],canReview=review,canWithdraw=user['role']=='parent' and row[2]==user['id'] and row[9]=='pending',files=self.files.linked(db,row[0]),
                      periodLocks=[p['name'] for p in self.closed_periods(db,row[1],row)] if review else [])
        return result

    def in_year(self,snapshot,body):
        settings=snapshot.state.settings
        if not settings.yearStart<=body.startDate<=body.endDate<=settings.yearEnd:raise HTTPException(422,'Период вне учебного года.')

    def subject_of(self,snapshot,body):
        """The subject id a note is limited to, or None for every subject."""
        if not body.subjectId:return None
        if body.subjectId not in {s.id for s in snapshot.state.subjects}:raise HTTPException(422,'Предмет не найден.')
        return body.subjectId

    def overlap(self,db,workspace,student,start,end,subject):
        """Status of a pending or accepted note that already covers part of this period and subject. Different subjects may overlap."""
        row=db.execute("SELECT status FROM absence_notices WHERE workspace=? AND student_id=? AND status IN ('pending','approved') AND start_at<? AND end_at>? AND (subject_id IS NULL OR ? IS NULL OR subject_id=?) LIMIT 1",(workspace,student,end,start,subject,subject)).fetchone()
        return row[0] if row else None

    def lessons(self,snapshot,ids,body,subject):
        """{pupil id: lessons fully inside the span}. Days are the outer loop: the snapshot caches only a few resolved days."""
        start,end=body.bounds();result={id:[] for id in ids}
        for offset in range((body.endDate-body.startDate).days+1):
            for id in ids:
                for r in snapshot.student_lessons(body.startDate+timedelta(days=offset),id):
                    if r['activity']=='lesson' and r['status']!='cancelled' and start<=r['date']+'T'+r['start'] and r['date']+'T'+r['end']<=end and (not subject or r['subjectId']==subject):result[id].append(r)
        return result

    def insert_notice(self,db,*,user,pupil,workspace,start,end,reason,request_id,fingerprint,status,origin,code_id,subject_id,action):
        """Notice and its first history row; the caller owns the transaction."""
        id=uuid4().hex;created=now().isoformat()
        db.execute('INSERT INTO absence_notices(id,workspace,author_id,student_id,class_id,student_name,created,updated,version,status,start_at,end_at,reason,request_id,request_hash,origin,author_name,code_id,subject_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                   (id,workspace,user['id'],pupil.id,pupil.classId,pupil.name,created,created,1,status,start,end,reason,request_id,fingerprint,origin,user['name'],code_id,subject_id))
        db.execute('INSERT INTO absence_history(notice_id,version,created,actor_id,actor_name,action,comment,request_id,request_hash) VALUES (?,?,?,?,?,?,?,?,?)',(id,1,created,user['id'],user['name'],action,'',request_id,fingerprint))
        return id

    def reviewable(self,db,user,snapshot,workspace,student):
        """The pupil, or 404 before anything about them is returned or written."""
        pupil=snapshot.entities['student'].get(student)
        if not pupil or not self.can_review(db,user,snapshot,workspace,student):raise HTTPException(404,'Ученик не найден или недоступен.')
        return pupil

    def notify(self,db,row,snapshot,action):
        workspace,student=row[1],row[3];pupil=snapshot.entities['student'].get(student)
        review_ids=self.reviewer_ids(db,workspace,snapshot,{pupil.classId}) if pupil else set()
        family={u['id'] for u in self.notifications.granted_users(db,workspace,'student',{student},['parent'])}
        query="SELECT * FROM accounts WHERE active=1 AND (role='admin' OR id=? OR id IN (SELECT value FROM json_each(?)))"
        users=[self.access.public(r) for r in db.execute(query,(row[2],encoded(sorted(review_ids|family))))]
        # Verified legal guardians have the full level, so they hear about the other guardian's note too.
        targets=[u for u in users if self.level(db,u,snapshot,workspace,row)=='full']
        self.notifications.deliver(db,targets,workspace,row[8],action,topic=f'absence:{workspace}:{row[0]}',supersede=True,target=notice_target(notice_title(action,row),[row]))

    def decision_status(self,db,row,user,snap,workspace,version,action):
        if action=='withdraw':
            if user['role']!='parent' or row[2]!=user['id']:raise HTTPException(403,'Отозвать может автор заявления.')
            allowed=row[9]=='pending';status='withdrawn'
        else:
            if not self.can_review(db,user,snap,workspace,row[3]):raise HTTPException(403,'Нет права рассматривать это заявление.')
            allowed=row[9]==('approved' if action=='revoke' else 'pending')
            status=dict(approve='approved',reject='rejected',revoke='revoked')[action]
        if not allowed or row[8]!=version:raise HTTPException(409,'Состояние заявления изменилось. Откройте его заново.')
        # Only approved notices change attendance figures; reject and withdraw move a pending notice and stay allowed.
        if action in ['approve','revoke'] and (closed:=self.closed_periods(db,workspace,row)):
            raise HTTPException(403,'Решение изменит посещаемость закрытого учебного периода: '+', '.join(p['name'] for p in closed)+'. Администратор должен сначала открыть период с указанием причины.')
        return status

    def apply_decision(self,db,row,user,snap,workspace,status,comment,request_id,fingerprint,*,notify=True):
        """Caller validates every decision first and owns the write transaction."""
        id=row[0];stamp=now().isoformat();version=row[8]+1
        db.execute('UPDATE absence_notices SET version=?,status=?,updated=? WHERE id=?',(version,status,stamp,id))
        db.execute('INSERT INTO absence_history(notice_id,version,created,actor_id,actor_name,action,comment,request_id,request_hash) VALUES (?,?,?,?,?,?,?,?,?)',(id,version,stamp,user['id'],user['name'],status,comment,request_id,fingerprint))
        changed=self.find(db,id,user,snap,workspace)
        if notify:self.notify(db,changed,snap,'absence.updated')
        if notify and status in ['approved','revoked']:
            candidates=db.execute("""SELECT * FROM accounts a WHERE active=1 AND role IN ('student','parent') AND EXISTS(
                SELECT 1 FROM json_each(a.permissions,'$.grants') g,json_each(g.value,'$.entityIds') s
                WHERE json_extract(g.value,'$.workspace')=? AND json_extract(g.value,'$.view')='student' AND s.value=?)""",(workspace,row[3])).fetchall()
            targets=[self.access.public(r) for r in candidates]
            self.notifications.deliver(db,[u for u in targets if row[3] in user_ids(u,workspace,'student')],workspace,version,'journal.updated',target=attendance_target(workspace,[row]))
        self.access.record(db,'absence.'+status,workspace,detail=id)
        return changed

    def open_notice(self,db,user,workspace,data,kids):
        row=db.execute('SELECT * FROM absence_notices WHERE id=? AND workspace=?',(data.get('recordId'),workspace)).fetchone()
        # Both levels open the panel; the panel itself shows only what the level allows.
        return {} if row and self.level(db,user,self.snapshots.get(workspace),workspace,row) else None

    def register(self,app,Workspace):
        late_opener('absences',self.open_notice)
        from .class_staff import register as register_staff
        register_staff(self,app,Workspace)
        from .absence_bulk import register
        register(self,app,Workspace)
        absence_codes.register(self,app,Workspace)
        @app.get('/api/journal/absences/options')
        def options(request:Request,workspace:Workspace='school'):
            from .journal import today
            user,snap=self.context(request,workspace)
            settings=snap.state.settings;default=min(max(today(),settings.yearStart),settings.yearEnd)
            with self.access.lock,self.access.connect() as db:
                tables(db);self.recheck(db,user,snap,workspace)
                config=self.config(db,workspace);classes=self.classes(db,user,snap,workspace)
                pupils=[dict(id=s.id,name=s.name,electronicNotes=self.electronic(db,workspace,s.classId)['enabled'],classTeacher=self.leader(snap,s.classId)) for s in snap.state.students if s.active and s.id in user_ids(user,workspace,'student')] if user['role']=='parent' else []
                result=dict(parent=user['role']=='parent',pupil=user['role']=='student',admin=user['role']=='admin',canReview=user['role']=='admin' or bool(classes),students=pupils,yearStart=settings.yearStart,yearEnd=settings.yearEnd,defaultDate=default.isoformat(),
                            codes=absence_codes.current(db,workspace)['items'],subjects=[dict(id=s.id,name=s.name) for s in snap.state.subjects])
                if result['canReview']:
                    from .class_staff import led
                    leading=led(user,snap,workspace)
                    result['classSettings']=[dict(id=c.id,name=c.name,classTeacher=self.leader(snap,c.id),**self.electronic(db,workspace,c.id),canChange=user['role']=='admin' or c.id in leading and c.id in classes) for c in snap.state.classes if user['role']=='admin' or c.id in classes]
                    # Pupils of the classes this account may review (every class for an administrator): the choice list of the school note form.
                    result['schoolPupils']=[dict(id=s.id,name=s.name,classId=s.classId) for s in snap.state.students if s.active and (user['role']=='admin' or s.classId in classes)]
                if user['role']=='admin':
                    candidates=[self.access.public(r) for r in db.execute("SELECT * FROM accounts WHERE active=1 AND role='teacher'")]
                    result.update(config=config,codesVersion=absence_codes.current(db,workspace)['version'],classes=[dict(id=c.id,name=c.name) for c in snap.state.classes],teachers=[dict(id=u['id'],name=u['name'],login=u['login']) for u in candidates if user_ids(u,workspace,'teacher')])
                return result

        @app.get('/api/journal/absences')
        def listing(request:Request,workspace:Workspace='school',status:Literal['all','pending','approved','rejected','withdrawn','revoked']='all',classId:str=Query(default='',max_length=100),start:date|None=None,end:date|None=None,before:int|None=Query(default=None,ge=1)):
            user,snap=self.context(request,workspace)
            if start and end and end<start:raise HTTPException(422,'Выберите последовательный период.')
            with self.access.lock,self.access.connect() as db:
                tables(db);self.recheck(db,user,snap,workspace)
                query='SELECT n.rowid,n.* FROM absence_notices n WHERE workspace=?';params=[workspace]
                children=sorted(user_ids(user,workspace,'student'))
                if user['role']=='parent':
                    # Own notes, every note entered by the school, and the notes of the other guardian when this link is a verified legal guardian one.
                    query+=" AND student_id IN (SELECT value FROM json_each(?)) AND (author_id=? OR origin='school' OR student_id IN (SELECT value FROM json_each(?)))"
                    params += [encoded(children),user['id'],encoded(sorted(self.legal_children(db,user,workspace)))]
                elif user['role']=='student':
                    query+=' AND student_id IN (SELECT value FROM json_each(?))';params.append(encoded(children))
                else:
                    # A class filter that is not one of the reviewer's classes is a 404, not an empty page: nothing is said about other classes.
                    classes=set(snap.entities['class_']) if user['role']=='admin' else self.classes(db,user,snap,workspace)
                    if classId and classId not in classes:raise HTTPException(404,'Класс не найден или недоступен.')
                    if classId or user['role']!='admin':
                        ids=[s.id for s in snap.state.students if s.classId==classId or not classId and s.classId in classes]
                        query+=' AND student_id IN (SELECT value FROM json_each(?))';params.append(encoded(ids))
                if status!='all':query+=' AND status=?';params.append(status)
                if end:query+=' AND start_at<=?';params.append(end.isoformat()+'T23:59')
                if start:query+=' AND end_at>=?';params.append(start.isoformat()+'T00:00')
                if before is not None:query+=' AND n.rowid<?';params.append(before)
                rows=db.execute(query+' ORDER BY n.rowid DESC LIMIT 51',params).fetchall()
                return dict(items=[self.item(r[1:])|dict(mine=r[3]==user['id']) for r in rows[:50]],nextBefore=rows[49][0] if len(rows)>50 else None)

        @app.post('/api/journal/absences/preview')
        def preview(body:Notice,request:Request,workspace:Workspace='school'):
            user,snap=self.context(request,workspace);pupil=self.parent(user,snap,workspace,body.studentId)
            self.in_year(snap,body);lessons=self.lessons(snap,[body.studentId],body,self.subject_of(snap,body))[body.studentId]
            with self.access.lock,self.access.connect() as db:
                self.recheck(db,user,snap,workspace);self.require_electronic(db,workspace,pupil)
            return dict(lessons=lessons,revision=snap.state.revision)

        @app.post('/api/journal/absences')
        def submit(body:Notice,request:Request,workspace:Workspace='school'):
            user,snap=self.context(request,workspace);pupil=self.parent(user,snap,workspace,body.studentId)
            self.in_year(snap,body);subject=self.subject_of(snap,body)
            fingerprint=hashlib.sha256(encoded(body.model_dump(mode='json')).encode()).hexdigest();start,end=body.bounds()
            with self.access.lock,self.access.connect() as db:
                tables(db);self.files.tables(db);db.execute('BEGIN IMMEDIATE');self.recheck(db,user,snap,workspace)
                previous=db.execute('SELECT * FROM absence_notices WHERE workspace=? AND author_id=? AND request_id=?',(workspace,user['id'],body.requestId)).fetchone()
                if previous:
                    if previous[14]!=fingerprint:raise HTTPException(409,'Этот запрос уже использован для другого заявления.')
                    return self.detail(db,previous,user,snap,workspace)
                self.require_electronic(db,workspace,pupil);code=absence_codes.check(db,workspace,body.codeId,'parent')
                if self.overlap(db,workspace,body.studentId,start,end,subject):raise HTTPException(409,'За этот период уже есть заявление. Уточните его состояние у школы или выберите другой период.')
                count=db.execute('SELECT count(*) FROM absence_notices WHERE author_id=? AND created>?',(user['id'],(now()-timedelta(hours=1)).isoformat())).fetchone()[0]
                if count>=10:raise HTTPException(429,'До 10 заявлений в час. Повторите позже.')
                id=self.insert_notice(db,user=user,pupil=pupil,workspace=workspace,start=start,end=end,reason=body.reason,request_id=body.requestId,fingerprint=fingerprint,status='pending',origin='parent',code_id=code,subject_id=subject,action='submitted')
                self.files.bind(db,body.fileIds,user,workspace,pupil.id,body.requestId,id)
                row=self.find(db,id,user,snap,workspace);self.notify(db,row,snap,'absence.submitted')
                self.access.record(db,'absence.submitted',workspace,detail=id)
                return self.detail(db,row,user,snap,workspace)

        @app.put('/api/journal/absences/classes/{class_id}/electronic')
        def switch(class_id:str,body:Electronic,request:Request,workspace:Workspace='school'):
            user,snap=self.context(request,workspace)
            if class_id not in snap.entities['class_']:raise HTTPException(404,'Класс не найден.')
            with self.access.lock,self.access.connect() as db:
                tables(db);db.execute('BEGIN IMMEDIATE');self.recheck(db,user,snap,workspace)
                if not self.can_switch(db,user,snap,workspace,class_id):raise HTTPException(403,'Электронные записки класса включает классный руководитель или администратор.')
                current=self.electronic(db,workspace,class_id)
                view=lambda value:dict(classId=class_id,enabled=value['enabled'],version=value['version'],updated=value['updated'],actor=value['actor'])
                previous=db.execute('SELECT enabled FROM absence_class_history WHERE workspace=? AND class_id=? AND actor_id=? AND request_id=?',(workspace,class_id,user['id'],body.requestId)).fetchone()
                if previous:
                    if bool(previous[0])!=body.enabled:raise HTTPException(409,'Запрос уже использован для другой настройки.')
                    return view(current)
                if current['version']!=body.version:raise HTTPException(409,'Настройку записок уже изменили. Обновите раздел.')
                if current['enabled']==body.enabled:return view(current)
                stamp=now().isoformat();version=current['version']+1
                db.execute('INSERT INTO absence_class_settings VALUES (?,?,?,?,?,?,?) ON CONFLICT(workspace,class_id) DO UPDATE SET version=excluded.version,enabled=excluded.enabled,updated=excluded.updated,actor_id=excluded.actor_id,actor_name=excluded.actor_name',(workspace,class_id,version,int(body.enabled),stamp,user['id'],user['name']))
                db.execute('INSERT INTO absence_class_history(workspace,class_id,version,created,actor_id,actor_name,enabled,reason,request_id) VALUES (?,?,?,?,?,?,?,?,?)',(workspace,class_id,version,stamp,user['id'],user['name'],int(body.enabled),body.reason,body.requestId))
                self.access.record(db,'absence.electronic',workspace,detail=f'{class_id}:{int(body.enabled)}')
                return view(dict(enabled=body.enabled,version=version,updated=stamp,actor=user['name']))

        @app.get('/api/journal/absences/{id}')
        def detail(id:str,request:Request,workspace:Workspace='school'):
            user,snap=self.context(request,workspace)
            with self.access.lock,self.access.connect() as db:
                tables(db);self.files.tables(db);self.recheck(db,user,snap,workspace);row=self.find(db,id,user,snap,workspace,summary=True)
                self.access.record(db,'absence.read',workspace,detail=id)
                return self.detail(db,row,user,snap,workspace)

        @app.get('/api/journal/absences/{id}/history')
        def history(id:str,request:Request,workspace:Workspace='school',before:int|None=Query(default=None,ge=1)):
            user,snap=self.context(request,workspace)
            with self.access.lock,self.access.connect() as db:
                tables(db);self.recheck(db,user,snap,workspace);self.find(db,id,user,snap,workspace)
                rows=db.execute('SELECT id,version,created,actor_name,action,comment,request_id FROM absence_history WHERE notice_id=? AND (? IS NULL OR id<?) ORDER BY id DESC LIMIT 51',(id,before,before)).fetchall()
                self.access.record(db,'absence.history_read',workspace,detail=id)
                return dict(items=[dict(id=r[0],version=r[1],created=r[2],actor=r[3],action=r[4],comment=r[5],batchId=r[6].split(':')[1] if r[6].startswith('batch:') else None) for r in rows[:50]],nextBefore=rows[49][0] if len(rows)>50 else None)

        @app.post('/api/journal/absences/{id}/decision')
        def decision(id:str,body:Decision,request:Request,workspace:Workspace='school'):
            user,snap=self.context(request,workspace);fingerprint=hashlib.sha256(encoded(body.model_dump()).encode()).hexdigest()
            if body.action!='withdraw' and not body.comment.strip():raise HTTPException(422,'Объясните решение.')
            with self.access.lock,self.access.connect() as db:
                # A summary reader reaches decision_status and is refused there with the reason, not with a misleading "not found".
                tables(db);self.files.tables(db);db.execute('BEGIN IMMEDIATE');self.recheck(db,user,snap,workspace);row=self.find(db,id,user,snap,workspace,summary=True)
                previous=db.execute('SELECT request_hash FROM absence_history WHERE notice_id=? AND actor_id=? AND request_id=?',(id,user['id'],body.requestId)).fetchone()
                if previous:
                    if previous[0]!=fingerprint:raise HTTPException(409,'Запрос уже использован для другого решения.')
                    return self.detail(db,row,user,snap,workspace)
                status=self.decision_status(db,row,user,snap,workspace,body.version,body.action)
                changed=self.apply_decision(db,row,user,snap,workspace,status,body.comment,body.requestId,fingerprint)
                return self.detail(db,changed,user,snap,workspace)
