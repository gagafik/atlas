"""Bounded, portable Atlas question packages. Never extract archive paths."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import stat
from typing import Literal
from zipfile import ZipFile, BadZipFile, ZIP_STORED

from fastapi import HTTPException
from pydantic import Field, ValidationError, model_validator
from .models import Model
from .quiz_models import Question, Reference
from . import teaching_media as media
from .notifications import encoded

MAX_ARCHIVE=55*1024*1024
MAX_TOTAL=50*1024*1024
MAX_MANIFEST=2*1024*1024


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
    files: list[PackageFile]=Field(default_factory=list,max_length=200)

    @model_validator(mode='after')
    def valid(self):
        ids=[f.id for f in self.files]
        used={id for q in self.questions for id in q.question.fileIds+q.question.solutionFileIds}
        if len(ids)!=len(set(ids)) or used!=set(ids):raise ValueError('Файлы должны быть уникальными и точно соответствовать ссылкам в вопросах.')
        if sum(f.size for f in self.files)>MAX_TOTAL:raise ValueError('Общий размер вложений не должен превышать 50 МБ.')
        for file in self.files:
            if media.name_for(file.filename)!=file.filename:raise ValueError('Имя файла не должно содержать путь.')
        return self


class Selection(Model):
    items: list[Reference]=Field(min_length=1,max_length=100)
    @model_validator(mode='after')
    def unique(self):
        if len({q.id for q in self.items})!=len(self.items):raise ValueError('Выберите каждый вопрос один раз.')
        return self


def parse_manifest(data):
    if len(data)>MAX_MANIFEST:raise HTTPException(413,'Описание пакета должно быть не больше 2 МБ.')
    try:return Package.model_validate_json(data)
    except ValidationError as exc:
        errors=[' / '.join(map(str,e['loc']))+': '+e['msg'] for e in exc.errors(include_input=False)[:10]]
        raise HTTPException(422,'Проверьте пакет:\n'+'\n'.join(errors)) from None


@contextmanager
def archive(path):
    try:
        with ZipFile(path) as z:yield z
    except (BadZipFile,RuntimeError,NotImplementedError,EOFError,ValueError) as exc:
        raise HTTPException(422,'ZIP повреждён, зашифрован или использует неподдерживаемое сжатие.') from exc


def inspect_package(path,filename):
    if filename.lower().endswith('.json'):
        if path.stat().st_size>MAX_MANIFEST:raise HTTPException(413,'JSON должен быть не больше 2 МБ.')
        package=parse_manifest(path.read_bytes())
        if package.files:raise HTTPException(422,'JSON переносит только вопросы без файлов. Для вложений используйте ZIP-пакет.')
        return package
    if not filename.lower().endswith('.zip'):raise HTTPException(422,'Выберите ZIP-пакет Атласа или JSON по шаблону.')
    with archive(path) as z:
        entries=z.infolist();names=[i.filename for i in entries]
        if len(entries)>201 or len(names)!=len(set(names)) or 'manifest.json' not in names:
            raise HTTPException(422,'Нужен manifest.json и до 200 уникальных файлов.')
        for info in entries:
            if info.is_dir() or info.flag_bits&1 or stat.S_ISLNK(info.external_attr>>16):raise HTTPException(422,'Папки, символические ссылки и зашифрованные файлы не поддерживаются.')
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
    with archive(path) as z:
        for file in package.files:
            target=destinations[file.id];target.parent.mkdir(parents=True,exist_ok=True)
            size=0;digest=hashlib.sha256()
            with z.open('files/'+file.id) as source,target.open('xb') as output:
                while chunk:=source.read(64*1024):
                    size+=len(chunk)
                    if size>file.size:raise HTTPException(422,'Размер вложения превышает описание пакета.')
                    digest.update(chunk);output.write(chunk)
                output.flush();os.fsync(output.fileno())
            if size!=file.size or digest.hexdigest()!=file.sha256:raise HTTPException(422,'Проверка целостности вложения не пройдена.')
            media.validate_file(target,file.filename)


def write_package(path,package,sources):
    manifest=encoded(package.model_dump(mode='json',exclude={'questions':{'__all__':{'question':{'attachments','solutionAttachments'}}}})).encode()
    if len(manifest)>MAX_MANIFEST:raise HTTPException(413,'Описание выбранных вопросов превышает 2 МБ. Уменьшите выборку.')
    with ZipFile(path,'w',compression=ZIP_STORED) as z:
        z.writestr('manifest.json',manifest)
        for file in package.files:
            size=0;digest=hashlib.sha256()
            try:
                with sources[file.id].open('rb') as source,z.open('files/'+file.id,'w') as output:
                    while chunk:=source.read(64*1024):
                        size+=len(chunk)
                        if size>file.size:raise HTTPException(409,'Вложение изменилось в хранилище. Обратитесь к администратору.')
                        digest.update(chunk);output.write(chunk)
            except OSError:raise HTTPException(409,'Вложение отсутствует в хранилище. Обратитесь к администратору.') from None
            if size!=file.size or digest.hexdigest()!=file.sha256:raise HTTPException(409,'Вложение повреждено. Обратитесь к администратору.')
