import asyncio

import httpx
import pytest

from tools.benchmark_http import Workers,ServerTiming,RequestTiming
from test_access import client


@pytest.mark.parametrize('mode,distinct',[('independent',2),('shared',1)])
def test_workers_limit_active_workflows_and_close_clients(mode,distinct):
    async def run():
        seen=[];active=0;peak=0;two_ready=asyncio.Event();release=asyncio.Event();third_ready=asyncio.Event()
        async with Workers('http://127.0.0.1:1',2,mode) as workers:
            async def work(index):
                nonlocal active,peak
                async with workers.slot() as session:
                    seen.append(session);active+=1;peak=max(peak,active)
                    if index==2:third_ready.set()
                    elif active==2:two_ready.set()
                    try:await release.wait()
                    finally:active-=1
            first=asyncio.create_task(work(0));second=asyncio.create_task(work(1))
            await asyncio.wait_for(two_ready.wait(),2)
            third=asyncio.create_task(work(2));await asyncio.sleep(0)
            assert not third_ready.is_set() and len({id(c) for c in seen})==distinct
            release.set();await asyncio.wait_for(asyncio.gather(first,second,third),2)
            assert third_ready.is_set() and peak==2
        assert all(c.is_closed for c in seen)
    asyncio.run(run())


def test_cancelled_workflow_returns_its_slot_and_closes_connection():
    async def run():
        entered=asyncio.Event();seen=[]
        async with Workers('http://127.0.0.1:1',1) as workers:
            async def work():
                async with workers.slot() as session:
                    seen.append(session);entered.set();await asyncio.Event().wait()
            task=asyncio.create_task(work());await asyncio.wait_for(entered.wait(),2)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):await task
            async with asyncio.timeout(2):
                async with workers.slot() as session:assert session is seen[0] and not session.is_closed
        assert seen[0].is_closed
    asyncio.run(run())


def test_timing_distinguishes_server_and_total_without_inventing_transport_events():
    async def app(scope,receive,send):
        await send(dict(type='http.response.start',status=200,headers=[(b'content-type',b'text/plain')]))
        await send(dict(type='http.response.body',body=b'ok'))
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=ServerTiming(app)),base_url='http://test') as session:
            timing=RequestTiming();response=await session.get('/');values=timing.finish(response)
        assert response.text=='ok' and 0<=values['server_ms']<=values['total_ms']
        assert values['outside_server_ms']==values['total_ms']-values['server_ms']
        assert 'tcp_connect_ms' not in values and 'before_send_ms' not in values
    asyncio.run(run())


def test_benchmark_timing_is_not_installed_on_normal_application(client):
    response=client.get('/api/health');assert response.status_code==200
    assert 'x-atlas-benchmark-app-ms' not in response.headers
    assert 'x-atlas-benchmark-loop' not in response.headers


def message(error):
    # An exception group hides its members' texts from str().
    return ' '.join(message(e) for e in error.exceptions) if isinstance(error,BaseExceptionGroup) else str(error)


def test_unexpected_http_error_aborts_load_and_closes_other_workflows(monkeypatch):
    from tools.benchmark_quizzes import probe
    original=httpx.AsyncClient
    async def run():
        entered=asyncio.Event();cancelled=asyncio.Event();clients=[]
        async def respond(request):
            if request.headers['cookie']=='pupil-0':
                await entered.wait()
                return httpx.Response(401,text='Synthetic access rejection')
            entered.set()
            try:await asyncio.Event().wait()
            except asyncio.CancelledError:cancelled.set();raise
        def create(**kwargs):
            session=original(**kwargs,transport=httpx.MockTransport(respond));clients.append(session);return session
        monkeypatch.setattr(httpx,'AsyncClient',create)
        identities={i:(f'pupil-{i}',) for i in [0,1,1100,1101]}
        tasks=[(i,'key','teacher','quiz') for i in [0,1]]
        async with asyncio.timeout(5):
            with pytest.raises((ExceptionGroup,RuntimeError)) as error:await probe('http://test',identities,tasks,[],2)
        assert '401' in message(error.value)
        assert cancelled.is_set() and clients and all(c.is_closed for c in clients)
    asyncio.run(run())
