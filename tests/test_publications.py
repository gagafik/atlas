from datetime import date, datetime, timedelta, timezone
import pytest
from fastapi.testclient import TestClient
from backend import api
from backend.models import Placement, DatedLesson, TeacherAbsence, Commitment
from backend.seed import initial_state
from backend.publications import PublicationRequest, public_document


def fixture():
    s=initial_state('demo'); s.events=[]
    s.placements=[Placement(id='eng',workloadId='w0',day=0,period=1,roomId='r0'),Placement(id='math',workloadId='w4',day=1,period=2,roomId='r1')]
    return s


def options(**values):
    return PublicationRequest.model_validate(dict(revision=0,name='Для 10A',entityIds=['c-demo-a'],cycleIds=['weekly'])|values)


def test_only_selected_document_fields_are_published_and_escaped():
    s=fixture();s.subjects[2].name='<script>alert(1)</script>'
    document, metadata=public_document(s,options(showTeachers=False,showRooms=False))
    assert '<script>' not in document and '&lt;script&gt;' in document
    assert s.students[0].name not in document and s.teachers[2].name not in document
    assert s.classes[1].name not in document and 'rosterStatus' not in document and 'sourceCells' not in document
    assert metadata['records']==1 and metadata['rows']==2


def test_dated_snapshot_contains_cancellations_and_bookings_without_private_notes():
    s=fixture();day=date(2026,8,17)
    s.datedLessons=[DatedLesson(id='cancel',name='Private reason',kind='cancel',onDate=day,sourceDate=day,placementId='eng')]
    s.absences=[TeacherAbsence(id='private',name='Personal medical detail',teacherId='t2',start=day,end=day)]
    s.commitments=[Commitment(id='book',name='Кружок',onDate=day,startTime='17:00',endTime='18:00',roomIds=['r0'],groupIds=['g-a'],note='Private meeting note')]
    document,meta=public_document(s,options(kind='dated',firstDate=day,lastDate=day))
    assert 'Отменено' in document and 'Кружок' in document and 'Бронирование' in document
    assert 'Private' not in document and 'Personal' not in document and meta['rows']==2


def test_publication_selection_and_date_bounds_are_explicit():
    for patch in [dict(entityIds=[]),dict(entityIds=['c-demo-a','c-demo-a']),dict(cycleIds=[]),dict(kind='dated'),dict(kind='dated',firstDate='2026-08-17',lastDate='2026-10-01'),dict(expiresInDays=0)]:
        with pytest.raises(ValueError): options(**patch)
    with pytest.raises(ValueError): public_document(fixture(),options(entityIds=['absent']))
    with pytest.raises(ValueError): public_document(fixture(),options(kind='dated',firstDate='2026-07-01',lastDate='2026-07-02'))


def prepare(tmp_path,monkeypatch):
    monkeypatch.setattr(api,'DB',tmp_path/'publications.db')
    client=TestClient(api.app);client.get('/api/state?workspace=demo')
    return client,api.save('demo',fixture())


def test_preview_publish_snapshot_and_revoke_are_isolated_from_draft(tmp_path,monkeypatch):
    client,s=prepare(tmp_path,monkeypatch)
    request=options(revision=s.revision).model_dump(mode='json')
    p=client.post('/api/publications/preview?workspace=demo',json=request).json()
    assert 'html' in p and client.get('/api/publications?workspace=demo').json()==[]
    assert client.get('/published/'+p['token']).status_code==404
    publish=dict(revision=s.revision,token=p['token'])
    result=client.post('/api/publications?workspace=demo',json=publish)
    assert result.status_code==200;entry=result.json()
    public=client.get(entry['url'])
    assert public.status_code==200 and public.text==p['html'] and public.headers['cache-control']=='no-store'
    assert "default-src 'none'" in public.headers['content-security-policy']
    assert client.post('/api/publications?workspace=demo',json=publish).status_code==404
    assert api.read('demo')==s
    s.subjects[2].name='Updated draft subject';api.save('demo',s)
    assert client.get(entry['url']).text==p['html']
    assert client.get('/api/publications?workspace=school').json()==[]
    assert client.delete('/api/publications/'+entry['id']+'?workspace=school').status_code==404
    assert client.delete('/api/publications/'+entry['id']+'?workspace=demo').status_code==200
    assert client.get(entry['url']).status_code==404
    assert client.get('/api/publications?workspace=demo').json()[0]['status']=='revoked'


def test_expired_previews_links_and_stale_revisions_cannot_publish(tmp_path,monkeypatch):
    client,s=prepare(tmp_path,monkeypatch)
    request=options(revision=s.revision).model_dump(mode='json')
    p=client.post('/api/publications/preview?workspace=demo',json=request).json()
    with api.connect() as db: db.execute('UPDATE publication_previews SET created=?',((datetime.now(timezone.utc)-timedelta(hours=1)).isoformat(),))
    assert client.post('/api/publications?workspace=demo',json=dict(revision=s.revision,token=p['token'])).status_code==404
    p=client.post('/api/publications/preview?workspace=demo',json=request).json()
    entry=client.post('/api/publications?workspace=demo',json=dict(revision=s.revision,token=p['token'])).json()
    with api.connect() as db: db.execute('UPDATE publications SET expires=?',((datetime.now(timezone.utc)-timedelta(hours=1)).isoformat(),))
    assert client.get(entry['url']).status_code==404
    p=client.post('/api/publications/preview?workspace=demo',json=request).json();api.save('demo',s)
    assert client.post('/api/publications?workspace=demo',json=dict(revision=s.revision,token=p['token'])).status_code==409
    assert client.post('/api/publications/preview?workspace=demo',json=request).status_code==409
