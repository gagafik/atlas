import json
from types import SimpleNamespace
import pytest
from fastapi import HTTPException
from backend import api, assistant
from backend.models import Placement
from backend.seed import initial_state
from test_access import client, enable, create, signed


@pytest.fixture(autouse=True)
def clear_requests(monkeypatch):
    api._assistant_requests.clear()
    monkeypatch.delenv('ATLAS_AI_URL',raising=False)
    monkeypatch.delenv('ATLAS_AI_KEY',raising=False)
    monkeypatch.delenv('ATLAS_AI_MODEL',raising=False)
    yield
    api._assistant_requests.clear()


def prepare(client,**values):
    body=dict(revision=1,question='Предложи дневной предел',model='fixture-model',workloadIds=['w0'])|values
    result=client.post('/api/assistant/prepare?workspace=demo',json=body)
    assert result.status_code==200,result.text
    return result.json()


def respond(monkeypatch,proposals=None,answer='Проверенный формат ответа'):
    calls=[]
    def request(config,path,body=None):
        calls.append(dict(config=config,path=path,body=body))
        return dict(message=dict(content=json.dumps(dict(answer=answer,proposals=proposals or []))))
    monkeypatch.setattr(assistant,'model_request',request)
    return calls


def test_prepared_context_is_scoped_and_excludes_pupils_marks_notes(monkeypatch,client):
    calls=respond(monkeypatch)
    p=prepare(client)
    raw=json.dumps(p,ensure_ascii=False)
    for pupil in api.read('demo').students: assert pupil.name not in raw and pupil.id not in raw
    assert 'sourceCells' not in raw and 'journal' not in raw and 'importInfo' not in raw
    context=p['payload']['CONTEXT']
    assert len(context['workloads'])==1 and len(context['placements'])==1
    assert not calls # Preparing never contacts a model.
    before=api.read('demo').model_dump_json()
    result=client.post('/api/assistant/ask?workspace=demo',json=dict(token=p['token']))
    assert result.status_code==200,result.text
    assert len(calls)==1 and calls[0]['body']['stream'] is False
    assert api.read('demo').model_dump_json()==before
    assert client.post('/api/assistant/ask?workspace=demo',json=dict(token=p['token'])).json()==result.json()
    assert len(calls)==1 # Duplicate submissions reuse the completed answer.


def test_model_rule_is_new_checked_preview_then_explicit_apply(monkeypatch,client):
    p=prepare(client)
    teacher=p['payload']['CONTEXT']['entities']['teacher'][0]['ref']
    respond(monkeypatch,[dict(title='Предел',explanation='Просмотрите правило',action='rule',rule=dict(id='existing-overwrite',name='До 15:00',kind='day_shape',scope='teacher',targetIds=[teacher],latestEnd='15:00'))])
    answer=client.post('/api/assistant/ask?workspace=demo',json=dict(token=p['token'])).json()
    assert len(answer['proposals'])==1 and api.read('demo').rules==[]
    preview=client.post('/api/assistant/preview?workspace=demo',json=dict(token=p['token'],proposalId=answer['proposals'][0]['id']))
    assert preview.status_code==200,preview.text
    assert preview.json()['canApply'] and api.read('demo').rules==[]
    applied=client.post('/api/editor/apply?workspace=demo',json=dict(token=preview.json()['token'],revision=1))
    assert applied.status_code==200
    rule=applied.json()['rules'][0]
    assert rule['id'].startswith('ai-') and rule['targetIds']==['t2']
    assert client.post('/api/assistant/preview?workspace=demo',json=dict(token=p['token'],proposalId=answer['proposals'][0]['id'])).status_code==409


def test_foreign_refs_unknown_actions_and_locked_moves_are_rejected(monkeypatch,client):
    state=api.read('demo');state.placements[0].locked=True;api.save('demo',state)
    p=prepare(client,revision=2)
    own=p['payload']['CONTEXT']['placements'][0]['ref']
    respond(monkeypatch,[dict(title='Удалить',action='delete',ids=[own]),
                         dict(title='Чужой',action='move',ids=['placement:999'],day=1,period=1),
                         dict(title='Закреплённый',action='move',ids=[own],day=1,period=1)])
    answer=client.post('/api/assistant/ask?workspace=demo',json=dict(token=p['token'])).json()
    assert answer['proposals']==[] and len(answer['rejected'])==3
    assert api.read('demo').revision==2


def test_external_models_require_specific_request_consent_and_hide_key(monkeypatch,client):
    monkeypatch.setenv('ATLAS_AI_URL','https://models.example.test')
    monkeypatch.setenv('ATLAS_AI_KEY','server-secret')
    calls=respond(monkeypatch)
    p=prepare(client);assert p['external'] and 'server-secret' not in json.dumps(p)
    assert client.post('/api/assistant/ask?workspace=demo',json=dict(token=p['token'])).status_code==422
    assert not calls
    assert client.post('/api/assistant/ask?workspace=demo',json=dict(token=p['token'],allowExternal=True)).status_code==200
    assert calls[0]['config']['key']=='server-secret'
    monkeypatch.delenv('ATLAS_AI_URL')
    cloud=prepare(client,model='fixture:cloud');assert cloud['external']


def test_permissions_owner_workspace_staleness_and_expiry(monkeypatch,client):
    enable(client);create(client,'student');create(client,'editor');create(client,'teacher')
    pupil,teacher,editor=signed('student'),signed('teacher'),signed('editor')
    for personal in [pupil,teacher]:
        assert personal.get('/api/assistant/status?workspace=demo').status_code==403
    p=prepare(client)
    assert editor.post('/api/assistant/ask?workspace=demo',json=dict(token=p['token'])).status_code==404
    assert editor.get('/api/assistant/status?workspace=school').status_code==403
    calls=respond(monkeypatch)
    state=api.read('demo');api.save('demo',state)
    assert client.post('/api/assistant/ask?workspace=demo',json=dict(token=p['token'])).status_code==409
    assert not calls
    p=prepare(client,revision=2);assert client.post('/api/assistant/ask?workspace=demo',json=dict(token=p['token'])).status_code==200
    old=prepare(client,revision=2);later=assistant.time.monotonic()+old['expiresInSeconds']+1
    monkeypatch.setattr(assistant,'time',SimpleNamespace(monotonic=lambda:later))
    assert client.post('/api/assistant/ask?workspace=demo',json=dict(token=old['token'])).status_code==404


def test_malformed_reply_and_connection_failures_never_write(monkeypatch,client):
    p=prepare(client)
    monkeypatch.setattr(assistant,'model_request',lambda *args:dict(message=dict(content='not JSON')))
    assert client.post('/api/assistant/ask?workspace=demo',json=dict(token=p['token'])).status_code==502
    assert client.post('/api/assistant/ask?workspace=demo',json=dict(token=p['token'])).status_code==409
    assert api.read('demo').revision==1
    monkeypatch.setenv('ATLAS_AI_URL','http://remote.example.test')
    assert client.get('/api/assistant/status?workspace=demo').status_code==503
    monkeypatch.setenv('ATLAS_AI_URL','http://user:secret@localhost:11434')
    assert client.get('/api/assistant/status?workspace=demo').status_code==503


def test_role_changed_while_model_runs_is_rechecked(monkeypatch,client):
    enable(client);created=create(client,'editor');editor=signed('editor')
    p=prepare(editor)
    def request(*args):
        with api.connect() as db: db.execute('UPDATE accounts SET active=0 WHERE id=?',(created['id'],))
        return dict(message=dict(content=json.dumps(dict(answer='Не выдавать после отзыва прав',proposals=[]))))
    monkeypatch.setattr(assistant,'model_request',request)
    assert editor.post('/api/assistant/ask?workspace=demo',json=dict(token=p['token'])).status_code in [403,404]


def test_ollama_http_contract_no_redirects_proxy_and_bounded_response(monkeypatch):
    import httpx
    original=httpx.Client
    seen=[]
    def handler(request):
        seen.append(request)
        assert request.url.path=='/api/chat'
        assert json.loads(request.content)['stream'] is False
        return httpx.Response(200,json=dict(message=dict(content='{}')))
    def factory(**kwargs):
        assert kwargs['trust_env'] is False and kwargs['follow_redirects'] is False
        return original(**kwargs,transport=httpx.MockTransport(handler))
    monkeypatch.setattr(assistant.httpx,'Client',factory)
    config=dict(url='http://127.0.0.1:11434',key='private')
    result=assistant.model_request(config,'/api/chat',dict(stream=False))
    assert result['message']['content']=='{}' and seen[0].headers['authorization']=='Bearer private'
    def large(request): return httpx.Response(200,content=b'x'*260000)
    monkeypatch.setattr(assistant.httpx,'Client',lambda **kw:original(**kw,transport=httpx.MockTransport(large)))
    with pytest.raises(HTTPException) as error: assistant.model_request(config,'/api/tags')
    assert error.value.status_code==502
    def redirect(request): return httpx.Response(302,headers={'Location':'https://unexpected.example.test'})
    monkeypatch.setattr(assistant.httpx,'Client',lambda **kw:original(**kw,transport=httpx.MockTransport(redirect)))
    with pytest.raises(HTTPException) as error: assistant.model_request(config,'/api/tags')
    assert error.value.status_code==502
