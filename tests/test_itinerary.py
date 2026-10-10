import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import cast
import unittest
from unittest.mock import patch

from anthropic import AsyncAnthropic
from mcp import Client

from agent import agent_loop
from amap_mcp import AmapTools
from test_agent import ModelService, response
from test_dining import tool_call


# 天气契约来自 2026-10-08 高德实际发现；地图返回均为可控测试数据。
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


class ItineraryTests(unittest.IsolatedAsyncioTestCase):
    async def test_fast_batch_keeps_at_most_three_map_requests_in_any_second(self):
        class TimedService(ItineraryMapService):
            def __init__(self):
                super().__init__([map_result(WEATHER) for _ in range(4)])
                self.started = []

            async def call_tool(self, name, arguments):
                self.started.append(asyncio.get_running_loop().time())
                return await super().call_tool(name, arguments)

        service = TimedService()
        tools = AmapTools(cast(Client, service))
        await tools.discover()
        model = ModelService([
            response([tool_call(f"weather-{i}", "maps_weather", {"city": "杭州"})
                      for i in range(4)], "tool_use"),
            response([{"type": "text", "text": "查询完成。"}]),
        ])

        await agent_loop(
            [{"role": "user", "content": "查询杭州天气"}],
            cast(AsyncAnthropic, model), tools, "test-model",
        )

        self.assertEqual(len(service.calls), 4)
        self.assertGreaterEqual(service.started[-1] - service.started[0], 1.0)
        results = model.requests[1]["messages"][-1]["content"]
        self.assertEqual({item["tool_use_id"] for item in results},
                         {"weather-0", "weather-1", "weather-2", "weather-3"})
        self.assertTrue(all(not item["is_error"] for item in results))

    async def test_weather_uses_discovered_contract_and_preserves_forecast_dates(self):
        service = ItineraryMapService([map_result(WEATHER, text_only=True)])
        tools = AmapTools(cast(Client, service))
        await tools.discover()
        model = ModelService([
            response([tool_call("weather", "maps_weather", {"city": "杭州"})], "tool_use"),
            response([{"type": "text", "text": "查询完成。"}]),
        ])

        await agent_loop(
            [{"role": "user", "content": "2026年10月9日起在杭州旅行三天，请结合天气安排。"}],
            cast(AsyncAnthropic, model), tools, "test-model",
        )

        self.assertEqual(service.calls, [("maps_weather", {"city": "杭州"})])
        self.assertEqual(
            {tool["name"]: tool["input_schema"] for tool in model.requests[0]["tools"]},
            {tool["name"]: tool["input_schema"] for tool in DECLARATIONS},
        )
        result = model.requests[1]["messages"][-1]["content"][0]
        self.assertEqual(result["tool_use_id"], "weather")
        self.assertFalse(result["is_error"])
        self.assertEqual(json.loads(json.loads(result["content"])["text"][0]), WEATHER)
        self.assertIsNone(json.loads(result["content"])["data"])

    async def test_three_day_combination_finishes_within_default_budget(self):
        # 每天三个活动、一次餐饮、从用户给定酒店出发的四段交通。
        days = [
            [("test-a", "测试公园甲", "120.11,30.21"),
             ("test-b", "测试展馆乙", "120.12,30.22"),
             ("test-c", "测试街区丙", "120.13,30.23")],
            [("test-d", "测试展馆丁", "120.14,30.24"),
             ("test-e", "测试公园戊", "120.15,30.25"),
             ("test-f", "测试街区己", "120.16,30.26")],
            [("test-g", "测试公园庚", "120.17,30.27"),
             ("test-h", "测试展馆辛", "120.18,30.28"),
             ("test-i", "测试街区壬", "120.19,30.29")],
        ]
        outcomes, replies = [], []
        expected_calls = []
        for day, places in enumerate(days, start=1):
            searches, details = [], []
            for poi_id, name, location in places:
                arguments = {"keywords": name, "city": "杭州", "citylimit": True}
                searches.append(tool_call(poi_id + "-search", "maps_text_search", arguments))
                expected_calls.append(("maps_text_search", arguments))
                outcomes.append(map_result({"pois": [{"id": poi_id, "name": name}]}))
            if day == 1:
                searches.append(tool_call("weather", "maps_weather", {"city": "杭州"}))
                expected_calls.append(("maps_weather", {"city": "杭州"}))
                outcomes.append(map_result(WEATHER))
            replies.append(response(searches, "tool_use"))
            for poi_id, name, location in places:
                details.append(tool_call(poi_id + "-detail", "maps_search_detail", {"id": poi_id}))
                expected_calls.append(("maps_search_detail", {"id": poi_id}))
                outcomes.append(map_result({"id": poi_id, "name": name, "location": location,
                                            "address": "杭州市测试路" + poi_id}))
            replies.append(response(details, "tool_use"))
            dining_location = f"120.2{day},30.3{day}"
            dining_arguments = {"keywords": "餐厅", "location": places[0][2], "radius": "1000"}
            replies.append(response([tool_call(f"dining-{day}", "maps_around_search",
                                               dining_arguments)], "tool_use"))
            expected_calls.append(("maps_around_search", dining_arguments))
            outcomes.append(map_result({"pois": [{"id": f"test-dining-{day}",
                "name": f"测试餐馆{day}", "type": "餐饮服务", "location": dining_location,
                "address": f"杭州市测试街{day}号"}]}))
            locations = ["120.10,30.20", places[0][2], dining_location, places[1][2], places[2][2]]
            routes = []
            for segment, (origin, destination) in enumerate(zip(locations, locations[1:])):
                arguments = {"origin": origin, "destination": destination}
                routes.append(tool_call(f"route-{day}-{segment}", "maps_direction_driving", arguments))
                expected_calls.append(("maps_direction_driving", arguments))
                outcomes.append(map_result({"origin": origin, "destination": destination,
                    "paths": [{"duration": "600", "distance": "2000"}]}))
            replies.append(response(routes, "tool_use"))
        replies.append(response([{"type": "text", "text": "组合查询完成。"}]))
        # 实际模型可能逐条查询；同样的必要查询不能依赖模型批量发起才能完成。
        sequential_replies = [response([block.model_dump(mode="json")], "tool_use")
                              for reply in replies[:-1] for block in reply.content]
        sequential_replies.append(replies[-1])
        for scenario, model_replies in (("批量查询", replies), ("逐条查询", sequential_replies)):
            with self.subTest(scenario=scenario), patch("amap_mcp.MAP_CALL_INTERVAL", 0):
                service = ItineraryMapService(outcomes)
                tools = AmapTools(cast(Client, service))
                await tools.discover()
                model = ModelService(model_replies)
                history = [{"role": "user", "content": "2026年10月9日起在杭州旅行三天，"
                            "每天从酒店高德坐标120.10,30.20出发，驾车，清淡不辣，请安排活动、餐饮及交通。"}]

                answer = await agent_loop(history, cast(AsyncAnthropic, model), tools, "test-model")

                self.assertEqual(answer, "组合查询完成。")
                self.assertEqual(len(service.calls), 34)
                self.assertCountEqual(service.calls, expected_calls)
                self.assertEqual(len(model.requests), len(model_replies))
                results = [item for message in model.requests[-1]["messages"] if message["role"] == "user"
                           and isinstance(message["content"], list) for item in message["content"]]
                self.assertEqual(len(results), 34)
                self.assertTrue(all(not item["is_error"] for item in results))
                self.assertEqual(json.loads(results[3]["content"])["data"], WEATHER)
                self.assertEqual(json.loads(results[-1]["content"])["data"]["paths"][0],
                                 {"duration": "600", "distance": "2000"})

    async def test_replacing_place_reuses_conditions_and_queries_changed_dining_and_routes(self):
        old_place = {"id": "test-old", "name": "测试公园", "location": "120.11,30.21",
                     "address": "杭州市测试路1号"}
        new_place = {"id": "test-new", "name": "测试展馆", "location": "120.12,30.22",
                     "address": "杭州市测试路2号"}
        dining = {"id": "test-vegetarian", "name": "测试素食馆", "type": "餐饮服务",
                  "location": "120.13,30.23", "address": "杭州市测试路3号"}
        routes = [
            {"origin": new_place["location"], "destination": dining["location"]},
            {"origin": dining["location"], "destination": "120.14,30.24"},
        ]
        service = ItineraryMapService([
            map_result({"pois": [old_place]}), map_result({"pois": [new_place]}),
            map_result({"pois": [dining]}),
            *(map_result({**route, "paths": [{"duration": "300", "distance": "400"}]})
              for route in routes),
        ])
        tools = AmapTools(cast(Client, service))
        await tools.discover()
        model = ModelService([
            response([tool_call("old", "maps_text_search",
                                {"keywords": "测试公园", "city": "杭州", "citylimit": True})], "tool_use"),
            response([{"type": "text", "text": "第一天草案已给出，可继续修改。"}]),
            response([tool_call("new", "maps_text_search",
                                {"keywords": "测试展馆", "city": "杭州", "citylimit": True})], "tool_use"),
            response([tool_call("dining", "maps_around_search",
                                {"keywords": "素食", "location": "120.12,30.22", "radius": "1000"})],
                     "tool_use"),
            response([tool_call(f"route-{i}", "maps_direction_walking", arguments)
                      for i, arguments in enumerate(routes)], "tool_use"),
            response([{"type": "text", "text": "修改查询完成。"}]),
        ])
        history = [{"role": "user", "content": "2026年10月9日起杭州三日旅行，清淡不辣，"
                    "第一天从测试公园开始，最后去高德坐标120.14,30.24的活动地点。"}]
        await agent_loop(history, cast(AsyncAnthropic, model), tools, "test-model")
        history.append({"role": "user", "content": "第一天改为测试展馆，午餐换成素食，其他条件沿用。"})
        previous = json.loads(json.dumps(history))

        await agent_loop(history, cast(AsyncAnthropic, model), tools, "test-model")

        self.assertEqual(model.requests[2]["messages"], previous)
        self.assertEqual(model.requests[-1]["messages"][:len(previous)], previous)
        self.assertCountEqual(service.calls[1:], [
            ("maps_text_search", {"keywords": "测试展馆", "city": "杭州", "citylimit": True}),
            ("maps_around_search", {"keywords": "素食", "location": "120.12,30.22", "radius": "1000"}),
            *(("maps_direction_walking", arguments) for arguments in routes),
        ])
        results = model.requests[-1]["messages"][-1]["content"]
        self.assertEqual({item["tool_use_id"] for item in results}, {"route-0", "route-1"})
        self.assertTrue(all(not item["is_error"] for item in results))
        returned_routes = [json.loads(item["content"])["data"] for item in results]
        self.assertEqual(returned_routes[0]["origin"], "120.12,30.22")
        self.assertEqual(returned_routes[1]["destination"], "120.14,30.24")

    async def test_one_day_plan_preserves_weather_range_and_partial_route_failure(self):
        place = {"id": "test-park", "name": "测试公园", "location": "120.11,30.21"}
        dining = {"id": "test-dining", "name": "测试餐馆", "type": "餐饮服务",
                  "location": "120.12,30.22", "address": "杭州市测试路1号"}
        route_arguments = {"origin": place["location"], "destination": dining["location"]}
        for weather_result in (map_result(WEATHER), map_result("天气查询失败", error=True)):
            with self.subTest(weather_failed=weather_result.is_error):
                service = ItineraryMapService([
                    map_result({"pois": [place]}), weather_result,
                    map_result({"pois": [dining]}), map_result("交通查询失败", error=True),
                ])
                tools = AmapTools(cast(Client, service))
                await tools.discover()
                model = ModelService([
                    response([
                        tool_call("place", "maps_text_search", {"keywords": "测试公园", "city": "杭州"}),
                        tool_call("weather", "maps_weather", {"city": "330100"}),
                    ], "tool_use"),
                    response([tool_call("dining", "maps_around_search",
                                        {"keywords": "餐厅", "location": place["location"]})], "tool_use"),
                    response([tool_call("route", "maps_direction_walking", route_arguments)], "tool_use"),
                    response([{"type": "text", "text": "查询处理完成。"}]),
                ])
                history = [{"role": "user", "content": "2026年11月1日杭州一天旅行，从测试公园开始，"
                            "推荐途中午餐并查询交通与天气。"}]

                await agent_loop(history, cast(AsyncAnthropic, model), tools, "test-model")

                self.assertEqual(len(service.calls), 4)
                self.assertIn(("maps_weather", {"city": "330100"}), service.calls)
                self.assertIn(("maps_direction_walking", route_arguments), service.calls)
                results = [item for message in model.requests[-1]["messages"]
                           if message["role"] == "user" and isinstance(message["content"], list)
                           for item in message["content"]]
                self.assertEqual([item["tool_use_id"] for item in results],
                                 ["place", "weather", "dining", "route"])
                self.assertEqual([item["is_error"] for item in results],
                                 [False, weather_result.is_error, False, True])
                self.assertEqual(json.loads(results[0]["content"])["data"]["pois"], [place])
                self.assertEqual(json.loads(results[2]["content"])["data"]["pois"], [dining])
                if not weather_result.is_error:
                    self.assertEqual(json.loads(results[1]["content"])["data"], WEATHER)
