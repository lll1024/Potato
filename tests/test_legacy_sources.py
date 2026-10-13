"""从旧版保存载荷验证 Tavily 历史查看与续聊，不重新开放旧工具。"""
import json
from pathlib import Path
import tempfile
import unittest

from travel_app.store import Store
from test_agent import ModelService, response
from test_source_failures import SourceService, finished


class LegacySourceTests(unittest.IsolatedAsyncioTestCase):
    async def test_normal_and_interrupted_tavily_materials_survive_restart(self):
        for name in ("tavily-search", "tavily_search", "tavily-extract", "tavily_extract"):
            for interrupted in (False, True):
                with self.subTest(name=name, interrupted=interrupted), tempfile.TemporaryDirectory() as directory:
                    search = "search" in name
                    url = "https://museum.example.test/legacy"
                    source = {"url": url, "content" if search else "raw_content": "旧资料唯一标记：预约条件须核对。"}
                    raw = json.dumps({"text": [json.dumps({"results": [source]}, ensure_ascii=False)],
                                      "data": None, "retrieved_at": "2026-10-09T00:00:00+00:00",
                                      "document_status": "search_clues" if search else "extraction_result"}, ensure_ascii=False)
                    result = {"content": raw, "is_error": False}
                    arguments = {"query": "官方预约"} if search else {"urls": [url]}
                    protocol = [{"role": "user", "content": "2026年10月17日去博物馆"},
                                {"role": "assistant", "content": [{"type": "tool_use", "id": "legacy", "name": name, "input": arguments}]},
                                {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "legacy", **result}]}]
                    store = Store(Path(directory))
                    identity = store.accept(protocol[0]["content"])
                    session_id, turn_id = identity["session_id"], identity["turn_id"]
                    store.observe_request(session_id, turn_id, "request.started", {
                        "request_id": "legacy-request", "ordinal": 1, "started_at": "2026-10-09T00:00:00+00:00",
                        "input": {"system": "旧版旅行助手", "messages": protocol[:1]}})
                    store.observe_tool(session_id, turn_id, "tool.completed", {
                        "tool_call_id": "legacy-tool", "request_id": "legacy-request", "ordinal": 1,
                        "name": name, "provider": "tavily", "arguments": arguments, "result": result})
                    if not interrupted:
                        store.finish(session_id, turn_id, "旧回答", protocol, {
                            "status": "completed", "reason": None, "answer_source": "model", "tool_error_count": 0}, 10)
                    store.close()
                    service = SourceService([])
                    model = ModelService([response([{"type": "text", "text": "旧资料仅供参考，继续核对。"}])])
                    async with service.serving(directory, model) as client:
                        restored = (await client.get("/api/sessions/" + session_id)).json()
                        material = restored["turns"][0]["query_materials"][0]
                        self.assertIn("旧资料唯一标记", str(material))
                        self.assertIn("2026-10-09T00:00:00+00:00", str(material))
                        payload = (await client.get("/api/payloads/" + material["payload_id"])).json()
                        self.assertEqual(payload["content"], result)
                        accepted = (await client.post("/api/turns", json={"input": "继续原来的旅行", "session_id": session_id})).json()
                        await finished(client, accepted)
                    request = model.requests[0]
                    self.assertIn("旧资料唯一标记", str(request))
                    self.assertEqual({tool["name"] for tool in request["tools"]}, {"maps_text_search", "web_search_exa", "web_fetch_exa", "session_history_search", "session_history_read"})
                    if interrupted:
                        self.assertEqual(request["messages"], [{"role": "user", "content": "继续原来的旅行"}])
                        self.assertIn("<saved_source_materials>", request["system"])
                    else:
                        self.assertEqual(request["messages"][:-1], protocol)
                    self.assertEqual(service.source_calls, [])

    async def test_legacy_tool_proposal_is_rejected_without_forwarding(self):
        service = SourceService([])
        model = ModelService([
            response([{"type": "tool_use", "id": "old", "name": "tavily_search", "input": {"query": "旧工具"}},
                      {"type": "tool_use", "id": "map", "name": "maps_text_search", "input": {"keywords": "博物馆", "city": "北京"}}], "tool_use"),
            response([{"type": "text", "text": "旧工具不能使用，地点查询成功。"}])])
        with tempfile.TemporaryDirectory() as directory:
            async with service.serving(directory, model) as client:
                accepted = (await client.post("/api/turns", json={"input": "查询博物馆"})).json()
                saved = await finished(client, accepted)
        self.assertEqual([tool["status"] for tool in saved["tool_calls"]], ["failed", "completed"])
        self.assertEqual(service.source_calls, [])
        self.assertEqual(len(service.map_calls), 1)
