"""Synthetic queue benchmark; no school files, browser subscriptions or network I/O.

Run: python -m tools.benchmark_web_push --accounts 3450 --bursts 5
Measures fanout storage and queue draining, not provider latency or real delivery.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import statistics
from tempfile import TemporaryDirectory
import time


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--accounts',type=int,default=3450)
    parser.add_argument('--bursts',type=int,default=5)
    args=parser.parse_args()
    if not 1<=args.accounts<=10000 or not 1<=args.bursts<=20:parser.error('accounts: 1..10000; bursts: 1..20')
    with TemporaryDirectory(prefix='atlas-push-benchmark-') as folder:
        os.environ['TIMETABLE_DB']=str(Path(folder)/'synthetic.db')
        os.environ['ATLAS_WEB_PUSH_ENABLED']='1'
        os.environ['ATLAS_WEB_PUSH_SUBJECT']='mailto:benchmark@example.org'
        from backend import api
        from backend.web_push import PushService
        from backend.seed import initial_state
        state=initial_state('demo')
        stamp=time.time()
        permissions=json.dumps(dict(workspaces=['demo'],grants=[]))
        accounts=[(f'u{i}',f'user{i}','Synthetic account','student',1,1,'disabled',permissions) for i in range(args.accounts)]
        users=[api.access.public(row) for row in accounts]
        with api.connect() as db:
            api.access.tables(db);api.notifications.tables(db)
            db.execute('INSERT INTO workspaces VALUES (?,?,?)',('demo',state.revision,state.model_dump_json()))
            db.executemany('INSERT INTO accounts VALUES (?,?,?,?,?,?,?,?)',accounts)
            db.executemany('INSERT INTO push_devices VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',[
                (f'd{i}',f'h{i}',f'https://fcm.googleapis.com/fcm/send/synthetic-{i}','unused','unused',f'u{i}','unused',1,1,'Synthetic device',stamp,stamp,stamp+86400,1,None,'enabled',None) for i in range(args.accounts)])
        timings=[]
        for i in range(args.bursts):
            started=time.perf_counter()
            with api.access.lock,api.connect() as db:api.notifications.deliver(db,users,'demo',i,'message.received')
            timings.append((time.perf_counter()-started)*1000)
        with api.connect() as db:
            pending=db.execute("SELECT count(*) FROM push_outbox WHERE status='queued'").fetchone()[0]
            notices=db.execute('SELECT count(*) FROM notifications').fetchone()[0]
        clock=time.time()+3
        service=PushService(api.access,send=lambda *_:(201,None),clock=lambda:clock)
        with service.transaction() as db:service.config(db)
        def drain(_):
            count=0
            while service.process_one():count+=1
            return count
        started=time.perf_counter()
        with ThreadPoolExecutor(max_workers=4) as pool:processed=sum(pool.map(drain,range(4)))
        elapsed=time.perf_counter()-started
        with api.connect() as db:
            sent=db.execute("SELECT count(*) FROM push_outbox WHERE status='sent'").fetchone()[0]
            rows=db.execute('SELECT count(*) FROM push_outbox').fetchone()[0]
        assert pending==processed==sent==rows==args.accounts and notices==args.accounts*args.bursts
        print(json.dumps(dict(accounts=args.accounts,devices=args.accounts,bursts=args.bursts,
            inboxRows=notices,outboxRows=rows,producerMedianMs=round(statistics.median(timings),2),
            producerMaxMs=round(max(timings),2),drainSeconds=round(elapsed,3),
            simulatedDeliveriesPerSecond=round(sent/elapsed,1),network=False)))


if __name__=='__main__':main()
