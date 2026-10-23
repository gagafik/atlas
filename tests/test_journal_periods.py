from datetime import date,datetime,timedelta,timezone
from uuid import uuid4
import pytest
from backend import api,journal_periods
from backend.models import DatedLesson,Event
from test_access import client,enable,create,signed
from test_journal import entry,lesson,write,graded,DAY
from test_journal_coverage import known

BASE='/api/journal/periods'


@pytest.fixture(autouse=True)
def period_clock(monkeypatch):
    stamp=datetime(2026,10,14,10,tzinfo=timezone.utc)
    monkeypatch.setattr(journal_periods,'now',lambda:stamp)
    monkeypatch.setattr('backend.journal_coverage.now',lambda:stamp)

def period(client,**extra):
    id=uuid4().hex
    response=client.put(BASE+'/'+id+'?workspace=demo',json=dict(name='Первая четверть',start=DAY,end=DAY,kind='quarter',version=0,reason='Создание периода')|extra)
    assert response.status_code==200,response.text
    return response.json()

def action(client,p,op,**extra):
    body=dict(version=p['version'])
    if op!='preview-close':body['reason']='Завершение проверки'
    return client.post(BASE+'/'+p['id']+'/'+op+'?workspace=demo',json=body|extra)

def close(client,p,ack=True):
    preview=action(client,p,'preview-close');assert preview.status_code==200,preview.text
    result=action(client,p,'close',previewId=preview.json()['previewId'],acknowledgeIssues=ack)
    assert result.status_code==200,result.text
    return result.json()

def archive(client,p,version=None,**params):
    return client.get(f"{BASE}/{p['id']}/closures/{version or p['version']}/lessons",params=dict(workspace='demo')|params)

def test_preview_requires_explicit_issues_ack_and_freezes_only_publication(client):
    known();record=write(client,graded(entry(client,lesson(client)['lessonKey'])),publish=True).json()
    record['document']['content']['topic']='Secret draft';write(client,record)
    p=period(client);preview=action(client,p,'preview-close').json()
    assert preview['counts']['missing']==1 and preview['draftChanges']==1 and preview['requiresAcknowledgement']
    assert action(client,p,'close',previewId=preview['previewId']).status_code==422
    closed=action(client,p,'close',previewId=preview['previewId'],acknowledgeIssues=True).json()
    assert closed['status']=='closed' and closed['version']==2
    rows=archive(client,closed).json();assert len(rows['items'])==1
    saved=archive(client,closed,lessonKey=record['lessonKey']).json()
    assert saved['document']['content']['topic']=='Тема урока' and saved['publishedVersion']==1
    locked=entry(client,record['lessonKey']);assert not locked['writable'] and not locked['canWithdraw']
    assert locked['periodLocks'][0]['id']==p['id']
    assert write(client,locked).status_code==403
    assert client.post('/api/journal/withdraw?workspace=demo',json=dict(lessonKey=locked['lessonKey'],version=locked['version'])).status_code==403
    absent=entry(client,lesson(client,'Математика')['lessonKey']);assert not absent['writable'] and write(client,absent).status_code==403

def test_reopen_requires_reason_keeps_each_archive_and_permanent_period_boundaries(client):
    record=write(client,graded(entry(client,lesson(client)['lessonKey'])),publish=True).json()
    p=close(client,period(client))
    assert action(client,p,'reopen',reason=' ').status_code==422
    reopened=action(client,p,'reopen',reason='Исправление ошибочной оценки').json()
    changed=entry(client,record['lessonKey']);changed['document']['content']['records'][0]['grades'][0]['score']=9
    write(client,changed,publish=True).raise_for_status()
    final=close(client,reopened)
    old=archive(client,final,version=2,lessonKey=record['lessonKey']).json()
    current=archive(client,final,lessonKey=record['lessonKey']).json()
    assert old['document']['content']['records'][0]['grades'][0]['score']==8
    assert current['document']['content']['records'][0]['grades'][0]['score']==9
    history=client.get(f"{BASE}/{p['id']}/history?workspace=demo").json()['items']
    assert [x['action'] for x in history]==['closed','reopened','closed','created']
    assert history[1]['reason']=='Исправление ошибочной оценки'
    reopened=action(client,final,'reopen').json()
    response=client.put(f"{BASE}/{p['id']}?workspace=demo",json=dict(version=reopened['version'],name='Changed',start=DAY,end=DAY,reason='Изменить границы'))
    assert response.status_code==409

def test_roles_archive_scopes_and_current_permission_revocation(client):
    enable(client)
    for role in ['teacher','parent','student','editor']:create(client,role)
    create(client,'teacher',login='unrelated',grants=[dict(workspace='demo',view='teacher',entityIds=['t1'])])
    record=write(client,graded(entry(client,lesson(client)['lessonKey'])),publish=True).json()
    p=close(client,period(client));teacher=signed('teacher');other=signed('unrelated')
    assert archive(teacher,p,lessonKey=record['lessonKey']).status_code==200
    assert archive(other,p).json()['items']==[] and archive(other,p,lessonKey=record['lessonKey']).status_code==404
    assert action(teacher,p,'reopen').status_code==403
    assert teacher.get(f"{BASE}/{p['id']}/history?workspace=demo").status_code==403
    assert 'reason' not in teacher.get(f"{BASE}/{p['id']}/closures?workspace=demo").json()['items'][0]
    for role in ['parent','student','editor']:
        account=signed(role)
        assert account.get(BASE+'?workspace=demo').status_code==403
        assert archive(account,p).status_code==403
    assert teacher.get(BASE+'?workspace=school').status_code==403
    with api.connect() as db:db.execute("UPDATE accounts SET active=0 WHERE login='teacher'")
    assert archive(teacher,p,lessonKey=record['lessonKey']).status_code==401

def test_period_preview_stale_on_journal_state_period_or_account_change(client,monkeypatch):
    p=period(client);preview=action(client,p,'preview-close').json()
    write(client,entry(client,lesson(client)['lessonKey']))
    assert action(client,p,'close',previewId=preview['previewId'],acknowledgeIssues=True).status_code==409
    p=period(client);preview=action(client,p,'preview-close').json();actual=journal_periods.now
    monkeypatch.setattr(journal_periods,'now',lambda:actual()+timedelta(minutes=16))
    assert action(client,p,'close',previewId=preview['previewId'],acknowledgeIssues=True).status_code==409
    monkeypatch.setattr(journal_periods,'now',actual)
    # An unrelated timetable save does not change the result of the period.
    p=period(client);preview=action(client,p,'preview-close').json();api.save('demo',api.read('demo'))
    assert action(client,p,'close',previewId=preview['previewId'],acknowledgeIssues=True).status_code==200

def test_period_close_ignores_journal_saves_outside_the_period(client):
    known();p=period(client);preview=action(client,p,'preview-close').json()
    other='2026-08-24';write(client,graded(entry(client,lesson(client,day=other)['lessonKey'],day=other)),publish=True).raise_for_status()
    closed=action(client,p,'close',previewId=preview['previewId'],acknowledgeIssues=True);assert closed.status_code==200,closed.text
    assert client.get(f"{BASE}/{p['id']}/closures?workspace=demo").json()['items'][0]['counts']==preview['counts']

def test_timetable_change_affecting_period_requires_new_preview(client):
    known();p=period(client);preview=action(client,p,'preview-close').json()
    state=api.read('demo');state.events=[Event(id='close',name='Closed',start=date(2026,8,17),end=date(2026,8,17),kind='holiday',effect='cancel',classIds=['c-demo-a'])];api.save('demo',state)
    assert action(client,p,'close',previewId=preview['previewId'],acknowledgeIssues=True).status_code==409
    assert client.get(BASE+'?workspace=demo').json()['items'][0]['status']=='open'
    assert close(client,p)['status']=='closed'

def test_preview_table_gets_school_revision_column_once(client):
    with api.connect() as db:
        db.execute('DROP TABLE IF EXISTS journal_period_previews');journal_periods.tables(db);journal_periods.tables(db)
        assert [r[1] for r in db.execute('PRAGMA table_info(journal_period_previews)')].count('school_revision')==1

def test_future_period_and_year_bounds_and_version_validation(client):
    p=period(client,end='2027-06-11');preview=action(client,p,'preview-close').json()
    assert not preview['canClose']
    assert action(client,p,'close',previewId=preview['previewId'],acknowledgeIssues=True).status_code==422
    assert action(client,p,'preview-close',version=12).status_code==409
    response=client.put(BASE+'/'+uuid4().hex+'?workspace=demo',json=dict(version=0,name='Outside',start='2026-01-01',end=DAY,reason='Неверные даты'))
    assert response.status_code==422
    assert client.get(f"{BASE}/{p['id']}/closures/99/lessons?workspace=demo").status_code==404

def test_overlapping_periods_and_moved_lessons_cannot_bypass_lock(client):
    record=write(client,graded(entry(client,lesson(client)['lessonKey'])),publish=True).json()
    first=close(client,period(client));second=close(client,period(client,name='Семестр',kind='semester'))
    with api.connect() as db:
        assert db.execute('SELECT count(*) FROM journal_period_documents').fetchone()[0]==1
        assert db.execute('SELECT count(*) FROM journal_period_snapshots').fetchone()[0]==2
    action(client,first,'reopen').raise_for_status()
    assert not entry(client,record['lessonKey'])['writable']
    state=api.read('demo');state.datedLessons=[DatedLesson(id='move',name='Move',kind='change',placementId='eng',sourceDate=date(2026,8,17),onDate=date(2026,8,18))];api.save('demo',state)
    moved=entry(client,record['lessonKey'],syncRoster=True)
    assert not moved['writable'] and write(client,moved,syncRoster=True).status_code==403
    # Changing the subject creates a new journal, but its source day stays locked.
    state=api.read('demo');state.datedLessons[0].subjectId='sub0';api.save('demo',state)
    rows=client.get('/api/journal/day?workspace=demo&day=2026-08-18').json()['lessons']
    fresh=next(r for r in rows if not r['archived'])
    assert not entry(client,fresh['lessonKey'],day='2026-08-18')['writable']
    action(client,second,'reopen').raise_for_status()
    assert entry(client,fresh['lessonKey'],day='2026-08-18')['writable']

def test_close_between_entry_read_and_save_is_rechecked_in_write_transaction(client,monkeypatch):
    p=period(client);record=entry(client,lesson(client)['lessonKey']);original=api.journal.entry
    def close_after_read(*args,**kwargs):
        result=original(*args,**kwargs)
        close(client,p)
        return result
    monkeypatch.setattr(api.journal,'entry',close_after_read)
    assert write(client,record,publish=True).status_code==403
    with api.connect() as db:assert db.execute('SELECT count(*) FROM journal_entries').fetchone()[0]==0

def test_failed_close_rolls_back_snapshots_lock_and_history(client,monkeypatch):
    record=write(client,graded(entry(client,lesson(client)['lessonKey'])),publish=True).json();p=period(client)
    preview=action(client,p,'preview-close').json()
    def fail(*args):raise RuntimeError('Audit unavailable')
    monkeypatch.setattr(api.journal_periods,'record',fail)
    with pytest.raises(RuntimeError):action(client,p,'close',previewId=preview['previewId'],acknowledgeIssues=True)
    with api.connect() as db:
        assert db.execute('SELECT count(*) FROM journal_period_snapshots').fetchone()[0]==0
        assert db.execute('SELECT count(*) FROM journal_period_closures').fetchone()[0]==0
    assert entry(client,record['lessonKey'])['writable']

def test_archive_pages_remain_stable_after_live_journal_withdrawal(client):
    from unittest.mock import patch
    # Marks for future lessons are refused in production; the school date moves past the whole term here.
    with patch('backend.journal.today',return_value=date(2027,3,1)):
        for week in range(27):
            day=(date(2026,8,17)+timedelta(days=7*week)).isoformat()
            for row in client.get('/api/journal/day',params=dict(workspace='demo',day=day)).json()['lessons']:
                record=entry(client,row['lessonKey'],day=day);write(client,graded(record),publish=True).raise_for_status()
    p=period(client,end='2027-02-15')
    # Future dates are rejected in production; advance both clocks in this fixture.
    from datetime import datetime,timezone
    clock=datetime(2027,3,1,tzinfo=timezone.utc)
    with patch('backend.journal_periods.now',return_value=clock),patch('backend.journal_coverage.now',return_value=clock):p=close(client,p)
    first=archive(client,p).json();second=archive(client,p,offset=first['nextOffset']).json()
    assert len(first['items'])==50 and len(second['items'])==4 and second['nextOffset'] is None
    assert not {r['lessonKey'] for r in first['items']}&{r['lessonKey'] for r in second['items']}
    action(client,p,'reopen').raise_for_status()
    key=first['items'][0]['lessonKey'];record=entry(client,key)
    client.post('/api/journal/withdraw?workspace=demo',json=dict(lessonKey=key,version=record['version'])).raise_for_status()
    assert archive(client,p,lessonKey=key).status_code==200


def test_close_preview_is_owned_and_versioned_and_edit_audited(client):
    enable(client);create(client,'admin',login='second-admin');other=signed('second-admin')
    p=period(client);preview=action(client,p,'preview-close').json()
    assert action(other,p,'close',previewId=preview['previewId'],acknowledgeIssues=True).status_code==409
    payload=dict(version=p['version'],name='Уточнённая четверть',start=DAY,end=DAY,kind='quarter',reason='Уточнение названия')
    changed=client.put(f"{BASE}/{p['id']}?workspace=demo",json=payload);assert changed.status_code==200
    assert client.put(f"{BASE}/{p['id']}?workspace=demo",json=payload).status_code==409
    assert action(client,changed.json(),'close',previewId=preview['previewId'],acknowledgeIssues=True).status_code==409
    assert client.get(f"{BASE}/{p['id']}/history?workspace=demo").json()['items'][0]['action']=='updated'


def test_archive_state_and_locks_survive_full_backup_restore(client,tmp_path,monkeypatch):
    from tools.backup_school import create_backup
    record=write(client,graded(entry(client,lesson(client)['lessonKey'])),publish=True).json();state=api.read('demo')
    p=close(client,period(client))
    with api.connect() as db:
        saved=db.execute('SELECT state_document FROM journal_period_closures').fetchone()[0]
        assert api.State.model_validate_json(saved)==state
    destination=tmp_path/'period-backup';source=api.DB;create_backup(source,destination)
    monkeypatch.setattr(api,'DB',destination/source.name)
    assert not entry(client,record['lessonKey'])['writable']
    assert archive(client,p,lessonKey=record['lessonKey']).json()['document']==record['document']


def test_archive_rechecks_access_after_read_before_return(client,monkeypatch):
    enable(client);create(client,'teacher');teacher=signed('teacher')
    record=write(client,graded(entry(client,lesson(client)['lessonKey'])),publish=True).json();p=close(client,period(client))
    original=api.journal_periods.finish_archive
    def revoke(*args):
        with api.connect() as db:db.execute("UPDATE accounts SET active=0 WHERE login='teacher'")
        return original(*args)
    monkeypatch.setattr(api.journal_periods,'finish_archive',revoke)
    response=archive(teacher,p,lessonKey=record['lessonKey'])
    assert response.status_code==409 and 'Тема урока' not in response.text
