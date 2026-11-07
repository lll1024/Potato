"""公开 HTTP/SSE 中的查询进展与真实工具生命周期。"""
import asyncio
import json
import tempfile
import unittest
from contextlib import asynccontextmanager
from types import SimpleNamespace

from http_fixtures import serving, wait_finished
from service_fixtures import MapService, response


class QueryModel:
    def __init__(self):
        self.messages = self
        self.requests = []
        self.release = asyncio.Queue()
        self.delivered = asyncio.Queue()
        self.answer_entered = asyncio.Event()
        self.answer_release = asyncio.Event()
        self.parts = ["进展：准备查找西湖", "的地点资料。"]
        self.has_queries = True

    @asynccontextmanager
    async def stream(self, **parameters):
        self.requests.append(parameters)
        query = "tools" in parameters
        first = query and len(self.requests) == 1
        model = self
        class Stream:
            async def __aiter__(self):
                yield SimpleNamespace(type="content_block_start", content_block=SimpleNamespace(type="text", text=""))
                if first:
                    for part in model.parts:
                        await model.release.get()
                        yield SimpleNamespace(type="content_block_delta", delta=SimpleNamespace(type="text_delta", text=part))
                        model.delivered.put_nowait(part)
                    await model.release.get()
                else:
                    if not query:
                        model.answer_entered.set()
                        await model.answer_release.wait()
                    yield SimpleNamespace(type="content_block_delta", delta=SimpleNamespace(type="text_delta", text="资料齐全。" if query else "西湖旅行安排。"))
            async def get_final_message(self):
                if first:
                    content = [{"type":"text", "text":"".join(model.parts)}]
                    if model.has_queries:
                        content.append({"type":"tool_use", "id":"lake", "name":"maps_text_search", "input":{"keywords":"西湖", "city":"杭州"}})
                    return response(content, "tool_use" if model.has_queries else "end_turn")
                return response([{"type":"text", "text":"资料齐全。" if query else "西湖旅行安排。"}])
        yield Stream()

    async def create(self, **parameters):
        # 旧实现会卡在这里，无法在查询响应完成前提供进展。
        await self.release.get()
        return response([{"type":"text", "text":"资料齐全。"}])


class GatedMap(MapService):
    def __init__(self):
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.fail = False
    async def call_tool(self, name, arguments):
        self.entered.set()
        await self.release.wait()
        if self.fail:
            return SimpleNamespace(content=[SimpleNamespace(type="text", text="查询暂不可用")], structured_content=None, is_error=True)
        return await super().call_tool(name, arguments)


class QueryProgressTests(unittest.IsolatedAsyncioTestCase):
    async def test_query_text_arrives_before_tools_and_real_outcomes_remain_separate_from_answer(self):
        model = QueryModel()
        maps = GatedMap()
        with tempfile.TemporaryDirectory() as directory:
            async with serving(directory, model, maps=maps) as client:
                accepted = (await client.post("/api/turns", json={"input":"查找西湖资料"})).json()
                try:
                    model.release.put_nowait(None)
                    await asyncio.wait_for(model.delivered.get(), .5)
                    snapshot = (await client.get("/api/snapshot")).json()
                    progress = snapshot["progress_preview"]
                    self.assertEqual(progress["entries"][0]["text"], "准备查找西湖")
                    self.assertEqual(progress["entries"][0]["status"], "generating")
                    self.assertIsNone(snapshot["answer_preview"])
                    history = (await client.get("/api/sessions/" + accepted["session_id"])).json()
                    self.assertEqual(history["tool_calls"], [])
                    async with client.stream("GET", "/api/events") as stream:
                        async for line in stream.aiter_lines():
                            if line.startswith("data:"):
                                event = json.loads(line[5:])
                                if event.get("kind") == "progress.preview":
                                    self.assertEqual(event["preview"], progress)
                                    break
                    model.release.put_nowait(None)
                    await asyncio.wait_for(model.delivered.get(), 1)
                    self.assertEqual((await client.get("/api/snapshot")).json()["progress_preview"]["entries"][0]["text"], "准备查找西湖的地点资料。")
                    model.release.put_nowait(None)
                    await asyncio.wait_for(maps.entered.wait(), 2)
                    entries = (await client.get("/api/snapshot")).json()["progress_preview"]["entries"]
                    self.assertEqual(entries[-1]["kind"], "tool")
                    self.assertEqual(entries[-1]["status"], "running")
                    self.assertEqual(entries[-1]["text"], "查找地点资料")
                    self.assertNotIn("西湖", entries[-1]["text"])
                    maps.release.set()
                    await asyncio.wait_for(model.answer_entered.wait(), 2)
                    entries = (await client.get("/api/snapshot")).json()["progress_preview"]["entries"]
                    self.assertEqual(entries[1]["status"], "completed")
                    self.assertEqual(entries[1]["text"], "查找地点资料")
                    model.answer_release.set()
                    history = await asyncio.wait_for(wait_finished(client, accepted["session_id"]), 3)
                    self.assertEqual(history["turns"][0]["answer"], "西湖旅行安排。")
                    self.assertEqual(len(history["tool_calls"]), 1)
                    self.assertEqual(len(model.requests), 3)
                finally:
                    model.answer_release.set()
                    maps.release.set()
                    for _ in range(10): model.release.put_nowait(None)

    async def test_unmarked_long_no_query_answer_never_becomes_progress(self):
        model = QueryModel()
        model.parts = ["# 行前准备清单\n" + "带好证件和衣物。" * 200, "这一轮不需查询。"]
        model.has_queries = False
        with tempfile.TemporaryDirectory() as directory:
            async with serving(directory, model) as client:
                accepted = (await client.post("/api/turns", json={"input":"不查询，整理清单"})).json()
                try:
                    for _ in model.parts:
                        model.release.put_nowait(None)
                        await asyncio.wait_for(model.delivered.get(), 2)
                        preview = (await client.get("/api/snapshot")).json()["progress_preview"]
                        self.assertFalse(any(entry["text"] for entry in preview["entries"]))
                    model.release.put_nowait(None)
                    await asyncio.wait_for(model.answer_entered.wait(), 2)
                    snapshot = (await client.get("/api/snapshot")).json()
                    self.assertFalse(any(entry["text"] for entry in snapshot["progress_preview"]["entries"]))
                    model.answer_release.set()
                    history = await asyncio.wait_for(wait_finished(client, accepted["session_id"]), 3)
                    self.assertEqual(history["tool_calls"], [])
                    self.assertEqual(history["turns"][0]["answer"], "西湖旅行安排。")
                finally:
                    model.answer_release.set()
                    for _ in range(10): model.release.put_nowait(None)

    async def test_progress_marker_and_credentials_split_across_chunks_stay_safe_and_short(self):
        model = QueryModel()
        model.parts = ["进", "展：准备使用 known-", "secret 查询地点资料。" + "补充行动。" * 100]
        with tempfile.TemporaryDirectory() as directory:
            async with serving(directory, model) as client:
                accepted = (await client.post("/api/turns", json={"input":"查找资料"})).json()
                try:
                    observed = []
                    for _ in model.parts:
                        model.release.put_nowait(None)
                        await asyncio.wait_for(model.delivered.get(), 2)
                        preview = (await client.get("/api/snapshot")).json()["progress_preview"]
                        text = preview["entries"][0]["text"]
                        observed.append(text)
                        self.assertLessEqual(len(text), 240)
                        self.assertNotIn("known-secret", text)
                        self.assertNotIn("known-", text)
                    self.assertEqual(observed[0], "")
                    self.assertIn("[REDACTED]", observed[-1])
                    model.release.put_nowait(None)
                    await asyncio.wait_for(model.answer_entered.wait(), 3)
                    entries = (await client.get("/api/snapshot")).json()["progress_preview"]["entries"]
                    self.assertEqual(entries[1]["status"], "completed")
                    self.assertNotIn("核实", entries[1]["text"])
                    model.answer_release.set()
                    await asyncio.wait_for(wait_finished(client, accepted["session_id"]), 3)
                finally:
                    model.answer_release.set()
                    for _ in range(10): model.release.put_nowait(None)

    async def test_tool_failure_is_reported_as_failure_while_answer_can_continue(self):
        model = QueryModel()
        maps = GatedMap()
        maps.fail = True
        with tempfile.TemporaryDirectory() as directory:
            async with serving(directory, model, maps=maps) as client:
                accepted = (await client.post("/api/turns", json={"input":"资料失败情景"})).json()
                try:
                    for _ in range(3): model.release.put_nowait(None)
                    await asyncio.wait_for(maps.entered.wait(), 2)
                    maps.release.set()
                    await asyncio.wait_for(model.answer_entered.wait(), 3)
                    preview = (await client.get("/api/snapshot")).json()["progress_preview"]
                    self.assertEqual(preview["entries"][1]["status"], "failed")
                    model.answer_release.set()
                    history = await asyncio.wait_for(wait_finished(client, accepted["session_id"]), 3)
                    self.assertEqual(history["tool_calls"][0]["status"], "failed")
                    self.assertEqual(history["turns"][0]["tool_error_count"], 1)
                finally:
                    maps.release.set()
                    model.answer_release.set()
                    for _ in range(10): model.release.put_nowait(None)
