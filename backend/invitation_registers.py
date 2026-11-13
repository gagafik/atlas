"""Parent register import: an export of the previous school system becomes one reviewed batch.

The file is read with the same bounded reader as the exchange import. A register is stored as a
single batch (kind 'register'), so history, receipts and revocation work as for a hand-made batch.
Nothing about a parent is guessed: a child is matched to an active pupil by class and name only.
"""
import base64
import binascii
from collections import defaultdict
from datetime import timedelta
import json
import re
import secrets
from types import SimpleNamespace
from typing import Literal

from fastapi import HTTPException, Request
from pydantic import Field, model_validator

from .access import digest, now
from .invitation_batches import encoded, grant, receipt, review, tables as batch_tables
from .models import Model
from .tabular import parse_file

MAX_ROWS = 3000
MAX_CHILDREN = 50
PAGE = 100
EMPTY = {'', '-', '–', '—', '−'}
LATIN = {ord(a): b for a, b in zip('авсенкмортх', 'abcehkmoptx')}
HEADERS = dict(
    surname={'фамилия', 'фамилия родителя'},
    name={'имя', 'имя родителя'},
    full={'фио', 'ф.и.о.', 'фио родителя', 'родитель', 'законный представитель'},
    children={'дети', 'ребенок', 'дети (класс)', 'ученики', 'ученик', 'дети родителя'},
)


class Columns(Model):
    surname: int | None = Field(default=None, ge=0, lt=200)
    name: int | None = Field(default=None, ge=0, lt=200)
    children: int | None = Field(default=None, ge=0, lt=200)


class Source(Model):
    workspace: Literal['school', 'demo']
    filename: str = Field(min_length=1, max_length=250)
    content: str = Field(min_length=1, max_length=14_000_000)
    delimiter: Literal['auto', ';', ',', '\t', '|'] = 'auto'
    encoding: Literal['auto', 'utf-8-sig', 'utf-16', 'cp1251'] = 'auto'
    sheet: int = Field(default=0, ge=0, le=100)
    headerRow: int | None = Field(default=None, ge=1, le=100)
    columns: Columns | None = None
    days: int = Field(default=7, ge=1, le=30)

    @model_validator(mode='after')
    def distinct(self):
        chosen = [c for c in (self.columns.surname, self.columns.name, self.columns.children) if c is not None] if self.columns else []
        if len(set(chosen)) != len(chosen): raise ValueError('Фамилия, имя и дети должны быть в разных колонках.')
        return self


class Preview(Source):
    offset: int = Field(default=0, ge=0, le=MAX_ROWS)


class Issue(Source):
    requestId: str = Field(pattern=r'^[a-zA-Z0-9_-]{16,80}$')
    previewToken: str = Field(pattern=r'^[a-f0-9]{64}$')
    includePartial: bool = False
    includeCovered: bool = False


def text_key(value):
    return ' '.join(str(value).replace('ё', 'е').replace('Ё', 'Е').split()).casefold()


def name_key(value): return tuple(sorted(text_key(value).split()))


def class_key(value): return ''.join(str(value).split()).casefold().translate(LATIN)


def header_columns(cells):
    """Default column choices from a header row: Фамилия, Имя (or ФИО), Дети."""
    found = {}
    for index, cell in enumerate(cells):
        label = text_key(cell)
        for kind, names in HEADERS.items():
            if label in names and kind not in found: found[kind] = index
    surname = found.get('surname', found.get('full'))
    return dict(surname=surname, name=found.get('name') if 'surname' in found or 'full' not in found else None, children=found.get('children'))


def read(source):
    try: raw = base64.b64decode(source.content, validate=True)
    except (binascii.Error, ValueError): raise HTTPException(422, 'Файл повреждён при передаче. Выберите его заново.') from None
    from openpyxl.utils.exceptions import InvalidFileException
    from xlrd.biffh import XLRDError
    try: package = parse_file(source.filename, raw, source.delimiter, source.encoding)
    except (ValueError, InvalidFileException, XLRDError) as exc: raise HTTPException(422, str(exc)) from None
    sheets = package['sheets']
    if source.sheet >= len(sheets): raise HTTPException(422, 'Лист не найден.')
    rows = sheets[source.sheet]['rows']
    header = source.headerRow
    if header is None:
        header = next((i for i, row in enumerate(rows[:10], 1) if header_columns(row)['children'] is not None), 1)
    if header > len(rows): raise HTTPException(422, 'Строка заголовков выходит за границы листа.')
    width = max(map(len, rows), default=0)
    cells = rows[header - 1] + [''] * (width - len(rows[header - 1]))
    data = [(i, row + [''] * (width - len(row))) for i, row in enumerate(rows[header:], header + 1) if any(c.strip() for c in row)]
    if len(data) > MAX_ROWS:
        raise HTTPException(422, f'В реестре больше {MAX_ROWS} строк. Разделите файл на части не более чем по {MAX_ROWS} родителей.')
    return dict(sheets=[s['name'] for s in sheets], header=header, width=width,
                headers=[dict(index=i, label=c or f'Колонка {i + 1}') for i, c in enumerate(cells)],
                detected=header_columns(cells), data=data)


def parse_child(part):
    match = re.fullmatch(r'(.*?)\s*\(([^()]*)\)', part.strip())
    if not match or not match[1].strip() or not match[2].strip(): return None
    return match[1], match[2]


def evaluate(db, access, source):
    """Match every register row against the current school document, inside the caller's transaction."""
    current = db.execute('SELECT revision,document FROM workspaces WHERE id=?', (source.workspace,)).fetchone()
    if not current: raise HTTPException(422, 'Рабочее пространство не найдено. Обновите страницу.')
    document = json.loads(current[1])
    sheet = read(source)
    chosen = source.columns.model_dump() if source.columns else sheet['detected']
    base = dict(sheets=sheet['sheets'], sheet=source.sheet, headerRow=sheet['header'], headers=sheet['headers'],
                columns=chosen, detected=source.columns is None, rowCount=len(sheet['data']))
    for key in ['surname', 'name', 'children']:
        if chosen[key] is not None and chosen[key] >= sheet['width']: raise HTTPException(422, 'Выбранной колонки нет в файле.')
    if chosen['children'] is None or (chosen['surname'] is None and chosen['name'] is None):
        return base | dict(complete=False)
    classes = {c['id']: c['name'] for c in document['classes']}
    pupils = defaultdict(list)
    for student in document['students']:
        if student.get('active', True): pupils[class_key(classes.get(student['classId'], '')), name_key(student['name'])].append(student)
    entries, seen = [], set()
    for number, cells in sheet['data']:
        recipient = ' '.join(' '.join(cells[chosen[k]] for k in ['surname', 'name'] if chosen[k] is not None).split())
        raw = cells[chosen['children']]
        parts = [p.strip() for p in re.split(r'[,;\n]+', raw) if p.strip() not in EMPTY]
        entry = dict(row=number, key=f'r{number}', recipient=recipient, children=raw[:300], ids=[], found=[], missing=[], ambiguous=[], warnings=[], sources=None)
        entries.append(entry)
        if not parts: entry['outcome'] = 'noChildren'; continue
        if len(parts) > MAX_CHILDREN: raise HTTPException(422, f'Строка {number}: больше {MAX_CHILDREN} детей у одного родителя. Проверьте файл.')
        if not recipient or len(recipient) > 160: entry['outcome'] = 'noName'; continue
        identity, ids = [], []
        for part in parts:
            parsed = parse_child(part)
            if not parsed: identity.append(text_key(part)); entry['missing'].append(part[:120]); continue
            key = class_key(parsed[1]), name_key(parsed[0])
            identity.append(key)
            options = pupils.get(key, [])
            if not options: entry['missing'].append(part[:120])
            elif len(options) > 1: entry['ambiguous'].append(part[:120])
            elif options[0]['id'] not in ids: ids.append(options[0]['id'])
        signature = name_key(recipient), tuple(sorted(map(str, set(identity))))
        if signature in seen: entry['outcome'] = 'repeated'; continue
        seen.add(signature)
        entry['ids'] = ids
        entry['outcome'] = ('ambiguous' if entry['ambiguous'] else 'ready' if ids and not entry['missing'] else 'partial' if ids else 'notFound')
    matched = [e for e in entries if e['outcome'] in ('ready', 'partial')]
    result, sources = review(db, access, source.workspace,
                             [SimpleNamespace(key=e['key'], recipient=e['recipient'], role='parent', entityIds=e['ids']) for e in matched], document)
    for entry, item, origin in zip(matched, result, sources):
        entry['found'] = item['targets'];entry['warnings'] = item['warnings'];entry['sources'] = origin
    counts = dict(rows=len(entries), ready=0, readyCovered=0, partial=0, partialCovered=0, notFound=0, ambiguous=0, noChildren=0, noName=0, repeated=0)
    for entry in entries:
        covered = entry['outcome'] in ('ready', 'partial') and bool(entry['warnings'])
        counts[entry['outcome'] + ('Covered' if covered else '')] += 1
    token = digest(encoded(dict(kind='register', file=digest(source.content), sheet=source.sheet, header=sheet['header'], columns=chosen,
                                revision=current[0], rows=[[e['key'], e['recipient'], e['outcome'], e['ids'], e['warnings'], e['sources']] for e in entries])))
    return base | dict(complete=True, counts=counts, entries=entries, token=token)


def attention(entries):
    rows = []
    for e in entries:
        if e['outcome'] in ('partial', 'notFound', 'ambiguous', 'noName') or (e['outcome'] == 'ready' and e['warnings']):
            rows.append(dict(row=e['row'], recipient=e['recipient'], outcome=e['outcome'], children=e['children'], found=e['found'],
                             missing=e['missing'][:10], ambiguous=e['ambiguous'][:10], warnings=e['warnings']))
    return rows


def chosen_for_issue(entries, body):
    return [e for e in entries
            if (e['outcome'] == 'ready' or (e['outcome'] == 'partial' and body.includePartial)) and (not e['warnings'] or body.includeCovered)]


def register(app, access, transaction, admin):
    @app.post('/api/access/invitations/registers/preview')
    def review_register(body: Preview, request: Request):
        with transaction() as db:
            admin(db, request)
            plan = evaluate(db, access, body)
            if not plan['complete']:
                return dict(plan, entries=None, counts=None, attention=[], attentionTotal=0, offset=0, previewToken=None, needsColumns=True)
            marked = attention(plan['entries'])
            return {k: v for k, v in plan.items() if k not in ('entries', 'token')} | dict(
                needsColumns=False, attention=marked[body.offset:body.offset + PAGE], attentionTotal=len(marked), offset=body.offset, previewToken=plan['token'])

    @app.post('/api/access/invitations/registers')
    def issue_register(body: Issue, request: Request):
        with transaction() as db:
            admin(db, request);batch_tables(db)
            request_hash = digest(encoded(body.model_dump(exclude={'requestId'})))
            previous = db.execute('SELECT id,creator_id,request_hash FROM invitation_batches WHERE request_id=?', (body.requestId,)).fetchone()
            if previous:
                if previous[1] != request.state.user['id'] or previous[2] != request_hash:
                    raise HTTPException(409, 'Номер запроса уже использован для другого реестра.')
                return receipt(db, previous[0]) | dict(replayed=True)
            plan = evaluate(db, access, body)
            if not plan['complete']: raise HTTPException(422, 'Выберите колонки с родителем и детьми.')
            if plan['token'] != body.previewToken:
                raise HTTPException(409, 'Данные школы, аккаунты, приглашения или файл изменились. Повторите проверку реестра.')
            rows = chosen_for_issue(plan['entries'], body)
            if not rows: raise HTTPException(422, 'В реестре нет строк для выдачи с выбранными условиями.')
            created = now();expires = (created + timedelta(days=body.days)).isoformat()
            summary = dict(plan['counts'], issued=len(rows), includePartial=body.includePartial, includeCovered=body.includeCovered)
            batch_id = db.execute('''INSERT INTO invitation_batches (request_id,creator_id,workspace,request_hash,created,kind,source,summary)
                VALUES (?,?,?,?,?,'register',?,?)''', (body.requestId, request.state.user['id'], body.workspace, request_hash,
                                                      created.isoformat(), body.filename[:160], encoded(summary))).lastrowid
            codes = {}
            for entry in rows:
                raw = secrets.token_urlsafe(32)
                permissions = dict(workspaces=[body.workspace], grants=[grant(body.workspace, SimpleNamespace(role='parent', entityIds=entry['ids']))])
                invitation_id = db.execute('''INSERT INTO access_invitations
                    (token_hash,recipient,role,permissions,created,expires,creator_id) VALUES (?,?,?,?,?,?,?)''',
                    (digest(raw), entry['recipient'], 'parent', encoded(permissions), created.isoformat(), expires, request.state.user['id'])).lastrowid
                db.execute('INSERT INTO invitation_batch_items VALUES (?,?,?,?)', (batch_id, entry['key'], invitation_id, encoded(entry['found'])))
                codes[invitation_id] = raw
            access.record(db, 'invitations.register.created', detail=f'Реестр родителей {batch_id} · {len(rows)} приглашений')
            result = receipt(db, batch_id)
            for item in result['items']: item['code'] = codes[item['id']]
            return result | dict(replayed=False)
