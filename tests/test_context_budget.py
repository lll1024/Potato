"""从 HTTP/SSE 与真实查询循环验证上下文容量。"""
import json
import os
import tempfile
import unittest
from unittest.mock import patch

from travel_app.planning import plan
from travel_app.local_records import PlanningSeed
from service_fixtures import ModelService, response
from http_fixtures import serving, wait_finished


class ContextBudgetTests(unittest.IsolatedAsyncioTestCase):
    async def test_enabled_missing_capacity_ends_without_sending_and_keeps_input(self):
        model = ModelService([])
        with patch.dict(os.environ, {"CONTEXT_COMPACTION_ENABLED": "true"}, clear=True), tempfile.TemporaryDirectory() as directory:
            async with serving(directory, model) as client:
                accepted = (await client.post("/api/turns", json={"input": "杭州明天旅行"})).json()
                saved = await wait_finished(client, accepted["session_id"])
                self.assertEqual(saved["turns"][0]["reason"], "context_configuration")
                self.assertIn("MODEL_CONTEXT_WINDOW", saved["turns"][0]["answer"])
                self.assertEqual(saved["turns"][0]["input"], "杭州明天旅行")
                self.assertEqual(model.requests, [])

    async def test_oversized_chinese_json_is_not_sent_or_truncated(self):
        import json
        query = json.dumps({"要求": "中文酒店早餐预算" * 3000}, ensure_ascii=False)
        model = ModelService([])
        with patch.dict(os.environ, {"CONTEXT_COMPACTION_ENABLED": "true", "MODEL_CONTEXT_WINDOW": "40000"}), tempfile.TemporaryDirectory() as directory:
            async with serving(directory, model) as client:
                accepted = (await client.post("/api/turns", json={"input": query})).json()
                saved = await wait_finished(client, accepted["session_id"])
                self.assertEqual(saved["turns"][0]["reason"], "context_budget")
                self.assertEqual(saved["turns"][0]["input"], query)
                self.assertEqual(model.requests, [])
                check = next(event["data"] for event in saved["events"] if event["kind"] == "context.checked")
                self.assertEqual(check["input_budget"], 28000)
                self.assertGreater(check["estimated_tokens"], 28000)
                self.assertTrue(check["approximate"])

    async def test_low_pressure_preserves_protocol_and_missing_usage_is_unknown(self):
        first = response([{"type": "tool_use", "id": "place", "name": "maps_text_search",
                          "input": {"keywords": "西湖", "city": "杭州"}}], "tool_use")
        last = response([{"type": "text", "text": "西湖位于杭州。"}])
        last.__pydantic_fields_set__.discard("usage")
        model = ModelService([first, last])
        with patch.dict(os.environ, {"CONTEXT_COMPACTION_ENABLED": "true", "MODEL_CONTEXT_WINDOW": "40000"}, clear=True), tempfile.TemporaryDirectory() as directory:
            async with serving(directory, model) as client:
                accepted = (await client.post("/api/turns", json={"input": "杭州明天看西湖"})).json()
                saved = await wait_finished(client, accepted["session_id"])
                self.assertEqual(saved["turns"][0]["answer"], "西湖位于杭州。")
                self.assertEqual(len(model.requests), 2)
                self.assertEqual(model.requests[1]["messages"][1]["content"][0]["id"], "place")
                self.assertEqual(model.requests[1]["messages"][2]["content"][0]["tool_use_id"], "place")
                self.assertEqual(model.requests[0]["system"], model.requests[1]["system"])
                self.assertIsNone(saved["requests"][1]["usage"])
                self.assertEqual(saved["usage_summary"]["input_tokens"]["known_count"], 1)
                checks = [event["data"] for event in saved["events"] if event["kind"] == "context.checked"]
                self.assertEqual([item["original_watermark"] for item in checks], [1, 3])
                self.assertEqual(checks[1]["record_ids"], ["message:1", "message:2", "message:3"])

    async def test_budget_edge_covers_full_system_and_chinese_input(self):
        for length, expected in [(4300, "completed"), (4950, "terminated")]:
            model = ModelService([response([{"type": "text", "text": "已收到完整要求"}])])
            query = "中" * length
            with self.subTest(length=length), patch.dict(os.environ, {"CONTEXT_COMPACTION_ENABLED": "true", "MODEL_CONTEXT_WINDOW": "40000"}, clear=True), tempfile.TemporaryDirectory() as directory:
                async with serving(directory, model) as client:
                    accepted = (await client.post("/api/turns", json={"input": query})).json()
                    saved = await wait_finished(client, accepted["session_id"])
                    self.assertEqual(saved["turns"][0]["status"], expected)
                    if model.requests:
                        self.assertEqual(model.requests[0]["messages"][0]["content"], query)
                        self.assertEqual(model.requests[0]["max_tokens"], 8000)
                        self.assertTrue(model.requests[0]["tools"])

    async def test_usage_calibration_counts_official_cache_but_not_ambiguous_compatibility_cache(self):
        for compatible in (False, True):
            first = response([{"type": "tool_use", "id": "place", "name": "maps_text_search",
                              "input": {"keywords": "西湖", "city": "杭州"}}], "tool_use")
            first.usage = type(first.usage).model_validate({"input_tokens": 19000, "output_tokens": 10,
                "cache_creation_input_tokens": 20000, "cache_read_input_tokens": 30000})
            model = ModelService([first, response([{"type": "text", "text": "西湖"}])])
            env = {"CONTEXT_COMPACTION_ENABLED": "true", "MODEL_CONTEXT_WINDOW": "40000"}
            if compatible:
                env["ANTHROPIC_BASE_URL"] = "https://compatible.example.test"
            with self.subTest(compatible=compatible), patch.dict(os.environ, env, clear=True), tempfile.TemporaryDirectory() as directory:
                async with serving(directory, model) as client:
                    accepted = (await client.post("/api/turns", json={"input": "查杭州西湖"})).json()
                    saved = await wait_finished(client, accepted["session_id"])
                    self.assertEqual(len(model.requests), 2 if compatible else 1)
                    self.assertEqual(saved["requests"][0]["usage"]["cache_read_input_tokens"], 30000)
                    calibration = next(event["data"] for event in saved["events"] if event["kind"] == "context.usage")
                    self.assertEqual(calibration["input_tokens_for_calibration"], 19000 if compatible else 69000)
                    if not compatible:
                        self.assertEqual(saved["turns"][0]["reason"], "context_budget")
                        result_id = saved["tool_calls"][0]["result_payload_id"]
                        payload = (await client.get("/api/payloads/" + result_id)).json()
                        self.assertIn("poi-west-lake", str(payload))

    async def test_model_view_replacement_does_not_change_original_history(self):
        from typing import cast
        from anthropic import AsyncAnthropic
        from mcp import Client
        from travel_app.planning import plan
        from travel_app.local_records import PlanningSeed
        import json
        from travel_app.retrieval import Retrieval
        from service_fixtures import MapService
        history = [{"role": "user", "content": "旧酒店细节原文"}, {"role": "assistant", "content": "旧回答"}]
        state = {"messages": [], "record_ids": [], "original_watermark": 2, "version": 1}
        tools = Retrieval(cast(Client, MapService()))
        await tools.discover()
        model = ModelService([response([{"type": "text", "text": "请补充城市。"}])])
        with patch.dict(os.environ, {"CONTEXT_COMPACTION_ENABLED": "true", "MODEL_CONTEXT_WINDOW": "40000"}):
            result = await plan("继续规划", cast(AsyncAnthropic, model), tools, "test-model",
                seed=PlanningSeed(messages_json=json.dumps(history), context_json=json.dumps(state)))
        saved = json.loads(result.material.messages_json)
        self.assertEqual(result.answer, "请补充城市。")
        self.assertEqual(saved[0]["content"], "旧酒店细节原文")
        self.assertEqual(len(saved), 4)
        self.assertEqual(len(model.requests[0]["messages"]), 1)

    async def test_invalid_or_nonpositive_capacity_has_the_same_diagnosis(self):
        for capacity in ("0", "-1", "abc", "10048", "1.5", "２００００"):
            model = ModelService([])
            with self.subTest(capacity=capacity), patch.dict(os.environ, {"CONTEXT_COMPACTION_ENABLED": "true", "MODEL_CONTEXT_WINDOW": capacity}, clear=True), tempfile.TemporaryDirectory() as directory:
                async with serving(directory, model) as client:
                    accepted = (await client.post("/api/turns", json={"input": "杭州旅行"})).json()
                    saved = await wait_finished(client, accepted["session_id"])
                    self.assertEqual(saved["turns"][0]["reason"], "context_configuration")
                    self.assertEqual(model.requests, [])

    async def test_large_tool_result_stops_before_next_request_and_keeps_complete_pair(self):
        from typing import cast
        from anthropic import AsyncAnthropic
        from mcp import Client

        from travel_app.retrieval import Retrieval
        from travel_app.context import ContextPolicy
        from service_fixtures import MapService
        class LargeMap(MapService):
            async def call_tool(self, name, arguments):
                result = await super().call_tool(name, arguments)
                result.structured_content["pois"][0]["details"] = "酒店早餐价格待核实" * 3000
                return result
        tools = Retrieval(cast(Client, LargeMap()))
        await tools.discover()
        model = ModelService([response([{"type": "tool_use", "id": "place", "name": "maps_text_search",
                          "input": {"keywords": "西湖", "city": "杭州"}}], "tool_use")])
        history = [{"role": "user", "content": "查杭州西湖"}]
        planning_result = await plan(
            history[-1]["content"], cast(AsyncAnthropic, model), tools, "test-model",
            seed=PlanningSeed(messages_json=json.dumps(history[:-1], ensure_ascii=False)),
            context_policy=ContextPolicy(40000),
        )
        answer = planning_result.answer
        history[:] = json.loads(planning_result.material.messages_json)
        self.assertEqual(len(model.requests), 1)
        self.assertIn("安全预算", answer)
        self.assertEqual(history[1]["content"][0]["id"], "place")
        self.assertEqual(history[2]["content"][0]["tool_use_id"], "place")
        self.assertIn("酒店早餐价格待核实" * 3000, history[2]["content"][0]["content"])

    async def test_web_startup_configuration_error_is_actionable(self):
        from travel_app.sessions import create_app
        with patch.dict(os.environ, {"CONTEXT_COMPACTION_ENABLED": "true"}, clear=True), tempfile.TemporaryDirectory() as directory:
            app = create_app(directory)
            with self.assertRaisesRegex(RuntimeError, "MODEL_CONTEXT_WINDOW"):
                async with app.router.lifespan_context(app):
                    self.fail("不应启动")

    async def test_preferences_tool_declarations_and_saved_materials_are_in_budget(self):
        from typing import cast
        from anthropic import AsyncAnthropic
        from mcp import Client

        from travel_app.retrieval import Retrieval
        from travel_app.context import ContextPolicy
        from service_fixtures import MapService
        huge = "中文查询资料价格条件" * 3000
        for component in ("preferences", "tools", "materials"):
            class DeclaredMap(MapService):
                async def list_tools(self, **kwargs):
                    result = await super().list_tools(**kwargs)
                    if component == "tools":
                        result.tools[0].description += huge
                    return result
            tools = Retrieval(cast(Client, DeclaredMap()))
            await tools.discover()
            model = ModelService([])
            kwargs = {"preference_background": huge} if component == "preferences" else {"previous_source_materials": [{"content": huge}]} if component == "materials" else {}
            planning_result = await plan(
                '杭州旅行', cast(AsyncAnthropic, model), tools, "test-model",
                seed=PlanningSeed(source_materials_json=json.dumps(kwargs.pop("previous_source_materials", []), ensure_ascii=False)),
                context_policy=ContextPolicy(40000),
                **kwargs,
            )
            answer = planning_result.answer
            with self.subTest(component=component):
                self.assertIn("安全预算", answer)
                self.assertEqual(model.requests, [])

    async def test_actual_retry_is_checked_again_after_returned_usage_calibrates_upwards(self):
        from typing import cast
        from unittest.mock import AsyncMock
        from anthropic import AsyncAnthropic
        from mcp import Client

        from travel_app.retrieval import Retrieval
        from travel_app.context import ContextPolicy
        from service_fixtures import MapService
        from model_fixtures import RetryModel
        first = response([])
        first.usage = type(first.usage).model_validate({"input_tokens": 60000, "output_tokens": 1})
        model = RetryModel([first, response([{"type": "text", "text": "不应发送"}])])
        tools = Retrieval(cast(Client, MapService()))
        await tools.discover()
        with patch("asyncio.sleep", new_callable=AsyncMock):
            planning_result = await plan(
                '杭州旅行', cast(AsyncAnthropic, model), tools, "test-model",
                seed=PlanningSeed(messages_json=json.dumps([], ensure_ascii=False)),
                context_policy=ContextPolicy(40000),
            )
            answer = planning_result.answer
        self.assertIn("安全预算", answer)
        self.assertEqual(len(model.requests), 1)
