"""Durable local reminders: inbox insertion and completion share one transaction."""
from contextlib import closing, asynccontextmanager
import logging
import sqlite3
import threading
from starlette.concurrency import run_in_threadpool
from .access import now
from .meeting_models import utc


class MeetingReminders:
    def __init__(self,service):
        self.service=service
        self.stop=threading.Event()
        self.thread=None

    def process_one(self):
        service=self.service;service.prepare()
        with closing(service.access.read_connect()) as db:
            candidate=db.execute("SELECT r.booking_id,b.workspace FROM meeting_reminders r JOIN meeting_bookings b ON b.id=r.booking_id WHERE r.status='pending' AND r.due<=? ORDER BY r.due,r.booking_id LIMIT 1",(utc(now()),)).fetchone()
        if not candidate:return False
        id,workspace=candidate;snap=service.snapshots.get(workspace)
        with service.access.lock,service.access.connect() as db:
            db.execute('BEGIN IMMEDIATE');db.row_factory=sqlite3.Row
            revision=db.execute('SELECT revision FROM workspaces WHERE id=?',(workspace,)).fetchone()
            if not revision or revision[0]!=snap.state.revision:return False
            reminder=db.execute("SELECT 1 FROM meeting_reminders WHERE booking_id=? AND status='pending' AND due<=?",(id,utc(now()))).fetchone()
            if not reminder:return True
            booking=service.booking(db,workspace,id);event=service.event(db,workspace,booking['event_id'])
            recipients=[]
            if booking['status']=='scheduled' and event['status'] in ['open','closed'] and booking['starts_at']>utc(now()):
                owner=service.booking_owner(db,booking,snap,workspace)
                host=service.hosts(db,event,snap,workspace).get(booking['host_id'])
                if owner:recipients.append(owner)
                if host and host['current']:recipients.append(host['user'])
            if recipients:service.notifications.deliver(db,recipients,workspace,booking['version'],'meeting.reminder')
            db.execute('UPDATE meeting_reminders SET status=? WHERE booking_id=?',('sent' if recipients else 'skipped',id))
        return True

    def start(self):
        if self.thread and self.thread.is_alive():return
        self.service.prepare();self.stop.clear()
        def work():
            while not self.stop.is_set():
                try:progress=self.process_one()
                except Exception as exc:
                    logging.getLogger(__name__).warning('Meeting reminder worker error: %s',type(exc).__name__)
                    progress=False
                self.stop.wait(0.05 if progress else 20)
        self.thread=threading.Thread(target=work,name='atlas-meeting-reminders',daemon=True)
        self.thread.start()

    def shutdown(self):
        self.stop.set()
        if self.thread:self.thread.join(timeout=15)

    def register(self,app):
        previous=app.router.lifespan_context
        @asynccontextmanager
        async def lifespan(application):
            async with previous(application):
                await run_in_threadpool(self.start)
                try:yield
                finally:await run_in_threadpool(self.shutdown)
        app.router.lifespan_context=lifespan
