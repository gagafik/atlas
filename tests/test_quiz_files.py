"""Private quiz files follow immutable publications and per-attempt disclosure."""
from datetime import timedelta
import json
import pytest
from backend import api, quiz_files, teaching_media, homework_files
from test_access import client, signed
from test_homework import setup
from test_quizzes import clock, question, body, start, save, action, attempt


def ok(response):
    assert response.status_code==200,response.text
    return response.json()


def upload(reader,data=b'Question material',name='question.txt',workspace='demo'):
    return reader.post('/api/journal/quiz-files',params=dict(workspace=workspace,filename=name),content=data)


def file_get(reader,file,quiz=None,work=None,part='attachments',question_id='choice',**kwargs):
    params=dict(workspace='demo')
    if quiz:params.update(quizId=quiz['id'],questionId=question_id,part=part)
    if work:params['attemptId']=work['id']
    params.update(kwargs.pop('params',{}))
    return reader.get('/api/journal/quiz-files/'+file['id'],params=params,**kwargs)


def prepared(client):
    record,pupil,parent,teacher=setup(client)
    public=ok(upload(teacher));secret=ok(upload(teacher,b'Secret solution','solution.txt'))
    bank=question(teacher,fileIds=[public['id']],solutionFileIds=[secret['id']])
    payload=body(record,[bank]);quiz=ok(teacher.put('/api/journal/quizzes/test-one?workspace=demo',json=payload))
    return quiz,pupil,parent,teacher,bank,public,secret,payload


def update_question(reader,bank,**changes):
    q=bank['question']|changes
    return reader.put('/api/journal/quiz-bank/'+bank['id']+'?workspace=demo',json=dict(version=bank['version'],question=q))


def test_private_files_only_assigned_attempt_and_released_solutions(client):
    quiz,pupil,parent,teacher,bank,public,secret,_=prepared(client)
    for reader in (pupil,parent):
        assert file_get(reader,public).status_code==403
        assert file_get(reader,public,quiz).status_code==403
    work=ok(start(pupil,quiz))
    assert work['questions'][0]['attachments']==[public]
    assert secret['id'] not in json.dumps(work) and 'solutionFileIds' not in json.dumps(work)
    assert file_get(pupil,public,quiz,work).content==b'Question material'
    assert file_get(pupil,secret,quiz,work,part='solutionAttachments').status_code==404
    assert file_get(parent,public,quiz,work).status_code==404
    assert file_get(teacher,public,quiz,work).status_code==404
    assert file_get(teacher,secret,quiz,part='solutionAttachments').content==b'Secret solution'
    assert file_get(pupil,public,quiz,work,question_id='wrong').status_code==404
    assert file_get(signed('other-pupil'),public,quiz,work).status_code==403
    assert file_get(signed('unrelated'),public,quiz).status_code==403
    work=ok(save(pupil,work,submit=True))
    assert file_get(parent,public,quiz,work).content==b'Question material'
    assert file_get(parent,secret,quiz,work,part='solutionAttachments').status_code==404
    ok(action(teacher,ok(action(teacher,quiz,'close')),'release'))
    for reader in (pupil,parent):
        assert file_get(reader,secret,quiz,work,part='solutionAttachments').content==b'Secret solution'
        assert attempt(reader,work)['solutions'][0]['question']['solutionAttachments']==[secret]


def test_bank_edits_archive_and_quiz_copy_keep_old_bytes_and_metadata(client):
    quiz,pupil,_,teacher,bank,public,secret,payload=prepared(client)
    fresh=ok(upload(teacher,b'New diagram','new.txt'))
    ok(update_question(teacher,bank,fileIds=[fresh['id']],solutionFileIds=[]))
    copied=ok(teacher.put('/api/journal/quizzes/copy?workspace=demo',json=payload|dict(copyFrom=quiz['id'])))
    work=ok(start(pupil,copied))
    assert work['questions'][0]['attachments']==[public]
    assert file_get(pupil,public,copied,work).content==b'Question material'
    assert file_get(pupil,fresh,copied,work).status_code==404
    with api.connect() as db:
        db.execute('UPDATE quiz_files SET created=?',((quiz_files.now()-timedelta(days=2)).isoformat(),))
        quiz_files.collect_unused(db)
        assert db.execute('SELECT count(*) FROM quiz_files').fetchone()==(3,)


def test_copied_bank_admin_edit_and_metadata_not_trusted(client):
    quiz,_,_,teacher,bank,public,secret,_=prepared(client)
    replacement=ok(upload(client,b'Admin file','admin.txt'))
    updated=ok(update_question(client,bank,fileIds=[replacement['id']],attachments=[public|dict(filename='forged.txt')]))
    assert updated['question']['attachments']==[replacement]
    copy=question(teacher,'copy',**updated['question'])
    assert copy['question']['attachments']==[replacement]
    assert file_get(teacher,replacement).content==b'Admin file'
    assert file_get(signed('unrelated'),replacement).status_code==404
    denied=update_question(signed('unrelated'),copy,fileIds=[replacement['id']])
    assert denied.status_code==403
    q=copy['question'];q['fileIds']=[replacement['id']]
    assert signed('unrelated').put('/api/journal/quiz-bank/unowned?workspace=demo',json=dict(version=0,question=q)).status_code==422


def test_variants_cannot_fetch_each_others_files(client):
    record,pupil,parent,teacher=setup(client)
    f1=ok(upload(teacher,b'One'));f2=ok(upload(teacher,b'Two'))
    a=question(teacher,'one',fileIds=[f1['id']]);b=question(teacher,'two',fileIds=[f2['id']])
    payload=body(record,[a]);payload['definition']['variants'].append(dict(id='v2',name='Two',questions=[dict(id=b['id'],version=1)]))
    payload['variantsByStudent']={'s1':'v1','s2':'v2'}
    quiz=ok(teacher.put('/api/journal/quizzes/test-one?workspace=demo',json=payload));work=ok(start(pupil,quiz))
    assert file_get(pupil,f1,quiz,work,question_id='one').status_code==200
    assert file_get(pupil,f2,quiz,work,question_id='two').status_code==404


@pytest.mark.parametrize('change',['membership','cancelled','account','quiz_revision','school_revision'])
def test_current_links_and_race_recheck_before_range(client,monkeypatch,change):
    quiz,pupil,parent,teacher,_,public,_,_=prepared(client);work=ok(start(pupil,quiz))
    path=lambda:file_get(pupil,public,quiz,work,headers={'Range':'bytes=0-3'})
    assert path().status_code==206
    if change in ['account','quiz_revision','school_revision']:
        original=api.quizzes.context
        def changed(*args,**kwargs):
            ctx=original(*args,**kwargs)
            with api.connect() as db:
                if change=='account':db.execute('UPDATE accounts SET version=version+1 WHERE id=?',(ctx['user']['id'],))
                elif change=='quiz_revision':db.execute('UPDATE quizzes SET version=version+1 WHERE id=?',(quiz['id'],))
                else:db.execute("UPDATE workspaces SET revision=revision+1 WHERE id='demo'")
            return ctx
        monkeypatch.setattr(api.quizzes,'context',changed)
        assert path().status_code==409
    else:
        state=api.read('demo')
        if change=='membership':
            group=next(g for g in state.groups if g.id==quiz['document']['lesson']['groupId']);group.kind='elective';group.studentIds=['s2']
        else:
            from backend.models import DatedLesson
            from test_journal import DAY
            state.datedLessons=[DatedLesson(id='cancel',name='Cancelled',kind='cancel',placementId='eng',sourceDate=DAY,onDate=DAY)]
        api.save('demo',state)
        # A cancelled lesson does not freeze materials; only leaving the lesson group does.
        assert path().status_code==(206 if change=='cancelled' else 403)


def test_image_inline_range_download_and_workspace_isolation(client):
    _,pupil,_,teacher=setup(client)
    image=b'\x89PNG\r\n\x1a\n'+bytes(40);f=ok(upload(teacher,image,'diagram.png'))
    full=file_get(teacher,f)
    assert full.content==image and full.headers['content-type']=='image/png'
    assert full.headers['content-disposition'].startswith('inline;')
    assert full.headers['cache-control']=='no-store' and full.headers['x-content-type-options']=='nosniff'
    part=file_get(teacher,f,headers={'Range':'bytes=8-15','If-Range':full.headers['etag']})
    assert part.status_code==206 and part.content==image[8:16]
    assert file_get(teacher,f,params={'download':'true'}).headers['content-disposition'].startswith('attachment;')
    assert file_get(teacher,f,params={'workspace':'school'}).status_code==403
    assert upload(pupil).status_code==403


@pytest.mark.parametrize('data,name',[(b'','x.txt'),(b'<html/>','x.png'),(b'<svg/>','x.svg'),(b'\0bad','x.txt'),(b'x'*(10*1024*1024+1),'huge.txt')],ids=['empty','wrong-magic','svg','binary-text','oversize'])
def test_bad_uploads_clean_reservations(client,data,name):
    _,_,_,teacher=setup(client)
    assert upload(teacher,data,name).status_code in {413,422}
    with api.connect() as db:
        assert db.execute('SELECT count(*) FROM quiz_files').fetchone()==(0,)
        assert not list(quiz_files.root_for(db).glob('*.bin'))


def test_revoked_upload_cleans_bytes_and_semaphore(client,monkeypatch):
    _,_,_,teacher=setup(client);original=teaching_media.validate_file
    def revoked(path,name):
        digest=original(path,name)
        with api.connect() as db:db.execute("UPDATE accounts SET version=version+1 WHERE login='teacher'")
        return digest
    monkeypatch.setattr(teaching_media,'validate_file',revoked)
    assert upload(teacher).status_code==409
    with api.connect() as db:assert db.execute('SELECT count(*) FROM quiz_files').fetchone()==(0,)


def test_global_quota_and_abandoned_cleanup(client,monkeypatch):
    _,_,_,teacher=setup(client);f=ok(upload(teacher))
    monkeypatch.setenv('ATLAS_UPLOAD_TOTAL_MB','10')
    assert upload(teacher).status_code==413
    with api.connect() as db:
        assert homework_files.total_stored(db)==f['size']
        db.execute('UPDATE quiz_files SET created=?',((quiz_files.now()-timedelta(days=2)).isoformat(),))
    assert upload(teacher).status_code==200
    assert file_get(teacher,f).status_code==404


def test_question_rejects_duplicates_overlap_missing_and_too_many_files(client):
    _,_,_,teacher=setup(client);f=ok(upload(teacher))
    base=dict(kind='essay',text='Task')
    for extra in [dict(fileIds=[f['id']]*2),dict(fileIds=[f['id']],solutionFileIds=[f['id']]),dict(fileIds=['0'*32]),dict(fileIds=[f'{i:032x}' for i in range(7)])]:
        r=teacher.put('/api/journal/quiz-bank/bad?workspace=demo',json=dict(version=0,question=base|extra));assert r.status_code==422
    with api.connect() as db:assert db.execute('SELECT count(*) FROM quiz_file_links').fetchone()==(0,)


def test_backup_restores_old_published_bytes_and_solution_acl(client,tmp_path,monkeypatch):
    from tools.backup_school import create_backup
    from tools.restore_school import verify_backup
    quiz,pupil,parent,teacher,bank,public,secret,_=prepared(client);work=ok(start(pupil,quiz))
    ok(update_question(teacher,bank,fileIds=[],solutionFileIds=[]))
    target=tmp_path/'backup';report=create_backup(api.DB,target);verify_backup(target)
    assert len(report['files'])==2
    monkeypatch.setattr(api,'DB',target/api.DB.name)
    assert file_get(pupil,public,quiz,work).content==b'Question material'
    assert file_get(parent,secret,quiz,work,part='solutionAttachments').status_code==404
    work=ok(save(pupil,work,submit=True));ok(action(teacher,ok(action(teacher,quiz,'close')),'release'))
    assert file_get(parent,secret,quiz,work,part='solutionAttachments').content==b'Secret solution'
