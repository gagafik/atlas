from datetime import date
import re
from typing import Literal
from pydantic import AwareDatetime,Field,field_validator,model_validator
from .models import Model

Capability=Literal['visitors','incidents','inventory']
DocumentType=Literal['','passport','id_card','driver_license','service_id','other']
Condition=Literal['new','good','fair','poor','broken']

class Mutation(Model):
    requestId:str=Field(min_length=8,max_length=80)

class Assignment(Model):
    accountId:str=Field(min_length=1,max_length=100)
    capabilities:list[Capability]=Field(min_length=1,max_length=3)
    @model_validator(mode='after')
    def unique(self):
        if len(set(self.capabilities))!=len(self.capabilities):raise ValueError('Права не должны повторяться.')
        return self

class ConfigWrite(Mutation):
    version:int=Field(ge=0)
    reason:str=Field(min_length=3,max_length=500)
    assignments:list[Assignment]=Field(default_factory=list,max_length=150)
    familySummaries:bool=False
    @model_validator(mode='after')
    def unique(self):
        if len({a.accountId for a in self.assignments})!=len(self.assignments):raise ValueError('Сотрудник повторяется.')
        return self

class VisitorData(Model):
    name:str=Field(min_length=2,max_length=160)
    purpose:str=Field(min_length=2,max_length=300)
    hostId:str=Field(default='',max_length=100)
    hostText:str=Field(default='',max_length=160)
    badge:str=Field(default='',pattern=r'^[0-9A-Za-zА-Яа-яЁё-]{0,20}$')
    documentType:DocumentType=''
    note:str=Field(default='',max_length=500)
    arrivedAt:AwareDatetime|None=None
    @field_validator('name','purpose','hostText','note')
    @classmethod
    def no_numbers(cls,value):
        # The log keeps the document TYPE only: long digit runs are numbers of documents or phones.
        if re.search(r'\d{8,}',re.sub(r'[\s().-]','',value)):raise ValueError('Не вводите номера документов и телефонов.')
        return value
    @model_validator(mode='after')
    def host(self):
        if not self.hostId and len(self.hostText)<2:raise ValueError('Укажите, к кому пришёл посетитель.')
        return self

class VisitorWrite(Mutation):
    version:int=Field(ge=0)
    data:VisitorData
    reason:str=Field(default='',max_length=500)

class VisitorAction(Mutation):
    version:int=Field(ge=1)
    action:Literal['depart','void']
    at:AwareDatetime|None=None
    reason:str=Field(default='',max_length=500)

class ParentNotice(Model):
    studentId:str=Field(min_length=1,max_length=100)
    by:str=Field(min_length=2,max_length=160)
    at:AwareDatetime
    method:Literal['phone','in_person','message','other']
    note:str=Field(default='',max_length=300)

class IncidentData(Model):
    kind:Literal['accident','incident']
    severity:Literal['minor','medical','serious']='minor'
    occurredAt:AwareDatetime
    place:str=Field(min_length=2,max_length=200)
    roomId:str=Field(default='',max_length=100)
    studentIds:list[str]=Field(default_factory=list,max_length=40)
    staffIds:list[str]=Field(default_factory=list,max_length=20)
    otherPersons:str=Field(default='',max_length=500)
    description:str=Field(min_length=10,max_length=4000)
    firstAid:str=Field(default='',max_length=2000)
    parentNotices:list[ParentNotice]=Field(default_factory=list,max_length=80)
    followUp:str=Field(default='',max_length=4000)
    familySummary:str=Field(default='',max_length=1000)
    @model_validator(mode='after')
    def consistent(self):
        if len(set(self.studentIds))!=len(self.studentIds) or len(set(self.staffIds))!=len(self.staffIds):raise ValueError('Участник повторяется.')
        if any(n.studentId not in self.studentIds for n in self.parentNotices):raise ValueError('Сообщение родителям относится к ученику, не указанному в записи.')
        return self

class IncidentWrite(Mutation):
    version:int=Field(ge=0)
    data:IncidentData
    reason:str=Field(default='',max_length=500)

class IncidentAction(Mutation):
    version:int=Field(ge=1)
    action:Literal['close','reopen','publish_summary','withdraw_summary']
    reason:str=Field(min_length=3,max_length=500)

class ItemData(Model):
    number:str=Field(pattern=r'^[0-9A-Za-zА-Яа-яЁё./_-]{1,40}$')
    title:str=Field(min_length=2,max_length=160)
    category:str=Field(min_length=1,max_length=60)
    roomId:str=Field(default='',max_length=100)
    location:str=Field(default='',max_length=120)
    responsibleId:str=Field(default='',max_length=100)
    condition:Condition='good'
    acquiredOn:date|None=None
    serial:str=Field(default='',max_length=80)
    note:str=Field(default='',max_length=1000)
    @model_validator(mode='after')
    def place(self):
        if not self.roomId and not self.location:raise ValueError('Укажите кабинет или место хранения.')
        return self

class ItemWrite(Mutation):
    version:int=Field(ge=0)
    data:ItemData
    reason:str=Field(default='',max_length=500)

class ItemMove(Mutation):
    version:int=Field(ge=1)
    roomId:str=Field(default='',max_length=100)
    location:str=Field(default='',max_length=120)
    reason:str=Field(min_length=3,max_length=500)
    movedOn:date|None=None
    @model_validator(mode='after')
    def place(self):
        if not self.roomId and not self.location:raise ValueError('Укажите кабинет или место хранения.')
        return self

class ItemAction(Mutation):
    version:int=Field(ge=1)
    action:Literal['write_off','restore']
    reason:str=Field(min_length=3,max_length=500)
    on:date|None=None

class InventoryImport(Mutation):
    filename:str=Field(pattern=r'(?i)^[^/\\]{1,120}\.(csv|tsv|txt|xlsx)$')
    content:str=Field(max_length=3_000_000)
    previewHash:str=Field(default='',max_length=64)
    reason:str=Field(min_length=3,max_length=500)

class InventoryPreview(Model):
    # Accepts the same payload as the import, so a client may reuse it; the extra fields are ignored.
    filename:str=Field(pattern=r'(?i)^[^/\\]{1,120}\.(csv|tsv|txt|xlsx)$')
    content:str=Field(max_length=3_000_000)
    requestId:str=Field(default='',max_length=80)
    previewHash:str=Field(default='',max_length=64)
    reason:str=Field(default='',max_length=500)
