"""真实 HTTP/SSE、SQLite 与可控外部模型验证旧轮次摘要。测试容量刻意小于本机 1M。"""
import asyncio
import json
import os
import tempfile
import unittest
from unittest.mock import patch
from test_agent import ModelService, response
from test_trace import serving, wait_finished

ENV = {"CONTEXT_COMPACTION_ENABLED": "true", "MODEL_CONTEXT_WINDOW": "40000"}
OLD = "2026-10-18 杭州旅行，预算3000元，保留西湖酒店。" + "中" * 3900


def summary_response(**changes):
    value = {"travel_conditions": {"budget": "3000元", "hotel": "西湖酒店"},
             "confirmed_itinerary": [], "corrections": [], "unresolved": ["早餐价格待核实"],
             "sources": [{"record_id": "message:1", "verification": "用户要求"},
                         {"record_id": "message:2", "verification": "未预订"}]}
    value.update(changes)
    return response([{"type": "text", "text": json.dumps(value, ensure_ascii=False)}])


async def submit(client, text, session=None):
    accepted = (await client.post("/api/turns", json={"input": text, "session_id": session})).json()
    return await wait_finished(client, accepted["session_id"], accepted["turn_id"])


class ContextSummaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_old_history_summary_answers_preserves_original_and_restarts(self):
        model = ModelService([response([{"type": "text", "text": "酒店未预订，早餐待核实。"}]),
                              summary_response(), response([{"type": "text", "text": "继续保留酒店。"}])])
        with patch.dict(os.environ, ENV, clear=True), tempfile.TemporaryDirectory() as directory:
            async with serving(directory, model) as client:
                first = await submit(client, OLD)
                saved = await submit(client, "继续安排交通", first["session"]["session_id"])
                self.assertEqual(saved["turns"][-1]["answer"], "继续保留酒店。")
                self.assertEqual([item.get("purpose", "travel") for item in saved["requests"]], ["travel", "context_summary", "travel"])
                summary = model.requests[1]
                self.assertNotIn("tools", summary)
                self.assertLessEqual(summary["max_tokens"], 2048)
                self.assertNotIn("你是旅行助手", summary["system"])
                self.assertIn("message:1", str(summary["messages"]))
                self.assertEqual(saved["turns"][-1]["messages"][0]["content"], OLD)
                self.assertIn("travel_state_summary", str(model.requests[2]["messages"]))
                kinds = [event["kind"] for event in saved["events"]]
                self.assertIn("context.adopted", kinds)
                self.assertIn("context.saved", kinds)
                self.assertEqual(saved["turns"][-1]["travel_date_context"]["travel_dates"]["start_date"], "2026-10-18")
            restarted = ModelService([response([{"type": "text", "text": "续聊仍保留酒店。"}])])
            async with serving(directory, restarted) as client:
                saved = await submit(client, "继续", first["session"]["session_id"])
                self.assertEqual(saved["turns"][-1]["answer"], "续聊仍保留酒店。")
                self.assertIn("travel_state_summary", str(restarted.requests[0]["messages"]))
                self.assertNotIn(OLD, str(restarted.requests[0]["messages"]))
