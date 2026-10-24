"""通过 HTTP/SSE 与 CLI 验证当前助手会话回读。"""
import asyncio
import json
import tempfile
import unittest

from service_fixtures import cli_retrieval, ModelService, response
from test_trace import serving, wait_finished


def call(name, arguments, identifier="history"):
    return response([{"type": "tool_use", "id": identifier, "name": name, "input": arguments}], "tool_use")


class SessionHistoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_saved_hotel_detail_can_be_searched_and_read_after_restart(self):
        first = ModelService([response([{"type": "text", "text": "第二家酒店早餐 68 元，适用日期 2026-10-15，尚未预订。"}])])
        with tempfile.TemporaryDirectory() as directory:
            async with serving(directory, first) as client:
                accepted = (await client.post("/api/turns", json={"input": "查酒店"})).json()
                await wait_finished(client, accepted["session_id"])
            model = ModelService([call("session_history_search", {"query": "早餐 68"}), response([{"type": "text", "text": "历史早餐价为 68 元，需核对当前适用条件。"}])])
            # 第二轮的第一个请求在该模型中是索引 0。
            async def retrieve(**parameters):
                if len(model.requests) == 1:
                    data = json.loads(parameters["messages"][-1]["content"][0]["content"])
                    model.replies = iter([call("session_history_read", {"record_id": data["matches"][0]["record_id"]}), response([{ "type": "text", "text": "历史早餐价为 68 元，需核对当前适用条件。"}])])
                return await ModelService.create(model, **parameters)
            model.create = retrieve
            async with serving(directory, model) as client:
                turn = (await client.post("/api/turns", json={"input": "之前第二家早餐多少钱", "session_id": accepted["session_id"]})).json()
                snapshot = await asyncio.wait_for(wait_finished(client, turn["session_id"], turn["turn_id"]), 3)
                search = json.loads(model.requests[1]["messages"][-1]["content"][0]["content"])
                read = json.loads(model.requests[2]["messages"][-1]["content"][0]["content"])
                self.assertEqual(len(search["matches"]), 1)
                self.assertIn("早餐 68 元", read["text"])
                self.assertEqual(read["turn_ordinal"], 1)
                self.assertEqual(read["verification_status"], "historical_requires_recheck")
                self.assertIsNone(read["next_offset"])
                tools = snapshot["tool_calls"]
                self.assertEqual([tool["status"] for tool in tools], ["completed", "completed"])
                self.assertTrue(all(tool["call_started"] for tool in tools))

    async def test_pages_limits_errors_and_current_session_isolation(self):
        original = "酒店早餐 68 元，尚未预订。" + "原文。" * 3000
        model = ModelService([response([{"type": "text", "text": original}])])
        with tempfile.TemporaryDirectory() as directory:
            async with serving(directory, model) as client:
                first = (await client.post("/api/turns", json={"input": "查酒店"})).json()
                saved = await wait_finished(client, first["session_id"])
                response_id = saved["requests"][0]["response_payload_id"]
                input_id = saved["requests"][0]["input_payload_id"]
                model.replies = iter([call("session_history_search", {"query": "酒店"}),
                    call("session_history_read", {"record_id": "payload:" + response_id + ":text:0", "limit": 99999}),
                    call("session_history_read", {"record_id": "payload:" + response_id + ":text:0", "offset": 4000}),
                    call("session_history_read", {"record_id": "payload:" + input_id}),
                    response([{"type": "text", "text": "读取完毕"}])])
                second = (await client.post("/api/turns", json={"input": "回读", "session_id": first["session_id"]})).json()
                await wait_finished(client, second["session_id"], second["turn_id"])
                search = json.loads(model.requests[2]["messages"][-1]["content"][0]["content"])
                self.assertTrue(all(len(match["excerpt"]) <= 200 for match in search["matches"]))
                page = json.loads(model.requests[3]["messages"][-1]["content"][0]["content"])
                self.assertEqual(len(page["text"]), 4000)
                self.assertEqual(page["next_offset"], 4000)
                followup = json.loads(model.requests[4]["messages"][-1]["content"][0]["content"])
                self.assertEqual(followup["offset"], 4000)
                self.assertEqual(followup["text"], original[4000:8000])
                self.assertTrue(model.requests[5]["messages"][-1]["content"][0]["is_error"])
                model.replies = iter([call("session_history_read", {"record_id": "payload:" + response_id + ":text:0"}), response([{"type": "text", "text": "无法读另一会话"}])])
                other = (await client.post("/api/turns", json={"input": "新会话"})).json()
                other_saved = await wait_finished(client, other["session_id"])
                result = model.requests[-1]["messages"][-1]["content"][0]
                self.assertTrue(result["is_error"])
                self.assertIn("不属于当前", result["content"])
                self.assertEqual(other_saved["tool_calls"][0]["status"], "failed")

    async def test_eight_recall_calls_share_budget_and_record_unexecuted_proposals(self):
        model = ModelService([response([{"type": "tool_use", "id": f"recall-{index}", "name": "session_history_search", "input": {"query": "酒店"}} for index in range(10)], "tool_use"), response([{"type": "text", "text": "完成"}])])
        with tempfile.TemporaryDirectory() as directory:
            async with serving(directory, model) as client:
                accepted = (await client.post("/api/turns", json={"input": "酒店"})).json()
                saved = await wait_finished(client, accepted["session_id"])
                self.assertEqual([tool["status"] for tool in saved["tool_calls"]], ["completed"] * 8 + ["not_executed"] * 2)
                self.assertFalse(saved["tool_calls"][-1]["call_started"])
                self.assertEqual(saved["tool_calls"][-1]["reason"], "history_budget")

    async def test_cli_recalls_original_text_only_in_current_process(self):
        import io
        import os
        from contextlib import asynccontextmanager, redirect_stdout
        from unittest.mock import patch
        from travel_app.planning import run_cli
        from service_fixtures import MapService
        model = ModelService([response([{"type": "text", "text": "第二家酒店早餐 68 元，尚未预订。"}]),
            call("session_history_search", {"query": "早餐 68"}),
            call("session_history_read", {"record_id": "message:2"}),
            response([{"type": "text", "text": "旧报价 68 元，当前条件待核实。"}])])
        @asynccontextmanager
        async def external_model(*args, **kwargs):
            yield model
        @asynccontextmanager
        async def external_maps(*args, **kwargs):
            yield MapService()
        with patch('builtins.input', side_effect=['查酒店', '之前早餐多少钱', 'exit']), patch.dict(os.environ, {'MODEL_ID': 'test-model'}, clear=True), redirect_stdout(io.StringIO()) as output:
            self.assertEqual(await run_cli("test-key", retrieval_factory=cli_retrieval(external_maps, RuntimeError('资料服务不可用')), model_client_factory=external_model), 0)
        page = json.loads(model.requests[3]["messages"][-1]["content"][0]["content"])
        self.assertIn("早餐 68 元", page["text"])
        self.assertEqual(page["turn_ordinal"], 1)
        self.assertIn("旧报价 68 元", output.getvalue())

    async def test_read_page_shrinks_to_complete_request_budget(self):
        import os
        from unittest.mock import patch
        # W=40000 -> P=28000；保护本轮长输入，即使旧记录已摘要，4000 字回读仍放不下。
        original = "早餐 68 元。" + "中" * 3500
        model = ModelService([response([{"type": "text", "text": original}])])
        with tempfile.TemporaryDirectory() as directory:
            async with serving(directory, model) as client:
                first = (await client.post("/api/turns", json={"input": "查酒店"})).json()
                saved = await wait_finished(client, first["session_id"])
                identifier = "payload:" + saved["requests"][0]["response_payload_id"] + ":text:0"
                summary = response([{"type": "text", "text": json.dumps({
                    "travel_conditions": {"hotel_breakfast": "旧报价68元，未预订"},
                    "confirmed_itinerary": [], "corrections": [], "unresolved": ["当前价格待核实"],
                    "sources": [{"record_id": "message:1", "verification": "用户要求"},
                                {"record_id": "message:2", "verification": "旧报价待核实"}]
                }, ensure_ascii=False)}])
                model.replies = iter([summary, call("session_history_read", {"record_id": identifier}),
                                      response([{"type": "text", "text": "已读取有限历史"}])])
                with patch.dict(os.environ, {"CONTEXT_COMPACTION_ENABLED": "true", "MODEL_CONTEXT_WINDOW": "40000"}, clear=True):
                    second = (await client.post("/api/turns", json={"input": "回读" + "求" * 2000, "session_id": first["session_id"]})).json()
                    recalled = await wait_finished(client, second["session_id"], second["turn_id"])
                self.assertEqual(recalled["turns"][-1]["status"], "completed")
                page = json.loads(model.requests[-1]["messages"][-1]["content"][0]["content"])
                self.assertGreater(len(page["text"]), 0)
                self.assertLess(len(page["text"]), min(4000, len(original)))
                self.assertEqual(page["next_offset"], len(page["text"]))
                checks = [event["data"] for event in recalled["events"] if event["kind"] == "context.checked"]
                self.assertTrue(all(check["estimated_tokens"] <= 28000 for check in checks))

    async def test_search_caps_unique_originals_and_excludes_system_snapshots(self):
        model = ModelService([response([{"type": "text", "text": f"酒店早餐 {index} 元。" + "细节" * 200} for index in range(6)]),
            call("session_history_search", {"query": "酒店早餐"}),
            call("session_history_search", {"query": "你是旅行助手"}),
            response([{"type": "text", "text": "完成"}])])
        with tempfile.TemporaryDirectory() as directory:
            async with serving(directory, model) as client:
                first = (await client.post("/api/turns", json={"input": "查酒店"})).json()
                await wait_finished(client, first["session_id"])
                second = (await client.post("/api/turns", json={"input": "回读", "session_id": first["session_id"]})).json()
                await wait_finished(client, second["session_id"], second["turn_id"])
                matches = json.loads(model.requests[2]["messages"][-1]["content"][0]["content"])["matches"]
                self.assertEqual(len(matches), 5)
                self.assertEqual(len({match["record_id"] for match in matches}), 5)
                self.assertTrue(all(len(match["excerpt"]) == 200 for match in matches))
                self.assertEqual(json.loads(model.requests[3]["messages"][-1]["content"][0]["content"])["matches"], [])

    async def test_tool_result_can_be_read_by_full_protocol_reference(self):
        model = ModelService([call("maps_text_search", {"keywords": "西湖", "city": "杭州"}),
            response([{"type": "text", "text": "已取得旧地点资料"}]),
            call("session_history_search", {"query": "查到一个地点"}),
            call("session_history_read", {"record_id": "message:3"}),
            response([{"type": "text", "text": "旧资料仍须核对"}])])
        with tempfile.TemporaryDirectory() as directory:
            async with serving(directory, model) as client:
                first = (await client.post("/api/turns", json={"input": "查酒店"})).json()
                await wait_finished(client, first["session_id"])
                second = (await client.post("/api/turns", json={"input": "回读", "session_id": first["session_id"]})).json()
                await wait_finished(client, second["session_id"], second["turn_id"])
                matches = json.loads(model.requests[3]["messages"][-1]["content"][0]["content"])["matches"]
                page = json.loads(model.requests[4]["messages"][-1]["content"][0]["content"])
                self.assertEqual(len(matches), 1)
                self.assertEqual(page["kind"], "tool_result")
                self.assertEqual(page["source"], "maps_text_search")
                self.assertEqual(page["history_status"], "completed")
                self.assertIn("poi-west-lake", page["text"])

    async def test_history_and_map_proposals_share_total_tool_budget(self):
        from typing import cast
        from anthropic import AsyncAnthropic
        from mcp import Client
        from travel_app.retrieval import Retrieval
        from planning_fixtures import agent_loop
        from service_fixtures import MapService
        class CountingMap(MapService):
            def __init__(self):
                self.calls = []
            async def call_tool(self, name, arguments):
                self.calls.append((name, arguments))
                return await super().call_tool(name, arguments)
        external = CountingMap()
        tools = Retrieval(cast(Client, external))
        await tools.discover()
        model = ModelService([response([
            {"type": "tool_use", "id": "recall", "name": "session_history_search", "input": {"query": "酒店"}},
            {"type": "tool_use", "id": "map", "name": "maps_text_search", "input": {"keywords": "酒店", "city": "杭州"}},
        ], "tool_use")])
        events = []
        async def observe(kind, data):
            events.append((kind, data))
        await agent_loop([{"role": "user", "content": "酒店"}], cast(AsyncAnthropic, model), tools, "test-model", max_tool_calls=1, observer=observe)
        self.assertEqual(external.calls, [])
        statuses = [kind for kind, data in events if kind in ("tool.completed", "tool.not_executed")]
        self.assertEqual(statuses, ["tool.completed", "tool.not_executed"])

    async def test_partial_source_read_preserves_original_date_and_verification_boundary(self):
        from contextlib import asynccontextmanager
        from datetime import datetime
        from typing import cast
        import httpx
        from anthropic import AsyncAnthropic
        from mcp import Client
        from mcp.server import MCPServer
        from travel_app.retrieval import Retrieval
        from service_fixtures import MapService
        from test_source_failures import finished
        from travel_app.sessions import Runtime, create_app
        sources = MCPServer("历史资料边界")
        @sources.tool(name="web_search_exa")
        def search(query: str, objective: str) -> str:
            return "Title: 酒店须知\nURL: https://hotel.example.test/info\nPublished: 2026-10-10\nHighlights:\n早餐价格仍须核对。"
        @sources.tool(name="web_fetch_exa")
        def fetch(urls: list[str]) -> str:
            return "# 酒店须知\nURL: https://hotel.example.test/info\n\n早餐 68 元，适用日期 2026-10-15，尚未预订。" + "附录" * 3000
        model = ModelService([call("web_fetch_exa", {"urls": ["https://hotel.example.test/info"]}), response([{"type": "text", "text": "已取得旧资料"}])])
        @asynccontextmanager
        async def resources():
            async with Client(sources) as source_client:
                amap = Retrieval(cast(Client, MapService()))
                await amap.discover()
                amap = Retrieval(cast(Client, MapService()), source_client=source_client)
                await amap.discover()
                yield Runtime(cast(AsyncAnthropic, model), amap, "test-model")
        with tempfile.TemporaryDirectory() as directory:
            app = create_app(directory, resources=resources)
            async with app.router.lifespan_context(app):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                    first = (await client.post("/api/turns", json={"input": "读取酒店资料"})).json()
                    saved = await finished(client, first)
                    identifier = "payload:" + saved["tool_calls"][0]["result_payload_id"]
                    model.replies = iter([call("session_history_read", {"record_id": identifier, "offset": 2000, "limit": 25}), response([{"type": "text", "text": "旧条件须核对"}])])
                    second = (await client.post("/api/turns", json={"input": "回读", "session_id": first["session_id"]})).json()
                    await finished(client, second)
                    page = json.loads(model.requests[-1]["messages"][-1]["content"][0]["content"])
                    original = (await client.get("/api/payloads/" + saved["tool_calls"][0]["result_payload_id"])).json()["content"]
                    old = json.loads(original["content"])
                    self.assertEqual(page["source_metadata"]["retrieved_at"], old["retrieved_at"])
                    self.assertIsNotNone(datetime.fromisoformat(page["source_metadata"]["retrieved_at"]).tzinfo)
                    self.assertEqual(page["source_metadata"]["sources"][0]["url"], "https://hotel.example.test/info")
                    self.assertEqual(page["source_metadata"]["sources"][0]["content_type"], "page_body")
                    self.assertEqual(page["source_metadata"]["document_status"], "extraction_result")
                    self.assertEqual(page["text"], original["content"][2000:2025])
                    self.assertEqual(page["verification_status"], "historical_requires_recheck")

    async def test_recall_quota_rejections_do_not_spend_unexecuted_total_calls(self):
        from typing import cast
        from anthropic import AsyncAnthropic
        from mcp import Client
        from travel_app.retrieval import Retrieval
        from planning_fixtures import agent_loop
        from service_fixtures import MapService
        class CountingMap(MapService):
            def __init__(self):
                self.calls = []
            async def call_tool(self, name, arguments):
                self.calls.append(name)
                return await super().call_tool(name, arguments)
        external = CountingMap()
        tools = Retrieval(cast(Client, external))
        await tools.discover()
        proposed = [{"type": "tool_use", "id": f"recall-{index}", "name": "session_history_search", "input": {"query": "酒店"}} for index in range(10)]
        proposed.append({"type": "tool_use", "id": "map", "name": "maps_text_search", "input": {"keywords": "酒店", "city": "杭州"}})
        model = ModelService([response(proposed, "tool_use")])
        await agent_loop([{"role": "user", "content": "酒店"}], cast(AsyncAnthropic, model), tools, "test-model", max_tool_calls=9)
        self.assertEqual(external.calls, ["maps_text_search"])
