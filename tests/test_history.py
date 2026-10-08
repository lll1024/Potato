"""通过公开 HTTP 验证助手会话历史与完整协议续聊。"""

import asyncio
from contextlib import asynccontextmanager
import tempfile
from typing import cast
import unittest

import httpx
from anthropic import AsyncAnthropic, AnthropicError
from mcp import Client

from amap_mcp import AmapTools
from test_agent import MapService, ModelService, response
from web import Runtime, create_app


class HistoryTests(unittest.IsolatedAsyncioTestCase):
    @asynccontextmanager
    async def service(self, directory, model, maps=None):
        @asynccontextmanager
        async def resources():
            tools = AmapTools(cast(Client, maps or MapService()))
            await tools.discover()
            yield Runtime(cast(AsyncAnthropic, model), tools, "test-model")
        app = create_app(directory, resources=resources)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                yield client

    async def finish(self, client, text, session_id=None):
        body = {"input": text}
        if session_id:
            body["session_id"] = session_id
        accepted = await client.post("/api/turns", json=body)
        self.assertEqual(accepted.status_code, 202)
        identity = accepted.json()
        for _ in range(100):
            snapshot = (await client.get("/api/sessions/" + identity["session_id"])).json()
            if any(turn["turn_id"] == identity["turn_id"] and turn["status"] != "running" for turn in snapshot["turns"]):
                return identity, snapshot
            await asyncio.sleep(.01)
        self.fail("对话轮次未结束")

    async def test_restart_continues_complete_protocol_context_without_replaying_history(self):
        model = ModelService([
            response([{"type": "tool_use", "id": "west-lake", "name": "maps_text_search", "input": {"keywords": "西湖", "city": "杭州"}}], "tool_use"),
            response([{"type": "text", "text": "西湖位于杭州。"}]),
            response([{"type": "text", "text": "仍在杭州规划旅行行程。"}]),
        ])
        with tempfile.TemporaryDirectory() as directory:
            async with self.service(directory, model) as client:
                first, saved = await self.finish(client, "查询杭州西湖")
                history = saved["turns"][0]["messages"]
            async with self.service(directory, model) as client:
                self.assertEqual((await client.get("/api/sessions/" + first["session_id"])).json(), saved)
                self.assertEqual(len(model.requests), 2)
                continued, snapshot = await self.finish(client, "那里适合玩多久？", first["session_id"])
                self.assertEqual(continued["session_id"], first["session_id"])
                self.assertNotEqual(continued["turn_id"], first["turn_id"])
                self.assertEqual([turn["ordinal"] for turn in snapshot["turns"]], [1, 2])
                self.assertEqual(model.requests[2]["messages"], history + [{"role": "user", "content": "那里适合玩多久？"}])
                self.assertEqual(history[1]["content"][0]["id"], "west-lake")
                self.assertEqual(history[2]["content"][0]["tool_use_id"], "west-lake")

    async def test_failed_round_can_continue_and_other_sessions_keep_separate_context(self):
        class OnceFailedModel(ModelService):
            async def create(self, **kwargs):
                if not self.requests:
                    self.requests.append(kwargs)
                    raise AnthropicError("模拟模型失败")
                return await super().create(**kwargs)
        model = OnceFailedModel([
            response([{"type": "text", "text": "继续杭州旅行行程。"}]),
            response([{"type": "text", "text": "上海旅行行程。"}]),
        ])
        with tempfile.TemporaryDirectory() as directory:
            async with self.service(directory, model) as client:
                failed, snapshot = await self.finish(client, "杭州两日游")
                self.assertEqual(snapshot["turns"][0]["status"], "failed")
                saved_context = snapshot["turns"][0]["messages"]
            async with self.service(directory, model) as client:
                await self.finish(client, "换成三天", failed["session_id"])
                self.assertEqual(model.requests[1]["messages"], saved_context + [{"role": "user", "content": "换成三天"}])
                await self.finish(client, "上海一日游")
                self.assertEqual(model.requests[2]["messages"], [{"role": "user", "content": "上海一日游"}])

    async def test_history_and_rounds_are_paginated_by_activity_without_external_queries(self):
        model = ModelService([response([{"type": "text", "text": "答复"}]) for _ in range(5)])
        with tempfile.TemporaryDirectory() as directory:
            async with self.service(directory, model) as client:
                first, _ = await self.finish(client, "最初的杭州会话")
                second, _ = await self.finish(client, "上海会话")
                third, _ = await self.finish(client, "北京会话")
                await self.finish(client, "杭州续问", first["session_id"])
                await self.finish(client, "杭州再问", first["session_id"])
                page = (await client.get("/api/sessions?limit=2")).json()
                self.assertEqual([item["session_id"] for item in page["sessions"]], [first["session_id"], third["session_id"]])
                self.assertIsInstance(page["next_cursor"], str)
                self.assertEqual(page["sessions"][0]["status"], "completed")
                rest = (await client.get("/api/sessions?limit=2&cursor=" + page["next_cursor"])).json()
                self.assertEqual([item["session_id"] for item in rest["sessions"]], [second["session_id"]])
                self.assertIsNone(rest["next_cursor"])
                rounds = (await client.get("/api/sessions/" + first["session_id"] + "?limit=2")).json()
                self.assertEqual([item["ordinal"] for item in rounds["turns"]], [2, 3])
                self.assertEqual(rounds["next_before"], 2)
                last = (await client.get("/api/sessions/" + first["session_id"] + "?limit=2&before=2")).json()
                self.assertEqual([item["ordinal"] for item in last["turns"]], [1])
                self.assertIsNone(last["next_before"])
                self.assertEqual(len(model.requests), 5)

    async def test_rename_changes_only_display_title_and_not_context_or_old_events(self):
        model = ModelService([response([{"type": "text", "text": "杭州西湖"}]), response([{"type": "text", "text": "继续"}])])
        with tempfile.TemporaryDirectory() as directory:
            async with self.service(directory, model) as client:
                first, before = await self.finish(client, "杭州地点")
                renamed = await client.patch("/api/sessions/" + first["session_id"], json={"title": "我的杭州旅行行程"})
                self.assertEqual(renamed.status_code, 200)
                after = (await client.get("/api/sessions/" + first["session_id"])).json()
                self.assertEqual(after["session"]["title"], "我的杭州旅行行程")
                self.assertEqual(after["session"]["updated_at"], before["session"]["updated_at"])
                self.assertEqual(after["turns"], before["turns"])
                self.assertEqual(after["events"], before["events"])
                await self.finish(client, "附近餐饮", first["session_id"])
                self.assertEqual(model.requests[1]["messages"], before["turns"][0]["messages"] + [{"role": "user", "content": "附近餐饮"}])

    async def test_delete_removes_history_and_context_but_refuses_active_session(self):
        entered, release = asyncio.Event(), asyncio.Event()
        class WaitingModel(ModelService):
            async def create(self, **kwargs):
                if len(self.requests) == 1:
                    entered.set()
                    await release.wait()
                return await super().create(**kwargs)
        model = WaitingModel([response([{"type": "text", "text": "已保存"}]), response([{"type": "text", "text": "结束"}]), response([{"type": "text", "text": "新上下文"}])])
        with tempfile.TemporaryDirectory() as directory:
            async with self.service(directory, model) as client:
                idle, _ = await self.finish(client, "可删除的旧会话")
                active = (await client.post("/api/turns", json={"input": "运行中的会话"})).json()
                await entered.wait()
                try:
                    rejected = await client.delete("/api/sessions/" + active["session_id"])
                    self.assertEqual(rejected.status_code, 409)
                    removed = await client.delete("/api/sessions/" + idle["session_id"])
                    self.assertEqual(removed.status_code, 200)
                    self.assertEqual((await client.get("/api/sessions/" + idle["session_id"])).status_code, 404)
                    listed = (await client.get("/api/sessions")).json()["sessions"]
                    self.assertEqual([item["session_id"] for item in listed], [active["session_id"]])
                finally:
                    release.set()
            async with self.service(directory, model) as client:
                self.assertEqual((await client.get("/api/sessions/" + idle["session_id"])).status_code, 404)
                self.assertEqual((await client.post("/api/turns", json={"input": "不能恢复已删会话", "session_id": idle["session_id"]})).status_code, 404)
                await self.finish(client, "独立新会话")
                self.assertEqual(model.requests[-1]["messages"], [{"role": "user", "content": "独立新会话"}])

    async def test_activity_cursor_does_not_repeat_items_when_new_activity_moves_history(self):
        model = ModelService([response([{"type": "text", "text": "答复"}]) for _ in range(6)])
        with tempfile.TemporaryDirectory() as directory:
            async with self.service(directory, model) as client:
                first, _ = await self.finish(client, "最早会话")
                second, _ = await self.finish(client, "将被续聊的会话")
                third, _ = await self.finish(client, "第三个会话")
                fourth, _ = await self.finish(client, "第四个会话")
                page = (await client.get("/api/sessions?limit=2")).json()
                self.assertEqual([item["session_id"] for item in page["sessions"]], [fourth["session_id"], third["session_id"]])
                await self.finish(client, "新的活动", second["session_id"])
                await self.finish(client, "新增会话")
                remaining = (await client.get("/api/sessions?limit=2&cursor=" + page["next_cursor"])).json()
                self.assertEqual([item["session_id"] for item in remaining["sessions"]], [first["session_id"]])
                self.assertIsNone(remaining["next_cursor"])
                self.assertEqual((await client.get("/api/sessions?cursor=invalid")).status_code, 422)
