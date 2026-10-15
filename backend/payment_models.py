"""Bounded commands; all money is integer minor units, never floating point."""
from datetime import date
from typing import Literal
from pydantic import Field, model_validator
from .models import Model

MAX_MONEY = 1_000_000_000_000
class RequestId(Model):
    requestId: str = Field(pattern=r'^[a-f0-9-]{36}$')
class Versioned(RequestId):
    version: int = Field(ge=0)
    reason: str = Field(min_length=1, max_length=1000)
class ConfigWrite(Versioned):
    currency: str = Field(default='KZT', pattern=r'^[A-Z]{3}$')
    decimals: int = Field(default=2, ge=0, le=3)
    familyPolicy: Literal['assigned', 'verified'] = 'assigned'
    managerIds: list[str] = Field(default_factory=list, max_length=100)
    @model_validator(mode='after')
    def unique(self):
        if len(set(self.managerIds)) != len(self.managerIds): raise ValueError('Сотрудники повторяются.')
        return self
class PayersWrite(Versioned):
    parentIds: list[str] = Field(default_factory=list, max_length=10)
    @model_validator(mode='after')
    def unique(self):
        if len(set(self.parentIds)) != len(self.parentIds): raise ValueError('Плательщики повторяются.')
        return self
class Installment(Model):
    label: str = Field(min_length=1, max_length=100)
    due: date
    amount: int = Field(strict=True, ge=0, le=MAX_MONEY)
class PlanCreate(RequestId):
    title: str = Field(min_length=1, max_length=160)
    installments: list[Installment] = Field(min_length=1, max_length=12)
class IssueStudent(Model):
    studentId: str = Field(min_length=1, max_length=100)
    discountBps: int = Field(default=0, strict=True, ge=0, le=10000)
    discountAmount: int = Field(default=0, strict=True, ge=0, le=MAX_MONEY)
    @model_validator(mode='after')
    def one(self):
        if self.discountBps and self.discountAmount: raise ValueError('Выберите процент или фиксированную скидку.')
        return self
class PlanIssue(RequestId):
    planVersion: int = Field(ge=1)
    students: list[IssueStudent] = Field(min_length=1, max_length=100)
    previewHash: str = Field(default='', max_length=64)
    reason: str = Field(min_length=1, max_length=1000)
    @model_validator(mode='after')
    def unique(self):
        if len({x.studentId for x in self.students}) != len(self.students): raise ValueError('Ученики повторяются.')
        return self
class ChargeCreate(Versioned):
    title: str = Field(min_length=1, max_length=160)
    due: date
    amount: int = Field(strict=True, ge=1, le=MAX_MONEY)
class ChargeAdjust(Versioned):
    amount: int = Field(strict=True, ge=0, le=MAX_MONEY)
    label: str = Field(min_length=1, max_length=160)
class ReceiptPart(Model):
    studentId: str = Field(min_length=1, max_length=100)
    version: int = Field(ge=0)
    amount: int = Field(strict=True, ge=1, le=MAX_MONEY)
class ReceiptCreate(RequestId):
    paidDate: date
    method: Literal['bank', 'cash', 'transfer', 'opening'] = 'bank'
    reference: str = Field(min_length=1, max_length=100)
    source: str = Field(default='manual', min_length=1, max_length=40, pattern=r'^[\w .-]+$')
    payer: str = Field(default='', max_length=160)
    parts: list[ReceiptPart] = Field(min_length=1, max_length=20)
    reason: str = Field(min_length=1, max_length=1000)
    @model_validator(mode='after')
    def unique(self):
        if len({x.studentId for x in self.parts}) != len(self.parts): raise ValueError('Объедините суммы одного ученика.')
        return self
class Allocation(Versioned):
    receiptId: str = Field(min_length=1, max_length=100)
    chargeId: str = Field(min_length=1, max_length=100)
    amount: int = Field(strict=True, ge=1, le=MAX_MONEY)
class AllocationReverse(Versioned): pass
class ReceiptRefund(Versioned):
    amount: int = Field(strict=True, ge=1, le=MAX_MONEY)
    kind: Literal['refund', 'correction'] = 'refund'
    reference: str = Field(min_length=1, max_length=100)
class AutoAllocate(Versioned): pass
class ImportBank(RequestId):
    text: str = Field(min_length=1, max_length=60000)
    source: str = Field(min_length=1, max_length=40, pattern=r'^[\w .-]+$')
    previewHash: str = Field(default='', max_length=64)
    reason: str = Field(min_length=1, max_length=1000)
class ClosePeriod(Versioned):
    through: date
    previewHash: str = Field(default='', max_length=64)
class Remind(Versioned): pass
