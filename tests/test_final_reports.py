from backend import api
from test_access import client,enable,create,signed  # noqa: F401
from test_journal import entry,lesson,write,graded,DAY
from test_journal_periods import period,close,action,period_clock  # noqa: F401
from test_final_grades import BASE,prepare,sheet,batch,propose,seeded,scale,report,publish,opts,stop_gradebook_jobs  # noqa: F401


def test_old_database_gains_report_tables_on_first_read(client):
    enable(client);assert opts(client)['admin']
    with api.connect() as db:db.execute('DROP TABLE grading_report_settings')
    # An upgraded database still carries the older migration marker; the first read must create the missing table.
    assert client.get(BASE+'/options?workspace=demo').status_code==200
    with api.connect() as db:assert db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='grading_report_settings'").fetchone()
