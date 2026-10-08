import asyncio
import tempfile
import sqlite3
from pathlib import Path
import unittest
from contextlib import asynccontextmanager
from typing import cast

import httpx
from anthropic import AsyncAnthropic, AnthropicError
from mcp import Client

from amap_mcp import AmapTools
from test_agent import MapService, ModelService, response
from web import Runtime, create_app


class WebConversationTests(unittest.IsolatedAsyncioTestCase):
    async def test_first_input_runs_real_loop_and_survives_restart(self):
        model = ModelService([
            response([{"type": "tool_use", "id": "place-1", "name": "maps_text_search",
                       "input": {"keywords": "西湖", "city": "杭州"}}], "tool_use"),
            response([{"type": "text", "text": "西湖位于杭州市西湖区。"}]),
        ])
        @asynccontextmanager
        async def resources():
            tools = AmapTools(cast(Client, MapService()))
            await tools.discover()
            yield Runtime(cast(AsyncAnthropic, model), tools, "test-model")

        with tempfile.TemporaryDirectory() as directory:
            app = create_app(directory, resources=resources)
            async with app.router.lifespan_context(app):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                    self.assertEqual((await client.get("/api/sessions")).json()["sessions"], [])
                    accepted = await client.post("/api/turns", json={"input": "查询杭州的西湖"})
                    self.assertEqual(accepted.status_code, 202)
                    identity = accepted.json()
                    for _ in range(100):
                        snapshot = (await client.get("/api/sessions/" + identity["session_id"])).json()
                        if snapshot["turns"][0]["status"] != "running":
                            break
                        await asyncio.sleep(0.01)
                    turn = snapshot["turns"][0]
                    self.assertEqual(turn["status"], "completed")
                    self.assertEqual(turn["input"], "查询杭州的西湖")
                    self.assertEqual(turn["answer"], "西湖位于杭州市西湖区。")
                    self.assertEqual(turn["answer_source"], "model")
                    self.assertEqual(turn["turn_id"], identity["turn_id"])
                    self.assertEqual(snapshot["schema_version"], 1)
                    self.assertEqual(snapshot["session"]["title"], "查询杭州的西湖")
            restored = create_app(directory, resources=resources)
            async with restored.router.lifespan_context(restored):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=restored), base_url="http://test") as client:
                    saved = (await client.get("/api/sessions/" + identity["session_id"])).json()
                    self.assertEqual(saved["turns"][0]["answer"], "西湖位于杭州市西湖区。")
                    self.assertEqual(len(model.requests), 2)

    async def test_active_model_does_not_block_acceptance_and_rejects_another_turn(self):
        entered, release = asyncio.Event(), asyncio.Event()
        class WaitingModel(ModelService):
            async def create(self, **kwargs):
                entered.set()
                await release.wait()
                return await super().create(**kwargs)
        model = WaitingModel([response([{"type":"text","text":"请提供城市。"}])])
        @asynccontextmanager
        async def resources():
            tools = AmapTools(cast(Client, MapService()))
            await tools.discover()
            yield Runtime(cast(AsyncAnthropic, model),tools,"test-model")
        with tempfile.TemporaryDirectory() as directory:
            app = create_app(directory,resources=resources)
            async with app.router.lifespan_context(app):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="http://test") as client:
                    first = await asyncio.wait_for(client.post("/api/turns",json={"input":"查地点"}),timeout=1)
                    self.assertEqual(first.status_code,202)
                    await entered.wait()
                    busy = await client.post("/api/turns",json={"input":"另一轮"})
                    self.assertEqual(busy.status_code,409)
                    state = (await client.get("/api/state")).json()
                    self.assertEqual(state["active_turn_id"],first.json()["turn_id"])
                    self.assertEqual(len((await client.get("/api/sessions")).json()["sessions"]),1)
                    release.set()

    async def test_credential_fields_are_redacted_before_acceptance_and_result(self):
        model = ModelService([response([{"type":"text","text":'{"api_key":"answer-secret","city":"杭州"}'}])])
        @asynccontextmanager
        async def resources():
            tools = AmapTools(cast(Client,MapService()),api_key="known-secret")
            await tools.discover()
            yield Runtime(cast(AsyncAnthropic,model),tools,"test-model")
        with tempfile.TemporaryDirectory() as directory:
            app=create_app(directory,resources=resources)
            async with app.router.lifespan_context(app):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="http://test") as client:
                    accepted=(await client.post("/api/turns",json={"input":'{"token":"input-secret","city":"杭州"} known-secret'})).json()
                    for _ in range(100):
                        snapshot=(await client.get("/api/sessions/"+accepted["session_id"])).json()
                        if snapshot["turns"][0]["status"]!="running":
                            break
                        await asyncio.sleep(0.01)
                    payload=str(snapshot)+str(model.requests)
                    self.assertNotIn("input-secret",payload)
                    self.assertNotIn("answer-secret",payload)
                    self.assertNotIn("known-secret",payload)
                    self.assertIn("杭州",payload)

    async def test_normal_shutdown_waits_for_entered_model_and_prevents_following_tools(self):
        entered,release=asyncio.Event(),asyncio.Event()
        class WaitingModel(ModelService):
            async def create(self, **kwargs):
                entered.set()
                await release.wait()
                return await super().create(**kwargs)
        model=WaitingModel([
            response([{"type":"tool_use","id":"after-exit","name":"maps_text_search",
                       "input":{"keywords":"西湖","city":"杭州"}}],"tool_use"),
            response([{"type":"text","text":"不应调用"}]),
        ])
        map_calls=[]
        class MapMustNotBeCalled(MapService):
            async def call_tool(self,name,arguments):
                map_calls.append((name,arguments))
                return await super().call_tool(name,arguments)
        @asynccontextmanager
        async def resources():
            tools=AmapTools(cast(Client,MapMustNotBeCalled()))
            await tools.discover()
            yield Runtime(cast(AsyncAnthropic,model),tools,"test-model")
        with tempfile.TemporaryDirectory() as directory:
            app=create_app(directory,resources=resources)
            async with app.router.lifespan_context(app):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="http://test") as client:
                    accepted=(await client.post("/api/turns",json={"input":"查杭州西湖"})).json()
                    await entered.wait()
                    asyncio.get_running_loop().call_soon(release.set)
            self.assertEqual(map_calls,[])
            restarted=create_app(directory,resources=resources)
            async with restarted.router.lifespan_context(restarted):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=restarted),base_url="http://test") as client:
                    snapshot=(await client.get("/api/sessions/"+accepted["session_id"])).json()
                    self.assertEqual(snapshot["turns"][0]["status"],"terminated")
                    self.assertEqual(snapshot["turns"][0]["reason"],"service_shutdown")
                    self.assertEqual(snapshot["turns"][0]["answer_source"],"application")
                    self.assertEqual(len(model.requests),1)

    async def test_acceptance_storage_failure_starts_no_external_query(self):
        model=ModelService([response([{"type":"text","text":"不应请求"}])])
        @asynccontextmanager
        async def resources():
            tools=AmapTools(cast(Client,MapService()))
            await tools.discover()
            yield Runtime(cast(AsyncAnthropic,model),tools,"test-model")
        with tempfile.TemporaryDirectory() as directory:
            app=create_app(directory,resources=resources)
            async with app.router.lifespan_context(app):
                # 在真实 SQLite 边界注入写入失败，结果仍只从公开 HTTP 读取。
                with sqlite3.connect(Path(directory)/"travel.sqlite3") as database:
                    database.execute("CREATE TRIGGER fail_accept BEFORE INSERT ON events BEGIN SELECT RAISE(ABORT,'模拟磁盘保存故障'); END")
                database.close()
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="http://test") as client:
                    rejected=await client.post("/api/turns",json={"input":"杭州地点"})
                    self.assertEqual(rejected.status_code,503)
                    self.assertEqual((await client.get("/api/sessions")).json()["sessions"],[])
                    self.assertEqual(model.requests,[])
                    self.assertIsNone((await client.get("/api/state")).json()["active_turn_id"])

    async def test_model_failure_has_application_source_and_real_failed_status(self):
        class FailedModel(ModelService):
            async def create(self, **kwargs):
                raise AnthropicError("模拟模型故障 password=hidden-secret")
        @asynccontextmanager
        async def resources():
            tools=AmapTools(cast(Client,MapService()))
            await tools.discover()
            yield Runtime(cast(AsyncAnthropic,FailedModel([])),tools,"test-model")
        with tempfile.TemporaryDirectory() as directory:
            app=create_app(directory,resources=resources)
            async with app.router.lifespan_context(app):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="http://test") as client:
                    accepted=(await client.post("/api/turns",json={"input":"杭州地点"})).json()
                    for _ in range(100):
                        snapshot=(await client.get("/api/sessions/"+accepted["session_id"])).json()
                        if snapshot["turns"][0]["status"]!="running":
                            break
                        await asyncio.sleep(0.01)
                    turn=snapshot["turns"][0]
                    self.assertEqual(turn["status"],"failed")
                    self.assertEqual(turn["reason"],"model_error")
                    self.assertEqual(turn["answer_source"],"application")
                    self.assertNotIn("hidden-secret",str(snapshot))

    async def test_same_data_directory_refuses_second_service_and_releases_resources(self):
        entered,released=[],[]
        @asynccontextmanager
        async def resources():
            tools=AmapTools(cast(Client,MapService()))
            await tools.discover()
            entered.append(True)
            try:
                yield Runtime(cast(AsyncAnthropic,ModelService([])),tools,"test-model")
            finally:
                released.append(True)
        with tempfile.TemporaryDirectory() as directory:
            first=create_app(directory,resources=resources)
            async with first.router.lifespan_context(first):
                second=create_app(directory,resources=resources)
                with self.assertRaisesRegex(RuntimeError,"已有旅行服务"):
                    async with second.router.lifespan_context(second):
                        self.fail("第二服务不应启动")
                self.assertEqual(len(entered),1)
            self.assertEqual(len(released),1)
            third=create_app(directory,resources=resources)
            async with third.router.lifespan_context(third):
                self.assertEqual(len(entered),2)
