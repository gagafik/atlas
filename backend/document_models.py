"""Plain text school templates. No executable template expressions or HTML."""
import re
from typing import Literal
from pydantic import Field,model_validator
from .models import Model

BUILTINS={'schoolName','personName','className','schoolYear','date','number'}
TOKEN=re.compile(r'\{\{\s*([A-Za-z][A-Za-z0-9_]{0,39})\s*\}\}')
Kind=Literal['student','employee','internal']

class FieldDefinition(Model):
    key:str=Field(pattern=r'^[A-Za-z][A-Za-z0-9_]{0,39}$')
    label:str=Field(min_length=1,max_length=120)
    required:bool=True

class Definition(Model):
    title:str=Field(min_length=1,max_length=160)
    kind:Kind='student'
    series:str=Field(default='DOC',pattern=r'^[A-ZА-ЯЁ0-9-]{1,12}$')
    text:str=Field(min_length=1,max_length=12000)
    fields:list[FieldDefinition]=Field(default_factory=list,max_length=20)
    @model_validator(mode='after')
    def valid(self):
        keys=[f.key for f in self.fields]
        if len(keys)!=len(set(keys)) or set(keys)&BUILTINS:raise ValueError('Поля должны иметь уникальные имена, отличающиеся от встроенных.')
        if set(TOKEN.findall(self.text))-(set(keys)|BUILTINS):raise ValueError('В тексте есть неизвестное поле.')
        if '{{' in TOKEN.sub('',self.text) or '}}' in TOKEN.sub('',self.text):raise ValueError('Проверьте скобки и имена полей в шаблоне.')
        return self

class RequestId(Model):requestId:str=Field(pattern=r'^[a-f0-9-]{36}$')
class Mutation(RequestId):version:int=Field(ge=0)
class TemplateWrite(Mutation):
    definition:Definition
    active:bool=False
    reason:str=Field(default='',max_length=1000)
class NewDocument(RequestId):
    templateId:str=Field(min_length=1,max_length=100)
    templateVersion:int=Field(ge=1)
    subjectId:str=Field(default='',max_length=100)
    assigneeId:str=Field(default='',max_length=100)
class DraftWrite(Mutation):values:dict[str,str]=Field(default_factory=dict,max_length=20)
class Action(Mutation):
    action:Literal['submit','withdraw','return','cancel','issue','revoke','archive','unarchive','recipients']
    reason:str=Field(default='',max_length=1000)
    previewHash:str|None=Field(default=None,max_length=64)
class Assign(Mutation):
    assigneeId:str=Field(default='',max_length=100)
    reason:str=Field(min_length=1,max_length=1000)

def values_for(definition,values,complete=False):
    from fastapi import HTTPException
    fields={f['key']:f for f in definition['fields']}
    if set(values)-fields.keys() or any(len(v)>2000 for v in values.values()):raise HTTPException(422,'Проверьте поля документа: неизвестное поле или больше 2000 символов.')
    result={k:v.strip() for k,v in values.items()}
    if complete and any(f['required'] and not result.get(k) for k,f in fields.items()):raise HTTPException(422,'Заполните все обязательные поля.')
    return result
