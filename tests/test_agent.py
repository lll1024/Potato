import copy
import asyncio
import json
import unittest
from types import SimpleNamespace
from typing import cast

from anthropic import AsyncAnthropic
from anthropic.types import Message
from mcp import Client

from amap_mcp import AmapTools
from agent import agent_loop


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


class PlaceQueryTests(unittest.IsolatedAsyncioTestCase):
    async def test_place_query_returns_answer_and_grounded_tool_result(self):
        tools = AmapTools(cast(Client, MapService()))
        await tools.discover()
        model = ModelService(
            [
                response(
                    [{"type": "tool_use", "id": "query-1", "name": "maps_text_search",
                      "input": {"keywords": "西湖", "city": "杭州"}}],
                    "tool_use",
                ),
                response([{"type": "text", "text": "查到西湖，地址为杭州市西湖区。"}]),
            ]
        )
        history = [{"role": "user", "content": "查询杭州的西湖"}]

        answer = await agent_loop(
            history, cast(AsyncAnthropic, model), tools, "test-model"
        )

        self.assertEqual(answer, "查到西湖，地址为杭州市西湖区。")
        declaration = model.requests[0]["tools"][0]
        self.assertEqual(declaration["input_schema"]["required"], ["keywords", "city"])
        result = model.requests[1]["messages"][-1]["content"][0]
        self.assertEqual(result["tool_use_id"], "query-1")
        self.assertFalse(result["is_error"])
        self.assertIn("查到一个地点", result["content"])
        self.assertIn("杭州市西湖区", result["content"])
        self.assertIn("poi-west-lake", result["content"])

    async def test_only_place_tools_are_available_and_forbidden_calls_are_rejected(self):
        class ExtraToolsService(MapService):
            async def list_tools(self, *, cursor=None):
                listed = await super().list_tools()
                listed.tools.extend(
                    SimpleNamespace(
                        name=name, description="不属于地点查询", input_schema={"type": "object"}
                    )
                    for name in ["bash", "maps_weather"]
                )
                return listed

        tools = AmapTools(cast(Client, ExtraToolsService()))
        await tools.discover()
        model = ModelService([
            response([{"type": "tool_use", "id": "forbidden", "name": "bash",
                       "input": {"command": "echo test"}}], "tool_use"),
            response([{"type": "text", "text": "只能使用已开放的地点查询工具。"}]),
        ])

        await agent_loop(
            [{"role": "user", "content": "运行命令"}],
            cast(AsyncAnthropic, model), tools, "test-model",
        )

        self.assertEqual(
            [tool["name"] for tool in model.requests[0]["tools"]], ["maps_text_search"]
        )
        result = model.requests[1]["messages"][-1]["content"][0]
        self.assertEqual(result["tool_use_id"], "forbidden")
        self.assertTrue(result["is_error"])

    async def test_separate_histories_do_not_share_user_conditions(self):
        tools = AmapTools(cast(Client, MapService()))
        await tools.discover()
        model = ModelService([
            response([{"type": "text", "text": "请提供杭州的地点关键词。"}]),
            response([{"type": "text", "text": "请提供上海的地点关键词。"}]),
        ])

        await agent_loop(
            [{"role": "user", "content": "我在杭州"}],
            cast(AsyncAnthropic, model), tools, "test-model",
        )
        await agent_loop(
            [{"role": "user", "content": "我在上海"}],
            cast(AsyncAnthropic, model), tools, "test-model",
        )

        self.assertNotIn("杭州", json.dumps(model.requests[1]["messages"], ensure_ascii=False))
        self.assertIn("上海", json.dumps(model.requests[1]["messages"], ensure_ascii=False))

    async def test_round_budget_stops_repeated_model_requests(self):
        tools = AmapTools(cast(Client, MapService()))
        await tools.discover()
        model = ModelService([response([
            {"type": "tool_use", "id": "repeat", "name": "maps_text_search",
             "input": {"keywords": "西湖", "city": "杭州"}}
        ], "tool_use")])

        answer = await agent_loop(
            [{"role": "user", "content": "查询西湖"}],
            cast(AsyncAnthropic, model), tools, "test-model", max_rounds=1,
        )

        self.assertEqual(len(model.requests), 1)
        self.assertIn("上限", answer)

    async def test_startup_refuses_a_service_without_place_tools(self):
        class NoPlaceService(MapService):
            async def list_tools(self, *, cursor=None):
                return SimpleNamespace(tools=[], next_cursor=None)

        tools = AmapTools(cast(Client, NoPlaceService()))

        with self.assertRaisesRegex(RuntimeError, "地点"):
            await tools.discover()

    async def test_discovery_reads_all_pages_and_preserves_detail_schema(self):
        class PagedMapService(MapService):
            async def list_tools(self, *, cursor=None):
                if cursor is None:
                    listed = await super().list_tools()
                    listed.next_cursor = "details-page"
                    return listed
                if cursor != "details-page":
                    raise AssertionError("不正确的分页游标")
                return SimpleNamespace(
                    tools=[SimpleNamespace(
                        name="maps_search_detail", description="查询地点详情",
                        input_schema={"type": "object", "properties": {
                            "id": {"type": "string"}}, "required": ["id"]},
                    )],
                    next_cursor=None,
                )

        tools = AmapTools(cast(Client, PagedMapService()))
        await tools.discover()
        model = ModelService([response([{"type": "text", "text": "请输入地点。"}])])

        await agent_loop(
            [{"role": "user", "content": "查询地点"}],
            cast(AsyncAnthropic, model), tools, "test-model",
        )

        declarations = model.requests[0]["tools"]
        self.assertEqual(
            [tool["name"] for tool in declarations], ["maps_text_search", "maps_search_detail"]
        )
        self.assertEqual(declarations[1]["input_schema"]["required"], ["id"])

    async def test_tool_budget_stops_a_batch_without_extra_calls(self):
        class CountingMapService(MapService):
            calls = 0

            async def call_tool(self, name, arguments):
                self.calls += 1
                return await super().call_tool(name, arguments)

        service = CountingMapService()
        tools = AmapTools(cast(Client, service))
        await tools.discover()
        model = ModelService([response([
            {"type": "tool_use", "id": query_id, "name": "maps_text_search",
             "input": {"keywords": "西湖", "city": "杭州"}}
            for query_id in ["query-1", "query-2"]
        ], "tool_use")])
        history = [{"role": "user", "content": "查询西湖"}]

        answer = await agent_loop(
            history, cast(AsyncAnthropic, model), tools, "test-model", max_tool_calls=1
        )

        self.assertEqual(service.calls, 1)
        self.assertIn("上限", answer)
        results = history[-2]["content"]
        self.assertEqual([result["tool_use_id"] for result in results], ["query-1", "query-2"])
        self.assertTrue(results[1]["is_error"])
        self.assertEqual(history[-1]["role"], "assistant")

    async def test_timed_out_query_is_returned_as_an_error(self):
        class SlowMapService(MapService):
            async def call_tool(self, name, arguments):
                await asyncio.Event().wait()

        tools = AmapTools(cast(Client, SlowMapService()), tool_timeout=0.01)
        await tools.discover()
        model = ModelService([
            response([{"type": "tool_use", "id": "slow", "name": "maps_text_search",
                       "input": {"keywords": "西湖", "city": "杭州"}}], "tool_use"),
            response([{"type": "text", "text": "地点查询超时，结果待核实。"}]),
        ])

        answer = await agent_loop(
            [{"role": "user", "content": "查询杭州西湖"}],
            cast(AsyncAnthropic, model), tools, "test-model",
        )

        result = model.requests[1]["messages"][-1]["content"][0]
        self.assertTrue(result["is_error"])
        self.assertIn("超时", result["content"])
        self.assertIn("待核实", answer)

    async def test_credentials_in_service_content_never_reach_the_model(self):
        key = "test-amap-secret"

        class LeakyMapService(MapService):
            async def list_tools(self, *, cursor=None):
                listed = await super().list_tools()
                listed.tools[0].description += f" https://mcp.amap.com/mcp?key={key}"
                return listed

            async def call_tool(self, name, arguments):
                return SimpleNamespace(
                    content=[SimpleNamespace(type="text", text=f"服务错误 key={key}")],
                    structured_content={"url": f"https://mcp.amap.com/mcp?key={key}"},
                    is_error=True,
                )

        tools = AmapTools(cast(Client, LeakyMapService()), api_key=key)
        await tools.discover()
        model = ModelService([
            response([{"type": "tool_use", "id": "leaky", "name": "maps_text_search",
                       "input": {"keywords": "西湖", "city": "杭州"}}], "tool_use"),
            response([{"type": "text", "text": "地图服务暂时不可用。"}]),
        ])

        await agent_loop(
            [{"role": "user", "content": "查询杭州西湖"}],
            cast(AsyncAnthropic, model), tools, "test-model",
        )

        self.assertNotIn(key, json.dumps(model.requests))
        result = model.requests[1]["messages"][-1]["content"][0]
        self.assertTrue(result["is_error"])


if __name__ == "__main__":
    unittest.main()
