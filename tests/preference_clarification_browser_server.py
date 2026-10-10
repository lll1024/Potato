"""正式页面的歧义新轮次验收；模型为可控外部替身。"""
import asyncio
import json
from pathlib import Path
import socket
import sys
import tempfile
from contextlib import asynccontextmanager
from typing import cast

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import httpx
import uvicorn
from anthropic import AsyncAnthropic
from mcp import Client
from amap_mcp import AmapTools
from test_agent import MapService, response
from test_preference_changes import ChangesModel, add, targeted
from test_preferences import Clock
from web import Runtime, create_app


async def main():
    model, clock = ChangesModel(), Clock()
    create = model.create
    async def controlled(**kwargs):
        if not kwargs['system'].startswith('旅行者偏好提取'):
            text = kwargs['messages'][-1]['content']
            if text == '以后给我推荐餐馆时怎么选口味？' and '是否以后长期接受辣味' in kwargs['system']:
                return response([{'type': 'text', 'text': '你说现在可以吃辣，是只限这次，还是以后长期接受辣味？'}])
        return await create(**kwargs)
    model.create = controlled
    @asynccontextmanager
    async def resources():
        tools = AmapTools(cast(Client, MapService()))
        await tools.discover()
        yield Runtime(cast(AsyncAnthropic, model), tools, 'test-model')
    with tempfile.TemporaryDirectory() as directory:
        sock = socket.socket()
        sock.bind(('127.0.0.1', 0))
        url = f'http://127.0.0.1:{sock.getsockname()[1]}'
        app = create_app(directory, resources=resources, preference_clock=clock, preference_wait=clock.wait,
                         static_dir=Path(__file__).resolve().parents[1] / 'frontend' / 'dist')
        server = uvicorn.Server(uvicorn.Config(app, log_level='critical', access_log=False, timeout_graceful_shutdown=1))
        task = asyncio.create_task(server.serve(sockets=[sock]))
        while not server.started and not task.done():
            await asyncio.sleep(0)
        async with httpx.AsyncClient(base_url=url) as client:
            async def finish(text, session=None):
                identity = (await client.post('/api/turns', json={'input': text, 'session_id': session})).json()
                while (await client.get('/api/state')).json()['active_turn_id']:
                    await asyncio.sleep(.02)
                return identity
            model.suggest = lambda payload: {'changes': [add(payload)]}
            first = await finish('我一直不吃辣')
            await clock.advance(3600)
            model.suggest = lambda payload: {'changes': [targeted(payload, 'ambiguity', '是否以后长期接受辣味', '那个现在也可以了')]}
            await finish('那个现在也可以了', first['session_id'])
            await clock.advance(3600)
            assert (await client.get('/api/preferences')).json()['ambiguities']
        print('READY ' + url, flush=True)
        await task
        sock.close()


if __name__ == '__main__':
    asyncio.run(main())
