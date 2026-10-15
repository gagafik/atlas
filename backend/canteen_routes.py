from datetime import date, timedelta
import json
from typing import Literal

from fastapi import HTTPException, Query, Request

from .canteen import MEALS, STAFF, stamp, today, version, require_reason
from .canteen_models import Settings, MenuCreate, MenuEdit, MenuTransition, MenuCopy, MenuDefinition, OrderCreate, OrderAction, Batch
from .notifications import encoded, permission_key
from .payment_routes import csv_response
from .school_forms import enabled


def dates(start, end, maximum=93):
    start = start or today(); end = end or start + timedelta(days=13)
    if end < start or (end - start).days >= maximum: raise HTTPException(422, f'Выберите период до {maximum} дней.')
    return start.isoformat(), end.isoformat()


def register(s, app, Workspace):
    @app.get('/api/canteen/options')
    def options(request: Request, workspace: Workspace = 'school'):
        with s.context(request, workspace) as (db, u, snap):
            config = s.config(db, workspace); permission = s.permission(db, u, workspace)
            ids = s.student_ids(db, u, snap, workspace)
            students = [dict(id=p.id, name=p.name, classId=p.classId, className=snap.entities['class_'][p.classId].name) for p in snap.state.students if p.id in ids]
            staff = []
            if u['role'] == 'admin':
                for row in db.execute("SELECT * FROM accounts WHERE active=1 AND role IN ('admin','editor','teacher','staff') ORDER BY name,id"):
                    p = s.access.public(row)
                    if enabled(p, snap, workspace): staff.append(dict(id=p['id'], name=p['name']))
            exposed = {k: config[k] for k in ['version', 'financeEnabled', 'allowDebt', 'studentOrdering']}
            if u['role'] == 'admin': exposed['delegates'] = [{k: a[k] for k in ['userId', 'role', 'classIds']} for a in config['delegates']]
            finance = s.payments.config(db, workspace)
            return dict(admin=u['role'] == 'admin', permission=(permission or {}).get('role'), config=exposed, students=students,
                        classes=[dict(id=c.id, name=c.name) for c in snap.state.classes] if permission or u['role'] == 'admin' else [], staff=staff,
                        currency=finance['currency'], decimals=finance['decimals'], today=today().isoformat(), timeZone='Asia/Qyzylorda', meals=MEALS, canOrderPaid=u['role'] != 'student',
                        canOrder=(permission or {}).get('role') == 'manager' or u['role'] == 'parent' or u['role'] == 'student' and config['studentOrdering'])

    @app.put('/api/canteen/config')
    def config_write(body: Settings, request: Request, workspace: Workspace = 'school'):
        with s.context(request, workspace, True) as (db, u, snap):
            if u['role'] != 'admin': raise HTTPException(403, 'Настройки задаёт администрация школы.')
            key, old = s.replay(db, u, body, 'config:' + workspace)
            if old: return old
            current = s.config(db, workspace); version(current, body.version); require_reason(body.reason)
            delegates = []
            for item in body.delegates:
                row = db.execute('SELECT * FROM accounts WHERE id=? AND active=1', (item.userId,)).fetchone()
                p = s.access.public(row) if row else None
                if not p or p['role'] not in STAFF or not enabled(p, snap, workspace): raise HTTPException(422, 'Проверьте действующих сотрудников.')
                if set(item.classIds) - snap.entities['class_'].keys(): raise HTTPException(422, 'Проверьте классы сотрудника выдачи.')
                delegates.append(item.model_dump() | dict(permissions=permission_key(p)))
            doc = dict(delegates=delegates, financeEnabled=body.financeEnabled, allowDebt=body.allowDebt, studentOrdering=body.studentOrdering)
            db.execute('INSERT INTO canteen_config VALUES (?,?,?) ON CONFLICT(workspace) DO UPDATE SET version=excluded.version,document=excluded.document', (workspace, body.version + 1, encoded(doc)))
            s.history(db, workspace, u, 'configured', body.reason, dict(before=current, after=doc))
            return s.remember(db, u, body, key, dict(version=body.version + 1))

    @app.get('/api/canteen/menus')
    def menus(request: Request, workspace: Workspace = 'school', start: date | None = None, end: date | None = None,
              offset: int = Query(default=0, ge=0, le=100000), archived: bool = False):
        a, b = dates(start, end)
        with s.context(request, workspace) as (db, u, snap):
            rows = db.execute('SELECT id FROM canteen_menus WHERE workspace=? AND day BETWEEN ? AND ? ORDER BY day,meal,seq DESC LIMIT 5001', (workspace, a, b)).fetchall()
            if len(rows) > 5000: raise HTTPException(413, 'Уменьшите период просмотра меню.')
            items = []
            for row in rows:
                menu = s.menu(db, workspace, row['id'])
                if (archived or menu['status'] != 'archived') and s.menu_visible(db, u, snap, workspace, menu): items.append(menu)
            total = len(items); items = items[offset:offset + 50]
            for menu in items:
                menu['reserved'] = dict(db.execute("SELECT variant_id,count(*) FROM canteen_orders WHERE menu_id=? AND status<>'cancelled' GROUP BY variant_id", (menu['id'],)))
            return dict(items=items, totalCount=total, nextOffset=offset + 50 if offset + 50 < total else None)

    @app.get('/api/canteen/menus/{id}')
    def menu_read(id: str, request: Request, workspace: Workspace = 'school'):
        with s.context(request, workspace) as (db, u, snap):
            menu = s.menu(db, workspace, id)
            if not s.menu_visible(db, u, snap, workspace, menu): raise HTTPException(404, 'Меню недоступно.')
            menu['reserved'] = dict(db.execute("SELECT variant_id,count(*) FROM canteen_orders WHERE menu_id=? AND status<>'cancelled' GROUP BY variant_id", (id,)))
            return menu

    @app.post('/api/canteen/menus')
    def menu_create(body: MenuCreate, request: Request, workspace: Workspace = 'school'):
        with s.context(request, workspace, True) as (db, u, snap):
            s.require_manager(db, u, workspace); key, old = s.replay(db, u, body, 'menu:' + workspace)
            if old: return old
            return s.remember(db, u, body, key, s.create_menu(db, u, snap, workspace, body.definition))

    @app.put('/api/canteen/menus/{id}')
    def menu_edit(id: str, body: MenuEdit, request: Request, workspace: Workspace = 'school'):
        with s.context(request, workspace, True) as (db, u, snap):
            s.require_manager(db, u, workspace); key, old = s.replay(db, u, body, 'edit:' + workspace + ':' + id)
            if old: return old
            menu = s.menu(db, workspace, id); version(menu, body.version)
            if menu['status'] != 'draft': raise HTTPException(409, 'Опубликованные блюда, аллергены, цены и сроки не меняются. Скопируйте меню в новый черновик.')
            doc = s.validate_definition(db, workspace, snap, body.definition, id); cfg = s.payments.config(db, workspace)
            db.execute('UPDATE canteen_menus SET version=version+1,day=?,meal=?,currency=?,decimals=?,document=? WHERE id=?', (doc['day'], doc['meal'], cfg['currency'], cfg['decimals'], encoded(doc), id))
            s.history(db, workspace, u, 'menu_edited', body.reason, dict(before=menu['definition'], after=doc), menu=id)
            return s.remember(db, u, body, key, dict(id=id, version=body.version + 1))

    @app.post('/api/canteen/menus/{id}/transition')
    def menu_transition(id: str, body: MenuTransition, request: Request, workspace: Workspace = 'school'):
        with s.context(request, workspace, True) as (db, u, snap):
            s.require_manager(db, u, workspace); key, old = s.replay(db, u, body, 'transition:' + workspace + ':' + id)
            if old: return old
            menu = s.menu(db, workspace, id); version(menu, body.version); action = body.action
            if action == 'publish':
                if menu['status'] != 'draft': raise HTTPException(409, 'Опубликовать можно только черновик.')
                s.validate_definition(db, workspace, snap, MenuDefinition.model_validate(menu['definition']), id)
                if s.times(menu)[0] <= s.current(): raise HTTPException(409, 'Время начала выдачи уже наступило.')
                cfg = s.payments.config(db, workspace)
                if (cfg['currency'], cfg['decimals']) != (menu['currency'], menu['decimals']): raise HTTPException(409, 'Единицы учёта изменились. Сначала сохраните черновик заново.')
                status = 'published'
            elif action == 'close':
                if menu['status'] != 'published': raise HTTPException(409, 'Приём заказов уже закрыт.')
                status = 'closed'
            elif action == 'reopen':
                if menu['status'] != 'closed' or s.times(menu)[1] <= s.current(): raise HTTPException(409, 'Приём заказов нельзя открыть.')
                status = 'published'
            elif action == 'cancel':
                require_reason(body.reason)
                if menu['status'] not in {'draft', 'published', 'closed'}: raise HTTPException(409, 'Это меню уже завершено.')
                if db.execute("SELECT 1 FROM canteen_orders WHERE menu_id=? AND status<>'cancelled'", (id,)).fetchone():
                    raise HTTPException(409, 'Сначала закройте запись и отмените заказы. Отметки выдачи исправляются отдельно.')
                status = 'cancelled'
            else:
                if s.times(menu)[1] > s.current(): raise HTTPException(409, 'Меню архивируется после окончания выдачи.')
                if menu['status'] == 'draft' or db.execute("SELECT 1 FROM canteen_orders WHERE menu_id=? AND status='ordered'", (id,)).fetchone(): raise HTTPException(409, 'Сначала обработайте ожидающие заказы или отмените черновик.')
                status = 'archived'
            db.execute('UPDATE canteen_menus SET version=version+1,status=? WHERE id=?', (status, id))
            s.history(db, workspace, u, 'menu_' + action, body.reason, dict(before=menu['status'], after=status), menu=id)
            return s.remember(db, u, body, key, dict(id=id, version=body.version + 1, status=status))

    @app.post('/api/canteen/menus/{id}/copy')
    def menu_copy(id: str, body: MenuCopy, request: Request, workspace: Workspace = 'school'):
        with s.context(request, workspace, True) as (db, u, snap):
            s.require_manager(db, u, workspace); key, old = s.replay(db, u, body, 'copy:' + workspace + ':' + id)
            if old: return old
            menu = s.menu(db, workspace, id); version(menu, body.version)
            definition = MenuDefinition.model_validate(menu['definition']); delta = body.day - definition.day
            definition = definition.model_copy(update=dict(day=body.day, opensAt=definition.opensAt + delta, orderClosesAt=definition.orderClosesAt + delta, cancelClosesAt=definition.cancelClosesAt + delta))
            return s.remember(db, u, body, key, s.create_menu(db, u, snap, workspace, definition))

    def order_filter(db, u, snap, w, start, end, menu_id='', student='', status='all', q=''):
        where = ['workspace=?', 'day BETWEEN ? AND ?']; values = [w, start, end]
        if not s.manager(db, u, w):
            if u['role'] not in {'parent', 'student'} and (s.permission(db, u, w) or {}).get('role') != 'issuer': raise HTTPException(403, 'Нет доступа к личным заказам.')
            where.append('student_id IN (SELECT value FROM json_each(?))'); values.append(encoded(sorted(s.student_ids(db, u, snap, w))))
        for col, val in [('menu_id', menu_id), ('student_id', student)]:
            if val: where.append(col + '=?'); values.append(val)
        if status != 'all': where.append('status=?'); values.append(status)
        if q:
            db.create_function('canteen_casefold', 1, lambda value: str(value or '').casefold(), deterministic=True)
            where.append("instr(canteen_casefold(json_extract(document,'$.studentName')),?)>0"); values.append(q.casefold())
        return ' AND '.join(where), values

    @app.get('/api/canteen/orders')
    def orders(request: Request, workspace: Workspace = 'school', start: date | None = None, end: date | None = None,
               menuId: str = '', studentId: str = '', status: Literal['all','ordered','cancelled','served','missed'] = 'all',
               q: str = Query(default='', max_length=120), offset: int = Query(default=0, ge=0, le=100000)):
        a, b = dates(start, end)
        with s.context(request, workspace) as (db, u, snap):
            where, values = order_filter(db, u, snap, workspace, a, b, menuId, studentId, status, q)
            count = db.execute('SELECT count(*) FROM canteen_orders WHERE ' + where, values).fetchone()[0]
            rows = db.execute('SELECT id FROM canteen_orders WHERE ' + where + ' ORDER BY day,meal,seq LIMIT 50 OFFSET ?', (*values, offset)).fetchall()
            return dict(items=[s.public_order(db, u, workspace, s.order(db, workspace, r['id'])) for r in rows], totalCount=count, nextOffset=offset + 50 if offset + 50 < count else None)

    @app.get('/api/canteen/orders/export')
    def order_export(request: Request, workspace: Workspace = 'school', start: date | None = None, end: date | None = None):
        a, b = dates(start, end, 31)
        with s.context(request, workspace) as (db, u, snap):
            if (s.permission(db, u, workspace) or {}).get('role') not in {'manager', 'issuer'}: raise HTTPException(403, 'Выгрузка именного списка доступна сотрудникам столовой.')
            where, values = order_filter(db, u, snap, workspace, a, b)
            def rows():
                yield ['Дата', 'Приём пищи', 'Ученик', 'Класс при заказе', 'Вариант', 'Статус', 'Время выдачи']
                for row in db.execute('SELECT * FROM canteen_orders WHERE ' + where + ' ORDER BY day,meal,seq', values):
                    d = json.loads(row['document']); yield [row['day'], MEALS[row['meal']], d['studentName'], d['className'], d['variant']['title'], row['status'], row['served_at']]
            return csv_response(rows(), 'meal-orders.csv')

    @app.get('/api/canteen/orders/{id}')
    def order_read(id: str, request: Request, workspace: Workspace = 'school'):
        with s.context(request, workspace) as (db, u, snap):
            order = s.order(db, workspace, id); s.require_order(db, u, snap, workspace, order)
            return s.public_order(db, u, workspace, order)

    @app.post('/api/canteen/orders')
    def order_create(body: OrderCreate, request: Request, workspace: Workspace = 'school'):
        with s.context(request, workspace, True) as (db, u, snap):
            s.can_reserve(db, u, snap, workspace, body.reservation.studentId)
            key, old = s.replay(db, u, body, 'order:' + workspace)
            if old: return old
            order = s.reserve(db, u, snap, workspace, body.reservation, body.overrideCutoff, body.reason)
            s.notify(db, u, snap, workspace, [order], 'ordered')
            return s.remember(db, u, body, key, dict(id=order['id'], version=order['version']))

    def authorize_action(db, u, snap, w, order, action):
        s.require_order(db, u, snap, w, order)
        if action == 'cancel': s.can_reserve(db, u, snap, w, order['student_id'])
        elif action == 'undo-service': s.require_manager(db, u, w)
        elif (s.permission(db, u, w) or {}).get('role') not in {'manager', 'issuer'}: raise HTTPException(403, 'Нужны полномочия выдачи питания.')

    @app.post('/api/canteen/orders/{id}/action')
    def order_action(id: str, body: OrderAction, request: Request, workspace: Workspace = 'school'):
        with s.context(request, workspace, True) as (db, u, snap):
            order = s.order(db, workspace, id); authorize_action(db, u, snap, workspace, order, body.action)
            key, old = s.replay(db, u, body, 'order-action:' + workspace + ':' + id)
            if old: return old
            order = s.change_order(db, u, snap, workspace, id, body.version, body.action, body.overrideCutoff, body.reason)
            if body.action == 'cancel': s.notify(db, u, snap, workspace, [order], 'cancelled')
            return s.remember(db, u, body, key, dict(id=id, version=order['version'], status=order['status']))

    def authorize_batch(db, u, snap, w, body):
        for ref in body.reservations: s.can_reserve(db, u, snap, w, ref.studentId)
        for ref in body.orders: authorize_action(db, u, snap, w, s.order(db, w, ref.id), body.action)
        if body.overrideCutoff: s.require_manager(db, u, w); require_reason(body.reason)

    @app.post('/api/canteen/batch/preview')
    def batch_preview(body: Batch, request: Request, workspace: Workspace = 'school'):
        with s.context(request, workspace, True) as (db, u, snap):
            authorize_batch(db, u, snap, workspace, body)
            return s.batch_preview(db, u, snap, workspace, body)

    @app.post('/api/canteen/batch')
    def batch_commit(body: Batch, request: Request, workspace: Workspace = 'school'):
        with s.context(request, workspace, True) as (db, u, snap):
            authorize_batch(db, u, snap, workspace, body)
            key, old = s.replay(db, u, body, 'batch:' + workspace)
            if old: return old
            preview = s.batch_preview(db, u, snap, workspace, body)
            if preview['previewHash'] != body.previewHash: raise HTTPException(409, 'Условия или счета изменились. Повторите предпросмотр.')
            rows = s.batch_rows(db, u, snap, workspace, body)
            if body.action in {'reserve', 'cancel'}: s.notify(db, u, snap, workspace, rows, 'ordered' if body.action == 'reserve' else 'cancelled')
            return s.remember(db, u, body, key, dict(count=len(rows), ids=[x['id'] for x in rows]))

    def report_rows(db, u, snap, w, a, b):
        permission = s.permission(db, u, w)
        if not permission: raise HTTPException(403, 'Сводка доступна сотрудникам столовой.')
        scope = ''; values = [w, a, b]
        if permission['role'] == 'issuer':
            scope = ' AND student_id IN (SELECT value FROM json_each(?))'; values.append(encoded(sorted(s.student_ids(db, u, snap, w))))
        counts = db.execute('''SELECT menu_id,variant_id,status,count(*) AS quantity FROM canteen_orders
                               WHERE workspace=? AND day BETWEEN ? AND ?''' + scope + ' GROUP BY menu_id,variant_id,status', values).fetchall()
        grouped = {}
        for row in counts: grouped.setdefault((row['menu_id'], row['variant_id']), {})[row['status']] = row['quantity']
        result = []
        for row in db.execute("SELECT id FROM canteen_menus WHERE workspace=? AND day BETWEEN ? AND ? AND status<>'draft' ORDER BY day,meal,seq", (w, a, b)):
            menu = s.menu(db, w, row['id'])
            for variant in menu['definition']['variants']:
                quantities = {k: grouped.get((menu['id'], variant['id']), {}).get(k, 0) for k in ['ordered', 'served', 'missed', 'cancelled']}
                result.append(dict(menuId=menu['id'], day=menu['day'], meal=MEALS[menu['meal']], title=variant['title'], allergens=variant['allergens'], **quantities, portions=quantities['ordered'] + quantities['served'] + quantities['missed']))
        return result

    @app.get('/api/canteen/report')
    def report(request: Request, workspace: Workspace = 'school', start: date | None = None, end: date | None = None):
        a, b = dates(start, end, 31)
        with s.context(request, workspace) as (db, u, snap): return dict(items=report_rows(db, u, snap, workspace, a, b), start=a, end=b)

    @app.get('/api/canteen/report/export')
    def report_export(request: Request, workspace: Workspace = 'school', start: date | None = None, end: date | None = None):
        a, b = dates(start, end, 31)
        with s.context(request, workspace) as (db, u, snap):
            items = report_rows(db, u, snap, workspace, a, b)
            return csv_response([['Дата','Приём пищи','Вариант','Аллергены','Порций','Ожидают','Выдано','Не пришли','Отменено'], *[[x[k] if k != 'allergens' else ', '.join(x[k]) for k in ['day','meal','title','allergens','portions','ordered','served','missed','cancelled']] for x in items]], 'kitchen-totals.csv')

    @app.get('/api/canteen/history')
    def history(request: Request, workspace: Workspace = 'school', menuId: str = '', orderId: str = '', before: int = Query(default=9223372036854775807, ge=1)):
        with s.context(request, workspace) as (db, u, snap):
            s.require_manager(db, u, workspace); where = ['workspace=?', 'seq<?']; values = [workspace, before]
            if menuId: where.append('menu_id=?'); values.append(menuId)
            if orderId: where.append('order_id=?'); values.append(orderId)
            rows = db.execute('SELECT * FROM canteen_history WHERE ' + ' AND '.join(where) + ' ORDER BY seq DESC LIMIT 51', values).fetchall()
            return dict(items=[{k: row[k] for k in ['seq','created','actor_name','action','reason','menu_id','order_id']} for row in rows[:50]], nextBefore=rows[49]['seq'] if len(rows) > 50 else None)
