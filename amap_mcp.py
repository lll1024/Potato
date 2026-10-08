import asyncio
import json
import re
from typing import Any, NotRequired, TypedDict, cast
from urllib.parse import quote, quote_plus

from anthropic.types import ToolParam
import httpx2
from mcp import Client
from mcp.shared.exceptions import MCPError
from mcp.types import CONNECTION_CLOSED, INVALID_PARAMS, REQUEST_TIMEOUT

from limits import MAP_CALL_INTERVAL, TOOL_TIMEOUT
from amap_http import AmapHTTPClient

PLACE_TOOLS = {"maps_text_search", "maps_search_detail"}
DINING_TOOLS = {"maps_around_search"}
WEATHER_TOOLS = {"maps_weather"}
ROUTE_TOOLS = {
    "maps_geo", "maps_regeocode", "maps_distance",
    "maps_direction_walking", "maps_direction_bicycling",
    "maps_direction_driving", "maps_direction_transit_integrated",
}


class ToolResult(TypedDict):
    content: str
    is_error: bool
    stop_reason: NotRequired[str]


FAILURE_MESSAGES = {
    "auth": "地图鉴权失败，已停止查询。请检查 AMAP_MAPS_API_KEY 是否为有效的 Web 服务 Key、权限及白名单，修正后重启。",
    "quota": "地图额度耗尽或访问受限，已停止查询。请在高德控制台检查额度及限流，恢复后重启。",
    "connection": "地图连接不可用，已停止查询并结束运行。请检查网络及高德服务状态后重新运行。",
    "service": "地图服务暂不可用，已停止查询。请检查高德服务状态，恢复后重启。",
}
# 高德 Web 服务错误码：README 中附官方来源。
AUTH_CODES = {"10001", "10002", "10005", "10006", "10007", "10008", "10009",
              "10012", "10013", "10026", "10041", "40002"}
QUOTA_CODES = {"10003", "10004", "10010", "10014", "10015", "10019", "10020",
               "10021", "10029", "10044", "10045", "40000", "40003"}
AUTH_ERRORS = {"INVALID_USER_KEY", "SERVICE_NOT_AVAILABLE", "INVALID_USER_IP",
               "INVALID_USER_DOMAIN", "INVALID_USER_SIGNATURE", "INVALID_USER_SCODE",
               "USERKEY_PLAT_NOMATCH", "INSUFFICIENT_PRIVILEGES", "USER_KEY_RECYCLED",
               "NO_EFFECTIVE_INTERFACE", "SERVICE_EXPIRED", "UNAUTHORIZED"}
QUOTA_ERRORS = {"DAILY_QUERY_OVER_LIMIT", "ACCESS_TOO_FREQUENT", "IP_QUERY_OVER_LIMIT",
                "QPS_HAS_EXCEEDED_THE_LIMIT", "GATEWAY_TIMEOUT", "CQPS_HAS_EXCEEDED_THE_LIMIT",
                "CKQPS_HAS_EXCEEDED_THE_LIMIT", "CUQPS_HAS_EXCEEDED_THE_LIMIT",
                "ABROAD_DAILY_QUERY_OVER_LIMIT", "USER_DAILY_QUERY_OVER_LIMIT",
                "USER_ABROAD_DAILY_QUERY_OVER_LIMIT", "QUOTA_PLAN_RUN_OUT", "ABROAD_QUOTA_PLAN_RUN_OUT"}


def http_failure(status: object) -> str | None:
    if status in (401, 403):
        return "auth"
    if status == 429:
        return "quota"
    if isinstance(status, int) and status >= 500:
        return "service"
    return None


def exception_failure(error: BaseException) -> str | None:
    if isinstance(error, BaseExceptionGroup):
        failures = [exception_failure(item) for item in error.exceptions]
        return next((kind for kind in failures if kind in FAILURE_MESSAGES),
                    next((kind for kind in failures if kind), None))
    if isinstance(error, (TimeoutError, httpx2.TimeoutException)):
        return "timeout"
    if isinstance(error, MCPError):
        if isinstance(error.data, dict):
            if failure := http_failure(error.data.get("http_status")):
                return failure
        return {CONNECTION_CLOSED: "connection", REQUEST_TIMEOUT: "timeout",
                INVALID_PARAMS: "arguments"}.get(error.code)
    if isinstance(error, httpx2.HTTPStatusError):
        return http_failure(error.response.status_code)
    if isinstance(error, (ConnectionError, OSError, httpx2.TransportError)):
        return "connection"
    if isinstance(error, (ValueError, TypeError)):
        return "arguments"
    return None


def service_errors(data: Any) -> list[dict[str, Any]]:
    """识别 JSON 中高德自己的失败状态，即使 MCP 未设置 is_error。"""
    errors = []
    if isinstance(data, dict):
        if str(data.get("status")) == "0" or (
            "infocode" in data and str(data["infocode"]) != "10000"
        ):
            errors.append(data)
        for value in data.values():
            errors.extend(service_errors(value))
    elif isinstance(data, list):
        for value in data:
            errors.extend(service_errors(value))
    return errors


class AmapTools:
    def __init__(
        self, client: Client, *, api_key: str = "", tool_timeout: float = TOOL_TIMEOUT,
        secrets: tuple[str, ...] = (),
        http_client: AmapHTTPClient | None = None,
    ):
        self.client = client
        self.http_client = http_client
        self._secrets = (api_key, *secrets)
        self.tool_timeout = tool_timeout
        self.declarations: list[ToolParam] = []
        self.failure: str | None = None
        self._next_call_at = 0.0

    async def discover(self) -> None:
        self.declarations = []
        cursor = None
        while True:
            page = await asyncio.wait_for(
                self.client.list_tools(cursor=cursor), timeout=self.tool_timeout
            )
            self.declarations.extend(
                cast(ToolParam, json.loads(self.redact(json.dumps({
                    "name": tool.name,
                    "description": tool.description or "",
                    "input_schema": tool.input_schema,
                }, ensure_ascii=False))))
                for tool in page.tools
                if tool.name in PLACE_TOOLS | ROUTE_TOOLS | DINING_TOOLS | WEATHER_TOOLS
            )
            if page.next_cursor is None:
                break
            cursor = page.next_cursor
        if not any(tool["name"] in PLACE_TOOLS for tool in self.declarations):
            raise RuntimeError("高德未提供可用的地点查询工具，请检查服务配置。")

    async def call(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        if name not in {tool["name"] for tool in self.declarations}:
            return {"content": "工具未开放，仅可调用已发现的地点、餐饮、交通与天气查询工具。", "is_error": True}
        if self.failure:
            return self._stop(self.failure)
        if self.http_client and self.http_client.connection_failed:
            return self._stop("connection")
        loop = asyncio.get_running_loop()
        if delay := max(0.0, self._next_call_at - loop.time()):
            await asyncio.sleep(delay)
        self._next_call_at = loop.time() + MAP_CALL_INTERVAL
        try:
            result = await asyncio.wait_for(
                # Client.call_tool 会自动重试或多轮交互；一次预算只发送一次工具请求。
                self.client.session.call_tool(name, arguments), timeout=self.tool_timeout
            )
        except Exception as error:
            if self.http_client and self.http_client.connection_failed:
                return self._stop("connection")
            failure = exception_failure(error)
            if failure in FAILURE_MESSAGES:
                return self._stop(cast(str, failure))
            if failure == "timeout":
                return {"content": "地图查询超时，结果待核实。", "is_error": True}
            if failure == "arguments":
                return {"content": "地图查询参数错误，请按工具声明修正，结果待核实。", "is_error": True}
            return {"content": "地图查询失败，请检查参数或服务状态，结果待核实。", "is_error": True}
        if self.http_client and self.http_client.connection_failed:
            return self._stop("connection")
        text = [block.text for block in result.content if block.type == "text"]
        errors = service_errors(result.structured_content)
        for value in text:
            try:
                errors.extend(service_errors(json.loads(value)))
            except json.JSONDecodeError:
                pass
        for error in errors:
            code = str(error.get("infocode"))
            if code in AUTH_CODES:
                return self._stop("auth")
            if code in QUOTA_CODES:
                return self._stop("quota")
            if code in {"10016", "10017"}:
                return self._stop("service")
        if result.is_error or errors:
            error_text = json.dumps([text, errors, result.structured_content], ensure_ascii=False).upper()
            tokens = set(re.findall(r"[A-Z_]+", error_text))
            if tokens & AUTH_ERRORS:
                return self._stop("auth")
            if tokens & QUOTA_ERRORS:
                return self._stop("quota")
            if tokens & {"SERVER_IS_BUSY", "RESOURCE_UNAVAILABLE"}:
                return self._stop("service")
        content = json.dumps(
            {"text": text, "data": result.structured_content}, ensure_ascii=False
        )
        return {"content": self.redact(content), "is_error": result.is_error or bool(errors)}

    def _stop(self, failure: str) -> ToolResult:
        self.failure = failure
        message = FAILURE_MESSAGES[failure]
        return {"content": message, "is_error": True, "stop_reason": message}

    def redact(self, text: str) -> str:
        variants = {value for secret in self._secrets if secret
                    for value in (secret, quote(secret, safe=""), quote_plus(secret, safe=""))}
        fields = {"key", "api_key", "apikey", "access_token", "token", "auth",
                  "authorization", "password", "secret", "auth_token"}

        def redact_text(value: str) -> str:
            for secret in sorted(variants, key=len, reverse=True):
                value = value.replace(secret, "[REDACTED]")
            value = re.sub(
                r'https?://[^\s<>"\'\\]+',
                lambda match: "[REDACTED_URL]" if re.search(
                    r"//[^/]*@|[?&](?:key|api[_-]?key|access_token|token|auth|password)=",
                    match[0], re.IGNORECASE,
                ) else match[0], value,
            )
            # 普通文字中的 JSON 凭据字段也须隐藏，例如用户粘贴配置片段。
            return re.sub(
                r'("(?:key|api[_-]?key|access_token|token|auth|authorization|password|secret|auth_token)"\s*:\s*)"(?:[^"\\]|\\.)*"',
                lambda match: match[1] + '"[REDACTED]"', value, flags=re.IGNORECASE,
            )

        def clean(value):
            if isinstance(value, dict):
                return {key: "[REDACTED]" if key.lower().replace("-", "_") in fields
                        else clean(item) for key, item in value.items()}
            if isinstance(value, list):
                return [clean(item) for item in value]
            if isinstance(value, str):
                return self.redact(value)
            return value

        try:
            parsed = json.loads(text)
        except (json.JSONDecodeError, ValueError):
            return redact_text(text)
        if isinstance(parsed, (dict, list)):
            return json.dumps(clean(parsed), ensure_ascii=False)
        return redact_text(text)
