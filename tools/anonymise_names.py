"""Invented people for the pseudonymised copy of a school.

Surnames are made from ordinary words (a draft, a template, a sketch), so no
one mistakes them for family names of the school; first names are ordinary.
The pool is large (a few thousand full names for each gender) so that two
people of one school never share a pseudonym.
"""
import secrets

ROOTS = '''Эскиз Макет Шаблон Черновик Набросок Вариант Выдумка Сказ Заготовка Придумка Сочинка Проект Образец Пример Прототип
Лекало Выкройка Трафарет Оттиск Слепок Силуэт Контур Штрих Пунктир Орнамент Мотив Напев Рифма Строфа Куплет Припев Аккорд Тембр Такт
Темп Ритм Реплика Диалог Монолог Сценка Этюд Набор Заметка Конспект Тезис Абзац Параграф Страница Фрагмент Отрывок Цитата Пометка
Закладка Черта Рисунок Чертёж Замысел Задумка Догадка Версия Гипотеза Допуск Прикидка Расчёт Подсчёт Замер Отмер Разметка Каркас
Остов Макетка Кроква Опалубка Стропила Рейка Планка Брусок Колышек Вешка Веха Створ Буёк Бакен Якорь Швартов Кнехт Румпель
Киль Шпангоут Транец Фальшборт Планшир Ватерлиния Ростр Бушприт Утлегарь Гафель Спинакер Кливер Стаксель Грот Фок Бизань
Брамсель Трисель Лисель Бейдевинд Галфвинд Фордевинд Крамбол'''.split()

MALE = '''Александр Дмитрий Максим Артём Данияр Тимур Ильяс Руслан Арман Даниил Кирилл Матвей Егор Никита Иван Айдар Нурлан Адиль Ерлан Марат
Андрей Сергей Павел Роман Владимир Георгий Михаил Антон Олег Денис Станислав Леонид Артур Тигран Рустам Бекзат Алихан Санжар Азамат
Болат Мирас Ерасыл Темирлан Тимофей Фёдор Лев Глеб Семён Игорь Виктор Эмиль Ильдар Камиль Радик Булат Заур Мансур Салават Расул'''.split()

FEMALE = '''Анна Мария София Алина Дарья Аружан Айгерим Амина Диана Елизавета Полина Виктория Милана Камила Алия Малика Ксения Варвара Ева Дана
Екатерина Ольга Наталья Татьяна Юлия Светлана Ирина Елена Валерия Арина Вероника Злата Кира Лилия Маргарита Надежда Оксана Регина
Сабина Томирис Асель Жанна Мадина Динара Айша Гульнара Зарина Карина Лаура Мерей Айжан Назира Рената Эльвира Лейла Самира Фатима'''.split()


def surname(root):
    """The masculine surname of a root; roots end in a plain consonant, -а, -я or -й."""
    if root[-1] in 'ая':
        return root[:-1] + 'ин'
    if root[-1] in 'йь':
        return root[:-1] + 'ев'
    return root + 'ов'


def feminine(masculine):
    return masculine + 'а'


SURNAMES = sorted({surname(r) for r in ROOTS})
POOL_SIZE = len(SURNAMES) * (len(MALE) + len(FEMALE))


def is_female(name):
    """A guess from the endings of the words, used only to give the invented name the same grammatical gender."""
    for word in name.split():
        low = word.casefold()
        if low.endswith(('ова', 'ева', 'ёва', 'ина', 'ына', 'ская', 'цкая', 'ая')):
            return True
        if low.endswith(('ов', 'ев', 'ёв', 'ин', 'ын', 'ский', 'цкий', 'ой', 'ий', 'ич')):
            return False
    return secrets.randbelow(2) == 0


def invent(female):
    """A random full name in the form `Surname First`."""
    surname_ = SURNAMES[secrets.randbelow(len(SURNAMES))]
    first = (FEMALE if female else MALE)
    return f'{feminine(surname_) if female else surname_} {first[secrets.randbelow(len(first))]}'
