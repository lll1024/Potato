import json
import contextlib
import io
import os
import unittest
from typing import cast
from unittest.mock import patch

from anthropic import AsyncAnthropic
import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.server import MCPServer
from mcp.types import CallToolResult, TextContent

from agent import agent_loop, run_cli
from amap_mcp import AmapTools
from test_agent import ModelService, response


class SDKIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_cli_model_auth_headers_omit_unused_credentials(self):
        server = MCPServer("认证测试地图服务")

        @server.tool(name="maps_text_search")
        def search(keywords: str, city: str) -> str:
            return "西湖：杭州市西湖区"

        configurations = [
            ({"ANTHROPIC_API_KEY": "test-api-key",
              "ANTHROPIC_BASE_URL": "https://model.example.test",
              "ANTHROPIC_AUTH_TOKEN": "unused-token"},
             {"x-api-key": "test-api-key"}),
            ({"ANTHROPIC_API_KEY": "test-api-key"},
             {"x-api-key": "test-api-key"}),
            ({"ANTHROPIC_AUTH_TOKEN": "test-auth-token"},
             {"authorization": "Bearer test-auth-token"}),
        ]
        for environment, expected in configurations:
            with self.subTest(environment=list(environment)):
                requests = []

                def reply(request):
                    requests.append(request)
                    return httpx2.Response(200, json=response([
                        {"type": "text", "text": "请提供地点关键词。"}
                    ]).model_dump(mode="json"))

                async with httpx2.AsyncClient(
                    transport=httpx2.MockTransport(reply)
                ) as http_client:
                    def model_client(**kwargs):
                        return AsyncAnthropic(http_client=http_client, **kwargs)

                    with (
                        patch.dict(os.environ, {"MODEL_ID": "test-model", **environment}, clear=True),
                        patch("agent.Client", side_effect=lambda *args, **kwargs: Client(server)),
                        patch("agent.AsyncAnthropic", side_effect=model_client),
                        patch("builtins.input", side_effect=["查询杭州西湖", "exit"]),
                        contextlib.redirect_stdout(io.StringIO()),
                    ):
                        result = await run_cli("test-amap-key")

                self.assertEqual(result, 0)
                self.assertEqual(len(requests), 1)
                headers = requests[0].headers
                actual = {name: headers[name] for name in ["x-api-key", "authorization"]
                          if name in headers}
                self.assertEqual(actual, expected)

    async def test_real_sdk_queries_a_place_then_its_details(self):
        server = MCPServer("测试地图服务")

        @server.tool(name="maps_text_search")
        def search(keywords: str, city: str) -> CallToolResult:
            """按城市和关键词检索地点。"""
            if (keywords, city) != ("西湖", "杭州"):
                raise ValueError("地点条件错误")
            return CallToolResult(
                content=[TextContent(type="text", text="查到西湖")],
                structured_content={"pois": [{"id": "west-lake", "name": "西湖"}]},
            )

        @server.tool(name="maps_search_detail")
        def detail(id: str) -> dict:
            """按地点标识查询详情。"""
            if id != "west-lake":
                raise ValueError("地点标识错误")
            return {"id": id, "name": "西湖", "address": "杭州市西湖区"}

        @server.tool(name="bash")
        def forbidden(command: str) -> str:
            raise AssertionError("不应执行编程工具")

        model = ModelService([
            response([{"type": "tool_use", "id": "search", "name": "maps_text_search",
                       "input": {"keywords": "西湖", "city": "杭州"}}], "tool_use"),
            response([{"type": "tool_use", "id": "detail", "name": "maps_search_detail",
                       "input": {"id": "west-lake"}}], "tool_use"),
            response([{"type": "text", "text": "查到西湖，地址为杭州市西湖区。"}]),
        ])

        async with Client(server) as client:
            tools = AmapTools(client)
            await tools.discover()
            answer = await agent_loop(
                [{"role": "user", "content": "查询杭州西湖的详情"}],
                cast(AsyncAnthropic, model), tools, "test-model",
            )

        self.assertIn("杭州市西湖区", answer)
        self.assertEqual(
            {tool["name"] for tool in model.requests[0]["tools"]},
            {"maps_text_search", "maps_search_detail"},
        )
        search_result = model.requests[1]["messages"][-1]["content"][0]
        self.assertEqual(json.loads(search_result["content"])["data"]["pois"][0]["id"], "west-lake")
        detail_result = model.requests[2]["messages"][-1]["content"][0]
        self.assertEqual(detail_result["tool_use_id"], "detail")
        self.assertIn("杭州市西湖区", detail_result["content"])

    async def test_streamable_http_discovers_and_queries_places(self):
        server = MCPServer("HTTP 测试地图服务")

        @server.tool(name="maps_text_search")
        def search(keywords: str, city: str) -> str:
            return "西湖：杭州市西湖区"

        app = server.streamable_http_app(json_response=True, stateless_http=True)
        model = ModelService([
            response([{"type": "tool_use", "id": "http-query", "name": "maps_text_search",
                       "input": {"keywords": "西湖", "city": "杭州"}}], "tool_use"),
            response([{"type": "text", "text": "西湖位于杭州市西湖区。"}]),
        ])
        async with app.router.lifespan_context(app):
            async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app)) as http_client:
                transport = streamable_http_client(
                    "http://127.0.0.1:8000/mcp", http_client=http_client
                )
                async with Client(transport, read_timeout_seconds=2) as client:
                    tools = AmapTools(client)
                    await tools.discover()
                    answer = await agent_loop(
                        [{"role": "user", "content": "查询杭州西湖"}],
                        cast(AsyncAnthropic, model), tools, "test-model",
                    )

        self.assertTrue(http_client.is_closed)
        self.assertIn("杭州市西湖区", answer)
        result = model.requests[1]["messages"][-1]["content"][0]
        self.assertEqual(result["tool_use_id"], "http-query")
        self.assertIn("杭州市西湖区", result["content"])
