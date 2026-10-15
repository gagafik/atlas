"""Bounded, process-local aggregates. Never retain request paths or identities."""
from bisect import bisect_left
import asyncio
from contextlib import closing
from datetime import datetime, timezone
import math
import shutil
import sqlite3
import threading
import time

WINDOW = 300
BOUNDS_MS = (50, 100, 250, 500, 1000, 2000, 5000, 10000, 30000, math.inf)
EXCLUDED = frozenset(('/api/health', '/api/ready', '/api/access/operations'))


class Sample:
    __slots__ = ('second', 'count', 'server_errors', 'client_errors', 'busy',
                 'limited', 'incomplete', 'elapsed', 'maximum', 'histogram')

    def __init__(self, second):
        self.second = second
        self.count = self.server_errors = self.client_errors = 0
        self.busy = self.limited = self.incomplete = 0
        self.elapsed = self.maximum = 0.0
        self.histogram = [0] * len(BOUNDS_MS)


class RequestMetrics:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.started = clock()
        self.started_at = datetime.now(timezone.utc).isoformat()
        self.lock = threading.Lock()
        self.buckets = [None] * WINDOW
        self.in_flight = self.total = self.total_errors = 0

    def begin(self):
        with self.lock:
            self.in_flight += 1
        return self.clock()

    def finish(self, started, status, incomplete=False):
        now = self.clock()
        elapsed = max(0, (now - started) * 1000)
        second = int(now)
        with self.lock:
            self.in_flight -= 1
            self.total += 1
            self.total_errors += int(status >= 500)
            bucket = self.buckets[second % WINDOW]
            if bucket is None or bucket.second != second:
                bucket = self.buckets[second % WINDOW] = Sample(second)
            bucket.count += 1
            bucket.server_errors += int(status >= 500)
            bucket.client_errors += int(400 <= status < 500)
            bucket.busy += int(status == 503)
            bucket.limited += int(status == 429)
            bucket.incomplete += int(incomplete)
            bucket.elapsed += elapsed
            bucket.maximum = max(bucket.maximum, elapsed)
            bucket.histogram[bisect_left(BOUNDS_MS, elapsed)] += 1

    def snapshot(self):
        now = self.clock()
        with self.lock:
            samples = [b for b in self.buckets if b is not None and int(now)-WINDOW < b.second <= int(now)]
            count = sum(b.count for b in samples)
            histogram = [sum(b.histogram[i] for b in samples) for i in range(len(BOUNDS_MS))]
            p95, accumulated = None, 0
            if count:
                for bound, number in zip(BOUNDS_MS, histogram):
                    accumulated += number
                    if accumulated >= math.ceil(count * .95):
                        p95 = bound
                        break
            seconds = min(WINDOW, max(1, now-self.started))
            return dict(startedAt=self.started_at, uptimeSeconds=round(max(0, now-self.started)),
                inFlight=self.in_flight, totalRequests=self.total, totalServerErrors=self.total_errors,
                window=dict(seconds=round(seconds, 1), requests=count,
                    requestsPerSecond=round(count/seconds, 2),
                    serverErrors=sum(b.server_errors for b in samples),
                    clientErrors=sum(b.client_errors for b in samples),
                    unavailable=sum(b.busy for b in samples), rateLimited=sum(b.limited for b in samples),
                    incomplete=sum(b.incomplete for b in samples),
                    averageMs=round(sum(b.elapsed for b in samples)/count, 2) if count else None,
                    maximumMs=round(max((b.maximum for b in samples), default=0), 2) if count else None,
                    p95UpperMs=p95 if p95 is not None and math.isfinite(p95) else None,
                    p95Overflow=p95 == math.inf))


class MetricsMiddleware:
    """Pure ASGI: measure through the last body chunk, without buffering it."""
    def __init__(self, app, metrics):
        self.app, self.metrics = app, metrics

    async def __call__(self, scope, receive, send):
        path = scope.get('path', '')
        if scope['type'] != 'http' or not path.startswith('/api/') or path in EXCLUDED:
            return await self.app(scope, receive, send)
        started = self.metrics.begin()
        status, completed, response_started = 500, False, False

        async def measured_send(message):
            nonlocal status, completed, response_started
            if message['type'] == 'http.response.start':
                status = message['status']
                response_started = True
            await send(message)
            if message['type'] == 'http.response.body' and not message.get('more_body', False) and not completed:
                completed = True
                self.metrics.finish(started, status)

        try:
            await self.app(scope, receive, measured_send)
        except asyncio.CancelledError:
            # Cancellation before headers does not prove an HTTP 500 was sent.
            if not response_started:
                status = 0
            raise
        finally:
            if not completed:
                self.metrics.finish(started, status, incomplete=True)


# Fixed allowlist. Read only existing indexed status columns, never job documents.
QUEUES = (
    ('generation', 'generation_queue', 'generation_queue_pending', ('queued', 'running', 'cancelling')),
    ('reports', 'grading_preparations', 'grading_job_pending', ('queued', 'running', 'cancelling')),
    ('push', 'push_outbox', 'push_outbox_due', ('queued', 'sending', 'failed')),
)
COUNT_LIMIT = 1000


class ResourceSnapshot:
    def __init__(self, clock=time.monotonic, ttl=10):
        self.clock, self.ttl = clock, ttl
        self.lock = threading.Lock()
        self.key, self.value, self.checked = None, None, -math.inf

    def snapshot(self, database):
        key = str(database.resolve())
        with self.lock:
            if key != self.key or self.clock()-self.checked >= self.ttl:
                self.value = self.collect(database)
                self.key, self.checked = key, self.clock()
            return dict(self.value, ageSeconds=round(max(0, self.clock()-self.checked), 1))

    def collect(self, database):
        result = dict(checkedAt=datetime.now(timezone.utc).isoformat(), disk=None, queues=None)
        try:
            usage = shutil.disk_usage(database.parent)
            ratio = usage.free / usage.total if usage.total else 0
            level = 'critical' if usage.free < 256*1024**2 or ratio < .01 else 'warning' if usage.free < 1024**3 or ratio < .1 else 'normal'
            result['disk'] = dict(freeBytes=usage.free, totalBytes=usage.total, freePercent=round(ratio*100, 1), level=level)
        except OSError:
            pass
        try:
            # A separate read-only connection cannot initialize or migrate school data.
            deadline = time.monotonic()+.25
            with closing(sqlite3.connect(database.resolve().as_uri()+'?mode=ro', uri=True, timeout=.1)) as db:
                db.set_progress_handler(lambda: int(time.monotonic() > deadline), 100)
                db.execute('BEGIN')
                queues = []
                for name, table, index, statuses in QUEUES:
                    exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
                    counts = {}
                    if exists:
                        for status in statuses:
                            count = db.execute(f'SELECT count(*) FROM (SELECT 1 FROM {table} INDEXED BY {index} WHERE status=? LIMIT ?)', (status, COUNT_LIMIT+1)).fetchone()[0]
                            counts[status] = dict(count=min(COUNT_LIMIT, count), capped=count > COUNT_LIMIT)
                    queues.append(dict(name=name, initialized=bool(exists), counts=counts))
                result['queues'] = queues
        except (OSError, sqlite3.Error):
            pass
        return result
