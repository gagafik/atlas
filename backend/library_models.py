from datetime import date
from typing import Literal
from pydantic import Field,model_validator
from .models import Model

class RequestId(Model):requestId:str=Field(pattern=r'^[a-f0-9-]{36}$')
class Mutation(RequestId):version:int=Field(ge=0)
class TitleData(Model):
    title:str=Field(min_length=1,max_length=200)
    author:str=Field(default='',max_length=200)
    isbn:str=Field(default='',max_length=32)
    publisher:str=Field(default='',max_length=160)
    year:int|None=Field(default=None,ge=1000,le=9999)
    kind:Literal['book','textbook']='book'
    grade:str=Field(default='',max_length=40)
    subject:str=Field(default='',max_length=100)
    description:str=Field(default='',max_length=2000)
class TitleWrite(Mutation):
    data:TitleData
    archived:bool=False
    reason:str=Field(default='',max_length=1000)
Condition=Literal['good','worn','damaged']
CopyStatus=Literal['available','repair','lost','withdrawn']
class CopyData(Model):
    inventory:str=Field(min_length=1,max_length=80)
    location:str=Field(default='',max_length=160)
    condition:Condition='good'
    status:CopyStatus='available'
class CopiesCreate(RequestId):
    titleVersion:int=Field(ge=1)
    items:list[CopyData]=Field(min_length=1,max_length=200)
    @model_validator(mode='after')
    def unique(self):
        if len({x.inventory.casefold() for x in self.items})!=len(self.items):raise ValueError('Инвентарные номера не должны повторяться.')
        return self
class CopyWrite(Mutation):
    data:CopyData
    reason:str=Field(min_length=1,max_length=1000)
class Assignment(Model):
    accountId:str=Field(min_length=1,max_length=100)
    wholeSchool:bool=False
    classIds:list[str]=Field(default_factory=list,max_length=500)
    @model_validator(mode='after')
    def valid(self):
        if (self.wholeSchool and self.classIds) or (not self.wholeSchool and not self.classIds) or len(self.classIds)!=len(set(self.classIds)):raise ValueError('Выберите всю школу либо разные классы.')
        return self
class ConfigWrite(Mutation):
    assignments:list[Assignment]=Field(default_factory=list,max_length=100)
    loanDays:int=Field(default=14,ge=1,le=365)
    maxRenewals:int=Field(default=2,ge=0,le=20)
    maxLoans:int=Field(default=30,ge=1,le=200)
    reason:str=Field(min_length=1,max_length=1000)
    @model_validator(mode='after')
    def unique(self):
        if len({a.accountId for a in self.assignments})!=len(self.assignments):raise ValueError('Сотрудники не должны повторяться.')
        return self
class Borrower(Model):
    kind:Literal['student','employee']='student'
    id:str=Field(min_length=1,max_length=100)
class IssueItem(Model):
    copyId:str=Field(min_length=1,max_length=100)
    copyVersion:int=Field(ge=1)
    borrower:Borrower
class Issue(RequestId):
    items:list[IssueItem]=Field(min_length=1,max_length=100)
    due:date
    previewHash:str=Field(default='',max_length=64)
    @model_validator(mode='after')
    def unique(self):
        if len({i.copyId for i in self.items})!=len(self.items):raise ValueError('Экземпляры не должны повторяться.')
        return self
class LoanAction(Mutation):
    action:Literal['return','renew','lost','found','writeoff','cancel','remind']
    condition:Condition='good'
    due:date|None=None
    reason:str=Field(default='',max_length=1000)
class AuditStart(RequestId):title:str=Field(min_length=1,max_length=160)
class AuditMark(Mutation):
    result:Literal['seen','missing','loaned','refresh']
    note:str=Field(default='',max_length=1000)
class AuditClose(Mutation):
    action:Literal['close','cancel']
    reason:str=Field(min_length=1,max_length=1000)
