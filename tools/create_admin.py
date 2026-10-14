"""Create the first administrator interactively, without opening web setup."""
import argparse
from getpass import getpass
import json
from uuid import uuid4
from pydantic import ValidationError


def create_first_admin(login, name, password):
    from backend import api
    from backend.access import Setup, WORKSPACES, password_hash
    body=Setup(login=login,name=name,password=password)
    encoded=password_hash(body.password.get_secret_value())
    user_id=str(uuid4())
    with api.access.lock,api.access.connect() as db:
        api.access.tables(db);db.execute('BEGIN IMMEDIATE')
        if db.execute('SELECT 1 FROM accounts LIMIT 1').fetchone():
            raise ValueError('Аккаунты уже существуют. Используйте вход или восстановление пароля; данные не изменены.')
        db.execute('INSERT INTO accounts VALUES (?,?,?,?,?,?,?,?)',(user_id,body.login,body.name,'admin',1,0,encoded,json.dumps(dict(workspaces=WORKSPACES,grants=[]))))
        api.access.record(db,'access.enabled',detail='console bootstrap',principal=dict(id=user_id,name=body.name))
    return user_id


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('login');parser.add_argument('--name',required=True)
    args=parser.parse_args()
    password=getpass('Пароль администратора (12–128 символов): ')
    if getpass('Повторите пароль: ')!=password:parser.exit(2,'Пароли не совпадают.\n')
    try:create_first_admin(args.login,args.name,password)
    except ValidationError as exc:
        details='; '.join('.'.join(map(str,e['loc']))+': '+e['msg'] for e in exc.errors(include_input=False))
        parser.exit(2,'Некорректные данные: '+details+'\n')
    except ValueError as exc:parser.exit(2,str(exc)+'\n')
    print('Администратор создан. Можно запускать сервер и входить с указанным логином.')


if __name__=='__main__':main()
