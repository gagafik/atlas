"""Explicit school approval and physical child handover commands."""
from datetime import date, datetime
from typing import Literal
from pydantic import Field, model_validator
from .models import Model

STAFF = {'admin', 'editor', 'teacher', 'staff'}
PENDING = {'requested', 'arrived', 'ready'}


class Command(Model):
    requestId: str = Field(pattern=r'^[a-f0-9-]{36}$')


class Versioned(Command):
    version: int = Field(ge=0)
    reason: str = Field(default='', max_length=1000)


class Delegate(Model):
    userId: str = Field(min_length=1, max_length=100)
    role: Literal['manager', 'issuer']
    classIds: list[str] = Field(default_factory=list, max_length=200)


class PickupPoint(Model):
    id: str = Field(pattern=r'^[a-zA-Z0-9_-]{1,40}$')
    name: str = Field(min_length=1, max_length=120)


class Settings(Versioned):
    enabled: bool = False
    parentRequests: bool = True
    advanceDays: int = Field(default=30, ge=1, le=90)
    policy: str = Field(default='', max_length=5000)
    locations: list[PickupPoint] = Field(default_factory=lambda:[PickupPoint(id='main',name='Основной выход')], min_length=1, max_length=20)
    delegates: list[Delegate] = Field(default_factory=list, max_length=100)

    @model_validator(mode='after')
    def unique(self):
        if len({a.userId for a in self.delegates}) != len(self.delegates) or len({p.id for p in self.locations}) != len(self.locations):
            raise ValueError('Назначения сотрудников и места выдачи не должны повторяться.')
        if any(len(set(a.classIds)) != len(a.classIds) for a in self.delegates): raise ValueError('Классы повторяются.')
        if self.enabled and len(self.policy.strip()) < 20: raise ValueError('До включения задайте школьный порядок проверки взрослого и передачи ребёнка.')
        return self


class AdultProposal(Command):
    studentId: str = Field(min_length=1, max_length=100)
    kind: Literal['parent', 'representative']
    sponsorId: str = Field(default='', max_length=100)
    name: str = Field(default='', max_length=160)
    relationship: str = Field(default='', max_length=100)
    contact: str = Field(default='', max_length=100)
    startDate: date
    endDate: date
    consentConfirmed: bool = Field(default=False, strict=True)
    reason: str = Field(default='', max_length=1000)

    @model_validator(mode='after')
    def dates(self):
        if self.endDate < self.startDate or (self.endDate-self.startDate).days > 730: raise ValueError('Укажите срок разрешения до двух лет.')
        if self.kind == 'representative' and (not self.name or not self.relationship): raise ValueError('Укажите имя и отношение взрослого к ребёнку.')
        if not self.consentConfirmed: raise ValueError('Подтвердите согласие на предложение взрослого для проверки школой.')
        return self


class AdultDecision(Versioned):
    action: Literal['approve', 'reject', 'revoke']
    consentChecked: bool = Field(default=False, strict=True)
    identityChecked: bool = Field(default=False, strict=True)


class PickupCreate(Command):
    adultId: str = Field(min_length=1, max_length=100)
    adultVersion: int = Field(ge=1)
    day: date
    expectedTime: str = Field(pattern=r'^([01]\d|2[0-3]):[0-5]\d$')
    locationId: str = Field(min_length=1, max_length=40)
    note: str = Field(default='', max_length=1000)


class PickupAction(Versioned):
    action: Literal['arrive', 'ready', 'cancel', 'note', 'returned']
    childReturned: bool = Field(default=False, strict=True)


class TransferReview(Versioned):
    identityMethod: Literal['known-person', 'document-checked', 'school-procedure']
    identityChecked: bool = Field(default=False, strict=True)
    childPresent: bool = Field(default=False, strict=True)


class TransferConfirm(TransferReview):
    reviewedAt: datetime
    previewHash: str = Field(min_length=64, max_length=64)
    handedOver: bool = Field(default=False, strict=True)

    @model_validator(mode='after')
    def aware(self):
        if self.reviewedAt.utcoffset() is None: raise ValueError('Нужен часовой пояс предпросмотра.')
        return self
