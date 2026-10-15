"""Actual staff time and leave requests, independent of teaching schedules."""
from datetime import date
from typing import Literal
from pydantic import Field,model_validator
from .models import Model

WorkCode=Literal['work','remote','training','trip']
AbsenceCode=Literal['leave','sick','absence','day_off']
Code=Literal['work','remote','training','trip','leave','sick','absence','day_off']
WORK={'work','remote','training','trip'}
ABSENT={'leave','sick','absence','day_off'}
TIME=r'^(?:[01]\d|2[0-3]):[0-5]\d$'
END=r'^(?:(?:[01]\d|2[0-3]):[0-5]\d|24:00)$'

def minutes(value):
    hour,minute=value.split(':');return int(hour)*60+int(minute)


class Segment(Model):
    start:str=Field(pattern=TIME)
    end:str=Field(pattern=END)
    code:Code='work'
    @model_validator(mode='after')
    def valid(self):
        if minutes(self.end)<=minutes(self.start):raise ValueError('Окончание должно быть позже начала. Ночную смену разделите по датам.')
        return self


class Day(Model):
    fullDay:AbsenceCode|None=None
    segments:list[Segment]=Field(default_factory=list,max_length=12)
    note:str=Field(default='',max_length=1000)
    @model_validator(mode='after')
    def valid(self):
        if bool(self.fullDay)==bool(self.segments):raise ValueError('Укажите интервалы либо отсутствие на весь день.')
        ordered=sorted(self.segments,key=lambda s:s.start)
        if any(a.end>b.start for a,b in zip(ordered,ordered[1:])):raise ValueError('Интервалы одного дня не должны пересекаться.')
        self.segments=ordered
        return self


class RequestId(Model):
    requestId:str=Field(pattern=r'^[a-f0-9-]{36}$')


class Mutation(RequestId):
    version:int=Field(ge=0)


class SaveDay(Mutation):
    employeeId:str=Field(min_length=1,max_length=100)
    onDate:date
    document:Day


class DayAction(Mutation):
    action:Literal['submit','withdraw','approve','return','reopen','void']
    reason:str=Field(default='',max_length=1000)


class Leave(RequestId):
    employeeId:str=Field(min_length=1,max_length=100)
    startDate:date
    endDate:date
    code:AbsenceCode='leave'
    startTime:str|None=Field(default=None,pattern=TIME)
    endTime:str|None=Field(default=None,pattern=END)
    note:str=Field(default='',max_length=1000)
    @model_validator(mode='after')
    def valid(self):
        if self.endDate<self.startDate or (self.endDate-self.startDate).days>30:raise ValueError('Укажите период до 31 календарного дня.')
        if bool(self.startTime)!=bool(self.endTime) or self.startTime and minutes(self.endTime)<=minutes(self.startTime):raise ValueError('Укажите оба времени; окончание позже начала.')
        return self


class LeaveAction(Mutation):
    action:Literal['approve','reject','withdraw','cancel']
    reason:str=Field(default='',max_length=1000)
    previewHash:str|None=Field(default=None,max_length=64)


class Settings(Mutation):
    selfReport:bool=False
    reason:str=Field(min_length=1,max_length=1000)


class PeriodAction(Mutation):
    action:Literal['close','reopen']
    previewHash:str|None=Field(default=None,max_length=64)
    reason:str=Field(default='',max_length=1000)
