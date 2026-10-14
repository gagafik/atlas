from datetime import date
import pytest
from backend import api
from backend.models import Course, CourseRequest, Group
from test_access import client, enable, create, signed


def setup(client):
    state=api.read('demo')
    state.courses=[Course(id='math-course',name='Математика',sections=[dict(groupId='g-math')]),Course(id='physics-course',name='Физика',sections=[dict(groupId='g-core')])]
    api.save('demo',state)
    enable(client);create(client,'student');create(client,'parent');create(client,'teacher');create(client,'editor')
    return signed('student'),signed('parent')


def configure(client,**values):
    config=client.get('/api/intake/config?workspace=demo').json()
    result=client.put('/api/intake/config?workspace=demo',json=config|dict(revision=api.read('demo').revision,open=True,courseIds=['math-course','physics-course'])|values)
    assert result.status_code==200,result.text
    return result.json()


def personal(pupil):
    result=pupil.get('/api/intake/student?workspace=demo&studentId=s1')
    assert result.status_code==200,result.text
    return result.json()


def submit(pupil,choices=None,**fields):
    data=personal(pupil)
    return pupil.put('/api/intake/student?workspace=demo',json=dict(studentId='s1',revision=data['revision'],version=data['submission']['version'],configVersion=data['config']['version'],choices=choices if choices is not None else [dict(courseIds=['math-course','physics-course'])],note='Мой выбор')|fields)


def review(client,record,action='preview',**fields):
    return client.post('/api/intake/'+action+'?workspace=demo',json=dict(revision=api.read('demo').revision,studentId='s1',version=record['submission']['version'])|fields)


def test_pupil_preferences_review_apply_and_undo_do_not_assign_groups(client):
    pupil,parent=setup(client)
    assert not personal(pupil)['accepting']
    assert submit(pupil).status_code==422
    configure(client)
    before=api.read('demo')
    response=submit(pupil);assert response.status_code==200,response.text
    record=response.json()
    assert record['submission']['status']=='pending' and api.read('demo')==before
    assert personal(parent)['submission']['choices']==record['submission']['choices']
    preview=review(client,record);assert preview.status_code==200,preview.text
    assert preview.json()['canApply'] and api.read('demo')==before
    applied=client.post('/api/intake/apply?workspace=demo',json=dict(revision=before.revision,token=preview.json()['token']))
    assert applied.status_code==200,applied.text
    state=api.read('demo');assert state.groups==before.groups and len(state.courseRequests)==1
    assert state.courseRequests[0].courseIds==['math-course','physics-course']
    assert personal(pupil)['inPlan'] and personal(pupil)['submission']['status']=='accepted'
    assert client.post('/api/undo?workspace=demo',json=dict(revision=state.revision)).status_code==200
    assert not personal(pupil)['inPlan'] and personal(pupil)['submission']['status']=='accepted'


def test_submission_update_invalidates_review_and_rolls_back_state_history(client):
    pupil,parent=setup(client);configure(client)
    first=submit(pupil).json();preview=review(client,first).json()
    assert submit(parent,[dict(courseIds=['physics-course'])]).status_code==200
    before=api.read('demo').model_dump_json()
    with api.connect() as db:
        history=db.execute('SELECT COUNT(*) FROM history').fetchone()[0]
        audit=db.execute('SELECT COUNT(*) FROM audit').fetchone()[0]
    applied=client.post('/api/intake/apply?workspace=demo',json=dict(revision=api.read('demo').revision,token=preview['token']))
    assert applied.status_code==409
    assert api.read('demo').model_dump_json()==before
    with api.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM history').fetchone()[0]==history
        assert db.execute('SELECT COUNT(*) FROM audit').fetchone()[0]==audit


def test_role_scope_student_isolation_and_archived_pupil(client):
    pupil,parent=setup(client);configure(client)
    assert pupil.get('/api/intake/config?workspace=demo').status_code==403
    assert pupil.get('/api/intake/incoming?workspace=demo').status_code==403
    assert pupil.get('/api/intake/student?workspace=demo&studentId=s2').status_code==403
    assert signed('teacher').get('/api/intake/options?workspace=demo').status_code==403
    assert signed('editor').get('/api/intake/config?workspace=school').status_code==403
    raw=str(personal(pupil));assert 'Ученик 2' not in raw and 's2' not in raw
    state=api.read('demo');state.students[0].active=False;api.save('demo',state)
    assert not personal(pupil)['accepting']
    assert submit(pupil).status_code==422


def test_closed_changed_catalog_limits_and_duplicate_choices(client):
    pupil,_=setup(client);cfg=configure(client,maxRequests=1,maxAlternatives=1)
    assert submit(pupil).status_code==422
    assert submit(pupil,[dict(courseIds=['math-course']),dict(courseIds=['physics-course'])]).status_code==422
    assert submit(pupil,[dict(courseIds=['absent'])]).status_code==422
    configure(client,maxRequests=2,maxAlternatives=2)
    assert submit(pupil,[dict(courseIds=['math-course']),dict(courseIds=['math-course'])]).status_code==422
    assert submit(pupil,[dict(courseIds=['math-course'])],configVersion=cfg['version']).status_code==409
    configure(client,closesOn='2000-01-01')
    assert not personal(pupil)['accepting'] and submit(pupil).status_code==422


def test_return_resubmit_withdraw_and_preserve_manual_requests(client):
    pupil,_=setup(client);configure(client)
    first=submit(pupil).json()
    assert review(client,first,'reject').status_code==422
    assert review(client,first,'reject',comment='Уточните приоритет').status_code==200
    assert personal(pupil)['submission']['comment']=='Уточните приоритет'
    assert review(client,first,'preview').status_code==409
    record=submit(pupil,[dict(courseIds=['math-course'])]).json()
    state=api.read('demo');state.courseRequests=[CourseRequest(id='staff-choice',studentId='s2',courseIds=['physics-course'])];api.save('demo',state)
    preview=review(client,record).json()
    assert client.post('/api/intake/apply?workspace=demo',json=dict(revision=api.read('demo').revision,token=preview['token'])).status_code==200
    empty=submit(pupil,[]).json();preview=review(client,empty).json()
    assert client.post('/api/intake/apply?workspace=demo',json=dict(revision=api.read('demo').revision,token=preview['token'])).status_code==200
    assert [r.id for r in api.read('demo').courseRequests]==['staff-choice']


def test_locked_accepted_request_and_stale_personal_form(client):
    pupil,_=setup(client);configure(client)
    record=submit(pupil,[dict(courseIds=['math-course'])]).json();preview=review(client,record).json()
    assert client.post('/api/intake/apply?workspace=demo',json=dict(revision=api.read('demo').revision,token=preview['token'])).status_code==200
    state=api.read('demo');state.courseRequests[0].lockedGroupId='g-math';api.save('demo',state)
    assert review(client,record).status_code==422
    assert submit(pupil,version=0).status_code==409
