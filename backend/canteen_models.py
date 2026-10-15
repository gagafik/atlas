"""Bounded canteen commands. Published meal conditions remain immutable."""
from datetime import date, datetime, time
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import Field, model_validator

from .models import Model

ZONE = ZoneInfo('Asia/Qyzylorda')
STAFF = {'admin', 'editor', 'teacher', 'staff'}
MEALS = {'breakfast': 'Завтрак', 'lunch': 'Обед', 'snack': 'Полдник', 'dinner': 'Ужин'}
MAX_PRICE = 1_000_000_000_000


class Command(Model):
    requestId: str = Field(pattern=r'^[a-f0-9-]{36}$')


class Versioned(Command):
    version: int = Field(ge=0)
    reason: str = Field(default='', max_length=1000)


class Delegate(Model):
    userId: str = Field(min_length=1, max_length=100)
    role: Literal['manager', 'issuer', 'kitchen']
    classIds: list[str] = Field(default_factory=list, max_length=200)


class Settings(Versioned):
    delegates: list[Delegate] = Field(default_factory=list, max_length=100)
    financeEnabled: bool = False
    allowDebt: bool = False
    studentOrdering: bool = False

    @model_validator(mode='after')
    def unique(self):
        if len({x.userId for x in self.delegates}) != len(self.delegates):
            raise ValueError('Сотрудники повторяются.')
        if any(len(set(x.classIds)) != len(x.classIds) for x in self.delegates):
            raise ValueError('Классы повторяются.')
        if any(x.role != 'issuer' and x.classIds for x in self.delegates):
            raise ValueError('Ограничение по классам применяется к сотрудникам выдачи.')
        return self


class Variant(Model):
    id: str = Field(pattern=r'^[a-zA-Z0-9_-]{1,40}$')
    title: str = Field(min_length=1, max_length=120)
    dishes: str = Field(min_length=1, max_length=3000)
    allergens: list[str] = Field(default_factory=list, max_length=30)
    notes: str = Field(default='', max_length=1000)
    classIds: list[str] = Field(default_factory=list, max_length=200)
    price: int = Field(default=0, strict=True, ge=0, le=MAX_PRICE)
    capacity: int | None = Field(default=None, ge=1, le=5000)

    @model_validator(mode='after')
    def clean(self):
        self.title = self.title.strip(); self.dishes = self.dishes.strip()
        self.allergens = [x.strip() for x in self.allergens]
        if not self.title or not self.dishes or any(not x or len(x) > 100 for x in self.allergens):
            raise ValueError('Заполните название, блюда и короткие названия аллергенов.')
        if len(set(self.allergens)) != len(self.allergens) or len(set(self.classIds)) != len(self.classIds):
            raise ValueError('Значения не должны повторяться.')
        return self


class MenuDefinition(Model):
    day: date
    meal: Literal['breakfast', 'lunch', 'snack', 'dinner'] = 'lunch'
    title: str = Field(min_length=1, max_length=160)
    starts: str = Field(default='12:00', pattern=r'^([01][0-9]|2[0-3]):[0-5][0-9]$')
    ends: str = Field(default='14:00', pattern=r'^([01][0-9]|2[0-3]):[0-5][0-9]$')
    opensAt: datetime
    orderClosesAt: datetime
    cancelClosesAt: datetime
    classIds: list[str] = Field(default_factory=list, max_length=200)
    variants: list[Variant] = Field(min_length=1, max_length=12)
    notice: str = Field(default='', max_length=2000)

    @model_validator(mode='after')
    def valid(self):
        self.title = self.title.strip()
        if not self.title: raise ValueError('Укажите название меню.')
        if self.starts >= self.ends: raise ValueError('Окончание выдачи должно быть позже начала.')
        start = datetime.combine(self.day, time.fromisoformat(self.starts), ZONE)
        if any(x.tzinfo is None for x in [self.opensAt, self.orderClosesAt, self.cancelClosesAt]):
            raise ValueError('Для сроков нужен часовой пояс.')
        if not self.opensAt < self.orderClosesAt <= start or not self.opensAt < self.cancelClosesAt <= start:
            raise ValueError('Сроки заказа и отмены должны быть после открытия и не позже выдачи.')
        if len(set(self.classIds)) != len(self.classIds) or len({v.id for v in self.variants}) != len(self.variants):
            raise ValueError('Классы или варианты повторяются.')
        if any(self.classIds and set(v.classIds) - set(self.classIds) for v in self.variants):
            raise ValueError('Классы варианта должны входить в аудиторию меню.')
        return self


class MenuCreate(Command):
    definition: MenuDefinition


class MenuEdit(Versioned):
    definition: MenuDefinition


class MenuTransition(Versioned):
    action: Literal['publish', 'close', 'reopen', 'cancel', 'archive']


class MenuCopy(Versioned):
    day: date


class Reservation(Model):
    menuId: str = Field(min_length=1, max_length=100)
    menuVersion: int = Field(ge=1)
    studentId: str = Field(min_length=1, max_length=100)
    variantId: str = Field(min_length=1, max_length=40)
    expectedPrice: int = Field(strict=True, ge=0, le=MAX_PRICE)


class OrderCreate(Command):
    reservation: Reservation
    overrideCutoff: bool = False
    reason: str = Field(default='', max_length=1000)


class OrderAction(Versioned):
    action: Literal['cancel', 'serve', 'missed', 'undo-service']
    overrideCutoff: bool = False


class OrderRef(Model):
    id: str = Field(min_length=1, max_length=100)
    version: int = Field(ge=1)


class Batch(Command):
    action: Literal['reserve', 'cancel', 'serve', 'missed']
    reservations: list[Reservation] = Field(default_factory=list, max_length=100)
    orders: list[OrderRef] = Field(default_factory=list, max_length=100)
    overrideCutoff: bool = False
    reason: str = Field(default='', max_length=1000)
    previewHash: str | None = Field(default=None, pattern=r'^[a-f0-9]{64}$')

    @model_validator(mode='after')
    def valid(self):
        if self.action == 'reserve':
            if not self.reservations or self.orders: raise ValueError('Выберите заказы для оформления.')
            keys = [(r.menuId, r.studentId) for r in self.reservations]
        else:
            if not self.orders or self.reservations: raise ValueError('Выберите существующие заказы.')
            keys = [r.id for r in self.orders]
        if len(set(keys)) != len(keys): raise ValueError('В выборе есть повторяющиеся заказы.')
        return self
