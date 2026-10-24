"""显式测试支撑：只装配公开能力、可控外部响应或指定故障。"""
from contextlib import asynccontextmanager
from datetime import datetime
from typing import AsyncIterator, cast
import asyncio
import unittest
import httpx
import json
from anthropic import AsyncAnthropic
from mcp import Client
from travel_app.retrieval import Retrieval
from travel_app.sessions import Runtime, create_app
from service_fixtures import MapService


def context_from_request(request):
    return json.loads(request["system"].split("<travel_date_context>")[1].split("</travel_date_context>")[0])


class HistoryHarness(unittest.TestCase):
    @asynccontextmanager
    async def service(self, directory, model, maps=None):
        @asynccontextmanager
        async def resources():
            tools = Retrieval(cast(Client, maps or MapService()))
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


class DateSessionHarness(HistoryHarness):
    moment = datetime.fromisoformat("2026-10-09T23:59:59+08:00")
    finish = HistoryHarness.finish

    @asynccontextmanager
    async def service(self, directory, model, maps=None) -> AsyncIterator[httpx.AsyncClient]:
        @asynccontextmanager
        async def resources():
            tools = Retrieval(cast(Client, maps or MapService()))
            await tools.discover()
            yield Runtime(cast(AsyncAnthropic, model), tools, "test-model")
        app = create_app(directory, resources=resources, clock=lambda: self.moment)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                yield client


__all__ = ['context_from_request', 'HistoryHarness', 'DateSessionHarness']
