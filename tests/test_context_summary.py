"""真实 HTTP/SSE、SQLite 与可控外部模型验证旧轮次摘要。测试容量刻意小于本机 1M。"""
import asyncio
import json
import os
import tempfile
import unittest
from unittest.mock import patch
from test_agent import ModelService, response
from test_trace import serving, wait_finished

ENV = {"CONTEXT_COMPACTION_ENABLED": "true", "MODEL_CONTEXT_WINDOW": "40000"}
OLD = "2026-10-18 杭州旅行，预算3000元，保留西湖酒店。" + "中" * 3900


def summary_response(**changes):
    value = {"travel_conditions": {"budget": "3000元", "hotel": "西湖酒店"},
             "confirmed_itinerary": [], "corrections": [], "unresolved": ["早餐价格待核实"],
             "sources": [{"record_id": "message:1", "verification": "用户要求"},
                         {"record_id": "message:2", "verification": "未预订"}]}
    value.update(changes)
    return response([{"type": "text", "text": json.dumps(value, ensure_ascii=False)}])


async def submit(client, text, session=None):
    accepted = (await client.post("/api/turns", json={"input": text, "session_id": session})).json()
    return await wait_finished(client, accepted["session_id"], accepted["turn_id"])


class ContextSummaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_old_history_summary_answers_preserves_original_and_restarts(self):
        model = ModelService([response([{"type": "text", "text": "酒店未预订，早餐待核实。"}]),
                              summary_response(), response([{"type": "text", "text": "继续保留酒店。"}])])
        with patch.dict(os.environ, ENV, clear=True), tempfile.TemporaryDirectory() as directory:
            async with serving(directory, model) as client:
                first = await submit(client, OLD)
                saved = await submit(client, "继续安排交通", first["session"]["session_id"])
                self.assertEqual(saved["turns"][-1]["answer"], "继续保留酒店。")
                self.assertEqual([item.get("purpose", "travel") for item in saved["requests"]], ["travel", "context_summary", "travel"])
                summary = model.requests[1]
                self.assertNotIn("tools", summary)
                self.assertLessEqual(summary["max_tokens"], 2048)
                self.assertNotIn("你是旅行助手", summary["system"])
                self.assertIn("message:1", str(summary["messages"]))
                self.assertEqual(saved["turns"][-1]["messages"][0]["content"], OLD)
                self.assertIn("travel_state_summary", str(model.requests[2]["messages"]))
                kinds = [event["kind"] for event in saved["events"]]
                self.assertIn("context.adopted", kinds)
                self.assertIn("context.saved", kinds)
                self.assertEqual(saved["turns"][-1]["travel_date_context"]["travel_dates"]["start_date"], "2026-10-18")
            restarted = ModelService([response([{"type": "text", "text": "续聊仍保留酒店。"}])])
            async with serving(directory, restarted) as client:
                saved = await submit(client, "继续", first["session"]["session_id"])
                self.assertEqual(saved["turns"][-1]["answer"], "续聊仍保留酒店。")
                self.assertIn("travel_state_summary", str(restarted.requests[0]["messages"]))
                self.assertNotIn(OLD, str(restarted.requests[0]["messages"]))

    async def test_invalid_candidate_retried_once_then_original_input_answers(self):
        for bad in (summary_response(sources=[{"record_id": "message:999", "verification": "已核实"}]),
                    response([{"type": "text", "text": "{}"}]),
                    response([{"type": "text", "text": "摘要被截断"}], "max_tokens"),
                    summary_response(travel_conditions={"details": "中" * 5000})):
            with self.subTest(candidate=bad.stop_reason), patch.dict(os.environ, ENV, clear=True), tempfile.TemporaryDirectory() as directory:
                model = ModelService([response([{"type": "text", "text": "酒店未预订。"}]), bad, bad,
                                      response([{"type": "text", "text": "保留原要求继续回答。"}])])
                async with serving(directory, model) as client:
                    first = await submit(client, OLD)
                    saved = await submit(client, "继续", first["session"]["session_id"])
                    self.assertEqual(saved["turns"][-1]["answer"], "保留原要求继续回答。")
                    self.assertEqual(len([request for request in saved["requests"] if request.get("purpose") == "context_summary"]), 2)
                    self.assertIn(OLD, str(model.requests[-1]["messages"]))
                    self.assertFalse(any(event["kind"] in ("context.adopted", "context.saved") for event in saved["events"]))

    async def test_invalid_summary_over_budget_ends_without_travel_request(self):
        model = ModelService([response([{"type": "text", "text": "酒店未预订。"}]),
                              response([{"type": "text", "text": "{}"}]), response([{"type": "text", "text": "{}"}])])
        with patch.dict(os.environ, ENV, clear=True), tempfile.TemporaryDirectory() as directory:
            async with serving(directory, model) as client:
                first = await submit(client, OLD)
                saved = await submit(client, "继续" + "中" * 1800, first["session"]["session_id"])
                self.assertEqual(saved["turns"][-1]["reason"], "context_budget")
                self.assertEqual(len(model.requests), 3)
                self.assertEqual(saved["turns"][-1]["messages"][0]["content"], OLD)

    async def test_stop_inflight_summary_records_return_without_adoption_or_next_call(self):
        entered, release = asyncio.Event(), asyncio.Event()
        class WaitingModel(ModelService):
            async def create(self, **kwargs):
                if "整理旅行状态" in kwargs["system"]:
                    entered.set()
                    await release.wait()
                return await super().create(**kwargs)
        model = WaitingModel([response([{"type": "text", "text": "酒店未预订。"}]), summary_response()])
        with patch.dict(os.environ, ENV, clear=True), tempfile.TemporaryDirectory() as directory:
            async with serving(directory, model) as client:
                first = await submit(client, OLD)
                accepted = (await client.post("/api/turns", json={"input": "继续", "session_id": first["session"]["session_id"]})).json()
                await asyncio.wait_for(entered.wait(), 2)
                try:
                    await client.post("/api/turns/" + accepted["turn_id"] + "/stop")
                finally:
                    release.set()
                saved = await wait_finished(client, accepted["session_id"], accepted["turn_id"])
                self.assertEqual(saved["turns"][-1]["reason"], "user_stop")
                self.assertEqual(saved["requests"][-1]["status"], "completed")
                self.assertIsNotNone(saved["requests"][-1]["usage"])
                self.assertEqual(len(model.requests), 2)
                self.assertFalse(any(event["kind"] in ("context.adopted", "context.saved") for event in saved["events"]))

    async def test_candidate_storage_failure_stops_before_next_request(self):
        import sqlite3
        from contextlib import closing
        from pathlib import Path
        model = ModelService([response([{"type": "text", "text": "酒店未预订。"}]), summary_response()])
        with patch.dict(os.environ, ENV, clear=True), tempfile.TemporaryDirectory() as directory:
            async with serving(directory, model) as client:
                first = await submit(client, OLD)
                with closing(sqlite3.connect(Path(directory) / "travel.sqlite3")) as database, database:
                    database.execute("CREATE TRIGGER fail_candidate BEFORE INSERT ON events WHEN NEW.kind='context.candidate' BEGIN SELECT RAISE(ABORT,'controlled storage failure'); END")
                accepted = (await client.post("/api/turns", json={"input": "继续", "session_id": first["session"]["session_id"]})).json()
                for _ in range(100):
                    state = (await client.get("/api/state")).json()
                    if not state["accepting"]:
                        break
                    await asyncio.sleep(0.01)
                self.assertFalse(state["accepting"])
                self.assertEqual(len(model.requests), 2)
                saved = (await client.get("/api/sessions/" + accepted["session_id"])).json()
                self.assertFalse(any(event["kind"] in ("context.adopted", "context.saved", "turn.finished") and event["turn_id"] == accepted["turn_id"] for event in saved["events"]))

    async def test_low_pressure_never_summarizes(self):
        model = ModelService([response([{"type": "text", "text": "请补充人数。"}]), response([{"type": "text", "text": "两人出行。"}])])
        with patch.dict(os.environ, ENV, clear=True), tempfile.TemporaryDirectory() as directory:
            async with serving(directory, model) as client:
                first = await submit(client, "杭州旅行")
                saved = await submit(client, "两人", first["session"]["session_id"])
                self.assertEqual(saved["turns"][-1]["answer"], "两人出行。")
                self.assertEqual(len(model.requests), 2)
                self.assertFalse(any(event["kind"] == "context.summary_started" for event in saved["events"]))

    async def test_summary_requests_have_a_four_call_turn_limit_without_using_travel_steps(self):
        invalid = response([{"type": "text", "text": "{}"}])
        query1 = response([{"type": "tool_use", "id": "first", "name": "maps_text_search", "input": {"keywords": "西湖", "city": "杭州"}}], "tool_use")
        query2 = response([{"type": "tool_use", "id": "second", "name": "maps_text_search", "input": {"keywords": "餐馆", "city": "杭州"}}], "tool_use")
        model = ModelService([response([{"type": "text", "text": "酒店未预订。"}]), invalid, invalid,
                              query1, invalid, invalid, query2, response([{"type": "text", "text": "保留酒店，查询已完成。"}])])
        with patch.dict(os.environ, ENV, clear=True), tempfile.TemporaryDirectory() as directory:
            async with serving(directory, model) as client:
                first = await submit(client, OLD)
                saved = await submit(client, "继续查询", first["session"]["session_id"])
                self.assertEqual(saved["turns"][-1]["answer"], "保留酒店，查询已完成。")
                current = [request for request in saved["requests"] if request["turn_id"] == saved["turns"][-1]["turn_id"]]
                self.assertEqual(len([request for request in current if request["purpose"] == "context_summary"]), 4)
                self.assertEqual([request["step"] for request in current if request["purpose"] == "travel"], [1, 2, 3])
                self.assertEqual(len(saved["tool_calls"]), 2)

    async def test_auth_and_quota_do_not_retry_summary(self):
        from test_model_retry import RetryModel, status_error
        for error in (status_error(401), status_error(402)):
            with self.subTest(status=error.status_code), patch.dict(os.environ, ENV, clear=True), tempfile.TemporaryDirectory() as directory:
                model = RetryModel([response([{"type": "text", "text": "酒店未预订。"}]), error,
                                    response([{"type": "text", "text": "保留原输入继续。"}])])
                async with serving(directory, model) as client:
                    first = await submit(client, OLD)
                    saved = await submit(client, "继续", first["session"]["session_id"])
                    self.assertEqual(saved["turns"][-1]["answer"], "保留原输入继续。")
                    self.assertEqual(len([request for request in saved["requests"] if request.get("purpose") == "context_summary"]), 1)

    async def test_changed_dates_survive_stopped_summary_before_any_travel_request(self):
        from travel_dates import saved_date_context
        entered, release = asyncio.Event(), asyncio.Event()
        class WaitingModel(ModelService):
            async def create(self, **kwargs):
                if "整理旅行状态" in kwargs["system"]:
                    entered.set()
                    await release.wait()
                return await super().create(**kwargs)
        model = WaitingModel([response([{"type": "text", "text": "酒店未预订。"}]), summary_response()])
        with patch.dict(os.environ, ENV, clear=True), tempfile.TemporaryDirectory() as directory:
            async with serving(directory, model) as client:
                first = await submit(client, OLD)
                accepted = (await client.post("/api/turns", json={"input": "旅行日期改到2026-10-19", "session_id": first["session"]["session_id"]})).json()
                await asyncio.wait_for(entered.wait(), 2)
                await client.post("/api/turns/" + accepted["turn_id"] + "/stop")
                release.set()
                saved = await wait_finished(client, accepted["session_id"], accepted["turn_id"])
                self.assertEqual(saved["turns"][-1]["travel_date_context"]["travel_dates"]["start_date"], "2026-10-19")
            restarted = ModelService([summary_response(), response([{"type": "text", "text": "已保留改期。"}])])
            async with serving(directory, restarted) as client:
                saved = await submit(client, "继续规划", first["session"]["session_id"])
                self.assertEqual(saved["turns"][-1]["answer"], "已保留改期。")
                self.assertEqual(saved_date_context(restarted.requests[-1]["system"])["travel_dates"]["start_date"], "2026-10-19")

    async def test_cli_keeps_summary_within_process_and_prints_short_status(self):
        import io
        from contextlib import asynccontextmanager, redirect_stdout
        from agent import run_cli
        from test_agent import MapService
        model = ModelService([response([{"type": "text", "text": "酒店未预订。"}]), summary_response(),
                              response([{"type": "text", "text": "继续保留酒店。"}]), response([{"type": "text", "text": "继续规划。"}])])
        @asynccontextmanager
        async def external_model(*args, **kwargs):
            yield model
        @asynccontextmanager
        async def external_maps(*args, **kwargs):
            yield MapService()
        output = io.StringIO()
        with patch.dict(os.environ, {**ENV, "MODEL_ID": "test-model"}, clear=True), patch("agent.Client", side_effect=external_maps), patch("travel_tools.Client", side_effect=RuntimeError("资料服务不可用")), patch("agent.AsyncAnthropic", side_effect=external_model), patch("agent.streamable_http_client", return_value=None), patch("builtins.input", side_effect=[OLD, "继续", "再继续", "exit"]), redirect_stdout(output):
            self.assertEqual(await run_cli("test-key"), 0)
        self.assertIn("摘要已用于本轮", output.getvalue())
        self.assertNotIn('"travel_conditions"', output.getvalue())
        self.assertIn("travel_state_summary", str(model.requests[-1]["messages"]))

    async def test_finish_failure_does_not_promote_unsaved_version(self):
        import sqlite3
        from contextlib import closing
        from pathlib import Path
        from test_storage import fail_event, wait_state
        model = ModelService([response([{"type": "text", "text": "酒店未预订。"}]), summary_response(),
                              response([{"type": "text", "text": "继续保留酒店。"}]), summary_response(),
                              response([{"type": "text", "text": "本轮回答尚未保存。"}])])
        with patch.dict(os.environ, ENV, clear=True), tempfile.TemporaryDirectory() as directory:
            async with serving(directory, model) as client:
                first = await submit(client, OLD)
                good = await submit(client, "继续", first["session"]["session_id"])
                fail_event(directory, "context.saved")
                accepted = (await client.post("/api/turns", json={"input": "仅本轮未保存要求" + "中" * 3800, "session_id": first["session"]["session_id"]})).json()
                await wait_state(client, lambda state: not state["accepting"] and state["active_turn_id"] is None)
                failed = (await client.get("/api/sessions/" + accepted["session_id"])).json()
                self.assertEqual(failed["turns"][-1]["status"], "running")
                self.assertIsNone(failed["turns"][-1]["answer"])
                self.assertFalse(any(event["kind"] in ("context.saved", "turn.finished") and event["turn_id"] == accepted["turn_id"] for event in failed["events"]))
            with closing(sqlite3.connect(Path(directory) / "travel.sqlite3")) as database, database:
                database.execute("DROP TRIGGER fail_event")
            restarted = ModelService([response([{"type": "text", "text": "恢复上一已保存酒店要求。"}])])
            async with serving(directory, restarted) as client:
                saved = await submit(client, "继续规划", first["session"]["session_id"])
                self.assertEqual(saved["turns"][-1]["answer"], "恢复上一已保存酒店要求。")
                self.assertIn("travel_state_summary", str(restarted.requests[-1]["messages"]))
                self.assertNotIn("仅本轮未保存要求", str(restarted.requests[-1]["messages"]))
                self.assertEqual(saved["turns"][1]["messages"], good["turns"][1]["messages"])

    async def test_summary_transport_failure_has_only_two_attempts(self):
        from test_model_retry import RetryModel, connection_error
        model = RetryModel([response([{"type": "text", "text": "酒店未预订。"}]), connection_error(),
                            connection_error(), response([{"type": "text", "text": "保留原输入继续。"}])])
        with patch.dict(os.environ, ENV, clear=True), tempfile.TemporaryDirectory() as directory:
            async with serving(directory, model) as client:
                first = await submit(client, OLD)
                saved = await submit(client, "继续", first["session"]["session_id"])
                self.assertEqual(saved["turns"][-1]["answer"], "保留原输入继续。")
                summaries = [request for request in saved["requests"] if request.get("purpose") == "context_summary"]
                self.assertEqual([request["status"] for request in summaries], ["failed", "failed"])
                self.assertEqual(len(model.requests), 4)

    async def test_terminal_summary_error_is_not_retried_on_later_travel_steps(self):
        from test_model_retry import RetryModel, status_error
        query = response([{"type": "tool_use", "id": "first", "name": "maps_text_search", "input": {"keywords": "西湖", "city": "杭州"}}], "tool_use")
        model = RetryModel([response([{"type": "text", "text": "酒店未预订。"}]), status_error(402), query,
                            response([{"type": "text", "text": "保留原要求，查询完成。"}])])
        with patch.dict(os.environ, ENV, clear=True), tempfile.TemporaryDirectory() as directory:
            async with serving(directory, model) as client:
                first = await submit(client, OLD)
                saved = await submit(client, "继续查询", first["session"]["session_id"])
                self.assertEqual(saved["turns"][-1]["answer"], "保留原要求，查询完成。")
                self.assertEqual(len([request for request in saved["requests"] if request.get("purpose") == "context_summary"]), 1)
