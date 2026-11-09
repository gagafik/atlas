"""Login journal: when, who, from which network, with which browser. No secrets.

Never stored: password, token, the full address, the raw User-Agent and the login typed for an unknown
account. Rows are kept RETENTION_DAYS and at most MAX_ROWS. Only administrators read it.
"""
from contextlib import closing
from datetime import date,datetime,time,timedelta,timezone
from typing import Literal
from zoneinfo import ZoneInfo
import csv,io,ipaddress

from fastapi import HTTPException,Query,Request,Response

RETENTION_DAYS=180
MAX_ROWS=100000
EXPORT_ROWS=50000
ZONE=ZoneInfo('Asia/Qyzylorda')
MARKER='login_journal_created'
BROWSERS=[('YaBrowser/','yandex'),('Edg/','edge'),('EdgA/','edge'),('EdgiOS/','edge'),('OPR/','opera'),('Opera','opera'),('Firefox/','firefox'),('FxiOS/','firefox'),
          ('Chrome/','chrome'),('CriOS/','chrome'),('Chromium/','chrome'),('Safari/','safari')]
COLUMNS=['seq','created','outcome','account_id','account_login','account_name','role','address','browser']


def cell(v):
    v=str(v)
    # Spreadsheet programs run text that starts with these characters as formulas.
    return "'"+v if v.lstrip().startswith(('=','+','-','@','\t','\r')) else v
def has_table(db,name):return bool(db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(name,)).fetchone())


# access imports this module, so it keeps its own clock; tests patch backend.login_journal.now.
def now():return datetime.now(timezone.utc)


def tables(db):
    """Statement by statement: this runs inside the sign-in transaction and executescript would commit it."""
    fresh=not db.execute('SELECT 1 FROM sqlite_master WHERE name=?',(MARKER,)).fetchone()
    db.execute('CREATE TABLE IF NOT EXISTS login_journal(seq INTEGER PRIMARY KEY AUTOINCREMENT,created TEXT NOT NULL,outcome TEXT NOT NULL,account_id TEXT,account_login TEXT,account_name TEXT,role TEXT,address TEXT NOT NULL,browser TEXT NOT NULL)')
    db.execute('CREATE INDEX IF NOT EXISTS login_journal_created ON login_journal(created)')
    db.execute('CREATE INDEX IF NOT EXISTS login_journal_account ON login_journal(account_id,seq)')
    # Once: earlier versions kept the typed name of a failed sign-in in the audit even when no such account exists.
    if fresh and has_table(db,'audit') and has_table(db,'accounts'):
        db.execute("UPDATE audit SET detail='' WHERE action='account.login_failed' AND detail!='' AND detail NOT IN (SELECT login FROM accounts)")


def masked(host):
    try:ip=ipaddress.ip_address(host)
    except ValueError:return ''
    if ip.version==6 and ip.ipv4_mapped:ip=ip.ipv4_mapped
    return str(ipaddress.ip_network(f'{ip}/{24 if ip.version==4 else 48}',strict=False))


def browser_family(agent):
    for mark,family in BROWSERS:
        if mark in agent:return family
    return 'other'


def record(db,request,outcome,account=None):
    """account: the accounts row or None. The typed login of an unknown account is never an input."""
    tables(db)
    host=request.client.host if request.client else ''
    stamp=now()
    db.execute('INSERT INTO login_journal(created,outcome,account_id,account_login,account_name,role,address,browser) VALUES (?,?,?,?,?,?,?,?)',
               (stamp.replace(microsecond=0).isoformat(),outcome,*(account[0:3] if account else (None,None,None)),account[3] if account else None,masked(host),browser_family(request.headers.get('user-agent',''))))
    db.execute('DELETE FROM login_journal WHERE created<?',((stamp-timedelta(days=RETENTION_DAYS)).replace(microsecond=0).isoformat(),))
    db.execute('DELETE FROM login_journal WHERE seq<=(SELECT max(seq) FROM login_journal)-?',(MAX_ROWS,))


def register(app,access):
    def bounds(start,end):
        if start and end and end<start:raise HTTPException(422,'Конец периода раньше начала.')
        low=(now()-timedelta(days=RETENTION_DAYS)).replace(microsecond=0)
        first=datetime.combine(start,time(),ZONE).astimezone(timezone.utc).replace(microsecond=0) if start else None
        last=datetime.combine(end+timedelta(days=1),time(),ZONE).astimezone(timezone.utc).replace(microsecond=0) if end else None
        return max(low,first) if first else low,last
    def query(request,outcome,account,start,end,before,limit):
        user=request.state.user
        if not user or user['role']!='admin':raise HTTPException(403,'Журнал входов доступен администрации.')
        low,high=bounds(start,end)
        with closing(access.read_connect()) as db:
            ready=db.execute('SELECT 1 FROM sqlite_master WHERE name=?',(MARKER,)).fetchone()
        if not ready:
            with access.lock,access.connect() as db:tables(db)
        where=['created>=?'];params=[low.isoformat()]
        if high:where.append('created<?');params.append(high.isoformat())
        if outcome:where.append('outcome=?');params.append(outcome)
        if account=='unknown':where.append('account_id IS NULL')
        elif account:where.append('account_id=?');params.append(account)
        if before is not None:where.append('seq<?');params.append(before)
        with closing(access.read_connect()) as db:
            return db.execute(f"SELECT {','.join(COLUMNS)} FROM login_journal WHERE {' AND '.join(where)} ORDER BY seq DESC LIMIT ?",(*params,limit)).fetchall()
    @app.get('/api/access/logins')
    def logins(request:Request,outcome:Literal['','success','failed']='',account:str='',start:date|None=None,end:date|None=None,before:int|None=None,limit:int=Query(100,ge=1,le=200)):
        rows=query(request,outcome,account,start,end,before,limit+1)
        items=[dict(seq=r[0],created=r[1],outcome=r[2],accountId=r[3],accountLogin=r[4],accountName=r[5],role=r[6],address=r[7],browser=r[8]) for r in rows[:limit]]
        return dict(items=items,nextBefore=items[-1]['seq'] if len(rows)>limit else None,retentionDays=RETENTION_DAYS)
    @app.get('/api/access/logins/export')
    def export(request:Request,outcome:Literal['','success','failed']='',account:str='',start:date|None=None,end:date|None=None):
        rows=query(request,outcome,account,start,end,None,EXPORT_ROWS)
        stream=io.StringIO(newline='');writer=csv.writer(stream,delimiter=';')
        writer.writerow(['Время','Результат','Учётная запись','Логин','Роль','Сеть','Браузер'])
        for r in rows:
            local=datetime.fromisoformat(r[1]).astimezone(ZONE).strftime('%d.%m.%Y %H:%M')
            writer.writerow([cell(v) for v in (local,'Вход' if r[2]=='success' else 'Неудачная попытка',r[5] or 'Неизвестный логин',r[4] or '',r[6] or '',r[7],r[8])])
        return Response(('﻿'+stream.getvalue()).encode('utf-8'),media_type='text/csv; charset=utf-8',headers={'Content-Disposition':'attachment; filename="logins.csv"','Cache-Control':'no-store','X-Content-Type-Options':'nosniff'})
