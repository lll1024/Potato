"""公开 HTTP 核对官方正文、资料日期和续聊状态；不以预设回答验证模型语义。"""
import json
import tempfile
import unittest
from contextlib import asynccontextmanager
from datetime import datetime
from typing import cast

import httpx
from anthropic import AsyncAnthropic
from mcp import Client
from mcp.server import MCPServer

from amap_mcp import AmapTools
from tavily_mcp import TavilyTools
from travel_tools import TravelTools
from test_agent import MapService, ModelService, response
import test_history
from test_travel_dates import context_from_request
from web import Runtime, create_app


class OfficialDateApplicabilityTests(unittest.IsolatedAsyncioTestCase):
    finish = test_history.HistoryTests.finish
    moment = datetime.fromisoformat("2026-10-09T23:59:59+08:00")

    @asynccontextmanager
    async def service(self, directory, model, pages):
        sources = MCPServer("日期适用性资料边界")

        @sources.tool(name="tavily_search")
        def search(query: str) -> dict:
            return {"results": [{"url": url, "content": "搜索摘要，仅作为线索"} for url in pages]}

        @sources.tool(name="tavily_extract")
        def extract(urls: list[str]) -> dict:
            return {"results": [{"url": url, **pages[url]} for url in urls]}

        @asynccontextmanager
        async def resources():
            async with Client(sources) as source:
                amap = AmapTools(cast(Client, MapService()))
                await amap.discover()
                tavily = TavilyTools(source, redact=amap.redact)
                await tavily.discover()
                yield Runtime(cast(AsyncAnthropic, model), TravelTools(amap, tavily), "test-model")
        app = create_app(directory, resources=resources, clock=lambda: self.moment)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                yield client

    def querying_model(self, urls, query):
        return ModelService([
            response([{"type": "tool_use", "id": "search", "name": "tavily_search", "input": {"query": query}}], "tool_use"),
            response([{"type": "tool_use", "id": "extract", "name": "tavily_extract", "input": {"urls": urls}}], "tool_use"),
            response([{"type": "text", "text": "协议测试占位回答，不表示官方政策已核实。"}]),
        ])

    async def test_weekend_materials_show_resolved_dates_and_preserve_policy_conditions(self):
        url = "https://museum.example.test/visit"
        body = "常规9:00–17:00，周一闭馆；节假日以官方通知为准。预约为提前7日内。"
        pages = {url: {"raw_content": body}}
        model = self.querying_model([url], "测试博物馆 官方规则 2026-10-10 2026-10-11")
        with tempfile.TemporaryDirectory() as directory:
            async with self.service(directory, model, pages) as client:
                _, saved = await self.finish(client, "本周末去北京测试博物馆，核实开放与预约")
                payload = (await client.get("/api/payloads/" + saved["turns"][0]["query_materials"][1]["payload_id"])).json()
        turn = saved["turns"][0]
        self.assertEqual(turn["status"], "completed")
        dates = context_from_request(model.requests[0])["travel_dates"]
        self.assertEqual((dates["start_date"], dates["end_date"]), ("2026-10-10", "2026-10-11"))
        self.assertTrue(all(context_from_request(request)["travel_dates"] == dates for request in model.requests))
        materials = turn["query_materials"]
        self.assertEqual(len(materials), 2)
        self.assertIn(body, str(materials[1]))
        for material in materials:
            self.assertIn("2026-10-10 至 2026-10-11", str(material["notes"]))
        envelope = json.loads(payload["content"]["content"])
        data = envelope["data"] or json.loads(envelope["text"][0])
        self.assertEqual(data["results"][0]["raw_content"], body)
        self.assertEqual(context_from_request(model.requests[2]), turn["travel_date_context"])

    async def test_restart_and_date_change_keep_raw_announcements_and_separate_date_basis(self):
        pages = {
            "https://museum.example.test/valid": {"raw_content": "展厅2026-10-09至2026-10-11临时闭馆。", "published_date": "2026-10-08", "applicable_dates": "2026-10-09 至 2026-10-11"},
            "https://museum.example.test/expired": {"raw_content": "展厅2026-08-11至2026-09-14闭馆。"},
            "https://museum.example.test/no-end": {"raw_content": "另一展厅2026-08-03开始施工封闭，未载明结束日期。"},
            "https://museum.example.test/rule-a": {"raw_content": "测试博物馆周一闭馆，节假日以公告为准。"},
            "https://museum.example.test/rule-b": {"raw_content": "测试博物馆周二闭馆，适用版本未载明。"},
            "https://museum.example.test/unread": {},
        }
        urls = list(pages)
        first_model = self.querying_model(urls, "测试博物馆 公告 2026-10-10 2026-10-11")
        second_model = self.querying_model(urls, "测试博物馆 公告 2026-10-17 2026-10-18")
        with tempfile.TemporaryDirectory() as directory:
            self.moment = datetime.fromisoformat("2026-10-09T23:59:59+08:00")
            async with self.service(directory, first_model, pages) as client:
                first, original = await self.finish(client, "本周末去北京测试博物馆，核实公告")
            self.moment = datetime.fromisoformat("2026-10-10T09:00:00+08:00")
            # 重启后 first 的资料保留；新的检索和正文结果属于明确改期的轮次。
            async with self.service(directory, second_model, pages) as client:
                before = (await client.get("/api/sessions/" + first["session_id"])).json()
                _, changed = await self.finish(client, "旅行日期改到下周末，保留测试博物馆并重新核实", first["session_id"])
                raw = (await client.get("/api/payloads/" + changed["turns"][-1]["query_materials"][1]["payload_id"])).json()
        self.assertEqual(before["turns"][0], original["turns"][0])
        self.assertEqual(changed["turns"][0], original["turns"][0])
        context = context_from_request(second_model.requests[0])
        self.assertEqual((context["travel_dates"]["start_date"], context["travel_dates"]["end_date"]), ("2026-10-17", "2026-10-18"))
        self.assertTrue(context["dates_changed"])
        self.assertEqual(context["previous_travel_dates"]["start_date"], "2026-10-10")
        self.assertEqual(second_model.requests[0]["messages"][:-1], original["turns"][0]["messages"])
        self.assertEqual(second_model.requests[0]["messages"][-1]["content"], "旅行日期改到下周末，保留测试博物馆并重新核实")
        turn = changed["turns"][-1]
        self.assertEqual(turn["status"], "completed")
        materials = turn["query_materials"]
        for material in materials:
            self.assertIn("2026-10-17 至 2026-10-18", str(material["notes"]))
        body_material = materials[1]
        for page in pages.values():
            if "raw_content" in page:
                self.assertIn(page["raw_content"], str(body_material["entries"]))
        self.assertIn("正文未取得", str(body_material["entries"]))
        envelope = json.loads(raw["content"]["content"])
        data = envelope["data"] or json.loads(envelope["text"][0])
        self.assertEqual(data["results"], [{"url": url, **page} for url, page in pages.items()])
        self.assertEqual(context_from_request(second_model.requests[2]), turn["travel_date_context"])

    async def test_undated_trip_keeps_source_applicability_unconfirmed(self):
        url = "https://museum.example.test/visit"
        model = self.querying_model([url], "测试博物馆 官方")
        with tempfile.TemporaryDirectory() as directory:
            async with self.service(directory, model, {url: {"raw_content": "常规9:00–17:00。"}}) as client:
                _, saved = await self.finish(client, "去北京测试博物馆，先看看参观规则")
        context = context_from_request(model.requests[0])
        self.assertEqual(context["travel_dates"]["status"], "unspecified")
        for material in saved["turns"][0]["query_materials"]:
            self.assertIn("旅行日期尚未确定", str(material["notes"]))
