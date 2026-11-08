"""公开 HTTP/SSE 中的查询进展与真实工具生命周期。"""
import asyncio
import copy
import json
import tempfile
import unittest
from contextlib import asynccontextmanager
from types import SimpleNamespace

import httpx2
from anthropic import APITimeoutError

from http_fixtures import serving, wait_finished
from service_fixtures import MapService, ModelService, response
from record_faults import fail_event
from model_fixtures import connection_error, status_error


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
        self.query_failure = None

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
                    if model.query_failure: raise model.query_failure
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


class QueryFailureModel(ModelService):
    """仅在可控外部模型流控制查询尝试；完整返回和重试仍由真实规划执行。"""
    def __init__(self, failures, *, hold_complete=False, prefix_queries=0):
        super().__init__([
            response([{"type": "text", "text": "进展：重新查找地点资料。"},
                      {"type": "tool_use", "id": "lake", "name": "maps_text_search",
                       "input": {"keywords": "西湖", "city": "杭州"}}], "tool_use"),
            response([{"type": "text", "text": "无需查询"}]),
            response([{"type": "text", "text": "新的完整回答。"}]),
        ])
        self.failures = list(failures)
        self.in_stream = False
        self.query_successes = 0
        self.prefix_queries = prefix_queries
        self.final_failure = None
        self.hold_failures = False
        self.failure_entered = asyncio.Queue()
        self.failure_release = asyncio.Queue()
        self.hold_complete = hold_complete
        self.complete_entered = asyncio.Event()
        self.complete_release = asyncio.Event()

    async def create(self, **parameters):
        if not self.in_stream and self.hold_complete:
            self.complete_entered.set()
            await self.complete_release.wait()
        reply = await super().create(**parameters)
        if "tools" in parameters:
            self.query_successes += 1
        return reply

    @asynccontextmanager
    async def stream(self, **parameters):
        if ("tools" in parameters and self.failures and self.query_successes >= self.prefix_queries) or ("tools" not in parameters and self.final_failure):
            self.requests.append(copy.deepcopy(parameters))
            failure = self.final_failure if "tools" not in parameters else self.failures.pop(0)
            self.final_failure = None
            model = self
            class Stream:
                async def __aiter__(self):
                    if failure is None:
                        return
                    yield SimpleNamespace(type="content_block_start", content_block=SimpleNamespace(type="text", text=""))
                    yield SimpleNamespace(type="content_block_delta", delta=SimpleNamespace(type="text_delta", text="进展：失败旧说明"))
                    model.failure_entered.put_nowait(None)
                    if model.hold_failures:
                        await model.failure_release.get()
                    raise failure
                async def get_final_message(self):
                    return None
            yield Stream()
        else:
            self.in_stream = True
            try:
                reply = await self.create(**parameters)
            finally:
                self.in_stream = False
            class Stream:
                async def __aiter__(self):
                    for block in reply.content:
                        yield SimpleNamespace(type="content_block_start", content_block=block)
                async def get_final_message(self):
                    return reply
            yield Stream()


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
    async def test_query_stream_rejection_explains_complete_wait_before_answer_and_counts_every_call(self):
        model = QueryFailureModel([status_error(400, {"error": {"code": "streaming_not_supported"}})], hold_complete=True)
        with tempfile.TemporaryDirectory() as directory:
            async with serving(directory, model) as client:
                accepted = (await client.post("/api/turns", json={"input": "查询资料"})).json()
                try:
                    await asyncio.wait_for(model.complete_entered.wait(), 2)
                    snapshot = (await client.get("/api/snapshot")).json()
                    self.assertIsNone(snapshot["answer_preview"])
                    entry = snapshot["progress_preview"]["entries"][-1]
                    self.assertEqual((entry["status"], entry["text"], entry.get("delivery")), ("generating", "", "complete"))
                    model.complete_release.set()
                    history = await asyncio.wait_for(wait_finished(client, accepted["session_id"]), 3)
                    self.assertEqual(history["turns"][0]["answer"], "新的完整回答。")
                    self.assertEqual(history["turns"][0]["answer_delivery"], "complete")
                    self.assertEqual(len(history["requests"]), 4)
                    self.assertEqual(len(history["tool_calls"]), 1)
                    self.assertEqual([entry["status"] for entry in history["turns"][0]["query_progress"]], ["generated", "completed"])
                    self.assertNotIn("失败旧说明", str(history["turns"][0]["messages"]))
                finally:
                    model.complete_release.set()

    async def test_query_retry_replaces_failed_attempt_but_keeps_prior_saved_tool_once(self):
        model = QueryFailureModel([status_error(429, headers={"Retry-After": "0.1"})], prefix_queries=1)
        model.hold_failures = True
        with tempfile.TemporaryDirectory() as directory:
            async with serving(directory, model) as client:
                accepted = (await client.post("/api/turns", json={"input": "先查地点再整理"})).json()
                try:
                    await asyncio.wait_for(model.failure_entered.get(), 2)
                    before = (await client.get("/api/snapshot")).json()["progress_preview"]["entries"]
                    self.assertEqual([entry["status"] for entry in before], ["generated", "completed", "generating"])
                    self.assertEqual(before[-1]["text"], "失败旧说明")
                    async with client.stream("GET", "/api/events") as stream:
                        model.failure_release.put_nowait(None)
                        async with asyncio.timeout(3):
                            async for line in stream.aiter_lines():
                                if not line.startswith("data:"): continue
                                notice = json.loads(line[5:])
                                if notice.get("kind") != "progress.preview" or not notice.get("preview"): continue
                                entries = notice["preview"]["entries"]
                                if entries[-1]["status"] == "retrying":
                                    self.assertEqual(entries[-1]["text"], "")
                                    self.assertEqual(entries[1], before[1])
                                    self.assertEqual(entries[-1]["delivery"], "stream")
                                    break
                    history = await asyncio.wait_for(wait_finished(client, accepted["session_id"]), 3)
                    self.assertEqual(len(history["requests"]), 4)
                    self.assertEqual(len(history["tool_calls"]), 1)
                    self.assertEqual(model.requests[1], model.requests[2])
                    self.assertEqual(history["turns"][0]["answer"], "新的完整回答。")
                    self.assertNotIn("失败旧说明", str(history["turns"][0]["messages"]))
                    self.assertEqual(history["turns"][0]["query_progress"], before[:2])
                finally:
                    model.failure_release.put_nowait(None)

    async def test_query_errors_keep_existing_classification_and_never_accidentally_fall_back(self):
        cases = [(connection_error(), "TRANSPORT", True),
                 (APITimeoutError(request=httpx2.Request("POST", "https://model.example.test")), "TIMEOUT", True),
                 (None, "EMPTY_RESPONSE", True),
                 (status_error(429, headers={"Retry-After": "0.01"}), "RATE_LIMIT", True),
                 (status_error(503), "SERVER", True), (status_error(401), "AUTH", False),
                 (status_error(402), "QUOTA", False), (status_error(400), "INVALID_REQUEST", False)]
        for failure, code, retried in cases:
            with self.subTest(code=code), tempfile.TemporaryDirectory() as directory:
                model = QueryFailureModel([failure], hold_complete=True)
                async with serving(directory, model) as client:
                    accepted = (await client.post("/api/turns", json={"input": "查询资料"})).json()
                    history = await asyncio.wait_for(wait_finished(client, accepted["session_id"]), 5)
                    self.assertEqual(history["requests"][0]["status"], "failed")
                    error = (await client.get("/api/payloads/" + history["requests"][0]["error_payload_id"])).json()["content"]
                    self.assertEqual(error["code"], code)
                    self.assertEqual(history["turns"][0]["status"], "completed" if retried else "failed")
                    self.assertEqual(len(history["requests"]), 4 if retried else 1)
                    self.assertFalse(model.complete_entered.is_set())
                    self.assertNotIn("失败旧说明", str(history["turns"][0]["messages"]))
                    self.assertNotIn("失败旧说明", str(history["turns"][0]["query_progress"]))
                    state = (await client.get("/api/snapshot")).json()
                    self.assertIsNone(state["active_turn_id"])
                    self.assertTrue(state["accepting"])

    async def test_query_retry_exhaustion_and_stop_during_backoff_release_without_more_attempts(self):
        for stop in (False, True):
            with self.subTest(stop=stop), tempfile.TemporaryDirectory() as directory:
                failure = status_error(429, headers={"Retry-After": "3" if stop else "0.01"})
                model = QueryFailureModel([failure] * 6)
                async with serving(directory, model) as client:
                    accepted = (await client.post("/api/turns", json={"input": "持续查询失败"})).json()
                    if stop:
                        async with asyncio.timeout(3):
                            while True:
                                state = (await client.get("/api/snapshot")).json()
                                if state["progress_preview"] and state["progress_preview"]["entries"][-1]["status"] == "retrying": break
                                await asyncio.sleep(.01)
                        self.assertEqual((await client.post(f'/api/turns/{accepted["turn_id"]}/stop')).status_code, 200)
                    history = await asyncio.wait_for(wait_finished(client, accepted["session_id"]), 3)
                    self.assertEqual(history["turns"][0]["reason"], "user_stop" if stop else "model_error")
                    self.assertEqual(len(history["requests"]), 1 if stop else 6)
                    self.assertEqual(history["turns"][0]["query_progress"], [])
                    self.assertEqual(history["tool_calls"], [])
                    self.assertIsNone((await client.get("/api/snapshot")).json()["active_turn_id"])
                    self.assertTrue((await client.get("/api/snapshot")).json()["accepting"])
                    self.assertNotIn("失败旧说明", str(history["turns"][0]["messages"]))

    async def test_query_rejection_at_63_uses_final_reserve_and_final_stream_failure_never_retries(self):
        for prefix in (62, 63):
            with self.subTest(prefix=prefix), tempfile.TemporaryDirectory() as directory:
                rejection = status_error(400, {"error": {"code": "streaming_not_supported"}})
                model = QueryFailureModel([rejection], prefix_queries=prefix)
                model.final_failure = rejection if prefix == 63 else None
                model.replies = iter([response([{"type": "tool_use", "id": f"read-{index}",
                    "name": "session_history_search", "input": {"query": "旅行"}}], "tool_use") for index in range(prefix)]
                    + [response([{"type": "text", "text": "预算内的最终回答。"}])])
                async with serving(directory, model) as client:
                    accepted = (await client.post("/api/turns", json={"input": "查询到预算边界"})).json()
                    history = await asyncio.wait_for(wait_finished(client, accepted["session_id"]), 8)
                    self.assertEqual(len(history["requests"]), 64)
                    self.assertEqual(len(model.requests), 64)
                    self.assertEqual(history["turns"][0]["reason"], "budget")
                    self.assertEqual(history["requests"][-1]["purpose"], "travel_final")
                    self.assertEqual(history["requests"][-1]["status"], "completed" if prefix == 62 else "failed")
                    self.assertNotIn("tools", model.requests[-1])
                    self.assertEqual(len(history["tool_calls"]), prefix)
                    self.assertEqual(sum(tool["call_started"] for tool in history["tool_calls"]), 8)
                    self.assertEqual(len([event for event in history["events"] if event["kind"].startswith("request.retry_")]), 0)

    async def test_failed_query_response_text_is_excluded_from_followup_history_read(self):
        from anthropic.types import TextBlock
        malformed = response([{"type": "text", "text": "进展：失败旧说明"}])
        malformed.content.append(TextBlock.model_construct(type="text", text=123))
        with tempfile.TemporaryDirectory() as directory:
            async with serving(directory, ModelService([malformed])) as client:
                accepted = (await client.post("/api/turns", json={"input": "失败查询"})).json()
                history = await wait_finished(client, accepted["session_id"])
                self.assertEqual(history["turns"][0]["status"], "failed")
                payload = history["requests"][0]["response_payload_id"]
                self.assertIsNotNone(payload)
                self.assertEqual(history["turns"][0]["query_progress"], [])
            recall = ModelService([response([
                {"type": "tool_use", "id": "search-failed", "name": "session_history_search", "input": {"query": "失败旧说明"}},
                {"type": "tool_use", "id": "read-failed", "name": "session_history_read", "input": {"record_id": f"payload:{payload}:text:0"}},
            ], "tool_use"), response([{"type": "text", "text": "失败说明未作为事实采用。"}])])
            async with serving(directory, recall) as client:
                accepted = (await client.post("/api/turns", json={"input": "回看历史", "session_id": accepted["session_id"]})).json()
                await wait_finished(client, accepted["session_id"], accepted["turn_id"])
                results = recall.requests[1]["messages"][-1]["content"]
                self.assertEqual(json.loads(results[0]["content"])["matches"], [])
                self.assertTrue(results[1]["is_error"])

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

    async def test_saved_progress_survives_second_client_and_same_database_restart(self):
        model = QueryModel()
        with tempfile.TemporaryDirectory() as directory:
            async with serving(directory, model) as client:
                accepted = (await client.post("/api/turns", json={"input":"保存查询进展"})).json()
                try:
                    for _ in range(3): model.release.put_nowait(None)
                    await asyncio.wait_for(model.answer_entered.wait(), 3)
                    live = (await client.get("/api/snapshot")).json()["progress_preview"]["entries"]
                    expected = [entry for entry in live if entry["text"]]
                    model.answer_release.set()
                    history = await asyncio.wait_for(wait_finished(client, accepted["session_id"]), 3)
                    self.assertEqual(history["turns"][0]["query_progress"], expected)
                    import httpx
                    async with httpx.AsyncClient(base_url=client.base_url) as other:
                        second = (await other.get("/api/sessions/" + accepted["session_id"], params={"include_messages":False})).json()
                        self.assertEqual(second["turns"][0]["query_progress"], expected)
                finally:
                    model.answer_release.set()
                    for _ in range(10): model.release.put_nowait(None)
            async with serving(directory, ModelService([])) as reopened:
                history = (await reopened.get("/api/sessions/" + accepted["session_id"])).json()
                self.assertEqual(history["turns"][0]["query_progress"], expected)
                self.assertEqual(history["turns"][0]["answer"], "西湖旅行安排。")

    async def test_progress_fact_rollback_and_restart_never_promote_unsaved_text_or_tool_success(self):
        for kind in ("request.completed", "tool.completed"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                model = QueryModel()
                async with serving(directory, model) as client:
                    accepted = (await client.post("/api/turns", json={"input":"查询事实保存失败"})).json()
                    try:
                        for _ in model.parts:
                            model.release.put_nowait(None)
                            await asyncio.wait_for(model.delivered.get(), 2)
                        self.assertIn("准备查找西湖", (await client.get("/api/snapshot")).json()["progress_preview"]["entries"][0]["text"])
                        fail_event(directory, kind)
                        model.release.put_nowait(None)
                        async with asyncio.timeout(3):
                            while (await client.get("/api/snapshot")).json()["active_turn_id"]:
                                await asyncio.sleep(.01)
                        history = (await client.get("/api/sessions/" + accepted["session_id"])).json()
                        entries = history["turns"][0]["query_progress"]
                        self.assertIsNone(history["turns"][0]["answer"])
                        if kind == "request.completed":
                            self.assertEqual(entries, [])
                        else:
                            self.assertEqual([entry["status"] for entry in entries], ["generated", "running"])
                            self.assertFalse(any(entry["status"] == "completed" for entry in entries))
                    finally:
                        model.answer_release.set()
                        for _ in range(10): model.release.put_nowait(None)
                async with serving(directory, ModelService([])) as reopened:
                    history = (await reopened.get("/api/sessions/" + accepted["session_id"])).json()
                    self.assertEqual(history["turns"][0]["reason"], "service_interrupted")
                    entries = history["turns"][0]["query_progress"]
                    if kind == "request.completed": self.assertEqual(entries, [])
                    else: self.assertEqual(entries[-1]["status"], "interrupted")

    async def test_old_records_without_progress_metadata_remain_readable_and_can_continue(self):
        # 原 CLI 入口不启用显示进展；其保存事实与旧库缺少元数据的形状相同。
        from pathlib import Path
        from travel_app.local_records import LocalRecords
        from travel_app.planning import plan
        from travel_app.retrieval import Retrieval
        from typing import cast
        from mcp import Client
        with tempfile.TemporaryDirectory() as directory:
            tools = Retrieval(cast(Client, MapService()))
            await tools.discover()
            records = LocalRecords(Path(directory))
            accepted = records.accept("旧会话")
            result = await plan("旧会话", ModelService([response([{"type":"text", "text":"旧回答"}])]), tools, "test")
            records.finish(accepted["session_id"], accepted["turn_id"], result.answer, result.material, result.fields(), 0)
            records.close()
            async with serving(directory, ModelService([response([{"type":"text", "text":"续聊回答"}])])) as client:
                history = (await client.get("/api/sessions/" + accepted["session_id"])).json()
                self.assertEqual(history["turns"][0]["query_progress"], [])
                self.assertEqual(history["turns"][0]["answer"], "旧回答")
                later = (await client.post("/api/turns", json={"input":"继续", "session_id":accepted["session_id"]})).json()
                history = await asyncio.wait_for(wait_finished(client, accepted["session_id"], later["turn_id"]), 3)
                self.assertEqual(history["turns"][0]["answer"], "旧回答")
                self.assertEqual(history["turns"][1]["answer"], "续聊回答")

    async def test_failed_model_attempt_partial_progress_is_not_saved_as_history(self):
        model = QueryModel()
        model.query_failure = status_error(401)
        with tempfile.TemporaryDirectory() as directory:
            async with serving(directory, model) as client:
                accepted = (await client.post("/api/turns", json={"input":"失败进展不进入历史"})).json()
                try:
                    for _ in model.parts:
                        model.release.put_nowait(None)
                        await asyncio.wait_for(model.delivered.get(), 2)
                    self.assertIn("准备查找西湖", (await client.get("/api/snapshot")).json()["progress_preview"]["entries"][0]["text"])
                    model.release.put_nowait(None)
                    history = await asyncio.wait_for(wait_finished(client, accepted["session_id"]), 3)
                    self.assertEqual(history["turns"][0]["status"], "failed")
                    self.assertEqual(history["turns"][0]["query_progress"], [])
                    self.assertEqual(history["tool_calls"], [])
                finally:
                    model.answer_release.set()
                    for _ in range(10): model.release.put_nowait(None)
