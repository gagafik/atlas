"""Prospective families are independent of enrolled pupils and school accounts."""
from datetime import date
from typing import Literal
from uuid import UUID
from pydantic import AwareDatetime, Field, SecretStr, model_validator
from .models import Model
from .access import Credentials
from .school_forms_models import Question


class DocumentKind(Model):
    id: str = Field(pattern='^[a-zA-Z0-9_-]{1,40}$')
    title: str = Field(min_length=1, max_length=160)
    required: bool = True


class CampaignData(Model):
    title: str = Field(min_length=1, max_length=160)
    year: str = Field(min_length=1, max_length=40)
    description: str = Field(default='', max_length=5000)
    contact: str = Field(min_length=1, max_length=1000)
    privacyNotice: str = Field(min_length=1, max_length=5000)
    grades: list[int] = Field(min_length=1, max_length=12)
    opensAt: AwareDatetime
    closesAt: AwareDatetime
    documents: list[DocumentKind] = Field(default_factory=list, max_length=8)
    questions: list[Question] = Field(default_factory=list, max_length=20)

    @model_validator(mode='after')
    def check(self):
        if self.opensAt >= self.closesAt: raise ValueError('Окончание приёма должно быть позже начала.')
        if len(set(self.grades)) != len(self.grades) or any(g < 0 or g > 11 for g in self.grades): raise ValueError('Укажите неповторяющиеся параллели от 0 до 11.')
        if len({d.id for d in self.documents}) != len(self.documents) or len({q.id for q in self.questions}) != len(self.questions): raise ValueError('Поля анкеты не должны повторяться.')
        if len(self.model_dump_json().encode()) > 30000: raise ValueError('Сократите описание анкеты.')
        return self


class Mutation(Model):
    requestId: UUID
    version: int = Field(ge=0)


class CampaignSave(Mutation):
    data: CampaignData


class CampaignAction(Mutation):
    action: Literal['open', 'close', 'archive']
    reason: str = Field(min_length=1, max_length=1000)


class Reviewers(Mutation):
    accountIds: list[str] = Field(max_length=30)


class FamilyLogin(Credentials):
    login: str = Field(min_length=1, max_length=64)
    password: SecretStr = Field(min_length=1, max_length=128)


class FamilyRegister(Credentials):
    campaignId: str = Field(pattern='^[a-f0-9]{32}$')
    login: str = Field(pattern='^[a-z0-9][a-z0-9._-]{2,63}$')
    name: str = Field(min_length=1, max_length=160)
    password: SecretStr = Field(min_length=12, max_length=128)
    recoveryCode: SecretStr = Field(min_length=32, max_length=128)


class FamilyRecover(Credentials):
    login: str = Field(min_length=1, max_length=64)
    recoveryCode: SecretStr = Field(min_length=32, max_length=128)
    password: SecretStr = Field(min_length=12, max_length=128)
    newRecoveryCode: SecretStr = Field(min_length=32, max_length=128)


class Attachment(Model):
    id: str = Field(pattern='^[a-f0-9]{32}$')
    kind: str = Field(pattern='^[a-zA-Z0-9_-]{1,40}$')


class ApplicationData(Model):
    childName: str = Field(default='', max_length=160)
    birthDate: date | None = None
    grade: int | None = Field(default=None, ge=0, le=11)
    guardianName: str = Field(default='', max_length=160)
    email: str = Field(default='', max_length=254)
    phone: str = Field(default='', max_length=80)
    previousSchool: str = Field(default='', max_length=300)
    answers: dict[str, str | list[str] | bool | int | float | None] = Field(default_factory=dict, max_length=20)
    files: list[Attachment] = Field(default_factory=list, max_length=16)
    consent: bool = False

    @model_validator(mode='after')
    def check(self):
        if len({f.id for f in self.files}) != len(self.files): raise ValueError('Документ указан дважды.')
        if len(self.model_dump_json().encode()) > 24000: raise ValueError('Сократите анкету.')
        return self


class ApplicationCreate(Mutation):
    campaignId: str = Field(pattern='^[a-f0-9]{32}$')


class ApplicationSave(Mutation):
    data: ApplicationData
    action: Literal['save', 'submit'] = 'save'


class Withdrawal(Mutation):
    reason: str = Field(min_length=1, max_length=2000)


class Review(Mutation):
    action: Literal['in_review', 'returned', 'waitlisted', 'accepted', 'rejected', 'reopen', 'note', 'message']
    message: str = Field(min_length=1, max_length=4000)
    replyUntil: AwareDatetime | None = None

    @model_validator(mode='after')
    def deadline(self):
        if self.action == 'returned' and not self.replyUntil: raise ValueError('Укажите срок для уточнений семьи.')
        if self.action != 'returned' and self.replyUntil: raise ValueError('Срок нужен только при запросе уточнений.')
        return self
