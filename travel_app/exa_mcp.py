"""Exa 免费免密钥 MCP 的搜索和正文提取边界。"""
import asyncio
import json
import re
from urllib.parse import urlsplit
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, cast

from anthropic.types import ToolParam
from mcp import Client
from mcp.shared.exceptions import MCPError
from mcp.types import CONNECTION_CLOSED, PARSE_ERROR, REQUEST_TIMEOUT

from travel_app.amap_mcp import ToolResult
from travel_app.limits import SOURCE_CALL_INTERVAL, TOOL_TIMEOUT

EXA_URL = "https://mcp.exa.ai/mcp"
SEARCH_TOOLS = {"web_search_exa"}
EXTRACT_TOOLS = {"web_fetch_exa"}
SOURCE_TOOLS = SEARCH_TOOLS | EXTRACT_TOOLS
SOURCE_FAILURES = {
    "auth": "资料鉴权失败", "rate_limit": "资料服务限流", "quota": "资料免费额度耗尽",
    "connection": "资料连接不可用", "timeout": "资料查询超时", "service": "资料服务暂不可用",
}

UNAVAILABLE_MESSAGE = "网页资料服务暂不可用，未取得的信息不能作为规划依据；其他服务按可用状态使用，不启用付费服务。"


class ExaTools:
    def __init__(self, client: Client, *, redact: Callable[[str], str], tool_timeout: float = TOOL_TIMEOUT, call_interval: float = SOURCE_CALL_INTERVAL):
        self.client = client
        self.redact = redact
        self.tool_timeout = tool_timeout
        self.call_interval = call_interval
        self._next_call_at = 0.0
        self._slots = asyncio.Semaphore(10)
        self._start_lock = asyncio.Lock()
        self.declarations: list[ToolParam] = []
        self.failure: str | None = None

    @property
    def failure_message(self) -> str:
        return SOURCE_FAILURES.get(self.failure or "", "资料查询失败") + "，当前运行暂停资料查询；保留已取得资料，其他服务按可用状态使用，不启用付费服务。"

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
            raise RuntimeError("网页资料服务未提供搜索与正文提取工具。")

    async def call(self, name: str, arguments: dict[str, Any], *,
                   observer: Callable[[str, dict[str, Any]], Awaitable[None]] | None = None,
                   stop_requested: asyncio.Event | None = None) -> ToolResult:
        loop = asyncio.get_running_loop()
        queued_at = loop.time()
        started_at = datetime.now(timezone.utc).isoformat()
        if self._slots.locked() and observer:
            await observer("tool.waiting", {"status": "waiting", "call_started": False,
                "started_at": started_at, "waiting_at": started_at})
        async with self._slots:
            return await self._call(name, arguments, observer=observer, stop_requested=stop_requested,
                                    queued_at=queued_at, started_at=started_at)

    async def _call(self, name: str, arguments: dict[str, Any], *,
                   observer: Callable[[str, dict[str, Any]], Awaitable[None]] | None = None,
                   stop_requested: asyncio.Event | None = None,
                   queued_at: float, started_at: str) -> ToolResult:
        loop = asyncio.get_running_loop()
        started = queued_at
        facts: dict[str, Any] = {"started_at": started_at,
            "provider": "exa", "call_started": False, "wait_duration_ms": max(0,(loop.time()-started)*1000),
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
            return await finish({"content": self.failure_message, "is_error": True, "not_executed": True}, self.failure)
        if self._start_lock.locked() and observer:
            await observer("tool.waiting", {**facts, "status": "waiting",
                "waiting_at": datetime.now(timezone.utc).isoformat()})
        async with self._start_lock:
            if delay := max(0.0, self._next_call_at - loop.time()):
                waiting = loop.time()
                if observer:
                    await observer("tool.waiting", {**facts, "status": "waiting", "waiting_at": datetime.now(timezone.utc).isoformat()})
                if stop_requested:
                    try:
                        await asyncio.wait_for(stop_requested.wait(), delay)
                    except asyncio.TimeoutError:
                        pass
                else:
                    await asyncio.sleep(delay)
                facts["wait_duration_ms"] += max(0, (loop.time() - waiting) * 1000)
            if stop_requested and stop_requested.is_set():
                return await finish({"content": "未执行：已停止本轮查询。", "is_error": True,
                    "not_executed": True, "stop_reason": "已停止本轮查询。"}, "user_stop")
            if self.failure:
                return await finish({"content": self.failure_message, "is_error": True, "not_executed": True}, self.failure)
            facts["wait_duration_ms"] = max(0, (loop.time() - started) * 1000)
            if observer:
                await observer("tool.running", {**facts, "status": "running", "call_started": True,
                    "call_started_at": datetime.now(timezone.utc).isoformat()})
            facts["call_started"] = True
            call_start = loop.time()
            self._next_call_at = call_start + self.call_interval
        try:
            # 一次预算仅发送一次 MCP 请求，不采用 Client 的自动重试。
            result = await asyncio.wait_for(self.client.session.call_tool(name, arguments), self.tool_timeout)
        except Exception as error:
            facts["call_duration_ms"] = max(0, (loop.time() - call_start) * 1000)
            facts["call_finished_at"] = datetime.now(timezone.utc).isoformat()
            error_fact: dict[str, Any] = {"category": type(error).__name__, "message": str(error)}
            category = "source_error"
            if isinstance(error, TimeoutError):
                category = "timeout"
            elif isinstance(error, MCPError):
                status = error.data.get("http_status") if isinstance(error.data, dict) else None
                category = ("auth" if status in (401, 403) else "rate_limit" if status == 429 else
                            "service" if isinstance(status, int) and status >= 500 else
                            "connection" if error.code == CONNECTION_CLOSED else
                            "timeout" if error.code == REQUEST_TIMEOUT else
                            "malformed" if error.code == PARSE_ERROR else "source_error")
                error_fact.update(code=error.code, data=error.data)
            facts["error"] = json.loads(self.redact(json.dumps(error_fact, ensure_ascii=False)))
            if category in SOURCE_FAILURES:
                self.failure = category
            return await finish({"content": self.failure_message if self.failure else UNAVAILABLE_MESSAGE, "is_error": True}, category)
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
        rate_limited = isinstance(result.meta, dict) and result.meta.get("ai.exa/rateLimited") is True
        if rate_limited or any(any(marker in code for marker in ("cap_reached", "rate_limit", "quota", "credit")) for code in codes):
            self.failure = "rate_limit" if rate_limited or any("rate_limit" in code for code in codes) else "quota"
            # 服务返回的付款、注册和奖励指令仅保留在原始轨迹，不作为模型操作要求。
            return await finish({"content": "资料免费额度耗尽或限流，未取得的信息不能作为规划依据；其他服务按可用状态使用，不切换付费或领取奖励额度。", "is_error": True}, self.failure)
        texts = [block.text for block in result.content if block.type == "text"]
        error_text = "\n".join(texts)
        if result.is_error or re.match(r"Error:", error_text, re.IGNORECASE):
            if re.search(r"rate.?limit|too many requests|\b429\b", error_text, re.IGNORECASE):
                self.failure = "rate_limit"
            elif re.search(r"quota|credit.?exhaust|cap.?reached", error_text, re.IGNORECASE):
                self.failure = "quota"
            if self.failure:
                return await finish({"content": self.failure_message, "is_error": True}, self.failure)
            return await finish({"content": UNAVAILABLE_MESSAGE, "is_error": True}, "sdk_error")
        sources, failed = parse_sources(texts, search=name in SEARCH_TOOLS)
        parsed_failure = bool(failed)
        if name in EXTRACT_TOOLS:
            returned_urls = {source["url"] for source in [*sources, *failed]}
            requested_urls = arguments.get("urls", [])
            if isinstance(requested_urls, list):
                failed.extend({"url": url, "error": "该页面未返回可用内容，正文未取得"} for url in requested_urls
                              if isinstance(url, str) and url not in returned_urls)
        if not sources:
            category = "empty" if parsed_failure or not any(text.strip() for text in texts) or any(
                re.search(r"no (?:results|content) found", text, re.IGNORECASE) for text in texts) else "malformed"
            return await finish({"content": "资料未返回可用搜索内容或正文，未取得的信息不能作为规划依据；其他服务按可用状态使用。", "is_error": True}, category)
        content = self.redact(json.dumps({
            "text": texts, "data": result.structured_content,
            "provider": "exa", "sources": sources, "failed_sources": failed,
            "retrieved_at": retrieved_at,
            "document_status": "search_clues" if name in SEARCH_TOOLS else "extraction_result" if any(
                source["content_type"] == "page_body" for source in sources) else "content_excerpt",
            "content_boundary": "正文可能受字符上限截断或条件不完整，不能由缺失内容推断规则；查询时间不是页面更新时间或服务重新抓取时间。",
            "max_characters": arguments.get("maxCharacters", 3000) if name in EXTRACT_TOOLS else None,
        }, ensure_ascii=False))
        return await finish({"content": content, "is_error": False})


def parse_sources(texts: list[str], *, search: bool) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """只识别 Exa 的来源头及内容标记，不把自由文本或生成摘要当作正文。"""
    sources, failed = [], []
    header = r"^Title: (?P<title>[^\n]*)\nURL: (?P<url>[^\n]+)\n" if search else r"^# (?P<title>[^\n]*)\nURL: (?P<url>[^\n]+)\n"
    for text in texts:
        errors = list(re.finditer(r"^Error fetching (?P<url>https?://\S+): (?P<error>[^\n]+)", text, re.MULTILINE))
        failed.extend({"url": error["url"], "error": error["error"]} for error in errors)
        matches = list(re.finditer(header, text, re.MULTILINE))
        for index, match in enumerate(matches):
            url = match["url"].strip()
            try:
                parsed = urlsplit(url)
                if parsed.scheme not in ("http", "https") or not parsed.netloc:
                    continue
            except ValueError:
                continue
            end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
            end = min([end, *(error.start() for error in errors if match.end() <= error.start() < end)])
            rest = text[match.end():end].strip()
            source: dict[str, Any] = {"title": match["title"].strip(), "url": url}
            if search:
                marker = re.search(r"^(Highlights|Text|Summary):[ \t]*\n", rest, re.MULTILINE)
                if marker is None:
                    continue
                published = re.search(r"^Published: (.+)$", rest[:marker.start()], re.MULTILINE)
                if published and published[1].strip() not in ("N/A", "Unknown", ""):
                    source["published_date"] = published[1].strip()
                source["content_type"] = {"Highlights": "highlights", "Text": "search_text", "Summary": "generated_summary"}[marker[1]]
                rest = rest[marker.end():].strip()
            else:
                source["content_type"] = "page_body"
                marker = re.match(r"(Highlights|Summary):[ \t]*\n", rest)
                if marker:
                    source["content_type"] = "highlights" if marker[1] == "Highlights" else "generated_summary"
                    rest = rest[marker.end():].strip()
            # 服务多页分隔符不属于页面正文；内容保留原样，不从正文猜测日期。
            rest = re.sub(r"\n---\s*$", "", rest).strip()
            if rest:
                source["content"] = rest
                sources.append(source)
            else:
                failed.append({"url": url, "error": "来源未返回可用内容"})
    return sources, failed
