"""Private, bounded homework attachments, outside the publicly served directory."""
import asyncio
from datetime import timedelta
import hashlib
import os
from pathlib import Path
import re
import shutil
import threading
import time
from uuid import uuid4

from fastapi import HTTPException
from .access import now

MAX_FILE = 10*1024*1024
MAX_STUDENT = 250*1024*1024
MAX_TASK_FILES = 30
# module -> (table, folder, env, default MiB, title, import path). Folders equal tools/backup_school.py STORES.
STORES = {
    'homework':('homework_files','homework','ATLAS_UPLOAD_HOMEWORK_MB',30720,'Домашние работы','backend.homework_files'),
    'messages':('message_files','messages','ATLAS_UPLOAD_MESSAGES_MB',8192,'Сообщения','backend.message_files'),
    'absences':('absence_files','absences','ATLAS_UPLOAD_ABSENCES_MB',2048,'Объяснительные','backend.absence_files'),
    'assignments':('assignment_files','assignments','ATLAS_UPLOAD_ASSIGNMENTS_MB',8192,'Материалы уроков','backend.assignment_files'),
    'materials':('teaching_material_files','materials','ATLAS_UPLOAD_MATERIALS_MB',8192,'Библиотека материалов','backend.teaching_material_files'),
    'quizzes':('quiz_files','quizzes','ATLAS_UPLOAD_QUIZZES_MB',4096,'Тесты','backend.quiz_files'),
    'lost-found':('lost_found_files','lost-found','ATLAS_UPLOAD_LOST_FOUND_MB',1024,'Бюро находок','backend.lost_found_files'),
    'school-site':('school_site_files','school-site','ATLAS_UPLOAD_SCHOOL_SITE_MB',2048,'Сайт школы','backend.school_site_files'),
    'admissions':('admission_files','admissions','ATLAS_UPLOAD_ADMISSIONS_MB',2048,'Приём','backend.admission_files')}


def setting(name, default, low, high):
    # Empty, unset or invalid values mean the default; the result is always clamped.
    try: value=int(os.environ.get(name) or default)
    except ValueError: value=default
    return max(low,min(value,high))


def megabytes(name, default):
    return setting(name,default,10,1048576)*1024*1024


QUEUE = setting('ATLAS_UPLOAD_QUEUE',64,0,1024)
# Slots, queue and wait are read once at start: one API process, changing them needs a restart.
UPLOAD_SLOTS = threading.BoundedSemaphore(setting('ATLAS_UPLOAD_SLOTS',16,1,64))
UPLOAD_WAITERS = threading.BoundedSemaphore(QUEUE)
UPLOAD_WAIT = setting('ATLAS_UPLOAD_WAIT_SECONDS',15,0,60)
RETRY_AFTER = 5


def busy(detail='Сервер принимает другие файлы. Повторите немного позже.'):
    return HTTPException(429,detail,headers={'Retry-After':str(RETRY_AFTER)})


async def acquire_slot():
    # A short queue absorbs bursts; a full queue answers at once so clients back off.
    # Polling never blocks the event loop or a threadpool worker.
    slots=UPLOAD_SLOTS
    if slots.acquire(blocking=False): return slots
    if not UPLOAD_WAITERS.acquire(blocking=False): raise busy()
    try:
        deadline=time.monotonic()+UPLOAD_WAIT
        while (left:=deadline-time.monotonic())>0:
            await asyncio.sleep(min(0.2,left))
            if slots.acquire(blocking=False): return slots
        raise busy()
    finally: UPLOAD_WAITERS.release()


def columns(db, table):
    return {r[1] for r in db.execute(f'PRAGMA table_info({table})')}


def stored(db, table):
    # A deduplicated blob (several rows with one blob_id) counts once.
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(table,)).fetchone(): return 0
    if 'blob_id' in columns(db,table):
        return db.execute(f'SELECT coalesce(sum(size),0) FROM (SELECT max(size) size FROM {table} GROUP BY coalesce(blob_id,id))').fetchone()[0]
    return db.execute(f'SELECT coalesce(sum(size),0) FROM {table}').fetchone()[0]


def module_stored(db, module):
    return stored(db,STORES[module][0])


def total_stored(db):
    return sum(stored(db,table) for table,*_ in STORES.values())


def total_limit():
    return megabytes('ATLAS_UPLOAD_TOTAL_MB',65536)


def module_limit(module):
    return megabytes(STORES[module][2],STORES[module][3])


def reserve_bytes():
    return setting('ATLAS_UPLOAD_RESERVE_MB',2048,0,1048576)*1024*1024


def free_bytes(db):
    try: return shutil.disk_usage(Path(db.execute('PRAGMA database_list').fetchone()[2]).resolve().parent).free
    except OSError: return None  # Never block uploads because the probe failed.


def require_capacity(db, module, extra=None):
    # Called inside the reservation transaction, so concurrent reservations are counted.
    extra=MAX_FILE if extra is None else extra
    if module_stored(db,module)+extra>module_limit(module):
        raise HTTPException(413,f'Хранилище раздела «{STORES[module][4]}» заполнено. Обратитесь к администратору.')
    if total_stored(db)+extra>total_limit(): raise HTTPException(413,'Лимит хранилища достигнут. Обратитесь к администратору.')
    free=free_bytes(db)
    if free is not None and free-extra<reserve_bytes():
        raise HTTPException(507,'На сервере заканчивается место для файлов. Обратитесь к администратору.')


def tables(db):
    db.execute('''CREATE TABLE IF NOT EXISTS homework_files (
        id TEXT PRIMARY KEY,workspace TEXT,lesson_key TEXT,student_id TEXT,owner_id TEXT,
        filename TEXT,size INTEGER,created TEXT,status TEXT,sha256 TEXT)''')
    db.execute('CREATE INDEX IF NOT EXISTS homework_files_target ON homework_files(workspace,lesson_key,student_id,status)')
    db.execute('CREATE INDEX IF NOT EXISTS homework_files_owner ON homework_files(workspace,student_id)')
    db.execute('CREATE INDEX IF NOT EXISTS homework_files_created ON homework_files(status,created)')
    db.execute('CREATE TABLE IF NOT EXISTS homework_file_links (file_id TEXT,history_id INTEGER,PRIMARY KEY(file_id,history_id))')
    db.execute('CREATE TABLE IF NOT EXISTS homework_draft_files (file_id TEXT PRIMARY KEY,workspace TEXT,lesson_key TEXT,student_id TEXT)')
    db.execute('CREATE INDEX IF NOT EXISTS homework_draft_files_target ON homework_draft_files(workspace,lesson_key,student_id)')


def root_for(db):
    database=Path(db.execute('PRAGMA database_list').fetchone()[2]).resolve()
    return database.with_suffix('.files')/'homework'


def file_path(root, id):
    if not re.fullmatch(r'[a-f0-9]{32}',id): raise HTTPException(404,'Файл не найден.')
    return root/(id+'.bin')


def name_for(filename):
    name=filename.replace('\\','/').rsplit('/',1)[-1].strip()
    if not name or len(name)>180 or any(ord(c)<32 or ord(c)==127 for c in name):
        raise HTTPException(422,'Используйте имя файла длиной до 180 символов без управляющих знаков.')
    if Path(name).suffix.lower() not in ['.pdf','.png','.jpg','.jpeg','.txt','.docx','.xlsx','.pptx']:
        raise HTTPException(422,'Можно приложить PDF, PNG, JPG, TXT, DOCX, XLSX или PPTX.')
    return name


def validate_file(path, name):
    with path.open('rb') as file:
        prefix=file.read(8)
        suffix=Path(name).suffix.lower()
        valid=(suffix=='.pdf' and prefix.startswith(b'%PDF-') or
               suffix=='.png' and prefix==b'\x89PNG\r\n\x1a\n' or
               suffix in ['.jpg','.jpeg'] and prefix.startswith(b'\xff\xd8\xff') or
               suffix in ['.docx','.xlsx','.pptx'] and prefix.startswith(b'PK\x03\x04') or
               suffix=='.txt' and b'\0' not in prefix)
        if not valid: raise HTTPException(422,'Содержимое не соответствует выбранному типу файла.')
        file.seek(0)
        digest=hashlib.file_digest(file,'sha256').hexdigest()
    return digest


def meta(row):
    return dict(id=row[0],filename=row[5],size=row[6],created=row[7])


def reserve(db, workspace, key, student, owner, filename):
    name=name_for(filename)
    own_size=db.execute('SELECT coalesce(sum(size),0) FROM homework_files WHERE workspace=? AND student_id=?',(workspace,student)).fetchone()[0]
    count=db.execute('SELECT count(*) FROM homework_files WHERE workspace=? AND lesson_key=? AND student_id=?',(workspace,key,student)).fetchone()[0]
    pending=db.execute("SELECT count(*) FROM homework_files WHERE workspace=? AND student_id=? AND status='uploading'",(workspace,student)).fetchone()[0]
    if own_size+MAX_FILE>MAX_STUDENT: raise HTTPException(413,'Лимит хранилища достигнут. Обратитесь к администратору.')
    require_capacity(db,'homework')
    if count>=MAX_TASK_FILES: raise HTTPException(413,'Достигнут лимит файлов для этого задания с учётом прежних отправок.')
    if pending>=2: raise busy('Дождитесь окончания текущих загрузок.')
    id=uuid4().hex
    db.execute('INSERT INTO homework_files VALUES (?,?,?,?,?,?,?,?,?,NULL)',
               (id,workspace,key,student,owner,name,MAX_FILE,now().isoformat(),'uploading'))
    return id,name,root_for(db)


def check_files(db, ids, workspace, key, student):
    if len(set(ids))!=len(ids): raise HTTPException(422,'Один файл указан несколько раз.')
    for id in ids:
        if not db.execute("SELECT 1 FROM homework_files WHERE id=? AND workspace=? AND lesson_key=? AND student_id=? AND status='ready'",
                          (id,workspace,key,student)).fetchone():
            raise HTTPException(422,'Файл не найден или относится к другому ученику/заданию.')


def cutoffs(days=1):
    # Incomplete uploads expire after 30 minutes, never-used ready uploads after `days`.
    return (now()-timedelta(minutes=30)).isoformat(),(now()-timedelta(days=days)).isoformat()


def blob_of(db, table, id):
    # The physical file of a row: its own id unless a deduplicated blob_id points elsewhere.
    if 'blob_id' not in columns(db,table): return id
    row=db.execute(f'SELECT coalesce(blob_id,id) FROM {table} WHERE id=?',(id,)).fetchone()
    return row[0] if row else id


def referenced(db, table, blob, exclude=None):
    if 'blob_id' in columns(db,table):
        return bool(db.execute(f"SELECT 1 FROM {table} WHERE (blob_id=? OR (blob_id IS NULL AND id=?)) AND id<>coalesce(?,'') LIMIT 1",(blob,blob,exclude)).fetchone())
    return bool(db.execute(f"SELECT 1 FROM {table} WHERE id=? AND id<>coalesce(?,'') LIMIT 1",(blob,exclude)).fetchone())


def release_row(db, table, root, id):
    # Delete the row; its bytes only when no other row still points at them.
    # OSError propagates so callers keep the row (and its quota) until a later run.
    blob=blob_of(db,table,id)
    if not referenced(db,table,blob,exclude=id): file_path(root,blob).unlink(missing_ok=True)
    db.execute(f'DELETE FROM {table} WHERE id=?',(id,))


def unused_rows(db, limit=20, days=1):
    # Select-only: what collect_unused would delete. Retain every attachment used in submitted history.
    stale,old=cutoffs(days)
    return [(r[0],r[1]) for r in db.execute('''SELECT f.id,f.size FROM homework_files f WHERE
        ((f.status='uploading' AND f.created<?) OR (f.status='ready' AND f.created<?))
        AND NOT EXISTS(SELECT 1 FROM homework_file_links l WHERE l.file_id=f.id)
        AND NOT EXISTS(SELECT 1 FROM homework_draft_files d WHERE d.file_id=f.id)
        ORDER BY f.created LIMIT ?''',(stale,old,limit))]


def collect_unused(db):
    # Bounded online cleanup.
    root=root_for(db)
    for id,_ in unused_rows(db):
        try: release_row(db,'homework_files',root,id)
        except OSError: continue  # Keep metadata/quota until a later cleanup succeeds.
