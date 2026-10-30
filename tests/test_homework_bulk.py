"""Bulk acceptance of submitted works: partial success, skip reasons, retries and the course-teacher rule."""
import json

from backend import api
from backend.models import DatedLesson
from test_access import client, create, signed
from test_homework import setup, get, answer, review, target, unread
from test_journal import DAY, entry, write

FIRST='00000000-0000-4000-8000-000000000001'
SECOND='00000000-0000-4000-8000-000000000002'


def overview(client, key):
    response=client.get(f'/api/journal/homework/{key}?workspace=demo')
    assert response.status_code==200,response.text
    return response.json()


def versions(client, key):
    return {s['studentId']:s['version'] for s in overview(client,key)['students']}


def bulk(client, key, assignment, items, request=FIRST, comment=''):
    return client.post(f'/api/journal/homework/{key}/review-bulk?workspace=demo',json=dict(
        requestId=request,assignment=assignment,comment=comment,items=[dict(studentId=s,version=v) for s,v in items]))


def submit(pupil, key, student, text='Решение ученика'):
    current=get(pupil,key,student)
    response=pupil.put(target(key,student),json=dict(studentId=student,version=current['version'],assignment=current['assignment'],action='submit',text=text,fileIds=[]))
    assert response.status_code==200,response.text
    return response.json()


def draft(pupil, key, student):
    current=get(pupil,key,student)
    response=pupil.put(target(key,student),json=dict(studentId=student,version=current['version'],assignment=current['assignment'],action='draft',text='Черновик',fileIds=[]))
    assert response.status_code==200,response.text
    return response.json()


def history(student='s1'):
    with api.connect() as db:
        return [r[0] for r in db.execute('SELECT action FROM homework_history WHERE student_id=? ORDER BY id',(student,))]


def test_bulk_accepts_submitted_and_reports_skipped(client):
    record,pupil,parent,teacher=setup(client);key=record['lessonKey'];other=signed('other-pupil')
    submit(pupil,key,'s1');submit(other,key,'s2')
    current=overview(teacher,key);assert current['canReview'] and current['submission']=='online'
    assert [s['status'] for s in current['students']]==['submitted','submitted']
    waiting=unread(parent);before=api.read('demo')
    response=bulk(teacher,key,current['assignment'],[(s['studentId'],s['version']) for s in current['students']],comment='Хорошая работа')
    assert response.status_code==200,response.text
    result=response.json();assert result['accepted']==['s1','s2'] and result['skipped']==[]
    assert [s['status'] for s in result['overview']['students']]==['accepted','accepted']
    work=get(parent,key);assert work['status']=='accepted'
    assert work['submitted']['review']['reviewer']==teacher.get('/api/auth/status').json()['user']['name']
    assert work['submitted']['review']['comment']=='Хорошая работа' and 'grading' not in work['submitted']
    assert history('s1')[-1]=='homework.reviewed' and history('s2')[-1]=='homework.reviewed'
    assert unread(parent)==waiting+1
    with api.connect() as db:
        assert db.execute("SELECT count(*) FROM audit WHERE action='homework.bulk_reviewed'").fetchone()[0]==1
        assert db.execute("SELECT count(*) FROM audit WHERE action='homework.reviewed'").fetchone()[0]==2
    assert entry(client,key)['document']['content']==record['document']['content']
    assert api.read('demo')==before


def test_bulk_skips_stale_returned_unsubmitted_and_changed(client):
    record,pupil,parent,teacher=setup(client);key=record['lessonKey'];other=signed('other-pupil')
    draft(pupil,key,'s1');sent=submit(pupil,key,'s1');draft(other,key,'s2')
    current=overview(teacher,key);assignment=current['assignment'];moved=versions(teacher,key)
    assert moved['s1']==sent['version']==2 and moved['s2']==1
    response=bulk(teacher,key,assignment,[('s1',1),('s2',moved['s2'])])
    assert response.status_code==200,response.text
    skipped={s['studentId']:s for s in response.json()['skipped']}
    assert response.json()['accepted']==[]
    assert 'изменилась' in skipped['s1']['reason'] and 'не отправлена' in skipped['s2']['reason']
    assert skipped['s1']['name']=='Ученик 1 (пример)'
    assert review(teacher,key,sent,'returned','Доработайте').status_code==200
    returned=bulk(teacher,key,assignment,[('s1',versions(teacher,key)['s1'])],request=SECOND)
    assert 'на доработке' in returned.json()['skipped'][0]['reason']
    resent=submit(pupil,key,'s1','Новое решение')
    # An accepted work is skipped, and the single review remains the way to look at it again.
    assert review(teacher,key,resent).status_code==200
    accepted=bulk(teacher,key,assignment,[('s1',versions(teacher,key)['s1'])],request='00000000-0000-4000-8000-000000000003')
    assert accepted.json()['accepted']==[] and 'уже принята' in accepted.json()['skipped'][0]['reason']
    # After a switch to offline submission earlier answers belong to other conditions.
    submit(other,key,'s2')
    offline=entry(client,key);offline['document']['content']['homeworkSubmission']='offline'
    assert write(client,offline,publish=True).status_code==200
    changed=overview(teacher,key);assert changed['assignment']!=assignment and changed['students'][1]['assignmentChanged']
    refused=bulk(teacher,key,changed['assignment'],[('s2',changed['students'][1]['version'])],request='00000000-0000-4000-8000-000000000004')
    assert refused.status_code==200 and refused.json()['accepted']==[]
    assert 'Условия задания изменились' in refused.json()['skipped'][0]['reason']
    assert get(teacher,key,'s2')['status']=='submitted'
    assert bulk(teacher,key,assignment,[('s2',1)],request='00000000-0000-4000-8000-000000000005').status_code==409


def test_bulk_is_idempotent_per_request_id(client):
    record,pupil,parent,teacher=setup(client);key=record['lessonKey']
    submit(pupil,key,'s1')
    current=overview(teacher,key);items=[(s['studentId'],s['version']) for s in current['students'] if s['status']=='submitted']
    first=bulk(teacher,key,current['assignment'],items);assert first.status_code==200 and first.json()['accepted']==['s1']
    with api.connect() as db: rows=db.execute('SELECT count(*) FROM homework_history').fetchone()[0],db.execute('SELECT count(*) FROM audit').fetchone()[0]
    waiting=unread(parent)
    again=bulk(teacher,key,current['assignment'],items)
    # The stored result is returned even though the version has moved on since.
    assert again.status_code==200
    assert (again.json()['accepted'],again.json()['skipped'])==(first.json()['accepted'],first.json()['skipped'])
    with api.connect() as db: assert (db.execute('SELECT count(*) FROM homework_history').fetchone()[0],db.execute('SELECT count(*) FROM audit').fetchone()[0])==rows
    assert unread(parent)==waiting
    different=bulk(teacher,key,current['assignment'],items,comment='другое')
    assert different.status_code==409 and 'другим действием' in different.text
    with api.connect() as db: assert db.execute('SELECT count(*) FROM homework_requests').fetchone()[0]==1
    assert history()==['homework.submitted','homework.reviewed']


def test_bulk_permissions(client):
    record,pupil,parent,teacher=setup(client);key=record['lessonKey'];submit(pupil,key,'s1')
    current=overview(teacher,key);assignment=current['assignment'];items=[('s1',current['students'][0]['version'])]
    for reader in [pupil,parent,signed('other-pupil'),signed('unrelated')]:
        assert bulk(reader,key,assignment,items).status_code==403
    # A substitute reads the work but only the course teacher accepts it.
    state=api.read('demo')
    state.datedLessons=[DatedLesson(id='replacement',name='Замена',kind='change',placementId='eng',sourceDate=DAY,onDate=DAY,teacherIds=['t1'])]
    api.save('demo',state)
    refused=bulk(signed('unrelated'),key,assignment,items);assert refused.status_code==403 and 'учитель курса' in refused.text
    assert bulk(teacher,key,assignment,items).status_code==200
    state=api.read('demo');state.datedLessons=[];next(w for w in state.workloads if w.id=='w0').teacherId='t1';api.save('demo',state)
    assert bulk(teacher,key,assignment,items,request=SECOND).status_code==403
    assert bulk(signed('unrelated'),key,assignment,items,request=SECOND).status_code==200
    assert bulk(client,key,assignment,items,request='00000000-0000-4000-8000-000000000003').status_code==200


def test_bulk_validation(client):
    record,pupil,parent,teacher=setup(client);key=record['lessonKey'];submit(pupil,key,'s1')
    current=overview(teacher,key);assignment=current['assignment'];version=current['students'][0]['version']
    assert bulk(teacher,key,assignment,[('s1',version),('s1',version)]).status_code==422
    assert bulk(teacher,key,assignment,[('s9',1)]).status_code==404
    assert bulk(teacher,key,'0'*64,[('s1',version)]).status_code==409
    assert bulk(teacher,key,assignment,[]).status_code==422
    assert bulk(teacher,key,assignment,[('s1',0)]).status_code==422
    assert bulk(teacher,key,assignment,[('s1',version)],request='not-an-id').status_code==422
    with api.connect() as db: assert db.execute('SELECT count(*) FROM homework_history WHERE action=?',('homework.reviewed',)).fetchone()[0]==0
    assert client.post(f'/api/journal/withdraw?workspace=demo',json=dict(lessonKey=key,version=record['version'])).status_code==200
    assert bulk(teacher,key,assignment,[('s1',version)]).status_code==404
