"""Read-only readiness and administrator backup status. No school-data bootstrap."""
from contextlib import closing
import os
import sqlite3
import time

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

from tools.backup_service import public_status


def ready(database):
    try:
        if not database.is_file():return False
        deadline=time.monotonic()+1
        with closing(sqlite3.connect(database.resolve().as_uri()+'?mode=ro',uri=True,timeout=0.25)) as db:
            db.set_progress_handler(lambda:int(time.monotonic()>deadline),1000)
            # A bounded schema/read check, not a full integrity scan on every probe.
            db.execute('SELECT revision FROM workspaces LIMIT 1').fetchone()
            if os.environ.get('TIMETABLE_REQUIRE_AUTH')=='1':
                if not db.execute("SELECT 1 FROM accounts WHERE active=1 AND role='admin' LIMIT 1").fetchone():return False
        return True
    except (OSError,sqlite3.Error):return False


def register(app,access,database):
    @app.get('/api/ready')
    def readiness():
        ok=ready(database())
        return JSONResponse(status_code=200 if ok else 503,content={'status':'ready' if ok else 'unavailable'},headers={'Cache-Control':'no-store'})

    @app.get('/api/access/operations')
    def status(request:Request):
        user=request.state.user
        with access.read_connect() as db:
            db.execute('BEGIN')
            row=db.execute('SELECT role,active,version FROM accounts WHERE id=?',(user['id'],)).fetchone() if user else None
            if not row or row[0]!='admin' or not row[1] or row[2]!=user['version']:
                raise HTTPException(403,'Состояние сервера доступно действующему администратору.')
            return dict(databaseReady=ready(database()),backup=public_status(database()))

    @app.exception_handler(sqlite3.OperationalError)
    async def database_busy(request,exc):
        # A consistent file snapshot briefly reserves the single SQLite writer.
        # A failed write remains a failure; the browser must never claim it saved.
        if getattr(exc,'sqlite_errorcode',0)&255 in (sqlite3.SQLITE_BUSY,sqlite3.SQLITE_LOCKED):
            return JSONResponse(status_code=503,content={'detail':'Сервер занят. Повторите действие через несколько секунд.'},headers={'Retry-After':'5','Cache-Control':'no-store'})
        return JSONResponse(status_code=500,content={'detail':'Не удалось выполнить операцию с базой данных.'},headers={'Cache-Control':'no-store'})
