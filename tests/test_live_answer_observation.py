"""验收观测器在外部 SDK 与 ASGI 边界保持透明，并只导出允许元数据。"""
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace as Value
import unittest

from live_answer_observation import Recorder, ObservedClient, ObservedSSE


class LiveObservationTests(unittest.IsolatedAsyncioTestCase):
    async def test_sdk_yields_original_events_and_final_response_without_exporting_text(self):
        text = Value(type="content_block_delta", index=0, delta=Value(type="text_delta", text="这是可见正文"))
        private = Value(type="content_block_delta", index=1, delta=Value(type="thinking_delta", thinking="不可导出的推理"))
        events = [text, private]
        response = Value(content=[Value(type="text", text="这是可见正文")], stop_reason="end_turn")
        received = []
        class Upstream:
            @asynccontextmanager
            async def stream(self, **parameters):
                received.append(parameters)
                class Stream:
                    async def __aiter__(self):
                        for item in events:
                            yield item
                    async def get_final_message(self):
                        return response
                yield Stream()
        recorder = Recorder()
        client = ObservedClient(Value(messages=Upstream()), recorder)
        parameters = {"model": "test", "messages": [{"role": "user", "content": "私密输入"}]}
        async with client.messages.stream(**parameters) as stream:
            actual = [item async for item in stream]
            final = await stream.get_final_message()
        self.assertIs(actual[0], text)
        self.assertIs(actual[1], private)
        self.assertIs(final, response)
        self.assertEqual(received, [parameters])
        self.assertEqual([row["delta_chars"] for row in recorder.events if row["kind"] == "text"], [6])
        encoded = json.dumps(recorder.events, ensure_ascii=False)
        for forbidden in ("这是可见正文", "不可导出的推理", "私密输入"):
            self.assertNotIn(forbidden, encoded)

    async def test_sse_original_chunks_and_disconnect_are_unchanged(self):
        value = {"turn_id": "turn", "revision": 3, "text": "不能落盘的原始片段", "status": "generating"}
        body = ("event: answer.preview\ndata: " + json.dumps(value) + "\n\n").encode()
        messages = [{"type": "http.response.start", "status": 200, "headers": []},
                    {"type": "http.response.body", "body": body[:24], "more_body": True},
                    {"type": "http.response.body", "body": body[24:], "more_body": False}]
        async def app(scope, receive, send):
            self.assertEqual(await receive(), {"type": "http.disconnect"})
            for message in messages:
                await send(message)
        async def receive():
            return {"type": "http.disconnect"}
        sent = []
        async def send(message):
            sent.append(message)
        recorder = Recorder()
        await ObservedSSE(app, recorder)({"type": "http", "path": "/api/events", "headers": [(b"user-agent", b"Potato/1")]}, receive, send)
        self.assertEqual(sent, messages)
        self.assertIs(sent[1], messages[1])
        self.assertEqual(recorder.events[0]["content"]["text_chars"], 9)
        self.assertEqual(recorder.events[0]["client"], "ios")
        self.assertNotIn(value["text"], json.dumps(recorder.events, ensure_ascii=False))

    async def test_complete_call_and_error_propagate_without_exporting_exception_details(self):
        response = Value(content=[Value(type="text", text="完成")], stop_reason="end_turn")
        class Upstream:
            async def create(self, **parameters):
                if parameters.get("fail"):
                    raise RuntimeError("secret credential")
                return response
        recorder = Recorder()
        client = ObservedClient(Value(messages=Upstream()), recorder)
        self.assertIs(await client.messages.create(model="test"), response)
        with self.assertRaisesRegex(RuntimeError, "secret credential"):
            await client.messages.create(model="test", fail=True)
        self.assertEqual(recorder.events[-1]["error_type"], "RuntimeError")
        self.assertNotIn("secret credential", json.dumps(recorder.events))
