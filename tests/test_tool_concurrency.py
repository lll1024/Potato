"""公开 Web 入口与真实 MCP/HTTP 的分服务调度验收。"""
import asyncio
from contextlib import asynccontextmanager
import json
import sqlite3
from contextlib import closing
from pathlib import Path
import tempfile
import socket
import uvicorn
import unittest
from typing import cast

import httpx
import httpx2
from anthropic import AsyncAnthropic
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.server import MCPServer

from travel_app.amap_http import AmapHTTPClient
from travel_app.amap_mcp import AmapTools
from travel_app.exa_mcp import ExaTools
from travel_app.source_http import SourceHTTPClient
from travel_app.travel_tools import TravelTools
from travel_app.web import Runtime, create_app
from test_agent import ModelService, response
from test_source_failures import SourceService, call, finished


class ControlledServices(SourceService):
    def __init__(self, *, interval=0.4):
        super().__init__([])
        self.interval = interval
        self.started = []
        self.gates = {}
        self.replies_by_id = {}
        self.inflight = {"map": 0, "web": 0}
        self.maximum = {"map": 0, "web": 0}

    async def request(self, provider, identity):
        self.started.append((provider, identity, asyncio.get_running_loop().time()))
        self.inflight[provider] += 1
        self.maximum[provider] = max(self.maximum[provider], self.inflight[provider])
        try:
            await self.gates.setdefault(identity, asyncio.Event()).wait()
            reply = self.replies_by_id.get(identity)
            if isinstance(reply, Exception):
                raise reply
            return reply
        finally:
            self.inflight[provider] -= 1

    async def transport(self, request):
        payload = json.loads(request.content) if request.method == "POST" else {}
        if payload.get("method") != "tools/call":
            return await super().transport(request)
        arguments = payload["params"]["arguments"]
        identity = arguments.get("query") or arguments["urls"][0].rsplit("/", 1)[-1]
        fault = await self.request("web", identity)
        self.source_calls.append((payload, asyncio.get_running_loop().time()))
        if isinstance(fault, int):
            return httpx2.Response(fault, request=request)
        search = payload["params"]["name"] == "web_search_exa"
        text = (f"Title: {identity}\nURL: https://guide.example.test/{identity}\nHighlights:\n资料 {identity}"
                if search else f"# {identity}\nURL: https://guide.example.test/{identity}\n\n正文 {identity}")
        if isinstance(fault, dict):
            text = json.dumps(fault)
        return httpx2.Response(200, request=request, json={"jsonrpc": "2.0", "id": payload["id"],
            "result": {"content": [{"type": "text", "text": text}], "isError": False}})

    @asynccontextmanager
    async def resources(self, model, **kwargs):
        maps = MCPServer("可控地图查询")
        @maps.tool(name="maps_text_search")
        async def place(keywords: str, city: str) -> dict:
            fault = await self.request("map", keywords)
            return fault or {"name": keywords, "address": city}
        service = self
        class MapTransport(httpx2.ASGITransport):
            async def handle_async_request(self, request):
                payload = json.loads(request.content) if request.method == "POST" else {}
                if payload.get("method") == "tools/call":
                    identity = payload["params"]["arguments"]["keywords"]
                    fault = service.replies_by_id.get(identity)
                    if isinstance(fault, (Exception, int)):
                        await service.request("map", identity)
                        return httpx2.Response(fault, request=request)
                return await super().handle_async_request(request)
        app = maps.streamable_http_app(json_response=True, stateless_http=True)
        async with app.router.lifespan_context(app):
            async with AmapHTTPClient(transport=MapTransport(app=app)) as map_http:
                async with Client(streamable_http_client("http://127.0.0.1:8000/mcp", http_client=map_http)) as map_client:
                    amap = AmapTools(map_client, tool_timeout=15, http_client=map_http)
                    await amap.discover()
                    async with SourceHTTPClient(transport=httpx2.MockTransport(self.transport), headers={}) as source_http:
                        async with Client(streamable_http_client("http://sources.test/mcp", http_client=source_http), read_timeout_seconds=15) as source_client:
                            sources = ExaTools(source_client, redact=amap.redact, tool_timeout=15, call_interval=self.interval)
                            await sources.discover()
                            yield Runtime(cast(AsyncAnthropic, model), TravelTools(amap, sources), "test-model")

    @asynccontextmanager
    async def serving(self, directory, model):
        @asynccontextmanager
        async def resources():
            async with self.resources(model) as runtime:
                yield runtime
        app = create_app(directory, resources=resources)
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        self.server = uvicorn.Server(uvicorn.Config(app, log_level="critical", access_log=False))
        task = asyncio.create_task(self.server.serve(sockets=[sock]))
        try:
            while not self.server.started and not task.done():
                await asyncio.sleep(0.01)
            async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{sock.getsockname()[1]}") as client:
                yield client
        finally:
            self.release_all()
            self.server.should_exit = True
            await task
            sock.close()

    def unblock(self, *identities):
        for identity in identities:
            self.gates.setdefault(identity, asyncio.Event()).set()

    def release_all(self):
        self.unblock(*[f"w{i}" for i in range(140)], *[f"m{i}" for i in range(4)])

    async def wait_started(self, provider, count):
        async def poll():
            while len([s for s in self.started if s[0] == provider]) < count:
                await asyncio.sleep(0.01)
        await asyncio.wait_for(poll(), 6)


def web_block(index):
    return call(f"w{index}", "web_search_exa", {"query": f"w{index}"}) if index % 2 == 0 else call(
        f"w{index}", "web_fetch_exa", {"urls": [f"https://guide.example.test/w{index}", "https://guide.example.test/other"]})


def map_block(index):
    return call(f"m{index}", "maps_text_search", {"keywords": f"m{index}", "city": "杭州"})


class ToolConcurrencyTests(unittest.IsolatedAsyncioTestCase):
    async def test_ten_mixed_web_requests_overlap_serial_maps_and_keep_identity_and_live_order(self):
        service = ControlledServices()
        blocks = [map_block(0), *[web_block(i) for i in range(12)], map_block(1), map_block(2)]
        model = ModelService([response(blocks, "tool_use"), response([{"type": "text", "text": "已有查询资料。"}])])
        with tempfile.TemporaryDirectory() as directory:
            async with service.serving(directory, model) as client:
                accepted = (await client.post("/api/turns", json={"input": "安排杭州旅行"})).json()
                try:
                    await service.wait_started("web", 10)
                    self.assertEqual(service.inflight, {"map": 1, "web": 10})
                    self.assertEqual(len(model.requests), 1)
                    live = (await client.get("/api/sessions/" + accepted["session_id"])).json()
                    self.assertEqual([t["tool_use_id"] for t in live["tool_calls"]], [b["id"] for b in blocks])
                    self.assertFalse(live["tool_calls"][11]["call_started"])
                    service.unblock("w7")
                    await service.wait_started("web", 11)
                    live = (await client.get("/api/sessions/" + accepted["session_id"])).json()
                    self.assertEqual(live["tool_calls"][8]["status"], "completed")
                    self.assertEqual(live["tool_calls"][1]["status"], "running")
                    self.assertEqual(len(model.requests), 1)
                    service.unblock("m0")
                    await service.wait_started("map", 2)
                    self.assertEqual([s[1] for s in service.started if s[0] == "map"], ["m0", "m1"])
                finally:
                    service.release_all()
                saved = await finished(client, accepted)
                self.assertEqual(service.maximum, {"map": 1, "web": 10})
                for provider in ("map", "web"):
                    starts = [s[2] for s in service.started if s[0] == provider]
                    self.assertTrue(all(b - a >= 0.39 for a, b in zip(starts, starts[1:])))
                results = model.requests[1]["messages"][-1]["content"]
                self.assertEqual([r["tool_use_id"] for r in results], [b["id"] for b in blocks])
                for result, block in zip(results, blocks):
                    self.assertIn(block["id"], result["content"])
                self.assertEqual([t["status"] for t in saved["tool_calls"]], ["completed"] * len(blocks))
            restarted = ModelService([])
            async with service.serving(directory, restarted) as client:
                restored = (await client.get("/api/sessions/" + accepted["session_id"])).json()
                self.assertEqual(restored["tool_calls"], saved["tool_calls"])
                self.assertEqual(restarted.requests, [])

    async def test_history_read_is_a_barrier_and_reads_real_prior_result(self):
        service = ControlledServices(interval=0)
        blocks = [web_block(0), map_block(0),
            call("history", "session_history_read", {"record_id": "message:3:block:0", "limit": 4000}), web_block(1)]
        model = ModelService([response(blocks, "tool_use"), response([{"type": "text", "text": "整理资料。"}])])
        with tempfile.TemporaryDirectory() as directory:
            async with service.serving(directory, model) as client:
                accepted = (await client.post("/api/turns", json={"input": "查杭州旅行资料"})).json()
                try:
                    await service.wait_started("web", 1)
                    await service.wait_started("map", 1)
                    live = (await client.get("/api/sessions/" + accepted["session_id"])).json()
                    self.assertEqual(live["tool_calls"][2]["status"], "pending")
                    self.assertEqual(live["tool_calls"][3]["status"], "pending")
                    service.unblock("w0")
                    # 地图尚未收束，历史和后段网页都不能推进。
                    await asyncio.sleep(0.03)
                    self.assertEqual(len(service.started), 2)
                    service.unblock("m0")
                    await service.wait_started("web", 2)
                finally:
                    service.release_all()
                saved = await finished(client, accepted)
                history_result = model.requests[1]["messages"][-1]["content"][2]
                self.assertFalse(history_result["is_error"])
                content = json.loads(history_result["content"])
                self.assertIn("资料 w0", content["text"])
                self.assertEqual(content["verification_status"], "historical_requires_recheck")
                self.assertEqual(content["source_metadata"]["sources"][0]["url"], "https://guide.example.test/w0")
                self.assertEqual(saved["tool_calls"][2]["status"], "completed")

    async def test_stop_keeps_global_slot_until_all_inflight_results_are_saved(self):
        service = ControlledServices(interval=0)
        blocks = [map_block(0), *[web_block(i) for i in range(12)], map_block(1)]
        model = ModelService([response(blocks, "tool_use"), response([{"type": "text", "text": "新轮次。"}])])
        with tempfile.TemporaryDirectory() as directory:
            async with service.serving(directory, model) as client:
                accepted = (await client.post("/api/turns", json={"input": "安排杭州旅行"})).json()
                try:
                    await service.wait_started("web", 10)
                    await client.post("/api/turns/" + accepted["turn_id"] + "/stop")
                    service.unblock("w7")
                    await asyncio.sleep(0.05)
                    self.assertEqual(len(service.started), 11)
                    state = (await client.get("/api/state")).json()
                    self.assertEqual(state["active_turn_id"], accepted["turn_id"])
                    self.assertEqual((await client.post("/api/turns", json={"input": "下一轮"})).status_code, 409)
                finally:
                    service.release_all()
                saved = await finished(client, accepted)
                self.assertEqual(saved["turns"][0]["reason"], "user_stop")
                self.assertEqual(len(model.requests), 1)
                self.assertEqual([t["status"] for t in saved["tool_calls"]], ["completed"] * 11 + ["not_executed"] * 3)
                self.assertEqual([t["reason"] for t in saved["tool_calls"][-3:]], ["user_stop"] * 3)
                next_turn = (await client.post("/api/turns", json={"input": "下一轮"})).json()
                await client.post("/api/turns/" + accepted["turn_id"] + "/stop")
                new = await finished(client, next_turn)
                self.assertEqual(new["turns"][0]["status"], "completed")

    async def test_result_storage_failure_drains_other_calls_and_restores_only_committed_facts(self):
        service = ControlledServices(interval=0)
        model = ModelService([response([map_block(0), *[web_block(i) for i in range(12)], map_block(1)], "tool_use")])
        with tempfile.TemporaryDirectory() as directory:
            async with service.serving(directory, model) as client:
                accepted = (await client.post("/api/turns", json={"input": "安排杭州旅行"})).json()
                try:
                    await service.wait_started("web", 10)
                    live = (await client.get("/api/sessions/" + accepted["session_id"])).json()
                    fail_id = live["tool_calls"][1]["tool_call_id"]
                    with closing(sqlite3.connect(Path(directory) / "travel.sqlite3")) as database, database:
                        database.execute("CREATE TRIGGER fail_one BEFORE INSERT ON events WHEN NEW.kind='tool.completed' AND NEW.tool_call_id='" + fail_id + "' BEGIN SELECT RAISE(ABORT,'failed result'); END")
                    service.unblock("w0")
                    await asyncio.sleep(0.05)
                    state = (await client.get("/api/state")).json()
                    self.assertFalse(state["accepting"])
                    self.assertEqual(state["active_turn_id"], accepted["turn_id"])
                    self.assertEqual((await client.post("/api/turns", json={"input": "新一轮"})).status_code, 503)
                    self.assertEqual(len(service.started), 11)
                    self.assertEqual(len(model.requests), 1)
                finally:
                    service.release_all()
                saved = await finished(client, accepted)
                self.assertEqual(saved["turns"][0]["status"], "running")
                self.assertEqual(saved["tool_calls"][1]["status"], "running")
                self.assertNotIn("result_payload_id", saved["tool_calls"][1])
                self.assertEqual([t["status"] for t in saved["tool_calls"][2:11]], ["completed"] * 9)
                self.assertEqual(saved["tool_calls"][0]["status"], "completed")
            restarted = ModelService([])
            async with service.serving(directory, restarted) as client:
                restored = (await client.get("/api/sessions/" + accepted["session_id"])).json()
                self.assertEqual(restored["turns"][0]["reason"], "service_interrupted")
                self.assertTrue(restored["tool_calls"][1]["interrupted"])
                self.assertEqual(restored["tool_calls"][2:11], saved["tool_calls"][2:11])
                self.assertEqual(restarted.requests, [])

    async def test_provider_faults_pause_only_that_provider_and_keep_other_inflight_results(self):
        cases = [("map", 401, "auth"), ("map", 429, "quota"), ("map", 503, "service"),
                 ("map", httpx2.ConnectError("地图断连"), "connection"),
                 ("web", 429, "rate_limit"), ("web", httpx2.ConnectError("网页断连"), "connection")]
        for provider, fault, category in cases:
            with self.subTest(provider=provider, category=category), tempfile.TemporaryDirectory() as directory:
                service = ControlledServices(interval=0.05)
                identity = "m0" if provider == "map" else "w0"
                service.replies_by_id[identity] = fault
                blocks = [map_block(0), *[web_block(i) for i in range(12)], map_block(1)]
                model = ModelService([response(blocks, "tool_use"), response([{"type": "text", "text": "缺失资料说明。"}]),
                    response([map_block(2) if provider == "web" else web_block(12)], "tool_use"),
                    response([{"type": "text", "text": "剩余能力继续取得资料。"}])])
                async with service.serving(directory, model) as client:
                    accepted = (await client.post("/api/turns", json={"input": "安排杭州旅行"})).json()
                    try:
                        await service.wait_started("web", 2)
                        service.unblock(identity)
                        # 等故障落库，再释放其他在途调用；后续同家不应进入。
                        for _ in range(100):
                            live = (await client.get("/api/sessions/" + accepted["session_id"])).json()
                            failed = next(t for t in live["tool_calls"] if t["tool_use_id"] == identity)
                            if failed["status"] == "failed":
                                break
                            await asyncio.sleep(0.01)
                        self.assertEqual(failed["failure_category"], category)
                    finally:
                        service.release_all()
                    saved = await finished(client, accepted)
                    self.assertTrue((await client.get("/api/state")).json()["accepting"])
                    self.assertEqual(saved["tool_calls"][2]["status"], "completed")
                    self.assertEqual(saved["tool_calls"][-1]["status"], "not_executed" if provider == "map" else "completed")
                    self.assertEqual([s[1] for s in service.started if s[0] == provider], ["m0"] if provider == "map" else ["w0", "w1"])
                    self.assertIn("暂停地图查询" if provider == "map" else "暂停资料查询", model.requests[1]["system"])
                    continued = (await client.post("/api/turns", json={"input": "继续可用查询"})).json()
                    next_saved = await finished(client, continued)
                    self.assertEqual(next_saved["tool_calls"][0]["status"], "completed")

    async def test_sse_reports_out_of_order_completion_before_batch_finishes(self):
        service = ControlledServices(interval=0)
        model = ModelService([response([web_block(0), web_block(1), map_block(0)], "tool_use"),
            response([{"type": "text", "text": "整理资料。"}])])
        with tempfile.TemporaryDirectory() as directory:
            async with service.serving(directory, model) as client:
                accepted = (await client.post("/api/turns", json={"input": "获取杭州资料"})).json()
                try:
                    await service.wait_started("web", 2)
                    live = (await client.get("/api/sessions/" + accepted["session_id"])).json()
                    completed_id = live["tool_calls"][1]["tool_call_id"]
                    service.unblock("w1")
                    async def completed_event():
                        async with client.stream("GET", "/api/events?after=0") as stream:
                            async for line in stream.aiter_lines():
                                if line.startswith("data:"):
                                    event = json.loads(line[5:])
                                    if event["kind"] == "tool.completed" and event["tool_call_id"] == completed_id:
                                        return event
                    event = await asyncio.wait_for(completed_event(), 2)
                    live = (await client.get("/api/sessions/" + accepted["session_id"])).json()
                    self.assertEqual(event["tool_call_id"], live["tool_calls"][1]["tool_call_id"])
                    self.assertEqual([t["status"] for t in live["tool_calls"]], ["running", "completed", "running"])
                    self.assertEqual(len(model.requests), 1)
                finally:
                    service.release_all()
                await finished(client, accepted)

    async def test_real_service_shutdown_drains_all_inflight_without_followup_dispatch(self):
        service = ControlledServices(interval=0)
        model = ModelService([response([map_block(0), *[web_block(i) for i in range(12)], map_block(1)], "tool_use")])
        with tempfile.TemporaryDirectory() as directory:
            async with service.serving(directory, model) as client:
                accepted = (await client.post("/api/turns", json={"input": "获取杭州资料"})).json()
                await service.wait_started("web", 10)
                service.server.should_exit = True
                await asyncio.sleep(0.3)
                self.assertTrue(service.server.started)
                self.assertEqual(service.inflight, {"map": 1, "web": 10})
                self.assertEqual(len(model.requests), 1)
                service.unblock("w1")
                await asyncio.sleep(0.03)
                self.assertEqual(len(service.started), 11)
                service.release_all()
            restarted = ModelService([])
            async with service.serving(directory, restarted) as client:
                saved = (await client.get("/api/sessions/" + accepted["session_id"])).json()
                self.assertEqual(saved["turns"][0]["reason"], "service_shutdown")
                self.assertEqual([t["status"] for t in saved["tool_calls"]], ["completed"] * 11 + ["not_executed"] * 3)
                self.assertEqual([t["reason"] for t in saved["tool_calls"][-3:]], ["service_shutdown"] * 3)
                self.assertEqual(restarted.requests, [])

    async def test_budget_is_reserved_in_proposal_order_and_final_waits_for_all_results(self):
        service = ControlledServices(interval=0)
        service.replies_by_id["w1"] = 400  # 可恢复单次失败也消耗额度。
        blocks = [*[web_block(i) for i in range(127)], map_block(0), web_block(127), map_block(1)]
        model = ModelService([response(blocks, "tool_use"), response([{"type": "text", "text": "仅整理已有资料，剩余查询未完成。"}])])
        with tempfile.TemporaryDirectory() as directory:
            async with service.serving(directory, model) as client:
                accepted = (await client.post("/api/turns", json={"input": "杭州旅行"})).json()
                try:
                    await service.wait_started("web", 10)
                    service.unblock(*[f"w{i}" for i in range(1, 140)], "m0", "m1")
                    await service.wait_started("web", 127)
                    self.assertEqual(len(model.requests), 1)
                    self.assertEqual([s[1] for s in service.started if s[0] == "map"], ["m0"])
                    self.assertNotIn("w127", [s[1] for s in service.started])
                    live = (await client.get("/api/sessions/" + accepted["session_id"])).json()
                    self.assertEqual([t["reason"] for t in live["tool_calls"][-2:]], ["budget", "budget"])
                finally:
                    service.release_all()
                saved = await finished(client, accepted)
                self.assertEqual(len(service.started), 128)
                self.assertEqual(saved["turns"][0]["reason"], "budget")
                self.assertEqual(saved["turns"][0]["tool_error_count"], 1)
                self.assertNotIn("tools", model.requests[1])
                self.assertEqual(saved["requests"][-1]["purpose"], "travel_final")
                self.assertEqual([r["tool_use_id"] for r in model.requests[1]["messages"][-1]["content"]], [b["id"] for b in blocks])

    async def test_both_providers_paused_still_return_complete_protocol_and_available_status(self):
        service = ControlledServices(interval=0.05)
        service.replies_by_id.update(m0=401, w0=429)
        service.unblock("m0", "w0")
        model = ModelService([response([map_block(0), web_block(0), map_block(1), web_block(1)], "tool_use"),
            response([{"type": "text", "text": "地图和网页均暂停，没有取得可靠资料。"}])])
        with tempfile.TemporaryDirectory() as directory:
            async with service.serving(directory, model) as client:
                accepted = (await client.post("/api/turns", json={"input": "获取杭州资料"})).json()
                saved = await finished(client, accepted)
                self.assertEqual([t["status"] for t in saved["tool_calls"]], ["failed", "failed", "not_executed", "not_executed"])
                self.assertEqual(saved["turns"][0]["query_materials"], [])
                self.assertEqual(len(service.started), 2)
                self.assertIn("暂停地图查询", model.requests[1]["system"])
                self.assertIn("暂停资料查询", model.requests[1]["system"])
                self.assertEqual([r["tool_use_id"] for r in model.requests[1]["messages"][-1]["content"]], ["m0", "w0", "m1", "w1"])
                self.assertTrue((await client.get("/api/state")).json()["accepting"])

    async def test_running_state_storage_failure_prevents_dispatch_and_drains_inflight(self):
        from test_storage import fail_event
        service = ControlledServices(interval=0.4)
        model = ModelService([response([map_block(0), *[web_block(i) for i in range(12)]], "tool_use")])
        with tempfile.TemporaryDirectory() as directory:
            async with service.serving(directory, model) as client:
                accepted = (await client.post("/api/turns", json={"input": "杭州旅行"})).json()
                try:
                    await service.wait_started("web", 1)
                    fail_event(directory, "tool.running")
                    for _ in range(100):
                        state = (await client.get("/api/state")).json()
                        if not state["accepting"]:
                            break
                        await asyncio.sleep(0.01)
                    self.assertEqual(state["unsaved_fact"]["kind"], "tool.running")
                    self.assertEqual(state["active_turn_id"], accepted["turn_id"])
                    self.assertEqual(service.inflight, {"map": 1, "web": 1})
                finally:
                    service.release_all()
                saved = await finished(client, accepted)
                self.assertEqual(len(service.started), 2)
                self.assertEqual(saved["tool_calls"][0]["status"], "completed")
                self.assertEqual(saved["tool_calls"][1]["status"], "completed")
                self.assertEqual(saved["tool_calls"][2]["status"], "waiting")
                self.assertFalse(saved["tool_calls"][2]["call_started"])
                self.assertEqual(len(model.requests), 1)

    async def test_web_quota_after_map_pause_does_not_claim_map_is_available(self):
        service = ControlledServices(interval=0)
        service.replies_by_id.update(m0=401, w0={"code": "quota_cap_reached"})
        service.unblock("m0", "w0")
        model = ModelService([response([map_block(0), web_block(0)], "tool_use"),
            response([{"type": "text", "text": "地图和网页均暂停，资料缺失。"}])])
        with tempfile.TemporaryDirectory() as directory:
            async with service.serving(directory, model) as client:
                accepted = (await client.post("/api/turns", json={"input": "获取杭州资料"})).json()
                saved = await finished(client, accepted)
                results = model.requests[1]["messages"][-1]["content"]
                self.assertNotRegex(str(results), r"可继续(?:使用)?地图")
                self.assertIn("暂停地图查询", model.requests[1]["system"])
                self.assertIn("暂停资料查询", model.requests[1]["system"])
                self.assertEqual(saved["turns"][0]["query_materials"], [])
