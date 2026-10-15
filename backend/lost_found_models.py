from datetime import date
from typing import Literal
from pydantic import Field,model_validator
from .models import Model

class Mutation(Model):
    requestId:str=Field(min_length=8,max_length=80)

class ItemData(Model):
    title:str=Field(min_length=2,max_length=160)
    description:str=Field(default='',max_length=2000)
    category:Literal['clothing','electronics','books','keys','other']='other'
    foundOn:date
    foundPlace:str=Field(min_length=2,max_length=200)
    privateNote:str=Field(default='',max_length=2000)
    pickupInstructions:str=Field(default='',max_length=600)
    photoIds:list[str]=Field(default_factory=list,max_length=3)
    @model_validator(mode='after')
    def unique(self):
        if len(set(self.photoIds))!=len(self.photoIds):raise ValueError('Фото повторяется.')
        return self

class ItemWrite(Mutation):
    version:int=Field(ge=0)
    data:ItemData
    reason:str=Field(default='',max_length=1000)

class ItemAction(Mutation):
    version:int=Field(ge=1)
    action:Literal['publish','withdraw','archive']
    reason:str=Field(min_length=3,max_length=1000)

class ClaimCreate(Mutation):
    itemVersion:int=Field(ge=1)
    studentId:str|None=Field(default=None,max_length=100)
    proof:str=Field(min_length=3,max_length=2000)

class ClaimAction(Mutation):
    version:int=Field(ge=1)
    itemVersion:int=Field(ge=1)
    action:Literal['approve','reject','reopen','collect','withdraw']
    reason:str=Field(min_length=3,max_length=1000)
    receivedBy:str=Field(default='',max_length=200)

class ConfigWrite(Mutation):
    version:int=Field(ge=0)
    accountIds:list[str]=Field(default_factory=list,max_length=150)
    reason:str=Field(min_length=3,max_length=1000)
    @model_validator(mode='after')
    def unique(self):
        if len(set(self.accountIds))!=len(self.accountIds):raise ValueError('Ответственный повторяется.')
        return self
