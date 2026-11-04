"""The fictional school of the public demo: pure data, no I/O.

Everything here is invented. base_data() is a State document (see
backend/models.py) built by hand; backend.seed is deliberately not used,
because its production path reads data/imported-school.json.
"""
from . import names

DEMO_NOW = '2026-11-02T11:30:00+05:00'  # a Monday, before midday in Asia/Qyzylorda
SCHOOL_NAME = 'Школа Пример (вымышленная)'
YEAR_START = '2026-10-05'
YEAR_END = '2027-05-28'

CLASS_NAMES = ['7А', '7Б', '8А', '8Б']
PUPILS_PER_CLASS = 15
SHOWCASE_CLASS = 'c0'
SHOWCASE_PUPIL = 's0'
# A pupil of 7Б who is the showcase guardian's second child: the child switcher of the family portal has two to show.
# She sorts after the showcase pupil, so the portal opens on the showcase pupil.
SIBLING_PUPIL = 's22'
SHOWCASE_TEACHER = 't0'

SUBJECTS = [
    ('Математика', '#dceae2'), ('Русский язык', '#e4e0f3'), ('Английский язык', '#f6e5ce'),
    ('История', '#dce9f4'), ('Физика', '#f3dede'), ('Биология', '#e0f3e6'),
    ('География', '#f3f0d8'), ('Информатика', '#e6e6e6'), ('Казахский язык', '#f0e1ec'), ('Физкультура', '#e1eef5'),
]
HOURS = [5, 4, 3, 2, 3, 2, 2, 2, 3, 2]  # lessons per week, in SUBJECTS order
TEACHER_COUNT = 14
# Teacher index for each subject in the 7th and the 8th grade (Kazakh and physical education are shared).
TEACHER_OF = {
    0: [0, 1, 2, 3, 4, 5, 6, 7, 12, 13],
    1: [8, 9, 10, 11, 4, 5, 6, 7, 12, 13],
}
CLASS_TEACHERS = {0: 't0', 1: 't1', 2: 't8', 3: 't9'}
ROOMS = [('Кабинет 101', 28), ('Кабинет 102', 28), ('Кабинет 103', 28), ('Кабинет 104', 28),
         ('Кабинет 201', 24), ('Кабинет 202', 24), ('Кабинет 203', 24), ('Кабинет 204', 24),
         ('Кабинет информатики', 16), ('Кабинет физики', 20), ('Спортивный зал', 40)]
SPECIAL_ROOM = {7: 'r8', 4: 'r9', 9: 'r10'}  # Информатика, Физика, Физкультура


assert len(names.TEACHERS) == TEACHER_COUNT


def pupil_id(index):
    return f's{index}'


def class_pupils(class_index):
    first = class_index * PUPILS_PER_CLASS
    return [pupil_id(i) for i in range(first, first + PUPILS_PER_CLASS)]


def logins():
    """Account logins by role; passwords are generated at seed time."""
    return {
        'admin': 'demo-admin',
        'teachers': [f'demo-teacher-{i + 1:02d}' for i in range(TEACHER_COUNT)],
        'parents': [f'demo-parent-{i + 1:02d}' for i in range(PUPILS_PER_CLASS)],
        'pupils': [f'demo-pupil-{i + 1:02d}' for i in range(PUPILS_PER_CLASS)],
    }


def course_teacher(class_index, subject_name):
    """The teacher the timetable gives a class for a subject (a substitute is not the teacher of the course)."""
    subject_index = [name for name, _ in SUBJECTS].index(subject_name)
    return f't{TEACHER_OF[class_index // 2][subject_index]}'


def workload_id(class_index, subject_index):
    return f'w{class_index}-{subject_index}'


def _placements(classes, workloads):
    """A deterministic greedy timetable: spread over the week, no overlaps."""
    busy = {}
    per_day = {}
    placements = []
    subject_order = sorted(range(len(SUBJECTS)), key=lambda i: -HOURS[i])
    for subject_index in subject_order:
        for class_index, school_class in enumerate(classes):
            workload = next(w for w in workloads if w['id'] == workload_id(class_index, subject_index))
            used_days = []
            keys = [('c', school_class['id']), ('t', workload['teacherId'])] + [('r', r) for r in workload['roomIds']]
            for lesson in range(workload['hours']):
                options = []
                for day in range(5):
                    if day in used_days:
                        continue
                    start = per_day.get((school_class['id'], day), 0)
                    free = [p for p in range(start, 7) if not any((kind, ident, day, p) in busy for kind, ident in keys)]
                    if free:
                        # No window in the class's day first, then the lightest day, then a rotation that varies the week.
                        options.append((free[0] - start, start, (day - subject_index - class_index) % 5, day, free[0]))
                if not options:
                    raise RuntimeError(f'Не удалось поставить урок {workload["id"]}')
                *_, day, period = min(options)
                for kind, ident in keys:
                    busy[(kind, ident, day, period)] = True
                per_day[(school_class['id'], day)] = period + 1
                used_days.append(day)
                placements.append(dict(id=f'{workload["id"]}-{lesson}', workloadId=workload['id'], day=day,
                                       period=period, roomId=workload['roomIds'][0]))
    return placements


def base_data():
    periods = [dict(label=str(i + 1), start=f'{8 + i:02d}:30', end=f'{9 + i:02d}:15') for i in range(7)]
    classes = [dict(id=f'c{i}', name=name, maxDaily=7, profileId='main', studentCount=PUPILS_PER_CLASS,
                    classTeacherId=CLASS_TEACHERS[i]) for i, name in enumerate(CLASS_NAMES)]
    subjects = [dict(id=f'b{i}', name=name, color=color) for i, (name, color) in enumerate(SUBJECTS)]
    teachers = [dict(id=f't{i}', name=name) for i, name in enumerate(names.TEACHERS)]
    rooms = [dict(id=f'r{i}', name=name, capacity=capacity) for i, (name, capacity) in enumerate(ROOMS)]
    students = []
    for class_index in range(len(classes)):
        for i, pid in enumerate(class_pupils(class_index)):
            students.append(dict(id=pid, name=names.PUPILS[class_index * PUPILS_PER_CLASS + i], classId=f'c{class_index}'))
    groups = [dict(id='g' + c['id'], name=c['name'], classIds=[c['id']], kind='whole') for c in classes]
    workloads = []
    for class_index, school_class in enumerate(classes):
        parallel = class_index // 2
        for subject_index, hours in enumerate(HOURS):
            teacher = f't{TEACHER_OF[parallel][subject_index]}'
            room = SPECIAL_ROOM.get(subject_index, f'r{class_index}')
            workloads.append(dict(id=workload_id(class_index, subject_index), groupId='g' + school_class['id'],
                                  subjectId=f'b{subject_index}', teacherId=teacher, hours=hours, profileId='main',
                                  roomIds=[room], maxPerDay=2, studentCount=PUPILS_PER_CLASS))
    return dict(
        settings=dict(name=SCHOOL_NAME, yearStart=YEAR_START, yearEnd=YEAR_END),
        classes=classes, profiles=[dict(id='main', name='Основное', periods=periods)], groups=groups,
        students=students, teachers=teachers, subjects=subjects, rooms=rooms, workloads=workloads,
        placements=_placements(classes, workloads),
        events=[dict(id='winter', name='Зимние каникулы', start='2026-12-26', end='2027-01-10', kind='winter')],
    )
