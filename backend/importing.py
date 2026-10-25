"""Column mapping, exact identity resolution, and atomic import previews."""
import base64
from collections import defaultdict
from datetime import date
import json
import threading
from time import monotonic
from typing import Literal, get_args, get_origin
from uuid import uuid4, uuid5, NAMESPACE_URL

from pydantic import Field, ValidationError
from .models import Model, State
from .exporting import TABLES
from .tabular import parse_file


LABELS = dict(id='ID записи', name='Имя / название', classId='Класс', classIds='Классы группы', studentId='Ученик', studentIds='Состав группы', externalId='Внешний ID ученика', active='Действующий ученик',
              maxDaily='Ориентир уроков в день', profileId='Профиль звонков', studentCount='Численность учеников', capacity='Вместимость', buildingId='Здание', color='Цвет', unavailable='Недоступность',
              kind='Тип', rosterStatus='Полнота состава', rosterSources='Источники состава', rosterNote='Примечание к составу', periods='Периоды звонков', dayPeriods='Звонки по дням',
              groupId='Учебная группа', subjectId='Предмет', teacherId='Учитель', additionalTeacherIds='Совместные преподаватели', teacherRequired='Нужен учитель', hours='Недельные часы', maxPerDay='Максимум за день',
              roomIds='Варианты кабинета', requiredRoomIds='Постоянные кабинеты', roomRequired='Нужен кабинет', block='Синхронный блок', allowedPeriods='Допустимые уроки', lessonLengths='Длины занятий', maxLessonBreak='Перемена внутри занятия',
              cycleIds='Активные недели', cycleHours='Часы по неделям', cycleLessonLengths='Длины по неделям', workloadId='Нагрузка', day='День недели', period='Номер урока', roomId='Кабинет', locked='Закрепление', sourceCells='Исходные ячейки', cycleId='Неделя',
              start='Начало', end='Конец', grades='Классы календарного события', scope='Область правила', targetIds='Записи правила', days='Дни правила', enabled='Правило включено', mode='Обязательность правила', weight='Вес пожелания',
              sections='Секции курса', courseIds='Курсы по приоритету', required='Обязательная заявка', lockedGroupId='Закреплённая секция', minLessons='Минимум уроков', maxLessons='Максимум уроков', includeEmptyDays='Проверять пустые дни',
              earliestStart='Не раньше', latestEnd='Не позже', maxIdleMinutes='Свободных минут за день', maxIdlePeriods='Пустых периодов за день', maxIdleBlocks='Окон за день', maxWeeklyIdlePeriods='Пустых периодов за неделю', gapProfileId='Звонки для окон', gapExcludedPeriods='Исключённые периоды',
              maxConsecutive='Уроков подряд', shortBreakMinutes='Короткая перемена', lunchStart='Начало окна обеда', lunchEnd='Конец окна обеда', lunchMinutes='Длительность обеда', minDays='Минимум учебных дней', maxDays='Максимум учебных дней', minDayDistance='Расстояние между днями',
              relation='Связь предметов', subjectIds='Первый набор предметов', otherSubjectIds='Второй набор предметов', relationGapMinutes='Перемена для следования',
              effect='Влияние на уроки', onDate='Дата занятия', sourceDate='Исходная дата', placementId='Исходное занятие', teacherIds='Учителя занятия', startTime='Время начала', endTime='Время окончания',
              groupIds='Группы участников', location='Место', requiredTeachers='Требуется дежурных', eligibleTeacherIds='Кандидаты на дежурство', note='Примечание',
              changeType='Тип замены', mergeWith='Объединить с уроком')
ALIASES = dict(name=['ФИО', 'Ф.И.О.', 'Название', 'Имя'], classId=['Класс ученика', 'Class'], teacherId=['Преподаватель', 'Teacher'], subjectId=['Subject'], groupId=['Группа', 'Group'], hours=['Часы', 'Hours'], roomId=['Аудитория', 'Room'], period=['Урок'], externalId=['Код ученика'])
REFS = dict(classId='classes', classIds='classes', studentId='students', studentIds='students', teacherId='teachers', additionalTeacherIds='teachers', subjectId='subjects', subjectIds='subjects', otherSubjectIds='subjects', groupId='groups', lockedGroupId='groups', roomId='rooms', roomIds='rooms', requiredRoomIds='rooms', profileId='profiles', gapProfileId='profiles', workloadId='workloads', courseIds='courses', cycleId='cycles', cycleIds='cycles', buildingId='buildings')
REFS.update(teacherIds='teachers', placementId='placements', eligibleTeacherIds='teachers', groupIds='groups')
COMMON = dict(classes='id name profileId maxDaily studentCount', teachers='id name unavailable', rooms='id name capacity buildingId', subjects='id name color', students='id name classId externalId active', groups='id name kind classIds studentIds rosterStatus rosterNote',
              profiles='id name periods dayPeriods', workloads='id groupId subjectId teacherId hours profileId roomIds maxPerDay', placements='id workloadId day period roomId cycleId locked', events='id name start end kind grades',
              rules='id name kind scope targetIds mode weight unavailable minLessons maxLessons', courses='id name sections', courseRequests='id studentId courseIds required lockedGroupId',
              datedLessons='id name kind onDate sourceDate placementId groupId profileId subjectId startTime endTime teacherIds roomIds changeType mergeWith', absences='id name teacherId start end startTime endTime',
              commitments='id name kind onDate startTime endTime roomIds teacherIds groupIds profileId location requiredTeachers eligibleTeacherIds locked', scheduleDays='id name onDate day cycleId classIds')


def model_for(table):
    if table not in TABLES: raise ValueError('Неизвестная таблица назначения.')
    return get_args(State.model_fields[table].annotation)[0]


def base_type(annotation):
    args = get_args(annotation)
    if type(None) in args:
        return next(a for a in args if a is not type(None))
    return annotation


def field_meta(key, field):
    annotation = base_type(field.annotation)
    origin, args = get_origin(annotation), get_args(annotation)
    kind = 'list' if origin is list else 'object' if origin is dict else 'boolean' if annotation is bool else 'number' if annotation is int else 'date' if annotation is date else 'text'
    choices = list(args) if origin is Literal else []
    hint = 'Дата ГГГГ-ММ-ДД.' if kind == 'date' else 'Да/нет, true/false или 1/0.' if kind == 'boolean' else ''
    if kind == 'list':
        item = args[0]
        hint = 'Список JSON объектов.' if isinstance(item, type) and issubclass(item, Model) else 'Список JSON либо значения через выбранный разделитель.'
    elif kind == 'object': hint = 'Объект JSON.'
    if key in ['workloadId', 'placementId']: hint += ' Ссылка: внутренний ID записи.'
    elif key in REFS or key == 'targetIds': hint += ' Ссылки: точный ID или однозначное имя; во вложенном JSON нужны ID.'
    if key in ['day', 'days']: hint += ' День по названию или номеру: 0 — первый день недели.'
    if key in ['period', 'allowedPeriods', 'gapExcludedPeriods']: hint += ' Номера периодов начинаются с 0.'
    return dict(key=key, label=LABELS.get(key, key), required=field.is_required() and key != 'id', kind=kind, choices=choices, hint=hint.strip(), aliases=ALIASES.get(key, []))


def metadata():
    return [dict(key=table, label=label, canMatchName='name' in model_for(table).model_fields, canMatchExternal=table=='students', common=COMMON[table].split(), fields=[field_meta(k,f) for k,f in model_for(table).model_fields.items()]) for table,label in TABLES.items()]


def normalize(value): return str(value).strip().casefold()


class Resolver:
    def __init__(self, state):
        self.indices = {}
        for table in TABLES.keys() | {'cycles', 'buildings'}:
            rows = state.cycles.patterns if table == 'cycles' else state.campus.buildings if table == 'buildings' else getattr(state, table)
            by_id, by_name = {}, defaultdict(list)
            for row in rows:
                by_id[row.id] = row
                if hasattr(row, 'name'): by_name[normalize(row.name)].append(row)
            self.indices[table] = by_id, by_name

    def get(self, table, value):
        value = str(value).strip()
        ids, names = self.indices[table]
        if value in ids: return value
        matches = names[normalize(value)]
        if len(matches) == 1: return matches[0].id
        if len(matches) > 1:
            raise ValueError(f'Неоднозначное имя «{value[:100]}»: укажите ID ({", ".join(r.id for r in matches[:6])}).')
        raise ValueError(f'Не найдено в «{TABLES.get(table, table)}»: {value[:100]}. Сначала импортируйте эту запись или исправьте ссылку.')


def convert(field, key, value, resolver, state, separator, row):
    value = str(value).strip()
    annotation = base_type(field.annotation)
    args, origin = get_args(annotation), get_origin(annotation)
    optional = type(None) in get_args(field.annotation)
    if not value or value == 'null' and optional:
        if optional: return None
        if not field.is_required(): return field.get_default(call_default_factory=True)
        raise ValueError('Поле не заполнено.')
    if origin in [list, dict]:
        if value.startswith('[') or value.startswith('{'):
            result = json.loads(value)
            if not isinstance(result, list if origin is list else dict): raise ValueError('Неверный тип JSON.')
        elif origin is list and args[0] in [str, int]:
            result = [x.strip() for x in value.split(separator) if x.strip()]
        else: raise ValueError('Введите корректный JSON.')
        if origin is list and args[0] in [str, int]:
            if key == 'days':
                day_names = {normalize(x): i for i,x in enumerate(state.settings.days)}
                result = [day_names.get(normalize(v), v) for v in result]
            if args[0] is int:
                if any(isinstance(x, bool) or str(x).strip().lstrip('-').isdigit() is False for x in result): raise ValueError('В списке нужны целые числа.')
                result = [int(x) for x in result]
            ref = REFS.get(key)
            if key == 'targetIds': ref = dict(student='students', **{'class':'classes'}, teacher='teachers', group='groups', subject='subjects', room='rooms', workload='workloads').get(row.get('scope'))
            if ref: result = [resolver.get(ref, x) for x in result]
        return result
    if annotation is bool:
        val = normalize(value)
        if val in ['true','да','yes','1']: return True
        if val in ['false','нет','no','0']: return False
        raise ValueError('Ожидается да/нет, true/false или 1/0.')
    if annotation is int:
        if key == 'day':
            days = {normalize(x): i for i,x in enumerate(state.settings.days)}
            if normalize(value) in days: return days[normalize(value)]
        if not value.lstrip('-').isdigit(): raise ValueError('Ожидается целое число.')
        return int(value)
    if key in REFS: return resolver.get(REFS[key], value)
    return value


class UploadRequest(Model):
    filename: str = Field(min_length=1, max_length=250)
    content: str = Field(max_length=14_000_000)
    delimiter: Literal['auto', ';', ',', '\t', '|'] = 'auto'
    encoding: Literal['auto', 'utf-8-sig', 'utf-16', 'cp1251'] = 'auto'


class SheetRequest(Model):
    token: str
    sheet: int = Field(default=0, ge=0)
    headerRow: int = Field(default=1, ge=0, le=100)


class MappingRequest(SheetRequest):
    revision: int
    table: str
    mapping: dict[str, int] = Field(default_factory=dict)
    constants: dict[str, str] = Field(default_factory=dict)
    mode: Literal['add', 'update', 'upsert'] = 'upsert'
    match: Literal['id', 'name', 'externalId'] = 'id'
    skipBlanks: bool = True
    listSeparator: Literal[';', ',', '|', '\n'] = ';'


def selected_sheet(package, index, header_row):
    if index >= len(package['sheets']): raise ValueError('Лист не найден.')
    sheet = package['sheets'][index]
    if header_row > len(sheet['rows']): raise ValueError('Строка заголовков выходит за границы листа.')
    return sheet


def inspect_sheet(package, index=0, header_row=1):
    sheet = selected_sheet(package,index,header_row)
    cols = max(map(len, sheet['rows']), default=0)
    heading = sheet['rows'][header_row-1] if header_row else []
    rows = [dict(number=i+1, cells=r) for i,r in enumerate(sheet['rows']) if i >= header_row and any(r)]
    return dict(sheet=index, headerRow=header_row, headers=[dict(index=i, label=heading[i] if i < len(heading) and heading[i] else f'Колонка {i+1}') for i in range(cols)], rows=rows[:12], rowCount=len(rows), warnings=package['warnings'])


def mapped_state(state, package, body):
    model = model_for(body.table)
    fields = model.model_fields
    if not body.mapping and not body.constants: raise ValueError('Сопоставьте хотя бы одно поле.')
    if (body.mapping.keys() | body.constants.keys()) - fields.keys() or body.mapping.keys() & body.constants.keys():
        raise ValueError('Есть неизвестные или дважды сопоставленные поля.')
    sheet = selected_sheet(package,body.sheet,body.headerRow)
    columns = max(map(len,sheet['rows']),default=0)
    if any(i < 0 or i >= columns for i in body.mapping.values()): raise ValueError('Колонка не найдена.')
    if body.mode != 'add' and body.match not in body.mapping and body.match not in body.constants:
        raise ValueError('Сопоставьте поле для поиска существующей записи.')
    if body.match == 'name' and 'name' not in fields: raise ValueError('Для этой таблицы поиск возможен только по ID.')
    if body.match == 'externalId' and body.table != 'students': raise ValueError('Внешний ID поддерживается только для учеников.')
    resolver = Resolver(state)
    old = {r.id: r for r in getattr(state, body.table)}
    names = defaultdict(list)
    for obj in old.values():
        if hasattr(obj, 'name'): names[normalize(obj.name)].append(obj)
    formulas = set(map(tuple, sheet.get('formulas', [])))
    replacements, seen, errors = {}, set(), []
    counts = dict(rows=0, added=0, changed=0, unchanged=0)
    for index, cells in enumerate(sheet['rows']):
        if index < body.headerRow or not any(cells): continue
        counts['rows'] += 1
        key = ''
        try:
            raw = {key: cells[column] if column < len(cells) else '' for key,column in body.mapping.items()}
            raw.update(body.constants)
            used_formula = next((k for k,c in body.mapping.items() if (index,c) in formulas), None)
            if used_formula: key=used_formula; raise ValueError('В сопоставленной ячейке формула. Сохраните её как значение.')
            patch = {}
            # Target scope determines how targetIds are resolved.
            for key in sorted(raw, key=lambda k: (k != 'scope', k)):
                if key == 'id' and not raw[key].strip(): continue
                if body.skipBlanks and not raw[key].strip(): continue
                patch[key] = convert(fields[key],key,raw[key],resolver,state,body.listSeparator,{**raw,**patch})
            existing = None
            if body.mode != 'add':
                key = body.match
                needle = patch.get(body.match)
                if not needle: raise ValueError('Поле для поиска существующей записи не заполнено.')
                if body.match == 'id': existing = old.get(needle)
                else:
                    matches = [m for m in old.values() if m.externalId == needle] if body.match == 'externalId' else names[normalize(needle)]
                    if body.table == 'students' and body.match == 'name':
                        if not patch.get('classId'): raise ValueError('При поиске ученика по имени укажите также класс.')
                        matches = [m for m in matches if m.classId == patch['classId']]
                    if len(matches) > 1: raise ValueError('Совпадение неоднозначно; используйте поиск по ID: ' + ', '.join(m.id for m in matches[:6]))
                    existing = matches[0] if matches else None
                    if existing and patch.get('id',existing.id) != existing.id: raise ValueError('ID в файле отличается от найденной записи. Используйте поиск по ID.')
                if body.mode == 'update' and existing is None: raise ValueError('Запись для обновления не найдена.')
            id = existing.id if existing else patch.get('id') or 'import-' + str(uuid5(NAMESPACE_URL, f'{body.token}:{body.table}:{body.sheet}:{index}'))
            if not existing and id in old: key='id'; raise ValueError('Такой ID уже существует. Выберите обновление или другой ID.')
            if id in seen: key='id'; raise ValueError('Несколько строк файла изменяют одну запись. Объедините их или исправьте ID.')
            if not existing and body.match in ['name','externalId'] and body.mode != 'add':
                match_key = (normalize(patch.get(body.match,'')) if body.match=='name' else patch.get(body.match,''), patch.get('classId') if body.table == 'students' and body.match=='name' else '')
                if match_key in seen: key='name'; raise ValueError('Повторяющееся имя новой записи в файле.')
                seen.add(match_key)
            seen.add(id)
            data = existing.model_dump(mode='json') if existing else {}
            data.update(patch); data['id'] = id
            # For a rule update, an unmapped scope must come from the old rule.
            if body.table == 'rules' and 'targetIds' in raw and (raw['targetIds'].strip() or not body.skipBlanks):
                key='targetIds'; data[key] = convert(fields[key],key,raw[key],resolver,state,body.listSeparator,{**data})
            obj = model.model_validate(data)
            replacements[id] = obj
            counts['unchanged' if obj == existing else 'changed' if existing else 'added'] += 1
        except (ValueError, TypeError) as exc:
            if isinstance(exc, ValidationError):
                first = exc.errors()[0]
                key = '.'.join(map(str, first['loc']))
                message = 'Поле не заполнено.' if first['type'] == 'missing' else first['msg'].removeprefix('Value error, ')
            else: message = str(exc)
            errors.append(dict(row=index+1, field=LABELS.get(key,key), message=message))
    if not counts['rows']: raise ValueError('После строки заголовков нет данных.')
    if errors: return dict(status='errors', errors=errors[:100], errorCount=len(errors), counts=counts)
    candidate = state.model_dump(mode='json')
    candidate[body.table] = [(replacements.get(obj.id,obj)).model_dump(mode='json') for obj in getattr(state,body.table)] + [obj.model_dump(mode='json') for id,obj in replacements.items() if id not in old]
    try: candidate = State.model_validate(candidate)
    except ValidationError as exc:
        return dict(status='errors', errors=[dict(row=None,field='Связи базы',message=e['msg'].removeprefix('Value error, ')) for e in exc.errors()[:100]], errorCount=len(exc.errors()), counts=counts)
    return dict(status='success', candidate=candidate, counts=counts, errors=[], errorCount=0)


def register_imports(app, Workspace, require_revision, create_preview):
    from fastapi import HTTPException
    from openpyxl.utils.exceptions import InvalidFileException
    from xlrd.biffh import XLRDError
    cache, lock = {}, threading.Lock()

    def get(token, workspace):
        with lock:
            entry = cache.get((workspace,token))
            if not entry or monotonic()-entry['created'] > 1800:
                raise HTTPException(404,'Загруженный файл устарел или сервер перезапущен. Выберите файл заново.')
            return entry['package']

    @app.get('/api/exchange/import-schema')
    def schema(): return metadata()

    @app.post('/api/exchange/inspect')
    def inspect(body: UploadRequest, workspace: Workspace = 'school'):
        try:
            raw = base64.b64decode(body.content, validate=True)
            package = parse_file(body.filename, raw, body.delimiter, body.encoding)
            token = str(uuid4())
            with lock:
                for key in list(cache):
                    if monotonic()-cache[key]['created'] > 1800: del cache[key]
                own = [k for k in cache if k[0] == workspace]
                for key in own[:-2]: del cache[key]
                cache[workspace,token] = dict(created=monotonic(),package=package)
            return dict(token=token, filename=body.filename, sheets=[dict(index=i,name=s['name'],rows=len(s['rows']),columns=max(map(len,s['rows']),default=0)) for i,s in enumerate(package['sheets'])], **inspect_sheet(package))
        except (ValueError, InvalidFileException, XLRDError) as exc: raise HTTPException(422,str(exc))

    @app.post('/api/exchange/sheet')
    def sheet(body: SheetRequest, workspace: Workspace = 'school'):
        try: return inspect_sheet(get(body.token,workspace),body.sheet,body.headerRow)
        except ValueError as exc: raise HTTPException(422,str(exc))

    @app.post('/api/exchange/import-preview')
    def preview(body: MappingRequest, workspace: Workspace = 'school'):
        state = require_revision(workspace,body.revision)
        try: result = mapped_state(state,get(body.token,workspace),body)
        except ValueError as exc: raise HTTPException(422,str(exc))
        if result['status'] == 'success':
            result['preview'] = create_preview(workspace,state,result.pop('candidate'),allow_issues=body.table == 'rules')
        return result
