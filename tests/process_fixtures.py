"""显式测试支撑：只装配公开能力、可控外部响应或指定故障。"""
import asyncio
import httpx
import os
import socket
import sys
import uvicorn
from pathlib import Path
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from mcp.server import MCPServer


ROOT = Path(__file__).resolve().parents[1]


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


class ExternalServices:
    def __init__(self, preference_handler=None, *, model_handler=None, map_result="西湖位于杭州。"):
        self.model_calls = []
        self.map_calls = []
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.block: str | None = None
        self.model_failure = False
        maps = MCPServer('恢复验收地图')

        @maps.tool(name='maps_text_search')
        async def search(keywords: str, city: str) -> str:
            self.map_calls.append((keywords, city))
            if self.block == 'tool':
                self.entered.set()
                await self.release.wait()
            return map_result

        mcp_app = maps.streamable_http_app(json_response=True, stateless_http=True)
        @asynccontextmanager
        async def lifespan(app):
            async with mcp_app.router.lifespan_context(mcp_app):
                yield
        self.app = FastAPI(lifespan=lifespan)
        self.app.mount('/maps', mcp_app)

        @self.app.post('/v1/messages')
        async def message(request: Request):
            data = await request.json()
            self.model_calls.append(data)
            if model_handler:
                return await model_handler(data)
            if self.model_failure:
                return JSONResponse(status_code=429, content={"error": {"type": "rate_limit_error", "message": "slow down"}},
                                    headers={"Retry-After": "10"})
            if preference_handler and data['system'].startswith('旅行者偏好提取'):
                return await preference_handler(data)
            if self.block == 'model':
                self.entered.set()
                await self.release.wait()
            last = data['messages'][-1]['content']
            use_tools = isinstance(last, str) and '地图' in last
            content = ([{'type':'tool_use','id':f'map-{index}','name':'maps_text_search',
                         'input':{'keywords':'西湖','city':'杭州'}} for index in range(2)] if use_tools
                       else [{'type':'text','text':'完整旅行建议。'}])
            return {'id':'response-test','type':'message','role':'assistant','model':'test-model',
                    'content':content,'stop_reason':'tool_use' if use_tools else 'end_turn',
                    'usage':{'input_tokens':3,'output_tokens':2}}

    @asynccontextmanager
    async def serving(self):
        port = free_port()
        server = uvicorn.Server(uvicorn.Config(self.app, host='127.0.0.1', port=port, log_level='critical'))
        task = asyncio.create_task(server.serve())
        try:
            while not server.started and not task.done():
                await asyncio.sleep(0)
            yield f'http://127.0.0.1:{port}'
        finally:
            self.release.set()
            server.should_exit = True
            await task


@asynccontextmanager
async def process(directory, external, *, expected_failure=False, clock_file=None):
    port = free_port()
    env = {**os.environ, 'PYTHONPATH':str(ROOT)}
    arguments = [directory, str(port), external] + ([str(clock_file)] if clock_file else [])
    child = await asyncio.create_subprocess_exec(sys.executable, str(ROOT/'tests/recovery_process.py'),
        *arguments, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, env=env)
    assert child.stdout is not None
    output = b''
    try:
        async with httpx.AsyncClient(base_url=f'http://127.0.0.1:{port}', timeout=3) as client:
            async def ready():
                while child.returncode is None:
                    try:
                        if (await client.get('/api/state')).status_code == 200:
                            return True
                    except httpx.HTTPError:
                        pass
                    await asyncio.sleep(.02)
                return False
            available = await asyncio.wait_for(ready(), 10)
            if expected_failure:
                assert not available, '启动应拒绝此数据目录'
                output = await child.stdout.read()
            else:
                assert available, (await child.stdout.read()).decode()
            yield child, client, output.decode()
    finally:
        if child.returncode is None:
            child.kill()
        await child.wait()


__all__ = ['ROOT', 'free_port', 'ExternalServices', 'process']
