"""通过公开 HTTP 与真实 MCP 协议验证资料故障的局部降级。"""
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime
import json
import tempfile
import sqlite3
from contextlib import closing
from pathlib import Path
import unittest
from typing import cast

import httpx
import httpx2
from anthropic import AsyncAnthropic
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.server import MCPServer

from amap_mcp import AmapTools
from tavily_mcp import KEYLESS_HEADERS, TavilyTools
from source_http import SourceHTTPClient
from travel_tools import TravelTools
from test_agent import ModelService, response
from web import Runtime, create_app


def call(identity, name, arguments):
    return {"type": "tool_use", "id": identity, "name": name, "input": arguments}


async def finished(client, identity):
    for _ in range(500):
        state = (await client.get("/api/state")).json()
        if state["active_turn_id"] is None:
            return (await client.get("/api/sessions/" + identity["session_id"])).json()
        await asyncio.sleep(0.01)
    raise AssertionError("对话未结束")


class SourceService:
    def __init__(self, replies):
        self.replies = list(replies)
        self.source_calls = []
        self.map_calls = []
        self.wire = []
        self.closed = []
        self.stateful = False
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def transport(self, request):
        payload = json.loads(request.content) if request.method == "POST" else {}
        self.wire.append((request.method, payload.get("method"), dict(request.headers)))
        method = payload.get("method")
        if method == "initialize":
            result = {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}},
                      "serverInfo": {"name": "资料故障边界", "version": "1"}}
        elif method == "tools/list":
            result = {"tools": [{"name": name, "inputSchema": {"type": "object"}}
                                for name in ("tavily_search", "tavily_extract")]}
        elif method == "tools/call":
            self.source_calls.append((payload, asyncio.get_running_loop().time()))
            reply = self.replies.pop(0)
            if reply == "wait":
                self.entered.set()
                await self.release.wait()
                reply = {"results": [{"url": "https://museum.example.test/visit", "raw_content": "官方预约须知"}]}
            if reply == "disconnect":
                raise httpx2.ConnectError("资料调用断连", request=request)
            if reply in ("json-read", "sse-read", "sse-eof", "timeout", "json-invalid", "rpc-invalid", "rpc-wrong-id", "sse-ok"):
                if reply == "timeout":
                    raise httpx2.ReadTimeout("资料读取超时", request=request)
                if reply in ("rpc-wrong-id", "sse-ok"):
                    result = {"content": [{"type": "text", "text": json.dumps({"results": [{"url": "https://museum.example.test/visit", "raw_content": "已读官方预约须知"}]})}], "isError": False}
                    if reply == "rpc-wrong-id":
                        return httpx2.Response(200, request=request, json={"jsonrpc": "2.0", "id": 999999, "result": result})
                    return httpx2.Response(200, request=request, headers={"content-type": "text/event-stream"},
                                           content=b"event: message\ndata: " + json.dumps({"jsonrpc": "2.0", "id": payload["id"], "result": result}).encode() + b"\n\n")
                if reply in ("json-invalid", "rpc-invalid"):
                    return httpx2.Response(200, request=request, headers={"content-type": "application/json"},
                                           content=b"invalid json" if reply == "json-invalid" else b'{"results": []}')
                service = self
                class FailedStream(httpx2.AsyncByteStream):
                    async def __aiter__(self):
                        yield b"id: resume-token\n\n" if reply.startswith("sse") else b'{"jsonrpc":'
                        if reply != "sse-eof":
                            raise httpx2.ReadError("资料响应读取中断", request=request)
                    async def aclose(self):
                        service.closed.append(True)
                return httpx2.Response(200, request=request, stream=FailedStream(), headers={
                    "content-type": "text/event-stream" if reply.startswith("sse") else "application/json"})
            if isinstance(reply, int):
                return httpx2.Response(reply, request=request, text="资料网关故障")
            result = {"content": [{"type": "text", "text": json.dumps(reply, ensure_ascii=False)}], "isError": False}
        elif method is None:
            return httpx2.Response(405, request=request)
        else:
            return httpx2.Response(202, request=request)
        return httpx2.Response(200, request=request,
                              headers={"Mcp-Session-Id": "source-session"} if self.stateful and method == "initialize" else {},
                              json={"jsonrpc": "2.0", "id": payload["id"], "result": result})

    @asynccontextmanager
    async def resources(self, model, *, interval=None):
        maps = MCPServer("资料故障后的可用地图")
        @maps.tool(name="maps_text_search")
        async def place(keywords: str, city: str) -> dict:
            self.map_calls.append((keywords, city, asyncio.get_running_loop().time()))
            return {"name": keywords, "address": city + "市博物馆路"}
        async with Client(maps) as map_client:
            amap = AmapTools(map_client)
            await amap.discover()
            async with SourceHTTPClient(transport=httpx2.MockTransport(self.transport), headers=KEYLESS_HEADERS) as source_http:
                async with Client(streamable_http_client("http://sources.test/mcp", http_client=source_http), read_timeout_seconds=0.2) as source_client:
                    kwargs = {"call_interval": interval} if interval is not None else {}
                    sources = TavilyTools(source_client, redact=amap.redact, tool_timeout=0.2, **kwargs)
                    await sources.discover()
                    yield Runtime(cast(AsyncAnthropic, model), TravelTools(amap, sources), "test-model")

    @asynccontextmanager
    async def serving(self, directory, model, *, interval=None, clock=None):
        @asynccontextmanager
        async def resources():
            async with self.resources(model, interval=interval) as runtime:
                yield runtime
        app = create_app(directory, resources=resources, clock=clock)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                yield client


class SourceFailureTests(unittest.IsolatedAsyncioTestCase):
    async def test_disconnected_source_does_not_cancel_map_or_http_runtime(self):
        service = SourceService(["disconnect"])
        model = ModelService([
            response([call("source", "tavily_search", {"query": "官方规则"}),
                      call("map", "maps_text_search", {"keywords": "博物馆", "city": "北京"})], "tool_use"),
            response([{"type": "text", "text": "地图地点已取得；资料连接失败，开放和预约待核实。"}]),
        ])
        with tempfile.TemporaryDirectory() as directory:
            async with service.serving(directory, model) as client:
                accepted = (await client.post("/api/turns", json={"input": "2026年10月17日去北京博物馆"})).json()
                snapshot = await finished(client, accepted)
                self.assertEqual(snapshot["turns"][0]["status"], "completed")
                self.assertEqual([(tool["name"], tool["status"]) for tool in snapshot["tool_calls"]],
                                 [("tavily_search", "failed"), ("maps_text_search", "completed")])
                self.assertEqual(snapshot["tool_calls"][0]["failure_category"], "connection")
                self.assertEqual(service.map_calls[0][:2], ("博物馆", "北京"))
                self.assertEqual(len(service.source_calls), 1)
                state = (await client.get("/api/state")).json()
                self.assertTrue(state["accepting"])
                self.assertFalse(state["map_paused"])
                results = model.requests[1]["messages"][-1]["content"]
                self.assertEqual([result["tool_use_id"] for result in results], ["source", "map"])
                self.assertEqual([result["is_error"] for result in results], [True, False])
                self.assertIn("资料连接", model.requests[1]["system"])

    async def test_malformed_wire_returns_failure_with_map_still_available(self):
        for fault in ("json-invalid", "rpc-invalid", "rpc-wrong-id"):
            with self.subTest(fault=fault):
                service = SourceService([fault])
                model = ModelService([
                    response([call("bad", "tavily_search", {"query": "官方规则"}),
                              call("map", "maps_text_search", {"keywords": "博物馆", "city": "北京"})], "tool_use"),
                    response([{"type": "text", "text": "资料无法解析，开放与预约待核实；地图查询成功。"}]),
                ])
                with tempfile.TemporaryDirectory() as directory:
                    async with service.serving(directory, model) as client:
                        accepted = (await client.post("/api/turns", json={"input": "2026年10月17日去北京博物馆"})).json()
                        snapshot = await finished(client, accepted)
                        self.assertEqual(snapshot["tool_calls"][0]["failure_category"], "malformed")
                        self.assertEqual(snapshot["tool_calls"][1]["status"], "completed")
                        self.assertEqual(len(service.map_calls), 1)
                        self.assertFalse((await client.get("/api/state")).json()["map_paused"])
                        self.assertEqual(snapshot["turns"][0]["query_materials"], [])

    async def test_source_http_faults_are_local_and_do_not_retry_or_resume_streams(self):
        for fault, category in [(401, "auth"), (403, "auth"), (429, "rate_limit"), (503, "service"),
                                ("json-read", "connection"), ("sse-read", "connection"),
                                ("sse-eof", "connection"), ("timeout", "timeout")]:
            with self.subTest(fault=fault):
                service = SourceService([fault])
                service.stateful = True
                model = ModelService([
                    response([call("failed", "tavily_search", {"query": "官方规则"}),
                              call("map", "maps_text_search", {"keywords": "博物馆", "city": "北京"}),
                              call("paused", "tavily_extract", {"urls": ["https://museum.example.test"]})], "tool_use"),
                    response([{"type": "text", "text": "地点已取得，资料核实未完成。"}]),
                ])
                with tempfile.TemporaryDirectory() as directory:
                    async with service.serving(directory, model) as client:
                        accepted = (await client.post("/api/turns", json={"input": "2026年10月17日去北京博物馆"})).json()
                        snapshot = await finished(client, accepted)
                        self.assertEqual([tool["status"] for tool in snapshot["tool_calls"]],
                                         ["failed", "completed", "not_executed"])
                        self.assertEqual(snapshot["tool_calls"][0]["failure_category"], category)
                        self.assertEqual(len(service.source_calls), 1)
                        self.assertEqual(len(service.map_calls), 1)
                        self.assertFalse(any(method == "GET" for method, _, _ in service.wire))
                        self.assertFalse((await client.get("/api/state")).json()["map_paused"])
                        self.assertIn("暂停资料查询", model.requests[1]["system"])
                        self.assertNotIn("stop_reason", (await client.get("/api/payloads/" + snapshot["tool_calls"][0]["result_payload_id"])).text)
                        self.assertEqual(snapshot["turns"][0]["tool_error_count"], 1)
                        for method, _, headers in service.wire:
                            self.assertNotIn("authorization", headers)
                            if method == "POST":
                                self.assertEqual(headers["x-tavily-access-mode"], "keyless")
                        if fault in ("json-read", "sse-read", "sse-eof"):
                            self.assertTrue(service.closed)

    async def test_empty_or_unreadable_source_returns_do_not_become_saved_facts(self):
        for unusable, category in [({"results": []}, "empty"), ("无法解析的正文", "malformed"),
                                    ({"results": [{"url": "https://museum.example.test", "raw_content": ""}]}, "empty")]:
            with self.subTest(unusable=unusable):
                search = {"results": [{"url": "https://museum.example.test/visit", "content": "官网线索"}]}
                service = SourceService([search, unusable])
                model = ModelService([
                    response([call("search", "tavily_search", {"query": "官方规则"}),
                              call("body", "tavily_extract", {"urls": ["https://museum.example.test/visit"]}),
                              call("map", "maps_text_search", {"keywords": "博物馆", "city": "北京"})], "tool_use"),
                    response([{"type": "text", "text": "搜索取得线索，但正文未取得，规则仍待核实。"}]),
                ])
                with tempfile.TemporaryDirectory() as directory:
                    async with service.serving(directory, model) as client:
                        accepted = (await client.post("/api/turns", json={"input": "2026年10月17日去北京博物馆"})).json()
                        snapshot = await finished(client, accepted)
                        self.assertEqual(snapshot["tool_calls"][1]["failure_category"], category)
                        self.assertEqual(snapshot["tool_calls"][1]["status"], "failed")
                        materials = snapshot["turns"][0]["query_materials"]
                        self.assertEqual(len(materials), 1)
                        self.assertIn("搜索线索", materials[0]["title"])
                        self.assertIn("正文尚未核实", materials[0]["title"])
                        self.assertEqual(len(service.map_calls), 1)
                        self.assertTrue(model.requests[1]["messages"][-1]["content"][1]["is_error"])

    async def test_sources_have_their_own_spacing_without_delaying_first_map_call(self):
        data = {"results": [{"url": "https://museum.example.test/visit", "content": "官方线索"}]}
        service = SourceService([data, data])
        model = ModelService([
            response([call("first", "tavily_search", {"query": "参观"}),
                      call("map", "maps_text_search", {"keywords": "博物馆", "city": "北京"}),
                      call("second", "tavily_search", {"query": "预约"})], "tool_use"),
            response([{"type": "text", "text": "已取得搜索线索，规则仍需正文核实。"}]),
        ])
        with tempfile.TemporaryDirectory() as directory:
            async with service.serving(directory, model) as client:
                accepted = (await client.post("/api/turns", json={"input": "2026年10月17日去北京博物馆"})).json()
                snapshot = await finished(client, accepted)
                first, second = [when for _, when in service.source_calls]
                self.assertGreaterEqual(second - first, 0.38)
                self.assertLess(service.map_calls[0][2], second)
                self.assertGreater(snapshot["tool_calls"][2]["wait_duration_ms"], 0)
                self.assertTrue(any(event["kind"] == "tool.waiting" for event in snapshot["events"]))

    async def test_stop_during_source_rate_wait_keeps_first_saved_body_only(self):
        body = {"results": [{"url": "https://museum.example.test/visit", "raw_content": "官方预约须知"}]}
        service = SourceService([body])
        model = ModelService([response([
            call("saved", "tavily_extract", {"urls": ["https://museum.example.test/visit"]}),
            call("waiting", "tavily_search", {"query": "补充公告"}),
            call("pending-map", "maps_text_search", {"keywords": "博物馆", "city": "北京"})], "tool_use")])
        with tempfile.TemporaryDirectory() as directory:
            async with service.serving(directory, model) as client:
                accepted = (await client.post("/api/turns", json={"input": "2026年10月17日去北京博物馆"})).json()
                for _ in range(100):
                    snapshot = (await client.get("/api/sessions/" + accepted["session_id"])).json()
                    if any(tool["status"] == "waiting" for tool in snapshot["tool_calls"]):
                        break
                    await asyncio.sleep(0.005)
                else:
                    self.fail("未观察到资料限速等待")
                await client.post("/api/turns/" + accepted["turn_id"] + "/stop")
                snapshot = await finished(client, accepted)
                self.assertEqual(snapshot["turns"][0]["reason"], "user_stop")
                self.assertEqual([tool["status"] for tool in snapshot["tool_calls"]], ["completed", "not_executed", "not_executed"])
                self.assertEqual(len(service.source_calls), 1)
                self.assertEqual(service.map_calls, [])
                self.assertIn("官方预约须知", str(snapshot["turns"][0]["query_materials"]))
                self.assertEqual(snapshot["turns"][0]["travel_date_context"]["travel_dates"]["start_date"], "2026-10-17")
                self.assertEqual(len(model.requests), 1)

    async def test_stop_waits_for_entered_source_and_keeps_its_real_saved_result(self):
        service = SourceService(["wait"])
        model = ModelService([response([
            call("entered", "tavily_extract", {"urls": ["https://museum.example.test/visit"]}),
            call("pending-map", "maps_text_search", {"keywords": "博物馆", "city": "北京"})], "tool_use")])
        with tempfile.TemporaryDirectory() as directory:
            async with service.serving(directory, model) as client:
                accepted = (await client.post("/api/turns", json={"input": "2026年10月17日去北京博物馆"})).json()
                await asyncio.wait_for(service.entered.wait(), 1)
                await client.post("/api/turns/" + accepted["turn_id"] + "/stop")
                self.assertEqual((await client.get("/api/state")).json()["active_turn_id"], accepted["turn_id"])
                self.assertEqual((await client.post("/api/turns", json={"input": "另一请求"})).status_code, 409)
                service.release.set()
                snapshot = await finished(client, accepted)
                self.assertEqual(snapshot["turns"][0]["reason"], "user_stop")
                self.assertEqual([tool["status"] for tool in snapshot["tool_calls"]], ["completed", "not_executed"])
                self.assertIn("官方预约须知", str(snapshot["turns"][0]["query_materials"]))
                self.assertEqual(service.map_calls, [])

    async def test_mixed_services_share_the_total_budget(self):
        data = {"results": [{"url": "https://museum.example.test/visit", "content": "官方线索"}]}
        service = SourceService([data] * 62)
        proposals = [call("map-first", "maps_text_search", {"keywords": "博物馆", "city": "北京"})]
        proposals += [call("source-" + str(i), "tavily_search", {"query": "官方线索 " + str(i)}) for i in range(62)]
        proposals += [call("map-last", "maps_text_search", {"keywords": "博物馆", "city": "北京"}),
                      call("over-budget", "tavily_search", {"query": "不可再查询"})]
        model = ModelService([response(proposals, "tool_use")])
        with tempfile.TemporaryDirectory() as directory:
            async with service.serving(directory, model, interval=0) as client:
                accepted = (await client.post("/api/turns", json={"input": "2026年10月17日去北京博物馆"})).json()
                snapshot = await finished(client, accepted)
                self.assertEqual(snapshot["turns"][0]["reason"], "budget")
                self.assertEqual(len(service.source_calls), 62)
                self.assertEqual(len(service.map_calls), 2)
                self.assertEqual(snapshot["tool_calls"][-1]["status"], "not_executed")
                self.assertEqual(len(model.requests), 1)
                self.assertIn("上限", snapshot["turns"][0]["answer"])

    async def test_source_credentials_and_sensitive_url_parameters_are_redacted_everywhere(self):
        public = "https://museum.example.test/visit?language=zh"
        data = {"results": [{"url": "https://museum.example.test/visit?client_secret=url-secret&language=zh", "raw_content":
                'TAVILY_API_KEY=source-secret; {"tavily_api_key": "nested-secret"} ' + public}],
                "refresh_token": "refresh-secret"}
        service = SourceService([data])
        model = ModelService([
            response([call("body", "tavily_extract", {"urls": [public], "tavily_api_key": "proposed-secret"})], "tool_use"),
            response([{"type": "text", "text": "TAVILY_API_KEY=answer-secret " + public}]),
        ])
        with tempfile.TemporaryDirectory() as directory:
            async with service.serving(directory, model) as client:
                accepted = (await client.post("/api/turns", json={"input": '2026年10月17日去博物馆 TAVILY_API_KEY=input-secret https://museum.example.test/?refresh_token=input-url-secret'})).json()
                snapshot = await finished(client, accepted)
                payloads = []
                for tool in snapshot["tool_calls"]:
                    for field in ("arguments_payload_id", "service_payload_id", "result_payload_id", "error_payload_id"):
                        if tool.get(field):
                            payloads.append((await client.get("/api/payloads/" + tool[field])).json())
                for request in snapshot["requests"]:
                    for field in ("input_payload_id", "response_payload_id", "error_payload_id"):
                        if request.get(field):
                            payloads.append((await client.get("/api/payloads/" + request[field])).json())
                exposed = json.dumps([snapshot, model.requests, payloads, service.source_calls], ensure_ascii=False)
                for secret in ("source-secret", "nested-secret", "refresh-secret", "proposed-secret", "answer-secret", "input-secret", "url-secret", "input-url-secret"):
                    self.assertNotIn(secret, exposed)
                self.assertIn(public, exposed)
                self.assertIn("REDACTED", exposed)

    async def test_restart_recovers_saved_body_as_reference_without_replaying_interrupted_protocol(self):
        data = {"results": [{"url": "https://museum.example.test/notice", "raw_content": "10月10日至11日暂停开放。"}]}
        service = SourceService([data])
        first_model = ModelService([
            response([call("saved-body", "tavily_extract", {"urls": ["https://museum.example.test/notice"]})], "tool_use"),
            response([{"type": "text", "text": "已有正文，但本轮保存失败。"}]),
        ])
        with tempfile.TemporaryDirectory() as directory:
            async with service.serving(directory, first_model, clock=lambda: datetime.fromisoformat("2026-10-09T08:00:00+08:00")) as client:
                with closing(sqlite3.connect(Path(directory) / "travel.sqlite3")) as database, database:
                    database.execute("CREATE TRIGGER fail_finish BEFORE INSERT ON events WHEN NEW.kind='turn.finished' BEGIN SELECT RAISE(ABORT,'保存故障'); END")
                accepted = (await client.post("/api/turns", json={"input": "本周末去北京博物馆"})).json()
                before = await finished(client, accepted)
                self.assertEqual(before["tool_calls"][0]["status"], "completed")
                self.assertIn("暂停开放", str(before["turns"][0]["query_materials"]))
                self.assertEqual((await client.get("/api/state")).json()["unsaved_fact"]["kind"], "turn.finished")
            with closing(sqlite3.connect(Path(directory) / "travel.sqlite3")) as database, database:
                database.execute("DROP TRIGGER fail_finish")
            next_model = ModelService([response([{"type": "text", "text": "按原日期继续，保存的正文仍须按日期核对。"}]),
                                       response([{"type": "text", "text": "已经改期，旧公告不自动适用于新日期。"}])])
            async with service.serving(directory, next_model, clock=lambda: datetime.fromisoformat("2026-10-20T08:00:00+08:00")) as client:
                restored = (await client.get("/api/sessions/" + accepted["session_id"])).json()
                self.assertTrue(restored["turns"][0]["context_excluded"])
                self.assertEqual(restored["tool_calls"], before["tool_calls"])
                self.assertEqual(next_model.requests, [])
                later = (await client.post("/api/turns", json={"input": "按原旅行日期继续", "session_id": accepted["session_id"]})).json()
                continued = await finished(client, later)
                self.assertEqual(next_model.requests[0]["messages"], [{"role": "user", "content": "按原旅行日期继续"}])
                self.assertIn("10月10日至11日暂停开放", next_model.requests[0]["system"])
                self.assertIn('"recheck_required": false', next_model.requests[0]["system"])
                self.assertEqual(continued["turns"][1]["travel_date_context"]["travel_dates"]["start_date"], "2026-10-10")
                changed = (await client.post("/api/turns", json={"input": "改成2026年10月24日", "session_id": accepted["session_id"]})).json()
                resumed = await finished(client, changed)
                self.assertIn('"recheck_required": true', next_model.requests[1]["system"])
                self.assertEqual(resumed["turns"][2]["travel_date_context"]["travel_dates"]["start_date"], "2026-10-24")
                self.assertEqual(len(service.source_calls), 1)
                self.assertEqual(service.map_calls, [])

    async def test_unsaved_source_return_is_not_recovered_as_fact(self):
        service = SourceService([{"results": [{"url": "https://museum.example.test/visit", "raw_content": "尚未保存的正文唯一标记"}]}])
        first_model = ModelService([response([
            call("unsaved", "tavily_extract", {"urls": ["https://museum.example.test/visit"]}),
            call("pending-map", "maps_text_search", {"keywords": "博物馆", "city": "北京"})], "tool_use")])
        with tempfile.TemporaryDirectory() as directory:
            async with service.serving(directory, first_model) as client:
                with closing(sqlite3.connect(Path(directory) / "travel.sqlite3")) as database, database:
                    database.execute("CREATE TRIGGER fail_result BEFORE INSERT ON events WHEN NEW.kind='tool.completed' BEGIN SELECT RAISE(ABORT,'保存故障'); END")
                accepted = (await client.post("/api/turns", json={"input": "2026年10月17日去北京博物馆"})).json()
                before = await finished(client, accepted)
                self.assertEqual(before["tool_calls"][0]["status"], "running")
                self.assertNotIn("result_payload_id", before["tool_calls"][0])
                self.assertEqual(before["turns"][0]["query_materials"], [])
                self.assertEqual((await client.get("/api/state")).json()["unsaved_fact"]["kind"], "tool.completed")
                self.assertEqual(service.map_calls, [])
            with closing(sqlite3.connect(Path(directory) / "travel.sqlite3")) as database, database:
                database.execute("DROP TRIGGER fail_result")
            next_model = ModelService([response([{"type": "text", "text": "没有已保存正文，规则待核实。"}])])
            async with service.serving(directory, next_model) as client:
                later = (await client.post("/api/turns", json={"input": "继续原旅行", "session_id": accepted["session_id"]})).json()
                after = await finished(client, later)
                self.assertTrue(after["tool_calls"][0]["interrupted"])
                self.assertEqual(after["turns"][0]["query_materials"], [])
                self.assertNotIn("尚未保存的正文唯一标记", str(next_model.requests))
                self.assertNotIn("<saved_source_materials>", next_model.requests[0]["system"])
                self.assertEqual(next_model.requests[0]["messages"], [{"role": "user", "content": "继续原旅行"}])
                self.assertEqual(after["turns"][1]["travel_date_context"]["travel_dates"]["start_date"], "2026-10-17")
                self.assertEqual(len(service.source_calls), 1)

    async def test_search_success_body_failure_and_model_failure_preserve_clues_and_allow_next_map(self):
        import copy
        class FailingModel(ModelService):
            async def create(self, **kwargs):
                if len(self.requests) == 1:
                    self.requests.append(copy.deepcopy(kwargs))
                    raise RuntimeError("TAVILY_API_KEY=model-error-secret")
                return await super().create(**kwargs)
        search = {"results": [{"title": "官方参观须知", "url": "https://museum.example.test/visit", "content": "预约规则见正文"}]}
        service = SourceService([search, 503])
        model = FailingModel([
            response([call("search", "tavily_search", {"query": "官方参观须知"}),
                      call("body", "tavily_extract", {"urls": ["https://museum.example.test/visit"]})], "tool_use"),
            response([call("map", "maps_text_search", {"keywords": "博物馆", "city": "北京"})], "tool_use"),
            response([{"type": "text", "text": "地点已查到，正文读取失败，预约仍待核实。"}]),
        ])
        with tempfile.TemporaryDirectory() as directory:
            async with service.serving(directory, model) as client:
                accepted = (await client.post("/api/turns", json={"input": "2026年10月17日去北京博物馆"})).json()
                before = await finished(client, accepted)
                self.assertEqual(before["turns"][0]["reason"], "model_error")
                self.assertEqual([tool["status"] for tool in before["tool_calls"]], ["completed", "failed"])
                materials = before["turns"][0]["query_materials"]
                self.assertEqual(len(materials), 1)
                self.assertIn("正文尚未核实", materials[0]["title"])
                error_payload = (await client.get("/api/payloads/" + before["requests"][1]["error_payload_id"])).json()
                self.assertNotIn("model-error-secret", str(error_payload))
                retry = (await client.post("/api/turns", json={"input": "继续查地点", "session_id": accepted["session_id"]})).json()
                after = await finished(client, retry)
                self.assertEqual(after["turns"][0], before["turns"][0])
                self.assertEqual(after["turns"][1]["status"], "completed")
                self.assertEqual(after["turns"][1]["travel_date_context"]["travel_dates"]["start_date"], "2026-10-17")
                self.assertEqual(len(service.source_calls), 2)
                self.assertEqual(len(service.map_calls), 1)
                self.assertFalse((await client.get("/api/state")).json()["map_paused"])
                self.assertIn("官方参观须知", str(model.requests[2]["messages"]))

    async def test_successful_source_sse_is_saved_without_a_spurious_failure(self):
        service = SourceService(["sse-ok"])
        model = ModelService([
            response([call("body", "tavily_extract", {"urls": ["https://museum.example.test/visit"]})], "tool_use"),
            response([{"type": "text", "text": "已取得正文，政策仍须核对身份与适用日期。"}]),
        ])
        with tempfile.TemporaryDirectory() as directory:
            async with service.serving(directory, model) as client:
                accepted = (await client.post("/api/turns", json={"input": "2026年10月17日去北京博物馆"})).json()
                snapshot = await finished(client, accepted)
                self.assertEqual(snapshot["tool_calls"][0]["status"], "completed")
                self.assertFalse(model.requests[1]["messages"][-1]["content"][0]["is_error"])
                self.assertIn("已读官方预约须知", str(snapshot["turns"][0]["query_materials"]))
                self.assertEqual(snapshot["turns"][0]["tool_error_count"], 0)
