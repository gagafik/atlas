"""Independent, teacher-paced questions with current teaching and pupil access."""
from contextlib import closing, contextmanager
from datetime import timedelta
import hashlib
import json
import sqlite3
from uuid import uuid4
from fastapi import HTTPException
from .access import now
from .notifications import encoded
from .portal_cache import PortalSnapshots
from .school_forms import enabled, grant_ids
from .resources import teacher_ids
from .quiz_models import auto_mark


def stamp(): return now().isoformat()


def version(row, expected):
    if row['version'] != expected:
        raise HTTPException(409, 'Запись изменилась. Обновите её перед действием.')


def tables(db):
    db.executescript('''
    CREATE TABLE IF NOT EXISTS live_lessons(seq INTEGER PRIMARY KEY AUTOINCREMENT,id TEXT UNIQUE,
        workspace TEXT,owner_id TEXT,version INTEGER,status TEXT,group_id TEXT,subject_id TEXT,
        title TEXT,created TEXT,updated TEXT,document TEXT);
    CREATE INDEX IF NOT EXISTS live_lesson_scope ON live_lessons(workspace,owner_id,status,seq);
    CREATE TABLE IF NOT EXISTS live_lesson_members(lesson_id TEXT,student_id TEXT,PRIMARY KEY(lesson_id,student_id));
    CREATE INDEX IF NOT EXISTS live_lesson_personal ON live_lesson_members(student_id,lesson_id);
    CREATE TABLE IF NOT EXISTS live_lesson_rounds(lesson_id TEXT,question_index INTEGER,phase TEXT,deadline TEXT,
        PRIMARY KEY(lesson_id,question_index));
    CREATE TABLE IF NOT EXISTS live_lesson_answers(lesson_id TEXT,question_index INTEGER,student_id TEXT,
        version INTEGER,submitted INTEGER,updated TEXT,document TEXT,mark TEXT,
        PRIMARY KEY(lesson_id,question_index,student_id));
    CREATE INDEX IF NOT EXISTS live_lesson_own_answers ON live_lesson_answers(lesson_id,student_id,question_index);
    CREATE TABLE IF NOT EXISTS live_lesson_history(seq INTEGER PRIMARY KEY AUTOINCREMENT,lesson_id TEXT,
        created TEXT,actor_id TEXT,actor_name TEXT,action TEXT,document TEXT);
    CREATE INDEX IF NOT EXISTS live_lesson_history_item ON live_lesson_history(lesson_id,seq);
    CREATE TABLE IF NOT EXISTS live_lesson_requests(user_id TEXT,request_id TEXT,fingerprint TEXT,result TEXT,
        PRIMARY KEY(user_id,request_id));
    ''')


class LiveLessons:
    def __init__(self, access, state_stamp):
        self.access = access
        self.snapshots = PortalSnapshots(access.read, state_stamp)

    def prepare(self):
        with closing(self.access.read_connect()) as db:
            if db.execute("SELECT count(*) FROM sqlite_master WHERE name IN ('live_lesson_requests','teaching_material_file_links')").fetchone()[0] == 2: return
        from .teaching_materials import tables as material_tables
        with self.access.lock, self.access.connect() as db:
            tables(db)
            material_tables(db)

    @contextmanager
    def context(self, request, workspace, write=False):
        u = request.state.user
        snap = self.snapshots.get(workspace)
        if not u or u['role'] not in {'admin','teacher','student','parent'} or not enabled(u,snap,workspace):
            raise HTTPException(403, 'Занятия доступны учителям, ученикам и их родителям после входа.')
        self.prepare()
        def check(db):
            row = db.execute('SELECT * FROM accounts WHERE id=? AND active=1',(u['id'],)).fetchone()
            rev = db.execute('SELECT revision FROM workspaces WHERE id=?',(workspace,)).fetchone()
            if not row or self.access.public(row) != u or not rev or rev[0] != snap.state.revision:
                raise HTTPException(409,'Доступ или состав школы изменились. Обновите страницу.')
            db.row_factory = sqlite3.Row
        if write:
            with self.access.lock, self.access.connect() as db:
                db.execute('BEGIN IMMEDIATE'); check(db); yield db,u,snap
        else:
            with closing(self.access.read_connect()) as db:
                db.execute('PRAGMA query_only=ON'); db.execute('BEGIN'); check(db); yield db,u,snap

    def pairs(self,u,snap,w):
        ids = grant_ids(u,w,'teacher') & snap.entities['teacher'].keys()
        return {(x.groupId,x.subjectId) for x in snap.state.workloads if u['role']=='admin' or ids & set(teacher_ids(x))}

    def members(self,snap,group):
        g = snap.entities['group'].get(group)
        return {p.id for p in snap.state.students if p.active and g and (p.classId in g.classIds if g.kind=='whole' else p.id in g.studentIds)}

    def row(self,db,w,id,metadata=False):
        columns='id,workspace,owner_id,version,status,group_id,subject_id' if metadata else '*'
        r = db.execute('SELECT '+columns+' FROM live_lessons WHERE workspace=? AND id=?',(w,id)).fetchone()
        if not r: raise HTTPException(404,'Занятие не найдено.')
        return dict(r)

    def manage(self,u,snap,w,row):
        return u['role']=='admin' or (u['role']=='teacher' and row['owner_id']==u['id'] and (row['group_id'],row['subject_id']) in self.pairs(u,snap,w))

    def audience(self,db,snap,row):
        return self.members(snap,row['group_id']) & {r[0] for r in db.execute('SELECT student_id FROM live_lesson_members WHERE lesson_id=?',(row['id'],))}

    def access_row(self,db,u,snap,w,id,student='',staff=False,metadata=False):
        row = self.row(db,w,id,metadata)
        if self.manage(u,snap,w,row): return row
        ids = self.audience(db,snap,row) & grant_ids(u,w,'student') if u['role'] in {'student','parent'} and row['status']!='draft' else set()
        if staff or not ids or (student and student not in ids): raise HTTPException(404,'Нет доступа к занятию.')
        return row

    def require_pair(self,u,snap,w,group,subject):
        if u['role'] not in {'admin','teacher'} or (group,subject) not in self.pairs(u,snap,w):
            raise HTTPException(403,'Нет действующей учебной нагрузки по этому предмету и группе.')

    def replay(self,db,u,body,operation):
        key = hashlib.sha256(encoded([operation,body.model_dump(mode='json')]).encode()).hexdigest()
        r = db.execute('SELECT * FROM live_lesson_requests WHERE user_id=? AND request_id=?',(u['id'],body.requestId)).fetchone()
        if r and r['fingerprint'] != key: raise HTTPException(409,'Идентификатор уже использован другим действием.')
        return key,json.loads(r['result']) if r else None

    def remember(self,db,u,body,key,result):
        db.execute('INSERT INTO live_lesson_requests VALUES (?,?,?,?)',(u['id'],body.requestId,key,encoded(result)))
        return result

    def log(self,db,u,id,action,detail):
        db.execute('INSERT INTO live_lesson_history(lesson_id,created,actor_id,actor_name,action,document) VALUES (?,?,?,?,?,?)',(id,stamp(),u['id'],u['name'],action,encoded(detail)))

    def questions(self,db,u,w,refs,old=None):
        from .live_lesson_media import bind_materials
        retained = {(q['id'],q['version']):q for q in (old or {}).get('questions',[])}
        exists = db.execute("SELECT 1 FROM sqlite_master WHERE name='quiz_questions'").fetchone()
        result=[]
        for ref in refs:
            previous = retained.get((ref.id,ref.version))
            if previous:
                result.append(dict(id=ref.id,version=ref.version,question=previous['question']))
                continue
            q = db.execute('SELECT * FROM quiz_questions WHERE workspace=? AND id=?',(w,ref.id)).fetchone() if exists else None
            if not q or q['archived'] or (u['role']!='admin' and q['owner_id']!=u['id']): raise HTTPException(422,'Выберите доступный вопрос из своего банка.')
            if q['version']!=ref.version: raise HTTPException(409,'Вопрос изменён. Обновите банк вопросов.')
            result.append(dict(id=ref.id,version=ref.version,question=json.loads(q['document'])['question']))
        for q,ref in zip(result,refs):
            previous = retained.get((ref.id,ref.version),{})
            for field in ('materials','solutionMaterials'):
                q[field] = bind_materials(db,u,w,getattr(ref,field),previous.get(field,[]))
        return result

    def save(self,db,u,snap,w,body,row=None):
        d = body.definition
        self.require_pair(u,snap,w,d.groupId,d.subjectId)
        if row:
            version(row,body.version)
            if row['status']!='draft': raise HTTPException(409,'Начатое занятие неизменно. Создайте копию.')
        elif body.version: raise HTTPException(409,'Для нового занятия нужна версия 0.')
        old=json.loads(row['document']) if row else None
        doc=d.model_dump(exclude={'questions'}) | dict(questions=self.questions(db,u,w,d.questions,old))
        id=row['id'] if row else uuid4().hex
        if row:
            db.execute('UPDATE live_lessons SET version=version+1,group_id=?,subject_id=?,title=?,updated=?,document=? WHERE id=?',(d.groupId,d.subjectId,d.title,stamp(),encoded(doc),id))
        else:
            db.execute("INSERT INTO live_lessons(id,workspace,owner_id,version,status,group_id,subject_id,title,created,updated,document) VALUES (?,?,?,1,'draft',?,?,?,?,?,?)",(id,w,u['id'],d.groupId,d.subjectId,d.title,stamp(),stamp(),encoded(doc)))
        self.log(db,u,id,'saved',dict(version=body.version+1))
        return dict(id=id,version=body.version+1)

    def rounds(self,db,row):
        current=stamp()
        return [dict(r) | dict(phase='closed' if r['phase']=='open' and (current>=r['deadline'] or row['status']!='live') else r['phase']) for r in db.execute('SELECT * FROM live_lesson_rounds WHERE lesson_id=? ORDER BY question_index',(row['id'],))]

    def control(self,db,u,snap,w,row,b):
        version(row,b.version); action=b.action; doc=json.loads(row['document']); rounds=self.rounds(db,row); id=row['id']
        if action=='copy':
            self.require_pair(u,snap,w,row['group_id'],row['subject_id'])
            new=uuid4().hex
            db.execute("INSERT INTO live_lessons(id,workspace,owner_id,version,status,group_id,subject_id,title,created,updated,document) VALUES (?,?,?,1,'draft',?,?,?,?,?,?)",(new,w,u['id'],row['group_id'],row['subject_id'],row['title'],stamp(),stamp(),row['document']))
            self.log(db,u,new,'copied',dict(source=id)); return dict(id=new,version=1)
        if action=='start':
            if row['status']!='draft': raise HTTPException(409,'Занятие уже начато или отменено.')
            self.require_pair(u,snap,w,row['group_id'],row['subject_id']); members=self.members(snap,row['group_id'])
            if not members or len(members)>500: raise HTTPException(422,'Нужна группа с 1–500 действующими учениками.')
            db.executemany('INSERT INTO live_lesson_members VALUES (?,?)',[(id,p) for p in sorted(members)])
            db.execute("UPDATE live_lessons SET status='live' WHERE id=?",(id,))
        elif action=='open':
            if row['status']!='live' or (rounds and rounds[-1]['phase']=='open') or b.questionIndex!=len(rounds) or b.questionIndex>=len(doc['questions']):
                raise HTTPException(409,'Закройте текущий вопрос и откройте следующий по порядку.')
            db.execute("INSERT INTO live_lesson_rounds VALUES (?,?,'open',?)",(id,b.questionIndex,(now()+timedelta(seconds=b.seconds)).isoformat()))
        elif action in {'close','reveal'}:
            r=next((r for r in rounds if r['question_index']==b.questionIndex),None)
            if row['status'] not in {'live','ended'} or not r or (action=='close' and r['phase']!='open') or (action=='reveal' and r['phase']!='closed'):
                raise HTTPException(409,'Сначала завершите приём ответов на этот вопрос.')
            db.execute('UPDATE live_lesson_rounds SET phase=? WHERE lesson_id=? AND question_index=?',('closed' if action=='close' else 'revealed',id,b.questionIndex))
        elif action in {'end','cancel'}:
            if row['status'] not in ({'live'} if action=='end' else {'draft','live'}): raise HTTPException(409,'Занятие уже завершено.')
            if action=='cancel' and not b.reason.strip(): raise HTTPException(422,'Укажите причину отмены.')
            db.execute('UPDATE live_lessons SET status=? WHERE id=?',('ended' if action=='end' else 'cancelled',id))
            db.execute("UPDATE live_lesson_rounds SET phase='closed' WHERE lesson_id=? AND phase='open'",(id,))
        db.execute('UPDATE live_lessons SET version=version+1,updated=? WHERE id=?',(stamp(),id))
        self.log(db,u,id,action,dict(questionIndex=b.questionIndex,reason=b.reason,seconds=b.seconds))
        return dict(id=id,version=row['version']+1)

    def answer(self,db,u,snap,row,b):
        if u['role']!='student' or b.studentId not in grant_ids(u,row['workspace'],'student') or b.studentId not in self.audience(db,snap,row):
            raise HTTPException(403,'Отвечать может только сам ученик с действующим доступом.')
        rounds=self.rounds(db,row); r=next((r for r in rounds if r['question_index']==b.questionIndex),None)
        if row['status']!='live' or not r or r['phase']!='open': raise HTTPException(409,'Приём ответов завершён. Сохранённый ранее ответ учтён.')
        old=db.execute('SELECT * FROM live_lesson_answers WHERE lesson_id=? AND question_index=? AND student_id=?',(row['id'],b.questionIndex,b.studentId)).fetchone()
        if old and old['submitted']: raise HTTPException(409,'Ответ уже отправлен.')
        if b.version!=(old['version'] if old else 0): raise HTTPException(409,'Ответ изменён в другом окне. Сначала загрузите сохранённый ответ.')
        q=json.loads(row['document'])['questions'][b.questionIndex]['question']; a=b.answer
        choice=q['kind'] in {'single','multiple'}
        if len(a.selected)!=len(set(a.selected)) or set(a.selected)-{x['id'] for x in q['options']} or (choice and a.text) or (not choice and a.selected) or (q['kind']=='single' and len(a.selected)>1):
            raise HTTPException(422,'Ответ не соответствует типу вопроса.')
        db.execute('''INSERT INTO live_lesson_answers VALUES (?,?,?,?,?,?,?,NULL) ON CONFLICT(lesson_id,question_index,student_id)
            DO UPDATE SET version=excluded.version,submitted=excluded.submitted,updated=excluded.updated,document=excluded.document''',
            (row['id'],b.questionIndex,b.studentId,b.version+1,int(b.submit),stamp(),encoded(a.model_dump())))
        if b.submit:self.log(db,u,row['id'],'submitted',dict(studentId=b.studentId,questionIndex=b.questionIndex))
        return dict(id=row['id'],version=b.version+1,submitted=b.submit)

    def mark(self,q,a):
        if not a:return None
        return json.loads(a['mark']) if a['mark'] else auto_mark(q,json.loads(a['document']))

    def review(self,db,u,snap,row,b):
        if b.studentId not in self.audience(db,snap,row): raise HTTPException(404,'Ученик больше не входит в текущую группу.')
        rounds=self.rounds(db,row); r=next((r for r in rounds if r['question_index']==b.questionIndex),None)
        a=db.execute('SELECT * FROM live_lesson_answers WHERE lesson_id=? AND question_index=? AND student_id=?',(row['id'],b.questionIndex,b.studentId)).fetchone()
        if not r or r['phase']=='open' or not a:raise HTTPException(409,'Проверка доступна после окончания приёма сохранённого ответа.')
        version(a,b.version); q=json.loads(row['document'])['questions'][b.questionIndex]['question']
        if b.score>q['points']:raise HTTPException(422,'Балл превышает максимум вопроса.')
        if (r['phase']=='revealed' or a['mark']) and not b.reason.strip():raise HTTPException(422,'Укажите причину изменения результата.')
        if q['kind']!='essay' and not b.comment.strip():raise HTTPException(422,'Поясните изменение автоматической проверки.')
        mark=dict(score=b.score,comment=b.comment,automatic=False)
        db.execute('UPDATE live_lesson_answers SET mark=?,version=version+1,updated=? WHERE lesson_id=? AND question_index=? AND student_id=?',(encoded(mark),stamp(),row['id'],b.questionIndex,b.studentId))
        self.log(db,u,row['id'],'reviewed',dict(studentId=b.studentId,questionIndex=b.questionIndex,before=self.mark(q,a),after=mark,reason=b.reason))
        return dict(id=row['id'],version=b.version+1)

    def register(self,app,Workspace):
        from .live_lesson_routes import register
        register(self,app,Workspace)
