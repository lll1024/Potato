import asyncio
import json
from typing import Any, TypedDict, cast
from urllib.parse import quote

from anthropic.types import ToolParam
from mcp import Client

PLACE_TOOLS = {"maps_text_search", "maps_search_detail"}
DINING_TOOLS = {"maps_around_search"}
ROUTE_TOOLS = {
    "maps_geo", "maps_regeocode", "maps_distance",
    "maps_direction_walking", "maps_direction_bicycling",
    "maps_direction_driving", "maps_direction_transit_integrated",
}
TOOL_TIMEOUT = 30.0


class ToolResult(TypedDict):
    content: str
    is_error: bool


class AmapTools:
    def __init__(
        self, client: Client, *, api_key: str = "", tool_timeout: float = TOOL_TIMEOUT
    ):
        self.client = client
        self._api_key = api_key
        self.tool_timeout = tool_timeout
        self.declarations: list[ToolParam] = []

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
                for tool in page.tools if tool.name in PLACE_TOOLS | ROUTE_TOOLS | DINING_TOOLS
            )
            if page.next_cursor is None:
                break
            cursor = page.next_cursor
        if not any(tool["name"] in PLACE_TOOLS for tool in self.declarations):
            raise RuntimeError("高德未提供可用的地点查询工具，请检查服务配置。")

    async def call(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        if name not in {tool["name"] for tool in self.declarations}:
            return {"content": "工具未开放，仅可调用已发现的地点、餐饮与交通查询工具。", "is_error": True}
        try:
            result = await asyncio.wait_for(
                self.client.call_tool(name, arguments), timeout=self.tool_timeout
            )
        except TimeoutError:
            return {"content": "地图查询超时，结果待核实。", "is_error": True}
        except Exception:
            return {"content": "地图查询失败，请检查参数或服务状态，结果待核实。", "is_error": True}
        text = [block.text for block in result.content if block.type == "text"]
        content = json.dumps(
            {"text": text, "data": result.structured_content}, ensure_ascii=False
        )
        return {"content": self.redact(content), "is_error": result.is_error}

    def redact(self, text: str) -> str:
        if not self._api_key:
            return text
        for secret in {self._api_key, quote(self._api_key, safe="")}:
            text = text.replace(secret, "[REDACTED]")
        return text
