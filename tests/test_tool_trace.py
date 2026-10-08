"""从公开 HTTP/SSE 验证串行地图调用真实边界。"""
import asyncio
import json
import socket
import tempfile
import unittest
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import cast

import httpx
import uvicorn
from anthropic import AsyncAnthropic
from mcp import Client

from amap_mcp import AmapTools
from test_agent import MapService, ModelService, response
from test_trace import wait_finished
from web import Runtime, create_app


@asynccontextmanager
async def serving_tools(directory, model, maps, timeout=1):
    @asynccontextmanager
    async def resources():
        tools=AmapTools(cast(Client,maps),api_key="known-secret",tool_timeout=timeout)
        await tools.discover()
        yield Runtime(cast(AsyncAnthropic,model),tools,"test-model")
    sock=socket.socket();sock.bind(("127.0.0.1",0))
    server=uvicorn.Server(uvicorn.Config(create_app(directory,resources=resources),log_level="critical",access_log=False))
    task=asyncio.create_task(server.serve(sockets=[sock]))
    try:
        while not server.started and not task.done(): await asyncio.sleep(0)
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{sock.getsockname()[1]}") as client:
            yield client
    finally:
        server.should_exit=True
        await task
        sock.close()


def tool_reply(count=2):
    return response([{"type":"tool_use","id":f"lake-{i}","name":"maps_text_search",
        "input":{"keywords":"西湖","city":"杭州"}} for i in range(count)],"tool_use")


class ToolTraceTests(unittest.IsolatedAsyncioTestCase):
    async def test_pending_running_and_complete_sources_preserve_same_name_identity(self):
        entered,release=asyncio.Event(),asyncio.Event()
        class WaitingMaps(MapService):
            async def call_tool(self,name,arguments):
                entered.set();await release.wait()
                result=await super().call_tool(name,arguments)
                result.meta={"source":"高德","Authorization":"unknown-secret"}
                return result
        with tempfile.TemporaryDirectory() as directory:
            model=ModelService([tool_reply(),response([{"type":"text","text":"西湖已核实"}])])
            async with serving_tools(directory,model,WaitingMaps()) as client:
                accepted=(await client.post("/api/turns",json={"input":"查询西湖"})).json()
                await entered.wait()
                try:
                    snapshot=(await client.get("/api/sessions/"+accepted["session_id"])).json()
                    one,two=snapshot["tool_calls"]
                    self.assertEqual([one["status"],two["status"]],["running","pending"])
                    self.assertTrue(one["call_started"])
                    self.assertFalse(two["call_started"])
                    self.assertIsNone(one["call_duration_ms"])
                    self.assertEqual([one["tool_use_id"],two["tool_use_id"]],["lake-0","lake-1"])
                    self.assertEqual(one["request_id"],snapshot["requests"][0]["request_id"])
                    self.assertNotEqual(one["tool_call_id"],two["tool_call_id"])
                finally: release.set()
                snapshot=await asyncio.wait_for(wait_finished(client,accepted["session_id"]),3)
                self.assertEqual(len(snapshot["turns"]),1)
                self.assertEqual(len(snapshot["requests"]),2)
                for call in snapshot["tool_calls"]:
                    self.assertEqual(call["status"],"completed")
                    self.assertGreaterEqual(call["call_duration_ms"],0)
                    self.assertGreaterEqual(call["wait_duration_ms"],0)
                    self.assertGreaterEqual(call["total_duration_ms"],call["call_duration_ms"])
                    async def payload(field):
                        return (await client.get("/api/payloads/"+call[field])).json()["content"]
                    self.assertEqual(await payload("arguments_payload_id"),{"keywords":"西湖","city":"杭州"})
                    raw=await payload("service_payload_id")
                    self.assertEqual(raw["meta"]["source"],"高德")
                    self.assertFalse(raw["is_error"])
                    self.assertNotIn("unknown-secret",str(raw))
                    fill=await payload("result_payload_id")
                    self.assertEqual(fill["tool_use_id"],call["tool_use_id"])
                    self.assertFalse(fill["is_error"])
                self.assertIn("tool.waiting",[event["kind"] for event in snapshot["events"]])
                self.assertEqual(snapshot["turns"][0]["tool_error_count"],0)
