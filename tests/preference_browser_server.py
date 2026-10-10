"""偏好正式页面验收：真实生命周期/SQLite，模型及文件时钟是外部资源替身。"""
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
from test_preferences import PreferenceModel
from web import Runtime, create_app


async def main():
    clock_file, release_file = Path(sys.argv[1]), Path(sys.argv[2])
    model = PreferenceModel()
    original = model.create
    failed_calls = 0
    async def controlled(**kwargs):
        nonlocal failed_calls
        if kwargs['system'].startswith('旅行者偏好提取'):
            inputs = json.loads(kwargs['messages'][0]['content'])['new_inputs']
            if '博物馆' in inputs[0]['input']:
                failed_calls += 1
                while not release_file.exists():
                    await asyncio.sleep(.02)
                return response([{'type': 'text', 'text': '模型返回无效JSON'}])
        return await original(**kwargs)
    model.create = controlled
    @asynccontextmanager
    async def resources():
        tools = AmapTools(cast(Client, MapService()))
        await tools.discover()
        try:
            yield Runtime(cast(AsyncAnthropic, model), tools, 'test-model')
        finally:
            print('COUNTS ' + json.dumps({'failed_extract': failed_calls}), flush=True)
    async def wait(_delay):
        await asyncio.sleep(.02)
    with tempfile.TemporaryDirectory() as directory:
        sock = socket.socket()
        sock.bind(('127.0.0.1', 0))
        url = f'http://127.0.0.1:{sock.getsockname()[1]}'
        app = create_app(directory, resources=resources, preference_clock=lambda: float(clock_file.read_text()),
                         preference_wait=wait, static_dir=Path(__file__).resolve().parents[1] / 'frontend' / 'dist')
        server = uvicorn.Server(uvicorn.Config(app, log_level='critical', access_log=False, timeout_graceful_shutdown=1))
        task = asyncio.create_task(server.serve(sockets=[sock]))
        while not server.started and not task.done():
            await asyncio.sleep(0)
        async with httpx.AsyncClient(base_url=url) as client:
            await client.post('/api/turns', json={'input': '我长期不吃辣'})
            while (await client.get('/api/state')).json()['active_turn_id']:
                await asyncio.sleep(.02)
            replacement = clock_file.with_suffix('.next')
            replacement.write_text('13600')
            replacement.replace(clock_file)
            while not (await client.get('/api/preferences')).json()['preferences']:
                await asyncio.sleep(.02)
            await client.post('/api/turns', json={'input': '我长期喜欢博物馆'})
            while (await client.get('/api/state')).json()['active_turn_id']:
                await asyncio.sleep(.02)
        print('READY ' + url, flush=True)
        await task
        sock.close()


if __name__ == '__main__':
    asyncio.run(main())
