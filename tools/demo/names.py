"""Invented people for the public demo school.

Every surname is made up and obviously not a real family name; first names are
ordinary ones. No e-mail addresses and no phone numbers exist anywhere in the
demo. The lists are deterministic: the same seed gives the same school.
"""
import random

SEED = 20261102

# (masculine, feminine)
SURNAME_PAIRS = [
    ('Примеров', 'Примерова'), ('Образцов', 'Образцова'), ('Вымыслов', 'Вымыслова'),
    ('Условнов', 'Условнова'), ('Макетов', 'Макетова'), ('Черновиков', 'Черновикова'),
    ('Шаблонов', 'Шаблонова'), ('Эскизов', 'Эскизова'), ('Пробников', 'Пробникова'),
    ('Заготовкин', 'Заготовкина'), ('Придумов', 'Придумова'), ('Сочинов', 'Сочинова'),
    ('Наброскин', 'Наброскина'), ('Вариантов', 'Вариантова'), ('Выдумкин', 'Выдумкина'),
    ('Сказов', 'Сказова'),
]
SURNAMES = frozenset(name for pair in SURNAME_PAIRS for name in pair)

MALE = ['Александр', 'Дмитрий', 'Максим', 'Артём', 'Данияр', 'Тимур', 'Ильяс', 'Руслан', 'Арман',
        'Даниил', 'Кирилл', 'Матвей', 'Егор', 'Никита', 'Иван', 'Айдар', 'Нурлан', 'Адиль', 'Ерлан', 'Марат']
FEMALE = ['Анна', 'Мария', 'София', 'Алина', 'Дарья', 'Аружан', 'Айгерим', 'Амина', 'Диана', 'Елизавета',
          'Полина', 'Виктория', 'Милана', 'Камила', 'Алия', 'Малика', 'Ксения', 'Варвара', 'Ева', 'Дана']


def surname_of(full_name):
    return full_name.split()[0]


class _Factory:
    def __init__(self):
        self.random = random.Random(SEED)
        self.used = set()

    def person(self, female=None):
        while True:
            is_female = self.random.random() < .5 if female is None else female
            pair = self.random.choice(SURNAME_PAIRS)
            first = self.random.choice(FEMALE if is_female else MALE)
            name = f'{pair[1] if is_female else pair[0]} {first}'
            if name not in self.used:
                self.used.add(name)
                return name


def _build():
    factory = _Factory()
    factory.used.add('Эскизова Ирина')
    teachers = [factory.person(female=i % 3 != 2) for i in range(14)]
    pupils = [factory.person(female=i % 2 == 0) for i in range(60)]
    guardians = []
    for pupil in pupils[:15]:
        pair = next(p for p in SURNAME_PAIRS if surname_of(pupil) in p)
        mother = factory.random.random() < .6
        first = factory.random.choice(FEMALE if mother else MALE)
        guardians.append(f'{pair[1] if mother else pair[0]} {first}')
    admin = 'Эскизова Ирина'
    return admin, teachers, pupils, guardians


ADMIN_NAME, TEACHERS, PUPILS, GUARDIANS = _build()
# The guardian of two children (7А and 7Б) for the local demo; a name made by hand, like the administrator's.
FAMILY_GUARDIAN = 'Примерова Светлана'
