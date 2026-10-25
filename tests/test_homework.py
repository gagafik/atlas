from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timezone,timedelta
import json
from pathlib import Path

import pytest
from backend import api,homework,homework_files
from backend.models import DatedLesson
from test_access import client,enable,create,signed,HEADERS
from test_journal import lesson,entry,write,graded,report,DAY


def setup(client):
    enable(client);create(client,'student');create(client,'parent');create(client,'teacher')
    create(client,'student',login='other-pupil',grants=[dict(workspace='demo',view='student',entityIds=['s2'])])
    create(client,'teacher',login='unrelated',grants=[dict(workspace='demo',view='teacher',entityIds=['t1'])])
    record=write(client,graded(entry(client,lesson(client)['lessonKey'])),publish=True).json()
    return record,signed('student'),signed('parent'),signed('teacher')


def target(key,student='s1'): return f'/api/journal/homework/{key}?workspace=demo&studentId={student}'


def get(client,key,student='s1'):
    response=client.get(target(key,student));assert response.status_code==200,response.text
    return response.json()


def answer(client,key,current,action='submit',text='Решение ученика',files=None):
    return client.put(target(key),json=dict(studentId='s1',version=current['version'],assignment=current['assignment'],action=action,text=text,fileIds=files or []))


def review(client,key,current,decision='accepted',comment='Проверено'):
    return client.post(f'/api/journal/homework/{key}/review?workspace=demo',json=dict(studentId='s1',version=current['version'],assignment=current['assignment'],decision=decision,comment=comment))


def upload(client,key,assignment,body=b'Answer in a file',filename='answer.txt',**extra):
    return client.post(f'/api/journal/homework/{key}/files',params=dict(workspace='demo',studentId='s1',filename=filename,assignment=assignment),content=body,headers={'Content-Type':'application/octet-stream',**extra})


def download(client,key,id,student='s1'):
    return client.get(f'/api/journal/homework/{key}/files/{id}',params=dict(workspace='demo',studentId=student))


def test_private_draft_submit_return_resubmit_review_and_history(client):
    record,pupil,parent,teacher=setup(client);key=record['lessonKey'];before=api.read('demo')
    current=get(pupil,key)
    r=answer(pupil,key,current,'draft','Только черновик');assert r.status_code==200,r.text
    assert 'Только черновик' not in json.dumps(get(parent,key),ensure_ascii=False)
    assert 'Только черновик' not in json.dumps(get(teacher,key),ensure_ascii=False)
    assert pupil.get(f'/api/journal/homework/{key}/history?workspace=demo&studentId=s1').json()['items']==[]
    assert teacher.get('/api/notifications/inbox').json()['unread']==0
    sent=answer(pupil,key,r.json()).json()
    assert sent['status']=='submitted' and not sent['canSubmit'] and sent['canWithdraw']
    assert get(parent,key)['submitted']['text']=='Решение ученика'
    assert get(parent,key)['draft'] is None and get(parent,key)['availableFiles']==[]
    assert teacher.get('/api/notifications/inbox').json()['unread']==1
    returned=review(teacher,key,sent,'returned','Добавьте обоснование').json()
    assert returned['status']=='returned'
    current=get(pupil,key);assert current['canSubmit']
    drafted=answer(pupil,key,current,'draft','Ещё не готово').json()
    assert get(parent,key)['submitted']['text']=='Решение ученика'
    sent=answer(pupil,key,drafted,text='Полное решение').json()
    assert sent['submitted']['attempt']==2
    accepted=review(teacher,key,sent).json()
    assert accepted['status']=='accepted' and get(parent,key)['submitted']['review']['comment']=='Проверено'
    assert not get(pupil,key)['canSubmit']
    assert answer(pupil,key,get(pupil,key),'draft').status_code==409
    history=parent.get(f'/api/journal/homework/{key}/history?workspace=demo&studentId=s1').json()['items']
    assert [h['action'] for h in history]==['homework.reviewed','homework.submitted','homework.reviewed','homework.submitted']
    assert history[-1]['submitted']['text']=='Решение ученика'
    assert api.read('demo')==before
    assert entry(client,key)['document']['content']==record['document']['content']


def test_student_parent_teacher_and_publication_boundaries(client):
    record,pupil,parent,teacher=setup(client);key=record['lessonKey'];current=get(pupil,key)
    assert pupil.get(f'/api/journal/homework/{key}?workspace=demo').status_code==403
    assert pupil.get(target(key,'s2')).status_code==403
    assert signed('other-pupil').get(target(key)).status_code==403
    assert signed('unrelated').get(target(key)).status_code==403
    assert answer(parent,key,current).status_code==403
    assert review(pupil,key,{**current,'version':1}).status_code==403
    assert teacher.get(f'/api/journal/homework/{key}?workspace=demo').json()['students']
    assert pupil.get(f'/api/journal/homework/{key}?workspace=school&studentId=s1').status_code==403
    sent=answer(pupil,key,current).json()
    assert client.post('/api/journal/withdraw?workspace=demo',json=dict(lessonKey=key,version=record['version'])).status_code==200
    for reader in [pupil,parent,teacher]: assert reader.get(target(key)).status_code==404
    assert review(teacher,key,sent).status_code==404
    with api.connect() as db: assert db.execute('SELECT count(*) FROM homework_submissions').fetchone()[0]==1


def test_attachments_private_scoped_history_and_download_headers(client):
    record,pupil,parent,teacher=setup(client);key=record['lessonKey'];current=get(pupil,key)
    response=upload(pupil,key,current['assignment'],filename='../папка/ответ.txt')
    assert response.status_code==200,response.text
    file=response.json();assert file['filename']=='ответ.txt'
    assert download(parent,key,file['id']).status_code==404
    assert download(teacher,key,file['id']).status_code==404
    assert download(signed('other-pupil'),key,file['id']).status_code==403
    wrong=download(signed('other-pupil'),key,file['id'],'s2');assert wrong.status_code==404
    assert download(pupil,key,file['id']).content==b'Answer in a file'
    sent=answer(pupil,key,current,files=[file['id']]).json()
    for reader in [pupil,parent,teacher]:
        result=download(reader,key,file['id']);assert result.status_code==200
        assert result.headers['content-type']=='application/octet-stream'
        assert result.headers['content-disposition'].startswith('attachment;')
        assert result.headers['x-content-type-options']=='nosniff' and result.headers['cache-control']=='no-store'
        assert result.content==b'Answer in a file'
    assert review(teacher,key,sent,'returned','Уточните').status_code==200
    next_upload=upload(pupil,key,current['assignment'],body=b'Draft private file').json()
    assert download(parent,key,next_upload['id']).status_code==404
    assert answer(pupil,key,get(pupil,key),files=[next_upload['id']]).status_code==200
    assert download(parent,key,file['id']).status_code==200 # submitted history retained
    with api.connect() as db:
        root=homework_files.root_for(db)
        assert len(list(root.glob('*.bin')))==2
        assert (root/(file['id']+'.bin')).read_bytes()==b'Answer in a file'


def test_upload_limits_quota_invalid_files_and_failed_upload_cleanup(client,monkeypatch):
    record,pupil,_,_=setup(client);key=record['lessonKey'];assignment=get(pupil,key)['assignment']
    for filename,content in [('bad.exe',b'123'),('fake.png',b'not png'),('empty.txt',b'')]:
        assert upload(pupil,key,assignment,body=content,filename=filename).status_code==422
    assert upload(pupil,key,assignment,body=b'x',**{'Content-Length':str(homework_files.MAX_FILE+1)}).status_code==413
    with api.connect() as db:
        assert db.execute('SELECT count(*) FROM homework_files').fetchone()[0]==0
        assert not list(homework_files.root_for(db).glob('*.bin'))
    monkeypatch.setattr(homework_files,'MAX_FILE',8)
    assert upload(pupil,key,assignment,body=b'123456789',**{'Content-Length':'1'}).status_code==413
    # A bad upload releases its concurrency slot and quota.
    assert upload(pupil,key,assignment,body=b'123').status_code==200
    monkeypatch.setattr(homework_files,'MAX_STUDENT',4)
    assert upload(pupil,key,assignment,body=b'1').status_code==413


def test_attachment_cannot_be_reused_across_students_or_assignments(client):
    record,pupil,_,_=setup(client);key=record['lessonKey'];current=get(pupil,key)
    id=upload(pupil,key,current['assignment']).json()['id']
    other=signed('other-pupil');other_work=get(other,key,'s2')
    data=dict(studentId='s2',version=0,assignment=other_work['assignment'],action='submit',text='',fileIds=[id])
    assert other.put(target(key,'s2'),json=data).status_code==422
    second_record=write(client,graded(entry(client,lesson(client,subject='Математика')['lessonKey'])),publish=True).json()
    second=second_record['lessonKey']
    assert answer(pupil,second,get(pupil,second),files=[id]).status_code==422
    assert answer(pupil,key,current,files=[id,id]).status_code==422
    assert answer(pupil,key,current,files=['unknown']).status_code==422


def test_stale_versions_assignment_changes_and_withdraw_review_race(client):
    record,pupil,parent,teacher=setup(client);key=record['lessonKey'];current=get(pupil,key)
    sent=answer(pupil,key,current).json()
    assert answer(pupil,key,current,'draft').status_code==409
    with ThreadPoolExecutor(max_workers=2) as pool:
        a=pool.submit(answer,pupil,key,sent,'withdraw')
        b=pool.submit(review,teacher,key,sent)
        assert sorted([a.result().status_code,b.result().status_code])==[200,409]
    from test_assignment_files import upload as upload_material,save_materials
    updated=entry(client,key);material=upload_material(client,updated).json()
    save_materials(client,updated,[material['id']],homework='Новое задание')
    assert answer(pupil,key,current).status_code==409
    latest=get(pupil,key);assert latest['canSubmit']
    newer=answer(pupil,key,latest,text='Новое решение').json()
    assert review(teacher,key,newer).status_code==200
    assert get(parent,key)['submitted']['homework']=='Новое задание'


def test_late_flag_uses_school_end_of_day(client,monkeypatch):
    record,pupil,_,_=setup(client);key=record['lessonKey']
    monkeypatch.setattr(homework,'now',lambda:datetime(2026,8,19,18,59,tzinfo=timezone.utc))
    sent=answer(pupil,key,get(pupil,key)).json();assert not sent['submitted']['late']
    assert answer(pupil,key,sent,'withdraw').status_code==200
    monkeypatch.setattr(homework,'now',lambda:datetime(2026,8,19,19,0,tzinfo=timezone.utc))
    sent=answer(pupil,key,get(pupil,key)).json();assert sent['submitted']['late']


def test_schedule_changes_and_account_revocation_block_writes(client,monkeypatch):
    record,pupil,parent,teacher=setup(client);key=record['lessonKey'];current=get(pupil,key)
    sent=answer(pupil,key,current).json()
    state=api.read('demo');state.datedLessons=[DatedLesson(id='cancel',name='Отмена',kind='cancel',placementId='eng',sourceDate=DAY,onDate=DAY)]
    api.save('demo',state)
    assert get(parent,key)['submitted']['text']=='Решение ученика'
    assert not get(pupil,key)['canSubmit']
    assert answer(pupil,key,sent,'withdraw').status_code==403
    state=api.read('demo');state.datedLessons=[];api.save('demo',state)
    original=api.homework.context
    def revoke(*args,**kwargs):
        ctx=original(*args,**kwargs)
        with api.connect() as db: db.execute('UPDATE accounts SET version=version+1 WHERE id=?',(ctx['user']['id'],))
        return ctx
    # The answer is still unreviewed and its version current, so only the account recheck can refuse this withdrawal.
    with monkeypatch.context() as patched:
        patched.setattr(api.homework,'context',revoke)
        refused=answer(pupil,key,sent,'withdraw');assert refused.status_code==409 and 'Учётная запись изменилась' in refused.text
    state=api.read('demo');state.datedLessons=[DatedLesson(id='cancel-again',name='Отмена',kind='cancel',placementId='eng',sourceDate=DAY,onDate=DAY)];api.save('demo',state)
    assert review(teacher,key,sent).status_code==200  # the course teacher checks work that was already sent


def test_gc_keeps_draft_and_submitted_history_but_removes_abandoned_uploads(client):
    record,pupil,_,teacher=setup(client);key=record['lessonKey'];current=get(pupil,key)
    used=upload(pupil,key,current['assignment'],body=b'Used').json()['id']
    abandoned=upload(pupil,key,current['assignment'],body=b'Unused').json()['id']
    sent=answer(pupil,key,current,files=[used]).json()
    review(teacher,key,sent,'returned','Ещё раз')
    draft=upload(pupil,key,current['assignment'],body=b'Private draft').json()['id']
    assert answer(pupil,key,get(pupil,key),'draft',files=[draft]).status_code==200
    with api.connect() as db:
        db.execute('UPDATE homework_files SET created=?',((homework.now()-timedelta(days=2)).isoformat(),))
        homework_files.collect_unused(db)
        root=homework_files.root_for(db)
        ids={r[0] for r in db.execute('SELECT id FROM homework_files')}
        assert ids=={used,draft}
        assert not (root/(abandoned+'.bin')).exists()
        assert (root/(draft+'.bin')).exists() and (root/(used+'.bin')).exists()


def test_publication_withdrawn_during_upload_discards_the_file(client,monkeypatch):
    record,pupil,_,_=setup(client);key=record['lessonKey'];assignment=get(pupil,key)['assignment']
    original=homework_files.validate_file
    def withdraw(path,name):
        assert client.post('/api/journal/withdraw?workspace=demo',json=dict(lessonKey=key,version=record['version'])).status_code==200
        return original(path,name)
    monkeypatch.setattr(homework_files,'validate_file',withdraw)
    assert upload(pupil,key,assignment).status_code==409
    with api.connect() as db:
        assert db.execute('SELECT count(*) FROM homework_files').fetchone()[0]==0
        assert not list(homework_files.root_for(db).glob('*.bin'))


def test_course_teacher_keeps_review_after_replacement_and_parent_grants_revoke_files(client):
    record,pupil,parent,teacher=setup(client);key=record['lessonKey'];current=get(pupil,key)
    file=upload(pupil,key,current['assignment']).json()['id']
    sent=answer(pupil,key,current,files=[file]).json()
    state=api.read('demo')
    state.datedLessons=[DatedLesson(id='replacement',name='Замена',kind='change',placementId='eng',sourceDate=DAY,onDate=DAY,teacherIds=['t1'])]
    api.save('demo',state)
    assert get(teacher,key)['submitted']['text']=='Решение ученика'
    assert review(signed('unrelated'),key,sent).status_code==403  # a substitute reads but does not review
    assert review(teacher,key,sent).status_code==200
    account=parent.get('/api/auth/status').json()['user']
    body={k:v for k,v in account.items() if k!='id'};body['grants'][0]['entityIds']=['s2']
    assert client.put('/api/access/users/'+account['id'],json=body).status_code==200
    assert download(parent,key,file).status_code==401
    parent=signed('parent')
    assert download(parent,key,file).status_code==403


def test_full_backup_restores_private_answer_and_authorized_download(client,tmp_path,monkeypatch):
    from tools.backup_school import create_backup
    record,pupil,parent,teacher=setup(client);key=record['lessonKey'];current=get(pupil,key)
    file=upload(pupil,key,current['assignment'],body=b'Answer to restore').json()['id']
    sent=answer(pupil,key,current,files=[file]).json()
    assert review(teacher,key,sent).status_code==200
    expected=get(parent,key)
    source=api.DB;destination=tmp_path/'full-backup'
    report=create_backup(source,destination)
    assert len(report['files'])==1
    monkeypatch.setattr(api,'DB',destination/source.name)
    assert get(parent,key)==expected
    assert download(parent,key,file).content==b'Answer to restore'
    assert download(signed('other-pupil'),key,file).status_code==403


def grading_url(key): return f'/api/journal/homework/{key}/grading?workspace=demo&studentId=s1'


def unread(client): return client.get('/api/notifications/inbox').json()['unread']


def test_typo_and_deadline_edits_keep_accepted_work(client):
    record,pupil,parent,teacher=setup(client);key=record['lessonKey']
    sent=answer(pupil,key,get(pupil,key)).json();assert review(teacher,key,sent).status_code==200
    token=get(pupil,key)['assignment'];original=record['document']['content']['homework']
    for field,value in [('homework','Прочитать страницу 12.'),('dueDate','2026-08-26'),('homeworkLatePolicy','block')]:
        edited=entry(client,key);edited['document']['content'][field]=value
        assert write(client,edited,publish=True).status_code==200
        for reader in [pupil,parent]:
            work=get(reader,key);assert work['status']=='accepted' and not work['assignmentChanged'] and work['assignment']==token
        work=get(pupil,key);assert work['canSaveDraft'] is False and work['canSubmit'] is False
        # The pupil sees the current text, while the answer keeps the conditions it was given under.
        assert work['homework']=='Прочитать страницу 12.' and work['submitted']['homework']==original
    assert get(teacher,key)['status']=='accepted'


def test_substantive_changes_reopen_accepted_work(client):
    from test_assignment_files import upload as upload_material,save_materials
    record,pupil,parent,teacher=setup(client);key=record['lessonKey']
    sent=answer(pupil,key,get(pupil,key)).json()
    material=upload_material(client,record).json();save_materials(client,record,[material['id']])
    work=get(pupil,key);assert work['assignmentChanged'] and work['canSubmit']
    assert review(teacher,key,sent).status_code==409  # the token the teacher saw is outdated
    current=get(teacher,key);assert current['assignmentChanged']
    assert review(teacher,key,current).status_code==409  # changed conditions cannot be accepted
    assert review(teacher,key,current,'returned','Выполните по новым материалам').status_code==200


def test_stale_conditions_between_context_and_write_conflict(client,monkeypatch):
    record,pupil,_,_=setup(client);key=record['lessonKey'];current=get(pupil,key);original=api.homework.context
    def raced(*args,**kwargs):
        ctx=original(*args,**kwargs)
        moved=entry(client,key);moved['document']['content']['dueDate']='2026-08-26'
        assert write(client,moved,publish=True).status_code==200
        return ctx
    monkeypatch.setattr(api.homework,'context',raced)
    assert answer(pupil,key,current).status_code==409
    with api.connect() as db: assert db.execute('SELECT count(*) FROM homework_submissions').fetchone()[0]==0


def test_offline_homework_rejects_online_answers(client):
    record,pupil,parent,teacher=setup(client);key=record['lessonKey']
    sent=answer(pupil,key,get(pupil,key)).json()
    for fields,message in [(dict(homeworkLatePolicy='block'),'только при онлайн-сдаче'),(dict(homework='',dueDate=None),'относится к домашнему заданию')]:
        invalid=entry(client,key);invalid['document']['content'].update(homeworkSubmission='offline',**fields)
        response=write(client,invalid,publish=True);assert response.status_code==422 and message in response.text
    offline=entry(client,key);offline['document']['content']['homeworkSubmission']='offline'
    assert write(client,offline,publish=True).status_code==200
    work=get(pupil,key);assert work['submission']=='offline' and not work['canSaveDraft'] and not work['canSubmit']
    response=answer(pupil,key,work,'draft');assert response.status_code==409 and 'без онлайн-отправки' in response.text
    assert answer(pupil,key,work).status_code==409
    assert upload(pupil,key,work['assignment']).status_code==403
    # An answer sent before the switch belongs to other conditions, but it can still be withdrawn.
    assert work['assignmentChanged'] and get(teacher,key)['assignmentChanged']
    assert answer(pupil,key,work,'withdraw').status_code==200 and get(pupil,key)['submitted'] is None
    assert report(parent).json()['rows'][0]['content']['homeworkSubmission']=='offline'


def test_substitute_cannot_review_but_course_teacher_can(client):
    from backend.homework_grading import FOREIGN
    record,pupil,parent,teacher=setup(client);key=record['lessonKey']
    state=api.read('demo')
    state.datedLessons=[DatedLesson(id='replacement',name='Замена',kind='change',placementId='eng',sourceDate=DAY,onDate=DAY,teacherIds=['t1'])]
    api.save('demo',state);substitute=signed('unrelated')
    write(substitute,entry(substitute,key,syncRoster=True),publish=True,syncRoster=True).raise_for_status()
    waiting=(unread(teacher),unread(substitute))
    sent=answer(pupil,key,get(pupil,key)).json()
    assert (unread(teacher),unread(substitute))==(waiting[0]+1,waiting[1])
    assert review(substitute,key,sent).status_code==403
    assert get(substitute,key)['canReview'] is False and substitute.get(grading_url(key)).status_code==403
    options=teacher.get(grading_url(key));assert options.status_code==200
    assert options.json()['allowed'] is False and options.json()['reason']==FOREIGN
    # Posting a grade is refused with the same explanation, writes nothing, and a plain review still works.
    from test_homework_grading import payload,grade
    refused=grade(teacher,key,sent,payload(options.json()));assert refused.status_code==409 and refused.json()['detail']==FOREIGN
    assert get(teacher,key)['status']=='submitted' and 'grading' not in get(teacher,key)['submitted']
    assert review(teacher,key,sent).json()['status']=='accepted'


def test_workload_change_moves_review_rights(client):
    record,pupil,parent,teacher=setup(client);key=record['lessonKey']
    sent=answer(pupil,key,get(pupil,key)).json()
    state=api.read('demo');next(w for w in state.workloads if w.id=='w0').teacherId='t1';api.save('demo',state)
    assert get(teacher,key)['canReview'] is False and review(teacher,key,sent).status_code==403
    assert get(teacher,key)['submitted']['text']=='Решение ученика'  # a past teacher keeps read access
    assert review(signed('unrelated'),key,sent).status_code==200


def test_extra_lesson_without_workload_falls_back_to_lesson_teachers(client):
    record,pupil,parent,teacher=setup(client)
    state=api.read('demo')
    state.datedLessons=[DatedLesson(id='extra',name='Дополнительный урок',kind='add',onDate='2026-08-18',groupId='g-a',subjectId='sub0',teacherIds=['t1'],roomIds=['r0'],profileId='primary',startTime='09:00',endTime='09:45')]
    api.save('demo',state);key=lesson(client,subject='Математика',day='2026-08-18')['lessonKey']
    extra=graded(entry(client,key,day='2026-08-18'));assert write(client,extra,publish=True).status_code==200
    sent=answer(pupil,key,get(pupil,key)).json()
    assert review(teacher,key,sent).status_code==403
    assert review(signed('unrelated'),key,sent).status_code==200
