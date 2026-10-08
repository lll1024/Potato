"""真实子进程仅替换外部模型与地图地址，复用生产执行和保存路径。"""
import sys
from contextlib import asynccontextmanager

import uvicorn
from anthropic import AsyncAnthropic
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

from amap_http import AmapHTTPClient
from amap_mcp import AmapTools
from web import Runtime, create_app


@asynccontextmanager
async def resources():
    async with (
        AmapHTTPClient() as map_http,
        Client(streamable_http_client(sys.argv[3] + '/maps/mcp', http_client=map_http)) as maps,
        AsyncAnthropic(api_key='test-key', base_url=sys.argv[3], max_retries=0, timeout=60) as model,
    ):
        tools = AmapTools(maps, http_client=map_http)
        await tools.discover()
        yield Runtime(model, tools, 'test-model')


if __name__ == '__main__':
    app = create_app(sys.argv[1], resources=resources)
    server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=int(sys.argv[2]),
                                         log_level='critical', access_log=False, timeout_graceful_shutdown=1))
    try:
        server.run()
    except SystemExit:
        print(getattr(app.state, 'startup_error', '服务启动失败。'))
        raise SystemExit(1)
    if not server.started:
        print(getattr(app.state, 'startup_error', '服务启动失败。'))
        raise SystemExit(1)
