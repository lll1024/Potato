"""上下文浏览器验收：正式HTTP/SQLite，外部模型为可控机制替身。"""
import argparse
import asyncio
import json
import os
from pathlib import Path
import socket
import sys
from contextlib import asynccontextmanager
from typing import cast
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import uvicorn
from anthropic import AsyncAnthropic
from mcp import Client
from travel_app.retrieval import Retrieval
from service_fixtures import MapService, response
from travel_app.sessions import Runtime, create_app


class BrowserModel:
    def __init__(self, mode):
        self.messages = self
        self.mode = mode
        self.travel_count = 0

    async def create(self, **kwargs):
        if kwargs['system'].startswith('整理旅行状态'):
            if self.mode == 'interrupted':
                await asyncio.Event().wait()
            if self.mode == 'stop':
                await asyncio.sleep(3)
            value = json.loads(kwargs['messages'][0]['content'])
            refs = [r['record_id'] for r in value['records'] if r['reference_readable']]
            refs += [r['record_id'] for r in (value['previous_summary'] or {}).get('sources', [])]
            return response([{'type': 'text', 'text': json.dumps({
                'travel_conditions': {'budget': '3000元人民币', 'hotel': '湖滨酒店'},
                'confirmed_itinerary': [], 'corrections': [], 'unresolved': ['尚未预订'],
                'sources': [{'record_id': ref, 'verification': '用户要求；酒店未预订'} for ref in sorted(set(refs))],
            }, ensure_ascii=False)}])
        self.travel_count += 1
        if self.travel_count > 1:
            await asyncio.sleep(6)
        return response([{'type': 'text', 'text': '酒店未预订，预算3000元人民币。' if self.travel_count == 1 else '继续保留原条件，酒店仍未预订。'}])


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--data-dir', required=True)
    parser.add_argument('--mode', choices=['success', 'stop', 'interrupted'], required=True)
    args = parser.parse_args()
    os.environ.update(CONTEXT_COMPACTION_ENABLED='true', MODEL_CONTEXT_WINDOW='40000')
    model = BrowserModel(args.mode)
    @asynccontextmanager
    async def resources():
        tools = Retrieval(cast(Client, MapService()))
        await tools.discover()
        yield Runtime(cast(AsyncAnthropic, model), tools, 'test-model')
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    app = create_app(args.data_dir, resources=resources, static_dir=Path(__file__).resolve().parents[1] / 'frontend' / 'dist')
    server = uvicorn.Server(uvicorn.Config(app, log_level='critical', access_log=False, timeout_graceful_shutdown=1))
    task = asyncio.create_task(server.serve(sockets=[sock]))
    while not server.started and not task.done():
        await asyncio.sleep(.01)
    print(f'READY http://127.0.0.1:{sock.getsockname()[1]}', flush=True)
    await task
    sock.close()


if __name__ == '__main__':
    asyncio.run(main())
