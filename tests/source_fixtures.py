"""显式测试支撑：只装配公开能力、可控外部响应或指定故障。"""
import asyncio
import httpx
import httpx2
import json
from contextlib import asynccontextmanager
from typing import cast
from anthropic import AsyncAnthropic
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.server import MCPServer
from travel_app.retrieval import Retrieval, SourceHTTPClient
from exa_fixtures import source_text
from travel_app.sessions import Runtime, create_app


def call(identity, name, arguments):
    if name == "web_search_exa":
        arguments = {"objective": "优先官方参观规则", **arguments}
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
                                for name in ("web_search_exa", "web_fetch_exa")]}
        elif method == "tools/call":
            self.source_calls.append((payload, asyncio.get_running_loop().time()))
            reply = self.replies.pop(0)
            if reply == "wait":
                self.entered.set()
                await self.release.wait()
                reply = {"results": [{"url": "https://museum.example.test/visit", "text": "官方预约须知"}]}
            if reply == "disconnect":
                raise httpx2.ConnectError("资料调用断连", request=request)
            if reply in ("json-read", "sse-read", "sse-eof", "timeout", "json-invalid", "rpc-invalid", "rpc-wrong-id", "sse-ok"):
                if reply == "timeout":
                    raise httpx2.ReadTimeout("资料读取超时", request=request)
                if reply in ("rpc-wrong-id", "sse-ok"):
                    result = {"content": [{"type": "text", "text": "# 参观须知\nURL: https://museum.example.test/visit\n\n已读官方预约须知"}], "isError": False}
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
            result = {"content": [{"type": "text", "text": source_text(reply, search=payload["params"]["name"] == "web_search_exa")}], "isError": False}
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
            amap = Retrieval(map_client)
            await amap.discover()
            async with SourceHTTPClient(transport=httpx2.MockTransport(self.transport), headers={}) as source_http:
                async with Client(streamable_http_client("http://sources.test/mcp", http_client=source_http), read_timeout_seconds=0.2) as source_client:
                    kwargs = {"call_interval": interval} if interval is not None else {}
                    amap = Retrieval(map_client, source_client=source_client, source_timeout=0.2, source_interval=kwargs.get("call_interval", 0.4))
                    await amap.discover()
                    yield Runtime(cast(AsyncAnthropic, model), amap, "test-model")

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


__all__ = ['call', 'finished', 'SourceService']
