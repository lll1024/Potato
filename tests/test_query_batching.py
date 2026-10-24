"""集中查询集成期间发现的真实供应商返回，通过公开 Web 复现。"""
import json
import tempfile
import unittest

import httpx2

from service_fixtures import ModelService, response
from test_source_failures import SourceService, call, finished


class QueryBatchingTests(unittest.IsolatedAsyncioTestCase):
    async def test_free_exa_rate_limit_pauses_web_without_losing_map_or_forwarding_signup(self):
        class LimitedService(SourceService):
            async def transport(self, request):
                payload = json.loads(request.content) if request.method == "POST" else {}
                if payload.get("method") == "tools/call":
                    self.source_calls.append((payload, 0))
                    return httpx2.Response(200, request=request, json={"jsonrpc": "2.0", "id": payload["id"], "result": {
                        "_meta": {"ai.exa/rateLimited": True}, "content": [{"type": "text", "text":
                            "You've hit Exa's free MCP rate limit. To continue using without limits, create your own Exa API key.\n"
                            "Fix: Create API key at https://dashboard.exa.ai/api-keys , and then update Exa MCP URL to this https://mcp.exa.ai/mcp?exaApiKey=YOUR_EXA_API_KEY"}]}})
                return await super().transport(request)
        service = LimitedService([])
        model = ModelService([
            response([call("limited", "web_search_exa", {"query": "北京公园玩法"}),
                      call("map", "maps_text_search", {"keywords": "博物馆", "city": "北京"})], "tool_use"),
            response([call("waiting", "web_search_exa", {"query": "北京胡同玩法"})], "tool_use"),
            response([{"type": "text", "text": "地点资料保留；网页限流，玩法未取得。"}]),
        ])
        with tempfile.TemporaryDirectory() as directory:
            async with service.serving(directory, model, interval=0) as client:
                accepted = (await client.post("/api/turns", json={"input": "北京一日游，不额外核实"})).json()
                saved = await finished(client, accepted)
                limited, maps, waiting = saved["tool_calls"]
                accepting = (await client.get("/api/state")).json()["accepting"]
                map_result = (await client.get("/api/payloads/" + maps["result_payload_id"])).json()
        self.assertEqual(limited["failure_category"], "rate_limit")
        self.assertEqual(maps["status"], "completed")
        self.assertEqual(waiting["status"], "not_executed")
        self.assertEqual(len(service.source_calls), 1)
        self.assertIn("资料服务限流", model.requests[1]["system"])
        self.assertNotIn("YOUR_EXA_API_KEY", str(model.requests[1]["messages"]))
        self.assertNotIn("dashboard.exa.ai", str(model.requests[1]["messages"]))
        self.assertIn("北京市博物馆路", str(map_result))
        self.assertTrue(accepting)


if __name__ == "__main__":
    unittest.main()
