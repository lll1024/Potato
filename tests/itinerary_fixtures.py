"""显式测试支撑：只装配公开能力、可控外部响应或指定故障。"""
import json
from pathlib import Path
from types import SimpleNamespace


FIXTURES = Path(__file__).with_name("fixtures")


DECLARATIONS = list({tool["name"]: tool for filename in (
    "amap_route_tools.json", "amap_dining_tools.json", "amap_weather_tools.json"
) for tool in json.loads(FIXTURES.joinpath(filename).read_text())}.values())


WEATHER = {
    "city": "杭州市",
    "forecasts": [{"date": "2026-10-09", "dayweather": "小雨", "nightweather": "阴"}],
}


class ItineraryMapService:
    @property
    def session(self):
        return self

    def __init__(self, outcomes):
        self.outcomes = iter(outcomes)
        self.calls = []

    async def list_tools(self, *, cursor=None):
        return SimpleNamespace(tools=[SimpleNamespace(
            name=tool["name"], description=tool["description"],
            input_schema=tool["input_schema"],
        ) for tool in DECLARATIONS], next_cursor=None)

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        return next(self.outcomes)


def map_result(data=None, *, error=False, text_only=False):
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=json.dumps(data, ensure_ascii=False))],
        structured_content=None if text_only else data, is_error=error,
    )


def tool_call(call_id, name, arguments):
    return {"type": "tool_use", "id": call_id, "name": name, "input": arguments}


__all__ = ['FIXTURES', 'DECLARATIONS', 'WEATHER', 'ItineraryMapService', 'map_result', 'tool_call']
