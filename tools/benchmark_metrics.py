"""Synthetic in-process ASGI overhead measurement, never opens school databases.

python -m tools.benchmark_metrics --requests 20000 --repeats 5
Not an HTTP load test or a prediction for the target school server.
"""
import argparse
import asyncio
import gc
import json
import statistics
import time
import tracemalloc

from backend.server_metrics import MetricsMiddleware, RequestMetrics


async def endpoint(scope, receive, send):
    await send({'type': 'http.response.start', 'status': 200, 'headers': []})
    await send({'type': 'http.response.body', 'body': b'ok'})


async def receive(): return {'type': 'http.disconnect'}
async def send(message): pass


async def batch(app, count):
    scope = {'type': 'http', 'path': '/api/synthetic'}
    start = time.perf_counter()
    for _ in range(count):
        await app(scope, receive, send)
    return (time.perf_counter()-start)*1e6/count


async def measure(count, repeats):
    plain, measured = [], []
    for iteration in range(repeats):
        metrics = RequestMetrics()
        middleware = MetricsMiddleware(endpoint, metrics)
        await batch(middleware, 1000)
        cases = [(endpoint, plain), (middleware, measured)]
        if iteration % 2: cases.reverse()
        for app, results in cases: results.append(await batch(app, count))
    gc.collect()
    tracemalloc.start()
    # Advance a synthetic clock to allocate and replace all time buckets.
    tick = [1000.]
    metrics = RequestMetrics(lambda: tick[0])
    for _ in range(count):
        start = metrics.begin(); tick[0] += 1; metrics.finish(start, 200)
    current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return dict(requestsPerRun=count, repeats=repeats, plainMicroseconds=plain,
                measuredMicroseconds=measured,
                medianAddedMicroseconds=statistics.median(measured)-statistics.median(plain),
                retainedBytes=current, peakBytes=peak, bucketSlots=len(metrics.buckets),
                scope='Synthetic ASGI only; no network, authentication, database or school data.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--requests', type=int, default=20000)
    parser.add_argument('--repeats', type=int, default=5)
    args = parser.parse_args()
    if not 1000 <= args.requests <= 1000000 or not 1 <= args.repeats <= 20:
        parser.error('requests: 1000–1000000; repeats: 1–20')
    print(json.dumps(asyncio.run(measure(args.requests, args.repeats)), indent=2))
