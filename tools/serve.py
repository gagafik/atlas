"""Single-process server entry point; requires an existing active administrator."""
import argparse
import os


def verify_ready():
    from backend import api
    with api.access.lock,api.access.connect() as db:
        api.access.tables(db)
        if not db.execute("SELECT 1 FROM accounts WHERE active=1 AND role='admin' LIMIT 1").fetchone():
            raise ValueError('Создайте администратора: python -m tools.create_admin admin --name "School administrator"')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check',action='store_true')
    args=parser.parse_args()
    os.environ['TIMETABLE_REQUIRE_AUTH']='1'
    try:verify_ready()
    except ValueError as exc:parser.exit(2,str(exc)+'\n')
    if args.check:
        print('Server configuration is ready; authentication is required.');return
    import uvicorn
    uvicorn.run('backend.api:app',host='0.0.0.0',port=8000,workers=1,proxy_headers=True,
                forwarded_allow_ips=os.environ.get('FORWARDED_ALLOW_IPS','127.0.0.1'),timeout_graceful_shutdown=30)


if __name__=='__main__':main()
