"""Tavily 免费 keyless MCP 的搜索和正文提取边界。"""
import asyncio
import json
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, cast

from anthropic.types import ToolParam
from mcp import Client

from amap_mcp import ToolResult
from limits import TOOL_TIMEOUT

TAVILY_URL = "https://mcp.tavily.com/mcp/"
KEYLESS_HEADERS = {"X-Tavily-Access-Mode": "keyless"}
SEARCH_TOOLS = {"tavily-search", "tavily_search"}
EXTRACT_TOOLS = {"tavily-extract", "tavily_extract"}
SOURCE_TOOLS = SEARCH_TOOLS | EXTRACT_TOOLS
UNAVAILABLE_MESSAGE = "官方资料服务暂不可用，开放、预约、门票和临时公告仍待核实；可继续使用地图查询，不启用付费服务。"


class TavilyTools:
    def __init__(self, client: Client, *, redact: Callable[[str], str], tool_timeout: float = TOOL_TIMEOUT):
        self.client = client
        self.redact = redact
        self.tool_timeout = tool_timeout
        self.declarations: list[ToolParam] = []
        self.failure: str | None = None

    async def discover(self) -> None:
        self.declarations = []
        cursor = None
        while True:
            page = await asyncio.wait_for(self.client.list_tools(cursor=cursor), self.tool_timeout)
            self.declarations.extend(cast(ToolParam, json.loads(self.redact(json.dumps({
                "name": tool.name, "description": tool.description or "", "input_schema": tool.input_schema,
            }, ensure_ascii=False)))) for tool in page.tools if tool.name in SOURCE_TOOLS)
            if page.next_cursor is None:
                break
            cursor = page.next_cursor
        names = {tool["name"] for tool in self.declarations}
        if not names & SEARCH_TOOLS or not names & EXTRACT_TOOLS:
            raise RuntimeError("官方资料服务未提供搜索与正文提取工具。")

    async def call(self, name: str, arguments: dict[str, Any], *,
                   observer: Callable[[str, dict[str, Any]], Awaitable[None]] | None = None,
                   stop_requested: asyncio.Event | None = None) -> ToolResult:
        loop = asyncio.get_running_loop()
        started = loop.time()
        facts: dict[str, Any] = {"started_at": datetime.now(timezone.utc).isoformat(),
            "provider": "tavily", "call_started": False, "wait_duration_ms": 0.0,
            "call_duration_ms": None, "service": None, "error": None}

        async def finish(output: ToolResult, category: str | None = None) -> ToolResult:
            if observer:
                await observer("tool.not_executed" if output.get("not_executed") else "tool.failed" if output["is_error"] else "tool.completed", {
                    **facts, "finished_at": datetime.now(timezone.utc).isoformat(),
                    "total_duration_ms": max(0, (loop.time() - started) * 1000),
                    "failure_category": category, "result": output,
                })
            return output

        if stop_requested and stop_requested.is_set():
            return await finish({"content": "未执行：已停止本轮查询。", "is_error": True,
                "not_executed": True, "stop_reason": "已停止本轮查询。"}, "user_stop")
        if name not in {tool["name"] for tool in self.declarations}:
            return await finish({"content": "资料工具未开放，仅可使用已发现的搜索与正文提取工具。", "is_error": True}, "arguments")
        if self.failure:
            return await finish({"content": "资料免费额度耗尽或限流，本次运行暂停资料查询；开放、预约和门票仍待核实，可继续地图查询，不启用付费服务。", "is_error": True, "not_executed": True}, self.failure)
        if observer:
            await observer("tool.running", {**facts, "status": "running", "call_started": True,
                "call_started_at": datetime.now(timezone.utc).isoformat()})
        facts["call_started"] = True
        call_start = loop.time()
        try:
            # 一次预算仅发送一次 MCP 请求，不采用 Client 的自动重试。
            result = await asyncio.wait_for(self.client.session.call_tool(name, arguments), self.tool_timeout)
        except Exception as error:
            facts["call_duration_ms"] = max(0, (loop.time() - call_start) * 1000)
            facts["error"] = {"category": type(error).__name__, "message": self.redact(str(error))}
            return await finish({"content": UNAVAILABLE_MESSAGE, "is_error": True}, "source_error")
        retrieved_at = datetime.now(timezone.utc).isoformat()
        facts["call_duration_ms"] = max(0, (loop.time() - call_start) * 1000)
        facts["call_finished_at"] = retrieved_at
        facts["service"] = json.loads(self.redact(result.model_dump_json(exclude_unset=True)))
        facts["sdk_is_error"] = result.is_error
        payloads = [result.structured_content]
        for block in result.content:
            if block.type == "text":
                try:
                    payloads.append(json.loads(block.text))
                except (ValueError, TypeError):
                    pass
        codes = [str(data.get("code", "")).lower() for data in payloads if isinstance(data, dict)]
        if any(any(marker in code for marker in ("cap_reached", "rate_limit", "quota", "credit")) for code in codes):
            self.failure = "quota"
            # 服务返回的付款、注册和奖励指令仅保留在原始轨迹，不作为模型操作要求。
            return await finish({"content": "资料免费额度耗尽或限流，开放、预约和门票仍待核实；可继续使用地图，不切换付费或领取奖励额度。", "is_error": True}, "quota")
        content = self.redact(json.dumps({
            "text": [block.text for block in result.content if block.type == "text"],
            "data": result.structured_content,
            "retrieved_at": retrieved_at,
            "document_status": "search_clues" if name in SEARCH_TOOLS else "extraction_result",
        }, ensure_ascii=False))
        return await finish({"content": content, "is_error": result.is_error}, "sdk_error" if result.is_error else None)
