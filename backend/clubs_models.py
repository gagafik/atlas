from datetime import date
from typing import Literal
from pydantic import Field,model_validator
from .models import Model

class Mutation(Model):
    requestId:str=Field(min_length=8,max_length=80)

class ClubData(Model):
    title:str=Field(min_length=2,max_length=160)
    description:str=Field(default='',max_length=4000)
    location:str=Field(default='',max_length=200)
    startsOn:date
    endsOn:date
    opensOn:date
    closesOn:date
    capacity:int=Field(ge=1,le=300)
    queueMode:Literal['automatic','manual']='automatic'
    classIds:list[str]=Field(min_length=1,max_length=200)
    @model_validator(mode='after')
    def valid(self):
        if self.startsOn>self.endsOn or self.opensOn>self.closesOn or self.closesOn>self.endsOn:raise ValueError('Проверьте даты кружка и записи.')
        if (self.endsOn-self.startsOn).days>730:raise ValueError('Период кружка ограничен двумя годами.')
        if len(set(self.classIds))!=len(self.classIds):raise ValueError('Класс повторяется.')
        return self

class ClubWrite(Mutation):
    version:int=Field(ge=0)
    data:ClubData
    reason:str=Field(default='',max_length=1000)

class ClubAction(Mutation):
    version:int=Field(ge=1)
    action:Literal['publish','close','archive','advance']
    reason:str=Field(min_length=3,max_length=1000)

class AccessWrite(Mutation):
    version:int=Field(ge=1)
    accountIds:list[str]=Field(default_factory=list,max_length=30)
    reason:str=Field(min_length=3,max_length=1000)
    @model_validator(mode='after')
    def unique(self):
        if len(set(self.accountIds))!=len(self.accountIds):raise ValueError('Ответственный повторяется.')
        return self

class Join(Mutation):
    clubVersion:int=Field(ge=1)
    studentId:str=Field(min_length=1,max_length=100)
    reason:str=Field(default='',max_length=1000)

class MemberAction(Mutation):
    version:int=Field(ge=1)
    clubVersion:int=Field(ge=1)
    action:Literal['withdraw','reject','promote']
    reason:str=Field(min_length=3,max_length=1000)

class SessionData(Model):
    date:date
    start:str=Field(pattern=r'^([01]\d|2[0-3]):[0-5]\d$')
    end:str=Field(pattern=r'^([01]\d|2[0-3]):[0-5]\d$')
    title:str=Field(min_length=2,max_length=200)
    location:str=Field(default='',max_length=200)
    @model_validator(mode='after')
    def valid(self):
        if self.start>=self.end:raise ValueError('Окончание должно быть позже начала в тот же день.')
        return self

class SessionWrite(Mutation):
    version:int=Field(ge=0)
    clubVersion:int=Field(ge=1)
    data:SessionData
    reason:str=Field(default='',max_length=1000)

class SessionAction(Mutation):
    version:int=Field(ge=1)
    action:Literal['start','complete','reopen','cancel']
    reason:str=Field(min_length=3,max_length=1000)

class Mark(Model):
    studentId:str=Field(min_length=1,max_length=100)
    status:Literal['unknown','present','absent','excused','late']
    note:str=Field(default='',max_length=500)

class AttendanceWrite(Mutation):
    version:int=Field(ge=1)
    marks:list[Mark]=Field(min_length=1,max_length=300)
    reason:str=Field(min_length=3,max_length=1000)
    @model_validator(mode='after')
    def valid(self):
        if len({m.studentId for m in self.marks})!=len(self.marks):raise ValueError('Ученик повторяется.')
        return self
