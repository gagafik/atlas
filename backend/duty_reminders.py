"""Duty reminders for the assigned teachers. One row per duty, teacher and date makes a notice happen once, also across restarts."""
from contextlib import closing, asynccontextmanager
from datetime import timedelta
import logging
import sqlite3
import threading
from starlette.concurrency import run_in_threadpool
from .access import WORKSPACES, now
from .duty_schedule import ZONE, labeller, duty_text, reminder_due, settings

PASS_LIMIT = 500


class DutyReminders:
    def __init__(self, service):
        self.service = service
        self.stop = threading.Event()
        self.thread = None

    def process_one(self):
        """One pass over the workspaces; True when notices or skip marks were stored."""
        service = self.service; service.prepare()
        progress = False
        for workspace in WORKSPACES:
            with closing(service.access.read_connect()) as db:
                found = db.execute('SELECT revision FROM workspaces WHERE id=?', (workspace,)).fetchone()
            # Never creates a workspace: a missing one has no duties.
            if not found: continue
            snap = service.snapshots.get(workspace)
            current = now().astimezone(ZONE)
            today = current.date()
            soon = [c for c in snap.state.commitments if c.kind == 'duty' and c.teacherIds and c.onDate in (today, today + timedelta(days=1))]
            if not soon: continue
            with service.access.lock, service.access.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                revision = db.execute('SELECT revision FROM workspaces WHERE id=?', (workspace,)).fetchone()
                if not revision or revision[0] != snap.state.revision: continue
                config = settings(db, workspace)
                if not config['enabled']: continue
                label, sent = labeller(snap.state), 0
                for c in sorted(soon, key=lambda c: (c.onDate, c.startTime, c.id)):
                    if not reminder_due(config, c, current): continue
                    for teacher in c.teacherIds:
                        if sent >= PASS_LIMIT: break
                        if db.execute('SELECT 1 FROM duty_reminders WHERE workspace=? AND commitment_id=? AND teacher_id=? AND on_date=?', (workspace, c.id, teacher, c.onDate.isoformat())).fetchone(): continue
                        users = service.notifications.granted_users(db, workspace, 'teacher', [teacher], ['teacher'])
                        if users:
                            service.notifications.deliver(db, users, workspace, snap.state.revision, 'duty.reminder', topic=f'duty-reminder:{workspace}:{c.id}', target=dict(module='duty', title=duty_text(snap.state, c, current, label)))
                        db.execute('INSERT INTO duty_reminders VALUES (?,?,?,?,?,?)', (workspace, c.id, teacher, c.onDate.isoformat(), 'sent' if users else 'skipped', now().isoformat()))
                        sent += 1
                db.execute('DELETE FROM duty_reminders WHERE workspace=? AND on_date<?', (workspace, (today - timedelta(days=30)).isoformat()))
                progress = progress or sent > 0
        return progress

    def start(self):
        if self.thread and self.thread.is_alive(): return
        self.service.prepare(); self.stop.clear()
        def work():
            while not self.stop.is_set():
                try: progress = self.process_one()
                except Exception as exc:
                    logging.getLogger(__name__).warning('Duty reminder worker error: %s', type(exc).__name__)
                    progress = False
                self.stop.wait(0.05 if progress else 60)
        self.thread = threading.Thread(target=work, name='atlas-duty-reminders', daemon=True)
        self.thread.start()

    def shutdown(self):
        self.stop.set()
        if self.thread: self.thread.join(timeout=15)

    def register(self, app):
        previous = app.router.lifespan_context
        @asynccontextmanager
        async def lifespan(application):
            async with previous(application):
                await run_in_threadpool(self.start)
                try: yield
                finally: await run_in_threadpool(self.shutdown)
        app.router.lifespan_context = lifespan
