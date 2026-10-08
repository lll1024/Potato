"""从公开提交边界验证多页安全接受，不替换查询循环或持久化。"""

import asyncio
import tempfile
import unittest
from contextlib import asynccontextmanager
from typing import cast

import httpx
from anthropic import AsyncAnthropic
from mcp import Client

from amap_mcp import AmapTools
from test_agent import MapService, ModelService, response
from web import Runtime, create_app


class SubmissionTests(unittest.IsolatedAsyncioTestCase):
    async def test_lost_response_can_retry_and_query_original_acceptance_while_busy(self):
        entered, release = asyncio.Event(), asyncio.Event()
        class WaitingModel(ModelService):
            async def create(self, **kwargs):
                entered.set()
                await release.wait()
                return await super().create(**kwargs)
        model = WaitingModel([response([{"type":"text","text":"西湖在杭州。"}])])
        @asynccontextmanager
        async def resources():
            tools = AmapTools(cast(Client, MapService()))
            await tools.discover()
            yield Runtime(cast(AsyncAnthropic, model), tools, "test-model")
        with tempfile.TemporaryDirectory() as directory:
            app = create_app(directory, resources=resources)
            async with app.router.lifespan_context(app):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as page:
                    body = {"submission_id":"lost-response", "input":"查询西湖"}
                    accepted = await page.post("/api/turns", json=body)
                    self.assertEqual(accepted.status_code, 202)
                    await entered.wait()
                    try:
                        retried = await page.post("/api/turns", json=body)
                        self.assertEqual(retried.status_code, 202)
                        self.assertEqual(retried.json(), accepted.json())
                        queried = await page.get("/api/submissions/lost-response")
                        self.assertEqual(queried.json(), accepted.json())
                        self.assertEqual(len((await page.get("/api/sessions")).json()["sessions"]), 1)
                    finally:
                        release.set()
            self.assertEqual(len(model.requests), 1)

    async def test_same_identity_with_different_input_or_target_is_explicit_conflict(self):
        entered, release = asyncio.Event(), asyncio.Event()
        class WaitingModel(ModelService):
            async def create(self, **kwargs):
                entered.set()
                await release.wait()
                return await super().create(**kwargs)
        model = WaitingModel([response([{"type":"text","text":"杭州。"}])])
        @asynccontextmanager
        async def resources():
            tools = AmapTools(cast(Client, MapService()))
            await tools.discover()
            yield Runtime(cast(AsyncAnthropic, model), tools, "test-model")
        with tempfile.TemporaryDirectory() as directory:
            app = create_app(directory, resources=resources)
            async with app.router.lifespan_context(app):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as page:
                    body = {"submission_id":"one-question", "input":"城市杭州 token=first-secret"}
                    accepted = await page.post("/api/turns", json=body)
                    await entered.wait()
                    try:
                        changed_input = await page.post("/api/turns", json={**body,"input":"城市杭州 token=second-secret"})
                        self.assertEqual(changed_input.status_code, 409)
                        self.assertEqual(changed_input.json()["detail"]["code"], "SUBMISSION_CONFLICT")
                        changed_target = await page.post("/api/turns", json={**body,"session_id":accepted.json()["session_id"]})
                        self.assertEqual(changed_target.status_code, 409)
                        self.assertEqual(changed_target.json()["detail"]["code"], "SUBMISSION_CONFLICT")
                        self.assertNotIn("secret", str(changed_input.json()))
                    finally:
                        release.set()

    async def test_two_pages_compete_for_one_global_turn_without_queueing(self):
        entered, release = asyncio.Event(), asyncio.Event()
        class WaitingModel(ModelService):
            async def create(self, **kwargs):
                entered.set()
                await release.wait()
                return await super().create(**kwargs)
        model = WaitingModel([response([{"type":"text","text":"第一轮结果。"}])])
        @asynccontextmanager
        async def resources():
            tools = AmapTools(cast(Client, MapService()))
            await tools.discover()
            yield Runtime(cast(AsyncAnthropic, model), tools, "test-model")
        with tempfile.TemporaryDirectory() as directory:
            app = create_app(directory, resources=resources)
            async with app.router.lifespan_context(app):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as page:
                    replies = await asyncio.gather(*[
                        page.post("/api/turns", json={"submission_id":f"page-{i}","input":f"第{i}个问题"})
                        for i in range(2)
                    ])
                    await entered.wait()
                    try:
                        self.assertEqual(sorted(reply.status_code for reply in replies), [202,409])
                        accepted = next(reply.json() for reply in replies if reply.status_code == 202)
                        busy = next(reply.json() for reply in replies if reply.status_code == 409)
                        self.assertEqual(busy["detail"]["code"], "BUSY")
                        state = (await page.get("/api/state")).json()
                        self.assertEqual(state["active_turn_id"], accepted["turn_id"])
                        self.assertEqual(state["active_session_id"], accepted["session_id"])
                        self.assertFalse(state["stopping"])
                        self.assertEqual(len((await page.get("/api/sessions")).json()["sessions"]), 1)
                    finally:
                        release.set()
            self.assertEqual(len(model.requests), 1)

    async def test_completed_submission_stays_queryable_and_is_not_replayed_after_restart(self):
        model = ModelService([response([{"type":"text","text":"西湖在杭州。"}])])
        @asynccontextmanager
        async def resources():
            tools = AmapTools(cast(Client, MapService()))
            await tools.discover()
            yield Runtime(cast(AsyncAnthropic, model), tools, "test-model")
        with tempfile.TemporaryDirectory() as directory:
            body = {"submission_id":"finished-question","input":"查询西湖"}
            app = create_app(directory, resources=resources)
            async with app.router.lifespan_context(app):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as page:
                    accepted = (await page.post("/api/turns", json=body)).json()
                    async with asyncio.timeout(1):
                        while (await page.get("/api/state")).json()["active_turn_id"]:
                            await asyncio.sleep(0)
                    retried = await page.post("/api/turns", json=body)
                    self.assertEqual(retried.json(), accepted)
            restored = create_app(directory, resources=resources)
            async with restored.router.lifespan_context(restored):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=restored), base_url="http://test") as page:
                    self.assertEqual((await page.post("/api/turns", json=body)).json(), accepted)
                    self.assertEqual((await page.get("/api/submissions/finished-question")).json(), accepted)
                    unknown = await page.get("/api/submissions/never-accepted")
                    self.assertEqual(unknown.status_code,404)
                    self.assertEqual(unknown.json()["detail"]["code"],"SUBMISSION_NOT_FOUND")
                    self.assertEqual(len((await page.get("/api/sessions")).json()["sessions"]),1)
            self.assertEqual(len(model.requests),1)

    async def test_failed_acceptance_is_atomic_and_has_safe_storage_error(self):
        import sqlite3
        from pathlib import Path
        model = ModelService([response([{"type":"text","text":"不应请求。"}])])
        @asynccontextmanager
        async def resources():
            tools = AmapTools(cast(Client, MapService()))
            await tools.discover()
            yield Runtime(cast(AsyncAnthropic, model), tools, "test-model")
        with tempfile.TemporaryDirectory() as directory:
            app = create_app(directory, resources=resources)
            async with app.router.lifespan_context(app):
                with sqlite3.connect(Path(directory)/"travel.sqlite3") as database:
                    database.execute("CREATE TRIGGER fail_submission BEFORE INSERT ON submissions BEGIN SELECT RAISE(ABORT,'password=private-storage-secret'); END")
                database.close()
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as page:
                    rejected = await page.post("/api/turns", json={"submission_id":"uncommitted-question","input":"杭州地点"})
                    self.assertEqual(rejected.status_code,503)
                    self.assertEqual(rejected.json()["detail"]["code"],"STORAGE_FAILURE")
                    self.assertNotIn("private-storage-secret",str(rejected.json()))
                    self.assertEqual((await page.get("/api/submissions/uncommitted-question")).status_code,404)
                    self.assertEqual((await page.get("/api/sessions")).json()["sessions"],[])
                    state = (await page.get("/api/state")).json()
                    self.assertIsNone(state["active_turn_id"])
                    self.assertFalse(state["accepting"])
                    self.assertEqual(state["service_status"],"unavailable")
            self.assertEqual(model.requests,[])

    async def test_invalid_submission_reports_safe_validation_error(self):
        model = ModelService([])
        @asynccontextmanager
        async def resources():
            tools = AmapTools(cast(Client, MapService()))
            await tools.discover()
            yield Runtime(cast(AsyncAnthropic, model), tools, "test-model")
        with tempfile.TemporaryDirectory() as directory:
            app = create_app(directory, resources=resources)
            async with app.router.lifespan_context(app):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as page:
                    rejected = await page.post("/api/turns", json={"submission_id":"password=validation-secret"*10,"input":"杭州"})
                    self.assertEqual(rejected.status_code,422)
                    self.assertNotIn("validation-secret",str(rejected.json()))
                    self.assertEqual(rejected.json()["detail"]["code"],"INVALID_INPUT")
            self.assertEqual(model.requests,[])

    async def test_deleted_submission_is_only_a_processed_identity_and_never_recreates_history(self):
        model = ModelService([response([{"type":"text","text":"已经查询西湖。"}])])
        @asynccontextmanager
        async def resources():
            tools = AmapTools(cast(Client, MapService()))
            await tools.discover()
            yield Runtime(cast(AsyncAnthropic, model), tools, "test-model")
        with tempfile.TemporaryDirectory() as directory:
            app = create_app(directory, resources=resources)
            body = {"submission_id":"deleted-question","input":"删除后不能重建的西湖输入"}
            async with app.router.lifespan_context(app):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as page:
                    accepted = (await page.post("/api/turns",json=body)).json()
                    async with asyncio.timeout(1):
                        while (await page.get("/api/state")).json()["active_turn_id"]:
                            await asyncio.sleep(0)
                    self.assertEqual((await page.delete("/api/sessions/"+accepted["session_id"])).status_code,200)
                    queried = await page.get("/api/submissions/deleted-question")
                    retried = await page.post("/api/turns",json=body)
                    for reply in (queried,retried):
                        self.assertEqual(reply.status_code,410)
                        self.assertEqual(reply.json()["detail"]["code"],"SUBMISSION_DELETED")
                        self.assertNotIn(body["input"],str(reply.json()))
                        self.assertNotIn(accepted["session_id"],str(reply.json()))
                    self.assertEqual((await page.get("/api/sessions")).json()["sessions"],[])
            restored = create_app(directory, resources=resources)
            async with restored.router.lifespan_context(restored):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=restored), base_url="http://test") as page:
                    self.assertEqual((await page.post("/api/turns",json=body)).status_code,410)
                    self.assertEqual((await page.get("/api/sessions")).json()["sessions"],[])
            self.assertEqual(len(model.requests),1)

    async def test_invalid_history_title_and_target_do_not_echo_original_payload(self):
        model = ModelService([])
        @asynccontextmanager
        async def resources():
            tools = AmapTools(cast(Client, MapService()))
            await tools.discover()
            yield Runtime(cast(AsyncAnthropic, model), tools, "test-model")
        with tempfile.TemporaryDirectory() as directory:
            app = create_app(directory, resources=resources)
            async with app.router.lifespan_context(app):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as page:
                    invalid_title = await page.patch("/api/sessions/unknown",json={"title":"token=title-secret"*10})
                    invalid_target = await page.post("/api/turns",json={"input":"杭州","session_id":{"token":"target-secret"}})
                    for reply in (invalid_title,invalid_target):
                        self.assertEqual(reply.status_code,422)
                        self.assertEqual(reply.json()["detail"]["code"],"INVALID_INPUT")
                        self.assertNotIn("secret",str(reply.json()))
            self.assertEqual(model.requests,[])
