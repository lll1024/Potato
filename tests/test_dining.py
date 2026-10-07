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


# 2026-10-08 从高德实际 list_tools 返回中保留的餐饮检索工具契约。
DECLARATIONS = json.loads(
    Path(__file__).with_name("fixtures").joinpath("amap_dining_tools.json").read_text()
)
LOCATION = "120.158291,30.250123"
# 可控地图样例，不代表真实门店或实连验收结果。
RESTAURANT = {
    "id": "test-noodle-shop", "name": "测试面馆", "type": "餐饮服务;中餐厅",
    "address": "杭州市上城区测试路1号", "location": "120.159,30.251",
}


def tool_call(call_id, name, arguments):
    return {"type": "tool_use", "id": call_id, "name": name, "input": arguments}


class DiningMapService:
    @property
    def session(self):
        return self

    def __init__(self, data, *, text_only=False):
        self.data = data
        self.text_only = text_only
        self.calls = []

    async def list_tools(self, *, cursor=None):
        return SimpleNamespace(tools=[SimpleNamespace(
            name=tool["name"], description=tool["description"],
            input_schema=tool["input_schema"],
        ) for tool in DECLARATIONS], next_cursor=None)

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text=json.dumps(self.data, ensure_ascii=False))],
            structured_content=None if self.text_only else self.data, is_error=False,
        )


class DiningRecommendationTests(unittest.IsolatedAsyncioTestCase):
    async def test_around_search_contract_and_locatable_candidates_reach_model(self):
        service = DiningMapService({"pois": [RESTAURANT]}, text_only=True)
        tools = AmapTools(cast(Client, service))
        await tools.discover()
        arguments = {"keywords": "面馆", "location": LOCATION, "radius": "1000"}
        model = ModelService([
            response([tool_call("dining", "maps_around_search", arguments)], "tool_use"),
            response([{"type": "text", "text": "查询完成。"}]),
        ])

        await agent_loop(
            [{"role": "user", "content": f"推荐高德坐标 {LOCATION} 周边1公里的面馆"}],
            cast(AsyncAnthropic, model), tools, "test-model",
        )

        self.assertEqual(
            {tool["name"]: tool["input_schema"] for tool in model.requests[0]["tools"]},
            {tool["name"]: tool["input_schema"] for tool in DECLARATIONS},
        )
        self.assertEqual(service.calls, [("maps_around_search", arguments)])
        result = model.requests[1]["messages"][-1]["content"][0]
        self.assertEqual(result["tool_use_id"], "dining")
        self.assertFalse(result["is_error"])
        content = json.loads(result["content"])
        self.assertIsNone(content["data"])
        self.assertEqual(json.loads(content["text"][0]), {"pois": [RESTAURANT]})

    async def test_region_search_returns_candidates_without_inventing_missing_fields(self):
        service = DiningMapService({"pois": [RESTAURANT]})
        tools = AmapTools(cast(Client, service))
        await tools.discover()
        arguments = {"keywords": "上城区 面馆", "city": "杭州", "citylimit": True}
        model = ModelService([
            response([tool_call("region", "maps_text_search", arguments)], "tool_use"),
            response([{"type": "text", "text": "查询完成。"}]),
        ])

        await agent_loop(
            [{"role": "user", "content": "在杭州上城区推荐面馆，我喜欢清淡、不吃辣。"}],
            cast(AsyncAnthropic, model), tools, "test-model",
        )

        self.assertEqual(service.calls, [("maps_text_search", arguments)])
        result = model.requests[1]["messages"][-1]["content"][0]
        self.assertEqual(result["tool_use_id"], "region")
        self.assertFalse(result["is_error"])
        candidates = json.loads(result["content"])["data"]["pois"]
        self.assertEqual(candidates, [RESTAURANT])
        for field in ("rating", "cost", "opening_hours"):
            self.assertNotIn(field, candidates[0])

    async def test_preference_adjustment_keeps_conversation_and_queries_new_candidates(self):
        service = DiningMapService({"pois": [RESTAURANT]})
        tools = AmapTools(cast(Client, service))
        await tools.discover()
        first_arguments = {"keywords": "上城区 面馆", "city": "杭州", "citylimit": True}
        changed_arguments = {"keywords": "粤菜", "location": LOCATION, "radius": "2000"}
        model = ModelService([
            response([tool_call("noodles", "maps_text_search", first_arguments)], "tool_use"),
            response([{"type": "text", "text": "已查到测试面馆，口味及营业信息待核实。"}]),
            response([tool_call("cantonese", "maps_around_search", changed_arguments)], "tool_use"),
            response([{"type": "text", "text": "查询完成。"}]),
        ])
        history = [{"role": "user", "content": "我在杭州上城区，清淡、不吃辣，推荐面馆。"}]

        await agent_loop(history, cast(AsyncAnthropic, model), tools, "test-model")
        changed_restaurant = {
            "id": "test-cantonese-shop", "name": "测试粤菜馆", "type": "餐饮服务;中餐厅;广东菜",
            "address": "杭州市上城区测试路2号", "location": "120.160,30.252",
        }
        service.data = {"pois": [changed_restaurant]}
        history.append({"role": "user", "content":
                        f"改成粤菜，在高德坐标 {LOCATION} 周边2公里找，其他条件沿用。"})
        previous_messages = history[:]

        await agent_loop(history, cast(AsyncAnthropic, model), tools, "test-model")

        self.assertEqual(model.requests[2]["messages"], previous_messages)
        previous_result = model.requests[2]["messages"][2]["content"][0]
        self.assertEqual(json.loads(previous_result["content"])["data"]["pois"], [RESTAURANT])
        self.assertEqual(service.calls,
                         [("maps_text_search", first_arguments), ("maps_around_search", changed_arguments)])
        changed_result = model.requests[3]["messages"][-1]["content"][0]
        self.assertEqual(changed_result["tool_use_id"], "cantonese")
        self.assertFalse(changed_result["is_error"])
        self.assertEqual(json.loads(changed_result["content"])["data"]["pois"], [changed_restaurant])

    async def test_empty_or_non_dining_results_reach_model_without_added_candidates(self):
        non_dining = {"id": "test-park", "name": "测试公园", "type": "风景名胜",
                      "address": "杭州市上城区测试路3号"}
        for data in ({"pois": []}, {"pois": [non_dining]}):
            with self.subTest(data=data):
                service = DiningMapService(data)
                tools = AmapTools(cast(Client, service))
                await tools.discover()
                model = ModelService([
                    response([tool_call("empty", "maps_around_search",
                                        {"keywords": "素食", "location": LOCATION, "radius": "500"})],
                             "tool_use"),
                    response([{"type": "text", "text": "未查到符合条件的餐饮候选。"}]),
                ])

                await agent_loop(
                    [{"role": "user", "content": f"推荐高德坐标 {LOCATION} 周边500米的素食餐厅"}],
                    cast(AsyncAnthropic, model), tools, "test-model",
                )

                result = model.requests[1]["messages"][-1]["content"][0]
                self.assertEqual(result["tool_use_id"], "empty")
                self.assertFalse(result["is_error"])
                self.assertEqual(json.loads(result["content"])["data"], data)
