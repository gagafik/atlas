"""Isolated HTTP workers and timing for synthetic load tools, never production."""
import asyncio
from contextlib import AsyncExitStack,asynccontextmanager
import time

import httpx

CLIENT_IDLE_SECONDS=2
SERVER_IDLE_SECONDS=5


class Workers:
    """Lease one client for a complete workflow; bound simultaneous workflows."""
    def __init__(self,url,concurrency,mode='independent'):
        if concurrency<1 or mode not in ['independent','shared']:raise ValueError('Invalid worker configuration')
        self.url,self.concurrency,self.mode=url,concurrency,mode
        self.stack=AsyncExitStack();self.available=asyncio.Queue()

    async def __aenter__(self):
        try:
            count=self.concurrency if self.mode=='independent' else 1
            connections=1 if self.mode=='independent' else self.concurrency
            clients=[await self.stack.enter_async_context(httpx.AsyncClient(base_url=self.url,timeout=120,trust_env=False,
                limits=httpx.Limits(max_connections=connections,max_keepalive_connections=connections,keepalive_expiry=CLIENT_IDLE_SECONDS))) for _ in range(count)]
            for index in range(self.concurrency):self.available.put_nowait(clients[index%count])
            return self
        except BaseException:
            await self.stack.aclose();raise

    async def __aexit__(self,*args):return await self.stack.__aexit__(*args)

    @asynccontextmanager
    async def slot(self):
        client=await self.available.get()
        try:yield client
        finally:self.available.put_nowait(client)


class ServerTiming:
    """Add processing time only to the separately launched benchmark ASGI app."""
    def __init__(self,app):self.app=app

    async def __call__(self,scope,receive,send):
        started=time.perf_counter()
        async def timed_send(message):
            if message['type']=='http.response.start':
                message=dict(message,headers=list(message.get('headers',[]))+[
                    (b'x-atlas-benchmark-app-ms',str((time.perf_counter()-started)*1000).encode()),
                    (b'x-atlas-benchmark-loop',type(asyncio.get_running_loop()).__name__.encode())])
            await send(message)
        await self.app(scope,receive,timed_send)


class RequestTiming:
    def __init__(self):self.started=time.perf_counter();self.events={}

    async def trace(self,name,info):self.events[name]=time.perf_counter()

    def finish(self,response):
        ended=time.perf_counter();events=self.events
        values={'total_ms':(ended-self.started)*1000}
        for name,begin,end in [
            ('before_send_ms',self.started,events.get('http11.send_request_headers.started')),
            ('tcp_connect_ms',events.get('connection.connect_tcp.started'),events.get('connection.connect_tcp.complete')),
            ('sent_to_headers_ms',events.get('http11.send_request_headers.complete'),events.get('http11.receive_response_headers.complete')),
            ('after_headers_ms',events.get('http11.receive_response_headers.complete'),ended),
        ]:
            if begin is not None and end is not None:values[name]=(end-begin)*1000
        if 'x-atlas-benchmark-app-ms' in response.headers:
            values['server_ms']=float(response.headers['x-atlas-benchmark-app-ms'])
            values['outside_server_ms']=values['total_ms']-values['server_ms']
        return values
