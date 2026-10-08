"""从公开 HTTP/SSE 验证真实查询循环的模型执行轨迹。"""
import asyncio
import json
import socket
import tempfile
import unittest
from contextlib import asynccontextmanager
from typing import cast

import httpx
import httpx2
import uvicorn
from anthropic import AsyncAnthropic
from mcp import Client

from amap_mcp import AmapTools
from test_agent import MapService, ModelService, response
from web import Runtime, create_app


@asynccontextmanager
async def serving(directory, model):
    @asynccontextmanager
    async def resources():
        tools = AmapTools(cast(Client, MapService()), api_key="known-secret")
        await tools.discover()
        yield Runtime(cast(AsyncAnthropic, model), tools, "test-model")
    app = create_app(directory, resources=resources)
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="critical", access_log=False))
    task = asyncio.create_task(server.serve(sockets=[sock]))
    try:
        while not server.started and not task.done():
            await asyncio.sleep(0)
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as client:
            yield client
    finally:
        server.should_exit = True
        await task
        sock.close()


async def wait_finished(client, session_id, turn_id=None):
    async with client.stream("GET", "/api/events?after=0") as stream:
        async for line in stream.aiter_lines():
            if line.startswith("data:"):
                event = json.loads(line[5:])
                if event.get("session_id") == session_id and event["kind"] == "turn.finished" and (turn_id is None or event["turn_id"] == turn_id):
                    return (await client.get("/api/sessions/" + session_id)).json()
    raise AssertionError("没有收到轮次结束事件")


class ModelTraceTests(unittest.IsolatedAsyncioTestCase):
    async def test_model_start_is_saved_and_streamed_before_response(self):
        entered, release = asyncio.Event(), asyncio.Event()
        class WaitingModel(ModelService):
            async def create(self, **kwargs):
                entered.set()
                await release.wait()
                return await super().create(**kwargs)
        model = WaitingModel([response([{"type":"text","text":"请提供城市。"}])])
        with tempfile.TemporaryDirectory() as directory:
            async with serving(directory, model) as client:
                accepted = (await client.post("/api/turns",json={"input":"查地点"})).json()
                await entered.wait()
                try:
                    snapshot = (await client.get("/api/sessions/" + accepted["session_id"])).json()
                    self.assertEqual(len(snapshot["requests"]), 1)
                    request = snapshot["requests"][0]
                    self.assertEqual(request["status"], "running")
                    self.assertEqual(request["turn_id"], accepted["turn_id"])
                    self.assertEqual(request["ordinal"], 1)
                    self.assertIsNone(request["duration_ms"])
                    self.assertEqual(request["usage_state"], "not_completed")
                    async with client.stream("GET", "/api/events?after=0") as stream:
                        self.assertEqual(stream.status_code, 200)
                        async for line in stream.aiter_lines():
                            if line.startswith("data:"):
                                event = json.loads(line[5:])
                                if event["kind"] == "request.started":
                                    self.assertEqual(event["request_id"], request["request_id"])
                                    self.assertNotIn("messages", str(event))
                                    self.assertGreater(event["cursor"], 0)
                                    break
                    payload = (await client.get("/api/payloads/" + request["input_payload_id"])).json()["content"]
                    self.assertEqual(payload["model"], "test-model")
                    self.assertEqual(payload["max_tokens"], 8000)
                    self.assertEqual(payload["messages"], [{"role":"user","content":"查地点"}])
                    self.assertEqual(payload["tools"][0]["name"], "maps_text_search")
                    self.assertIn("旅行助手", payload["system"])
                finally:
                    release.set()
                await asyncio.wait_for(wait_finished(client, accepted["session_id"]), 2)

    async def test_multiple_requests_keep_snapshots_and_actual_response_fields(self):
        from anthropic.types import Message, TextBlock
        first = response([{"type":"tool_use","id":"west-lake","name":"maps_text_search",
                           "input":{"keywords":"西湖","city":"杭州"}}],"tool_use")
        first.usage = type(first.usage).model_validate({"input_tokens":0,"output_tokens":1,"cache_read_input_tokens":0,"cache_creation":{"ephemeral_5m_input_tokens":2,"ephemeral_1h_input_tokens":0}})
        first.usage.__pydantic_fields_set__.add("cache_read_input_tokens")
        first._request_id = "http-model-first"
        final = Message.model_construct(id="service-final",type="message",role="assistant",model="test-model",
                    stop_reason="max_tokens",content=[TextBlock(type="text",text="部分旅行建议 known-secret")])
        model = ModelService([first,final])
        with tempfile.TemporaryDirectory() as directory:
            async with serving(directory, model) as client:
                accepted=(await client.post("/api/turns",json={"input":"查询西湖"})).json()
                snapshot=await asyncio.wait_for(wait_finished(client,accepted["session_id"]),2)
                self.assertEqual(len(snapshot["requests"]),2)
                one,two=sorted(snapshot["requests"],key=lambda item:item["ordinal"])
                self.assertEqual([one["status"],two["status"]],["completed","completed"])
                self.assertNotEqual(one["request_id"],two["request_id"])
                self.assertNotEqual(one["request_id"],"message-test")
                self.assertEqual(one["usage"]["input_tokens"],0)
                self.assertEqual(one["usage"]["cache_read_input_tokens"],0)
                self.assertNotIn("cache_creation_input_tokens",one["usage"])
                self.assertEqual(two["usage_state"],"not_returned")
                self.assertIsNone(two["usage"])
                self.assertEqual(snapshot["usage_summary"]["input_tokens"], {"value":0,"known_count":1,"request_count":2})
                self.assertEqual(snapshot["usage_summary"]["cache_creation.ephemeral_5m_input_tokens"], {"value":2,"known_count":1,"request_count":2})
                self.assertEqual(snapshot["usage_summary"]["cache_creation.ephemeral_1h_input_tokens"], {"value":0,"known_count":1,"request_count":2})
                self.assertEqual(snapshot["usage_summary"]["cache_creation_input_tokens"], {"value":None,"known_count":0,"request_count":2})
                self.assertGreaterEqual(one["duration_ms"],0)
                self.assertIsNotNone(two["finished_at"])
                payloads={}
                for request in [one,two]:
                    for key in ["input_payload_id","response_payload_id"]:
                        payloads[(request["ordinal"],key)]=(await client.get("/api/payloads/"+request[key])).json()["content"]
                self.assertEqual(payloads[(1,"input_payload_id")]["messages"],[{"role":"user","content":"查询西湖"}])
                followup=payloads[(2,"input_payload_id")]["messages"][-1]["content"][0]
                self.assertEqual(followup["tool_use_id"],"west-lake")
                self.assertIn("查到一个地点",followup["content"])
                self.assertEqual(payloads[(1,"response_payload_id")]["_request_id"],"http-model-first")
                self.assertNotIn("usage",payloads[(2,"response_payload_id")])
                self.assertNotIn("stop_sequence",payloads[(2,"response_payload_id")])
                self.assertNotIn("known-secret",str(payloads))
                self.assertEqual(snapshot["turns"][0]["status"],"terminated")
                self.assertEqual(snapshot["turns"][0]["reason"],"output_limit")
            async with serving(directory, ModelService([])) as restored:
                saved=(await restored.get("/api/sessions/"+accepted["session_id"])).json()
                self.assertEqual(saved["requests"],snapshot["requests"])

    async def test_model_failure_keeps_prior_result_and_real_error_details(self):
        from anthropic import APIStatusError
        first=response([{"type":"tool_use","id":"west-lake","name":"maps_text_search",
                        "input":{"keywords":"西湖","city":"杭州"}}],"tool_use")
        class FailedModel(ModelService):
            async def create(self, **kwargs):
                if self.requests:
                    raise APIStatusError("模型额度不足 known-secret",response=httpx2.Response(429,
                        request=httpx2.Request("POST","https://example.test"),headers={"request-id":"http-failure"}),
                        body={"error":{"type":"rate_limit_error","message":"拒绝请求","authorization":"unknown-secret"}})
                return await super().create(**kwargs)
        with tempfile.TemporaryDirectory() as directory:
            async with serving(directory, FailedModel([first])) as client:
                accepted=(await client.post("/api/turns",json={"input":"查询西湖"})).json()
                snapshot=await asyncio.wait_for(wait_finished(client,accepted["session_id"]),2)
                one,two=sorted(snapshot["requests"],key=lambda item:item["ordinal"])
                self.assertEqual([one["status"],two["status"]],["completed","failed"])
                self.assertEqual(two["usage_state"],"not_returned")
                self.assertIsNone(two["response_payload_id"])
                error=(await client.get("/api/payloads/"+two["error_payload_id"])).json()["content"]
                self.assertEqual(error["stage"],"model_call")
                self.assertEqual(error["status_code"],429)
                self.assertEqual(error["request_id"],"http-failure")
                self.assertEqual(error["body"]["error"]["type"],"rate_limit_error")
                self.assertNotIn("known-secret",str(error))
                self.assertNotIn("unknown-secret",str(error))
                self.assertGreaterEqual(two["duration_ms"],0)
                self.assertEqual(snapshot["turns"][0]["status"],"failed")
                self.assertEqual(snapshot["turns"][0]["reason"],"model_error")
                self.assertIn("查到一个地点",snapshot["turns"][0]["answer"])

    async def test_request_save_failure_stops_queries_and_preserves_last_committed_fact(self):
        import sqlite3
        from pathlib import Path
        entered,release=asyncio.Event(),asyncio.Event()
        class WaitingModel(ModelService):
            async def create(self, **kwargs):
                entered.set()
                await release.wait()
                return await super().create(**kwargs)
        model=WaitingModel([response([{"type":"tool_use","id":"west-lake","name":"maps_text_search",
                          "input":{"keywords":"西湖","city":"杭州"}}],"tool_use")])
        with tempfile.TemporaryDirectory() as directory:
            async with serving(directory,model) as client:
                accepted=(await client.post("/api/turns",json={"input":"查询西湖"})).json()
                await entered.wait()
                with sqlite3.connect(Path(directory)/"travel.sqlite3") as database:
                    database.execute("CREATE TRIGGER fail_response BEFORE INSERT ON payloads WHEN NEW.kind='response' BEGIN SELECT RAISE(ABORT,'模拟保存故障'); END")
                database.close()
                release.set()
                async def wait_idle():
                    while True:
                        state=(await client.get("/api/state")).json()
                        if state["active_turn_id"] is None:
                            return state
                state=await asyncio.wait_for(wait_idle(),2)
                self.assertFalse(state["accepting"])
                self.assertIn("未保存",state["storage_error"])
                snapshot=(await client.get("/api/sessions/"+accepted["session_id"])).json()
                self.assertEqual(snapshot["requests"][0]["status"],"running")
                self.assertIsNone(snapshot["requests"][0]["duration_ms"])
                self.assertEqual(snapshot["turns"][0]["status"],"running")
                self.assertNotIn("request.completed",[event["kind"] for event in snapshot["events"]])
                self.assertNotIn("turn.finished",[event["kind"] for event in snapshot["events"]])
                self.assertEqual(len(model.requests),1)
                self.assertEqual((await client.post("/api/turns",json={"input":"再次查询"})).status_code,503)

    async def test_full_response_keeps_content_blocks_and_redacts_credential_fields(self):
        from anthropic.types import Message, TextBlock, ThinkingBlock
        final=Message.model_construct(id="service-extended",type="message",role="assistant",model="test-model",
            stop_reason="end_turn",content=[ThinkingBlock(type="thinking",thinking="已核实的规划依据",signature="opaque-signature"),
                TextBlock(type="text",text="杭州建议")],
            usage={"input_tokens":0,"output_tokens":5,"cache_creation_input_tokens":0},
            service_meta={"Cookie":"cookie-secret","city":"杭州","Authorization":"header-secret"})
        with tempfile.TemporaryDirectory() as directory:
            async with serving(directory,ModelService([final])) as client:
                accepted=(await client.post("/api/turns",json={"input":"查询杭州"})).json()
                snapshot=await asyncio.wait_for(wait_finished(client,accepted["session_id"]),2)
                request=snapshot["requests"][0]
                self.assertEqual(request["status"],"completed")
                parsed=(await client.get("/api/payloads/"+request["response_payload_id"])).json()["content"]
                self.assertEqual(parsed["content"][0]["signature"],"opaque-signature")
                self.assertEqual(parsed["content"][0]["thinking"],"已核实的规划依据")
                self.assertNotIn("cookie-secret",str(parsed))
                self.assertNotIn("header-secret",str(parsed))
                self.assertEqual(parsed["service_meta"]["city"],"杭州")

    async def test_stream_replays_stable_events_after_snapshot_with_small_summaries(self):
        model=ModelService([response([{"type":"text","text":"一"}]),response([{"type":"text","text":"建议"*1000}])])
        with tempfile.TemporaryDirectory() as directory:
            async with serving(directory,model) as client:
                first=(await client.post("/api/turns",json={"input":"第一条旅行需求"})).json()
                await asyncio.wait_for(wait_finished(client,first["session_id"]),2)
                baseline=(await client.get("/api/snapshot")).json()
                second=(await client.post("/api/turns",json={"input":"杭州"*1000})).json()
                await asyncio.wait_for(wait_finished(client,second["session_id"]),2)
                async def replay():
                    events=[]
                    async with client.stream("GET",f'/api/events?after={baseline["cursor"]}&stream_id={baseline["stream_id"]}') as stream:
                        async for line in stream.aiter_lines():
                            if line.startswith("data:"):
                                self.assertLess(len(line),1000)
                                event=json.loads(line[5:]);events.append(event)
                                if event["kind"]=="turn.finished":
                                    return events
                one=await asyncio.wait_for(replay(),2)
                two=await asyncio.wait_for(replay(),2)
                assert one is not None and two is not None
                self.assertEqual(one,two)
                self.assertEqual([event["sequence"] for event in one],[1,2,3,4])
                self.assertEqual([event["cursor"] for event in one],sorted({event["cursor"] for event in one}))
                self.assertTrue(all(event["cursor"]>baseline["cursor"] for event in one))
                self.assertEqual(len({event["event_id"] for event in one}),4)
                self.assertEqual((await client.get("/api/events?after=999999")).status_code,409)
                self.assertEqual((await client.get("/api/events?after=0&stream_id=other-directory")).status_code,409)

    async def test_paged_requests_match_visible_rounds_and_deleted_cursors_are_not_reused(self):
        model=ModelService([response([{"type":"text","text":"首轮"}]),response([{"type":"text","text":"续聊"}]),response([{"type":"text","text":"新会话"}])])
        with tempfile.TemporaryDirectory() as directory:
            async with serving(directory,model) as client:
                first=(await client.post("/api/turns",json={"input":"查询杭州"})).json()
                original=await asyncio.wait_for(wait_finished(client,first["session_id"]),2)
                followup=(await client.post("/api/turns",json={"input":"继续查询","session_id":first["session_id"]})).json()
                await asyncio.wait_for(wait_finished(client,followup["session_id"],followup["turn_id"]),2)
                latest=(await client.get(f'/api/sessions/{first["session_id"]}?limit=1')).json()
                earlier=(await client.get(f'/api/sessions/{first["session_id"]}?limit=1&before={latest["next_before"]}')).json()
                self.assertEqual([item["turn_id"] for item in latest["requests"]],[followup["turn_id"]])
                self.assertEqual([item["turn_id"] for item in earlier["requests"]],[first["turn_id"]])
                self.assertEqual(latest["usage_summary"]["input_tokens"]["request_count"],2)
                self.assertEqual(earlier["requests"][0],original["requests"][0])
                old_cursor=latest["cursor"]
                old_payload=original["requests"][0]["input_payload_id"]
                self.assertEqual((await client.delete("/api/sessions/"+first["session_id"])).status_code,200)
                self.assertEqual((await client.get("/api/payloads/"+old_payload)).status_code,404)
                final=(await client.post("/api/turns",json={"input":"新查询"})).json()
                snapshot=await asyncio.wait_for(wait_finished(client,final["session_id"]),2)
                self.assertTrue(all(event["cursor"]>old_cursor for event in snapshot["events"]))
                self.assertEqual([event["sequence"] for event in snapshot["events"]],[1,2,3,4])
                self.assertNotEqual(snapshot["requests"][0]["request_id"],original["requests"][0]["request_id"])

    async def test_response_processing_failure_retains_received_response_and_usage(self):
        from anthropic.types import Message, TextBlock
        import warnings
        malformed=Message.model_construct(id="service-malformed",type="message",role="assistant",model="test-model",
            stop_reason="end_turn",content=[TextBlock.model_construct(type="text",text=123)],
            usage={"input_tokens":0,"output_tokens":2})
        with tempfile.TemporaryDirectory() as directory:
            async with serving(directory,ModelService([malformed])) as client:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore",UserWarning)
                    accepted=(await client.post("/api/turns",json={"input":"查询杭州"})).json()
                    snapshot=await asyncio.wait_for(wait_finished(client,accepted["session_id"]),2)
                request=snapshot["requests"][0]
                self.assertEqual(request["status"],"failed")
                self.assertIsNotNone(request["response_payload_id"])
                self.assertEqual(request["usage"]["input_tokens"],0)
                error=(await client.get("/api/payloads/"+request["error_payload_id"])).json()["content"]
                self.assertEqual(error["stage"],"response_processing")
                parsed=(await client.get("/api/payloads/"+request["response_payload_id"])).json()["content"]
                self.assertEqual(parsed["content"][0]["text"],123)
                self.assertEqual(snapshot["turns"][0]["status"],"failed")
