"""Bounded public CMS reads using synthetic data, no real school DB or network."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from time import perf_counter
import json, os, statistics, tempfile

def main():
    with tempfile.TemporaryDirectory(prefix='atlas-school-site-') as directory:
        target=Path(directory)/'benchmark.db';os.environ.update(TIMETABLE_DB=str(target),TIMETABLE_REQUIRE_AUTH='1',ATLAS_WEB_PUSH_ENABLED='0',ATLAS_BACKUP_DIR='')
        from backend import api
        from backend.school_site import stamp
        from backend.school_site_models import Content,SiteData
        from tools.create_admin import create_first_admin
        from fastapi.testclient import TestClient
        if api.DB.resolve()!=target.resolve():raise RuntimeError('Start in a fresh process with an isolated DB.')
        create_first_admin('admin','Synthetic admin','Synthetic-CMS-benchmark!')
        before=api.read('demo').model_dump_json();api.school_site.prepare();when=stamp()
        site=SiteData(title='Synthetic school',intro='Public information').model_dump_json()
        with api.connect() as db:
            db.execute('INSERT INTO school_site_config VALUES (?,1,?,?,1)',('demo',site,site))
            for i in range(3000):
                data=Content(slug=f'page-{i}',kind='news' if i%2 else 'page',title=f'Synthetic public material {i}',summary='Synthetic information',blocks=[dict(kind='text',text=('Example paragraph. '*250))]).model_dump_json()
                db.execute('INSERT INTO school_site_entries(id,workspace,version,slug,kind,archived,created,updated,document,published,published_at,published_search,draft_search) VALUES (?,?,1,?,?,0,?,?,?,?,?,?,?)',(f'item-{i}','demo',f'page-{i}','news' if i%2 else 'page',when,when,data,data,when,f'synthetic public material {i}',data.casefold()))
        clients=[TestClient(api.app) for _ in range(32)]
        def flow(pair):
            i,c=pair;times=[];sizes=[]
            for _ in range(4):
                for route in ['/school/demo','/school/demo?kind=news','/school/demo?q=material+29',f'/school/demo/p/page-{i}']:
                    start=perf_counter();r=c.get(route);times.append((perf_counter()-start)*1000);r.raise_for_status();sizes.append(len(r.content))
                    assert '<script' not in r.text and r.headers['cache-control']=='no-store'
            return times,sizes
        start=perf_counter();times=[];sizes=[]
        with ThreadPoolExecutor(16) as pool:
            for elapsed,lengths in pool.map(flow,enumerate(clients)):times.extend(elapsed);sizes.extend(lengths)
        elapsed=perf_counter()-start
        assert api.read('demo').model_dump_json()==before
        for c in clients:c.close()
        print(json.dumps(dict(scope='In-process TestClient; excludes TCP/TLS, browser rendering, image delivery and sustained production traffic',materials=3000,clients=32,parallelFlows=16,requests=len(times),errors=0,p50Ms=round(statistics.median(times),2),p95Ms=round(sorted(times)[int(len(times)*.95)],2),maxMs=round(max(times),2),elapsedSeconds=round(elapsed,2),maxHtmlBytes=max(sizes),schoolStateUnchanged=True),indent=2))

if __name__=='__main__':main()
