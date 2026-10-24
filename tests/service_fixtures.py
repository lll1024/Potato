"""可控外部模型与地图响应；不访问业务 implementation。"""
import copy
from types import SimpleNamespace
from anthropic.types import Message


def response(content, stop_reason="end_turn"):
    return Message.model_validate(
        {
            "id": "message-test",
            "type": "message",
            "role": "assistant",
            "model": "test-model",
            "stop_reason": stop_reason,
            "stop_sequence": None,
            "usage": {"input_tokens": 1, "output_tokens": 1},
            "content": content,
        }
    )


class ModelService:
    def __init__(self, replies):
        self.messages = self
        self.replies = iter(replies)
        self.requests = []

    async def create(self, **kwargs):
        self.requests.append(copy.deepcopy(kwargs))
        return next(self.replies)


class MapService:
    @property
    def session(self):
        return self

    async def list_tools(self, *, cursor=None):
        return SimpleNamespace(
            tools=[
                SimpleNamespace(
                    name="maps_text_search",
                    description="按城市和关键词检索地点",
                    input_schema={
                        "type": "object",
                        "properties": {
                            "keywords": {"type": "string"},
                            "city": {"type": "string"},
                        },
                        "required": ["keywords", "city"],
                    },
                )
            ],
            next_cursor=None,
        )

    async def call_tool(self, name, arguments):
        if (name, arguments) != (
            "maps_text_search", {"keywords": "西湖", "city": "杭州"}
        ):
            raise AssertionError("地图服务收到了错误的名称或参数")
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text="查到一个地点")],
            structured_content={
                "pois": [{"id": "poi-west-lake", "name": "西湖", "address": "杭州市西湖区"}]
            },
            is_error=False,
        )



__all__ = ["MapService", "ModelService", "response", "cli_retrieval"]


def cli_retrieval(map_factory, source_factory=None, *, source_http_factory=None):
    """CLI公开装配的可控客户端资源，保留实际查询adapter。"""
    from contextlib import AsyncExitStack, asynccontextmanager
    from mcp import Client
    from mcp.client.streamable_http import streamable_http_client
    from travel_app.retrieval import Retrieval
    @asynccontextmanager
    async def resources(api_key, *, include_sources=True, secrets=()):
        async with AsyncExitStack() as stack:
            maps = await stack.enter_async_context(map_factory())
            sources = None
            if include_sources:
                try:
                    if isinstance(source_factory, Exception):
                        raise source_factory
                    if source_http_factory:
                        http = await stack.enter_async_context(source_http_factory(timeout=30))
                        sources = await stack.enter_async_context(Client(streamable_http_client(
                            "https://mcp.exa.ai/mcp", http_client=http), read_timeout_seconds=30))
                    elif source_factory:
                        sources = await stack.enter_async_context(source_factory())
                except Exception:
                    pass
            tools = Retrieval(maps, source_client=sources, api_key=api_key, secrets=secrets,
                              include_source_status=include_sources)
            await tools.discover()
            yield tools
    return resources
