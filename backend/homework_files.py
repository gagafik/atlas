"""Private, bounded homework attachments, outside the publicly served directory."""
from datetime import timedelta
import hashlib
import os
from pathlib import Path
import re
import threading
from uuid import uuid4

from fastapi import HTTPException
from .access import now

MAX_FILE = 10*1024*1024
MAX_STUDENT = 250*1024*1024
MAX_TASK_FILES = 30
UPLOAD_SLOTS = threading.BoundedSemaphore(4)


def total_stored(db):
    total=0
    for table in ['homework_files','message_files','absence_files','assignment_files','teaching_material_files','lost_found_files','school_site_files','admission_files']:
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(table,)).fetchone():
            total+=db.execute(f'SELECT coalesce(sum(size),0) FROM {table}').fetchone()[0]
    return total


def total_limit():
    try: value=int(os.environ.get('ATLAS_UPLOAD_TOTAL_MB','2048'))
    except ValueError: value=2048
    return max(10,min(value,1048576))*1024*1024


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
    all_size=total_stored(db)
    own_size=db.execute('SELECT coalesce(sum(size),0) FROM homework_files WHERE workspace=? AND student_id=?',(workspace,student)).fetchone()[0]
    count=db.execute('SELECT count(*) FROM homework_files WHERE workspace=? AND lesson_key=? AND student_id=?',(workspace,key,student)).fetchone()[0]
    pending=db.execute("SELECT count(*) FROM homework_files WHERE workspace=? AND student_id=? AND status='uploading'",(workspace,student)).fetchone()[0]
    if all_size+MAX_FILE>total_limit() or own_size+MAX_FILE>MAX_STUDENT:
        raise HTTPException(413,'Лимит хранилища достигнут. Обратитесь к администратору.')
    if count>=MAX_TASK_FILES: raise HTTPException(413,'Достигнут лимит файлов для этого задания с учётом прежних отправок.')
    if pending>=2: raise HTTPException(429,'Дождитесь окончания текущих загрузок.')
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


def collect_unused(db):
    # Bound each cleanup and retain every attachment used in submitted history.
    rows=db.execute('''SELECT f.id FROM homework_files f WHERE
        ((f.status='uploading' AND f.created<?) OR (f.status='ready' AND f.created<?))
        AND NOT EXISTS(SELECT 1 FROM homework_file_links l WHERE l.file_id=f.id)
        AND NOT EXISTS(SELECT 1 FROM homework_draft_files d WHERE d.file_id=f.id)
        ORDER BY f.created LIMIT 20''',((now()-timedelta(minutes=30)).isoformat(),(now()-timedelta(days=1)).isoformat())).fetchall()
    root=root_for(db)
    for (id,) in rows:
        try: file_path(root,id).unlink(missing_ok=True)
        except OSError: continue  # Keep metadata/quota until a later cleanup succeeds.
        db.execute('DELETE FROM homework_files WHERE id=?',(id,))
