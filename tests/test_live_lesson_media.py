"""Question-specific files, immutable editions and a projection without pupils."""
from datetime import timedelta
import json
from backend import api, teaching_material_files
from test_access import client, signed
from test_live_lessons import setup, ok, get, new, current, action, cmd, url, answer
from test_quizzes import question
from test_teaching_materials import save, upload


def reference(item): return dict(id=item['id'], version=item['version'])


def prepared(t):
    prompt_file = ok(upload(t, b'\x89PNG\r\n\x1a\nsynthetic-image', 'diagram.png'))
    solution_file = ok(upload(t, b'SECRET-SOLUTION', 'solution.txt'))
    prompt = ok(save(t, title='Prompt material', text='Read this diagram', fileIds=[prompt_file['id']]))
    solution = ok(save(t, title='Hidden solution', text='SECRET-SOLUTION', fileIds=[solution_file['id']]))
    q = question(t)
    q2 = question(t, 'future')
    w = api.read('demo').workloads[0]
    definition = dict(title='Lesson with materials', groupId=w.groupId, subjectId=w.subjectId,
                      questions=[reference(q) | dict(materials=[reference(prompt)], solutionMaterials=[reference(solution)]), reference(q2) | dict(materials=[reference(solution)])])
    lesson = ok(t.post(url(), json=cmd(definition=definition)))
    return lesson['id'], prompt, solution, prompt_file, solution_file


def download(c, id, file, index=0, part='materials', projection=False):
    return c.get(url(f'/{id}/files/{file["id"]}?questionIndex={index}&part={part}&projection={str(projection).lower()}'))


def test_pinned_materials_file_visibility_and_no_journal_mutation(client, setup):
    t, p, parent, _ = setup; before = api.read('demo')
    id, prompt, solution, image, hidden = prepared(t)
    assert download(t, id, image).status_code == 200
    assert download(p, id, image).status_code == 404
    ok(action(t, id, 'start'))
    assert download(p, id, image).status_code == 404
    ok(action(t, id, 'open'))
    for c in [p, parent]:
        view = current(c, id)
        assert view['rounds'][0]['materials'][0]['title'] == 'Prompt material'
        assert 'SECRET-SOLUTION' not in json.dumps(view) and hidden['id'] not in json.dumps(view)
        assert download(c, id, image).headers['content-type'] == 'image/png'
        assert download(c, id, image).headers['content-disposition'].startswith('inline')
        assert download(c, id, hidden, part='solutionMaterials').status_code == 404
        assert download(c, id, hidden, index=1).status_code == 404
        assert c.get('/api/journal/material-files/' + image['id'] + '?workspace=demo').status_code == 403
    unrelated = ok(upload(t))
    assert download(t, id, unrelated).status_code == 404
    assert download(signed('other'), id, image).status_code == 404
    other = new(t, [current(t, id)['definition']['questions'][0]])['id']; assert download(t, other, image).status_code == 404
    ok(action(t, id, 'close'))
    assert download(p, id, hidden, part='solutionMaterials').status_code == 404
    ok(action(t, id, 'reveal'))
    r = download(parent, id, hidden, part='solutionMaterials')
    assert r.content == b'SECRET-SOLUTION'
    assert r.headers['cache-control'] == 'no-store' and r.headers['x-content-type-options'] == 'nosniff'
    assert r.headers['content-disposition'].startswith('attachment')
    assert api.read('demo') == before
    with api.connect() as db:
        assert db.execute('SELECT count(*) FROM journal_entries').fetchone()[0] == 0
        assert db.execute('SELECT count(*) FROM teaching_material_lessons').fetchone()[0] == 0


def test_library_edits_archive_copy_and_retention(client, setup):
    t, p, _, _ = setup; id, prompt, solution, image, hidden = prepared(t)
    changed = ok(save(t, prompt, text='New edition', fileIds=[]))
    ok(t.post('/api/journal/library/' + prompt['id'] + '/archive?workspace=demo', json=dict(version=changed['version'], archived=True)))
    copy = ok(action(t, id, 'copy'))['id']
    doc = current(t, copy)['definition']; q = doc['questions'][0]
    assert q['materials'][0]['text'] == 'Read this diagram'
    doc['questions'] = [reference(q) | {f: [reference(m) for m in q.get(f, [])] for f in ['materials', 'solutionMaterials']}]
    ok(t.put(url('/' + copy), json=cmd(version=1, definition=doc)))
    with api.connect() as db:
        db.execute("UPDATE teaching_material_files SET created='2000-01-01T00:00:00+00:00'")
        teaching_material_files.collect_unused(db)
    ok(action(t, copy, 'start')); ok(action(t, copy, 'open'))
    assert download(p, copy, image).status_code == 200
    assert download(p, copy, hidden, part='solutionMaterials').status_code == 404


def test_canonical_private_material_rights_and_validation(client, setup):
    t, p, _, _ = setup
    private = ok(save(signed('other'), text='Other private content'))
    q = question(t); w = api.read('demo').workloads[0]
    d = dict(title='Unsafe', groupId=w.groupId, subjectId=w.subjectId, questions=[reference(q) | dict(materials=[reference(private)])])
    assert t.post(url(), json=cmd(definition=d)).status_code == 404
    shared = ok(save(signed('other'), private, visibility='staff', text='Shared edition'))
    assert t.post(url(), json=cmd(definition=d)).status_code == 404  # old private version stays private
    d['questions'][0]['materials'] = [reference(shared)]
    created = ok(t.post(url(), json=cmd(definition=d)))
    assert current(t, created['id'])['definition']['questions'][0]['materials'][0]['text'] == 'Shared edition'
    d['questions'][0]['materials'] = [reference(shared)] * 4
    assert t.post(url(), json=cmd(definition=d)).status_code == 422
    d['questions'][0]['materials'] = [reference(shared)] * 2
    assert t.post(url(), json=cmd(definition=d)).status_code == 422
    assert p.post(url(), json=cmd(definition=d)).status_code in {403, 422}


def test_projection_never_contains_future_solutions_or_personal_responses(client, setup):
    t, p, parent, clock = setup; id, prompt, solution, image, hidden = prepared(t)
    path = '/' + id + '/projection'
    assert get(t, path)['round'] is None
    assert t.get(url(path + '?questionIndex=0')).status_code == 404
    for c in [p, parent, signed('other')]: assert c.get(url(path)).status_code == 404
    assert download(t, id, image, projection=True).status_code == 404
    ok(action(t, id, 'start')); ok(action(t, id, 'open', seconds=30)); ok(answer(p, id))
    r = t.get(url(path)); view = r.json()
    assert view['round']['question']['text'] and view['opened'] == [0]
    serialized = json.dumps(view)
    for key in ['definition', 'correctIds', 'response', 'student', 'SECRET-SOLUTION', 'owner_id', 'answerRevision']:
        assert key not in serialized
    assert t.get(url(path), headers={'If-None-Match': r.headers['etag']}).status_code == 304
    assert download(t, id, hidden, part='solutionMaterials', projection=True).status_code == 404
    assert download(t, id, image, projection=True).status_code == 200
    assert download(p, id, image, projection=True).status_code == 404
    clock[0] += timedelta(seconds=31)
    expired = t.get(url(path), headers={'If-None-Match': r.headers['etag']})
    assert expired.status_code == 200 and expired.json()['round']['phase'] == 'closed'
    ok(action(t, id, 'reveal'))
    assert get(t, path)['round']['solutionMaterials'][0]['text'] == 'SECRET-SOLUTION'
    assert download(t, id, hidden, part='solutionMaterials', projection=True).status_code == 200
    ok(action(t, id, 'open', questionIndex=1))
    assert get(t, path)['round']['index'] == 1
    assert get(t, path + '?questionIndex=0')['round']['index'] == 0


def test_media_and_conditional_projection_recheck_current_access(client, setup):
    t, p, parent, _ = setup; id, _, _, image, _ = prepared(t)
    ok(action(t, id, 'start')); ok(action(t, id, 'open'))
    projection = t.get(url('/' + id + '/projection'))
    state = api.read('demo'); state.students[0].active = False; api.save('demo', state)
    for c in [p, parent]: assert download(c, id, image).status_code in {403, 404}
    state = api.read('demo')
    for w in state.workloads:
        w.teacherId = 't1'
    api.save('demo', state)
    assert t.get(url('/' + id + '/projection'), headers={'If-None-Match': projection.headers['etag']}).status_code in {403, 404}
    assert download(t, id, image).status_code in {403, 404}
