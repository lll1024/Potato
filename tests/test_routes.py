import json
from pathlib import Path
from types import SimpleNamespace
from typing import cast
import unittest

from anthropic import AsyncAnthropic
from mcp import Client

from agent import agent_loop
from amap_mcp import AmapTools
from test_agent import ModelService, response


# 2026-10-08 从高德实际 list_tools 返回中保留的本任务工具契约。
DECLARATIONS = json.loads(
    Path(__file__).with_name("fixtures").joinpath("amap_route_tools.json").read_text()
)
ORIGIN = "120.212677,30.290948"
DESTINATION = "120.158291,30.250123"


def tool_call(call_id, name, arguments):
    return {"type": "tool_use", "id": call_id, "name": name, "input": arguments}


class RouteMapService:
    def __init__(self, *, failed_route=None, ambiguous=False):
        self.calls = []
        self.failed_route = failed_route
        self.ambiguous = ambiguous
        self.locations = set()

    async def list_tools(self, *, cursor=None):
        tools = [SimpleNamespace(
            name=tool["name"], description=tool["description"],
            input_schema=tool["input_schema"],
        ) for tool in DECLARATIONS]
        tools.append(SimpleNamespace(
            name="maps_schema_take_taxi", description="唤起打车",
            input_schema={"type": "object"},
        ))
        return SimpleNamespace(tools=tools, next_cursor=None)

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        if name == "maps_text_search":
            if arguments != {"keywords": "杭州东站", "city": "杭州", "citylimit": True}:
                raise AssertionError("地点搜索参数不正确")
            data = {"pois": [{"id": "east-station", "name": "杭州东站",
                              "address": "天城路1号", "location": ORIGIN}]}
            if self.ambiguous:
                data["pois"].append({"id": "east-station-square", "name": "杭州东站东广场",
                                     "address": "新塘路", "location": "120.216,30.291"})
            else:
                self.locations.add(ORIGIN)
        elif name == "maps_geo":
            if arguments != {"address": "杭州市上城区湖滨路1号", "city": "杭州"}:
                raise AssertionError("地理编码参数不正确")
            data = {"geocodes": [{"formatted_address": "浙江省杭州市上城区湖滨路1号",
                                  "city": "杭州市", "location": DESTINATION}]}
            self.locations.add(DESTINATION)
        elif name in {"maps_direction_walking", "maps_direction_bicycling",
                      "maps_direction_driving", "maps_direction_transit_integrated"}:
            if not {ORIGIN, DESTINATION}.issubset(self.locations):
                raise AssertionError("必须先确定实际地点坐标再查询交通路线")
            expected = {"origin": ORIGIN, "destination": DESTINATION}
            if name == "maps_direction_transit_integrated":
                expected.update(city="杭州", cityd="杭州")
            if arguments != expected:
                raise AssertionError("交通路线参数不正确")
            if name == self.failed_route:
                return SimpleNamespace(
                    content=[SimpleNamespace(type="text", text="公交方案查询失败")],
                    structured_content=None, is_error=True,
                )
            distance, duration, instruction = {
                "maps_direction_walking": ("8500", "6800", "沿天城路步行"),
                "maps_direction_bicycling": ("9000", "2400", "沿非机动车道骑行"),
                "maps_direction_driving": ("12000", "1800", "沿环城东路行驶"),
                "maps_direction_transit_integrated": ("10000", "2100", "乘地铁1号线"),
            }[name]
            data = {"route": {"origin": ORIGIN, "destination": DESTINATION,
                              "paths": [{"distance": distance, "duration": duration,
                                         "steps": [{"instruction": instruction}]}]}}
            # 实际高德驾车、公交返回文本 JSON，且公交使用 transits 而不是 paths。
            if name == "maps_direction_driving":
                data = data["route"]
            elif name == "maps_direction_transit_integrated":
                data = {"origin": ORIGIN, "destination": DESTINATION, "distance": distance,
                        "transits": [{"duration": duration, "segments": [{"bus": {
                            "buslines": [{"name": instruction}],
                        }}]}]}
            if name in {"maps_direction_driving", "maps_direction_transit_integrated"}:
                return SimpleNamespace(
                    content=[SimpleNamespace(type="text", text=json.dumps(data, ensure_ascii=False))],
                    structured_content=None, is_error=False,
                )
        elif name == "maps_distance":
            if arguments != {"origins": ORIGIN, "destination": DESTINATION, "type": "0"}:
                raise AssertionError("距离查询参数不正确")
            data = {"results": [{"distance": "7180"}]}
        else:
            raise AssertionError("不应调用其他工具")
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text="地图查询结果")],
            structured_content=data, is_error=False,
        )


class RouteComparisonTests(unittest.IsolatedAsyncioTestCase):
    async def test_route_capabilities_use_discovered_schemas(self):
        tools = AmapTools(cast(Client, RouteMapService()))
        await tools.discover()
        model = ModelService([response([{"type": "text", "text": "请提供起终点。"}])])

        await agent_loop(
            [{"role": "user", "content": "比较交通路线"}],
            cast(AsyncAnthropic, model), tools, "test-model",
        )

        self.assertEqual(
            {tool["name"]: tool["input_schema"] for tool in model.requests[0]["tools"]},
            {tool["name"]: tool["input_schema"] for tool in DECLARATIONS},
        )

    async def test_confirmed_places_feed_all_route_results_back_to_model(self):
        service = RouteMapService()
        tools = AmapTools(cast(Client, service))
        await tools.discover()
        routes = {name: {"origin": ORIGIN, "destination": DESTINATION} for name in [
            "maps_direction_walking", "maps_direction_bicycling", "maps_direction_driving",
            "maps_direction_transit_integrated",
        ]}
        routes["maps_direction_transit_integrated"].update(city="杭州", cityd="杭州")
        model = ModelService([
            response([
                tool_call("origin", "maps_text_search",
                          {"keywords": "杭州东站", "city": "杭州", "citylimit": True}),
                tool_call("destination", "maps_geo",
                          {"address": "杭州市上城区湖滨路1号", "city": "杭州"}),
            ], "tool_use"),
            response([tool_call(name, name, args) for name, args in routes.items()], "tool_use"),
            response([{"type": "text", "text": "已查询四种交通方式，结合公交偏好建议乘地铁。"}]),
        ])
        history = [
            {"role": "user", "content": "我在杭州，倾向公交。"},
            {"role": "assistant", "content": "请提供起终点。"},
            {"role": "user", "content": "杭州东站到上城区湖滨路1号，比较交通方式。"},
        ]

        answer = await agent_loop(history, cast(AsyncAnthropic, model), tools, "test-model")

        self.assertIn("四种交通方式", answer)
        self.assertEqual(model.requests[0]["messages"], history[:3])
        locations = model.requests[1]["messages"][-1]["content"]
        self.assertTrue(all(not result["is_error"] for result in locations))
        self.assertIn(ORIGIN, locations[0]["content"])
        self.assertIn(DESTINATION, locations[1]["content"])
        results = {result["tool_use_id"]: result for result in
                   model.requests[2]["messages"][-1]["content"]}
        expected = {
            "maps_direction_walking": ("8500", "6800", "沿天城路步行"),
            "maps_direction_bicycling": ("9000", "2400", "沿非机动车道骑行"),
            "maps_direction_driving": ("12000", "1800", "沿环城东路行驶"),
            "maps_direction_transit_integrated": ("10000", "2100", "乘地铁1号线"),
        }
        self.assertEqual(set(results), set(expected))
        for name, (distance, duration, instruction) in expected.items():
            with self.subTest(mode=name):
                self.assertFalse(results[name]["is_error"])
                content = json.loads(results[name]["content"])
                if name in {"maps_direction_driving", "maps_direction_transit_integrated"}:
                    self.assertIsNone(content["data"])
                    data = json.loads(content["text"][0])
                else:
                    data = content["data"]["route"]
                if name == "maps_direction_transit_integrated":
                    path = data["transits"][0]
                    self.assertEqual(data["distance"], distance)
                    self.assertEqual(path["segments"][0]["bus"]["buslines"][0]["name"], instruction)
                else:
                    path = data["paths"][0]
                    self.assertEqual(path["distance"], distance)
                    self.assertEqual(path["steps"][0]["instruction"], instruction)
                self.assertEqual(path["duration"], duration)
                self.assertIn((name, routes[name]), service.calls)

    async def test_failed_route_does_not_drop_verified_route_or_invent_cost(self):
        service = RouteMapService(failed_route="maps_direction_transit_integrated")
        tools = AmapTools(cast(Client, service))
        await tools.discover()
        model = ModelService([
            response([
                tool_call("origin", "maps_text_search",
                          {"keywords": "杭州东站", "city": "杭州", "citylimit": True}),
                tool_call("destination", "maps_geo",
                          {"address": "杭州市上城区湖滨路1号", "city": "杭州"}),
            ], "tool_use"),
            response([
                tool_call("transit", "maps_direction_transit_integrated",
                          {"origin": ORIGIN, "destination": DESTINATION,
                           "city": "杭州", "cityd": "杭州"}),
                tool_call("driving", "maps_direction_driving",
                          {"origin": ORIGIN, "destination": DESTINATION}),
                tool_call("distance", "maps_distance",
                          {"origins": ORIGIN, "destination": DESTINATION, "type": "0"}),
            ], "tool_use"),
            response([{"type": "text", "text": "驾车30分钟、12公里；公交及费用待核实。"}]),
        ])

        await agent_loop(
            [{"role": "user", "content": "杭州东站到上城区湖滨路1号，比较公交和驾车。"}],
            cast(AsyncAnthropic, model), tools, "test-model",
        )

        results = {result["tool_use_id"]: result for result in
                   model.requests[2]["messages"][-1]["content"]}
        self.assertTrue(results["transit"]["is_error"])
        self.assertIn("查询失败", results["transit"]["content"])
        self.assertIsNone(json.loads(results["transit"]["content"])["data"])
        self.assertFalse(results["driving"]["is_error"])
        driving = json.loads(json.loads(results["driving"]["content"])["text"][0])
        self.assertEqual(driving["paths"][0]["duration"], "1800")
        self.assertNotIn("cost", results["driving"]["content"])
        self.assertFalse(results["distance"]["is_error"])
        self.assertEqual(json.loads(results["distance"]["content"])["data"],
                         {"results": [{"distance": "7180"}]})

    async def test_ambiguous_places_reach_model_before_clarification(self):
        service = RouteMapService(ambiguous=True)
        tools = AmapTools(cast(Client, service))
        await tools.discover()
        model = ModelService([
            response([tool_call("candidates", "maps_text_search",
                                {"keywords": "杭州东站", "city": "杭州", "citylimit": True})],
                     "tool_use"),
            response([{"type": "text", "text": "请选择杭州东站或杭州东站东广场。"}]),
        ])

        await agent_loop(
            [{"role": "user", "content": "杭州东站去湖滨路1号怎么走？"}],
            cast(AsyncAnthropic, model), tools, "test-model",
        )

        result = model.requests[1]["messages"][-1]["content"][0]
        self.assertFalse(result["is_error"])
        candidates = json.loads(result["content"])["data"]["pois"]
        self.assertEqual({poi["id"] for poi in candidates},
                         {"east-station", "east-station-square"})
        self.assertEqual({poi["address"] for poi in candidates}, {"天城路1号", "新塘路"})
        self.assertEqual([name for name, _ in service.calls], ["maps_text_search"])

    async def test_clarification_keeps_known_conditions_without_map_calls(self):
        service = RouteMapService()
        tools = AmapTools(cast(Client, service))
        await tools.discover()
        model = ModelService([response([{"type": "text", "text": "请提供起点。"}])])
        history = [
            {"role": "user", "content": "我在杭州，倾向公交。"},
            {"role": "assistant", "content": "好的。"},
            {"role": "user", "content": "去龙翔桥地铁站怎么走？"},
        ]

        await agent_loop(history, cast(AsyncAnthropic, model), tools, "test-model")

        self.assertEqual(model.requests[0]["messages"], history[:3])
        self.assertEqual(service.calls, [])

    async def test_taxi_app_link_is_not_available_as_a_verified_route(self):
        service = RouteMapService()
        tools = AmapTools(cast(Client, service))
        await tools.discover()
        model = ModelService([
            response([tool_call("taxi", "maps_schema_take_taxi", {})], "tool_use"),
            response([{"type": "text", "text": "不能通过打车链接查询交通路线或费用。"}]),
        ])

        await agent_loop(
            [{"role": "user", "content": "生成打车链接"}],
            cast(AsyncAnthropic, model), tools, "test-model",
        )

        self.assertEqual(service.calls, [])
        result = model.requests[1]["messages"][-1]["content"][0]
        self.assertEqual(result["tool_use_id"], "taxi")
        self.assertTrue(result["is_error"])
