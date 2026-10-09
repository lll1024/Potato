"""通过 HTTP 对话与真实 MCP 协议核对官方资料接入。"""
import asyncio
import json
import contextlib
import io
import os
from unittest.mock import patch
import tempfile
import unittest
from contextlib import asynccontextmanager
from typing import cast

import httpx
import httpx2
from anthropic import AsyncAnthropic
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.server import MCPServer

from agent import run_cli
from amap_mcp import AmapTools
from tavily_mcp import TavilyTools
from travel_tools import TravelTools
from test_agent import MapService, ModelService, response
from web import Runtime, create_app


class OfficialSourceTests(unittest.IsolatedAsyncioTestCase):
    async def test_map_search_and_body_are_paired_and_saved_through_http(self):
        maps = MCPServer("地图边界")
        sources = MCPServer("资料边界")
        url = "https://museum.example.test/visit"

        @maps.tool(name="maps_text_search")
        def place(keywords: str, city: str) -> dict:
            if (keywords, city) != ("测试博物馆", "北京"):
                raise ValueError("地点条件错误")
            return {"pois": [{"name": "测试博物馆", "address": "北京市", "location": "116,39"}]}

        @sources.tool(name="tavily-search")
        def search(query: str) -> dict:
            if query != "测试博物馆 官方参观须知":
                raise ValueError("搜索条件错误")
            return {"results": [{"title": "官方参观须知", "url": url, "content": "周一闭馆，预约规则见正文"}]}

        @sources.tool(name="tavily-extract")
        def extract(urls: list[str]) -> dict:
            if urls != [url]:
                raise ValueError("正文地址错误")
            return {"results": [{"url": url, "raw_content": "常规周一闭馆，门票免费；预约须使用官方小程序。"}], "failed_results": []}

        @sources.tool(name="tavily-crawl")
        def forbidden(url: str) -> dict:
            raise AssertionError("不能开放付费或爬取能力")

        model = ModelService([
            response([{"type": "tool_use", "id": "map", "name": "maps_text_search", "input": {"keywords": "测试博物馆", "city": "北京"}},
                      {"type": "tool_use", "id": "search", "name": "tavily-search", "input": {"query": "测试博物馆 官方参观须知"}}], "tool_use"),
            response([{"type": "tool_use", "id": "body", "name": "tavily-extract", "input": {"urls": [url]}}], "tool_use"),
            response([{"type": "text", "text": "官方正文提供常规规则，未来当日开放仍待核实；通过官方小程序预约，尚未预约。"}]),
        ])
        source_app = sources.streamable_http_app(json_response=True, stateless_http=True)
        source_clients = []
        @asynccontextmanager
        async def resources():
            async with Client(maps) as map_client, source_app.router.lifespan_context(source_app):
                async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=source_app)) as source_http:
                    source_clients.append(source_http)
                    async with Client(streamable_http_client("http://127.0.0.1:8000/mcp", http_client=source_http)) as source_client:
                        amap = AmapTools(map_client)
                        await amap.discover()
                        tavily = TavilyTools(source_client, redact=amap.redact)
                        await tavily.discover()
                        yield Runtime(cast(AsyncAnthropic, model), TravelTools(amap, tavily), "test-model")

        with tempfile.TemporaryDirectory() as directory:
            app = create_app(directory, resources=resources)
            async with app.router.lifespan_context(app):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                    accepted = (await client.post("/api/turns", json={"input": "2026年10月10日去北京测试博物馆"})).json()
                    saved = {}
                    for _ in range(100):
                        saved = (await client.get("/api/sessions/" + accepted["session_id"])).json()
                        if saved["turns"][0]["status"] != "running":
                            break
                        await asyncio.sleep(0.01)
                    turn = saved["turns"][0]
                    self.assertEqual(turn["status"], "completed")
                    names = {tool["name"] for tool in model.requests[0]["tools"]}
                    self.assertEqual(names, {"maps_text_search", "tavily-search", "tavily-extract"})
                    search_result = model.requests[1]["messages"][-1]["content"][1]
                    self.assertEqual(search_result["tool_use_id"], "search")
                    self.assertIn(url, search_result["content"])
                    body_result = model.requests[2]["messages"][-1]["content"][0]
                    self.assertEqual(body_result["tool_use_id"], "body")
                    self.assertIn("官方小程序", body_result["content"])
                    materials = turn["query_materials"]
                    self.assertEqual(len(materials), 2)
                    self.assertIn("线索", materials[0]["title"])
                    self.assertIn("正文", materials[1]["title"])
                    self.assertIn("查询时间", str(materials[1]))
                    self.assertIn("发布时间未返回", str(materials[1]))
                    self.assertIn("适用日期未返回", str(materials[1]))
                    for material in materials:
                        raw = (await client.get("/api/payloads/" + material["payload_id"])).json()
                        self.assertIn(url, str(raw))
            self.assertTrue(source_clients[0].is_closed)
            @asynccontextmanager
            async def restored_resources():
                amap = AmapTools(cast(Client, MapService()))
                await amap.discover()
                yield Runtime(cast(AsyncAnthropic, model), amap, "test-model")
            restored = create_app(directory, resources=restored_resources)
            async with restored.router.lifespan_context(restored):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=restored), base_url="http://test") as client:
                    snapshot = (await client.get("/api/sessions/" + accepted["session_id"])).json()
                    self.assertEqual(snapshot["turns"][0]["query_materials"], materials)

    async def test_cli_uses_the_same_discovered_search_and_body_tools(self):
        maps = MCPServer("CLI 地图")
        sources = MCPServer("CLI 资料")
        @maps.tool(name="maps_text_search")
        def place(keywords: str, city: str) -> dict:
            return {"name": "测试博物馆", "address": "北京市"}
        @sources.tool(name="tavily_search")
        def search(query: str) -> dict:
            return {"results": [{"url": "https://museum.example.test/visit", "content": "参观须知"}]}
        @sources.tool(name="tavily_extract")
        def extract(urls: list[str]) -> dict:
            return {"results": [{"url": urls[0], "raw_content": "预约仅限官方小程序。"}]}
        model = ModelService([
            response([{"type": "tool_use", "id": "cli-search", "name": "tavily_search", "input": {"query": "测试博物馆 官网"}}], "tool_use"),
            response([{"type": "tool_use", "id": "cli-body", "name": "tavily_extract", "input": {"urls": ["https://museum.example.test/visit"]}}], "tool_use"),
            response([{"type": "text", "text": "官方正文要求通过小程序预约，尚未完成预约。"}]),
        ])
        output = io.StringIO()
        with patch.dict(os.environ, {"MODEL_ID": "test-model", "ANTHROPIC_API_KEY": "model-key"}, clear=True), \
             patch("agent.Client", side_effect=lambda *args, **kwargs: Client(maps)), \
             patch("travel_tools.Client", side_effect=lambda *args, **kwargs: Client(sources)), \
             patch("agent.AsyncAnthropic", return_value=ModelContext(model)), \
             patch("builtins.input", side_effect=["2026年10月10日去测试博物馆", "exit"]), \
             contextlib.redirect_stdout(output):
            result = await run_cli("map-key")
        self.assertEqual(result, 0)
        names = {tool["name"] for tool in model.requests[0]["tools"]}
        self.assertEqual(names, {"maps_text_search", "tavily_search", "tavily_extract"})
        self.assertEqual(model.requests[2]["messages"][-1]["content"][0]["tool_use_id"], "cli-body")
        self.assertIn("官方小程序", model.requests[2]["messages"][-1]["content"][0]["content"])
        self.assertIn("尚未完成预约", output.getvalue())

    async def test_provider_quota_reported_as_success_keeps_map_queries_available(self):
        maps = MCPServer("额度降级地图")
        sources = MCPServer("额度降级资料")
        @maps.tool(name="maps_text_search")
        def place(keywords: str, city: str) -> dict:
            return {"name": "测试博物馆", "address": "北京市"}
        @sources.tool(name="tavily_search")
        def search(query: str) -> dict:
            return {"code": "monthly_cap_reached_bonus_eligible", "message": "pay via x402 or POST /keyless/bonus", "next_actions": [{"type": "agentic_payment"}]}
        @sources.tool(name="tavily_extract")
        def extract(urls: list[str]) -> dict:
            raise AssertionError("额度耗尽后不应启动另一资料调用")
        model = ModelService([
            response([{"type": "tool_use", "id": "quota", "name": "tavily_search", "input": {"query": "官方规则"}},
                      {"type": "tool_use", "id": "map-after-quota", "name": "maps_text_search", "input": {"keywords": "测试博物馆", "city": "北京"}},
                      {"type": "tool_use", "id": "body-after-quota", "name": "tavily_extract", "input": {"urls": ["https://museum.example.test"]}}], "tool_use"),
            response([{"type": "text", "text": "地点已查到，资料额度耗尽，开放和预约待核实。"}]),
        ])
        with patch.dict(os.environ, {"MODEL_ID": "test-model", "ANTHROPIC_API_KEY": "model-key"}, clear=True), \
             patch("agent.Client", side_effect=lambda *args, **kwargs: Client(maps)), \
             patch("travel_tools.Client", side_effect=lambda *args, **kwargs: Client(sources)), \
             patch("agent.AsyncAnthropic", return_value=ModelContext(model)), \
             patch("builtins.input", side_effect=["2026年10月10日去测试博物馆", "exit"]), \
             contextlib.redirect_stdout(io.StringIO()):
            result = await run_cli("map-key")
        self.assertEqual(result, 0)
        results = model.requests[1]["messages"][-1]["content"]
        self.assertEqual([item["tool_use_id"] for item in results], ["quota", "map-after-quota", "body-after-quota"])
        self.assertTrue(results[0]["is_error"])
        self.assertFalse(results[1]["is_error"])
        self.assertIn("北京市", results[1]["content"])
        self.assertTrue(results[2]["is_error"])
        self.assertNotIn("x402", results[0]["content"])
        self.assertNotIn("keyless/bonus", results[0]["content"])

    async def test_keyless_connection_failure_still_starts_cli_map_queries(self):
        maps = MCPServer("初始化降级地图")
        @maps.tool(name="maps_text_search")
        def place(keywords: str, city: str) -> dict:
            return {"name": "测试博物馆", "address": "北京市"}
        model = ModelService([
            response([{"type": "tool_use", "id": "map-fallback", "name": "maps_text_search", "input": {"keywords": "测试博物馆", "city": "北京"}}], "tool_use"),
            response([{"type": "text", "text": "已查到地点，官方开放与预约资料未能核实。"}]),
        ])
        requests = []
        def unavailable(request):
            requests.append(request)
            raise httpx2.ConnectError("资料服务连接失败", request=request)
        real_client = httpx2.AsyncClient
        def source_http(**kwargs):
            return real_client(transport=httpx2.MockTransport(unavailable), **kwargs)
        output = io.StringIO()
        with patch.dict(os.environ, {"MODEL_ID": "test-model", "ANTHROPIC_API_KEY": "model-key", "TAVILY_API_KEY": "paid-key-must-not-be-used"}, clear=True), \
             patch("agent.Client", side_effect=lambda *args, **kwargs: Client(maps)), \
             patch("travel_tools.httpx2.AsyncClient", side_effect=source_http), \
             patch("agent.AsyncAnthropic", return_value=ModelContext(model)), \
             patch("builtins.input", side_effect=["2026年10月10日去测试博物馆", "exit"]), \
             contextlib.redirect_stdout(output):
            result = await run_cli("map-key")
        self.assertEqual(result, 0)
        self.assertTrue(requests)
        self.assertEqual(requests[0].headers["X-Tavily-Access-Mode"], "keyless")
        self.assertNotIn("authorization", requests[0].headers)
        self.assertNotIn("paid-key-must-not-be-used", str(requests[0].headers) + str(requests[0].url))
        self.assertIn("官方资料服务暂不可用", model.requests[0]["system"])
        self.assertEqual({tool["name"] for tool in model.requests[0]["tools"]}, {"maps_text_search"})
        self.assertIn("北京市", model.requests[1]["messages"][-1]["content"][0]["content"])


class ModelContext:
    def __init__(self, model):
        self.model = model
    async def __aenter__(self):
        return self.model
    async def __aexit__(self, *args):
        return False
