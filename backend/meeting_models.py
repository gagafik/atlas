"""School-administered parent/teacher meeting contracts."""
from datetime import timezone
from typing import Literal
from pydantic import AwareDatetime, Field, model_validator
from .models import Model


def utc(value): return value.astimezone(timezone.utc).isoformat()


class Host(Model):
    accountId: str = Field(min_length=1,max_length=100)
    teacherId: str | None = Field(default=None,max_length=100)
    scope: Literal['teaching','classes'] = 'teaching'
    classIds: list[str] = Field(default_factory=list,max_length=200)
    roomId: str | None = Field(default=None,max_length=100)
    location: str = Field(default='',max_length=300)

    @model_validator(mode='after')
    def valid(self):
        if len(set(self.classIds))!=len(self.classIds):raise ValueError('Не повторяйте классы.')
        if self.scope=='classes' and not self.classIds:raise ValueError('Для приёма по классам выберите классы.')
        if self.scope=='teaching' and (not self.teacherId or self.classIds):raise ValueError('Для предметного учителя укажите преподавателя без списка классов.')
        if not self.roomId and not self.location:raise ValueError('Укажите кабинет или место встречи.')
        return self


class Definition(Model):
    title: str = Field(min_length=1,max_length=160)
    description: str = Field(default='',max_length=4000)
    startsAt: AwareDatetime
    endsAt: AwareDatetime
    bookingOpensAt: AwareDatetime | None = None
    bookingClosesAt: AwareDatetime | None = None
    slotMinutes: int = Field(default=10,ge=5,le=60)
    gapMinutes: int = Field(default=0,ge=0,le=30)
    cancelMinutes: int = Field(default=60,ge=0,le=10080)
    reminderMinutes: int = Field(default=60,ge=0,le=10080)
    hosts: list[Host] = Field(min_length=1,max_length=200)

    @model_validator(mode='after')
    def valid(self):
        if self.endsAt<=self.startsAt or (self.endsAt-self.startsAt).total_seconds()>31*86400:raise ValueError('Период встреч — от одного интервала до 31 дня.')
        if any(t and (t.second or t.microsecond) for t in [self.startsAt,self.endsAt,self.bookingOpensAt,self.bookingClosesAt]):raise ValueError('Указывайте время с точностью до минуты.')
        if self.bookingClosesAt and (self.bookingClosesAt>self.endsAt or self.bookingOpensAt and self.bookingClosesAt<=self.bookingOpensAt):raise ValueError('Проверьте начало и окончание записи.')
        if self.bookingOpensAt and self.bookingOpensAt>=self.endsAt:raise ValueError('Запись должна открыться до окончания встреч.')
        if len({h.accountId for h in self.hosts})!=len(self.hosts):raise ValueError('Не повторяйте аккаунты ведущих.')
        teachers=[h.teacherId for h in self.hosts if h.teacherId]
        if len(set(teachers))!=len(teachers):raise ValueError('Один преподаватель — один ведущий в мероприятии.')
        return self

    def document(self):
        result=self.model_dump(mode='json')
        for name in ['startsAt','endsAt','bookingOpensAt','bookingClosesAt']:
            if getattr(self,name):result[name]=utc(getattr(self,name))
        return result


class RequestId(Model):
    requestId: str = Field(pattern=r'^[a-f0-9-]{36}$')


class Mutation(RequestId):
    version: int = Field(ge=1)


class Create(RequestId):
    definition: Definition


class Edit(Mutation):
    definition: Definition


class Transition(Mutation):
    action: Literal['publish','close','reopen','cancel','archive']
    reason: str = Field(default='',max_length=500)


class Availability(Mutation):
    action: Literal['generate','block','unblock']
    hostIds: list[str] = Field(min_length=1,max_length=200)
    startsAt: AwareDatetime
    endsAt: AwareDatetime
    reason: str = Field(default='',max_length=500)
    previewHash: str | None = Field(default=None,max_length=64)

    @model_validator(mode='after')
    def valid(self):
        if self.endsAt<=self.startsAt or (self.endsAt-self.startsAt).total_seconds()>86400:raise ValueError('Выберите интервал в пределах одного дня.')
        if len(set(self.hostIds))!=len(self.hostIds):raise ValueError('Не повторяйте ведущих.')
        if self.action=='block' and not self.reason:raise ValueError('Укажите причину отмены интервалов.')
        if self.startsAt.second or self.startsAt.microsecond or self.endsAt.second or self.endsAt.microsecond:raise ValueError('Укажите целые минуты.')
        return self


class Book(Mutation):
    eventVersion: int = Field(ge=1)
    studentId: str = Field(min_length=1,max_length=100)
    note: str = Field(default='',max_length=500)


class BookingChange(Mutation):
    action: Literal['cancel','attended','no_show','scheduled']
    reason: str = Field(default='',max_length=500)


class Reschedule(Mutation):
    eventVersion: int = Field(ge=1)
    sourceSlotVersion: int = Field(ge=1)
    targetSlotId: str = Field(min_length=1,max_length=100)
    targetSlotVersion: int = Field(ge=1)
    reason: str = Field(min_length=1,max_length=500)
    blockSource: bool = False

    @model_validator(mode='after')
    def valid(self):
        self.reason=self.reason.strip()
        if not self.reason:raise ValueError('Укажите причину переноса.')
        return self
