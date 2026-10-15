"""Versioned teaching plans and publication-based, indexed progress."""
import json
from datetime import date
from uuid import uuid4
from zoneinfo import ZoneInfo

from fastapi import HTTPException, Query, Request
from pydantic import Field, model_validator
from .access import now
from .models import Model
from .notifications import encoded
from .journal import user_ids, staff_read, key_for, TopicReference
from .resources import teacher_ids


class Topic(Model):
    id: str = Field(pattern=r'^[a-zA-Z0-9_-]{1,80}$')
    title: str = Field(min_length=1, max_length=300)
    hours: int = Field(ge=1, le=1000)
    plannedDate: date | None = None
    standard: str = Field(default='', max_length=2000)


class PlanContent(Model):
    title: str = Field(min_length=1, max_length=200)
    groupId: str = Field(min_length=1, max_length=80)
    subjectId: str = Field(min_length=1, max_length=80)
    start: date
    end: date
    topics: list[Topic] = Field(min_length=1, max_length=500)

    @model_validator(mode='after')
    def valid(self):
        if not 0 <= (self.end-self.start).days <= 730:
            raise ValueError('Период плана должен быть от одного дня до двух лет.')
        if len({t.id for t in self.topics}) != len(self.topics):
            raise ValueError('Идентификаторы тем не должны повторяться.')
        if any(t.plannedDate and not self.start <= t.plannedDate <= self.end for t in self.topics):
            raise ValueError('Плановая дата темы должна входить в период плана.')
        return self


class SavePlan(Model):
    version: int = Field(default=0, ge=0)
    content: PlanContent


class ArchivePlan(Model):
    version: int = Field(ge=1)
    archived: bool


class CopyPlan(Model):
    version: int = Field(ge=1)
    groupId: str = Field(min_length=1, max_length=80)
    start: date
    end: date

    @model_validator(mode='after')
    def valid(self):
        if not 0 <= (self.end-self.start).days <= 730:
            raise ValueError('Период копии должен быть от одного дня до двух лет.')
        return self


def tables(db):
    db.execute('''CREATE TABLE IF NOT EXISTS curriculum_plans (
        id TEXT PRIMARY KEY,workspace TEXT NOT NULL,group_id TEXT NOT NULL,subject_id TEXT NOT NULL,
        start TEXT NOT NULL,end TEXT NOT NULL,title TEXT NOT NULL,version INTEGER NOT NULL,
        archived INTEGER NOT NULL,updated TEXT NOT NULL,actor TEXT NOT NULL)''')
    db.execute('CREATE INDEX IF NOT EXISTS curriculum_scope ON curriculum_plans(workspace,group_id,subject_id,archived,start,end)')
    db.execute('''CREATE TABLE IF NOT EXISTS curriculum_versions (
        plan_id TEXT,version INTEGER,created TEXT,actor TEXT,document TEXT,archived INTEGER,
        PRIMARY KEY(plan_id,version))''')
    db.execute('''CREATE TABLE IF NOT EXISTS curriculum_bindings (
        workspace TEXT,lesson_key TEXT,plan_id TEXT,version INTEGER,topic_id TEXT,
        PRIMARY KEY(workspace,lesson_key,plan_id,version,topic_id))''')
    db.execute('''CREATE TABLE IF NOT EXISTS curriculum_publications (
        workspace TEXT,lesson_key TEXT,plan_id TEXT,topic_id TEXT,units INTEGER,day TEXT,
        PRIMARY KEY(workspace,lesson_key))''')
    db.execute('CREATE INDEX IF NOT EXISTS curriculum_progress ON curriculum_publications(workspace,plan_id,day,lesson_key)')


def plan_row(db, workspace, id):
    row=db.execute('SELECT * FROM curriculum_plans WHERE workspace=? AND id=?',(workspace,id)).fetchone()
    if not row: raise HTTPException(404,'Учебный план не найден.')
    return dict(zip(['id','workspace','groupId','subjectId','start','end','title','version','archived','updated','actor'],row))


def version_content(db, id, version):
    row=db.execute('SELECT document,created,actor,archived FROM curriculum_versions WHERE plan_id=? AND version=?',(id,version)).fetchone()
    if not row: raise HTTPException(404,'Редакция плана не найдена.')
    return json.loads(row[0]),row[1],row[2],bool(row[3])


def bind(db, ref, workspace, key, lesson):
    """Journal callers already verify lesson permissions and school/account revisions."""
    tables(db)
    ref=TopicReference.model_validate(ref).model_dump()
    row=plan_row(db,workspace,ref['planId'])
    content,_,_,_=version_content(db,row['id'],ref['version'])
    topic=next((t for t in content['topics'] if t['id']==ref['topicId']),None)
    if not topic: raise HTTPException(422,'Тема отсутствует в выбранной редакции плана.')
    retained=db.execute('SELECT 1 FROM curriculum_bindings WHERE workspace=? AND lesson_key=? AND plan_id=? AND version=? AND topic_id=?',
                        (workspace,key,row['id'],ref['version'],ref['topicId'])).fetchone()
    if not retained:
        if row['archived'] or row['version']!=ref['version']:
            raise HTTPException(409,'План обновлён или находится в архиве. Выберите тему заново.')
    if (content['groupId'],content['subjectId'])!=(lesson['groupId'],lesson['subjectId']) or not content['start']<=lesson['date']<=content['end']:
        raise HTTPException(422,'План не соответствует предмету, группе или дате урока.')
    db.execute('INSERT OR IGNORE INTO curriculum_bindings VALUES (?,?,?,?,?)',(workspace,key,row['id'],ref['version'],ref['topicId']))
    return ref | dict(planTitle=content['title'],title=topic['title'],standard=topic['standard'],plannedHours=topic['hours'])


def project(db, workspace, key, document):
    # Called atomically with publication/withdrawal, never on a mere draft save.
    ref=document['content'].get('curriculum') if document else None
    if not ref and not db.execute("SELECT 1 FROM sqlite_master WHERE name='curriculum_publications' AND type='table'").fetchone():
        return
    tables(db)
    db.execute('DELETE FROM curriculum_publications WHERE workspace=? AND lesson_key=?',(workspace,key))
    if ref:
        db.execute('INSERT INTO curriculum_publications VALUES (?,?,?,?,?,?)',
                   (workspace,key,ref['planId'],ref['topicId'],ref['units'],document['lesson']['date']))


class Curriculum:
    def __init__(self, materials):
        self.materials=materials
        self.journal,self.access,self.snapshots=materials.journal,materials.access,materials.snapshots

    def context(self, request, workspace):
        return self.materials.scope(request,workspace),self.snapshots.get(workspace)

    def recheck(self, db, user, workspace, school):
        self.materials.recheck(db,user,workspace)
        row=db.execute('SELECT revision FROM workspaces WHERE id=?',(workspace,)).fetchone()
        if not row or row[0]!=school.state.revision: raise HTTPException(409,'Учебные назначения изменились. Обновите страницу.')

    def pairs(self, school, user, workspace):
        ids=user_ids(user,workspace,'teacher') & {t.id for t in school.state.teachers}
        return {(w.groupId,w.subjectId) for w in school.state.workloads if user['role']=='admin' or ids & set(teacher_ids(w))}

    def require_pair(self, school, user, workspace, group, subject):
        if (group,subject) not in self.pairs(school,user,workspace):
            raise HTTPException(403,'Нет действующей учебной нагрузки по этому предмету и группе.')

    def detail(self, db, row, version=None):
        content,created,actor,archived=version_content(db,row['id'],version or row['version'])
        return dict(id=row['id'],version=version or row['version'],latestVersion=row['version'],
                    archived=archived,created=created,actor=actor,content=content)

    def accessible(self, db, workspace, id, school, user):
        row=plan_row(db,workspace,id)
        if user['role']!='admin': self.require_pair(school,user,workspace,row['groupId'],row['subjectId'])
        return row

    def save(self, db, workspace, user, school, body, row=None, archived=False):
        c=body.content.model_dump(mode='json')
        self.require_pair(school,user,workspace,c['groupId'],c['subjectId'])
        if body.version!=(row['version'] if row else 0): raise HTTPException(409,'План изменён в другом окне. Откройте актуальную редакцию.')
        if row and any(c[k]!=row[k] for k in ['groupId','subjectId','start','end']):
            raise HTTPException(422,'Предмет, группу и период меняйте через создание копии плана.')
        id=row['id'] if row else uuid4().hex
        if not archived and db.execute('''SELECT 1 FROM curriculum_plans WHERE workspace=? AND group_id=? AND subject_id=?
            AND archived=0 AND start<=? AND end>=? AND id<>? LIMIT 1''',(workspace,c['groupId'],c['subjectId'],c['end'],c['start'],id)).fetchone():
            raise HTTPException(409,'Для этой группы и предмета уже есть действующий план на пересекающийся период.')
        version=body.version+1;created=now().isoformat()
        db.execute('''INSERT INTO curriculum_plans VALUES (?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
            title=excluded.title,version=excluded.version,archived=excluded.archived,updated=excluded.updated,actor=excluded.actor''',
            (id,workspace,c['groupId'],c['subjectId'],c['start'],c['end'],c['title'],version,int(archived),created,user['name']))
        db.execute('INSERT INTO curriculum_versions VALUES (?,?,?,?,?,?)',(id,version,created,user['name'],encoded(c),int(archived)))
        self.access.record(db,'curriculum.saved',workspace,detail=f'{id}: редакция {version}')
        return self.detail(db,plan_row(db,workspace,id))

    def register(self, app, Workspace):
        @app.get('/api/journal/curriculum/options')
        def options(request:Request,workspace:Workspace='school'):
            user,school=self.context(request,workspace)
            with self.access.lock,self.access.connect() as db:
                self.access.tables(db)
                if not db.in_transaction: db.execute('BEGIN')
                self.recheck(db,user,workspace,school)
                groups={g.id:g.name for g in school.state.groups};subjects={s.id:s.name for s in school.state.subjects}
                return dict(start=school.state.settings.yearStart,end=school.state.settings.yearEnd,
                    pairs=[dict(groupId=g,group=groups[g],subjectId=s,subject=subjects[s]) for g,s in sorted(self.pairs(school,user,workspace))])

        @app.get('/api/journal/curriculum/lesson')
        def lesson_options(request:Request,day:date,lessonKey:str=Query(min_length=64,max_length=64),workspace:Workspace='school'):
            user,school=self.context(request,workspace)
            lesson=next((r for r in school.lesson_rows(day) if r['activity']=='lesson' and key_for(r)==lessonKey),None)
            if not lesson or not staff_read(user,workspace,lesson) or lesson['status']=='cancelled':
                raise HTTPException(403,'Нет доступа к действующему уроку.')
            with self.access.lock,self.access.connect() as db:
                tables(db);db.execute('BEGIN');self.recheck(db,user,workspace,school)
                rows=db.execute('''SELECT id FROM curriculum_plans WHERE workspace=? AND group_id=? AND subject_id=?
                    AND archived=0 AND start<=? AND end>=?''',(workspace,lesson['groupId'],lesson['subjectId'],day.isoformat(),day.isoformat())).fetchall()
                return dict(items=[self.detail(db,plan_row(db,workspace,r[0])) for r in rows])

        @app.get('/api/journal/curriculum')
        def listing(request:Request,groupId:str,subjectId:str,workspace:Workspace='school',archived:bool=False,
                    offset:int=Query(default=0,ge=0,le=100000)):
            user,school=self.context(request,workspace)
            with self.access.lock,self.access.connect() as db:
                tables(db);db.execute('BEGIN');self.recheck(db,user,workspace,school)
                self.require_pair(school,user,workspace,groupId,subjectId)
                rows=db.execute('''SELECT id FROM curriculum_plans WHERE workspace=? AND group_id=? AND subject_id=? AND archived=?
                    ORDER BY start DESC,id LIMIT 21 OFFSET ?''',(workspace,groupId,subjectId,int(archived),offset)).fetchall()
                return dict(items=[plan_row(db,workspace,r[0]) for r in rows[:20]],hasMore=len(rows)>20)

        @app.post('/api/journal/curriculum')
        def create(body:SavePlan,request:Request,workspace:Workspace='school'):
            user,school=self.context(request,workspace)
            with self.access.lock,self.access.connect() as db:
                tables(db);db.execute('BEGIN IMMEDIATE');self.recheck(db,user,workspace,school)
                return self.save(db,workspace,user,school,body)

        @app.get('/api/journal/curriculum/{id}')
        def get(id:str,request:Request,workspace:Workspace='school',version:int|None=Query(default=None,ge=1)):
            user,school=self.context(request,workspace)
            with self.access.lock,self.access.connect() as db:
                tables(db);db.execute('BEGIN');self.recheck(db,user,workspace,school)
                return self.detail(db,self.accessible(db,workspace,id,school,user),version)

        @app.put('/api/journal/curriculum/{id}')
        def update(id:str,body:SavePlan,request:Request,workspace:Workspace='school'):
            user,school=self.context(request,workspace)
            with self.access.lock,self.access.connect() as db:
                tables(db);db.execute('BEGIN IMMEDIATE');self.recheck(db,user,workspace,school)
                row=self.accessible(db,workspace,id,school,user)
                if row['archived']: raise HTTPException(409,'Сначала восстановите план из архива.')
                return self.save(db,workspace,user,school,body,row)

        @app.post('/api/journal/curriculum/{id}/archive')
        def archive(id:str,body:ArchivePlan,request:Request,workspace:Workspace='school'):
            user,school=self.context(request,workspace)
            with self.access.lock,self.access.connect() as db:
                tables(db);db.execute('BEGIN IMMEDIATE');self.recheck(db,user,workspace,school)
                row=self.accessible(db,workspace,id,school,user)
                content,*_=version_content(db,id,row['version'])
                return self.save(db,workspace,user,school,SavePlan(version=body.version,content=content),row,body.archived)

        @app.post('/api/journal/curriculum/{id}/copy')
        def copy(id:str,body:CopyPlan,request:Request,workspace:Workspace='school'):
            user,school=self.context(request,workspace)
            with self.access.lock,self.access.connect() as db:
                tables(db);db.execute('BEGIN IMMEDIATE');self.recheck(db,user,workspace,school)
                self.accessible(db,workspace,id,school,user)
                content,*_=version_content(db,id,body.version)
                content.update(groupId=body.groupId,start=body.start,end=body.end)
                for topic in content['topics']: topic.update(id=uuid4().hex,plannedDate=None)
                return self.save(db,workspace,user,school,SavePlan(content=content))

        @app.get('/api/journal/curriculum/{id}/history')
        def history(id:str,request:Request,workspace:Workspace='school',before:int|None=Query(default=None,ge=1)):
            user,school=self.context(request,workspace)
            with self.access.lock,self.access.connect() as db:
                tables(db);db.execute('BEGIN');self.recheck(db,user,workspace,school)
                self.accessible(db,workspace,id,school,user)
                rows=db.execute('SELECT version,created,actor,archived FROM curriculum_versions WHERE plan_id=? AND version<? ORDER BY version DESC LIMIT 21',
                                (id,before or 2**63-1)).fetchall()
                return dict(items=[dict(version=r[0],created=r[1],actor=r[2],archived=bool(r[3])) for r in rows[:20]],hasMore=len(rows)>20)

        @app.get('/api/journal/curriculum/{id}/progress')
        def progress(id:str,request:Request,workspace:Workspace='school',asOf:date|None=None,
                     lessonOffset:int=Query(default=0,ge=0,le=100000)):
            user,school=self.context(request,workspace)
            cutoff=min(asOf or date.max,now().astimezone(ZoneInfo('Asia/Qyzylorda')).date()).isoformat()
            with self.access.lock,self.access.connect() as db:
                tables(db);db.execute('BEGIN');self.recheck(db,user,workspace,school)
                row=self.accessible(db,workspace,id,school,user);content,*_=version_content(db,id,row['version'])
                topics={t['id']:dict(id=t['id'],title=t['title'],planned=t['hours'],actual=0,review=0,upcoming=0,retired=False) for t in content['topics']}
                lessons=[]
                if db.execute("SELECT 1 FROM sqlite_master WHERE name='journal_entries' AND type='table'").fetchone():
                    records=db.execute('''SELECT p.lesson_key,p.topic_id,p.units,p.day,
                        json_object('sourceKey',json_extract(j.published_document,'$.lesson.sourceKey'),
                        'date',json_extract(j.published_document,'$.lesson.date'),
                        'start',json_extract(j.published_document,'$.lesson.start'),
                        'end',json_extract(j.published_document,'$.lesson.end'),
                        'groupId',json_extract(j.published_document,'$.lesson.groupId'),
                        'subjectId',json_extract(j.published_document,'$.lesson.subjectId')),
                        j.published_version,json_extract(j.published_document,'$.content.curriculum.title')
                        FROM curriculum_publications p JOIN journal_entries j ON j.workspace=p.workspace AND j.lesson_key=p.lesson_key
                        WHERE p.workspace=? AND p.plan_id=? ORDER BY p.day,p.lesson_key''',(workspace,id)).fetchall()
                    for key,topic,units,day,raw,version,title in records:
                        lesson=json.loads(raw);current=school.current_lesson(lesson)
                        status='review' if not current or current['status']=='cancelled' or any(current[k]!=lesson[k] for k in ['date','start','end','groupId','subjectId']) else 'upcoming' if day>cutoff else 'actual'
                        topics.setdefault(topic,dict(id=topic,title=title,planned=0,actual=0,review=0,upcoming=0,retired=True))[status]+=units
                        lessons.append(dict(lessonKey=key,day=day,topicId=topic,title=title,units=units,status=status,version=version))
                return dict(asOf=cutoff,topics=list(topics.values()),lessons=lessons[lessonOffset:lessonOffset+50],
                    lessonOffset=lessonOffset,totalLessons=len(lessons),hasMore=lessonOffset+50<len(lessons),
                    totals={k:sum(t[k] for t in topics.values()) for k in ['planned','actual','review','upcoming']})
