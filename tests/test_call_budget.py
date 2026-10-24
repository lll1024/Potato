"""通过公开 Web 入口验证整轮实际调用预算与最终收束。"""
import asyncio
from contextlib import asynccontextmanager, redirect_stdout
import io
import os
import tempfile
import unittest
from unittest.mock import patch

from service_fixtures import cli_retrieval, MapService, ModelService, response
from test_model_retry import RetryModel, status_error
from test_context_summary import OLD, summary_response, submit
from test_source_failures import SourceService, call, finished
from test_storage import fail_event, wait_state
from test_trace import serving, wait_finished


def history_query(index):
    return response([{"type": "tool_use", "id": f"history-{index}",
        "name": "session_history_search", "input": {"query": "西湖"}}], "tool_use")


class CallBudgetTests(unittest.IsolatedAsyncioTestCase):
    async def test_final_start_storage_failure_preserves_saved_material_without_dispatch_or_replay(self):
        entered, release = asyncio.Event(), asyncio.Event()
        class WaitingBoundary(ModelService):
            async def create(self, **parameters):
                if len(self.requests) == 62:
                    entered.set()
                    await release.wait()
                return await super().create(**parameters)
        model = WaitingBoundary([response([call("map", "maps_text_search", {"keywords": "西湖", "city": "杭州"})], "tool_use"),
            *[history_query(i) for i in range(62)]])
        with patch.dict(os.environ, {"CONTEXT_COMPACTION_ENABLED": "false"}, clear=True), tempfile.TemporaryDirectory() as directory:
            async with serving(directory, model) as client:
                accepted = (await client.post("/api/turns", json={"input": "规划西湖旅行"})).json()
                await asyncio.wait_for(entered.wait(), 10)
                try:
                    fail_event(directory, "request.started")
                finally:
                    release.set()
                state = await wait_state(client, lambda s: s["active_turn_id"] is None)
                self.assertFalse(state["accepting"])
                self.assertEqual(state["unsaved_fact"]["kind"], "request.started")
                saved = (await client.get("/api/sessions/" + accepted["session_id"])).json()
                self.assertEqual(len(model.requests), 63)
                self.assertEqual(saved["turns"][-1]["status"], "running")
                self.assertEqual(saved["tool_calls"][0]["status"], "completed")
                self.assertTrue(saved["turns"][-1]["query_materials"])
            restarted = ModelService([])
            async with serving(directory, restarted) as client:
                restored = (await client.get("/api/sessions/" + accepted["session_id"])).json()
                self.assertEqual(restored["turns"][-1]["reason"], "service_interrupted")
                self.assertEqual(restored["tool_calls"][0], saved["tool_calls"][0])
                self.assertEqual(restarted.requests, [])

    async def test_summary_at_request_63_cannot_take_final_reserve_or_recover(self):
        for summary in (summary_response(), response([{"type": "text", "text": "{}"}])):
            with self.subTest(valid=summary.content[0].text != "{}"), patch.dict(os.environ, {"CONTEXT_COMPACTION_ENABLED": "true", "MODEL_CONTEXT_WINDOW": "200000"}, clear=True), tempfile.TemporaryDirectory() as directory:
                model = RetryModel([response([{"type": "text", "text": "酒店未预订。"}]),
                    *[history_query(i) for i in range(61)],
                    status_error(400, {"error": {"code": "context_window_exceeded"}}), summary,
                    response([{"type": "text", "text": "只整理已有资料。"}])])
                async with serving(directory, model) as client:
                    first = await submit(client, OLD)
                    saved = await asyncio.wait_for(submit(client, "继续规划", first["session"]["session_id"]), 15)
                    actual = [r for r in saved["requests"] if r["turn_id"] == saved["turns"][-1]["turn_id"]]
                    self.assertEqual(len(actual), 64)
                    self.assertEqual([r["purpose"] for r in actual[-3:]], ["travel", "context_summary", "travel_final"])
                    self.assertFalse(any(e["kind"] == "context.recovery_started" for e in saved["events"]))
                    self.assertEqual(len([r for r in actual if r["purpose"] == "context_summary"]), 1)
                    self.assertEqual(saved["turns"][-1]["reason"], "budget")

    async def test_final_complete_input_is_checked_without_summary_or_recovery(self):
        last = history_query(62)
        last.usage.input_tokens = 2_000_000
        model = ModelService([*[history_query(i) for i in range(62)], last])
        with patch.dict(os.environ, {"CONTEXT_COMPACTION_ENABLED": "true", "MODEL_CONTEXT_WINDOW": "200000"}, clear=True), tempfile.TemporaryDirectory() as directory:
            async with serving(directory, model) as client:
                saved = await asyncio.wait_for(submit(client, "规划西湖旅行"), 15)
                self.assertEqual(len(model.requests), 63)
                self.assertEqual(saved["turns"][-1]["reason"], "context_budget")
                self.assertEqual(saved["turns"][-1]["answer_source"], "application")
                self.assertFalse(any(r["purpose"] == "context_summary" for r in saved["requests"]))
                checked = [e for e in saved["events"] if e["kind"] == "context.checked"][-1]["data"]
                self.assertFalse(checked["allowed"])
                self.assertIn("尚未完成", saved["turns"][-1]["answer"])

    async def test_cli_uses_final_reserve_and_next_turn_resets(self):
        from travel_app.planning import run_cli
        model = ModelService([*[history_query(i) for i in range(63)],
            response([{"type": "text", "text": "仅整理已有资料。"}]),
            response([{"type": "text", "text": "新轮次回答。"}])])
        @asynccontextmanager
        async def external_model(*args, **kwargs):
            self.assertEqual(kwargs["max_retries"], 0)
            yield model
        @asynccontextmanager
        async def external_maps(*args, **kwargs):
            yield MapService()
        output = io.StringIO()
        with patch.dict(os.environ, {'MODEL_ID': 'test-model', 'CONTEXT_COMPACTION_ENABLED': 'false'}, clear=True), patch('builtins.input', side_effect=['规划西湖旅行', '继续', 'exit']), redirect_stdout(output):
            self.assertEqual(await run_cli("test-key", retrieval_factory=cli_retrieval(external_maps, RuntimeError('资料服务不可用')), model_client_factory=external_model), 0)
        self.assertEqual(len(model.requests), 65)
        self.assertNotIn("tools", model.requests[63])
        self.assertIn("tools", model.requests[64])
        self.assertIn("尚未完成", output.getvalue())
        self.assertIn("仅整理已有资料", output.getvalue())
        self.assertIn("新轮次回答", output.getvalue())

    async def test_128_tools_share_map_web_history_budget_and_failed_calls_count(self):
        blocks = [call("map", "maps_text_search", {"keywords": "西湖", "city": "杭州"})]
        blocks += [history_query(i).content[0].model_dump() for i in range(9)]
        blocks += [call(f"web-{i}", "web_search_exa", {"query": "西湖玩法"}) for i in range(120)]
        service = SourceService(["No results found", *[{"results": [{"url": "https://guide.example.test", "content": "玩法资料"}]} for _ in range(118)]])
        model = ModelService([response(blocks, "tool_use"), response([{"type": "text", "text": "地点与玩法已取得，其余资料缺失。"}])])
        with patch.dict(os.environ, {"CONTEXT_COMPACTION_ENABLED": "false"}, clear=True), tempfile.TemporaryDirectory() as directory:
            async with service.serving(directory, model, interval=0) as client:
                accepted = (await client.post("/api/turns", json={"input": "规划西湖旅行"})).json()
                saved = await finished(client, accepted)
                self.assertEqual(len(service.map_calls), 1)
                self.assertEqual(len(service.source_calls), 119)
                self.assertEqual(len(saved["tool_calls"]), 130)
                self.assertEqual([t["status"] for t in saved["tool_calls"][1:10]], ["completed"] * 8 + ["not_executed"])
                self.assertEqual(saved["tool_calls"][10]["status"], "failed")
                self.assertEqual(saved["tool_calls"][-1]["reason"], "budget")
                self.assertEqual(saved["requests"][-1]["purpose"], "travel_final")
                results = model.requests[-1]["messages"][-1]["content"]
                self.assertEqual([r["tool_use_id"] for r in results], [b["id"] for b in blocks])
                self.assertEqual(saved["turns"][-1]["tool_error_count"], 1)
                self.assertEqual(saved["turns"][-1]["reason"], "budget")

    async def test_final_failure_invalid_or_truncated_ends_without_retry(self):
        for final in (status_error(503), response([]), response([{"type": "text", "text": "   "}]),
                      response([{"type": "text", "text": "部分回答"}], "max_tokens"), history_query("invalid")):
            with self.subTest(final=type(final).__name__), patch.dict(os.environ, {"CONTEXT_COMPACTION_ENABLED": "false"}, clear=True), tempfile.TemporaryDirectory() as directory:
                model = RetryModel([*[history_query(i) for i in range(63)], final])
                async with serving(directory, model) as client:
                    saved = await asyncio.wait_for(submit(client, "规划西湖旅行"), 10)
                    self.assertEqual(len(model.requests), 64)
                    self.assertEqual(saved["turns"][-1]["status"], "terminated")
                    self.assertEqual(saved["turns"][-1]["answer_source"], "application")
                    self.assertEqual(saved["requests"][-1]["status"], "failed")
                    self.assertFalse(any(e["kind"] == "request.retry_scheduled" for e in saved["events"]))
                    self.assertEqual(len(saved["tool_calls"]), 63)
                    self.assertIn("尚未完成", saved["turns"][-1]["answer"])

    async def test_stop_inflight_final_request_keeps_slot_and_failure_facts(self):
        entered, release = asyncio.Event(), asyncio.Event()
        class WaitingFinal(RetryModel):
            async def create(self, **parameters):
                if "tools" not in parameters:
                    entered.set()
                    await release.wait()
                return await super().create(**parameters)
        model = WaitingFinal([*[history_query(i) for i in range(63)], status_error(503)])
        with patch.dict(os.environ, {"CONTEXT_COMPACTION_ENABLED": "false"}, clear=True), tempfile.TemporaryDirectory() as directory:
            async with serving(directory, model) as client:
                accepted = (await client.post("/api/turns", json={"input": "规划西湖旅行"})).json()
                await asyncio.wait_for(entered.wait(), 10)
                try:
                    await client.post("/api/turns/" + accepted["turn_id"] + "/stop")
                    self.assertEqual((await client.get("/api/state")).json()["active_turn_id"], accepted["turn_id"])
                    self.assertEqual((await client.post("/api/turns", json={"input": "新请求"})).status_code, 409)
                finally:
                    release.set()
                saved = await asyncio.wait_for(wait_finished(client, accepted["session_id"]), 3)
                self.assertEqual(saved["turns"][-1]["reason"], "user_stop")
                self.assertEqual(saved["requests"][-1]["status"], "failed")
                self.assertEqual(saved["requests"][-1]["purpose"], "travel_final")
                self.assertEqual(len(model.requests), 64)
                self.assertIsNone((await client.get("/api/state")).json()["active_turn_id"])

    async def test_retries_and_recovery_summary_share_actual_requests_and_reserve_final(self):
        # 前一轮建立真实历史；本轮一次失败、一次摘要和一次恢复占前三次额度。
        error = lambda: status_error(503, headers={"Retry-After": "0.001"})
        model = RetryModel([response([{"type": "text", "text": "酒店未预订。"}]),
            status_error(400, {"error": {"code": "context_window_exceeded"}}), summary_response(),
            history_query(0),
            *[item for i in range(10) for item in [*[error() for _ in range(5)], history_query(i + 1)]],
            response([{"type": "text", "text": "仅整理已有资料，交通缺失。"}])])
        with patch.dict(os.environ, {"CONTEXT_COMPACTION_ENABLED": "true", "MODEL_CONTEXT_WINDOW": "200000"}, clear=True), tempfile.TemporaryDirectory() as directory:
            async with serving(directory, model) as client:
                first = await submit(client, OLD)
                saved = await asyncio.wait_for(submit(client, "继续规划", first["session"]["session_id"]), 15)
                turn = saved["turns"][-1]
                actual = [r for r in saved["requests"] if r["turn_id"] == turn["turn_id"]]
                self.assertEqual(len(actual), 64)
                self.assertEqual([r["ordinal"] for r in actual], list(range(1, 65)))
                self.assertEqual([r["purpose"] for r in actual[:3]], ["travel", "context_summary", "travel"])
                self.assertEqual([r["step"] for r in [actual[0], actual[2]]], [1, 1])
                self.assertEqual(len([r for r in actual if r["status"] == "failed"]), 51)
                self.assertEqual(actual[-1]["purpose"], "travel_final")
                self.assertEqual(saved["tool_calls"][-1]["reason"], "budget")
                self.assertEqual(turn["reason"], "budget")
                self.assertIn("交通缺失", turn["answer"])
                self.assertNotIn("tools", model.requests[-1])
                self.assertIn("travel_state_summary", str(model.requests[-1]["messages"]))

    async def test_model_request_63_does_not_dispatch_and_64_summarizes_then_restart_resets(self):
        model = ModelService([*[history_query(i) for i in range(63)],
            response([{"type": "text", "text": "已取得资料，后续交通尚未查询。"}])])
        with patch.dict(os.environ, {"CONTEXT_COMPACTION_ENABLED": "false"}, clear=True), tempfile.TemporaryDirectory() as directory:
            async with serving(directory, model) as client:
                accepted = (await client.post("/api/turns", json={"input": "规划西湖旅行"})).json()
                saved = await asyncio.wait_for(wait_finished(client, accepted["session_id"]), 10)
                self.assertEqual(len(model.requests), 64)
                self.assertEqual(saved["requests"][-1]["purpose"], "travel_final")
                self.assertNotIn("tools", model.requests[-1])
                self.assertEqual(saved["tool_calls"][-1]["status"], "not_executed")
                self.assertEqual(saved["tool_calls"][-1]["reason"], "budget")
                self.assertEqual(saved["tool_calls"][-1]["tool_use_id"], "history-62")
                result = model.requests[-1]["messages"][-1]["content"][0]
                self.assertEqual(result["tool_use_id"], "history-62")
                self.assertTrue(result["is_error"])
                turn = saved["turns"][-1]
                self.assertEqual((turn["status"], turn["reason"], turn["answer_source"]), ("terminated", "budget", "model"))
                self.assertIn("后续交通尚未查询", turn["answer"])
                self.assertIn("尚未完成", turn["answer"])
            restarted = ModelService([response([{"type": "text", "text": "新轮次回答"}])])
            async with serving(directory, restarted) as client:
                restored = (await client.get("/api/sessions/" + accepted["session_id"])).json()
                self.assertEqual(restored["turns"][-1]["answer"], turn["answer"])
                self.assertEqual(restarted.requests, [])
                next_turn = (await client.post("/api/turns", json={"input": "继续", "session_id": accepted["session_id"]})).json()
                continued = await wait_finished(client, accepted["session_id"], next_turn["turn_id"])
                self.assertEqual(continued["requests"][-1]["ordinal"], 1)
                self.assertEqual(continued["turns"][-1]["status"], "completed")
                self.assertEqual(len(restarted.requests), 1)
