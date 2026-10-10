"""公开 HTTP 与 CLI 验证旅行日期的跨日、重启、改期和存储故障。"""
import asyncio
import tempfile
import unittest
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from typing import cast

import httpx
from anthropic import AsyncAnthropic
from mcp import Client

from amap_mcp import AmapTools
from test_agent import MapService, ModelService, response
import test_history
from test_travel_dates import context_from_request
from web import Runtime, create_app


class TravelDateContinuationTests(unittest.IsolatedAsyncioTestCase):
    finish = test_history.HistoryTests.finish
    moment = datetime.fromisoformat("2026-10-09T23:59:59+08:00")

    @asynccontextmanager
    async def service(self, directory, model, maps=None) -> AsyncIterator[httpx.AsyncClient]:
        @asynccontextmanager
        async def resources():
            tools = AmapTools(cast(Client, maps or MapService()))
            await tools.discover()
            yield Runtime(cast(AsyncAnthropic, model), tools, "test-model")
        app = create_app(directory, resources=resources, clock=lambda: self.moment)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                yield client

    async def test_trip_dates_survive_midnight_and_restart_without_reinterpreting_history(self):
        for text, start, end in [
            ("明天去杭州西湖", "2026-10-10", "2026-10-10"),
            ("本周末去杭州西湖", "2026-10-10", "2026-10-11"),
        ]:
            with self.subTest(text=text), tempfile.TemporaryDirectory() as directory:
                self.moment = datetime.fromisoformat("2026-10-09T23:59:59+08:00")
                model = ModelService([
                    response([{"type": "tool_use", "id": "west-lake", "name": "maps_text_search",
                               "input": {"keywords": "西湖", "city": "杭州"}}], "tool_use"),
                    response([{"type": "text", "text": "测试模型回答"}]),
                    response([{"type": "text", "text": "测试模型回答"}]),
                    response([{"type": "text", "text": "测试模型回答"}]),
                ])
                async with self.service(directory, model) as client:
                    first, saved = await self.finish(client, text)
                    history = saved["turns"][0]["messages"]
                    self.moment = datetime.fromisoformat("2026-10-10T00:00:01+08:00")
                    await self.finish(client, "保留日期，只调整餐馆", first["session_id"])
                    dates = context_from_request(model.requests[2])["travel_dates"]
                    self.assertEqual(context_from_request(model.requests[2])["today"], "2026-10-10")
                    self.assertEqual(model.requests[2]["messages"], history + [{"role": "user", "content": "保留日期，只调整餐馆"}])
                    before_restart = (await client.get("/api/sessions/" + first["session_id"])).json()
                self.moment = datetime.fromisoformat("2026-10-12T09:00:00+08:00")
                async with self.service(directory, model) as client:
                    self.assertEqual((await client.get("/api/sessions/" + first["session_id"])).json(), before_restart)
                    _, restored = await self.finish(client, "减少一个景点", first["session_id"])
                    context = context_from_request(model.requests[3])
                    self.assertEqual(context["today"], "2026-10-12")
                    self.assertEqual(restored["turns"][-1]["travel_date_context"], context)
                    self.assertEqual(restored["turns"][0], before_restart["turns"][0])
                self.assertEqual((dates.get("start_date"), dates.get("end_date")), (start, end))
                self.assertEqual((context["travel_dates"].get("start_date"), context["travel_dates"].get("end_date")), (start, end))


    async def test_reference_to_original_weekend_preserves_dates_until_explicit_change(self):
        model = ModelService([response([{"type": "text", "text": "测试模型回答"}]) for _ in range(3)])
        with tempfile.TemporaryDirectory() as directory:
            self.moment = datetime.fromisoformat("2026-10-09T09:00:00+08:00")
            async with self.service(directory, model) as client:
                first, _ = await self.finish(client, "本周末去北京")
                self.moment = datetime.fromisoformat("2026-10-12T09:00:00+08:00")
                await self.finish(client, "把本周末行程的餐馆换一家", first["session_id"])
                await self.finish(client, "旅行日期改到下周末，其他条件不变", first["session_id"])
        reference = context_from_request(model.requests[1])
        self.assertEqual((reference["travel_dates"]["start_date"], reference["travel_dates"]["end_date"]),
                         ("2026-10-10", "2026-10-11"))
        self.assertEqual(reference["travel_dates"]["reference_time"], "2026-10-09T09:00:00+08:00")
        changed = context_from_request(model.requests[2])
        self.assertEqual((changed["travel_dates"]["start_date"], changed["travel_dates"]["end_date"]),
                         ("2026-10-24", "2026-10-25"))
        self.assertEqual(changed["previous_travel_dates"]["start_date"], "2026-10-10")
        self.assertEqual(changed["previous_travel_dates"]["end_date"], "2026-10-11")
        self.assertTrue(changed["dates_changed"])


    async def test_unclear_date_change_requires_clarification_instead_of_reusing_old_dates(self):
        model = ModelService([response([{"type": "text", "text": "测试模型回答"}]) for _ in range(2)])
        with tempfile.TemporaryDirectory() as directory:
            async with self.service(directory, model) as client:
                first, _ = await self.finish(client, "本周末去北京")
                await self.finish(client, "旅行日期改到下个月", first["session_id"])
        dates = context_from_request(model.requests[1])["travel_dates"]
        self.assertEqual(dates["status"], "needs_clarification")
        self.assertNotIn("start_date", dates)


    async def test_saved_date_change_survives_interruption_while_protocol_history_is_excluded(self):
        import sqlite3
        from contextlib import closing
        from pathlib import Path
        from test_storage import fail_event

        model = ModelService([response([{"type": "text", "text": "测试模型回答"}]) for _ in range(3)])
        with tempfile.TemporaryDirectory() as directory:
            self.moment = datetime.fromisoformat("2026-10-09T09:00:00+08:00")
            async with self.service(directory, model) as client:
                first, completed = await self.finish(client, "本周末去北京")
                fail_event(directory, "turn.finished")
                accepted = (await client.post("/api/turns", json={"input": "日期改到下周末", "session_id": first["session_id"]})).json()
                for _ in range(100):
                    await asyncio.sleep(.01)
                    if (await client.get("/api/state")).json()["active_turn_id"] is None:
                        break
                interrupted = (await client.get("/api/sessions/" + first["session_id"])).json()
            with closing(sqlite3.connect(Path(directory) / "travel.sqlite3")) as database, database:
                database.execute("DROP TRIGGER fail_event")
            self.moment = datetime.fromisoformat("2026-10-12T09:00:00+08:00")
            async with self.service(directory, model) as client:
                _, resumed = await self.finish(client, "继续原旅行安排，只换餐馆", first["session_id"])
        changed = context_from_request(model.requests[-1])
        self.assertEqual((changed["travel_dates"].get("start_date"), changed["travel_dates"].get("end_date")),
                         ("2026-10-17", "2026-10-18"))
        self.assertEqual(model.requests[-1]["messages"], completed["turns"][0]["messages"] +
                         [{"role": "user", "content": "继续原旅行安排，只换餐馆"}])
        self.assertTrue(resumed["turns"][1]["context_excluded"])
        self.assertEqual(resumed["requests"][:2], interrupted["requests"])
        self.assertEqual(resumed["turns"][1]["turn_id"], accepted["turn_id"])


    async def test_legacy_record_without_date_basis_does_not_infer_dates_from_history(self):
        from pathlib import Path
        from store import Store

        with tempfile.TemporaryDirectory() as directory:
            legacy = Store(Path(directory))
            first = legacy.accept("明天去北京")
            history = [{"role": "user", "content": "明天去北京"},
                       {"role": "assistant", "content": "旅行安排"}]
            legacy.finish(first["session_id"], first["turn_id"], "旅行安排", history,
                          {"status": "completed", "reason": None, "answer_source": "model", "tool_error_count": 0}, 0)
            legacy.db.close()
            model = ModelService([response([{"type": "text", "text": "测试模型回答"}])])
            self.moment = datetime.fromisoformat("2026-10-12T09:00:00+08:00")
            async with self.service(directory, model) as client:
                _, saved = await self.finish(client, "只改餐馆", first["session_id"])
        self.assertIsNone(saved["turns"][0]["travel_date_context"])
        self.assertEqual(context_from_request(model.requests[0])["travel_dates"]["status"], "unspecified")
        self.assertEqual(model.requests[0]["messages"], history + [{"role": "user", "content": "只改餐馆"}])

    async def test_new_session_does_not_inherit_another_trips_dates(self):
        model = ModelService([response([{"type": "text", "text": "测试模型回答"}]) for _ in range(3)])
        with tempfile.TemporaryDirectory() as directory:
            async with self.service(directory, model) as client:
                first, _ = await self.finish(client, "本周末去北京")
                await self.finish(client, "去杭州旅行")
                await self.finish(client, "只改餐馆", first["session_id"])
        self.assertEqual(context_from_request(model.requests[1])["travel_dates"]["status"], "unspecified")
        self.assertEqual(context_from_request(model.requests[2])["travel_dates"]["start_date"], "2026-10-10")

    async def test_unsaved_date_change_does_not_replace_last_reliable_dates(self):
        import sqlite3
        from contextlib import closing
        from pathlib import Path
        from test_storage import fail_event

        initial = ModelService([response([{"type": "text", "text": "测试模型回答"}])])
        next_model = ModelService([response([{"type": "text", "text": "测试模型回答"}])])
        with tempfile.TemporaryDirectory() as directory:
            self.moment = datetime.fromisoformat("2026-10-09T09:00:00+08:00")
            async with self.service(directory, initial) as client:
                first, completed = await self.finish(client, "本周末去北京")
                fail_event(directory, "request.started")
                await client.post("/api/turns", json={"input": "日期改到下周末", "session_id": first["session_id"]})
                state = {}
                for _ in range(100):
                    await asyncio.sleep(.01)
                    state = (await client.get("/api/state")).json()
                    if state["active_turn_id"] is None:
                        break
                interrupted = (await client.get("/api/sessions/" + first["session_id"])).json()
            with closing(sqlite3.connect(Path(directory) / "travel.sqlite3")) as database, database:
                database.execute("DROP TRIGGER fail_event")
            self.moment = datetime.fromisoformat("2026-10-12T09:00:00+08:00")
            async with self.service(directory, next_model) as client:
                await self.finish(client, "继续原旅行安排", first["session_id"])
        self.assertFalse(state["accepting"])
        self.assertEqual(state["unsaved_fact"]["kind"], "request.started")
        self.assertEqual(interrupted["requests"], completed["requests"])
        self.assertEqual(len(initial.requests), 1)
        self.assertEqual(context_from_request(next_model.requests[0])["travel_dates"]["start_date"], "2026-10-10")
        self.assertEqual(context_from_request(next_model.requests[0])["travel_dates"]["end_date"], "2026-10-11")

    async def test_changed_dates_and_failed_weather_materials_use_the_same_full_range(self):
        from test_exception_experience import FailingAfterTools
        from test_itinerary import ItineraryMapService, map_result

        first_model = ModelService([response([{"type": "text", "text": "测试模型回答"}])])
        weather_request = response([{"type": "tool_use", "id": "weather", "name": "maps_weather",
                                    "input": {"city": "北京"}}], "tool_use")
        for forecast in [["2026-10-10", "2026-10-11"], ["2026-10-17"], ["2026-10-17", "2026-10-18"]]:
            with self.subTest(forecast=forecast), tempfile.TemporaryDirectory() as directory:
                self.moment = datetime.fromisoformat("2026-10-09T09:00:00+08:00")
                async with self.service(directory, first_model) as client:
                    first, _ = await self.finish(client, "本周末去北京")
                changed_model = FailingAfterTools([weather_request])
                maps = ItineraryMapService([map_result({"city": "北京", "forecasts": [
                    {"date": day, "dayweather": "晴"} for day in forecast]})])
                async with self.service(directory, changed_model, maps) as client:
                    _, saved = await self.finish(client, "旅行日期改到下周末，其他条件不变", first["session_id"])
                context = context_from_request(changed_model.requests[0])
                turn = saved["turns"][-1]
                self.assertEqual(turn["travel_date_context"], context)
                self.assertEqual(turn["reason"], "model_error")
                materials = str(turn["query_materials"])
                self.assertIn("2026-10-17 至 2026-10-18", materials)
                self.assertNotIn("尚未确认", materials)
                self.assertIn("覆盖已确定的旅行日期" if len(forecast) == 2 and forecast[0] == "2026-10-17" else "超出", materials)
                first_model = ModelService([response([{"type": "text", "text": "测试模型回答"}])])


    async def test_cli_continuation_retains_trip_dates_when_time_advances(self):
        import io
        import os
        from contextlib import redirect_stdout
        from unittest.mock import patch
        from agent import run_cli

        before = datetime.fromisoformat("2026-10-09T23:59:59+08:00")
        after = datetime.fromisoformat("2026-10-10T00:00:01+08:00")
        moment = before
        model = ModelService([response([{"type": "text", "text": "测试模型回答"}]) for _ in range(2)])
        queries = iter(["明天去杭州西湖", "保持日期，改一下餐馆", "exit"])
        def user_input(prompt):
            nonlocal moment
            text = next(queries)
            if "餐馆" in text:
                moment = after
            return text
        @asynccontextmanager
        async def external_model(*args, **kwargs):
            yield model
        @asynccontextmanager
        async def external_maps(*args, **kwargs):
            yield MapService()
        with (
            patch("agent.Client", side_effect=external_maps),
            patch("travel_tools.Client", side_effect=RuntimeError("外部资料服务不可用")),
            patch("agent.AsyncAnthropic", side_effect=external_model),
            patch("agent.streamable_http_client", return_value=None),
            patch("builtins.input", side_effect=user_input),
            patch.dict(os.environ, {"MODEL_ID": "test-model"}),
            redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(await run_cli("test-key", clock=lambda: moment), 0)
        context = context_from_request(model.requests[1])
        self.assertEqual(context["now"], "2026-10-10T00:00:01+08:00")
        self.assertEqual(context["travel_dates"].get("start_date"), "2026-10-10")
        self.assertEqual(context["travel_dates"].get("end_date"), "2026-10-10")
