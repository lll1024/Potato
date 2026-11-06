"""通过真实 HTTP、SQLite 与可控外部模型验证最终回答流。"""
import asyncio
import json
import tempfile
import unittest
import copy
import httpx2
from anthropic import AsyncAnthropic
from types import SimpleNamespace
from contextlib import asynccontextmanager

from http_fixtures import serving, wait_finished, tool_reply
from service_fixtures import ModelService, response
from process_fixtures import ExternalServices, process
from model_fixtures import connection_error, status_error


class FailingAnswerModel:
    """仅在外部模型连接控制尝试、分块和失败。"""

    def __init__(self, failures, *, query_rounds=2):
        self.messages = self
        self.requests = []
        self.failures = iter(failures)
        self.release = asyncio.Queue()
        self.delivered = asyncio.Queue()
        self.fallback_entered = asyncio.Event()
        self.query_rounds = query_rounds

    async def create(self, **parameters):
        self.requests.append(copy.deepcopy(parameters))
        if len(self.requests) == 1:
            return tool_reply(1)
        if len(self.requests) < self.query_rounds:
            return response([{"type": "tool_use", "id": f"history-{len(self.requests)}",
                "name": "session_history_search", "input": {"query": "西湖"}}], "tool_use")
        if len(self.requests) == self.query_rounds:
            return response([{"type": "text", "text": "资料齐全。"}])
        self.fallback_entered.set()
        await self.release.get()
        return response([{"type": "text", "text": "完整的杭州安排。"}])

    @asynccontextmanager
    async def stream(self, **parameters):
        self.requests.append(copy.deepcopy(parameters))
        failure = next(self.failures)
        model = self
        class Stream:
            async def __aiter__(self):
                yield SimpleNamespace(type="content_block_start", content_block=SimpleNamespace(type="text", text=""))
                await model.release.get()
                yield SimpleNamespace(type="content_block_delta", delta=SimpleNamespace(type="text_delta", text="失败旧正文" if failure else "重新作答。"))
                model.delivered.put_nowait(None)
                await model.release.get()
                if failure:
                    raise failure

            async def get_final_message(self):
                return response([{"type": "text", "text": "重新作答。"}])
        yield Stream()


class StreamingModel:
    def __init__(self):
        self.messages = self
        self.requests = []
        self.release = asyncio.Queue()
        self.delivered = asyncio.Queue()
        self.entered = asyncio.Event()
        self.returned = False
        self.block_breaks = set()
        self.parts = ["## 杭州旅行\n\n", "上午游览西湖。\n\n", "下午品尝杭帮菜。\n"]

    async def create(self, **parameters):
        self.requests.append(parameters)
        if len(self.requests) == 1:
            return tool_reply(1)
        return response([{"type": "text", "text": "工具前后的说明，不应成为正文。"}])

    @asynccontextmanager
    async def stream(self, **parameters):
        self.requests.append(parameters)
        model = self
        class Stream:
            async def __aiter__(self):
                model.entered.set()
                for index, part in enumerate(model.parts):
                    if index == 0 or index in model.block_breaks:
                        yield SimpleNamespace(type="content_block_start", content_block=SimpleNamespace(type="text", text=""))
                    await model.release.get()
                    yield SimpleNamespace(type="content_block_delta", delta=SimpleNamespace(type="text_delta", text=part))
                    model.delivered.put_nowait(part)
                await model.release.get()


            async def get_final_message(self):
                model.returned = True
                blocks = []
                for index, part in enumerate(model.parts):
                    if index == 0 or index in model.block_breaks:
                        blocks.append({"type": "text", "text": part})
                    else:
                        blocks[-1]["text"] += part
                return response(blocks)
        yield Stream()


class AnswerStreamingTests(unittest.IsolatedAsyncioTestCase):
    async def test_sdk_stream_ending_without_stop_reason_is_retried_instead_of_saved(self):
        for empty in (False, True):
            with self.subTest(empty=empty):
                await self.check_incomplete_sdk_stream(empty=empty)

    async def check_incomplete_sdk_stream(self, *, empty):
        calls = []
        def external(request):
            parameters = json.loads(request.content)
            calls.append(parameters)
            message = response([{"type": "text", "text": "重新作答。"}]).model_dump(mode="json")
            if not parameters.get("stream"):
                return httpx2.Response(200, json=message)
            attempt = len([call for call in calls if call.get("stream")])
            frames = [
                {"type": "message_start", "message": {**message, "content": [], "stop_reason": None}},
                {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
                {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "失败旧正文" if attempt == 1 else "重新作答。"}},
            ]
            if attempt > 1:
                frames.extend([{"type": "content_block_stop", "index": 0},
                    {"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None}, "usage": {"output_tokens": 3}},
                    {"type": "message_stop"}])
            elif empty:
                frames = []
            return httpx2.Response(200, headers={"Content-Type": "text/event-stream"}, content="".join(
                "event: " + frame["type"] + "\ndata: " + json.dumps(frame) + "\n\n" for frame in frames))

        async with AsyncAnthropic(api_key="fake", max_retries=0,
                http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(external))) as model:
            with tempfile.TemporaryDirectory() as directory:
                async with serving(directory, model) as client:
                    accepted = (await client.post("/api/turns", json={"input": "杭州一天"})).json()
                    saved = await asyncio.wait_for(wait_finished(client, accepted["session_id"]), 5)
                    self.assertEqual(saved["turns"][0]["answer"], "重新作答。")
                    self.assertEqual(len(saved["requests"]), 3)
                    self.assertEqual(saved["requests"][1]["status"], "failed")
                    self.assertEqual(calls[1], calls[2])
                    self.assertNotIn("失败旧正文", json.dumps(saved["turns"][0]["messages"], ensure_ascii=False))
                history_calls = [{"type": "tool_use", "id": "failed-answer-search",
                    "name": "session_history_search", "input": {"query": "失败旧正文"}}]
                failed_payload = saved["requests"][1]["response_payload_id"]
                if failed_payload:
                    history_calls.append({"type": "tool_use", "id": "failed-answer-read",
                        "name": "session_history_read", "input": {"record_id": f"payload:{failed_payload}:text:0"}})
                recall = ModelService([response(history_calls, "tool_use"),
                    response([{"type": "text", "text": "未找到失败正文。"}])])
                async with serving(directory, recall) as client:
                    later = (await client.post("/api/turns", json={"input": "核对旧记录", "session_id": accepted["session_id"]})).json()
                    await wait_finished(client, accepted["session_id"], later["turn_id"])
                    found = json.loads(recall.requests[1]["messages"][-1]["content"][0]["content"])
                    self.assertEqual(found["matches"], [])
                    if failed_payload:
                        self.assertTrue(recall.requests[1]["messages"][-1]["content"][1]["is_error"])
                        diagnostic = (await client.get("/api/payloads/" + failed_payload)).json()
                        self.assertIn("失败旧正文", json.dumps(diagnostic, ensure_ascii=False))

    async def test_midstream_disconnect_retries_with_empty_preview_and_unchanged_input(self):
        model = FailingAnswerModel([httpx2.ReadError("stream disconnected"), None])
        with tempfile.TemporaryDirectory() as directory:
            async with serving(directory, model) as client:
                accepted = (await client.post("/api/turns", json={"input": "明天杭州一天"})).json()
                try:
                    model.release.put_nowait(None)
                    await asyncio.wait_for(model.delivered.get(), 3)
                    before = (await client.get("/api/snapshot")).json()["answer_preview"]
                    self.assertEqual(before["text"], "失败旧正文")
                    async with client.stream("GET", "/api/events?after=0") as stream:
                        model.release.put_nowait(None)
                        async with asyncio.timeout(3):
                            async for line in stream.aiter_lines():
                                if line.startswith("data:"):
                                    event = json.loads(line[5:])
                                    preview = event.get("preview")
                                    if preview and preview["status"] == "retrying":
                                        self.assertEqual(preview["text"], "")
                                        self.assertEqual(preview["attempt_id"], before["attempt_id"])
                                        break
                            else:
                                self.fail("未收到正在重试状态")
                    model.release.put_nowait(None)
                    await asyncio.wait_for(model.delivered.get(), 3)
                    new = (await client.get("/api/snapshot")).json()["answer_preview"]
                    self.assertEqual(new["text"], "重新作答。")
                    self.assertNotEqual(new["attempt_id"], before["attempt_id"])
                    self.assertEqual(model.requests[2], model.requests[3])
                    model.release.put_nowait(None)
                    saved = await wait_finished(client, accepted["session_id"])
                    self.assertEqual(saved["turns"][0]["answer"], "重新作答。")
                    self.assertEqual(len(saved["tool_calls"]), 1)
                    self.assertEqual(len(saved["requests"]), 4)
                    self.assertNotIn("失败旧正文", json.dumps(saved["turns"][0]["messages"], ensure_ascii=False))
                finally:
                    for _ in range(5):
                        model.release.put_nowait(None)

    async def test_explicit_stream_unsupported_falls_back_to_counted_complete_answer(self):
        error = status_error(400, {"error": {"message": "This model does not support streaming"}})
        model = FailingAnswerModel([error])
        with tempfile.TemporaryDirectory() as directory:
            async with serving(directory, model) as client:
                accepted = (await client.post("/api/turns", json={"input": "杭州安排"})).json()
                try:
                    model.release.put_nowait(None)
                    await asyncio.wait_for(model.delivered.get(), 3)
                    model.release.put_nowait(None)
                    await asyncio.wait_for(model.fallback_entered.wait(), 3)
                    preview = (await client.get("/api/snapshot")).json()["answer_preview"]
                    self.assertEqual(preview["text"], "")
                    self.assertEqual(preview["delivery"], "complete")
                    self.assertEqual(model.requests[2], model.requests[3])
                    model.release.put_nowait(None)
                    saved = await wait_finished(client, accepted["session_id"])
                    self.assertEqual(saved["turns"][0]["answer"], "完整的杭州安排。")
                    self.assertEqual(saved["turns"][0]["answer_delivery"], "complete")
                    self.assertEqual(len(saved["requests"]), 4)
                    self.assertEqual(len(saved["tool_calls"]), 1)
                    self.assertNotIn("失败旧正文", json.dumps(saved["turns"][0]["messages"], ensure_ascii=False))
                finally:
                    for _ in range(5):
                        model.release.put_nowait(None)

    async def test_stream_errors_keep_classification_without_accidental_fallback(self):
        cases = [
            (httpx2.ReadTimeout("read timed out"), "TIMEOUT", True),
            (connection_error(), "TRANSPORT", True),
            (status_error(429, headers={"Retry-After": "0.001"}), "RATE_LIMIT", True),
            (status_error(503, headers={"Retry-After": "0.001"}), "SERVER", True),
            (status_error(401), "AUTH", False),
            (status_error(402), "QUOTA", False),
            (status_error(400, {"error": {"message": "invalid request"}}), "INVALID_REQUEST", False),
            (status_error(400, {"error": {"message": "unsupported model; streaming enabled"}}), "INVALID_REQUEST", False),
            (status_error(503, {"error": {"message": "streaming is not supported"}}, {"Retry-After": "0.001"}), "SERVER", True),
        ]
        for error, code, retried in cases:
            with self.subTest(code=code, message=error), tempfile.TemporaryDirectory() as directory:
                model = FailingAnswerModel([error, None])
                for _ in range(5):
                    model.release.put_nowait(None)
                async with serving(directory, model) as client:
                    accepted = (await client.post("/api/turns", json={"input": "杭州一天"})).json()
                    saved = await asyncio.wait_for(wait_finished(client, accepted["session_id"]), 5)
                    self.assertEqual(saved["turns"][0]["status"], "completed" if retried else "failed")
                    self.assertEqual(len(saved["requests"]), 4 if retried else 3)
                    self.assertFalse(model.fallback_entered.is_set())
                    payload = (await client.get("/api/payloads/" + saved["requests"][2]["error_payload_id"])).json()
                    self.assertEqual(payload["content"]["code"], code)
                    self.assertIsNone((await client.get("/api/snapshot")).json()["answer_preview"])

    async def test_explicit_parameter_rejection_and_structured_codes_allow_fallback(self):
        for detail in ({"message": "The stream parameter is not supported"},
                       {"code": "unsupported_parameter", "param": "stream"},
                       {"code": "streaming_not_supported"}):
            with self.subTest(detail=detail), tempfile.TemporaryDirectory() as directory:
                model = FailingAnswerModel([status_error(400, {"error": detail})])
                for _ in range(3):
                    model.release.put_nowait(None)
                async with serving(directory, model) as client:
                    accepted = (await client.post("/api/turns", json={"input": "杭州安排"})).json()
                    saved = await asyncio.wait_for(wait_finished(client, accepted["session_id"]), 5)
                    self.assertEqual(saved["turns"][0]["answer"], "完整的杭州安排。")
                    self.assertEqual(saved["turns"][0]["answer_delivery"], "complete")

    async def test_retry_exhaustion_and_stop_during_backoff_release_the_turn(self):
        for stop in (False, True):
            with self.subTest(stop=stop), tempfile.TemporaryDirectory() as directory:
                model = FailingAnswerModel([status_error(429, headers={"Retry-After": "10" if stop else "0.001"}) for _ in range(6)])
                for _ in range(12):
                    model.release.put_nowait(None)
                async with serving(directory, model) as client:
                    accepted = (await client.post("/api/turns", json={"input": "杭州一天"})).json()
                    if stop:
                        async with asyncio.timeout(3):
                            while True:
                                state = (await client.get("/api/snapshot")).json()
                                if state["answer_preview"] and state["answer_preview"]["status"] == "retrying":
                                    break
                        await client.post("/api/turns/" + accepted["turn_id"] + "/stop")
                    saved = await asyncio.wait_for(wait_finished(client, accepted["session_id"]), 5)
                    self.assertEqual(saved["turns"][0]["reason"], "user_stop" if stop else "model_error")
                    self.assertEqual(len(saved["requests"]), 3 if stop else 8)
                    self.assertEqual(len([event for event in saved["events"] if event["kind"] == "request.retry_scheduled"]), 1 if stop else 5)
                    self.assertFalse(model.fallback_entered.is_set())

    async def test_stop_before_inflight_failure_takes_priority_over_retry_and_fallback(self):
        for error, expected in (
            (connection_error(), ("failed", "model_error")),
            (status_error(400, {"error": {"code": "streaming_not_supported"}}), ("terminated", "user_stop")),
        ):
            with self.subTest(error=error), tempfile.TemporaryDirectory() as directory:
                model = FailingAnswerModel([error])
                async with serving(directory, model) as client:
                    accepted = (await client.post("/api/turns", json={"input": "杭州一天"})).json()
                    model.release.put_nowait(None)
                    await asyncio.wait_for(model.delivered.get(), 3)
                    await client.post("/api/turns/" + accepted["turn_id"] + "/stop")
                    model.release.put_nowait(None)
                    saved = await asyncio.wait_for(wait_finished(client, accepted["session_id"]), 5)
                    self.assertEqual((saved["turns"][0]["status"], saved["turns"][0]["reason"]), expected)
                    self.assertEqual(len(model.requests), 3)
                    self.assertFalse(model.fallback_entered.is_set())

    async def test_fallback_at_63_uses_final_reserve_but_final_stream_never_retries(self):
        error = status_error(400, {"error": {"code": "streaming_not_supported"}})
        for query_rounds in (62, 63):
            with self.subTest(query_rounds=query_rounds), tempfile.TemporaryDirectory() as directory:
                model = FailingAnswerModel([error], query_rounds=query_rounds)
                for _ in range(4):
                    model.release.put_nowait(None)
                async with serving(directory, model) as client:
                    accepted = (await client.post("/api/turns", json={"input": "杭州一天"})).json()
                    saved = await asyncio.wait_for(wait_finished(client, accepted["session_id"]), 12)
                    self.assertEqual(len(model.requests), 64)
                    self.assertEqual(len(saved["requests"]), 64)
                    self.assertEqual(saved["requests"][-1]["purpose"], "travel_final")
                    self.assertEqual(saved["turns"][0]["reason"], "budget")
                    self.assertEqual(model.fallback_entered.is_set(), query_rounds == 62)
                    self.assertEqual(saved["requests"][-1]["status"], "completed" if query_rounds == 62 else "failed")
                    self.assertNotIn("tools", model.requests[-1])
                    self.assertEqual(len([event for event in saved["events"] if event["kind"].startswith("request.retry_")]), 0)

    async def test_web_receives_real_batches_before_response_and_atomic_history(self):
        model = StreamingModel()
        with tempfile.TemporaryDirectory() as directory:
            async with serving(directory, model) as client:
                identity = (await client.post("/api/turns", json={"input": "安排杭州一天"})).json()
                try:
                    async with asyncio.timeout(3):
                        await model.entered.wait()
                    self.assertNotIn("tools", model.requests[-1])
                    snapshot = (await client.get("/api/snapshot")).json()
                    cursor = snapshot["cursor"]
                    async with client.stream("GET", f"/api/events?after={cursor}") as stream:
                        lines = stream.aiter_lines()
                        async def preview():
                            async with asyncio.timeout(3):
                                async for line in lines:
                                    if line.startswith("data:"):
                                        notice = json.loads(line[5:])
                                        if notice.get("kind") == "answer.preview" and notice["preview"] and notice["preview"]["text"]:
                                            return notice["preview"]
                        model.release.put_nowait(None)
                        first = await preview()
                        self.assertEqual(first["text"], model.parts[0])
                        self.assertEqual(first["turn_id"], identity["turn_id"])
                        model.release.put_nowait(None)
                        second = await preview()
                        self.assertEqual(second["text"], "".join(model.parts[:2]))
                        self.assertGreater(second["revision"], first["revision"])
                        self.assertEqual(second["attempt_id"], first["attempt_id"])
                        self.assertFalse(model.returned)
                        current = (await client.get("/api/snapshot")).json()
                        self.assertEqual(current["cursor"], cursor)
                        self.assertEqual(current["answer_preview"], second)
                        history = (await client.get("/api/sessions/" + identity["session_id"])).json()
                        self.assertEqual(history["turns"][0]["status"], "running")
                        self.assertFalse(history["turns"][0]["answer"])
                    model.release.put_nowait(None)
                    model.release.put_nowait(None)
                    saved = await wait_finished(client, identity["session_id"])
                    self.assertEqual(saved["turns"][0]["answer"], "".join(model.parts))
                    self.assertEqual(saved["turns"][0]["status"], "completed")
                    self.assertIsNone((await client.get("/api/snapshot")).json()["answer_preview"])
                    self.assertEqual(len(model.requests), 3)
                finally:
                    for _ in range(5):
                        model.release.put_nowait(None)


    async def test_snapshot_subscription_handoff_and_two_clients_resume_cumulative_text(self):
        model = StreamingModel()
        with tempfile.TemporaryDirectory() as directory:
            async with serving(directory, model) as client:
                identity = (await client.post("/api/turns", json={"input": "跨设备旅行"})).json()
                try:
                    await asyncio.wait_for(model.entered.wait(), 3)
                    model.release.put_nowait(None)
                    await asyncio.wait_for(model.delivered.get(), 3)
                    snapshot = (await client.get("/api/snapshot")).json()
                    self.assertEqual(snapshot["answer_preview"]["text"], model.parts[0])
                    # 在快照返回与订阅建立之间生成第二批，订阅必须先交付最新累计正文。
                    model.release.put_nowait(None)
                    await asyncio.wait_for(model.delivered.get(), 3)
                    path = f'/api/events?after={snapshot["cursor"]}&stream_id={snapshot["stream_id"]}'
                    async with client.stream("GET", path) as first, client.stream("GET", path) as second:
                        first_lines, second_lines = first.aiter_lines(), second.aiter_lines()
                        async def answer(lines):
                            async with asyncio.timeout(3):
                                async for line in lines:
                                    if line.startswith("data:"):
                                        notice = json.loads(line[5:])
                                        if notice.get("kind") == "service.state":
                                            self.assertIsInstance(notice["state"]["answer_revision"], int)
                                        if notice.get("kind") == "answer.preview":
                                            self.assertNotIn("cursor", notice)
                                            return notice
                            self.fail("未收到当前正文核对结果")
                        a, b = await asyncio.gather(answer(first_lines), answer(second_lines))
                        self.assertEqual(a, b)
                        self.assertEqual(a["preview"]["text"], "".join(model.parts[:2]))
                        self.assertGreater(a["revision"], snapshot["answer_revision"])
                        # 关闭一个订阅不影响另一个订阅或在途模型。
                        await first.aclose()
                        model.release.put_nowait(None)
                        await asyncio.wait_for(model.delivered.get(), 3)
                        third = await answer(second_lines)
                        self.assertEqual(third["preview"]["text"], "".join(model.parts))
                        self.assertEqual(third["preview"]["attempt_id"], a["preview"]["attempt_id"])
                        self.assertFalse(model.returned)
                    # 普通断线后的新连接仍直接取得当前正文，而不是拼接旧增量。
                    async with client.stream("GET", path) as resumed:
                        restored = await answer(resumed.aiter_lines())
                        self.assertEqual(restored, third)
                    self.assertEqual((await client.get("/api/snapshot")).json()["cursor"], snapshot["cursor"])
                    model.release.put_nowait(None)
                    history = await wait_finished(client, identity["session_id"])
                    self.assertEqual(history["turns"][0]["answer"], "".join(model.parts))
                    async with client.stream("GET", path) as finished:
                        cleared = await answer(finished.aiter_lines())
                        self.assertIsNone(cleared["preview"])
                        self.assertGreater(cleared["revision"], third["revision"])
                finally:
                    for _ in range(5):
                        model.release.put_nowait(None)

    async def test_process_restart_discards_preview_preserves_dataset_and_never_replays_execution(self):
        release = asyncio.Queue()
        external = ExternalServices(stream_wait=release.get)
        with tempfile.TemporaryDirectory() as directory:
            async with external.serving() as address:
                async with process(directory, address) as (child, client, _):
                    accepted = (await client.post("/api/turns", json={"input": "重启前的旅行回答"})).json()
                    release.put_nowait(None)
                    async with asyncio.timeout(5):
                        while True:
                            before = (await client.get("/api/snapshot")).json()
                            if before["answer_preview"] and before["answer_preview"]["text"]:
                                break
                    self.assertEqual(before["answer_preview"]["text"], "完整旅行建议。")
                    child.kill()
                    await child.wait()
                calls = len(external.model_calls)
                async with process(directory, address) as (_, client, _):
                    after = (await client.get("/api/snapshot")).json()
                    self.assertEqual(after["stream_id"], before["stream_id"])
                    self.assertNotEqual(after["instance_id"], before["instance_id"])
                    self.assertIsNone(after["answer_preview"])
                    self.assertIsNone(after["active_turn_id"])
                    history = (await client.get("/api/sessions/" + accepted["session_id"])).json()
                    self.assertEqual(history["turns"][0]["reason"], "service_interrupted")
                    self.assertFalse(history["turns"][0]["answer"])
                    self.assertEqual(len(external.model_calls), calls)
                    self.assertEqual(external.map_calls, [])
                # 释放外部已取消请求的屏障，让验收资源正常退出。
                release.put_nowait(None)

    async def test_every_preview_redacts_credentials_split_across_external_chunks(self):
        model = StreamingModel()
        model.parts = ["杭州旅行\n\n", "known-", "secret\n\n", "Authorization: Bea",
                       "rer header-secret, extra=hidden\n\n", "[预约](https://example.test/?api_",
                       "key=url-secret)\n\n", '{"password":\n', '"json-secret", "city":"杭州"}\n\n', "结束\n"]
        with tempfile.TemporaryDirectory() as directory:
            async with serving(directory, model) as client:
                identity = (await client.post("/api/turns", json={"input": "安排杭州一天"})).json()
                try:
                    await asyncio.wait_for(model.entered.wait(), 3)
                    for part in model.parts:
                        model.release.put_nowait(None)
                        await asyncio.wait_for(model.delivered.get(), 3)
                        state = (await client.get("/api/snapshot")).json()
                        visible = json.dumps(state, ensure_ascii=False)
                        for secret in ("known-", "header-secret", "extra=hidden", "url-secret", "json-secret"):
                            self.assertNotIn(secret, visible)
                    self.assertFalse(model.returned)
                    model.release.put_nowait(None)
                    history = await wait_finished(client, identity["session_id"])
                    answer = history["turns"][0]["answer"]
                    self.assertIn("杭州", answer)
                    self.assertIn("[REDACTED]", answer)
                    self.assertIn("[REDACTED_URL]", answer)
                    for secret in ("known-secret", "header-secret", "url-secret", "json-secret"):
                        self.assertNotIn(secret, answer)
                finally:
                    for _ in range(len(model.parts) + 1):
                        model.release.put_nowait(None)


    async def test_plain_chinese_batches_are_visible_without_line_breaks(self):
        model = StreamingModel()
        model.parts = ["上午游览西湖。", "下午品尝杭帮菜。", "傍晚沿湖散步。"]
        with tempfile.TemporaryDirectory() as directory:
            async with serving(directory, model) as client:
                identity = (await client.post("/api/turns", json={"input": "杭州一天"})).json()
                try:
                    await asyncio.wait_for(model.entered.wait(), 3)
                    text = ""
                    for part in model.parts:
                        model.release.put_nowait(None)
                        await asyncio.wait_for(model.delivered.get(), 3)
                        text += part
                        state = (await client.get("/api/snapshot")).json()
                        self.assertEqual(state["answer_preview"]["text"], text)
                        self.assertFalse(model.returned)
                    model.release.put_nowait(None)
                    await wait_finished(client, identity["session_id"])
                finally:
                    for _ in range(4):
                        model.release.put_nowait(None)


    async def test_query_commentary_is_not_recalled_as_an_assistant_answer(self):
        model = StreamingModel()
        for _ in range(len(model.parts) + 1):
            model.release.put_nowait(None)
        with tempfile.TemporaryDirectory() as directory:
            async with serving(directory, model) as client:
                identity = (await client.post("/api/turns", json={"input": "杭州一天"})).json()
                await wait_finished(client, identity["session_id"])
            recall = ModelService([response([{"type": "tool_use", "id": "search-commentary",
                "name": "session_history_search", "input": {"query": "工具前后的说明"}}], "tool_use"),
                response([{"type": "text", "text": "未找到该历史回答。"}])])
            async with serving(directory, recall) as client:
                later = (await client.post("/api/turns", json={"input": "回读说明", "session_id": identity["session_id"]})).json()
                await wait_finished(client, later["session_id"], later["turn_id"])
                found = json.loads(recall.requests[1]["messages"][-1]["content"][0]["content"])
                self.assertEqual(found["matches"], [])


    async def test_preview_preserves_text_block_boundaries_for_multiline_secrets(self):
        model = StreamingModel()
        model.parts = ["杭州安排。", "alpha", "beta", "结束。"]
        model.block_breaks = {1, 2, 3}
        with tempfile.TemporaryDirectory() as directory:
            async with serving(directory, model, api_key="alpha\nbeta") as client:
                identity = (await client.post("/api/turns", json={"input": "杭州一天"})).json()
                try:
                    await asyncio.wait_for(model.entered.wait(), 3)
                    for part in model.parts:
                        model.release.put_nowait(None)
                        await asyncio.wait_for(model.delivered.get(), 3)
                        state = (await client.get("/api/snapshot")).json()
                        self.assertNotIn("alpha", state["answer_preview"]["text"])
                        self.assertNotIn("beta", state["answer_preview"]["text"])
                    self.assertFalse(model.returned)
                    model.release.put_nowait(None)
                    history = await wait_finished(client, identity["session_id"])
                    self.assertEqual(history["turns"][0]["answer"], "杭州安排。\n[REDACTED]\n结束。")
                finally:
                    for _ in range(5):
                        model.release.put_nowait(None)
