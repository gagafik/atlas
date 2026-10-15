"""School meal ordering, kitchen counts and accountable meal issuance."""
from contextlib import closing, contextmanager
from datetime import datetime, time, timedelta
import hashlib
import json
import sqlite3
from uuid import uuid4

from fastapi import HTTPException

from .access import now
from .canteen_models import STAFF, ZONE, MEALS
from .canteen_finance import charge_meal, cancel_meal
from .notifications import encoded, permission_key
from .portal_cache import PortalSnapshots
from .school_forms import enabled, grant_ids


def today(): return now().astimezone(ZONE).date()
def stamp(): return now().isoformat()
def fingerprint(value): return hashlib.sha256(encoded(value).encode()).hexdigest()
def version(row, expected):
    if row['version'] != expected: raise HTTPException(409, 'Данные столовой изменились. Обновите страницу.')
def require_reason(reason):
    if not reason.strip(): raise HTTPException(422, 'Укажите основание действия школы.')


def tables(db):
    db.executescript('''
    CREATE TABLE IF NOT EXISTS canteen_config(workspace TEXT PRIMARY KEY,version INTEGER,document TEXT);
    CREATE TABLE IF NOT EXISTS canteen_menus(seq INTEGER PRIMARY KEY AUTOINCREMENT,id TEXT UNIQUE,workspace TEXT,version INTEGER,status TEXT,day TEXT,meal TEXT,currency TEXT,decimals INTEGER,created TEXT,document TEXT);
    CREATE INDEX IF NOT EXISTS canteen_menu_day ON canteen_menus(workspace,day,meal);
    CREATE UNIQUE INDEX IF NOT EXISTS canteen_menu_active ON canteen_menus(workspace,day,meal) WHERE status IN ('draft','published','closed');
    CREATE TABLE IF NOT EXISTS canteen_orders(seq INTEGER PRIMARY KEY AUTOINCREMENT,id TEXT UNIQUE,workspace TEXT,menu_id TEXT,student_id TEXT,version INTEGER,status TEXT,day TEXT,meal TEXT,variant_id TEXT,created TEXT,updated TEXT,served_at TEXT,charge_id TEXT,document TEXT);
    CREATE UNIQUE INDEX IF NOT EXISTS canteen_order_active ON canteen_orders(workspace,student_id,day,meal) WHERE status IN ('ordered','served','missed');
    CREATE INDEX IF NOT EXISTS canteen_order_menu ON canteen_orders(menu_id,status,variant_id);
    CREATE INDEX IF NOT EXISTS canteen_order_student ON canteen_orders(workspace,student_id,day,seq);
    CREATE INDEX IF NOT EXISTS canteen_order_day ON canteen_orders(workspace,day,status,seq);
    CREATE TABLE IF NOT EXISTS canteen_history(seq INTEGER PRIMARY KEY AUTOINCREMENT,workspace TEXT,menu_id TEXT,order_id TEXT,created TEXT,actor_id TEXT,actor_name TEXT,action TEXT,reason TEXT,document TEXT);
    CREATE INDEX IF NOT EXISTS canteen_history_menu ON canteen_history(workspace,menu_id,seq);
    CREATE INDEX IF NOT EXISTS canteen_history_order ON canteen_history(workspace,order_id,seq);
    CREATE TABLE IF NOT EXISTS canteen_requests(seq INTEGER PRIMARY KEY AUTOINCREMENT,user_id TEXT,request_id TEXT,fingerprint TEXT,result TEXT,UNIQUE(user_id,request_id));
    CREATE INDEX IF NOT EXISTS canteen_request_owner ON canteen_requests(user_id,seq);
    ''')


class Canteen:
    def __init__(self, access, notifications, payments, state_stamp):
        self.access, self.notifications, self.payments = access, notifications, payments
        self.snapshots = PortalSnapshots(access.read, state_stamp)

    def current(self): return now()

    def prepare(self):
        self.payments.prepare()
        with closing(self.access.read_connect()) as db:
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='canteen_request_owner'").fetchone(): return
        with self.access.lock, self.access.connect() as db: tables(db)

    @contextmanager
    def context(self, request, workspace, write=False):
        user = request.state.user
        if not user: raise HTTPException(403, 'Столовая доступна после входа.')
        snap = self.snapshots.get(workspace)
        if not enabled(user, snap, workspace): raise HTTPException(403, 'Нет доступа к этому пространству.')
        self.prepare()
        def check(db):
            row = db.execute('SELECT * FROM accounts WHERE id=? AND active=1', (user['id'],)).fetchone()
            revision = db.execute('SELECT revision FROM workspaces WHERE id=?', (workspace,)).fetchone()
            if not row or self.access.public(row) != user or not revision or revision[0] != snap.state.revision:
                raise HTTPException(409, 'Доступ или состав школы изменились. Обновите страницу.')
            db.row_factory = sqlite3.Row
        if write:
            with self.access.lock, self.access.connect() as db:
                db.execute('BEGIN IMMEDIATE'); check(db); yield db, user, snap
        else:
            with closing(self.access.read_connect()) as db:
                db.execute('BEGIN'); check(db); yield db, user, snap

    def config(self, db, w):
        row = db.execute('SELECT * FROM canteen_config WHERE workspace=?', (w,)).fetchone()
        return dict(version=row['version'], **json.loads(row['document'])) if row else dict(version=0, delegates=[], financeEnabled=False, allowDebt=False, studentOrdering=False)

    def permission(self, db, u, w):
        if u['role'] == 'admin': return dict(role='manager', classIds=[])
        if u['role'] not in STAFF: return None
        return next((a for a in self.config(db, w)['delegates'] if a['userId'] == u['id'] and a['permissions'] == permission_key(u)), None)

    def manager(self, db, u, w): return (self.permission(db, u, w) or {}).get('role') == 'manager'
    def require_manager(self, db, u, w):
        if not self.manager(db, u, w): raise HTTPException(403, 'Нужны полномочия администратора столовой.')

    def student_ids(self, db, u, snap, w):
        p = self.permission(db, u, w)
        if p and p['role'] in {'manager', 'issuer'}:
            return {x.id for x in snap.state.students if x.active and (not p['classIds'] or x.classId in p['classIds'])}
        if u['role'] in {'parent', 'student'}:
            return {id for id in grant_ids(u, w, 'student') if id in snap.entities['student'] and snap.entities['student'][id].active}
        return set()

    def menu(self, db, w, id):
        row = db.execute('SELECT * FROM canteen_menus WHERE workspace=? AND id=?', (w, id)).fetchone()
        if not row: raise HTTPException(404, 'Меню не найдено.')
        return {k: row[k] for k in row.keys() if k != 'document'} | dict(definition=json.loads(row['document']))

    def order(self, db, w, id):
        row = db.execute('SELECT * FROM canteen_orders WHERE workspace=? AND id=?', (w, id)).fetchone()
        if not row: raise HTTPException(404, 'Заказ не найден.')
        return {k: row[k] for k in row.keys() if k != 'document'} | dict(detail=json.loads(row['document']))

    def require_order(self, db, u, snap, w, order):
        if not self.manager(db, u, w) and order['student_id'] not in self.student_ids(db, u, snap, w):
            raise HTTPException(404, 'Заказ недоступен.')

    def menu_visible(self, db, u, snap, w, menu):
        permission = self.permission(db, u, w)
        if permission and permission['role'] in {'manager', 'kitchen'}: return True
        if menu['status'] == 'draft': return False
        classes = menu['definition']['classIds']
        return any(not classes or snap.entities['student'][id].classId in classes for id in self.student_ids(db, u, snap, w))

    def public_order(self, db, u, w, row):
        result = {k: row[k] for k in ['id', 'version', 'status', 'menu_id', 'student_id', 'day', 'meal', 'variant_id', 'created', 'updated', 'served_at', 'detail']}
        if self.manager(db, u, w): result['chargeId'] = row['charge_id']
        return result

    def history(self, db, w, u, action, reason, document, menu='', order=''):
        db.execute('''INSERT INTO canteen_history(workspace,menu_id,order_id,created,actor_id,actor_name,action,reason,document)
                      VALUES (?,?,?,?,?,?,?,?,?)''', (w, menu, order, stamp(), u['id'], u['name'], action, reason, encoded(document)))

    def replay(self, db, u, body, operation):
        key = fingerprint([operation, body.model_dump(mode='json')])
        row = db.execute('SELECT fingerprint,result FROM canteen_requests WHERE user_id=? AND request_id=?', (u['id'], body.requestId)).fetchone()
        if row:
            if key != row['fingerprint']: raise HTTPException(409, 'Этот идентификатор уже использован другим действием.')
            return key, json.loads(row['result'])
        return key, None

    def remember(self, db, u, body, key, result):
        # Retained with the associated meal/financial records, not a short-lived cache.
        db.execute('INSERT INTO canteen_requests(user_id,request_id,fingerprint,result) VALUES (?,?,?,?)', (u['id'], body.requestId, key, encoded(result)))
        return result

    def validate_definition(self, db, w, snap, definition, excluding=''):
        doc = definition.model_dump(mode='json')
        if definition.day < today() or definition.day > today() + timedelta(days=366):
            raise HTTPException(422, 'Меню создаётся на сегодня или ближайший год.')
        classes = set(doc['classIds']) | {id for v in doc['variants'] for id in v['classIds']}
        if classes - snap.entities['class_'].keys(): raise HTTPException(422, 'Проверьте действующие классы меню.')
        if db.execute("SELECT 1 FROM canteen_menus WHERE workspace=? AND day=? AND meal=? AND status IN ('draft','published','closed') AND id<>?", (w, doc['day'], doc['meal'], excluding)).fetchone():
            raise HTTPException(409, 'Для этого дня и приёма пищи уже есть меню. Добавьте варианты в него.')
        if any(v['price'] for v in doc['variants']) and not self.config(db, w)['financeEnabled']:
            raise HTTPException(409, 'Сначала администрация включает финансовый учёт питания.')
        return doc

    def create_menu(self, db, u, snap, w, definition):
        self.require_manager(db, u, w)
        doc = self.validate_definition(db, w, snap, definition)
        if db.execute("SELECT count(*) FROM canteen_menus WHERE workspace=? AND status NOT IN ('archived','cancelled')", (w,)).fetchone()[0] >= 1500:
            raise HTTPException(413, 'Архивируйте завершённые меню перед созданием новых.')
        cfg = self.payments.config(db, w); id = uuid4().hex
        db.execute('''INSERT INTO canteen_menus(id,workspace,version,status,day,meal,currency,decimals,created,document)
                      VALUES (?,?,1,'draft',?,?,?,?,?,?)''', (id, w, doc['day'], doc['meal'], cfg['currency'], cfg['decimals'], stamp(), encoded(doc)))
        self.history(db, w, u, 'menu_created', '', doc, menu=id)
        return dict(id=id, version=1)

    def times(self, menu):
        d = menu['definition']; day = datetime.fromisoformat(d['day']).date()
        return datetime.combine(day, time.fromisoformat(d['starts']), ZONE), datetime.combine(day, time.fromisoformat(d['ends']), ZONE)

    def can_reserve(self, db, u, snap, w, student):
        if self.manager(db, u, w): return
        if u['role'] not in {'parent', 'student'} or student not in self.student_ids(db, u, snap, w):
            raise HTTPException(404, 'Нет доступа к заказу для этого ученика.')
        if u['role'] == 'student' and not self.config(db, w)['studentOrdering']:
            raise HTTPException(403, 'Заказы оформляют родители или администрация.')

    def reserve(self, db, u, snap, w, item, override=False, reason=''):
        self.can_reserve(db, u, snap, w, item.studentId)
        pupil = snap.entities['student'].get(item.studentId)
        if not pupil or not pupil.active: raise HTTPException(422, 'Для заказа нужен действующий ученик.')
        menu = self.menu(db, w, item.menuId); version(menu, item.menuVersion); d = menu['definition']
        variant = next((v for v in d['variants'] if v['id'] == item.variantId), None)
        if not variant or d['classIds'] and pupil.classId not in d['classIds'] or variant['classIds'] and pupil.classId not in variant['classIds']:
            raise HTTPException(404, 'Вариант питания недоступен этому ученику.')
        if menu['status'] != 'published': raise HTTPException(409, 'Приём заказов на это меню закрыт.')
        if now() >= self.times(menu)[1]: raise HTTPException(409, 'Выдача по этому меню уже закончилась.')
        if override:
            self.require_manager(db, u, w); require_reason(reason)
        elif not datetime.fromisoformat(d['opensAt']) <= now() < datetime.fromisoformat(d['orderClosesAt']):
            raise HTTPException(409, 'Срок оформления заказа ещё не наступил или уже истёк.')
        if variant['price'] != item.expectedPrice: raise HTTPException(409, 'Цена изменилась. Проверьте меню заново.')
        if variant['price'] and not self.config(db, w)['financeEnabled']: raise HTTPException(409, 'Платное питание временно не принимает заказы.')
        if db.execute("SELECT 1 FROM canteen_orders WHERE workspace=? AND student_id=? AND day=? AND meal=? AND status<>'cancelled'", (w, pupil.id, menu['day'], menu['meal'])).fetchone():
            raise HTTPException(409, 'На этот приём пищи уже есть заказ, в том числе от другого представителя.')
        counts = dict(db.execute("SELECT variant_id,count(*) FROM canteen_orders WHERE menu_id=? AND status<>'cancelled' GROUP BY variant_id", (menu['id'],)))
        if sum(counts.values()) >= 5000 or variant['capacity'] is not None and counts.get(variant['id'], 0) >= variant['capacity']:
            raise HTTPException(409, 'Свободных порций этого варианта больше нет.')
        id = uuid4().hex
        charge = charge_meal(self.payments, db, u, snap, w, pupil.id, menu, variant, id, self.config(db, w)['allowDebt'])
        detail = dict(studentName=pupil.name, classId=pupil.classId, className=snap.entities['class_'][pupil.classId].name,
                      menuTitle=d['title'], variant=variant, currency=menu['currency'], decimals=menu['decimals'], starts=d['starts'], ends=d['ends'])
        db.execute('''INSERT INTO canteen_orders(id,workspace,menu_id,student_id,version,status,day,meal,variant_id,created,updated,served_at,charge_id,document)
                      VALUES (?,?,?,?,1,'ordered',?,?,?,?,?,NULL,?,?)''', (id, w, menu['id'], pupil.id, menu['day'], menu['meal'], variant['id'], stamp(), stamp(), charge, encoded(detail)))
        self.history(db, w, u, 'ordered', reason, dict(studentId=pupil.id, variantId=variant['id'], price=variant['price'], overrideCutoff=override), menu=menu['id'], order=id)
        return self.order(db, w, id)

    def change_order(self, db, u, snap, w, id, expected, action, override=False, reason=''):
        order = self.order(db, w, id); self.require_order(db, u, snap, w, order); version(order, expected)
        menu = self.menu(db, w, order['menu_id']); manager = self.manager(db, u, w)
        permission = self.permission(db, u, w) or {}; start, end = self.times(menu)
        if override:
            self.require_manager(db, u, w); require_reason(reason)
        if action == 'cancel':
            self.can_reserve(db, u, snap, w, order['student_id'])
            if order['status'] != 'ordered': raise HTTPException(409, 'Можно отменить только ожидающий выдачи заказ. Школа сначала исправляет отметку выдачи.')
            if not override and now() >= datetime.fromisoformat(menu['definition']['cancelClosesAt']):
                raise HTTPException(409, 'Срок самостоятельной отмены истёк. Обратитесь в школу.')
            cancel_meal(self.payments, db, u, w, order['student_id'], order['charge_id'], id)
            status = 'cancelled'
        elif action in {'serve', 'missed'}:
            if permission.get('role') not in {'manager', 'issuer'}: raise HTTPException(403, 'Нужны полномочия выдачи питания.')
            pupil = snap.entities['student'].get(order['student_id'])
            if not pupil or not pupil.active: raise HTTPException(409, 'Ученик неактивен. Заказ нужно сверить с администрацией.')
            if order['status'] != 'ordered': raise HTTPException(409, 'Этот заказ уже обработан.')
            if action == 'serve' and (now() < start or now() >= end and not override): raise HTTPException(409, 'Сейчас вне времени выдачи. Позднюю выдачу подтверждает администрация.')
            if action == 'missed' and now() < end: raise HTTPException(409, 'Неявка отмечается после окончания выдачи.')
            status = 'served' if action == 'serve' else 'missed'
        else:
            self.require_manager(db, u, w); require_reason(reason)
            if order['status'] not in {'served', 'missed'}: raise HTTPException(409, 'Нет отметки выдачи для исправления.')
            status = 'ordered'
        db.execute('UPDATE canteen_orders SET version=version+1,status=?,updated=?,served_at=? WHERE id=?', (status, stamp(), stamp() if status == 'served' else None, id))
        self.history(db, w, u, action, reason, dict(before=order['status'], after=status, overrideCutoff=override), menu=menu['id'], order=id)
        return self.order(db, w, id)

    def notify(self, db, u, snap, w, rows, action):
        ids = {x['student_id'] for x in rows}; recipients = []
        for p in self.notifications.granted_users(db, w, 'student', ids, ['parent']):
            if p['id'] != u['id'] and ids & self.student_ids(db, p, snap, w): recipients.append(p)
        self.notifications.deliver(db, recipients, w, snap.state.revision, 'canteen:' + action)

    def batch_rows(self, db, u, snap, w, body):
        if body.action == 'reserve':
            return [self.reserve(db, u, snap, w, item, body.overrideCutoff, body.reason) for item in body.reservations]
        return [self.change_order(db, u, snap, w, item.id, item.version, body.action, body.overrideCutoff, body.reason) for item in body.orders]

    def batch_preview(self, db, u, snap, w, body):
        students = {x.studentId for x in body.reservations}
        for ref in body.orders:
            order = self.order(db, w, ref.id); self.require_order(db, u, snap, w, order); students.add(order['student_id'])
        finance_versions = {id: self.payments.account(db, w, id, snap)['version'] for id in students}
        scope = [body.model_dump(mode='json', exclude={'requestId', 'previewHash'}), snap.state.revision, permission_key(u), self.config(db, w), self.payments.config(db, w)['version'], finance_versions, today().isoformat()]
        db.execute('SAVEPOINT canteen_preview')
        try:
            rows = self.batch_rows(db, u, snap, w, body)
            items = [dict(studentId=x['student_id'], name=x['detail']['studentName'], day=x['day'], meal=x['meal'], title=x['detail']['variant']['title'], price=x['detail']['variant']['price'], status=x['status']) for x in rows]
        finally:
            db.execute('ROLLBACK TO canteen_preview'); db.execute('RELEASE canteen_preview')
        return dict(items=items, count=len(items), previewHash=fingerprint([scope, items]))

    def register(self, app, Workspace):
        from .canteen_routes import register
        register(self, app, Workspace)
