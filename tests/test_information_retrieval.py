"""通过公开 HTTP 验证普通资料获取的展示边界，不模拟模型语义。"""
import tempfile
import unittest

from service_fixtures import ModelService, response
from source_fixtures import SourceService, call, finished


class InformationRetrievalTests(unittest.IsolatedAsyncioTestCase):
    async def test_search_materials_do_not_claim_official_identity_or_require_verification(self):
        url = "https://guide.example.test/weekend"
        service = SourceService([{"results": [{"title": "周末玩法攻略", "url": url,
            "content": "博物馆与公园可安排在同一天。"}]}])
        model = ModelService([
            response([call("guide", "web_search_exa", {"query": "北京 周末玩法"})], "tool_use"),
            response([{"type": "text", "text": "已取得玩法资料。"}]),
        ])
        with tempfile.TemporaryDirectory() as directory:
            async with service.serving(directory, model) as client:
                identity = (await client.post("/api/turns", json={"input": "找北京周末玩法"})).json()
                snapshot = await finished(client, identity)
                material = snapshot["turns"][0]["query_materials"][0]
                payload = (await client.get("/api/payloads/" + material["payload_id"])).json()
        self.assertEqual(material["title"], "网页搜索资料（摘录）")
        self.assertIn(url, str(material["entries"]))
        self.assertIn("搜索摘录：", str(material["entries"]))
        self.assertNotIn("须逐项核对", str(material))
        self.assertIn("由旅行者出行前自行核实", str(material["notes"]))
        self.assertIn(url, str(payload))

    async def test_unavailable_page_is_reported_as_missing_information_not_a_verification_task(self):
        service = SourceService(["No content found"])
        model = ModelService([
            response([call("page", "web_fetch_exa", {"urls": ["https://guide.example.test/weekend"]})], "tool_use"),
            response([{"type": "text", "text": "这份资料未取得，可以继续规划。"}]),
        ])
        with tempfile.TemporaryDirectory() as directory:
            async with service.serving(directory, model) as client:
                identity = (await client.post("/api/turns", json={"input": "读取周末玩法资料"})).json()
                snapshot = await finished(client, identity)
                tool = snapshot["tool_calls"][0]
                payload = (await client.get("/api/payloads/" + tool["result_payload_id"])).json()
        self.assertEqual(tool["status"], "failed")
        self.assertIn("资料未返回可用搜索内容或正文", str(payload))
        self.assertNotIn("未完成正文核实", str(payload))
        self.assertEqual(snapshot["turns"][0]["query_materials"], [])
