"""资料供应商连接生命周期与可选网页能力发现。"""
from contextlib import AsyncExitStack, asynccontextmanager
from urllib.parse import urlencode

from mcp import Client
from mcp.client.streamable_http import streamable_http_client

from .amap_http import AmapHTTPClient
from .source_connection import connect_sources
from .service import Retrieval
from travel_app.limits import TOOL_TIMEOUT


@asynccontextmanager
async def connected_retrieval(api_key: str, *, secrets: tuple[str, ...] = (),
                              map_url: str | None = None, source_url: str | None = None,
                              include_sources: bool = True):
    async with AsyncExitStack() as stack:
        http = await stack.enter_async_context(AmapHTTPClient(timeout=TOOL_TIMEOUT))
        maps = await stack.enter_async_context(Client(streamable_http_client(
            map_url or "https://mcp.amap.com/mcp?" + urlencode({"key": api_key}), http_client=http),
            read_timeout_seconds=TOOL_TIMEOUT))
        retrieval = Retrieval(maps, api_key=api_key, secrets=secrets, http_client=http,
                              include_source_status=include_sources)
        await retrieval.discover()
        if include_sources:
            await connect_sources(stack, retrieval, source_url)
        yield retrieval
