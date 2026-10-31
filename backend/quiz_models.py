"""Validated question snapshots, assignment settings and answer contracts."""
from datetime import date, datetime, timezone
from typing import Annotated, Literal
import unicodedata
from zoneinfo import ZoneInfo

from pydantic import Field, field_validator, model_validator
from .models import Model
from .assessment_categories import SHAPE


class Option(Model):
    id: str = Field(min_length=1,max_length=80)
    text: str = Field(min_length=1,max_length=1000)


class Attachment(Model):
    id: str = Field(pattern=r'^[a-f0-9]{32}$')
    filename: str = Field(min_length=1,max_length=180)
    size: int = Field(ge=1,le=10*1024*1024)
    created: str = Field(max_length=80)


class Question(Model):
    kind: Literal['single','multiple','short','number','essay']
    text: str = Field(min_length=1,max_length=6000)
    points: float = Field(default=1,gt=0,le=1000,allow_inf_nan=False)
    options: list[Option] = Field(default_factory=list,max_length=12)
    correctIds: list[str] = Field(default_factory=list,max_length=12)
    accepted: list[str] = Field(default_factory=list,max_length=20)
    caseSensitive: bool = False
    number: float | None = Field(default=None,ge=-1e12,le=1e12,allow_inf_nan=False)
    tolerance: float = Field(default=0,ge=0,le=1e12,allow_inf_nan=False)
    explanation: str = Field(default='',max_length=4000)
    fileIds: list[Annotated[str, Field(pattern=r'^[a-f0-9]{32}$')]] = Field(default_factory=list,max_length=6)
    solutionFileIds: list[Annotated[str, Field(pattern=r'^[a-f0-9]{32}$')]] = Field(default_factory=list,max_length=6)
    # Accepted for editor round trips; bind_question replaces all client metadata.
    attachments: list[Attachment] = Field(default_factory=list,max_length=6)
    solutionAttachments: list[Attachment] = Field(default_factory=list,max_length=6)

    @model_validator(mode='after')
    def valid(self):
        files=self.fileIds+self.solutionFileIds
        if len(files)!=len(set(files)):raise ValueError('Файлы вопроса и разбора должны быть разными и не повторяться.')
        ids=[o.id for o in self.options]
        if len(ids)!=len(set(ids)) or len(self.correctIds)!=len(set(self.correctIds)):raise ValueError('Варианты ответа должны быть уникальны.')
        if self.kind in ['single','multiple']:
            if len(ids)<2 or not self.correctIds or set(self.correctIds)-set(ids):raise ValueError('Добавьте варианты и выберите правильные ответы.')
            if self.kind=='single' and len(self.correctIds)!=1:raise ValueError('Выберите один правильный ответ.')
        elif self.options or self.correctIds:raise ValueError('Варианты ответа допустимы только в вопросах с выбором.')
        if self.kind=='short':
            if not self.accepted or any(not normalize(a) or len(a)>1000 for a in self.accepted):raise ValueError('Укажите допустимые короткие ответы до 1000 символов.')
        elif self.accepted or self.caseSensitive:raise ValueError('Текстовые эталоны допустимы только для короткого ответа.')
        if self.kind=='number':
            if self.number is None:raise ValueError('Укажите правильное число.')
        elif self.number is not None or self.tolerance:raise ValueError('Число и погрешность допустимы только для числового ответа.')
        return self


class BankWrite(Model):
    version: int = Field(ge=0)
    subject: str = Field(default='',max_length=160)
    tags: list[str] = Field(default_factory=list,max_length=10)
    question: Question
    archived: bool = False
    @model_validator(mode='after')
    def valid_tags(self):
        if any(not t.strip() or len(t)>60 for t in self.tags):raise ValueError('Метки должны содержать от 1 до 60 символов.')
        return self


class Reference(Model):
    id: str = Field(min_length=1,max_length=80)
    version: int = Field(ge=1)


class Variant(Model):
    id: str = Field(min_length=1,max_length=80)
    name: str = Field(min_length=1,max_length=100)
    questions: list[Reference] = Field(min_length=1,max_length=50)
    @model_validator(mode='after')
    def unique(self):
        if len({q.id for q in self.questions})!=len(self.questions):raise ValueError('Вопрос повторяется в варианте.')
        return self


class Definition(Model):
    title: str = Field(min_length=1,max_length=160)
    instructions: str = Field(default='',max_length=4000)
    variants: list[Variant] = Field(min_length=1,max_length=8)
    opensAt: datetime
    closesAt: datetime
    timeLimitMinutes: int = Field(default=30,ge=1,le=240)
    maxAttempts: int = Field(default=1,ge=1,le=10)
    shuffleQuestions: bool = True
    shuffleOptions: bool = True
    # The journal category a transferred result lands in; empty means the school default.
    journalCategory: str = Field(default='',max_length=40)
    @field_validator('journalCategory',mode='before')
    @classmethod
    def category_shape(cls,value):
        if not isinstance(value,str) or not SHAPE.fullmatch(value):raise ValueError('Категория журнала указана неверно.')
        return value
    @model_validator(mode='after')
    def valid_window(self):
        if self.opensAt.tzinfo is None:self.opensAt=self.opensAt.replace(tzinfo=ZoneInfo('Asia/Qyzylorda'))
        if self.closesAt.tzinfo is None:self.closesAt=self.closesAt.replace(tzinfo=ZoneInfo('Asia/Qyzylorda'))
        try:
            self.opensAt=self.opensAt.astimezone(timezone.utc);self.closesAt=self.closesAt.astimezone(timezone.utc)
        except (ValueError,OverflowError):raise ValueError('Дата выходит за допустимые границы.')
        if not 0<(self.closesAt-self.opensAt).total_seconds()<=366*86400:raise ValueError('Период теста должен быть от одного мгновения до 366 дней.')
        if len({v.id for v in self.variants})!=len(self.variants):raise ValueError('Идентификаторы вариантов повторяются.')
        return self


class QuizWrite(Model):
    version: int = Field(ge=0)
    revision: int = Field(ge=0)
    # A lesson test names the lesson and its day; a course test names the group and the subject, and no lesson.
    lessonKey: str | None = Field(default=None,pattern=r'^[a-f0-9]{64}$')
    day: date | None = None
    groupId: str | None = Field(default=None,min_length=1,max_length=100)
    subjectId: str | None = Field(default=None,min_length=1,max_length=100)
    definition: Definition
    studentIds: list[str] = Field(min_length=1,max_length=500)
    variantsByStudent: dict[str,str] = Field(default_factory=dict,max_length=500)
    publish: bool = False
    copyFrom: str | None = Field(default=None,max_length=80)
    @model_validator(mode='after')
    def scope(self):
        lesson,course=[self.lessonKey,self.day],[self.groupId,self.subjectId]
        if not (all(x is not None for x in lesson) and all(x is None for x in course) or all(x is None for x in lesson) and all(x is not None for x in course)):
            raise ValueError('Укажите урок или курс теста.')
        return self
    @model_validator(mode='after')
    def roster(self):
        if len(set(self.studentIds))!=len(self.studentIds):raise ValueError('Ученики повторяются.')
        if set(self.variantsByStudent)-set(self.studentIds):raise ValueError('Вариант задан неназначенному ученику.')
        if set(self.variantsByStudent.values())-{v.id for v in self.definition.variants}:raise ValueError('Выбран отсутствующий вариант.')
        return self


class Version(Model):
    version: int = Field(ge=1)


class QuizAction(Version):
    action: Literal['close','withdraw','release']
    reason: str = Field(min_length=1,max_length=1000)


class Start(Version):
    studentId: str = Field(min_length=1,max_length=100)
    requestId: str = Field(pattern=r'^[a-f0-9-]{32,36}$')


class Answer(Model):
    selected: list[str] = Field(default_factory=list,max_length=12)
    text: str = Field(default='',max_length=10000)


class SaveAnswers(Version):
    answers: dict[str,Answer] = Field(max_length=50)
    submit: bool = False


class Mark(Model):
    score: float = Field(ge=0,le=1000,allow_inf_nan=False)
    comment: str = Field(default='',max_length=2000)


class ReviewAttempt(Version):
    marks: dict[str,Mark] = Field(max_length=50)
    comment: str = Field(default='',max_length=4000)
    reason: str = Field(default='',max_length=1000)


def normalize(value, case=False):
    result=' '.join(unicodedata.normalize('NFKC',value).split())
    return result if case else result.casefold()


def auto_mark(question, answer):
    kind=question['kind'];selected=answer.get('selected',[]);text=answer.get('text','')
    if kind=='essay':return None
    if kind in ['single','multiple']:correct=set(selected)==set(question['correctIds'])
    elif kind=='short':correct=normalize(text,question['caseSensitive']) in [normalize(a,question['caseSensitive']) for a in question['accepted']]
    else:
        try:
            from decimal import Decimal, DecimalException
            number=Decimal(text.strip().replace(',','.'))
            correct=number.is_finite() and abs(number-Decimal(str(question['number'])))<=Decimal(str(question['tolerance']))
        except (DecimalException,ValueError):correct=False
    return dict(score=question['points'] if correct else 0,comment='',automatic=True)


def public_question(item):
    q=item['question']
    return dict(id=item['id'],kind=q['kind'],text=q['text'],points=q['points'],options=q['options'],attachments=q.get('attachments',[]))
