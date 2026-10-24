"""网页资料的可选连接；失败不影响地图初始化。"""
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from .source_http import SourceHTTPClient
from .exa_mcp import EXA_URL, ExaTools
from travel_app.limits import TOOL_TIMEOUT


async def connect_sources(stack, retrieval, source_url=None):
    try:
        http = await stack.enter_async_context(SourceHTTPClient(timeout=TOOL_TIMEOUT))
        client = await stack.enter_async_context(Client(streamable_http_client(
            source_url or EXA_URL, http_client=http), read_timeout_seconds=TOOL_TIMEOUT))
        candidate = ExaTools(client, redact=retrieval.redact)
        await candidate.discover()
        retrieval._sources = candidate
    except Exception:
        pass
