"""Narrow meal-to-ledger integration on the caller's existing transaction.

Canteen permissions never grant general access to financial accounts. The school
explicitly enables this integration; the caller validates the exact published meal.
"""
import json
from uuid import uuid4
from fastapi import HTTPException
from .notifications import encoded
from .payments import stamp


def charge_meal(finance, db, user, snap, workspace, student, menu, variant, order_id, allow_debt):
    price = variant['price']
    if not price: return None
    cfg = finance.config(db, workspace)
    if (cfg['currency'], cfg['decimals']) != (menu['currency'], menu['decimals']):
        raise HTTPException(409, 'Единицы финансового учёта изменились. Школе нужно переиздать меню.')
    account = finance.account(db, workspace, student, snap)
    if user['role'] in {'parent', 'student'} and not finance.family_access(db, user, snap, workspace, account):
        raise HTTPException(403, 'Платное питание оформляет назначенный плательщик или администрация.')
    day = finance.open_day(db, workspace)
    balance = finance.summary(db, workspace, student)
    if not allow_debt and -balance['balance'] < price:
        raise HTTPException(409, 'Недостаточно средств для заказа питания. Пополните лицевой счёт.')
    finance.account(db, workspace, student, snap, True)
    charge_id = uuid4().hex
    label = f"Питание {menu['day']} · {variant['title']}"
    db.execute('''INSERT INTO finance_charges(id,workspace,student_id,plan_id,installment,title,due,created,book_date,document)
                  VALUES (?,?,?,NULL,NULL,?,?,?,?,?)''',
               (charge_id, workspace, student, label, menu['day'], stamp(), day, encoded(dict(source='canteen', orderId=order_id, menuId=menu['id'], amount=price))))
    finance.entry(db, workspace, user, student, 'charge', price, label, charge=charge_id)
    remaining = price; allocations = 0
    existing = db.execute('SELECT count(*) FROM finance_allocations WHERE workspace=? AND student_id=?', (workspace, student)).fetchone()[0]
    for receipt in finance.credits(db, workspace, student):
        amount = min(remaining, receipt['available'])
        if amount <= 0: continue
        allocations += 1
        if existing + allocations > 40000: raise HTTPException(413, 'Достигнут предел распределений счёта. Требуется расширение архива.')
        if allocations > 500: raise HTTPException(413, 'Слишком много частей оплаты. Финансовой службе нужно сверить счёт.')
        finance.allocate(db, workspace, user, student, charge_id, receipt['id'], amount, 'Оплата заказа питания')
        remaining -= amount
        if not remaining: break
    finance.touch(db, workspace, student)
    finance.history(db, workspace, user, student, 'canteen_charge', 'Заказ питания', dict(orderId=order_id, chargeId=charge_id, amount=price))
    return charge_id


def cancel_meal(finance, db, user, workspace, student, charge_id, order_id):
    if not charge_id: return
    finance.open_day(db, workspace)
    charge = db.execute('SELECT * FROM finance_charges WHERE id=? AND workspace=? AND student_id=?', (charge_id, workspace, student)).fetchone()
    if not charge or json.loads(charge['document']).get('orderId') != order_id:
        raise HTTPException(409, 'Связь заказа с начислением требует сверки финансовой службой.')
    allocations = db.execute('''SELECT a.* FROM finance_allocations a WHERE a.charge_id=? AND a.reversal_of IS NULL
                               AND NOT EXISTS(SELECT 1 FROM finance_allocations b WHERE b.reversal_of=a.id)''', (charge_id,)).fetchall()
    if len(allocations) > 500: raise HTTPException(413, 'Слишком много распределений. Финансовой службе нужно сверить счёт.')
    if db.execute('SELECT count(*) FROM finance_allocations WHERE workspace=? AND student_id=?', (workspace, student)).fetchone()[0] + len(allocations) > 40000:
        raise HTTPException(413, 'Достигнут предел распределений счёта. Требуется расширение архива.')
    for row in allocations:
        db.execute('''INSERT INTO finance_allocations(id,workspace,student_id,charge_id,receipt_id,amount,reversal_of,created,book_date,reason,actor_id)
                      VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
                   (uuid4().hex, workspace, student, charge_id, row['receipt_id'], -row['amount'], row['id'], stamp(), finance.open_day(db, workspace), 'Отмена питания', user['id']))
    amount = db.execute('SELECT COALESCE(SUM(amount),0) FROM finance_entries WHERE charge_id=?', (charge_id,)).fetchone()[0]
    if amount < 0: raise HTTPException(409, 'Начисление требует сверки финансовой службой.')
    if amount: finance.entry(db, workspace, user, student, 'adjustment', -amount, 'Отмена питания', charge=charge_id)
    finance.touch(db, workspace, student)
    finance.history(db, workspace, user, student, 'canteen_cancelled', 'Отмена заказа питания', dict(orderId=order_id, chargeId=charge_id, amount=amount))
