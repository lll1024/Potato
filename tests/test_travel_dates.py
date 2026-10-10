"""通过公开 HTTP 对话入口核对固定时间、旅行日期与异常天气资料。"""
import asyncio
import json
import tempfile
import unittest
from contextlib import asynccontextmanager
from datetime import datetime
from typing import cast

import httpx
from anthropic import AsyncAnthropic
from mcp import Client

from amap_mcp import AmapTools
from test_agent import MapService, ModelService, response
from web import Runtime, create_app


def context_from_request(request):
    return json.loads(request["system"].split("<travel_date_context>")[1].split("</travel_date_context>")[0])


class TravelDatesTests(unittest.IsolatedAsyncioTestCase):
    async def run_turn(self, text, now, model=None, maps=None):
        model = model or ModelService([response([{"type": "text", "text": "测试模型回答"}])])
        @asynccontextmanager
        async def resources():
            tools = AmapTools(cast(Client, maps or MapService()))
            await tools.discover()
            yield Runtime(cast(AsyncAnthropic, model), tools, "test-model")
        with tempfile.TemporaryDirectory() as directory:
            app = create_app(directory, resources=resources, clock=lambda: datetime.fromisoformat(now))
            async with app.router.lifespan_context(app):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                    accepted = (await client.post("/api/turns", json={"input": text})).json()
                    saved = {}
                    for _ in range(100):
                        saved = (await client.get("/api/sessions/" + accepted["session_id"])).json()
                        if saved["turns"][0]["status"] != "running":
                            break
                        await asyncio.sleep(0.01)
                    self.assertNotEqual(saved["turns"][0]["status"], "running")
                    return saved, model

    async def test_this_weekend_uses_beijing_date_and_supplies_concrete_dates(self):
        saved, model = await self.run_turn("本周末去北京", "2026-10-09T16:30:00+00:00")
        context = context_from_request(model.requests[0])
        self.assertEqual(context["timezone"], "Asia/Shanghai")
        self.assertEqual(context["now"], "2026-10-10T00:30:00+08:00")
        self.assertEqual(context["weekday"], "星期六")
        self.assertEqual(context["travel_dates"]["status"], "resolved")
        self.assertEqual(context["travel_dates"]["start_date"], "2026-10-10")
        self.assertEqual(context["travel_dates"]["end_date"], "2026-10-11")
        self.assertEqual(saved["turns"][0]["travel_date_context"], context)

    async def test_weekend_rules_and_year_boundary_are_concrete(self):
        examples = [
            ("本周末去北京", "2026-10-09T09:00:00+08:00", "2026-10-10", "2026-10-11"),
            ("下周末去北京", "2026-10-09T09:00:00+08:00", "2026-10-17", "2026-10-18"),
            ("本周末去北京", "2026-10-11T09:00:00+08:00", "2026-10-11", "2026-10-11"),
            ("明天去杭州", "2026-12-31T09:00:00+08:00", "2027-01-01", "2027-01-01"),
        ]
        for text, now, start, end in examples:
            with self.subTest(text=text, now=now):
                saved, model = await self.run_turn(text, now)
                dates = context_from_request(model.requests[0])["travel_dates"]
                self.assertEqual(dates["status"], "resolved")
                self.assertEqual((dates["start_date"], dates["end_date"]), (start, end))
                if now.startswith("2026-10-11"):
                    self.assertIn("周六已过去", dates["explanation"])
                self.assertEqual(saved["turns"][0]["travel_date_context"]["travel_dates"], dates)

    async def test_sunday_two_day_request_requires_targeted_clarification(self):
        saved, model = await self.run_turn("本周末两日游去北京", "2026-10-11T09:00:00+08:00")
        dates = context_from_request(model.requests[0])["travel_dates"]
        self.assertEqual(dates["status"], "needs_clarification")
        self.assertIn("两日", dates["explanation"])
        self.assertIn("2026-10-11", dates["explanation"])
        self.assertNotIn("start_date", dates)
        self.assertEqual(saved["turns"][0]["travel_date_context"]["travel_dates"], dates)

    async def test_explicit_dates_override_relative_default_and_infer_duration(self):
        examples = [
            ("本周末去北京，改为2026年10月17日至2026年10月18日", "2026-10-17", "2026-10-18"),
            ("2026年10月9日起杭州旅行三天", "2026-10-09", "2026-10-11"),
            ("10月17日至18日去北京", "2026-10-17", "2026-10-18"),
        ]
        for text, start, end in examples:
            with self.subTest(text=text):
                saved, model = await self.run_turn(text, "2026-10-09T09:00:00+08:00")
                dates = context_from_request(model.requests[0])["travel_dates"]
                self.assertEqual(dates["status"], "resolved")
                self.assertEqual((dates["start_date"], dates["end_date"]), (start, end))
                self.assertEqual(dates["source"], "explicit")

    async def test_invalid_ranges_and_ambiguous_dates_do_not_choose_a_date(self):
        examples = [
            "2026年10月10日至2026年10月11日去北京三天",
            "2026年10月12日至2026年10月10日去北京",
            "2026年2月30日去北京",
            "本周末还是下周末去北京",
            "2026年10月10日或2026年10月11日去北京",
            "本周末去北京三日游",
        ]
        for text in examples:
            with self.subTest(text=text):
                _, model = await self.run_turn(text, "2026-10-09T09:00:00+08:00")
                dates = context_from_request(model.requests[0])["travel_dates"]
                self.assertEqual(dates["status"], "needs_clarification")
                self.assertTrue(dates["explanation"])
                self.assertNotIn("start_date", dates)

    async def test_same_round_keeps_snapshot_across_midnight_and_next_round_refreshes(self):
        before = datetime.fromisoformat("2026-12-31T23:59:59+08:00")
        after = datetime.fromisoformat("2027-01-01T00:00:01+08:00")
        moment = before
        class MidnightModel(ModelService):
            async def create(self, **kwargs):
                nonlocal moment
                moment = after
                return await super().create(**kwargs)
        model = MidnightModel([
            response([{"type": "tool_use", "id": "place", "name": "maps_text_search",
                       "input": {"keywords": "西湖", "city": "杭州"}}], "tool_use"),
            response([{"type": "text", "text": "测试模型回答"}]),
            response([{"type": "text", "text": "测试模型回答"}]),
        ])
        @asynccontextmanager
        async def resources():
            tools = AmapTools(cast(Client, MapService()))
            await tools.discover()
            yield Runtime(cast(AsyncAnthropic, model), tools, "test-model")
        with tempfile.TemporaryDirectory() as directory:
            app = create_app(directory, resources=resources, clock=lambda: moment)
            async with app.router.lifespan_context(app):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                    for _ in range(2):
                        # 两个独立会话避免把历史日期持久化验收混入本切片。
                        accepted = (await client.post("/api/turns", json={"input": "明天去杭州西湖"})).json()
                        saved = {}
                        for _ in range(100):
                            saved = (await client.get("/api/sessions/" + accepted["session_id"])).json()
                            if saved["turns"][0]["status"] != "running":
                                break
                            await asyncio.sleep(0.01)
                        self.assertEqual(saved["turns"][0]["status"], "completed")
        contexts = [context_from_request(request) for request in model.requests]
        self.assertEqual(contexts[0], contexts[1])
        self.assertEqual(contexts[0]["now"], "2026-12-31T23:59:59+08:00")
        self.assertEqual(contexts[2]["now"], "2027-01-01T00:00:01+08:00")
        self.assertEqual(contexts[0]["travel_dates"]["start_date"], "2027-01-01")
        self.assertEqual(contexts[2]["travel_dates"]["start_date"], "2027-01-02")

    async def test_failed_answer_weather_uses_resolved_relative_date_range(self):
        from test_exception_experience import FailingAfterTools
        from test_itinerary import ItineraryMapService, map_result
        for covered, expected in [
            (["2026-10-10"], "超出"),
            (["2026-10-10", "2026-10-10-invalid"], "超出"),
            (["2026-10-10", "2026-10-11"], "覆盖已确定的旅行日期"),
        ]:
            with self.subTest(covered=covered):
                maps = ItineraryMapService([map_result({"city": "北京", "forecasts": [
                    {"date": day, "dayweather": "晴"} for day in covered]})])
                model = FailingAfterTools([response([{"type": "tool_use", "id": "weather", "name": "maps_weather",
                    "input": {"city": "北京"}}], "tool_use")])
                saved, model = await self.run_turn("本周末去北京", "2026-10-09T09:00:00+08:00", model, maps)
                turn = saved["turns"][0]
                self.assertEqual(turn["reason"], "model_error")
                materials = str(turn["query_materials"])
                self.assertIn(expected, materials)
                self.assertNotIn("旅行年份或日期尚未确认", materials)
                self.assertEqual(turn["travel_date_context"], context_from_request(model.requests[0]))

    async def test_cli_entry_supplies_same_date_context_as_web(self):
        import io
        import os
        from contextlib import redirect_stdout
        from unittest.mock import patch
        from agent import run_cli

        _, web_model = await self.run_turn("本周末去北京", "2026-10-09T09:00:00+08:00")
        cli_model = ModelService([response([{"type": "text", "text": "测试模型回答"}])])
        @asynccontextmanager
        async def external_model(*args, **kwargs):
            yield cli_model
        @asynccontextmanager
        async def external_maps(*args, **kwargs):
            yield MapService()
        with (
            patch("agent.Client", side_effect=external_maps),
            patch("travel_tools.Client", side_effect=RuntimeError("外部资料服务不可用")),
            patch("agent.AsyncAnthropic", side_effect=external_model),
            patch("agent.streamable_http_client", return_value=None),
            patch("builtins.input", side_effect=["本周末去北京", "exit"]),
            patch.dict(os.environ, {"MODEL_ID": "test-model"}),
            redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(await run_cli("test-key", clock=lambda: datetime.fromisoformat("2026-10-09T09:00:00+08:00")), 0)
        self.assertEqual(context_from_request(cli_model.requests[0]), context_from_request(web_model.requests[0]))

    async def test_clock_times_and_number_ranges_do_not_override_weekend(self):
        _, model = await self.run_turn("本周末去北京，10:00-18:00活动，步行10-20分钟", "2026-10-09T09:00:00+08:00")
        dates = context_from_request(model.requests[0])["travel_dates"]
        self.assertEqual(dates["status"], "resolved")
        self.assertEqual((dates["start_date"], dates["end_date"]), ("2026-10-10", "2026-10-11"))
