"""通过模型上下文公开入口验证协议、模型输入与冻结保存材料。"""
import json
from types import SimpleNamespace
import unittest

from travel_app.context import ContextPolicy, ModelContext, PlanningSeed, RecordAbort


class ContextContractsTests(unittest.TestCase):
    def test_response_and_tool_results_share_one_protocol_and_continuation(self):
        context = ModelContext.begin("保留西湖酒店")
        context.append_response([{"type": "tool_use", "id": "place", "name": "maps_text_search", "input": {}}])
        results = [{"type": "tool_result", "tool_use_id": "place", "content": "早餐68元；待核实", "is_error": False}]
        context.append_tool_results(results)
        material = context.material()
        results[0]["content"] = "调用方的迟到修改"
        saved = json.loads(material.messages_json)
        self.assertEqual(saved[-1]["content"][0]["content"], "早餐68元；待核实")
        self.assertEqual(json.loads(material.context_checkpoint.state_json)["messages"], saved)
        context.append_answer("完成")
        continued = ModelContext.begin("继续比较", continuation=context.continuation())
        self.assertEqual(json.loads(continued.material().messages_json), saved + [
            {"role": "assistant", "content": "完成"}, {"role": "user", "content": "继续比较"}])
        self.assertEqual(json.loads(material.messages_json), saved)

    def test_mismatched_tool_results_do_not_modify_either_saved_view(self):
        context = ModelContext.begin("查询杭州")
        context.append_response([{"type": "tool_use", "id": "place", "name": "maps_text_search", "input": {}}])
        before = context.material()
        with self.assertRaises(ValueError):
            context.append_tool_results([{"type": "tool_result", "tool_use_id": "another-call", "content": "不能错配"}])
        self.assertEqual(context.material(), before)

    def test_corrupt_saved_context_aborts_at_the_recovery_boundary(self):
        with self.assertRaises(RecordAbort) as raised:
            ModelContext.begin("继续", seed=PlanningSeed(context_json='{"version": 1}'))
        self.assertEqual(raised.exception.kind, "turn.context")

    def test_corrupt_protocol_aborts_instead_of_leaking_a_runtime_error(self):
        for seed in (PlanningSeed(messages_json="{}"), PlanningSeed(context_json=json.dumps({
                "messages": {}, "original_watermark": 0, "record_ids": []}))):
            with self.subTest(seed=seed):
                with self.assertRaises(RecordAbort) as raised:
                    ModelContext.begin("继续", seed=seed)
                self.assertEqual(raised.exception.kind, "turn.context")


class ContextRequestTests(unittest.IsolatedAsyncioTestCase):
    def configure(self, context, *, policy=None, dates=None):
        context.configure(policy=policy, model="test-model", client=None,
            tools=SimpleNamespace(redact=lambda value: value), date_context=dates,
            next_request_ordinal=lambda: 1, remaining_requests=lambda: 0,
            observer=None, stop_requested=None)

    async def test_request_copy_cannot_change_protocol_or_continued_dates(self):
        dates = {"now": "2026-12-31T23:50:00+08:00", "travel_dates": {
            "status": "resolved", "start_date": "2027-01-01", "end_date": "2027-01-01"}}
        context = ModelContext.begin("明天去杭州")
        self.configure(context, dates=dates)
        parameters = {"model": "test-model", "system": "旅行规则", "max_tokens": 8000}
        await context.prepare_request(parameters, step=1, attempt=1)
        parameters["messages"][0]["content"] = "外部修改"
        dates["travel_dates"]["start_date"] = "错误日期"
        continued = ModelContext.begin("继续", continuation=context.continuation())
        self.assertEqual(continued.previous_date_context["travel_dates"]["start_date"], "2027-01-01")
        self.assertEqual(json.loads(continued.material().messages_json)[0]["content"], "明天去杭州")

    async def test_history_reads_completed_batch_prefix_without_duplicating_protocol(self):
        context = ModelContext.begin("查询酒店")
        context.append_response([
            {"type": "tool_use", "id": "place", "name": "maps_text_search", "input": {}},
            {"type": "tool_use", "id": "read", "name": "session_history_read", "input": {}},
        ])
        self.configure(context)
        completed = [{"type": "tool_result", "tool_use_id": "place", "content": "酒店早餐68元，待核实", "is_error": False}]
        result = await context.read_history("session_history_read", {"record_id": "message:3:block:0"},
            parameters={}, completed=completed, pending=[], tool_use_id="read")
        self.assertEqual(json.loads(result["content"])["text"], "酒店早餐68元，待核实")
        context.append_tool_results([*completed, {"type": "tool_result", "tool_use_id": "read", **result}])
        self.assertEqual(len(json.loads(context.material().messages_json)), 3)

    async def test_over_limit_recovery_requires_smaller_input_and_is_once_per_step(self):
        context = ModelContext.begin("查酒店")
        context.append_response([{"type": "tool_use", "id": "place", "name": "maps_text_search", "input": {}}])
        data = {"酒店": "早餐价格待核实" * 200}
        context.append_tool_results([{"type": "tool_result", "tool_use_id": "place",
            "content": json.dumps({"data": data, "text": [json.dumps(data, ensure_ascii=False)]}, ensure_ascii=False)}])
        self.configure(context, policy=ContextPolicy(1000000))
        parameters = {"model": "test-model", "system": "旅行规则", "max_tokens": 8000}
        await context.prepare_request(parameters, step=2, attempt=1)
        original = context.material().messages_json
        before = len(json.dumps(parameters["messages"]))
        self.assertIsNone(await context.prepare_recovery(parameters, request_id="first", step=2, attempt=1))
        self.assertLess(len(json.dumps(parameters["messages"])), before)
        self.assertEqual(await context.prepare_recovery(parameters, request_id="second", step=2, attempt=2), "context_window_exceeded")
        self.assertEqual(context.material().messages_json, original)
