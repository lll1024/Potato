"""通过助手会话公开入口验证查看与管理，不依赖 HTTP 装配或记录实现。"""
import asyncio
import tempfile
import unittest
from pathlib import Path
from typing import cast

from anthropic import AsyncAnthropic
from mcp import Client
from travel_app.sessions import Runtime, RunRejected, TurnRuntime
from travel_app.local_records import LocalRecords
from travel_app.retrieval import Retrieval
from service_fixtures import MapService, ModelService, response


class SessionContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_accepted_turn_can_be_read_searched_renamed_and_deleted(self):
        model = ModelService([response([{"type": "text", "text": "西湖位于杭州。"}])])
        tools = Retrieval(cast(Client, MapService()))
        await tools.discover()
        with tempfile.TemporaryDirectory() as directory:
            records = LocalRecords(Path(directory))
            session = TurnRuntime(records, Runtime(cast(AsyncAnthropic, model), tools, "test-model"))
            session.start()
            try:
                accepted = await session.submit("查询西湖", None, "public-view")
                async with asyncio.timeout(2):
                    while session.snapshot()["active_turn_id"]:
                        await asyncio.sleep(0)
                identity = accepted["session_id"]
                self.assertEqual(session.history()["sessions"][0]["session_id"], identity)
                saved = session.history_page(identity)
                self.assertEqual(saved["turns"][0]["answer"], "西湖位于杭州。")
                self.assertTrue(session.search(identity, "西湖")["matches"])
                self.assertEqual(session.payload(saved["requests"][0]["input_payload_id"])["schema_version"], 1)
                self.assertEqual(session.rename(identity, "杭州旅行")["title"], "杭州旅行")
                self.assertEqual(session.history_page(identity)["session"]["title"], "杭州旅行")
                session.delete(identity)
                with self.assertRaises(RunRejected) as missing:
                    session.history_page(identity)
                self.assertEqual(missing.exception.status_code, 404)
                with self.assertRaises(RunRejected) as deleted:
                    session.submission_result("public-view")
                self.assertEqual(deleted.exception.status_code, 410)
            finally:
                await session.shutdown()
                records.close()
