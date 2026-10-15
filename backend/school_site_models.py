"""Explicit public content; never accepts workspace/roster documents or raw HTML."""
from datetime import datetime
from typing import Literal
from uuid import UUID
from urllib.parse import urlsplit
from pydantic import Field, model_validator
from .models import Model


class Block(Model):
    kind: Literal['text', 'heading', 'link'] = 'text'
    text: str = Field(min_length=1, max_length=5000)
    url: str = Field(default='', max_length=1500)

    @model_validator(mode='after')
    def check(self):
        if not self.text.strip(): raise ValueError('Заполните текст блока.')
        if self.kind == 'link':
            parsed = urlsplit(self.url)
            if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or any(c.isspace() for c in self.url):
                raise ValueError('Ссылка должна быть полным адресом HTTPS без логина и пароля.')
        elif self.url: raise ValueError('Адрес нужен только блоку ссылки.')
        return self


class Photo(Model):
    id: str = Field(pattern='^[a-f0-9]{32}$')
    alt: str = Field(min_length=1, max_length=300)
    caption: str = Field(default='', max_length=500)


class Content(Model):
    kind: Literal['page', 'news', 'event', 'gallery'] = 'page'
    slug: str = Field(pattern='^[a-z0-9][a-z0-9-]{0,79}$')
    title: str = Field(min_length=1, max_length=160)
    summary: str = Field(default='', max_length=600)
    blocks: list[Block] = Field(default_factory=list, max_length=30)
    photos: list[Photo] = Field(default_factory=list, max_length=12)
    startsAt: datetime | None = None
    endsAt: datetime | None = None
    place: str = Field(default='', max_length=300)

    @model_validator(mode='after')
    def check(self):
        if not self.title.strip(): raise ValueError('Укажите заголовок.')
        if sum(len(b.text)+len(b.url) for b in self.blocks) > 20000: raise ValueError('Сократите материал до 20 000 символов.')
        if len({p.id for p in self.photos}) != len(self.photos): raise ValueError('Фото не должно повторяться.')
        if self.kind == 'event':
            if not self.startsAt or not self.endsAt or self.startsAt.tzinfo is None or self.endsAt.tzinfo is None or self.startsAt >= self.endsAt:
                raise ValueError('Укажите начало и конец события с часовым поясом.')
            if (self.endsAt-self.startsAt).days > 366: raise ValueError('Событие не может длиться больше года.')
        elif self.startsAt or self.endsAt or self.place: raise ValueError('Время и место относятся только к событию.')
        return self


class SiteData(Model):
    title: str = Field(min_length=1, max_length=160)
    intro: str = Field(default='', max_length=3000)
    contact: str = Field(default='', max_length=1500)
    footer: str = Field(default='', max_length=1500)
    menuIds: list[str] = Field(default_factory=list, max_length=12)
    indexable: bool = False

    @model_validator(mode='after')
    def check(self):
        if not self.title.strip(): raise ValueError('Укажите название сайта.')
        if len(set(self.menuIds)) != len(self.menuIds): raise ValueError('Пункт меню не должен повторяться.')
        return self


class Mutation(Model):
    requestId: UUID
    version: int = Field(ge=0)
    reason: str = Field(default='', max_length=1000)


class SaveEntry(Mutation):
    data: Content


class SaveConfig(Mutation):
    data: SiteData


class AccessChange(Mutation):
    accountIds: list[str] = Field(max_length=30)


class Preview(Model):
    version: int = Field(ge=1)


class Action(Mutation):
    action: Literal['publish', 'withdraw', 'archive', 'restore', 'restore_revision']
    token: str = Field(default='', max_length=100)
    confirmedPublic: bool = False
    historyId: int | None = Field(default=None, ge=1)
