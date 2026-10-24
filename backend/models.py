from datetime import date
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


class Model(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)


class Named(Model):
    id: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=160)


class SchoolClass(Named):
    maxDaily: int = Field(default=8, ge=1, le=12)
    profileId: str = 'primary'
    studentCount: int | None = Field(default=None, ge=1, le=500)


class Teacher(Named):
    unavailable: list[str] = Field(default_factory=list)


class Room(Named):
    capacity: int | None = Field(default=None, ge=1, le=5000)
    buildingId: str | None = None


class Transfer(Model):
    fromId: str
    toId: str
    minutes: int = Field(ge=0, le=180)


class Campus(Model):
    buildings: list[Named] = Field(default_factory=list)
    transfers: list[Transfer] = Field(default_factory=list)
    defaultTravelMinutes: int = Field(default=0, ge=0, le=180)

    @model_validator(mode='after')
    def valid_routes(self):
        ids = {b.id for b in self.buildings}
        pairs = {(t.fromId, t.toId) for t in self.transfers}
        if len(ids) != len(self.buildings) or len(pairs) != len(self.transfers):
            raise ValueError('Здания и направления переходов не должны повторяться.')
        if any(t.fromId not in ids or t.toId not in ids or t.fromId == t.toId for t in self.transfers):
            raise ValueError('Переход должен связывать два разных существующих здания.')
        return self


class Subject(Named):
    color: str = Field(default='#dfeade', pattern=r'^#[0-9a-fA-F]{6}$')


class Student(Named):
    classId: str
    externalId: str | None = None
    active: bool = True


class Group(Named):
    classIds: list[str] = Field(default_factory=list)
    studentIds: list[str] = Field(default_factory=list)
    kind: Literal['whole', 'core', 'math', 'elective', 'imported', 'staff'] = 'whole'
    rosterStatus: Literal['unknown', 'partial', 'complete'] | None = None
    rosterSources: list[str] = Field(default_factory=list)
    rosterNote: str = ''

    @model_validator(mode='after')
    def audience(self):
        if self.kind == 'staff':
            if self.classIds or self.studentIds:
                raise ValueError('Занятия без класса не должны содержать классы или учеников.')
        elif not self.classIds:
            raise ValueError('Выберите хотя бы один класс группы.')
        if len(set(self.studentIds)) != len(self.studentIds) or len(set(self.classIds)) != len(self.classIds):
            raise ValueError('Состав группы не должен содержать повторов.')
        return self


class Period(Model):
    label: str
    start: str = Field(pattern=r'^([01]\d|2[0-3]):[0-5]\d$')
    end: str = Field(pattern=r'^([01]\d|2[0-3]):[0-5]\d$')

    @model_validator(mode='after')
    def chronological(self):
        if self.end <= self.start:
            raise ValueError('Конец урока должен быть позже начала.')
        return self


class Profile(Named):
    periods: list[Period] = Field(min_length=1, max_length=12)
    dayPeriods: dict[str, list[Period]] = Field(default_factory=dict)

    def for_day(self, day):
        return self.dayPeriods.get(str(day), self.periods)

    @model_validator(mode='after')
    def day_bells(self):
        for key, periods in self.dayPeriods.items():
            if key not in [str(day) for day in range(7)]:
                raise ValueError('Недопустимый день в профиле звонков.')
            if len(periods) != len(self.periods):
                raise ValueError('Число периодов в отдельном дне должно совпадать с основным профилем. Номера уроков сохраняются.')
        for periods in [self.periods, *self.dayPeriods.values()]:
            if any(b.start < a.end for a, b in zip(periods, periods[1:])):
                raise ValueError('Периоды звонков должны идти по порядку и не пересекаться.')
        return self


class Event(Named):
    start: date
    end: date
    kind: Literal['holiday', 'winter', 'reading', 'extra', 'exam']
    grades: str = 'Все классы'
    effect: Literal['note', 'cancel'] = 'note'
    classIds: list[str] = Field(default_factory=list)

    @model_validator(mode='after')
    def chronological(self):
        if self.end < self.start:
            raise ValueError('Проверьте даты периода.')
        if self.effect == 'cancel' and (self.end - self.start).days > 366:
            raise ValueError('Отмена по календарю ограничена 367 днями.')
        return self


class DatedLesson(Named):
    kind: Literal['change', 'cancel', 'add']
    onDate: date
    sourceDate: date | None = None
    placementId: str | None = None
    groupId: str | None = None
    profileId: str | None = None
    subjectId: str | None = None
    startTime: str | None = Field(default=None, pattern=r'^([01]\d|2[0-3]):[0-5]\d$')
    endTime: str | None = Field(default=None, pattern=r'^([01]\d|2[0-3]):[0-5]\d$')
    teacherIds: list[str] | None = None
    roomIds: list[str] | None = None
    changeType: Literal['substitution', 'merge', 'move', 'self_study', 'cancel', 'extra'] | None = None
    mergeWith: str | None = Field(default=None, min_length=1, max_length=200)

    @model_validator(mode='after')
    def valid_change(self):
        if bool(self.startTime) != bool(self.endTime) or self.startTime and self.endTime <= self.startTime:
            raise ValueError('Укажите начало и конец занятия; конец позже начала.')
        if self.kind == 'add':
            if self.placementId or self.sourceDate or not all([self.groupId, self.profileId, self.subjectId, self.startTime, self.endTime]):
                raise ValueError('Разовому уроку нужны группа, профиль звонков для запретов учителя, предмет и время, без исходного урока.')
        else:
            if not self.placementId or not self.sourceDate or self.groupId:
                raise ValueError('Укажите исходный урок и дату. Состав его группы сохраняется.')
            if self.kind == 'cancel' and (self.onDate != self.sourceDate or any(v is not None for v in [self.subjectId, self.profileId, self.startTime, self.teacherIds, self.roomIds])):
                raise ValueError('Отмена относится к исходной дате и не содержит новых назначений.')
        for values in [self.teacherIds, self.roomIds]:
            if values is not None and len(values) != len(set(values)):
                raise ValueError('Учителя и кабинеты не должны повторяться.')
        allowed = dict(cancel=(None, 'cancel'), add=(None, 'extra'), change=(None, 'substitution', 'merge', 'move', 'self_study'))[self.kind]
        if self.changeType not in allowed:
            raise ValueError('Тип замены не подходит к виду изменения.')
        if (self.changeType == 'merge') != bool(self.mergeWith):
            raise ValueError('Для объединения выберите урок, к которому присоединяется группа.')
        if self.changeType == 'self_study' and not self.teacherIds:
            raise ValueError('Для самостоятельной работы укажите ответственного сотрудника.')
        if self.changeType == 'move' and self.onDate == self.sourceDate and not self.startTime:
            raise ValueError('Перенос должен менять дату или время урока.')
        return self


class TeacherAbsence(Named):
    teacherId: str
    start: date
    end: date
    startTime: str = Field(default='00:00', pattern=r'^([01]\d|2[0-3]):[0-5]\d$')
    endTime: str = Field(default='23:59', pattern=r'^([01]\d|2[0-3]):[0-5]\d$')

    @model_validator(mode='after')
    def valid_interval(self):
        if self.end < self.start or (self.end-self.start).days > 366 or self.endTime <= self.startTime:
            raise ValueError('Проверьте даты отсутствия (до 367 дней) и время каждого дня.')
        return self


class ScheduleDay(Named):
    """A date that runs another weekday's timetable (and optionally another week), for all or selected classes."""
    onDate: date
    day: int = Field(ge=0, le=6)
    cycleId: str | None = None
    classIds: list[str] = Field(default_factory=list, max_length=200)

    @model_validator(mode='after')
    def valid_day(self):
        if len(self.classIds) != len(set(self.classIds)):
            raise ValueError('Классы не должны повторяться.')
        if self.day == self.onDate.weekday() and self.cycleId is None:
            raise ValueError('Дата и так работает по этому дню недели. Выберите другой день или неделю.')
        return self


class Commitment(Named):
    kind: Literal['booking', 'duty'] = 'booking'
    onDate: date
    startTime: str = Field(pattern=r'^([01]\d|2[0-3]):[0-5]\d$')
    endTime: str = Field(pattern=r'^([01]\d|2[0-3]):[0-5]\d$')
    roomIds: list[str] = Field(default_factory=list, max_length=100)
    teacherIds: list[str] = Field(default_factory=list, max_length=300)
    groupIds: list[str] = Field(default_factory=list, max_length=300)
    profileId: str | None = None
    location: str = Field(default='', max_length=160)
    buildingId: str | None = None
    studentCount: int | None = Field(default=None, ge=0, le=10000)
    requiredTeachers: int = Field(default=0, ge=0, le=50)
    eligibleTeacherIds: list[str] = Field(default_factory=list, max_length=300)
    locked: bool = False
    note: str = Field(default='', max_length=4000)

    @model_validator(mode='after')
    def valid_commitment(self):
        if self.endTime <= self.startTime:
            raise ValueError('Конец бронирования/дежурства должен быть позже начала.')
        for values in [self.roomIds, self.teacherIds, self.groupIds, self.eligibleTeacherIds]:
            if len(values) != len(set(values)): raise ValueError('Назначения не должны повторяться.')
        if self.kind == 'booking' and not self.roomIds:
            raise ValueError('Выберите хотя бы один кабинет для бронирования.')
        if self.kind == 'duty' and (self.requiredTeachers < 1 or not self.roomIds and not self.location):
            raise ValueError('Для дежурства нужны место и число сотрудников (не меньше 1).')
        if (self.teacherIds or self.kind == 'duty') and not self.profileId:
            raise ValueError('Выберите профиль звонков для запретов учителей по номерам уроков.')
        if self.kind == 'booking' and (self.requiredTeachers or self.eligibleTeacherIds):
            raise ValueError('Число дежурных и кандидаты задаются только для дежурства.')
        if self.eligibleTeacherIds and not set(self.teacherIds) <= set(self.eligibleTeacherIds):
            raise ValueError('Назначенный дежурный должен входить в список кандидатов.')
        return self


class SubstitutionWeights(Model):
    """Points a substitute earns per criterion; see SUBSTITUTIONS.md."""
    subject: int = Field(default=40, ge=0, le=100)
    group: int = Field(default=30, ge=0, le=100)
    presence: int = Field(default=20, ge=0, le=100)
    fairness: int = Field(default=20, ge=0, le=100)
    building: int = Field(default=10, ge=0, le=100)


class Settings(Model):
    name: str = 'Наша школа'
    yearStart: date = date(2026, 8, 17)
    yearEnd: date = date(2027, 6, 11)
    days: list[str] = Field(default_factory=lambda: ['Понедельник', 'Вторник', 'Среда', 'Четверг', 'Пятница'], min_length=1, max_length=7)
    shifts: Literal[1] = 1
    substitutionWeights: SubstitutionWeights = Field(default_factory=SubstitutionWeights)

    @model_validator(mode='after')
    def chronological(self):
        if self.yearEnd < self.yearStart:
            raise ValueError('Конец учебного года раньше его начала.')
        return self


class TeachingPeriod(Named):
    start: date
    end: date
    anchor: date
    patternIds: list[str] = Field(min_length=1, max_length=8)

    @model_validator(mode='after')
    def valid_dates(self):
        if self.end < self.start or self.anchor.weekday() != 0:
            raise ValueError('Проверьте границы периода; дата начала цикла должна быть понедельником.')
        return self


class Cycles(Model):
    patterns: list[Named] = Field(default_factory=lambda: [Named(id='weekly', name='Каждая неделя')], min_length=1, max_length=32)
    periods: list[TeachingPeriod] = Field(default_factory=list)

    @model_validator(mode='after')
    def valid_patterns(self):
        ids = {p.id for p in self.patterns}
        if len(ids) != len(self.patterns) or len({p.id for p in self.periods}) != len(self.periods):
            raise ValueError('Идентификаторы недель и учебных периодов не должны повторяться.')
        if any(id not in ids for period in self.periods for id in period.patternIds):
            raise ValueError('Учебный период ссылается на отсутствующую неделю расписания.')
        ordered = sorted(self.periods, key=lambda p: p.start)
        if any(b.start <= a.end for a, b in zip(ordered, ordered[1:])):
            raise ValueError('Учебные периоды не должны пересекаться по датам.')
        return self


class Workload(Model):
    id: str
    groupId: str
    subjectId: str
    teacherId: str | None = None
    additionalTeacherIds: list[str] = Field(default_factory=list)
    teacherRequired: bool = True
    hours: int = Field(ge=1, le=100)
    maxPerDay: int = Field(default=2, ge=1, le=12)
    roomIds: list[str] = Field(default_factory=list)
    requiredRoomIds: list[str] = Field(default_factory=list)
    roomRequired: bool = True
    studentCount: int | None = Field(default=None, ge=1, le=5000)
    profileId: str = 'primary'
    block: str = ''
    allowedPeriods: list[int] = Field(default_factory=list)
    lessonLengths: list[int] = Field(default_factory=list)
    maxLessonBreak: int = Field(default=20, ge=0, le=180)
    cycleIds: list[str] = Field(default_factory=list)
    cycleHours: dict[str, int] = Field(default_factory=dict)
    cycleLessonLengths: dict[str, list[int]] = Field(default_factory=dict)

    @model_validator(mode='after')
    def lesson_pattern(self):
        if len(set(self.cycleIds)) != len(self.cycleIds) or any(h < 1 or h > 100 for h in self.cycleHours.values()):
            raise ValueError('Недели нагрузки не должны повторяться; недельные часы — от 1 до 100.')
        teachers = ([self.teacherId] if self.teacherId else []) + self.additionalTeacherIds
        rooms = self.roomIds + self.requiredRoomIds
        if len(teachers) != len(set(teachers)) or len(rooms) != len(set(rooms)):
            raise ValueError('Учителя и кабинеты нагрузки не должны повторяться.')
        if not self.teacherRequired and teachers:
            raise ValueError('Для занятия без учителя очистите список преподавателей.')
        if not self.roomRequired and rooms:
            raise ValueError('Для занятия без кабинета очистите списки кабинетов.')
        if self.lessonLengths and (any(n not in [1, 2, 3] for n in self.lessonLengths) or sum(self.lessonLengths) != self.hours):
            raise ValueError('Длины занятий: только 1, 2 или 3; сумма должна совпадать с недельными часами.')
        return self


class UnavailableWindow(Model):
    day: int = Field(ge=0, le=6)
    start: str = Field(pattern=r'^([01]\d|2[0-3]):[0-5]\d$')
    end: str = Field(pattern=r'^([01]\d|2[0-3]):[0-5]\d$')

    @model_validator(mode='after')
    def chronological(self):
        if self.end <= self.start:
            raise ValueError('Конец недоступности должен быть позже начала.')
        return self


class TimetableRule(Named):
    kind: Literal['availability', 'daily_load', 'day_shape', 'distribution', 'relation']
    scope: Literal['class', 'teacher', 'student', 'group', 'subject', 'room', 'workload']
    targetIds: list[str] = Field(default_factory=list)
    days: list[int] = Field(default_factory=list)
    enabled: bool = True
    mode: Literal['hard', 'soft'] = 'hard'
    weight: int = Field(default=10, ge=1, le=1000)
    unavailable: list[UnavailableWindow] = Field(default_factory=list)
    minLessons: int | None = Field(default=None, ge=0, le=24)
    maxLessons: int | None = Field(default=None, ge=0, le=24)
    includeEmptyDays: bool = False
    earliestStart: str | None = Field(default=None, pattern=r'^([01]\d|2[0-3]):[0-5]\d$')
    latestEnd: str | None = Field(default=None, pattern=r'^([01]\d|2[0-3]):[0-5]\d$')
    maxIdleMinutes: int | None = Field(default=None, ge=0, le=1440)
    maxIdlePeriods: int | None = Field(default=None, ge=0, le=12)
    maxIdleBlocks: int | None = Field(default=None, ge=0, le=12)
    maxWeeklyIdlePeriods: int | None = Field(default=None, ge=0, le=84)
    gapProfileId: str | None = None
    gapExcludedPeriods: list[int] = Field(default_factory=list)
    maxConsecutive: int | None = Field(default=None, ge=1, le=24)
    shortBreakMinutes: int = Field(default=20, ge=0, le=180)
    lunchStart: str | None = Field(default=None, pattern=r'^([01]\d|2[0-3]):[0-5]\d$')
    lunchEnd: str | None = Field(default=None, pattern=r'^([01]\d|2[0-3]):[0-5]\d$')
    lunchMinutes: int | None = Field(default=None, ge=1, le=180)
    minDays: int | None = Field(default=None, ge=0, le=7)
    maxDays: int | None = Field(default=None, ge=0, le=7)
    minDayDistance: int = Field(default=1, ge=1, le=7)
    relation: Literal['before', 'not_following', 'same_day', 'different_days'] = 'before'
    subjectIds: list[str] = Field(default_factory=list)
    otherSubjectIds: list[str] = Field(default_factory=list)
    relationGapMinutes: int = Field(default=30, ge=0, le=180)
    cycleIds: list[str] = Field(default_factory=list)

    @model_validator(mode='after')
    def valid_options(self):
        if self.kind == 'relation':
            if self.scope in ['subject', 'workload']:
                raise ValueError('Для связи предметов выберите классы, учеников, учителей, группы или кабинеты.')
            if not self.subjectIds or not self.otherSubjectIds or set(self.subjectIds) & set(self.otherSubjectIds):
                raise ValueError('Выберите два непустых непересекающихся набора предметов.')
        for values in [self.subjectIds, self.otherSubjectIds, self.gapExcludedPeriods]:
            if len(values) != len(set(values)):
                raise ValueError('Предметы и исключённые периоды не должны повторяться.')
        if any(v is not None for v in [self.maxIdlePeriods, self.maxIdleBlocks, self.maxWeeklyIdlePeriods]) and not self.gapProfileId:
            raise ValueError('Выберите профиль звонков для подсчёта окон.')
        if len(set(self.targetIds)) != len(self.targetIds) or len(set(self.days)) != len(self.days):
            raise ValueError('Цели и дни правила не должны повторяться.')
        if self.kind == 'availability' and not self.unavailable:
            raise ValueError('Добавьте хотя бы один интервал недоступности.')
        if self.kind == 'daily_load' and self.minLessons is None and self.maxLessons is None:
            raise ValueError('Укажите минимальную или максимальную дневную нагрузку.')
        if self.minLessons is not None and self.maxLessons is not None and self.minLessons > self.maxLessons:
            raise ValueError('Минимальная нагрузка больше максимальной.')
        if self.earliestStart and self.latestEnd and self.earliestStart >= self.latestEnd:
            raise ValueError('Начало учебного дня должно быть раньше его конца.')
        lunch = [self.lunchStart, self.lunchEnd, self.lunchMinutes]
        if any(v is not None for v in lunch):
            if not all(v is not None for v in lunch) or self.lunchStart >= self.lunchEnd:
                raise ValueError('Для обеда укажите начало, конец окна и длительность перерыва.')
            def mins(t):
                h, m = map(int, t.split(':'))
                return h * 60 + m
            if mins(self.lunchEnd) - mins(self.lunchStart) < self.lunchMinutes:
                raise ValueError('Обеденный перерыв длиннее заданного окна.')
        if self.kind == 'day_shape' and all(v is None for v in [self.earliestStart, self.latestEnd, self.maxIdleMinutes, self.maxIdlePeriods, self.maxIdleBlocks, self.maxWeeklyIdlePeriods, self.maxConsecutive, self.lunchMinutes]):
            raise ValueError('Укажите хотя бы одно условие учебного дня.')
        if self.minDays is not None and self.maxDays is not None and self.minDays > self.maxDays:
            raise ValueError('Минимальное число дней больше максимального.')
        if self.kind == 'distribution' and self.minDays is None and self.maxDays is None and self.minDayDistance == 1:
            raise ValueError('Укажите число дней или расстояние между ними.')
        return self


class Placement(Model):
    id: str
    workloadId: str
    day: int = Field(ge=0, le=6)
    period: int = Field(ge=0, le=11)
    roomId: str | None = None
    locked: bool = False
    classIds: list[str] = Field(default_factory=list)
    sourceCells: list[str] = Field(default_factory=list)
    cycleId: str = 'weekly'


class ImportInfo(Model):
    filename: str = ''
    sourceCells: int = 0
    importedLessons: int = 0
    notes: list[str] = Field(default_factory=list)


class CourseSection(Model):
    groupId: str
    minSize: int = Field(default=0, ge=0, le=5000)
    maxSize: int = Field(default=25, ge=1, le=5000)

    @model_validator(mode='after')
    def bounds(self):
        if self.minSize > self.maxSize:
            raise ValueError('Минимум секции превышает максимум.')
        return self


class Course(Named):
    sections: list[CourseSection] = Field(min_length=1, max_length=100)


class CourseRequest(Model):
    id: str = Field(min_length=1, max_length=100)
    studentId: str
    courseIds: list[str] = Field(min_length=1, max_length=20)
    required: bool = True
    lockedGroupId: str | None = None

    @model_validator(mode='after')
    def unique_choices(self):
        if len(set(self.courseIds)) != len(self.courseIds):
            raise ValueError('Курс не должен повторяться в одной заявке.')
        return self


class State(Model):
    schemaVersion: Literal[1] = 1
    revision: int = 0
    settings: Settings = Field(default_factory=Settings)
    cycles: Cycles = Field(default_factory=Cycles)
    classes: list[SchoolClass] = Field(default_factory=list)
    teachers: list[Teacher] = Field(default_factory=list)
    rooms: list[Room] = Field(default_factory=list)
    campus: Campus = Field(default_factory=Campus)
    subjects: list[Subject] = Field(default_factory=list)
    students: list[Student] = Field(default_factory=list)
    groups: list[Group] = Field(default_factory=list)
    profiles: list[Profile] = Field(default_factory=list)
    workloads: list[Workload] = Field(default_factory=list)
    placements: list[Placement] = Field(default_factory=list)
    events: list[Event] = Field(default_factory=list)
    rules: list[TimetableRule] = Field(default_factory=list)
    courses: list[Course] = Field(default_factory=list)
    courseRequests: list[CourseRequest] = Field(default_factory=list)
    datedLessons: list[DatedLesson] = Field(default_factory=list)
    absences: list[TeacherAbsence] = Field(default_factory=list)
    commitments: list[Commitment] = Field(default_factory=list, max_length=20000)
    scheduleDays: list[ScheduleDay] = Field(default_factory=list, max_length=400)
    importInfo: ImportInfo = Field(default_factory=ImportInfo)

    @model_validator(mode='after')
    def references(self):
        cycle_ids = {p.id for p in self.cycles.patterns}
        for period in self.cycles.periods:
            if period.start < self.settings.yearStart or period.end > self.settings.yearEnd:
                raise ValueError('Учебный период выходит за границы учебного года.')
        for w in self.workloads:
            active = set(w.cycleIds) if w.cycleIds else cycle_ids
            if not active <= cycle_ids or not set(w.cycleHours) <= active or not set(w.cycleLessonLengths) <= active:
                raise ValueError('Нагрузка ссылается на отсутствующую или неактивную неделю.')
            for id in active:
                lengths = w.cycleLessonLengths.get(id, w.lessonLengths)
                if lengths and (any(n not in [1,2,3] for n in lengths) or sum(lengths) != w.cycleHours.get(id, w.hours)):
                    raise ValueError('Длины занятий должны совпадать с часами каждой активной недели.')
        for rule in self.rules:
            if not set(rule.cycleIds) <= cycle_ids or len(set(rule.cycleIds)) != len(rule.cycleIds):
                raise ValueError('Проверьте недели дополнительного правила.')
        maps = {}
        for key in ['classes', 'teachers', 'rooms', 'subjects', 'students', 'groups', 'profiles', 'workloads', 'placements', 'events', 'rules', 'courses', 'courseRequests', 'datedLessons', 'absences', 'commitments', 'scheduleDays']:
            items = getattr(self, key)
            maps[key] = {i.id: i for i in items}
            if len(maps[key]) != len(items):
                raise ValueError(f'Повторяющиеся идентификаторы: {key}')

        def require(key, values):
            if any(v not in maps[key] for v in values if v is not None):
                names = dict(classes='классы', teachers='учителя', rooms='кабинеты', subjects='предметы', students='ученики', groups='группы', profiles='звонки', workloads='нагрузка')
                raise ValueError(f'Отсутствуют связанные данные: {names.get(key, key)}. Сначала измените связанные занятия.')

        for c in self.classes:
            require('profiles', [c.profileId])
        for event in self.events:
            require('classes', event.classIds)
            if len(event.classIds) != len(set(event.classIds)):
                raise ValueError('Классы календарного события не должны повторяться.')
        source_keys = set()
        for change in self.datedLessons:
            require('groups', [change.groupId])
            require('profiles', [change.profileId])
            require('subjects', [change.subjectId])
            require('teachers', change.teacherIds or [])
            require('rooms', change.roomIds or [])
            for day in [change.onDate, change.sourceDate]:
                if day and not self.settings.yearStart <= day <= self.settings.yearEnd:
                    raise ValueError('Дата изменения урока выходит за учебный год.')
            if change.placementId:
                key = change.placementId, change.sourceDate
                if key in source_keys:
                    raise ValueError('Для урока в конкретную дату допускается только одно изменение.')
                source_keys.add(key)
        for absence in self.absences:
            require('teachers', [absence.teacherId])
            if absence.start < self.settings.yearStart or absence.end > self.settings.yearEnd:
                raise ValueError('Отсутствие учителя выходит за учебный год.')
        from .cycles import pattern_on
        for item in self.scheduleDays:
            require('classes', item.classIds)
            if not self.settings.yearStart <= item.onDate <= self.settings.yearEnd:
                raise ValueError('«День в расписании» выходит за учебный год.')
            if item.day >= len(self.settings.days):
                raise ValueError('«День в расписании» ссылается на день вне учебной недели.')
            if item.cycleId is not None and item.cycleId not in cycle_ids:
                raise ValueError('«День в расписании» ссылается на отсутствующую неделю расписания.')
            if item.cycleId is None and pattern_on(self, item.onDate) is None:
                raise ValueError('Дата вне учебных периодов: выберите неделю расписания.')
        if len({item.onDate for item in self.scheduleDays}) != len(self.scheduleDays):
            raise ValueError('Для одной даты допускается один «день в расписании».')
        buildings = {b.id for b in self.campus.buildings}
        for item in self.commitments:
            require('rooms', item.roomIds)
            require('teachers', item.teacherIds + item.eligibleTeacherIds)
            require('groups', item.groupIds)
            require('profiles', [item.profileId])
            if not self.settings.yearStart <= item.onDate <= self.settings.yearEnd:
                raise ValueError('Бронирование или дежурство выходит за учебный год.')
            if item.buildingId and item.buildingId not in buildings:
                raise ValueError('Отсутствует здание места бронирования/дежурства.')
        if any(r.buildingId is not None and r.buildingId not in buildings for r in self.rooms):
            raise ValueError('Кабинет ссылается на отсутствующее здание. Сначала измените кабинет.')
        for rule in self.rules:
            require('subjects', rule.subjectIds + rule.otherSubjectIds)
            require('profiles', [rule.gapProfileId])
            if rule.gapExcludedPeriods and (not rule.gapProfileId or any(p < 0 or p >= len(maps['profiles'][rule.gapProfileId].periods) for p in rule.gapExcludedPeriods)):
                raise ValueError('Недопустимый исключённый период в правиле окон.')
            table = {'class': 'classes', 'teacher': 'teachers', 'student': 'students', 'group': 'groups', 'subject': 'subjects', 'room': 'rooms', 'workload': 'workloads'}[rule.scope]
            require(table, rule.targetIds)
            if any(d < 0 or d >= len(self.settings.days) for d in rule.days) or any(w.day >= len(self.settings.days) for w in rule.unavailable):
                raise ValueError('Правило ссылается на день вне учебной недели.')
        for profile in self.profiles:
            if any(int(day) >= len(self.settings.days) for day in profile.dayPeriods):
                raise ValueError('Отдельные звонки заданы для дня вне учебной недели.')
        for s in self.students:
            require('classes', [s.classId])
        for g in self.groups:
            require('classes', g.classIds)
            require('students', g.studentIds)
            if any(maps['students'][s].classId not in g.classIds for s in g.studentIds):
                raise ValueError(f'Ученик не относится к указанным классам группы {g.name}.')
        section_ids = []
        for course in self.courses:
            require('groups', [s.groupId for s in course.sections])
            for section in course.sections:
                group = maps['groups'][section.groupId]
                if group.kind in ['whole', 'staff']:
                    raise ValueError('Секция курса должна быть учебной группой с поимённым составом.')
                section_ids.append(section.groupId)
        if len(set(section_ids)) != len(section_ids):
            raise ValueError('Учебная группа может быть секцией только одного курса.')
        for request in self.courseRequests:
            require('students', [request.studentId])
            require('courses', request.courseIds)
            if request.lockedGroupId is not None:
                choices = {s.groupId for id in request.courseIds for s in maps['courses'][id].sections}
                if request.lockedGroupId not in choices:
                    raise ValueError('Закреплённая секция должна относиться к одному из выбранных курсов.')
                if maps['students'][request.studentId].classId not in maps['groups'][request.lockedGroupId].classIds:
                    raise ValueError('Закреплённая секция не принимает класс ученика.')
        for w in self.workloads:
            require('groups', [w.groupId])
            require('subjects', [w.subjectId])
            require('teachers', [w.teacherId, *w.additionalTeacherIds])
            require('rooms', [*w.roomIds, *w.requiredRoomIds])
            require('profiles', [w.profileId])
            if any(p < 0 or p >= len(maps['profiles'][w.profileId].periods) for p in w.allowedPeriods):
                raise ValueError('Недопустимый номер урока в нагрузке.')
        for p in self.placements:
            require('workloads', [p.workloadId])
            require('rooms', [p.roomId])
            require('classes', p.classIds)
            w = maps['workloads'][p.workloadId]
            if p.cycleId not in cycle_ids or w.cycleIds and p.cycleId not in w.cycleIds:
                raise ValueError('Занятие относится к отсутствующей или неактивной неделе нагрузки.')
            if not w.roomRequired and p.roomId:
                raise ValueError('У занятия без кабинета не может быть назначен кабинет.')
            if p.day >= len(self.settings.days) or p.period >= len(maps['profiles'][w.profileId].periods):
                raise ValueError('Занятие выходит за границы учебной недели.')
        return self
