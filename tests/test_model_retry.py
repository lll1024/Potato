"""通过查询循环和公开持久接口验证模型请求重试。"""
import json
import asyncio
from email.utils import formatdate
from datetime import datetime
import unittest
import tempfile
from unittest.mock import AsyncMock, patch
from typing import cast

import httpx2
from anthropic import APITimeoutError, AsyncAnthropic
from mcp import Client

from travel_app.planning import plan
from travel_app.local_records import PlanningSeed
from travel_app.retrieval import Retrieval

from service_fixtures import MapService, ModelService, response
from http_fixtures import serving, wait_finished, wait_state
from record_faults import fail_event
from model_fixtures import RetryModel, connection_error, status_error


class ModelRetryTests(unittest.IsolatedAsyncioTestCase):
    async def test_snapshot_reports_terminal_failure_code_without_reusing_previous_attempt(self):
        model = RetryModel([connection_error(), status_error(401), response([{"type": "text", "text": "请提供城市。"}])])
        with tempfile.TemporaryDirectory() as directory:
            async with serving(directory, model) as client:
                accepted = (await client.post("/api/turns", json={"input": "规划旅行"})).json()
                saved = await asyncio.wait_for(wait_finished(client, accepted["session_id"]), 5)
                self.assertEqual(saved["turns"][0]["model_error_code"], "AUTH")
                failure = saved["turns"][0]
                retry = (await client.post("/api/turns", json={"input": "规划旅行", "session_id": accepted["session_id"]})).json()
                finished = await asyncio.wait_for(wait_finished(client, accepted["session_id"], retry["turn_id"]), 5)
                self.assertEqual(finished["turns"][0], failure)
                self.assertIsNone(finished["turns"][1]["model_error_code"])
                reopened = (await client.get("/api/sessions/" + accepted["session_id"] + "?include_messages=false")).json()
                self.assertEqual(reopened["turns"][0]["model_error_code"], "AUTH")

    async def test_transient_failure_recovers_in_last_allowed_step_without_repeating_input(self):
        model = RetryModel([connection_error(), response([{"type": "text", "text": "请提供城市。"}])])
        tools = Retrieval(cast(Client, MapService()))
        await tools.discover()
        events = []

        async def observe(kind, data):
            events.append((kind, data))

        messages = [{"role": "user", "content": "帮我规划明天旅行"}]
        planning_result = await plan(
            messages[-1]["content"], cast(AsyncAnthropic, model), tools, "test-model",
            seed=PlanningSeed(messages_json=json.dumps(messages[:-1], ensure_ascii=False)),
            max_rounds=1,
            observer=observe,
        )
        answer = planning_result.answer
        messages[:] = json.loads(planning_result.material.messages_json)
        self.assertEqual(answer, "请提供城市。")
        self.assertEqual(model.requests[0], model.requests[1])
        self.assertEqual([item["role"] for item in messages], ["user", "assistant"])
        self.assertEqual(messages[0]["content"], "帮我规划明天旅行")
        self.assertEqual(messages[1]["content"][0]["text"], "请提供城市。")
        starts = [data for kind, data in events if kind == "request.started"]
        self.assertEqual([item["step"] for item in starts], [1, 1])
        self.assertEqual([item["attempt"] for item in starts], [1, 2])
        self.assertEqual(len({item["request_id"] for item in starts}), 2)
        self.assertEqual(events[-1][1]["status"], "completed")

    async def test_only_classified_transient_model_failures_are_retried(self):
        cases = [
            (APITimeoutError(request=httpx2.Request("POST", "https://model.example.test")), "TIMEOUT", True),
            (status_error(429), "RATE_LIMIT", True),
            (status_error(503), "SERVER", True),
            (status_error(401), "AUTH", False),
            (status_error(403), "AUTH", False),
            (status_error(402), "QUOTA", False),
            (status_error(429, {"error": {"code": "insufficient_quota"}}), "QUOTA", False),
            (status_error(400, {"error": {"message": "maximum context length exceeded"}}), "CONTEXT_WINDOW_EXCEEDED", False),
            (status_error(413, {"error": {"message": "request too large for model context window"}}), "CONTEXT_WINDOW_EXCEEDED", False),
            (status_error(400, {"error": {"code": "context_window_limit_exceeded"}}), "CONTEXT_WINDOW_EXCEEDED", False),
            (status_error(400, {"error": {"message": "messages exceed the model context"}}), "CONTEXT_WINDOW_EXCEEDED", False),
            (status_error(413), "INVALID_REQUEST", False),
            (status_error(408), "HTTP_408", False),
            (status_error(409), "HTTP_409", False),
            (status_error(409, {"error": {"type": "overloaded_error"}}), "SERVER", True),
            (RuntimeError("local failure"), "UNKNOWN", False),
        ]
        tools = Retrieval(cast(Client, MapService()))
        await tools.discover()
        for error, code, retried in cases:
            with self.subTest(code=code, status=getattr(error, "status_code", None)):
                model = RetryModel([error, response([{"type": "text", "text": "已恢复"}])])
                events = []

                async def observe(kind, data):
                    events.append((kind, data))

                with patch("asyncio.sleep", new_callable=AsyncMock):
                    planning_result = await plan(
                        '查地点', cast(AsyncAnthropic, model), tools, "test",
                        seed=PlanningSeed(messages_json=json.dumps([], ensure_ascii=False)),
                        observer=observe,
                    )
                failure = next(data["error"] for kind, data in events if kind == "request.failed")
                self.assertEqual(failure["code"], code)
                self.assertEqual(len(model.requests), 2 if retried else 1)
                self.assertEqual(events[-1][1]["status"], "completed" if retried else "failed")

    async def test_retry_after_is_honored_without_jitter_and_long_waits_end_the_turn(self):
        tools = Retrieval(cast(Client, MapService()))
        await tools.discover()
        for header, delay in [("2", 2), ("10", 10), ("11", None), ("0", 0.5),
                              ("-1", 0.5), ("invalid", 0.5),
                              (formatdate(2002, usegmt=True), 2), (formatdate(1999, usegmt=True), 0.5)]:
            with self.subTest(header=header):
                model = RetryModel([status_error(429, headers={"Retry-After": header}),
                                    response([{"type": "text", "text": "已恢复"}])])
                with patch("travel_app.model_retry.time.time", return_value=2000), \
                     patch("travel_app.model_retry.random.random", return_value=0.5), \
                     patch("asyncio.sleep", new_callable=AsyncMock) as sleep:
                    planning_result = await plan(
                        '查地点', cast(AsyncAnthropic, model), tools, "test",
                        seed=PlanningSeed(messages_json=json.dumps([], ensure_ascii=False)),
                    )
                self.assertEqual([call.args[0] for call in sleep.await_args_list], [] if delay is None else [delay])
                self.assertEqual(len(model.requests), 1 if delay is None else 2)

    async def test_empty_response_is_retried_but_failed_attempts_never_enter_context(self):
        model = RetryModel([response([]), response([{"type": "text", "text": "已恢复"}])])
        tools = Retrieval(cast(Client, MapService()))
        await tools.discover()
        with patch("asyncio.sleep", new_callable=AsyncMock):
            planning_result = await plan(
                '查地点', cast(AsyncAnthropic, model), tools, "test",
                seed=PlanningSeed(messages_json=json.dumps([], ensure_ascii=False)),
            )
            answer = planning_result.answer
        self.assertEqual(answer, "已恢复")
        self.assertEqual(model.requests[0], model.requests[1])

    async def test_retry_history_and_underlying_cause_survive_restart(self):
        error = connection_error()
        error.__cause__ = httpx2.ConnectError("TLS failed known-secret")
        model = RetryModel([error, response([{"type": "text", "text": "已恢复"}])])
        with tempfile.TemporaryDirectory() as directory:
            async with serving(directory, model) as client:
                accepted = (await client.post("/api/turns", json={"input": "明天去杭州"})).json()
                saved = await asyncio.wait_for(wait_finished(client, accepted["session_id"]), 3)
                self.assertEqual(saved["turns"][0]["status"], "completed")
                self.assertEqual([item["step"] for item in saved["requests"]], [1, 1])
                self.assertEqual([item["attempt"] for item in saved["requests"]], [1, 2])
                self.assertEqual(saved["usage_summary"]["input_tokens"],
                                 {"value": 1, "known_count": 1, "request_count": 2})
                retries = [event for event in saved["events"] if event["kind"].startswith("request.retry_")]
                self.assertEqual([event["kind"] for event in retries], ["request.retry_scheduled", "request.retry_started"])
                self.assertEqual(retries[0]["data"]["retry_id"], retries[1]["data"]["retry_id"])
                payload = (await client.get("/api/payloads/" + saved["requests"][0]["error_payload_id"])).json()["content"]
                self.assertEqual(payload["causes"][0]["category"], "ConnectError")
                self.assertIn("TLS failed", payload["causes"][0]["message"])
                self.assertNotIn("known-secret", str(payload))
            async with serving(directory, ModelService([])) as client:
                restored = (await client.get("/api/sessions/" + accepted["session_id"])).json()
                self.assertEqual(restored["requests"], saved["requests"])
                self.assertEqual(restored["events"], saved["events"])

    async def test_exhaustion_has_six_actual_attempts_and_independent_backoff(self):
        tools = Retrieval(cast(Client, MapService()))
        await tools.discover()
        for random_value, delays in [(0, [0.45, 0.9, 1.8, 3.6, 7.2]),
                                     (1, [0.55, 1.1, 2.2, 4.4, 8.8])]:
            model = RetryModel([connection_error() for _ in range(6)])
            events = []

            async def observe(kind, data):
                events.append((kind, data))

            with patch("travel_app.model_retry.random.random", return_value=random_value), \
                 patch("asyncio.sleep", new_callable=AsyncMock) as sleep:
                planning_result = await plan(
                    '查地点', cast(AsyncAnthropic, model), tools, "test",
                    seed=PlanningSeed(messages_json=json.dumps([], ensure_ascii=False)),
                    max_rounds=1,
                    observer=observe,
                )
            self.assertEqual(len(model.requests), 6)
            self.assertEqual([call.args[0] for call in sleep.await_args_list], delays)
            self.assertEqual(events[-1][1]["status"], "failed")

    async def test_steps_reset_retry_budget_without_repeating_tools_or_refreshing_date(self):
        service = MapService()
        tools = Retrieval(cast(Client, service))
        await tools.discover()
        model = RetryModel([*[connection_error() for _ in range(5)],
            response([{"type": "tool_use", "id": "place", "name": "maps_text_search",
                       "input": {"keywords": "西湖", "city": "杭州"}}], "tool_use"),
            *[connection_error() for _ in range(5)], response([{"type": "text", "text": "西湖"}])])
        dates = []

        def clock():
            dates.append("read")
            return datetime.fromisoformat("2026-10-12T23:59:59+08:00")

        with patch("asyncio.sleep", new_callable=AsyncMock):
            planning_result = await plan(
                '明天查杭州西湖', cast(AsyncAnthropic, model), tools, "test",
                seed=PlanningSeed(messages_json=json.dumps([], ensure_ascii=False)),
                max_rounds=2,
                max_tool_calls=2,
                clock=clock,
                preference_background="住宿喜好：经济型酒店",
            )
            answer = planning_result.answer
        self.assertEqual(answer, "西湖")
        self.assertEqual(dates, ["read"])
        self.assertEqual(len(model.requests), 12)
        self.assertEqual(model.requests[6]["messages"][-1]["content"][0]["tool_use_id"], "place")
        self.assertEqual(model.requests[6], model.requests[11])
        self.assertEqual(model.requests[0]["system"], model.requests[11]["system"])

    async def test_stop_during_wait_and_before_next_attempt_never_dispatches_again(self):
        tools = Retrieval(cast(Client, MapService()))
        await tools.discover()
        for boundary in ("request.failed", "request.retry_scheduled", "request.retry_started"):
            with self.subTest(boundary=boundary):
                stop = asyncio.Event()
                events = []

                async def observe(kind, data):
                    events.append((kind, data))
                    if kind == boundary:
                        stop.set()

                model = RetryModel([connection_error()])
                planning_result = await plan(
                    '查地点', cast(AsyncAnthropic, model), tools, "test",
                    seed=PlanningSeed(messages_json=json.dumps([], ensure_ascii=False)),
                    observer=observe,
                    stop_requested=stop,
                )
                self.assertEqual(len(model.requests), 1)
                self.assertEqual(events[-1][1]["reason"], "model_error" if boundary == "request.failed" else "user_stop")

    async def test_authentication_diagnostics_never_keep_provider_key_fragments(self):
        model = RetryModel([status_error(401, {"error": {"message": "key private-fragment is invalid"}})])
        with tempfile.TemporaryDirectory() as directory:
            async with serving(directory, model) as client:
                accepted = (await client.post("/api/turns", json={"input": "查地点"})).json()
                saved = await asyncio.wait_for(wait_finished(client, accepted["session_id"]), 3)
                payload = (await client.get("/api/payloads/" + saved["requests"][0]["error_payload_id"])).json()
                self.assertNotIn("private-fragment", str(payload))
                self.assertIn("API 密钥无效", str(payload))

    async def test_retry_schedule_storage_failure_prevents_another_model_request(self):
        model = RetryModel([connection_error(), response([{"type": "text", "text": "不应执行"}])])
        with tempfile.TemporaryDirectory() as directory:
            async with serving(directory, model) as client:
                fail_event(directory, "request.retry_scheduled")
                accepted = (await client.post("/api/turns", json={"input": "查地点"})).json()
                state = await wait_state(client, lambda state: not state["accepting"])
                self.assertEqual(state["unsaved_fact"]["kind"], "request.retry_scheduled")
                saved = (await client.get("/api/sessions/" + accepted["session_id"])).json()
                self.assertEqual(len(model.requests), 1)
                self.assertEqual(saved["requests"][0]["status"], "failed")
                self.assertEqual(saved["turns"][0]["status"], "running")

    async def test_public_stop_interrupts_retry_wait_and_releases_execution_slot(self):
        model = RetryModel([status_error(429, headers={"Retry-After": "10"})])
        with tempfile.TemporaryDirectory() as directory:
            async with serving(directory, model) as client:
                accepted = (await client.post("/api/turns", json={"input": "查地点"})).json()

                async def waiting():
                    while True:
                        saved = (await client.get("/api/sessions/" + accepted["session_id"])).json()
                        if any(event["kind"] == "request.retry_scheduled" for event in saved["events"]):
                            return
                        await asyncio.sleep(.01)

                await asyncio.wait_for(waiting(), 3)
                await client.post("/api/turns/" + accepted["turn_id"] + "/stop")
                saved = await asyncio.wait_for(wait_finished(client, accepted["session_id"]), 2)
                self.assertEqual(saved["turns"][0]["reason"], "user_stop")
                self.assertEqual(len(saved["requests"]), 1)
                self.assertFalse(any(event["kind"] == "request.retry_started" for event in saved["events"]))
                self.assertIsNone((await client.get("/api/state")).json()["active_turn_id"])

    async def test_provider_request_headers_are_preserved_in_saved_failure_details(self):
        for header in ("request-id", "x-request-id", "x-deepseek-request-id"):
            with self.subTest(header=header), tempfile.TemporaryDirectory() as directory:
                model = RetryModel([status_error(503, headers={header: "service-123", "Retry-After": "11"})])
                async with serving(directory, model) as client:
                    accepted = (await client.post("/api/turns", json={"input": "查地点"})).json()
                    saved = await asyncio.wait_for(wait_finished(client, accepted["session_id"]), 3)
                    payload = (await client.get("/api/payloads/" + saved["requests"][0]["error_payload_id"])).json()["content"]
                    self.assertEqual(payload.get("request_id"), "service-123")
