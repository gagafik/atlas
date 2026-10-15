"""Local recovery by the computer owner; prompts without echoing passwords."""
import argparse
from getpass import getpass
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from backend import api
from backend.access import password_hash
from backend.recovery import revoke_all


def main():
    parser=argparse.ArgumentParser(description='Сброс пароля локальной учётной записи Атласа. Расписание не меняется.')
    parser.add_argument('login')
    args=parser.parse_args()
    with api.db_lock,api.connect() as db:
        api.access.tables(db)
        row=db.execute('SELECT id,active,version FROM accounts WHERE login=?',(args.login.lower(),)).fetchone()
    if not row: raise SystemExit('Учётная запись не найдена.')
    if not row[1]: raise SystemExit('Запись отключена. Включите её через другого администратора.')
    password=getpass('Новый пароль (12–128 символов): ')
    if not 12 <= len(password) <= 128: raise SystemExit('Недопустимая длина пароля.')
    if password != getpass('Повторите пароль: '): raise SystemExit('Пароли не совпадают.')
    encoded=password_hash(password)
    with api.db_lock,api.connect() as db:
        cursor=db.execute('UPDATE accounts SET password=?,version=version+1 WHERE id=? AND version=?',(encoded,row[0],row[2]))
        if not cursor.rowcount: raise SystemExit('Запись изменилась. Повторите команду.')
        revoke_all(db,row[0])
        db.execute('DELETE FROM sessions WHERE user_id=?',(row[0],))
        api.access.record(db,'account.password_reset_locally',detail=args.login.lower())
    print('Пароль изменён, прежние сеансы завершены, резервные коды отозваны. Расписание сохранено без изменений.')


if __name__=='__main__': main()
