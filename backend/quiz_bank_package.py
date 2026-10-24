"""Bounded, portable Atlas question packages. Never extract archive paths."""
from contextlib import contextmanager
import hashlib
import json
import os
import stat
import struct
from typing import Literal
import zlib
from zipfile import ZipFile, BadZipFile, ZIP_STORED, ZIP_DEFLATED

from fastapi import HTTPException
from pydantic import Field, ValidationError, model_validator
from .models import Model
from .quiz_models import Question, Reference
from . import teaching_media as media
from .notifications import encoded

MAX_ARCHIVE=55*1024*1024
MAX_TOTAL=50*1024*1024
MAX_MANIFEST=2*1024*1024
MAX_FILES=200
MAX_ENTRIES=MAX_FILES+1
MAX_DIRECTORY=256*1024


class Entry(Model):
    subject: str=Field(default='',max_length=160)
    tags: list[str]=Field(default_factory=list,max_length=10)
    archived: bool=False
    question: Question

    @model_validator(mode='after')
    def valid(self):
        if any(not t.strip() or len(t)>60 for t in self.tags):raise ValueError('Метки должны содержать от 1 до 60 символов.')
        if self.question.attachments or self.question.solutionAttachments:
            raise ValueError('Описания вложений должны находиться в разделе files пакета.')
        return self


class PackageFile(Model):
    id: str=Field(pattern=r'^[a-f0-9]{32}$')
    filename: str=Field(min_length=1,max_length=180)
    size: int=Field(ge=1,le=10*1024*1024)
    sha256: str=Field(pattern=r'^[a-f0-9]{64}$')


class Package(Model):
    format: Literal['atlas.quiz-bank']
    version: Literal[1]
    questions: list[Entry]=Field(min_length=1,max_length=100)
    files: list[PackageFile]=Field(default_factory=list,max_length=MAX_FILES)

    @model_validator(mode='after')
    def valid(self):
        ids=[f.id for f in self.files]
        used={id for q in self.questions for id in q.question.fileIds+q.question.solutionFileIds}
        if len(ids)!=len(set(ids)) or used!=set(ids):raise ValueError('Файлы должны быть уникальными и точно соответствовать ссылкам в вопросах.')
        if sum(f.size for f in self.files)>MAX_TOTAL:raise ValueError('Общий размер вложений не должен превышать 50 МБ.')
        for file in self.files:
            try:name=media.name_for(file.filename)
            except HTTPException as exc:raise ValueError(f'Файл «{file.filename}»: {exc.detail}') from None
            if name!=file.filename:raise ValueError(f'Файл «{file.filename}»: имя файла не должно содержать путь.')
        return self


class Selection(Model):
    items: list[Reference]=Field(min_length=1,max_length=100)
    @model_validator(mode='after')
    def unique(self):
        if len({q.id for q in self.items})!=len(self.items):raise ValueError('Выберите каждый вопрос один раз.')
        return self


def describe(e):
    loc=list(e['loc']);label=''
    if len(loc)>1 and loc[0] in ('questions','files') and isinstance(loc[1],int):label=f"{'Вопрос' if loc[0]=='questions' else 'Файл'} {loc[1]+1}";loc=loc[2:]
    message={'missing':'Поле не заполнено.','extra_forbidden':'Лишнее поле.','json_invalid':'Файл не является корректным JSON.'}.get(e['type']) or e['msg'].removeprefix('Value error, ')
    where=' · '.join(filter(None,[label,'.'.join(map(str,loc))]))
    return f'{where}: {message}' if where else message


def parse_manifest(data):
    data=data.removeprefix(b'\xef\xbb\xbf')
    if len(data)>MAX_MANIFEST:raise HTTPException(413,'Описание пакета должно быть не больше 2 МБ.')
    try:return Package.model_validate_json(data)
    except ValidationError as exc:
        raise HTTPException(422,'Проверьте пакет:\n'+'\n'.join(describe(e) for e in exc.errors(include_input=False)[:10])) from None


@contextmanager
def archive(path):
    try:
        with ZipFile(path) as z:yield z
    except (BadZipFile,RuntimeError,NotImplementedError,EOFError,ValueError,zlib.error) as exc:
        raise HTTPException(422,'ZIP повреждён, зашифрован или использует неподдерживаемое сжатие.') from exc


def directory_size(path):
    """(entries, central directory bytes) from the end records, or None.

    Read directly (APPNOTE 4.3.14-4.3.16) rather than through zipfile's private
    parser, whose checks differ between 3.12 patch releases. As zipfile always
    did, the ZIP64 record is the one right before its locator.
    """
    with open(path,'rb') as f:
        f.seek(0,2);size=f.tell()
        f.seek(max(0,size-(22+65535+20+56)));tail=f.read()
    at=tail.rfind(b'PK\x05\x06')
    if at<0 or len(tail)-at<22:return None
    entries,directory=struct.unpack_from('<HL',tail,at+10)
    if at>=20 and tail[at-20:at-16]==b'PK\x06\x07':
        if at<76 or tail[at-76:at-72]!=b'PK\x06\x06':return None
        entries,directory=struct.unpack_from('<QQ',tail,at-76+32)
    return entries,directory


def check_directory(path):
    # ZipFile parses size_cd bytes regardless of the declared count, so bound it first.
    bounds=directory_size(path)
    if not bounds:raise HTTPException(422,'ZIP повреждён, зашифрован или использует неподдерживаемое сжатие.')
    if bounds[0]>MAX_ENTRIES or bounds[1]>MAX_DIRECTORY:raise HTTPException(422,'Нужен manifest.json и до 200 уникальных файлов.')


def inspect_package(path,filename):
    if filename.lower().endswith('.json'):
        if path.stat().st_size>MAX_MANIFEST:raise HTTPException(413,'JSON должен быть не больше 2 МБ.')
        package=parse_manifest(path.read_bytes())
        if package.files:raise HTTPException(422,'JSON переносит только вопросы без файлов. Для вложений используйте ZIP-пакет.')
        return package
    if not filename.lower().endswith('.zip'):raise HTTPException(422,'Выберите ZIP-пакет Атласа или JSON по шаблону.')
    check_directory(path)
    with archive(path) as z:
        entries=z.infolist();names=[i.filename for i in entries]
        if len(entries)>MAX_ENTRIES or len(names)!=len(set(names)) or 'manifest.json' not in names:
            raise HTTPException(422,'Нужен manifest.json и до 200 уникальных файлов.')
        for info in entries:
            if info.is_dir() or info.flag_bits&1 or stat.S_ISLNK(info.external_attr>>16):raise HTTPException(422,'Папки, символические ссылки и зашифрованные файлы не поддерживаются.')
            if info.orig_filename!=info.filename:raise HTTPException(422,'Пакет содержит лишние файлы, пути или отсутствующие вложения.')
            if info.compress_type not in (ZIP_STORED,ZIP_DEFLATED):raise HTTPException(422,'Используйте ZIP без сжатия или со сжатием Deflate.')
        manifest=z.getinfo('manifest.json')
        if manifest.file_size>MAX_MANIFEST:raise HTTPException(413,'Описание пакета должно быть не больше 2 МБ.')
        with z.open(manifest) as stream:data=stream.read(MAX_MANIFEST+1)
        package=parse_manifest(data)
        expected={'manifest.json'}|{'files/'+f.id for f in package.files}
        if set(names)!=expected:raise HTTPException(422,'Пакет содержит лишние файлы, пути или отсутствующие вложения.')
        if any(z.getinfo('files/'+f.id).file_size!=f.size for f in package.files):raise HTTPException(422,'Размер вложения не совпадает с описанием пакета.')
        return package


def extract_files(path,package,destinations):
    if not package.files:return
    created=[]
    try:
        with archive(path) as z:
            for file in package.files:
                target=destinations[file.id];target.parent.mkdir(parents=True,exist_ok=True)
                size=0;digest=hashlib.sha256()
                with z.open('files/'+file.id) as source,target.open('xb') as output:
                    created.append(target)
                    while chunk:=source.read(64*1024):
                        size+=len(chunk)
                        if size>file.size:raise HTTPException(422,'Размер вложения превышает описание пакета.')
                        digest.update(chunk);output.write(chunk)
                    output.flush();os.fsync(output.fileno())
                if size!=file.size or digest.hexdigest()!=file.sha256:raise HTTPException(422,'Проверка целостности вложения не пройдена.')
                media.validate_file(target,file.filename)
    except BaseException:
        for target in created:target.unlink(missing_ok=True)
        raise


def manifest_bytes(package,pretty=False):
    data=package.model_dump(mode='json',exclude={'questions':{'__all__':{'question':{'attachments','solutionAttachments'}}}})
    result=json.dumps(data,ensure_ascii=False,indent=2).encode() if pretty else encoded(data).encode()
    if len(result)>MAX_MANIFEST:raise HTTPException(413,'Описание выбранных вопросов превышает 2 МБ. Уменьшите выборку.')
    return result


def write_package(path,package,sources):
    manifest=manifest_bytes(package)
    with ZipFile(path,'w',compression=ZIP_STORED) as z:
        z.writestr('manifest.json',manifest)
        for file in package.files:
            size=0;digest=hashlib.sha256()
            try:source=sources[file.id].open('rb')
            except OSError:raise HTTPException(409,'Вложение отсутствует в хранилище. Обратитесь к администратору.') from None
            with source,z.open('files/'+file.id,'w') as output:
                while chunk:=source.read(64*1024):
                    size+=len(chunk)
                    if size>file.size:raise HTTPException(409,'Вложение изменилось в хранилище. Обратитесь к администратору.')
                    digest.update(chunk);output.write(chunk)
            if size!=file.size or digest.hexdigest()!=file.sha256:raise HTTPException(409,'Вложение повреждено. Обратитесь к администратору.')


TEMPLATE=dict(format='atlas.quiz-bank',version=1,files=[],questions=[
    dict(subject='География',tags=['пример'],archived=False,question=dict(kind='single',text='Какой город является столицей Великобритании?',points=1,
         options=[dict(id='a',text='Лондон'),dict(id='b',text='Париж')],correctIds=['a'],explanation='Лондон — столица Великобритании.')),
    dict(subject='Математика',tags=['пример'],question=dict(kind='number',text='Чему равно число π, округлённое до сотых?',number=3.14,tolerance=0.005)),
    dict(subject='Английский язык',tags=['пример'],question=dict(kind='short',text='Переведите на английский: «книга».',accepted=['book','a book'])),
    dict(subject='Литература',tags=['пример'],question=dict(kind='essay',text='Напишите небольшое рассуждение о любимой книге.',points=5)),
])


def template_bytes():
    return json.dumps(TEMPLATE,ensure_ascii=False,indent=2).encode()
