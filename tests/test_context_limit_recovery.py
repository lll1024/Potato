"""公开 HTTP/SSE 的超限恢复；小压力预算仅用于测试，不代表服务实际窗口。"""
import os
import tempfile
import unittest
from unittest.mock import patch

from service_fixtures import cli_retrieval, response
from context_fixtures import submit, summary_response
from model_fixtures import RetryModel, status_error
from http_fixtures import serving


ENV = {"CONTEXT_COMPACTION_ENABLED": "true", "MODEL_CONTEXT_WINDOW": "100000"}
OLD = "2026-10-18 杭州旅行，预算3000元，指定西湖酒店。" + "史" * 3900

def exceeded():
    return status_error(400, {"error": {"code": "context_window_exceeded"}})

class ContextRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_underestimated_input_is_shrunk_before_one_same_step_recovery(self):
        model = RetryModel([response([{"type": "text", "text": "酒店未预订。"}]), exceeded(),
                            summary_response(), response([{"type": "text", "text": "已继续安排交通。"}])])
        with patch.dict(os.environ, ENV, clear=True), tempfile.TemporaryDirectory() as directory:
            async with serving(directory, model) as client:
                first = await submit(client, OLD)
                saved = await submit(client, "继续安排交通", first["session"]["session_id"])
                self.assertEqual(saved["turns"][-1]["answer"], "已继续安排交通。")
                actual = [r for r in saved["requests"] if r["turn_id"] == saved["turns"][-1]["turn_id"]]
                self.assertEqual([r["purpose"] for r in actual], ["travel", "context_summary", "travel"])
                self.assertEqual([r["step"] for r in actual if r["purpose"] == "travel"], [1, 1])
                self.assertEqual(len({r["request_id"] for r in actual}), 3)
                self.assertEqual(actual[0]["usage_state"], "not_returned")
                self.assertEqual(model.requests[1]["system"], model.requests[3]["system"])
                self.assertNotIn(OLD, str(model.requests[3]["messages"]))
                self.assertIn({"role": "user", "content": "继续安排交通"}, model.requests[3]["messages"])
                self.assertEqual(saved["turns"][-1]["messages"][0]["content"], OLD)
                events = [e for e in saved["events"] if e["turn_id"] == saved["turns"][-1]["turn_id"]]
                self.assertEqual([e["kind"] for e in events if e["kind"].startswith("context.recovery")],
                                 ["context.recovery_prepared", "context.recovery_started", "context.recovery_result"])
                self.assertFalse(any(e["kind"].startswith("request.retry_") for e in events))
                checked = next(e for e in events if e["kind"] == "context.checked")
                self.assertLess(checked["data"]["estimated_tokens"], 65600)

    async def test_no_history_cannot_shrink_and_repeated_exceeded_is_terminal(self):
        for history in (False, True):
            with self.subTest(history=history), patch.dict(os.environ, ENV, clear=True), tempfile.TemporaryDirectory() as directory:
                replies = ([response([{"type": "text", "text": "酒店未预订。"}])] if history else [])
                replies += [exceeded()] + ([summary_response(), exceeded()] if history else [])
                model = RetryModel(replies)
                async with serving(directory, model) as client:
                    first = await submit(client, OLD) if history else None
                    saved = await submit(client, "继续安排交通", first["session"]["session_id"] if first else None)
                    self.assertEqual(saved["turns"][-1]["status"], "failed")
                    self.assertEqual(saved["turns"][-1]["reason"], "context_window_exceeded" if history else "context_recovery_unavailable")
                    self.assertEqual(len(model.requests), 4 if history else 1)
                    self.assertFalse(any(e["kind"].startswith("request.retry_") for e in saved["events"]))

    async def test_recovery_network_failures_keep_all_five_normal_retries(self):
        fast_error = lambda: status_error(503, headers={"Retry-After": "0.001"})
        model = RetryModel([response([{"type": "text", "text": "酒店未预订。"}]),
                            fast_error(), exceeded(), summary_response(),
                            *[fast_error() for _ in range(4)], response([{"type": "text", "text": "成功回答。"}])])
        with patch.dict(os.environ, ENV, clear=True), tempfile.TemporaryDirectory() as directory:
            async with serving(directory, model) as client:
                first = await submit(client, OLD)
                saved = await submit(client, "继续安排交通", first["session"]["session_id"])
                self.assertEqual(saved["turns"][-1]["answer"], "成功回答。")
                events = [e for e in saved["events"] if e["turn_id"] == saved["turns"][-1]["turn_id"]]
                self.assertEqual([e["data"]["retry"] for e in events if e["kind"] == "request.retry_scheduled"], [1, 2, 3, 4, 5])
                self.assertEqual(len([e for e in events if e["kind"] == "context.recovery_started"]), 1)
                travels = [r for r in saved["requests"] if r["turn_id"] == saved["turns"][-1]["turn_id"] and r["purpose"] == "travel"]
                self.assertEqual([r["step"] for r in travels], [1] * 7)
                self.assertEqual([r["attempt"] for r in travels], list(range(1, 8)))

    async def test_recovery_does_not_repeat_completed_map_queries(self):
        from context_fixtures import LargeMaps
        class CountingMaps(LargeMaps):
            def __init__(self):
                self.calls = 0
            async def call_tool(self, name, arguments):
                self.calls += 1
                return await super().call_tool(name, arguments)
        query = lambda ident: response([{"type": "tool_use", "id": ident, "name": "maps_text_search",
                                        "input": {"keywords": "西湖", "city": "杭州"}}], "tool_use")
        model = RetryModel([query("first"), query("latest"), exceeded(), summary_response(sources=[
            {"record_id": "message:3", "verification": "待核实"}]), response([{"type": "text", "text": "保留已取得资料。"}])])
        maps = CountingMaps()
        with patch.dict(os.environ, ENV, clear=True), tempfile.TemporaryDirectory() as directory:
            async with serving(directory, model, maps=maps) as client:
                saved = await submit(client, "2026-10-18 预算3000元，指定西湖酒店，查询两项资料。")
                self.assertEqual(saved["turns"][-1]["answer"], "保留已取得资料。")
                self.assertEqual(maps.calls, 2)
                self.assertEqual(len(saved["tool_calls"]), 2)
                self.assertEqual([r["step"] for r in saved["requests"] if r["purpose"] == "travel"], [1, 2, 3, 3])
                self.assertEqual(model.requests[2]["system"], model.requests[4]["system"])
                self.assertIn("latest", str(model.requests[4]["messages"]))
                self.assertNotIn('"first"', str(model.requests[4]["messages"]))

    async def test_stop_inflight_recovery_summary_preserves_real_usage_and_skips_retry(self):
        import asyncio
        from http_fixtures import wait_finished
        entered, release = asyncio.Event(), asyncio.Event()
        class WaitingModel(RetryModel):
            async def create(self, **kwargs):
                if "整理旅行状态" in kwargs["system"]:
                    entered.set()
                    await release.wait()
                return await super().create(**kwargs)
        model = WaitingModel([response([{"type": "text", "text": "酒店未预订。"}]), exceeded(), summary_response()])
        with patch.dict(os.environ, ENV, clear=True), tempfile.TemporaryDirectory() as directory:
            async with serving(directory, model) as client:
                first = await submit(client, OLD)
                accepted = (await client.post("/api/turns", json={"input": "继续安排交通", "session_id": first["session"]["session_id"]})).json()
                await asyncio.wait_for(entered.wait(), 2)
                try:
                    await client.post("/api/turns/" + accepted["turn_id"] + "/stop")
                finally:
                    release.set()
                saved = await wait_finished(client, accepted["session_id"], accepted["turn_id"])
                self.assertEqual(saved["turns"][-1]["reason"], "user_stop")
                self.assertEqual(saved["requests"][-1]["status"], "completed")
                self.assertIsNotNone(saved["requests"][-1]["usage"])
                self.assertEqual(len(model.requests), 3)
                self.assertFalse(any(e["kind"] in ("context.adopted", "context.recovery_started") for e in saved["events"]))

    async def test_non_context_errors_never_start_compaction_recovery(self):
        for error in (status_error(401), status_error(402), status_error(413), RuntimeError("unknown")):
            with self.subTest(error=type(error).__name__), patch.dict(os.environ, ENV, clear=True), tempfile.TemporaryDirectory() as directory:
                model = RetryModel([response([{"type": "text", "text": "酒店未预订。"}]), error])
                async with serving(directory, model) as client:
                    first = await submit(client, OLD)
                    saved = await submit(client, "继续", first["session"]["session_id"])
                    self.assertEqual(saved["turns"][-1]["reason"], "model_error")
                    self.assertEqual(len(model.requests), 2)
                    self.assertFalse(any(e["kind"].startswith("context.recovery") or e["kind"] == "context.summary_started" for e in saved["events"]))

    async def test_pressure_and_recovery_share_four_summary_calls_and_auth_blocks_whole_turn(self):
        from context_fixtures import ENV as SMALL_ENV, OLD as PRESSURE_OLD
        invalid = response([{"type": "text", "text": "{}"}])
        for before, recovery, summaries in [([invalid, invalid], [invalid, invalid], 4),
                                            ([status_error(401)], [], 1),
                                            ([status_error(402)], [], 1)]:
            with self.subTest(summaries=summaries), patch.dict(os.environ, SMALL_ENV, clear=True), tempfile.TemporaryDirectory() as directory:
                model = RetryModel([response([{"type": "text", "text": "酒店未预订。"}]), *before, exceeded(), *recovery])
                async with serving(directory, model) as client:
                    first = await submit(client, PRESSURE_OLD)
                    saved = await submit(client, "继续安排交通", first["session"]["session_id"])
                    self.assertEqual(saved["turns"][-1]["reason"], "context_recovery_unavailable")
                    current = [r for r in saved["requests"] if r["turn_id"] == saved["turns"][-1]["turn_id"]]
                    self.assertEqual(len([r for r in current if r["purpose"] == "context_summary"]), summaries)
                    self.assertEqual(len([r for r in current if r["purpose"] == "travel"]), 1)

    async def test_deterministic_tool_organization_can_recover_without_a_summary_request(self):
        import json
        from types import SimpleNamespace
        from service_fixtures import MapService
        class DuplicatedMaps(MapService):
            async def call_tool(self, name, arguments):
                data = {"id": "west-lake", "details": "查" * 1000, "verification": "待核实"}
                return SimpleNamespace(content=[SimpleNamespace(type="text", text=json.dumps(data, ensure_ascii=False))],
                                       structured_content=data, is_error=False)
        query = response([{"type": "tool_use", "id": "lake", "name": "maps_text_search",
                           "input": {"keywords": "西湖", "city": "杭州"}}], "tool_use")
        model = RetryModel([query, exceeded(), response([{"type": "text", "text": "整理后继续回答。"}])])
        with patch.dict(os.environ, ENV, clear=True), tempfile.TemporaryDirectory() as directory:
            async with serving(directory, model, maps=DuplicatedMaps()) as client:
                saved = await submit(client, "2026-10-18，查杭州西湖资料")
                self.assertEqual(saved["turns"][-1]["answer"], "整理后继续回答。")
                self.assertEqual([r["purpose"] for r in saved["requests"]], ["travel", "travel", "travel"])
                self.assertLess(len(str(model.requests[2]["messages"])), len(str(model.requests[1]["messages"])))
                old_request = saved["requests"][1]["request_id"]
                new_request = saved["requests"][2]["request_id"]
                started = next(e for e in saved["events"] if e["kind"] == "context.recovery_started")
                self.assertEqual(started["data"]["recovery_of"], old_request)
                self.assertEqual(started["data"]["request_id"], new_request)

    async def test_cli_distinguishes_exceeded_prepared_started_and_returned_without_summary_body(self):
        import io
        from contextlib import asynccontextmanager, redirect_stdout
        from travel_app.planning import run_cli
        from service_fixtures import MapService
        model = RetryModel([response([{"type": "text", "text": "酒店未预订。"}]), exceeded(), summary_response(),
                            response([{"type": "text", "text": "继续安排交通。"}])])
        @asynccontextmanager
        async def external_model(*args, **kwargs):
            yield model
        @asynccontextmanager
        async def external_maps(*args, **kwargs):
            yield MapService()
        output = io.StringIO()
        with patch.dict(os.environ, {**ENV, 'MODEL_ID': 'test-model'}, clear=True), patch('builtins.input', side_effect=[OLD, '继续安排交通', 'exit']), redirect_stdout(output):
            self.assertEqual(await run_cli("test-key", retrieval_factory=cli_retrieval(external_maps, RuntimeError('资料服务不可用')), model_client_factory=external_model), 0)
        self.assertIn("服务报告上下文超限", output.getvalue())
        self.assertIn("已缩小且符合预算", output.getvalue())
        self.assertIn("正在恢复原模型步骤", output.getvalue())
        self.assertIn("原模型步骤已返回有效响应", output.getvalue())
        self.assertNotIn('"travel_conditions"', output.getvalue())
