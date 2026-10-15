"""Commands for teacher-paced sessions; results stay outside the journal."""
from typing import Literal
from pydantic import Field, model_validator
from .models import Model
from .quiz_models import Reference, Answer


class Mutation(Model):
    requestId: str = Field(pattern=r'^[a-zA-Z0-9_-]{16,80}$')
    version: int = Field(default=0, ge=0)


class QuestionReference(Reference):
    materials: list[Reference] = Field(default_factory=list, max_length=3)
    solutionMaterials: list[Reference] = Field(default_factory=list, max_length=3)

    @model_validator(mode='after')
    def unique_materials(self):
        for refs in (self.materials, self.solutionMaterials):
            if len({r.id for r in refs}) != len(refs):
                raise ValueError('Материал указан несколько раз.')
        return self


class Definition(Model):
    title: str = Field(min_length=1, max_length=160)
    instructions: str = Field(default='', max_length=4000)
    groupId: str = Field(min_length=1, max_length=80)
    subjectId: str = Field(min_length=1, max_length=80)
    questions: list[QuestionReference] = Field(min_length=1, max_length=50)

    @model_validator(mode='after')
    def unique(self):
        if not self.title.strip() or len({q.id for q in self.questions}) != len(self.questions):
            raise ValueError('Укажите название и выберите разные вопросы.')
        return self


class Save(Mutation):
    definition: Definition


class Control(Mutation):
    action: Literal['start', 'open', 'close', 'reveal', 'end', 'cancel', 'copy']
    questionIndex: int = Field(default=0, ge=0, le=49)
    seconds: int = Field(default=120, ge=10, le=7200)
    reason: str = Field(default='', max_length=1000)


class AnswerWrite(Mutation):
    studentId: str = Field(min_length=1, max_length=80)
    questionIndex: int = Field(ge=0, le=49)
    answer: Answer
    submit: bool = False


class Review(Mutation):
    studentId: str = Field(min_length=1, max_length=80)
    questionIndex: int = Field(ge=0, le=49)
    score: float = Field(ge=0, le=1000, allow_inf_nan=False)
    comment: str = Field(default='', max_length=4000)
    reason: str = Field(default='', max_length=1000)
