"""Reusable staff materials; immutable versions pinned into dated journals."""
import json
from typing import Literal
from uuid import uuid4

from fastapi import HTTPException, Query, Request
from pydantic import Field, model_validator
from .access import LOCAL, now
from .models import Model
from .journal import staff_read, user_ids
from .notifications import encoded


class MaterialContent(Model):
    title: str = Field(min_length=1, max_length=200)
    subject: str = Field(default='', max_length=160)
    topics: list[str] = Field(default_factory=list, max_length=20)
    text: str = Field(default='', max_length=20000)
    fileIds: list[str] = Field(default_factory=list, max_length=5)

    @model_validator(mode='after')
    def validate_content(self):
        if any(not t or len(t)>100 for t in self.topics):
            raise ValueError('Тема должна содержать от 1 до 100 символов.')
        if len({t.casefold() for t in self.topics})!=len(self.topics):
            raise ValueError('Темы не должны повторяться.')
        if len(set(self.fileIds))!=len(self.fileIds): raise ValueError('Файл указан несколько раз.')
        if not self.text and not self.fileIds: raise ValueError('Добавьте текст или файл материала.')
        return self


class SaveMaterial(Model):
    version: int = Field(default=0, ge=0)
    content: MaterialContent
    visibility: Literal['private','staff'] = 'private'


class ArchiveMaterial(Model):
    version: int = Field(ge=1)
    archived: bool


class CopyMaterial(Model):
    version: int = Field(ge=1)


def tables(db):
    db.execute('''CREATE TABLE IF NOT EXISTS teaching_materials (
        id TEXT PRIMARY KEY,workspace TEXT NOT NULL,owner_id TEXT NOT NULL,owner_name TEXT NOT NULL,
        version INTEGER NOT NULL,visibility TEXT NOT NULL,archived INTEGER NOT NULL,updated TEXT NOT NULL,
        title TEXT NOT NULL,subject TEXT NOT NULL,topics TEXT NOT NULL,search_text TEXT NOT NULL)''')
    db.execute('CREATE INDEX IF NOT EXISTS teaching_materials_scope ON teaching_materials(workspace,archived,updated DESC,id)')
    db.execute('CREATE INDEX IF NOT EXISTS teaching_materials_owner ON teaching_materials(workspace,owner_id,archived)')
    db.execute('''CREATE TABLE IF NOT EXISTS teaching_material_versions (
        material_id TEXT,version INTEGER,created TEXT,actor_name TEXT,document TEXT,visibility TEXT,
        PRIMARY KEY(material_id,version))''')
    db.execute('''CREATE TABLE IF NOT EXISTS teaching_material_lessons (
        workspace TEXT,lesson_key TEXT,material_id TEXT,version INTEGER,
        PRIMARY KEY(workspace,lesson_key,material_id,version))''')
    from .teaching_material_files import tables as file_tables
    file_tables(db)


def row_for(db, workspace, id):
    row=db.execute('SELECT * FROM teaching_materials WHERE workspace=? AND id=?',(workspace,id)).fetchone()
    if not row: raise HTTPException(404,'Материал не найден.')
    return dict(zip(['id','workspace','ownerId','owner','version','visibility','archived','updated','title','subject','topics','search'],row))


def can_edit(user, row):
    return user['role']=='admin' or row['ownerId']==user['id']


def readable(user, row):
    return can_edit(user,row) or row['visibility']=='staff' and not row['archived']


def version_document(db, id, version):
    row=db.execute('SELECT document,created,actor_name FROM teaching_material_versions WHERE material_id=? AND version=?',(id,version)).fetchone()
    if not row: raise HTTPException(404,'Версия материала не найдена.')
    return json.loads(row[0]),row[1],row[2]


def require_version_access(db, user, row, version):
    shared=db.execute("SELECT 1 FROM teaching_material_versions WHERE material_id=? AND version=? AND visibility='staff'",(row['id'],version)).fetchone()
    if not readable(user,row) or not can_edit(user,row) and not shared:
        raise HTTPException(404,'Версия материала не найдена.')


def snapshot(db, id, version):
    from .teaching_material_files import attachments
    document,_,_=version_document(db,id,version)
    return dict(id=id,version=version,**{k:document[k] for k in ['title','subject','topics','text']},files=attachments(db,document['fileIds']))


def bind(db, refs, workspace, key, user):
    """Called in the journal write transaction. Never trust client snapshot fields."""
    if not refs: return []
    tables(db)
    if len({r['id'] for r in refs})!=len(refs): raise HTTPException(422,'Один материал можно прикрепить к уроку только один раз.')
    result=[]
    for ref in refs:
        id,version=ref['id'],ref['version']
        row=row_for(db,workspace,id)
        retained=db.execute('SELECT 1 FROM teaching_material_lessons WHERE workspace=? AND lesson_key=? AND material_id=? AND version=?',
                            (workspace,key,id,version)).fetchone()
        if not retained and (not readable(user,row) or row['archived']):
            raise HTTPException(403,'Материал недоступен для нового урока. Обновите выбор из библиотеки.')
        if not retained: require_version_access(db,user,row,version)
        result.append(snapshot(db,id,version))
        db.execute('INSERT OR IGNORE INTO teaching_material_lessons VALUES (?,?,?,?)',(workspace,key,id,version))
    return result


class TeachingMaterials:
    def __init__(self, journal, snapshots):
        self.journal,self.access,self.snapshots=journal,journal.access,snapshots

    def scope(self, request, workspace):
        user=request.state.user or LOCAL
        if not (user['role']=='admin' and workspace in user['workspaces'] or
                user['role']=='teacher' and user_ids(user,workspace,'teacher')):
            raise HTTPException(403,'Библиотека доступна учителям и администратору этого пространства.')
        return user

    def recheck(self, db, user, workspace):
        self.journal.recheck_account(db,user)
        if user['role']=='teacher':
            assigned=user_ids(user,workspace,'teacher')
            current={r[0] for r in db.execute("SELECT json_extract(t.value,'$.id') FROM workspaces w,json_each(w.document,'$.teachers') t WHERE w.id=?",(workspace,))}
            if not current & assigned: raise HTTPException(403,'Нет действующего назначения учителя в этом пространстве.')

    def detail(self, db, row, user, version=None):
        if not readable(user,row): raise HTTPException(404,'Материал не найден.')
        version=version or row['version']
        require_version_access(db,user,row,version)
        document,created,author=version_document(db,row['id'],version)
        return dict(id=row['id'],version=version,latestVersion=row['version'],owner=row['owner'],visibility=row['visibility'],
                    archived=bool(row['archived']),canEdit=can_edit(user,row),created=created,actor=author,
                    canUseInPlan=bool(not row['archived'] and row['visibility']=='staff' and db.execute(
                        "SELECT 1 FROM teaching_material_versions WHERE material_id=? AND version=? AND visibility='staff'",(row['id'],version)).fetchone()),
                    content=document,files=snapshot(db,row['id'],version)['files'])

    def save(self, db, workspace, user, body, row=None, *, trusted_files=False, archived=False):
        from .teaching_material_files import bind_files
        if row and (not can_edit(user,row) or row['archived'] and not archived):
            raise HTTPException(403,'Изменять материал может автор или администратор. Сначала восстановите архивный материал.')
        if body.version!=(row['version'] if row else 0): raise HTTPException(409,'Материал изменён в другом окне. Откройте актуальную версию.')
        id=row['id'] if row else uuid4().hex
        version=body.version+1
        document=body.content.model_dump(mode='json')
        bind_files(db,document['fileIds'],workspace,id,version,user,trusted=trusted_files)
        created=now().isoformat()
        search='\n'.join([document['title'],document['subject'],*document['topics'],document['text']]).casefold()
        owner,owner_name=(row['ownerId'],row['owner']) if row else (user['id'],user['name'])
        db.execute('''INSERT INTO teaching_materials VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET version=excluded.version,visibility=excluded.visibility,archived=excluded.archived,
            updated=excluded.updated,title=excluded.title,subject=excluded.subject,topics=excluded.topics,search_text=excluded.search_text''',
            (id,workspace,owner,owner_name,version,body.visibility,int(archived),created,document['title'],document['subject'],encoded(document['topics']),search))
        db.execute('INSERT INTO teaching_material_versions VALUES (?,?,?,?,?,?)',(id,version,created,user['name'],encoded(document),body.visibility))
        self.access.record(db,'material.saved',workspace,detail=f'{id}: редакция {version}')
        return self.detail(db,row_for(db,workspace,id),user)

    def lesson_file(self, db, user, workspace, key, material, version, file, school):
        """Staff see retained lesson versions, families only the current publication."""
        self.journal.recheck_account(db,user)
        revision=db.execute('SELECT revision FROM workspaces WHERE id=?',(workspace,)).fetchone()
        if not revision or revision[0]!=school.state.revision: raise HTTPException(409,'Расписание изменилось. Обновите урок.')
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='journal_entries'").fetchone():
            raise HTTPException(404,'Материал урока не найден.')
        raw=db.execute('SELECT * FROM journal_entries WHERE workspace=? AND lesson_key=?',(workspace,key)).fetchone()
        if not raw: raise HTTPException(404,'Материал урока не найден.')
        record=self.journal.unpack(raw)
        if user['role'] in ['admin','teacher']:
            current=school.current_lesson(record['document']['lesson'])
            if not (staff_read(user,workspace,record['document']['lesson']) or current and staff_read(user,workspace,current)):
                raise HTTPException(403,'Нет доступа к этому уроку.')
            visible=db.execute('SELECT 1 FROM teaching_material_lessons WHERE workspace=? AND lesson_key=? AND material_id=? AND version=?',
                               (workspace,key,material,version)).fetchone()
        else:
            published=record['published']
            allowed=user_ids(user,workspace,'student')
            visible=published and any(r['studentId'] in allowed for r in published['content']['records']) and any(
                m['id']==material and m['version']==version for m in published['content'].get('materials',[]))
        if not visible: raise HTTPException(404,'Материал не опубликован для вас.')
        document,_,_=version_document(db,material,version)
        if file not in document['fileIds']: raise HTTPException(404,'Файл не найден в этой версии.')

    def register(self, app, Workspace):
        from .teaching_material_files import MaterialFiles
        MaterialFiles(self).register(app,Workspace)

        @app.get('/api/journal/library')
        def listing(request:Request,workspace:Workspace='school',q:str=Query(default='',max_length=200),
                    mine:bool=False,archived:bool=False,sharedOnly:bool=False,offset:int=Query(default=0,ge=0,le=100000),limit:int=Query(default=20,ge=1,le=50)):
            user=self.scope(request,workspace)
            with self.access.lock,self.access.connect() as db:
                tables(db);db.execute('BEGIN');self.recheck(db,user,workspace)
                where='workspace=? AND archived=?';params=[workspace,int(archived)]
                if sharedOnly: where+=" AND visibility='staff' AND archived=0"
                if mine: where+=' AND owner_id=?';params.append(user['id'])
                if user['role']!='admin':
                    where+=' AND (owner_id=? OR (visibility=\'staff\' AND archived=0))';params.append(user['id'])
                for word in q.casefold().split():
                    where+=' AND instr(search_text,?)>0';params.append(word)
                rows=db.execute(f'''SELECT id,version,title,subject,topics,owner_name,visibility,archived,updated,owner_id
                    FROM teaching_materials WHERE {where} ORDER BY updated DESC,id LIMIT ? OFFSET ?''',(*params,limit+1,offset)).fetchall()
                items=[dict(id=r[0],version=r[1],title=r[2],subject=r[3],topics=json.loads(r[4]),owner=r[5],visibility=r[6],archived=bool(r[7]),
                            updated=r[8],canEdit=user['role']=='admin' or r[9]==user['id']) for r in rows[:limit]]
                return dict(items=items,hasMore=len(rows)>limit,offset=offset)

        @app.post('/api/journal/library')
        def create(body:SaveMaterial,request:Request,workspace:Workspace='school'):
            user=self.scope(request,workspace)
            with self.access.lock,self.access.connect() as db:
                tables(db);db.execute('BEGIN IMMEDIATE');self.recheck(db,user,workspace)
                return self.save(db,workspace,user,body)

        @app.get('/api/journal/library/{id}')
        def get(id:str,request:Request,workspace:Workspace='school',version:int|None=Query(default=None,ge=1)):
            user=self.scope(request,workspace)
            with self.access.lock,self.access.connect() as db:
                tables(db);db.execute('BEGIN');self.recheck(db,user,workspace)
                return self.detail(db,row_for(db,workspace,id),user,version)

        @app.put('/api/journal/library/{id}')
        def update(id:str,body:SaveMaterial,request:Request,workspace:Workspace='school'):
            user=self.scope(request,workspace)
            with self.access.lock,self.access.connect() as db:
                tables(db);db.execute('BEGIN IMMEDIATE');self.recheck(db,user,workspace)
                return self.save(db,workspace,user,body,row_for(db,workspace,id))

        @app.get('/api/journal/library/{id}/history')
        def history(id:str,request:Request,workspace:Workspace='school',before:int|None=Query(default=None,ge=1)):
            user=self.scope(request,workspace)
            with self.access.lock,self.access.connect() as db:
                tables(db);db.execute('BEGIN');self.recheck(db,user,workspace)
                row=row_for(db,workspace,id)
                if not readable(user,row): raise HTTPException(404,'Материал не найден.')
                rows=db.execute("SELECT version,created,actor_name FROM teaching_material_versions WHERE material_id=? AND (? IS NULL OR version<?) AND (? OR visibility='staff') ORDER BY version DESC LIMIT 21",(id,before,before,can_edit(user,row))).fetchall()
                return dict(items=[dict(version=r[0],created=r[1],actor=r[2]) for r in rows[:20]],hasMore=len(rows)>20)

        @app.post('/api/journal/library/{id}/archive')
        def archive(id:str,body:ArchiveMaterial,request:Request,workspace:Workspace='school'):
            user=self.scope(request,workspace)
            with self.access.lock,self.access.connect() as db:
                tables(db);db.execute('BEGIN IMMEDIATE');self.recheck(db,user,workspace)
                row=row_for(db,workspace,id)
                if not can_edit(user,row): raise HTTPException(403,'Архивировать материал может автор или администратор.')
                if row['version']!=body.version: raise HTTPException(409,'Материал изменён. Обновите его перед архивированием.')
                document,_,_=version_document(db,id,body.version)
                # The same version counter covers content, sharing and archive changes.
                row['archived']=False
                result=self.save(db,workspace,user,SaveMaterial(version=body.version,content=MaterialContent(**document),visibility=row['visibility']),row,archived=body.archived)
                self.access.record(db,'material.archived' if body.archived else 'material.restored',workspace,detail=id)
                return result

        @app.post('/api/journal/library/{id}/copy')
        def copy(id:str,body:CopyMaterial,request:Request,workspace:Workspace='school'):
            user=self.scope(request,workspace)
            with self.access.lock,self.access.connect() as db:
                tables(db);db.execute('BEGIN IMMEDIATE');self.recheck(db,user,workspace)
                source=self.detail(db,row_for(db,workspace,id),user,body.version)
                document=source['content']|dict(title=(source['content']['title'][:190]+' · копия'))
                result=self.save(db,workspace,user,SaveMaterial(content=MaterialContent(**document)),trusted_files=True)
                self.access.record(db,'material.copied',workspace,detail=f"{id} v{body.version} → {result['id']}")
                return result
