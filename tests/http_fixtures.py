"""显式测试支撑：只装配公开能力、可控外部响应或指定故障。"""
import asyncio
import httpx
import json
import socket
import uvicorn
from contextlib import asynccontextmanager
from typing import cast
from anthropic import AsyncAnthropic
from mcp import Client
from travel_app.retrieval import Retrieval
from service_fixtures import MapService, response
from travel_app.sessions import Runtime, create_app


@asynccontextmanager
async def serving(directory, model, *, maps=None, sources=None):
    @asynccontextmanager
    async def resources():
        tools = Retrieval(cast(Client, maps if maps is not None else MapService()), api_key="known-secret")
        await tools.discover()
        if sources is not None:
            tools = Retrieval(cast(Client, maps if maps is not None else MapService()), source_client=cast(Client, sources), api_key="known-secret", source_interval=0)
            await tools.discover()
            yield Runtime(cast(AsyncAnthropic, model), tools, "test-model")
        else:
            yield Runtime(cast(AsyncAnthropic, model), tools, "test-model")
    app = create_app(directory, resources=resources)
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="critical", access_log=False))
    task = asyncio.create_task(server.serve(sockets=[sock]))
    try:
        while not server.started and not task.done():
            await asyncio.sleep(0)
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as client:
            yield client
    finally:
        server.should_exit = True
        await task
        sock.close()


async def wait_finished(client, session_id, turn_id=None):
    async with client.stream("GET", "/api/events?after=0") as stream:
        async for line in stream.aiter_lines():
            if line.startswith("data:"):
                event = json.loads(line[5:])
                if event.get("session_id") == session_id and event["kind"] == "turn.finished" and (turn_id is None or event["turn_id"] == turn_id):
                    return (await client.get("/api/sessions/" + session_id)).json()
    raise AssertionError("没有收到轮次结束事件")


@asynccontextmanager
async def serving_tools(directory, model, maps, timeout=1, released=None):
    @asynccontextmanager
    async def resources():
        tools=Retrieval(cast(Client,maps),api_key="known-secret",tool_timeout=timeout)
        await tools.discover()
        try:
            yield Runtime(cast(AsyncAnthropic,model),tools,"test-model")
        finally:
            if released: released.set()
    sock=socket.socket();sock.bind(("127.0.0.1",0))
    server=uvicorn.Server(uvicorn.Config(create_app(directory,resources=resources,request_shutdown=lambda: setattr(server,"should_exit",True)),log_level="critical",access_log=False,timeout_graceful_shutdown=1))
    task=asyncio.create_task(server.serve(sockets=[sock]))
    try:
        while not server.started and not task.done(): await asyncio.sleep(0)
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{sock.getsockname()[1]}") as client:
            yield client
    finally:
        server.should_exit=True
        await task
        sock.close()


def tool_reply(count=2):
    return response([{"type":"tool_use","id":f"lake-{i}","name":"maps_text_search",
        "input":{"keywords":"西湖","city":"杭州"}} for i in range(count)],"tool_use")


async def notices(client, path, headers=None):
    result = []
    async with client.stream("GET", path, headers=headers) as stream:
        async for line in stream.aiter_lines():
            if line.startswith("data:"):
                event = json.loads(line[5:])
                result.append(event)
                if event["kind"] == "service.state":
                    return result
    raise AssertionError("没有收到当前服务状态")


async def wait_state(client, predicate):
    async def poll():
        while True:
            state=(await client.get('/api/state')).json()
            if predicate(state): return state
    return await asyncio.wait_for(poll(),3)


__all__ = ['serving', 'wait_finished', 'serving_tools', 'tool_reply', 'notices', 'wait_state']
