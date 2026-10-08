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
async def serving_tools(directory, model, maps, timeout=1, released=None):
    @asynccontextmanager
    async def resources():
        tools=AmapTools(cast(Client,maps),api_key="known-secret",tool_timeout=timeout)
        await tools.discover()
        try:
            yield Runtime(cast(AsyncAnthropic,model),tools,"test-model")
        finally:
            if released: released.set()
    sock=socket.socket();sock.bind(("127.0.0.1",0))
    server=uvicorn.Server(uvicorn.Config(create_app(directory,resources=resources,request_shutdown=lambda: setattr(server,"should_exit",True)),log_level="critical",access_log=False,timeout_graceful_shutdown=1))
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

    async def test_budget_keeps_proposed_parameters_and_no_invented_service_result(self):
        model=ModelService([tool_reply(65)])
        class QuickMaps(MapService):
            calls=0
            async def call_tool(self,name,arguments):
                self.calls+=1
                return await super().call_tool(name,arguments)
        maps=QuickMaps()
        with tempfile.TemporaryDirectory() as directory:
            async with serving_tools(directory,model,maps) as client:
                accepted=(await client.post("/api/turns",json={"input":"预算查询"})).json()
                snapshot=await asyncio.wait_for(wait_finished(client,accepted["session_id"]),30)
                self.assertEqual(maps.calls,64)
                skipped=snapshot["tool_calls"][-1]
                self.assertEqual(skipped["status"],"not_executed")
                self.assertEqual(skipped["reason"],"budget")
                self.assertFalse(skipped["call_started"])
                self.assertIsNone(skipped["call_duration_ms"])
                self.assertNotIn("service_payload_id",skipped)
                self.assertIn("arguments_payload_id",skipped)
                self.assertIn("result_payload_id",skipped)
                self.assertEqual(snapshot["turns"][0]["tool_error_count"],0)

    async def test_failures_remain_distinct_from_sdk_flag_and_preserve_measured_times(self):
        from mcp.types import CallToolResult,TextContent
        for fault in ('ordinary','arguments','timeout','auth','quota'):
            with self.subTest(fault=fault):
                class FaultMaps(MapService):
                    calls=0
                    async def call_tool(self,name,arguments):
                        self.calls+=1
                        if self.calls==1: return await super().call_tool(name,arguments)
                        if self.calls==2:
                            if fault=='arguments': raise ValueError('参数 known-secret 错误')
                            if fault=='timeout': await asyncio.sleep(0.2)
                            code={'ordinary':'10099','auth':'10001','quota':'10003'}[fault]
                            return CallToolResult(content=[TextContent(type='text',text='完整失败 known-secret')],
                                structured_content={'status':'0','infocode':code,'Authorization':'unknown-secret'},is_error=False)
                        return await super().call_tool(name,arguments)
                maps=FaultMaps()
                model=ModelService([tool_reply(3),tool_reply(1) if fault in ('auth','quota') else response([{'type':'text','text':'保留西湖成功结果，其余待核实'}])])
                with tempfile.TemporaryDirectory() as directory:
                    async with serving_tools(directory,model,maps,timeout=0.05) as client:
                        accepted=(await client.post('/api/turns',json={'input':'混合查询'})).json()
                        snapshot=await asyncio.wait_for(wait_finished(client,accepted['session_id']),3)
                        good,bad,last=snapshot['tool_calls']
                        self.assertEqual(good['status'],'completed')
                        self.assertEqual(bad['status'],'failed')
                        self.assertTrue(bad['call_started'])
                        self.assertGreaterEqual(bad['call_duration_ms'],0)
                        self.assertEqual(bad['failure_category'],{'ordinary':'business'}.get(fault,fault))
                        self.assertEqual(snapshot['turns'][0]['tool_error_count'],1)
                        state=(await client.get('/api/state')).json()
                        if fault in ('auth','quota'):
                            self.assertEqual(last['status'],'not_executed')
                            self.assertFalse(last['call_started'])
                            self.assertIsNone(last['call_duration_ms'])
                            self.assertNotIn('service_payload_id',last)
                            self.assertTrue(state['map_paused'])
                            self.assertEqual(state['map_pause_reason'],fault)
                            self.assertEqual(snapshot['turns'][0]['status'],'terminated')
                            self.assertEqual(maps.calls,2)
                            again=(await client.post('/api/turns',json={'input':'换个会话查询'})).json()
                            await asyncio.wait_for(wait_finished(client,again['session_id']),3)
                            self.assertTrue((await client.get('/api/state')).json()['map_paused'])
                            self.assertEqual(maps.calls,2)
                        else:
                            self.assertEqual(last['status'],'completed')
                            self.assertFalse(state['map_paused'])
                            self.assertEqual(snapshot['turns'][0]['status'],'completed')
                        if fault in ('ordinary','auth','quota'):
                            self.assertFalse(bad['sdk_is_error'])
                            raw=(await client.get('/api/payloads/'+bad['service_payload_id'])).json()['content']
                            self.assertEqual(raw['structured_content']['status'],'0')
                            self.assertNotIn('unknown-secret',str(raw))
                        else:
                            self.assertNotIn('service_payload_id',bad)
                            err=(await client.get('/api/payloads/'+bad['error_payload_id'])).json()['content']
                            self.assertNotIn('known-secret',str(err))

    async def test_unrecoverable_connection_finishes_saved_trace_and_releases_runtime(self):
        released=asyncio.Event()
        class DisconnectedMaps(MapService):
            async def call_tool(self,name,arguments):
                raise ConnectionError("连接中断 known-secret")
        with tempfile.TemporaryDirectory() as directory:
            async with serving_tools(directory,ModelService([tool_reply()]),DisconnectedMaps(),released=released) as client:
                accepted=(await client.post('/api/turns',json={'input':'连接查询'})).json()
                snapshot=await asyncio.wait_for(wait_finished(client,accepted['session_id']),2)
                self.assertEqual(snapshot['turns'][0]['reason'],'map_paused')
                self.assertEqual([call['status'] for call in snapshot['tool_calls']],['failed','not_executed'])
                await asyncio.wait_for(released.wait(),2)
            async with serving_tools(directory,ModelService([]),MapService()) as restored:
                saved=(await restored.get('/api/sessions/'+accepted['session_id'])).json()
                self.assertEqual(saved['tool_calls'],snapshot['tool_calls'])
                self.assertEqual(saved['turns'][0]['status'],'terminated')
