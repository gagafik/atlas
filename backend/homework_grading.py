"""Atomic homework review grades using journal revisions and published projections."""
from copy import deepcopy
from datetime import date

from fastapi import HTTPException
from pydantic import Field, ValidationError, model_validator

from .models import Model
from .journal import Assessment, Content, Grade, signature, staff_read
from .journal_periods import locks, require_open
from .assessment_categories import config, names


FOREIGN='Урок по расписанию ведёт другой учитель: оценку в журнал переносит он или администратор. Проверку можно сохранить без оценки.'


class ReviewGrade(Model):
    journalVersion: int = Field(ge=1)
    revision: int = Field(ge=0)
    assessmentId: str = Field(min_length=1,max_length=80)
    newAssessment: Assessment | None = None
    score: float | None = Field(default=None,ge=0,le=10000,allow_inf_nan=False)
    label: str = Field(default='',max_length=30)
    comment: str = Field(default='',max_length=1000)
    replaceExisting: bool = False

    @model_validator(mode='after')
    def actual_grade(self):
        if self.score is None and not self.label:raise ValueError('Укажите баллы или текстовую оценку.')
        if self.newAssessment and self.newAssessment.id!=self.assessmentId:raise ValueError('Идентификаторы новой работы должны совпадать.')
        return self


def assessment_map(document):
    return {a['id']:a for a in document['content']['assessments']}


def pupil_record(document, student):
    return next((p for p in document['content']['records'] if p['studentId']==student),None)


def existing_grade(document, student, assessment):
    pupil=pupil_record(document,student)
    return next((g for g in pupil['grades'] if g['assessmentId']==assessment),None) if pupil else None


def meaningful(grade):
    return bool(grade and (grade['score'] is not None or grade['label'] or grade['comment']))


class HomeworkGrading:
    def __init__(self, homework):
        self.homework=homework
        self.journal=homework.journal

    def journal_access(self, ctx):
        """The grade lands in the journal, which only its current lesson's teachers (or an admin) may write.

        A frozen past lesson can still list the scheduled teacher while a substitute held it,
        so the stored lesson alone must not let a course teacher past this check."""
        user,workspace=ctx['user'],ctx['workspace']
        return user['role']=='admin' or bool(ctx['current'] and staff_read(user,workspace,ctx['current']))

    def default_category(self, ctx, cfg):
        """A new work lands in the quiz's journal category when it is still active, else in the school default."""
        quiz=ctx.get('quiz')
        wanted=quiz and (quiz['published'] or quiz['document'])['definition'].get('journalCategory','')
        found=names(cfg).get(wanted)
        return wanted if found and found['active'] else cfg['defaultCategory']

    def prepare(self, ctx, *, db=None):
        if not ctx['canReview']:raise HTTPException(403,'Оценку выставляет учитель курса или администратор.')
        if not self.journal_access(ctx):raise HTTPException(409,FOREIGN)
        # Homework.context already resolved this lesson against its cached state.
        # recheck() validates that state, account and journal again in the transaction.
        return self.journal.resolved_entry(ctx['state'],ctx['workspace'],ctx['user'],ctx['key'],ctx['record'],ctx['current'],db=db)

    def reason(self, db, ctx, record):
        closed=locks(db,ctx['workspace'],record['document']['lesson'],ctx['published']['lesson'],ctx['current'])
        if closed:return 'Журнал закрыт в периоде: '+', '.join(p['name'] for p in closed)+'.'
        if not record['writable']:return 'Урок доступен только для просмотра.'
        if record['needsSync'] or signature(record['document']['lesson'])!=signature(ctx['published']['lesson']):
            return 'Сначала обновите и опубликуйте состав и время урока в журнале.'
        day=date.fromisoformat(record['document']['lesson']['date'])
        if record['currentDate']!=day.isoformat() or not ctx['state'].settings.yearStart<=day<=ctx['state'].settings.yearEnd:
            return 'Дата урока изменилась или находится вне учебного года.'
        from .journal import today
        if day>today():return 'Урок ещё не прошёл: оценку можно перенести в журнал в день урока или позже.'
        if any(pupil_record(d,ctx['student']) is None for d in [record['document'],ctx['published']]):
            return 'Ученик отсутствует в одной из редакций журнала.'
        return ''

    def recheck(self, db, ctx, record):
        self.homework.recheck(db,ctx)
        row=db.execute('SELECT * FROM journal_entries WHERE workspace=? AND lesson_key=?',(ctx['workspace'],ctx['key'])).fetchone()
        if not row or row[2]!=record['version']:raise HTTPException(409,'Журнал изменился. Обновите параметры оценки.')
        return self.journal.unpack(row)

    def options(self, ctx, *, db=None):
        if ctx['canReview'] and not self.journal_access(ctx):
            return dict(journalVersion=ctx['record']['version'],revision=ctx['revision'],allowed=False,reason=FOREIGN,hasDraftChanges=False,canCreate=False,assessments=[],categories=[],defaultCategory='')
        record=self.prepare(ctx) if db is None else self.prepare(ctx,db=db)
        def read(db):
            saved=self.recheck(db,ctx,record)
            reason=self.reason(db,ctx,record)
            draft=assessment_map(saved['document']);published=assessment_map(saved['published']);cfg=config(db,ctx['workspace'])
            return dict(journalVersion=record['version'],revision=ctx['revision'],allowed=not reason,reason=reason,categories=cfg['categories'],defaultCategory=self.default_category(ctx,cfg),
                hasDraftChanges=saved['version']!=saved['publishedVersion'],canCreate=not reason and max(len(draft),len(published))<10,
                assessments=[dict(**a,selectable=not reason and a==draft.get(id),
                    reason='Параметры этой работы изменены в черновике. Сначала согласуйте их в журнале.' if a!=draft.get(id) else reason,
                    publishedGrade=existing_grade(saved['published'],ctx['student'],id),
                    draftGrade=existing_grade(saved['document'],ctx['student'],id)) for id,a in published.items()])
        if db is not None:return read(db)
        with self.homework.access.lock,self.homework.access.connect() as connection:
            self.homework.tables(connection);connection.execute('BEGIN')
            return read(connection)

    def apply(self, db, ctx, record, body, created):
        if body.revision!=ctx['revision'] or body.journalVersion!=record['version']:
            raise HTTPException(409,'Журнал или расписание изменились. Обновите параметры оценки.')
        saved=self.recheck(db,ctx,record)
        reason=self.reason(db,ctx,record)
        if reason:raise HTTPException(409,reason)
        require_open(db,ctx['workspace'],record['document']['lesson'],saved['published']['lesson'],ctx['current'])
        draft,published=deepcopy(saved['document']),deepcopy(saved['published'])
        if body.newAssessment:
            assessment=body.newAssessment.model_dump(mode='json')
            # The calendar link is made in the lesson's journal; a grade form only picks a category.
            if assessment.pop('bookingId',None):raise HTTPException(422,'Связь с календарём контрольных задаётся в журнале урока.')
            cfg=config(db,ctx['workspace']);assessment['category']=assessment['category'] or self.default_category(ctx,cfg)
            found=names(cfg).get(assessment['category'])
            if not found or not found['active']:raise HTTPException(422,'Выберите действующую категорию работы.')
            if any(body.assessmentId in assessment_map(d) for d in [draft,published]):
                raise HTTPException(409,'Такая работа уже существует. Выберите её после обновления.')
            for d in [draft,published]:d['content']['assessments'].append(assessment)
        else:
            assessment=assessment_map(published).get(body.assessmentId)
            if not assessment or assessment!=assessment_map(draft).get(body.assessmentId):
                raise HTTPException(409,'Параметры работы изменены или отсутствуют. Обновите журнал.')
        grade=Grade(assessmentId=body.assessmentId,score=body.score,label=body.label,comment=body.comment).model_dump(mode='json')
        if not body.replaceExisting and any(meaningful(existing_grade(d,ctx['student'],body.assessmentId)) for d in [draft,published]):
            raise HTTPException(409,'Оценка уже есть. Подтвердите замену опубликованного и чернового значения.')
        for document in [draft,published]:
            pupil=pupil_record(document,ctx['student'])
            if not pupil:raise HTTPException(409,'Состав журнала изменился.')
            pupil['grades']=[g for g in pupil['grades'] if g['assessmentId']!=body.assessmentId]+[grade]
            if {p['studentId'] for p in document['content']['records']}!={s['id'] for s in document['lesson']['students']}:
                raise HTTPException(409,'Состав записей журнала требует обновления.')
            try:Content.model_validate(document['content'])
            except ValidationError:raise HTTPException(422,'Проверьте формат и максимум оценки, параметры работы и лимит десяти работ в уроке.') from None
        # Two ordinary revisions preserve the existing version/publishedVersion contract.
        # Neither the transient publication nor its following draft is observable mid-commit.
        published_version=saved['version']+1
        updated=self.journal.store_revision(db,ctx['workspace'],ctx['key'],ctx['user'],ctx['revision'],published_version,
                                           published,saved,publish=True,created=created)
        if draft!=published:
            updated=self.journal.store_revision(db,ctx['workspace'],ctx['key'],ctx['user'],ctx['revision'],published_version+1,
                                               draft,updated,created=created)
        ctx['published']=published;ctx['record']=updated
        return dict(assessment=assessment,grade=grade,publishedVersion=published_version,
                    journalVersion=updated['version'],gradedAt=created,grader=ctx['user']['name'])
