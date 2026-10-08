"""公开 HTTP/SSE 验证断线补齐、删除通知及页面生命周期。"""
import asyncio
import json
import tempfile
import unittest

from test_agent import ModelService, response
from test_trace import serving, wait_finished


async def notices(client, path, headers=None):
    result = []
    async with client.stream("GET", path, headers=headers) as stream:
        async for line in stream.aiter_lines():
            if line.startswith("data:"):
                event = json.loads(line[5:])
                result.append(event)
                if event["kind"] == "service.state":
                    return result
    raise AssertionError("没有收到当前服务状态")


class ReconnectTests(unittest.IsolatedAsyncioTestCase):
    async def test_deleted_session_replays_only_identity_and_survives_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            async with serving(directory, ModelService([response([{"type":"text","text":"应被删除的回答"}])])) as client:
                accepted = (await client.post("/api/turns", json={"input":"应被删除的输入"})).json()
                saved = await asyncio.wait_for(wait_finished(client, accepted["session_id"]), 2)
                boundary = (await client.get("/api/snapshot")).json()
                await client.delete("/api/sessions/" + accepted["session_id"])
                replay = await asyncio.wait_for(notices(client, "/api/events?after=0"), 2)
                persistent = [event for event in replay if not event.get("transient")]
                self.assertEqual([event["kind"] for event in persistent], ["session.deleted"])
                deleted = persistent[0]
                self.assertEqual(deleted["session_id"], accepted["session_id"])
                self.assertEqual(deleted["data"], {})
                self.assertGreater(deleted["cursor"], boundary["cursor"])
                self.assertNotIn("应被删除", json.dumps(replay, ensure_ascii=False))
                self.assertEqual((await client.get("/api/payloads/" + saved["requests"][0]["input_payload_id"])).status_code, 404)
            async with serving(directory, ModelService([])) as client:
                replay = await asyncio.wait_for(notices(client, f'/api/events?after={boundary["cursor"]}&stream_id={boundary["stream_id"]}'), 2)
                self.assertEqual(replay[0], deleted)
                self.assertIsNone(replay[-1]["state"]["active_turn_id"])

    async def test_last_processed_cursor_takes_precedence_over_initial_connection_url(self):
        with tempfile.TemporaryDirectory() as directory:
            async with serving(directory, ModelService([response([{"type":"text","text":"真实结束"}])])) as client:
                baseline = (await client.get("/api/snapshot")).json()
                accepted = (await client.post("/api/turns", json={"input":"快照后、连接前的查询"})).json()
                await asyncio.wait_for(wait_finished(client, accepted["session_id"]), 2)
                path = f'/api/events?after={baseline["cursor"]}&stream_id={baseline["stream_id"]}'
                replay = await asyncio.wait_for(notices(client, path), 2)
                actual = [item for item in replay if not item.get("transient")]
                self.assertEqual([item["kind"] for item in actual], ["turn.accepted","request.started","request.completed","turn.finished"])
                again = await asyncio.wait_for(notices(client, path, {"Last-Event-ID":str(actual[1]["cursor"])}), 2)
                self.assertEqual([item for item in again if not item.get("transient")], actual[2:])
                duplicate = await asyncio.wait_for(notices(client, path), 2)
                self.assertEqual(duplicate, replay)

    async def test_closing_subscription_keeps_background_call_and_other_page_gets_release(self):
        entered, release = asyncio.Event(), asyncio.Event()
        class WaitingModel(ModelService):
            async def create(self, **kwargs):
                entered.set()
                await release.wait()
                return await super().create(**kwargs)
        model = WaitingModel([response([{"type":"text","text":"后台继续完成"}])])
        with tempfile.TemporaryDirectory() as directory:
            async with serving(directory, model) as client:
                baseline = (await client.get("/api/snapshot")).json()
                accepted = (await client.post("/api/turns", json={"input":"关闭页面期间查询"})).json()
                await entered.wait()
                try:
                    async with client.stream("GET", "/api/events?after=0") as page_a:
                        async for line in page_a.aiter_lines():
                            if line.startswith("data:") and json.loads(line[5:])["kind"] == "request.started":
                                break
                    busy = (await client.get("/api/snapshot")).json()
                    self.assertEqual(busy["active_turn_id"], accepted["turn_id"])
                    self.assertEqual(busy["active_session_id"], accepted["session_id"])
                    still_waiting = (await client.get("/api/sessions/" + accepted["session_id"])).json()
                    self.assertEqual(still_waiting["requests"][0]["status"], "running")
                finally:
                    release.set()
                await asyncio.wait_for(wait_finished(client, accepted["session_id"]), 2)
                page_b = await asyncio.wait_for(notices(client, f'/api/events?after={baseline["cursor"]}&stream_id={baseline["stream_id"]}'), 2)
                self.assertEqual(page_b[-2]["kind"], "turn.finished")
                self.assertEqual(page_b[-2]["turn_id"], accepted["turn_id"])
                self.assertIsNone(page_b[-1]["state"]["active_turn_id"])
                self.assertTrue(page_b[-1]["state"]["accepting"])
                self.assertNotIn("cursor", page_b[-1])
                self.assertEqual(len(model.requests), 1)

    async def test_dataset_change_and_invalid_cursor_require_explicit_snapshot(self):
        with tempfile.TemporaryDirectory() as original, tempfile.TemporaryDirectory() as replacement:
            async with serving(original, ModelService([])) as client:
                original_snapshot = (await client.get("/api/snapshot")).json()
            async with serving(replacement, ModelService([])) as client:
                replacement_snapshot = (await client.get("/api/snapshot")).json()
                self.assertNotEqual(replacement_snapshot["stream_id"], original_snapshot["stream_id"])
                for path, headers in [(f'/api/events?after=0&stream_id={original_snapshot["stream_id"]}',None),
                                      ('/api/events?after=99999',None),('/api/events?after=-1',None),
                                      ('/api/events?after=0',{"Last-Event-ID":"broken"})]:
                    failure = await client.get(path, headers=headers)
                    self.assertEqual(failure.status_code, 409)
                    self.assertEqual(failure.json()["detail"]["code"], "SNAPSHOT_REQUIRED")

    async def test_trace_summary_page_defers_complete_input_and_answer_to_stable_payloads(self):
        answer = "真实完整回答" * 500
        user_input = "真实完整输入" * 500
        with tempfile.TemporaryDirectory() as directory:
            async with serving(directory, ModelService([response([{"type":"text","text":answer}])])) as client:
                accepted = (await client.post("/api/turns", json={"input":user_input})).json()
                await asyncio.wait_for(wait_finished(client, accepted["session_id"]), 2)
                summary = (await client.get('/api/sessions/' + accepted["session_id"] + '?summary=true&limit=1')).json()
                turn = summary["turns"][0]
                self.assertNotIn("messages", turn)
                self.assertTrue(turn["input_is_summary"])
                self.assertTrue(turn["answer_is_summary"])
                self.assertLessEqual(len(turn["input"]), 120)
                self.assertLessEqual(len(turn["answer"]), 120)
                for field, full in [("input",user_input),("answer",answer)]:
                    payload = (await client.get('/api/payloads/' + turn[field + '_payload_id'])).json()
                    self.assertEqual(payload["content"], full)
