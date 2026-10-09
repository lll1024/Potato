import json
import asyncio
import contextlib
import io
import os
import unittest
from typing import cast
from unittest.mock import patch

from anthropic import AsyncAnthropic
import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.server import MCPServer
from mcp.types import CallToolResult, CONNECTION_CLOSED, HEADER_MISMATCH, TextContent
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from agent import agent_loop, run_cli
from amap_http import AmapHTTPClient
from amap_mcp import AmapTools
from test_agent import ModelService, response


class SDKIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_custom_http_client_preserves_successful_sse_results(self):
        server = MCPServer("SSE 查询测试")

        @server.tool(name="maps_text_search")
        def search(keywords: str, city: str) -> dict:
            return {"name": "西湖", "address": "杭州市西湖区"}

        app = server.streamable_http_app(stateless_http=True)
        model = ModelService([
            response([{"type": "tool_use", "id": "sse", "name": "maps_text_search",
                       "input": {"keywords": "西湖", "city": "杭州"}}], "tool_use"),
            response([{"type": "text", "text": "西湖位于杭州市西湖区。"}]),
        ])
        async with app.router.lifespan_context(app):
            async with AmapHTTPClient(transport=httpx2.ASGITransport(app=app)) as http_client:
                async with Client(streamable_http_client("http://127.0.0.1:8000/mcp", http_client=http_client)) as client:
                    tools = AmapTools(client, http_client=http_client)
                    await tools.discover()
                    answer = await agent_loop(
                        [{"role": "user", "content": "查杭州西湖"}],
                        cast(AsyncAnthropic, model), tools, "test-model",
                    )
                    self.assertIsNone(tools.failure)

        self.assertIn("杭州市西湖区", answer)
        self.assertFalse(model.requests[1]["messages"][-1]["content"][0]["is_error"])

    async def test_http_faults_keep_prior_success_and_stop_remaining_batch(self):
        for fault, explanation in [(401, "Key"), (429, "额度"), (503, "服务"),
                                   ("disconnect", "连接"), ("json-read", "连接"), ("sse-read", "连接")]:
            with self.subTest(fault=fault):
                server = MCPServer("HTTP 故障测试")
                sent = []
                closed_streams = []

                @server.tool(name="maps_text_search")
                def search(keywords: str, city: str) -> dict:
                    return {"name": "西湖", "address": "杭州市西湖区"}

                class FaultTransport(httpx2.ASGITransport):
                    async def handle_async_request(self, request):
                        payload = json.loads(request.content) if request.method == "POST" else {}
                        if payload.get("method") == "tools/call":
                            sent.append(payload)
                            if len(sent) > 1:
                                if fault == "disconnect":
                                    raise httpx2.ConnectError("network failed", request=request)
                                if fault in ("json-read", "sse-read"):
                                    class FailedStream(httpx2.AsyncByteStream):
                                        async def __aiter__(self):
                                            yield b"id: resume\n\n" if fault == "sse-read" else b'{"jsonrpc":'
                                            raise httpx2.ReadError("stream disconnected", request=request)

                                        async def aclose(self):
                                            closed_streams.append(True)

                                    return httpx2.Response(200, stream=FailedStream(), headers={
                                        "content-type": "text/event-stream" if fault == "sse-read" else "application/json"
                                    })
                                return httpx2.Response(fault, text="gateway error")
                        return await super().handle_async_request(request)

                app = server.streamable_http_app(json_response=True, stateless_http=True)
                model = ModelService([response([
                    {"type": "tool_use", "id": call_id, "name": "maps_text_search",
                     "input": {"keywords": "西湖", "city": "杭州"}}
                    for call_id in ["ok", "fault", "pending"]
                ], "tool_use")])
                history = [{"role": "user", "content": "查杭州的地点"}]
                async with app.router.lifespan_context(app):
                    async with AmapHTTPClient(transport=FaultTransport(app=app)) as http_client:
                        transport = streamable_http_client("http://127.0.0.1:8000/mcp", http_client=http_client)
                        async with Client(transport) as client:
                            tools = AmapTools(client, tool_timeout=0.1, http_client=http_client)
                            await tools.discover()
                            answer = await agent_loop(history, cast(AsyncAnthropic, model), tools,
                                                      "test-model", max_rounds=1)

                self.assertEqual(len(sent), 2)
                self.assertIn("地图查询已暂停", answer)
                self.assertNotIn("上限", answer)
                self.assertNotIn("杭州市西湖区", answer)
                self.assertIn("杭州市西湖区", str(history))
                self.assertIn("待核实", answer)
                results = history[-2]["content"]
                self.assertEqual([item["tool_use_id"] for item in results], ["ok", "fault", "pending"])
                self.assertEqual([item["is_error"] for item in results], [False, True, True])
                if fault in ("json-read", "sse-read"):
                    self.assertTrue(closed_streams)

    async def test_cli_connection_failure_ends_runtime_and_closes_clients(self):
        server = MCPServer("断连测试地图服务")

        @server.tool(name="maps_text_search")
        def search(keywords: str, city: str) -> str:
            return "西湖"

        async def disconnect(request, call_next):
            payload = await request.json()
            if payload.get("method") == "tools/call":
                return JSONResponse({"jsonrpc": "2.0", "id": payload["id"],
                                     "error": {"code": CONNECTION_CLOSED, "message": "Connection closed"}})
            return await call_next(request)

        app = server.streamable_http_app(json_response=True, stateless_http=True)
        app.add_middleware(BaseHTTPMiddleware, dispatch=disconnect)
        model_reply = response([
            {"type": "tool_use", "id": "disconnect", "name": "maps_text_search",
             "input": {"keywords": "西湖", "city": "杭州"}}
        ], "tool_use")
        model_http = httpx2.AsyncClient(transport=httpx2.MockTransport(
            lambda request: httpx2.Response(200, json=model_reply.model_dump(mode="json"))
        ))
        captured = io.StringIO()
        async with app.router.lifespan_context(app):
            async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app)) as map_http:
                client = Client(streamable_http_client("http://127.0.0.1:8000/mcp", http_client=map_http))
                with (
                    patch.dict(os.environ, {"MODEL_ID": "test-model", "ANTHROPIC_API_KEY": "test-key"}, clear=True),
                    patch("agent.Client", return_value=client),
                    patch("travel_tools.Client", side_effect=ConnectionError("资料服务不可用")),
                    patch("agent.AsyncAnthropic", side_effect=lambda **kwargs: AsyncAnthropic(http_client=model_http, **kwargs)),
                    patch("builtins.input", side_effect=["查询杭州西湖"]) as user_input,
                    contextlib.redirect_stdout(captured),
                ):
                    self.assertEqual(await run_cli("test-amap-key"), 1)
                user_input.assert_called_once()

        self.assertIn("地图查询已暂停", captured.getvalue())
        self.assertIn("待核实", captured.getvalue())
        self.assertTrue(model_http.is_closed)
        with self.assertRaises(RuntimeError):
            _ = client.session

    async def test_sdk_schema_failure_does_not_retry_outside_tool_budget(self):
        server = MCPServer("不重试测试地图服务")
        tool_requests = []

        @server.tool(name="maps_text_search")
        def search(keywords: str, city: str) -> str:
            return "西湖"

        async def reject_call(request, call_next):
            payload = await request.json()
            if payload.get("method") == "tools/call":
                tool_requests.append(payload)
                return JSONResponse({"jsonrpc": "2.0", "id": payload["id"],
                                     "error": {"code": HEADER_MISMATCH, "message": "stale schema"}})
            return await call_next(request)

        app = server.streamable_http_app(json_response=True, stateless_http=True)
        app.add_middleware(BaseHTTPMiddleware, dispatch=reject_call)
        model = ModelService([response([
            {"type": "tool_use", "id": "mismatch", "name": "maps_text_search",
             "input": {"keywords": "西湖", "city": "杭州"}}
        ], "tool_use")])
        history = [{"role": "user", "content": "查询杭州西湖"}]
        async with app.router.lifespan_context(app):
            async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app)) as http_client:
                transport = streamable_http_client("http://127.0.0.1:8000/mcp", http_client=http_client)
                async with Client(transport) as client:
                    tools = AmapTools(client)
                    await tools.discover()
                    await agent_loop(history, cast(AsyncAnthropic, model), tools,
                                     "test-model", max_tool_calls=1)

        self.assertEqual(len(tool_requests), 1)
        self.assertTrue(history[-2]["content"][0]["is_error"])

    async def test_cli_releases_both_sdk_clients_on_exit_interrupt_and_exception(self):
        released = asyncio.Event()

        @contextlib.asynccontextmanager
        async def lifespan(server):
            try:
                yield {}
            finally:
                released.set()

        server = MCPServer("退出测试地图服务", lifespan=lifespan)

        @server.tool(name="maps_text_search")
        def search(keywords: str, city: str) -> str:
            return "西湖：杭州市西湖区"

        for user_input in ["exit", EOFError(), KeyboardInterrupt(),
                           asyncio.CancelledError(), RuntimeError("可控异常")]:
            with self.subTest(exit=type(user_input).__name__):
                released.clear()
                models = []
                def map_client(*args, **kwargs):
                    return Client(server)

                def model_client(**kwargs):
                    client = AsyncAnthropic(**kwargs)
                    models.append(client)
                    return client

                with (
                    patch.dict(os.environ, {"MODEL_ID": "test-model", "ANTHROPIC_API_KEY": "test-key"}, clear=True),
                    patch("agent.Client", side_effect=map_client),
                    patch("travel_tools.Client", side_effect=ConnectionError("资料服务不可用")),
                    patch("agent.AsyncAnthropic", side_effect=model_client),
                    patch("builtins.input", side_effect=[user_input]),
                ):
                    if isinstance(user_input, (KeyboardInterrupt, asyncio.CancelledError, RuntimeError)):
                        with self.assertRaises(BaseException) as caught:
                            await run_cli("test-amap-key")
                        error = caught.exception
                        if isinstance(error, BaseExceptionGroup):
                            self.assertIsNotNone(error.subgroup(type(user_input)))
                        else:
                            self.assertIsInstance(error, type(user_input))
                    else:
                        self.assertEqual(await run_cli("test-amap-key"), 0)

                self.assertTrue(models[0].is_closed())
                self.assertTrue(released.is_set())

    async def test_default_budget_fits_three_days_of_place_queries(self):
        server = MCPServer("三日查询预算测试")
        calls = []

        @server.tool(name="maps_text_search")
        def search(keywords: str, city: str) -> dict:
            calls.append((keywords, city))
            return {"id": keywords, "name": keywords, "city": city}

        @server.tool(name="maps_search_detail")
        def detail(id: str) -> dict:
            calls.append(id)
            return {"id": id, "address": "模拟地点地址", "location": "120.1,30.2"}

        replies = []
        for day in range(1, 4):
            replies.append(response([
                {"type": "tool_use", "id": f"search-{day}-{place}", "name": "maps_text_search",
                 "input": {"keywords": f"第{day}天地点{place}", "city": "杭州"}}
                for place in range(1, 4)
            ], "tool_use"))
            replies.append(response([
                {"type": "tool_use", "id": f"detail-{day}-{place}", "name": "maps_search_detail",
                 "input": {"id": f"第{day}天地点{place}"}}
                for place in range(1, 4)
            ], "tool_use"))
        replies.append(response([{"type": "text", "text": "三日共九个地点已完成检索与详情查询。"}]))
        model = ModelService(replies)

        async with Client(server) as client:
            tools = AmapTools(client)
            await tools.discover()
            answer = await agent_loop(
                [{"role": "user", "content": "查询杭州三天每天三个地点的详情"}],
                cast(AsyncAnthropic, model), tools, "test-model",
            )

        self.assertEqual(len(calls), 18)
        self.assertEqual(len(model.requests), 7)
        self.assertIn("已完成", answer)

    async def test_cli_model_auth_headers_omit_unused_credentials(self):
        server = MCPServer("认证测试地图服务")

        @server.tool(name="maps_text_search")
        def search(keywords: str, city: str) -> str:
            return "西湖：杭州市西湖区"

        configurations = [
            ({"ANTHROPIC_API_KEY": "test-api-key",
              "ANTHROPIC_BASE_URL": "https://model.example.test",
              "ANTHROPIC_AUTH_TOKEN": "unused-token"},
             {"x-api-key": "test-api-key"}),
            ({"ANTHROPIC_API_KEY": "test-api-key"},
             {"x-api-key": "test-api-key"}),
            ({"ANTHROPIC_AUTH_TOKEN": "test-auth-token"},
             {"authorization": "Bearer test-auth-token"}),
        ]
        for environment, expected in configurations:
            with self.subTest(environment=list(environment)):
                requests = []

                def reply(request):
                    requests.append(request)
                    return httpx2.Response(200, json=response([
                        {"type": "text", "text": "请提供地点关键词。"}
                    ]).model_dump(mode="json"))

                async with httpx2.AsyncClient(
                    transport=httpx2.MockTransport(reply)
                ) as http_client:
                    def model_client(**kwargs):
                        return AsyncAnthropic(http_client=http_client, **kwargs)

                    with (
                        patch.dict(os.environ, {"MODEL_ID": "test-model", **environment}, clear=True),
                        patch("agent.Client", side_effect=lambda *args, **kwargs: Client(server)),
                        patch("travel_tools.Client", side_effect=ConnectionError("资料服务不可用")),
                        patch("agent.AsyncAnthropic", side_effect=model_client),
                        patch("builtins.input", side_effect=["查询杭州西湖", "exit"]),
                        contextlib.redirect_stdout(io.StringIO()),
                    ):
                        result = await run_cli("test-amap-key")

                self.assertEqual(result, 0)
                self.assertEqual(len(requests), 1)
                headers = requests[0].headers
                actual = {name: headers[name] for name in ["x-api-key", "authorization"]
                          if name in headers}
                self.assertEqual(actual, expected)

    async def test_real_sdk_queries_a_place_then_its_details(self):
        server = MCPServer("测试地图服务")

        @server.tool(name="maps_text_search")
        def search(keywords: str, city: str) -> CallToolResult:
            """按城市和关键词检索地点。"""
            if (keywords, city) != ("西湖", "杭州"):
                raise ValueError("地点条件错误")
            return CallToolResult(
                content=[TextContent(type="text", text="查到西湖")],
                structured_content={"pois": [{"id": "west-lake", "name": "西湖"}]},
            )

        @server.tool(name="maps_search_detail")
        def detail(id: str) -> dict:
            """按地点标识查询详情。"""
            if id != "west-lake":
                raise ValueError("地点标识错误")
            return {"id": id, "name": "西湖", "address": "杭州市西湖区"}

        @server.tool(name="bash")
        def forbidden(command: str) -> str:
            raise AssertionError("不应执行编程工具")

        model = ModelService([
            response([{"type": "tool_use", "id": "search", "name": "maps_text_search",
                       "input": {"keywords": "西湖", "city": "杭州"}}], "tool_use"),
            response([{"type": "tool_use", "id": "detail", "name": "maps_search_detail",
                       "input": {"id": "west-lake"}}], "tool_use"),
            response([{"type": "text", "text": "查到西湖，地址为杭州市西湖区。"}]),
        ])

        async with Client(server) as client:
            tools = AmapTools(client)
            await tools.discover()
            answer = await agent_loop(
                [{"role": "user", "content": "查询杭州西湖的详情"}],
                cast(AsyncAnthropic, model), tools, "test-model",
            )

        self.assertIn("杭州市西湖区", answer)
        self.assertEqual(
            {tool["name"] for tool in model.requests[0]["tools"]},
            {"maps_text_search", "maps_search_detail"},
        )
        search_result = model.requests[1]["messages"][-1]["content"][0]
        self.assertEqual(json.loads(search_result["content"])["data"]["pois"][0]["id"], "west-lake")
        detail_result = model.requests[2]["messages"][-1]["content"][0]
        self.assertEqual(detail_result["tool_use_id"], "detail")
        self.assertIn("杭州市西湖区", detail_result["content"])

    async def test_streamable_http_discovers_and_queries_places(self):
        server = MCPServer("HTTP 测试地图服务")

        @server.tool(name="maps_text_search")
        def search(keywords: str, city: str) -> str:
            return "西湖：杭州市西湖区"

        app = server.streamable_http_app(json_response=True, stateless_http=True)
        model = ModelService([
            response([{"type": "tool_use", "id": "http-query", "name": "maps_text_search",
                       "input": {"keywords": "西湖", "city": "杭州"}}], "tool_use"),
            response([{"type": "text", "text": "西湖位于杭州市西湖区。"}]),
        ])
        async with app.router.lifespan_context(app):
            async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app)) as http_client:
                transport = streamable_http_client(
                    "http://127.0.0.1:8000/mcp", http_client=http_client
                )
                async with Client(transport, read_timeout_seconds=2) as client:
                    tools = AmapTools(client)
                    await tools.discover()
                    answer = await agent_loop(
                        [{"role": "user", "content": "查询杭州西湖"}],
                        cast(AsyncAnthropic, model), tools, "test-model",
                    )

        self.assertTrue(http_client.is_closed)
        self.assertIn("杭州市西湖区", answer)
        result = model.requests[1]["messages"][-1]["content"][0]
        self.assertEqual(result["tool_use_id"], "http-query")
        self.assertIn("杭州市西湖区", result["content"])
