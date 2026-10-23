"""公开 HTTP/SSE 验证单轮增量压缩；测试显式使用小容量。"""
import copy
import json
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from test_agent import MapService, ModelService, response
from test_context_summary import ENV, submit
from test_trace import serving


class LargeMaps(MapService):
    async def call_tool(self, name, arguments):
        return SimpleNamespace(content=[SimpleNamespace(type="text", text="来源2026-10-18，价格待核实、未预订。" + "资" * 1600)],
                               structured_content=None, is_error=False)


class IncrementalModel(ModelService):
    def __init__(self, groups=5):
        super().__init__([])
        self.groups = groups
        self.travel_count = 0

    async def create(self, **kwargs):
        self.requests.append(copy.deepcopy(kwargs))
        if "整理旅行状态" in kwargs["system"]:
            source = json.loads(kwargs["messages"][0]["content"])
            refs = [r["record_id"] for r in source["records"] if r["reference_readable"]]
            refs += [r["record_id"] for r in (source["previous_summary"] or {}).get("sources", [])]
            return response([{"type": "text", "text": json.dumps({
                "travel_conditions": {"budget": "3000元", "hotel": "西湖酒店"},
                "confirmed_itinerary": [], "corrections": ["预算5000元改为3000元"],
                "unresolved": ["早餐待核实，酒店未预订"],
                "sources": [{"record_id": ref, "verification": "待核实"} for ref in refs]
            }, ensure_ascii=False)}])
        self.travel_count += 1
        if self.travel_count <= self.groups:
            return response([{"type": "tool_use", "id": f"group{self.travel_count}-{i}",
                              "name": "maps_text_search", "input": {"keywords": "西湖", "city": "杭州"}}
                             for i in range(2)], "tool_use")
        return response([{"type": "text", "text": "保持3000元和西湖酒店，资料待核实。"}])


class IncrementalContextTests(unittest.IsolatedAsyncioTestCase):
    async def test_single_turn_repeated_compaction_keeps_current_input_and_latest_multitool_group(self):
        model = IncrementalModel()
        text = "2026-10-18 杭州旅行，预算3000元，保留西湖酒店，请查询多个地点。"
        with patch.dict(os.environ, ENV, clear=True), tempfile.TemporaryDirectory() as directory:
            async with serving(directory, model, maps=LargeMaps()) as client:
                saved = await submit(client, text)
                self.assertEqual(saved["turns"][-1]["answer"], "保持3000元和西湖酒店，资料待核实。")
                summaries = [r for r in saved["requests"] if r["purpose"] == "context_summary"]
                self.assertGreaterEqual(len(summaries), 2)
                self.assertLessEqual(len(summaries), 4)
                self.assertEqual(len(saved["tool_calls"]), 10)
                self.assertEqual(len(saved["turns"][-1]["messages"]), 12)
                travels = [r for r in model.requests if "整理旅行状态" not in r["system"]]
                for step, request in enumerate(travels):
                    messages = request["messages"]
                    self.assertIn({"role": "user", "content": text}, messages)
                    uses = {b["id"] for m in messages if isinstance(m["content"], list)
                            for b in m["content"] if b["type"] == "tool_use"}
                    results = {b["tool_use_id"] for m in messages if isinstance(m["content"], list)
                               for b in m["content"] if b["type"] == "tool_result"}
                    self.assertEqual(uses, results)
                    if step:
                        self.assertTrue({f"group{step}-0", f"group{step}-1"}.issubset(uses))
                self.assertIn("travel_state_summary", str(travels[-1]["messages"]))
                sources = json.loads(next(m["content"] for m in travels[-1]["messages"]
                                         if isinstance(m["content"], str) and "<travel_state_summary>" in m["content"])
                                     .removeprefix("<travel_state_summary>").removesuffix("</travel_state_summary>"))
                for ref in sources["original_records"]:
                    self.assertTrue(ref.startswith("message:"))

    async def test_oversized_history_batches_merge_correction_and_keep_deterministic_dates(self):
        from travel_app.context.view import TokenMeter
        from travel_app.travel_dates import saved_date_context
        model = IncrementalModel(groups=0)
        with patch.dict(os.environ, {"CONTEXT_COMPACTION_ENABLED": "false"}, clear=True), tempfile.TemporaryDirectory() as directory:
            async with serving(directory, model) as client:
                session = None
                originals = ["2026-10-18 杭州旅行，预算5000元，指定西湖酒店。",
                             "预算改为3000元，酒店继续保留。", "继续核对早餐价格。", "暂不预订，请核对资料。"]
                for text in originals:
                    saved = await submit(client, text + "史" * 4900, session)
                    session = saved["session"]["session_id"]
                with patch.dict(os.environ, ENV, clear=True):
                    saved = await submit(client, "继续安排交通", session)
                turn_id = saved["turns"][-1]["turn_id"]
                actual = [r for r in saved["requests"] if r["turn_id"] == turn_id]
                self.assertEqual([r["purpose"] for r in actual], ["context_summary", "context_summary", "travel"])
                summaries = [r for r in model.requests if "整理旅行状态" in r["system"]]
                self.assertEqual(len(summaries), 2)
                sources = [json.loads(r["messages"][0]["content"]) for r in summaries]
                self.assertIsNone(sources[0]["previous_summary"])
                self.assertEqual(sources[1]["previous_summary"]["travel_conditions"]["budget"], "3000元")
                for source in sources:
                    self.assertEqual(source["travel_dates"]["start_date"], "2026-10-18")
                    self.assertNotIn("preferences", source)
                refs = [r["record_id"] for source in sources for r in source["records"]]
                self.assertEqual(refs, [f"message:{i}" for i in range(1, 9)])
                self.assertTrue(any(originals[1] in str(source["records"]) for source in sources))
                meter = TokenMeter("test-model")
                for request in model.requests[-3:]:
                    self.assertLessEqual(meter.measure(request)["estimated_tokens"], 28000)
                self.assertEqual(saved_date_context(model.requests[-1]["system"])["travel_dates"]["start_date"], "2026-10-18")
                self.assertEqual(saved["turns"][-1]["messages"][0]["content"], originals[0] + "史" * 4900)
                adopted = [event for event in saved["events"] if event["turn_id"] == turn_id and event["kind"] == "context.adopted"]
                self.assertEqual(len(adopted), 2)
                self.assertEqual((await client.get("/api/preferences")).json()["preferences"], [])

    async def test_multiple_summary_versions_keep_original_tool_results_readable(self):
        class ReadableMaps(LargeMaps):
            async def call_tool(self, name, arguments):
                result = await super().call_tool(name, arguments)
                # 仍触发多版本摘要，给回读元数据与正文页留出完整请求余量。
                result.content[0].text = "来源2026-10-18，价格待核实、未预订。" + "资" * 1400
                return result

        class ReadModel(IncrementalModel):
            async def create(self, **kwargs):
                if "整理旅行状态" not in kwargs["system"] and self.travel_count == self.groups:
                    self.travel_count += 1
                    self.requests.append(copy.deepcopy(kwargs))
                    state = json.loads(next(m["content"] for m in kwargs["messages"]
                                            if isinstance(m["content"], str) and "<travel_state_summary>" in m["content"])
                                       .removeprefix("<travel_state_summary>").removesuffix("</travel_state_summary>"))
                    assert "message:3" in state["original_records"]
                    return response([{"type": "tool_use", "id": "read-old", "name": "session_history_read",
                                      "input": {"record_id": "message:3", "limit": 500}}], "tool_use")
                return await super().create(**kwargs)
        model = ReadModel()
        with patch.dict(os.environ, ENV, clear=True), tempfile.TemporaryDirectory() as directory:
            async with serving(directory, model, maps=ReadableMaps()) as client:
                saved = await submit(client, "2026-10-18，预算3000元，保留西湖酒店，核对旧来源。")
                self.assertEqual(saved["turns"][-1]["answer"], "保持3000元和西湖酒店，资料待核实。")
                reads = [call for call in saved["tool_calls"] if call["name"] == "session_history_read"]
                self.assertEqual(len(reads), 1)
                payload = (await client.get("/api/payloads/" + reads[0]["result_payload_id"])).json()["content"]
                self.assertFalse(payload["is_error"])
                self.assertIn("来源2026-10-18", payload["content"])
                self.assertIn("historical_requires_recheck", payload["content"])
                page = json.loads(payload["content"])
                self.assertGreater(page["end_offset"], 0)
                self.assertEqual(page["next_offset"], page["end_offset"])
                original = saved["turns"][-1]["messages"][2]["content"]
                self.assertEqual(len(original), 2)
                for block in original:
                    self.assertEqual(json.loads(block["content"])["text"],
                                     ["来源2026-10-18，价格待核实、未预订。" + "资" * 1400])
                versions = [event["data"]["version"] for event in saved["events"]
                            if event["kind"] == "context.adopted"]
                self.assertGreaterEqual(len(set(versions)), 2)
                self.assertGreaterEqual(len([r for r in saved["requests"] if r["purpose"] == "context_summary"]), 2)

    async def test_indivisible_latest_multitool_group_ends_without_summary_or_next_travel_call(self):
        class HugeMaps(LargeMaps):
            async def call_tool(self, name, arguments):
                result = await super().call_tool(name, arguments)
                result.content[0].text += "巨" * 10000
                return result
        model = IncrementalModel(groups=1)
        text = "2026-10-18 杭州旅行，预算3000元，保留西湖酒店。"
        with patch.dict(os.environ, ENV, clear=True), tempfile.TemporaryDirectory() as directory:
            async with serving(directory, model, maps=HugeMaps()) as client:
                saved = await submit(client, text)
                self.assertEqual(saved["turns"][-1]["reason"], "context_budget")
                self.assertEqual(len(model.requests), 1)
                self.assertEqual(len(saved["tool_calls"]), 2)
                self.assertEqual(saved["turns"][-1]["messages"][0]["content"], text)
                self.assertEqual(len(saved["turns"][-1]["messages"][2]["content"]), 2)

    async def test_second_batch_failure_keeps_first_version_and_shares_two_request_limit(self):
        class FailedBatchModel(IncrementalModel):
            async def create(self, **kwargs):
                if "整理旅行状态" in kwargs["system"] and any(
                        "整理旅行状态" in request["system"] for request in self.requests):
                    self.requests.append(copy.deepcopy(kwargs))
                    return response([{"type": "text", "text": "{}"}])
                return await super().create(**kwargs)
        model = FailedBatchModel(groups=0)
        with patch.dict(os.environ, {"CONTEXT_COMPACTION_ENABLED": "false"}, clear=True), tempfile.TemporaryDirectory() as directory:
            async with serving(directory, model) as client:
                session = None
                for index in range(4):
                    saved = await submit(client, f"2026-10-18 酒店原始记录{index}，预算3000元。" + "史" * 4900, session)
                    session = saved["session"]["session_id"]
                with patch.dict(os.environ, ENV, clear=True):
                    saved = await submit(client, "继续，仍保留西湖酒店", session)
                self.assertEqual(saved["turns"][-1]["reason"], "context_budget")
                current = [r for r in saved["requests"] if r["turn_id"] == saved["turns"][-1]["turn_id"]]
                self.assertEqual([r["purpose"] for r in current], ["context_summary", "context_summary"])
                adopted = [e for e in saved["events"] if e["kind"] == "context.adopted"]
                self.assertEqual(len(adopted), 1)
                self.assertTrue(any(e["kind"] == "context.saved" for e in saved["events"]))
                self.assertIn("酒店原始记录3", str(saved["turns"][-1]["messages"]))
                self.assertEqual(json.loads(model.requests[-1]["messages"][0]["content"])
                                 ["previous_summary"]["travel_conditions"]["budget"], "3000元")

    async def test_growing_tool_history_exhausts_shared_four_summary_calls_and_preserves_results(self):
        model = IncrementalModel(groups=6)
        with patch.dict(os.environ, ENV, clear=True), tempfile.TemporaryDirectory() as directory:
            async with serving(directory, model, maps=LargeMaps()) as client:
                saved = await submit(client, "2026-10-18，预算3000元，指定西湖酒店，持续查询地点。")
                self.assertEqual(saved["turns"][-1]["reason"], "context_budget")
                self.assertEqual(len([r for r in saved["requests"] if r["purpose"] == "context_summary"]), 4)
                self.assertEqual(len([r for r in saved["requests"] if r["purpose"] == "travel"]), 6)
                self.assertEqual(len(saved["tool_calls"]), 12)
                self.assertEqual(len(saved["turns"][-1]["messages"]), 14)
