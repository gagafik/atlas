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
ANONYMOUS_MIN = 5  # fewest addressees and answers for which an anonymous survey shows anything


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


class FormQuestion(Question):
    """A question of a school form. Admissions reuse Question and gain no seat limits."""
    limits: dict[str, int] = Field(default_factory=dict, max_length=20)

    @model_validator(mode='after')
    def seats(self):
        if self.limits and (self.kind not in ['single', 'multiple'] or not set(self.limits) <= set(self.choices) or any(not 1 <= v <= 5000 for v in self.limits.values())):
            raise ValueError('Лимит мест задаётся для существующих вариантов: от 1 до 5000.')
        return self


class Audience(Model):
    roles: list[Literal['admin','editor','teacher','staff','viewer','student','parent']] = Field(default_factory=list,max_length=7)
    classIds: list[str] = Field(default_factory=list,max_length=200)
    # Presets are the audiences of announcements (message_audiences.py); 'roles' is the audience by roles and classes.
    preset: Literal['roles','teachers','class_teachers','staff','saved'] = 'roles'
    savedGroupId: str | None = Field(default=None,max_length=100)

    @model_validator(mode='after')
    def unique(self):
        if len(set(self.roles)) != len(self.roles) or len(set(self.classIds)) != len(self.classIds):
            raise ValueError('Не повторяйте роли и классы.')
        if self.preset != 'roles' and (self.roles or self.classIds): raise ValueError('Для этой аудитории роли и классы не выбираются.')
        if self.preset == 'saved' and not self.savedGroupId: raise ValueError('Выберите сохранённую группу.')
        if self.preset != 'saved' and self.savedGroupId: raise ValueError('Сохранённая группа относится только к одноимённой аудитории.')
        return self


class Definition(Model):
    title: str = Field(min_length=1,max_length=160)
    kind: Literal['form','survey','consent','application'] = 'form'
    description: str = Field(default='',max_length=6000)
    questions: list[FormQuestion] = Field(default_factory=list,max_length=30)
    audience: Audience = Field(default_factory=Audience)
    opensAt: AwareDatetime | None = None
    closesAt: AwareDatetime | None = None
    standing: bool = False  # accepts answers without a deadline from accounts that appear after publication
    anonymous: bool = False  # a survey whose answers are stored without any link to the person

    @model_validator(mode='after')
    def valid(self):
        if len({q.id for q in self.questions}) != len(self.questions): raise ValueError('Вопросы должны иметь разные идентификаторы.')
        if self.opensAt and self.closesAt and self.opensAt >= self.closesAt: raise ValueError('Окончание должно быть позже начала.')
        if self.anonymous and self.kind != 'survey': raise ValueError('Анонимным может быть только опрос.')
        if self.anonymous and any(q.limits for q in self.questions): raise ValueError('В анонимном опросе нет лимита мест: он связал бы ответ с человеком.')
        if self.anonymous and self.standing: raise ValueError('Анонимный опрос не может быть постоянным: ему нужен известный состав адресатов.')
        if self.standing and self.closesAt: raise ValueError('Постоянная форма принимает ответы без срока.')
        if self.standing and self.audience.preset != 'roles': raise ValueError('Постоянная форма адресуется по ролям и классам.')
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


class Reminder(Model):
    requestId: str = Field(pattern=r'^[a-f0-9-]{36}$')


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
