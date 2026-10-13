"""通过 HTTP/SSE 验证工具结果整理和完整原文保留。"""
import json
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from mcp.types import CallToolResult, TextContent

from test_agent import MapService, ModelService, response
from test_trace import serving, wait_finished


class RepeatedMap(MapService):
    body_repeats = 90

    async def call_tool(self, name, arguments):
        result = await super().call_tool(name, arguments)
        result.structured_content = {"pois": [
            {"id": "hotel-east", "name": "湖畔酒店", "address": "东门", "price": "早餐 80 元，仅工作日",
             "source": "酒店公告", "valid_date": "2026-10-13", "verification": "待核实",
             "body": "价格仅适用于成人；儿童需另询。" * self.body_repeats},
            {"id": "hotel-west", "name": "湖畔酒店", "address": "西门", "price": "早餐 100 元，仅周末"},
        ]}
        result.content[0].text = json.dumps(result.structured_content, ensure_ascii=False)
        result.content.append(result.content[0])
        return result


def query_response():
    return response([{"type": "tool_use", "id": "query-place", "name": "maps_text_search",
                      "input": {"keywords": "西湖", "city": "杭州"}}], "tool_use")


class ContextOrganizationTests(unittest.IsolatedAsyncioTestCase):
    async def test_pressure_removes_duplicate_views_and_preserves_complete_original(self):
        class PressureMap(RepeatedMap):
            # 保留完整价格正文，并给新增固定系统规则留下整理后余量。
            body_repeats = 80

        model = ModelService([query_response(), response([{"type": "text", "text": "两家同名酒店需要澄清，早餐价格条件待核实。"}])])
        with patch.dict(os.environ, {"CONTEXT_COMPACTION_ENABLED": "true", "MODEL_CONTEXT_WINDOW": "40000"}, clear=True), patch("test_trace.MapService", PressureMap), tempfile.TemporaryDirectory() as directory:
            async with serving(directory, model) as client:
                accepted = (await client.post("/api/turns", json={"input": "查询杭州西湖附近酒店"})).json()
                saved = await wait_finished(client, accepted["session_id"])
                self.assertEqual(saved["turns"][0]["status"], "completed")
                self.assertEqual(len(model.requests), 2)
                viewed = json.loads(model.requests[1]["messages"][-1]["content"][0]["content"])
                self.assertNotIn("text", viewed)
                self.assertEqual([poi["id"] for poi in viewed["data"]["pois"]], ["hotel-east", "hotel-west"])
                self.assertEqual(viewed["data"]["pois"][0]["body"], "价格仅适用于成人；儿童需另询。" * 80)
                self.assertEqual(viewed["data"]["pois"][0]["price"], "早餐 80 元，仅工作日")
                self.assertEqual(viewed["data"]["pois"][0]["verification"], "待核实")
                result_id = saved["tool_calls"][0]["result_payload_id"]
                original = (await client.get("/api/payloads/" + result_id)).json()["content"]
                raw = json.loads(original["content"])
                self.assertEqual(len(raw["text"]), 2)
                self.assertEqual(json.loads(raw["text"][0]), viewed["data"])
                organized = next(event["data"] for event in saved["events"] if event["kind"] == "context.organized")
                self.assertGreaterEqual(organized["before_tokens"], 22400)
                self.assertLess(organized["after_tokens"], 22400)
                self.assertEqual(organized["changed_result_count"], 1)
                self.assertFalse(organized["pressure_remaining"])

    async def test_low_pressure_keeps_duplicate_tool_result_unchanged(self):
        model = ModelService([query_response(), response([{"type": "text", "text": "早餐仍待核实。"}])])
        with patch.dict(os.environ, {"CONTEXT_COMPACTION_ENABLED": "true", "MODEL_CONTEXT_WINDOW": "1000000"}, clear=True), patch("test_trace.MapService", RepeatedMap), tempfile.TemporaryDirectory() as directory:
            async with serving(directory, model) as client:
                accepted = (await client.post("/api/turns", json={"input": "查杭州西湖附近酒店"})).json()
                saved = await wait_finished(client, accepted["session_id"])
                viewed = json.loads(model.requests[1]["messages"][-1]["content"][0]["content"])
                self.assertEqual(len(viewed["text"]), 2)
                self.assertFalse(any(event["kind"] == "context.organized" for event in saved["events"]))

    async def test_source_body_duplicates_use_references_without_losing_conditions_or_failures(self):
        body = "成人门票80元；儿童价格待核实。" * 100 + "仅适用2026-10-13，入口页面未读取，尚未预订。"
        raw_text = "# 官方公告\nURL: https://museum.example.test/visit\n\n" + body + "\n\n---\n\nError fetching https://museum.example.test/missing: 正文无法读取"
        class Sources:
            @property
            def session(self):
                return self

            async def list_tools(self, *, cursor=None):
                return SimpleNamespace(tools=[SimpleNamespace(name=name, description="官方资料", input_schema={"type": "object"})
                    for name in ("web_search_exa", "web_fetch_exa")], next_cursor=None)

            async def call_tool(self, name, arguments):
                return CallToolResult(content=[TextContent(text=raw_text)], is_error=False)

        model = ModelService([response([{"type": "tool_use", "id": "source", "name": "web_fetch_exa", "input": {
            "urls": ["https://museum.example.test/visit", "https://museum.example.test/missing"]}}], "tool_use"),
            response([{"type": "text", "text": "只查到公告，缺失页面待核实，尚未预订。"}])])
        with patch.dict(os.environ, {"CONTEXT_COMPACTION_ENABLED": "true", "MODEL_CONTEXT_WINDOW": "40000"}, clear=True), tempfile.TemporaryDirectory() as directory:
            async with serving(directory, model, sources=Sources()) as client:
                accepted = (await client.post("/api/turns", json={"input": "查博物馆门票与预订入口"})).json()
                saved = await wait_finished(client, accepted["session_id"])
                self.assertEqual(saved["turns"][0]["status"], "completed")
                viewed_raw = model.requests[1]["messages"][-1]["content"][0]["content"]
                viewed = json.loads(viewed_raw)
                self.assertEqual(viewed_raw.count(body), 1)
                self.assertEqual(viewed["sources"][0]["content"], body)
                self.assertEqual(viewed["sources"][0]["content_type"], "page_body")
                self.assertIn("sources[0].content", viewed["text"][0])
                self.assertIn("正文无法读取", viewed["text"][0])
                self.assertEqual(viewed["failed_sources"][0]["url"], "https://museum.example.test/missing")
                self.assertIn("条件不完整", viewed["content_boundary"])
                self.assertTrue(viewed["retrieved_at"])
                result_id = saved["tool_calls"][0]["result_payload_id"]
                original = (await client.get("/api/payloads/" + result_id)).json()["content"]
                self.assertEqual(json.loads(original["content"])["text"], [raw_text])

    async def test_failure_warning_and_different_json_value_types_are_preserved(self):
        class FailedMap(RepeatedMap):
            async def call_tool(self, name, arguments):
                result = await super().call_tool(name, arguments)
                result.structured_content["warning"] = "价格条件不完整，失败结果不得作为核实事实"
                result.structured_content["available"] = 1
                different = {**result.structured_content, "available": True}
                result.content = [SimpleNamespace(type="text", text=json.dumps(different, ensure_ascii=False))]
                result.is_error = True
                return result

        model = ModelService([query_response(), response([{"type": "text", "text": "查询失败，价格条件待核实。"}])])
        with patch.dict(os.environ, {"CONTEXT_COMPACTION_ENABLED": "true", "MODEL_CONTEXT_WINDOW": "40000"}, clear=True), tempfile.TemporaryDirectory() as directory:
            async with serving(directory, model, maps=FailedMap()) as client:
                accepted = (await client.post("/api/turns", json={"input": "查杭州西湖酒店"})).json()
                saved = await wait_finished(client, accepted["session_id"])
                viewed_block = model.requests[1]["messages"][-1]["content"][0]
                viewed = json.loads(viewed_block["content"])
                self.assertTrue(viewed_block["is_error"])
                self.assertIs(json.loads(viewed["text"][0])["available"], True)
                self.assertEqual(viewed["data"]["available"], 1)
                self.assertIn("失败结果不得作为核实事实", viewed["data"]["warning"])
                self.assertTrue(any(event["kind"] == "context.organized" for event in saved["events"]))
