"""共享地图与官方资料的工具声明、分派及连接生命周期。"""
from contextlib import AsyncExitStack, asynccontextmanager

import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

from amap_mcp import AmapTools
from limits import TOOL_TIMEOUT
from tavily_mcp import KEYLESS_HEADERS, SOURCE_TOOLS, TAVILY_URL, TavilyTools, UNAVAILABLE_MESSAGE


class TravelTools:
    def __init__(self, amap: AmapTools, sources: TavilyTools | None = None):
        self.amap = amap
        self.sources = sources
        self.declarations = [*amap.declarations, *(sources.declarations if sources else [])]

    @property
    def source_status(self):
        if self.sources and self.sources.failure == "quota":
            return "资料免费额度耗尽或限流，当前运行暂停资料查询；可继续地图查询，不切换付费。"
        return "官方资料搜索与正文提取可用。" if self.sources else UNAVAILABLE_MESSAGE

    @property
    def failure(self):
        return self.amap.failure

    def redact(self, text: str) -> str:
        return self.amap.redact(text)

    async def call(self, name, arguments, *, observer=None, stop_requested=None):
        if name in SOURCE_TOOLS and self.sources:
            return await self.sources.call(name, arguments, observer=observer, stop_requested=stop_requested)
        return await self.amap.call(name, arguments, observer=observer, stop_requested=stop_requested)


@asynccontextmanager
async def connected_travel_tools(amap: AmapTools):
    async with AsyncExitStack() as stack:
        sources = None
        try:
            # 固定免费 keyless 端点，不读取 API Key，也不携带 Authorization。
            http_client = await stack.enter_async_context(httpx2.AsyncClient(timeout=TOOL_TIMEOUT, headers=KEYLESS_HEADERS))
            client = await stack.enter_async_context(Client(streamable_http_client(TAVILY_URL, http_client=http_client), read_timeout_seconds=TOOL_TIMEOUT))
            candidate = TavilyTools(client, redact=amap.redact)
            await candidate.discover()
            sources = candidate
        except Exception:
            # 资料初始化失败不阻止既有地图能力启动。
            pass
        yield TravelTools(amap, sources)
