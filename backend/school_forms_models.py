"""Contracts for named school forms. Independent of timetable and journal writes."""
from datetime import date
import math
from typing import Literal
from pydantic import AwareDatetime, Field, model_validator
from fastapi import HTTPException
from .models import Model
from .notifications import encoded

STAFF = {'admin', 'editor', 'teacher', 'staff', 'viewer'}
ROLES = STAFF | {'student', 'parent'}


class Question(Model):
    id: str = Field(pattern=r'^[a-zA-Z0-9_-]{1,64}$')
    title: str = Field(min_length=1, max_length=300)
    kind: Literal['text', 'single', 'multiple', 'consent', 'date', 'number'] = 'text'
    required: bool = True
    choices: list[str] = Field(default_factory=list, max_length=20)

    @model_validator(mode='after')
    def valid(self):
        if self.kind in ['single', 'multiple']:
            if not 2 <= len(self.choices) <= 20 or len(set(self.choices)) != len(self.choices) or any(not x.strip() or len(x)>160 for x in self.choices):
                raise ValueError('Добавьте от 2 до 20 разных непустых вариантов.')
        elif self.choices:
            raise ValueError('Варианты допустимы только для вопросов с выбором.')
        return self


class Audience(Model):
    roles: list[Literal['admin','editor','teacher','staff','viewer','student','parent']] = Field(default_factory=list,max_length=7)
    classIds: list[str] = Field(default_factory=list,max_length=200)

    @model_validator(mode='after')
    def unique(self):
        if len(set(self.roles)) != len(self.roles) or len(set(self.classIds)) != len(self.classIds):
            raise ValueError('Не повторяйте роли и классы.')
        return self


class Definition(Model):
    title: str = Field(min_length=1,max_length=160)
    kind: Literal['form','survey','consent','application'] = 'form'
    description: str = Field(default='',max_length=6000)
    questions: list[Question] = Field(default_factory=list,max_length=30)
    audience: Audience = Field(default_factory=Audience)
    opensAt: AwareDatetime | None = None
    closesAt: AwareDatetime | None = None

    @model_validator(mode='after')
    def valid(self):
        if len({q.id for q in self.questions}) != len(self.questions): raise ValueError('Вопросы должны иметь разные идентификаторы.')
        if self.opensAt and self.closesAt and self.opensAt >= self.closesAt: raise ValueError('Окончание должно быть позже начала.')
        if len(encoded(self.model_dump(mode='json')).encode()) > 32000: raise ValueError('Форма слишком велика: сократите вопросы и варианты.')
        return self


class Mutation(Model):
    requestId: str = Field(pattern=r'^[a-f0-9-]{36}$')
    version: int = Field(ge=0)


class NewForm(Model):
    requestId: str = Field(pattern=r'^[a-f0-9-]{36}$')
    title: str = Field(min_length=1,max_length=160)
    kind: Literal['form','survey','consent','application'] = 'form'


class EditForm(Mutation):
    definition: Definition


class Collaborator(Model):
    userId: str = Field(min_length=1,max_length=100)
    permission: Literal['manage','review'] = 'manage'


class AccessChange(Mutation):
    collaborators: list[Collaborator] = Field(default_factory=list,max_length=20)

    @model_validator(mode='after')
    def unique(self):
        if len({x.userId for x in self.collaborators}) != len(self.collaborators): raise ValueError('Не повторяйте сотрудников.')
        return self


class Transition(Mutation):
    action: Literal['publish','close','reopen','archive']
    audienceHash: str | None = Field(default=None,max_length=64)
    closesAt: AwareDatetime | None = None


class AnswerWrite(Mutation):
    formVersion: int = Field(ge=1)
    entityId: str = Field(default='',max_length=100)
    action: Literal['save','submit','withdraw']
    answers: dict[str, str | list[str] | bool | int | float | None] = Field(default_factory=dict,max_length=30)


class Review(Mutation):
    formVersion: int = Field(ge=1)
    action: Literal['approved','rejected','returned']
    comment: str = Field(default='',max_length=2000)

    @model_validator(mode='after')
    def reason(self):
        if self.action in ['rejected','returned'] and not self.comment.strip(): raise ValueError('Объясните решение или что нужно исправить.')
        return self


def validate_answers(definition, answers, submitting):
    if len(encoded(answers).encode()) > 16000: raise HTTPException(422,'Ответ слишком велик. Сократите текст.')
    questions={q['id']:q for q in definition['questions']}
    if set(answers)-questions.keys(): raise HTTPException(422,'Форма изменилась: неизвестный вопрос.')
    for id,q in questions.items():
        value=answers.get(id)
        empty=value is None or value == '' or value == []
        if empty:
            if submitting and q['required']: raise HTTPException(422,'Ответьте на все обязательные вопросы.')
            continue
        kind=q['kind']
        valid=(kind=='text' and type(value) is str and len(value)<=4000 or
               kind=='single' and type(value) is str and value in q['choices'] or
               kind=='multiple' and type(value) is list and len(set(value))==len(value) and set(value)<=set(q['choices']) or
               kind=='consent' and type(value) is bool or
               kind=='number' and type(value) in [int,float] and math.isfinite(value) and abs(value)<=1e12)
        if kind=='date' and type(value) is str:
            try: valid=date.fromisoformat(value).isoformat()==value
            except ValueError: valid=False
        if not valid: raise HTTPException(422,'Проверьте формат ответа и доступные варианты.')
