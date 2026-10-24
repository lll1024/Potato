"""外部资料的能力说明与有序分段执行；历史回读不属于本能力。"""
import asyncio
import copy
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from anthropic.types import ToolParam
from mcp import Client

from .amap_mcp import AmapTools, FAILURE_MESSAGES, ToolResult
from .amap_http import AmapHTTPClient
from .exa_mcp import ExaTools, SOURCE_TOOLS, UNAVAILABLE_MESSAGE
from travel_app.limits import MAP_CALL_INTERVAL, SOURCE_CALL_INTERVAL, TOOL_TIMEOUT

Observer = Callable[[str, dict[str, Any]], Awaitable[None]]


@dataclass(frozen=True)
class Query:
    query_id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class QueryResult:
    query_id: str
    content: str
    is_error: bool
    not_executed: bool = False


@dataclass(frozen=True)
class Capabilities:
    declarations: list[ToolParam]
    map_status: str
    source_status: str | None
    map_pause_reason: str | None


class Retrieval:
    def __init__(self, map_client: Client, *, source_client: Client | None = None,
                 api_key: str = "", secrets: tuple[str, ...] = (),
                 http_client: AmapHTTPClient | None = None, tool_timeout: float = TOOL_TIMEOUT,
                 source_timeout: float = TOOL_TIMEOUT, source_interval: float = SOURCE_CALL_INTERVAL,
                 map_interval: float = MAP_CALL_INTERVAL,
                 include_source_status: bool = False):
        self._map = AmapTools(map_client, api_key=api_key, secrets=secrets,
                              http_client=http_client, tool_timeout=tool_timeout, call_interval=map_interval)
        self._sources = ExaTools(source_client, redact=self._map.redact, tool_timeout=source_timeout,
                                 call_interval=source_interval) if source_client else None
        self._include_source_status = include_source_status or source_client is not None

    async def discover(self) -> None:
        await self._map.discover()
        if self._sources:
            try:
                await self._sources.discover()
            except Exception:
                self._sources = None

    @property
    def capabilities(self) -> Capabilities:
        sources = self._sources
        source_status = (sources.failure_message if sources and sources.failure else
            "网页资料搜索与正文提取可用，可按规划需要获取资料；仅在用户明确要求时专项核实。" if sources else UNAVAILABLE_MESSAGE)
        return Capabilities(copy.deepcopy([*self._map.declarations, *(sources.declarations if sources else [])]),
                            FAILURE_MESSAGES.get(self._map.failure or "", ""),
                            source_status if self._include_source_status else None, self._map.failure)

    def redact(self, text: str) -> str:
        return self._map.redact(text)

    async def execute(self, queries: list[Query], *, observer: Observer | None = None,
                      stop_requested: asyncio.Event | None = None) -> list[QueryResult]:
        """每项保留身份和位置；事实保存失败先停止新启、收齐在途，再传播。"""
        stop = stop_requested if stop_requested is not None else asyncio.Event()
        queries = copy.deepcopy(queries)

        async def execute_one(query):
            async def observe(kind, data):
                if observer:
                    try:
                        await observer(kind, copy.deepcopy({**data, "query_id": query.query_id}))
                    except Exception:
                        stop.set()
                        raise
            if stop.is_set():
                output: ToolResult = {"content": "未执行：已停止本轮查询。", "is_error": True, "not_executed": True}
                await observe("tool.not_executed", {"status": "not_executed", "reason": "user_stop",
                    "finished_at": datetime.now(timezone.utc).isoformat(), "result": output})
            else:
                provider = self._sources if query.name in SOURCE_TOOLS and self._sources else self._map
                output = await provider.call(query.name, query.arguments, observer=observe, stop_requested=stop)
            return QueryResult(query.query_id, output["content"], output["is_error"], output.get("not_executed", False))

        completed = await asyncio.gather(*(execute_one(query) for query in queries), return_exceptions=True)
        for output in completed:
            if isinstance(output, BaseException):
                raise output
        return [output for output in completed if isinstance(output, QueryResult)]
